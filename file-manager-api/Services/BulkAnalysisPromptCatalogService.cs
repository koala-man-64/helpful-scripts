using Azure;
using Azure.Storage.Files.DataLake;
using FileManagerBlazor.Models;
using Microsoft.Extensions.Caching.Memory;

namespace FileManagerApi.Services;

public sealed class BulkAnalysisPromptCatalogService : IBulkAnalysisPromptCatalogService
{
    private const string CacheKey = "bulk-analysis-prompt-catalog";

    private readonly BulkAnalysisPromptCatalogOptions options;
    private readonly BulkAnalysisAdlsOptions adlsOptions;
    private readonly IMemoryCache memoryCache;
    private readonly IWebHostEnvironment environment;
    private readonly ILogger<BulkAnalysisPromptCatalogService> logger;
    private readonly SemaphoreSlim catalogLock = new(1, 1);
    private DataLakeFileSystemClient? fileSystemClient;

    public BulkAnalysisPromptCatalogService(
        BulkAnalysisPromptCatalogOptions options,
        BulkAnalysisAdlsOptions adlsOptions,
        IMemoryCache memoryCache,
        IWebHostEnvironment environment,
        ILogger<BulkAnalysisPromptCatalogService> logger)
    {
        this.options = options;
        this.adlsOptions = adlsOptions;
        this.memoryCache = memoryCache;
        this.environment = environment;
        this.logger = logger;
    }

    public async Task<IReadOnlyList<BulkAnalysisPrompt>> GetPromptsAsync(CancellationToken cancellationToken = default)
    {
        if (memoryCache.TryGetValue(CacheKey, out IReadOnlyList<BulkAnalysisPrompt>? cachedPrompts) && cachedPrompts is not null)
        {
            return cachedPrompts;
        }

        await catalogLock.WaitAsync(cancellationToken);
        try
        {
            if (memoryCache.TryGetValue(CacheKey, out cachedPrompts) && cachedPrompts is not null)
            {
                return cachedPrompts;
            }

            var prompts = await LoadPromptsAsync(cancellationToken);
            memoryCache.Set(CacheKey, prompts, options.CatalogCacheDuration);
            return prompts;
        }
        finally
        {
            catalogLock.Release();
        }
    }

    private async Task<IReadOnlyList<BulkAnalysisPrompt>> LoadPromptsAsync(CancellationToken cancellationToken)
    {
        if (adlsOptions.IsConfigured)
        {
            try
            {
                var manifestPath = string.IsNullOrWhiteSpace(options.ManifestPath) ? "prompts/catalog.json" : options.ManifestPath;
                var json = await DownloadAdlsTextAsync(manifestPath, cancellationToken);
                return BulkAnalysisPromptCatalogBuilder.BuildFromJson(json, manifestPath);
            }
            catch (RequestFailedException ex) when (ex.Status == 404)
            {
                logger.LogWarning("Bulk Analysis prompt catalog manifest {ManifestPath} was not found in ADLS.", options.ManifestPath);
                return [];
            }
        }

        var localPath = ResolveLocalCatalogPath();
        if (string.IsNullOrWhiteSpace(localPath) || !File.Exists(localPath))
        {
            logger.LogWarning("Bulk Analysis local prompt catalog is not configured or was not found at {LocalCatalogPath}.", localPath);
            return [];
        }

        var localJson = await File.ReadAllTextAsync(localPath, cancellationToken);
        return BulkAnalysisPromptCatalogBuilder.BuildFromJson(localJson, localPath);
    }

    private string ResolveLocalCatalogPath()
    {
        var configuredPath = string.IsNullOrWhiteSpace(options.LocalCatalogPath)
            ? "prompts/catalog.json"
            : options.LocalCatalogPath;

        return Path.IsPathRooted(configuredPath)
            ? configuredPath
            : Path.GetFullPath(Path.Combine(environment.ContentRootPath, configuredPath));
    }

    private async Task<string> DownloadAdlsTextAsync(string path, CancellationToken cancellationToken)
    {
        fileSystemClient ??= new DataLakeFileSystemClient(adlsOptions.ConnectionString, adlsOptions.FileSystemName);
        var fileClient = fileSystemClient.GetFileClient(path);
        var response = await fileClient.ReadAsync(cancellationToken: cancellationToken);
        await using var content = response.Value.Content;
        using var reader = new StreamReader(content);
        return await reader.ReadToEndAsync(cancellationToken);
    }
}
