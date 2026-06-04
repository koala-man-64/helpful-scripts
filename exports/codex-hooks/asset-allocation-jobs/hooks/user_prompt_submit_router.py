from hook_utils import additional_context, emit_json, extract_prompt, read_hook_input


LANES = (
    (
        "finish",
        ("finish it", "complete workflow", "complete your workflow", "commit", "push", "pull request", " pr ", "merge", "squash"),
        "delivery-orchestrator-agent -> gateway-bookkeeper -> git-hygiene-orchestrator -> qa-release-gate-agent as needed",
    ),
    (
        "ci-pipeline",
        ("pipeline", "build failed", "failed build", "failing check", "failed check", "ci", "validation failed", "re-queue", "rerun"),
        "delivery-orchestrator-agent -> actionmedic -> azure-devops-cicd-expert -> qa-release-gate-agent -> gateway-bookkeeper",
    ),
    (
        "production-incident",
        ("production", "prod", "live", "incident", "500", "traceback", "exception", "relation ", "does not exist", "unavailable"),
        "delivery-orchestrator-agent -> forensic-debugger -> relevant specialist -> qa-release-gate-agent -> gateway-bookkeeper",
    ),
    (
        "azure-boards-bookkeeping",
        ("azure boards", "work item", "workitem", "ab#", "backlog", "board", "bookkeeper", "sprint"),
        "delivery-orchestrator-agent -> gateway-bookkeeper",
    ),
    (
        "repo-cleanup",
        ("git hygiene", "branch cleanup", "stale branch", "worktree", "repo cleanup", "prune", "conflict"),
        "delivery-orchestrator-agent -> repoops-custodian -> git-hygiene-orchestrator -> gateway-bookkeeper",
    ),
    (
        "review",
        ("review", "audit", "risks", "findings", "regression"),
        "delivery-orchestrator-agent -> relevant reviewer -> qa-release-gate-agent as needed -> gateway-bookkeeper when tracked",
    ),
    (
        "frontend",
        ("ui", "react", "component", "page", "css", "layout", "design", "browser", "playwright"),
        "delivery-orchestrator-agent -> frontend-design or delivery-engineer-agent -> ui-testing-expert -> git-hygiene-orchestrator",
    ),
    (
        "db-data",
        ("database", "postgres", "sql", "migration", "schema", "dataframe", "pipeline data", "copy error"),
        "delivery-orchestrator-agent -> db-steward or data-engineer-data-architect-advisor -> qa-release-gate-agent -> gateway-bookkeeper",
    ),
    (
        "architecture",
        ("architecture", "design", "approach", "plan", "tradeoff", "proposal"),
        "delivery-orchestrator-agent -> architecture-review-agent or critical-counterbalance-agent -> gateway-bookkeeper when tracked",
    ),
    (
        "trading-model-risk",
        ("strategy", "backtest", "portfolio", "trade", "ranking", "model risk", "regime", "exposure"),
        "delivery-orchestrator-agent -> senior-trading-desk-reviewer or strategy-validation-model-risk-reviewer -> relevant implementation/QA agents",
    ),
    (
        "docs",
        ("documentation", "docs", "readme", "runbook", "developer guide"),
        "delivery-orchestrator-agent -> technical-writer-dev-advocate -> git-hygiene-orchestrator",
    ),
)


def classify(prompt: str) -> tuple[str, str]:
    normalized = f" {prompt.lower()} "
    for lane, needles, sequence in LANES:
        if any(needle in normalized for needle in needles):
            return lane, sequence
    return "implementation", "delivery-orchestrator-agent -> gateway-bookkeeper when tracked -> delivery-engineer-agent -> qa-release-gate-agent -> git-hygiene-orchestrator"


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
    context = "\n".join(
        [
            "Codex team workflow routing:",
            f"- Classified lane: {lane}",
            f"- Recommended agent sequence: {sequence}",
            "- Start substantive task execution with delivery-orchestrator-agent.",
            "- Use gateway-bookkeeper before and after auditable, multi-repo, PR, CI/CD, deployment, or Azure DevOps work.",
            "- If the prompt asks to finish, commit, push, open a PR, merge, or complete workflows, run the git-hygiene-orchestrator finish workflow before closeout.",
            f"- Contract routing: {contract_hint(prompt)}",
        ]
    )
    return emit_json(additional_context("UserPromptSubmit", context))


if __name__ == "__main__":
    raise SystemExit(main())
