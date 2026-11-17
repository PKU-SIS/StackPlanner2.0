"""Source-unit delimiter observations; shared position is not a risk verdict."""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from .core import norm
from .punctuation_sources import punctuation_units
from .structured_text import blocks, evidence

PAIRS = {"(": ")", "[": "]", "{": "}", "【": "】", "《": "》"}
WIDTH = str.maketrans("（）［］｛｝", "()[]{}")
CLOSE = {v: k for k, v in PAIRS.items()}


def delimiter_events(text):
    """Offsets address the original text; width folding is one-to-one only."""
    folded = text.translate(WIDTH)
    enumerators = {m.end() - 1 for m in re.finditer(r"(?m)^\s*(?:编号\s*)?(?:\d{1,3}|[一二三四五六七八九十]+)[)）]", text)}
    stack, events = [], []
    for i, char in enumerate(folded):
        if char in PAIRS:
            stack.append((char, i))
        elif char in CLOSE:
            if i in enumerators and not stack:
                continue
            if not stack:
                events.append(
                    {
                        "kind": "unexpected_close",
                        "family": CLOSE[char] + char,
                        "offset": i,
                        "character": text[i],
                    }
                )
            elif stack[-1][0] == CLOSE[char]:
                stack.pop()
            else:
                events.append(
                    {
                        "kind": "crossed_delimiter",
                        "family": CLOSE[char] + char,
                        "offset": i,
                        "character": text[i],
                        "open_offset": stack[-1][1],
                        "expected_close": PAIRS[stack[-1][0]],
                    }
                )
                # Keep unmatched openers observable; no text is corrected.
                if any(op == CLOSE[char] for op, _ in stack):
                    while stack and stack[-1][0] != CLOSE[char]:
                        stack.pop()
                    stack.pop()
    events.extend(
        {
            "kind": "unclosed_open",
            "family": op + PAIRS[op],
            "offset": i,
            "character": text[i],
        }
        for op, i in stack
    )
    return sorted(events, key=lambda row: row["offset"])


def _key(text):
    return norm(text.translate(WIDTH))


def _same_position(a, b, threshold=0.85):
    if (a["kind"], a["family"]) != (b["kind"], b["family"]):
        return False
    # Compare each side separately: errors in different positions of an otherwise
    # identical paragraph must not be grouped by paragraph similarity alone.
    for side in ("left_context", "right_context"):
        x, y = _key(a[side]), _key(b[side])
        if not x and not y:
            continue
        if not x or not y or SequenceMatcher(None, x, y, autojunk=False).ratio() < threshold:
            return False
    return True


