"""Deterministic navigation over complete current-run code facts; no risk inference."""

from collections import Counter, defaultdict
from pathlib import Path

from .full_results import unpack_complete_result


def ranges(values):
    result = []
    for value in sorted(set(v for v in values if type(v) is int)):
        if result and value == result[-1][1] + 1:
            result[-1][1] = value
        else:
            result.append([value, value])
    return result


def pair_sources(packet, offset):
    collections = unpack_complete_result(packet)["collections"]
    findings = collections.get("findings", [])
    if type(offset) is not int or not 0 <= offset < len(findings):
        raise ValueError("Candidate offset is outside current calculation")
    refs = findings[offset].get("evidence_refs", [])
    by_id = {row["evidence_id"]: row for row in collections.get("evidence", [])}
    if len(refs) != 2 or any(ref not in by_id for ref in refs):
        raise ValueError("Candidate must have exactly two current evidence endpoints")
    return [by_id[ref] for ref in refs]


def build_fact_index(packet):
    decoded = unpack_complete_result(packet)
    summary, cols = decoded["summary"], decoded["collections"]
    evidence = {r["evidence_id"]: r for r in cols.get("evidence", [])}
    sources = decoded["sources"] if isinstance(decoded["sources"], list) else []
    sources = [s for s in sources if isinstance(s, dict)]
    rid = summary.get("rule_id", "")
    roles = {"quote_detail", "quote_opening"} if rid in {"M13", "M14", "M15"} else {"bid"}
    pdf_only = rid in {"M06", "M10", "M11", "M12"}
    compared = [s["id"] for s in sources if s.get("role") in roles and (not pdf_only or Path(s.get("path", "")).suffix.lower() == ".pdf")]
    supplemental_pair_documents = set()
    if rid == "M15":
        for row in cols.get("supplemental_quote_comparisons", []):
            supplemental_pair_documents.update(evidence[ref]["document_id"] for ref in row.get("evidence_refs", []) if ref in evidence)
        compared = list(dict.fromkeys([*compared, *sorted(supplemental_pair_documents)]))
        roles.update(s.get("role") for s in sources if s["id"] in supplemental_pair_documents)
    channels = {r["document_id"]: r for r in summary.get("metrics", {}).get("input_channel_coverage", [])}
    source_access = {
        "documents": [
            {
                "document_id": source["id"],
                "role": source.get("role"),
                "original_read_input": source["id"],
                "original_source_tools_available": ["read_file", "project_read", "project_search"] if Path(source.get("path", "")).suffix.lower() in {".pdf", ".docx", ".txt"} else [],
                **{key: channels[source["id"]][key] for key in ["original_text_characters", "mineru_chunk_count", "pdf_pages_without_mineru_chunk_position"] if key in channels.get(source["id"], {})},
            }
            for source in sources
        ],
        "note": (
            "Not a pair-comparison member is not unreadable or unused as a baseline. "
            "No MinerU chunks does not disable original DOCX/PDF reading. "
            "Missing OCR positions do not prove the original text channel was omitted. "
            "Tool availability is not proof of complete parsing or actual model review."
        ),
    }
    findings = []
    for offset, row in enumerate(cols.get("findings", [])):
        refs = row.get("evidence_refs", [])
        missing = [ref for ref in refs if ref not in evidence]
        if missing:
            raise ValueError("Code finding references unknown evidence")
        by_doc = defaultdict(list)
        for ref in refs:
            by_doc[evidence[ref]["document_id"]].append(evidence[ref])
        navigation = {
            key: row[key]
            for key in [
                "kind",
                "before",
                "after",
                "occurrence_count",
                "section",
                "alignment_warnings",
                "field",
                "value",
                "sha256",
                "text_a",
                "text_b",
                "match_mode",
                "differences",
                "non_template_shared_chars",
                "document_a",
                "document_b",
                "dhash_distance",
                "similarity",
                "round",
                "lot",
                "statistics",
                "sorted_quotes",
                "pairwise_gaps",
                "n",
                "m",
                "equal_fraction",
                "all_prices_equal",
                "unit_pricing_verified",
                "comparison_is_risk_verdict",
                "price_kind",
                "scope_note",
            ]
            if key in row
        }
        navigation.update(
            offset=offset,
            evidence_ref_count=len(refs),
            evidence_by_document={
                doc: {
                    "resource_occurrences": len(rows),
                    "distinct_physical_pages": len({r.get("page") for r in rows if type(r.get("page")) is int}),
                    "physical_page_ranges": ranges(r.get("page") for r in rows),
                    "first_evidence_id": rows[0]["evidence_id"],
                    "last_evidence_id": rows[-1]["evidence_id"],
                }
                for doc, rows in by_doc.items()
            },
        )
        if rid == "M01":
            navigation["paired_sources"] = [
                {
                    key: evidence[ref].get(key)
                    for key in [
                        "evidence_id",
                        "document_id",
                        "bidder",
                        "page",
                        "physical_pages",
                        "chunk_ids",
                        "quote",
                    ]
                }
                for ref in refs[:2]
            ]
        if rid == "M06" and len(refs) == 2:
            navigation["pair"] = [
                {
                    key: evidence[ref].get(key)
                    for key in [
                        "evidence_id",
                        "document_id",
                        "bidder",
                        "page",
                        "image_name",
                    ]
                }
                for ref in refs
            ]
        if rid == "M01":
            from .m01_delivery import review_features

            category, priority = review_features(row)
            navigation["category_hint"] = category
            navigation["review_priority"] = priority
            navigation["candidate_id"] = f"C{offset + 1:06d}"
            navigation["candidate_path"] = "/mnt/user-data/outputs/bid_audit/" + str(summary.get("run_id")) + "/m01_candidates/" + navigation["candidate_id"] + ".json"
        findings.append(navigation)
    index = {
        "schema": "audit_fact_index_v1",
        "rule_id": rid,
        "audit_run_id": summary.get("run_id"),
        "code_status": summary.get("status"),
        "metrics": summary.get("metrics", {}),
        "warnings": summary.get("warnings", []),
        "limitations": summary.get("limitations", []),
        "comparison_scope": {
            "document_ids": compared,
            "roles": sorted(roles),
            "pdf_only": pdf_only,
            "not_compared_document_ids": [s["id"] for s in sources if s["id"] not in compared],
            "note": "Input inventory is not comparison coverage; not-compared is not a zero-match result.",
            **({"supplemental_pair_document_ids": sorted(supplemental_pair_documents)} if supplemental_pair_documents else {}),
        },
        "collections": {key: len(rows) for key, rows in cols.items()},
        "findings": findings,
        "navigation_note": (
            "All original fields, texts and order remain in complete-code-result.json and raw collections. "
            "This index contains counts and locations, not model judgements. "
            "first/last_evidence_id are navigation endpoints, not a declaration of visual review."
        ),
    }
    if rid == "M11":
        index["font_comparison"] = {
            "byte_comparison_performed": "embedded_font_occurrences" in index["metrics"],
            "embedded_font_occurrences": index["metrics"].get("embedded_font_occurrences"),
            "identical_embedded_byte_groups": sum(r.get("kind") == "identical_embedded_font_bytes" for r in cols.get("findings", [])),
            "name_or_prefix_groups": sum(r.get("kind") == "shared_font_name_or_prefix" for r in cols.get("findings", [])),
            "note": "Zero identical-byte findings is distinct from no byte comparison. Font resource presence does not prove per-glyph rendering or independent/random tag generation.",
        }
    if rid == "M06":
        pair_counts = Counter()
        for row in findings:
            if len(row.get("pair", [])) == 2:
                pair_counts[" ↔ ".join(sorted(str(p.get("bidder") or p["document_id"]) for p in row["pair"]))] += 1
        candidate_pages = defaultdict(set)
        for row in findings:
            for endpoint in row.get("pair", []):
                candidate_pages[endpoint["document_id"]].add(endpoint["page"])
        index["image_comparison"] = {
            "distance_counts": dict(Counter(str(r["dhash_distance"]) for r in cols.get("findings", []) if "dhash_distance" in r)),
            "candidate_offsets_by_dhash": {str(distance): [i for i, r in enumerate(findings) if r.get("dhash_distance") == distance] for distance in sorted({r["dhash_distance"] for r in findings if "dhash_distance" in r})},
            "unique_candidate_pages": sum(len(pages) for pages in candidate_pages.values()),
            "candidate_pages_by_document": {doc: sorted(pages) for doc, pages in candidate_pages.items()},
            "bidder_pair_counts": dict(pair_counts),
            "candidate_offsets_by_distance": sorted(
                range(len(findings)),
                key=lambda i: (findings[i].get("dhash_distance", -1), i),
            ),
            "note": "Distances are candidate-filter metrics, not risk probabilities. Each candidate is an image pair, not one displayed image.",
        }
    if rid in {"M10", "M13", "M14", "M15"}:
        index["records"] = cols.get("records", [])
    index["source_access"] = source_access
    index["evidence_document_counts"] = dict(Counter(r.get("document_id") for r in evidence.values()))
    if rid == "M06":
        # Navigation belongs before verbose per-candidate source details.
        details = index.pop("findings")
        index["findings"] = details
    return index


