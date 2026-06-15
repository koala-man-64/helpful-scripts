using System.Text.Json;
using FileManagerBlazor.Models;

namespace FileManagerApi.Services;

public static class BulkAnalysisPromptCatalogBuilder
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public static IReadOnlyList<BulkAnalysisPrompt> BuildFromJson(
        string json,
        string? sourcePath = null)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return [];
        }

        var prompts = JsonSerializer.Deserialize<IReadOnlyList<BulkAnalysisPrompt>>(json, SerializerOptions) ?? [];
        var duplicates = prompts
            .Where(prompt => !string.IsNullOrWhiteSpace(prompt.Id))
            .GroupBy(prompt => prompt.Id.Trim(), StringComparer.OrdinalIgnoreCase)
            .Where(group => group.Count() > 1)
            .Select(group => group.Key)
            .OrderBy(id => id, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (duplicates.Length > 0)
        {
            throw new InvalidOperationException($"Duplicate bulk analysis prompt ids detected: {string.Join(", ", duplicates)}");
        }

        return prompts
            .Select(prompt => NormalizePrompt(prompt, sourcePath))
            .Where(prompt => !string.IsNullOrWhiteSpace(prompt.Id))
            .OrderBy(prompt => prompt.DisplayName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(prompt => prompt.Id, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static BulkAnalysisPrompt NormalizePrompt(BulkAnalysisPrompt prompt, string? sourcePath)
    {
        var id = prompt.Id?.Trim() ?? string.Empty;
        var displayName = prompt.DisplayName?.Trim() ?? string.Empty;

        var tags = (prompt.Tags ?? [])
            .Where(tag => !string.IsNullOrWhiteSpace(tag))
            .Select(tag => tag.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(tag => tag, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        return prompt with
        {
            Id = id,
            AnalysisSlug = prompt.AnalysisSlug?.Trim() ?? string.Empty,
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? id : displayName,
            Description = prompt.Description?.Trim() ?? string.Empty,
            PromptText = prompt.PromptText?.Trim() ?? string.Empty,
            Tags = tags,
            SourcePath = string.IsNullOrWhiteSpace(prompt.SourcePath) ? sourcePath : prompt.SourcePath
        };
    }
}
