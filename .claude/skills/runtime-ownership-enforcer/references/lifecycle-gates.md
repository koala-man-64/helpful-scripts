# Cloud-native development lifecycle gates

## Contents

- Gate model
- Phase gates
- Cross-cutting controls
- Lifecycle evidence manifest
- Completion decision

## Gate model

Evaluate every phase for the requested environment and change scope. Record `pass`, `conditional`, `fail`, or `not_applicable` with a named owner and direct evidence. Require a rationale for `not_applicable` and an owner, expiry, tracking item, and enforcement date for `conditional`.

Use these ownership planes consistently:

| Plane | Owns | Runtime boundary |
|---|---|---|
| Management | Cloud resources, networking, identity, policy, platform configuration, encryption, backup policy | Read configuration and use provisioned resources only |
| Delivery | Build, migration, deployment, promotion, rollback, release evidence | Report artifact/version; do not invoke delivery from serving code |
| AI control | Datasets, prompts, models, evaluations, registries, endpoints, indices, safety policies | Consume pinned, promoted assets; emit inference and monitoring data |
| Runtime data | Requests, inference, retrieval, events, business DML, caches, owned outputs | Operate only with documented data-plane permissions |

## Phase gates

### 1. Discovery and planning

Require:

- Business objective, users, criticality, environments, regions, tenancy, data classes, regulatory constraints, and acceptable automation level.
- Named application, platform, security, data, AI/model, SRE, delivery, cost, and retirement owners or an explicit role consolidation.
- SLOs, error budget, recovery objectives, cost envelope, capacity assumptions, and failure/abuse cases.
- Work tracking, acceptance criteria, dependencies, rollout scope, and definition of done.

Block when ownership, production criticality, data classification, AI use, or release authority is ambiguous.

Evidence: charter or work item, owner map, risk register, SLO/RTO/RPO decision, cost assumptions.

### 2. Architecture and threat modeling

Require:

- Context, container, deployment, data-flow, identity/trust, AI-flow, and dependency views.
- Management, delivery, AI-control, and runtime-data plane boundaries.
- Authentication, authorization, tenancy isolation, network boundaries, encryption, key ownership, audit trail, and abuse/threat model.
- State, consistency, idempotency, concurrency, retry, backpressure, degradation, failover, rollback, and compatibility decisions.
- Build-versus-buy, regional/data residency, quota, vendor, lock-in, and exit decisions.

Block when runtime needs management-plane credentials, a critical state has multiple writers without coordination, or a failure path relies on silent repair.

Evidence: ADRs, diagrams, threat model, data inventory, API/event contracts, failure-mode analysis.

### 3. Development and local environments

Require:

- Reproducible setup, pinned toolchain, explicit configuration schema, typed/validated inputs, deterministic seeds where relevant, and documented environment differences.
- Local/test bootstrap guarded by an explicit non-production check and absent from production packaging.
- Least-privilege local identities, safe sample data, no production credentials, and no hidden network dependency in unit tests.
- Clear failure messages for missing configuration, resources, schema versions, AI assets, and permissions.
- Focused tests with code review, lint, type, formatting, static analysis, and generated-file discipline.

Block production-reachable bootstrap, repair, broad exception swallowing, mutable global state, or implicit fallback to unsafe defaults.

Evidence: setup automation, configuration schema, tests, review output, package/image contents.

### 4. Data and AI engineering

Require:

- Provenance, ownership, permitted use, classification, lineage, retention, deletion, quality, freshness, leakage, and train/eval/test separation for data.
- Versioned datasets, transformations, features, prompts, system instructions, tools, models, embeddings, indices, safety policies, and evaluation sets.
- Baseline and candidate evaluation for quality, groundedness, retrieval, hallucination, bias/fairness, harmful output, prompt injection, tool misuse, privacy, latency, and cost.
- Reproducible training/fine-tuning, registry lineage, promotion criteria, approval authority, rollback target, and monitoring thresholds.
- Human oversight and bounded autonomy for consequential or state-changing agent actions.

Block unlicensed or untraceable data, evaluation on contaminated data, mutable `latest` assets, serving-time retraining or model swapping, missing safety evaluation, or agents with unbounded tools.

Evidence: data/model cards, lineage, evaluation reports, prompt/model hashes, registry records, approval and rollback record.

### 5. Build and software supply chain

Require:

- Reproducible build, locked dependencies, pinned base images, supported runtimes, license policy, vulnerability scanning, SBOM, provenance/attestation, and artifact signing where supported.
- Minimal multi-stage image, non-root execution, read-only filesystem where practical, dropped capabilities, bounded resources, and no build-only material in the runtime image.
- Build and runtime identities separated; artifact digest recorded; generated artifacts traceable to source.

Block critical unmitigated vulnerabilities, mutable base tags for production, embedded credentials, unsigned/untraceable artifacts where policy requires signing, or privileged containers without exception.

Evidence: lockfiles, build logs, SBOM, scan result, signature/attestation, image configuration and digest.

### 6. Verification and quality engineering

Require:

- Unit, component, integration, contract, end-to-end, migration, permission, resilience, recovery, performance, security, privacy, and observability coverage proportional to risk.
- Negative tests for missing resources, denied permissions, incompatible schemas/assets, timeouts, retries, duplicates, stale data, partial failure, and dependency outage.
- AI tests for deterministic components plus statistical evaluation with thresholds, confidence, representative slices, red-team cases, and regression comparisons.
- Proof that provisioning creates required state, runtime works with least privilege, runtime fails clearly when provisioning is incomplete, and no runtime path repairs infrastructure.

