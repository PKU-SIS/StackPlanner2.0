"""Source-preserving diagnostics and arithmetic, never collusion verdicts."""

import copy
import hashlib
import re
from collections import defaultdict
from datetime import UTC
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

VERSION = "body-quality-v41"


def source_chunk(raw):
    """Detach only explicit leading hierarchical paths; do not edit paper text."""
    row = copy.deepcopy(raw)
    content = row.get("content") or row.get("content_with_weight") or ""
    row["raw_content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    match = re.match(r"^\[([^\]\n]+ > [^\]\n]+)\]\s*\n", content)
    if match:
        row["structural_metadata"] = {
            "generated_path": match[1].split(" > "),
            "not_original_body": True,
        }
        row["content"] = content[match.end() :]
        row.pop("content_with_weight", None)
    else:
        row["content"] = content
    row["source_view_version"] = VERSION
    return row


QUANTITY = re.compile(r"(?P<op>≥|≤|>=|<=|>|<|不小于|不大于|不少于|不超过|小于|大于|至多|至少)?\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>°\s*[/／]\s*(?:秒|s)|度\s*[/／]\s*秒|rpm|RPM|转\s*[/／]\s*(?:分|分钟)|mm|毫米|cm|厘米|种|倍)")


def quantities(text):
    result = []
    for match in QUANTITY.finditer(text):
        unit = re.sub(r"\s", "", match["unit"])
        value = Decimal(match["n"])
        normalized = "rpm" if unit.lower() == "rpm" or "转" in unit or "°" in unit or "度" in unit else "cm" if unit in {"mm", "毫米", "cm", "厘米"} else unit
        if "°" in unit or "度" in unit:
            value /= Decimal(6)
        elif unit in {"mm", "毫米"}:
            value /= Decimal(10)
        result.append(
            {
                "source_text": match[0].strip(),
                "source_span": list(match.span()),
                "operator": match["op"] or "=",
                "normalized_value": str(value),
                "normalized_unit": normalized,
                "conversion_applied": bool("°" in unit or "度" in unit or unit in {"mm", "毫米"}),
                "calculation": (f"{match['n']} / 360 * 60 = {value} {normalized}" if "°" in unit or "度" in unit else f"{match['n']} / 10 = {value} {normalized}" if unit in {"mm", "毫米"} else f"{match['n']} = {value} {normalized}"),
                "note": "Arithmetic only; an inequality boundary is not an actual response or a shared error.",
            }
        )
    return result


def pdf_date_fact(value):
    """Only complete, explicitly zoned PDF dates get an absolute UTC instant."""

    from .pdf_rules import parse_pdf_date

    parsed = parse_pdf_date(value)
    zoned = parsed is not None and parsed.tzinfo is not None
    return {
        "source_text": value,
        "local_iso": parsed.isoformat() if parsed else None,
        "utc_iso": parsed.astimezone(UTC).isoformat() if zoned else None,
        "timezone_offset_minutes": int(parsed.utcoffset().total_seconds() / 60) if zoned else None,
        "timezone_inferred": False,
        "status": "absolute_instant" if zoned else "timezone_unspecified" if parsed else "incomplete_or_invalid",
        "note": "Raw PDF info value, not an author identity or document truth guarantee. Unknown timezone is never guessed.",
    }


def explicit_structure_labels(text):
    """Bounded literal shape aliases, not product equivalence or deviation."""
    import unicodedata

    normalized = unicodedata.normalize("NFKC", text)
    result = []
    for match in re.finditer(
        r"(?<![A-Za-z])(?P<shape>(?:单|双|多)?[A-Za-z])(?:臂)?[形型](?P<object>机架|支架|框架|结构)",
        normalized,
    ):
        result.append(
            {
                "source_text": match[0],
                "shape": match["shape"].upper(),
                "object": match["object"],
            }
        )
    return result


