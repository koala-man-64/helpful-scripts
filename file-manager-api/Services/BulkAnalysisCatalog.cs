using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public sealed record AdlsPathItem(string Name, bool IsDirectory, DateTimeOffset LastModified);

public sealed record RawFileReference(string SourcePath, string FileName, string ContentType);

public sealed record ResultFileReference(
    string ResultPath,
    string FileName,
    string ContentType,
    string FileExtension);

public sealed record BulkAnalysisCatalog(
    IReadOnlyList<BulkAnalysisFolder> Folders,
    IReadOnlyDictionary<string, RawFileReference> RawFilesByDocumentId,
    IReadOnlyDictionary<string, ResultFileReference> ResultFilesById);
