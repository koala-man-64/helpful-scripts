using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class MockBulkAnalysisPromptProvider : IBulkAnalysisPromptProvider
{
    private static readonly IReadOnlyList<BulkAnalysisPrompt> Prompts =
    [
        new(
            "context-summary",
            "summary",
            "Context Summary",
            "Use selected results to produce an executive-ready synthesis.",
            "Review the selected Bulk Analysis results as context. Produce a concise synthesis that identifies the common themes, important differences, action items, and any evidence gaps that need follow-up.",
            [ "contextual-analysis", "summary" ],
            Version: "1.0.0"),
        new(
            "risk-control-review",
            "risk-review",
            "Risk And Control Review",
            "Turn selected analysis outputs into a risk and control readout.",
            "Use the selected Bulk Analysis results as source context. Identify the highest-priority risks, missing controls, control owners, suggested mitigations, and the evidence that supports each finding.",
            [ "contextual-analysis", "risk", "controls" ],
            Version: "1.0.0"),
        new(
            "implementation-brief",
            "recommendations",
            "Implementation Brief",
            "Convert selected results into a practical delivery brief.",
            "Analyze the selected Bulk Analysis results and draft an implementation brief with recommended next steps, dependencies, decisions needed, open questions, and a short validation checklist.",
            [ "contextual-analysis", "recommendations" ],
            Version: "1.0.0"),
        new(
            "evidence-gap-review",
            "gap-analysis",
            "Evidence Gap Review",
            "Find missing evidence and unresolved questions across selected results.",
            "Using the selected Bulk Analysis results, list missing evidence, unclear ownership, unresolved decisions, and the smallest next action needed to close each gap.",
            [ "contextual-analysis", "evidence", "gaps" ],
            Version: "1.0.0")
    ];

    public Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(Prompts);
}
