using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class MockBulkAnalysisResultProvider : IBulkAnalysisResultProvider
{
    private static readonly DateTime BaseDate = new(2026, 6, 6, 9, 30, 0);

    private static readonly Lazy<IReadOnlyList<BulkAnalysisFolder>> Folders = new(CreateFolders);

    public Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(Folders.Value);
    }

    private static IReadOnlyList<BulkAnalysisFolder> CreateFolders()
    {
        return
        [
            CreateFolder(
                "claims-ops",
                "Claims Operations",
                null,
                [
                    CreateSop(
                        "claims-intake",
                        "claims-ops",
                        "Claims Intake SOP",
                        "claims-intake-sop.pdf",
                        [
                            Result("claims-intake-summary", "claims-ops", "claims-intake", "Claims Operations", "Claims Intake SOP", "claims-intake-sop.pdf", "Executive Summary", BaseDate.AddHours(-2), ClaimsSummary()),
                            Result("claims-intake-risk", "claims-ops", "claims-intake", "Claims Operations", "Claims Intake SOP", "claims-intake-sop.pdf", "Risk Analysis", BaseDate.AddHours(-3), ClaimsRisk()),
                            Result("claims-intake-gap", "claims-ops", "claims-intake", "Claims Operations", "Claims Intake SOP", "claims-intake-sop.pdf", "Gap Analysis", BaseDate.AddDays(-1), ClaimsGap())
                        ]),
                    CreateSop(
                        "appeals-resolution",
                        "claims-ops",
                        "Appeals Resolution SOP",
                        "appeals-resolution.docx",
                        [
                            Result("appeals-summary", "claims-ops", "appeals-resolution", "Claims Operations", "Appeals Resolution SOP", "appeals-resolution.docx", "Executive Summary", BaseDate.AddDays(-2), AppealsSummary()),
                            Result("appeals-compliance", "claims-ops", "appeals-resolution", "Claims Operations", "Appeals Resolution SOP", "appeals-resolution.docx", "Compliance Review", BaseDate.AddDays(-2).AddHours(-2), AppealsCompliance())
                        ])
                ]),
            CreateFolder(
                "prior-auth",
                "Prior Authorization",
                null,
                [
                    CreateSop(
                        "pa-clinical-review",
                        "prior-auth",
                        "Clinical Review SOP",
                        "clinical-review.md",
                        [
                            Result("pa-review-impact", "prior-auth", "pa-clinical-review", "Prior Authorization", "Clinical Review SOP", "clinical-review.md", "Operational Impact", BaseDate.AddDays(-4), PriorAuthImpact()),
                            Result("pa-review-recommendations", "prior-auth", "pa-clinical-review", "Prior Authorization", "Clinical Review SOP", "clinical-review.md", "Recommendations", BaseDate.AddDays(-5), PriorAuthRecommendations())
                        ]),
                    CreateSop(
                        "pa-data-quality",
                        "prior-auth",
                        "Authorization Data Quality SOP",
                        "authorization-data-quality.pdf",
                        [
                            Result("pa-data-quality", "prior-auth", "pa-data-quality", "Prior Authorization", "Authorization Data Quality SOP", "authorization-data-quality.pdf", "Data Quality Review", BaseDate.AddDays(-6), DataQualityReview())
                        ])
                ]),
            CreateFolder(
                "member-comms",
                "Member Communications",
                "Operations Library",
                [
                    CreateSop(
                        "notice-generation",
                        "member-comms",
                        "Notice Generation SOP With A Very Long Title That Must Truncate Gracefully",
                        "notice-generation-long-title.docx",
                        [
                            Result("notice-summary", "member-comms", "notice-generation", "Member Communications", "Notice Generation SOP With A Very Long Title That Must Truncate Gracefully", "notice-generation-long-title.docx", "Executive Summary", BaseDate.AddDays(-7), NoticeSummary()),
                            Result("notice-unavailable", "member-comms", "notice-generation", "Member Communications", "Notice Generation SOP With A Very Long Title That Must Truncate Gracefully", "notice-generation-long-title.docx", "Compliance Review", BaseDate.AddDays(-8), UnavailablePreviewContent(), IsPreviewAvailable: false)
                        ])
                ]),
            CreateFolder(
                "enterprise-corpus",
                "Enterprise SOP Corpus",
                "Archive",
                [
                    CreateSop(
                        "claims-corpus",
                        "enterprise-corpus",
                        "Complete Claims SOP Corpus",
                        "complete-claims-corpus.zip",
                        [
                            Result("enterprise-corpus-crosswalk", "enterprise-corpus", "claims-corpus", "Enterprise SOP Corpus", "Complete Claims SOP Corpus", "complete-claims-corpus.zip", "Full Corpus Crosswalk", BaseDate.AddDays(-10), LargeCorpusMarkdown(), IsPreviewAvailable: false)
                        ])
                ])
        ];
    }

    private static BulkAnalysisFolder CreateFolder(
        string id,
        string displayName,
        string? parentDisplayName,
        IReadOnlyList<BulkAnalysisSop> sops) =>
        new(id, displayName, parentDisplayName, sops);

    private static BulkAnalysisSop CreateSop(
        string id,
        string folderId,
        string title,
        string originalFileName,
        IReadOnlyList<BulkAnalysisResult> results) =>
        new(id, folderId, title, originalFileName, results);

    private static BulkAnalysisResult Result(
        string id,
        string folderId,
        string sopId,
        string folderName,
        string sopTitle,
        string originalFileName,
        string analysisType,
        DateTime generatedAt,
        string markdown,
        bool IsPreviewAvailable = true) =>
        new(id, folderId, sopId, folderName, sopTitle, originalFileName, analysisType, generatedAt, markdown.Trim(), IsPreviewAvailable);

    private static string ClaimsSummary() =>
        """
        # Claims Intake SOP - Executive Summary

        The claims intake workflow is stable and well documented. The SOP gives intake staff a clear path for validating member identity, capturing claim metadata, and routing urgent cases.

        ## Strengths

        - Strong front-door validation steps
        - Clear escalation trigger for urgent clinical or payment risk
        - Defined ownership between intake and downstream adjudication

        ## Context for Chat

        Use this result when asking about intake sequence, handoff accountability, or first-contact quality controls.
        """;

    private static string ClaimsRisk() =>
        """
        # Claims Intake SOP - Risk Analysis

        | Risk | Impact | Mitigation |
        | --- | --- | --- |
        | Duplicate intake records | Medium | Add duplicate search before case creation |
        | Incomplete provider data | High | Require provider identifier validation |
        | Late escalation | High | Add same-day supervisor review for urgent cases |

        ## Watch Items

        - The SOP does not define a hard SLA for incomplete submissions.
        - Manual corrections are allowed without a second-review checkpoint.
        - Training references are split across two different procedure pages.
        """;

    private static string ClaimsGap() =>
        """
        # Claims Intake SOP - Gap Analysis

        ## Missing Controls

        1. No explicit sampling requirement for intake quality.
        2. No standard disposition for cases missing a provider taxonomy.
        3. No owner named for weekly aging review.

        ## Suggested Additions

        ```text
        IF case_age > 2 business_days AND required_fields_missing
        THEN route to intake supervisor queue
        ```

        <strong>Raw HTML should not render as HTML in the preview.</strong>
        """;

    private static string AppealsSummary() =>
        """
        # Appeals Resolution SOP - Executive Summary

        The appeals process has clear case milestones and strong documentation discipline. The biggest workflow benefit is the explicit separation between evidence collection and determination drafting.

        ## Decision Milestones

        - Appeal received and classified
        - Evidence packet assembled
        - Clinical or administrative review completed
        - Determination drafted and member notice issued
        """;

    private static string AppealsCompliance() =>
        """
        # Appeals Resolution SOP - Compliance Review

        ## Findings

        The procedure aligns with standard timeliness expectations, but it relies on manual date tracking in two steps.

        ## Required Evidence

        - Date received
        - Appeal category
        - Reviewer assignment
        - Determination date
        - Member notice date

        ## Recommendation

        Add a single timeline checkpoint table to reduce inconsistent date handling.
        """;

    private static string PriorAuthImpact() =>
        """
        # Clinical Review SOP - Operational Impact

        The clinical review workflow is moderately complex and depends on complete evidence packets. The highest operational load appears around missing clinical documentation and peer-review escalation.

        ## Impact Areas

        - Nurse reviewer queue balancing
        - Medical director escalation volume
        - Turnaround-time monitoring
        - Member and provider notification sequencing
        """;

    private static string PriorAuthRecommendations() =>
        """
        # Clinical Review SOP - Recommendations

        ## Recommended Changes

        - Add a front-end completeness checklist.
        - Define a same-day escalation rule for urgent authorization requests.
        - Make peer-review ownership explicit.
        - Add a concise fallback path when clinical criteria are unavailable.

        ## Expected Outcome

        The changes should reduce rework and make urgent cases easier to identify before they age.
        """;

    private static string DataQualityReview() =>
        """
        # Authorization Data Quality SOP - Data Quality Review

        | Field | Quality Concern | Control |
        | --- | --- | --- |
        | Member ID | Typographical mismatch | Validate against eligibility source |
        | Procedure code | Missing modifier | Require code/modifier pair review |
        | Request date | Late or blank entry | Block submission without date |

        ## Summary

        The SOP has useful validation guidance but should centralize the authoritative source for each required field.
        """;

    private static string NoticeSummary() =>
        """
        # Notice Generation SOP - Executive Summary

        The notice generation workflow emphasizes plain-language consistency and a controlled review path. The SOP is useful context for questions about member-facing communication risk.

        ## Key Points

        - Templates are versioned.
        - Clinical rationale must map to approved language.
        - Member notices require a quality check before release.
        """;

    private static string UnavailablePreviewContent() =>
        """
        # Notice Generation SOP - Compliance Review

        This mock result is intentionally marked unavailable for preview so the UI can show the unavailable state while preserving selection and context-size behavior.
        """;

    private static string LargeCorpusMarkdown()
    {
        var repeatedSection = """
            ## Corpus Crosswalk Segment

            This segment summarizes intake, adjudication, appeals, member notice, and authorization controls across the enterprise SOP library. It is intentionally large so the context meter can demonstrate over-limit handling without a backend token service.

            """;

        return "# Complete Claims SOP Corpus - Full Corpus Crosswalk\n\n" +
            string.Concat(Enumerable.Repeat(repeatedSection, 1_900));
    }
}
