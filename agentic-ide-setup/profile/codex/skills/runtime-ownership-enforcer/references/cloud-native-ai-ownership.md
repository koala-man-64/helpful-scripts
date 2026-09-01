# Cloud-native AI ownership decisions

## Contents

- AI asset ownership
- Allowed and prohibited runtime behavior
- Agent and tool boundaries
- RAG and vector systems
- Training, evaluation, and promotion
- Cloud-native platform boundaries
- Operational and retirement requirements

## AI asset ownership

Treat AI systems as software plus governed, versioned assets. Map each asset independently:

| Asset | Authoritative mechanism | Runtime allowance |
|---|---|---|
| Dataset and labels | Governed data pipeline and catalog | Read approved version; write owned inference/feedback events |
| Feature definition/store schema | Versioned data/ML pipeline and provisioning | Read/write feature values within schema; never create or alter store/schema |
| Prompt/system instruction | Reviewed source or prompt registry with immutable version | Load pinned promoted version; never silently rewrite or select mutable latest |
| Model/fine-tune | Reproducible training pipeline and model registry | Invoke pinned deployment/version; never train as serving recovery |
| Evaluation set and thresholds | AI assurance/evaluation owner | Emit candidates/results; never lower thresholds at runtime |
| Model endpoint/deployment | IaC or governed ML platform deployment | Invoke endpoint; never create, resize, redeploy, or change policy |
| Vector collection/index schema | IaC, migration, or governed indexing pipeline | Query and write owned vectors/documents; never create/alter collection or index |
| Embedding model | AI-control promotion | Use pinned version and dimension contract |
| Agent tools and policies | Reviewed application/security configuration | Invoke allowlisted tools within per-action authorization |
| Safety filters/guardrails | Security/AI governance policy | Apply and report policy; never weaken or bypass it |
| Feedback and telemetry | Runtime data pipeline | Append privacy-reviewed events; never use feedback for unapproved online learning |

Record content hash or immutable version, owner, environment, evaluation evidence, promotion decision, dependency compatibility, runtime consumer, rollback target, retention, and retirement state for every applicable asset.

## Allowed and prohibited runtime behavior

Allow runtime to:

- Perform inference, retrieval, ranking, summarization, classification, tool selection, and business DML within documented ownership.
- Produce embeddings or index documents when ingestion/indexing is the workload's explicit data-plane responsibility and the collection/schema already exists.
- Retry transient calls with bounded backoff, idempotency, deadlines, circuit breaking, and observable failure.
- Fall back only to a pre-evaluated, pinned, compatible asset through an approved policy with explicit telemetry.
- Emit feedback, quality, drift, safety, latency, token, and cost signals through governed data paths.

Prohibit runtime from:

- Creating or altering model deployments, endpoints, registries, collections, indices, feature-store schemas, identities, role assignments, or network rules.
- Selecting `latest`, discovering an arbitrary model dynamically, or changing prompt/model/tool versions to recover from failure.
- Fine-tuning, retraining, promoting, registering, or replacing a model from a request-serving path.
- Lowering evaluation thresholds, disabling content/safety controls, expanding agent tools, or increasing autonomy as fallback.
- Automatically granting access to missing data, expanding network reach, or copying production data into an ungoverned store.
- Treating cached, synthetic, stale, or lower-quality data as authoritative without an approved degraded-mode contract.

## Agent and tool boundaries

For autonomous or tool-using systems:

- Define the agent's objective, bounded plan horizon, allowed tools, per-tool schema, authorization context, rate/financial limits, data boundaries, and termination conditions.
- Authorize each consequential action at execution time; do not rely solely on the model's intent or earlier prompt context.
- Separate read, propose, approve, and execute capabilities. Require human approval for destructive, financially material, security-sensitive, external-communication, or production-management actions unless an explicit policy delegates them.
- Treat tool output, retrieved content, user files, and web content as untrusted input. Defend against prompt injection, confused-deputy behavior, data exfiltration, and cross-tenant leakage.
- Validate tool arguments, constrain destinations, redact sensitive material, record actor/model/prompt/tool/version/correlation, and make writes idempotent where possible.
- Provide kill switch, safe stop, replay/audit, compensation or rollback, and escalation to a named human owner.

