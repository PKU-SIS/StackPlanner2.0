"""Explicit large-result navigation, preserving every M01 candidate on disk."""

import json
from collections import Counter, defaultdict

from langchain_core.tools import StructuredTool

from .knowledge import atomic


def review_features(row):
    text = row.get("text_a", "") + row.get("text_b", "")
    service = sum(word in text for word in ["实施", "安装", "调试", "培训", "售后", "维护", "验收", "服务方案", "质量保证"])
    public = sum(word in text for word in ["信用中国", "公共信用", "财务报表", "会计政策", "资产负债", "会计准则", "行政许可"])
    table = ("无偏离" in text or "负偏离" in text) and service < 2
    category = "possible_public_material" if public else "service_or_implementation" if service >= 2 else "technical_response" if table else "other_passage"
    diffs = row.get("observed_edit_patterns", row.get("differences", []))
    edits = sum(bool(d.get("before") and d.get("after") and len(d["before"]) <= 12 and len(d["after"]) <= 12 and not d["before"].isdigit() and not d["after"].isdigit()) for d in diffs)
    score = min(row.get("non_template_shared_chars", 0), 600) + 300 * (category == "service_or_implementation") + 30 * min(edits, 4)
    if category == "possible_public_material":
        score *= 0.25
    return category, score


def evidence_preview(row, evidence):
    refs = row.get("evidence_refs", [])
    sources = [evidence[r] for r in refs[:2] if r in evidence] or row.get("evidence", [])[:2]
    diffs = row.get("observed_edit_patterns", row.get("differences", []))
    return {
        "text_a": row.get("text_a", row.get("text", "")),
        "text_b": row.get("text_b", row.get("text", "")),
        "non_template_shared_chars": row.get("non_template_shared_chars"),
        "match_mode": row.get("match_mode"),
        "differences": [{k: v[:80] if isinstance(v, str) else v for k, v in d.items() if k in {"operation", "before", "after"}} for d in diffs[:8]],
        "text_delivery": "complete_candidate_passages_and_primary_source_quotes",
        "difference_preview_count": min(8, len(diffs)),
        "total_difference_patterns": len(diffs),
        "sources": [{k: e.get(k) for k in ["evidence_id", "document_id", "bidder", "page", "physical_pages", "chunk_ids", "quote"]} for e in sources],
    }


def materialize_candidates(root, virtual_root, packet, findings, evidence):
    from pathlib import Path

    root = Path(root)
    directory = root / "m01_candidates"
    directory.mkdir(exist_ok=True)
    by_id = {e["evidence_id"]: e for e in evidence}
    index = []
    categories = defaultdict(list)
    previews = []
    ranked = sorted(enumerate(findings), key=lambda item: (-review_features(item[1])[1], item[0]))
    for rank, (offset, row) in enumerate(ranked, 1):
        cid = f"C{offset + 1:06d}"
        category, score = review_features(row)
        path = virtual_root + "/m01_candidates/" + cid + ".json"
        item = {
            "candidate_id": cid,
            "finding_offset": offset,
            "review_rank": rank,
            "category_hint": category,
            "non_template_shared_chars": row.get("non_template_shared_chars"),
            "match_mode": row.get("match_mode"),
            "document_pair": [row.get("document_a"), row.get("document_b")],
            "path": path,
        }
        sources = [by_id[r] for r in row.get("evidence_refs", []) if r in by_id]
        atomic(directory / (cid + ".json"), {"candidate_id": cid, "category_hint": category, "note": "Category/priority is navigation, not source verification or a risk conclusion.", "evidence": sources, "candidate": row})
        index.append(item)
        categories[category].append(item)
        if len(previews) < 6:
            previews.append({**item, **evidence_preview(row, by_id)})
    atomic(
        directory / "index.json", {"audit_run_id": packet["summary"]["run_id"], "candidate_count": len(findings), "complete_candidate_index": True, "candidates": index, "category_counts": dict(Counter(i["category_hint"] for i in index))}
    )
    category_paths = {}
    for category, items in categories.items():
        path = directory / "categories" / (category + ".json")
        atomic(path, {"audit_run_id": packet["summary"]["run_id"], "category_hint": category, "total": len(items), "candidates": items})
        category_paths[category] = {"count": len(items), "path": virtual_root + "/m01_candidates/categories/" + category + ".json"}
    return {
        "candidate_count": len(findings),
        "preview_count": len(previews),
        "candidate_previews": previews,
        "candidate_index_path": virtual_root + "/m01_candidates/index.json",
        "category_indexes": category_paths,
        "complete_code_result_path": virtual_root + "/complete-code-result.json",
        "complete_code_result_in_this_message": False,
        "preview_is_not_full_review": True,
        "delivery": "indexed_large_M01",
        "note": (
            "All candidates/evidence/template exclusions remain in the immutable complete result. "
            "Preview is explicitly bounded, not a complete review. Read any candidate by its exact path "
            "or audit_m01_candidates; no judgement ledger is required."
        ),
    }


