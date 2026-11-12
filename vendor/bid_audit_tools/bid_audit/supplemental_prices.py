"""Supplementary price sources from original bids, not inferred unit prices."""

import re
from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from .core import norm
from .price_sources import source_scope
from .structured_text import blocks, evidence, html_tables


def amount_cents(cell, heading):
    value = norm(cell).replace("，", ",")
    scale = 10000 if "万元" in value + heading else 1
    value = re.sub(r"^(?:人民币|CNY|RMB|USD|HKD|EUR|[￥¥$])", "", value, flags=re.I)
    value = re.sub(r"(?:万元|美元|港元|欧元|元)$", "", value)
    if not re.fullmatch(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,6})?", value):
        raise ValueError("Incomplete or unsupported literal monetary value")
    cents = Decimal(value.replace(",", "")) * scale * 100
    if cents <= 0:
        return None
    if cents != cents.to_integral_value():
        raise ValueError("Literal value has sub-cent precision; not rounded")
    return int(cents)


def _currency(heading, fallback):
    for labels, code in [
        (("美元", "USD"), "USD"),
        (("港元", "HKD"), "HKD"),
        (("欧元", "EUR"), "EUR"),
        (("人民币", "RMB", "CNY"), "CNY"),
    ]:
        if any(label in heading.upper() for label in labels):
            return code
    return fallback if fallback else "unknown"


def _header(grid):
    for index, row in enumerate(grid[:5]):
        cells = [norm(c) for c in row]
        names = [
            i
            for i, c in enumerate(cells)
            if len(c) <= 24
            and re.search(r"设备名称|产品名称|货物名称|材料名称|品名|物料描述", c)
        ]
        if not names:
            names = [i for i, c in enumerate(cells) if c == "名称"]
        explicit = [i for i, c in enumerate(cells) if len(c) <= 24 and "单价" in c]
        prices = explicit or [
            i
            for i, c in enumerate(cells)
            if len(c) <= 24
            and re.search(r"报价|价格|价款|金额|总价", c)
            and not re.search(r"名称|数量|合计", c)
        ]
        if not names or not prices:
            continue
        if len(names) > 1 or len(prices) > 1:
            return {
                "ambiguous": True,
                "header_index": index,
                "reason": "Repeated side-by-side name/price headers; requirement/response ownership requires source review",
            }
        scope = next(
            (
                i
                for i, c in enumerate(cells)
                if re.search(r"配置|清单|分项说明|规格型号|型号规格", c)
            ),
            None,
        )
        unit = next((i for i, c in enumerate(cells) if c in {"单位", "计量单位"}), None)
        quantity = next(
            (i for i, c in enumerate(cells) if "数量" in c and len(c) <= 12), None
        )
        return {
            "ambiguous": False,
            "header_index": index,
            "name": names[0],
            "price": prices[0],
            "scope": scope,
            "unit": unit,
            "quantity": quantity,
            "heading": cells[prices[0]],
        }
    return None


