using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public interface IBulkAnalysisPromptProvider
{
    Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default);
}
