using System.IO.Compression;
using System.Net;
using System.Security;
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

    public Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        var result = Folders.Value
            .SelectMany(EnumerateDocuments)
            .SelectMany(document => document.Results)
            .FirstOrDefault(result => string.Equals(result.Id, resultId, StringComparison.Ordinal));

        if (result is null ||
            string.IsNullOrWhiteSpace(result.ResultFileName) ||
            string.IsNullOrWhiteSpace(result.ResultContentType) ||
            string.IsNullOrWhiteSpace(result.ResultExtension) ||
            string.IsNullOrWhiteSpace(result.ResultPath))
        {
            return Task.FromResult<BulkAnalysisResultFile?>(null);
        }

        var content = BuildResultFileContent(result, GetResultBody(result.Id));
        return Task.FromResult<BulkAnalysisResultFile?>(new(
            result.Id,
            result.ResultFileName,
            result.ResultContentType,
            result.ResultExtension,
            result.ResultPath,
            content));
    }

    public Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        var result = Folders.Value
            .SelectMany(EnumerateDocuments)
            .SelectMany(document => document.Results)
            .FirstOrDefault(result => string.Equals(result.Id, resultId, StringComparison.Ordinal));

        if (result is null || string.IsNullOrWhiteSpace(result.ResultExtension))
        {
            return Task.FromResult<BulkAnalysisResultPreview?>(null);
        }

        var body = GetResultBody(result.Id);
        var preview = BuildResultPreview(result, body);
        return Task.FromResult<BulkAnalysisResultPreview?>(preview);
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
                                            Result("claims-intake-gap", "claims-front-door", "claims-intake", "Front Door Intake", "Claims Intake Playbook", "claims-intake-playbook.pdf", "Gap Analysis", BaseDate.AddDays(-1), ClaimsGap(), ResultExtension: "pdf")
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
                                            Result("appeals-summary", "claims-appeals", "appeals-resolution", "Appeals and Disputes", "Appeals Resolution Guide", "appeals-resolution-guide.docx", "Executive Summary", BaseDate.AddDays(-2), AppealsSummary(), ResultExtension: "docx"),
                                            Result("appeals-compliance", "claims-appeals", "appeals-resolution", "Appeals and Disputes", "Appeals Resolution Guide", "appeals-resolution-guide.docx", "Compliance Review", BaseDate.AddDays(-2).AddHours(-2), AppealsCompliance(), ResultExtension: "doc")
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
                                            Result("notice-summary", "member-notices", "notice-generation", "Notice Operations", "Notice Generation Style Guide With A Very Long Title That Must Truncate Gracefully", "notice-generation-style-guide-long-title.docx", "Executive Summary", BaseDate.AddDays(-7), NoticeSummary(), ResultExtension: "html"),
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
                                                TypographyRegressionReview(),
                                                ResultExtension: "txt")
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
        string content,
        bool IsPreviewAvailable = true,
        string ResultExtension = "md")
    {
        var analysisSlug = GetResultAnalysisSlug(analysisType);
        var resultFileName = BuildResultFileName(originalFileName, analysisSlug, ResultExtension);

        return new BulkAnalysisResult(
            id,
            folderId,
            documentId,
            folderName,
            documentTitle,
            originalFileName,
            analysisType,
            generatedAt,
            IsPreviewAvailable,
            AnalysisSlug: analysisSlug,
            ResultPath: $"mock/{folderId}/llm_results/{analysisSlug}/{resultFileName}",
            ResultFileName: resultFileName,
            ResultContentType: GetContentType(resultFileName),
            ResultExtension: ResultExtension);
    }

    private static string GetContentType(string fileName) =>
        Path.GetExtension(fileName).ToLowerInvariant() switch
        {
            ".doc" => "application/msword",
            ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".htm" => "text/html;charset=utf-8",
            ".html" => "text/html;charset=utf-8",
            ".md" => "text/markdown;charset=utf-8",
            ".pdf" => "application/pdf",
            ".txt" => "text/plain;charset=utf-8",
            _ => "application/octet-stream"
        };

    private static string GetResultBody(string resultId) =>
        resultId switch
        {
            "claims-intake-summary" => ClaimsSummary(),
            "claims-intake-risk" => ClaimsRisk(),
            "claims-intake-gap" => ClaimsGap(),
            "provider-attachments-summary" => ProviderAttachmentSummary(),
            "provider-attachments-controls" => ProviderAttachmentControls(),
            "appeals-summary" => AppealsSummary(),
            "appeals-compliance" => AppealsCompliance(),
            "appeal-evidence-summary" => AppealEvidenceSummary(),
            "appeal-evidence-risk" => AppealEvidenceRisk(),
            "notice-summary" => NoticeSummary(),
            "notice-unavailable" => UnavailablePreviewContent(),
            "notice-timing-review" => NoticeTimingReview(),
            "notice-timing-compliance" => NoticeTimingCompliance(),
            "member-notice-typography-regression-report" => TypographyRegressionReview(),
            "pa-review-impact" => PriorAuthImpact(),
            "pa-review-recommendations" => PriorAuthRecommendations(),
            "peer-review-escalation-summary" => PeerReviewEscalationSummary(),
            "peer-review-controls" => PeerReviewDecisionControls(),
            "pa-data-quality" => DataQualityReview(),
            "pa-audit-sampling-review" => AuthorizationAuditSamplingReview(),
            "pa-audit-sampling-recommendations" => AuthorizationAuditSamplingRecommendations(),
            "enterprise-corpus-crosswalk" => LargeCorpusMarkdown(),
            _ => string.Empty
        };

    private static BulkAnalysisResultPreview BuildResultPreview(BulkAnalysisResult result, string body)
    {
        return result.ResultExtension switch
        {
            "doc" => new BulkAnalysisResultPreview(
                result.Id,
                Path.ChangeExtension(result.ResultFileName, ".docx") ?? $"{result.DocumentId}.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "docx",
                "Word",
                CreateDocxDocumentBytes(ConvertMarkdownToPreviewText(body))),
            "docx" => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.docx",
                result.ResultContentType ?? "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "docx",
                "Word",
                CreateDocxDocumentBytes(ConvertMarkdownToPreviewText(body))),
            "html" or "htm" => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.html",
                result.ResultContentType ?? "text/html;charset=utf-8",
                result.ResultExtension,
                "HTML",
                Encoding.UTF8.GetBytes(BuildHtmlDocument(result, body))),
            "md" => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.md",
                result.ResultContentType ?? "text/markdown;charset=utf-8",
                "md",
                "Markdown",
                Encoding.UTF8.GetBytes(StripFrontMatter(BuildStoredMarkdownContent(result, body)))),
            "pdf" => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.pdf",
                result.ResultContentType ?? "application/pdf",
                "pdf",
                "PDF",
                CreatePdfDocumentBytes(result, ConvertMarkdownToPreviewText(body))),
            "txt" => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.txt",
                result.ResultContentType ?? "text/plain;charset=utf-8",
                "txt",
                "Plain Text",
                Encoding.UTF8.GetBytes(ConvertMarkdownToPreviewText(body))),
            _ => new BulkAnalysisResultPreview(
                result.Id,
                result.ResultFileName ?? $"{result.DocumentId}.bin",
                result.ResultContentType ?? "application/octet-stream",
                result.ResultExtension ?? "bin",
                "Binary",
                BuildResultFileContent(result, body))
        };
    }

    private static byte[] BuildResultFileContent(BulkAnalysisResult result, string body) =>
        result.ResultExtension switch
        {
            "doc" => CreateLegacyWordDocumentBytes(ConvertMarkdownToPreviewText(body)),
            "docx" => CreateDocxDocumentBytes(ConvertMarkdownToPreviewText(body)),
            "html" or "htm" => Encoding.UTF8.GetBytes(BuildHtmlDocument(result, body)),
            "md" => Encoding.UTF8.GetBytes(BuildStoredMarkdownContent(result, body)),
            "pdf" => CreatePdfDocumentBytes(result, ConvertMarkdownToPreviewText(body)),
            "txt" => Encoding.UTF8.GetBytes(ConvertMarkdownToPreviewText(body)),
            _ => Encoding.UTF8.GetBytes(body)
        };

    private static string BuildResultFileName(string originalFileName, string analysisSlug, string resultExtension)
    {
        var sourceStem = Path.GetFileNameWithoutExtension(originalFileName);
        return $"{sourceStem}.{analysisSlug}.{resultExtension}";
    }

    private static string GetResultAnalysisSlug(string analysisType)
    {
        if (analysisType.Contains("summary", StringComparison.OrdinalIgnoreCase))
        {
            return "summary";
        }

        var slug = new string(analysisType
            .Trim()
            .ToLowerInvariant()
            .Select(character => char.IsLetterOrDigit(character) ? character : '-')
            .ToArray());

        while (slug.Contains("--", StringComparison.Ordinal))
        {
            slug = slug.Replace("--", "-", StringComparison.Ordinal);
        }

        return slug.Trim('-');
    }

    private static string BuildStoredMarkdownContent(BulkAnalysisResult result, string body) =>
        $"---\ntitle: {result.DocumentTitle} - {result.AnalysisType}\nformat: markdown\n---\n\n{body.Trim()}";

    private static string BuildHtmlDocument(BulkAnalysisResult result, string body) =>
        $$"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8" />
            <title>{{WebUtility.HtmlEncode(result.DocumentTitle)}} - {{WebUtility.HtmlEncode(result.AnalysisType)}}</title>
            <style>
                body { font-family: "Segoe UI", sans-serif; margin: 0; padding: 2rem; color: #132033; background: #f4f6fb; }
                main { max-width: 52rem; margin: 0 auto; padding: 1.5rem; border-radius: 1rem; background: white; box-shadow: 0 18px 40px rgba(19, 32, 51, 0.12); }
                h1 { margin-top: 0; font-size: 1.25rem; }
                pre { margin: 0; white-space: pre-wrap; font: 0.95rem/1.55 Consolas, "Courier New", monospace; }
            </style>
        </head>
        <body>
            <main>
                <h1>{{WebUtility.HtmlEncode(result.AnalysisType)}}</h1>
                <pre>{{WebUtility.HtmlEncode(ConvertMarkdownToPreviewText(body))}}</pre>
            </main>
        </body>
        </html>
        """;

    private static string ConvertMarkdownToPreviewText(string value)
    {
        var normalized = NormalizeLineEndings(value);
        var lines = normalized
            .Split('\n')
            .Where(line => !line.StartsWith("---", StringComparison.Ordinal))
            .Select(line =>
            {
                var trimmed = line.TrimEnd();
                if (trimmed.StartsWith("```", StringComparison.Ordinal))
                {
                    return string.Empty;
                }

                return trimmed.TrimStart('#', ' ');
            });

        return string.Join(Environment.NewLine, lines).Trim();
    }

    private static string StripFrontMatter(string markdown)
    {
        markdown = NormalizeLineEndings(markdown);

        if (!markdown.StartsWith("---\n", StringComparison.Ordinal))
        {
            return markdown.Trim();
        }

        var end = markdown.IndexOf("\n---\n", 4, StringComparison.Ordinal);
        return end < 0 ? markdown.Trim() : markdown[(end + 5)..].Trim();
    }

    private static string NormalizeLineEndings(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');

    private static byte[] CreateLegacyWordDocumentBytes(string body)
    {
        var escaped = body
            .Replace(@"\", @"\\", StringComparison.Ordinal)
            .Replace("{", @"\{", StringComparison.Ordinal)
            .Replace("}", @"\}", StringComparison.Ordinal)
            .Replace("\r\n", @"\par ", StringComparison.Ordinal)
            .Replace("\n", @"\par ", StringComparison.Ordinal);

        return Encoding.ASCII.GetBytes(@"{\rtf1\ansi\deff0 {\fonttbl{\f0 Calibri;}}\fs22 " + escaped + "}");
    }

    private static byte[] CreateDocxDocumentBytes(string body)
    {
        using var stream = new MemoryStream();
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
        {
            WriteZipEntry(archive, "[Content_Types].xml",
                """
                <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
                  <Default Extension="xml" ContentType="application/xml" />
                  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" />
                </Types>
                """);
            WriteZipEntry(archive, "_rels/.rels",
                """
                <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml" />
                </Relationships>
                """);
            WriteZipEntry(archive, "word/document.xml", BuildDocxDocumentXml(body));
        }

        return stream.ToArray();
    }

    private static string BuildDocxDocumentXml(string body)
    {
        var paragraphs = NormalizeLineEndings(body)
            .Split('\n')
            .Select(line => line.TrimEnd())
            .Where(line => line.Length > 0)
            .Select(line => $"<w:p><w:r><w:t xml:space=\"preserve\">{SecurityElement.Escape(line)}</w:t></w:r></w:p>");

        return $$"""
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            {{string.Join(string.Empty, paragraphs)}}
            <w:sectPr>
              <w:pgSz w:w="12240" w:h="15840" />
              <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0" />
            </w:sectPr>
          </w:body>
        </w:document>
        """;
    }

    private static byte[] CreatePdfDocumentBytes(BulkAnalysisResult result, string body)
    {
        var lines = NormalizeLineEndings($"{result.AnalysisType}\n{body}")
            .Split('\n')
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Take(28)
            .ToArray();

        var streamBuilder = new StringBuilder();
        streamBuilder.AppendLine("BT");
        streamBuilder.AppendLine("/F1 11 Tf");
        streamBuilder.AppendLine("50 760 Td");
        streamBuilder.AppendLine("14 TL");

        foreach (var line in lines)
        {
            streamBuilder.Append('(')
                .Append(EscapePdfText(line))
                .AppendLine(") Tj");
            streamBuilder.AppendLine("T*");
        }

        streamBuilder.AppendLine("ET");
        var pageStream = streamBuilder.ToString();

        var objects = new[]
        {
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
            "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
            "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
            $"5 0 obj << /Length {Encoding.ASCII.GetByteCount(pageStream)} >> stream\n{pageStream}endstream\nendobj\n"
        };

        var builder = new StringBuilder("%PDF-1.4\n");
        var offsets = new List<int>();
        foreach (var pdfObject in objects)
        {
            offsets.Add(Encoding.ASCII.GetByteCount(builder.ToString()));
            builder.Append(pdfObject);
        }

        var xrefOffset = Encoding.ASCII.GetByteCount(builder.ToString());
        builder.AppendLine("xref");
        builder.AppendLine($"0 {objects.Length + 1}");
        builder.AppendLine("0000000000 65535 f ");
        foreach (var offset in offsets)
        {
            builder.AppendLine($"{offset:D10} 00000 n ");
        }

        builder.AppendLine($"trailer << /Size {objects.Length + 1} /Root 1 0 R >>");
        builder.AppendLine("startxref");
        builder.AppendLine(xrefOffset.ToString());
        builder.Append("%%EOF");

        return Encoding.ASCII.GetBytes(builder.ToString());
    }

    private static string EscapePdfText(string value) =>
        value.Replace(@"\", @"\\", StringComparison.Ordinal)
            .Replace("(", @"\(", StringComparison.Ordinal)
            .Replace(")", @"\)", StringComparison.Ordinal);

    private static void WriteZipEntry(ZipArchive archive, string name, string content)
    {
        var entry = archive.CreateEntry(name);
        using var writer = new StreamWriter(entry.Open(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        writer.Write(content);
    }

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
