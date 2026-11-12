"""Literal price constraints and configuration sources, never risk verdicts."""

import re
from collections import Counter

from .core import norm
from .structured_text import blocks, evidence, html_tables


_PRICE_LABEL = re.compile(
    r"预算(?:金额|总价)?|采购预算|(?:最高(?:投标|响应|报价)?|采购|单价|单套|分项|项目|总价)?限价|(?:报价|价格|单价|总价)[^。；\n]{0,12}不(?:得)?(?:超过|高于)"
)


def price_constraints(text):
    seen = set()
    for match in _PRICE_LABEL.finditer(text):
        start = (
            max(text.rfind(mark, 0, match.start()) for mark in ["。", "；", "\n"]) + 1
        )
        start = max(start, match.start() - 100)
        ends = [
            at
            for mark in ["。", "；", "\n"]
            if (at := text.find(mark, match.end())) >= 0
        ]
        end = min(ends) + 1 if ends else len(text)
        end = min(end, match.end() + 180)
        quote = text[start:end].strip()
        # Adjacent scalar cells are literal source text, not an inferred cap.
        if not re.search(
            r"[0-9零一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]", quote
        ) and not text[match.end() : end].strip(" ：:；;\n\t"):
            scalar = re.match(
                r"\s*(?:人民币|RMB|￥|¥)?\s*[0-9零一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟,，.．]+\s*(?:万元|元|万)?(?:[;；\n]|$)",
                text[end:],
            )
            if scalar:
                end += scalar.end()
                quote = text[start:end].strip()
        if not quote or quote in seen:
            continue
        seen.add(quote)
        label = match.group()
        yield {
            "field": "maximum_price"
            if "限价" in label or re.search(r"不(?:得)?(?:超过|高于)", label)
            else "budget",
            "text": quote,
            "scope_hint": "per_unit_or_item"
            if re.search(r"单[套台项价]|每[套台项]", quote)
            else "package_or_total"
            if re.search(r"采购包|总价|总额", quote)
            else "unspecified",
            "contains_arabic_number": bool(re.search(r"\d", quote)),
            "review_note": "A limit label or generic validity clause alone does not establish a numeric cap. Preserve unit/item/package scope and the original comparator.",
        }


def configuration_header(grid):
    """Find an actual short quantity/name header, including title+split rows."""
    name = quantity = False
    for index, row in enumerate(grid[:5]):
        cells = [re.sub(r"\s+", "", cell) for cell in row]
        name |= any(
            len(cell) <= 20
            and re.search(r"(?:产品|设备|货物|配件)?名称|配置(?:项目|内容|清单)?", cell)
            for cell in cells
        )
        quantity |= any(
            len(cell) <= 12 and re.search(r"数量|数目|配置数", cell) for cell in cells
        )
        if name and quantity:
            return index
    return None


def source_scope(doc, text):
    if doc["role"] == "tender":
        return "tender_requirement"
    heading = text[:1000]
    if re.search(
        r"业绩|以往|过往|类似项目|案例|合同货物|合同生效|采购合同|买卖合同|供货合同|甲方|乙方|中标通知书",
        heading,
    ):
        return "contract_or_performance_attachment_unverified"
    if re.search(r"分项报价|报价明细|开标一览|投标报价|货物一览", heading):
        return "bid_quotation"
    return "bid_configuration_scope_unverified"


def price_context(ctx, data):
    configs, budgets, contexts = [], [], []
    counts = Counter()
    for doc in ctx.documents("bid", "tender"):
        seen_budget, seen_tables, seen_context = set(), set(), set()
        for block in blocks(ctx, doc):
            if doc["role"] == "tender":
                for item in price_constraints(block["text"]):
                    key = (block["source_channel"], norm(item["text"]))
                    if key in seen_budget:
                        continue
                    seen_budget.add(key)
                    budgets.append(
                        {
                            **item,
                            "document_id": doc["id"],
                            "evidence": [
                                evidence(
                                    ctx,
                                    doc,
                                    block,
                                    item["text"],
                                    extraction="tender_budget_or_cap",
                                )
                            ],
                        }
                    )
            if block["source_channel"] != "ragflow_mineru":
                continue
            scope = source_scope(doc, block["text"])
            for table in html_tables(block["raw"]):
                grid = table["rows"]
                header_end = configuration_header(grid)
                if header_end is None:
                    continue
                key = (str(block["chunk_ids"]), str(grid))
                if key in seen_tables:
                    continue
                seen_tables.add(key)
                headings = grid[: header_end + 1]
                joined = "".join("".join(row) for row in headings)
                kind = (
                    "quotation_items"
                    if any(w in joined for w in ["单价", "总价", "金额"])
                    else "quantity_name_table"
                )
                configs.append(
                    {
                        "document_id": doc["id"],
                        "document_role": doc["role"],
                        "bidder": doc.get("bidder", ""),
                        "source_scope": scope,
                        "table_kind": kind,
                        "source_context": block["text"][:1000],
                        "source_context_truncated": len(block["text"]) > 1000,
                        "header": grid[header_end],
                        "header_rows": headings,
                        "rows": grid[header_end + 1 :],
                        "layout_warnings": table.get("layout_warnings", []),
                        "review_note": "A quoted overall item is not a component configuration. Contract/performance attachment rows are not automatically current-project equipment. Compare actual scope, package and quantities.",
                        "evidence": [
                            evidence(
                                ctx,
                                doc,
                                block,
                                block["text"],
                                extraction="equipment_configuration_table",
                            )
                        ],
                    }
                )
                counts[doc["id"] + ":" + scope] += 1
            if re.search(r"配置清单|设备配置|配置如下|第一套|第二套", block["text"]):
                key = (str(block["chunk_ids"]), norm(block["text"]))
                if key not in seen_context:
                    seen_context.add(key)
                    contexts.append(
                        {
                            "document_id": doc["id"],
                            "document_role": doc["role"],
                            "source_scope": scope,
                            "text": block["text"],
                            "interpretation": "Original context only; may be a requirement, a copied requirement, quotation, commitment or past contract. Not proof of actual offered configuration.",
                            "evidence": [
                                evidence(
                                    ctx,
                                    doc,
                                    block,
                                    block["text"],
                                    extraction="equipment_configuration_context",
                                )
                            ],
                        }
                    )
    data["configuration_observations"] = configs
    data["budget_observations"] = budgets
    data["configuration_contexts"] = contexts
    data["metrics"].update(
        configuration_tables_collected=len(configs),
        budget_or_cap_observations=len(budgets),
        configuration_contexts_collected=len(contexts),
        configuration_scope_counts=dict(counts),
        configuration_coverage_note="Counts are observed sources, not complete component coverage or current-project configuration equality.",
    )
    return data
