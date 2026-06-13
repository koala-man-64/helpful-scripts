namespace FileManagerBlazor.Services;

public sealed class BulkAnalysisApiOptions
{
    public const string SectionName = "BulkAnalysisApi";

    public string BaseUrl { get; init; } = string.Empty;

    public bool UseMockFallback { get; init; }
}
