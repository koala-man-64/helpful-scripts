using System.Text;
using FileManagerBlazor.Models;
using FileManagerBlazor.Services;

namespace FileManagerApi.Tests;

public sealed class StriderMarkdownDocumentBuilderTests
{
    [Fact]
    public void Build_combines_selected_results_into_delimited_markdown_document()
    {
        var generatedAt = new DateTimeOffset(2026, 6, 16, 12, 30, 0, TimeSpan.FromHours(-5));
        var prompt = new BulkAnalysisPrompt(
            "prompt-1",
            "executive-summary",
            "Executive synthesis",
            "Summarize selected results.",
            "Use the evidence.",
            ["summary"]);
        var result = CreateResult("result-1", "Claims Guide", "Executive Summary", generatedAt.UtcDateTime);
        var resultFile = new BulkAnalysisResultFile(
            result.Id,
            "claims-guide.executive-summary.md",
            "text/markdown;charset=utf-8",
            "md",
            "claims/llm_results/executive-summary/claims-guide.executive-summary.md",
            Encoding.UTF8.GetBytes(
                """
                ---
                title: Claims Guide - Executive Summary
                format: markdown
                ---

                # Finding

                - Selected result content.
                """));

        var markdown = Normalize(StriderMarkdownDocumentBuilder.Build(
            "Use the evidence.",
            prompt,
            [result],
            new Dictionary<string, BulkAnalysisResultFile> { [result.Id] = resultFile },
            generatedAt));

        Assert.Contains("# Strider Bulk Analysis Context", markdown);
        Assert.Contains("Prompt: Executive synthesis", markdown);
        Assert.Contains("## Prompt\n\nUse the evidence.", markdown);
        Assert.Contains("---\n\n## Result 1: Claims Guide - Executive Summary", markdown);
        Assert.Contains("- Result file: claims-guide.executive-summary.md", markdown);
        Assert.Contains("# Finding\n\n- Selected result content.", markdown);
        Assert.DoesNotContain("format: markdown", markdown);
    }

    [Fact]
    public void GetFileName_uses_result_name_for_single_result()
    {
        var result = CreateResult("result-1", "Claims Guide", "Executive Summary", DateTime.UtcNow);

        var fileName = StriderMarkdownDocumentBuilder.GetFileName(
            [result],
            new DateTimeOffset(2026, 6, 16, 12, 30, 0, TimeSpan.Zero));

        Assert.Equal("claims-guide-executive-summary-strider-context.md", fileName);
    }

    private static BulkAnalysisResult CreateResult(
        string id,
        string documentTitle,
        string analysisType,
        DateTime generatedAt) =>
        new(
            id,
            "claims",
            "claims-guide",
            "Claims",
            documentTitle,
            "claims-guide.pdf",
            analysisType,
            generatedAt,
            AnalysisSlug: "executive-summary",
            ResultPath: "claims/llm_results/executive-summary/claims-guide.executive-summary.md",
            ResultFileName: "claims-guide.executive-summary.md",
            ResultContentType: "text/markdown;charset=utf-8",
            ResultExtension: "md");

    private static string Normalize(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal);
}
