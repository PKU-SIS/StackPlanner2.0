"""Explicit normalized phrase/AND-term search with offsets in original text."""

import re
import unicodedata


def search_text(text, query, mode="auto"):
    if mode not in {"auto", "literal", "terms"}:
        raise ValueError("Search mode must be auto, literal or terms")
    if not query.strip():
        raise ValueError("Search query must be nonempty")
    if mode == "auto":
        mode = "terms" if re.search(r"\s", query.strip()) else "literal"

    def normalize(s):
        return "".join(unicodedata.normalize("NFKC", c) for c in s if not c.isspace()).casefold()

    normalized, positions = [], []
    for i, c in enumerate(text):
        if c.isspace():
            continue
        for token in unicodedata.normalize("NFKC", c).casefold():
            normalized.append(token)
            positions.append(i)
    haystack = "".join(normalized)
    needles = [normalize(query)] if mode == "literal" else list(dict.fromkeys(normalize(x) for x in query.split()))
    if any(not n or n not in haystack for n in needles):
        return []
    if mode == "terms":
        starts = [haystack.find(n) for n in needles]
        return [{"start": positions[min(starts)], "end": max(positions[p + len(n) - 1] + 1 for p, n in zip(starts, needles)), "mode": mode, "matched_terms": query.split()}]
    result = []
    start = 0
    while (found := haystack.find(needles[0], start)) >= 0:
        result.append({"start": positions[found], "end": positions[found + len(needles[0]) - 1] + 1, "mode": mode, "matched_terms": [query]})
        start = found + len(needles[0])
    return result
