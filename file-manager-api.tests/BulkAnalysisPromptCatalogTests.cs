using FileManagerApi.Services;
using FileManagerBlazor.Models;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Logging.Abstractions;
using System.Runtime.CompilerServices;

namespace FileManagerApi.Tests;

public sealed class BulkAnalysisPromptCatalogTests
{
    [Fact]
    public async Task SeedPromptCatalog_parses_expected_contextual_prompts()
    {
        var catalogPath = Path.Combine(GetRepositoryRoot(), "file-manager-api", "Data", "Prompts", "catalog.json");
        var prompts = BulkAnalysisPromptCatalogBuilder.BuildFromJson(
            await File.ReadAllTextAsync(catalogPath),
            "Data/Prompts/catalog.json");

        Assert.Equal(8, prompts.Count);
        Assert.Equal(prompts.Count, prompts.Select(prompt => prompt.Id).Distinct(StringComparer.OrdinalIgnoreCase).Count());
        Assert.All(prompts, prompt => Assert.Contains("contextual-analysis", prompt.Tags, StringComparer.OrdinalIgnoreCase));
        Assert.All(prompts, AssertPromptIsComplete);
        Assert.Contains(prompts, prompt => prompt.Id == "executive-synthesis" && prompt.AnalysisSlug == "summary");
        Assert.Contains(prompts, prompt => prompt.Id == "data-quality-reconciliation" && prompt.AnalysisSlug == "data-quality-review");
    }

    [Fact]
    public void BuildFromJson_sorts_prompts_by_display_name()
    {
        var prompts = BulkAnalysisPromptCatalogBuilder.BuildFromJson(
            """
            [
              {
                "id": "risk",
                "analysisSlug": "risk-review",
                "displayName": "Risk Review",
                "description": "Risk review prompt.",
                "promptText": "Review risk.",
                "tags": [ "risk" ]
              },
              {
                "id": "summary",
                "analysisSlug": "summary",
                "displayName": "Summary",
                "description": "Summary prompt.",
                "promptText": "Summarize.",
                "tags": [ "summary" ]
              }
            ]
            """,
            "prompts/catalog.json");

        Assert.Equal(["Risk Review", "Summary"], prompts.Select(prompt => prompt.DisplayName).ToArray());
        Assert.All(prompts, prompt => Assert.Equal("prompts/catalog.json", prompt.SourcePath));
    }

