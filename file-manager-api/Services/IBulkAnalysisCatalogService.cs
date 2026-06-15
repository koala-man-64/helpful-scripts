using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public interface IBulkAnalysisCatalogService
{
    Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default);

    Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default);

    Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default);

    Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default);
}