def bounded_html_candidates(run_id, rows, *, byte_budget=None):
    """Return whole requested records or explicit file references, never previews."""
    budget = byte_budget if byte_budget is not None else 60000

    def size_of(value):
        text = json.dumps(value, ensure_ascii=False)
        return len(text.encode("utf-8")) if byte_budget is not None else len(text)

    packet = {
        "audit_run_id": run_id,
        "delivery": "complete_records_with_explicit_continuation",
        "candidates": [],
        "remaining_candidate_ids": [],
        "oversized_candidate_refs": [],
        "complete_requested_set": False,
        "note": "All returned candidate records are complete. Remaining IDs are not yet delivered; oversized records require continuous reads from the exact original path. No semantic judgement or JSON repair.",
    }
    normal = []
    for row in rows:
        size = size_of(row)
        cid = row["candidate_id"]
        if size + 2000 > budget:
            packet["oversized_candidate_refs"].append(
                {
                    "candidate_id": cid,
                    "path": f"/mnt/user-data/outputs/bid_audit/{run_id}/m01_candidates/{cid}.json",
                    "character_count": size,
                    "read_method": "read_file with consecutive character offsets; record is unchanged",
                }
            )
        else:
            normal.append(row)
            packet["remaining_candidate_ids"].append(cid)
    for row in normal:
        remaining = list(packet["remaining_candidate_ids"])
        remaining.remove(row["candidate_id"])
        tentative = {**packet, "candidates": [*packet["candidates"], row], "remaining_candidate_ids": remaining}
        if size_of(tentative) <= budget:
            packet = tentative
    packet["complete_requested_set"] = not packet["remaining_candidate_ids"] and not packet["oversized_candidate_refs"]
    return packet


def build_candidate_tool(adapter):
    async def read(run_id: str, candidate_ids: list[str]):
        def invoke():
            import re

            if not 1 <= len(candidate_ids) <= (10000 if getattr(adapter, "resilient_delivery", False) else 8) or any(not re.fullmatch(r"C\d{6}", cid) for cid in candidate_ids):
                raise ValueError("Request 1..8 exact candidate IDs from the current index")
            root = adapter.result_root(run_id)
            rows = [json.loads((root / "m01_candidates" / (cid + ".json")).read_text()) for cid in candidate_ids]
            if getattr(adapter, "report_format", "markdown") == "html":
                return bounded_html_candidates(run_id, rows, **({"byte_budget": 512000} if getattr(adapter, "resilient_delivery", False) else {}))
            return {"audit_run_id": run_id, "candidates": rows}

        return await adapter.recorded("audit_m01_candidates", {"run_id": run_id, "candidate_ids": candidate_ids}, invoke)

    description = "按当前M01索引的C000001等ID读取完整双端候选、原文、位置和差异，单次1至8个ID。全部候选均可读，无逐条判断提交要求；Perception也可用read_file读同一JSON路径。"
    if getattr(adapter, "report_format", "markdown") == "html":
        description += (
            "按60000字符预算返回完整记录，不截断引文；按证据需要自主续读remaining_candidate_ids，单条超长时可按oversized_candidate_refs原路径用read_file连续读取；这些字段是传输边界，不是强制全量审核清单。没有返回全文的候选不能称已复核。"
        )
    if getattr(adapter, "resilient_delivery", False):
        description = (
            "按当前真实候选ID读取完整双端原文、差异和来源；可一次请求多份，不限8条。"
            "完整记录在保守上下文传输余量内返回，超大记录原样持久化并明确给出remaining_candidate_ids或oversized_candidate_refs。"
            "按证据需要自主续读，不强制全量审核；没有返回全文不能称已复核。"
        )
    return StructuredTool.from_function(coroutine=read, name="audit_m01_candidates", description=description)


