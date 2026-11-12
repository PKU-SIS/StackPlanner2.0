"""Request-local evidence compression must preserve every source and native state."""

import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def history():
    body = "<table>完整评分表与跨页位置，不能截断</table>" * 300
    a = {"id": "a", "document_id": "doc", "content": body, "positions": [[1]], "similarity": 0.9, "table_part": 0, "old_field": "keep", "flag": 0}
    changed = {**a, "similarity": 0.5, "new_field": False, "flag": False}
    changed.pop("old_field")
    b = {**a, "id": "b", "content": body + "不同内容"}
    values = [
        {"dataset_id": "bound", "chunks": [a, b], "chunk_count": 2, "query_meta": {"zero": 0}},
        {"dataset_id": "bound", "chunks": [changed, b], "chunk_count": 2},
    ]
    messages = [HumanMessage("审核原规则", id="human")]
    for i, value in enumerate(values):
        messages.extend(
            [
                AIMessage("", id=f"ai-{i}", tool_calls=[{"name": "local_search_tool", "args": {"query": str(i)}, "id": str(i)}]),
                ToolMessage(json.dumps(value, ensure_ascii=False), name="local_search_tool", tool_call_id=str(i), id=f"tool-{i}"),
            ]
        )
    return messages, values


def test_projection_preserves_bodies_metadata_per_query_and_message_protocol():
    from app.experiments.tender_audit.request_projection import project_evidence_messages

    original, values = history()
    before = copy.deepcopy(original)
    projected, stats = project_evidence_messages(original, dataset_id="bound")
    assert original == before and len(projected) == len(original)
    assert stats["source_occurrences"] == 4 and stats["unique_sources"] == 2
    assert stats["after_chars"] < stats["before_chars"]
    last = json.loads(projected[-1].content)
    sources = last["audit_shared_sources"]
    for message, value in zip([m for m in projected if isinstance(m, ToolMessage)], values):
        data = json.loads(message.content)
        reconstructed = []
        for ref in data["chunks"]:
            identity = ref if isinstance(ref, str) else ref["ref"]
            chunk = {**copy.deepcopy(last["audit_shared_source_metadata"]), **copy.deepcopy(sources[identity])}
            if isinstance(ref, dict):
                for key in ref.get("drop", []):
                    chunk.pop(key)
                chunk.update(ref.get("set", {}))
            reconstructed.append(chunk)
        assert reconstructed == value["chunks"]
        assert type(reconstructed[0]["flag"]) is type(value["chunks"][0]["flag"])
        assert {k: data[k] for k in value if k != "chunks"} == {k: v for k, v in value.items() if k != "chunks"}
    for old, new in zip(original, projected):
        assert old.type == new.type and old.id == new.id
        if isinstance(old, ToolMessage):
            assert old.tool_call_id == new.tool_call_id and old.name == new.name
        else:
            assert old == new
    # Applying twice cannot create dangling references or change the request again.
    twice, _ = project_evidence_messages(projected, dataset_id="bound")
    assert twice == projected


@pytest.mark.parametrize("mutation", ["wrong_dataset", "bad_json", "missing_content", "other_tool"])
def test_projection_refuses_unsupported_tool_results_without_losing_data(mutation):
    from app.experiments.tender_audit.request_projection import project_evidence_messages

    messages, _ = history()
    if mutation == "bad_json":
        messages[-1] = messages[-1].model_copy(update={"content": "invalid JSON"})
    elif mutation == "other_tool":
        messages = [m.model_copy(update={"name": "other"}) if isinstance(m, ToolMessage) else m for m in messages]
    else:
        value = json.loads(messages[-1].content)
        if mutation == "wrong_dataset":
            value["dataset_id"] = "other"
        else:
            value["chunks"][0].pop("content")
        messages[-1] = messages[-1].model_copy(update={"content": json.dumps(value)})
    projected, _ = project_evidence_messages(messages, dataset_id="bound")
    assert projected == messages


