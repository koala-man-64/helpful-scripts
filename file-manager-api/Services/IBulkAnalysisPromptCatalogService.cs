using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public interface IBulkAnalysisPromptCatalogService
{
    Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default);
}
