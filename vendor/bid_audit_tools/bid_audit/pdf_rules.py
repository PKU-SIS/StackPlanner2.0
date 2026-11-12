from collections import defaultdict
from itertools import combinations
import json
import re
from datetime import datetime

from PIL import Image, ImageStat
from pypdf import PdfReader
from pypdf.generic import NullObject

from .core import digest, dump, outcome, groups


def pdf_docs(ctx, roles=("bid",)):
    return [d for d in ctx.documents(*roles) if d["_path"].suffix.lower() == ".pdf"]


def selected_pages(doc, reader):
    values = doc.get("pages", list(range(1, len(reader.pages) + 1)))
    if not values or any(
        type(i) is not int or i < 1 or i > len(reader.pages) for i in values
    ):
        raise ValueError("Invalid PDF page selection")
    return [(i, reader.pages[i - 1]) for i in values]


def shared(records, key):
    index = defaultdict(list)
    for r in records:
        value = r.get(key)
        if value:
            index[(r["round"], r["lot"], value)].append(r)
    return [
        (scope, rs)
        for scope, rs in index.items()
        if len({r["bidder"] for r in rs}) >= 2
    ]


def image_records(ctx, doc):
    cache = ctx.cache / (
        digest((doc["sha256"] + "images-v1" + str(doc.get("pages"))).encode())
        + ".images.json"
    )
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        raw = []
        reader = PdfReader(doc["_path"])
        for pnum, page in selected_pages(doc, reader):
            try:
                images = page.images
                for key in images.keys():
                    try:
                        item = images[key]
                        im = item.image.convert("RGB")
                        if im.width < 40 or im.height < 40:
                            continue
                        gray = im.convert("L")
                        spread = ImageStat.Stat(gray.resize((32, 32))).stddev[0]
                        small = gray.resize((9, 8), Image.Resampling.LANCZOS)
                        pix = list(
                            small.get_flattened_data()
                            if hasattr(small, "get_flattened_data")
                            else small.getdata()
                        )
                        bits = 0
                        for y in range(8):
                            for x in range(8):
                                bits = (bits << 1) | (
                                    pix[y * 9 + x] > pix[y * 9 + x + 1]
                                )
                        raw.append(
                            {
                                "page": pnum,
                                "image_name": str(key),
                                "width": im.width,
                                "height": im.height,
                                "encoded_sha256": digest(item.data),
                                "pixel_sha256": digest(
                                    str(im.size).encode() + im.tobytes()
                                ),
                                "dhash": f"{bits:016x}",
                                "grayscale_stddev": spread,
                            }
                        )
                    except Exception as e:
                        raw.append(
                            {
                                "page": pnum,
                                "image_name": str(key),
                                "extraction_error": type(e).__name__,
                            }
                        )
            except Exception as e:
                raw.append({"page": pnum, "extraction_error": type(e).__name__})
        dump(cache, raw)
    results = []
    for r in raw:
        if r.get("extraction_error"):
            ctx.warnings.append(
                f"{doc['id']} page {r['page']}: image extraction incomplete ({r['extraction_error']})"
            )
            continue
        results.append(
            {
                **r,
                "bidder": doc.get("bidder"),
                "round": doc["round"],
                "lot": doc["lot"],
                "evidence": ctx.evidence(
                    doc,
                    r["page"],
                    image_name=r["image_name"],
                    extraction="embedded_image",
                ),
            }
        )
    return results


