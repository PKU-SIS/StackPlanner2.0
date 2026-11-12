"""Optional generic original/OCR enhancement dispatch; no historical answers."""

from collections import Counter
import re

from .structured_text import blocks, coverage


def image_ocr(ctx, data):
    by_doc = {d["id"]: d for d in ctx.documents("bid")}
    cache = {}
    types = Counter()
    for finding in data["findings"]:
        endpoints = []
        for ref in finding.get("evidence", []):
            doc_id, page = ref["document_id"], ref.get("page")
            if (doc_id, page) not in cache:
                rows = []
                if doc_id in by_doc and page is not None:
                    for block in blocks(ctx, by_doc[doc_id], raw=False):
                        if page in block["physical_pages"]:
                            rows.append(
                                {
                                    "document_id": doc_id,
                                    "chunk_id": block["chunk_ids"][0],
                                    "content": block["raw"],
                                    "physical_pages": block["physical_pages"],
                                    "positions": block["positions"],
                                }
                            )
                cache[(doc_id, page)] = rows
            rows = cache[(doc_id, page)]
            text = "\n".join(r["content"] for r in rows)
            hint = (
                "public_credit_material"
                if any(w in text for w in ["信用中国", "公共信用信息"])
                else "identity_or_authorization_material"
                if any(w in text for w in ["许可证", "授权", "营业执照", "注册证"])
                else "other_or_image_only"
            )
            endpoints.append(
                {
                    "document_id": doc_id,
                    "page": page,
                    "material_type_hint": hint,
                    "ocr_chunks": rows,
                    "ocr_position_coverage_only": True,
                }
            )
            types[hint] += 1
        finding["ocr_endpoints"] = endpoints
        if len(endpoints) == 2:
            ids = [
                set(
                    re.findall(
                        r"(?<![0-9A-Z])[0-9A-Z]{15,18}(?![0-9A-Z])",
                        "\n".join(r["content"] for r in e["ocr_chunks"]),
                    )
                )
                for e in endpoints
            ]
            finding["shared_visible_identifiers"] = sorted(ids[0] & ids[1])
            finding["public_material_hint"] = all(
                e["material_type_hint"] == "public_credit_material" for e in endpoints
            )
        finding["review_note"] = (
            "OCR is page/chunk context, not cropped image identity or visual review. Exact public-credit hints are navigation only; compare certificate owner, identifiers and actual paired images."
        )
    data["metrics"]["ocr_endpoint_type_counts"] = dict(types)
    data["metrics"]["ocr_candidate_endpoints_collected"] = sum(
        len(f.get("ocr_endpoints", [])) for f in data["findings"]
    )
    return data


def price_context(ctx, data):
    from .price_sources import price_context as collect_price_sources

    return collect_price_sources(ctx, data)


def run(ctx, rule_id):
    ctx._include_geometry = rule_id in {"M03", "M04", "M07"}
    if rule_id in {"M03", "M04"}:
        from . import enhanced_tables

        data = getattr(enhanced_tables, rule_id.lower())(ctx)
    elif rule_id in {"M02", "M05", "M07", "M08", "M09"}:
        from . import enhanced_text

        data = getattr(enhanced_text, rule_id.lower())(ctx)
    elif rule_id in {"M10", "M12"}:
        from . import enhanced_pdf

        data = getattr(enhanced_pdf, rule_id.lower())(ctx)
    else:
        from .registry import TOOLS

        data = TOOLS[rule_id](ctx)
        if rule_id == "M11":
            from .pdf_rules import font_records, pdf_docs
            from pypdf import PdfReader

            records = []
            scan = []
            for doc in pdf_docs(ctx):
                fonts = font_records(ctx, doc)
                records.extend(fonts)
                pages = sorted({r["page"] for r in fonts})
                total = len(PdfReader(doc["_path"]).pages)
                scan.append(
                    {
                        "document_id": doc["id"],
                        "physical_page_count": total,
                        "pages_with_font_resources": len(pages),
                        "pages_without_recovered_font_resources": [
                            p for p in range(1, total + 1) if p not in pages
                        ],
                        "resource_occurrences": len(fonts),
                        "note": "No resource on a page may be valid image/vector content; unresolved resources remain warnings, not zero-match evidence.",
                    }
                )
            data["font_resource_inventory"] = records
            data["metrics"]["font_scan_coverage"] = scan
        if rule_id == "M06":
            data = image_ocr(ctx, data)
        if rule_id == "M13":
            data = price_context(ctx, data)
        if rule_id == "M15":
            from .supplemental_prices import supplement_m15

            data = supplement_m15(ctx, data)
    if hasattr(ctx, "_response_alignment_conflicts_v20"):
        data["alignment_conflicts"] = ctx._response_alignment_conflicts_v20
        data["metrics"]["source_alignment_conflict_rows"] = len(
            data["alignment_conflicts"]
        )
    data["metrics"]["enhancement_version"] = "general-source-v20"
    if rule_id not in {"M10", "M11", "M12", "M14"}:
        data["metrics"]["input_channel_coverage"] = coverage(ctx)
    return data
