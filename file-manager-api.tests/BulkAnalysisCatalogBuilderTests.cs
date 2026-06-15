using FileManagerApi.Services;
using FileManagerBlazor.Models;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Logging.Abstractions;

namespace FileManagerApi.Tests;

public sealed class BulkAnalysisCatalogBuilderTests
{
    [Fact]
    public void BulkAnalysisAdlsOptions_rejects_placeholder_connection_string()
    {
        var options = new BulkAnalysisAdlsOptions
        {
            ConnectionString = "DefaultEndpointsProtocol=https;AccountName=your_account;AccountKey=your_key;EndpointSuffix=core.windows.net",
            FileSystemName = "bulk-analysis"
        };

        Assert.True(options.HasPlaceholderConnectionString);
        Assert.False(options.IsConfigured);
    }

    [Fact]
    public async Task AdlsCatalogService_returns_empty_catalog_when_options_are_placeholder()
    {
        var options = new BulkAnalysisAdlsOptions
        {
            ConnectionString = "DefaultEndpointsProtocol=https;AccountName=your_account;AccountKey=your_key;EndpointSuffix=core.windows.net",
            FileSystemName = "bulk-analysis"
        };

        using var memoryCache = new MemoryCache(new MemoryCacheOptions());
        var service = new AdlsBulkAnalysisCatalogService(
            options,
            memoryCache,
            NullLogger<AdlsBulkAnalysisCatalogService>.Instance,
            new StubResultPreviewBuilder());

        var folders = await service.GetFoldersAsync();

        Assert.Empty(folders);
    }

    [Fact]
    public async Task BuildAsync_groups_supported_raw_documents_by_category()
    {
        var now = DateTimeOffset.Parse("2026-06-13T00:00:00Z");
        var paths = new[]
        {
            new AdlsPathItem("claims", IsDirectory: true, now),
            new AdlsPathItem("claims/raw/2024_01_claims-handbook.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/readme.txt", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/archive/ignored.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/transformed/2024_01_claims-handbook.md", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/2024_01_claims-handbook.summary.md", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/risk-review/2024_01_claims-handbook.risk-review.md", IsDirectory: false, now),
            new AdlsPathItem("appeals/raw/2024_01_appeals-guide.docx", IsDirectory: false, now)
        };

        var catalog = await BulkAnalysisCatalogBuilder.BuildAsync(
            paths,
            (path, _) => Task.FromResult<IReadOnlyDictionary<string, string>>(
                path == "claims/transformed/2024_01_claims-handbook.md"
                    ? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
                    {
                        ["title"] = "Claims Handbook"
                    }
                    : new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)));

        Assert.Equal(["Appeals", "Claims"], catalog.Folders.Select(folder => folder.DisplayName).ToArray());

        var claimsFolder = catalog.Folders.Single(folder => folder.Id == "claims");
        Assert.Equal("Documents and generated analysis results for Claims.", claimsFolder.Description);
        Assert.All(catalog.Folders, folder => Assert.False(string.IsNullOrWhiteSpace(folder.Description)));

        var claimsDocument = Assert.Single(claimsFolder.Documents);
        Assert.Equal("claims/2024_01_claims-handbook", claimsDocument.Id);
        Assert.Equal("Claims Handbook", claimsDocument.Title);
        Assert.Equal("claims/raw/2024_01_claims-handbook.pdf", claimsDocument.SourcePath);
        Assert.Equal("pdf", claimsDocument.SourceExtension);
        Assert.Equal(["Risk Review", "Summary"], claimsDocument.Results.Select(result => result.AnalysisType).ToArray());
        Assert.Equal("2024_01_claims-handbook.summary.md", claimsDocument.Results.Single(result => result.AnalysisType == "Summary").ResultFileName);
        Assert.Equal("md", claimsDocument.Results.Single(result => result.AnalysisType == "Summary").ResultExtension);

        Assert.True(catalog.RawFilesByDocumentId.ContainsKey(claimsDocument.Id));
        Assert.True(catalog.ResultFilesById.ContainsKey("claims/2024_01_claims-handbook/summary"));
        Assert.DoesNotContain(catalog.Folders.SelectMany(folder => folder.Documents), document => document.OriginalFileName == "readme.txt");
    }

    [Fact]
    public async Task BuildAsync_matches_results_by_category_and_document_stem()
    {
        var now = DateTimeOffset.Parse("2026-06-13T00:00:00Z");
        var paths = new[]
        {
            new AdlsPathItem("appeals/raw/shared.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/shared.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/shared.summary.md", IsDirectory: false, now)
        };

        var catalog = await BulkAnalysisCatalogBuilder.BuildAsync(
            paths,
            (_, _) => Task.FromResult<IReadOnlyDictionary<string, string>>(
                new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)));

        var appealsDocument = catalog.Folders.Single(folder => folder.Id == "appeals").Documents.Single();
        var claimsDocument = catalog.Folders.Single(folder => folder.Id == "claims").Documents.Single();

        Assert.Empty(appealsDocument.Results);
        Assert.Single(claimsDocument.Results);
        Assert.Equal("claims/shared/summary", claimsDocument.Results.Single().Id);
    }

    [Fact]
    public async Task BuildAsync_discovers_supported_result_extensions()
    {
        var now = DateTimeOffset.Parse("2026-06-13T00:00:00Z");
        var paths = new[]
        {
            new AdlsPathItem("claims/raw/doc-html.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-htm.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-doc.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-docx.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-pdf.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-md.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/raw/doc-txt.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-html.summary.html", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-htm.summary.htm", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-doc.summary.doc", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-docx.summary.docx", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-pdf.summary.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-md.summary.md", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/doc-txt.summary.txt", IsDirectory: false, now)
        };

        var catalog = await BulkAnalysisCatalogBuilder.BuildAsync(
            paths,
            (_, _) => Task.FromResult<IReadOnlyDictionary<string, string>>(
                new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)));

        var resultExtensions = catalog.Folders
            .Single(folder => folder.Id == "claims")
            .Documents
            .OrderBy(document => document.Id, StringComparer.Ordinal)
            .Select(document => document.Results.Single().ResultExtension!)
            .ToArray();

        Assert.Equal(["doc", "docx", "htm", "html", "md", "pdf", "txt"], resultExtensions);
    }

    [Fact]
    public async Task BuildAsync_throws_for_duplicate_result_artifacts_for_same_document_and_analysis()
    {
        var now = DateTimeOffset.Parse("2026-06-13T00:00:00Z");
        var paths = new[]
        {
            new AdlsPathItem("claims/raw/shared.pdf", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/shared.summary.md", IsDirectory: false, now),
            new AdlsPathItem("claims/llm_results/summary/shared.summary.html", IsDirectory: false, now)
        };

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            BulkAnalysisCatalogBuilder.BuildAsync(
                paths,
                (_, _) => Task.FromResult<IReadOnlyDictionary<string, string>>(
                    new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase))));

        Assert.Contains("Duplicate bulk analysis result files detected", exception.Message);
        Assert.Contains("claims|shared|summary", exception.Message);
    }

    private sealed class StubResultPreviewBuilder : IBulkAnalysisResultPreviewBuilder
    {
        public Task<BulkAnalysisResultPreview> BuildAsync(
            string resultId,
            ResultFileReference reference,
            byte[] content,
            CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();
    }
}
