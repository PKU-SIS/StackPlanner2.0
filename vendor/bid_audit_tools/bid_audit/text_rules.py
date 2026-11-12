from collections import defaultdict, Counter
from difflib import SequenceMatcher
from itertools import combinations
import re

from .core import norm, outcome, groups
from .tables import response_rows, requirement_key

ACTORS = (
    "中标供应商",
    "投标供应商",
    "供应商",
    "投标人",
    "中标人",
    "我公司",
    "本公司",
    "我单位",
    "我方",
    "我司",
)
ACTOR_RE = re.compile("|".join(ACTORS))


class Template:
    def __init__(self, ctx, round_id, lot):
        self.texts = []
        for d in ctx.documents("tender", "public_template"):
            if d["round"] == round_id and d["lot"] == lot:
                self.texts.extend(norm(p["text"]) for p in ctx.pages(d))
        self.all = "\n".join(self.texts)
        self.span = int(ctx.params.get("template_min_exact_chars", 24))
        if not 12 <= self.span <= 100:
            raise ValueError("template_min_exact_chars must be 12..100")
        self.index = defaultdict(list)
        for ti, t in enumerate(self.texts):
            for i in range(len(t) - self.span + 1):
                self.index[t[i : i + self.span]].append((ti, i))

    def strip(self, text):
        text = norm(text)
        chunks, removed, pos, start = [], 0, 0, 0
        while pos <= len(text) - self.span:
            matches = self.index.get(text[pos : pos + self.span], ())
            best = 0
            for ti, i in matches:
                t = self.texts[ti]
                n = self.span
                while (
                    pos + n < len(text) and i + n < len(t) and text[pos + n] == t[i + n]
                ):
                    n += 1
                best = max(best, n)
            if best:
                if start < pos:
                    chunks.append(text[start:pos])
                removed += best
                pos += best
                start = pos
            else:
                pos += 1
        if start < len(text):
            chunks.append(text[start:])
        return chunks, removed


def segments(ctx, doc):
    for p in ctx.pages(doc):
        # Remove only recognized platform/footer lines, retaining the original evidence.
        lines = [
            x
            for x in p["text"].splitlines()
            if not re.match(r"^\s*(广东政府采购智慧云平台|第\s*\d+\s*页)", x)
            and not (
                doc.get("bidder")
                and doc["bidder"] in x
                and re.search(r"20\d{2}[-年]\d", x)
            )
        ]
        text = "".join(lines)
        for m in re.finditer(r"[^。；;！？!?]+[。；;！？!?]?", text):
            t = m.group().strip()
            if 18 <= len(norm(t)) <= 1200:
                yield (
                    t,
                    ctx.evidence(
                        doc, p["page"], t, extraction="text_sentence_fragment"
                    ),
                )


def m01(ctx, parsed_texts=None):
    """Generic high-recall text candidates; risk interpretation stays with SP."""
    from .m01_recall import audit_m01

    return audit_m01(ctx, parsed_texts)


def m02(ctx):
    findings = []
    substitution_counts = Counter()
    for (rnd, lot), docs in groups(ctx.documents("bid")).items():
        index = defaultdict(list)
        for d in docs:
            for s, ref in segments(ctx, d):
                n = norm(s)
                actors = ACTOR_RE.findall(n)
                if not actors:
                    continue
                key = ACTOR_RE.sub("{主体}", n)
                index[key].append((n, actors, ref))
        for key, entries in index.items():
            if (
                len({x[2]["bidder"] for x in entries}) < 2
                or len({x[0] for x in entries}) < 2
            ):
                continue
            forms = sorted({a for _, acts, _ in entries for a in acts})
            substitution_counts[" ↔ ".join(forms)] += 1
            findings.append(
                {
                    "kind": "actor_word_substitution",
                    "round": rnd,
                    "lot": lot,
                    "shared_structure": key,
                    "word_forms": forms,
                    "evidence": [x[2] for x in entries],
                }
            )
    return outcome(
        {"replacement_pair_counts": dict(substitution_counts)},
        findings,
        limitations=[
            "Only the disclosed actor-word lexicon is normalized. No semantic equivalence is inferred.",
            "Standard first-person responses can be legitimate; review common template and persistent accompanying errors.",
        ],
    )


