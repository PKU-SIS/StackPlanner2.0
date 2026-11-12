"""Compact only model-visible duplication, retaining native contracts and reasoning."""

import copy
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def delegated_messages():
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

    original = "完整用户约束：仅审核分值矛盾；先研究后Reporter；不能引用外部资料。" * 25
    objective = "检索所有相关评分表，保留跨页原文和来源，按完整规则独立判断。" * 15
    task = SPSubagentTask("a", "researcher", objective, "研究", expected_output="完整研究证据、判断与证据缺口")
    task.context_refs.update(
        original_query=original,
        latest_user_input=original,
        audit_task_context="完整规则：不得修改case4/5判断口径。",
        audit_report_instructions="完整审核输出契约。",
        task_contract={"immutable": True, "mandatory": "真实调用者约束"},
        mandatory_requirements={"human_feedback": [{"content": "新增人工硬约束", "priority": "critical"}]},
        task_memory={
            "entries": [
                {"id": "user", "action": "user_request", "actor": "human", "content": original, "priority": "high"},
                {"id": "old", "action": "user_request", "actor": "human", "content": "以前的用户偏好不能删除", "priority": "high"},
                {"id": "recall", "action": "recall_memory", "actor": "memory_recaller", "content": "确有来源的历史偏好"},
                {"id": "current", "action": "delegate", "actor": "central", "content": "Delegate to researcher: " + objective},
            ]
        },
    )
    messages = [HumanMessage(render_sp_subagent_prompt(task), id="task"), AIMessage("", additional_kwargs={"reasoning": "PRIVATE_UNCHANGED", "reasoning_content": "PRIVATE_UNCHANGED"}, id="old-ai")]
    return task, messages


def payload(message):
    return json.loads(re.search(r"<sp-subagent-task>\s*(.*?)\s*</sp-subagent-task>", message.content, re.S)[1])


def test_delegation_compaction_keeps_full_contract_feedback_memory_ids_and_reasoning():
    from app.experiments.tender_audit.context_projection import project_delegation_messages

    task, messages = delegated_messages()
    original = copy.deepcopy(messages)
    before = payload(messages[0])
    projected, stats = project_delegation_messages(messages, action_id="a")
    assert stats["applied"] and len(projected[0].content) < len(messages[0].content)
    after = payload(projected[0])
    assert after["context_refs"]["latest_user_input"] == before["context_refs"]["latest_user_input"]
    assert "original_query" not in after["context_refs"]
    for key in ["audit_task_context", "audit_report_instructions", "task_contract", "mandatory_requirements"]:
        assert after["context_refs"][key] == before["context_refs"][key]
    assert after["task"] == before["task"] and after["expected_output"] == before["expected_output"]
    assert after["protocol"]["objective_ref"] == "task"
    assert [e["id"] for e in after["context_refs"]["task_memory"]["entries"]] == ["user", "old", "recall", "current"]
    assert after["context_refs"]["task_memory"]["entries"][1:3] == before["context_refs"]["task_memory"]["entries"][1:3]
    assert projected[1].model_dump() == original[1].model_dump()
    assert messages == original and task.task == before["task"]


@pytest.mark.parametrize("change", ["wrong_action", "different_original", "bad_json"])
def test_compaction_never_discards_different_or_untrusted_user_text(change):
    from app.experiments.tender_audit.context_projection import project_delegation_messages

    _, messages = delegated_messages()
    if change == "bad_json":
        messages[0] = HumanMessage("<sp-subagent-task>invalid</sp-subagent-task>")
    elif change == "different_original":
        value = payload(messages[0])
        value["context_refs"]["original_query"] = "此前的不同原始要求，必须保留"
        messages[0] = HumanMessage("<sp-subagent-task>" + json.dumps(value, ensure_ascii=False) + "</sp-subagent-task>")
    result, _ = project_delegation_messages(messages, action_id="other" if change == "wrong_action" else "a")
    if change == "different_original":
        assert payload(result[0])["context_refs"]["original_query"] == "此前的不同原始要求，必须保留"
    else:
        assert result == messages


def test_memory_profile_requests_only_additional_history_without_skipping_recall(tmp_path):
    from app.experiments.tender_audit.cases import AuditCase
    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.profile import AuditProfile
    from deerflow.sp.subagents import SPSubagentTask
    from deerflow.subagents.config import SubagentConfig

    profile = AuditProfile(AuditCase(9, "标书", "bound", ("规则",), "完整审核规则", "评分"), SimpleNamespace(), AuditTrace(tmp_path), tmp_path / "out")
    task = SPSubagentTask("a", "memory_recaller", "召回", "召回")
    original = SubagentConfig(name="sp-memory-recaller", description="recall", system_prompt="原生召回prompt")
    configured = profile.configure_subagent(task, original)
    assert configured.system_prompt.startswith("原生召回prompt")
    assert "无新增" in configured.system_prompt and "items" in configured.system_prompt
    assert "当前任务" in configured.system_prompt
    assert original.system_prompt == "原生召回prompt"


