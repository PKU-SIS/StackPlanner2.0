"""Provenance-preserving OCR table recovery and scoped parameter comparison."""

import json

from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations
import re
import unicodedata

from .core import norm, outcome
from .structured_text import blocks, evidence, html_tables

HEADER_REQ = ("采购文件规定", "招标要求", "技术要求", "采购要求")
HEADER_RESP = ("投标文件响应", "实际响应", "具体响应", "响应内容")
DEV = {"无偏离", "负偏离", "正偏离", "满足", "不满足", "响应", "不响应"}
HEADING = re.compile(r"^[（(][一二三四五六七八九十百\d]+[）)][^:：;；]{2,75}$")


def key(text):
    return (
        re.sub(r"^[★▲△\s\d.、)）(（]+", "", norm(text))
        .rstrip("。；;.")
        .replace("：", ":")
        .replace("，", ",")
    )


def section_key(text):
    text = norm(text)
    return re.sub(r"^[（(][一二三四五六七八九十百\d]+[）)]", "", text)


def is_model(text):
    return bool(re.fullmatch(r"[A-Za-z0-9 +_./\-]{2,70}", text.strip())) and bool(
        re.search("[A-Za-z]", text)
    )


def parse_grid(grid, state=None):
    state = dict(state or {})
    rows = []
    section = state.get("section", "")
    header = state.get("header")
    if not any(
        any(norm(c) in DEV or any(w in norm(c) for w in HEADER_RESP) for c in raw)
        for raw in grid
    ):
        return [], state
    for rn, raw in enumerate(grid):
        cells = [str(c or "").strip() for c in raw]
        n = [norm(c) for c in cells]
        q = next((i for i, c in enumerate(n) if any(w in c for w in HEADER_REQ)), None)
        a = next((i for i, c in enumerate(n) if any(w in c for w in HEADER_RESP)), None)
        if q is not None and a is not None:
            header = (
                q,
                a,
                next((i for i, c in enumerate(n) if "偏离" in c), None),
                len(cells),
            )
            state["header"] = header
            if "商务" in n[q]:
                section = "商务条件"
                state["table_family"] = "business"
            else:
                state["table_family"] = "technical"
            continue
        heading = next((c for c in cells if HEADING.fullmatch(c)), None)
        if heading:
            section = section_key(heading)
            continue
        di = next((i for i, c in enumerate(n) if c in DEV), None)
        qi = ai = None
        confidence = "header"
        if di is not None:
            ai = di - 1
            while ai >= 0 and (is_model(cells[ai]) or n[ai] in {"/", "-", "——"}):
                ai -= 1
            qi = ai - 1
            if ai < 0 or len(n[ai]) < 2:
                qi = ai = None
            elif qi < 0 or len(n[qi]) < 5:
                qi = -1
            else:
                confidence = "deviation_anchor"
        if qi is None and header is not None:
            hq, ha, hd, width = header
            if (
                len(cells) == width
                and max(hq, ha) < len(cells)
                and len(n[hq]) >= 5
                and len(n[ha]) >= 2
            ):
                qi, ai = hq, ha
                di = hd if hd is not None and hd < len(cells) else None
        if qi is None:
            continue
        req, resp = cells[qi] if qi >= 0 else "", cells[ai]
        if HEADING.fullmatch(req):
            section = section_key(req)
            continue
        if not resp or any(x in norm(req) for x in HEADER_REQ):
            continue
        item = next(
            (c for c in n[: max(0, qi)] if re.fullmatch(r"\d+(?:\.\d+)*", c)), ""
        )
        starts = bool(re.match(r"^[★▲△]*\s*\d+[)）.、]", req))
        if (
            rows
            and not item
            and not starts
            and di is None
            and rows[-1]["section"] == section
        ):
            rows[-1]["requirement"] += "\n" + req
            rows[-1]["response"] += "\n" + resp
            rows[-1]["table_rows"].append(rn + 1)
            continue
        rows.append(
            {
                "section": section,
                "table_family": state.get("table_family", "technical"),
                "item_no": item,
                "requirement": req,
                "response": resp,
                "declared_deviation": cells[di] if di is not None else "",
                "model": next((c for c in cells[ai + 1 : di] if is_model(c)), "")
                if di is not None
                else "",
                "table_rows": [rn + 1],
                "column_inference": confidence,
                "source_cell_count": len(cells),
                "split_column_orphan": not bool(req),
            }
        )
    state["section"] = section
    return rows, state


