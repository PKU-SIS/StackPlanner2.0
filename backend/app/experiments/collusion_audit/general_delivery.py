"""Explicit current-rule candidate navigation, never model verdicts."""

import json
import re
from collections import Counter
from pathlib import Path

from .knowledge import atomic


def priority(row):
    text = json.dumps(row, ensure_ascii=False)
    score = float(row.get("occurrence_count", 0)) * 8 + min(float(row.get("non_template_shared_chars", 0)), 600)
    if row.get("kind") in {
        "shared_unfavorable_response",
        "shared_numeric_mismatch_for_semantic_review",
        "shared_error_hypothesis",
    }:
        score += 800
    if row.get("kind") == "stable_cross_document_replacement":
        score += 300
    if row.get("kind") == "single_side_edit_artifact_for_review":
        score += 250
    if row.get("entity_type") in {"person", "identity_number", "mobile_phone", "email"}:
        score += 400
    if row.get("kind") == "common_requirement_word_replacement" or row.get("public_source_match") or row.get("kind") == "shared_entity" and row.get("entity_type") == "medical_certificate":
        score *= 0.2
    # Navigation prominence is not a verdict: surface actual scoped constraints
    # before repeated generic validity clauses, retaining every source file.
    if row.get("field") == "maximum_price" and row.get("contains_arabic_number"):
        score += 900
        if row.get("scope_hint") == "per_unit_or_item":
            score += 250
    if row.get("document_role") == "tender":
        score += 300
    if row.get("source_scope") == "bid_quotation":
        score += 200
    score += 100 * sum(w in text for w in ["安装", "实施", "培训", "服务方案"])
    if any(w in text for w in ["信用中国", "财务报表", "会计政策"]):
        score *= 0.25
    # Structured record labels are navigation hints, not a public-source verdict.
    if sum(label in text for label in ["许可机关", "数据来源单位", "统一社会信用代码", "行政许可决定文书号"]) >= 3:
        score *= 0.25
    if row.get("shared_visible_identifiers"):
        score += 500
    if row.get("public_material_hint"):
        score *= 0.2
    return score


def balanced_sources(rows, limit):
    """Round-robin actual document IDs so repetition cannot hide the other side."""
    if len(rows) <= limit:
        return list(rows)
    groups = {}
    for row in rows:
        identity = str(row.get("document_id") or row.get("doc_id") or "") if isinstance(row, dict) else ""
        groups.setdefault(identity, []).append(row)
    selected = []
    depth = 0
    while len(selected) < limit:
        added = False
        for group in groups.values():
            if depth < len(group):
                selected.append(group[depth])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        depth += 1
    return selected


def selected_preview_offsets(ranked, limit=6):
    """Cover distinct observed kinds before repeating one prolific category."""
    if len(ranked) <= 20:
        return {offset for offset, _ in ranked}
    groups = {}
    for offset, row in ranked:
        groups.setdefault(row.get("kind", ""), []).append(offset)
    selected = []
    depth = 0
    while len(selected) < min(limit, len(ranked)):
        for group in groups.values():
            if depth < len(group):
                selected.append(group[depth])
                if len(selected) == limit:
                    break
        depth += 1
    return set(selected)


def collection_statistics(findings):
    result = {
        "counts_are_risk_verdicts": False,
        "unit": "candidate group count, not independent errors or nonoverlapping occurrences",
    }
    for name in ["kind", "before", "after", "section", "match_mode", "entity_type", "punctuation", "pattern"]:
        result["by_" + name] = dict(Counter(str(row[name]) for row in findings if row.get(name) is not None))
    result["by_document_pair"] = dict(Counter("|".join([row["document_a"], row["document_b"]]) for row in findings if row.get("document_a") and row.get("document_b")))
    return result


