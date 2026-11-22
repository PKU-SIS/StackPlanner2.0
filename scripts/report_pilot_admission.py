"""Frozen public review for the <=50 CNY pilot; never authorizes execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from report_pilot_audit import RateCard, integer, money

REVIEW_PATH = (
    "docs/research-report/multiturn-pilot-preparation-20261122/ADMISSION_REVIEW.json"
)


def reviewed_limits(root, budget_cny, public_profile, roles):
    path = Path(root) / REVIEW_PATH
    raw = path.read_bytes()
    review = json.loads(raw)
    rates = RateCard()
    if (
        budget_cny is None
        or money(budget_cny) != money("50")
        or money(review["approved_total_ceiling_cny"]) != money(budget_cny)
    ):
        raise ValueError("frozen review requires the user's 50 CNY ceiling")
    if (
        review["rates_cny"] != rates.__dict__
        or review["documented_context_length"] != "1M"
        or review["input_reservation_ceiling_tokens"] != 1_048_576
        or review["request_model"] != public_profile["model"]["model"]
        or review["request_model"] != "deepseek-flash"
        or review["account_billing_currency_confirmed"] is not False
        or review["api_experiments_authorized"] is not False
    ):
        raise ValueError("public provider review requires renewed verification")
    for role, row in roles.items():
        expected_max = 32768 if role == "outline" else 8192
        if (
            row["model_alias"] != "deepseek-flash"
            or row["max_tokens"] != expected_max
            or row["sdk_max_retries"] != 2
        ):
            raise ValueError("native role request shape differs from review")
    inp, out, direct = (
        integer(review[name])
        for name in (
            "max_input_tokens",
            "max_output_tokens",
            "max_direct_tool_attempts",
        )
    )
    operating = money(review["operating_ceiling_cny"])
    margin = money(review["safety_margin_cny"])
    combined = rates.model_cost(inp, out) + direct * money(
        rates.direct_tool_per_success
    )
    if combined > operating or operating + margin > money(budget_cny):
        raise ValueError("combined exposure exceeds reviewed ceiling")
    parallel = integer(review["stress_parallel_model_calls"])
    reserve = parallel * rates.model_cost(
        review["input_reservation_ceiling_tokens"],
        max(row["max_tokens"] for row in roles.values()),
    )
    if (
        reserve > operating
        or parallel * review["input_reservation_ceiling_tokens"] > inp
        or parallel * 32768 > out
    ):
        raise ValueError("parallel reservation stress case exceeds limits")
    return {
        "budget_status": "confirmed_ceiling_not_execution_authorization",
        "operating_max_cost_cny": str(operating),
        "safety_margin_cny": str(margin),
        "proposed_max_cost_cny": str(money(budget_cny)),
        "max_input_tokens": inp,
        "max_output_tokens": out,
        "max_direct_tool_attempts": direct,
        "provider_input_context_ceiling_tokens": review[
            "input_reservation_ceiling_tokens"
        ],
        "provider_input_context_ceiling_verified": True,
    }, {
        "review_id": review["review_id"],
        "source_file": REVIEW_PATH,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "context_bound_basis": review["context_interpretation"],
        "combined_envelope_cny": str(combined),
        "stress_parallel_model_calls": parallel,
        "stress_parallel_reservation_cny": str(reserve),
        "billing_currency_status": "unverified; do not load account credentials during preparation",
        "execution_authorized": False,
    }
