"""Run a small SWE-bench batch through the embedded StackPlanner graph.

This is intentionally a pilot runner.  It prepares one isolated repository and
one StackPlanner thread per instance, captures the stream, and emits the final
git diff in a predictions JSONL file that can later be graded by the official
SWE-bench harness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr[-4000:]}")
    return result.stdout


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Accept both native SWE-bench rows and the local canonical-50 schema."""

    if row.get("instance_id") and row.get("repo") and row.get("base_commit"):
        return dict(row)

    metadata = row.get("swe_bench_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    instance_id = row.get("original_id")
    if not instance_id:
        task_id = str(row.get("task_id") or "")
        marker = "extracted-swe_bench_verified-test-"
        if task_id.startswith(marker):
            instance_id = task_id[len(marker) :]

    normalized = dict(row)
    normalized.update(
        {
            "instance_id": instance_id,
            "repo": row.get("repo") or metadata.get("repo"),
            "base_commit": row.get("base_commit") or metadata.get("base_commit"),
            "problem_statement": (
                row.get("problem_statement")
                or row.get("fully_specified_question")
                or row.get("question")
            ),
            "FAIL_TO_PASS": row.get("FAIL_TO_PASS", metadata.get("FAIL_TO_PASS")),
            "PASS_TO_PASS": row.get("PASS_TO_PASS", metadata.get("PASS_TO_PASS")),
            "version": row.get("version", metadata.get("version")),
            "difficulty": row.get("difficulty", metadata.get("difficulty")),
        }
    )
    missing = [
        key
        for key in ("instance_id", "repo", "base_commit", "problem_statement")
        if not normalized.get(key)
    ]
    if missing:
        raise ValueError(f"invalid SWE-bench row; missing required fields: {', '.join(missing)}")
    return normalized


_TEST_RANK_STOP_WORDS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "function",
        "functions",
        "class",
        "method",
        "module",
        "python",
    }
)

_VERIFICATION_ENVIRONMENT_BLOCKER_PATTERN = re.compile(
    r"(?:"
    r"extension modules? (?:were |was )?not built|"
    r"ModuleNotFoundError|No module named|"
    r"(?:package|dependency|dependencies) .{0,80}(?:not found|not installed|missing)|"
    r"(?:pytest|test runner).{0,40}(?:not found|not installed|unavailable)|"
    r"(?:cannot|can't|could not|unable to) (?:import|run).{0,100}(?:dependency|build|environment)|"
    r"historical .{0,40}(?:environment|dependency).{0,40}(?:incompatible|unavailable)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_VERIFICATION_BEHAVIOR_FAILURE_PATTERN = re.compile(
    r"(?:AssertionError|CHECK\s*FAIL|PASS(?:ED)?\s*[:=]\s*False|"
    r"(?:test|behavior|behaviour|regression).{0,80}(?:fail(?:ed|ure)?|broken|incorrect)|"
    r"(?:breaks?|broke).{0,80}(?:test|behavior|behaviour|compatibility))",
    re.IGNORECASE | re.DOTALL,
)


def _verification_event_text(mode: str, payload: Any) -> str:
    """Extract bounded observable verification evidence from an A2A event."""

    if mode != "custom" or not isinstance(payload, dict):
        return ""
    if str(payload.get("a2a_stage") or "") != "verification":
        return ""
    values: list[str] = []
    for key in ("result", "error", "stop_reason"):
        value = payload.get(key)
        if value:
            values.append(str(value))
    message = payload.get("message")
    if isinstance(message, dict):
        for key in ("content", "error"):
            value = message.get(key)
            if value:
                values.append(str(value))
    envelope = payload.get("result_envelope")
    if isinstance(envelope, dict):
        for key in ("summary", "error", "stop_reason"):
            value = envelope.get(key)
            if value:
                values.append(str(value))
        metadata = envelope.get("artifact_metadata")
        gaps = metadata.get("evidence_gaps") if isinstance(metadata, dict) else None
        if isinstance(gaps, list):
            values.extend(str(gap) for gap in gaps if str(gap).strip())
    return "\n".join(values)[:20_000]


def _verification_event_flags(mode: str, payload: Any) -> tuple[bool, bool]:
    """Return (environment_blocked, observable_behavior_failed)."""

    text = _verification_event_text(mode, payload)
    return (
        bool(text and _VERIFICATION_ENVIRONMENT_BLOCKER_PATTERN.search(text)),
        bool(text and _VERIFICATION_BEHAVIOR_FAILURE_PATTERN.search(text)),
    )


def _test_relevance_tokens(value: str) -> set[str]:
    # For pytest node IDs, rank on the concrete test name rather than shared
    # repository/path/class tokens that make every test look equally relevant.
    if "::" in value:
        value = value.rsplit("::", 1)[-1]
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9]*", value.lower()):
        token = re.sub(r"\d+$", "", raw)
        if token.startswith("float"):
            token = "float"
        elif token.startswith("card"):
            token = "card"
        elif token.startswith("represent"):
            token = "represent"
        if len(token) >= 3 and token not in _TEST_RANK_STOP_WORDS:
            tokens.add(token)
    return tokens


