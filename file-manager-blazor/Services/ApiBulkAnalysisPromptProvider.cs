using System.Net;
using System.Net.Http.Json;
using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class ApiBulkAnalysisPromptProvider : IBulkAnalysisPromptProvider
{
    private readonly HttpClient httpClient;
    private readonly SemaphoreSlim catalogLock = new(1, 1);
    private IReadOnlyList<BulkAnalysisPrompt>? cachedPrompts;

    public ApiBulkAnalysisPromptProvider(HttpClient httpClient)
    {
        this.httpClient = httpClient;
    }

    public async Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default)
    {
        if (cachedPrompts is not null)
        {
            return cachedPrompts;
        }

        await catalogLock.WaitAsync(cancellationToken);
        try
        {
            if (cachedPrompts is not null)
            {
                return cachedPrompts;
            }

            using var response = await httpClient.GetAsync("api/bulk-analysis/prompts", cancellationToken);
            if (response.StatusCode == HttpStatusCode.NotFound)
            {
                cachedPrompts = [];
                return cachedPrompts;
            }

            response.EnsureSuccessStatusCode();
            cachedPrompts = await response.Content.ReadFromJsonAsync<IReadOnlyList<BulkAnalysisPrompt>>(cancellationToken: cancellationToken) ?? [];
            return cachedPrompts;
        }
        finally
        {
            catalogLock.Release();
        }
    }
}
