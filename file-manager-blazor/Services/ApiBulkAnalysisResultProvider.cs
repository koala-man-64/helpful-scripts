using System.Net;
using System.Net.Http.Json;
using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class ApiBulkAnalysisResultProvider : IBulkAnalysisResultProvider
{
    private readonly HttpClient httpClient;
    private readonly SemaphoreSlim catalogLock = new(1, 1);
    private readonly Dictionary<string, string> resultMarkdownById = new(StringComparer.Ordinal);

    private IReadOnlyList<BulkAnalysisFolder>? cachedFolders;

    public ApiBulkAnalysisResultProvider(HttpClient httpClient)
    {
        this.httpClient = httpClient;
    }

    public async Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default)
    {
        if (cachedFolders is not null)
        {
            return cachedFolders;
        }

        await catalogLock.WaitAsync(cancellationToken);
        try
        {
            if (cachedFolders is not null)
            {
                return cachedFolders;
            }

            cachedFolders = await httpClient.GetFromJsonAsync<IReadOnlyList<BulkAnalysisFolder>>(
                "api/bulk-analysis/folders",
                cancellationToken) ?? [];

            return cachedFolders;
        }
        finally
        {
            catalogLock.Release();
        }
    }

    public async Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default)
    {
        using var response = await httpClient.GetAsync(
            $"api/bulk-analysis/raw?documentId={Uri.EscapeDataString(documentId)}",
            cancellationToken);

        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        response.EnsureSuccessStatusCode();

        return await response.Content.ReadFromJsonAsync<BulkAnalysisRawFile>(cancellationToken: cancellationToken);
    }

    public async Task<string?> GetResultMarkdownAsync(string resultId, CancellationToken cancellationToken = default)
    {
        if (resultMarkdownById.TryGetValue(resultId, out var cachedMarkdown))
        {
            return cachedMarkdown;
        }

        using var response = await httpClient.GetAsync(
            $"api/bulk-analysis/results/markdown?resultId={Uri.EscapeDataString(resultId)}",
            cancellationToken);

        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        response.EnsureSuccessStatusCode();

        var markdown = await response.Content.ReadAsStringAsync(cancellationToken);
        resultMarkdownById[resultId] = markdown;
        return markdown;
    }
}