def _select_prompt_tests(row: dict[str, Any], *, limit: int = 10) -> list[str]:
    """Fit benchmark tests into the prompt by behavioral relevance, not file order."""

    fail_to_pass = _test_names(row.get("FAIL_TO_PASS"))
    pass_to_pass = _test_names(row.get("PASS_TO_PASS"))
    selected = list(dict.fromkeys(fail_to_pass))[:limit]
    remaining = max(0, limit - len(selected))
    if remaining == 0:
        return selected
    seed = _test_relevance_tokens(
        "\n".join([str(row.get("problem_statement") or ""), *fail_to_pass])
    )
    ranked = sorted(
        enumerate(pass_to_pass),
        key=lambda item: (
            -len(seed & _test_relevance_tokens(item[1])),
            item[0],
        ),
    )
    selected.extend(test for _, test in ranked[:remaining] if test not in selected)
    return selected


def _is_external_grader_handoff_event(
    mode: str,
    payload: Any,
    *,
    enabled: bool,
    verification_completions: int = 0,
    max_verification_completions: int = 3,
    verification_environment_blocked: bool = False,
    verification_behavior_failed: bool = False,
) -> bool:
    """Recognize an accepted or bounded external-grader handoff boundary.

    A raw A2A ``task_completed`` event only means that the child process
    returned.  The SP adapter has not yet classified its test evidence or
    evidence gaps at that point.  Treating that event as success bypasses the
    normal recovery path (for example, a verifier can return after a pytest
    command reported a non-zero status).  Only an event that explicitly
    carries the post-adapter acceptance fields is safe for early handoff.
    Current raw child events do not carry those fields, so the graph continues
    until SP has consumed and validated the result. If the local environment
    blocks successful verification repeatedly, the benchmark runner hands a
    valid source patch to the canonical Docker grader after a bounded number of
    completed verification attempts; normal SP2 runs are unaffected.
    """
    test_verification = payload.get("test_verification") if isinstance(payload, dict) else None
    bounded_verification_boundary = bool(
        isinstance(payload, dict)
        and payload.get("type") == "task_completed"
        and str(payload.get("a2a_stage") or "") == "verification"
        and (
            verification_completions >= max(1, max_verification_completions)
            or (
                verification_completions >= 1
                and verification_environment_blocked
                and not verification_behavior_failed
            )
        )
    )
    return bool(
        enabled
        and mode == "custom"
        and isinstance(payload, dict)
        and payload.get("type") == "task_completed"
        and str(payload.get("a2a_stage") or "") == "verification"
        and (
            (
                payload.get("completion_status") == "complete"
                and isinstance(test_verification, dict)
                and test_verification.get("passed") is True
            )
            or bounded_verification_boundary
        )
    )


def _materialize_archive_instance(row: dict[str, Any], repo_dir: Path) -> Path:
    """Materialize a base commit when Git transport cannot clone the repo."""
    archive_url = f"https://codeload.github.com/{row['repo']}/tar.gz/{row['base_commit']}"
    with tempfile.TemporaryDirectory(prefix="swebench-archive-") as temp_dir:
        archive_path = Path(temp_dir) / "source.tar.gz"
        request = urllib.request.Request(archive_url, headers={"User-Agent": "StackPlanner2-SWE-bench"})
        with urllib.request.urlopen(request, timeout=1800) as response, archive_path.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        extract_dir = Path(temp_dir) / "extracted"
        extract_dir.mkdir()
        with tarfile.open(archive_path, mode="r:gz") as archive:
            archive.extractall(extract_dir, filter="data")
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError(f"unexpected GitHub archive layout for {archive_url}")
        shutil.copytree(roots[0], repo_dir)

    _run(["git", "init", "--quiet"], cwd=repo_dir, timeout=120)
    _run(["git", "add", "--all"], cwd=repo_dir, timeout=120)
    _run(
        ["git", "-c", "user.name=StackPlanner2", "-c", "user.email=stackplanner2@localhost", "commit", "--quiet", "-m", f"SWE-bench base {row['base_commit']}"],
        cwd=repo_dir,
        timeout=120,
    )
    return repo_dir


def _load_docker_image_map(path: Path | None) -> dict[str, str]:
    """Load instance→canonical-image mappings from prepull status/audit JSON."""

    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_images = payload.get("images") if isinstance(payload, dict) else None
    mapping: dict[str, str] = {}
    if isinstance(raw_images, dict):
        records = raw_images.values()
    elif isinstance(raw_images, list):
        records = raw_images
    else:
        raise ValueError(f"Docker image manifest has no images object/list: {path}")
    for record in records:
        if not isinstance(record, dict):
            continue
        instance_id = str(record.get("instance_id") or "").strip()
        image = str(record.get("canonical_tag") or record.get("canonical_image") or "").strip()
        if instance_id and image:
            mapping[instance_id] = image
    return mapping


def _materialize_docker_instance(row: dict[str, Any], repo_dir: Path, image: str) -> Path:
    """Copy an exact base checkout from an already-local canonical image."""

    _run(["docker", "image", "inspect", image], timeout=120)
    container_id = _run(["docker", "create", "--network", "none", image], timeout=120).strip()
    if not container_id:
        raise RuntimeError(f"docker create returned no container id for {image}")
    try:
        _run(["docker", "cp", f"{container_id}:/testbed", str(repo_dir)], timeout=900)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    if not (repo_dir / ".git").exists():
        raise RuntimeError(f"canonical image {image} did not contain a git checkout at /testbed")
    _run(["git", "checkout", "--detach", row["base_commit"]], cwd=repo_dir, timeout=300)
    _run(["git", "reset", "--hard", row["base_commit"]], cwd=repo_dir, timeout=300)
    _run(["git", "clean", "-fdx"], cwd=repo_dir, timeout=300)
    return repo_dir


