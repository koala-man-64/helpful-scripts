using System.Globalization;
using Azure;
using Azure.Storage.Files.DataLake;
using Azure.Storage.Files.DataLake.Models;
using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class AdlsBulkAnalysisResultProvider : IBulkAnalysisResultProvider
{
    private static readonly HashSet<string> SupportedRawExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".docx",
        ".pdf"
    };

    private readonly DataLakeFileSystemClient fileSystemClient;
    private readonly SemaphoreSlim catalogLock = new(1, 1);

    private IReadOnlyList<BulkAnalysisFolder>? cachedFolders;
    private IReadOnlyDictionary<string, RawFileReference> rawFilesByDocumentId =
        new Dictionary<string, RawFileReference>(StringComparer.Ordinal);
    private IReadOnlyDictionary<string, ResultFileReference> resultFilesById =
        new Dictionary<string, ResultFileReference>(StringComparer.Ordinal);

    public AdlsBulkAnalysisResultProvider(BulkAnalysisAdlsOptions options)
    {
        if (!options.IsConfigured)
        {
            throw new InvalidOperationException("Bulk Analysis ADLS options are incomplete.");
        }

        var fileSystemUri = new Uri(
            $"https://{options.AccountName}.dfs.core.windows.net/{options.FileSystemName}?{options.NormalizedSasToken}");

        fileSystemClient = new DataLakeFileSystemClient(fileSystemUri);
    }

    public async Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default)
    {
        await EnsureCatalogAsync(cancellationToken);
        return cachedFolders ?? [];
    }

    public async Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default)
    {
        await EnsureCatalogAsync(cancellationToken);

        if (!rawFilesByDocumentId.TryGetValue(documentId, out var reference))
        {
            return null;
        }

        var content = await DownloadBytesAsync(reference.SourcePath, cancellationToken);

        return new BulkAnalysisRawFile(
            documentId,
            reference.FileName,
            reference.ContentType,
            reference.SourcePath,
            content);
    }

    public async Task<string?> GetResultMarkdownAsync(string resultId, CancellationToken cancellationToken = default)
    {
        await EnsureCatalogAsync(cancellationToken);

        if (!resultFilesById.TryGetValue(resultId, out var reference))
        {
            return null;
        }

        return StripFrontMatter(await DownloadTextAsync(reference.ResultPath, cancellationToken));
    }

    private async Task EnsureCatalogAsync(CancellationToken cancellationToken)
    {
        if (cachedFolders is not null)
        {
            return;
        }

        await catalogLock.WaitAsync(cancellationToken);
        try
        {
            if (cachedFolders is not null)
            {
                return;
            }

            var pathItems = await ListPathsAsync(cancellationToken);
            var fileItems = pathItems
                .Where(item => item.IsDirectory != true && !string.IsNullOrWhiteSpace(item.Name))
                .ToArray();

            var transformedByStem = fileItems
                .Where(IsTransformedMarkdownPath)
                .ToDictionary(item => GetDocumentStem(item.Name), item => item, StringComparer.OrdinalIgnoreCase);

            var resultItemsByCategoryAndStem = fileItems
                .Where(IsNestedResultMarkdownPath)
                .GroupBy(item => GetResultLookupKey(GetSegment(item.Name, 0), GetResultDocumentStem(item.Name)), StringComparer.OrdinalIgnoreCase)
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

                foreach (var rawItem in categoryGroup.OrderBy(item => GetDisplayName(GetDocumentStem(item.Name)), StringComparer.OrdinalIgnoreCase))
                {
                    var rawPath = rawItem.Name;
                    var stem = GetDocumentStem(rawPath);
                    var transformedPath = $"{category}/transformed/{stem}.md";
                    var metadata = transformedByStem.ContainsKey(stem)
                        ? await DownloadMarkdownMetadataAsync(transformedPath, cancellationToken)
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
                    if (resultItemsByCategoryAndStem.TryGetValue(GetResultLookupKey(category, stem), out var resultItems))
                    {
                        foreach (var resultItem in resultItems.OrderBy(item => GetSegment(item.Name, 2), StringComparer.OrdinalIgnoreCase))
                        {
                            var analysisSlug = GetSegment(resultItem.Name, 2);
                            var analysisType = GetDisplayName(analysisSlug);
                            var generatedAt = resultItem.LastModified.DateTime;
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

                    var document = new BulkAnalysisDocument(
                        documentId,
                        category,
                        title,
                        originalFileName,
                        results,
                        SourcePath: rawPath,
                        ContentType: contentType,
                        SourceExtension: sourceExtension,
                        TransformedPath: transformedPath);

                    documents.Add(document);
                    rawReferences[documentId] = new RawFileReference(rawPath, originalFileName, contentType);
                }

                folders.Add(new BulkAnalysisFolder(category, GetDisplayName(category), null, documents, []));
            }

            cachedFolders = folders;
            rawFilesByDocumentId = rawReferences;
            resultFilesById = resultReferences;
        }
        finally
        {
            catalogLock.Release();
        }
    }

    private async Task<IReadOnlyList<PathItem>> ListPathsAsync(CancellationToken cancellationToken)
    {
        var paths = new List<PathItem>();

        await foreach (var path in fileSystemClient
            .GetPathsAsync(path: string.Empty, recursive: true, userPrincipalName: false, cancellationToken: cancellationToken)
            .ConfigureAwait(false))
        {
            paths.Add(path);
        }

        return paths;
    }

    private async Task<IReadOnlyDictionary<string, string>> DownloadMarkdownMetadataAsync(
        string path,
        CancellationToken cancellationToken)
    {
        try
        {
            return ReadFrontMatter(await DownloadTextAsync(path, cancellationToken));
        }
        catch (RequestFailedException)
        {
            return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        }
    }

    private async Task<string> DownloadTextAsync(string path, CancellationToken cancellationToken)
    {
        var bytes = await DownloadBytesAsync(path, cancellationToken);
        return System.Text.Encoding.UTF8.GetString(bytes);
    }

    private async Task<byte[]> DownloadBytesAsync(string path, CancellationToken cancellationToken)
    {
        var fileClient = fileSystemClient.GetFileClient(path);
        var response = await fileClient.ReadAsync(cancellationToken: cancellationToken);
        await using var content = response.Value.Content;
        using var memory = new MemoryStream();
        await content.CopyToAsync(memory, cancellationToken);
        return memory.ToArray();
    }

    private static bool IsRawDocumentPath(PathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 3 &&
            string.Equals(parts[1], "raw", StringComparison.OrdinalIgnoreCase) &&
            SupportedRawExtensions.Contains(Path.GetExtension(parts[2]));
    }

    private static bool IsTransformedMarkdownPath(PathItem item)
    {
        var parts = SplitPath(item.Name);
        return parts.Length == 3 &&
            string.Equals(parts[1], "transformed", StringComparison.OrdinalIgnoreCase) &&
            string.Equals(Path.GetExtension(parts[2]), ".md", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsNestedResultMarkdownPath(PathItem item)
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

    private static string GetResultLookupKey(string category, string stem) =>
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

    private static IReadOnlyDictionary<string, string> ReadFrontMatter(string markdown)
    {
        markdown = NormalizeLineEndings(markdown);

        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (!markdown.StartsWith("---\n", StringComparison.Ordinal))
        {
            return result;
        }

        var end = markdown.IndexOf("\n---\n", 4, StringComparison.Ordinal);
        if (end < 0)
        {
            return result;
        }

        foreach (var line in markdown[4..end].Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            var separatorIndex = line.IndexOf(": ", StringComparison.Ordinal);
            if (separatorIndex <= 0)
            {
                continue;
            }

            result[line[..separatorIndex]] = line[(separatorIndex + 2)..].Trim();
        }

        return result;
    }

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

    private sealed record RawFileReference(string SourcePath, string FileName, string ContentType);

    private sealed record ResultFileReference(string ResultPath);
}