def price_constraints(text):
    result = []
    for clause in re.split(r"[；;\n]", text):
        kind = "upper_bound" if re.search(r"限价|上限|最高|不得超过|不高于|≤|<=", clause) else "fixed_price" if re.search(r"固定(?:价格|单价|价)|统一(?:价格|单价)", clause) else None
        if kind:
            result.append(
                {
                    "source_text": clause.strip(),
                    "kind": kind,
                    "forces_equal_bid_prices": kind == "fixed_price",
                    "note": "A maximum permits different lower prices; matching a ceiling does not by itself establish a common private source.",
                }
            )
    return result


def canonical(text):
    return re.sub(r"[\W_]+", "", text).lower()


def passages(block):
    """Keep source passages alongside matching forms; no spelling correction."""
    from .m01_recall import plain

    text = plain(block.get("text", block.get("raw", "")))
    # Keep both physical lines and sentence windows across layout line wraps.
    seen = set()
    for variant in (text, re.sub(r"\n+", "", text)):
        for part in re.split(r"[\n。；;]", variant):
            part = part.strip()
            for start in range(0, len(part), 600):
                sentence = part[start : start + 900]
                key = canonical(sentence)
                if len(key) >= 24 and key not in seen:
                    seen.add(key)
                    yield key, sentence


def _grams(text):
    return {text[i : i + 4] for i in range(0, max(0, len(text) - 3))}


