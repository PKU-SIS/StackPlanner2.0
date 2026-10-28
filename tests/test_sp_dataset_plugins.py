from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from uuid import uuid4

import pytest

from dcc_harness.datasets import benchmark_plugins as plugins
from dcc_harness.datasets.benchmarks import Task, prepare_task

ROOT = Path(__file__).resolve().parents[1]


def sample(name="widesearch"):
    return Task(id="public-id", benchmark=name, split="validation", prompt="Public question",
                document="Public contract" if name == "cuad" else "", gold=["PRIVATE_GOLD"],
                metadata={"private": "PRIVATE_META", "answer_columns": ["name"]})


def base_app():
    from deerflow.config.app_config import AppConfig

    return AppConfig.from_file(str(ROOT / "configs/stackplanner_benchmarks.yaml"))


def test_unknown_plugin_and_duplicate_names_fail_closed(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="unknown dataset plugin"):
        plugins.resolve_dataset_plugin("missing")
    extension = plugins.BenchmarkPlugin("third", lambda task: task.prompt,
        lambda task, thread_id, **kwargs: {}, tmp_path)
    module = types.ModuleType("test_dataset_extension")
    module.plugin = extension
    monkeypatch.setitem(sys.modules, module.__name__, module)
    resolved = plugins.resolve_dataset_plugin("third", ("test_dataset_extension:plugin",))
    assert resolved.prepare_task(sample("third")) == "Public question"
    with pytest.raises(ValueError, match="unknown dataset plugin"):
        plugins.resolve_dataset_plugin("third")
    with pytest.raises(ValueError, match="duplicate dataset plugin"):
        plugins.dataset_plugins(("test_dataset_extension:plugin", "test_dataset_extension:plugin"))
    with pytest.raises(ValueError, match="module:attribute"):
        plugins.dataset_plugins(("not-a-spec",))


def test_task_specific_tools_and_skills_do_not_modify_base_app():
    base = base_app()
    before = base.model_dump(mode="python")
    cuad = plugins.configure_dataset_app(base, plugins.resolve_dataset_plugin("cuad"))
    web = plugins.configure_dataset_app(base, plugins.resolve_dataset_plugin("widesearch"))
    assert "web_search" not in {tool.name for tool in cuad.tools}
    assert {"web_search", "web_fetch"} <= {tool.name for tool in web.tools}
    assert cuad.skills.path != web.skills.path
    assert base.model_dump(mode="python") == before
    with pytest.raises(ValueError, match="duplicate tool"):
        plugins.configure_dataset_app(web, plugins.resolve_dataset_plugin("widesearch"))


