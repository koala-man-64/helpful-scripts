namespace FileManagerBlazor.Models;

public sealed record BulkAnalysisFolder(
    string Id,
    string DisplayName,
    string? ParentDisplayName,
    IReadOnlyList<BulkAnalysisDocument> Documents,
    IReadOnlyList<BulkAnalysisFolder> ChildFolders)
{
    public int DocumentCount => Documents.Count + ChildFolders.Sum(folder => folder.DocumentCount);

    public int ResultCount =>
        Documents.Sum(document => document.Results.Count) +
        ChildFolders.Sum(folder => folder.ResultCount);
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
