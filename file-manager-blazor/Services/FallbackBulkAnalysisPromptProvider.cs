using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

internal sealed class FallbackBulkAnalysisPromptProvider(
    IBulkAnalysisPromptProvider primary,
    IBulkAnalysisPromptProvider fallback) : IBulkAnalysisPromptProvider
{
    public async Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            var prompts = await primary.GetPromptsAsync(cancellationToken);
            return prompts.Count > 0 ? prompts : await fallback.GetPromptsAsync(cancellationToken);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested && CanUseFallback(ex))
        {
            return await fallback.GetPromptsAsync(cancellationToken);
        }
    }

    private static bool CanUseFallback(Exception ex) =>
        ex is HttpRequestException or InvalidOperationException or TaskCanceledException;
}