def test_skill_allowlist_is_opt_in_and_keeps_capability_metadata_consistent(monkeypatch):
    from deerflow.sp.central import runtime_context as module

    skills = [SimpleNamespace(name="video-generation", description="unrelated"), SimpleNamespace(name="stackplanner-reporting", description="report")]
    monkeypatch.setattr(module, "_load_profile", lambda *a, **kw: None)
    monkeypatch.setattr(module, "_load_soul", lambda *a, **kw: "native soul")
    monkeypatch.setattr(module, "_load_skills", lambda *a, **kw: (skills, frozenset(s.name for s in skills)))
    baseline = module.build_sp_central_runtime_context(SimpleNamespace())
    narrowed = module.build_sp_central_runtime_context(SimpleNamespace(), skill_names=("stackplanner-reporting",))
    assert "video-generation" in baseline.system_prompt_section
    assert "video-generation" not in narrowed.system_prompt_section
    assert narrowed.available_skill_names == frozenset({"stackplanner-reporting"})
    assert "native soul" in narrowed.system_prompt_section


def test_evidence_v2_keeps_every_field_and_body_with_one_shared_guide():
    from langchain_core.messages import ToolMessage

    from app.experiments.tender_audit.request_projection import project_evidence_messages

    body = "<table><tr><td rowspan='2'>完整原文40与43</td></tr></table>" * 100
    a = {"id": "a", "document_id": "d", "dataset_id": "bound", "document_keyword": "长标书名称" * 50, "content": body, "positions": [[1]], "flag": 0, "optional": "present"}
    b = {**a, "flag": False, "similarity": 0.123456789012345}
    b.pop("optional")
    values = [{"dataset_id": "bound", "chunks": [a], "chunk_count": 1}, {"dataset_id": "bound", "chunks": [b], "chunk_count": 1}]
    messages = [ToolMessage(json.dumps(v, ensure_ascii=False), name="local_search_tool", tool_call_id=str(i)) for i, v in enumerate(values)]
    original = copy.deepcopy(messages)
    packed, stats = project_evidence_messages(messages, dataset_id="bound")
    packet = json.loads(packed[-1].content)
    assert stats["policy"] == "same_request_lossless_sources_v2" and stats["applied"]
    assert packet["audit_shared_source_metadata"]["document_keyword"] == a["document_keyword"]
    assert sum("audit_source_reference_instructions" in json.loads(m.content) for m in packed) == 1
    for message, value in zip(packed, values):
        result = json.loads(message.content)
        restored = []
        for hit in result["chunks"]:
            ref = hit if isinstance(hit, str) else hit["ref"]
            chunk = {**packet["audit_shared_source_metadata"], **packet["audit_shared_sources"][ref]}
            if isinstance(hit, dict):
                for k in hit.get("drop", []):
                    chunk.pop(k)
                chunk.update(hit.get("set", {}))
            restored.append(chunk)
        assert json.dumps(restored, sort_keys=True, ensure_ascii=False) == json.dumps(value["chunks"], sort_keys=True, ensure_ascii=False)
    assert messages == original


def test_timeout_multiplier_scales_real_client_and_subagent_caps_without_global_mutation(monkeypatch):
    from app.experiments.tender_audit.runner import experiment_config, runtime_model_settings
    from app.experiments.tender_audit.timing import scaled_timeout, timeout_multiplier_environment
    from deerflow.config.app_config import AppConfig
    from deerflow.config.model_config import ModelConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.subagents.registry import get_subagent_config

    original = AppConfig(models=[ModelConfig(name="qwen-test", use="deerflow.models.vllm_provider:VllmChatModel", model="local", api_key="dummy", base_url="https://model.test/v1")], sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    baseline = experiment_config("qwen-test")
    baseline_stream = runtime_model_settings(baseline, thinking=True)["stream_chunk_timeout_seconds"]
    with timeout_multiplier_environment(2):
        config = experiment_config("qwen-test")
        settings = runtime_model_settings(config, thinking=True)
        assert settings["http_timeout_seconds"] == 240
        assert settings["stream_chunk_timeout_seconds"] == baseline_stream * 2
        assert settings["sdk_max_retries"] == 0 and settings["max_tokens"] == 8192
        assert scaled_timeout(900) == 1800 and scaled_timeout(180) == 360 and scaled_timeout(150) == 300
        assert config.subagents.timeout_seconds == baseline.subagents.timeout_seconds * 2
        assert get_subagent_config("sp-researcher", app_config=config).timeout_seconds == get_subagent_config("sp-researcher", app_config=baseline).timeout_seconds * 2
    assert scaled_timeout(120) == 120
    assert original.models[0].model_extra.get("timeout") is None


def test_inherited_worker_environment_does_not_double_timeouts_twice():
    import os

    from app.experiments.tender_audit.timing import timeout_multiplier_environment
    from deerflow.utils.runtime_timeouts import scaled_runtime_timeout

    with timeout_multiplier_environment(2):
        stream = os.environ["LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S"]
        assert scaled_runtime_timeout(5) == 10
        with timeout_multiplier_environment(2):
            assert os.environ["LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S"] == stream
            assert scaled_runtime_timeout(1) == 2


def test_projection_leaves_actual_vllm_reasoning_replay_identical():
    from app.experiments.tender_audit.context_projection import project_delegation_messages
    from deerflow.models.vllm_provider import VllmChatModel

    _, messages = delegated_messages()
    projected, _ = project_delegation_messages(messages, action_id="a")
    model = VllmChatModel(model="test", api_key="dummy", base_url="https://model.test/v1")
    before = model._get_request_payload(messages)
    after = model._get_request_payload(projected)
    assert before["messages"][-1] == after["messages"][-1]
    assert after["messages"][-1]["reasoning"] == "PRIVATE_UNCHANGED"


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_multiplier_is_rejected(value):
    from app.experiments.tender_audit.timing import timeout_multiplier_environment

    with pytest.raises(ValueError), timeout_multiplier_environment(value):
        pass