def parameter_label(text):
    # Numerals/operators are ignored only for candidate column alignment,
    # never for the actual numeric comparison or requirement identity.
    text = key(text)
    text = re.sub(r"\d+(?:\.\d+)?|>=|<=|≥|≤|>|<", "", text)
    return re.sub(r"[\W_]", "", text)


def recover_orphan(row, catalog):
    candidates = []
    for q in catalog:
        if not set(row.get("physical_pages", [])) & set(q.get("physical_pages", [])):
            continue
        score = SequenceMatcher(
            None,
            parameter_label(q["requirement"]),
            parameter_label(row["response"]),
            autojunk=False,
        ).ratio()
        if score >= 0.65:
            candidates.append((score, q))
    candidates.sort(key=lambda r: -r[0])
    if not candidates:
        return None
    best = candidates[0]
    competitors = [
        q
        for score, q in candidates[1:]
        if score >= best[0] - 0.025
        and key(q["requirement"]) != key(best[1]["requirement"])
    ]
    if competitors:
        tied = [
            q
            for score, q in candidates
            if score >= best[0] - 0.025
            and section_key(q.get("section", "")) == section_key(row.get("section", ""))
        ]
        distinct = {key(q["requirement"]) for q in tied}
        if len(distinct) != 1:
            return None
        best = (best[0], tied[0])
    return {
        **row,
        "requirement": best[1]["requirement"],
        "section": best[1].get("section") or row.get("section", ""),
        "item_no": best[1].get("item_no", ""),
        "requirement_evidence": best[1].get("evidence", []),
        "alignment_method": "same_page_section_unique_parameter_label",
        "split_column_orphan": False,
    }


def response_views(views):
    seeds = set()
    candidates = set()
    for block, grids in views:
        for table in grids:
            if any(
                any(w in norm(c) for w in HEADER_RESP)
                for row in table["rows"]
                for c in row
            ):
                seeds.update(block["physical_pages"])
            if any(norm(c) in DEV for row in table["rows"] for c in row):
                candidates.update(block["physical_pages"])
    if not seeds:
        seeds = set(candidates)
    selected = set(seeds)
    changed = True
    while changed:
        neighbors = {p for p in candidates if any(abs(p - q) <= 3 for q in selected)}
        changed = not neighbors <= selected
        selected.update(neighbors)
    return [
        (block, grids)
        for block, grids in views
        if set(block["physical_pages"]) & selected
    ]


