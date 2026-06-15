namespace FileManagerApi.Services;

public sealed class BulkAnalysisAdlsOptions
{
    public const string SectionName = "BulkAnalysisAdls";

    private static readonly string[] PlaceholderConnectionStringFragments =
    [
        "AccountName=your_account",
        "AccountKey=your_key",
        "AccountName=...",
        "AccountKey=..."
    ];

    public string ConnectionString { get; init; } = string.Empty;

    public string FileSystemName { get; init; } = string.Empty;

    public int CatalogCacheMinutes { get; init; } = 5;

    public bool IsConfigured =>
        !string.IsNullOrWhiteSpace(ConnectionString) &&
        !HasPlaceholderConnectionString &&
        !string.IsNullOrWhiteSpace(FileSystemName);

    public bool HasPlaceholderConnectionString =>
        PlaceholderConnectionStringFragments.Any(fragment =>
            ConnectionString.Contains(fragment, StringComparison.OrdinalIgnoreCase));

    public TimeSpan CatalogCacheDuration =>
        TimeSpan.FromMinutes(Math.Max(1, CatalogCacheMinutes));
}
