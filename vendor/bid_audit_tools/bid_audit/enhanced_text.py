"""General editing, punctuation, policy and entity evidence from original views."""

from collections import Counter, defaultdict
from difflib import SequenceMatcher
import re

from .core import norm, outcome
from .structured_text import blocks, evidence, html_tables, shared_passages


def edit_patterns(passages):
    groups = defaultdict(dict)
    for pair in passages:
        a, b = pair["text_a"], pair["text_b"]
        for tag, i, j, k, z in SequenceMatcher(
            None, a, b, autojunk=False
        ).get_opcodes():
            before, after = a[i:j], b[k:z]
            if (
                tag != "replace"
                or not 1 <= len(before) <= 16
                or not 1 <= len(after) <= 16
            ):
                continue
            if not re.search("[\u4e00-\u9fff]", before + after):
                continue
            ca, cb = a[max(0, i - 30) : j + 30], b[max(0, k - 30) : z + 30]
            identity = (pair["document_a"], pair["document_b"], before, after)
            # Repeated occurrences within one sentence are distinguished by local spans.
            context_key = (ca, cb)
            groups[identity][context_key] = {
                "before": before,
                "after": after,
                "context_a": ca,
                "context_b": cb,
                "span_a": [i, j],
                "span_b": [k, z],
                "text_a": a,
                "text_b": b,
                "evidence": pair.get("evidence", []),
                "non_template_shared_chars": pair.get("non_template_shared_chars", 0),
            }
    result = []
    for (da, db, before, after), contexts in groups.items():
        items = list(contexts.values())
        sample = max(
            items,
            key=lambda r: (
                len(REPEATED.findall(r["text_a"])) + len(REPEATED.findall(r["text_b"])),
                r.get("non_template_shared_chars", 0),
            ),
        )
        result.append(
            {
                "kind": "stable_cross_document_replacement"
                if len(items) >= 2
                else "single_replacement_for_review",
                "document_a": da,
                "document_b": db,
                "before": before,
                "after": after,
                "occurrence_count": len(items),
                "contexts": [
                    {
                        "context_a": r["context_a"],
                        "context_b": r["context_b"],
                        "span_a": r["span_a"],
                        "span_b": r["span_b"],
                    }
                    for r in items
                ],
                "text_a": sample["text_a"],
                "text_b": sample["text_b"],
                "non_template_shared_chars": sample["non_template_shared_chars"],
                "evidence": sample["evidence"],
                "review_note": "Repeated edits are observed text differences, not automatically errors or collusion. Check both original contexts and public/template origins.",
            }
        )
    return result


REPEATED = re.compile(r"([\u4e00-\u9fff]{2,4})\1")


def paired_anomalies(passages):
    result = []
    seen = set()
    for pair in passages:
        a, b = pair["text_a"], pair["text_b"]
        aa = {m.group(): m for m in REPEATED.finditer(a)}
        bb = {m.group(): m for m in REPEATED.finditer(b)}
        for token in aa.keys() | bb.keys():
            shared = token in aa and token in bb
            identity = (pair["document_a"], pair["document_b"], token, shared)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(
                {
                    "kind": "shared_error_hypothesis"
                    if shared
                    else "single_side_edit_artifact_for_review",
                    "pattern": token,
                    "same_error_on_both_sides": shared,
                    "text_a": a,
                    "text_b": b,
                    "document_a": pair["document_a"],
                    "document_b": pair["document_b"],
                    "evidence": pair.get("evidence", []),
                    "non_template_shared_chars": pair.get(
                        "non_template_shared_chars", 0
                    ),
                    "review_note": "Repeated character groups can be grammatical, quoted, OCR-produced or template-origin; review is required. A single-sided artifact is not a cross-bidder common error.",
                }
            )
    return result