def materialize(root, virtual_root, packet, findings, evidence, collections=None):
    root = Path(root)
    directory = root / "candidates"
    directory.mkdir(exist_ok=True)
    by_id = {e["evidence_id"]: e for e in evidence}
    index = []
    previews = []
    summary_statistics = collection_statistics(findings)
    ranked = sorted(enumerate(findings), key=lambda r: (-priority(r[1]), r[0]))
    preview_offsets = selected_preview_offsets(ranked)
    for rank, (offset, row) in enumerate(ranked, 1):
        cid = f"C{offset + 1:06d}"
        sources = [by_id[r] for r in row.get("evidence_refs", []) if r in by_id]
        full = {"candidate_id": cid, "candidate": row, "evidence": sources}
        atomic(directory / (cid + ".json"), full)
        item = {
            "candidate_id": cid,
            "finding_offset": offset,
            "review_rank": rank,
            "kind": row.get("kind"),
            "path": virtual_root + "/candidates/" + cid + ".json",
        }
        item.update(
            {
                key: row[key]
                for key in [
                    "context",
                    "punctuation",
                    "pattern",
                    "before",
                    "after",
                    "occurrence_count",
                    "section",
                    "non_template_shared_chars",
                    "numeric_relation",
                    "entity_type",
                    "value",
                    "sha256",
                ]
                if key in row
            }
        )
        item["document_pair"] = [row["document_a"], row["document_b"]] if row.get("document_a") and row.get("document_b") else sorted({e["document_id"] for e in sources})
        item["evidence_count"] = len(sources)
        if "context" in row and "punctuation" in row:
            pages = {}
            for source in sources:
                doc = source.get("document_id")
                if doc:
                    known = source.get("physical_pages") or ([source["page"]] if type(source.get("page")) is int else [])
                    pages.setdefault(doc, set()).update(known)
            item["source_pages_by_document"] = {doc: sorted(values) for doc, values in pages.items()}
        index.append(item)
        if offset in preview_offsets:
            # Complete selected body; bound oversized multi-context arrays explicitly.
            body = dict(row)
            omitted = {}
            for key in ["contexts", "comparisons"]:
                if isinstance(body.get(key), list) and len(body[key]) > 6:
                    omitted[key] = {"total": len(body[key]), "shown": 6}
                    body[key] = balanced_sources(body[key], 6)
            previews.append(
                {
                    **item,
                    "candidate": body,
                    "evidence": balanced_sources(sources, 6),
                    "explicit_subset_fields": {
                        **omitted,
                        "evidence": {
                            "shown": min(6, len(sources)),
                            "total": len(sources),
                        },
                    },
                }
            )
    literal_rows = [(row, item) for item in index for row in [findings[item["finding_offset"]]] if "context" in row and "punctuation" in row]
    if literal_rows:
        groups = Counter()
        matrix = {}
        docsets = Counter()
        cross = single = 0
        for row, item in literal_rows:
            context = str(row["context"])
            if any(word in context for word in ["是否偏离", "无偏离", "商务条款偏离表", "商务条件响应表"]):
                group = "deviation_table_marker"
            elif any(word in context for word in ["配置清单", "设备名称", "产品名称"]):
                group = "configuration_marker"
            elif re.search(r"\.{5,}|…{3,}", context) or any(word in context for word in ["公司全称", "签署和盖章"]):
                group = "placeholder_marker"
            else:
                group = "unclassified_context"
            docs = "|".join(item["document_pair"])
            groups[group] += 1
            matrix.setdefault(group, Counter())[docs] += 1
            docsets[docs] += 1
            cross += len(item["document_pair"]) >= 2
            single += len(item["document_pair"]) == 1
        summary_statistics["literal_context_navigation"] = {
            "by_marker_group": dict(groups),
            "by_document_set": dict(docsets),
            "by_marker_group_and_document_set": {k: dict(v) for k, v in matrix.items()},
            "cross_document_candidates": cross,
            "single_document_candidates": single,
            "unattributed_document_candidates": len(literal_rows) - cross - single,
            "semantic_location_verified": False,
            "note": (
                "Current findings only; unpaired anomalies stay separate. Literal words/structure in actual short source contexts only; "
                "unknown is not prose or a risk verdict. Table titles, body ownership and public-source origin require source review. "
                "No mandatory full reclassification ledger."
            ),
        }
    atomic(
        directory / "index.json",
        {
            "audit_run_id": packet["summary"]["run_id"],
            "candidate_count": len(findings),
            "summary_statistics": summary_statistics,
            "candidates": index,
            "complete_index": True,
        },
    )
    supplements = {}
    for name in [
        "raw_syntax_representatives",
        "raw_metadata_key_observations",
        "requirement_copy_pairs",
        "alignment_conflicts",
        "same_direction_review_pairs",
        "template_exclusions",
        "form_observations",
        "configuration_observations",
        "configuration_contexts",
        "supplemental_quote_observations",
        "supplemental_quote_comparisons",
        "supplemental_quote_ambiguities",
        "supplemental_quote_conflicts",
        "supplemental_tender_price_observations",
        "budget_observations",
        "performance_observations",
        "replacement_contexts",
    ]:
        rows = (collections or {}).get(name, [])
        if not rows:
            continue
        selected = []
        groups = set()
        for row in sorted(rows, key=priority, reverse=True):
            grouping = (row.get("document_id"), row.get("field"), row["scope_hint"]) if row.get("scope_hint") else row.get("document_id") or row.get("punctuation") or row.get("pattern") or row.get("field") or str(len(selected))
            if grouping in groups and len(rows) > 12:
                continue
            groups.add(grouping)
            sources = [by_id[r] for r in row.get("evidence_refs", []) if r in by_id]
            selected.append({"record": row, "evidence": balanced_sources(sources, 4), "evidence_total": len(sources)})
            if len(selected) >= (len(rows) if len(rows) <= 12 else 6):
                break
        compact_index = []
        if len(rows) <= 100:
            for offset, record in enumerate(rows):
                entry = {"offset": offset}
                entry.update(
                    {
                        key: record[key]
                        for key in [
                            "kind",
                            "form_titles",
                            "default_markers",
                            "blank_cell_count",
                            "form_body_status",
                            "context",
                            "matched_local_context",
                            "punctuation",
                            "requirement_reference_verified",
                            "template_source_positions",
                            "document_ids",
                            "resource_type_hint",
                            "observed_literal_pdf_keys",
                            "decoded_preview",
                            "sha256",
                            "window_hash_groups",
                            "representative_source_positions",
                            "section",
                            "requirement",
                            "response_a",
                            "response_b",
                            "document_a",
                            "document_b",
                            "document_id",
                            "before",
                            "after",
                            "occurrence_count",
                        ]
                        if key in record
                    }
                )
                if name in {"form_observations", "template_exclusions"}:
                    source_refs = list(record.get("evidence_refs", []))
                    if isinstance(record.get("template_source"), dict):
                        source_refs += record["template_source"].get("evidence_refs", [])
                    entry["source_positions"] = [{key: by_id[ref][key] for key in ["evidence_id", "document_id", "page", "physical_pages", "chunk_ids"] if key in by_id[ref]} for ref in dict.fromkeys(source_refs) if ref in by_id]
                compact_index.append(entry)
        supplements[name] = {
            "source_observation_index": compact_index,
            "source_observation_index_complete": len(compact_index) == len(rows),
            "total": len(rows),
            "shown": len(selected),
            "examples": selected,
            "full_collection_path": virtual_root + "/" + name + ".jsonl",
            "note": "Examples are a declared subset, not full semantic review.",
        }
    response_pairs = []
    for offset, row in enumerate(findings):
        if row.get("kind") != "aligned_response_similarity" or any(key not in row for key in ["requirement", "response_a", "response_b"]):
            continue
        positions = {}
        for ref in row.get("evidence_refs", []):
            source = by_id.get(ref, {})
            doc = source.get("document_id")
            if not doc:
                continue
            item = positions.setdefault(doc, {"document_id": doc, "physical_pages": [], "evidence_ids": []})
            pages = source.get("physical_pages") or ([source["page"]] if type(source.get("page")) is int else [])
            item["physical_pages"] = sorted(set(item["physical_pages"] + pages))
            item["evidence_ids"].append(ref)
        response_pairs.append(
            {
                "candidate_id": f"C{offset + 1:06d}",
                **{key: row[key] for key in ["section", "requirement", "response_a", "response_b", "document_a", "document_b", "character_sequence_similarity"] if key in row},
                "source_positions": list(positions.values()),
                "full_candidate_path": virtual_root + f"/candidates/C{offset + 1:06d}.json",
            }
        )
    if response_pairs:
        atomic(directory / "response-pairs.json", {"view": "exact source-field projection of current aligned findings", "response_pairs": response_pairs})
        fits = len(response_pairs) <= 200 and len(json.dumps(response_pairs, ensure_ascii=False)) <= 120000
        supplements["paired_response_observations"] = {
            "total": len(response_pairs),
            "shown": len(response_pairs) if fits else 0,
            "source_observation_index": response_pairs if fits else [],
            "source_observation_index_complete": fits,
            "full_collection_path": virtual_root + "/candidates/response-pairs.json",
            "note": (
                "Full requirement and both full response texts with source locations; extra extraction fields remain in each candidate file. "
                "Availability is not semantic review. When over size limits use the persisted source view; no repair or per-row judgement ledger."
            ),
        }
    return {
        "delivery": "indexed_large_rule_result",
        "summary_statistics": summary_statistics,
        "supplementary_collections": supplements,
        "candidate_count": len(findings),
        "preview_count": len(previews),
        "preview_selection": "complete when <=20; otherwise bounded round-robin across actual finding kinds, priority within kind; not semantic review or a risk verdict",
        "candidate_previews": previews,
        "candidate_index_path": virtual_root + "/candidates/index.json",
        "complete_code_result_path": virtual_root + "/complete-code-result.json",
        "complete_code_result_in_this_message": False,
        "note": "Selected candidate bodies and source records are actual code facts, not risk verdicts. Explicit subset fields are navigation only; all remaining records are persisted and readable. No per-row judgement ledger.",
    }