def compact_facts(index):
    keys = [
        "rule_id",
        "audit_run_id",
        "code_status",
        "metrics",
        "comparison_scope",
        "collections",
        "font_comparison",
        "warnings",
        "result_navigation",
        "source_access",
    ]
    result = {key: index[key] for key in keys if key in index}
    if index.get("rule_id") == "M01":
        selected = sorted(
            index.get("findings", []),
            key=lambda row: (-row.get("review_priority", 0), row["offset"]),
        )[:6]
        previews = []
        for row in selected:
            item = {
                k: row[k]
                for k in [
                    "offset",
                    "candidate_id",
                    "candidate_path",
                    "category_hint",
                    "match_mode",
                    "non_template_shared_chars",
                ]
                if k in row
            }
            item.update({key: row.get(key, "") for key in ["text_a", "text_b"]})
            item["differences"] = [{k: str(d.get(k, ""))[:80] for k in ["operation", "before", "after"]} for d in row.get("differences", [])[:8]]
            item["paired_sources"] = [
                {
                    k: e.get(k)
                    for k in [
                        "evidence_id",
                        "document_id",
                        "bidder",
                        "page",
                        "physical_pages",
                        "chunk_ids",
                        "quote",
                    ]
                }
                for e in row.get("paired_sources", [])
            ]
            previews.append(item)
        result["m01_evidence_previews"] = previews
        result["m01_preview_scope"] = {
            "total": len(index.get("findings", [])),
            "shown": len(previews),
            "shown_is_candidate_subset_not_clipped_text": True,
            "note": "Navigation only, all independent candidates remain available; previews are not full review.",
        }
    if "image_comparison" in index:
        result["image_comparison"] = dict(index["image_comparison"])
    if "records" in index:
        result["records"] = index["records"]
    if len(index.get("findings", [])) <= 20:
        result["finding_fields"] = [
            {
                k: row[k]
                for k in [
                    "offset",
                    "kind",
                    "field",
                    "value",
                    "sha256",
                    "evidence_ref_count",
                    "statistics",
                    "sorted_quotes",
                    "pairwise_gaps",
                ]
                if k in row
            }
            for row in index["findings"]
        ]
    return result
