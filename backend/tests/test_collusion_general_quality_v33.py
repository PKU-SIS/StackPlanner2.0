"""Synthetic regressions: original truth, arithmetic, and review handoff."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))


def test_generated_hierarchy_is_not_paper_body():
    from bid_audit.source_quality import source_chunk

    raw = {"id": "c", "content": "[旧附件名称 > 新的响应表]\n新的响应表\n响应正文保持完整。", "positions": [[8, 1, 2, 3, 4]]}
    before = copy.deepcopy(raw)
    result = source_chunk(raw)
    assert result["content"] == "新的响应表\n响应正文保持完整。"
    assert result["structural_metadata"]["generated_path"] == ["旧附件名称", "新的响应表"]
    assert result["raw_content_sha256"] == hashlib.sha256(raw["content"].encode()).hexdigest()
    assert raw == before
    assert source_chunk({"id": "literal", "content": "[正文中的保留文本]\n真实内容"})["content"].startswith("[正文中的")


def test_clean_knowledge_is_opt_in_and_never_rewrites_snapshot(tmp_path):
    from app.experiments.collusion_audit.knowledge import KnowledgeStore

    d = tmp_path / "doc"
    d.mkdir()
    row = {"id": "c", "content": "[前一章节 > 本节]\n本节正文", "positions": [[1, 0, 1, 0, 1]]}
    raw = (json.dumps(row, ensure_ascii=False) + "\n").encode()
    (d / "chunks.jsonl").write_bytes(raw)
    (tmp_path / "index.json").write_text(json.dumps({"ready": True, "documents": {"d": {"dataset_id": "kb", "document_id": "remote", "snapshot": "doc", "source_sha256": "sha", "original_pages": 1}}}))
    assert KnowledgeStore(tmp_path).rows("d")[0]["content"] == row["content"]
    store = KnowledgeStore(tmp_path, general_quality=True)
    assert store.rows("d")[0]["content"] == "本节正文"
    assert "前一章节" not in store.page_text("d", 1, 1)["text"]
    assert (d / "chunks.jsonl").read_bytes() == raw


@pytest.mark.parametrize("text,value,unit", [("12°/秒", "2", "rpm"), ("2RPM", "2", "rpm"), ("600mm", "60", "cm"), ("≥5种", "5", "种"), ("≤1.50mm", "0.150", "cm")])
def test_units_are_computed_not_inferred(text, value, unit):
    from decimal import Decimal

    from bid_audit.source_quality import quantities

    result = quantities(text)[0]
    assert Decimal(result["normalized_value"]) == Decimal(value)
    assert result["normalized_unit"] == unit
    assert result["source_text"] == text


def test_price_cap_does_not_become_fixed_price():
    from bid_audit.source_quality import price_constraints

    rows = price_constraints("耗材甲限价≤1200元/根；耗材乙统一固定价格800元/件")
    assert rows[0]["kind"] == "upper_bound" and not rows[0]["forces_equal_bid_prices"]
    assert rows[1]["kind"] == "fixed_price"


def test_baseline_common_changes_survive_qualification_location():
    from bid_audit.source_quality import common_baseline_edits

    def block(doc, text):
        return {"document_id": doc, "text": text, "page": 3, "physical_pages": [3], "source_channel": "synthetic_original"}

    shared = "资格声明：查询时点以评审当日的查询结果为准，供应商应当提供相关证明材料。"
    baseline = "资格声明：查询时点以投标截止当日的查询结果为准，供应商应当提供相关证明材料。"
    rows = common_baseline_edits({"a": [block("a", shared)], "b": [block("b", shared)]}, {"t": [block("t", baseline)]})
    assert rows and rows[0]["kind"] == "shared_text_differs_from_reference"
    assert rows[0]["requires_original_verification"]
    assert set(rows[0]["document_ids"]) == {"a", "b"} and rows[0]["reference_document_id"] == "t"
    assert "risk_verdict" not in rows[0]
    assert common_baseline_edits({"a": [block("a", shared)], "b": [block("b", shared)]}, {"t": [block("t", shared)]}) == []


def test_same_customer_different_project_is_observation_not_collusion():
    from bid_audit.source_quality import customer_matches

    rows = [{"document_id": "a", "bidder": "甲", "rows": [["客户名称", "项目名称"], ["某大学", "项目甲"]]}, {"document_id": "b", "bidder": "乙", "rows": [["客户名称", "项目名称"], ["某大学", "项目乙"], ["某大学附属医院", "项目丙"]]}]
    result = customer_matches(rows)
    assert len(result) == 1 and result[0]["customer_name"] == "某大学"
    assert not result[0]["same_contract_verified"] and "risk_verdict" not in result[0]


def test_prompt_is_rule_specific_without_project_answers():
    from app.experiments.collusion_audit.review_quality import guidance

    common = guidance("M07")
    assert "同一处" in common and "单侧" in common and "原图" in common
    assert "上限" in guidance("M15") and "资格" in guidance("M01")
    for name in ["九州通", "诚屹", "玮琳", "恒汇", "原报告", "GT"]:
        assert name not in common


class Request:
    def __init__(self, messages):
        self.messages = messages
        self.state = {}
        self.tools = ["read"]

    def override(self, **kw):
        result = Request(kw.get("messages", self.messages))
        result.tools = kw.get("tools", self.tools)
        result.model_settings = kw.get("model_settings", getattr(self, "model_settings", {}))
        return result


def test_duplicate_context_keeps_full_first_body_and_distinct_reads():
    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    original = json.dumps({"text": "完整正文" * 200, "source_sha256": "a"})
    different = json.dumps({"text": "另一个页段", "source_sha256": "a"})
    messages = [ToolMessage(original, name="read_file", tool_call_id="1"), ToolMessage(original, name="read_file", tool_call_id="2"), ToolMessage(different, name="read_file", tool_call_id="3")]
    result = ReviewBudgetMiddleware().project(Request(messages))
    assert result.messages[0].content == original and len(result.messages[1].content) < len(original)
    assert result.messages[2].content == different and messages[1].content == original


def test_budget_reserves_a_normal_reply_without_declaring_completion():
    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    middleware = ReviewBudgetMiddleware(token_budget=1000, reserve_tokens=200)
    middleware.spent_tokens = 740
    middleware.last_input_tokens = 100
    result = middleware.project(Request([HumanMessage("现有证据")]))
    assert result.tools == [] and "partial" in result.messages[-1].content and "Central" in result.messages[-1].content
    assert "JSON修复" not in result.messages[-1].content


def test_budget_uses_actual_provider_usage():
    from langchain_core.messages import AIMessage

    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    middleware = ReviewBudgetMiddleware()
    message = AIMessage(content="done", usage_metadata={"input_tokens": 400, "output_tokens": 30, "total_tokens": 430})
    middleware.observe(SimpleNamespace(result=[message]))
    assert middleware.spent_tokens == 430 and middleware.last_input_tokens == 400


def test_shared_customer_supports_actual_tool_table_shape():
    from bid_audit.source_quality import customer_matches

    rows = [{"document_id": doc, "table_rows": [["项目名称", "用户名称"], [project, "某市医院"]]} for doc, project in [("a", "项目甲"), ("b", "项目乙")]]
    assert customer_matches(rows)[0]["customer_name"] == "某市医院"


def test_direct_read_cache_is_opt_in_and_keeps_complete_body(tmp_path):
    import asyncio

    from app.experiments.collusion_audit.repetition import CURRENT_DELEGATION
    from app.experiments.collusion_audit.tools import AuditTools

    async def run(enabled):
        outputs = tmp_path / str(enabled)
        outputs.mkdir()
        trace = SimpleNamespace(event=lambda *a, **k: None, record_tool=lambda *a, **k: None, append=lambda *a, **k: None)
        adapter = AuditTools("M01", tmp_path, outputs, tmp_path / "cache", trace, knowledge=SimpleNamespace(), rule_level_review=True)
        adapter.general_quality = enabled
        called = []

        def read():
            called.append(1)
            return {"text": "完整源文", "document_id": "d1"}

        token = CURRENT_DELEGATION.set("action")
        try:
            first = json.loads(await adapter.recorded("read_file", {"file_path": "/knowledge/d1"}, read))
            second = json.loads(await adapter.recorded("read_file", {"file_path": "/knowledge/d1"}, read))
        finally:
            CURRENT_DELEGATION.reset(token)
        assert len(called) == 1
        if enabled:
            assert first == second
        else:
            assert second["status"] == "already_returned"

    asyncio.run(run(False))
    asyncio.run(run(True))


def test_reference_recall_survives_line_wrap_and_small_ocr_difference():
    from bid_audit.source_quality import common_baseline_edits

    def block(text):
        return {"text": text, "physical_pages": [2], "page": 2}

    a = "信用证明资料应以评审当日的查询结果为准，如记录已经失效，供应商需要提交相关的证明材料。"
    b = "信用证明资料应以评审当天的查询结果为准，如记录已经失效，供应商需要提交相关的证明材料。"
    ref = "信用证明资料应以投标截止当日\n的查询结果为准，如记录已经失效，供应商需要提交相关的证明材料。"
    result = common_baseline_edits({"a": [block(a)], "b": [block(b)]}, {"t": [block(ref)]})
    assert result and set(result[0]["document_ids"]) == {"a", "b"}
    assert {s["text"] for s in result[0]["bid_sources"]} == {a[:-1], b[:-1]}


def test_named_destinations_resolve_actual_target_pages(tmp_path):
    from bid_audit.source_quality import pdf_destinations
    from pypdf import PdfWriter

    p = tmp_path / "source.pdf"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_named_destination("ordinary-anchor", 0)
    with p.open("wb") as f:
        w.write(f)
    rows = pdf_destinations({"id": "d", "_path": p})
    assert rows[0]["name"] == "ordinary-anchor" and rows[0]["physical_page"] == 1
    assert rows[0]["meaning"] == "named_page_destination"


def test_enriched_result_obeys_complete_packet_collection_contract(tmp_path):
    from bid_audit.runner import _externalize
    from bid_audit.source_quality import VERSION, enrich

    from app.experiments.collusion_audit.full_results import pack_complete_result

    # A real but empty clean context can have no source rows.
    ctx = SimpleNamespace(derived_sources={}, by_id={"d": {"sha256": "x"}}, documents=lambda *roles: [])
    data = enrich(ctx, "M15", {"status": "ok", "metrics": {}, "findings": [], "limitations": []}, {"d": {"source_view_version": VERSION, "source_sha256": "x", "chunks": []}})
    data, evidence = _externalize(data)
    collections = {k: data.pop(k) for k in list(data) if isinstance(data[k], list) and k != "limitations"}
    collections["evidence"] = evidence
    for name, rows in collections.items():
        assert all(isinstance(row, dict) for row in rows), name
        (tmp_path / (name + ".jsonl")).write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {**data, "run_id": "M15-test", "rule_id": "M15", "collections": {name: {"file": name + ".jsonl", "count": len(rows)} for name, rows in collections.items()}}
    (tmp_path / "result.json").write_text(json.dumps(summary))
    (tmp_path / "sources.json").write_text("[]")
    assert pack_complete_result(tmp_path)


def test_grade_intro_is_not_a_changed_evidence_grade():
    from app.experiments.collusion_audit.report_checks import report_text_errors

    grade = "价格一致性线索；须结合其他证据"
    text = "证据等级：本报告所列两项价格观察为“" + grade + "”，尚不足单独认定串通。"
    assert not report_text_errors(text, {}, expected_grade=grade, rule_id="M15", general_quality=True)
    assert report_text_errors("证据等级：本报告所列观察为“强风险”；规则原文为“" + grade + "”", {}, expected_grade=grade, rule_id="M15", general_quality=True)


def test_budget_closing_reaches_actual_model_tool_choice():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from pydantic import PrivateAttr

    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    class Model(FakeMessagesListChatModel):
        _seen = PrivateAttr(default_factory=list)

        def bind_tools(self, *a, **kw):
            raise AssertionError("closing request should have no active tools")

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._seen.append(kwargs)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def read(value: str) -> str:
        """Synthetic read tool."""
        return value

    model = Model(responses=[AIMessage(content='{"summary":"真实partial交接"}')])
    mw = ReviewBudgetMiddleware(token_budget=1000, reserve_tokens=200)
    mw.spent_tokens = 750
    mw.last_input_tokens = 100
    agent = create_agent(model=model, tools=[StructuredTool.from_function(read)], middleware=[mw])
    agent.invoke({"messages": [HumanMessage("按原生协议交接已核事实和未核事项")]})
    assert model._seen[-1].get("tool_choice") == "none"


def test_own_identity_field_differences_are_not_evidence_of_replacing_another_bidder():
    from bid_audit.source_quality import replacement_context_advice

    rows = [{"document_a": "a", "document_b": "b", "before": "甲方贸易", "after": "乙方贸易", "occurrence_count": 21, "contexts": [{"context_a": "投标人名称：甲方贸易有限公司", "context_b": "投标人名称：乙方贸易有限公司"}]}]
    docs = {"a": {"bidder": "甲方贸易有限公司"}, "b": {"bidder": "乙方贸易有限公司"}}
    result = replacement_context_advice(rows, docs)
    assert result[0]["bidder_identity_fragment_substitution"]
    assert result[0]["cannot_alone_establish_common_error_or_authorship"]
    assert result[0]["occurrence_count_is_not_independent_evidence_count"]
    assert "risk_verdict" not in result[0]


def test_report_created_closing_also_disables_provider_tool_choice():
    from app.experiments.collusion_audit.closing import BudgetClosingMiddleware
    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    path = "/mnt/user-data/outputs/report.html"
    request = Request([ToolMessage(json.dumps({"created": path}), name="write_file", tool_call_id="written")])
    request.model_settings = {}
    closed = BudgetClosingMiddleware(report_path=path, enforce_read_limits=False).project(request)
    result = ReviewBudgetMiddleware().project(closed)
    assert result.tools == [] and result.model_settings["tool_choice"] == "none"


def test_common_reference_changes_are_visible_as_primary_candidates():
    from bid_audit.source_quality import promote_reference_changes

    docs = {d: {"id": d, "bidder": d} for d in ["a", "b", "t"]}
    ctx = SimpleNamespace(by_id=docs, evidence=lambda doc, page, quote, **kw: {"document_id": doc["id"], "page": page, "quote": quote, **kw})
    row = {
        "document_ids": ["a", "b"],
        "bid_sources": [{"document_id": d, "text": "查询应以评审当天结果为准，供应商应提交证明。", "page": 2, "physical_pages": [2]} for d in ["a", "b"]],
        "reference_document_id": "t",
        "reference_source": {"text": "查询应以投标截止当天结果为准，供应商应提交证明。", "page": 3, "physical_pages": [3]},
        "shared_reference_edits": [{"shared": "评审", "reference": "投标截止"}],
        "matching_similarity": 0.85,
    }
    data = {"metrics": {}, "findings": []}
    promote_reference_changes(ctx, data, [row])
    assert len(data["findings"]) == 1
    finding = data["findings"][0]
    assert finding["kind"] == "shared_reference_change_for_review" and finding["selected_reference_comparison"]["quote"] == row["reference_source"]["text"]
    assert len(finding["evidence"]) == 3 and "risk_verdict" not in finding
    assert finding["requires_original_verification"]
    normal = {**row, "shared_reference_edits": [{"shared": "我方", "reference": "投标人"}]}
    no_changes = {"metrics": {}, "findings": []}
    promote_reference_changes(ctx, no_changes, [normal])
    assert no_changes["findings"] == []


def test_primary_reference_candidate_preview_keeps_baseline_text():
    from app.experiments.collusion_audit.m01_delivery import evidence_preview, review_features

    row = {
        "kind": "shared_reference_change_for_review",
        "non_template_shared_chars": 0,
        "matching_similarity": 0.85,
        "selected_reference_comparison": {"quote": "完整基线，保留判断条件。", "shared_reference_edits": [{"shared": "某条件甲", "reference": "某条件乙"}]},
    }
    preview = evidence_preview(row, {})
    assert preview["selected_reference_comparison"] == row["selected_reference_comparison"]
    assert review_features(row)[0] == "reference_clause_change"


def test_reporter_closing_repeats_literal_grade_in_final_request():
    from app.experiments.collusion_audit.review_budget import ReviewBudgetMiddleware

    request = Request([HumanMessage("文件已创建，返回原生阶段协议")])
    request.tools = []
    result = ReviewBudgetMiddleware(expected_grade="辅助编辑线索").project(request)
    assert "evidence_level" in result.messages[-1].content and "辅助编辑线索" in result.messages[-1].content
    assert result.model_settings["tool_choice"] == "none"


def test_primary_previews_cover_distinct_reference_changes():
    from app.experiments.collusion_audit.general_delivery import selected_preview_offsets

    ranked = [(i, {"kind": "shared_reference_change_for_review", "shared_reference_edits": [{"shared": f"条款甲{i}", "reference": f"条款乙{i}"}]}) for i in range(5)]
    ranked += [(i, {"kind": "shared_error_hypothesis"}) for i in range(5, 28)]
    assert set(range(5)) <= selected_preview_offsets(ranked)


def test_central_transport_preserves_distinct_reference_clause_bodies():
    from app.experiments.collusion_audit.fact_context import bounded_control_navigation

    previews = [{"kind": "shared_reference_change_for_review", "candidate": {"shared_reference_edits": [{"shared": f"甲{i}", "reference": f"乙{i}"}]}, "source_body": f"完整来源{i}"} for i in range(5)]
    result = bounded_control_navigation({"candidate_previews": previews, "complete_code_result_path": "/full", "candidate_index_path": "/index"})
    assert result["candidate_previews"] == previews
