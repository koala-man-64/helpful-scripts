"""Retain separate pricing derivations without altering original usage rows."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from .semantic_evidence import file_digest
from .validators import load_hook_usage


def _calculator():
    path = (
        Path(__file__).resolve().parents[2]
        / "codex-token-usage-audit/codex_equivalent_pricing.py"
    )
    # Loading explicit owned source avoids depending on package installation.
    spec = importlib.util.spec_from_file_location("benchmark_codex_pricing", path)
    if spec is None or spec.loader is None:
        raise ValueError("Codex pricing calculator is unavailable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module, path


def derive_request_prices(
    usage_path: Path,
    contexts_path: Path,
    *,
    hook_source: Path,
    expected_hook_usage_digest: str,
) -> dict:
    """Price parser-validated requests against supplied, retained context evidence.

    This is a diagnostic artifact. The central verifier must establish the
    context observations and attempt census before accepting any task cost.
    """
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hook_usage_digest):
        raise ValueError("an exact hook usage source digest is required")
    usage_digest, contexts_digest = file_digest(usage_path), file_digest(contexts_path)
    usage_api = load_hook_usage(hook_source, expected_digest=expected_hook_usage_digest)
    rows = usage_api.validate_export(json.loads(usage_path.read_bytes()))
    # Preserve the authoritative parser's duplicate/conflicting-event checks.
    usage_api.summarize_observations(rows)
    contexts = json.loads(contexts_path.read_bytes())
    if (
        not isinstance(contexts, dict)
        or set(contexts) != {"schema_version", "contexts"}
        or contexts["schema_version"] != "codex-request-pricing-contexts-v1"
    ):
        raise ValueError(
            "pricing contexts must use the explicit versioned context contract"
        )
    by_event = {}
    for record in contexts["contexts"]:
        if (
            not isinstance(record, dict)
            or set(record) != {"source_id", "event_id", "context"}
            or not isinstance(record["context"], dict)
        ):
            raise ValueError("invalid request pricing context record")
        key = record["source_id"], record["event_id"]
        if key in by_event:
            raise ValueError("duplicate request pricing context")
        by_event[key] = record["context"]
    calculator, source = _calculator()
    estimates = []
    priced_events = set()
    for row in rows:
        if row["kind"] != "request":
            continue
        key = row["source_id"], row["event_id"]
        # The owning parser has already rejected conflicting replays. Preserve
        # one derivation per retained ledger event for identical copies.
        if key in priced_events:
            continue
        priced_events.add(key)
        context = dict(by_event.get(key, {}))
        # Caller metadata cannot relabel the model already observed by the parser.
        if context.get("observed_model") != row["model"]:
            context["observed_model"] = None
        counts = {
            name: row[name]
            for name in ("input_tokens", "cached_input_tokens", "output_tokens")
        }
        counts["reasoning_output_tokens"] = row["reasoning_tokens"]
        result = calculator.estimate(counts, context)
        estimates.append(
            {
                "source_id": row["source_id"],
                "event_id": row["event_id"],
                "kind": "request",
                "task_id": row["task_id"],
                "estimate": result,
            }
        )
    if usage_digest != file_digest(usage_path) or contexts_digest != file_digest(
        contexts_path
    ):
        raise ValueError("pricing inputs changed during derivation")
    return {
        "schema_version": "codex-request-pricing-derivation-v1",
        "usage_digest": usage_digest,
        "contexts_digest": contexts_digest,
        "calculator_digest": file_digest(source),
        "hook_usage_digest": expected_hook_usage_digest,
        "rate_card_digest": calculator.RATE_CARD_DIGEST,
        "estimates": estimates,
        "context_verification": "unverified",
        "complete_accounting": False,
        "estimated_task_cost_usd": None,
        "promotion_eligible": False,
    }
