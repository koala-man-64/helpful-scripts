namespace FileManagerBlazor.Models;

public sealed record BulkAnalysisFolder(
    string Id,
    string DisplayName,
    string? ParentDisplayName,
    IReadOnlyList<BulkAnalysisDocument> Documents)
{
    public int DocumentCount => Documents.Count;

    public int ResultCount => Documents.Sum(document => document.Results.Count);
}

public sealed record BulkAnalysisDocument(
    string Id,
    string FolderId,
    string Title,
    string OriginalFileName,
    IReadOnlyList<BulkAnalysisResult> Results);

public sealed record BulkAnalysisResult(
    string Id,
    string FolderId,
    string DocumentId,
    string FolderName,
    string DocumentTitle,
    string OriginalFileName,
    string AnalysisType,
    DateTime GeneratedAt,
    string Markdown,
    bool IsPreviewAvailable = true)
{
    public int EstimatedTokens => (int)Math.Ceiling(Markdown.Length / 4.0);
}

public enum BulkAnalysisContextItemKind
{
    SourceDocument,
    AnalysisResult
}

public sealed record BulkAnalysisContextItem(
    string Id,
    BulkAnalysisContextItemKind Kind,
    string FolderName,
    string DocumentTitle,
    string OriginalFileName,
    string? AnalysisType,
    DateTime? GeneratedAt,
    int EstimatedTokens)
{
    public string ResultId => Id;

    public string DisplayName =>
        Kind == BulkAnalysisContextItemKind.SourceDocument
            ? OriginalFileName
            : $"{DocumentTitle} - {AnalysisType}";

    public string TypeLabel =>
        Kind == BulkAnalysisContextItemKind.SourceDocument
            ? "Source document"
            : AnalysisType ?? "Analysis result";

    public static BulkAnalysisContextItem FromResult(BulkAnalysisResult result) =>
        new(
            result.Id,
            BulkAnalysisContextItemKind.AnalysisResult,
            result.FolderName,
            result.DocumentTitle,
            result.OriginalFileName,
            result.AnalysisType,
            result.GeneratedAt,
            result.EstimatedTokens);

    public static BulkAnalysisContextItem FromSourceDocument(BulkAnalysisFolder folder, BulkAnalysisDocument document) =>
        new(
            $"doc:{document.Id}",
            BulkAnalysisContextItemKind.SourceDocument,
            folder.DisplayName,
            document.Title,
            document.OriginalFileName,
            null,
            null,
            EstimateSourceDocumentTokens(document));

    private static int EstimateSourceDocumentTokens(BulkAnalysisDocument document)
    {
        var resultAverage = document.Results.Count == 0
            ? 1_200
            : document.Results.Sum(result => result.EstimatedTokens) / document.Results.Count;

        return Math.Clamp(resultAverage * 2, 1_200, 8_000);
    }
}