def align_responses(ctx):
    rows = response_rows(ctx)
    index = defaultdict(list)
    for row in rows:
        key = requirement_key(row["requirement"])
        if len(key) >= 12:
            index[(row["round"], row["lot"], key)].append(row)
    # Repeated requirement text within one bidder is not a unique item key.
    # Do not form Cartesian comparisons between distinct equipment/sections.
    ambiguous = [
        k for k, rr in index.items() if len(rr) != len({r["bidder"] for r in rr})
    ]
    for k in ambiguous:
        del index[k]
    if ambiguous:
        ctx.warnings.append(
            f"{len(ambiguous)} requirement keys repeat within a bidder; excluded from automatic alignment; review original response tables"
        )
    return rows, index


def m03(ctx):
    rows, index = align_responses(ctx)
    findings = []
    comparisons = 0
    template_only = 0
    threshold = float(ctx.params.get("response_similarity", 0.85))
    if not 0 <= threshold <= 1:
        raise ValueError("response_similarity must be 0..1")
    for (rnd, lot, key), rr in index.items():
        for a, b in combinations(rr, 2):
            if a["bidder"] == b["bidder"]:
                continue
            x, y = norm(a["response"]), norm(b["response"])
            comparisons += 1
            if requirement_key(x) == key and requirement_key(y) == key:
                template_only += 1
                continue
            score = SequenceMatcher(None, x, y, autojunk=False).ratio()
            if score >= threshold:
                findings.append(
                    {
                        "kind": "aligned_response_similarity",
                        "requirement": a["requirement"],
                        "round": rnd,
                        "lot": lot,
                        "character_sequence_similarity": score,
                        "exact": x == y,
                        "response_a": a["response"],
                        "response_b": b["response"],
                        "evidence": a["evidence"] + b["evidence"],
                    }
                )
    unmatched = sum(
        len(rr) for rr in index.values() if len({r["bidder"] for r in rr}) < 2
    )
    return outcome(
        {
            "extracted_rows": len(rows),
            "aligned_pair_comparisons": comparisons,
            "unaligned_response_rows": unmatched,
            "template_only_pairs_excluded": template_only,
            "candidate_threshold": threshold,
        },
        findings,
        status=("partial" if unmatched else "ok")
        if comparisons
        else "insufficient_data",
        limitations=[
            "Exact requirement text, not row number alone, establishes alignment.",
            "Character SequenceMatcher scores are retrieval aids, not semantic/legal findings.",
            "Automatic table parsing supports ruled digital tables; skipped/unmatched clauses need review.",
        ],
    )


UNIT = {
    "mm": ("length", 1),
    "cm": ("length", 10),
    "m": ("length", 1000),
    "nm": ("length", 0.000001),
    "小时": ("time", 3600),
    "分钟": ("time", 60),
    "秒": ("time", 1),
    "天": ("time", 86400),
    "%": ("percent", 1),
    "倍": ("倍", 1),
    "种": ("种", 1),
    "档": ("档", 1),
    "年": ("年", 1),
    "°": ("angle", 1),
}
NUM = re.compile(
    r"(>=|<=|≥|≤|>|<|不少于|不低于|不超过|至多|至少)?(\d+(?:\.\d+)?)\s*(mm|cm|nm|m|小时|分钟|秒|天|%|倍|种|档|年|°)"
)


def numeric_deviation(req, resp):
    from decimal import Decimal

    if any(c in req + resp for c in ["±", "～", "~", "约", "左右"]):
        return None
    q = NUM.findall(norm(req))
    a = NUM.findall(norm(resp))
    if len(q) != 1 or len(a) != 1 or not q[0][0]:
        return None
    op, val, u = q[0]
    aop, aval, au = a[0]
    if aop or UNIT[u][0] != UNIT[au][0]:
        return None
    v = Decimal(val) * Decimal(str(UNIT[u][1]))
    av = Decimal(aval) * Decimal(str(UNIT[au][1]))
    valid = (
        av >= v
        if op in (">=", "≥", "不少于", "不低于", "至少")
        else av <= v
        if op in ("<=", "≤", "不超过", "至多")
        else av > v
        if op == ">"
        else av < v
    )
    return {
        "requirement_operator": op,
        "required_value": str(v),
        "response_value": str(av),
        "canonical_unit_dimension": UNIT[u][0],
        "unfavorable": not valid,
    }