def audit_delimiters(ctx):
    observations, refs, ref_events = [], [], []
    seen = set()
    for doc in ctx.documents("tender", "public_template", "bid"):
        for unit in punctuation_units(doc, blocks(ctx, doc)):
            text = unit["text"]
            if doc["role"] != "bid":
                refs.append((doc, unit))
            for event in delimiter_events(text):
                pos = event["offset"]
                start = max(0, pos - 90, text.rfind("。", 0, pos) + 1)
                stop = text.find("。", pos + 1)
                end = min(len(text), pos + 91, stop + 1 if stop >= 0 else len(text))
                left, right = text[start:pos], text[pos + 1 : end]
                identity = (
                    doc["id"],
                    tuple(unit["physical_pages"]),
                    event["kind"],
                    event["family"],
                    _key(left),
                    _key(right),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                row = {
                    **event,
                    "document_id": doc["id"],
                    "bidder": doc.get("bidder"),
                    "round": doc["round"],
                    "lot": doc["lot"],
                    "context": left + text[pos] + right,
                    "left_context": left,
                    "right_context": right,
                    "source_segmentation": unit["source_segmentation"],
                    "source_unit_characters": len(text),
                    "source_boundary_may_split_delimiter": True,
                    "requires_original_verification": True,
                    "evidence": [
                        evidence(
                            ctx,
                            doc,
                            unit,
                            left + text[pos] + right,
                            extraction="delimiter_structure_source_unit",
                            delimiter_offset=pos,
                            original_delimiter=text[pos],
                            source_segmentation=unit["source_segmentation"],
                        )
                    ],
                }
                (observations if doc["role"] == "bid" else ref_events).append(row)
    exclusions, remaining = [], []
    for row in observations:
        inherited = [r for r in ref_events if (r["round"], r["lot"]) == (row["round"], row["lot"]) and _same_position(row, r, 0.92)]
        if inherited:
            exclusions.append(
                {
                    "kind": "inherited_local_delimiter_anomaly",
                    "observation": row,
                    "reference_observation": inherited[0],
                    "scope": "same local anomaly only; other shared edits not excluded",
                }
            )
        else:
            remaining.append(row)
    scopes = defaultdict(list)
    for row in remaining:
        scopes[(row["round"], row["lot"], row["kind"], row["family"])].append(row)
    findings = []
    for scope, rows in scopes.items():
        groups = []
        for row in rows:
            group = next((g for g in groups if _same_position(g[0], row)), None)
            if group is None:
                groups.append([row])
            else:
                group.append(row)
        for group in groups:
            if len({r["bidder"] for r in group}) < 2:
                continue
            comparisons = []
            sample = group[0]
            needle = _key(sample["context"])
            for doc, unit in refs:
                if (doc["round"], doc["lot"]) != scope[:2]:
                    continue
                haystack = _key(unit["text"])
                match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match()
                score = match.size / max(1, min(len(needle), len(haystack)))
                if match.size < 24 or score < 0.45:
                    continue
                comparisons.append(
                    {
                        "document_id": doc["id"],
                        "textual_overlap_score": round(score, 4),
                        "reference_unit_delimiter_events": [
                            {
                                **event,
                                "context": unit["text"][max(0, event["offset"] - 90) : event["offset"] + 91],
                            }
                            for event in delimiter_events(unit["text"])
                        ],
                        "reference_events_are_source_unit_observations_not_paper_verdicts": True,
                        "reference_text": unit["text"][:6000],
                        "reference_text_truncated": len(unit["text"]) > 6000,
                        "same_local_anomaly": any(r["document_id"] == doc["id"] and _same_position(sample, r, 0.92) for r in ref_events),
                        "evidence": [
                            evidence(
                                ctx,
                                doc,
                                unit,
                                unit["text"][:6000],
                                extraction="delimiter_reference_candidate",
                            )
                        ],
                        "note": "Text overlap locates a possible baseline; semantic identity and original punctuation require model verification.",
                    }
                )
            comparisons.sort(key=lambda r: r["textual_overlap_score"], reverse=True)
            findings.append(
                {
                    "kind": "shared_delimiter_structure_candidate",
                    "anomaly_kind": scope[2],
                    "delimiter_family": scope[3],
                    "document_ids": sorted({r["document_id"] for r in group}),
                    "context": sample["context"],
                    "observations": group,
                    "reference_comparisons": comparisons[:3],
                    "reference_comparison_count": len(comparisons),
                    "reference_preview_is_complete": len(comparisons) <= 3,
                    "requires_original_verification": True,
                    "source_boundary_may_split_delimiter": True,
                    "public_source_status": "not_verified",
                    "evidence": [e for r in group for e in r["evidence"]],
                    "review_note": (
                        "Same-position observed structure, not a proven paper error. Check original images, split units/"
                        "pages, source templates and normal enumerators; unknown external source is not exclusion or pro"
                        "of of private authorship."
                    ),
                }
            )
    return {
        "findings": findings,
        "observations": observations,
        "template_exclusions": exclusions,
        "scope": "round/square/curly/lenticular/book-title nesting and unmatched delimiters within actual visible source units; not exhaustive cross-page or layout validation",
    }
