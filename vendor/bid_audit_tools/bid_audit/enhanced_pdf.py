"""Disclosed PDF observation domains; equality is not authorship evidence."""

from collections import Counter, defaultdict
from itertools import combinations
import re

from pypdf import PdfReader

from .core import digest
from .pdf_rules import pdf_docs


def syntax_windows(raw, width=80):
    """Every byte-offset window in printable, non-stream indirect-object syntax."""
    objects = list(re.finditer(rb"(?m)(\d+)\s+(\d+)\s+obj\b", raw))
    for index, m in enumerate(objects):
        end = objects[index + 1].start() if index + 1 < len(objects) else len(raw)
        body = raw[m.end() : end]
        endobj = body.find(b"endobj")
        if endobj >= 0:
            body = body[:endobj]
        ranges = []
        cursor = 0
        for stream in re.finditer(
            rb"\bstream(?:\r\n|\r|\n).*?\bendstream\b", body, re.S
        ):
            ranges.append((cursor, stream.start()))
            cursor = stream.end()
        ranges.append((cursor, len(body)))
        for start, stop in ranges:
            for printable in re.finditer(
                rb"[\x09\x0a\x0d\x20-\x7e]{" + str(width).encode() + rb",}",
                body[start:stop],
            ):
                region = printable.group()
                base = m.end() + start + printable.start()
                for offset in range(len(region) - width + 1):
                    yield {
                        "object_id": int(m[1]),
                        "generation": int(m[2]),
                        "offset": base + offset,
                        "length": width,
                        "bytes": region[offset : offset + width],
                    }


def marker_features(raw):
    return {
        "eof_token_occurrences": len(re.findall(rb"%%EOF", raw)),
        "eof_line_markers": len(re.findall(rb"(?m)^\s*%%EOF\s*$", raw)),
        "startxref_markers": len(re.findall(rb"\bstartxref\s+\d+", raw)),
        "prev_reference_values": sorted(
            {int(m[1]) for m in re.finditer(rb"/Prev\s+(\d+)", raw)}
        ),
        "xref_stream_type_occurrences": len(re.findall(rb"/Type\s*/XRef\b", raw)),
        "verified_incremental_save_count": None,
        "interpretation": "Raw tokens/Prev references are observations, not a verified save-operation count or a normal/abnormal threshold.",
    }


def m10(ctx):
    from .pdf_rules import m10 as legacy

    data = legacy(ctx)
    by_doc = {r["evidence"]["document_id"]: r for r in data["records"]}
    for doc in pdf_docs(ctx):
        raw = doc["_path"].read_bytes()
        reader = PdfReader(doc["_path"])
        ocgs = []
        try:
            root = reader.trailer["/Root"]
            props = root.get("/OCProperties") or {}
            props = props.get_object() if hasattr(props, "get_object") else props
            for ref in props.get("/OCGs", []):
                obj = ref.get_object()
                ocgs.append(
                    {
                        "name": str(obj.get("/Name", "")),
                        "object_id": getattr(ref, "idnum", None),
                    }
                )
        except Exception as exc:
            ctx.warnings.append(
                doc["id"]
                + ": optional-content metadata unavailable ("
                + type(exc).__name__
                + ")"
            )
        row = by_doc[doc["id"]]
        row["structure_markers"] = marker_features(raw)
        row["optional_content_groups"] = ocgs
    data["metrics"]["structural_marker_scan_performed"] = True
    data["limitations"] += [
        "EOF/startxref/Prev token counts and optional-content names are disclosed, not called incremental-save operations. Watermark names/default software alone cannot establish a common machine."
    ]
    return data


def raw_representatives(groups):
    """Expose every observed participant-set/type, not six adjacent windows."""
    representatives = {}
    for group in groups:
        docs = sorted(group["occurrence_counts_by_document"])
        key = (tuple(docs), group["resource_type_hint"])
        if key not in representatives:
            positions = {}
            for source in group["evidence"]:
                positions.setdefault(
                    source["document_id"],
                    {
                        k: source[k]
                        for k in [
                            "document_id",
                            "object_id",
                            "object_generation",
                            "raw_byte_offset",
                            "byte_length",
                        ]
                        if k in source
                    },
                )
            representatives[key] = {
                "kind": "raw_syntax_class_representative",
                "document_ids": docs,
                "resource_type_hint": group["resource_type_hint"],
                "decoded_preview": group["decoded_preview"],
                "sha256": group["sha256"],
                "window_hash_groups": 0,
                "representative_source_positions": list(positions.values()),
                "evidence": group["evidence"],
                "note": "One real byte example per participant-set/type. Group count includes overlapping windows and is neither independent objects nor a risk count. Full positions are in the original findings.",
            }
        representatives[key]["window_hash_groups"] += 1
    return list(representatives.values())