def m06(ctx):
    for rnd, lot in groups(ctx.documents("bid")):
        if not any(
            d["round"] == rnd and d["lot"] == lot
            for d in ctx.documents("tender", "public_template")
        ):
            ctx.warnings.append(
                f"{rnd}/{lot}: no tender/public baseline for excluding common images"
            )
    records = [r for d in pdf_docs(ctx) for r in image_records(ctx, d)]
    public = defaultdict(set)
    for d in pdf_docs(ctx, ("tender", "public_template")):
        for r in image_records(ctx, d):
            public[(d["round"], d["lot"])].add(r["pixel_sha256"])
    usable = [
        r
        for r in records
        if r["grayscale_stddev"] >= 5
        and r["pixel_sha256"] not in public[(r["round"], r["lot"])]
    ]
    findings = []
    for scope, rs in shared(usable, "pixel_sha256"):
        findings.append(
            {
                "kind": "identical_decoded_pixels",
                "pixel_sha256": scope[2],
                "evidence": [r["evidence"] for r in rs],
            }
        )
    # Collapse repeated copies within a bidder before pairwise perceptual comparison.
    unique = {(r["round"], r["lot"], r["bidder"], r["pixel_sha256"]): r for r in usable}
    compared = 0
    cap = int(ctx.params.get("image_pair_limit", 500000))
    threshold = int(ctx.params.get("image_dhash_distance", 3))
    if not 1 <= cap <= 2000000 or not 0 <= threshold <= 16:
        raise ValueError("Invalid image comparison limit/threshold")
    truncated = False
    for a, b in combinations(unique.values(), 2):
        if a["bidder"] == b["bidder"] or (a["round"], a["lot"]) != (
            b["round"],
            b["lot"],
        ):
            continue
        if a["pixel_sha256"] == b["pixel_sha256"]:
            continue
        if abs(a["width"] / a["height"] - b["width"] / b["height"]) > 0.04:
            continue
        if compared >= cap:
            truncated = True
            break
        compared += 1
        distance = (int(a["dhash"], 16) ^ int(b["dhash"], 16)).bit_count()
        if distance <= threshold:
            findings.append(
                {
                    "kind": "perceptual_image_candidate",
                    "dhash_distance": distance,
                    "evidence": [a["evidence"], b["evidence"]],
                }
            )
    if truncated:
        ctx.warnings.append(
            "Perceptual image pair limit reached; remaining comparisons not performed"
        )
    return outcome(
        {
            "embedded_images": len(records),
            "usable_images": len(usable),
            "perceptual_pairs_compared": compared,
            "pair_limit_reached": truncated,
            "dhash_threshold": threshold,
        },
        findings,
        status="partial" if truncated else "ok",
        limitations=[
            "Embedded images are compared; vector-only page composition and arbitrary cropped-page matches are not covered.",
            "dHash is a coarse candidate filter, especially for similar scanned forms; visually compare numbers, seals, and content.",
            "Public template images are excluded by exact decoded-pixel hash; other common logos/product brochures require model exclusion.",
        ],
    )


def parse_pdf_date(value):
    m = re.fullmatch(r"D:(\d{14})(Z|[+-]\d{2}\x27?\d{2}\x27?)?", str(value))
    if not m:
        return None
    zone = m[2]
    try:
        dt = datetime.strptime(m[1], "%Y%m%d%H%M%S")
        if zone:
            zone = "+0000" if zone == "Z" else zone.replace("'", "")
            dt = datetime.strptime(m[1] + zone, "%Y%m%d%H%M%S%z")
        return dt
    except ValueError:
        return None


def m10(ctx):
    records = []
    findings = []
    for d in pdf_docs(ctx):
        reader = PdfReader(d["_path"])
        meta = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
        rec = {
            "bidder": d["bidder"],
            "round": d["round"],
            "lot": d["lot"],
            "metadata": meta,
            "pdf_pages": len(reader.pages),
            "evidence": ctx.evidence(d, None, extraction="pdf_info_dictionary"),
        }
        a = parse_pdf_date(meta.get("/CreationDate"))
        b = parse_pdf_date(meta.get("/ModDate"))
        rec["time_order_check"] = "unavailable"
        if a and b and (a.tzinfo is None) == (b.tzinfo is None):
            rec["time_order_check"] = (
                "creation_after_modification"
                if a > b
                else "creation_not_after_modification"
            )
        records.append(rec)
    for field in ("/Author", "/Creator", "/Producer", "/CreationDate", "/ModDate"):
        rr = [{**r, "value": r["metadata"].get(field)} for r in records]
        for scope, rs in shared(rr, "value"):
            findings.append(
                {
                    "kind": "shared_metadata_value",
                    "field": field,
                    "value": scope[2],
                    "common_software_field": field in ("/Creator", "/Producer"),
                    "evidence": [r["evidence"] for r in rs],
                }
            )
    return outcome(
        {"pdfs_examined": len(records)},
        findings,
        records=records,
        limitations=[
            "Metadata can be edited or overwritten. Shared producer/creator commonly indicates the same software/platform.",
            "Only complete PDF dates are ordered; unspecified timezone is not guessed. No MAC/IP or platform logs were collected.",
        ],
    )