def common_baseline_edits(bids, references, *, source_observations=None):
    from .reference_context import (
        ReferenceCatalog,
        material_context,
        reference_compatibility,
    )

    catalog = ReferenceCatalog(references)
    shared = defaultdict(dict)
    for doc_id, blocks in bids.items():
        for block in blocks:
            for key, text in passages(block):
                shared[key].setdefault(
                    doc_id,
                    {
                        **{
                            k: block.get(k)
                            for k in (
                                "page",
                                "physical_pages",
                                "chunk_ids",
                                "positions",
                                "source_channel",
                            )
                        },
                        "document_id": doc_id,
                        "text": text,
                    },
                )
    # OCR can differ across the same printed clause. Keep distinct original
    # variants and label matching as approximate rather than repairing text.
    original_endpoints = {key: dict(values) for key, values in shared.items()}
    keys = list(shared)
    signatures = {key: _grams(key) for key in keys}
    inverted_bids = defaultdict(set)
    for key in keys:
        for gram in signatures[key]:
            inverted_bids[gram].add(key)
    for key in keys:
        if len(shared[key]) >= 2 or len(key) < 24:
            continue
        candidates = set()
        for gram in signatures[key]:
            candidates.update(inverted_bids[gram])
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -len(signatures[key] & signatures[candidate]),
                candidate,
            ),
        )
        for other in ranked[:12]:
            if other == key or not (set(shared[other]) - set(shared[key])):
                continue
            if min(len(key), len(other)) / max(len(key), len(other)) < 0.65:
                continue
            if SequenceMatcher(None, key, other, autojunk=False).ratio() >= 0.78:
                for doc_id, source in original_endpoints[other].items():
                    shared[key].setdefault(doc_id, source)
    refs, inverted = [], defaultdict(set)
    exact = set()
    for doc_id, blocks in references.items():
        for block in blocks:
            for key, text in passages(block):
                index = len(refs)
                refs.append(
                    (
                        key,
                        doc_id,
                        {
                            **{
                                k: block.get(k)
                                for k in (
                                    "page",
                                    "physical_pages",
                                    "chunk_ids",
                                    "positions",
                                    "source_channel",
                                )
                            },
                            "text": text,
                        },
                    )
                )
                exact.add(key)
                for gram in _grams(key):
                    inverted[gram].add(index)
    result = []
    observed_groups = set()
    reference_grams = [_grams(row[0]) for row in refs]
    for key, endpoints in shared.items():
        if len(endpoints) < 2 or key in exact:
            continue
        reference_matches = catalog.lookup(next(iter(endpoints.values()))["text"])
        if source_observations is not None and reference_matches:
            source_observations.append(
                {
                    "document_ids": sorted(endpoints),
                    "bid_sources": list(endpoints.values()),
                    "reference_matches": reference_matches,
                    "note": "公共原文定位是逐片段的事实，不证明整段全部已解释；填空匹配不涵盖后续实质条款。",
                }
            )
        if any(match["query_fully_explained_by_literal_template"] for match in reference_matches):
            continue
        candidates = set()
        for gram in _grams(key):
            candidates.update(inverted.get(gram, ()))
        ranked = sorted(
            candidates,
            key=lambda i: (
                -len(signatures[key] & reference_grams[i]),
                refs[i][0],
                refs[i][1],
                i,
            ),
        )
        best = None
        options = [refs[index] for index in ranked[:30]]
        options.extend(
            (
                canonical(match["matched_template_quote"]),
                match["reference_document_id"],
                match["reference_source"],
            )
            for match in reference_matches
        )
        for rkey, doc_id, source in options:
            if min(len(key), len(rkey)) / max(len(key), len(rkey)) < 0.45:
                continue
            compatibility = reference_compatibility(next(iter(endpoints.values()))["text"], source["text"])
            if compatibility["status"] == "different_explicit_purpose":
                continue
            ratio = SequenceMatcher(None, key, rkey, autojunk=False).ratio()
            purpose_priority = int(compatibility["status"] == "same_purpose_observed")
            if 0.60 <= ratio < 1 and (best is None or (purpose_priority, ratio) > (best[4], best[0])):
                best = ratio, rkey, doc_id, source, purpose_priority
        if best is None:
            continue
        ratio, rkey, doc_id, source, _ = best
        group_key = (
            tuple(sorted((doc_id, item["text"]) for doc_id, item in endpoints.items())),
            rkey,
        )
        if group_key in observed_groups:
            continue
        observed_groups.add(group_key)
        differences = [{"shared": key[a:b], "reference": rkey[c:d]} for op, a, b, c, d in SequenceMatcher(None, key, rkey, autojunk=False).get_opcodes() if op != "equal"]
        edit_sets = []
        for endpoint in endpoints.values():
            source_key = canonical(endpoint["text"])
            edit_sets.append({(source_key[a:b], rkey[c:d]) for op, a, b, c, d in SequenceMatcher(None, source_key, rkey, autojunk=False).get_opcodes() if op != "equal" and b - a >= 2 and d - c >= 2})
        common_edits = set.intersection(*edit_sets) if edit_sets else set()
        if not common_edits:
            continue
        result.append(
            {
                "shared_reference_edits": [{"shared": a, "reference": b} for a, b in sorted(common_edits)],
                "kind": "shared_text_differs_from_reference",
                "document_ids": sorted(endpoints),
                "bid_sources": [
                    {
                        **endpoint,
                        "material_context": material_context(bids[endpoint["document_id"]], endpoint),
                    }
                    for endpoint in endpoints.values()
                ],
                "reference_compatibility": reference_compatibility(next(iter(endpoints.values()))["text"], source["text"]),
                "public_source_candidates": reference_matches,
                "reference_document_id": doc_id,
                "reference_source": source,
                "matching_similarity": round(ratio, 4),
                "comparison_differences": differences,
                "requires_original_verification": True,
                "same_as_selected_reference": False,
                "other_public_source_status": "not_verified_by_this_comparison",
                "bid_text_match_mode": "exact_normalized" if len({canonical(v["text"]) for v in endpoints.values()}) == 1 else "near_match_with_original_variants",
                "note": "Shared wording and a local reference difference are facts, not proof of error, template exclusion, or collusion. Qualification/business location alone does not establish a public source.",
            }
        )
    return result


