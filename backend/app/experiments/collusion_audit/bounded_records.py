"""Lossless collection transport over the unchanged twenty-row backend reader."""

import json


def read_bounded(read_page, run_id, collection, offset=0, limit=20, *, max_chars=60000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("offset >=0 and limit1..200 required")
    if type(max_chars) is not int or max_chars < 1000:
        raise ValueError("Character budget must be at least1000")
    items = []
    cursor = offset
    total = None

    def packet(values, next_cursor):
        return {
            "run_id": run_id,
            "collection": collection,
            "offset": offset,
            "total": total,
            "items": values,
            "next_offset": next_cursor if next_cursor < total else None,
            "requested_limit": limit,
            "returned_count": len(values),
            "character_budget": max_chars,
            "reading_contract": "Every returned row is complete. Continue at next_offset if nonnull; no source fields were truncated.",
        }

    while len(items) < limit:
        page = read_page(run_id, collection, cursor, min(20, limit - len(items)))
        if page.get("run_id") != run_id or page.get("collection") != collection or page.get("offset") != cursor:
            raise ValueError("Backend source identity or cursor mismatch")
        value = page.get("total")
        rows = page.get("items")
        if type(value) is not int or value < 0 or (total is not None and value != total) or not isinstance(rows, list):
            raise ValueError("Invalid or changing source collection")
        total = value
        if cursor >= total:
            return packet(items, cursor)
        if not rows or len(rows) > min(20, limit - len(items)) or cursor + len(rows) > total:
            raise ValueError("Backend read made no valid progress")
        for row in rows:
            proposed = items + [row]
            candidate = packet(proposed, cursor + 1)
            if len(json.dumps(candidate, ensure_ascii=False)) > max_chars:
                if not items:
                    raise ValueError("One record exceeds the response budget; use read_file on its full_collection_path with character cursors")
                return packet(items, cursor)
            items.append(row)
            cursor += 1
        if cursor >= total:
            break
    return packet(items, cursor)
