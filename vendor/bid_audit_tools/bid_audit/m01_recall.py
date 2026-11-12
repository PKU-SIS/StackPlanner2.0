"""Provenance-preserving lexical recall, without project answers or risk verdicts.

Exact anchors are maximal common substrings, independent of sentence/page
boundaries. Approximate candidates use documented character seeds and alignment.
A candidate is never a collusion finding: public-source attribution is model work.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from html.parser import HTMLParser
from itertools import combinations
import hashlib
import json
import re
import unicodedata

VERSION = "m01-recall-2.0"
MIN_EXACT = 24
MIN_TEMPLATE = 12
MIN_NEAR = 40
NEAR_RATIO = 0.74
SEED_SIZE = 7
SEEDS_PER_UNIT = 12
MAX_UNIT = 1200


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"tr", "p", "div", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append("；")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"tr", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    if not re.search(r"</?(?:table|tr|td|th|p|div|span|b|strong)\b", value, re.I):
        return value
    reader = PlainHTML()
    reader.feed(value)
    return "".join(reader.parts)


def canonical(text, bidder=""):
    """Return comparison characters, original-character offsets, sentence stops."""
    chars, offsets, stops = [], [], [0]
    pos = 0
    for line in text.splitlines(keepends=True):
        if (
            re.fullmatch(r"[\s—\-\d]+", line)
            or re.match(r"^\s*(?:广东政府采购智慧云平台|第\s*\d+\s*页)", line)
            or (bidder and bidder in line and re.search(r"20\d{2}[-年]\d", line))
        ):
            pos += len(line)
            continue
        ordinal = re.match(
            r"^\s*(?:[（(]\d{1,3}[）)]|\d{1,3}[、.)）])\s*(?=[^\d\s])", line
        )
        start = ordinal.end() if ordinal else 0
        for i, char in enumerate(line[start:], start):
            if char in "。；;！？!?":
                stops.append(len(chars))
            for c in unicodedata.normalize("NFKC", char):
                # Keep operators, minus signs, and decimal points: normalization
                # must not make distinct numbers appear identical.
                decimal = (
                    c == "."
                    and i > 0
                    and i + 1 < len(line)
                    and line[i - 1].isdigit()
                    and line[i + 1].isdigit()
                )
                if c.isspace() or (
                    unicodedata.category(c).startswith("P")
                    and c not in "-−"
                    and not decimal
                ):
                    continue
                chars.append(c)
                offsets.append(pos + i)
        pos += len(line)
    stops.append(len(chars))
    return "".join(chars), offsets, sorted(set(stops))


@dataclass
class Stream:
    doc: dict
    channel: str
    text: str
    blocks: list
    starts: list
    stops: list
    mask: bytearray

    def evidence(self, start, end, context=100):
        selected = [b for b in self.blocks if b["end"] > start and b["start"] < end]
        quotes, before, after, pages, ids, positions = [], "", "", set(), [], []
        for j, b in enumerate(selected):
            a = max(0, start - b["start"])
            z = min(len(b["map"]), end - b["start"])
            if a >= z:
                continue
            lo, hi = b["map"][a], b["map"][z - 1] + 1
            quotes.append(b["plain"][lo:hi])
            if j == 0:
                before = b["plain"][max(0, lo - context) : lo]
            after = b["plain"][hi : hi + context]
            pages.update(b.get("physical_pages", []))
            if b.get("chunk_id"):
                ids.append(b["chunk_id"])
            positions.extend(b.get("positions", []))
        page = next(iter(pages)) if len(pages) == 1 else None
        return {
            "document_id": self.doc["id"],
            "path": self.doc.get("path", ""),
            "sha256": self.doc.get("sha256", ""),
            "bidder": self.doc.get("bidder"),
            "round": self.doc.get("round", "1"),
            "lot": self.doc.get("lot", "1"),
            "page": page,
            "physical_pages": sorted(pages),
            "chunk_ids": list(dict.fromkeys(ids)),
            "source_channel": self.channel,
            "locator_kind": "ragflow_chunk" if ids else "original_text",
            "normalized_span": [start, end],
            "quote": "\n".join(quotes),
            "context_before": before,
            "context_after": after,
            "chunk_positions": positions,
            "quote_encoding": "plain_text_of_source_chunk"
            if ids
            else "original_extracted_text",
            "comparison_normalization": "NFKC; whitespace/punctuation/line ordinals removed; numbers/operators retained",
        }


def streams(document):
    grouped = defaultdict(list)
    for b in document["blocks"]:
        grouped[b.get("source_channel", "original_text")].append(b)
    result = []
    for channel, blocks in grouped.items():
        text, positions, stops, seen = [], [], [0], set()
        cursor = 0
        for b in blocks:
            raw = plain(b["text"])
            n, mapping, ends = canonical(raw, document.get("bidder", ""))
            if not n:
                continue
            # Deduplicate identical overlapping OCR chunks, preserving locators.
            key = (
                n,
                tuple(b.get("physical_pages", [b["page"]] if b.get("page") else [])),
            )
            if key in seen:
                continue
            seen.add(key)
            page_numbers = b.get("physical_pages", [b["page"]] if b.get("page") else [])
            positions.append(
                {
                    **b,
                    "plain": raw,
                    "map": mapping,
                    "start": cursor,
                    "end": cursor + len(n),
                    "physical_pages": page_numbers,
                }
            )
            stops.extend(cursor + x for x in ends)
            text.append(n)
            cursor += len(n)
        joined = "".join(text)
        result.append(
            Stream(
                document,
                channel,
                joined,
                positions,
                [b["start"] for b in positions],
                sorted(set(stops + [cursor])),
                bytearray(len(joined)),
            )
        )
    return result


class SuffixAutomaton:
    """Linear-size exact lexical index; no top-k occurrence or sentence filter."""

    def __init__(self, text):
        self.text = text
        self.next = [{}]
        self.link = [-1]
        self.length = [0]
        self.end = [-1]
        last = 0
        for i, c in enumerate(text):
            cur = len(self.next)
            self.next.append({})
            self.link.append(0)
            self.length.append(self.length[last] + 1)
            self.end.append(i)
            p = last
            while p >= 0 and c not in self.next[p]:
                self.next[p][c] = cur
                p = self.link[p]
            if p >= 0:
                q = self.next[p][c]
                if self.length[p] + 1 == self.length[q]:
                    self.link[cur] = q
                else:
                    clone = len(self.next)
                    self.next.append(self.next[q].copy())
                    self.link.append(self.link[q])
                    self.length.append(self.length[p] + 1)
                    self.end.append(self.end[q])
                    while p >= 0 and self.next[p].get(c) == q:
                        self.next[p][c] = clone
                        p = self.link[p]
                    self.link[q] = self.link[cur] = clone
            last = cur

    def matches(self, other, minimum=MIN_EXACT):
        state = size = 0
        previous = None
        for i, c in enumerate(other):
            if c == "\0":
                if previous:
                    yield previous
                state = size = 0
                previous = None
                continue
            while state and c not in self.next[state]:
                state = self.link[state]
                size = min(size, self.length[state])
            if c in self.next[state]:
                state = self.next[state][c]
                size += 1
            else:
                state = size = 0
            if previous and size <= previous[2]:
                yield previous
                previous = None
            if size >= minimum:
                previous = (self.end[state] - size + 1, i - size + 1, size)
        if previous:
            yield previous


def masked(stream):
    return "".join(
        "\0" if covered else c for c, covered in zip(stream.text, stream.mask)
    )


def extent(stream, start, end):
    lo = stream.stops[max(0, bisect_right(stream.stops, start) - 1)]
    hi = stream.stops[min(len(stream.stops) - 1, bisect_left(stream.stops, end))]
    # Context can be longer than a seed but is never silently used to claim
    # every word in a long chapter is aligned.
    return (lo, hi) if hi - lo <= MAX_UNIT else (start, end)


def units(stream, minimum=MIN_EXACT):
    spans = set()
    for i, start in enumerate(stream.stops[:-1]):
        for count in (1, 2, 3, 6):
            j = min(i + count, len(stream.stops) - 1)
            end = stream.stops[j]
            if (
                MIN_NEAR <= end - start <= MAX_UNIT
                and end - start - sum(stream.mask[start:end]) >= minimum
            ):
                spans.add((start, end))
    for b in stream.blocks:
        for start in range(b["start"], b["end"], MAX_UNIT // 2):
            end = min(start + MAX_UNIT, b["end"])
            if (
                end - start >= MIN_NEAR
                and end - start - sum(stream.mask[start:end]) >= minimum
            ):
                spans.add((start, end))
    return sorted(spans)


def differences(a, b):
    return [
        {
            "operation": tag,
            "before": a[i:j],
            "after": b[k:right_end],
            "a_span": [i, j],
            "b_span": [k, right_end],
        }
        for tag, i, j, k, right_end in SequenceMatcher(
            None, a, b, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]


def candidate(a, b, sa, sb, method, minimum=MIN_EXACT):
    x, y = a.text[sa[0] : sa[1]], b.text[sb[0] : sb[1]]
    alignment = SequenceMatcher(None, x, y, autojunk=False)
    blocks = alignment.get_matching_blocks()
    shared = sum(n for _, _, n in blocks)
    residual = sum(
        sum(not a.mask[sa[0] + i + t] and not b.mask[sb[0] + j + t] for t in range(n))
        for i, j, n in blocks
    )
    if residual < (
        max(MIN_NEAR, minimum) if method == "rare_character_seed_alignment" else minimum
    ):
        return None
    return {
        "kind": "non_template_shared_passage",
        "round": a.doc.get("round", "1"),
        "lot": a.doc.get("lot", "1"),
        "text": x,
        "text_a": x,
        "text_b": y,
        "document_a": a.doc["id"],
        "document_b": b.doc["id"],
        "match_mode": "normalized_exact" if x == y else "near_copy",
        "retrieval_method": method,
        "character_sequence_similarity": round(alignment.ratio(), 6),
        "shared_normalized_chars": shared,
        "non_template_shared_chars": residual,
        "template_overlap_chars_a": sum(a.mask[sa[0] : sa[1]]),
        "template_overlap_chars_b": sum(b.mask[sb[0] : sb[1]]),
        "differences": differences(x, y),
        "evidence": [a.evidence(*sa), b.evidence(*sb)],
        "review_note": "Lexical candidate only. Public source, actual edit lineage, and risk remain unverified; inspect both passages and context.",
    }


def collapse_windows(rows):
    """Union overlapping double-ended windows; preserve independent passages."""
    parent = list(range(len(rows)))
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        a, b = row["evidence"][:2]
        groups[
            (
                row["document_a"],
                row["document_b"],
                a["source_channel"],
                b["source_channel"],
            )
        ].append(i)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def overlap(a, b):
        return max(0, min(a[1], b[1]) - max(a[0], b[0])) / max(
            1, min(a[1] - a[0], b[1] - b[0])
        )

    for ids in groups.values():
        ids.sort(key=lambda i: rows[i]["evidence"][0]["normalized_span"][0])
        for at, i in enumerate(ids):
            a, b = [e["normalized_span"] for e in rows[i]["evidence"][:2]]
            for j in ids[at + 1 :]:
                c, d = [e["normalized_span"] for e in rows[j]["evidence"][:2]]
                if c[0] >= a[1]:
                    break
                if overlap(a, c) >= 0.5 and overlap(b, d) >= 0.5:
                    parent[root(j)] = root(i)
    merged = defaultdict(list)
    for i, row in enumerate(rows):
        merged[root(i)].append(row)
    results = []
    for family in merged.values():
        best = max(
            family,
            key=lambda r: (
                r["non_template_shared_chars"],
                r["character_sequence_similarity"],
            ),
        )
        best = dict(best)
        locations = []
        edits = Counter()
        edit_examples = {}
        for row in family:
            locations.extend(row.get("supporting_locations", []))
            locations.extend(
                {
                    k: e[k]
                    for k in [
                        "document_id",
                        "source_channel",
                        "page",
                        "physical_pages",
                        "chunk_ids",
                        "normalized_span",
                    ]
                }
                for e in row["evidence"]
            )
            for diff in row["differences"]:
                key = (diff["operation"], diff["before"], diff["after"])
                edits[key] += 1
                edit_examples[key] = diff
        unique = {
            json.dumps(location, sort_keys=True, ensure_ascii=False): location
            for location in locations
        }
        best["supporting_locations"] = list(unique.values())
        best["overlapping_windows_merged"] = len(family)
        best["observed_edit_patterns"] = [
            {"operation": k[0], "before": k[1], "after": k[2], "supporting_windows": n}
            for k, n in sorted(edits.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        best["cluster_note"] = (
            "Representative aligned passage plus all distinct supporting source locations/edit patterns; overlapping window counts are not independent evidence."
        )
        results.append(best)
    return results


def compare_documents(
    documents,
    templates,
    *,
    minimum_exact_chars=MIN_EXACT,
    minimum_template_chars=MIN_TEMPLATE,
):
    all_streams = [s for d in documents for s in streams(d)]
    template_streams = [s for d in templates for s in streams(d)]
    template_indices = {}
    for scope in {(d.get("round", "1"), d.get("lot", "1")) for d in documents}:
        text = "\0".join(
            s.text
            for s in template_streams
            if (s.doc.get("round", "1"), s.doc.get("lot", "1")) == scope
        )
        template_indices[scope] = SuffixAutomaton(text) if text else None
    exclusions = []
    for s in all_streams:
        base = template_indices[(s.doc.get("round", "1"), s.doc.get("lot", "1"))]
        if base:
            for _, start, n in base.matches(s.text, minimum=minimum_template_chars):
                s.mask[start : start + n] = b"\1" * n
            # Explicit merged intervals make template exclusion auditable.
            start = None
            for i, v in enumerate([*s.mask, 0]):
                if v and start is None:
                    start = i
                if not v and start is not None:
                    exclusions.append(
                        {
                            "document_id": s.doc["id"],
                            "source_channel": s.channel,
                            "normalized_span": [start, i],
                            "normalized_text": s.text[start:i],
                            "reason": "exact_tender_or_public_template_overlap",
                            "source": s.evidence(start, i),
                        }
                    )
                    start = None
    found = []
    stats = Counter()
    for a, b in combinations(all_streams, 2):
        if (
            a.doc["id"] == b.doc["id"]
            or a.doc.get("bidder") == b.doc.get("bidder")
            or (a.doc.get("round", "1"), a.doc.get("lot", "1"))
            != (b.doc.get("round", "1"), b.doc.get("lot", "1"))
        ):
            continue
        stats["compared_stream_pairs"] += 1
        index = SuffixAutomaton(masked(a))
        seen = set()
        for i, j, n in index.matches(masked(b), minimum=minimum_exact_chars):
            sa, sb = extent(a, i, i + n), extent(b, j, j + n)
            if (sa, sb) in seen:
                continue
            seen.add((sa, sb))
            x, y = a.text[sa[0] : sa[1]], b.text[sb[0] : sb[1]]
            if SequenceMatcher(None, x, y, autojunk=False).ratio() < 0.60:
                sa, sb = (i, i + n), (j, j + n)
            c = candidate(
                a, b, sa, sb, "maximal_exact_anchor_with_context", minimum_exact_chars
            )
            if c:
                found.append(c)
                stats["exact_anchor_candidates"] += 1
        del index
        ua, ub = units(a, minimum_exact_chars), units(b, minimum_exact_chars)
        stats["fuzzy_units_a"] += len(ua)
        stats["fuzzy_units_b"] += len(ub)
        postings = defaultdict(set)
        for k, (i, z) in enumerate(ua):
            text = a.text[i:z]
            for gram in {
                text[t : t + SEED_SIZE] for t in range(len(text) - SEED_SIZE + 1)
            }:
                postings[gram].add(k)
        compared = set()
        for sb in ub:
            y = b.text[sb[0] : sb[1]]
            grams = {y[t : t + SEED_SIZE] for t in range(len(y) - SEED_SIZE + 1)}
            seeds = sorted(
                (g for g in grams if g in postings), key=lambda g: (len(postings[g]), g)
            )[:SEEDS_PER_UNIT]
            choices = Counter(k for g in seeds for k in postings[g])
            for k, votes in choices.items():
                if votes < 2:
                    continue
                sa = ua[k]
                if (sa, sb) in seen or (sa, sb) in compared:
                    continue
                compared.add((sa, sb))
                x = a.text[sa[0] : sa[1]]
                if min(len(x), len(y)) / max(len(x), len(y)) < 0.65:
                    continue
                alignment = SequenceMatcher(None, x, y, autojunk=False)
                if (
                    alignment.quick_ratio() < NEAR_RATIO
                    or alignment.ratio() < NEAR_RATIO
                ):
                    continue
                c = candidate(
                    a, b, sa, sb, "rare_character_seed_alignment", minimum_exact_chars
                )
                if c:
                    found.append(c)
                    stats["fuzzy_candidates"] += 1
        stats["fuzzy_pairs_examined"] += len(compared)
    # Maximal aligned passages subsume their overlapping sentence windows.
    # Deduplication never drops an unrelated passage or bidder pair.
    accepted = []
    for row in sorted(
        found,
        key=lambda r: (
            -r["non_template_shared_chars"],
            -r["character_sequence_similarity"],
            r["document_a"],
            r["document_b"],
        ),
    ):
        duplicate = None
        for old in accepted:
            if (row["document_a"], row["document_b"]) != (
                old["document_a"],
                old["document_b"],
            ):
                continue
            if row["text_a"] in old["text_a"] and row["text_b"] in old["text_b"]:
                duplicate = old
                break
        if duplicate is not None:
            # Keep alternative channel/page occurrences for traceability.
            existing = {
                json.dumps(e, sort_keys=True, ensure_ascii=False)
                for e in duplicate["evidence"]
            }
            duplicate.setdefault("supporting_locations", []).extend(
                {
                    k: e[k]
                    for k in [
                        "document_id",
                        "source_channel",
                        "page",
                        "physical_pages",
                        "chunk_ids",
                        "normalized_span",
                    ]
                }
                for e in row["evidence"]
                if json.dumps(e, sort_keys=True, ensure_ascii=False) not in existing
            )
            stats["contained_candidates_merged"] += 1
        else:
            accepted.append(row)
    accepted = collapse_windows(accepted)
    coverage = [
        {
            "document_id": s.doc["id"],
            "source_channel": s.channel,
            "characters_compared": len(s.text),
            "template_characters": sum(s.mask),
            "blocks": len(s.blocks),
            "physical_pages": sorted(
                {p for b in s.blocks for p in b.get("physical_pages", [])}
            ),
        }
        for s in all_streams
    ]
    return {
        "findings": accepted,
        "template_exclusions": exclusions,
        "source_coverage": coverage,
        "metrics": {
            "algorithm": VERSION,
            "unique_shared_residuals": len(accepted),
            "template_matches_excluded": len(exclusions),
            "minimum_exact_chars": minimum_exact_chars,
            "minimum_template_chars": minimum_template_chars,
            "minimum_near_chars": MIN_NEAR,
            "near_similarity_threshold": NEAR_RATIO,
            "fuzzy_seed_chars": SEED_SIZE,
            "fuzzy_seeds_per_unit": SEEDS_PER_UNIT,
            "max_fuzzy_window_chars": MAX_UNIT,
            "silent_candidate_cap": False,
            **dict(stats),
        },
    }


def audit_m01(ctx, parsed_texts=None):
    parsed_texts = parsed_texts or {}
    unknown = set(parsed_texts) - set(ctx.by_id)
    if unknown:
        raise ValueError("Unknown derived-source document IDs")
    docs = []
    fingerprints = {}
    channel_coverage = []
    for doc in ctx.documents("bid", "tender", "public_template"):
        blocks = []
        parsed = parsed_texts.get(doc["id"])
        if parsed is not None:
            if parsed.get("source_sha256") != doc["sha256"]:
                raise ValueError("Source hash mismatch for derived text: " + doc["id"])
            fingerprints[doc["id"]] = hashlib.sha256(
                json.dumps(parsed, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            for chunk in parsed.get("chunks", []):
                pages = sorted(
                    {
                        p[0]
                        for p in chunk.get("positions", [])
                        if isinstance(p, list) and p and type(p[0]) is int and p[0] >= 1
                    }
                )
                if not isinstance(chunk.get("content"), str) or not chunk.get("id"):
                    raise ValueError(
                        "Derived source needs original content and chunk ID"
                    )
                blocks.append(
                    {
                        "text": chunk["content"],
                        "source_channel": "ragflow_mineru",
                        "chunk_id": chunk["id"],
                        "physical_pages": pages,
                        "positions": chunk.get("positions", []),
                    }
                )
        # Original text remains an independent, complementary source. Never
        # silently replace original PDF bytes, especially for non-text rules.
        original_pages = ctx.pages(doc)
        for page in original_pages:
            blocks.append(
                {
                    "text": page["text"],
                    "page": page["page"],
                    "source_channel": "original_text",
                }
            )
        ocr_pages = sorted(
            {
                page
                for block in blocks
                if block["source_channel"] == "ragflow_mineru"
                for page in block["physical_pages"]
            }
        )
        pdf_pages = {
            page["page"] for page in original_pages if type(page["page"]) is int
        }
        ocr_chunks = sum(
            block["source_channel"] == "ragflow_mineru" for block in blocks
        )
        channel_coverage.append(
            {
                "document_id": doc["id"],
                "role": doc["role"],
                "original_text_characters": sum(
                    len(page["text"]) for page in original_pages
                ),
                "original_pdf_physical_page_count": len(pdf_pages)
                if pdf_pages
                else None,
                "mineru_used_in_comparison": bool(ocr_chunks),
                "mineru_chunk_count": ocr_chunks,
                "mineru_physical_pages": ocr_pages,
                "pdf_pages_without_mineru_chunk_position": sorted(
                    pdf_pages - set(ocr_pages)
                )
                if pdf_pages
                else [],
                "note": "Chunk page positions describe supplied OCR input coverage, not guaranteed correct recognition or model review.",
            }
        )
        revised = []
        for warning in ctx.warnings:
            if warning.startswith(doc["id"] + ":") and "no OCR performed" in warning:
                warning = warning.replace(
                    "; no OCR performed",
                    "; this describes the ORIGINAL TEXT LAYER only",
                )
                if ocr_chunks:
                    warning += f"; M01 ALSO compared persisted MinerU: {ocr_chunks} chunks on {len(ocr_pages)} positioned pages; see input_channel_coverage for remaining gaps"
                else:
                    warning += (
                        "; no persisted MinerU chunks were supplied for this document"
                    )
            revised.append(warning)
        ctx.warnings[:] = revised
        docs.append({**doc, "blocks": blocks})
    templates = [d for d in docs if d["role"] in {"tender", "public_template"}]
    scopes = {
        (d.get("round", "1"), d.get("lot", "1")) for d in docs if d["role"] == "bid"
    }
    for scope in scopes:
        if not any(
            (d.get("round", "1"), d.get("lot", "1")) == scope for d in templates
        ):
            ctx.warnings.append(
                "/".join(scope)
                + ": no tender/public baseline; template exclusion unavailable"
            )
    data = compare_documents(
        [d for d in docs if d["role"] == "bid"],
        templates,
        minimum_exact_chars=int(ctx.params.get("min_residual_chars", MIN_EXACT)),
        minimum_template_chars=int(
            ctx.params.get("template_min_exact_chars", MIN_TEMPLATE)
        ),
    )
    data["metrics"]["text_source_fingerprint"] = hashlib.sha256(
        json.dumps(
            {
                "originals": {d["id"]: d["sha256"] for d in docs},
                "derived": fingerprints,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    data["metrics"]["derived_document_count"] = len(fingerprints)
    data["metrics"]["input_channel_coverage"] = channel_coverage
    data["metrics"]["candidate_counts_by_document_pair"] = dict(
        Counter(row["document_a"] + "|" + row["document_b"] for row in data["findings"])
    )
    data["metrics"]["candidate_counts_by_match_mode"] = dict(
        Counter(row["match_mode"] for row in data["findings"])
    )
    data["status"] = "ok"
    data["limitations"] = [
        "All results are lexical candidates, not risk verdicts. No project-specific words, pages, or prior model conclusions are inputs.",
        "Exact anchors use documented normalization and minimum length. Approximate recall uses bounded character seeds/windows, not exhaustive semantic paraphrase search.",
        "A template mask is exact-source evidence only; undocumented public sources remain for model review.",
        "Multi-page chunks retain all page positions and do not claim one exact physical page.",
        "Raw-text and OCR coverage are separate; missing OCR pages and recognition errors still need source review.",
    ]
    return data
