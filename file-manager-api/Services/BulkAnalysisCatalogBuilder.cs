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

    private static readonly HashSet<string> SupportedResultExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".html",
        ".htm",
        ".doc",
        ".docx",
        ".pdf",
        ".md",
        ".txt"
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

        var resultItemsByCategoryAndStem = BuildResultItemsByCategoryAndStem(fileItems);

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
                    foreach (var resultItem in resultItems.OrderBy(item => item.AnalysisSlug, StringComparer.OrdinalIgnoreCase))
                    {
                        var analysisSlug = resultItem.AnalysisSlug;
                        var analysisType = GetDisplayName(analysisSlug);
                        var generatedAt = resultItem.GeneratedAt.UtcDateTime;
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
                            IsPreviewAvailable: true,
                            AnalysisSlug: analysisSlug,
                            ResultPath: resultItem.Path,
                            ResultFileName: resultItem.FileName,
                            ResultContentType: resultItem.ContentType,
                            ResultExtension: resultItem.FileExtension));
                        resultReferences[resultId] = new ResultFileReference(
                            resultItem.Path,
                            resultItem.FileName,
                            resultItem.ContentType,
                            resultItem.FileExtension);
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

            var displayName = GetDisplayName(category);
            folders.Add(new BulkAnalysisFolder(category, displayName, GetDescription(displayName), null, documents, []));
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

    private static IReadOnlyDictionary<string, ResultCatalogItem[]> BuildResultItemsByCategoryAndStem(IEnumerable<AdlsPathItem> fileItems)
    {
        var resultItems = fileItems
            .Where(IsNestedResultFilePath)
            .Select(CreateResultCatalogItem)
            .ToArray();

        var duplicates = resultItems
            .GroupBy(
                item => GetResultLookupKey(item.Category, item.DocumentStem, item.AnalysisSlug),
                StringComparer.OrdinalIgnoreCase)
            .Where(group => group.Count() > 1)
            .ToArray();

        if (duplicates.Length > 0)
        {
            var duplicateMessage = string.Join(
                "; ",
                duplicates.Select(group =>
                    $"{group.Key} => {string.Join(", ", group.Select(item => item.Path).OrderBy(path => path, StringComparer.OrdinalIgnoreCase))}"));

            throw new InvalidOperationException($"Duplicate bulk analysis result files detected: {duplicateMessage}");
        }

        return resultItems
            .GroupBy(item => GetLookupKey(item.Category, item.DocumentStem), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                group => group.Key,
                group => group.ToArray(),
                StringComparer.OrdinalIgnoreCase);
    }

    private static ResultCatalogItem CreateResultCatalogItem(AdlsPathItem item)
    {
        var fileName = Path.GetFileName(item.Name);
        var fileExtension = Path.GetExtension(fileName).TrimStart('.').ToLowerInvariant();

        return new ResultCatalogItem(
            GetSegment(item.Name, 0),
            GetResultDocumentStem(item.Name),
            GetSegment(item.Name, 2),
            item.Name,
            fileName,
            GetContentType(fileName),
            fileExtension,
            item.LastModified);
    }

    private static bool IsNestedResultFilePath(AdlsPathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 4 &&
            string.Equals(parts[1], "llm_results", StringComparison.OrdinalIgnoreCase) &&
            SupportedResultExtensions.Contains(Path.GetExtension(parts[3]));
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
        var extension = Path.GetExtension(fileName);
        var analysisSuffix = $".{analysisSlug}{extension}";

        return fileName.EndsWith(analysisSuffix, StringComparison.OrdinalIgnoreCase)
            ? fileName[..^analysisSuffix.Length]
            : GetDocumentStem(path);
    }

    private static string GetLookupKey(string category, string stem) =>
        $"{category}|{stem}";

    private static string GetResultLookupKey(string category, string stem, string analysisSlug) =>
        $"{category}|{stem}|{analysisSlug}";

    private static string GetSlugFromStem(string stem)
    {
        var parts = stem.Split('_', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return parts.Length >= 3 ? parts[2] : stem;
    }

    private static string GetDisplayName(string slug) =>
        CultureInfo.CurrentCulture.TextInfo.ToTitleCase(slug.Replace('-', ' ').Replace('_', ' '));

    private static string GetDescription(string displayName) =>
        $"Documents and generated analysis results for {displayName}.";

    private static string GetContentType(string fileName) =>
        Path.GetExtension(fileName).ToLowerInvariant() switch
        {
            ".doc" => "application/msword",
            ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".htm" => "text/html;charset=utf-8",
            ".html" => "text/html;charset=utf-8",
            ".md" => "text/markdown;charset=utf-8",
            ".pdf" => "application/pdf",
            ".txt" => "text/plain;charset=utf-8",
            _ => "application/octet-stream"
        };

    private sealed record ResultCatalogItem(
        string Category,
        string DocumentStem,
        string AnalysisSlug,
        string Path,
        string FileName,
        string ContentType,
        string FileExtension,
        DateTimeOffset GeneratedAt);
}