def customer_matches(observations):
    by_name = defaultdict(list)
    for table in observations:
        rows = table.get("rows", table.get("table_rows", []))
        if not rows:
            continue
        header_index = next(
            (i for i, row in enumerate(rows[:4]) if any(re.search(r"客户|采购人|业主|用户名称|建设单位|合同甲方", str(c)) for c in row)),
            None,
        )
        if header_index is None:
            continue
        header = rows[header_index]
        col = next(i for i, c in enumerate(header) if re.search(r"客户|采购人|业主|用户名称|建设单位|合同甲方", str(c)))
        project_col = next((i for i, c in enumerate(header) if "项目" in str(c)), None)
        for row in rows[header_index + 1 :]:
            if len(row) <= col:
                continue
            name = re.sub(r"\s", "", str(row[col]))
            if len(name) < 3 or name in {"客户名称", "采购人名称", "用户名称"}:
                continue
            entry = {
                "document_id": table["document_id"],
                "bidder": table.get("bidder"),
                "page": table.get("page"),
                "chunk_ids": table.get("chunk_ids", []),
                "customer_name": name,
                "project_name": row[project_col] if project_col is not None and len(row) > project_col else None,
                "source_row": row,
            }
            if entry not in by_name[name]:
                by_name[name].append(entry)
    return [
        {
            "customer_name": name,
            "observations": values,
            "same_contract_verified": False,
            "note": "Exact customer-name co-occurrence only; different projects or a public customer do not establish shared contracts or collusion. Affiliated entities are not merged.",
        }
        for name, values in by_name.items()
        if len({v["document_id"] for v in values}) >= 2
    ]