def collect_catalog(ctx, doc, views):
    catalog = []
    headings = {}
    section = ""
    family = "technical"
    for block, grids in response_views(views):
        for ti, table in enumerate(grids):
            header_text = "".join(str(c) for row in table["rows"][:2] for c in row)
            if any(w in header_text for w in HEADER_RESP):
                if "商务" in header_text:
                    family = "business"
                    section = "商务条件"
                elif "技术" in header_text:
                    family = "technical"
                    section = ""
            for rn, cells in enumerate(table["rows"]):
                n = [norm(c) for c in cells]
                ordinal = next((x for x in n[:3] if re.fullmatch(r"\d{1,4}", x)), None)
                title = next((c for c in cells if HEADING.fullmatch(c)), None)
                if title and family != "business":
                    section = section_key(title)
                    if ordinal:
                        headings[(family, int(ordinal))] = section
                    continue
                if not ordinal:
                    continue
                texts = [
                    c
                    for c in cells
                    if len(norm(c)) >= 6
                    and not any(w in norm(c) for w in HEADER_REQ + HEADER_RESP)
                    and not is_model(c)
                ]
                parameter = next(
                    (
                        c
                        for c in texts
                        if re.match(r"^[★▲△]*\s*\d+[)）.、]", c)
                        or any(w in c for w in [":", "：", "≥", "≤"])
                    ),
                    None,
                )
                if parameter:
                    refs = [
                        evidence(
                            ctx,
                            doc,
                            block,
                            parameter,
                            extraction="same_document_requirement_column",
                            table_index=ti,
                            table_row=rn + 1,
                        )
                    ]
                    catalog.append(
                        {
                            "item_no": ordinal,
                            "requirement": parameter,
                            "section": section,
                            "table_family": family,
                            "physical_pages": block["physical_pages"],
                            "evidence": refs,
                        }
                    )
    for row in catalog:
        preceding = [
            i
            for kind, i in headings
            if kind == row["table_family"] and i <= int(row["item_no"])
        ]
        if row["table_family"] == "business":
            row["section"] = "商务条件"
        elif preceding:
            row["section"] = headings[(row["table_family"], max(preceding))]
    unique = {}
    for row in catalog:
        unique.setdefault(
            (row["table_family"], row["item_no"], key(row["requirement"])), row
        )
    return list(unique.values())


def infer_sections(rows, catalog):
    lookup = {
        (r.get("table_family", "technical"), r["item_no"], key(r["requirement"])): r
        for r in catalog
    }
    for row in rows:
        if row.get("table_family") == "business":
            row["section"] = "商务条件"
            continue
        match = lookup.get(
            (
                row.get("table_family", "technical"),
                row.get("item_no", ""),
                key(row["requirement"]),
            )
        )
        if match and match.get("section"):
            row["section"] = match["section"]
            row["section_inference"] = "same_document_requirement_ordinal_and_text"


def alignment_flags(requirement, response):
    def dimensions(text):
        result = {m["dimension"] for m in measurements(text)}
        if re.search(r"\d+\s*[xX×*]\s*\d+\s*[pPiI]?", text):
            result.add("resolution")
        if re.search(r"\d+(?:\.\d+)?\s*(?:英寸|inches|inch)", text, re.I):
            result.add("length")
        return result

    a, b = dimensions(requirement), dimensions(response)
    return ["incommensurate_numeric_dimensions"] if a and b and not a & b else []


def merge_source_views(rows):
    """Consolidate readings of the same physical row; keep both raw variants."""
    result = []
    for row in rows:
        pages = set(row.get("physical_pages", []))
        previous = next(
            (
                old
                for old in result
                if old["document_id"] == row["document_id"]
                and old.get("table_family", "technical")
                == row.get("table_family", "technical")
                and (
                    not old.get("section")
                    or not row.get("section")
                    or section_key(old["section"]) == section_key(row["section"])
                )
                and pages.intersection(old.get("physical_pages", []))
                and (
                    not old.get("item_no")
                    or not row.get("item_no")
                    or old["item_no"] == row["item_no"]
                )
                and SequenceMatcher(
                    None,
                    key(old["requirement"]),
                    key(row["requirement"]),
                    autojunk=False,
                ).ratio()
                >= 0.85
            ),
            None,
        )
        if previous is None:
            result.append(dict(row))
            continue
        variants = previous.get(
            "extraction_variants",
            [{k: v for k, v in previous.items() if k != "extraction_variants"}],
        )
        variants.append({k: v for k, v in row.items() if k != "extraction_variants"})
        # Length recovers truncations; neither OCR nor embedded PDF text is truth by itself.
        chosen = max(
            variants,
            key=lambda r: len(norm(r["requirement"])) + len(norm(r["response"])),
        )
        previous.update(chosen)
        previous["extraction_variants"] = variants
        previous["evidence"] = [
            e for variant in variants for e in variant.get("evidence", [])
        ]
        readings = [measurements(r["response"]) for r in variants]
        signatures = {
            str([(m["dimension"], m["values"]) for m in rr]) for rr in readings if rr
        }
        if len(signatures) > 1:
            previous["readout_conflict"] = (
                "Original/OCR numeric readings differ; inspect original before a numeric verdict."
            )
    return result


