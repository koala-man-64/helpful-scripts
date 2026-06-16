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
            """
            Role: Act as an executive analyst reviewing the selected Bulk Analysis results as the only source material.

            Task: Produce a decision-ready executive synthesis. Identify the dominant themes, meaningful differences between documents or analysis types, the highest-impact findings, the decisions that need leadership attention, recommended next actions, and evidence gaps that require follow-up.

            Source handling: Ground every substantive claim in the selected results. Do not invent facts, owners, dates, metrics, risks, or conclusions that are not supported by the source context. When evidence is partial, outdated, ambiguous, or conflicting, say so explicitly.

            Output format:
            1. Executive takeaway with 3-5 bullets.
            2. Common themes and notable differences.
            3. Priority findings with supporting evidence.
            4. Decisions needed and recommended actions.
            5. Evidence gaps, open questions, and follow-up items.

            Style: Be concise but complete, use business-ready language, and separate facts from recommendations.
            """,
            [ "contextual-analysis", "summary", "executive" ],
            Version: "1.1.0"),
        new(
            "risk-control-readout",
            "risk-analysis",
            "Risk Control Readout",
            "Identify priority risks, controls, owners, and mitigations.",
            """
            Role: Act as a risk and controls reviewer using the selected Bulk Analysis results as source evidence.

            Task: Identify the highest-priority risks, the controls that appear to exist, the controls that are missing or weak, likely accountable owner roles, recommended mitigations, and the evidence supporting each finding.

            Source handling: Treat the selected results as the authority. Do not assume a control exists unless it is stated or directly evidenced. If ownership, severity, or mitigation priority must be inferred, label it as an inference and explain why.

            Output format:
            1. Risk control summary with the most important 3-5 observations.
            2. Risk register with risk, evidence, impact, likelihood or priority, existing controls, control gaps, likely owner role, mitigation, and evidence needed for closure.
            3. Cross-cutting control weaknesses or repeated patterns.
            4. Immediate actions, near-term actions, and longer-term control improvements.
            5. Open questions and missing proof points.

            Style: Be specific, audit-friendly, and action-oriented.
            """,
            [ "contextual-analysis", "risk", "controls" ],
            Version: "1.1.0"),
        new(
            "evidence-gap-review",
            "gap-analysis",
            "Evidence Gap Review",
            "Find missing evidence and unresolved questions across selected results.",
            """
            Role: Act as an evidence reviewer responsible for turning the selected Bulk Analysis results into a clear gap register.

            Task: Identify missing evidence, unresolved questions, ambiguous ownership, conflicting findings, weak assumptions, and incomplete analysis threads. For each gap, explain why it matters and the smallest practical action needed to close it.

            Source handling: Use only the selected results and clearly distinguish observed facts from inferred gaps. Do not treat absence of evidence as proof that something failed; describe the evidence needed to confirm the point.

            Output format:
            1. Short gap summary by theme.
            2. Gap register with topic, available evidence, missing evidence, why it matters, likely owner role, priority, smallest next action, and expected closure artifact.
            3. Questions that require stakeholder input.
            4. Questions that require document, data, or system evidence.
            5. Items that can be closed now based on the selected context.

            Style: Be precise, practical, and focused on closure.
            """,
            [ "contextual-analysis", "evidence", "gaps" ],
            Version: "1.1.0"),
        new(
            "compliance-obligation-crosswalk",
            "compliance-review",
            "Compliance Obligation Crosswalk",
            "Map compliance obligations to concerns, workflows, and proof points.",
            """
            Role: Act as a compliance analyst creating a traceable obligation and concern crosswalk from the selected Bulk Analysis results.

            Task: Map each stated compliance obligation, policy concern, control expectation, or regulatory risk signal to the impacted workflow, supporting proof points, missing evidence, risk level, owner role, and recommended next action.

            Source handling: Do not create legal obligations that are not present in the selected results. If a concern appears compliance-relevant but the applicable rule, policy, jurisdiction, or standard is unclear, flag it for formal interpretation instead of overstating the conclusion.

            Output format:
            1. Compliance summary with the most material obligations or concerns.
            2. Crosswalk table with obligation or concern, source evidence, impacted workflow, control or proof required, current evidence, missing evidence, risk level, owner role, and next action.
            3. Items requiring legal, compliance, or policy-owner review.
            4. Evidence collection plan ordered by urgency.
            5. Residual ambiguity and assumptions.

            Style: Be careful, evidence-led, and suitable for review by compliance owners.
            """,
            [ "contextual-analysis", "compliance", "obligations" ],
            Version: "1.1.0"),
        new(
            "control-owner-action-plan",
            "control-review",
            "Control Owner Action Plan",
            "Convert findings into owner, action, due date, and control-test format.",
            """
            Role: Act as a control remediation lead preparing an owner-ready action plan from the selected Bulk Analysis results.

            Task: Convert findings into clear remediation actions. For each finding, identify the control objective, accountable owner or owner role, required action, suggested due-date priority, dependencies, control test, acceptance criteria, and evidence needed for closure.

            Source handling: Use named owners only when the selected results provide them; otherwise assign an owner role. Do not invent due dates. Express timing as priority or sequencing unless the source context includes specific dates.

            Output format:
            1. Action plan summary with the highest-priority owner actions.
            2. Owner action table with finding, evidence, control objective, owner role, action required, priority, dependency, control test, acceptance criteria, and closure evidence.
            3. Quick wins versus actions that need planning or approval.
            4. Risks if actions are delayed.
            5. Follow-up questions for owners.

            Style: Make the plan concrete enough that owners can execute without additional interpretation.
            """,
            [ "contextual-analysis", "controls", "action-plan" ],
            Version: "1.1.0"),
        new(
            "operational-impact-brief",
            "operational-impact",
            "Operational Impact Brief",
            "Summarize process impact, affected teams, dependencies, and rollout risk.",
            """
            Role: Act as an operations readiness analyst assessing the selected Bulk Analysis results for delivery and run-state impact.

            Task: Produce an operational impact brief that explains affected processes, impacted teams or owner roles, upstream and downstream dependencies, rollout risks, sequencing concerns, failure modes, readiness checks, and post-implementation monitoring needs.

            Source handling: Ground the analysis in the selected results. If a team, dependency, or failure mode is inferred rather than stated, label it as an inference and explain the evidence behind it.

            Output format:
            1. Operational impact summary with the biggest readiness concerns.
            2. Impact map covering process, team or role, dependency, expected change, operational risk, and mitigation.
            3. Sequencing and rollout considerations.
            4. Pre-implementation checks and post-implementation monitoring.
            5. Open operational questions and evidence gaps.

            Style: Be practical, implementation-aware, and focused on reducing operational surprise.
            """,
            [ "contextual-analysis", "operations", "impact" ],
            Version: "1.1.0"),
        new(
            "implementation-brief",
            "recommendations",
            "Implementation Brief",
            "Convert selected results into a practical delivery brief.",
            """
            Role: Act as a delivery lead turning the selected Bulk Analysis results into an implementation brief.

            Task: Draft a practical implementation brief with goals, scope boundaries, recommended delivery steps, dependencies, sequencing, decisions needed, owner roles, open questions, validation steps, rollout considerations, and rollback or recovery notes when relevant.

            Source handling: Use only the selected results for factual claims. Label assumptions explicitly, avoid over-scoping beyond the evidence, and call out any missing inputs that could change the implementation path.

            Output format:
            1. Implementation objective and scope.
            2. Recommended delivery plan ordered by sequence.
            3. Dependencies, prerequisites, and decisions needed.
            4. Owner roles and handoffs.
            5. Validation checklist with expected evidence.
            6. Rollout, monitoring, and rollback considerations.
            7. Open questions and risks.

            Style: Make the brief executable, specific, and suitable for handoff to an implementation team.
            """,
            [ "contextual-analysis", "recommendations", "delivery" ],
            Version: "1.1.0"),
        new(
            "data-quality-reconciliation",
            "data-quality-review",
            "Data Quality Reconciliation",
            "Identify data defects, reconciliation checks, root causes, and remediation priorities.",
            """
            Role: Act as a data quality and reconciliation lead using the selected Bulk Analysis results as the source record.

            Task: Identify data defects, reconciliation breaks, completeness or freshness issues, mapping problems, likely root-cause hypotheses, remediation priorities, affected downstream processes, and the evidence needed to confirm each issue is resolved.

            Source handling: Separate confirmed defects from suspected issues. Do not assert a root cause unless it is directly supported; otherwise label it as a hypothesis and state the check needed to confirm or reject it.

            Output format:
            1. Data quality summary with the most material issues.
            2. Issue register with defect, supporting evidence, affected source or field, downstream impact, severity or priority, likely root-cause hypothesis, reconciliation check, remediation action, owner role, and verification evidence.
            3. Cross-system or recurring patterns.
            4. Recommended reconciliation tests and acceptance criteria.
            5. Open questions, missing data, and follow-up evidence.

            Style: Be exact, testable, and clear about confidence level.
            """,
            [ "contextual-analysis", "data-quality", "reconciliation" ],
            Version: "1.1.0")
    ];

    public Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(Prompts);
}