def resource_fonts(resources, seen=None, on_missing=None):
    seen = set() if seen is None else seen
    resources = (
        resources.get_object() if hasattr(resources, "get_object") else resources
    )
    if (
        resources is None
        or isinstance(resources, NullObject)
        or not hasattr(resources, "get")
    ):
        if on_missing:
            on_missing()
        return
    font_map = resources.get("/Font", {})
    if not hasattr(font_map, "items"):
        if on_missing:
            on_missing()
        font_map = {}
    for name, ref in font_map.items():
        try:
            font = ref.get_object() if hasattr(ref, "get_object") else ref
        except Exception:
            if on_missing:
                on_missing()
            continue
        if font is None or isinstance(font, NullObject) or not hasattr(font, "get"):
            if on_missing:
                on_missing()
            continue
        yield str(name), font
    xobjects = resources.get("/XObject", {})
    if not hasattr(xobjects, "values"):
        if on_missing:
            on_missing()
        xobjects = {}
    for ref in xobjects.values():
        obj = ref.get_object()
        key = (getattr(ref, "idnum", None), getattr(ref, "generation", None))
        if obj is None or isinstance(obj, NullObject):
            if on_missing:
                on_missing()
            continue
        if obj.get("/Subtype") == "/Form" and key not in seen:
            seen.add(key)
            if obj.get("/Resources"):
                yield from resource_fonts(obj["/Resources"], seen, on_missing)


def font_records(ctx, doc):
    cache = ctx.cache / (
        digest((doc["sha256"] + "fonts-v4" + str(doc.get("pages"))).encode())
        + ".fonts.json"
    )
    if cache.exists():
        saved = json.loads(cache.read_text())
        raw = saved["records"]
        issues = saved["warnings"]
    else:
        raw = []
        issues = []
        reader = PdfReader(doc["_path"])
        for pnum, p in selected_pages(doc, reader):

            def missing():
                issues.append(
                    f"{doc['id']} page {pnum}: null/unresolved XObject or font entry skipped; font-resource coverage may be incomplete"
                )

            for name, font in resource_fonts(
                p.get("/Resources", {}), on_missing=missing
            ):
                base = str(font.get("/BaseFont", "")).lstrip("/")
                match = re.match(r"^([A-Z]{6})\+(.+)$", base)
                children = font.get("/DescendantFonts") or [font]
                for child in children:
                    f = child.get_object()
                    descriptor = f.get("/FontDescriptor")
                    descriptor = descriptor.get_object() if descriptor else {}
                    hashes = {}
                    for field in ("/FontFile", "/FontFile2", "/FontFile3"):
                        if descriptor.get(field):
                            hashes[field] = digest(descriptor[field].get_data())
                    raw.append(
                        {
                            "page": pnum,
                            "resource_name": name,
                            "base_font": base,
                            "subset_prefix": match[1] if match else None,
                            "font_family": match[2] if match else base,
                            "embedded_hashes": hashes,
                        }
                    )
        dump(cache, {"records": raw, "warnings": issues})
    ctx.warnings.extend(issues)
    return [
        {
            **r,
            "bidder": doc["bidder"],
            "round": doc["round"],
            "lot": doc["lot"],
            "evidence": ctx.evidence(
                doc,
                r["page"],
                font_resource=r["resource_name"],
                base_font=r["base_font"],
            ),
        }
        for r in raw
    ]


def m11(ctx):
    records = [r for d in pdf_docs(ctx) for r in font_records(ctx, d)]
    findings = []
    for field in ("base_font", "subset_prefix"):
        for scope, rs in shared(records, field):
            findings.append(
                {
                    "kind": "shared_font_name_or_prefix",
                    "field": field,
                    "value": scope[2],
                    "evidence": [r["evidence"] for r in rs],
                }
            )
    hashes = [
        {**r, "font_bytes_sha256": v}
        for r in records
        for v in r["embedded_hashes"].values()
    ]
    for scope, rs in shared(hashes, "font_bytes_sha256"):
        findings.append(
            {
                "kind": "identical_embedded_font_bytes",
                "sha256": scope[2],
                "evidence": [r["evidence"] for r in rs],
            }
        )
    return outcome(
        {
            "font_resource_occurrences": len(records),
            "embedded_font_occurrences": len(hashes),
        },
        findings,
        status="ok" if records else "insufficient_data",
        limitations=[
            "Resource presence is reported, not per-glyph rendering usage.",
            "Same subset prefix, font family, or standard embedded font is not proof of a shared bid draft.",
        ],
    )


