"""Recover original PDF response bands independently of spurious subcolumns."""

import json
from pathlib import Path

import pdfplumber

from .core import digest, dump, norm
from .tables import clean_page


def row_interval(row):
    """Use a row's common vertical slice rather than merged-cell union."""
    cells = [cell for cell in row.cells if cell is not None]
    if not cells:
        return row.bbox[1], row.bbox[3]
    return max(cell[1] for cell in cells), min(cell[3] for cell in cells)


def extract_responses(ctx, doc, selected_pages):
    from .enhanced_tables import HEADER_REQ, HEADER_RESP, HEADING, section_key

    selected = sorted(set(selected_pages))
    algorithm = digest(Path(__file__).read_bytes())
    cache = ctx.cache / (
        digest(
            (
                doc["sha256"]
                + algorithm
                + str(selected)
                + getattr(pdfplumber, "__version__", "")
            ).encode()
        )
        + ".geometric-responses.json"
    )
    if cache.exists():
        return json.loads(cache.read_text())
    result = []
    active = None
    previous = None
    section = ""
    family = "technical"
    with pdfplumber.open(doc["_path"]) as pdf:
        for pnum in selected:
            if pnum < 1 or pnum > len(pdf.pages):
                ctx.warnings.append(doc["id"] + ": geometric source page outside PDF")
                continue
            if previous is not None and pnum - previous > 3:
                active = None
            page = clean_page(pdf.pages[pnum - 1])
            for table in page.find_tables():
                raw = table.extract()
                if not raw:
                    continue
                header = [norm(c) for c in raw[0]]
                qi = next(
                    (
                        i
                        for i, c in enumerate(header)
                        if any(w in c for w in HEADER_REQ)
                    ),
                    None,
                )
                ai = next(
                    (
                        i
                        for i, c in enumerate(header)
                        if any(w in c for w in HEADER_RESP)
                    ),
                    None,
                )
                found = qi is not None and ai is not None
                bbox = table.bbox
                width = bbox[2] - bbox[0]
                if width <= 0:
                    continue
                if found:
                    cells = table.rows[0].cells
                    indices = {
                        "requirement": qi,
                        "response": ai,
                        "deviation": next(
                            (i for i, c in enumerate(header) if "偏离" in c), None
                        ),
                        "item": next(
                            (i for i, c in enumerate(header) if c in {"序号", "编号"}),
                            None,
                        ),
                        "model": next(
                            (
                                i
                                for i, c in enumerate(header)
                                if c in {"型号", "规格型号"}
                            ),
                            None,
                        ),
                    }
                    bands = {}
                    for field, i in indices.items():
                        if i is not None and i < len(cells) and cells[i] is not None:
                            box = cells[i]
                            bands[field] = [
                                (box[0] - bbox[0]) / width,
                                (box[2] - bbox[0]) / width,
                            ]
                    if "requirement" not in bands or "response" not in bands:
                        continue
                    family = "business" if "商务" in header[qi] else "technical"
                    section = "商务条件" if family == "business" else ""
                    active = bands
                if active is None:
                    continue
                for rn, row in enumerate(table.rows):
                    if found and rn == 0:
                        continue
                    y0, y1 = row_interval(row)
                    if y1 <= y0:
                        continue
                    values = {}
                    boxes = {}
                    for field, (left, right) in active.items():
                        box = (bbox[0] + left * width, y0, bbox[0] + right * width, y1)
                        try:
                            text = (
                                page.crop(box).extract_text(
                                    x_tolerance=1, y_tolerance=2
                                )
                                or ""
                            )
                        except (ValueError, TypeError):
                            text = ""
                        values[field] = text.strip()
                        boxes[field] = list(box)
                    q, a = values.get("requirement", ""), values.get("response", "")
                    if HEADING.fullmatch(q):
                        section = section_key(q)
                        continue
                    if (
                        len(norm(q)) < 5
                        or len(norm(a)) < 2
                        or any(w in norm(q) for w in HEADER_REQ)
                    ):
                        continue
                    ref = ctx.evidence(
                        doc,
                        pnum,
                        a,
                        source_channel="original_geometry",
                        physical_pages=[pnum],
                        chunk_ids=[],
                        table_bbox=list(table.bbox),
                        field_bboxes=boxes,
                        requirement_quote=q,
                        extraction="header_band_original_pdf_text",
                    )
                    result.append(
                        {
                            "document_id": doc["id"],
                            "bidder": doc["bidder"],
                            "round": doc["round"],
                            "lot": doc["lot"],
                            "section": section,
                            "table_family": family,
                            "item_no": norm(values.get("item", "")),
                            "requirement": q,
                            "response": a,
                            "model": values.get("model", ""),
                            "declared_deviation": norm(values.get("deviation", "")),
                            "physical_pages": [pnum],
                            "column_inference": "original_pdf_header_geometry",
                            "source_channel": "original_geometry",
                            "evidence": [ref],
                        }
                    )
            previous = pnum
            page.close()
    dump(cache, result)
    return result