def response_rows(ctx):
    if hasattr(ctx, "_response_rows_v20"):
        return ctx._response_rows_v20
    from .tables import response_rows as legacy

    result = []
    extraction = []
    docs_with_ocr = set()
    unresolved = []
    catalogs = {}
    for doc in ctx.documents("bid"):
        views = [
            (block, html_tables(block["raw"])) for block in blocks(ctx, doc, raw=False)
        ]
        views = response_views(views)
        catalog = collect_catalog(ctx, doc, views)
        catalogs[doc["id"]] = catalog
        state = {}
        count = 0
        tables = 0
        unparsed = 0
        orphan_count = 0
        for block, grids in views:
            for ti, table in enumerate(grids):
                tables += 1
                if table.get("layout_warnings"):
                    ctx.warnings.append(
                        doc["id"]
                        + ": OCR table layout conflicts preserved for review; chunk "
                        + block["chunk_ids"][0]
                    )
                rows, state = parse_grid(table["rows"], state)
                for row in rows:
                    row["physical_pages"] = block["physical_pages"]
                    refs = [
                        evidence(
                            ctx,
                            doc,
                            block,
                            row["response"],
                            requirement_quote=row["requirement"],
                            section=row["section"],
                            table_index=ti,
                            table_rows=row["table_rows"],
                            extraction="mineru_response_table",
                        )
                    ]
                    if row["split_column_orphan"]:
                        recovered = recover_orphan(row, catalog)
                        if recovered is None:
                            unresolved.append(
                                {**row, "document_id": doc["id"], "evidence": refs}
                            )
                            continue
                        row = recovered
                        refs += row.pop("requirement_evidence", [])
                        orphan_count += 1
                    result.append(
                        {
                            **row,
                            "document_id": doc["id"],
                            "bidder": doc["bidder"],
                            "round": doc["round"],
                            "lot": doc["lot"],
                            "evidence": refs,
                        }
                    )
                    count += 1
                if not rows:
                    unparsed += 1
        geometry_count = 0
        if getattr(ctx, "_include_geometry", False) and str(
            doc["_path"]
        ).lower().endswith(".pdf"):
            from .geometric_tables import extract_responses

            selected_pages = sorted(
                {p for block, _ in views for p in block["physical_pages"]}
            )
            geometry = extract_responses(ctx, doc, selected_pages)
            infer_sections(geometry, catalog)
            geometry_count = len(geometry)
            result.extend(geometry)
        own = [r for r in result if r["document_id"] == doc["id"]]
        infer_sections(own, catalog)
        if count:
            docs_with_ocr.add(doc["id"])
        extraction.append(
            {
                "document_id": doc["id"],
                "ocr_tables_seen": tables,
                "response_rows_recovered": count,
                "original_geometry_rows_recovered": geometry_count,
                "split_column_rows_recovered": orphan_count,
                "requirement_catalog_rows": len(catalog),
                "tables_not_accepted_as_response_tables": unparsed,
            }
        )
    if len(docs_with_ocr) < len(ctx.documents("bid")):
        for row in legacy(ctx):
            doc_id = row["evidence"][0]["document_id"]
            if doc_id not in docs_with_ocr:
                result.append(
                    {
                        **row,
                        "document_id": doc_id,
                        "section": row.get("section", ""),
                        "column_inference": "legacy_ruled_table",
                    }
                )
    if getattr(ctx, "_include_geometry", False):
        result = merge_source_views(result)
    # Where OCR lost a heading, use an actual ordered multi-item requirement
    # sequence from another current original. Never align by row ordinal alone.
    by_doc = defaultdict(list)
    for row in result:
        by_doc[row["document_id"]].append(row)
    if by_doc:
        master_id = max(
            by_doc, key=lambda doc_id: len({r.get("section") for r in by_doc[doc_id]})
        )
        master = by_doc[master_id]
        master_keys = [
            (r.get("table_family", "technical"), key(r["requirement"])) for r in master
        ]
        for doc_id, rr in by_doc.items():
            if doc_id == master_id:
                continue
            for a, b, n in SequenceMatcher(
                None,
                master_keys,
                [
                    (r.get("table_family", "technical"), key(r["requirement"]))
                    for r in rr
                ],
                autojunk=False,
            ).get_matching_blocks():
                if n < 2:
                    continue
                for offset in range(n):
                    source = master[a + offset]
                    row = rr[b + offset]
                    if source.get("section"):
                        row["section"] = source["section"]
                        row["section_inference"] = (
                            "ordered_multi_item_requirement_sequence"
                        )
                        row["section_reference_document"] = master_id
    dedup = {}
    for row in result:
        k = (
            row["document_id"],
            section_key(row.get("section", "")),
            key(row["requirement"]),
            norm(row["response"]),
        )
        if k in dedup:
            old = dedup[k]
            known = {str(e) for e in old["evidence"]}
            old["evidence"].extend(e for e in row["evidence"] if str(e) not in known)
        else:
            dedup[k] = row
    for row in dedup.values():
        row["alignment_warnings"] = alignment_flags(row["requirement"], row["response"])
    ctx._response_alignment_conflicts_v20 = [
        r for r in dedup.values() if r["alignment_warnings"]
    ]
    ctx._response_rows_v20 = list(dedup.values())
    ctx._response_coverage_v20 = extraction
    ctx._unresolved_response_rows_v20 = unresolved
    return ctx._response_rows_v20


