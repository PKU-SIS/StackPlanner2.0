"""Context navigation is actual source metadata, never a semantic verdict."""

import json

from app.experiments.collusion_audit.general_delivery import materialize


def test_literal_context_counts_and_source_pages_do_not_require_model_counting(tmp_path):
    bodies = ["原始 是否偏离;无偏离;;字段", "配置清单;设备名称;;字段", "签署和盖章......", "没有表头的原始上下文。;正文"]
    rows = [{"kind": "shared_punctuation_context", "context": bodies[i % 4], "punctuation": ";;", "evidence_refs": ["EA", "EB"]} for i in range(24)]
    evidence = [{"evidence_id": "EA", "document_id": "a", "physical_pages": [2, 3]}, {"evidence_id": "EB", "document_id": "b", "page": 5}]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M08_synthetic"}}, rows, evidence)
    index = json.loads((tmp_path / "candidates/index.json").read_text())
    assert index["candidates"][0]["context"] == bodies[0]
    assert index["candidates"][0]["source_pages_by_document"] == {"a": [2, 3], "b": [5]}
    stats = nav["summary_statistics"]["literal_context_navigation"]
    assert stats["by_marker_group"] == {"deviation_table_marker": 6, "configuration_marker": 6, "placeholder_marker": 6, "unclassified_context": 6}
    assert stats["by_document_set"] == {"a|b": 24}
    assert stats["cross_document_candidates"] == 24 and stats["single_document_candidates"] == 0
    assert stats["semantic_location_verified"] is False
    assert nav["candidate_count"] == 24
    assert json.loads((tmp_path / "candidates/C000024.json").read_text())["candidate"] == rows[-1]


def test_marker_absence_is_unknown_not_a_prose_or_risk_verdict(tmp_path):
    row = {"kind": "shared_punctuation_context", "context": "设备要求1;;参数响应无表头", "punctuation": ";;"}
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M08_synthetic"}}, [row], [])
    stats = nav["summary_statistics"]["literal_context_navigation"]
    assert stats["by_marker_group"] == {"unclassified_context": 1}
    assert "not prose" in stats["note"]
    assert "risk_verdict" not in nav["candidate_previews"][0]["candidate"]
