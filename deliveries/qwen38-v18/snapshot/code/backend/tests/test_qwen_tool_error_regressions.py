"""Replay saved Qwen tool mistakes; compatibility must retain native gates."""

import asyncio
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.search import find_grep_matches
from deerflow.sandbox.tools import grep_tool, ls_tool, read_file_tool, validate_local_tool_path
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.read_material import build_read_material_tool
from deerflow.sp.report.store import ReportPaths
from deerflow.sp.report.submit_outline import build_submit_outline_tool
from deerflow.sp.report.tree import validate_tree_with_diagnostics

EXPERIMENT = Path(__file__).resolve().parents[3]
CALLS = json.loads((EXPERIMENT / "local-storage/replay/failed-calls.json").read_text())
BASELINE = EXPERIMENT.parent / "qwen38-27b-adapter-v3-20261117"
SAVED_OUTPUTS = BASELINE / "runs/band10000-case17-outline-selected/runtime/users/full-eval/threads/full-17/user-data/outputs"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    roots = {}
    for name in ("workspace", "uploads", "outputs"):
        directory = tmp_path / name
        directory.mkdir()
        roots[name + "_path"] = str(directory)
    result = SimpleNamespace(state={"sandbox": {"sandbox_id": "local"}, "thread_data": roots}, context={"thread_id": "thread-1"})
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox(id="local"))
    async def initialized_async(runtime):
        return LocalSandbox(id="local")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized_async", initialized_async)
    monkeypatch.setattr("deerflow.sandbox.tools.get_app_config", lambda: SimpleNamespace(get_tool_config=lambda name: None))
    return result


def material_fixture(runtime):
    """Relocate only registered bytes; never write into the completed run."""
    paths = ReportPaths(Path(runtime.state["thread_data"]["outputs_path"]), "r1")
    ledger = ReportLedger.create(paths, request_text="Research")
    saved = json.loads((SAVED_OUTPUTS / "report/r1/ledger.json").read_text())
    material_ids = {Path(c["args"]["path"]).stem for c in CALLS if c["name"] == "read_file"}
    ledger.payload["sources"] = deepcopy(saved["sources"])
    for row in saved["materials"]:
        if row["material_id"] not in material_ids:
            continue
        row = deepcopy(row)
        old_path = Path(row["path"])
        target = paths.materials / old_path.relative_to(SAVED_OUTPUTS / "report/r1/materials")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_path, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]
        row["path"] = str(target)
        ledger.payload["materials"].append(row)
    assert len(ledger.materials()) == 7
    ledger.save()
    return ledger


@pytest.mark.parametrize("call", [c for c in CALLS if c["name"] == "read_file"], ids=lambda c: Path(c["args"]["path"]).stem)
def test_saved_material_failures_use_native_identity_reader(runtime, call):
    ledger = material_fixture(runtime)
    before = ledger.paths.ledger.read_bytes()
    result = json.loads(read_file_tool.func(runtime=runtime, **call["args"]))
    args = call["args"]
    expected = json.loads(build_read_material_tool(paths=ledger.paths).invoke({
        "material_id": Path(args["path"]).stem,
        "start_line": args.get("start_line") or 1,
        "end_line": args.get("end_line"),
    }))
    assert result == expected
    assert "error" not in result
    assert ledger.paths.ledger.read_bytes() == before
    assert not (ledger.paths.materials / Path(args["path"]).name).exists()