def align_rows(rows):
    index = defaultdict(list)
    for row in rows:
        if len(key(row["requirement"])) >= 5:
            index[
                (
                    row["round"],
                    row["lot"],
                    section_key(row.get("section", "")),
                    key(row["requirement"]),
                )
            ].append(row)
    ambiguous = []
    for k, rr in list(index.items()):
        if len(rr) != len({r["bidder"] for r in rr}):
            ambiguous.extend(rr)
            del index[k]
    return index, ambiguous


UNIT = {
    "mm": ("length", Decimal(1)),
    "cm": ("length", Decimal(10)),
    "m": ("length", Decimal(1000)),
    "nm": ("length", Decimal(".000001")),
    "小时": ("time", Decimal(3600)),
    "分钟": ("time", Decimal(60)),
    "秒": ("time", Decimal(1)),
    "天": ("time", Decimal(86400)),
    "年": ("years", Decimal(1)),
    "%": ("percent", Decimal(1)),
    "倍": ("magnification", Decimal(1)),
    "°": ("angle", Decimal(1)),
    "℃": ("temperature", Decimal(1)),
    "万像素": ("pixels", Decimal(10000)),
    "像素": ("pixels", Decimal(1)),
    "种": ("kinds", Decimal(1)),
    "档": ("levels", Decimal(1)),
}
MEASURE = re.compile(
    r"(>=|<=|≥|≤|>|<|不少于|不低于|不超过|至多|至少)?(\d+(?:\.\d+)?)(?:\s*[-–—~～至]\s*(\d+(?:\.\d+)?))?\s*(万像素|像素|mm|cm|nm|小时|分钟|秒|天|年|%|倍|种|档|°|℃|m)(?![A-Za-z])",
    re.I,
)