def supplement_m15(ctx, data):
    primary_status = data["status"]
    currencies = {
        (r["bidder"], r["round"], r["lot"]): r["currency"]
        for r in data.get("records", [])
    }
    observations, ambiguities, tender = [], [], []
    duplicate_index = {}
    for doc in ctx.documents("bid", "tender"):
        for block in blocks(ctx, doc, raw=False):
            scope = source_scope(doc, block["text"])
            for table in html_tables(block["raw"]):
                grid = table["rows"]
                head = _header(grid)
                if head is None:
                    continue

                def source(note, row_index=None):
                    return evidence(
                        ctx,
                        doc,
                        block,
                        block["text"],
                        extraction="supplemental_price_table",
                        table_row_index=row_index,
                        source_note=note,
                    )

                if head["ambiguous"]:
                    ambiguities.append(
                        {
                            "document_id": doc["id"],
                            "reason": head["reason"],
                            "rows": grid,
                            "evidence": [source(head["reason"])],
                        }
                    )
                    continue
                for row_index, row in enumerate(
                    grid[head["header_index"] + 1 :], head["header_index"] + 1
                ):
                    if any(
                        re.fullmatch(r"(?:合计|总计|小计)(?:\(.*\))?", norm(c))
                        for c in row[:2]
                    ):
                        continue

                    def cell(key):
                        return (
                            row[head[key]]
                            if head[key] is not None and head[key] < len(row)
                            else ""
                        )

                    product = norm(cell("name"))
                    raw_price = cell("price")
                    if (
                        not product
                        or not norm(raw_price)
                        or norm(product) in {"名称", "设备名称", "产品名称"}
                    ):
                        continue
                    try:
                        cents = amount_cents(raw_price, head["heading"])
                    except ValueError as exc:
                        ambiguities.append(
                            {
                                "document_id": doc["id"],
                                "reason": str(exc),
                                "literal_price": raw_price,
                                "row": row,
                                "evidence": [source(str(exc), row_index)],
                            }
                        )
                        continue
                    if cents is None:
                        continue
                    unit = norm(cell("unit"))
                    configuration = norm(cell("scope"))
                    currency = _currency(
                        head["heading"] + str(raw_price),
                        currencies.get(
                            (doc.get("bidder", ""), doc["round"], doc["lot"])
                        ),
                    )
                    unit_cents = (
                        cents
                        if "单价" in head["heading"] and unit and currency != "unknown"
                        else None
                    )
                    quantity_literal = norm(cell("quantity"))
                    if (
                        unit_cents is None
                        and unit
                        and currency != "unknown"
                        and "总价" in head["heading"]
                        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", quantity_literal)
                    ):
                        quantity = Decimal(quantity_literal)
                        if (
                            quantity > 0
                            and Decimal(cents) / quantity
                            == (Decimal(cents) / quantity).to_integral_value()
                        ):
                            unit_cents = int(Decimal(cents) / quantity)
                    value = unit_cents if unit_cents is not None else cents
                    value_kind = (
                        "unit_price" if unit_cents is not None else "quote_amount"
                    )
                    scope_type = (
                        "configuration_lines"
                        if head["scope"] is not None
                        and re.search(
                            r"配置|清单",
                            norm(grid[head["header_index"]][head["scope"]]),
                        )
                        else "priced_items"
                    )
                    row_value = {
                        "document_id": doc["id"],
                        "document_role": doc["role"],
                        "bidder": doc.get("bidder", ""),
                        "round": doc["round"],
                        "lot": doc["lot"],
                        "product": product,
                        "configuration": configuration,
                        "unit": unit,
                        "currency": currency,
                        "source_scope": scope,
                        "scope_type": scope_type,
                        "price_kind": value_kind,
                        "literal_price": raw_price,
                        "quote_price_yuan": f"{Decimal(cents) / 100:.2f}",
                        "comparison_value_yuan": f"{Decimal(value) / 100:.2f}",
                        "unit_price_yuan": f"{Decimal(unit_cents) / 100:.2f}"
                        if unit_cents is not None
                        else None,
                        "unit_pricing_verified": unit_cents is not None,
                        "quantity_literal": quantity_literal or None,
                        "review_note": "Unit pricing verification is limited to the literal column/arithmetic. Independent items, product comparability, tax basis and current procurement scope still require source review. Never derive missing subprices from the overall total.",
                        "evidence": [
                            source("Actual priced row; no quantity inferred", row_index)
                        ],
                    }
                    key = (
                        doc["role"],
                        doc.get("bidder", ""),
                        doc["round"],
                        doc["lot"],
                        product,
                        configuration,
                        unit,
                        currency,
                        value_kind,
                        scope_type,
                        value,
                        scope,
                    )
                    if key in duplicate_index:
                        existing = duplicate_index[key]
                        for ev in row_value["evidence"]:
                            if ev not in existing["evidence"]:
                                existing["evidence"].append(ev)
                    else:
                        duplicate_index[key] = row_value
                        (tender if doc["role"] == "tender" else observations).append(
                            row_value
                        )
    conflicts = []
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in observations:
        if (
            row["source_scope"] == "contract_or_performance_attachment_unverified"
            or not row["unit"]
            or row["currency"] == "unknown"
        ):
            continue
        group = (
            row["round"],
            row["lot"],
            row["currency"],
            row["unit"],
            row["price_kind"],
            row["scope_type"],
        )
        groups[group][row["bidder"]][(row["product"], row["configuration"])].append(row)
    comparisons = []
    for group, bidders in groups.items():
        clean = {}
        for bidder, items in bidders.items():
            clean[bidder] = {}
            for key, values in items.items():
                distinct = {r["comparison_value_yuan"] for r in values}
                if len(distinct) > 1:
                    conflicts.append(
                        {
                            "bidder": bidder,
                            "key": list(key),
                            "values": sorted(distinct),
                            "evidence": [e for r in values for e in r["evidence"]],
                            "reason": "Conflicting copied source prices; no preferred copy selected",
                        }
                    )
                else:
                    clean[bidder][key] = values[0]
        for a, b in combinations(sorted(clean), 2):
            common = sorted(clean[a].keys() & clean[b].keys())
            if len(common) < 2:
                continue
            lines = []
            for key in common:
                x, y = clean[a][key], clean[b][key]
                lines.append(
                    {
                        "key": list(key),
                        "value_a_yuan": x["comparison_value_yuan"],
                        "value_b_yuan": y["comparison_value_yuan"],
                        "equal": x["comparison_value_yuan"]
                        == y["comparison_value_yuan"],
                        "evidence": x["evidence"] + y["evidence"],
                    }
                )
            matched = sum(r["equal"] for r in lines)
            comparisons.append(
                {
                    "kind": "supplemental_price_vector_for_review",
                    "bidder_a": a,
                    "bidder_b": b,
                    "round": group[0],
                    "lot": group[1],
                    "currency": group[2],
                    "unit": group[3],
                    "price_kind": group[4],
                    "scope_type": group[5],
                    "n": len(common),
                    "m": matched,
                    "equal_fraction": matched / len(common),
                    "all_prices_equal": matched == len(common),
                    "unit_pricing_verified": group[4] == "unit_price",
                    "comparison_is_risk_verdict": False,
                    "scope_note": "Provisional aligned literal prices only. Quote amounts are not automatically independently priced unit items; the agent must verify rule applicability before interpreting m/n. Within-bid equal prices alone are not cross-bid agreement.",
                    "lines": lines,
                    "evidence": [e for line in lines for e in line["evidence"]],
                }
            )
    data["supplemental_quote_observations"] = observations
    data["supplemental_quote_comparisons"] = comparisons
    data["supplemental_quote_ambiguities"] = ambiguities
    data["supplemental_quote_conflicts"] = conflicts
    data["supplemental_tender_price_observations"] = tender
    data["findings"].extend(comparisons)
    for pair in data.get("not_applicable_pairs", []):
        pair["scope"] = (
            "Validated dedicated primary quote files only; not a claim about all bid attachments"
        )
    data["metrics"].update(
        primary_quote_status=primary_status,
        supplemental_priced_observations=len(observations),
        supplemental_provisional_comparison_pairs=len(comparisons),
        supplemental_quote_ambiguities=len(ambiguities),
        supplemental_quote_conflicts=len(conflicts),
    )
    if observations or ambiguities:
        data["status"] = "partial"
    data["limitations"].append(
        "Primary not_applicable applies only to dedicated quote-file rows. Supplemental raw price vectors require verification of independent unit pricing, item scope, units, configuration, tax and shared fixed-price sources; total/quantity is never used to invent missing subquotes."
    )
    return data
