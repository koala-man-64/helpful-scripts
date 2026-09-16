from hook_utils import (
    additional_context,
    azure_devops_agent_authority_lines,
    classify_work_kind,
    compact_agent_summary,
    emit_json,
    extract_prompt,
    read_hook_input,
    requires_finish_workflow,
    requires_tracking,
    session_flag_once,
    workflow_scope_enabled,
)


# Stated once per session (see main); these do not vary by prompt.
STANDING_POLICY_LINES = (
    "- Blanket finish approval: when task-owned files change and the user does not explicitly limit scope, complete the git finish workflow (commit, push, PR, merge/completion) before closeout instead of waiting for a separate 'finish it' prompt.",
    "- Finish delegation: run the finish workflow and any Azure Boards bookkeeping as ONE sonnet-tier subagent spawn (git-hygiene-orchestrator for git, gateway-bookkeeper for boards). Do not run the az/git steps one by one from the main thread; each such call re-reads the whole conversation.",
)


LANES = (
    (
        "finish",
        (
            "finish it",
            "complete workflow",
            "complete your workflow",
            "commit",
            "push",
            "pull request",
            " pr ",
            "merge",
            "squash",
            "auto-complete",
            "approve pr",
            "approve pull request",
            "complete pr",
            "complete pull request",
            "close work item",
            "close workitem",
            "complete work item",
            "complete workitem",
            "transition-work-items",
        ),
        "delivery-orchestrator-agent -> Azure DevOps tracking -> code-drift-sentinel -> software-testing-validation-architect -> git finish workflow",
    ),
    (
        "ci-pipeline",
        ("pipeline", "build failed", "failed build", "failing check", "failed check", "ci", "validation failed", "re-queue", "rerun"),
        "delivery-orchestrator-agent -> actionmedic -> software-testing-validation-architect -> Azure DevOps tracking",
    ),
    (
        "production-incident",
        ("production", "prod", "live", "incident", "500", "traceback", "exception", "relation ", "does not exist", "unavailable"),
        "delivery-orchestrator-agent -> forensic-debugger -> relevant specialist -> software-testing-validation-architect -> Azure DevOps tracking",
    ),
    (
        "azure-boards-bookkeeping",
        ("azure boards", "work item", "workitem", "ab#", "backlog", "board", "bookkeeper", "sprint"),
        "delivery-orchestrator-agent -> Azure DevOps tracking",
    ),
    (
        "repo-cleanup",
        ("git hygiene", "branch cleanup", "stale branch", "worktree", "repo cleanup", "prune", "conflict"),
        "delivery-orchestrator-agent -> code-drift-sentinel -> git finish workflow -> Azure DevOps tracking",
    ),
    (
        "trading-strategy-validation",
        (
            "backtest",
            "model risk",
            "overfit",
            "overfitting",
            "leakage",
            "walk-forward",
            "train/test",
            "feature stability",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-execution-quality",
        (
            "fills",
            "slippage",
            "benchmark",
            "routing quality",
            "venue",
            "implementation shortfall",
            "participation rate",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-portfolio-risk",
        (
            "gross exposure",
            "net exposure",
            "factor exposure",
            "concentration",
            "crowding",
            "drawdown",
            "liquidity stress",
            "correlation cluster",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-performance-attribution",
        (
            "attribution",
            "return decomposition",
            "alpha vs beta",
            "cost drag",
            "net returns",
            "performance contribution",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-thesis-drift",
        (
            "trade thesis",
            "thesis drift",
            "what changed",
            "thesis weakened",
            "thesis broken",
            "thesis inverted",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-process-review",
        (
            "trade journal",
            "plan adherence",
            "chasing",
            "averaging down",
            "stop discipline",
            "override habit",
            "process discipline",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-compliance",
        (
            "restricted list",
            "approval log",
            "surveillance",
            "locate record",
            "policy breach",
            "audit trail",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-regime-scenario",
        (
            "market regime",
            "regime transition",
            "scenario analysis",
            "market breadth",
            "credit spreads",
            "volatility state",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-catalyst-calendar",
        (
            "earnings calendar",
            "policy event",
            "lockup expiry",
            "dividend calendar",
            "index change",
            "corporate action calendar",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-data-integrity",
        (
            "corporate actions",
            "symbol map",
            "vendor feed",
            "stale prices",
            "reference data",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "trading-evidence-pack",
        (
            "evidence pack",
            "filings",
            "transcripts",
            "source traceability",
        ),
        "delivery-orchestrator-agent -> relevant specialist -> relevant implementation/QA agents",
    ),
    (
        "review",
        ("review", "audit", "risks", "findings", "regression"),
        "delivery-orchestrator-agent -> relevant reviewer -> code-drift-sentinel as needed -> software-testing-validation-architect as needed -> Azure DevOps tracking when tracked",
    ),
    (
        "frontend",
        ("ui", "react", "component", "page", "css", "layout", "design", "browser", "playwright"),
        "delivery-orchestrator-agent -> relevant specialist -> relevant specialist -> git finish workflow",
    ),
    (
        "db-data",
        ("database", "postgres", "sql", "migration", "schema", "dataframe", "pipeline data", "copy error"),
        "delivery-orchestrator-agent -> db-steward -> software-testing-validation-architect -> Azure DevOps tracking",
    ),
    (
        "architecture",
        ("architecture", "design", "approach", "plan", "tradeoff", "proposal"),
        "delivery-orchestrator-agent -> architecture-review-agent -> Azure DevOps tracking when tracked",
    ),
    (
        "docs",
        ("documentation", "docs", "readme", "runbook", "developer guide"),
        "delivery-orchestrator-agent -> relevant specialist -> git finish workflow",
    ),
)


def classify(prompt: str) -> tuple[str, str]:
    normalized = f" {prompt.lower()} "
    for lane, needles, sequence in LANES:
        if any(needle in normalized for needle in needles):
            return lane, sequence
    return (
        "implementation",
        "delivery-orchestrator-agent -> Azure DevOps tracking when tracked -> the primary agent -> code-drift-sentinel -> software-testing-validation-architect -> git finish workflow",
    )


def contract_hint(prompt: str) -> str:
    normalized = prompt.lower()
    shared_terms = ("api response", "api request", "payload", "schema", "serialization", "contract", "@asset-allocation/contracts", "asset-allocation-contracts")
    if any(term in normalized for term in shared_terms):
        return "Potential shared contract surface detected. Route authoring through asset-allocation-contracts first unless local evidence proves this is repo-private."
    return "Before editing, classify the work as local-only or contracts-repo-first if shared shapes are involved."


def main() -> int:
    if not workflow_scope_enabled():
        return emit_json(None)
    payload = read_hook_input()
    session_id = str(payload.get("session_id") or "")
    prompt = extract_prompt(payload)
    lane, sequence = classify(prompt)
    work_kind = classify_work_kind(prompt)
    finish_required = lane == "finish" or requires_finish_workflow(prompt)
    tracking_required = finish_required or requires_tracking(prompt)
    required_agents, optional_agents = compact_agent_summary(
        sequence,
        tracking_required=tracking_required,
        finish_required=finish_required,
    )
    lines = [
        "Team workflow routing:",
        f"- Lane: {lane}",
        f"- Work kind: {work_kind}",
        f"- Required agents: {required_agents}",
        f"- Optional agents: {optional_agents}",
        f"- Tracking required: {'yes' if tracking_required else 'no'}",
        f"- Finish workflow required: {'yes' if finish_required else 'no'}",
        f"- Contract routing: {contract_hint(prompt)}",
    ]
    # Standing policy is stated once per session and again after compaction
    # (the session-start hook clears the flags). Every turn's text is re-sent
    # with every later request, so the per-turn block carries only the facts
    # that change.
    if session_flag_once(session_id, "router-standing-policy"):
        lines.extend(STANDING_POLICY_LINES)
    if (tracking_required or finish_required) and session_flag_once(
        session_id, "router-azure-devops-authority"
    ):
        lines.extend(azure_devops_agent_authority_lines())
    context = "\n".join(lines)
    return emit_json(additional_context("UserPromptSubmit", context))


if __name__ == "__main__":
    raise SystemExit(main())
