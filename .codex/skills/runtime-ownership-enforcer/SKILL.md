---
name: runtime-ownership-enforcer
description: Enforce cloud-native AI ownership boundaries across planning, architecture, development, data and model engineering, build, testing, provisioning, CI/CD, release, runtime operations, incident recovery, and retirement. Use when designing, auditing, implementing, or releasing services, jobs, agents, RAG systems, model endpoints, data pipelines, containers, Kubernetes workloads, IaC, migrations, identities, or platform integrations; especially when detecting runtime self-healing, management-plane mutation, ungoverned AI changes, least-privilege violations, missing lifecycle evidence, or environment drift.
---

# Runtime Ownership Enforcer

Enforce one invariant throughout the lifecycle: each state-changing capability must execute only in its authoritative plane, through its named owner and controlled mechanism. Let workloads perform documented data-plane work; never let request-serving or scheduled runtime code conceal a broken environment by mutating provisioned state.

## Establish Scope and Ownership

1. Read repository, architecture, security, data, model, deployment, operations, and retirement guidance.
2. Identify environments, workloads, entrypoints, CI/CD paths, IaC, migrations, identities, external dependencies, datasets, prompts, models, vector stores, feature stores, and model endpoints.
3. Map every resource to one plane:

   - `management`: infrastructure, networking, identity, policy, platform configuration, and resource lifecycle.
   - `delivery`: builds, migrations, promotion, deployment, rollback, and release approvals.
   - `ai_control`: dataset, prompt, model, evaluation, registry, endpoint, index, and safety-policy versioning.
   - `runtime_data`: inference, retrieval, business DML, message processing, and owned application outputs.

4. Record the authoritative owner, provisioner, runtime identity, allowed operations, environments, version source, and retirement mechanism. Treat ambiguous ownership as a blocker.
5. Read [references/lifecycle-gates.md](references/lifecycle-gates.md) for the complete phase-by-phase gate. Read [references/cloud-native-ai-ownership.md](references/cloud-native-ai-ownership.md) whenever the system uses models, prompts, agents, training, RAG, embeddings, vector search, or autonomous tool use.

## Run Both Enforcement Gates

Scan actual runtime directories for provisioning-like behavior:

```powershell
python <skill-dir>/scripts/scan_runtime_ownership.py <repo-root> `
  --runtime-dir src --runtime-dir jobs --format json `
  --output runtime-ownership-findings.json
```

Validate lifecycle evidence for the target release or environment:

```powershell
python <skill-dir>/scripts/validate_lifecycle_evidence.py `
  lifecycle-evidence.json --environment production --format text
```

Use enforcement mode by default. Source findings exit `1` for non-allowlisted hard failures; lifecycle validation exits `1` for failed, missing, conditional, or unsupported production gates. Both exit `2` for invalid input. Use audit mode only when explicitly requested and attach an owner, expiry, enforcement date, and tracking item.

Run the bundled regression suite after modifying either gate:

```powershell
python <skill-dir>/scripts/test_runtime_ownership_enforcer.py
```

## Classify Every Finding

Classify reviewed source findings exactly once:

- `valid_runtime_data_operation`: documented data-plane behavior within workload ownership.
- `valid_deployment_provisioning_operation`: authoritative migration, IaC, delivery, AI-control, or platform operation outside the serving runtime.
- `prohibited_runtime_infrastructure_mutation`: runtime mutation or recovery across another plane; hard fail.
- `explicit_temporary_exception`: complete, unexpired, non-production exception only.

Treat text matches as leads. Inspect symbols, callers, entrypoints, deployment packaging, identity permissions, and environment guards. Detect duplicate ownership, such as both IaC and a runtime helper creating the same queue, table, index, model deployment, or role assignment.

## Enforce Lifecycle Decisions

Apply these non-negotiable rules across all phases:

- Plan: name business, technical, security, data, AI, platform, SRE, and retirement owners; define risk, SLO, cost, compliance, tenancy, and failure boundaries.
- Architect: separate management, delivery, AI-control, and runtime-data planes; document dependency, identity, trust, data-flow, model-flow, rollback, and regional failure decisions.
- Develop: use explicit configuration, bounded retries, least privilege, reproducible local bootstrap, and production-off test helpers. Fail on missing provisioned state.
- Govern data and AI: version and trace datasets, prompts, models, tools, indices, evaluations, safety policy, and promotion decisions. Never select mutable `latest` aliases or silently retrain, fine-tune, reindex, reprompt, or swap models in serving recovery logic.
- Build: pin dependencies and base images; scan and attest artifacts; produce an SBOM; keep build credentials out of runtime artifacts; run containers as a non-root identity unless a reviewed exception exists.
- Validate: test positive, negative, permission-denied, missing-resource, incompatible-version, retry, rollback, resilience, security, privacy, model-quality, safety, bias, retrieval, hallucination, tool-abuse, and observability paths.
- Provision: create cloud resources, schemas, grants, identities, policies, model endpoints, registries, vector collections, and network controls only through versioned IaC, migrations, or governed platform workflows.
- Release: promote immutable artifacts and pinned AI assets through policy gates; verify compatibility, canary behavior, rollback, database expand/contract sequencing, and evidence retention.
- Operate: expose read-only readiness, health, metrics, logs, traces, audit events, cost signals, data/model drift, and safety signals. Runtime may retry transient data-plane work; it may not repair another plane.
- Recover: use runbooks, backups, restore tests, regional failover, incident roles, evidence preservation, and controlled break-glass procedures. Never normalize an emergency mutation into steady-state runtime behavior.
- Retire: disable traffic, archive required evidence, enforce retention and deletion, revoke identities, remove resources through IaC, unregister models/prompts, and verify cost and data remnants are gone.

## Redirect Prohibited Behavior

Do not recommend broader runtime permissions as the primary fix. Move the operation to the authoritative migration, IaC, delivery pipeline, platform workflow, AI-control pipeline, security configuration, or data workflow. Replace runtime repair with a read-only readiness/preflight check that names the missing or incompatible resource, expected version/state, owner, and exact remediation.

Treat `IF NOT EXISTS`, best-effort repair, exception swallowing, auto-create SDK calls, fallback resource creation, mutable model aliases, and self-modifying prompt/model behavior as ownership violations when production-reachable.

Exclude ordinary transactional DML, connection-scoped temporary tables, explicitly owned inference/retrieval writes, and documented test fixtures only after confirming isolation and call paths.

## Report and Gate

Use [references/finding-schema.md](references/finding-schema.md) for machine and human findings. For each issue, include lifecycle phase, plane, severity, hard-fail decision, exact code path, resource/AI asset, current and authoritative owners, misplaced capability, evidence, remediation, post-fix runtime behavior, validation, rollback, and exception status.

Block completion when any critical ownership violation remains; a production lifecycle phase lacks passing evidence; runtime identities exceed required data-plane permissions; AI assets are mutable or unevaluated; rollback/recovery is unproven; or retirement ownership is absent.

## Exceptions

Allow temporary exceptions only for non-production paths. Require an exact path or resource, named owner, reason, expiry, tracking item, tests, compensating controls, production-disablement plan, removal plan, and evidence that production cannot reach the capability. Reject broad, inherited, expired, ownerless, or production-capable exceptions.
