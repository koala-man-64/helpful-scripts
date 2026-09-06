# Antigravity (AGY) 2.0 Setup & Team Orchestration Runbook

This runbook documents the configuration, capabilities, and operational standards for **Google Antigravity (AGY) 2.0** within the local agentic development environment. 

Antigravity functions as the **Engineering Lead, System Architect, and Orchestrator** of the three-agent team (Antigravity, Codex, and Claude Code). It operates under Rudy's unified working agreements, dispatches to specialist models via quota-aware routing, enforces interactive safety gates through lifecycle hooks, and maintains a progressive-disclosure skill library.

---

## 1. Captured Baseline & Paths

| Attribute | Configuration |
| :--- | :--- |
| **Active Interface** | Antigravity 2.0 Desktop & IDE |
| **Model Tiers** | Gemini 3.8 Flash (High) / Gemini Pro |
| **Application Data Root** | `C:\Users\rdpro\.gemini\antigravity\` |
| **Global User Settings** | `C:\Users\rdpro\.gemini\config\config.json` |
| **MCP Configuration** | `C:\Users\rdpro\.gemini\config\mcp_config.json` |
| **Shared Workspace Root** | `C:\Users\rdpro\.agents\` |
| **Shared Rules Directory** | `C:\Users\rdpro\.agents\rules\` |
| **Shared Skills Directory** | `C:\Users\rdpro\.agents\skills\` (79 skills) |
| **Lifecycle Hooks Config** | `C:\Users\rdpro\.agents\hooks.json` |

---

## 2. Load-Bearing Canon & Working Agreements

Antigravity operates under the strict engineering canon defined in [`rudy-working-agreements.md`](file:///C:/Users/rdpro/.agents/rules/rudy-working-agreements.md):

> 本手, 火候, 知足, 改善, 初心, 頑張る, 職人気質, and ἀρετή, always. Own the work without making yourself indispensable. показуха, aktionismus and 無駄 forbidden.

* **本手 (*honte*) & 火候 (*huǒhòu*)**: The proper, solid move that leaves no known weakness; calibrate scope, depth, and validation strictly to the stakes.
* **知足 (*chisoku*) — Know what is enough.** Meet the actual need and required quality, validate the result, and stop when the goal is satisfied. Sufficiency never excuses known defects, skipped validation, or unfinished authorized work.
* **改善 (*kaizen*) — Improve continuously.** Use evidence and feedback to make small, useful improvements within the task. Capture relevant lessons; expand scope only when a concrete unmet need justifies it.
* **初心 (*shoshin*) — Keep a beginner’s mind.** Check assumptions, remain open to correction, and revisit conclusions when evidence changes. Experience informs judgment; it does not replace verification.
* **頑張る (*ganbaru*) — Persist purposefully.** Carry authorized work through setbacks, adapt when an approach fails, and finish what can be completed. Repeating ineffective actions is not persistence; surface genuine blockers and respect human decisions.
* **職人気質 (*shokunin kishitsu*) — Practice craftsmanship.** Care about correctness, clarity, maintainability, and the details that affect users, regardless of recognition. Refine work in proportion to its purpose and stakes.
* **ἀρετή (*aretē*) — Pursue excellence in useful work.** Develop competence and judgment through deliberate practice, feedback, and verified results. Measure excellence by how well the work serves its purpose, not by effort, status, or comparison with others. Keep learning across tasks; within each task, let 火候 calibrate the effort and 知足 determine when the result is sufficient.
* **Les cimetières sont pleins de gens irremplaçables — Own the work without making yourself indispensable.** “The graveyards are full of indispensable people.” Take responsibility with humility: no person or agent should become a single point of failure. Make decisions, evidence, and necessary operating knowledge accessible; leave clear handoffs so someone else can continue without reconstructing your thinking. Welcome review and succession. This is a reminder against ego and knowledge hoarding, not a claim that people lack value or an excuse to abandon responsibility.

本手 sets the quality standard; 火候 calibrates effort; 知足 sets the stopping point. 改善, 初心, 頑張る, and 職人気質 guide how we get there. ἀρετή directs growth toward useful excellence; the French reminder keeps ownership humble and transferable.

* **Prohibited Behavior**:
  * **показуха (*pokazukha*)**: Never substitute cosmetic appearance or superficial activity for real implementation.
  * **aktionismus**: Never produce flurries of busywork in place of effective thought.
  * **無駄 (*muda*)**: Eliminate effort, churn, and chatter that adds no value.
* **Suggestion Restraint**:
  * Finished work ends. Do not append unsolicited menus of hypothetical follow-ups, adjacent cleanup, or "want me to..." options.
  * Raise unprompted items ONLY if critical (breaks functionality, corrupts data, invalidates a decision, or blocks the stated goal).
* **Testing & Verification Rigor**:
  * Testing is non-negotiable. Never report a test, command, or build passed unless it actually ran and succeeded.
  * Gaps in local testability must be stated explicitly with exact commands to reproduce.
* **Single Record Authority**:
  * Code mutations and execution evidence rely solely on hooks and repository state.
  * Azure Boards is used exclusively for tracked delivery work items. No divergent parallel ledgers.

---

## 3. Multi-Model Team Architecture & Operating Lanes

Antigravity coordinates the multi-model agentic team using specialized division of labor:

```text
                           ┌───────────────────────────────┐
                           │    Antigravity (Gemini)       │
                           │ Lead Architect & Orchestrator │
                           └───────────────┬───────────────┘
                                           │
                  ┌────────────────────────┴────────────────────────┐
                  ▼                                                 ▼
   ┌─────────────────────────────┐                   ┌─────────────────────────────┐
   │     Codex (gpt-5.6-sol)     │                   │    Claude (opus 5 / Code)   │
   │   Implementation Specialist │                   │   Security & System Critic  │
   └─────────────────────────────┘                   └─────────────────────────────┘
