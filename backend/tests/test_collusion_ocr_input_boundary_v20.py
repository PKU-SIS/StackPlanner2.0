"""OCR receipts must match real projected inputs, including pinned source views."""

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class Knowledge:
    index = {"documents": {"d001": {}}}

    def rows(self, doc):
        return [{"id": "chunk1", "content": "完整OCR原文，末尾不可省略。", "positions": [[1, 0, 0, 100, 100]]}]


def test_full_verified_code_json_text_counts_as_actual_ocr_input():
    from app.experiments.collusion_audit.source_receipts import OCRReadReceipts

    receipts = OCRReadReceipts(Knowledge())
    source = {"document_id": "d001", "ocr_chunks": [{"chunk_id": "chunk1", "content": Knowledge().rows("d001")[0]["content"]}]}
    wrapped = {"source": "verified_current_calculation_source", "source_sha256": "trusted-host-hash", "text": json.dumps(source, ensure_ascii=False)}
    receipts.observe_model_input([ToolMessage(content=json.dumps(wrapped, ensure_ascii=False), tool_call_id="c")])
    assert receipts.seen["d001"] == {"chunk1"}


def test_invalid_or_untrusted_code_text_is_never_repaired_for_receipt():
    from app.experiments.collusion_audit.source_receipts import OCRReadReceipts

    for text, source in [('{"document_id":', "verified_current_calculation_source"), (json.dumps({"document_id": "d001", "chunk_id": "chunk1", "content": Knowledge().rows("d001")[0]["content"]}), "model_draft")]:
        receipts = OCRReadReceipts(Knowledge())
        receipts.observe_model_input([ToolMessage(content=json.dumps({"source": source, "source_sha256": "hash", "text": text}), tool_call_id="c")])
        assert not receipts.seen


def test_actual_profile_middleware_chain_records_pinned_ocr_seen_by_model(tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import PrivateAttr

    from app.experiments.collusion_audit.profile import CollusionProfile
    from app.experiments.collusion_audit.source_receipts import OCRReadReceipts
    from app.experiments.tender_audit.observability import AuditTrace
    from deerflow.sp.subagents import SPSubagentTask

    class Model(FakeMessagesListChatModel):
        _seen = PrivateAttr(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    receipts = OCRReadReceipts(Knowledge())
    body = Knowledge().rows("d001")[0]["content"]
    index = {"rule_id": "M06", "audit_run_id": "run", "findings": [], "result_navigation": {"candidate_previews": [{"ocr_endpoints": [{"document_id": "d001", "ocr_chunks": [{"chunk_id": "chunk1", "content": body}]}]}]}}
    adapter = SimpleNamespace(fact_indexes={"run": index}, ocr_receipts=receipts, rule_id="M06", ledger=None, displayed_pairs=lambda: set(), register_submitted_images=lambda messages: None)
    profile = object.__new__(CollusionProfile)
    profile.adapters = adapter
    profile.rule_id = "M06"
    profile.quality_controls = True
    profile.knowledge = None
    profile.tools = []
    profile.trace = AuditTrace(tmp_path / "trace")
    middlewares = profile.extensions().subagent_middlewares(SPSubagentTask("a", "researcher", "review", "test"))
    model = Model(responses=[AIMessage(content="done")])
    graph = create_agent(model, middleware=list(middlewares))
    graph.invoke({"messages": [HumanMessage(content="审核当前代码源事实")]})
    assert any(body in str(m.content) for m in model._seen[0])
    assert receipts.seen["d001"] == {"chunk1"}