def _clone_instance(
    row: dict[str, Any],
    work_dir: Path,
    *,
    repo_source: Path | None = None,
    docker_image: str | None = None,
) -> Path:
    repo_dir = work_dir / _safe(row["repo"].split("/")[-1])
    if repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            _run(["git", "reset", "--hard", row["base_commit"]], cwd=repo_dir)
            _run(["git", "clean", "-fdx"], cwd=repo_dir)
            return repo_dir
        except RuntimeError:
            # A failed network clone may leave a .git directory without the
            # requested commit. Treat it as a partial cache and rebuild it.
            shutil.rmtree(repo_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{row['repo']}.git"
    if repo_source is not None:
        source = repo_source.expanduser().resolve()
        if not (source / ".git").exists():
            raise RuntimeError(f"local SWE-bench repository cache is not a git checkout: {source}")
        # Do not use ``git clone --local`` for a blob:none partial clone: the
        # local clone can inherit promisor metadata without all blobs needed by
        # the target commit, leaving a worktree full of false deletions. Copy
        # the already-materialized isolated checkout and its metadata instead.
        shutil.copytree(source, repo_dir)
        try:
            _run(["git", "checkout", "--detach", row["base_commit"]], cwd=repo_dir, timeout=300)
            _run(["git", "reset", "--hard", row["base_commit"]], cwd=repo_dir, timeout=300)
        except RuntimeError:
            # Archive materialization creates a synthetic local base commit;
            # its tree is already the requested SWE-bench revision even though
            # the original upstream commit object is not present locally.
            _run(["git", "reset", "--hard", "HEAD"], cwd=repo_dir, timeout=300)
        _run(["git", "clean", "-fdx"], cwd=repo_dir, timeout=300)
        return repo_dir
    if docker_image:
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        try:
            return _materialize_docker_instance(row, repo_dir, docker_image)
        except Exception:
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
            logger.warning(
                "Failed to materialize %s from local canonical image %s; falling back to network",
                row["instance_id"],
                docker_image,
                exc_info=True,
            )
    last_error: Exception | None = None
    for attempt in range(1, 3):
        if repo_dir.exists():
            # A transport failure can leave a partial non-repository directory
            # behind. It is owned by this isolated benchmark run, so remove
            # only that directory before retrying the clone.
            shutil.rmtree(repo_dir, ignore_errors=True)
        try:
            _run(["git", "clone", "--filter=blob:none", "--no-tags", url, str(repo_dir)], timeout=1800)
            _run(["git", "checkout", "--detach", row["base_commit"]], cwd=repo_dir, timeout=300)
            return repo_dir
        except RuntimeError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * attempt)
    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    try:
        return _materialize_archive_instance(row, repo_dir)
    except Exception as archive_error:
        raise RuntimeError(
            f"failed to clone {row['repo']} after 3 attempts and archive fallback failed: {archive_error}"
        ) from archive_error


def _prompt(row: dict[str, Any]) -> str:
    repository_name = _safe(row["repo"].split("/")[-1])
    prompt_tests = _select_prompt_tests(row)
    expected_tests = "\n".join(f"- {test}" for test in prompt_tests) or "- No test list was supplied; locate the smallest existing focused test from the repository."
    fail_to_pass = _test_names(row.get("FAIL_TO_PASS"))
    pass_to_pass = _test_names(row.get("PASS_TO_PASS"))
    contract_tests = "\n".join(f"  - {test}" for test in fail_to_pass[:50]) or "  - none supplied"
    return f"""You are solving a SWE-bench issue in the repository currently mounted at /mnt/user-data/workspace.

Repository layout:
- The repository root is `/mnt/user-data/workspace/{repository_name}`.
- Inspect that directory first. Do not assume source files are directly under
  `/mnt/user-data/workspace`; the Python package may be nested one level below
  the repository root.
- If a named file is not where expected, use `find /mnt/user-data/workspace/{repository_name} -type f` or `rg` before concluding it is missing.

Issue:
{row['problem_statement']}

Immutable benchmark contract:
- The issue text above is the authoritative original requirement. Do not
  reinterpret it into a weaker behavior just because a local example is easy
  to satisfy; preserve the requested semantics through the full delegation.
- The primary FAIL_TO_PASS acceptance tests are:
{contract_tests}
- A patch is not complete merely because it changes source code, compiles, or
  produces a plausible explanation. The implementation must pass the primary
  tests and must not regress the supplied PASS_TO_PASS tests when the local
  environment can run them.
- If the provider returns an empty/fallback response, a command fails, or the
  environment blocks verification, preserve the repository and report that
  evidence. Never manufacture a source edit to turn missing evidence into a
  completion claim.

Implement the fix in the repository. Inspect the existing code and tests, make the smallest correct change, and run the relevant tests. Do not merely describe a patch: actually edit the files. If tests cannot run because of dependency or environment limitations, still implement the fix and report the exact limitation. Do not modify files outside the repository.

Execution contract for StackPlanner: use one coder delegation to inspect the
repository, make the source edit, and run focused checks. Do not terminate a
read-only location pass as the completed repository repair. Prefer surgical
`str_replace` edits over rewriting an existing file with a long `write_file`
payload.

Benchmark patch contract:
- Preserve all existing source and test files. Never replace or truncate a whole
  file to make a small change; use a surgical edit.
- Never add, delete, or rewrite test files. SWE-bench's external grader owns and
  injects the hidden test patch after the model patch; editing tests can conflict
  with that patch. Read and run existing tests, but edit implementation files only.
- The final patch must contain the actual implementation change, not only a new
  test or a written explanation.
- Before claiming completion, inspect `git diff --stat` and `git diff --check`.

Validation targets supplied by the benchmark:
{expected_tests}

Use the listed FAIL_TO_PASS tests as the primary regression target and keep the
listed PASS_TO_PASS tests green when practical. Do not infer correctness from a
plausible one-line diff alone: trace the changed control flow through the
relevant existing tests, then run the focused commands after the final source
edit. If the repository cannot run these historical tests in the current
environment, report the exact import/dependency failure, then locate and read
the actual named test functions in the checkout. Extract their literal and
parameterized inputs and expected invariants and reproduce those exact cases
through the smallest independent executable path available. Do not substitute
self-chosen examples for repository regression cases. Still perform syntax and
diff checks, and explicitly report any case that contradicts the patch.
"""