def requirement_edit_patterns(ctx):
    from .enhanced_tables import response_rows, alignment_flags

    templates = template_texts(ctx)
    groups = defaultdict(list)
    for row in response_rows(ctx):
        if alignment_flags(row["requirement"], row["response"]):
            continue
        a, b = norm(row["requirement"]), norm(row["response"])
        for tag, i, j, k, z in SequenceMatcher(
            None, a, b, autojunk=False
        ).get_opcodes():
            before, after = a[i:j], b[k:z]
            if (
                tag == "replace"
                and 1 <= len(before) <= 16
                and 1 <= len(after) <= 16
                and re.search("[\u4e00-\u9fff]", before + after)
            ):
                groups[(before, after)].append(row)
    result = []
    for (before, after), rows in groups.items():
        if len({r["bidder"] for r in rows}) < 2:
            continue
        result.append(
            {
                "kind": "common_requirement_word_replacement",
                "before": before,
                "after": after,
                "occurrence_count": len(rows),
                "public_source_match": all(
                    any(
                        norm(r["requirement"]) in t
                        for t in templates[(r["round"], r["lot"])]
                    )
                    for r in rows
                ),
                "requirement_reference_verified": all(
                    any(
                        norm(r["requirement"]) in t
                        for t in templates[(r["round"], r["lot"])]
                    )
                    for r in rows
                ),
                "contexts": [
                    {
                        "document_id": r["document_id"],
                        "requirement": r["requirement"],
                        "response": r["response"],
                        "section": r.get("section"),
                    }
                    for r in rows
                ],
                "evidence": [e for r in rows for e in r["evidence"]],
                "review_note": "The immediate reference is a bid-stated requirement column; only explicit source matches verify it against tender/public originals. Standard first-person response conversions alone are not abnormal and are not automatically excluded from the evidence inventory.",
            }
        )
    return result


def m02(ctx):
    data = shared_passages(ctx)
    patterns = edit_patterns(data["findings"])
    stable = [r for r in patterns if r["occurrence_count"] >= 2]
    reference_edits = requirement_edit_patterns(ctx)
    stable.extend(reference_edits)
    return outcome(
        {
            "paired_passages_examined": len(data["findings"]),
            "stable_replacement_groups": len(stable),
            "single_replacement_groups": sum(
                r["occurrence_count"] < 2 for r in patterns
            ),
            "requirement_reference_groups": len(reference_edits),
            "template_exclusions": len(data.get("template_exclusions", [])),
        },
        stable,
        status="partial",
        single_edit_candidates=[r for r in patterns if r["occurrence_count"] < 2],
        edit_artifacts=paired_anomalies(data["findings"]),
        limitations=[
            "No fixed actor-word lexicon limits discovery. Contexts can overlap and do not establish independent document authorship. Repeated normal pronouns alone are weak evidence."
        ],
    )


def m07(ctx):
    from .enhanced_tables import align_rows, response_rows

    passages = shared_passages(ctx)["findings"]
    findings = paired_anomalies(passages)
    patterns = edit_patterns(passages)
    rows = response_rows(ctx)
    index, _ = align_rows(rows)
    changes = defaultdict(list)
    for (rnd, lot, section, rkey), rr in index.items():
        for row in rr:
            a, b = norm(row["requirement"]), norm(row["response"])
            for tag, i, j, k, z in SequenceMatcher(
                None, a, b, autojunk=False
            ).get_opcodes():
                if tag == "equal" or max(j - i, z - k) > 30:
                    continue
                changes[(rnd, lot, section, rkey, tag, a[i:j], b[k:z])].append(row)
    for key, rr in changes.items():
        if len({r["bidder"] for r in rr}) < 2:
            continue
        findings.append(
            {
                "kind": "shared_edit_candidate_not_confirmed_error",
                "section": key[2],
                "requirement": rr[0]["requirement"],
                "before": key[5],
                "after": key[6],
                "comparisons": [
                    {
                        k: r[k]
                        for k in [
                            "document_id",
                            "bidder",
                            "response",
                            "declared_deviation",
                        ]
                    }
                    for r in rr
                ],
                "evidence": [e for r in rr for e in r["evidence"]],
                "review_note": "Same literal modification to an aligned requirement is a candidate, not proof it is erroneous.",
            }
        )
    return outcome(
        {
            "paired_passages_examined": len(passages),
            "response_rows_examined": len(rows),
            "shared_error_hypotheses": sum(
                r["kind"] == "shared_error_hypothesis" for r in findings
            ),
            "single_side_edit_artifacts": sum(
                r["kind"] == "single_side_edit_artifact_for_review" for r in findings
            ),
            "stable_replacement_groups": sum(
                r["occurrence_count"] >= 2 for r in patterns
            ),
        },
        findings,
        status="partial",
        replacement_contexts=[r for r in patterns if r["occurrence_count"] >= 2],
        limitations=[
            "Single-side mechanical-edit artifacts are disclosed for comparison, never promoted to joint error. Spelling/grammar and OCR validation remain model work; no project-specific error dictionary is used."
        ],
    )


