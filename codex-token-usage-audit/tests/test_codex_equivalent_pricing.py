import sys
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_equivalent_pricing as pricing


class PricingTests(unittest.TestCase):
    def context(self, **changes):
        value = {
            "observed_product": "codex",
            "observed_scope": "request",
            "observed_model": "gpt-5.6-sol",
            "observed_mode": "standard",
            "observed_regional": False,
        }
        value.update(changes)
        return value

    def usage(self, **changes):
        value = {
            "input_tokens": 272000,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        value.update(changes)
        return value

    def test_exact_rates_and_subsets(self):
        for model, rates in pricing.rate_card()["rates"].items():
            usage = self.usage(
                input_tokens=100000,
                output_tokens=100000,
                reasoning_output_tokens=100000,
            )
            result = pricing.estimate(
                usage,
                self.context(
                    observed_model=model, observed_request_input_tokens=100000
                ),
            )
            self.assertEqual(
                (Decimal(rates["input"]) + Decimal(rates["output"])) / 10,
                Decimal(result["exact_cost_usd"]),
            )
            self.assertFalse(result["complete_accounting"])
            self.assertEqual(pricing.COST_BASIS, result["cost_basis"])

    def test_threshold_astra_and_multipliers(self):
        self.assertFalse(
            pricing.estimate(
                self.usage(), self.context(observed_request_input_tokens=272000)
            )["derivation"]["long_context_multiplier_applied"]
        )
        sol = pricing.estimate(
            self.usage(input_tokens=272001),
            self.context(
                observed_request_input_tokens=272001,
                observed_mode="fast",
                observed_regional=True,
            ),
        )
        self.assertTrue(sol["derivation"]["long_context_multiplier_applied"])
        self.assertEqual("2.75", sol["derivation"]["mode_and_region_multiplier"])
        astra = pricing.estimate(
            self.usage(input_tokens=272001),
            self.context(
                observed_model="gpt-6-astra", observed_request_input_tokens=272001
            ),
        )
        self.assertFalse(astra["derivation"]["long_context_multiplier_applied"])

    def test_unknowns_and_frozen_provenance(self):
        result = pricing.estimate(
            self.usage(),
            self.context(
                observed_scope="aggregate", observed_request_input_tokens=272000
            ),
        )
        self.assertIsNone(result["estimated_cost_usd"])
        self.assertIsNone(
            pricing.estimate(
                self.usage(input_tokens=True),
                self.context(observed_request_input_tokens=272000),
            )["estimated_cost_usd"]
        )
        self.assertIsNone(
            pricing.estimate(
                self.usage(), self.context(observed_request_input_tokens=999)
            )["estimated_cost_usd"]
        )
        card = pricing.rate_card()
        card["rates"]["gpt-5.6-sol"]["input"] = "0"
        self.assertNotEqual("0", pricing.rate_card()["rates"]["gpt-5.6-sol"]["input"])
        self.assertIn("long_context", pricing.rate_card())
        self.assertIn("excluded_charges", pricing.rate_card())

    def test_cached_and_reasoning_subsets_and_unobserved_configuration(self):
        usage = self.usage(
            input_tokens=100000,
            cached_input_tokens=50000,
            output_tokens=10000,
            reasoning_output_tokens=10000,
        )
        context = self.context(observed_request_input_tokens=100000)
        result = pricing.estimate(usage, context)
        self.assertEqual(Decimal(result["exact_cost_usd"]), Decimal("0.42"))
        for invalid in (
            {"cached_input_tokens": 100001},
            {"reasoning_output_tokens": 10001},
            {"output_tokens": None},
        ):
            self.assertIsNone(
                pricing.estimate({**usage, **invalid}, context)["estimated_cost_usd"]
            )
        for field in (
            "observed_model",
            "observed_mode",
            "observed_regional",
            "observed_request_input_tokens",
        ):
            self.assertIsNone(
                pricing.estimate(usage, {**context, field: None})["estimated_cost_usd"]
            )

    def test_rate_card_digest_covers_all_rules_and_result_copies(self):
        card = pricing.rate_card()
        raw = json.dumps(
            card, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        self.assertEqual(
            pricing.RATE_CARD_DIGEST, "sha256:" + hashlib.sha256(raw).hexdigest()
        )
        for field in (
            "long_context",
            "fast_multiplier",
            "regional_multiplier",
            "applicability",
        ):
            changed = {**card, field: None}
            raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            self.assertNotEqual(
                pricing.RATE_CARD_DIGEST, "sha256:" + hashlib.sha256(raw).hexdigest()
            )
        result = pricing.estimate(
            self.usage(), self.context(observed_request_input_tokens=272000)
        )
        result["provenance"]["long_context"]["input_multiplier"] = "0"
        self.assertEqual(pricing.rate_card()["long_context"]["input_multiplier"], "2")
