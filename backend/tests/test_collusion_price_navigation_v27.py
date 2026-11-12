"""Large-result navigation must expose literal scoped caps, preserving all rows."""

import json

from app.experiments.collusion_audit.general_delivery import materialize


def test_scoped_cap_at_end_is_visible_without_dropping_other_budget_sources(tmp_path):
    rows = [{"document_id": "t" + str(i % 2), "field": "budget", "scope_hint": "package_or_total", "contains_arabic_number": True, "text": "采购包预算100万元。", "evidence_refs": []} for i in range(24)]
    cap = {"document_id": "t0", "field": "maximum_price", "scope_hint": "per_unit_or_item", "contains_arabic_number": True, "text": "单套采购限价38万元。", "evidence_refs": []}
    rows.append(cap)
    source = tmp_path / "budget_observations.jsonl"
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    source.write_text(raw)
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M13_run"}}, [], [], collections={"budget_observations": rows})
    shown = nav["supplementary_collections"]["budget_observations"]
    assert any(item["record"]["text"] == cap["text"] for item in shown["examples"])
    assert shown["total"] == 25
    assert source.read_text() == raw


def test_tender_baseline_not_hidden_by_repeated_unverified_bid_tables(tmp_path):
    rows = [{"document_id": "b", "document_role": "bid", "source_scope": "contract_or_performance_attachment_unverified", "table_kind": "quantity_name_table", "rows": [["旧设备", "11"]], "evidence_refs": []} for _ in range(24)]
    rows.append({"document_id": "t", "document_role": "tender", "source_scope": "tender_requirement", "table_kind": "quantity_name_table", "rows": [["本次设备", "2"]], "evidence_refs": []})
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M13_run"}}, [], [], collections={"configuration_observations": rows})
    shown = nav["supplementary_collections"]["configuration_observations"]
    assert shown["examples"][0]["record"]["document_role"] == "tender"
    assert shown["total"] == 25
