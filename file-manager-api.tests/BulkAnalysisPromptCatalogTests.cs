using FileManagerApi.Services;
using FileManagerBlazor.Models;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Logging.Abstractions;

namespace FileManagerApi.Tests;

public sealed class BulkAnalysisPromptCatalogTests
{
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
