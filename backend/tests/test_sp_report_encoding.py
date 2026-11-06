"""Encoding requirements must not introduce extra content into JSON artifacts."""

from types import SimpleNamespace

import pytest

from deerflow.sp.actions import ActionType, SPAction
from deerflow.sp.actions.handlers.delegate import _required_report_literal_tokens


@pytest.mark.parametrize("encoding", ["UTF-8", "UTF-16", "UTF-32", "US-ASCII", "ISO-8859-1"])
def test_report_encoding_is_not_an_answer_literal(encoding):
    text = f"Preserve verbatim source sentences. Write a pure JSON file encoded as {encoding}."
    action = SPAction.create(ActionType.DELEGATE, reason="audit", target_agent="reporter", task=text)
    tokens = _required_report_literal_tokens(action, SimpleNamespace(state={}), context_refs={"latest_user_input": text})
    assert encoding not in tokens


def test_explicit_encoding_literal_and_audit_markers_are_retained():
    text = "Include the literal token `UTF-8` and preserve AUDIT-HIGH exactly in the report."
    action = SPAction.create(ActionType.DELEGATE, reason="report", target_agent="reporter", task=text)
    tokens = _required_report_literal_tokens(action, SimpleNamespace(state={}), context_refs={"latest_user_input": text})
    assert "UTF-8" in tokens
    assert "AUDIT-HIGH" in tokens


def test_backticked_encoding_is_still_format_not_content():
    text = "Preserve the original sentences; write the answer using `UTF-8` encoding."
    action = SPAction.create(ActionType.DELEGATE, reason="audit", target_agent="reporter", task=text)
    tokens = _required_report_literal_tokens(action, SimpleNamespace(state={}), context_refs={"latest_user_input": text})
    assert "UTF-8" not in tokens


def test_explicit_public_schema_does_not_infer_transport_placeholder_as_answer_literal():
    text = (
        "Preserve the exact output format from the question/rule file. "
        'For each scoped ID call ragflow_retrieve(question="Read complete indexed source", document_id=THE_REAL_ID, page=1, page_size=100). '
        "Inspect every returned chunk content; continue until all total chunks are returned and read."
    )
    action = SPAction.create(ActionType.DELEGATE, reason="audit", target_agent="reporter", task=text)
    schema = {"type": "array", "items": {"type": "string"}}
    tokens = _required_report_literal_tokens(action, SimpleNamespace(state={"sp_task_contract": {"output_schema": schema}}), context_refs={"latest_user_input": text})
    assert tokens == []
