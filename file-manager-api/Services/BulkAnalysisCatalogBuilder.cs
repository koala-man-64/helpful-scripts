using System.Globalization;
using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public static class BulkAnalysisCatalogBuilder
{
    private static readonly HashSet<string> SupportedRawExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".docx",
        ".pdf"
    };

    public static async Task<BulkAnalysisCatalog> BuildAsync(
        IEnumerable<AdlsPathItem> pathItems,
        Func<string, CancellationToken, Task<IReadOnlyDictionary<string, string>>> loadTransformedMetadataAsync,
        CancellationToken cancellationToken = default)
    {
        var fileItems = pathItems
            .Where(item => !item.IsDirectory && !string.IsNullOrWhiteSpace(item.Name))
            .ToArray();

        var transformedByCategoryAndStem = fileItems
            .Where(IsTransformedMarkdownPath)
            .ToDictionary(
                item => GetLookupKey(GetSegment(item.Name, 0), GetDocumentStem(item.Name)),
                item => item,
                StringComparer.OrdinalIgnoreCase);

        var resultItemsByCategoryAndStem = fileItems
            .Where(IsNestedResultMarkdownPath)
            .GroupBy(
                item => GetLookupKey(GetSegment(item.Name, 0), GetResultDocumentStem(item.Name)),
                StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key, group => group.ToArray(), StringComparer.OrdinalIgnoreCase);

        var rawReferences = new Dictionary<string, RawFileReference>(StringComparer.Ordinal);
        var resultReferences = new Dictionary<string, ResultFileReference>(StringComparer.Ordinal);
        var folders = new List<BulkAnalysisFolder>();

        foreach (var categoryGroup in fileItems
            .Where(IsRawDocumentPath)
            .GroupBy(item => GetSegment(item.Name, 0), StringComparer.OrdinalIgnoreCase)
            .OrderBy(group => GetDisplayName(group.Key), StringComparer.OrdinalIgnoreCase))
        {
            var category = categoryGroup.Key;
            var documents = new List<BulkAnalysisDocument>();

            foreach (var rawItem in categoryGroup
                .OrderBy(item => GetDisplayName(GetDocumentStem(item.Name)), StringComparer.OrdinalIgnoreCase))
            {
                cancellationToken.ThrowIfCancellationRequested();

                var rawPath = rawItem.Name;
                var stem = GetDocumentStem(rawPath);
                var transformedPath = $"{category}/transformed/{stem}.md";
                var transformedKey = GetLookupKey(category, stem);
                var metadata = transformedByCategoryAndStem.ContainsKey(transformedKey)
                    ? await loadTransformedMetadataAsync(transformedPath, cancellationToken)
                    : new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

                var title = metadata.GetValueOrDefault("title");
                if (string.IsNullOrWhiteSpace(title))
                {
                    title = GetDisplayName(GetSlugFromStem(stem));
                }

                var documentId = $"{category}/{stem}";
                var originalFileName = Path.GetFileName(rawPath);
                var contentType = GetContentType(originalFileName);
                var sourceExtension = Path.GetExtension(originalFileName).TrimStart('.').ToLowerInvariant();

                var results = new List<BulkAnalysisResult>();
                if (resultItemsByCategoryAndStem.TryGetValue(GetLookupKey(category, stem), out var resultItems))
                {
                    foreach (var resultItem in resultItems.OrderBy(item => GetSegment(item.Name, 2), StringComparer.OrdinalIgnoreCase))
                    {
                        var analysisSlug = GetSegment(resultItem.Name, 2);
                        var analysisType = GetDisplayName(analysisSlug);
                        var generatedAt = resultItem.LastModified.UtcDateTime;
                        var resultId = $"{documentId}/{analysisSlug}";

                        results.Add(new BulkAnalysisResult(
                            resultId,
                            category,
                            documentId,
                            GetDisplayName(category),
                            title,
                            originalFileName,
                            analysisType,
                            generatedAt,
                            string.Empty,
                            IsPreviewAvailable: true,
                            AnalysisSlug: analysisSlug,
                            ResultPath: resultItem.Name));
                        resultReferences[resultId] = new ResultFileReference(resultItem.Name);
                    }
                }

                documents.Add(new BulkAnalysisDocument(
                    documentId,
                    category,
                    title,
                    originalFileName,
                    results,
                    SourcePath: rawPath,
                    ContentType: contentType,
                    SourceExtension: sourceExtension,
                    TransformedPath: transformedPath));

                rawReferences[documentId] = new RawFileReference(rawPath, originalFileName, contentType);
            }

            folders.Add(new BulkAnalysisFolder(category, GetDisplayName(category), null, documents, []));
        }

        return new BulkAnalysisCatalog(folders, rawReferences, resultReferences);
    }

    private static bool IsRawDocumentPath(AdlsPathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 3 &&
            string.Equals(parts[1], "raw", StringComparison.OrdinalIgnoreCase) &&
            SupportedRawExtensions.Contains(Path.GetExtension(parts[2]));
    }

    private static bool IsTransformedMarkdownPath(AdlsPathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 3 &&
            string.Equals(parts[1], "transformed", StringComparison.OrdinalIgnoreCase) &&
            string.Equals(Path.GetExtension(parts[2]), ".md", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsNestedResultMarkdownPath(AdlsPathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 4 &&
            string.Equals(parts[1], "llm_results", StringComparison.OrdinalIgnoreCase) &&
            string.Equals(Path.GetExtension(parts[3]), ".md", StringComparison.OrdinalIgnoreCase);
    }

    private static string[] SplitPath(string path) =>
        path.Split('/', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private static string GetSegment(string path, int index)
    {
        var parts = SplitPath(path);
        return index >= 0 && index < parts.Length ? parts[index] : string.Empty;
    }

    private static string GetDocumentStem(string path)
    {
        var fileName = Path.GetFileName(path);
        var extension = Path.GetExtension(fileName);
        return fileName[..^extension.Length];
    }

    private static string GetResultDocumentStem(string path)
    {
        var fileName = Path.GetFileName(path);
        var analysisSlug = GetSegment(path, 2);
        var summarySuffix = ".summary.md";
        var analysisSuffix = $".{analysisSlug}.md";

        if (fileName.EndsWith(summarySuffix, StringComparison.OrdinalIgnoreCase))
        {
            return fileName[..^summarySuffix.Length];
        }

        return fileName.EndsWith(analysisSuffix, StringComparison.OrdinalIgnoreCase)
            ? fileName[..^analysisSuffix.Length]
            : GetDocumentStem(path);
    }

    private static string GetLookupKey(string category, string stem) =>
        $"{category}|{stem}";

    private static string GetSlugFromStem(string stem)
    {
        var parts = stem.Split('_', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return parts.Length >= 3 ? parts[2] : stem;
    }

    private static string GetDisplayName(string slug) =>
        CultureInfo.CurrentCulture.TextInfo.ToTitleCase(slug.Replace('-', ' ').Replace('_', ' '));

    private static string GetContentType(string fileName) =>
        Path.GetExtension(fileName).ToLowerInvariant() switch
        {
            ".pdf" => "application/pdf",
            ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md" => "text/markdown;charset=utf-8",
            ".txt" => "text/plain;charset=utf-8",
            _ => "application/octet-stream"
        };
}
