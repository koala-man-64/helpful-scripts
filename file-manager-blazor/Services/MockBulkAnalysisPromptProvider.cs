using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class MockBulkAnalysisPromptProvider : IBulkAnalysisPromptProvider
{
    private static readonly IReadOnlyList<BulkAnalysisPrompt> Prompts =
    [
        new(
            "executive-synthesis",
            "summary",
            "Executive Synthesis",
            "Synthesize selected results into an executive-ready readout.",
            "Review the selected Bulk Analysis results as context. Produce an executive synthesis with the common themes, important differences, decisions needed, recommended actions, and evidence gaps that require follow-up.",
            [ "contextual-analysis", "summary", "executive" ],
            Version: "1.0.0"),
        new(
            "risk-control-readout",
            "risk-analysis",
            "Risk Control Readout",
            "Identify priority risks, controls, owners, and mitigations.",
            "Use the selected Bulk Analysis results as source context. Identify the highest-priority risks, existing or missing controls, likely control owners, suggested mitigations, and the evidence that supports each finding.",
            [ "contextual-analysis", "risk", "controls" ],
            Version: "1.0.0"),
        new(
            "evidence-gap-review",
            "gap-analysis",
            "Evidence Gap Review",
            "Find missing evidence and unresolved questions across selected results.",
            "Using the selected Bulk Analysis results, list missing evidence, unresolved questions, ambiguous ownership, and the smallest next action needed to close each gap.",
            [ "contextual-analysis", "evidence", "gaps" ],
            Version: "1.0.0"),
        new(
            "compliance-obligation-crosswalk",
            "compliance-review",
            "Compliance Obligation Crosswalk",
            "Map compliance obligations to concerns, workflows, and proof points.",
            "Review the selected Bulk Analysis results and create a compliance obligation crosswalk. For each obligation or concern, identify the impacted workflow, supporting proof points, missing evidence, risk level, and recommended next action.",
            [ "contextual-analysis", "compliance", "obligations" ],
            Version: "1.0.0"),
        new(
            "control-owner-action-plan",
            "control-review",
            "Control Owner Action Plan",
            "Convert findings into owner, action, due date, and control-test format.",
            "Use the selected Bulk Analysis results to draft a control owner action plan. Include each finding, accountable owner or owner role, action required, suggested due date priority, control test, and evidence needed for closure.",
            [ "contextual-analysis", "controls", "action-plan" ],
            Version: "1.0.0"),
        new(
            "operational-impact-brief",
            "operational-impact",
            "Operational Impact Brief",
            "Summarize process impact, affected teams, dependencies, and rollout risk.",
            "Analyze the selected Bulk Analysis results and produce an operational impact brief. Summarize affected processes, impacted teams, dependencies, likely rollout risks, sequencing concerns, and the checks needed before implementation.",
            [ "contextual-analysis", "operations", "impact" ],
            Version: "1.0.0"),
        new(
            "implementation-brief",
            "recommendations",
            "Implementation Brief",
            "Convert selected results into a practical delivery brief.",
            "Analyze the selected Bulk Analysis results and draft an implementation brief with recommended delivery steps, dependencies, decisions needed, open questions, and a short validation checklist.",
            [ "contextual-analysis", "recommendations", "delivery" ],
            Version: "1.0.0"),
        new(
            "data-quality-reconciliation",
            "data-quality-review",
            "Data Quality Reconciliation",
            "Identify data defects, reconciliation checks, root causes, and remediation priorities.",
            "Use the selected Bulk Analysis results to identify data defects, reconciliation checks, likely root causes, remediation priorities, affected downstream processes, and evidence needed to confirm each issue is resolved.",
            [ "contextual-analysis", "data-quality", "reconciliation" ],
            Version: "1.0.0")
    ];

    public Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(Prompts);
}