def raw_review_facts(groups, representatives):
    """Mechanical coverage and literal key observations over all shared windows."""
    names = [
        "/Producer",
        "/Creator",
        "/CreationDate",
        "/ModDate",
        "/ID",
        "/Info",
        "/Metadata",
    ]
    patterns = {
        name: re.compile(re.escape(name) + r"(?=[\s<>\[\](){}%/]|$)") for name in names
    }
    patterns["XMP"] = re.compile(r"\b(?:xmp|xmpmeta|xpacket)\b", re.I)
    counts = dict.fromkeys(patterns, 0)
    observations = []
    scope = (
        "All scanned shared printable80-byte non-stream syntax windows only; not a whole-PDF metadata absence claim. "
        "Literal tokens may be keys or values: they do not prove dictionary ownership, identical metadata values or risk. "
        "Overlapping window groups are not independent evidence."
    )
    for group in groups:
        text = group.get("decoded_preview", "")
        seen = [name for name, pattern in patterns.items() if pattern.search(text)]
        for name in seen:
            counts[name] += 1
        if seen:
            observations.append(
                {**group, "observed_literal_pdf_keys": seen, "observation_scope": scope}
            )
    covered = sum(row["window_hash_groups"] for row in representatives)
    return {
        "raw_representative_window_hash_groups_total": covered,
        "raw_representative_partition_complete": covered == len(groups),
        "raw_literal_pdf_key_group_counts": counts,
        "raw_literal_pdf_key_scope": scope,
    }, observations


def m12(ctx):
    from .pdf_rules import m12 as legacy

    data = legacy(ctx)
    width = 80
    index = defaultdict(dict)
    totals = Counter()
    public = set()
    for doc in pdf_docs(ctx, ("tender", "public_template")):
        for row in syntax_windows(doc["_path"].read_bytes(), width):
            public.add((doc["round"], doc["lot"], digest(row["bytes"])))
    for doc in pdf_docs(ctx):
        for row in syntax_windows(doc["_path"].read_bytes(), width):
            h = digest(row["bytes"])
            totals[doc["id"]] += 1
            if (doc["round"], doc["lot"], h) in public:
                continue
            item = index[(doc["round"], doc["lot"], h)].setdefault(
                doc["id"],
                {"doc": doc, "count": 0, "positions": [], "bytes": row["bytes"]},
            )
            item["count"] += 1
            if len(item["positions"]) < 12:
                item["positions"].append({k: v for k, v in row.items() if k != "bytes"})
    groups = []
    pairs = Counter()
    for (rnd, lot, h), by_doc in index.items():
        if len({x["doc"]["bidder"] for x in by_doc.values()}) < 2:
            continue
        refs = []
        counts = {}
        example = next(iter(by_doc.values()))["bytes"]
        for doc_id, item in by_doc.items():
            counts[doc_id] = item["count"]
            doc = item["doc"]
            for pos in item["positions"]:
                refs.append(
                    ctx.evidence(
                        doc,
                        None,
                        example.decode("ascii"),
                        extraction="raw_pdf_nonstream_syntax_sliding_window",
                        raw_byte_offset=pos["offset"],
                        object_id=pos["object_id"],
                        object_generation=pos["generation"],
                        byte_length=width,
                        sha256=h,
                    )
                )
        subtype = (
            "font_or_common_pdf_dictionary"
            if any(
                s in example for s in [b"/Font", b"/Encoding", b"/CID", b"/Descriptor"]
            )
            else "pdf_object_syntax"
        )
        groups.append(
            {
                "kind": "shared_raw_pdf_syntax_window",
                "sha256": h,
                "byte_length": width,
                "decoded_preview": example.decode("ascii"),
                "resource_type_hint": subtype,
                "occurrence_counts_by_document": counts,
                "position_examples_per_document_maximum": 12,
                "all_positions_shown": all(x["count"] <= 12 for x in by_doc.values()),
                "evidence": refs,
                "review_note": "Adjacent overlapping 80-byte windows are not independent evidence. Common font dictionaries/platform syntax may explain equality; authorship is not inferred.",
            }
        )
        for a, b in combinations(sorted(by_doc), 2):
            pairs[a + "|" + b] += 1
    data["findings"].extend(groups)
    data["raw_syntax_representatives"] = raw_representatives(groups)
    review_metrics, key_observations = raw_review_facts(
        groups, data["raw_syntax_representatives"]
    )
    data["metrics"].update(review_metrics)
    data["raw_metadata_key_observations"] = key_observations
    data["metrics"]["raw_representative_groups"] = len(
        data["raw_syntax_representatives"]
    )
    data["metrics"].update(
        raw_syntax_domain="printable non-stream indirect-object syntax only",
        raw_window_width=width,
        raw_window_stride=1,
        raw_syntax_windows_examined_by_document=dict(totals),
        raw_template_window_hashes=len(public),
        raw_shared_window_hash_groups=len(groups),
        raw_shared_hashes_by_document_pair=dict(pairs),
    )
    data["limitations"] += [
        "The additional 80-byte sliding domain covers printable original indirect-object syntax, excluding compressed stream payloads. It is distinct from legacy decoded content CDC and from unrestricted whole-file sliding bytes. Neighboring windows are heavily correlated, not independent matching objects."
    ]
    return data
