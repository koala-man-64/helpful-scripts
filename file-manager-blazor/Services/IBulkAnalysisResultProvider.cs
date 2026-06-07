using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public interface IBulkAnalysisResultProvider
{
    Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default);
}
