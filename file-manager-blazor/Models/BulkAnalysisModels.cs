namespace FileManagerBlazor.Models;

public sealed record BulkAnalysisFolder(
    string Id,
    string DisplayName,
    string? ParentDisplayName,
    IReadOnlyList<BulkAnalysisSop> Sops)
{
    public int SopCount => Sops.Count;

    public int ResultCount => Sops.Sum(sop => sop.Results.Count);
}

public sealed record BulkAnalysisSop(
    string Id,
    string FolderId,
    string Title,
    string OriginalFileName,
    IReadOnlyList<BulkAnalysisResult> Results);

public sealed record BulkAnalysisResult(
    string Id,
    string FolderId,
    string SopId,
    string FolderName,
    string SopTitle,
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
    string SopTitle,
    string OriginalFileName,
    string? AnalysisType,
    DateTime? GeneratedAt,
    int EstimatedTokens)
{
    public string ResultId => Id;

    public string DisplayName =>
        Kind == BulkAnalysisContextItemKind.SourceDocument
            ? OriginalFileName
            : $"{SopTitle} - {AnalysisType}";

    public string TypeLabel =>
        Kind == BulkAnalysisContextItemKind.SourceDocument
            ? "Source document"
            : AnalysisType ?? "Analysis result";

    public static BulkAnalysisContextItem FromResult(BulkAnalysisResult result) =>
        new(
            result.Id,
            BulkAnalysisContextItemKind.AnalysisResult,
            result.FolderName,
            result.SopTitle,
            result.OriginalFileName,
            result.AnalysisType,
            result.GeneratedAt,
            result.EstimatedTokens);

    public static BulkAnalysisContextItem FromSourceDocument(BulkAnalysisFolder folder, BulkAnalysisSop sop) =>
        new(
            $"doc:{sop.Id}",
            BulkAnalysisContextItemKind.SourceDocument,
            folder.DisplayName,
            sop.Title,
            sop.OriginalFileName,
            null,
            null,
            EstimateSourceDocumentTokens(sop));

    private static int EstimateSourceDocumentTokens(BulkAnalysisSop sop)
    {
        var resultAverage = sop.Results.Count == 0
            ? 1_200
            : sop.Results.Sum(result => result.EstimatedTokens) / sop.Results.Count;

        return Math.Clamp(resultAverage * 2, 1_200, 8_000);
    }
}