def _patch_quality(repo_dir: Path, *, run_error: str | None = None) -> dict[str, Any]:
    """Run cheap post-run checks so pilot results cannot masquerade as passes."""
    status = _run(["git", "status", "--short"], cwd=repo_dir, timeout=120)
    names = _run(["git", "diff", "--name-only"], cwd=repo_dir, timeout=120).splitlines()
    diff_check = subprocess.run(
        ["git", "diff", "--check"],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    source_paths = [path for path in names if not _is_test_path(path)]
    syntax_errors: list[str] = []
    for path in source_paths:
        if not path.endswith(".py"):
            continue
        check = subprocess.run(
            [sys.executable, "-m", "py_compile", path],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if check.returncode != 0:
            syntax_errors.append(f"{path}: {check.stderr.strip() or check.stdout.strip()}")
    gaps: list[str] = []
    if not source_paths:
        gaps.append("no non-test source file changed")
    if diff_check.returncode != 0:
        gaps.append("git diff --check failed")
    if syntax_errors:
        gaps.append("Python syntax check failed")
    if run_error:
        gaps.append("agent run did not complete successfully")
    return {
        "valid_source_patch": bool(source_paths and diff_check.returncode == 0 and not syntax_errors and not run_error),
        "changed_paths": names,
        "source_paths": source_paths,
        "diff_check_passed": diff_check.returncode == 0,
        "syntax_errors": syntax_errors,
        "gaps": gaps,
        "status": status,
    }


def _benchmark_handoff_eligibility(
    quality: dict[str, Any], benchmark_tests: dict[str, Any]
) -> dict[str, Any]:
    """Apply the benchmark-side acceptance gate before official grading."""
    if not quality.get("valid_source_patch"):
        return {
            "eligible": False,
            "reason": "invalid_source_patch",
            "verification_status": benchmark_tests.get("status", "not_run"),
        }
    status = str(benchmark_tests.get("status") or "not_run")
    if status == "failed":
        return {
            "eligible": False,
            "reason": "declared_benchmark_test_failed",
            "verification_status": status,
        }
    if status == "blocked":
        return {
            "eligible": True,
            "reason": "local_verification_blocked_external_grader_required",
            "verification_status": status,
        }
    if status in {"passed", "not_configured"}:
        return {"eligible": True, "reason": status, "verification_status": status}
    return {"eligible": False, "reason": "verification_not_completed", "verification_status": status}


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = normalized.split("/")
    name = parts[-1]
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def _test_names(value: Any) -> list[str]:
    """Normalize SWE-bench test metadata from JSONL/HF rows."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _validation_pythonpath(repo_dir: Path) -> str:
    """Build an isolated import path for historical repositories.

    SWE-bench contains projects predating the active Python runtime. Keep the
    compatibility adjustment local to the test subprocess and never modify the
    candidate repository. The vendored urllib3 layout used by old Requests
    releases needs its ssl_match_hostname directory importable on Python 3.12.
    """
    repo_dir = repo_dir.resolve()
    roots = [repo_dir / "src", repo_dir]
    roots.extend(
        path
        for path in repo_dir.rglob("ssl_match_hostname")
        if path.is_dir() and (path / "_implementation.py").exists()
    )
    return os.pathsep.join(str(path) for path in roots if path.exists())


def _run_test_targets(
    repo_dir: Path,
    targets: list[str],
    *,
    timeout: int,
    label: str,
) -> dict[str, Any]:
    """Run one SWE-bench target group and retain bounded evidence."""
    if not targets:
        return {"status": "not_configured", "label": label, "tests": []}
    command = [sys.executable, "-m", "pytest", "-q", *targets]
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONPATH"] = _validation_pythonpath(repo_dir)
    try:
        completed = subprocess.run(
            command,
            cwd=repo_dir,
            env=environment,
            text=True,
            capture_output=True,
            timeout=max(1, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "blocked",
            "label": label,
            "tests": targets,
            "command": command,
            "reason": "test_timeout",
            "stdout_tail": (exc.stdout or "")[-4000:],
            "stderr_tail": (exc.stderr or "")[-4000:],
        }
    passed = completed.returncode == 0
    return {
        "status": "passed" if passed else "failed",
        "label": label,
        "tests": targets,
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _run_benchmark_tests(repo_dir: Path, row: dict[str, Any], *, timeout: int = 300) -> dict[str, Any]:
    """Run the benchmark's declared tests without contaminating the source diff."""
    fail_to_pass = _test_names(row.get("FAIL_TO_PASS"))
    pass_to_pass = _test_names(row.get("PASS_TO_PASS"))
    if not fail_to_pass and not pass_to_pass:
        return {
            "status": "not_configured",
            "reason": "row has no FAIL_TO_PASS or PASS_TO_PASS metadata",
            "fail_to_pass": {"status": "not_configured", "tests": []},
            "pass_to_pass": {"status": "not_configured", "tests": []},
        }
    # Run in a copy so an optional SWE-bench test patch cannot enter the model
    # patch. The original candidate checkout remains the source of truth for
    # diff and syntax checks.
    validation_dir = repo_dir.parent / f"{repo_dir.name}-validation"
    if validation_dir.exists():
        shutil.rmtree(validation_dir, ignore_errors=True)
    shutil.copytree(repo_dir, validation_dir)
    (validation_dir / "sitecustomize.py").write_text(
        "import collections\n"
        "import collections.abc\n"
        "for _name in (\"Callable\", \"Mapping\", \"MutableMapping\", \"MutableSet\", \"Sequence\"):\n"
        "    if not hasattr(collections, _name):\n"
        "        setattr(collections, _name, getattr(collections.abc, _name))\n",
        encoding="utf-8",
    )
    test_patch = row.get("test_patch")
    patch_status = "not_supplied"
    if test_patch:
        patch_process = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=validation_dir,
            input=str(test_patch),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if patch_process.returncode != 0:
            return {
                "status": "blocked",
                "reason": "test_patch_apply_failed",
                "test_patch_stderr": patch_process.stderr[-4000:],
                "fail_to_pass": {"status": "not_run", "tests": fail_to_pass},
                "pass_to_pass": {"status": "not_run", "tests": pass_to_pass},
            }
        patch_status = "applied_in_validation_copy"
    fail_result = _run_test_targets(
        validation_dir,
        fail_to_pass,
        timeout=timeout,
        label="FAIL_TO_PASS",
    )
    pass_result = _run_test_targets(
        validation_dir,
        pass_to_pass,
        timeout=timeout,
        label="PASS_TO_PASS",
    )
    statuses = {fail_result["status"], pass_result["status"]}
    status = "passed" if statuses <= {"passed", "not_configured"} else "failed"
    if "blocked" in statuses:
        status = "blocked"
    return {
        "status": status,
        "test_patch": patch_status,
        "fail_to_pass": fail_result,
        "pass_to_pass": pass_result,
    }


def _diagnose_stop_reason(
    *,
    error: str | None,
    last_state: dict[str, Any] | None,
    event_count: int,
    last_event_type: str | None = None,
) -> dict[str, Any]:
    """Return an explicit, user-safe explanation for a benchmark stop.

    This is intentionally based on observable runtime state and exception
    classes, not model-private reasoning.  In particular, a timeout during
    perception with no action is reported as ``no_progress_timeout`` rather
    than the unhelpful generic ``TimeoutError``.
    """

    state = last_state or {}
    message = str(error or "")
    lowered = message.lower()
    stage = str(state.get("sp_current_stage") or "") or None
    action_id = state.get("sp_current_action_id") or state.get("sp_last_action_id")
    action_id = str(action_id) if action_id else None
    iteration = int(state.get("sp_loop_iteration") or 0)

    if "timeout" in lowered or "timed out" in lowered:
        reason = "no_progress_timeout" if stage in {"perception", "planning"} and not action_id else "timeout"
    elif "graphrecursionerror" in lowered or "recursion limit" in lowered:
        reason = "recursion_limit"
    elif "validation" in lowered and "action" in lowered:
        reason = "action_validation_failed"
    elif any(token in lowered for token in ("provider", "http", "connection", "rate limit", "llm")):
        reason = "provider_error"
    elif error:
        reason = "execution_error"
    else:
        reason = "completed"

    return {
        "stop_reason": reason,
        "stop_detail": message.splitlines()[-1][:2000] if message else None,
        "last_stage": stage,
        "last_action_id": action_id,
        "last_event_type": last_event_type,
        "last_loop_iteration": iteration,
        "event_count": event_count,
    }


def _provider_fallback_error(last_state: dict[str, Any] | None) -> str | None:
    """Return a safe provider error when the graph ended on its fallback message."""

    messages = (last_state or {}).get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    latest = messages[-1]
    kwargs = latest.get("additional_kwargs") if isinstance(latest, dict) else getattr(latest, "additional_kwargs", None)
    if not isinstance(kwargs, dict) or kwargs.get("deerflow_error_fallback") is not True:
        return None
    error_type = str(kwargs.get("error_type") or "ProviderError")
    error_reason = str(kwargs.get("error_reason") or "transient")
    detail = str(kwargs.get("error_detail") or "provider request failed")
    return f"LLM provider fallback ({error_type}, {error_reason}): {detail}"


async def _run_one(
    row: dict[str, Any],
    *,
    state_root: Path,
    output_root: Path,
    model_name: str = "qwen3-32b",
    central_max_tokens: int | None = None,
    subagent_max_tokens: int | None = None,
    coder_max_tokens: int | None = 1024,
    repo_source: Path | None = None,
    docker_image: str | None = None,
    timeout_seconds: int = 900,
    no_progress_timeout_seconds: int = 180,
    external_grader_handoff: bool = True,
) -> dict[str, Any]:
    # Imports are delayed until the process has established the runtime paths.
    from langchain_core.messages import HumanMessage
    from deerflow.config.paths import get_paths
    from deerflow.config.app_config import get_app_config
    from deerflow.sp.runtime import make_sp_agent

    instance = row["instance_id"]
    thread_id = f"swebench-{_safe(instance)}"
    user_id = "default"
    paths = get_paths()
    # The graph resolves thread_data with the authenticated user scope.  Use
    # the same scope here; cloning into the legacy ``threads/`` directory makes
    # the mounted ``/mnt/user-data/workspace`` appear empty to the coder.
    paths.ensure_thread_dirs(thread_id, user_id=user_id)
    work_dir = paths.sandbox_work_dir(thread_id, user_id=user_id)
    repo_dir = _clone_instance(row, work_dir, repo_source=repo_source, docker_image=docker_image)
    # Make source-layout repositories importable without mutating them via an
    # editable install. Delegated shell commands inherit this environment, so
    # focused tests exercise the checked-out source against the isolated
    # benchmark dependencies instead of an unrelated globally installed copy.
    source_roots = [repo_dir / "src", repo_dir]
    existing_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [
            *(str(path) for path in source_roots if path.exists()),
            *([existing_pythonpath] if existing_pythonpath else []),
        ]
    )
    prompt = _prompt(row)
    task_contract = {
        "version": 1,
        "mode": "swebench",
        "authoritative": True,
        "immutable": True,
        "instance_id": instance,
        "original_issue": str(row["problem_statement"]),
        "fail_to_pass": _test_names(row.get("FAIL_TO_PASS"))[:50],
        "pass_to_pass": _test_names(row.get("PASS_TO_PASS"))[:50],
        "source_edit_only": True,
        "acceptance_policy": (
            "Require a real non-test source patch, clean diff/syntax checks, and "
            "successful FAIL_TO_PASS verification. PASS_TO_PASS must remain green "
            "when the local environment is runnable; blocked environments are "
            "reported for the external grader rather than treated as passed."
        ),
    }
    run_config = {
        "configurable": {
            "thread_id": thread_id,
            "model_name": model_name,
            # The current Qwen endpoint can hang on a long CentralAgent
            # reasoning turn when SP tools are bound. Benchmark runs need a
            # bounded tool decision; normal web runs keep their configured
            # thinking mode unchanged.
            "thinking_enabled": False,
            # Keep Qwen/vLLM's tested tool-capable reasoning path for SWE-bench.
            # The runtime override remains available for controlled comparisons,
            # but the current endpoint is more reliable with this enabled.
            "sp_subagent_thinking_enabled": False,
            "sp_central_timeout_seconds": 75,
            "sp_central_retry_timeout_seconds": 45,
            "sp_central_retry_max_tokens": 2048,
            "debug_trace_enabled": True,
            "max_concurrent_subagents": 3,
            "sp_subagent_max_tokens": subagent_max_tokens,
            "sp_subagent_max_tokens_by_role": {
                "coder": coder_max_tokens,
            },
            "sp_protect_test_files": True,
        },
        "context": {
            "user_id": user_id,
            "debug_trace_enabled": True,
            "sp_subagent_thinking_enabled": False,
            "sp_task_contract": task_contract,
        },
        "metadata": {
            "benchmark": "swebench-lite-pilot",
            "instance_id": instance,
            "patch_contract": "source_change_required",
            "debug_trace_enabled": True,
            "swe_task_contract": task_contract,
        },
        # Native-agent middleware consumes several graph super-steps per one
        # logical SP action.  Keep LangGraph's emergency ceiling above the
        # independent 20-action Central budget plus shutdown margin.
        "recursion_limit": 320,
    }
    if central_max_tokens is not None and central_max_tokens > 0:
        run_config["configurable"]["sp_central_max_tokens"] = central_max_tokens
    if timeout_seconds > 0:
        run_config["configurable"]["sp_subagent_timeout_seconds"] = max(30, timeout_seconds - 30)
    started = time.time()
    event_path = output_root / f"{_safe(instance)}.events.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    events = 0
    verification_completions = 0
    verification_environment_blocked = False
    verification_behavior_failed = False
    error: str | None = None
    last_state: dict[str, Any] = {}
    last_event_type: str | None = None
    stop_reason_override: str | None = None
    try:
        graph = make_sp_agent(run_config, app_config=get_app_config())
        async with asyncio.timeout(timeout_seconds):
            with event_path.open("w", encoding="utf-8") as stream:
                stream_iterator = graph.astream(
                    {"messages": [HumanMessage(content=prompt)]},
                    config=run_config,
                    context={
                        "thread_id": thread_id,
                        "run_id": thread_id,
                        "user_id": user_id,
                        "debug_trace_enabled": True,
                        "sp_subagent_thinking_enabled": False,
                        "sp_central_timeout_seconds": 75,
                        "sp_central_retry_timeout_seconds": 45,
                        "sp_central_retry_max_tokens": 2048,
                        "sp_subagent_max_tokens": subagent_max_tokens,
                        "sp_subagent_max_tokens_by_role": {
                            "coder": coder_max_tokens,
                        },
                        "sp_protect_test_files": True,
                        "sp_task_contract": task_contract,
                    },
                    # ``custom`` carries task_started/task_running/task_* events
                    # from delegated specialists. Without it, a healthy child
                    # agent can look idle while the parent waits synchronously.
                    stream_mode=["values", "custom"],
                ).__aiter__()
                try:
                    while True:
                        handoff_candidate = False
                        try:
                            if no_progress_timeout_seconds > 0:
                                event = await asyncio.wait_for(
                                    anext(stream_iterator), timeout=no_progress_timeout_seconds
                                )
                            else:
                                event = await anext(stream_iterator)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            stop_reason_override = "no_progress_timeout"
                            stage = last_state.get("sp_current_stage") or "unknown"
                            action_id = last_state.get("sp_current_action_id") or last_state.get("sp_last_action_id")
                            error = (
                                f"No observable graph progress for {no_progress_timeout_seconds}s; "
                                f"last_stage={stage}; last_action_id={action_id or 'none'}; events={events}"
                            )
                            break
                        event_for_log: Any = event
                        if isinstance(event, tuple) and len(event) == 2 and isinstance(event[0], str):
                            mode, payload = event
                            event_for_log = {"stream_mode": mode, "data": payload}
                            if mode == "custom" and isinstance(payload, dict):
                                environment_blocked, behavior_failed = _verification_event_flags(mode, payload)
                                verification_environment_blocked = (
                                    verification_environment_blocked or environment_blocked
                                )
                                verification_behavior_failed = verification_behavior_failed or behavior_failed
                                raw_type = payload.get("type")
                                if raw_type:
                                    last_event_type = str(raw_type)
                                if (
                                    raw_type == "task_completed"
                                    and str(payload.get("a2a_stage") or "") == "verification"
                                ):
                                    verification_completions += 1
                                handoff_candidate = _is_external_grader_handoff_event(
                                    mode,
                                    payload,
                                    enabled=external_grader_handoff,
                                    verification_completions=verification_completions,
                                    verification_environment_blocked=verification_environment_blocked,
                                    verification_behavior_failed=verification_behavior_failed,
                                )
                        elif isinstance(event, dict):
                            raw_type = event.get("event_type") or event.get("type")
                            if raw_type:
                                last_event_type = str(raw_type)
                            last_state = dict(event)
                        if isinstance(event_for_log, dict) and event_for_log.get("stream_mode") == "values":
                            payload = event_for_log.get("data")
                            if isinstance(payload, dict):
                                last_state = dict(payload)
                        stream.write(json.dumps(event_for_log, ensure_ascii=False, default=str) + "\n")
                        stream.flush()
                        events += 1
                        if handoff_candidate:
                            handoff_quality = _patch_quality(repo_dir)
                            if handoff_quality["valid_source_patch"]:
                                stop_reason_override = "external_grader_handoff"
                                break
                finally:
                    close_stream = getattr(stream_iterator, "aclose", None)
                    if close_stream is not None:
                        await close_stream()
    except Exception as exc:  # keep the batch alive and preserve the failure
        if error is None:
            error = "".join(traceback.format_exception(exc)).strip()
    if error is None:
        error = _provider_fallback_error(last_state)
    diff = _run(["git", "diff", "--binary"], cwd=repo_dir, timeout=120)
    status = _run(["git", "status", "--short"], cwd=repo_dir, timeout=120)
    quality = _patch_quality(repo_dir, run_error=error)
    benchmark_tests = _run_benchmark_tests(
        repo_dir,
        row,
        timeout=min(300, max(30, timeout_seconds // 3)),
    )
    verification_gate = _benchmark_handoff_eligibility(quality, benchmark_tests)
    diagnostics = _diagnose_stop_reason(
        error=error,
        last_state=last_state,
        event_count=events,
        last_event_type=last_event_type,
    )
    if stop_reason_override is not None:
        diagnostics["stop_reason"] = stop_reason_override
    if not error and stop_reason_override is None:
        diagnostics["stop_reason"] = "completed"
    if verification_gate["eligible"]:
        patch_status = "ready"
    elif benchmark_tests.get("status") == "failed" and quality.get("valid_source_patch"):
        patch_status = "needs_revision"
    else:
        patch_status = "partial" if quality.get("source_paths") else "none"
    result = {
        "instance_id": instance,
        "model_name_or_path": f"{model_name}+stackplanner2",
        "model_patch": diff,
        "status": status,
        "patch_quality": quality,
        "benchmark_tests": benchmark_tests,
        "verification_gate": verification_gate,
        "patch_status": patch_status,
        **diagnostics,
        "error": error,
        "events": events,
        "duration_seconds": round(time.time() - started, 2),
        "event_log": str(event_path),
    }
    (output_root / f"{_safe(instance)}.result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def main(args: argparse.Namespace) -> None:
    if args.hf_dataset:
        from datasets import load_dataset

        dataset = load_dataset(args.hf_dataset, split=args.hf_split)
        all_rows = [dict(row) for row in dataset]
    elif args.dataset:
        all_rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    else:
        raise ValueError("one of --dataset or --hf-dataset is required")
    if args.repos:
        requested_repos = set(args.repos)
        all_rows = [row for row in all_rows if row.get("repo") in requested_repos]
    rows = [
        _normalize_row(row)
        for row in all_rows[args.start_index : args.start_index + args.max_tasks]
    ]
    docker_image_map = _load_docker_image_map(
        Path(args.docker_image_manifest).resolve() if args.docker_image_manifest else None
    )
    state_root = Path(args.state_root).resolve()
    output_root = Path(args.output_root).resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DEER_FLOW_HOME", str(state_root))
    os.environ.setdefault("DEER_FLOW_PROJECT_ROOT", str(Path.cwd().resolve()))
    if args.llm_min_request_interval > 0:
        os.environ["DEER_FLOW_LLM_MIN_INTERVAL_SECONDS"] = str(args.llm_min_request_interval)
    summary_path = output_root / "summary.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    base_pythonpath = os.environ.get("PYTHONPATH")
    with summary_path.open("a", encoding="utf-8") as summary:
        for row in rows:
            result_path = output_root / f"{_safe(row['instance_id'])}.result.json"
            if args.resume and result_path.exists():
                print(json.dumps({"instance_id": row["instance_id"], "status": "resume_skipped"}), flush=True)
                continue
            if base_pythonpath is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = base_pythonpath
            try:
                result = await _run_one(
                    row,
                    state_root=state_root,
                    output_root=output_root,
                    model_name=args.model_name,
                    central_max_tokens=args.central_max_tokens,
                    subagent_max_tokens=args.subagent_max_tokens,
                    coder_max_tokens=args.coder_max_tokens,
                    repo_source=Path(args.repo_source).resolve() if args.repo_source else None,
                    docker_image=docker_image_map.get(row["instance_id"]),
                    timeout_seconds=args.timeout,
                    no_progress_timeout_seconds=args.no_progress_timeout,
                    external_grader_handoff=args.external_grader_handoff,
                )
            except Exception as exc:
                error = "".join(traceback.format_exception(exc)).strip()
                result = {
                    "instance_id": row["instance_id"],
                    "model_name_or_path": f"{args.model_name}+stackplanner2",
                    "model_patch": "",
                    "status": "",
                    "patch_quality": {"valid_source_patch": False, "gaps": ["case setup or execution failed"]},
                    "benchmark_tests": {"status": "not_run"},
                    "patch_status": "none",
                    "stop_reason": "case_error",
                    "stop_detail": str(exc)[:2000],
                    "error": error,
                    "events": 0,
                    "duration_seconds": 0.0,
                    "event_log": None,
                }
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                if base_pythonpath is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = base_pythonpath
            summary.write(json.dumps(result, ensure_ascii=False) + "\n")
            summary.flush()
            print(json.dumps({k: result[k] for k in ("instance_id", "error", "events", "duration_seconds", "patch_quality")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset")
    parser.add_argument("--hf-dataset", help="Load a SWE-bench dataset directly from Hugging Face.")
    parser.add_argument("--hf-split", default="test")
    parser.add_argument("--repo", dest="repos", action="append", help="Restrict an HF dataset run to one repository; repeat for multiple repositories.")
    parser.add_argument("--state-root", default=".deer-flow/swebench/runtime")
    parser.add_argument("--output-root", default=".deer-flow/swebench/results")
    parser.add_argument("--max-tasks", type=int, default=3)
    parser.add_argument(
        "--model-name",
        default="qwen3-32b",
        help="Configured StackPlanner model to use for the run (for example: deepseek-r1).",
    )
    parser.add_argument("--timeout", type=int, default=900, help="Per-instance wall-clock timeout in seconds.")
    parser.add_argument(
        "--no-progress-timeout",
        type=int,
        default=180,
        help="Abort when no graph event is observed for this many seconds; 0 disables the watchdog.",
    )
    parser.add_argument(
        "--external-grader-handoff",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After bounded completed verification stages and a valid source diff, stop SP and hand the patch to the official Docker grader.",
    )
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip instances that already have a result JSON in output-root.",
    )
    parser.add_argument(
        "--llm-min-request-interval",
        type=float,
        default=0.0,
        help="Optional process-wide pacing interval between LLM requests; useful for low-RPM providers.",
    )
    parser.add_argument(
        "--repo-source",
        help="Optional local git checkout to copy, avoiding a network clone.",
    )
    parser.add_argument(
        "--docker-image-manifest",
        help="Optional prepull status/audit JSON used to copy /testbed from local canonical images before any network fallback.",
    )
    parser.add_argument(
        "--central-max-tokens",
        type=int,
        default=1024,
        help="Per-call CentralAgent action cap; truncated action JSON receives one bounded 2048-token recovery.",
    )
    parser.add_argument(
        "--subagent-max-tokens",
        type=int,
        default=512,
        help="Default per-call completion cap for delegated SP subagents; 0 keeps the model default.",
    )
    parser.add_argument(
        "--coder-max-tokens",
        type=int,
        default=1024,
        help="Per-call completion cap for coder tool actions; longer than Central because file-tool JSON can exceed 512 tokens.",
    )
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
