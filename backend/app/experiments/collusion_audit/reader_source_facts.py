"""Run-local reference aids from real tool receipts; never model verdicts."""

import json
from collections import defaultdict
from pathlib import Path


def reader_source_facts(adapter, manifest):
    from .html_report import reader_context

    documents = {row["id"]: row for row in manifest.get("documents", [])}
    labels = reader_context({}, manifest)["document_labels"]
    pages = defaultdict(set)
    present = set()
    runs = [r for r in getattr(adapter, "runs", []) if r.get("status") != "error"]
    current = runs[-1] if runs else None
    rule = getattr(adapter, "rule_id", (current or {}).get("rule_id", "M01"))
    count = (current or {}).get("collections", {}).get("findings")
    count = count.get("count") if isinstance(count, dict) else count
    total = count if type(count) is int and count >= 0 and rule == "M01" else None
    delivered = set()

    def remember(doc, values):
        if doc not in documents:
            return
        present.add(doc)
        if Path(documents[doc].get("path", "")).suffix.lower() != ".pdf":
            return
        for value in values:
            if type(value) is int and value >= 1:
                pages[doc].add(value)

    def walk(value, doc=None, run=None):
        if isinstance(value, list):
            for row in value:
                walk(row, doc, run)
            return
        if not isinstance(value, dict) or value.get("status") in {"error", "already_returned"}:
            return
        own_run = value.get("audit_run_id", run)
        if own_run is not None and (current is None or own_run != current.get("run_id")):
            return
        doc = value.get("document_id", doc)
        locations = [value.get("physical_page"), value.get("page")]
        for field in ["physical_pages", "source_pages"]:
            if isinstance(value.get(field), list):
                locations.extend(value[field])
        if doc in documents:
            remember(doc, locations)
        cid = value.get("candidate_id")
        if (
            total is not None
            and own_run == current["run_id"]
            and isinstance(cid, str)
            and cid.startswith("C")
            and cid[1:].isdigit()
            and 1 <= int(cid[1:]) <= total
            and isinstance(value.get("candidate"), dict)
            and isinstance(value.get("evidence"), list)
        ):
            delivered.add(cid)
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, doc, own_run)

    root = Path(adapter.outputs).resolve()
    for virtual in set(getattr(adapter, "_read_receipts", {}).values()):
        prefix = "/mnt/user-data/outputs/"
        if not virtual.startswith(prefix):
            continue
        path = (root / virtual.removeprefix(prefix)).resolve()
        if not path.is_relative_to(root / "read_receipts"):
            raise ValueError("Reader reference receipts must stay in the current receipt directory")
        walk(json.loads(path.read_text()))
    ocr = getattr(adapter, "ocr_receipts", None)
    if ocr is not None:
        for doc, values in ocr.page_sets()["consulted"].items():
            remember(doc, values)
    for doc, page, _ in getattr(adapter, "submitted_images", set()):
        remember(doc, [page])
    return {
        "source_locations": [{"document_id": doc, "document_label": labels[doc], "physical_pages": sorted(pages[doc])} for doc in documents if doc in present],
        "candidate_receipts": {"candidate_count_all": total, "fully_delivered_candidate_records_lower_bound": len(delivered)} if total is not None else None,
        "not_a_semantic_review_ledger": True,
        "coverage_disclosure": {
            "semantic_review_ledger_available": False,
            "wording": "qualitative",
            "instruction": (
                "本轮没有去重语义审核台账；任何阶段的读取累计、预览累计或模型声称的已复核/剩余数都不能视为可信覆盖指标，可能含重复。"
                "各角色交接及最终报告用自然企业、材料和页段说明实际范围，不再传递精确已复核或剩余候选数。"
                "图像规则另有真实回执支持的判读计数仍按其独立口径披露。"
            ),
        },
        "usage_note": (
            "这是当前真实工具回执的辅助对照，不是完整允许清单：收到记录、原文页段或原图不等于已做语义复核，也不等于整页读完。"
            "候选接收数是去重下界，不得据此推导精确已复核数或未复核余量。位置缺席不等于没有证据；引用未列出的页码前核对原始来源字段。"
            "各企业页码必须各自绑定，不得把另一企业的页段搬过来。"
        ),
    }