GEAR = [int(digest(bytes([i]))[:16], 16) for i in range(256)]


def cdc(data, minimum=512, maximum=8192, mask=2047):
    """Deterministic content-defined chunks; positions refer to decoded stream bytes."""
    start = 0
    h = 0
    for i, b in enumerate(data):
        h = ((h << 1) + GEAR[b]) & ((1 << 64) - 1)
        n = i + 1 - start
        if n >= minimum and ((h & mask) == 0 or n >= maximum):
            yield start, data[start : i + 1]
            start = i + 1
            h = 0
    if len(data) - start >= minimum:
        yield start, data[start:]


def stream_records(ctx, doc):
    cache = ctx.cache / (
        digest((doc["sha256"] + "cdc-v1" + str(doc.get("pages"))).encode())
        + ".chunks.json"
    )
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        raw = []
        reader = PdfReader(doc["_path"])
        for pnum, p in selected_pages(doc, reader):
            content = p.get_contents()
            if content is None:
                continue
            data = content.get_data()
            if len(data) >= 128:
                raw.append(
                    {
                        "page": pnum,
                        "kind": "decoded_page_content_stream",
                        "offset": 0,
                        "length": len(data),
                        "hash": digest(data),
                    }
                )
            for offset, chunk in cdc(data):
                raw.append(
                    {
                        "page": pnum,
                        "kind": "content_defined_chunk",
                        "offset": offset,
                        "length": len(chunk),
                        "hash": digest(chunk),
                    }
                )
        dump(cache, raw)
    return [
        {
            **r,
            "bidder": doc.get("bidder"),
            "round": doc["round"],
            "lot": doc["lot"],
            "evidence": ctx.evidence(
                doc,
                r["page"],
                decoded_stream_offset=r["offset"],
                byte_length=r["length"],
            ),
        }
        for r in raw
    ]


def m12(ctx):
    for rnd, lot in groups(ctx.documents("bid")):
        if not any(
            d["round"] == rnd and d["lot"] == lot
            for d in ctx.documents("tender", "public_template")
        ):
            ctx.warnings.append(
                f"{rnd}/{lot}: no tender/public baseline for excluding common content streams"
            )
    records = [r for d in pdf_docs(ctx) for r in stream_records(ctx, d)]
    public = defaultdict(set)
    for d in pdf_docs(ctx, ("tender", "public_template")):
        for r in stream_records(ctx, d):
            public[(r["round"], r["lot"])].add(r["hash"])
    rows = [r for r in records if r["hash"] not in public[(r["round"], r["lot"])]]
    findings = [
        {
            "kind": "shared_decoded_pdf_content",
            "sha256": k[2],
            "block_types": sorted({r["kind"] for r in rs}),
            "evidence": [r["evidence"] for r in rs],
        }
        for k, rs in shared(rows, "hash")
    ]
    stats = []
    per = defaultdict(set)
    for r in rows:
        if r["kind"] == "content_defined_chunk":
            per[(r["round"], r["lot"], r["bidder"])].add(r["hash"])
    for (ka, a), (kb, b) in combinations(per.items(), 2):
        if ka[:2] != kb[:2]:
            continue
        stats.append(
            {
                "round": ka[0],
                "lot": ka[1],
                "bidder_a": ka[2],
                "bidder_b": kb[2],
                "intersection_chunks": len(a & b),
                "union_chunks": len(a | b),
                "jaccard": len(a & b) / len(a | b) if a | b else None,
            }
        )
    return outcome(
        {
            "content_records": len(records),
            "template_records_excluded": len(records) - len(rows),
            "pairwise_chunk_sets": stats,
        },
        findings,
        limitations=[
            "Hashes cover decoded page content streams, excluding font/image streams; not whole-file binary Jaccard.",
            "CDC parameters: min512, mean boundary mask2047, max8192 bytes; short trailing chunks are omitted.",
            "Shared graphics operators/platform headers can match. Byte equality does not establish shared authorship.",
        ],
    )
