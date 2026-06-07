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

public sealed record BulkAnalysisContextItem(
    string ResultId,
    string FolderName,
    string SopTitle,
    string AnalysisType,
    DateTime GeneratedAt,
    int EstimatedTokens)
{
    public static BulkAnalysisContextItem FromResult(BulkAnalysisResult result) =>
        new(
            result.Id,
            result.FolderName,
            result.SopTitle,
            result.AnalysisType,
            result.GeneratedAt,
            result.EstimatedTokens);
}