def build_candidate_index_tool(adapter):
    async def navigate(run_id: str, document_pair: str = "", category_hint: str | None = None, match_mode: str | None = None, offset: int = 0, limit: int = 8, sort_by: str = "review_rank"):
        def invoke():
            if sort_by not in {"review_rank", "shared_chars"}:
                raise ValueError("sort_by must be review_rank or shared_chars; navigation is not a risk verdict")
            pair_ids = [part.strip() for part in document_pair.split("|")] if document_pair else None
            if offset < 0 or not 1 <= limit <= 32:
                raise ValueError("Index offset must be nonnegative and limit must be 1..32")
            if pair_ids is not None and (len(pair_ids) != 2 or len(set(pair_ids)) != 2):
                raise ValueError("Use a plain pair string such as d001|d007, with two distinct current document IDs; serialized JSON arrays are not accepted")
            source = json.loads((adapter.result_root(run_id) / "m01_candidates/index.json").read_text())
            if source["audit_run_id"] != run_id:
                raise ValueError("Index must belong to the registered current calculation")
            rows = source["candidates"]
            known_ids = {value for row in rows for value in row["document_pair"]}
            if pair_ids is not None and not set(pair_ids) <= known_ids:
                raise ValueError("Use two actual current index document IDs, separated by |")
            pairs = {}
            for row in rows:
                pair = tuple(sorted(row["document_pair"]))
                if pair not in pairs:
                    pairs[pair] = {"document_pair": list(pair), "candidate_count": 0, "match_mode_counts": {}}
                group = pairs[pair]
                group["candidate_count"] += 1
                mode = row["match_mode"]
                group["match_mode_counts"][mode] = group["match_mode_counts"].get(mode, 0) + 1
            filtered = [
                row for row in rows if (pair_ids is None or set(row["document_pair"]) == set(pair_ids)) and (category_hint is None or row["category_hint"] == category_hint) and (match_mode is None or row["match_mode"] == match_mode)
            ]
            if sort_by == "shared_chars":
                filtered = sorted(filtered, key=lambda row: (-(row.get("non_template_shared_chars") or 0), row["review_rank"]))
            packet = {
                "sort_by": sort_by,
                "source_locations": {},
                "source_location_unavailable_ids": [],
                "audit_run_id": run_id,
                "navigation_only": True,
                "full_passages_delivered": False,
                "candidate_count_all": len(rows),
                "document_pair_count_all": len(pairs),
                "document_pairs": [group for key, group in pairs.items() if pair_ids is None or set(key) == set(pair_ids)],
                "filters": {"document_pair": pair_ids, "category_hint": category_hint, "match_mode": match_mode},
                "matching_candidate_count": len(filtered),
                "offset": offset,
                "requested_limit": limit,
                "candidates": [],
                "next_offset": offset if offset < len(filtered) else None,
                "note": (
                    "Exact original index rows; shared_chars ordering is optional navigation, not risk scoring. "
                    "Source locations come from actual paired evidence; missing locations are unavailable, not unread or absent evidence. "
                    "Index lookup is not full passage delivery or semantic review. "
                    "All original candidates and evidence remain readable; no judgement or filtering of underlying facts."
                ),
            }
            for row in filtered[offset : offset + limit]:
                delivered = [*packet["candidates"], row]
                following = offset + len(delivered)
                locations = []
                path = adapter.result_root(run_id) / "m01_candidates" / (row["candidate_id"] + ".json")
                if path.is_file():
                    record = json.loads(path.read_text())
                    if record.get("candidate_id") != row["candidate_id"]:
                        raise ValueError("Candidate source identity does not match the index")
                    locations = [{key: source[key] for key in ("document_id", "page", "physical_pages") if key in source} for source in record.get("evidence", []) if source.get("document_id") in row["document_pair"]]
                source_locations = dict(packet["source_locations"])
                unavailable = list(packet["source_location_unavailable_ids"])
                if locations:
                    source_locations[row["candidate_id"]] = locations
                else:
                    unavailable.append(row["candidate_id"])
                proposed = {**packet, "candidates": delivered, "source_locations": source_locations, "source_location_unavailable_ids": unavailable, "next_offset": following if following < len(filtered) else None}
                if len(json.dumps(proposed, ensure_ascii=False)) > 10000:
                    break
                packet = proposed
            if len(json.dumps(packet, ensure_ascii=False)) > 10000 or (offset < len(filtered) and not packet["candidates"]):
                raise ValueError("Index metadata exceeds navigation budget; narrow the document pair or use the persisted original index")
            return packet

        return await adapter.recorded("audit_m01_index", {"run_id": run_id, "document_pair": document_pair, "category_hint": category_hint, "match_mode": match_mode, "offset": offset, "limit": limit, "sort_by": sort_by}, invoke)

    return StructuredTool.from_function(
        coroutine=navigate,
        name="audit_m01_index",
        description="查询当前M01完整候选索引，document_pair是普通字符串，如d001|d007（顺序不限），留空表示全部；不要传JSON数组或JSON数组字符串。可按此文档对、category_hint、match_mode筛选；offset从0，limit1..32；sort_by默认review_rank保持原有排名，可选shared_chars按共享字符数降序、同分按原排名。source_locations提供实际双端来源页码，缺失显式列出，不交付正文也不代表已复核。最多10000字符，完整索引行不裁剪，next_offset按实际返回条数续读，避免二次预览。返回全部或指定文档对计数和当前筛选窗口的原始索引行，避免为少量候选通读巨大JSON索引。此工具只导航、不交付双端正文、不算语义复核；正文仍用audit_m01_candidates或原路径read_file。按结论需要自主查询，不要求逐页读完索引或逐条提交判断。",
    )
