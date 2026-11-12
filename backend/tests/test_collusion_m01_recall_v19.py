"""M01 recall contracts use synthetic projects, never historical project answers."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def tool_root(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "vendor/bid_audit_tools"))
    return root


def audit(tmp_path, tool_root, bids, tender="采购文件只规定交付条件，具体服务方案由各供应商自行编写。", parsed=None):
    from bid_audit.core import digest
    from bid_audit.runner import run_rule

    project = tmp_path / "project"
    project.mkdir()
    docs = []
    for i, text in enumerate([*bids, tender]):
        path = project / f"{i}.txt"
        path.write_text(text)
        docs.append({"id": f"d{i}", "path": path.name, "role": "bid" if i < len(bids) else "tender", "bidder": f"Company{i}" if i < len(bids) else None, "round": "1", "lot": "1", "expected_sha256": digest(path.read_bytes())})
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": docs}))
    if parsed:
        parsed = parsed(docs)
    result = run_rule("M01", project / "manifest.json", tmp_path / "outputs", permitted_root=project, parsed_texts=parsed)
    assert result["status"] != "error", result
    folder = Path(result["result_directory"])
    rows = {k: [json.loads(line) for line in (folder / v["file"]).read_text().splitlines()] for k, v in result["collections"].items()}
    return result, rows


def test_embedded_common_passage_survives_different_sentence_prefixes(tmp_path, tool_root):
    common = "设备完成部署后双方技术人员应共同开展运行检查并将检查结果归档保存供后续维护使用"
    _, rows = audit(tmp_path, tool_root, ["甲方自行撰写的引言，" + common + "，另有独立结束语。", "乙方不同的章节开头：" + common + "。"])
    assert any(common[:25] in json.dumps(x, ensure_ascii=False) for x in rows["findings"])


def test_small_replacements_and_numbering_produce_paired_diff_not_exact_claim(tmp_path, tool_root):
    a = "一、实施安排\n1、服务团队将在到场后十天内开展机房环境调查并完成现场施工准备。\n2、服务团队的技术员负责系统部署和联调，并提交实施检查记录供客户验收。"
    b = "二、具体实施\n（1）交付团队将在到场后三天内开展机房环境调查并完成现场施工准备。\n（2）交付团队的交付员负责系统部署和联调，并提交实施检查记录供客户验收。"
    _, rows = audit(tmp_path, tool_root, [a, b])
    candidates = [x for x in rows["findings"] if x.get("match_mode") == "near_copy"]
    assert candidates
    assert any(x.get("differences") for x in candidates)
    assert all(len(x.get("evidence_refs", [])) >= 2 for x in candidates)


def test_short_list_items_are_recalled_as_a_long_shared_block(tmp_path, tool_root):
    items = ["设备连接检查", "网络配置复核", "应用参数记录", "传感器校准", "试运行日志归档", "异常处置人员确认"]
    a = "\n".join(f"{i + 1}、{s}；" for i, s in enumerate(items))
    b = "\n".join(f"（{i + 1}）{s}。" for i, s in enumerate(items))
    _, rows = audit(tmp_path, tool_root, [a, b])
    assert rows["findings"]
    assert any("网络配置复核" in json.dumps(x, ensure_ascii=False) for x in rows["findings"])


def test_template_only_overlap_is_excluded_and_recorded(tmp_path, tool_root):
    template = "供应商应按照采购合同规定的时间完成货物交付并负责运输安装调试及人员培训工作。"
    result, rows = audit(tmp_path, tool_root, [template, template], tender=template)
    assert rows["findings"] == []
    assert result["metrics"]["template_matches_excluded"] >= 1
    assert rows["template_exclusions"]


def test_template_commonality_does_not_swallow_adjacent_novel_passage(tmp_path, tool_root):
    template = "供应商应按照采购合同规定的时间完成货物交付并负责运输安装调试及人员培训工作。"
    novel = "设备的每次测试结果将连同校准编号和执行人员签名共同存入现场交付档案并交由双方代表审核。"
    _, rows = audit(tmp_path, tool_root, [template + novel, template + novel], tender=template)
    assert rows["findings"]
    assert any("校准编号" in json.dumps(x, ensure_ascii=False) for x in rows["findings"])


def test_derived_text_only_evidence_has_chunk_provenance_and_cache_identity(tmp_path, tool_root):
    quote = "交付人员每周应核对设备事件记录并制作独立的巡检追踪清单确保异常处理具有完整记录。"

    def parsed(docs):
        return {d["id"]: {"source_sha256": d["expected_sha256"], "chunks": [{"id": f"chunk-{d['id']}", "content": quote, "positions": [[3, 0, 1, 0, 1]]}]} for d in docs[:2]}

    result, rows = audit(tmp_path, tool_root, ["扫描图", "扫描图"], parsed=parsed)
    assert rows["findings"]
    assert all(e.get("source_channel") == "ragflow_mineru" for e in rows["evidence"])
    assert all(e.get("chunk_ids") and e.get("physical_pages") == [3] for e in rows["evidence"])
    assert result["metrics"]["text_source_fingerprint"]


def test_multi_page_chunk_does_not_invent_one_exact_page(tmp_path, tool_root):
    quote = "维护人员应将所有故障状态和诊断结论写入独立检查表以确保后续运行和校准过程能够准确追溯。"

    def parsed(docs):
        return {d["id"]: {"source_sha256": d["expected_sha256"], "chunks": [{"id": d["id"], "content": quote, "positions": [[4, 0, 1, 0, 1], [5, 0, 1, 0, 1]]}]} for d in docs[:2]}

    _, rows = audit(tmp_path, tool_root, ["图像", "图像"], parsed=parsed)
    assert rows["evidence"]
    assert all(e["page"] is None and e["physical_pages"] == [4, 5] for e in rows["evidence"])


def test_derived_source_hash_mismatch_is_rejected(tmp_path, tool_root):
    def parsed(docs):
        return {docs[0]["id"]: {"source_sha256": "wrong", "chunks": []}}

    with pytest.raises(AssertionError, match="Source hash"):
        audit(tmp_path, tool_root, ["A", "B"], parsed=parsed)


def test_same_content_in_different_lots_is_not_a_cross_bidder_candidate(tmp_path, tool_root):
    from bid_audit.m01_recall import compare_documents

    text = "该设备的测试过程和巡检报告应由双方技术人员共同签署并留存纸质档案用于后续服务核验。"
    docs = [{"id": "a", "bidder": "A", "round": "1", "lot": "1", "role": "bid", "blocks": [{"text": text, "page": 1}]}, {"id": "b", "bidder": "B", "round": "1", "lot": "2", "role": "bid", "blocks": [{"text": text, "page": 1}]}]
    assert compare_documents(docs, [])["findings"] == []


def test_common_text_can_cross_ocr_chunk_and_page_boundaries(tmp_path, tool_root):
    quote = "异常工况的原始记录应当由现场技术人员逐项校验并移交给后续维护部门作为长期检修凭据。"

    def parsed(docs):
        return {
            d["id"]: {"source_sha256": d["expected_sha256"], "chunks": [{"id": d["id"] + "first", "content": quote[:16], "positions": [[3, 0, 1, 0, 1]]}, {"id": d["id"] + "second", "content": quote[16:], "positions": [[4, 0, 1, 0, 1]]}]}
            for d in docs[:2]
        }

    _, rows = audit(tmp_path, tool_root, ["图像", "图像"], parsed=parsed)
    assert rows["findings"]
    assert any(e["physical_pages"] == [3, 4] and len(e["chunk_ids"]) == 2 for e in rows["evidence"])


def test_repeated_copy_not_only_the_first_sentence_is_available(tmp_path, tool_root):
    a = "全部测试应在业务停止后完成并由双方法定代表授权的技术人员签署清单留存以便后续复核。"
    b = "交付档案需要详细注明每个阶段发生的异常情况以及补救措施且应在项目结束后交由各方保管。"
    _, rows = audit(tmp_path, tool_root, [a + "另有甲方要求。" + b, a + "不同的补充条件。" + b])
    text = json.dumps(rows["findings"], ensure_ascii=False)
    assert a[:25] in text and b[:25] in text


def test_project_identifiers_and_history_words_are_not_implemented_as_match_rules(tool_root):
    files = [tool_root / "vendor/bid_audit_tools/bid_audit/m01_recall.py", tool_root / "backend/app/experiments/collusion_audit/m01_corpus.py", tool_root / "backend/app/experiments/collusion_audit/m01_delivery.py"]
    source = "\n".join(f.read_text() for f in files)
    assert all(word not in source for word in ["九州通", "天医", "采购单位单位", "项目师", "0877-26GZTP1ZC0146"])


def test_ocr_inputs_and_raw_layer_warnings_have_separate_coverage(tmp_path, tool_root):
    quote = "交付人员每周应核对设备事件记录并制作独立的巡检追踪清单确保异常处理具有完整记录。"

    def parsed(docs):
        return {d["id"]: {"source_sha256": d["expected_sha256"], "chunks": [{"id": d["id"], "content": quote, "positions": [[3, 0, 1, 0, 1]]}]} for d in docs[:2]}

    result, _ = audit(tmp_path, tool_root, ["扫描图", "扫描图"], parsed=parsed)
    coverage = result["metrics"]["input_channel_coverage"]
    assert coverage[0]["mineru_chunk_count"] == 1
    assert coverage[0]["mineru_physical_pages"] == [3]
    assert coverage[0]["mineru_used_in_comparison"] is True
    assert any("persisted MinerU" in w for w in result["warnings"])
    assert not any(w.endswith("no OCR performed") for w in result["warnings"] if w.startswith("d0:"))
