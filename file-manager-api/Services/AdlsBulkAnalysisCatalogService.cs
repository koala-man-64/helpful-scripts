using Azure;
using Azure.Storage.Files.DataLake;
using Azure.Storage.Files.DataLake.Models;
using FileManagerBlazor.Models;
using Microsoft.Extensions.Caching.Memory;

namespace FileManagerApi.Services;

public sealed class AdlsBulkAnalysisCatalogService : IBulkAnalysisCatalogService
{
    private const string CatalogCacheKey = "bulk-analysis-adls-catalog";

    private readonly BulkAnalysisAdlsOptions options;
    private readonly DataLakeFileSystemClient fileSystemClient;
    private readonly IMemoryCache memoryCache;
    private readonly ILogger<AdlsBulkAnalysisCatalogService> logger;
    private readonly IBulkAnalysisResultPreviewBuilder resultPreviewBuilder;
    private readonly SemaphoreSlim catalogLock = new(1, 1);

    public AdlsBulkAnalysisCatalogService(
        BulkAnalysisAdlsOptions options,
        IMemoryCache memoryCache,
        ILogger<AdlsBulkAnalysisCatalogService> logger,
        IBulkAnalysisResultPreviewBuilder resultPreviewBuilder)
    {
        if (!options.IsConfigured)
        {
            throw new InvalidOperationException("Bulk Analysis ADLS options are incomplete.");
        }

        this.options = options;
        fileSystemClient = new DataLakeFileSystemClient(options.ConnectionString, options.FileSystemName);
        this.memoryCache = memoryCache;
        this.logger = logger;
        this.resultPreviewBuilder = resultPreviewBuilder;
    }

    public async Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default)
    {
        var catalog = await GetCatalogAsync(cancellationToken);
        return catalog.Folders;
    }

    public async Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default)
    {
        var catalog = await GetCatalogAsync(cancellationToken);
        if (!catalog.RawFilesByDocumentId.TryGetValue(documentId, out var reference))
        {
            return null;
        }

        var content = await DownloadBytesAsync(reference.SourcePath, cancellationToken);
        return new BulkAnalysisRawFile(
            documentId,
            reference.FileName,
            reference.ContentType,
            reference.SourcePath,
            content);
    }

    public async Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default)
    {
        var catalog = await GetCatalogAsync(cancellationToken);
        if (!catalog.ResultFilesById.TryGetValue(resultId, out var reference))
        {
            return null;
        }

        var content = await DownloadBytesAsync(reference.ResultPath, cancellationToken);
        return new BulkAnalysisResultFile(
            resultId,
            reference.FileName,
            reference.ContentType,
            reference.FileExtension,
            reference.ResultPath,
            content);
    }

    public async Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default)
    {
        var resultFile = await GetResultFileAsync(resultId, cancellationToken);
        if (resultFile is null)
        {
            return null;
        }

        var reference = new ResultFileReference(
            resultFile.SourcePath,
            resultFile.FileName,
            resultFile.ContentType,
            resultFile.FileExtension);

        return await resultPreviewBuilder.BuildAsync(resultId, reference, resultFile.Content, cancellationToken);
    }

    private async Task<BulkAnalysisCatalog> GetCatalogAsync(CancellationToken cancellationToken)
    {
        if (memoryCache.TryGetValue(CatalogCacheKey, out BulkAnalysisCatalog? cachedCatalog) && cachedCatalog is not null)
        {
            return cachedCatalog;
        }

        await catalogLock.WaitAsync(cancellationToken);
        try
        {
            if (memoryCache.TryGetValue(CatalogCacheKey, out cachedCatalog) && cachedCatalog is not null)
            {
                return cachedCatalog;
            }

            logger.LogInformation("Loading bulk analysis ADLS catalog from file system {FileSystemName}.", options.FileSystemName);

            var catalog = await BulkAnalysisCatalogBuilder.BuildAsync(
                await ListPathsAsync(cancellationToken),
                DownloadMarkdownMetadataAsync,
                cancellationToken);

            memoryCache.Set(CatalogCacheKey, catalog, options.CatalogCacheDuration);
            logger.LogInformation(
                "Cached bulk analysis ADLS catalog with {FolderCount} categories for {CacheDurationMinutes} minutes.",
                catalog.Folders.Count,
                options.CatalogCacheDuration.TotalMinutes);

            return catalog;
        }
        finally
        {
            catalogLock.Release();
        }
    }

    private async Task<IReadOnlyList<AdlsPathItem>> ListPathsAsync(CancellationToken cancellationToken)
    {
        var paths = new List<AdlsPathItem>();

        await foreach (var path in fileSystemClient
            .GetPathsAsync(path: string.Empty, recursive: true, userPrincipalName: false, cancellationToken: cancellationToken)
            .ConfigureAwait(false))
        {
            paths.Add(ToAdlsPathItem(path));
        }

        return paths;
    }

    private async Task<IReadOnlyDictionary<string, string>> DownloadMarkdownMetadataAsync(
        string path,
        CancellationToken cancellationToken)
    {
        try
        {
            return ReadFrontMatter(await DownloadTextAsync(path, cancellationToken));
        }
        catch (RequestFailedException ex) when (ex.Status == 404)
        {
            return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        }
    }

    private async Task<string> DownloadTextAsync(string path, CancellationToken cancellationToken)
    {
        var bytes = await DownloadBytesAsync(path, cancellationToken);
        return System.Text.Encoding.UTF8.GetString(bytes);
    }

    private async Task<byte[]> DownloadBytesAsync(string path, CancellationToken cancellationToken)
    {
        var fileClient = fileSystemClient.GetFileClient(path);
        var response = await fileClient.ReadAsync(cancellationToken: cancellationToken);
        await using var content = response.Value.Content;
        using var memory = new MemoryStream();
        await content.CopyToAsync(memory, cancellationToken);
        return memory.ToArray();
    }

    private static AdlsPathItem ToAdlsPathItem(PathItem item) =>
        new(
            item.Name ?? string.Empty,
            item.IsDirectory == true,
            item.LastModified);

    private static IReadOnlyDictionary<string, string> ReadFrontMatter(string markdown)
    {
        markdown = NormalizeLineEndings(markdown);

        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (!markdown.StartsWith("---\n", StringComparison.Ordinal))
        {
            return result;
        }

        var end = markdown.IndexOf("\n---\n", 4, StringComparison.Ordinal);
        if (end < 0)
        {
            return result;
        }

        foreach (var line in markdown[4..end].Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            var separatorIndex = line.IndexOf(": ", StringComparison.Ordinal);
            if (separatorIndex <= 0)
            {
                continue;
            }

            result[line[..separatorIndex]] = line[(separatorIndex + 2)..].Trim();
        }

        return result;
    }

    private static string NormalizeLineEndings(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');
}
