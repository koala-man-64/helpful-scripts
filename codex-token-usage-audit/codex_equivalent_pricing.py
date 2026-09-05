"""Pure model-token estimates from a pinned Codex USD rate card.

This module consumes already-parsed usage. It does not establish observation
authenticity, a complete attempt census, feature charges, or account billing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal, localcontext
from typing import Any, Mapping

SOURCE_URL = "https://help.openai.com/en/articles/20001415"
RETRIEVED_AT = "2026-09-05T07:02:48Z"
RATE_CARD_ID = "codex-model-token-usd-2026-09-05"
COST_BASIS = "published_codex_equivalent_not_api_billing"
LABEL = "Published Codex-equivalent model-token estimate; not an account charge"
_CARD = {
    "id": RATE_CARD_ID,
    "source_url": SOURCE_URL,
    "source_title": "ChatGPT Rate Card (Enterprise token-based pricing)",
    "retrieved_at": RETRIEVED_AT,
    "product": "codex",
    "currency": "USD",
    "unit_tokens": 1000000,
    "applicability": "Published new-Enterprise USD rates used only as an equivalent estimate; account terms unverified",
    "rates": {
        "gpt-6-astra": {"input": "10", "cached_input": "1", "output": "50"},
        "gpt-5.6-sol": {"input": "4", "cached_input": "0.40", "output": "20"},
        "gpt-5.6-terra": {"input": "2", "cached_input": "0.20", "output": "12"},
        "gpt-5.6-luna": {"input": "0.20", "cached_input": "0.02", "output": "1.20"},
    },
    "long_context": {
        "request_input_tokens_exclusive_threshold": 272000,
        "input_multiplier": "2",
        "cached_input_multiplier": "2",
        "output_multiplier": "1.5",
        "exempt_models_in_codex": ["gpt-6-astra"],
    },
    "fast_multiplier": "2.5",
    "regional_multiplier": "1.1",
    "cache_write_charge": "0",
    "excluded_charges": [
        "web_search",
        "image_generation",
        "voice",
        "other_feature_fees",
    ],
    "sol_promotion_minimum_end_date": "2026-11-21",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


_CARD_BYTES = _canonical(_CARD)
RATE_CARD_DIGEST = "sha256:" + hashlib.sha256(_CARD_BYTES).hexdigest()
del _CARD


def rate_card() -> dict:
    """Return an independent copy of the complete, frozen pricing basis."""
    return json.loads(_CARD_BYTES)


def estimate(usage: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """Estimate one request, leaving uncertain scope/configuration unpriced.

    input_tokens includes cached_input_tokens; reasoning_output_tokens, when
    present, is a subset of output_tokens. Aggregates must be split by the owning
    parser into actual requests before using a request-dependent multiplier.
    """
    card = rate_card()
    reasons = []
    counts = {}
    for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
        value = usage.get(name)
        if type(value) is not int or value < 0:
            reasons.append(name)
        else:
            counts[name] = value
    if counts.get("cached_input_tokens", 0) > counts.get("input_tokens", 0):
        reasons.append("cached_input_not_subset")
    reasoning = usage.get("reasoning_output_tokens")
    if reasoning is not None and (
        type(reasoning) is not int
        or reasoning < 0
        or reasoning > counts.get("output_tokens", -1)
    ):
        reasons.append("reasoning_output_not_subset")
    model = context.get("observed_model")
    mode = context.get("observed_mode")
    regional = context.get("observed_regional")
    request_input = context.get("observed_request_input_tokens")
    if context.get("observed_product") != "codex":
        reasons.append("observed_product")
    if context.get("observed_scope") != "request":
        reasons.append("observed_scope")
    if not isinstance(model, str) or model not in card["rates"]:
        reasons.append("observed_model")
    if mode not in ("standard", "fast"):
        reasons.append("observed_mode")
    if type(regional) is not bool:
        reasons.append("observed_regional")
    if type(request_input) is not int or request_input < 0:
        reasons.append("observed_request_input_tokens")
    elif request_input != counts.get("input_tokens"):
        reasons.append("request_input_differs_from_usage")
    result = {
        "schema_version": "codex-equivalent-model-token-estimate-v1",
        "label": LABEL,
        "estimated_cost_usd": None,
        "exact_cost_usd": None,
        "cost_basis": None,
        "rate_card_id": RATE_CARD_ID,
        "rate_card_digest": RATE_CARD_DIGEST,
        "provenance": card,
        "derivation": None,
        "reasons": sorted(set(reasons)),
        "complete_accounting": False,
    }
    if reasons:
        return result
    rates = {key: Decimal(value) for key, value in card["rates"][model].items()}
    long_rule = card["long_context"]
    long_context = (
        request_input > long_rule["request_input_tokens_exclusive_threshold"]
        and model not in long_rule["exempt_models_in_codex"]
    )
    multiplier = Decimal(card["fast_multiplier"]) if mode == "fast" else Decimal(1)
    if regional:
        multiplier *= Decimal(card["regional_multiplier"])
    tokens = {
        "input": counts["input_tokens"] - counts["cached_input_tokens"],
        "cached_input": counts["cached_input_tokens"],
        "output": counts["output_tokens"],
    }
    # Integer counts may be large; retain exact decimal arithmetic before the
    # finite numeric projection required by the shared observation schema.
    with localcontext() as arithmetic:
        arithmetic.prec = max(
            40, max(len(str(value)) for value in tokens.values()) + 20
        )
        charges = {}
        for category, count in tokens.items():
            long_multiplier = (
                Decimal(long_rule[category + "_multiplier"])
                if long_context
                else Decimal(1)
            )
            charges[category] = (
                Decimal(count)
                * rates[category]
                * long_multiplier
                * multiplier
                / card["unit_tokens"]
            )
        total = sum(charges.values(), Decimal(0))
    projected = float(total)
    if not math.isfinite(projected):
        result["reasons"] = ["numeric_cost_out_of_range"]
        return result
    derivation = {
        "usage": copy.deepcopy(dict(usage)),
        "observed_context": copy.deepcopy(dict(context)),
        "charged_tokens": tokens,
        "reasoning_output_tokens_subset": reasoning,
        "cache_write_charge_usd": "0",
        "long_context_multiplier_applied": long_context,
        "mode_and_region_multiplier": str(multiplier),
        "category_charges_usd": {key: str(value) for key, value in charges.items()},
        "excluded_charges": card["excluded_charges"],
    }
    result.update(
        estimated_cost_usd=projected,
        exact_cost_usd=str(total),
        cost_basis=COST_BASIS,
        derivation=derivation,
        derivation_digest="sha256:"
        + hashlib.sha256(
            _canonical(
                {
                    "rate_card_digest": RATE_CARD_DIGEST,
                    "derivation": derivation,
                    "exact_cost_usd": str(total),
                }
            )
        ).hexdigest(),
    )
    return result
