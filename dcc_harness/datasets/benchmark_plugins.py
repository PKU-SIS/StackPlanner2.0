"""Dataset adapters for native SP configuration, inputs, and offline scoring.

Plugins are trusted Python extensions, loaded explicitly for a single run.
They supply public inputs and native capabilities, never an agent executor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from dcc_harness.evaluation.offline import EvaluationContext

from deerflow.config.app_config import AppConfig
from deerflow.config.tool_config import ToolConfig

from dcc_harness.data import InputFile, PublicTask, Task

from .benchmarks import _prepare_cuad_task, _prepare_widesearch_task


@dataclass(frozen=True)
class BenchmarkPlugin:
    name: str
    prepare_task: Callable[[PublicTask], str]
    prepare_inputs: Callable[[PublicTask, str], dict[str, Any]]
    skills_path: Path
    tools: tuple[ToolConfig, ...] = ()
    parse_answer: Callable[[PublicTask, str], Any] | None = None
    score_task: Callable[..., dict[str, Any]] | None = None
    aggregate: Callable[..., dict[str, Any]] | None = None
    validate_dataset: Callable[[list[Task]], None] | None = None
    setup_evaluation: Callable[["EvaluationContext"], None] | None = None
    evaluation_version: str = "1"
    evaluation_resources: tuple[Path, ...] = ()
    requires_judge: bool = False
    supports_exact_proxy: bool = False


def _no_inputs(task: PublicTask, thread_id: str) -> dict[str, Any]:
    return {}


def stage_files(
    task: PublicTask, thread_id: str, *, document_name: str = "document.txt"
) -> dict[str, Any]:
    """Stage only public UTF-8 bodies in the native per-user/thread uploads area."""
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    files = list(task.files)
    if task.document:
        files.insert(0, InputFile(document_name, task.document))
    if len({item.name for item in files}) != len(files):
        raise ValueError("duplicate public input filename")
    if not files:
        return {}
    root = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
    root.mkdir(parents=True, exist_ok=True)
    uploads = []
    for item in files:
        content = item.text.encode("utf-8")
        with (root / item.name).open("xb") as handle:
            handle.write(content)
        uploads.append(
            {
                "name": item.name,
                "path": f"/mnt/user-data/uploads/{item.name}",
                "size": len(content),
            }
        )
    return {"uploaded_files": uploads}


def _cuad_inputs(task: PublicTask, thread_id: str) -> dict[str, Any]:
    return stage_files(task, thread_id, document_name="contract.txt")


def build_inputs(
    plugin: BenchmarkPlugin, public: PublicTask, thread_id: str
) -> dict[str, Any]:
    """Do not permit a plugin input builder to override native execution state."""
    from langchain_core.messages import HumanMessage
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    prompt = plugin.prepare_task(public)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("plugin must return nonempty public instructions")
    prepared = plugin.prepare_inputs(public, thread_id)
    if not isinstance(prepared, dict) or set(prepared) - {"uploaded_files"}:
        raise ValueError("plugin input builder may only return uploaded_files")
    root = (
        get_paths()
        .sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
        .resolve()
    )
    paths = []
    uploads = prepared.get("uploaded_files", [])
    if not isinstance(uploads, list):
        raise ValueError("uploaded_files must be a list")
    for item in uploads:
        if not isinstance(item, dict) or set(item) != {"name", "path", "size"}:
            raise ValueError("upload descriptor requires exactly name/path/size")
        InputFile(item["name"], "")
        path = root / item["name"]
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["size"]
        ):
            raise ValueError("uploaded file is missing or its size differs")
        if item["path"] != f"/mnt/user-data/uploads/{item['name']}":
            raise ValueError("uploaded file must use its public virtual path")
        if item["path"] in paths:
            raise ValueError("duplicate uploaded file")
        paths.append(item["path"])
    if paths:
        prompt += (
            "\n\nPublic input files (read the complete source as needed):\n"
            + "\n".join(paths)
        )
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12_000:
        raise ValueError(
            "benchmark instructions must be 1..12000 characters; put documents in files"
        )
    return {
        "messages": [HumanMessage(content=prompt, id=f"input-{thread_id}")],
        **prepared,
        "sp_task_contract": {
            "version": 1,
            "mode": "benchmark",
            "authoritative": True,
            "immutable": True,
            "original_issue": prompt,
        },
    }


def _parse_answer(task: PublicTask, text: str) -> Any:
    from dcc_harness.evaluation.benchmarks import parse_answer

    return parse_answer(task, text)


def _score_task(task: Task, text: str, context=None) -> dict[str, Any]:
    from dcc_harness.evaluation.benchmarks import score_task

    return score_task(task, text, judge=context.judge if context else None)


def _cuad_aggregate(tasks: list[Task], rows: list[dict], context) -> dict:
    from dcc_harness.metrics import evaluate_cuad
    from dcc_harness.evaluation.offline import macro_average

    result = evaluate_cuad(
        {t.id: t.gold for t in tasks}, {r["task_id"]: r["answer"] for r in rows}
    )
    result["task_macro"] = macro_average(
        rows, ("f1", "precision", "recall", "no_answer_correct")
    )
    return result


def _wide_aggregate(tasks: list[Task], rows: list[dict], context) -> dict:
    from dcc_harness.evaluation.offline import macro_average

    result = macro_average(
        rows,
        tuple(
            f"{metric}_by_{unit}"
            for unit in ("row", "item")
            for metric in ("precision", "recall", "f1")
        )
        + ("score",),
    )
    result["evaluation_protocol"] = (
        "strict_exact_proxy"
        if context.exact_proxy
        else "official_protocol_custom_judge"
    )
    return result


def dataset_plugins(specs: tuple[str, ...] = ()) -> dict[str, BenchmarkPlugin]:
    """Build a fresh registry; specs are ``module:plugin_or_zero_arg_factory``."""
    from .clause import create_plugin as clause_plugin

    skills = Path(__file__).resolve().parents[2] / "configs" / "stackplanner_skills"
    registry = {
        "clause": clause_plugin(),
        "cuad": BenchmarkPlugin(
            "cuad",
            _prepare_cuad_task,
            _cuad_inputs,
            skills / "cuad",
            parse_answer=_parse_answer,
            score_task=_score_task,
            aggregate=_cuad_aggregate,
            evaluation_version="cuad-span-v1",
        ),
        "widesearch": BenchmarkPlugin(
            "widesearch",
            _prepare_widesearch_task,
            _no_inputs,
            skills / "widesearch",
            tools=(
                ToolConfig(
                    name="web_search",
                    group="web",
                    use="dcc_harness.evaluation.benchmark_tools:web_search",
                ),
                ToolConfig(
                    name="web_fetch",
                    group="web",
                    use="dcc_harness.evaluation.benchmark_tools:web_fetch",
                ),
            ),
            parse_answer=_parse_answer,
            score_task=_score_task,
            aggregate=_wide_aggregate,
            requires_judge=True,
            supports_exact_proxy=True,
            evaluation_version="widesearch-v1",
        ),
    }
    for spec in specs:
        module_name, separator, attribute = spec.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError("dataset plugin must use module:attribute")
        candidate = getattr(import_module(module_name), attribute)
        plugin = candidate() if callable(candidate) else candidate
        if not isinstance(plugin, BenchmarkPlugin) or not plugin.name.strip():
            raise ValueError(f"invalid dataset plugin: {spec}")
        if plugin.name in registry:
            raise ValueError(f"duplicate dataset plugin: {plugin.name}")
        registry[plugin.name] = plugin
    return registry


def resolve_dataset_plugin(name: str, specs: tuple[str, ...] = ()) -> BenchmarkPlugin:
    registry = dataset_plugins(specs)
    try:
        return registry[name]
    except KeyError:
        raise ValueError(f"unknown dataset plugin: {name}") from None


def configure_dataset_app(app: AppConfig, plugin: BenchmarkPlugin) -> AppConfig:
    """Return task-local native config; callers must use a unique run user_id.

    Native SP's user storage cache is keyed by user_id, so reusing a user's
    storage across dataset configs would retain the previous skill root.
    """
    payload = app.model_dump(mode="python")
    names: set[str] = set()
    for tool in [*app.tools, *plugin.tools]:
        if tool.name in names:
            raise ValueError(f"duplicate tool: {tool.name}")
        names.add(tool.name)
    payload["tools"].extend(tool.model_dump(mode="python") for tool in plugin.tools)
    groups = {group["name"] for group in payload["tool_groups"]}
    for tool in plugin.tools:
        if tool.group not in groups:
            payload["tool_groups"].append({"name": tool.group})
            groups.add(tool.group)
    payload["skills"]["path"] = str(Path(plugin.skills_path).resolve())
    payload["skills"]["use"] = (
        "deerflow.skills.storage.local_skill_storage:LocalSkillStorage"
    )
    return AppConfig.model_validate(payload)