def test_same_id_different_body_stays_separate_and_single_occurrence_is_not_expanded():
    from app.experiments.tender_audit.request_projection import project_evidence_messages

    messages, _ = history()
    data = json.loads(messages[-1].content)
    data["chunks"][0]["content"] += "修订版本"
    messages[-1] = messages[-1].model_copy(update={"content": json.dumps(data, ensure_ascii=False)})
    projected, stats = project_evidence_messages(messages, dataset_id="bound")
    assert stats["unique_sources"] == 3
    single = messages[:3]
    unchanged, _ = project_evidence_messages(single, dataset_id="bound")
    assert unchanged == single


@pytest.mark.asyncio
async def test_middleware_projects_actual_model_request_logs_both_views_and_keeps_state(tmp_path):
    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.request_projection import AuditEvidenceRequestMiddleware

    messages, _ = history()
    before = copy.deepcopy(messages)
    trace = AuditTrace(tmp_path)
    middleware = AuditEvidenceRequestMiddleware(dataset_id="bound", trace=trace)

    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kwargs):
            return Request(kwargs["messages"])

    captured = []

    async def handler(request):
        captured.append(request.messages)
        return "model response unchanged"

    assert await middleware.awrap_model_call(Request(messages), handler) == "model response unchanged"
    assert messages == before and captured[0] != messages
    record = json.loads((tmp_path / "model_request_projections.jsonl").read_text())
    assert record["before_messages"] and record["after_messages"]
    assert record["policy"] == "same_request_lossless_sources_v2"


def test_experiment_header_merges_without_mutating_global_config(monkeypatch):
    from app.experiments.tender_audit.runner import experiment_config, runtime_model_settings
    from deerflow.config.app_config import AppConfig
    from deerflow.config.model_config import ModelConfig
    from deerflow.config.sandbox_config import SandboxConfig

    original = AppConfig(
        models=[ModelConfig(name="local", use="deerflow.models.vllm_provider:VllmChatModel", model="local", api_key="dummy", base_url="http://localhost:9999/v1", default_headers={"X-Test": "retained"})], sandbox=SandboxConfig(use="test")
    )
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: original)
    config = experiment_config("local")
    assert config.models[0].model_extra["default_headers"] == {"X-Test": "retained", "Connection": "close"}
    assert original.models[0].model_extra["default_headers"] == {"X-Test": "retained"}
    settings = runtime_model_settings(config, thinking=True)
    assert settings["connection_header"] == "close"


def test_profile_installs_context_every_role_and_evidence_only_for_researcher(tmp_path):
    from app.experiments.tender_audit.cases import AuditCase
    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.profile import AuditProfile
    from app.experiments.tender_audit.ragflow import RagflowClient, RagflowSettings
    from deerflow.sp.subagents import SPSubagentTask

    trace = AuditTrace(tmp_path)
    profile = AuditProfile(AuditCase(9, "标书", "bound", ("评分",), "原规则", "检索"), RagflowClient(RagflowSettings("https://rag.test", "dummy"), "bound", trace), trace, tmp_path / "outputs")
    hook = profile.extensions().subagent_middlewares
    assert len(hook(SPSubagentTask("a", "researcher", "检索", "任务"))) == 2
    assert len(hook(SPSubagentTask("b", "reporter", "报告", "任务"))) == 1


@pytest.mark.asyncio
async def test_real_agent_keeps_raw_state_and_callbacks_see_projected_input(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.request_projection import AuditEvidenceRequestMiddleware

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "local-projection-test"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            assert "audit_shared_sources" in messages[-1].content
            return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])

    trace = AuditTrace(tmp_path)
    messages, _ = history()
    original = copy.deepcopy(messages)
    agent = create_agent(Model(), middleware=[AuditEvidenceRequestMiddleware(dataset_id="bound", trace=trace)])
    result = await agent.ainvoke({"messages": messages}, config={"callbacks": [trace]})
    assert messages == original
    raw_tools = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert all("audit_shared_sources" not in m.content and "audit_source_ref" not in m.content for m in raw_tools)
    assert "audit_shared_sources" in trace.model_records[0]["inputs"][0][-1]["content"]
