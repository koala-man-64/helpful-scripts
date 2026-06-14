namespace FileManagerBlazor.Models;

public sealed record BulkAnalysisFolder(
    string Id,
    string DisplayName,
    string Description,
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
    IReadOnlyList<BulkAnalysisResult> Results,
    string? SourcePath = null,
    string? ContentType = null,
    string? SourceExtension = null,
    string? TransformedPath = null);

public sealed record BulkAnalysisResult(
    string Id,
    string FolderId,
    string DocumentId,
    string FolderName,
    string DocumentTitle,
    string OriginalFileName,
    string AnalysisType,
    DateTime GeneratedAt,
    bool IsPreviewAvailable = true,
    string? AnalysisSlug = null,
    string? ResultPath = null,
    string? ResultFileName = null,
    string? ResultContentType = null,
    string? ResultExtension = null);

public sealed record BulkAnalysisRawFile(
    string DocumentId,
    string FileName,
    string ContentType,
    string SourcePath,
    byte[] Content);

public sealed record BulkAnalysisResultFile(
    string ResultId,
    string FileName,
    string ContentType,
    string FileExtension,
    string SourcePath,
    byte[] Content);

public sealed record BulkAnalysisResultPreview(
    string ResultId,
    string FileName,
    string ContentType,
    string FileExtension,
    string Format,
    byte[] Content);