def test_third_plugin_supplies_native_tools_skills_inputs_and_scoring(monkeypatch, tmp_path):
    from deerflow.config.tool_config import ToolConfig
    from deerflow.sp.central.runtime_context import build_sp_central_runtime_context
    from deerflow.sp.runtime import _apply_task_tool_policy
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents import get_subagent_config
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor
    from deerflow.tools import get_available_tools
    from langchain_core.tools import tool

    @tool
    def third_lookup(query: str) -> str:
        """Look up a public third-dataset value."""
        return "PUBLIC_VALUE:" + query

    skill_dir = tmp_path / "public" / "third"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: third\ndescription: Process the third dataset.\n---\nRead public records with third_lookup.\n",
        encoding="utf-8",
    )
    module = types.ModuleType("test_third_native_plugin")
    module.third_lookup = third_lookup
    module.plugin = plugins.BenchmarkPlugin(
        name="third", prepare_task=lambda task: task.prompt,
        prepare_inputs=lambda task, thread_id: {"public_record": task.document},
        skills_path=tmp_path,
        tools=(ToolConfig(name="third_lookup", group="third", use="test_third_native_plugin:third_lookup"),),
        parse_answer=lambda task, text: text.strip(),
        score_task=lambda task, text, judge=None: {"exact": float(text == task.gold)},
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    plugin = plugins.resolve_dataset_plugin("third", ("test_third_native_plugin:plugin",))
    task = Task(id="third-record", benchmark="third", split="test", prompt="Public question",
                document="PUBLIC_RECORD", gold="PRIVATE_GOLD")
    app = plugins.configure_dataset_app(base_app(), plugin)
    tools = {item.name: item for item in get_available_tools(app_config=app, include_mcp=False)}
    assert tools["third_lookup"].invoke({"query": "x"}) == "PUBLIC_VALUE:x"
    assert "web_search" not in tools
    delegation = SPSubagentTask(action_id="third-delegation", subagent_type="researcher",
        task="Read public record", description="test", metadata={"tool_names": ["third_lookup"]})
    role = get_subagent_config("sp-researcher", app_config=app)
    assert _apply_task_tool_policy(role, delegation, available_tools=list(tools.values())).tools == ["third_lookup"]
    assert plugin.prepare_inputs(task, "thread-third") == {"public_record": "PUBLIC_RECORD"}
    assert plugin.parse_answer(task, " prediction ") == "prediction"
    assert plugin.score_task(task, "PRIVATE_GOLD") == {"exact": 1.0}
    user_id = "third-test-" + uuid4().hex
    central = build_sp_central_runtime_context(app, user_id=user_id)
    assert central.available_skill_names == frozenset({"third"})
    executor = SubagentExecutor(config=SubagentConfig(name="test", description="test", skills=["third"]),
        tools=list(tools.values()), app_config=app, user_id=user_id, user_scoped_skills=True)
    skills = asyncio.run(executor._load_skills())
    messages = asyncio.run(executor._load_skill_messages(skills))
    assert len(messages) == 1 and "Read public records with third_lookup." in messages[0].content
    assert "PRIVATE_" not in messages[0].content + central.system_prompt_section


def test_duplicate_tools_within_plugin_are_rejected(tmp_path):
    from deerflow.config.tool_config import ToolConfig

    duplicate = ToolConfig(name="extra_tool", group="extra", use="unused:tool")
    plugin = plugins.BenchmarkPlugin("third", lambda task: task.prompt,
        lambda task, thread_id: {}, tmp_path, tools=(duplicate, duplicate))
    with pytest.raises(ValueError, match="duplicate tool: extra_tool"):
        plugins.configure_dataset_app(base_app(), plugin)


@pytest.mark.parametrize("name", ["cuad", "widesearch"])
def test_public_payload_does_not_expose_labels_or_arbitrary_metadata(name, monkeypatch, tmp_path):
    from deerflow.config import paths
    from deerflow.runtime.user_context import get_effective_user_id

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    plugin = plugins.resolve_dataset_plugin(name)
    task = sample(name)
    assert "PRIVATE_" not in prepare_task(task)
    inputs = plugin.prepare_inputs(task, "thread-public")
    assert "PRIVATE_" not in repr(inputs)
    if name == "cuad":
        upload = paths.get_paths().sandbox_uploads_dir("thread-public", user_id=get_effective_user_id()) / "contract.txt"
        assert upload.read_text(encoding="utf-8") == "Public contract"
        assert inputs["uploaded_files"][0]["path"] == "/mnt/user-data/uploads/contract.txt"
    else:
        assert inputs == {}


def test_cuad_uploaded_contract_is_readable_in_native_user_sandbox(monkeypatch, tmp_path):
    from deerflow.config.app_config import (
        pop_current_app_config,
        push_current_app_config,
    )
    from deerflow.config.paths import get_paths
    from deerflow.runtime.user_context import reset_current_user, set_current_user
    from deerflow.sandbox.local.local_sandbox_provider import LocalSandboxProvider

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    user_id = "contract-test-" + uuid4().hex
    thread_id = "contract-thread"
    plugin = plugins.resolve_dataset_plugin("cuad")
    push_current_app_config(plugins.configure_dataset_app(base_app(), plugin))
    token = set_current_user(types.SimpleNamespace(id=user_id))
    try:
        inputs = plugin.prepare_inputs(sample("cuad"), thread_id)
        provider = LocalSandboxProvider()
        sandbox_id = provider.acquire(thread_id, user_id=user_id)
        sandbox = provider.get(sandbox_id)
        assert sandbox.read_file(inputs["uploaded_files"][0]["path"]) == "Public contract"
        assert not (get_paths().sandbox_uploads_dir(thread_id) / "contract.txt").exists()
    finally:
        reset_current_user(token)
        pop_current_app_config()


def test_native_central_and_subagent_load_only_current_dataset_skill():
    from deerflow.sp.central.runtime_context import build_sp_central_runtime_context
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor

    for name, other in [("cuad", "widesearch"), ("widesearch", "cuad")]:
        app = plugins.configure_dataset_app(base_app(), plugins.resolve_dataset_plugin(name))
        user_id = "plugin-test-" + uuid4().hex
        context = build_sp_central_runtime_context(app, user_id=user_id)
        assert context.available_skill_names == frozenset({name})
        assert f'"name": "{name}"' in context.system_prompt_section
        assert f'"name": "{other}"' not in context.system_prompt_section
        config = SubagentConfig(name="test", description="test", skills=[name])
        executor = SubagentExecutor(config=config, tools=[], app_config=app,
                                    user_id=user_id, user_scoped_skills=True)
        skills = asyncio.run(executor._load_skills())
        messages = asyncio.run(executor._load_skill_messages(skills))
        assert [skill.name for skill in skills] == [name]
        assert len(messages) == 1 and f'<skill name="{name}">' in messages[0].content
        assert "PRIVATE_" not in messages[0].content


def test_plugin_tools_still_obey_native_role_allowlists():
    from deerflow.sp.runtime import _apply_task_tool_policy
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents import get_subagent_config
    from deerflow.tools import get_available_tools

    app = plugins.configure_dataset_app(base_app(), plugins.resolve_dataset_plugin("widesearch"))
    tools = get_available_tools(app_config=app, include_mcp=False)
    task = SPSubagentTask(action_id="test", subagent_type="reporter", task="Public task", description="test",
                          metadata={"tool_names": ["web_search"]})
    role = get_subagent_config("sp-reporter", app_config=app)
    with pytest.raises(ValueError, match="forbidden"):
        _apply_task_tool_policy(role, task, available_tools=tools)
    researcher = get_subagent_config("sp-researcher", app_config=app)
    assert _apply_task_tool_policy(researcher, task, available_tools=tools).tools == ["web_search"]