def measurements(text):
    text = unicodedata.normalize("NFKC", text)
    rows = []
    last = 0
    for m in MEASURE.finditer(text):
        unit = m[4].lower()
        dimension, scale = UNIT[unit]
        label = re.split("[；;，,。\n]", text[last : m.start()])[-1]
        label = re.sub(r"^[★▲△\d()））、.\s]+", "", label).strip(":： ")
        rows.append(
            {
                "operator": m[1] or "",
                "values": [
                    format((Decimal(m[i]) * scale).normalize(), "f")
                    for i in [2, 3]
                    if m[i] is not None
                ],
                "unit": unit,
                "dimension": dimension,
                "label": label,
                "source": m.group(),
                "uncertain": any(
                    x in text[max(last, m.start() - 5) : m.end() + 3]
                    for x in ["约", "±", "左右"]
                ),
            }
        )
        last = m.end()
    return rows


def numeric_checks(requirement, response):
    req, resp = measurements(requirement), measurements(response)
    result = []
    used = set()
    for q in req:
        candidates = [
            (i, a)
            for i, a in enumerate(resp)
            if i not in used and a["dimension"] == q["dimension"]
        ]
        if not candidates:
            continue
        i, a = max(
            candidates,
            key=lambda x: SequenceMatcher(None, q["label"], x[1]["label"]).ratio(),
        )
        if (
            q["label"]
            and a["label"]
            and SequenceMatcher(None, q["label"], a["label"]).ratio() < 0.45
        ):
            continue
        used.add(i)
        qv, av = list(map(Decimal, q["values"])), list(map(Decimal, a["values"]))
        bad = None
        relation = "needs_semantic_review"
        if not q["uncertain"] and not a["uncertain"]:
            if len(qv) == len(av) == 2:
                relation = (
                    "same_interval"
                    if qv == av
                    else "response_interval_narrower_than_required"
                    if av[0] > qv[0] or av[1] < qv[1]
                    else "response_interval_covers_required"
                )
                bad = (
                    None
                    if relation == "response_interval_narrower_than_required"
                    else False
                )
            elif len(qv) == len(av) == 1 and not a["operator"]:
                if q["operator"]:
                    op = q["operator"]
                    valid = (
                        av[0] >= qv[0]
                        if op in [">=", "≥", "不少于", "不低于", "至少"]
                        else av[0] <= qv[0]
                        if op in ["<=", "≤", "不超过", "至多"]
                        else av[0] > qv[0]
                        if op == ">"
                        else av[0] < qv[0]
                    )
                    bad = not valid
                    relation = (
                        "unfavorable_inequality" if bad else "satisfies_inequality"
                    )
                else:
                    relation = (
                        "same_point_value" if qv == av else "different_point_value"
                    )
                    bad = False if qv == av else None
        signature = json.dumps(
            [q["dimension"], q["operator"], q["values"], a["values"], relation],
            ensure_ascii=False,
        )
        result.append(
            {
                "requirement": q,
                "response": a,
                "relation": relation,
                "unfavorable": bad,
                "signature": signature,
                "semantic_judgement_needed": bad is None,
            }
        )
    return result


def template_requirement_proof(ctx, requirement, rnd, lot):
    """Normalized exact source support, never inferred from a bidder's own column."""
    needle = re.sub(
        r"^[★▲△]*(?:\(\d{1,3}\)|\d{1,3}[)）、]|\d{1,3}\.(?!\d))", "", norm(requirement)
    ).rstrip("。;.")
    if len(needle) < 8:
        return []
    if not hasattr(ctx, "_requirement_template_views_v20"):
        ctx._requirement_template_views_v20 = [
            (doc, block, norm(block["text"]))
            for doc in ctx.documents("tender", "public_template")
            for block in blocks(ctx, doc)
        ]
    matches = []
    used = set()
    for doc, block, text in ctx._requirement_template_views_v20:
        identity = (doc["id"], block["source_channel"])
        if (
            (doc["round"], doc["lot"]) != (rnd, lot)
            or identity in used
            or needle not in text
        ):
            continue
        used.add(identity)
        matches.append(
            {
                "document_id": doc["id"],
                "source_channel": block["source_channel"],
                "physical_pages": block["physical_pages"],
                "chunk_ids": block["chunk_ids"],
                "evidence": evidence(
                    ctx,
                    doc,
                    block,
                    needle,
                    extraction="actual_template_requirement_match",
                    normalized_quote=True,
                ),
            }
        )
    return matches