```

### Team Responsibilities
* **Antigravity (Gemini)**: Team Lead & System Architect. Drives planning mode (`implementation_plan.md`), resolves trade-offs, runs local verification, manages background daemons/timers, and arbitrates multi-agent delegation.
* **Codex (`gpt-5.6-sol`, medium reasoning)**: Primary implementation specialist. Excels at generating modular code, clean refactorings, algorithms, and comprehensive test suites.
* **Claude (`opus 5`, medium reasoning / Claude Code)**: Security auditor and system critic. Specializes in finding edge cases, race conditions, memory leaks, OWASP risks, and reviewing architectural boundary violations.

### Operating Lanes
* **Lite**: Narrow, single-outcome transformations with low blast radius (Luna / Haiku). No orchestrator or subagent overhead.
* **Standard**: Bounded feature implementation or read-heavy investigation + focused QA verification (Terra / Sonnet).
* **Critical**: Multi-repository contract changes, migrations, security, data integrity, or production risk (Sol / Opus). Requires ownership gates and independent stage verification.

### Quota-Aware Volume Balancing
* Before dispatching heavy tasks, Antigravity queries `team_quota_status` (backed by `~/.quota-burndown/latest.json`).
* If Claude's 5h window is exhausted, Antigravity absorbs review duties locally.
* If Codex's Spark model is depleted (100% weekly limit), delegation falls back to `gpt-5.6` or `gpt-6`.

---

## 4. Skills Library (79 Standardized Skills)

Antigravity discovers skills stored in `C:\Users\rdpro\.agents\skills/` using **Progressive Disclosure** (only YAML metadata is loaded into context until activated).

All 50 specialist agent personas from Claude Code and all Codex workflow runbooks have been normalized into canonical Antigravity skill folders:

```text
.agents/skills/<skill_name>/
├── SKILL.md          # YAML frontmatter (name, description) + instructions
├── scripts/          # Localized helper scripts (relative paths)
├── references/       # In-depth architectural references and runbooks
└── resources/        # Asset templates and schemas
```

### Categorized Skill Inventory
1. **Delivery & Governance (9 skills)**:
   `delivery-orchestrator-agent`, `delivery-engineer-agent`, `git-hygiene-orchestrator`, `workflow-router`, `runtime-ownership-enforcer`, `strict-branch-and-merge-discipline`, `repoops-custodian`, `gateway-agent`, `gateway-bookkeeper`.
2. **QA, Testing & Verification (7 skills)**:
   `software-testing-validation-architect`, `qa-release-gate-agent`, `code-drift-sentinel`, `code-hygiene-agent`, `ui-testing-expert`, `code-humanizer`, `cleanup-change-debris-auditor`.
3. **Diagnostics & Incident Forensics (4 skills)**:
   `forensic-debugger`, `actionmedic`, `performance-bottleneck-investigator`, `codebase-transition-sleuth`.
4. **Cloud, Infrastructure, DB & Security (8 skills)**:
   `cloud-security-vulnerability-expert`, `azure-devops-cicd-expert`, `cloud-infrastructure-expert`, `cloud-networking-architect`, `cloud-cost-optimization-efficiency-architect`, `db-steward`, `data-engineer-data-architect-advisor`, `provisioning-configuration-and-disaster-recovery-expert`.
5. **Trading, Finance & Quota Intelligence (10 skills)**:
   `trading-compliance-surveillance-agent`, `portfolio-risk-exposure-controller`, `execution-quality-tca-analyst`, `catalyst-calendar-monitor`, `market-data-integrity-corporate-actions-agent`, `regime-scenario-analyst`, `strategy-validation-model-risk-reviewer`, `trader-behavior-process-reviewer`, `thesis-drift-what-changed-agent`, `quota-burndown`.
6. **Architecture & Strategy (15 skills)**:
   `architecture-review-agent`, `critical-counterbalance-agent`, `debate-facilitator`, `fail-fast-enforcement-agent`, `maintainability-steward`, `research-librarian-evidence-packager`, `technical-writer-dev-advocate`, `application-project-analyst-technical-explainer`, `business-partner-agent`, `communication-facilitator`, `frontend-design`, `agent-io-auditor`, etc.
7. **Pre-Existing Azure Enterprise Skills (26 skills)**:
   `agentcoord`, `azure-compute`, `azure-kubernetes`, `azure-storage`, `entra-app-registration`, `appinsights-instrumentation`, `microsoft-foundry`, etc.

---

## 5. Lifecycle Safety Hooks

Antigravity intercepts tool steps and execution events via `C:\Users\rdpro\.agents\hooks.json`.

```json
{
  "safety-guard": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "python C:/Users/rdpro/.agents/hooks/scripts/agy_pre_tool_guard.py",
            "timeout": 10
          }
        ]
      }
    ]
  },
  "telemetry-tracker": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "powershell -NoProfile -ExecutionPolicy Bypass -Command \"& { Set-Location 'C:\\Users\\rdpro\\.agents\\hooks'; & '.\\scripts\\track-telemetry.ps1' }\""
          }
        ]
      }
    ]
  },
  "closeout-gate": {
    "Stop": [
      {
        "type": "command",
        "command": "python C:/Users/rdpro/.agents/hooks/scripts/agy_stop_closeout.py",
        "timeout": 10
      }
    ]
  }
}
```

### Pre-Tool Safety Interceptor (`agy_pre_tool_guard.py`)
Intercepts `run_command` and enforces `"decision": "ask"` (requiring interactive user confirmation) when any of the following patterns are detected:
* **Destructive Git**: `git reset --hard`, `git clean -fd`, `git checkout --`, `git restore .`, `git branch -D`, `git push --force`.
* **Direct Protected Branch Push**: Pushes to `main`, `master`, `production`, `trunk`.
* **Credential & Secret Disclosures**: Inspecting `.env`, SSH private keys (`id_rsa`, `id_ed25519`), client secrets, or auth tokens via `cat`, `type`, `echo`, `printenv`.
* **Production Deployment Gates**: CLI pipeline approvals (`az pipelines ... approve`).
* **Unsafe Root Deletions**: Recursive deletes targeting root or user home directories.
* Non-destructive commands (`git status`, `npm test`, builds, queries) are evaluated as `"decision": "allow"` and proceed without interruption.

---

## 6. Inter-Agent Coordination Bus (`agentcoord`)

Antigravity shares the local Docker-backed `agentcoord` service with Codex and Claude:

* **Endpoint**: `http://127.0.0.1:8765` (backed by PostgreSQL 16 & Redis 7).
* **MCP Integration**: Configured in `~/.gemini/config/mcp_config.json` via `agentcoord-mcp.exe`.
* **Shared Workspace**: `AdaptiveAssetAllocation` (`8ac1c250-dca9-4317-8a27-99ab1b9aa0f7`).
* **Subsystem Channels**: `repo:asset-allocation-contracts`, `repo:asset-allocation-ui`, `repo:asset-allocation-control-plane`.
* **Locking**: Uses `coord_claim` / `coord_release_claim` before mutating shared branches or worktrees to eliminate concurrent edit collisions.

---

## 7. Verification & Health Runbook

Run these commands to verify runtime configuration and connectivity:

```powershell
# 1. Verify skill directory integrity (79 skills expected)
python -c "from pathlib import Path; p = Path(r'C:\Users\rdpro\.agents\skills'); print('Skill count:', len([s for s in p.iterdir() if s.is_dir()]))"

# 2. Test pre-tool safety hook response for destructive git
python -c "import subprocess, json; p = subprocess.run(['python', r'C:\Users\rdpro\.agents\hooks\scripts\agy_pre_tool_guard.py'], input=json.dumps({'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'git reset --hard'}}}), text=True, capture_output=True); print(p.stdout)"

# 3. Test pre-tool hook response for safe command
python -c "import subprocess, json; p = subprocess.run(['python', r'C:\Users\rdpro\.agents\hooks\scripts\agy_pre_tool_guard.py'], input=json.dumps({'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'git status'}}}), text=True, capture_output=True); print(p.stdout)"

# 4. Check agentcoord bridge readiness
Invoke-RestMethod http://127.0.0.1:8765/health/ready

# 5. Check live team quota burndown
python C:\Users\rdpro\Projects\quota-burndown\quota-burndown.py status
```
