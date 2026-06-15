using System.Net;
using System.Net.Http.Json;
using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class ApiBulkAnalysisResultProvider : IBulkAnalysisResultProvider
{
    private readonly HttpClient httpClient;
    private readonly SemaphoreSlim catalogLock = new(1, 1);
    private readonly Dictionary<string, BulkAnalysisResultFile> resultFilesById = new(StringComparer.Ordinal);
    private readonly Dictionary<string, BulkAnalysisResultPreview> resultPreviewsById = new(StringComparer.Ordinal);

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

    public async Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default)
    {
        if (resultFilesById.TryGetValue(resultId, out var cachedFile))
        {
            return cachedFile;
        }

        using var response = await httpClient.GetAsync(
            $"api/bulk-analysis/results/file?resultId={Uri.EscapeDataString(resultId)}",
            cancellationToken);

        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        response.EnsureSuccessStatusCode();

        var resultFile = await response.Content.ReadFromJsonAsync<BulkAnalysisResultFile>(cancellationToken: cancellationToken);
        if (resultFile is not null)
        {
            resultFilesById[resultId] = resultFile;
        }

        return resultFile;
    }

    public async Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default)
    {
        if (resultPreviewsById.TryGetValue(resultId, out var cachedPreview))
        {
            return cachedPreview;
        }

        using var response = await httpClient.GetAsync(
            $"api/bulk-analysis/results/preview?resultId={Uri.EscapeDataString(resultId)}",
            cancellationToken);

        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        response.EnsureSuccessStatusCode();

        var preview = await response.Content.ReadFromJsonAsync<BulkAnalysisResultPreview>(cancellationToken: cancellationToken);
        if (preview is not null)
        {
            resultPreviewsById[resultId] = preview;
        }

        return preview;
    }
}
