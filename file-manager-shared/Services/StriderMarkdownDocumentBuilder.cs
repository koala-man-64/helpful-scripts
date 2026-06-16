using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml.Linq;
using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public static class StriderMarkdownDocumentBuilder
{
    public static string Build(
        string promptText,
        BulkAnalysisPrompt? prompt,
        IReadOnlyList<BulkAnalysisResult> results,
        IReadOnlyDictionary<string, BulkAnalysisResultFile> filesByResultId,
        DateTimeOffset generatedAt)
    {
        if (results.Count == 0)
        {
            throw new ArgumentException("At least one result is required.", nameof(results));
        }

        var builder = new StringBuilder();
        builder.AppendLine("# Strider Bulk Analysis Context");
        builder.AppendLine();
        builder.AppendLine($"Generated: {generatedAt:yyyy-MM-dd HH:mm:ss zzz}");
        builder.AppendLine($"Selected results: {results.Count}");
        if (prompt is not null)
        {
            builder.AppendLine($"Prompt: {NormalizeMetadata(prompt.DisplayName)}");
        }

        builder.AppendLine();
        builder.AppendLine("## Prompt");
        builder.AppendLine();
        builder.AppendLine(promptText.Trim());
        builder.AppendLine();
        builder.AppendLine("## Selected Results");

        for (var index = 0; index < results.Count; index++)
        {
            var result = results[index];
            if (!filesByResultId.TryGetValue(result.Id, out var resultFile))
            {
                throw new InvalidOperationException($"The selected {result.AnalysisType} result file was not loaded.");
            }

            AppendResult(builder, index + 1, result, resultFile);
        }

        return builder.ToString().TrimEnd() + Environment.NewLine;
    }

    public static string GetFileName(IReadOnlyList<BulkAnalysisResult> results, DateTimeOffset generatedAt)
    {
        var name = results.Count == 1
            ? $"{results[0].DocumentTitle}-{results[0].AnalysisType}-strider-context"
            : $"strider-bulk-analysis-context-{generatedAt:yyyyMMdd-HHmmss}";

        return $"{SlugifyFileName(name)}.md";
    }

    private static void AppendResult(
        StringBuilder builder,
        int ordinal,
        BulkAnalysisResult result,
        BulkAnalysisResultFile resultFile)
    {
        builder.AppendLine();
        builder.AppendLine("---");
        builder.AppendLine();
        builder.AppendLine($"## Result {ordinal}: {NormalizeHeading(result.DocumentTitle)} - {NormalizeHeading(result.AnalysisType)}");
        builder.AppendLine();
        builder.AppendLine($"- Folder: {NormalizeMetadata(result.FolderName)}");
        builder.AppendLine($"- Source document: {NormalizeMetadata(result.OriginalFileName)}");
        builder.AppendLine($"- Result file: {NormalizeMetadata(resultFile.FileName)}");
        builder.AppendLine($"- Result format: {NormalizeMetadata(GetFormatLabel(resultFile))}");
        builder.AppendLine($"- Generated: {result.GeneratedAt:yyyy-MM-dd HH:mm:ss}");

        var sourcePath = string.IsNullOrWhiteSpace(result.ResultPath) ? resultFile.SourcePath : result.ResultPath;
        if (!string.IsNullOrWhiteSpace(sourcePath))
        {
            builder.AppendLine($"- Source path: {NormalizeMetadata(sourcePath)}");
        }

        builder.AppendLine();
        builder.AppendLine("### Content");
        builder.AppendLine();
        builder.AppendLine(ConvertResultFileToMarkdown(resultFile).Trim());
    }

    private static string ConvertResultFileToMarkdown(BulkAnalysisResultFile resultFile)
    {
        var extension = NormalizeFileExtension(resultFile.FileExtension);
        if (string.IsNullOrWhiteSpace(extension))
        {
            extension = NormalizeFileExtension(Path.GetExtension(resultFile.FileName));
        }

        return extension switch
        {
            "md" => StripFrontMatter(DecodeUtf8(resultFile.Content)),
            "txt" => DecodeUtf8(resultFile.Content).Trim(),
            "html" or "htm" => ConvertHtmlToText(DecodeUtf8(resultFile.Content)),
            "docx" => TryExtractDocxText(resultFile.Content) ?? BuildUnsupportedContentMessage(resultFile),
            "doc" => TryExtractRtfText(resultFile.Content) ?? BuildUnsupportedContentMessage(resultFile),
            "pdf" => TryExtractPdfText(resultFile.Content) ?? BuildUnsupportedContentMessage(resultFile),
            _ => BuildUnsupportedContentMessage(resultFile)
        };
    }

    private static string BuildUnsupportedContentMessage(BulkAnalysisResultFile resultFile) =>
        $"""
        Result content could not be converted into readable markdown in the browser.

        - File: {NormalizeMetadata(resultFile.FileName)}
        - Content type: {NormalizeMetadata(resultFile.ContentType)}
        - Source path: {NormalizeMetadata(resultFile.SourcePath)}
        """;

    private static string? TryExtractDocxText(byte[] content)
    {
        try
        {
            using var stream = new MemoryStream(content);
            using var archive = new ZipArchive(stream, ZipArchiveMode.Read);
            var documentEntry = archive.GetEntry("word/document.xml");
            if (documentEntry is null)
            {
                return null;
            }

            using var reader = new StreamReader(documentEntry.Open(), Encoding.UTF8);
            var document = XDocument.Parse(reader.ReadToEnd());
            XNamespace word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
            var paragraphs = document
                .Descendants(word + "p")
                .Select(paragraph => ExtractDocxParagraphText(paragraph, word))
                .Where(paragraph => !string.IsNullOrWhiteSpace(paragraph));

            var text = string.Join($"{Environment.NewLine}{Environment.NewLine}", paragraphs);
            return string.IsNullOrWhiteSpace(text) ? null : NormalizeLineEndings(text).Trim();
        }
        catch
        {
            return null;
        }
    }

    private static string ExtractDocxParagraphText(XElement paragraph, XNamespace word)
    {
        var builder = new StringBuilder();
        foreach (var element in paragraph.Descendants())
        {
            if (element.Name == word + "t")
            {
                builder.Append(element.Value);
            }
            else if (element.Name == word + "tab")
            {
                builder.Append('\t');
            }
            else if (element.Name == word + "br")
            {
                builder.AppendLine();
            }
        }

        return builder.ToString().Trim();
    }

    private static string? TryExtractRtfText(byte[] content)
    {
        var rtf = Encoding.UTF8.GetString(content).Trim();
        if (!rtf.StartsWith(@"{\rtf", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        var text = Regex.Replace(rtf, @"\\par[d]?", "\n", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"\\'[0-9a-fA-F]{2}", match =>
        {
            var value = Convert.ToInt32(match.Value[2..], 16);
            return ((char)value).ToString();
        });
        text = Regex.Replace(text, @"\\[a-zA-Z]+\d* ?", string.Empty);
        text = Regex.Replace(text, @"[{}]", string.Empty);
        text = CollapseBlankLines(text);

        return string.IsNullOrWhiteSpace(text) ? null : text;
    }

    private static string? TryExtractPdfText(byte[] content)
    {
        var raw = Encoding.UTF8.GetString(content);
        var lines = Regex.Matches(raw, @"\((?:\\.|[^\\)])*\)\s*Tj")
            .Cast<Match>()
            .Select(match =>
            {
                var value = match.Value;
                var end = value.LastIndexOf(')');
                return end <= 1 ? string.Empty : UnescapePdfText(value[1..end]);
            })
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .ToArray();

        return lines.Length == 0 ? null : CollapseBlankLines(string.Join(Environment.NewLine, lines));
    }

    private static string UnescapePdfText(string value) =>
        value.Replace(@"\(", "(", StringComparison.Ordinal)
            .Replace(@"\)", ")", StringComparison.Ordinal)
            .Replace(@"\\", @"\", StringComparison.Ordinal);

    private static string ConvertHtmlToText(string html)
    {
        var text = NormalizeLineEndings(html);
        text = Regex.Replace(text, @"(?is)<(script|style).*?</\1>", string.Empty);
        text = Regex.Replace(text, @"(?i)<br\s*/?>", "\n");
        text = Regex.Replace(text, @"(?i)<li[^>]*>", "- ");
        text = Regex.Replace(text, @"(?i)</(p|div|h[1-6]|li|tr|table|section|article|main)>", "\n");
        text = Regex.Replace(text, "<[^>]+>", string.Empty);
        return CollapseBlankLines(WebUtility.HtmlDecode(text));
    }

    private static string StripFrontMatter(string markdown)
    {
        markdown = NormalizeLineEndings(markdown).Trim();

        if (!markdown.StartsWith("---\n", StringComparison.Ordinal))
        {
            return markdown;
        }

        var end = markdown.IndexOf("\n---\n", 4, StringComparison.Ordinal);
        return end < 0 ? markdown : markdown[(end + 5)..].Trim();
    }

    private static string DecodeUtf8(byte[] content) =>
        NormalizeLineEndings(Encoding.UTF8.GetString(content).TrimStart('\uFEFF'));

    private static string CollapseBlankLines(string value) =>
        Regex.Replace(NormalizeLineEndings(value).Trim(), "\n{3,}", "\n\n");

    private static string NormalizeLineEndings(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');

    private static string NormalizeFileExtension(string? extension) =>
        extension?.TrimStart('.').ToLowerInvariant() ?? string.Empty;

    private static string NormalizeHeading(string value) =>
        NormalizeMetadata(value).Replace("#", string.Empty, StringComparison.Ordinal).Trim();

    private static string NormalizeMetadata(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? "N/A"
            : NormalizeLineEndings(value).Replace('\n', ' ').Trim();

    private static string GetFormatLabel(BulkAnalysisResultFile resultFile) =>
        NormalizeFileExtension(resultFile.FileExtension) switch
        {
            "doc" or "docx" => "Word",
            "htm" or "html" => "HTML",
            "md" => "Markdown",
            "pdf" => "PDF",
            "txt" => "Plain Text",
            _ => resultFile.ContentType
        };

    private static string SlugifyFileName(string value)
    {
        var builder = new StringBuilder(value.Length);
        var lastWasSeparator = false;

        foreach (var character in value.Trim().ToLowerInvariant())
        {
            if (character is >= 'a' and <= 'z' or >= '0' and <= '9')
            {
                builder.Append(character);
                lastWasSeparator = false;
            }
            else if (!lastWasSeparator)
            {
                builder.Append('-');
                lastWasSeparator = true;
            }
        }

        var slug = builder.ToString().Trim('-');
        return string.IsNullOrWhiteSpace(slug) ? "strider-bulk-analysis-context" : slug;
    }
}
