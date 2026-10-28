"""Dataset adapters for native SP configuration, inputs, and offline scoring.

Plugins are trusted Python extensions, loaded explicitly for a single run.
They supply public inputs and native capabilities, never an agent executor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from deerflow.config.app_config import AppConfig
from deerflow.config.tool_config import ToolConfig

from .benchmarks import Task, _prepare_cuad_task, _prepare_widesearch_task


@dataclass(frozen=True)
class BenchmarkPlugin:
    name: str
    prepare_task: Callable[[Task], str]
    prepare_inputs: Callable[[Task, str], dict[str, Any]]
    skills_path: Path
    tools: tuple[ToolConfig, ...] = ()
    parse_answer: Callable[[Task, str], Any] | None = None
    score_task: Callable[..., dict[str, Any]] | None = None


def _no_inputs(task: Task, thread_id: str) -> dict[str, Any]:
    return {}


def _cuad_inputs(task: Task, thread_id: str) -> dict[str, Any]:
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import get_effective_user_id

    upload = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id()) / "contract.txt"
    upload.parent.mkdir(parents=True, exist_ok=True)
    with upload.open("x", encoding="utf-8") as handle:
        handle.write(task.document)
    return {"uploaded_files": [{"name": "contract.txt", "path": "/mnt/user-data/uploads/contract.txt", "size": upload.stat().st_size}]}


def _parse_answer(task: Task, text: str) -> Any:
    from dcc_harness.evaluation.benchmarks import parse_answer

    return parse_answer(task, text)


def _score_task(task: Task, text: str, judge=None) -> dict[str, Any]:
    from dcc_harness.evaluation.benchmarks import score_task

    return score_task(task, text, judge)


def dataset_plugins(specs: tuple[str, ...] = ()) -> dict[str, BenchmarkPlugin]:
    """Build a fresh registry; specs are ``module:plugin_or_zero_arg_factory``."""
    skills = Path(__file__).resolve().parents[2] / "configs" / "stackplanner_skills"
    registry = {
        "cuad": BenchmarkPlugin("cuad", _prepare_cuad_task, _cuad_inputs, skills / "cuad",
                                parse_answer=_parse_answer, score_task=_score_task),
        "widesearch": BenchmarkPlugin("widesearch", _prepare_widesearch_task, _no_inputs, skills / "widesearch",
            tools=(
                ToolConfig(name="web_search", group="web", use="dcc_harness.evaluation.benchmark_tools:web_search"),
                ToolConfig(name="web_fetch", group="web", use="dcc_harness.evaluation.benchmark_tools:web_fetch"),
            ), parse_answer=_parse_answer, score_task=_score_task),
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
    payload["skills"]["use"] = "deerflow.skills.storage.local_skill_storage:LocalSkillStorage"
    return AppConfig.model_validate(payload)
