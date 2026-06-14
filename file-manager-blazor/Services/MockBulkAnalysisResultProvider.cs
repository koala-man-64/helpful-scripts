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
                                        ]),
                                    CreateDocument(
                                        "provider-attachments",
                                        "claims-front-door",
                                        "Provider Attachment Requirements",
                                        "provider-attachment-requirements.pdf",
                                        [
                                            Result("provider-attachments-summary", "claims-front-door", "provider-attachments", "Front Door Intake", "Provider Attachment Requirements", "provider-attachment-requirements.pdf", "Executive Summary", BaseDate.AddHours(-5), ProviderAttachmentSummary()),
                                            Result("provider-attachments-controls", "claims-front-door", "provider-attachments", "Front Door Intake", "Provider Attachment Requirements", "provider-attachment-requirements.pdf", "Control Review", BaseDate.AddHours(-6), ProviderAttachmentControls())
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
                                        ]),
                                    CreateDocument(
                                        "appeal-evidence-checklist",
                                        "claims-appeals",
                                        "Appeal Evidence Checklist",
                                        "appeal-evidence-checklist.docx",
                                        [
                                            Result("appeal-evidence-summary", "claims-appeals", "appeal-evidence-checklist", "Appeals and Disputes", "Appeal Evidence Checklist", "appeal-evidence-checklist.docx", "Executive Summary", BaseDate.AddDays(-2).AddHours(-4), AppealEvidenceSummary()),
                                            Result("appeal-evidence-risk", "claims-appeals", "appeal-evidence-checklist", "Appeals and Disputes", "Appeal Evidence Checklist", "appeal-evidence-checklist.docx", "Risk Review", BaseDate.AddDays(-3), AppealEvidenceRisk())
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
                                        ]),
                                    CreateDocument(
                                        "notice-timing-matrix",
                                        "member-notices",
                                        "Notice Timing Matrix",
                                        "notice-timing-matrix.pdf",
                                        [
                                            Result("notice-timing-review", "member-notices", "notice-timing-matrix", "Notice Operations", "Notice Timing Matrix", "notice-timing-matrix.pdf", "Timing Review", BaseDate.AddDays(-8).AddHours(-4), NoticeTimingReview()),
                                            Result("notice-timing-compliance", "member-notices", "notice-timing-matrix", "Notice Operations", "Notice Timing Matrix", "notice-timing-matrix.pdf", "Compliance Review", BaseDate.AddDays(-9), NoticeTimingCompliance())
                                        ])
                                ]),
                            CreateFolder(
                                "member-typography-regression",
                                "Typography Regression Evidence With Long Folder Names",
                                "Member Communications",
                                [
                                    CreateDocument(
                                        "member-notice-typography-regression",
                                        "member-typography-regression",
                                        "Member Notice Typography Regression Review With Long Labels And Dense Metadata",
                                        "member-notice-typography-regression-source-file-with-extra-long-name-for-preview-wrapping.txt",
                                        [
                                            Result(
                                                "member-notice-typography-regression-report",
                                                "member-typography-regression",
                                                "member-notice-typography-regression",
                                                "Typography Regression Evidence With Long Folder Names",
                                                "Member Notice Typography Regression Review With Long Labels And Dense Metadata",
                                                "member-notice-typography-regression-source-file-with-extra-long-name-for-preview-wrapping.txt",
                                                "Typography Regression Review",
                                                BaseDate.AddDays(-9).AddHours(-3),
                                                TypographyRegressionReview())
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
                                        ]),
                                    CreateDocument(
                                        "peer-review-escalation",
                                        "clinical-review",
                                        "Peer Review Escalation Guide",
                                        "peer-review-escalation-guide.docx",
                                        [
                                            Result("peer-review-escalation-summary", "clinical-review", "peer-review-escalation", "Clinical Review", "Peer Review Escalation Guide", "peer-review-escalation-guide.docx", "Executive Summary", BaseDate.AddDays(-5).AddHours(-2), PeerReviewEscalationSummary()),
                                            Result("peer-review-controls", "clinical-review", "peer-review-escalation", "Clinical Review", "Peer Review Escalation Guide", "peer-review-escalation-guide.docx", "Decision Controls", BaseDate.AddDays(-5).AddHours(-4), PeerReviewDecisionControls())
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
                                        ]),
                                    CreateDocument(
                                        "pa-audit-sampling",
                                        "authorization-data",
                                        "Authorization Audit Sampling Plan",
                                        "authorization-audit-sampling-plan.pdf",
                                        [
                                            Result("pa-audit-sampling-review", "authorization-data", "pa-audit-sampling", "Authorization Data Controls", "Authorization Audit Sampling Plan", "authorization-audit-sampling-plan.pdf", "Audit Sampling Review", BaseDate.AddDays(-6).AddHours(-3), AuthorizationAuditSamplingReview()),
                                            Result("pa-audit-sampling-recommendations", "authorization-data", "pa-audit-sampling", "Authorization Data Controls", "Authorization Audit Sampling Plan", "authorization-audit-sampling-plan.pdf", "Recommendations", BaseDate.AddDays(-7), AuthorizationAuditSamplingRecommendations())
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
        new(id, displayName, GetFolderDescription(displayName), parentDisplayName, documents, childFolders ?? []);

    private static string GetFolderDescription(string displayName) =>
        $"Documents and generated analysis results for {displayName}.";

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

    private static string ProviderAttachmentSummary() =>
        """
        # Provider Attachment Requirements - Executive Summary

        The attachment requirements are clear enough for routine submissions, but the workflow depends on staff recognizing incomplete packets before case creation.

        ## Strengths

        - Lists required clinical and billing artifacts by claim type.
        - Separates provider-submitted files from internal research notes.
        - Gives intake staff a visible path for missing attachment outreach.
        """;

    private static string ProviderAttachmentControls() =>
        """
        # Provider Attachment Requirements - Control Review

        ## Control Observations

        | Control | Current State | Recommended Change |
        | --- | --- | --- |
        | Attachment completeness | Defined by claim type | Add a required checklist before submit |
        | Late document receipt | Routed manually | Add received-date capture |
        | Duplicate attachments | Reviewed during intake | Add duplicate-file warning |

        ## Recommendation

        Treat attachment completeness as a pre-routing control so downstream adjudication does not discover missing evidence after the claim ages.
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

    private static string AppealEvidenceSummary() =>
        """
        # Appeal Evidence Checklist - Executive Summary

        The checklist gives appeal coordinators a consistent packet standard before review assignment. It is most useful for distinguishing member-provided evidence from plan records.

        ## Evidence Groups

        - Original adverse determination
        - Member or representative statement
        - Clinical records or payment documentation
        - Applicable benefit or policy language
        """;

    private static string AppealEvidenceRisk() =>
        """
        # Appeal Evidence Checklist - Risk Review

        ## Watch Items

        - The checklist does not name a backup owner when evidence collection stalls.
        - External records can arrive after reviewer assignment without a visible packet refresh.
        - Representative authorization is referenced but not tied to a required validation step.

        ## Recommendation

        Add a packet-readiness checkpoint before review assignment and another checkpoint before determination drafting.
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

    private static string PeerReviewEscalationSummary() =>
        """
        # Peer Review Escalation Guide - Executive Summary

        The escalation guide defines when nurse reviewers should route prior authorization cases to a medical director. It improves consistency for ambiguous evidence and urgent service requests.

        ## Escalation Triggers

        - Criteria conflict with documented clinical facts.
        - Urgent requests lack enough evidence for a same-day decision.
        - A provider requests peer-to-peer review.
        """;

    private static string PeerReviewDecisionControls() =>
        """
        # Peer Review Escalation Guide - Decision Controls

        ## Control Review

        Peer-review decisions should include the reviewer name, criteria used, evidence gaps, outreach attempted, and final disposition.

        ## Suggested Control

        Require a same-day note when an urgent case is not escalated, including the reason the reviewer kept the case in standard review.
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

    private static string AuthorizationAuditSamplingReview() =>
        """
        # Authorization Audit Sampling Plan - Audit Sampling Review

        The sampling plan gives operations a repeatable way to inspect prior authorization record quality without reviewing every case.

        ## Sample Design

        - Stratify by urgent, standard, approved, and denied outcomes.
        - Include reopened cases and records with late attachments.
        - Compare reviewer notes against source evidence and outbound notices.
        """;

    private static string AuthorizationAuditSamplingRecommendations() =>
        """
        # Authorization Audit Sampling Plan - Recommendations

        ## Recommended Changes

        - Define the monthly sample size and replacement rule.
        - Add a severity scale for documentation defects.
        - Track repeat findings by queue and reviewer role.

        ## Expected Outcome

        A consistent sample plan should make recurring data-quality problems visible before they become audit findings.
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

    private static string NoticeTimingReview() =>
        """
        # Notice Timing Matrix - Timing Review

        The timing matrix maps notice deadlines to decision categories and receipt channels. It is useful for selecting the correct production path before the notice is drafted.

        ## Timing Inputs

        - Case type and urgency
        - Decision date
        - Member communication preference
        - Required outbound notice channel
        """;

    private static string NoticeTimingCompliance() =>
        """
        # Notice Timing Matrix - Compliance Review

        ## Findings

        The matrix centralizes key timing rules, but two rows rely on external policy notes for exceptions.

        ## Recommendation

        Add exception text directly in the matrix for urgent extensions and corrected notices so staff do not have to reconcile timing from multiple sources.
        """;

    private static string TypographyRegressionReview() =>
        """
        # Member Notice Typography Regression Review With Long Labels And Dense Metadata

        ## Findings

        This result is intentionally shaped to exercise long generated headings, dense metadata, tables, and code-style content in the Bulk Analysis preview.

        | Text Role | Regression Input | Expected Behavior |
        | --- | --- | --- |
        | Folder path | Member Communications / Typography Regression Evidence With Long Folder Names | Wrap or truncate without overlapping controls |
        | Source file | member-notice-typography-regression-source-file-with-extra-long-name-for-preview-wrapping.txt | Preserve readable metadata sizing |
        | Analysis type | Typography Regression Review | Keep action and selection rows aligned |

        ## Code Sample

        ```text
        WHEN preview_text_role == "metadata"
        THEN enforce_minimum_readable_size = true
        AND preserve_monospace_only_for_code = true
        ```

        ## Recommendation

        Keep generated report copy at the Markdown body scale and reserve smaller type for labels, counts, and secondary metadata only.
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
