using System.Text;
using FileManagerBlazor.Data;
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

    public Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        var document = Folders.Value
            .SelectMany(EnumerateDocuments)
            .FirstOrDefault(document => string.Equals(document.Id, documentId, StringComparison.Ordinal));

        if (document is null)
        {
            return Task.FromResult<BulkAnalysisRawFile?>(null);
        }

        var content = MockFileData.GetContent(document.OriginalFileName, AnalysisType.Original);
        var bytes = Encoding.UTF8.GetBytes(content);

        return Task.FromResult<BulkAnalysisRawFile?>(new(
            document.Id,
            document.OriginalFileName,
            GetContentType(document.OriginalFileName),
            document.SourcePath ?? document.OriginalFileName,
            bytes));
    }

    public Task<string?> GetResultMarkdownAsync(string resultId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        var result = Folders.Value
            .SelectMany(EnumerateDocuments)
            .SelectMany(document => document.Results)
            .FirstOrDefault(result => string.Equals(result.Id, resultId, StringComparison.Ordinal));

        return Task.FromResult(result?.Markdown);
    }

    private static IReadOnlyList<BulkAnalysisFolder> CreateFolders()
    {
        return
        [
            CreateFolder(
                "operations-library",
                "Operations Library",
                null,
                [],
                [
                    CreateFolder(
                        "claims-ops",
                        "Claims Operations",
                        "Operations Library",
                        [],
                        [
                            CreateFolder(
                                "claims-front-door",
                                "Front Door Intake",
                                "Claims Operations",
                                [
                                    CreateDocument(
                                        "claims-intake",
                                        "claims-front-door",
                                        "Claims Intake Playbook",
                                        "claims-intake-playbook.pdf",
                                        [
                                            Result("claims-intake-summary", "claims-front-door", "claims-intake", "Front Door Intake", "Claims Intake Playbook", "claims-intake-playbook.pdf", "Executive Summary", BaseDate.AddHours(-2), ClaimsSummary()),
                                            Result("claims-intake-risk", "claims-front-door", "claims-intake", "Front Door Intake", "Claims Intake Playbook", "claims-intake-playbook.pdf", "Risk Analysis", BaseDate.AddHours(-3), ClaimsRisk()),
                                            Result("claims-intake-gap", "claims-front-door", "claims-intake", "Front Door Intake", "Claims Intake Playbook", "claims-intake-playbook.pdf", "Gap Analysis", BaseDate.AddDays(-1), ClaimsGap())
                                        ])
                                ]),
                            CreateFolder(
                                "claims-appeals",
                                "Appeals and Disputes",
                                "Claims Operations",
                                [
                                    CreateDocument(
                                        "appeals-resolution",
                                        "claims-appeals",
                                        "Appeals Resolution Guide",
                                        "appeals-resolution-guide.docx",
                                        [
                                            Result("appeals-summary", "claims-appeals", "appeals-resolution", "Appeals and Disputes", "Appeals Resolution Guide", "appeals-resolution-guide.docx", "Executive Summary", BaseDate.AddDays(-2), AppealsSummary()),
                                            Result("appeals-compliance", "claims-appeals", "appeals-resolution", "Appeals and Disputes", "Appeals Resolution Guide", "appeals-resolution-guide.docx", "Compliance Review", BaseDate.AddDays(-2).AddHours(-2), AppealsCompliance())
                                        ])
                                ])
                        ]),
                    CreateFolder(
                        "member-comms",
                        "Member Communications",
                        "Operations Library",
                        [],
                        [
                            CreateFolder(
                                "member-notices",
                                "Notice Operations",
                                "Member Communications",
                                [
                                    CreateDocument(
                                        "notice-generation",
                                        "member-notices",
                                        "Notice Generation Style Guide With A Very Long Title That Must Truncate Gracefully",
                                        "notice-generation-style-guide-long-title.docx",
                                        [
                                            Result("notice-summary", "member-notices", "notice-generation", "Notice Operations", "Notice Generation Style Guide With A Very Long Title That Must Truncate Gracefully", "notice-generation-style-guide-long-title.docx", "Executive Summary", BaseDate.AddDays(-7), NoticeSummary()),
                                            Result("notice-unavailable", "member-notices", "notice-generation", "Notice Operations", "Notice Generation Style Guide With A Very Long Title That Must Truncate Gracefully", "notice-generation-style-guide-long-title.docx", "Compliance Review", BaseDate.AddDays(-8), UnavailablePreviewContent(), IsPreviewAvailable: false)
                                        ])
                                ])
                        ])
                ]),
            CreateFolder(
                "clinical-library",
                "Clinical Library",
                null,
                [],
                [
                    CreateFolder(
                        "prior-auth",
                        "Prior Authorization",
                        "Clinical Library",
                        [],
                        [
                            CreateFolder(
                                "clinical-review",
                                "Clinical Review",
                                "Prior Authorization",
                                [
                                    CreateDocument(
                                        "pa-clinical-review",
                                        "clinical-review",
                                        "Clinical Review Policy Brief",
                                        "clinical-review-policy.md",
                                        [
                                            Result("pa-review-impact", "clinical-review", "pa-clinical-review", "Clinical Review", "Clinical Review Policy Brief", "clinical-review-policy.md", "Operational Impact", BaseDate.AddDays(-4), PriorAuthImpact()),
                                            Result("pa-review-recommendations", "clinical-review", "pa-clinical-review", "Clinical Review", "Clinical Review Policy Brief", "clinical-review-policy.md", "Recommendations", BaseDate.AddDays(-5), PriorAuthRecommendations())
                                        ])
                                ]),
                            CreateFolder(
                                "authorization-data",
                                "Authorization Data Controls",
                                "Prior Authorization",
                                [
                                    CreateDocument(
                                        "pa-data-quality",
                                        "authorization-data",
                                        "Authorization Data Quality Checklist",
                                        "authorization-data-quality-checklist.pdf",
                                        [
                                            Result("pa-data-quality", "authorization-data", "pa-data-quality", "Authorization Data Controls", "Authorization Data Quality Checklist", "authorization-data-quality-checklist.pdf", "Data Quality Review", BaseDate.AddDays(-6), DataQualityReview())
                                        ])
                                ])
                        ])
                ]),
            CreateFolder(
                "archive",
                "Archive",
                null,
                [],
                [
                    CreateFolder(
                        "enterprise-corpus",
                        "Enterprise Document Corpus",
                        "Archive",
                        [],
                        [
                            CreateFolder(
                                "claims-corpus-archive",
                                "Claims Corpus Archive",
                                "Enterprise Document Corpus",
                                [
                                    CreateDocument(
                                        "claims-corpus",
                                        "claims-corpus-archive",
                                        "Complete Claims Document Corpus",
                                        "complete-claims-corpus.zip",
                                        [
                                            Result("enterprise-corpus-crosswalk", "claims-corpus-archive", "claims-corpus", "Claims Corpus Archive", "Complete Claims Document Corpus", "complete-claims-corpus.zip", "Full Corpus Crosswalk", BaseDate.AddDays(-10), LargeCorpusMarkdown(), IsPreviewAvailable: false)
                                        ])
                                ])
                        ])
                ])
        ];
    }

    private static BulkAnalysisFolder CreateFolder(
        string id,
        string displayName,
        string? parentDisplayName,
        IReadOnlyList<BulkAnalysisDocument> documents,
        IReadOnlyList<BulkAnalysisFolder>? childFolders = null) =>
        new(id, displayName, parentDisplayName, documents, childFolders ?? []);

    private static IEnumerable<BulkAnalysisDocument> EnumerateDocuments(BulkAnalysisFolder folder)
    {
        foreach (var document in folder.Documents)
        {
            yield return document;
        }

        foreach (var childFolder in folder.ChildFolders)
        {
            foreach (var document in EnumerateDocuments(childFolder))
            {
                yield return document;
            }
        }
    }

    private static BulkAnalysisDocument CreateDocument(
        string id,
        string folderId,
        string title,
        string originalFileName,
        IReadOnlyList<BulkAnalysisResult> results) =>
        new(id, folderId, title, originalFileName, results);

    private static BulkAnalysisResult Result(
        string id,
        string folderId,
        string documentId,
        string folderName,
        string documentTitle,
        string originalFileName,
        string analysisType,
        DateTime generatedAt,
        string markdown,
        bool IsPreviewAvailable = true) =>
        new(id, folderId, documentId, folderName, documentTitle, originalFileName, analysisType, generatedAt, markdown.Trim(), IsPreviewAvailable);

    private static string GetContentType(string fileName) =>
        Path.GetExtension(fileName).ToLowerInvariant() switch
        {
            ".pdf" => "application/pdf",
            ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md" => "text/markdown;charset=utf-8",
            ".txt" => "text/plain;charset=utf-8",
            _ => "application/octet-stream"
        };

    private static string ClaimsSummary() =>
        """
        # Claims Intake Playbook - Executive Summary

        The claims intake workflow is stable and well documented. The source document gives intake staff a clear path for validating member identity, capturing claim metadata, and routing urgent cases.

        ## Strengths

        - Strong front-door validation steps
        - Clear escalation trigger for urgent clinical or payment risk
        - Defined ownership between intake and downstream adjudication

        ## Context for Chat

        Use this result when asking about intake sequence, handoff accountability, or first-contact quality controls.
        """;

    private static string ClaimsRisk() =>
        """
        # Claims Intake Playbook - Risk Analysis

        | Risk | Impact | Mitigation |
        | --- | --- | --- |
        | Duplicate intake records | Medium | Add duplicate search before case creation |
        | Incomplete provider data | High | Require provider identifier validation |
        | Late escalation | High | Add same-day supervisor review for urgent cases |

        ## Watch Items

        - The document does not define a hard SLA for incomplete submissions.
        - Manual corrections are allowed without a second-review checkpoint.
        - Training references are split across two different documentation pages.
        """;

    private static string ClaimsGap() =>
        """
        # Claims Intake Playbook - Gap Analysis

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
        # Appeals Resolution Guide - Executive Summary

        The appeals process has clear case milestones and strong documentation discipline. The biggest workflow benefit is the explicit separation between evidence collection and determination drafting.

        ## Decision Milestones

        - Appeal received and classified
        - Evidence packet assembled
        - Clinical or administrative review completed
        - Determination drafted and member notice issued
        """;

    private static string AppealsCompliance() =>
        """
        # Appeals Resolution Guide - Compliance Review

        ## Findings

        The guide aligns with standard timeliness expectations, but it relies on manual date tracking in two steps.

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
        # Clinical Review Policy Brief - Operational Impact

        The clinical review workflow is moderately complex and depends on complete evidence packets. The highest operational load appears around missing clinical documentation and peer-review escalation.

        ## Impact Areas

        - Nurse reviewer queue balancing
        - Medical director escalation volume
        - Turnaround-time monitoring
        - Member and provider notification sequencing
        """;

    private static string PriorAuthRecommendations() =>
        """
        # Clinical Review Policy Brief - Recommendations

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
        # Authorization Data Quality Checklist - Data Quality Review

        | Field | Quality Concern | Control |
        | --- | --- | --- |
        | Member ID | Typographical mismatch | Validate against eligibility source |
        | Procedure code | Missing modifier | Require code/modifier pair review |
        | Request date | Late or blank entry | Block submission without date |

        ## Summary

        The checklist has useful validation guidance but should centralize the authoritative source for each required field.
        """;

    private static string NoticeSummary() =>
        """
        # Notice Generation Style Guide - Executive Summary

        The notice generation workflow emphasizes plain-language consistency and a controlled review path. The style guide is useful background for questions about member-facing communication risk.

        ## Key Points

        - Templates are versioned.
        - Clinical rationale must map to approved language.
        - Member notices require a quality check before release.
        """;

    private static string UnavailablePreviewContent() =>
        """
        # Notice Generation Style Guide - Compliance Review

        This mock result is intentionally marked unavailable for preview so the UI can show the unavailable state while preserving result selection behavior.
        """;

    private static string LargeCorpusMarkdown()
    {
        var repeatedSection = """
            ## Corpus Crosswalk Segment

            This segment summarizes intake, adjudication, appeals, member notice, and authorization controls across the enterprise document library. It is intentionally large so the preview and download flows can handle long generated results.

            """;

        return "# Complete Claims Document Corpus - Full Corpus Crosswalk\n\n" +
            string.Concat(Enumerable.Repeat(repeatedSection, 1_900));
    }
}
