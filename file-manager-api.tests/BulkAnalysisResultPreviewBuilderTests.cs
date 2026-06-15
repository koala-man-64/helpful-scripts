using System.Text;
using FileManagerApi.Services;

namespace FileManagerApi.Tests;

public sealed class BulkAnalysisResultPreviewBuilderTests
{
    [Fact]
    public async Task BuildAsync_strips_markdown_front_matter_from_preview()
    {
        var builder = new BulkAnalysisResultPreviewBuilder(new StubDocumentConverter());
        var reference = new ResultFileReference(
            "claims/llm_results/summary/handbook.summary.md",
            "handbook.summary.md",
            "text/markdown;charset=utf-8",
            "md");

        var preview = await builder.BuildAsync(
            "result-1",
            reference,
            Encoding.UTF8.GetBytes("""
                ---
                title: Handbook
                ---

                # Body
                """));

        Assert.Equal("Markdown", preview.Format);
        Assert.Equal("md", preview.FileExtension);
        Assert.Equal("# Body", Encoding.UTF8.GetString(preview.Content));
    }

    [Fact]
    public async Task BuildAsync_converts_doc_to_docx_preview_with_converter()
    {
        var converter = new StubDocumentConverter
        {
            ConvertedBytes = [1, 2, 3, 4]
        };
        var builder = new BulkAnalysisResultPreviewBuilder(converter);
        var reference = new ResultFileReference(
            "claims/llm_results/compliance/handbook.compliance.doc",
            "handbook.compliance.doc",
            "application/msword",
            "doc");

        var preview = await builder.BuildAsync(
            "result-2",
            reference,
            Encoding.UTF8.GetBytes("legacy-doc"));

        Assert.Equal("Word", preview.Format);
        Assert.Equal("docx", preview.FileExtension);
        Assert.Equal("handbook.compliance.docx", preview.FileName);
        Assert.Equal([1, 2, 3, 4], preview.Content);
        Assert.Equal("handbook.compliance.doc", converter.SourceFileName);
    }

    [Fact]
    public async Task BuildAsync_returns_doc_passthrough_preview_when_conversion_is_unavailable()
    {
        var converter = new StubDocumentConverter
        {
            Exception = new InvalidOperationException("LibreOffice is unavailable.")
        };
        var builder = new BulkAnalysisResultPreviewBuilder(converter);
        var reference = new ResultFileReference(
            "claims/llm_results/compliance/handbook.compliance.doc",
            "handbook.compliance.doc",
            "application/msword",
            "doc");
        var content = Encoding.UTF8.GetBytes("legacy-doc");

        var preview = await builder.BuildAsync("result-2", reference, content);

        Assert.Equal("Word", preview.Format);
        Assert.Equal("doc", preview.FileExtension);
        Assert.Equal("handbook.compliance.doc", preview.FileName);
        Assert.Equal("application/msword", preview.ContentType);
        Assert.Equal(content, preview.Content);
        Assert.Equal("handbook.compliance.doc", converter.SourceFileName);
    }

    private sealed class StubDocumentConverter : IBulkAnalysisDocumentConverter
    {
        public byte[] ConvertedBytes { get; init; } = [];

        public Exception? Exception { get; init; }

        public string? SourceFileName { get; private set; }

        public Task<byte[]> ConvertDocToDocxAsync(string sourceFileName, byte[] content, CancellationToken cancellationToken = default)
        {
            SourceFileName = sourceFileName;
            if (Exception is not null)
            {
                return Task.FromException<byte[]>(Exception);
            }

            return Task.FromResult(ConvertedBytes);
        }
    }
}
