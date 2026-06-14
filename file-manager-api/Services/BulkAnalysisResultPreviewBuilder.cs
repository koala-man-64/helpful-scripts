using System.Text;
using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public sealed class BulkAnalysisResultPreviewBuilder(
    IBulkAnalysisDocumentConverter documentConverter) : IBulkAnalysisResultPreviewBuilder
{
    public async Task<BulkAnalysisResultPreview> BuildAsync(
        string resultId,
        ResultFileReference reference,
        byte[] content,
        CancellationToken cancellationToken = default)
    {
        return reference.FileExtension switch
        {
            "doc" => await BuildDocPreviewAsync(resultId, reference, content, cancellationToken),
            "docx" => BuildPassthroughPreview(resultId, reference, "Word", content),
            "htm" or "html" => BuildPassthroughPreview(resultId, reference, "HTML", content),
            "md" => BuildMarkdownPreview(resultId, reference, content),
            "pdf" => BuildPassthroughPreview(resultId, reference, "PDF", content),
            "txt" => BuildPassthroughPreview(resultId, reference, "Plain Text", NormalizeUtf8(content), "text/plain;charset=utf-8"),
            _ => throw new InvalidOperationException($"Preview is not supported for .{reference.FileExtension} result files.")
        };
    }

    private async Task<BulkAnalysisResultPreview> BuildDocPreviewAsync(
        string resultId,
        ResultFileReference reference,
        byte[] content,
        CancellationToken cancellationToken)
    {
        var convertedContent = await documentConverter.ConvertDocToDocxAsync(reference.FileName, content, cancellationToken);
        return new BulkAnalysisResultPreview(
            resultId,
            Path.ChangeExtension(reference.FileName, ".docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
            "Word",
            convertedContent);
    }

    private static BulkAnalysisResultPreview BuildMarkdownPreview(
        string resultId,
        ResultFileReference reference,
        byte[] content)
    {
        var markdown = StripFrontMatter(Encoding.UTF8.GetString(content));
        return new BulkAnalysisResultPreview(
            resultId,
            reference.FileName,
            "text/markdown;charset=utf-8",
            reference.FileExtension,
            "Markdown",
            Encoding.UTF8.GetBytes(markdown));
    }

    private static BulkAnalysisResultPreview BuildPassthroughPreview(
        string resultId,
        ResultFileReference reference,
        string format,
        byte[] content,
        string? contentType = null) =>
        new(
            resultId,
            reference.FileName,
            contentType ?? reference.ContentType,
            reference.FileExtension,
            format,
            content);

    private static byte[] NormalizeUtf8(byte[] content) =>
        Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(content));

    private static string StripFrontMatter(string markdown)
    {
        markdown = NormalizeLineEndings(markdown);

        if (!markdown.StartsWith("---\n", StringComparison.Ordinal))
        {
            return markdown.Trim();
        }

        var end = markdown.IndexOf("\n---\n", 4, StringComparison.Ordinal);
        return end < 0 ? markdown.Trim() : markdown[(end + 5)..].Trim();
    }

    private static string NormalizeLineEndings(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');
}