def m04(ctx):
    rows, index = align_responses(ctx)
    findings = []
    evaluated = 0
    ambiguous = 0
    for (rnd, lot, key), rr in index.items():
        bad = defaultdict(list)
        for r in rr:
            numeric = numeric_deviation(r["requirement"], r["response"])
            if numeric is not None:
                evaluated += 1
            else:
                ambiguous += 1
            declared = norm(r.get("declared_deviation")) == "负偏离"
            if declared or (numeric and numeric["unfavorable"]):
                signature = (
                    ("numeric", str(numeric))
                    if numeric and numeric["unfavorable"]
                    else ("declared", norm(r["response"]))
                )
                bad[signature].append(
                    {**r, "numeric_check": numeric, "declared_negative": declared}
                )
        for rr2 in bad.values():
            if len({r["bidder"] for r in rr2}) >= 2:
                findings.append(
                    {
                        "kind": "shared_unfavorable_response",
                        "round": rnd,
                        "lot": lot,
                        "requirement": rr2[0]["requirement"],
                        "comparisons": [
                            {k: v for k, v in r.items() if k != "evidence"} for r in rr2
                        ],
                        "evidence": [e for r in rr2 for e in r["evidence"]],
                    }
                )
    aligned_ids = {id(r) for rr in index.values() for r in rr}
    review = [
        r
        for r in rows
        if id(r) not in aligned_ids
        or numeric_deviation(r["requirement"], r["response"]) is None
    ]
    return outcome(
        {
            "extracted_rows": len(rows),
            "simple_numeric_rows_evaluated": evaluated,
            "rows_requiring_semantic_review": len(review),
            "rows_excluded_from_alignment": len(rows) - len(aligned_ids),
        },
        findings,
        status="partial" if rows else "insufficient_data",
        review_rows=review,
        limitations=[
            "Only a single explicit inequality and a single exact response quantity are numerically decidable here.",
            "Tolerance, ranges, multiple quantities, units not listed, and implicit deviations remain for model review.",
            "Declared “无偏离” is not taken as proof of compliance.",
        ],
    )


def m05(ctx):
    import pdfplumber
    from .tables import clean_page

    titles = (
        "政策适用性说明",
        "中小企业声明函",
        "残疾人福利性单位声明函",
        "监狱企业",
        "节能产品",
        "环境标志产品",
    )
    index = defaultdict(list)
    reviewed = 0
    table_index = defaultdict(list)
    for d in ctx.documents("bid"):
        for p in ctx.pages(d):
            s = norm(p["text"])
            found = [t for t in titles if t in s]
            if not found or len(s) < 100:
                continue
            reviewed += 1
            if "政策适用性说明" in s and d["_path"].suffix.lower() == ".pdf":
                with pdfplumber.open(d["_path"]) as pdf:
                    for table in clean_page(pdf.pages[p["page"] - 1]).find_tables():
                        data = table.extract()
                        if not data or "序号" not in norm(
                            "".join(str(v or "") for v in data[0])
                        ):
                            continue
                        body = [r for r in data[1:] if re.fullmatch(r"\d+", norm(r[0]))]
                        signature = tuple(tuple(norm(v) for v in r[1:]) for r in body)
                        if len(body) < 2 or not any(
                            "暂无" in "".join(r) or "不适用" in "".join(r)
                            for r in signature
                        ):
                            continue
                        blanks = [
                            norm(r[0]) for r in body if not any(norm(v) for v in r[1:])
                        ]
                        table_index[(d["round"], d["lot"], signature)].append(
                            ctx.evidence(
                                d,
                                p["page"],
                                p["text"],
                                table_bbox=list(table.bbox),
                                extraction="policy_table",
                                empty_data_row_numbers=blanks,
                                table_rows=body,
                            )
                        )
            markers = [
                m.group()
                for m in re.finditer(
                    r"(?:我司|我公司|本公司|我单位)?(?:暂无|不适用|不涉及|无此项|无相关|无此类)|[/／]{2,}|[_＿]{3,}",
                    s,
                )
            ]
            if not markers:
                continue
            # An ordered signature is factual; table layout/blank cells remain visual review.
            key = (d["round"], d["lot"], tuple(found), tuple(markers))
            index[key].append(
                ctx.evidence(d, p["page"], p["text"], matched_markers=markers)
            )
    findings = [
        {
            "kind": "policy_default_pattern",
            "round": k[0],
            "lot": k[1],
            "form_titles": list(k[2]),
            "ordered_default_markers": list(k[3]),
            "evidence": refs,
        }
        for k, refs in index.items()
        if len({r["bidder"] for r in refs}) >= 2
    ]
    findings.extend(
        {
            "kind": "policy_table_fill_pattern",
            "round": k[0],
            "lot": k[1],
            "data_cell_pattern": [list(r) for r in k[2]],
            "evidence": refs,
        }
        for k, refs in table_index.items()
        if len({r["bidder"] for r in refs}) >= 2
    )
    return outcome(
        {"policy_pages_reviewed": reviewed},
        findings,
        status="partial",
        limitations=[
            "Default markers and order are extracted. Empty cells are counted only in successfully parsed ruled policy tables.",
            "Policy templates and legitimate inapplicability must be checked by the model; this is weak auxiliary evidence.",
        ],
    )


