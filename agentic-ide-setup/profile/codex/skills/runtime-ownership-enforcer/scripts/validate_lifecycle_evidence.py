#!/usr/bin/env python3
"""Validate lifecycle ownership evidence for a cloud-native AI system."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_CONDITIONAL_DAYS = 180
PHASES = (
    "discovery",
    "architecture",
    "development",
    "data_ai",
    "build_supply_chain",
    "verification",
    "provisioning",
    "release_deployment",
    "operations",
    "incident_recovery",
    "retirement",
)
PLANES = {"management", "delivery", "ai_control", "runtime_data"}
STATUSES = {"pass", "conditional", "fail", "not_applicable"}
PRODUCTION_REQUIRED = {"operations", "incident_recovery", "retirement"}
OWNERSHIP_FIELDS = {
    "resource",
    "plane",
    "authoritative_owner",
    "provisioned_by",
    "runtime_identity",
    "allowed_runtime_operations",
    "environments",
    "retirement_mechanism",
}
MUTATING_OPERATION = re.compile(
    r"\b(?:create|alter|delete|drop|grant|revoke|assign|deploy|provision|register|promote|train|fine[-_ ]?tune)\b",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--environment",
        choices=("local", "development", "test", "staging", "production"),
        default="development",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", action="store_true")
    return parser.parse_args()


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(nonempty(item) for item in value)
    return value is not None


def parse_date(value: Any, field: str) -> dt.date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def add_finding(
    findings: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    phase: str | None = None,
    owner: str | None = None,
    evidence: list[str] | None = None,
    hard_fail: bool = True,
) -> None:
    findings.append(
        {
            "id": f"LCE-{len(findings) + 1:04d}",
            "code": code,
            "severity": "critical" if hard_fail else "warning",
            "hard_fail": hard_fail,
            "phase": phase,
            "owner": owner,
            "evidence": evidence or [],
            "message": message,
        }
    )


def validate_system(document: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    system = document.get("system")
    if not isinstance(system, dict):
        add_finding(findings, "invalid_system", "system must be an object")
        return False
    for field in ("name", "ai_enabled", "criticality"):
        if field not in system or not nonempty(system[field]):
            add_finding(findings, "missing_system_field", f"system.{field} is required")
    if "ai_enabled" in system and not isinstance(system["ai_enabled"], bool):
        add_finding(findings, "invalid_ai_enabled", "system.ai_enabled must be boolean")
        return False
    return bool(system.get("ai_enabled"))


def validate_ownership(
    document: dict[str, Any], environment: str, findings: list[dict[str, Any]]
) -> None:
    ownership_map = document.get("ownership_map")
    if not isinstance(ownership_map, list) or not ownership_map:
        add_finding(findings, "missing_ownership_map", "ownership_map must contain at least one resource")
        return
    seen: set[tuple[str, tuple[str, ...]]] = set()
    applicable = 0
    for index, entry in enumerate(ownership_map):
        if not isinstance(entry, dict):
            add_finding(findings, "invalid_ownership_entry", f"ownership_map[{index}] must be an object")
            continue
        missing = sorted(field for field in OWNERSHIP_FIELDS if not nonempty(entry.get(field)))
        if missing:
            add_finding(
                findings,
                "incomplete_ownership_entry",
                f"ownership_map[{index}] is missing {missing}",
                owner=entry.get("authoritative_owner"),
            )
            continue
        if entry["plane"] not in PLANES:
            add_finding(findings, "invalid_plane", f"ownership_map[{index}].plane is invalid")
        if not isinstance(entry["environments"], list) or not isinstance(entry["allowed_runtime_operations"], list):
            add_finding(findings, "invalid_ownership_lists", f"ownership_map[{index}] list fields are invalid")
            continue
        environments = tuple(sorted(str(value) for value in entry["environments"]))
        key = (str(entry["resource"]), environments)
        if key in seen:
            add_finding(findings, "duplicate_resource_owner", f"duplicate ownership for {entry['resource']} in {environments}")
        seen.add(key)
        if environment in entry["environments"]:
            applicable += 1
        for operation in entry["allowed_runtime_operations"]:
            if entry["plane"] != "runtime_data" and MUTATING_OPERATION.search(str(operation)):
                add_finding(
                    findings,
                    "cross_plane_runtime_mutation",
                    f"runtime operation '{operation}' mutates {entry['plane']} resource {entry['resource']}",
                    owner=entry["authoritative_owner"],
                )
    if environment == "production" and applicable == 0:
        add_finding(findings, "no_production_resources", "ownership_map has no production resources")


def validate_phase(
    phase: str,
    record: Any,
    environment: str,
    ai_enabled: bool,
    findings: list[dict[str, Any]],
) -> None:
    if not isinstance(record, dict):
        add_finding(findings, "missing_phase", f"phase {phase} is missing", phase=phase)
        return
    status = record.get("status")
    owner = record.get("owner")
    evidence = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    for field in ("status", "owner", "evidence", "rationale"):
        if not nonempty(record.get(field)):
            add_finding(
                findings,
                "incomplete_phase_evidence",
                f"phase {phase} requires {field}",
                phase=phase,
                owner=owner,
                evidence=evidence,
            )
    if status not in STATUSES:
        add_finding(findings, "invalid_phase_status", f"phase {phase} has invalid status {status!r}", phase=phase, owner=owner, evidence=evidence)
        return
    if status == "fail":
        add_finding(findings, "failed_phase", f"phase {phase} is failed", phase=phase, owner=owner, evidence=evidence)
    elif status == "conditional":
        missing = [field for field in ("tracking_work_item", "expires_on", "enforcement_date") if not nonempty(record.get(field))]
        if missing:
            add_finding(findings, "incomplete_conditional", f"phase {phase} conditional status lacks {missing}", phase=phase, owner=owner, evidence=evidence)
            return
        try:
            expiry = parse_date(record["expires_on"], f"phases.{phase}.expires_on")
            enforcement = parse_date(record["enforcement_date"], f"phases.{phase}.enforcement_date")
        except ValueError as exc:
            add_finding(findings, "invalid_conditional_date", str(exc), phase=phase, owner=owner, evidence=evidence)
            return
        today = dt.date.today()
        if expiry < today or expiry > today + dt.timedelta(days=MAX_CONDITIONAL_DAYS) or enforcement > expiry:
            add_finding(findings, "invalid_conditional_window", f"phase {phase} conditional window is expired, exceeds {MAX_CONDITIONAL_DAYS} days, or has enforcement after expiry", phase=phase, owner=owner, evidence=evidence)
        elif environment == "production":
            add_finding(findings, "production_conditional", f"production phase {phase} must pass before release", phase=phase, owner=owner, evidence=evidence)
        else:
            add_finding(findings, "nonproduction_conditional", f"phase {phase} is temporarily conditional", phase=phase, owner=owner, evidence=evidence, hard_fail=False)
    elif status == "not_applicable":
        if not nonempty(record.get("applicability_rationale")):
            add_finding(findings, "missing_applicability_rationale", f"phase {phase} lacks applicability_rationale", phase=phase, owner=owner, evidence=evidence)
        if environment == "production" and (phase in PRODUCTION_REQUIRED or (phase == "data_ai" and ai_enabled)):
            add_finding(findings, "invalid_production_exclusion", f"phase {phase} cannot be excluded for this production system", phase=phase, owner=owner, evidence=evidence)


def validate(document: dict[str, Any], environment: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if document.get("schema_version") != 1:
        add_finding(findings, "unsupported_schema", "schema_version must be 1")
    ai_enabled = validate_system(document, findings)
    validate_ownership(document, environment, findings)
    phases = document.get("phases") if isinstance(document.get("phases"), dict) else {}
    for phase in PHASES:
        validate_phase(phase, phases.get(phase), environment, ai_enabled, findings)
    unknown = sorted(set(phases) - set(PHASES))
    if unknown:
        add_finding(findings, "unknown_phases", f"unknown lifecycle phases: {unknown}")
    return findings


def render_text(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        f"Lifecycle evidence: {summary['hard_failures']} hard failures, {summary['warnings']} warnings",
        f"Environment: {result['environment']}",
    ]
    for finding in result["findings"]:
        location = f" phase={finding['phase']}" if finding["phase"] else ""
        lines.append(f"{finding['id']} {finding['severity']} {finding['code']}{location}: {finding['message']}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("manifest root must be an object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"lifecycle evidence error: {exc}", file=sys.stderr)
        return 2
    findings = validate(document, args.environment)
    hard_failures = sum(1 for finding in findings if finding["hard_fail"])
    result = {
        "schema_version": 1,
        "environment": args.environment,
        "mode": "audit" if args.audit else "enforce",
        "summary": {"hard_failures": hard_failures, "warnings": len(findings) - hard_failures},
        "findings": findings,
    }
    payload = json.dumps(result, indent=2) if args.format == "json" else render_text(result)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if args.audit or hard_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
