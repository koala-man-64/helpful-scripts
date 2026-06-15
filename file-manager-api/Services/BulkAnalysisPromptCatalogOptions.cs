namespace FileManagerApi.Services;

public sealed class BulkAnalysisPromptCatalogOptions
{
    public const string SectionName = "BulkAnalysisPromptCatalog";

    public string ManifestPath { get; init; } = "prompts/catalog.json";

    public string LocalCatalogPath { get; init; } = string.Empty;

    public int CatalogCacheMinutes { get; init; } = 5;

    public TimeSpan CatalogCacheDuration =>
        TimeSpan.FromMinutes(Math.Max(1, CatalogCacheMinutes));
}
