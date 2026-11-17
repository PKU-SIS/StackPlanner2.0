"""Locate current reference text for response records, without excluding risks."""

from .reference_context import ReferenceCatalog


def locate_response_sources(data, documents, references):
    catalogs = {}
    doc_by_id = {d["id"]: d for d in documents}
    observations = []
    counts = {}
    for collection in ("findings", "requirement_copy_pairs"):
        rows = data.get(collection, [])
        found = 0
        checked = 0
        for offset, row in enumerate(rows):
            a, b = doc_by_id.get(row.get("document_a")), doc_by_id.get(row.get("document_b"))
            if not a or not b:
                continue
            scope = (a.get("round", "1"), a.get("lot", "1"))
            if scope != (b.get("round", "1"), b.get("lot", "1")):
                continue
            if scope not in catalogs:
                pool = {d["id"]: d["blocks"] for d in references if (d.get("round", "1"), d.get("lot", "1")) == scope}
                catalogs[scope] = ReferenceCatalog(pool)
            catalog = catalogs[scope]
            # Only byte-identical field values share a query; all originals stay
            # available, including punctuation/operators and OCR differences.
            queries = {}
            for field in ("requirement", "response_a", "response_b"):
                text = row.get(field)
                if isinstance(text, str) and text.strip():
                    queries.setdefault(text, []).append(field)
            if not queries:
                continue
            checked += 1
            located = []
            for text, fields in queries.items():
                matches = [m for m in catalog.lookup(text) if (m["query_fully_explained_by_literal_template"] or m["similarity"] >= 0.75) and m["reference_compatibility"]["status"] != "different_explicit_purpose"]
                if matches:
                    located.append({"fields": fields, "original_query": text, "reference_matches": matches})
            if not located:
                continue
            found += 1
            observations.append(
                {
                    "source_collection": collection,
                    "source_row_offset": offset,
                    "document_a": a["id"],
                    "document_b": b["id"],
                    "bidder_a": a.get("bidder"),
                    "bidder_b": b.get("bidder"),
                    "round": scope[0],
                    "lot": scope[1],
                    "section": row.get("section"),
                    "located_texts": located,
                    "observation_kind": "current_reference_text_locations",
                    "automatically_excludes_shared_response_risk": False,
                    "note": "以下是本次实际提供招标/公共文件中的原文与页码。请比较原句、对象、数值和条件，判断能解释哪一部分共文；它不是风险判定，也不代表整段或全部要求已验证。基础比对原来的未核标记不是查到这些原文后的最终结论。",
                }
            )
        counts[collection] = {"records_checked": checked, "records_with_source_locations": found, "records_without_a_located_candidate": checked - found}
    return {
        "observations": observations,
        "summary": {
            "counts_by_source_collection": counts,
            "document_assignment_scope": "same round and lot; clause applicability still requires review",
            "no_match_is_absence_proof": False,
            "changes_original_reference_verification_flags": False,
            "note": "来源定位与基础比对初始验证状态分开。已找到的原文应实际核对，未知项目继续待核；不能把全部商务条款一概写成招标中未找到，也不能从相似度直接认定已排除。",
        },
    }