def m03(ctx):
    rows = response_rows(ctx)
    index, ambiguous = align_rows(rows)
    findings = []
    excluded = 0
    copies = []
    compared = 0
    threshold = float(ctx.params.get("response_similarity", 0.85))
    for (rnd, lot, section, rkey), rr in index.items():
        for a, b in combinations(rr, 2):
            if a["bidder"] == b["bidder"]:
                continue
            compared += 1
            x, y = key(a["response"]), key(b["response"])
            if x == rkey and y == rkey:
                proof = template_requirement_proof(ctx, a["requirement"], rnd, lot)
                excluded += bool(proof)
                copies.append(
                    {
                        "kind": "same_own_requirement_copy",
                        "requirement_reference_verified": bool(proof),
                        "template_source_positions": [
                            {k: v for k, v in item.items() if k != "evidence"}
                            for item in proof
                        ],
                        "section": section,
                        "document_a": a["document_id"],
                        "document_b": b["document_id"],
                        "requirement": a["requirement"],
                        "response_a": a["response"],
                        "response_b": b["response"],
                        "evidence": a["evidence"]
                        + b["evidence"]
                        + [item["evidence"] for item in proof],
                        "review_note": "Both responses repeat their own stated requirement. Only requirement_reference_verified=true has actual current tender/public source support; otherwise not a verified template exclusion. No risk verdict.",
                    }
                )
                continue
            ratio = SequenceMatcher(
                None, norm(a["response"]), norm(b["response"]), autojunk=False
            ).ratio()
            if ratio >= threshold:
                findings.append(
                    {
                        "kind": "aligned_response_similarity",
                        "section": section,
                        "requirement": a["requirement"],
                        "response_a": a["response"],
                        "response_b": b["response"],
                        "character_sequence_similarity": ratio,
                        "exact": norm(a["response"]) == norm(b["response"]),
                        "document_a": a["document_id"],
                        "document_b": b["document_id"],
                        "model_a": a.get("model"),
                        "model_b": b.get("model"),
                        "round": rnd,
                        "lot": lot,
                        "evidence": a["evidence"] + b["evidence"],
                        "review_note": "Same required response or public product specification may explain similarity; not a risk verdict.",
                    }
                )
    return outcome(
        {
            "extracted_rows": len(rows),
            "aligned_pair_comparisons": compared,
            "template_only_pairs_excluded": excluded,
            "own_requirement_copy_pairs_observed": len(copies),
            "unverified_requirement_copy_pairs": len(copies) - excluded,
            "ambiguous_rows": len(ambiguous),
            "extraction_by_document": ctx._response_coverage_v20,
        },
        findings,
        status="partial" if rows else "insufficient_data",
        response_inventory=rows,
        requirement_copy_pairs=copies,
        ambiguous_rows=ambiguous,
        unresolved_rows=getattr(ctx, "_unresolved_response_rows_v20", []),
        limitations=[
            "Equipment section and requirement text define alignment; repeated ambiguous rows stay for review. OCR errors and product-public text remain model checks."
        ],
    )


