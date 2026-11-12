"""Original-only derived source views. Never replace original PDF bytes/text."""

from pathlib import Path

from html.parser import HTMLParser
import json

from .core import digest
from .m01_recall import plain


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.depth = 0
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.rows = []
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in {"td", "th"}:
            values = dict(attrs)
            self.cell = {
                "text": [],
                "rowspan": int(values.get("rowspan", "1")),
                "colspan": int(values.get("colspan", "1")),
            }
        elif self.cell is not None and tag in {"br", "p", "div"}:
            self.cell["text"].append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.depth == 1 and self.cell is not None:
            self.cell["text"] = "".join(self.cell["text"]).strip()
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.depth == 1:
            if self.row is not None:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            if self.depth == 1:
                issues = []
                self.tables.append(
                    {"rows": expand(self.rows, issues), "layout_warnings": issues}
                )
            self.depth = max(0, self.depth - 1)


def expand(rows, issues=None):
    result = []
    carry = {}
    for raw in rows:
        values = {column: text for column, (n, text) in carry.items()}
        next_carry = {
            column: (n - 1, text) for column, (n, text) in carry.items() if n > 1
        }
        cursor = 0
        for cell in raw:
            while cursor in values:
                cursor += 1
            rs, cs = cell["rowspan"], cell["colspan"]
            if not 1 <= rs <= 1000 or not 1 <= cs <= 100:
                raise ValueError("Invalid HTML table span")
            if any(col in values for col in range(cursor, cursor + cs)):
                if issues is not None:
                    issues.append(
                        "Cell colspan overlaps a carried rowspan; shifted to next free span; original cell texts preserved."
                    )
                while any(col in values for col in range(cursor, cursor + cs)):
                    cursor += 1
            for col in range(cursor, cursor + cs):
                values[col] = cell["text"]
                if rs > 1:
                    next_carry[col] = (rs - 1, cell["text"])
            cursor += cs
        result.append([values.get(i, "") for i in range(max(values, default=-1) + 1)])
        carry = next_carry
    return result


def html_tables(text):
    parser = TableParser()
    parser.feed(text)
    return parser.tables


def attach(ctx, parsed):
    ctx.derived_sources = parsed or {}
    for doc_id, value in ctx.derived_sources.items():
        if (
            doc_id not in ctx.by_id
            or value.get("source_sha256") != ctx.by_id[doc_id]["sha256"]
        ):
            raise ValueError("Derived text source hash mismatch: " + doc_id)
        for row in value.get("chunks", []):
            if not row.get("id") or not isinstance(row.get("content"), str):
                raise ValueError("Original chunk id/content required")


def blocks(ctx, doc, *, raw=True, ocr=True):
    if raw:
        for row in ctx.pages(doc):
            yield {
                "text": row["text"],
                "raw": row["text"],
                "page": row["page"],
                "physical_pages": [row["page"]] if row["page"] is not None else [],
                "chunk_ids": [],
                "positions": [],
                "source_channel": "original_text",
            }
    if ocr:
        for row in (
            getattr(ctx, "derived_sources", {}).get(doc["id"], {}).get("chunks", [])
        ):
            pages = sorted(
                {
                    p[0]
                    for p in row.get("positions", [])
                    if isinstance(p, list) and p and type(p[0]) is int
                }
            )
            yield {
                "text": plain(row["content"]),
                "raw": row["content"],
                "page": pages[0] if len(pages) == 1 else None,
                "physical_pages": pages,
                "chunk_ids": [row["id"]],
                "positions": row.get("positions", []),
                "source_channel": "ragflow_mineru",
            }


def evidence(ctx, doc, block, quote, **extra):
    return ctx.evidence(
        doc,
        block["page"],
        quote,
        physical_pages=block["physical_pages"],
        chunk_ids=block["chunk_ids"],
        positions=block["positions"],
        source_channel=block["source_channel"],
        **extra,
    )


def coverage(ctx):
    result = []
    for doc in ctx.documents("bid", "tender", "public_template"):
        raw = list(blocks(ctx, doc, ocr=False))
        derived = list(blocks(ctx, doc, raw=False))
        pages = sorted({p for r in raw for p in r["physical_pages"]})
        opages = sorted({p for r in derived for p in r["physical_pages"]})
        result.append(
            {
                "document_id": doc["id"],
                "role": doc["role"],
                "original_text_characters": sum(len(r["text"]) for r in raw),
                "mineru_chunk_count": len(derived),
                "mineru_physical_page_count": len(opages),
                "original_pdf_physical_page_count": len(pages) if pages else None,
                "pdf_pages_without_mineru_chunk_position": sorted(
                    set(pages) - set(opages)
                ),
                "mineru_used_in_comparison": bool(derived),
            }
        )
        for i, w in enumerate(ctx.warnings):
            if w.startswith(doc["id"] + ":") and "no OCR performed" in w:
                ctx.warnings[i] = (
                    w.replace(
                        "; no OCR performed", "; ORIGINAL TEXT LAYER sparsity only"
                    )
                    + f"; persisted MinerU input: {len(derived)} chunks, {len(opages)} positioned pages"
                )
    return result


def shared_passages(ctx):
    from .m01_recall import audit_m01

    data = getattr(ctx, "_shared_passage_data", None)
    if data is not None:
        return data
    source = {
        "manifest": ctx.manifest,
        "derived": getattr(ctx, "derived_sources", {}),
        "algorithm": Path(__file__).with_name("m01_recall.py").read_text(),
    }
    key = digest(json.dumps(source, ensure_ascii=False, sort_keys=True).encode())
    target = ctx.cache / (key + ".shared-passages-v20.json")
    if target.exists():
        data = json.loads(target.read_text())
    else:
        from .core import dump

        data = audit_m01(ctx, getattr(ctx, "derived_sources", {}))
        dump(target, data)
    ctx._shared_passage_data = data
    return data