def template_texts(ctx):
    result = defaultdict(list)
    for doc in ctx.documents("tender", "public_template"):
        for block in blocks(ctx, doc):
            result[(doc["round"], doc["lot"])].append(norm(block["text"]))
    return result


def m08(ctx):
    from .punctuation_sources import punctuation_units

    source_units = Counter()
    found = defaultdict(list)
    excluded = []
    all_rows = []
    template_sources = defaultdict(list)
    for td in ctx.documents("tender", "public_template"):
        for tb in punctuation_units(td, blocks(ctx, td)):
            template_sources[(td["round"], td["lot"])].append(
                (td, tb, norm(tb["text"]))
            )
    anomaly = re.compile(r"[，,；;：:。]{2,}|[.．]{5,}|…{3,}|[（(]{2,}|[）)]{2,}")
    seen = set()
    for doc in ctx.documents("bid"):
        for block in punctuation_units(doc, blocks(ctx, doc)):
            text = block["text"]
            source_units[block["source_segmentation"]] += 1
            for m in anomaly.finditer(text):
                context = text[max(0, m.start() - 35) : m.end() + 35]
                if re.fullmatch(r"[.． …]+", m.group()):
                    line_suffix = text[m.end() :].split("\n", 1)[0]
                    toc_lines = re.findall(
                        r"[^\n]*[.．]{5,}\s*\d+(?:[-–]\d+)?\s*(?:页)?\s*(?:\n|$)", text
                    )
                    if len(toc_lines) >= 2 and re.fullmatch(
                        r"\s*\d+(?:[-–]\d+)?\s*(?:页)?\s*", line_suffix
                    ):
                        continue
                if re.fullmatch(r"[.． …]+", m.group()) and (
                    re.search(r"\d+$", context.rstrip()) or "目录" in text
                ):
                    continue
                local = text[max(0, m.start() - 14) : m.end() + 14]
                ref = evidence(
                    ctx,
                    doc,
                    block,
                    context,
                    extraction="punctuation_context",
                    punctuation=norm(m.group()),
                    original_punctuation=m.group(),
                    source_segmentation=block["source_segmentation"],
                    source_unit_locator=block.get("source_unit_locator"),
                )
                identity = (doc["id"], tuple(block["physical_pages"]), norm(context))
                if identity in seen:
                    continue
                seen.add(identity)
                matched = next(
                    (
                        (td, tb)
                        for td, tb, normalized in template_sources[
                            (doc["round"], doc["lot"])
                        ]
                        if norm(local) in normalized
                    ),
                    None,
                )
                if matched is not None:
                    td, tb = matched
                    excluded.append(
                        {
                            "kind": "template_punctuation_exclusion",
                            "punctuation": norm(m.group()),
                            "original_punctuation": m.group(),
                            "context": context,
                            "matched_local_context": local,
                            "evidence": [ref],
                            "template_source": {
                                "evidence": [
                                    evidence(
                                        ctx,
                                        td,
                                        tb,
                                        tb["text"],
                                        extraction="actual_template_punctuation_context",
                                    )
                                ]
                            },
                        }
                    )
                    continue
                row = {
                    "document_id": doc["id"],
                    "bidder": doc["bidder"],
                    "context": context,
                    "punctuation": norm(m.group()),
                    "original_punctuation": m.group(),
                    "evidence": [ref],
                    "round": doc["round"],
                    "lot": doc["lot"],
                }
                all_rows.append(row)
                found[(doc["round"], doc["lot"], norm(m.group()))].append(row)
    findings = []
    for scope, rr in found.items():
        groups = []
        for row in rr:
            group = next(
                (
                    g
                    for g in groups
                    if SequenceMatcher(
                        None,
                        norm(g[0]["context"]),
                        norm(row["context"]),
                        autojunk=False,
                    ).ratio()
                    >= 0.85
                ),
                None,
            )
            if group is None:
                groups.append([row])
            else:
                group.append(row)
        for group in groups:
            if len({r["bidder"] for r in group}) >= 2:
                findings.append(
                    {
                        "kind": "shared_punctuation_context",
                        "punctuation": scope[2],
                        "context": group[0]["context"],
                        "contexts": [
                            {"document_id": r["document_id"], "text": r["context"]}
                            for r in group
                        ],
                        "evidence": [e for r in group for e in r["evidence"]],
                    }
                )
    return outcome(
        {
            "template_contexts_excluded": len(excluded),
            "punctuation_input_basis": "original visible units; no artificial HTML/table separators",
            "synthetic_table_separators_used": False,
            "source_units_examined": dict(source_units),
            "shared_contexts": len(findings),
            "non_template_anomaly_occurrences": len(all_rows),
        },
        findings,
        status="partial",
        template_exclusions=excluded,
        unpaired_anomalies=all_rows,
        limitations=[
            "Anomaly detection uses actual visible characters within original HTML/DOCX units, not delimiters inserted by plain-text conversion. Width/whitespace normalization is only used for grouping/source matching after detection. Original punctuation/context and source positions are retained; OCR and original-image verification remain necessary."
        ],
    )


