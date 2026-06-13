namespace FileManagerBlazor.Services;

public sealed class BulkAnalysisAdlsOptions
{
    public const string SectionName = "BulkAnalysisAdls";

    public bool Enabled { get; init; }

    public string AccountName { get; init; } = string.Empty;

    public string FileSystemName { get; init; } = string.Empty;

    public string SasToken { get; init; } = string.Empty;

    public bool IsConfigured =>
        Enabled &&
        !string.IsNullOrWhiteSpace(AccountName) &&
        !string.IsNullOrWhiteSpace(FileSystemName) &&
        !string.IsNullOrWhiteSpace(SasToken);

    public string NormalizedSasToken => SasToken.Trim().TrimStart('?');
}
