# Findings and lifecycle evidence formats

## Source-scan JSON

The source scanner emits:

```json
{
  "schema_version": 2,
  "root": "absolute path",
  "mode": "enforce",
  "summary": {"hard_failures": 0, "warnings": 0, "excluded": 0},
  "findings": []
}
```

Each finding contains:

- Identity: `id`, `severity`, `hard_fail`, `classification`.
- Lifecycle: `lifecycle_phase`, `plane`, `risk_domains`.
- Location: `file`, `line`, `symbol`, `code_path`, `evidence`.
- Ownership: `resource_hint`, `current_owner`, `authoritative_owner_layer`.
- Decision: `pattern`, `privileged_capability`, `reason`, `remediation`, `post_fix_runtime_behavior`.
- Governance: `validation_required`, `rollback_required`, `exception`.

Treat scanner-supplied owners and remediation as routing defaults. Confirm exact symbols, callers, entrypoints, identities, resources, and owners from repository and live-environment evidence.

## Human finding

```text
[severity] [hard fail: yes/no] <id> - <classification>
Phase and plane: <phase>; <management|delivery|ai_control|runtime_data>
Location and path: <file:line, symbol, caller/entrypoint>
Resource or AI asset: <resource/version/environment>
Current owner and identity: <observed owner>; <runtime principal>
Boundary: <why the capability belongs elsewhere>
Misplaced capability: <DDL, grant, resource create, model deploy, index create, etc.>
Authoritative fix: <named layer/owner and exact action>
Runtime after fix: <read-only readiness or bounded data-plane behavior>
Validation and rollback: <tests/evaluations/read-back>; <rollback target>
Exception: <none or validated record>
```

## Lifecycle evidence JSON

The lifecycle validator accepts:

```json
{
  "schema_version": 1,
  "system": {
    "name": "example-ai-service",
    "ai_enabled": true,
    "criticality": "high"
  },
  "ownership_map": [
    {
      "resource": "model-endpoint",
      "plane": "ai_control",
      "authoritative_owner": "ml-platform",
      "provisioned_by": "iac/model-endpoint.bicep",
      "runtime_identity": "example-runtime",
      "allowed_runtime_operations": ["invoke"],
      "environments": ["production"],
      "retirement_mechanism": "iac destroy after consumer drain"
    }
  ],
  "phases": {
    "discovery": {
      "status": "pass",
      "owner": "engineering-lead",
      "evidence": ["AB#1234", "docs/slo.md"],
      "rationale": "Scope and ownership approved."
    }
  }
}
```

Include all phase IDs listed in `lifecycle-gates.md`. For every phase require `status`, `owner`, non-empty `evidence`, and `rationale`. Valid statuses are `pass`, `conditional`, `fail`, and `not_applicable`.

For `conditional`, also require `tracking_work_item`, `expires_on`, and `enforcement_date`. Production rejects `conditional`. For `not_applicable`, require `applicability_rationale`; production rejects `not_applicable` for `operations`, `incident_recovery`, or `retirement`, and rejects it for `data_ai` when `system.ai_enabled` is true.

Each ownership-map entry requires `resource`, `plane`, `authoritative_owner`, `provisioned_by`, `runtime_identity`, `allowed_runtime_operations`, `environments`, and `retirement_mechanism`. Valid planes are `management`, `delivery`, `ai_control`, and `runtime_data`.

## Exception allowlist

Pass `--allowlist path/to/runtime-ownership-exceptions.json`. Use exact relative paths; never use globs. The scanner rejects missing fields, expiry beyond 180 days, non-boolean `non_production_only`, production-capable exceptions, and paths outside selected runtime directories.

```json
{
  "exceptions": [
    {
      "id": "local-bootstrap",
      "path": "local/bootstrap_dev.py",
      "owner": "platform-team",
      "reason": "Disposable local integration environment",
      "expires_on": "2026-12-31",
      "tracking_work_item": "AB#1234",
      "test_reference": "tests/test_bootstrap.py::test_local_only",
      "compensating_controls": "Packaging test proves the path is absent from the production image.",
      "non_production_only": true,
      "production_disablement_plan": "Entrypoint rejects production environment.",
      "removal_plan": "Replace with the shared development environment provisioner."
    }
  ]
}
```
