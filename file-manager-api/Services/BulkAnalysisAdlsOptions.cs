namespace FileManagerApi.Services;

public sealed class BulkAnalysisAdlsOptions
{
    public const string SectionName = "BulkAnalysisAdls";

    public string ConnectionString { get; init; } = string.Empty;

    public string FileSystemName { get; init; } = string.Empty;

    public int CatalogCacheMinutes { get; init; } = 5;

    public bool IsConfigured =>
        !string.IsNullOrWhiteSpace(ConnectionString) &&
        !string.IsNullOrWhiteSpace(FileSystemName);

    public TimeSpan CatalogCacheDuration =>
        TimeSpan.FromMinutes(Math.Max(1, CatalogCacheMinutes));
}