Block failed required tests, missing critical negative-path coverage, flaky required gates, unevaluated model/prompt changes, or untested rollback for high-risk releases.

Evidence: test/evaluation results, coverage map, failure artifacts, performance baseline, security results, rollback rehearsal.

### 7. Provisioning and configuration

Require:

- Versioned IaC and migrations for resources, schemas, grants, policies, identities, network controls, observability, model endpoints, registries, feature/vector stores, backup, and retention.
- Plan/preview, peer review, policy-as-code, environment parameters, drift detection, state locking, rollback or forward-fix strategy, and read-back verification.
- Separate deployment and runtime identities with minimum scopes and time-bounded elevation.

Block console-only production state, runtime provisioning, overprivileged workload identities, unmanaged drift, non-repeatable migrations, or resources without an authoritative owner.

Evidence: IaC/migration source, plan, policy result, deployment identity, applied-state read-back, drift report.

### 8. CI/CD, release, and deployment

Require:

- Protected source flow, immutable artifacts, isolated stages, environment approvals, policy gates, artifact and AI-asset promotion, compatibility checks, and evidence retention.
- Expand/contract database and event/API compatibility where consumers deploy independently.
- Canary, blue/green, ring, or justified rollout strategy; automated health criteria; rollback trigger and tested rollback target.
- Post-deploy read-only verification of artifact digest, configuration, identity, schema/assets, health, traffic, errors, latency, safety, and cost.

Block rebuilding per environment, mutable model/prompt promotion, skipped required approval, untested destructive migration, unknown rollback target, or source/CI success presented as runtime proof.

Evidence: PR, build, artifact digest, release, approvals, deployment, runtime read-back, canary and rollback records.

### 9. Operations and observability

Require:

- Health, readiness, metrics, structured logs, traces, correlation, audit events, dashboards, alerts, runbooks, SLO/error-budget reporting, and ownership routing.
- Capacity, autoscaling, quota, rate-limit, backpressure, circuit-breaker, cost, and dependency monitoring.
- Data quality/freshness, model/data/concept drift, prompt/model version, retrieval quality, safety, tool-action, human-escalation, latency, and token/cost signals for AI workloads.
- Least-privilege runtime identities and read-only readiness checks for provisioned state.

Block silent failure, false-success health, missing owner for critical alerts, auto-repair across planes, unbounded spend, or unobservable AI asset/version changes.

Evidence: live configuration, dashboards, alert tests, SLO report, runtime identity grants, runbooks, telemetry samples.

### 10. Incident response, resilience, and disaster recovery

Require:

- Severity model, on-call ownership, communication, audit/evidence preservation, containment, recovery, and post-incident workflow.
- Backups, restore validation, regional/service failover, dependency degradation, queue replay/idempotency, and data reconciliation plans.
- Controlled break-glass identity with approval, expiry, monitoring, and revocation; record emergency infrastructure changes for authoritative reconciliation.
- AI containment for unsafe model/prompt/tool behavior, including disablement, rollback, traffic isolation, and preserved evaluation evidence.

Block untested restore for critical state, permanent emergency privileges, runtime self-healing used as incident policy, or missing safe AI disable/rollback path.

Evidence: restore/failover tests, incident drills, break-glass audit, recovery objectives, post-incident actions.

### 11. Retirement and decommissioning

Require:

- Traffic and dependency removal order, consumer notification, data export/retention/deletion, legal hold, archive, and audit evidence.
- Identity and credential revocation, DNS/network cleanup, resource destruction through IaC, registry/model/prompt/index retirement, and monitoring removal after verification.
- Residual cost, data, replicas, backups, queues, endpoints, images, packages, and third-party access review.
- Final owner sign-off and recoverability decision.

Block orphaned identity, unmanaged retained data, active traffic/dependency, console-only deletion, or unowned residual cost/resource.

Evidence: decommission plan, dependency proof, deletion/retention records, IaC change, identity read-back, cost/resource inventory.

## Cross-cutting controls

Apply at every phase:

- Security, privacy, compliance, accessibility, reliability, performance, cost, sustainability, observability, data integrity, AI safety, and human accountability.
- Version and link source, work item, decision, artifact, deployment, runtime, incident, and retirement evidence separately.
- Distinguish verified current evidence from inference. Never treat merged source, a green build, or a created resource as proof of healthy production behavior.

## Lifecycle evidence manifest

Use `scripts/validate_lifecycle_evidence.py` with a JSON manifest containing all eleven phase IDs:

`discovery`, `architecture`, `development`, `data_ai`, `build_supply_chain`, `verification`, `provisioning`, `release_deployment`, `operations`, `incident_recovery`, and `retirement`.

Each phase must contain `status`, `owner`, `evidence`, and `rationale`. Production accepts only `pass` or justified `not_applicable`; an applicable phase cannot be `not_applicable`. Non-production may use `conditional` only with `tracking_work_item`, `expires_on`, and `enforcement_date`, for at most 180 days.

## Completion decision

Return `PASS` only when every applicable phase passes, every source hard failure is cleared, all exceptions are valid, and environment-specific evidence is current. Otherwise return `BLOCKED` with the failing phase, owner, missing evidence, and next authoritative action.