Block an agent that can mutate management or AI-control planes merely because its workload identity has permission. The same ownership boundary applies whether code or a model chooses the action.

## RAG and vector systems

Separate four responsibilities:

1. Source ingestion owns connector authorization, provenance, consent, classification, deletion, freshness, deduplication, and poisoning controls.
2. Transformation owns chunking, normalization, metadata, embedding model/version, dimension, and reproducibility.
3. Index provisioning owns collection/schema, encryption, network, capacity, replica, partition, retention, and access policy.
4. Query runtime owns authorized retrieval, filters, tenant scope, ranking, citation, context limits, and failure behavior.

Require deletion propagation, source-to-chunk-to-vector lineage, embedding compatibility, atomic/versioned reindexing, rollback pointer, relevance/groundedness evaluation, tenant filters enforced below the model, and monitoring for stale or poisoned content.

Do not let query runtime create a missing index, broaden filters, remove tenant predicates, switch embedding dimensions, or trigger an in-place repair. Fail readiness and route to ingestion/index owners.

## Training, evaluation, and promotion

Require training/fine-tuning pipelines to own:

- Reproducible code, environment, base model, hyperparameters, seeds, dataset versions, feature transforms, compute, checkpointing, lineage, and cost.
- Data licensing, consent, privacy, representativeness, leakage checks, class/slice analysis, and retention.
- Baseline comparison, task metrics, calibration, robustness, safety, bias/fairness, security/red-team, latency, throughput, and cost evaluation.
- Registry record, model card, approval, deployment compatibility, staged rollout, monitoring thresholds, rollback asset, and retirement.

Use deterministic tests for deterministic components and statistical tests for model behavior. Record sample size, slices, confidence/variance, thresholds, regressions, and known limitations. Do not promote solely because aggregate quality improved when a critical safety, privacy, latency, cost, or protected-slice guardrail regressed.

For third-party foundation models, record provider/version, region, data-use and retention settings, availability/quota, content policy, price, context/token constraints, fallback policy, dependency exit plan, and evidence that provider changes cannot silently alter the production contract.

## Cloud-native platform boundaries

Apply these provider-neutral decisions:

- Provision compute, Kubernetes/Container Apps resources, serverless functions, gateways, queues/topics, storage, databases, caches, observability, model endpoints, vector services, private connectivity, DNS, identities, and policies through IaC or governed platform workflows.
- Separate build, provisioning, deployment, migration, indexing/training, and runtime identities. Give each only its phase-specific operations.
- Use workload identity or short-lived federation where available; avoid static credentials and never bake credentials into images or model artifacts.
- Pin images and AI assets by digest/version, enforce resource requests/limits, disruption and autoscaling policy, health probes, graceful shutdown, and topology constraints appropriate to criticality.
- Define quota, concurrency, rate, token, GPU/accelerator, storage, network egress, and cost budgets with observable saturation and degradation behavior.
- Keep production approval and break-glass authority outside autonomous runtime identities.

## Operational and retirement requirements

Correlate each request with application version, deployment revision, prompt/model/embedding/index versions, tool policy, tenant/user authorization context, evaluation policy, and downstream dependency. Redact sensitive inputs and outputs while retaining enough structured evidence for incident analysis.

Monitor service and AI behavior together: availability, errors, latency, saturation, cost, tokens, cache, retrieval quality, groundedness, refusals, safety events, tool failures/actions, drift, feedback, and human escalations. Define action thresholds and owners; monitoring without an owned response is not a gate.

Retire AI assets deliberately: stop new traffic and training, preserve required lineage/evaluations, remove endpoint and registry access, revoke identities, delete or retain datasets/vectors/prompts/models according to policy, update consumers, verify bills and replicas, and preserve the rollback/recovery decision. Never leave a deprecated model, prompt, endpoint, index, or tool callable by production identities.
