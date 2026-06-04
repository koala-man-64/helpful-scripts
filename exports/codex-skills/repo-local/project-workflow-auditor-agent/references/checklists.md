# Audit Checklists (Project Workflow Auditor Agent)
Use these checklists to drive evidence collection. Prefer **safe commands** that do not echo secrets into logs.

## 1) Instructions & Governance
- Locate **AGENTS.md** files and record their scope and directives.
- Verify instructions are consistent (no conflicting requirements across scopes).
- Check for governance docs:
  - `README.md` (onboarding, run/test commands)
  - `CONTRIBUTING.md` (dev workflow, branching/review)
  - `SECURITY.md` (vuln reporting, security expectations)
  - `CODEOWNERS` (ownership/review enforcement)
- Confirm CI enforces required gates (tests, lint, formatting) if the repo claims it does.

**Evidence commands (safe):**
- `rg --files -g 'AGENTS.md'`
- `rg --files -g 'azure-pipelines*.yml' -g 'azure-pipelines*.yaml' -g '.azuredevops/**' -g '.azdo/**' -g '.pipelines/**'`

## 2) CI/CD Workflow Safety (Azure Pipelines)
Check each pipeline under `azure-pipelines.yml`, `azure-pipelines.yaml`, `.azuredevops/pipelines/`, `.azdo/pipelines/`, and `.pipelines/` for:
- **Least privilege**: scoped service connections, constrained pipeline permissions, and no broad project collection tokens.
- **Trigger safety**: branch/path filters, PR validation behavior, protected resource checks, and no untrusted PR code with privileged credentials.
- **Supply chain**: first-party tasks with explicit major versions, reviewed third-party tasks/extensions, and pinned container images.
- **Secrets handling**: variable groups and Key Vault-backed secrets only where needed; avoid printing secrets; avoid passing secrets through artifacts or logs.
- **Deploy protections**: Azure DevOps environments, checks/approvals for production, branch policies, concurrency or exclusive locks, and rollback paths.
- **Artifacts**: retention limits, sensitive artifact handling, provenance/SBOM expectations when release-critical.

**Evidence commands:**
- `rg -n \"^\\s*(trigger|pr|schedules|resources|extends|stages|jobs|deployment|environment|variables|task|script|checkout):\" azure-pipelines*.yml azure-pipelines*.yaml .azuredevops .azdo .pipelines || true`
- `rg -n \"(serviceConnection|azureSubscription|connectedServiceName|System\\.AccessToken|persistCredentials|allowScriptsAuthAccessToken)\" azure-pipelines*.yml azure-pipelines*.yaml .azuredevops .azdo .pipelines || true`
- `rg -n \"\\|\\s*(bash|sh|pwsh|powershell)\\b|curl[^\\n]*\\|\\s*(bash|sh|pwsh|powershell)\\b|wget[^\\n]*\\|\\s*(bash|sh|pwsh|powershell)\\b\" azure-pipelines*.yml azure-pipelines*.yaml .azuredevops .azdo .pipelines || true`

## 3) Secrets & Credential Hygiene
Look for committed secrets and risky files. Prefer **file-name-only** searches:

**Filename-based checks:**
- `rg --files -g '*.pem' -g '*.pfx' -g '*.p12' -g '*.key' -g '*.crt'`
- `rg --files -g '.env' -g '.env.*' -g '*secret*' -g '*credentials*'`

**Pattern-based checks (do not print matches):**
- `rg -l \"AKIA[0-9A-Z]{16}\" . || true`
- `rg -l \"ghp_[A-Za-z0-9]{36}\" . || true`
- `rg -l \"-----BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY-----\" . || true`
- `rg -l \"client_secret\" . || true`

If any hits appear, treat them as **Critical** until verified and remediated (rotation, purging, history rewrite if necessary).

## 4) Dependency & Supply Chain Hygiene
- Verify dependency pinning / lockfiles:
  - Python: `requirements.txt`, `requirements-dev.txt`, `poetry.lock`, `uv.lock`
  - Node: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`
- Confirm dependency update automation (optional): Renovate or Azure DevOps-compatible dependency scanning.
- Confirm vulnerability scanning expectations and gaps (e.g., `pip-audit`, SCA in CI)

**Evidence commands:**
- `ls -la | rg -n \"requirements|poetry|uv\\.lock|package(-lock)?\\.json|pnpm-lock\\.yaml|yarn\\.lock\" || true`
- `rg --files -g 'renovate.json' -g '.renovaterc*' -g 'azure-pipelines*.yml' -g 'azure-pipelines*.yaml' -g '.azuredevops/**' -g '.azdo/**' -g '.pipelines/**'`

## 5) Consistency & Conventions
- Identify format/lint/test tools and confirm they are used consistently:
  - Python: `ruff`, `black`, `isort`, `mypy`, `pytest`
  - JS: `eslint`, `prettier`, `jest`
- Verify single-source-of-truth for configuration and avoid drift (duplicate settings across files).
- Flag inconsistent naming/layout patterns and unused/dead configs.

**Evidence commands:**
- `ls -la | rg -n \"pyproject\\.toml|setup\\.cfg|tox\\.ini|\\.pre-commit-config\\.yaml|\\.editorconfig\" || true`

## 6) Quality Gates & Reproducibility
- Ensure tests run in CI and are deterministic.
- Ensure build artifacts are reproducible (pinned deps, fixed Python/node versions in CI).
- Confirm clear local developer workflow (how to run tests/lint/build).

## 7) Operational Readiness (If Service/App)
- Confirm health endpoints, logs, metrics/traces expectations.
- Confirm config is environment-driven and secrets are not embedded.
- Confirm safe defaults and rollback strategy for deploy workflows.