def m07(ctx):
    rows, index = align_responses(ctx)
    changed = defaultdict(list)
    for (rnd, lot, key), rr in index.items():
        for r in rr:
            a, b = norm(r["requirement"]), norm(r["response"])
            for tag, i, j, k, right_end in SequenceMatcher(
                None, a, b, autojunk=False
            ).get_opcodes():
                before, after = a[i:j], b[k:right_end]
                if tag == "equal" or len(before) > 30 or len(after) > 30:
                    continue
                if before in ACTORS or after in ACTORS:
                    continue
                if not before and not after:
                    continue
                signature = (rnd, lot, key, tag, before, after)
                changed[signature].append(
                    {
                        "bidder": r["bidder"],
                        "evidence": r["evidence"],
                        "requirement_context": a[max(0, i - 25) : j + 25],
                        "response_context": b[max(0, k - 25) : right_end + 25],
                    }
                )
    findings = []
    for key, rr in changed.items():
        if len({r["bidder"] for r in rr}) >= 2:
            findings.append(
                {
                    "kind": "shared_edit_candidate_not_confirmed_error",
                    "round": key[0],
                    "lot": key[1],
                    "requirement_key": key[2],
                    "edit_type": key[3],
                    "before": key[4],
                    "after": key[5],
                    "contexts": [
                        {k: v for k, v in r.items() if k != "evidence"} for r in rr
                    ],
                    "evidence": [e for r in rr for e in r["evidence"]],
                }
            )
    # Optional literal known-error dictionary, never arbitrary regex execution.
    for term in ctx.manifest.get("error_terms", []):
        if not isinstance(term, str) or not 2 <= len(term) <= 80:
            raise ValueError("error_terms: literal strings of 2..80 chars required")
        refs = []
        for d in ctx.documents("bid"):
            template = Template(ctx, d["round"], d["lot"])
            if norm(term) in template.all:
                continue
            for s, ref in segments(ctx, d):
                if norm(term) in norm(s):
                    refs.append(ref)
        for scope in {(r["round"], r["lot"]) for r in refs}:
            selected = [r for r in refs if (r["round"], r["lot"]) == scope]
            if len({r["bidder"] for r in selected}) >= 2:
                findings.append(
                    {
                        "kind": "supplied_error_term_match",
                        "term": term,
                        "evidence": selected,
                    }
                )
    return outcome(
        {"response_rows_examined": len(rows), "shared_edit_candidates": len(findings)},
        findings,
        status="partial"
        if rows or ctx.manifest.get("error_terms")
        else "insufficient_data",
        limitations=[
            "A common edit is not necessarily a mistake. Correctness and source lineage require model review.",
            "No general Chinese spelling checker is claimed; known-error literals are caller-supplied and disclosed.",
        ],
    )