def m05(ctx):
    titles = (
        "政策适用性说明",
        "中小企业声明函",
        "残疾人福利性单位声明函",
        "监狱企业",
        "节能产品",
        "环境标志产品",
    )
    forms = []
    index = defaultdict(list)
    seen = set()
    coverage = []
    for doc in ctx.documents("bid"):
        found = set()
        for block in blocks(ctx, doc):
            text = norm(block["text"])
            present = [t for t in titles if t in text]
            if not present:
                continue
            # A TOC mention alone is not a recovered form body.
            if "目录" in text[:80] and len(present) > 2:
                continue
            found.update(present)
            markers = re.findall(
                r"(?:暂无|不适用|不涉及|无此项|无相关|无此类)|[/／]{2,}|[_＿]{3,}", text
            )
            table_rows = [t["rows"] for t in html_tables(block["raw"])]
            ident = (
                doc["id"],
                tuple(block["physical_pages"]),
                tuple(present),
                tuple(markers),
            )
            if ident in seen:
                continue
            seen.add(ident)
            refs = [
                evidence(
                    ctx, doc, block, block["text"], extraction="policy_form_source"
                )
            ]
            row = {
                "kind": "policy_form_observation",
                "document_id": doc["id"],
                "form_titles": present,
                "default_markers": markers,
                "table_rows": table_rows,
                "blank_cell_count": sum(
                    not norm(c) for table in table_rows for rr in table for c in rr
                ),
                "evidence": refs,
                "form_body_status": "candidate_body_not_verified",
                "review_note": "Title/blank/default similarity may be a mandatory or public form; verify optional filling space and source before treating as a clue.",
            }
            forms.append(row)
            if markers:
                index[
                    (doc["round"], doc["lot"], tuple(present), tuple(markers))
                ].append({**row, "bidder": doc["bidder"]})
        coverage.append(
            {
                "document_id": doc["id"],
                "titles_located": sorted(found),
                "titles_not_located": [t for t in titles if t not in found],
            }
        )
    findings = [
        {
            "kind": "policy_default_pattern",
            "form_titles": list(k[2]),
            "ordered_default_markers": list(k[3]),
            "comparisons": [
                {x: r[x] for x in ["document_id", "blank_cell_count", "table_rows"]}
                for r in rr
            ],
            "evidence": [e for r in rr for e in r["evidence"]],
        }
        for k, rr in index.items()
        if len({r["bidder"] for r in rr}) >= 2
    ]
    return outcome(
        {"policy_form_observations": len(forms), "form_coverage": coverage},
        findings,
        status="partial",
        form_observations=forms,
        limitations=[
            "Full original form candidates, cell blanks and ordered defaults are exposed. A matching form title or platform default is not abnormal filling; missing titles do not prove absence."
        ],
    )


