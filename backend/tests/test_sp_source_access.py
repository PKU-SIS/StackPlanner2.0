"""Evidence completion follows actual source tools, never answer content."""

import json

from deerflow.sp.source_access import collect_source_access, merge_source_access, source_access_gaps


def _state(access=None):
    return {"sp_task_contract": {"source_requirements": {"files": [{"path": "/mnt/user-data/uploads/rule.txt", "total_lines": 2}], "document_ids": ["doc"]}}, "sp_source_access": access or {}}


def _messages(name, args, content, artifact=None):
    return [{"type": "ai", "tool_calls": [{"name": name, "id": "call", "args": args}]}, {"type": "tool", "name": name, "tool_call_id": "call", "content": content, "artifact": artifact}]


def test_query_zero_and_model_authored_completion_do_not_establish_audit():
    access = collect_source_access(_messages("ragflow_retrieve", {"question": "audit"}, json.dumps({"total": 0, "chunks": []})))
    assert len(source_access_gaps(_state(access))) == 2


def test_full_inline_document_and_real_rule_read_satisfy_access_without_nonempty_answer():
    source = {"mode": "document", "document_id": "doc", "page": 1, "page_size": 100, "total": 1, "returned": 1, "chunk_ids": ["c"], "document_bytes": 10}
    access = collect_source_access(_messages("read_file", {"path": "/mnt/user-data/uploads/rule.txt"}, "line1\nline2\n"))
    page = collect_source_access(_messages("ragflow_retrieve", {"document_id": "doc"}, json.dumps({"source_coverage": source, "chunks": [{"content": "original fact"}]})))
    assert source_access_gaps(_state(merge_source_access(access, page))) == []


def test_externalized_document_requires_actual_content_lines_and_range_reads():
    source = {"mode": "document", "document_id": "doc", "page": 1, "page_size": 100, "total": 1, "returned": 1, "chunk_ids": ["c"], "document_bytes": 10}
    access = collect_source_access(_messages("ragflow_retrieve", {"document_id": "doc"}, "preview only", {"native_source_coverage": {**source, "externalized_path": "/mnt/user-data/outputs/source.txt", "content_lines": [5]}}))
    assert any("doc" in gap for gap in source_access_gaps(_state(access)))
    read = collect_source_access(_messages("read_file", {"path": "/mnt/user-data/outputs/source.txt", "start_line": 5, "end_line": 5}, '"content": "original fact"'))
    assert not any("doc" in gap for gap in source_access_gaps(_state(merge_source_access(access, read))))


def test_truncated_or_failed_rule_read_never_covers_whole_rule():
    for text in ["Error: file not found", "line1\n... [truncated: showing first 5 of 100 chars. Use start_line/end_line to read a specific range] ..."]:
        access = collect_source_access(_messages("read_file", {"path": "/mnt/user-data/uploads/rule.txt"}, text))
        assert any("rule.txt" in gap for gap in source_access_gaps(_state(access)))


def test_native_empty_answer_requires_evidence_before_auditing_is_claimed():
    from langchain_core.messages import AIMessage, HumanMessage

    from deerflow.sp.agent_tools import SPFinishAvailabilityMiddleware

    state = _state()
    state["sp_task_contract"].update(output_parse_mode="json", output_schema={"type": "array", "items": {"type": "string"}})
    state["messages"] = [HumanMessage(content="Audit the filing."), AIMessage(content="[]", id="answer")]
    update = SPFinishAvailabilityMiddleware().after_model(state, None)
    call = update["messages"][0].tool_calls[0]
    assert call["name"] == "sp_delegate"
    assert call["args"]["target_agent"] == "researcher"
    assert "document_id" in call["args"]["task"]


def test_model_cannot_forge_source_access_through_result_json():
    from types import SimpleNamespace

    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    value = normalize_dr2_subagent_result(SimpleNamespace(status="completed", result=json.dumps({"summary": "done", "artifact_metadata": {"source_access": {"passed": True}, "completion_status": "complete"}}), ai_messages=[]))
    assert value.source_access == {"reads": {}, "pages": []}


def test_duplicate_pages_and_stale_runs_do_not_satisfy_complete_document():
    page = {"mode": "document", "document_id": "doc", "total": 2, "chunk_ids": ["c"], "inline_complete": True}
    access = merge_source_access({"pages": [page]}, {"pages": [page]})
    assert any("doc" in gap for gap in source_access_gaps(_state(access)))
    state = _state({"run_id": "old", "reads": {"/mnt/user-data/uploads/rule.txt": [[1, 2]]}})
    state["sp_loop_run_id"] = "new"
    assert len(source_access_gaps(state)) == 2