def enrich(ctx, rule_id, data, parsed):
    """All annotations are opt-in, source-addressed, and independent of project names."""
    from .structured_text import attach, blocks

    if not any(value.get("source_view_version") == VERSION for value in (parsed or {}).values()):
        return data
    attach(ctx, parsed)
    data.setdefault("metrics", {})["source_quality_version"] = VERSION
    data["metrics"]["unmatched_text_count_is_private_origin_proof"] = False
    data["metrics"]["unmatched_text_count_meaning"] = "Characters not removed by the current lexical source filter; not verified non-template, private prose, or independent evidence."
    if rule_id == "M01":
        for candidate in data.get("findings", []):
            if candidate.get("kind") == "non_template_shared_passage":
                candidate["original_detector_kind"] = candidate["kind"]
                candidate["kind"] = "shared_text_candidate_source_not_verified"
                candidate["source_origin_status"] = "not_verified_by_lexical_residual"
                candidate["residual_interpretation"] = "Original residual count retained. A lexical unmatched remainder does not establish a private source; template fields, names, dates and contents lists require source review."
    if rule_id in {"M07", "M08"}:
        from .delimiters import audit_delimiters

        delimiter_result = audit_delimiters(ctx)
        data["delimiter_structure_findings"] = delimiter_result["findings"]
        data["delimiter_structure_observations"] = delimiter_result["observations"]
        data["delimiter_template_exclusions"] = delimiter_result["template_exclusions"]
        data["findings"] = delimiter_result["findings"] + data.get("findings", [])
        data["metrics"]["shared_delimiter_structure_candidates"] = len(delimiter_result["findings"])
        data["metrics"]["delimiter_source_unit_observations"] = len(delimiter_result["observations"])
        data["metrics"]["delimiter_detection_version"] = "delimiter-v38"
        data["metrics"]["delimiter_structure_scope"] = delimiter_result["scope"]
    if rule_id == "M08":
        data["metrics"]["all_delimiter_balance_checked"] = False
        data["metrics"]["punctuation_detection_scope"] = "Consecutive punctuation/ellipsis plus source-unit delimiter nesting and unmatched open/close observations. No exhaustive cross-page, segmentation or layout validation."
    if rule_id == "M12":
        data["metrics"]["stream_block_match_is_unicode_text_comparison"] = False
        data["metrics"]["object_number_match_is_rarity_estimate"] = False
    if rule_id == "M15":
        data["metrics"]["one_main_row_excludes_supplemental_lists"] = False
        data["metrics"]["brand_difference_alone_verifies_incomparability"] = False
    data["source_quality_notes"] = {
        "note": (
            "Generated chunk paths are metadata, not paper body. Matching normalization is not an original quote; verify paper errors with original images. A missing reference match is an unknown source, not proof of private authorship."
        )
    }
    if rule_id in {"M01", "M02", "M07"}:
        from .core import groups

        changes = []
        source_observations = []
        material_observations = []
        for group_docs in groups(ctx.documents("bid")).values():
            if len({d["bidder"] for d in group_docs}) < 2:
                continue
            sample = group_docs[0]
            refs = [d for d in ctx.documents("tender", "public_template") if d.get("round") == sample.get("round") and d.get("lot") == sample.get("lot")]
            from .reference_context import material_context

            bid_blocks = {d["id"]: list(blocks(ctx, d)) for d in group_docs}
            changes.extend(
                common_baseline_edits(
                    bid_blocks,
                    {d["id"]: list(blocks(ctx, d)) for d in refs},
                    source_observations=source_observations,
                )
            )
            # Evidence scopes remain visible even when a weak/wrong reference
            # comparison is not promoted. Never classify all historical annexes as safe.
            for doc_id, items in bid_blocks.items():
                seen_pages = set()
                for item in items:
                    text = item.get("text", "")
                    pages = tuple(item.get("physical_pages") or [])
                    if pages in seen_pages or not re.search(r"政府采购合同|甲乙双方签订|业绩合同|合同编号|采购合同", text):
                        continue
                    seen_pages.add(pages)
                    material_observations.append(
                        {
                            "document_id": doc_id,
                            "bidder": ctx.by_id[doc_id].get("bidder"),
                            **material_context(items, item),
                        }
                    )
        data["reference_source_observations"] = source_observations
        data["material_scope_observations"] = material_observations
        data["common_reference_differences"] = changes
        promote_reference_changes(ctx, data, changes)
    if rule_id in {"M02", "M07"}:
        source_collection = "findings" if rule_id == "M02" else "replacement_contexts"
        data["replacement_interpretation_facts"] = replacement_context_advice(
            data.get(source_collection, []),
            ctx.by_id,
            source_collection=source_collection,
        )
    if rule_id == "M03":
        from .response_reference import locate_response_sources

        bids = list(ctx.documents("bid"))
        references = [{**d, "blocks": list(blocks(ctx, d))} for d in ctx.documents("tender", "public_template")]
        located = locate_response_sources(data, bids, references)
        data["response_public_source_observations"] = located["observations"]
        data["metrics"]["response_reference_source_locations"] = located["summary"]
        pairs = data.get("requirement_copy_pairs", [])
        verified = sum(row.get("requirement_reference_verified") is True for row in pairs)
        data["requirement_origin_scope_observations"] = [
            {
                "source_collection": "requirement_copy_pairs",
                "total_pairs": len(pairs),
                "public_source_verified_pairs": verified,
                "bid_stated_requirement_only_pairs": len(pairs) - verified,
                "all_requirements_verified_as_tender_text": bool(pairs) and verified == len(pairs),
                "verification_stage": "initial_detector_before_additional_source_location",
                "additional_source_location_summary": located["summary"],
                "additional_source_collection": "response_public_source_observations",
                "note": "此处是基础比对的初始验证统计，不是后续来源定位后的最终结果。请核对额外来源观测中的实际招标原句和页码，分别确认能解释的部分；未定位部分才称投标书自列要求。原始标记保留，此统计不判风险。",
            }
        ]
    if rule_id in {"M03", "M04", "M07"}:
        records = []

        def walk(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key not in {"evidence", "metrics", "warnings", "limitations"}:
                        walk(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, path + [index])
            elif isinstance(value, str) and len(value) < 12000:
                facts = quantities(value)
                if facts:
                    records.append({"data_path": path, "source_text": value, "quantities": facts})

        walk(data, [])
        data["quantity_computations"] = records
        response_cards = []
        for collection in ("findings", "same_direction_review_pairs"):
            for offset, record in enumerate(data.get(collection, [])):
                comparisons = record.get("comparisons", [])
                endpoints = []
                for comparison in comparisons:
                    if not isinstance(comparison, dict) or not isinstance(comparison.get("response"), str):
                        continue
                    comparison["computed_requirement_quantities"] = quantities(comparison.get("requirement", ""))
                    comparison["computed_response_quantities"] = quantities(comparison["response"])
                    endpoints.append(
                        {
                            key: comparison.get(key)
                            for key in (
                                "document_id",
                                "bidder",
                                "physical_pages",
                                "response",
                                "computed_requirement_quantities",
                                "computed_response_quantities",
                            )
                        }
                    )
                if any(e["computed_response_quantities"] for e in endpoints):
                    response_cards.append(
                        {
                            "source_collection": collection,
                            "source_offset": offset,
                            "requirement": record.get("requirement"),
                            "comparisons": endpoints,
                            "note": "Arithmetic attached to original actual responses; not a deviation or collusion verdict. Preserve the formula when quoting converted units.",
                        }
                    )
        if response_cards:
            data["response_quantity_comparisons"] = response_cards
    if rule_id == "M04":
        categories = []
        for collection in ("findings", "same_direction_review_pairs"):
            for offset, record in enumerate(data.get(collection, [])):
                endpoints = []
                for comparison in record.get("comparisons", []):
                    if not isinstance(comparison, dict) or not isinstance(comparison.get("response"), str):
                        continue
                    labels = explicit_structure_labels(comparison["response"])
                    comparison["explicit_structure_labels"] = labels
                    endpoints.append(
                        {
                            key: comparison.get(key)
                            for key in (
                                "document_id",
                                "bidder",
                                "physical_pages",
                                "response",
                                "explicit_structure_labels",
                            )
                        }
                    )
                if not any(e["explicit_structure_labels"] for e in endpoints):
                    continue
                label_sets = [{(label["shape"], label["object"]) for label in endpoint["explicit_structure_labels"]} for endpoint in endpoints]
                common = set.intersection(*label_sets) if len(label_sets) >= 2 else set()
                categories.append(
                    {
                        "source_collection": collection,
                        "source_offset": offset,
                        "requirement": record.get("requirement"),
                        "comparisons": endpoints,
                        "shared_explicit_structure_labels": [{"shape": shape, "object": noun} for shape, noun in sorted(common)],
                        "same_whole_response_verified": False,
                        "risk_verdict_computed": False,
                        "note": (
                            "Literal letter-shape aliases only; qualifier differences are retained. Compare the shared physical category se"
                            "parately from safety features, model names and numerical magnitudes. Different wording alone does not disprove"
                            " a shared category; missing values do not establish inequality. Actual deviation and evidentiary strength requ"
                            "ire source review."
                        ),
                    }
                )
        data["categorical_response_observations"] = categories
        data["metrics"]["missing_endpoint_is_confirmed_inequality"] = False
    if rule_id == "M15":
        records = []
        for doc in ctx.documents("tender", "public_template"):
            for block in blocks(ctx, doc):
                for item in price_constraints(block["text"]):
                    records.append(
                        {
                            **item,
                            "document_id": doc["id"],
                            "physical_pages": block["physical_pages"],
                            "chunk_ids": block["chunk_ids"],
                        }
                    )
        data["reference_price_constraints"] = records
    if rule_id == "M10":
        observations = []
        for record in data.get("records", []):
            computed = {}
            for field in ("/CreationDate", "/ModDate"):
                raw = record.get("metadata", {}).get(field)
                if raw is None:
                    continue
                computed[field] = pdf_date_fact(raw)
                observations.append(
                    {
                        "bidder": record.get("bidder"),
                        "evidence": record.get("evidence"),
                        "metadata_field": field,
                        **computed[field],
                    }
                )
            record["computed_pdf_dates"] = computed
        data["pdf_timestamp_observations"] = observations
    if rule_id == "M12":
        data["pdf_named_destination_observations"] = [row for doc in ctx.documents("bid") for row in pdf_destinations(doc)]
    if rule_id == "M09":
        data["shared_customer_observations"] = customer_matches(
            [
                {
                    **t,
                    "bidder": ctx.by_id[t["document_id"]]["bidder"],
                    "page": next(
                        (e.get("page") for e in t.get("evidence", []) if e.get("page")),
                        None,
                    ),
                }
                for t in data.get("performance_observations", [])
            ]
        )
    return data


def pdf_destinations(doc):
    """Resolve PDF navigation names to page targets, without origin attribution."""
    from pypdf import PdfReader

    if Path(doc["_path"]).suffix.lower() != ".pdf":
        return []
    reader = PdfReader(doc["_path"])
    return [
        {
            "document_id": doc["id"],
            "name": str(name),
            "physical_page": (page + 1 if (page := reader.get_destination_page_number(destination)) >= 0 else None),
            "meaning": "named_page_destination",
            "note": "An internal navigation label and its actual target page, not a watermark author or proof of copying.",
        }
        for name, destination in reader.named_destinations.items()
    ]


def _person_field_context(text, value):
    text, value = canonical(str(text)), canonical(str(value))
    if not value or value.isdecimal():
        return False
    position = text.find(value)
    if position < 0:
        return False
    prefix, suffix = text[:position], text[position + len(value) :]
    labels = list(
        re.finditer(
            r"法定代表人|法定代表|授权代表|正式授权|委托代理人|联系人|姓名|公司名称|投标人名称|地址|住所|电话|传真|邮编|日期|有效期|职务",
            prefix,
        )
    )
    if labels:
        return labels[-1].group() in {
            "法定代表人",
            "法定代表",
            "授权代表",
            "正式授权",
            "委托代理人",
            "联系人",
            "姓名",
        }
    return bool(re.match(r"现任|担任|总经理|经理|董事|先生|女士", suffix))


def replacement_context_advice(rows, docs, *, source_collection="replacement_contexts"):
    roles = {
        "投标人",
        "中标人",
        "供应商",
        "采购人",
        "采购方",
        "甲方",
        "乙方",
        "我方",
        "我公司",
        "本公司",
        "用户",
        "采购单位",
    }
    result = []
    for offset, row in enumerate(rows):
        a, b = (
            canonical(str(row.get("before", ""))),
            canonical(str(row.get("after", ""))),
        )
        if not a or not b:
            continue
        bidder_a = canonical(str(docs.get(row.get("document_a"), {}).get("bidder", "")))
        bidder_b = canonical(str(docs.get(row.get("document_b"), {}).get("bidder", "")))
        identity = bool(bidder_a and bidder_b and a in bidder_a and b in bidder_b)
        contexts = row.get("contexts", [])
        standard = any(
            all(
                re.search(
                    r"认证证书|ISO\s*\d|GBT\d|统一社会信用代码|经营备案|职业道德守则|财务报表附注",
                    str(context.get(k, "")),
                    re.I,
                )
                for k in ("context_a", "context_b")
            )
            for context in contexts
        )
        contact = any(
            all(
                re.search(
                    r"法定代表人|授权代表|职务|地址|住所|联系人",
                    str(context.get(k, "")),
                )
                for k in ("context_a", "context_b")
            )
            for context in contexts
        )
        role = any(a == canonical(x) for x in roles) and any(b == canonical(x) for x in roles)
        person = not (identity or role) and any(_person_field_context(context.get("context_a", ""), row.get("before", "")) and _person_field_context(context.get("context_b", ""), row.get("after", "")) for context in contexts)
        contact = contact or person
        if not (identity or standard or contact or role):
            continue
        result.append(
            {
                "source_collection_offset": offset,
                "source_collection": source_collection,
                "document_ids": [row.get("document_a"), row.get("document_b")],
                "before": row.get("before"),
                "after": row.get("after"),
                "bidder_identity_fragment_substitution": identity,
                "standard_form_context_observed": standard,
                "identity_or_contact_field_context_observed": contact,
                "person_role_field_context_observed": person,
                "contract_or_response_role_words": role,
                "occurrence_count": row.get("occurrence_count"),
                "occurrence_count_is_not_independent_evidence_count": True,
                "cannot_alone_establish_common_error_or_authorship": True,
                "public_source_for_surrounding_prose_verified": False,
                "note": "本条只分类字段差异和局部语境，不认定整段是模板。各家写自己的身份/证照不等于更改对方原件；须另外核对不正常编辑副作用或非模板共享正文。",
            }
        )
    return result


def promote_reference_changes(ctx, data, changes):
    """Source-backed wording observations join the primary candidate interface.

    They are explicitly unverified, not computed common errors or private prose.
    Ordinary identity/role substitutions remain in the full supplemental data.
    """
    roles = {
        canonical(word)
        for word in (
            "投标人",
            "供应商",
            "中标人",
            "采购人",
            "采购单位",
            "我方",
            "我公司",
            "本公司",
            "甲方",
            "乙方",
            "用户",
        )
    }
    selected = {}
    for change in changes:
        if change.get("matching_similarity", 0) < 0.75 or len(change.get("document_ids", [])) < 2:
            continue
        edits = [e for e in change.get("shared_reference_edits", []) if not (canonical(e["shared"]) in roles and canonical(e["reference"]) in roles)]
        if not edits:
            continue
        key = (
            tuple(sorted(change["document_ids"])),
            change["reference_document_id"],
            tuple(sorted((e["shared"], e["reference"]) for e in edits)),
        )
        if key not in selected or change["matching_similarity"] > selected[key]["matching_similarity"]:
            selected[key] = {**change, "shared_reference_edits": edits}
    added = []
    for change in selected.values():
        endpoints = change["bid_sources"]
        a, b = endpoints[:2]
        reference_id = change["reference_document_id"]
        reference = change["reference_source"]

        def evidence(doc_id, source):
            return ctx.evidence(
                ctx.by_id[doc_id],
                source.get("page"),
                source["text"],
                physical_pages=source.get("physical_pages", []),
                chunk_ids=source.get("chunk_ids", []),
                positions=source.get("positions", []),
                source_channel=source.get("source_channel"),
            )

        added.append(
            {
                "kind": "shared_reference_change_for_review",
                "match_mode": "shared_reference_change",
                "document_a": a["document_id"],
                "document_b": b["document_id"],
                "participating_document_ids": change["document_ids"],
                "text_a": a["text"],
                "text_b": b["text"],
                "non_template_shared_chars": 0,
                "non_template_origin_verified": False,
                "matching_similarity": change["matching_similarity"],
                "requires_original_verification": True,
                "candidate_is_not_risk_verdict": True,
                "shared_reference_edits": change["shared_reference_edits"],
                "endpoint_material_contexts": [
                    {
                        "document_id": endpoint["document_id"],
                        **endpoint.get("material_context", {}),
                    }
                    for endpoint in endpoints
                ],
                "public_source_candidates": change.get("public_source_candidates", []),
                "reference_compatibility": change.get("reference_compatibility"),
                "selected_reference_comparison": {
                    "document_id": reference_id,
                    "quote": reference["text"],
                    "physical_pages": reference.get("physical_pages", []),
                    "page": reference.get("page"),
                    "chunk_ids": reference.get("chunk_ids", []),
                    "source_channel": reference.get("source_channel"),
                    "shared_reference_edits": change["shared_reference_edits"],
                    "same_as_selected_reference": False,
                    "other_public_source_status": "not_verified",
                },
                "evidence": [evidence(endpoint["document_id"], endpoint) for endpoint in endpoints] + [evidence(reference_id, reference)],
                "review_note": (
                    "This primary candidate is an observed shared wording change against a selected current reference, not a confir"
                    "med error, non-template origin or collusion. Verify images and exact public-source coverage; document titles a"
                    "lone cannot explain the changed clause."
                ),
            }
        )
    original = len(data["findings"])
    data["findings"].extend(added)
    metrics = data.setdefault("metrics", {})
    metrics["base_lexical_candidate_count"] = original
    metrics["reference_change_candidates_added"] = len(added)
    metrics["total_delivered_candidates"] = len(data["findings"])
    metrics["candidate_count_basis"] = "base lexical observations plus explicitly unverified reference-wording observations; never risk count"
    from collections import Counter

    for name, field in (
        ("candidate_counts_by_document_pair", None),
        ("candidate_counts_by_match_mode", "match_mode"),
    ):
        if name in metrics:
            metrics["base_" + name] = metrics[name]
        metrics[name] = dict(Counter((row["document_a"] + "|" + row["document_b"]) if field is None else row.get(field, "unclassified") for row in data["findings"] if field is not None or (row.get("document_a") and row.get("document_b"))))
    metrics["reference_candidate_pair_note"] = "For a multi-bidder reference change, the pair is representative; participating_document_ids retains all endpoints."
    return data