def m08(ctx):
    index = defaultdict(list)
    excluded = 0
    anomaly = re.compile(r"[，,；;：:。]{2,}|\.{5,}|…{3,}|[（(]{2,}|[）)]{2,}")
    for (rnd, lot), docs in groups(ctx.documents("bid")).items():
        template = Template(ctx, rnd, lot)
        for d in docs:
            for p in ctx.pages(d):
                compact = re.sub(r"\s+", "", p["text"])
                for m in anomaly.finditer(compact):
                    context = compact[max(0, m.start() - 30) : m.end() + 30]
                    if re.fullmatch(r"[. …]+", m.group()) and (
                        re.search(r"\d+$", context) or "目录" in compact
                    ):
                        continue
                    if norm(context) in template.all:
                        excluded += 1
                        continue
                    ref = ctx.evidence(
                        d, p["page"], context, extraction="punctuation_context"
                    )
                    index[(rnd, lot, context)].append(
                        {**ref, "punctuation": m.group(), "context": context}
                    )
    findings = [
        {
            "kind": "shared_punctuation_context",
            "round": k[0],
            "lot": k[1],
            "context": k[2],
            "evidence": refs,
        }
        for k, refs in index.items()
        if len({r["bidder"] for r in refs}) >= 2
    ]
    return outcome(
        {"template_contexts_excluded": excluded, "shared_contexts": len(findings)},
        findings,
        status="partial",
        limitations=[
            "Detects selected textual punctuation patterns; typography, indentation and image-only glyphs need visual review.",
            "Extraction artifacts and legitimate quotation punctuation are not classified as errors automatically.",
        ],
    )


ENTITY_PATTERNS = {
    "credit_code": re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Z])"),
    "mobile_phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "medical_certificate": re.compile(
        r"(?:国械注[进准许]|[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]械注准)\d{8,14}"
    ),
    "person": re.compile(
        r"(?:法定代表人|授权代表|项目负责人|联系人)[：:]([\u4e00-\u9fff]{2,4})(?=[\s，,。；;（(]|$)"
    ),
}


def m09(ctx):
    index = defaultdict(list)
    counts = Counter()
    for d in ctx.documents("bid"):
        for p in ctx.pages(d):
            raw = p["text"]
            s = norm(raw)
            for kind, pattern in ENTITY_PATTERNS.items():
                subject = raw if kind == "person" else s
                for m in pattern.finditer(subject):
                    val = m.group(1) if kind == "person" else m.group()
                    counts[kind] += 1
                    ref = ctx.evidence(
                        d,
                        p["page"],
                        subject[max(0, m.start() - 40) : m.end() + 40],
                        entity=val,
                    )
                    index[(d["round"], d["lot"], kind, val)].append(ref)
    for item in ctx.manifest.get("entities", []):
        ref = ctx.verify_quote(item["source"])
        d = ctx.by_id[ref["document_id"]]
        if d["role"] != "bid" or norm(item["value"]) not in norm(ref["quote"]):
            raise ValueError("Entity not grounded in bid quote")
        index[(d["round"], d["lot"], item["type"], norm(item["value"]))].append(ref)
    findings = [
        {
            "kind": "shared_entity",
            "round": k[0],
            "lot": k[1],
            "entity_type": k[2],
            "value": k[3],
            "evidence": refs,
        }
        for k, refs in index.items()
        if len({r["bidder"] for r in refs}) >= 2
    ]
    return outcome(
        {"extracted_entity_occurrences": dict(counts)},
        findings,
        status="partial",
        limitations=[
            "Exact visible identifiers only; no external corporate/personnel database was queried.",
            "Buyer contacts, manufacturers, public certificates, and lawful agency relationships can explain overlaps.",
            "Person names are weak identifiers. Addresses and client/performance relations may be supplied as source-verified entities; their full semantic extraction is not automatic.",
        ],
    )
