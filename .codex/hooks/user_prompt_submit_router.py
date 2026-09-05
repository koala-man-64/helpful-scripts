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
        "delivery-orchestrator-agent -> cleanup-change-debris-auditor -> code-drift-sentinel as needed -> git finish workflow -> Azure DevOps tracking",
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
        "delivery-orchestrator-agent -> market-data-integrity-corporate-actions-agent -> relevant implementation/QA agents",
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
        "delivery-orchestrator-agent -> db-steward or data-engineer-data-architect-advisor -> software-testing-validation-architect -> Azure DevOps tracking",
    ),
    (
        "architecture",
        ("architecture", "design", "approach", "plan", "tradeoff", "proposal"),
        "delivery-orchestrator-agent -> architecture-review-agent -> Azure DevOps tracking when tracked",
    ),
    (
        "docs",
        ("documentation", "docs", "readme", "runbook", "developer guide"),
        "delivery-orchestrator-agent -> technical-writer-dev-advocate -> git finish workflow",
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
    payload = read_hook_input()
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
    context = "\n".join(
        [
            "Team workflow routing:",
            f"- Topic: {lane}",
            "- Apply the selected operating lane before considering optional specialists; topic hints do not select models, effort, delegation, or permissions.",
            f"- Work kind: {work_kind}",
            f"- Required workflow steps: {required_agents}",
            f"- Optional steps within the selected lane: {optional_agents}",
            f"- Tracking required: {'yes' if tracking_required else 'no'}",
            f"- Finish workflow required: {'yes' if finish_required else 'no'}",
            "- Blanket finish approval: when task-owned files change and the user does not explicitly limit scope, complete the git finish workflow (commit, push, PR, merge/completion) before closeout instead of waiting for a separate 'finish it' prompt.",
            f"- Contract routing: {contract_hint(prompt)}",
            *(
                azure_devops_agent_authority_lines()
                if (tracking_required or finish_required)
                else ()
            ),
        ]
    )
    return emit_json(additional_context("UserPromptSubmit", context))


if __name__ == "__main__":
    raise SystemExit(main())