    [Fact]
    public void BuildFromJson_rejects_duplicate_prompt_ids()
    {
        var exception = Assert.Throws<InvalidOperationException>(() =>
            BulkAnalysisPromptCatalogBuilder.BuildFromJson(
                """
                [
                  {
                    "id": "summary",
                    "analysisSlug": "summary",
                    "displayName": "Summary",
                    "description": "Summary prompt.",
                    "promptText": "Summarize.",
                    "tags": [ "summary" ]
                  },
                  {
                    "id": "SUMMARY",
                    "analysisSlug": "executive-summary",
                    "displayName": "Executive Summary",
                    "description": "Executive summary prompt.",
                    "promptText": "Summarize for executives.",
                    "tags": [ "summary" ]
                  }
                ]
                """));

        Assert.Contains("Duplicate bulk analysis prompt ids detected", exception.Message);
        Assert.Contains("summary", exception.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void BuildFromJson_allows_multiple_prompts_for_same_analysis_slug()
    {
        var prompts = BulkAnalysisPromptCatalogBuilder.BuildFromJson(
            """
            [
              {
                "id": "detailed-summary",
                "analysisSlug": "summary",
                "displayName": "Detailed Summary",
                "description": "Detailed prompt.",
                "promptText": "Create a detailed summary.",
                "tags": [ "summary" ]
              },
              {
                "id": "brief-summary",
                "analysisSlug": "summary",
                "displayName": "Brief Summary",
                "description": "Brief prompt.",
                "promptText": "Create a brief summary.",
                "tags": [ "summary" ]
              }
            ]
            """);

        Assert.Equal(["Brief Summary", "Detailed Summary"], prompts.Select(prompt => prompt.DisplayName).ToArray());
    }

    [Fact]
    public async Task PromptCatalogService_returns_empty_catalog_when_local_file_is_missing()
    {
        using var memoryCache = new MemoryCache(new MemoryCacheOptions());
        var service = CreateService(
            new BulkAnalysisPromptCatalogOptions { LocalCatalogPath = "missing-prompts.json" },
            new BulkAnalysisAdlsOptions(),
            memoryCache,
            Path.GetTempPath());

        var prompts = await service.GetPromptsAsync();

        Assert.Empty(prompts);
    }

    [Fact]
    public async Task PromptCatalogService_returns_empty_catalog_when_adls_options_are_placeholder()
    {
        using var memoryCache = new MemoryCache(new MemoryCacheOptions());
        var service = CreateService(
            new BulkAnalysisPromptCatalogOptions { LocalCatalogPath = "missing-prompts.json" },
            new BulkAnalysisAdlsOptions
            {
                ConnectionString = "DefaultEndpointsProtocol=https;AccountName=your_account;AccountKey=your_key;EndpointSuffix=core.windows.net",
                FileSystemName = "bulk-analysis"
            },
            memoryCache,
            Path.GetTempPath());

        var prompts = await service.GetPromptsAsync();

        Assert.Empty(prompts);
    }

    [Fact]
    public async Task PromptCatalogService_loads_local_prompt_catalog()
    {
        var directory = Directory.CreateTempSubdirectory("bulk-prompts-");
        var catalogPath = Path.Combine(directory.FullName, "catalog.json");
        await File.WriteAllTextAsync(
            catalogPath,
            """
            [
              {
                "id": "context-summary",
                "analysisSlug": "summary",
                "displayName": "Context Summary",
                "description": "Summary prompt.",
                "promptText": "Summarize selected results.",
                "tags": [ "contextual-analysis" ]
              }
            ]
            """);

        try
        {
            using var memoryCache = new MemoryCache(new MemoryCacheOptions());
            var service = CreateService(
                new BulkAnalysisPromptCatalogOptions { LocalCatalogPath = catalogPath },
                new BulkAnalysisAdlsOptions(),
                memoryCache,
                directory.FullName);

            var prompts = await service.GetPromptsAsync();

            var prompt = Assert.Single(prompts);
            Assert.Equal("context-summary", prompt.Id);
            Assert.Equal(catalogPath, prompt.SourcePath);
        }
        finally
        {
            directory.Delete(recursive: true);
        }
    }

    private static BulkAnalysisPromptCatalogService CreateService(
        BulkAnalysisPromptCatalogOptions options,
        BulkAnalysisAdlsOptions adlsOptions,
        IMemoryCache memoryCache,
        string contentRootPath) =>
        new(
            options,
            adlsOptions,
            memoryCache,
            new StubEnvironment(contentRootPath),
            NullLogger<BulkAnalysisPromptCatalogService>.Instance);

    private static string GetRepositoryRoot([CallerFilePath] string sourceFilePath = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(sourceFilePath)!, ".."));

    private static void AssertPromptIsComplete(BulkAnalysisPrompt prompt)
    {
        Assert.Equal("1.1.0", prompt.Version);
        Assert.Contains("Role:", prompt.PromptText, StringComparison.Ordinal);
        Assert.Contains("Task:", prompt.PromptText, StringComparison.Ordinal);
        Assert.Contains("Source handling:", prompt.PromptText, StringComparison.Ordinal);
        Assert.Contains("Output format:", prompt.PromptText, StringComparison.Ordinal);
        Assert.Contains("Style:", prompt.PromptText, StringComparison.Ordinal);
        Assert.True(prompt.PromptText.Length >= 900, $"Prompt {prompt.Id} should be complete, not sentence-length.");
    }

    private sealed class StubEnvironment(string contentRootPath) : IWebHostEnvironment
    {
        public string ApplicationName { get; set; } = "file-manager-api.tests";

        public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();

        public string ContentRootPath { get; set; } = contentRootPath;

        public string EnvironmentName { get; set; } = "Development";

        public string WebRootPath { get; set; } = contentRootPath;

        public IFileProvider WebRootFileProvider { get; set; } = new NullFileProvider();
    }
}
