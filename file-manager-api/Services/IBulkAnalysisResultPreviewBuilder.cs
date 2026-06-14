using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public interface IBulkAnalysisResultPreviewBuilder
{
    Task<BulkAnalysisResultPreview> BuildAsync(
        string resultId,
        ResultFileReference reference,
        byte[] content,
        CancellationToken cancellationToken = default);
}
