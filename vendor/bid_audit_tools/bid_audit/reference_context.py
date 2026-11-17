"""Source-addressed form and material context; no authorship or risk verdicts."""

import re
from difflib import SequenceMatcher


def canonical(text):
    return re.sub(r"[\W_]+", "", text).lower()


def purposes(text):
    result = set()
    compact = canonical(text)
    for name, pattern in [
        ("product_registration", r"医疗器械注册(?:证|证明)"),
        (
            "enterprise_license",
            r"医疗器械(?:生产许可|经营许可|经营备案)|第二类医疗器械经营备案",
        ),
        (
            "domestic_ratio_undertaking",
            r"产品成本之和|成本比例|比例达到\d+以上|(?=.*本国产品)(?=.*郑重承诺)",
        ),
        (
            "domestic_product_declaration",
            r"以下产品属于本国产品|本国产品声明函|(?=.*本国产品)(?=.*郑重声明)",
        ),
        ("undertaking_form", r"郑重承诺|严格落实采购文件以下条款|对于.{0,300}项目编号"),
        (
            "qualification_declaration",
            r"资格声明|关于贵公司发布.{0,300}采购公告.{0,100}愿意参加投标",
        ),
        ("bid_letter", r"我方愿参与投标|你方组织的.{0,100}项目的招标"),
    ]:
        if re.search(pattern, compact):
            result.add(name)
    if not result and re.search(r"采购计划编号|采购项目编号|采购项目名称", compact):
        result.add("document_metadata")
    return result


def reference_compatibility(query, reference):
    a, b = purposes(query), purposes(reference)
    conflicts = []
    for left, right in [
        ("product_registration", "enterprise_license"),
        ("domestic_ratio_undertaking", "domestic_product_declaration"),
        ("undertaking_form", "bid_letter"),
        ("qualification_declaration", "bid_letter"),
        ("undertaking_form", "document_metadata"),
        ("qualification_declaration", "document_metadata"),
    ]:
        if (left in a and right in b and left not in b) or (right in a and left in b and right not in b):
            conflicts.append([left, right])
    return {
        "query_purposes": sorted(a),
        "reference_purposes": sorted(b),
        "status": "different_explicit_purpose" if conflicts else "same_purpose_observed" if a & b else "purpose_unverified",
        "conflicting_purposes": conflicts,
        "same_clause_verified": False,
        "note": "语义用途分类只是参照定位提示，不证明条款对应关系或共同编辑；产品注册材料与企业生产经营资质不能相互替代。",
    }


def _units(block):
    """Retain literal text; add short form openings without swallowing conditions."""
    from .m01_recall import plain

    text = plain(block.get("text", block.get("raw", "")))
    seen = set()
    parts = [text]
    parts.extend(re.split(r"[\n。；;]", text))
    # A form opening is a useful source locator, never an explanation of the
    # later substantive promises. The complete source block remains attached.
    for match in re.finditer(r"对于[^。；;]{0,500}?郑重承诺如下[：:]?", text, re.S):
        parts.append(match[0])
    for part in parts:
        part = part.strip()
        if 16 <= len(canonical(part)) <= 2200 and part not in seen:
            seen.add(part)
            yield part


def _slot_pattern(text):
    # Only printed blanks and an explicitly empty project/number form are slots.
    # Do not wildcard arbitrary words, dates, responsibilities or conditions.
    text = re.sub(
        r"对于\s*(?:_{2,})?\s*项目\s*[（(]\s*项目编号\s*[:：]\s*(?:_{2,})?\s*[）)]",
        "对于ζ槽ζ项目（项目编号：ζ槽ζ）",
        text,
    )
    text = re.sub(r"_{2,}", "ζ槽ζ", text)
    anchors = [canonical(x) for x in text.split("ζ槽ζ")]
    if len(anchors) < 2 or sum(map(len, anchors)) < 16:
        return None
    # Leading/trailing blanks are not allowed to explain arbitrary extra prose.
    if not anchors[0] or not anchors[-1]:
        return None
    return re.compile(".{0,200}?".join(re.escape(x) for x in anchors))


