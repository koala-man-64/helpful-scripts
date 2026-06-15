namespace FileManagerApi.Services;

public sealed class BulkAnalysisRenderingOptions
{
    public const string SectionName = "BulkAnalysisRendering";

    public string LibreOfficePath { get; init; } = string.Empty;
}