def same_direction_pairs(rows):
    index, _ = align_rows(rows)
    result = []
    for (rnd, lot, section, rkey), rr in index.items():
        negative = [r for r in rr if norm(r.get("declared_deviation")) == "负偏离"]
        if len({r["bidder"] for r in negative}) < 2:
            continue
        values = [
            [(x["dimension"], x["values"]) for x in measurements(r["response"])]
            for r in negative
        ]
        result.append(
            {
                "kind": "co_declared_negative_for_review",
                "section": section,
                "requirement": negative[0]["requirement"],
                "same_actual_numeric_values": all(v == values[0] for v in values)
                if all(values)
                else None,
                "requires_agent_review": True,
                "comparisons": [
                    {k: v for k, v in r.items() if k != "evidence"} for r in negative
                ],
                "evidence": [e for r in negative for e in r["evidence"]],
                "review_note": "Same negative label is not same direction/magnitude. Read the complete actual responses and numeric checks before judging.",
            }
        )
    return result


def m04(ctx):
    rows = response_rows(ctx)
    index, ambiguous = align_rows(rows)
    findings = []
    review = []
    compared = 0
    for (rnd, lot, section, rkey), rr in index.items():
        matches = defaultdict(list)
        for row in rr:
            checks = numeric_checks(row["requirement"], row["response"])
            negative = norm(row.get("declared_deviation")) == "负偏离"
            for check in checks:
                if check["unfavorable"] is True or check["relation"] in [
                    "response_interval_narrower_than_required",
                    "different_point_value",
                ]:
                    matches[check["signature"]].append(
                        {**row, "numeric_check": check, "declared_negative": negative}
                    )
            if (
                not checks
                or negative
                or any(c["semantic_judgement_needed"] for c in checks)
            ):
                review.append({**row, "numeric_checks": checks})
        for signature, matched in matches.items():
            unique = {r["bidder"]: r for r in matched}
            if len(unique) < 2:
                continue
            compared += 1
            matched = list(unique.values())
            check = matched[0]["numeric_check"]
            confirmed = check["unfavorable"] is True or all(
                r["declared_negative"] for r in matched
            )
            findings.append(
                {
                    "kind": "shared_unfavorable_response"
                    if confirmed
                    else "shared_numeric_mismatch_for_semantic_review",
                    "section": section,
                    "requirement": matched[0]["requirement"],
                    "round": rnd,
                    "lot": lot,
                    "numeric_relation": check["relation"],
                    "same_numeric_signature": signature,
                    "comparisons": [
                        {k: v for k, v in r.items() if k != "evidence"} for r in matched
                    ],
                    "evidence": [e for r in matched for e in r["evidence"]],
                    "review_note": "Same range narrowing/point mismatch is a candidate; direction, allowed choices and actual values require model review. Self-declared no deviation is not proof.",
                }
            )
        # Non-numeric declared-negative responses remain comparable by full content.
        declared = defaultdict(list)
        for r in rr:
            if norm(r.get("declared_deviation")) == "负偏离" and not numeric_checks(
                r["requirement"], r["response"]
            ):
                declared[norm(r["response"])].append(r)
        for text, matched in declared.items():
            if len({r["bidder"] for r in matched}) >= 2:
                findings.append(
                    {
                        "kind": "shared_declared_negative_text",
                        "section": section,
                        "requirement": matched[0]["requirement"],
                        "response": matched[0]["response"],
                        "evidence": [e for r in matched for e in r["evidence"]],
                        "review_note": "Matching negative label alone is insufficient; inspect actual response.",
                    }
                )
    return outcome(
        {
            "extracted_rows": len(rows),
            "shared_numeric_groups": compared,
            "rows_requiring_semantic_review": len(review),
            "ambiguous_rows": len(ambiguous),
            "extraction_by_document": ctx._response_coverage_v20,
        },
        findings,
        status="partial" if rows else "insufficient_data",
        same_direction_review_pairs=same_direction_pairs(rows),
        review_rows=review,
        response_inventory=rows,
        ambiguous_rows=ambiguous,
        unresolved_rows=getattr(ctx, "_unresolved_response_rows_v20", []),
        limitations=[
            "Arithmetic comparison preserves units/operators and separates interval coverage from semantic benefit. Different magnitudes are not merged; labels alone do not prove actual compliance or joint error."
        ],
    )
