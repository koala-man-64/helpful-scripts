namespace FileManagerApi.Services;

public interface IBulkAnalysisDocumentConverter
{
    Task<byte[]> ConvertDocToDocxAsync(string sourceFileName, byte[] content, CancellationToken cancellationToken = default);
}
