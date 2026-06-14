using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public interface IBulkAnalysisResultProvider
{
    Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default);

    Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default);

    Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default);

    Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default);
}
