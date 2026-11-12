"""Visible source units for punctuation; never insert cell separators."""

import re
from html.parser import HTMLParser
from pathlib import Path


class VisibleUnits(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.units = []
        self.hidden = 0

    def flush(self):
        text = "".join(self.parts).strip()
        if text:
            self.units.append(text)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.flush()
            self.hidden += 1
        elif tag in {
            "table",
            "tr",
            "td",
            "th",
            "p",
            "div",
            "br",
            "li",
            "h1",
            "h2",
            "h3",
        }:
            self.flush()

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"table", "tr", "td", "th", "p", "div", "li", "h1", "h2", "h3"}:
            self.flush()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def docx_units(path):
    from docx import Document

    document = Document(path)
    base = {
        "page": None,
        "physical_pages": [],
        "chunk_ids": [],
        "positions": [],
        "source_channel": "original_text",
        "source_segmentation": "original_docx_paragraph_or_cell",
    }
    for i, paragraph in enumerate(document.paragraphs):
        if paragraph.text.strip():
            yield {
                **base,
                "text": paragraph.text,
                "raw": paragraph.text,
                "source_unit_locator": {"body_paragraph": i},
            }
    seen = set()

    def table_units(table, prefix):
        for ri, row in enumerate(table.rows):
            for ci, cell in enumerate(row.cells):
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                locator = {**prefix, "row": ri, "cell": ci}
                for pi, paragraph in enumerate(cell.paragraphs):
                    if paragraph.text.strip():
                        yield {
                            **base,
                            "text": paragraph.text,
                            "raw": paragraph.text,
                            "source_unit_locator": {**locator, "paragraph": pi},
                        }
                for ti, nested in enumerate(cell.tables):
                    yield from table_units(nested, {**locator, "nested_table": ti})

    for i, table in enumerate(document.tables):
        yield from table_units(table, {"table": i})


def punctuation_units(doc, source_blocks):
    path = Path(doc.get("_path", ""))
    structured_docx = path.suffix.lower() == ".docx" and path.is_file()
    if structured_docx:
        yield from docx_units(path)
    for block in source_blocks:
        if structured_docx and block.get("source_channel") == "original_text":
            continue
        raw = block.get("raw", block["text"])
        if re.search(r"</?(?:table|tr|td|th|p|div|span|b|strong)\b", raw, re.I):
            reader = VisibleUnits()
            reader.feed(raw)
            reader.close()
            reader.flush()
            for i, unit in enumerate(reader.units):
                yield {
                    **block,
                    "text": unit,
                    "source_segmentation": "visible_html_unit_no_added_delimiters",
                    "source_visible_unit": i,
                }
        else:
            yield {
                **block,
                "text": raw,
                "source_segmentation": "original_plain_text_no_added_delimiters",
            }
