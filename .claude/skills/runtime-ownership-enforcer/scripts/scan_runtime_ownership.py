#!/usr/bin/env python3
"""Fail CI when a selected runtime path contains provisioning-like mutations."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

MAX_EXCEPTION_DAYS = 180
TEXT_EXTENSIONS = {
    ".py", ".sql", ".cs", ".csx", ".ts", ".tsx", ".js", ".jsx", ".ps1",
    ".psm1", ".sh", ".bash", ".yml", ".yaml", ".json", ".dockerfile", "",
}
EXCLUDED_SEGMENTS = {"test", "tests", "fixture", "fixtures", "migrations", "migration", ".git", "node_modules", ".venv", "venv", "__pycache__"}
PATTERNS = (
    ("database_ddl", r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\s+(?:TABLE|SCHEMA|INDEX|DATABASE|EXTENSION|TYPE|ROLE|USER|SEQUENCE|VIEW)\b", "database DDL"),
    ("database_privilege", r"\b(?:GRANT|REVOKE)\b", "database privilege change"),
    ("migration_execution", r"\b(?:alembic\s+upgrade|dotnet\s+ef\s+database\s+update|flyway\s+(?:migrate|repair)|liquibase\s+update)\b", "migration execution"),
    ("runtime_bootstrap", r"\b(?:ensure(?:_created|_schema|_table)?|auto[-_ ]?create|self[-_ ]?heal|reconcile|repair|bootstrap|initialize|seed)\b", "runtime bootstrap or repair"),
    ("cloud_provisioning", r"\b(?:az\s+(?:resource|role\s+assignment|storage\s+container|servicebus|keyvault)|New-Az\w+|Create(?:IfNotExists|IfNotExistsAsync|Queue|Topic|Container|RoleAssignment))\b", "cloud resource or RBAC provisioning"),
    ("resource_creation", r"\b(?:create[_-](?:queue|topic|container|bucket|subscription|resource|role(?:_assignment)?|schema|table)|provision[_-])\w*\b", "resource creation"),
    ("infrastructure_apply", r"\b(?:terraform|tofu|pulumi|bicep)\s+(?:apply|up|deployment|deploy)\b", "infrastructure deployment execution"),
    ("kubernetes_mutation", r"\b(?:kubectl\s+(?:apply|create|delete|patch|replace|scale|set)|helm\s+(?:install|upgrade|uninstall))\b", "Kubernetes management-plane mutation"),
    ("ai_resource_provisioning", r"\b(?:create[_-](?:index|collection|vector_store|model_endpoint|deployment)|online_deployments\.begin_create_or_update|begin_create_or_update\s*\([^)]*(?:endpoint|deployment))\b", "AI resource provisioning"),
    ("mutable_ai_asset", r"\b(?:model|prompt|deployment|index)[_-]?(?:version|alias)?\s*[=:]\s*['\"]latest['\"]", "mutable AI asset selection"),
    ("ai_safety_bypass", r"\b(?:disable|skip|bypass)[_-]?(?:content_filter|guardrail|safety|evaluation|eval)\b", "AI safety or evaluation bypass"),
    ("masked_environment_error", r"\b(?:except\s+(?:Exception|[\w.]+Error)|catch\s*\([^)]*\)|catch\s*\{).{0,180}\b(?:permission|schema|resource|not found|does not exist|denied)\b", "error handling may mask environment defect"),
)

PATTERN_CONTEXT = {
    "database_ddl": ("provisioning", "management", ["data_integrity", "least_privilege"]),
    "database_privilege": ("provisioning", "management", ["security", "least_privilege"]),
    "migration_execution": ("release_deployment", "delivery", ["data_integrity", "release"]),
    "runtime_bootstrap": ("operations", "management", ["reliability", "ownership_drift"]),
    "cloud_provisioning": ("provisioning", "management", ["security", "cost", "ownership_drift"]),
    "resource_creation": ("provisioning", "management", ["reliability", "ownership_drift"]),
    "infrastructure_apply": ("release_deployment", "delivery", ["security", "release", "ownership_drift"]),
    "kubernetes_mutation": ("operations", "management", ["security", "reliability", "release"]),
    "ai_resource_provisioning": ("data_ai", "ai_control", ["ai_governance", "cost", "ownership_drift"]),
    "mutable_ai_asset": ("data_ai", "ai_control", ["ai_governance", "reproducibility", "safety"]),
    "ai_safety_bypass": ("verification", "ai_control", ["ai_safety", "security", "compliance"]),
    "masked_environment_error": ("operations", "management", ["reliability", "observability"]),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--runtime-dir", action="append", default=[], help="Relative runtime directory; repeatable")
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--audit", action="store_true", help="Report findings without a failing exit code")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_allowlist(path: Path | None, root: Path, runtime_roots: list[Path]) -> dict[str, dict]:
    if path is None:
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("exceptions", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid allowlist: {exc}") from exc
    required = {
        "id", "path", "owner", "reason", "expires_on", "tracking_work_item",
        "test_reference", "compensating_controls", "non_production_only",
        "production_disablement_plan", "removal_plan",
    }
    allowed: dict[str, dict] = {}
    for entry in entries:
        missing = required - entry.keys()
        if missing or entry.get("non_production_only") is not True:
            raise ValueError(f"invalid exception {entry.get('id', '<unknown>')}: missing {sorted(missing)} or not non-production")
        try:
            expiry = dt.date.fromisoformat(entry["expires_on"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid exception {entry['id']}: expires_on must be YYYY-MM-DD") from exc
        target = (root / entry["path"]).resolve()
        today = dt.date.today()
        if expiry < today or expiry > today + dt.timedelta(days=MAX_EXCEPTION_DAYS):
            raise ValueError(f"invalid exception {entry['id']}: expiry must be within {MAX_EXCEPTION_DAYS} days")
        if not any(target.is_relative_to(runtime_root) for runtime_root in runtime_roots):
            raise ValueError(f"invalid exception {entry['id']}: path is outside selected runtime paths")
        allowed[target.relative_to(root).as_posix()] = entry
    return allowed


def candidate_files(runtime_roots: list[Path]) -> tuple[list[Path], int]:
    files: set[Path] = set()
    excluded = 0
    for runtime_root in runtime_roots:
        if not runtime_root.is_dir():
            raise ValueError(f"runtime directory does not exist: {runtime_root}")
        for path in runtime_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.lower() in EXCLUDED_SEGMENTS for part in path.relative_to(runtime_root).parts):
                excluded += 1
            elif path.suffix.lower() in TEXT_EXTENSIONS or path.name.lower() == "dockerfile":
                files.add(path)
    return sorted(files), excluded


def scan(root: Path, runtime_roots: list[Path], allowed: dict[str, dict]) -> tuple[list[dict], int]:
    findings: list[dict] = []
    files, excluded = candidate_files(runtime_roots)
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if re.search(r"\bCREATE\s+(?:TEMP|TEMPORARY)\b", line, re.IGNORECASE):
                continue
            for kind, expression, capability in PATTERNS:
                candidate = "\n".join(lines[line_no - 1:line_no + 3]) if kind == "masked_environment_error" else line
                if not re.search(expression, candidate, re.IGNORECASE | re.DOTALL):
                    continue
                exception = allowed.get(relative)
                finding_id = f"ROE-{len(findings) + 1:04d}"
                hard_fail = exception is None
                lifecycle_phase, plane, risk_domains = PATTERN_CONTEXT[kind]
                findings.append({
                    "id": finding_id,
                    "severity": "critical" if hard_fail else "warning",
                    "hard_fail": hard_fail,
                    "classification": "prohibited_runtime_infrastructure_mutation" if hard_fail else "explicit_temporary_exception",
                    "lifecycle_phase": lifecycle_phase,
                    "plane": plane,
                    "risk_domains": risk_domains,
                    "file": relative,
                    "line": line_no,
                    "symbol": None,
                    "code_path": "Inspect callers and runtime entrypoint.",
                    "pattern": kind,
                    "resource_hint": "Determine from the matched operation and surrounding code.",
                    "current_owner": None,
                    "privileged_capability": capability,
                    "reason": "Provisioning-like behavior appears in a selected runtime path.",
                    "authoritative_owner_layer": "migration, IaC, deployment pipeline, platform configuration, or RBAC owner",
                    "remediation": "Move the mutation to its authoritative owner and replace this path with a read-only readiness/preflight check.",
                    "post_fix_runtime_behavior": "Validate required state read-only and fail with the owner and remediation when it is absent or incompatible.",
                    "validation_required": "Prove authoritative provisioning, least-privilege runtime success, clear incomplete-state failure, and absence of runtime repair.",
                    "rollback_required": "Name the authoritative rollback or forward-fix path for the affected resource or AI asset.",
                    "evidence": line.strip()[:500],
                    "exception": exception,
                })
    return findings, excluded


def render_text(document: dict) -> str:
    lines = [f"Runtime ownership scan: {document['summary']['hard_failures']} hard failures"]
    for finding in document["findings"]:
        lines.append(f"{finding['id']} {finding['severity']} {finding['file']}:{finding['line']} {finding['pattern']}")
        lines.append(f"  {finding['evidence']}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        root = args.root.resolve()
        runtime_roots = [(root / entry).resolve() for entry in args.runtime_dir] or [root]
        if any(not candidate.is_relative_to(root) for candidate in runtime_roots):
            raise ValueError("runtime directories must be below root")
        allowed = load_allowlist(args.allowlist, root, runtime_roots)
        findings, excluded = scan(root, runtime_roots, allowed)
    except ValueError as exc:
        print(f"runtime-ownership scanner error: {exc}", file=sys.stderr)
        return 2
    hard_failures = sum(1 for item in findings if item["hard_fail"])
    document = {"schema_version": 2, "root": str(root), "mode": "audit" if args.audit else "enforce", "summary": {"hard_failures": hard_failures, "warnings": len(findings) - hard_failures, "excluded": excluded}, "findings": findings}
    payload = json.dumps(document, indent=2) if args.format == "json" else render_text(document)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if args.audit or hard_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