class ReferenceCatalog:
    def __init__(self, references):
        from .source_quality import _grams

        self.rows = []
        self.inverted = {}
        for doc_id, blocks in references.items():
            for block in blocks:
                for quote in _units(block):
                    key = canonical(quote)
                    row = {
                        "document_id": doc_id,
                        "key": key,
                        "quote": quote,
                        "pattern": _slot_pattern(quote),
                        "purposes": purposes(quote),
                        "grams": _grams(key),
                        "source": {
                            k: block.get(k)
                            for k in (
                                "page",
                                "physical_pages",
                                "chunk_ids",
                                "positions",
                                "source_channel",
                            )
                        },
                    }
                    index = len(self.rows)
                    self.rows.append(row)
                    for gram in row["grams"]:
                        self.inverted.setdefault(gram, set()).add(index)

    def lookup(self, query, limit=3):
        from .source_quality import _grams

        key = canonical(query)
        if len(key) < 16:
            return []
        cache = getattr(self, "_lookup_cache", None)
        if cache is None:
            self._lookup_cache = cache = {}
        if key in cache:
            return cache[key][:limit]
        grams = _grams(key)
        candidates = set()
        for gram in grams:
            candidates.update(self.inverted.get(gram, ()))
        ranked = sorted(candidates, key=lambda i: (-len(grams & self.rows[i]["grams"]), i))[:80]

        def observation(row, ratio, kind, full):
            return {
                "reference_document_id": row["document_id"],
                "reference_source": {**row["source"], "text": row["quote"]},
                "matched_template_quote": row["quote"],
                "match_kind": kind,
                "query_fully_explained_by_literal_template": full,
                "reference_compatibility": reference_compatibility(query, row["quote"]),
                "similarity": round(ratio, 4),
                "scope": "Only this quoted unit; surrounding clauses and differences require separate checks.",
                "original_image_verified": False,
            }

        # Resolve literal/printed-blank coverage before fuzzy matching. Once a
        # complete quoted unit has a literal source, weaker alternatives cannot
        # change that observation and need not consume quadratic edit distance.
        results = []
        for i in ranked:
            row = self.rows[i]
            literal = key in row["key"]
            slot = row["pattern"].fullmatch(key) if row["pattern"] is not None else None
            if literal or slot:
                results.append(
                    observation(
                        row,
                        SequenceMatcher(None, key, row["key"], autojunk=False).ratio(),
                        "literal_containment" if literal else "explicit_template_slots",
                        True,
                    )
                )
        if not results:
            for i in ranked:
                row = self.rows[i]
                matcher = SequenceMatcher(None, key, row["key"], autojunk=False)
                # Both bounds are >= full ratio; this pruning loses no pair
                # that could reach the existing 0.45 similarity cutoff.
                if matcher.real_quick_ratio() < 0.45 or matcher.quick_ratio() < 0.45:
                    continue
                ratio = matcher.ratio()
                if ratio >= 0.45:
                    results.append(observation(row, ratio, "similar_source_only", False))
        results.sort(
            key=lambda r: (
                not r["query_fully_explained_by_literal_template"],
                r["reference_compatibility"]["status"] == "different_explicit_purpose",
                r["reference_compatibility"]["status"] != "same_purpose_observed",
                -r["similarity"],
                len(r["matched_template_quote"]),
            )
        )
        unique, seen = [], set()
        for row in results:
            signature = (
                row["reference_document_id"],
                tuple(row["reference_source"].get("physical_pages") or []),
                row["matched_template_quote"],
            )
            if signature not in seen:
                seen.add(signature)
                unique.append(row)
        cache[key] = unique
        return unique[:limit]


def material_context(blocks, source):
    """Expose same-page headers and neighboring OCR blocks beside a fragment."""
    page_set = set(source.get("physical_pages") or [])
    if source.get("page") is not None:
        page_set.add(source["page"])
    chunk_set = set(source.get("chunk_ids") or [])
    selected = []
    seen = set()
    for block in blocks:
        pages = set(block.get("physical_pages") or [])
        if block.get("page") is not None:
            pages.add(block["page"])
        if not (pages & page_set or chunk_set & set(block.get("chunk_ids") or [])):
            continue
        text = block.get("text", "")
        if text in seen:
            continue
        seen.add(text)
        # Keep whole ordinary OCR blocks/pages. Long sources retain a header and
        # the quote neighborhood, with explicit truncation, never a guessed role.
        original_length = len(text)
        query = source.get("text", "")
        where = text.find(query) if query else -1
        if original_length > 6000:
            text = text[:2400] + "\n[来源上下文中段省略，完整原页可读取]\n" + (text[max(0, where - 1200) : where + len(query) + 1200] if where >= 0 else text[-2400:])
        selected.append(
            {
                **{k: block.get(k) for k in ("page", "physical_pages", "chunk_ids", "source_channel")},
                "text": text,
                "original_length": original_length,
                "context_truncated": original_length > 6000,
            }
        )
    body = "\n".join(row["text"] for row in selected)
    markers = []
    for match in re.finditer(
        r"[^\n。；;]{0,35}(?:合同编号|项目编号|采购项目|项目名称|甲方|乙方|采购人|供应商|签订日期|签约日期|业绩合同)[^\n。；;]{0,150}",
        body,
    ):
        if match[0] not in markers:
            markers.append(match[0])
    return {
        "source_context": selected,
        "scope_markers": markers,
        "contract_or_performance_material_observed": bool(re.search(r"甲方|乙方|甲乙双方|政府采购合同|业绩合同", body)),
        "current_procurement_scope_verified": False,
        "automatically_excluded_from_cross_bid_review": False,
        "note": "先核项目、合同双方、日期及附件角色。历史业绩合同的采购方式不能直接与本次采购方式比较；但历史附件中跨主体共用的私有信息或异常编辑仍须核查，不能整体排除。",
    }