def test_saved_root_ls_is_virtual_mount_index_only(runtime, tmp_path, monkeypatch):
    (tmp_path / "secret-host-parent-file").write_text("private")
    sandbox = LocalSandbox(id="local")
    monkeypatch.setattr(sandbox, "list_dir", lambda path: pytest.fail("must not list a host parent"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: sandbox)
    call = next(c for c in CALLS if c["name"] == "ls")
    result = ls_tool.func(runtime=runtime, **call["args"])
    assert result.splitlines() == ["/mnt/user-data/workspace/", "/mnt/user-data/uploads/", "/mnt/user-data/outputs/"]
    assert ls_tool.func(runtime, "mounts", "/mnt/user-data/") == result
    assert "Permission denied" in ls_tool.func(runtime, "forbidden", "/mnt/user-data/../")
    assert "Permission denied" in ls_tool.func(runtime, "forbidden", "/mnt/user-data/private")
    # The general filesystem permission gate remains unchanged.
    with pytest.raises(PermissionError):
        validate_local_tool_path("/mnt/user-data", runtime.state["thread_data"], read_only=True)


def test_saved_writer_grep_accepts_only_selected_file(runtime):
    call = next(c for c in CALLS if c["name"] == "grep")
    rel = Path(call["args"]["path"]).relative_to("/mnt/user-data/outputs")
    target = Path(runtime.state["thread_data"]["outputs_path"]) / rel
    target.parent.mkdir(parents=True)
    shutil.copyfile(SAVED_OUTPUTS / rel, target)
    (target.parent / "unselected.md").write_text("Sibling must not appear\n")
    result = grep_tool.func(runtime=runtime, **call["args"])
    assert not result.startswith("Error:")
    assert "unselected" not in result
    assert str(target.parent) not in result


def outline_fixture(runtime):
    paths = ReportPaths(Path(runtime.state["thread_data"]["outputs_path"]), "r1")
    ledger = ReportLedger.create(paths, request_text="Research", inputs=[{"text": "Research"}])
    proposal = {
        "report_title": "Research", "requirements": [{"id": "R1", "input_id": "u1", "text": "Research", "hard": True}],
        "nodes": [{"node_id": "n1", "parent_id": None, "kind": "section", "title": "Theory"}],
        "removed_node_ids": [], "write_requests": [],
    }
    delivery = paths.directory / "outline/delivery-a1.json"
    tool = build_submit_outline_tool(paths=paths, delivery_path=delivery)
    assert json.loads(tool.invoke({"tree": proposal}))["validation_ok"]
    return ledger, proposal, delivery, tool


def test_saved_partial_confirmation_does_not_destroy_valid_proposal(runtime):
    ledger, proposal, delivery, tool = outline_fixture(runtime)
    call = next(c for c in CALLS if c["name"] == "report_submit_outline")
    ledger_before, delivery_before = ledger.paths.ledger.read_bytes(), delivery.read_bytes()
    result = json.loads(tool.invoke(call["args"]))
    assert result["error"] == "invalid_outline_tree"
    assert result["validation_ok"] is False
    assert result["previous_proposal_preserved"] is True
    assert result["last_valid_tree_path"].endswith("/outline/delivery-a1.json")
    assert json.loads(delivery.with_suffix(".invalid.json").read_text()) == call["args"]["tree"]
    assert delivery.read_bytes() == delivery_before
    assert ledger.paths.ledger.read_bytes() == ledger_before
    validate_tree_with_diagnostics(json.loads(delivery.read_text()), ledger)
    with pytest.raises(ReportValidationError):
        validate_tree_with_diagnostics(call["args"]["tree"], ledger)
    # A subsequent genuine complete revision can still replace the proposal.
    proposal["report_title"] = "Revised"
    assert json.loads(tool.invoke({"tree": proposal}))["validation_ok"]
    assert json.loads(delivery.read_text())["report_title"] == "Revised"
    assert ReportLedger.load(ledger.paths).payload["tree"] is None


def test_old_proposal_is_revalidated_against_current_ledger(runtime):
    ledger, proposal, delivery, tool = outline_fixture(runtime)
    ledger.payload["nodes"]["n2"] = {"node_id": "n2", "parent_id": None, "kind": "section", "title": "New accepted node"}
    ledger.save()
    with pytest.raises(ReportValidationError):
        validate_tree_with_diagnostics(proposal, ReportLedger.load(ledger.paths))
    result = json.loads(tool.invoke({"tree": {"report_title": "Incomplete"}}))
    assert result["validation_ok"] is False
    assert not result.get("previous_proposal_preserved")
    assert json.loads(delivery.read_text()) == {"report_title": "Incomplete"}


@pytest.mark.parametrize("fault", ["hash", "unknown", "duplicate", "escape", "other_report", "report_symlink", "ledger_symlink", "materials_symlink"])
def test_material_alias_fails_closed(runtime, tmp_path, fault):
    ledger = material_fixture(runtime)
    material = ledger.material("m22")
    path = "/mnt/user-data/outputs/report/r1/materials/m22.md"
    secret = tmp_path / "private.txt"
    secret.write_text("private bytes must not leak")
    if fault == "hash":
        Path(material["path"]).write_text("tampered bytes")
    elif fault == "unknown":
        path = path.replace("m22.md", "m999.md")
    elif fault == "duplicate":
        ledger.payload["materials"].append(deepcopy(material))
    elif fault == "escape":
        material["path"] = str(secret)
        material["sha256"] = hashlib.sha256(secret.read_bytes()).hexdigest()
    elif fault == "other_report":
        material["path"] = str(ledger.paths.outputs_path / "report/r2/materials/private.txt")
        Path(material["path"]).parent.mkdir(parents=True)
        Path(material["path"]).write_bytes(secret.read_bytes())
        material["sha256"] = hashlib.sha256(secret.read_bytes()).hexdigest()
    ledger.save()
    if fault in ("report_symlink", "materials_symlink"):
        location = ledger.paths.directory if fault == "report_symlink" else ledger.paths.materials
        moved = location.with_name(location.name + "-moved")
        location.rename(moved)
        location.symlink_to(moved, target_is_directory=True)
    elif fault == "ledger_symlink":
        ledger.paths.ledger.unlink()
        ledger.paths.ledger.symlink_to(secret)
    result = read_file_tool.func(runtime, "evidence", path)
    if result.startswith("{"):
        assert json.loads(result).get("error")
    else:
        assert result.startswith("Error:")
    assert "private bytes" not in result
    assert "tampered bytes" not in result


def test_real_file_has_priority_and_ordinary_missing_file_stays_missing(runtime):
    ledger = material_fixture(runtime)
    alias = ledger.paths.materials / "m22.md"
    alias.write_text("existing actual file\n")
    assert read_file_tool.func(runtime, "existing", "/mnt/user-data/outputs/report/r1/materials/m22.md") == "existing actual file\n"
    assert read_file_tool.func(runtime, "missing", "/mnt/user-data/workspace/m22.md").startswith("Error: File not found:")


def test_material_alias_ranged_read_keeps_native_numbers_and_continuation(runtime):
    ledger = material_fixture(runtime)
    result = json.loads(read_file_tool.func(runtime, "range", "/mnt/user-data/outputs/report/r1/materials/m22.md", start_line=2, end_line=3, line_numbers=True))
    assert result == json.loads(build_read_material_tool(paths=ledger.paths).invoke({"material_id": "m22", "start_line": 2, "end_line": 3}))
    assert result["content"].startswith("2:")


def test_file_grep_keeps_filters_limits_and_sibling_isolation(tmp_path):
    selected = tmp_path / "selected.md"
    selected.write_text("TODO (a+b)\ntodo lower\nTODO last\n" + "TODO " * 1000 + "\n")
    (tmp_path / "sibling.md").write_text("TODO sibling\n")
    matches, truncated = find_grep_matches(selected, "TODO", case_sensitive=True, max_results=1)
    assert len(matches) == 1 and truncated and matches[0].line_number == 1
    matches, truncated = find_grep_matches(selected, "(a+b)", literal=True)
    assert len(matches) == 1 and not truncated
    assert find_grep_matches(selected, "TODO", glob_pattern="**/*.txt")[0] == []
    assert find_grep_matches(selected, "TODO", max_file_size=2)[0] == []
    matches, _ = find_grep_matches(selected, "TODO", glob_pattern="**/*.md", case_sensitive=True)
    assert [m.line_number for m in matches] == [1, 3]
    assert all(m.path == str(selected) for m in matches)
    selected.write_bytes(b"\0TODO")
    assert find_grep_matches(selected, "TODO")[0] == []
    link = tmp_path / "link.md"
    link.symlink_to(selected)
    assert find_grep_matches(link, "TODO")[0] == []


def test_file_grep_cannot_escape_thread_via_symlink(runtime, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("SECRET")
    workspace = Path(runtime.state["thread_data"]["workspace_path"])
    (workspace / "link.md").symlink_to(outside)
    result = grep_tool.func(runtime, "search", "SECRET", "/mnt/user-data/workspace/link.md")
    assert result.startswith("Error: Permission denied")
    assert "SECRET" not in result


def test_async_tools_use_same_fixes(runtime):
    material_fixture(runtime)
    result = asyncio.run(read_file_tool.coroutine(runtime, "read", "/mnt/user-data/outputs/report/r1/materials/m22.md"))
    assert json.loads(result)["material_id"] == "m22"
    assert not asyncio.run(ls_tool.coroutine(runtime, "mounts", "/mnt/user-data")).startswith("Error:")
    draft = Path(runtime.state["thread_data"]["workspace_path"]) / "draft.md"
    draft.write_text("hello TODO\n")
    assert "hello TODO" in asyncio.run(grep_tool.coroutine(runtime, "search", "TODO", "/mnt/user-data/workspace/draft.md"))