def performance_table(grid):
    if not grid:
        return None
    headers = "".join(norm(x) for x in grid[0])
    if not (
        "项目名称" in headers
        and any(
            w in headers for w in ["客户", "用户", "合同", "金额", "联系", "采购人"]
        )
    ):
        return None
    body = []
    for row in grid[1:]:
        values = [norm(c) for c in row[1:]]
        if any(v not in {"", "无", "暂无", "不适用", "/", "-", "——"} for v in values):
            body.append(row)
    return {
        "table_rows": grid,
        "nonempty_data_row_count": len(body),
        "nonempty_data_rows": body,
        "qualification_verdict": None,
        "review_note": "Count describes this located table only, not the company overall performance or eligibility. External qualification clauses and actual certificates require verification.",
    }


def m09(ctx):
    from .text_rules import ENTITY_PATTERNS

    patterns = {
        **ENTITY_PATTERNS,
        "identity_number": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "bank_account": re.compile(r"(?:账号|帐号)[：:]?\s*([0-9]{12,30})"),
        "address": re.compile(
            r"(?:注册地址|住所|经营场所|联系地址)[：:]\s*([^\n；;]{6,100})"
        ),
        "person": re.compile(
            r"(?:法定代表人|授权代表|委托代理人|项目负责人|联系人)[：:]\s*([\u4e00-\u9fff]{2,4})(?=[\s，,。；;（(]|$)"
        ),
    }
    template = template_texts(ctx)
    index = defaultdict(list)
    inventory = []
    seen = set()
    by_doc = Counter()
    performance = []
    for doc in ctx.documents("bid"):
        for block in blocks(ctx, doc):
            raw = block["text"]
            for table in html_tables(block["raw"]):
                observed = performance_table(table["rows"])
                if observed is not None:
                    performance.append(
                        {
                            **observed,
                            "document_id": doc["id"],
                            "evidence": [
                                evidence(
                                    ctx,
                                    doc,
                                    block,
                                    raw,
                                    extraction="performance_form_observation",
                                )
                            ],
                        }
                    )
            for kind, pattern in patterns.items():
                text = raw if kind in {"person", "address"} else norm(raw)
                for m in pattern.finditer(text):
                    value = m.group(1) if m.lastindex else m.group()
                    context = text[max(0, m.start() - 100) : m.end() + 100]
                    ident = (doc["id"], kind, value, context)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    refs = [
                        evidence(
                            ctx, doc, block, context, entity=value, entity_type=kind
                        )
                    ]
                    public = any(
                        norm(value) in t for t in template[(doc["round"], doc["lot"])]
                    )
                    row = {
                        "kind": "entity_occurrence",
                        "document_id": doc["id"],
                        "bidder": doc["bidder"],
                        "entity_type": kind,
                        "value": value,
                        "context": context,
                        "found_in_tender_or_public_template": public,
                        "evidence": refs,
                        "round": doc["round"],
                        "lot": doc["lot"],
                    }
                    inventory.append(row)
                    by_doc[doc["id"]] += 1
                    index[(doc["round"], doc["lot"], kind, norm(value))].append(row)
    findings = []
    for k, rr in index.items():
        if len({r["bidder"] for r in rr}) < 2:
            continue
        findings.append(
            {
                "kind": "shared_entity",
                "entity_type": k[2],
                "value": k[3],
                "public_source_match": any(
                    r["found_in_tender_or_public_template"] for r in rr
                ),
                "occurrence_count": len(rr),
                "contexts": [
                    {"document_id": r["document_id"], "text": r["context"]} for r in rr
                ],
                "evidence": [e for r in rr for e in r["evidence"]],
                "review_note": "Identity belongs to the owner in each local context, not necessarily the bidder. Shared manufacturer/issuer/buyer/bank details require role verification.",
            }
        )
    return outcome(
        {
            "extracted_entity_occurrences": dict(
                Counter(r["entity_type"] for r in inventory)
            ),
            "entity_occurrences_by_document": dict(by_doc),
            "shared_identifier_groups": len(findings),
            "performance_tables_collected": len(performance),
        },
        findings,
        status="partial",
        entity_inventory=inventory,
        performance_observations=performance,
        limitations=[
            "Original and OCR visible fields are scanned; no external relationship data. Reused certificates/customers/manufacturer identifiers are distinct from bidder principals. No history-derived entity dictionary is used."
        ],
    )
