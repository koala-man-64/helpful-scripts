using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class AssistantLaunchState
{
    private AssistantLaunchContext? pendingContext;

    public void SetPending(AssistantLaunchContext context)
    {
        pendingContext = context;
    }

    public AssistantLaunchContext? ConsumePending()
    {
        var context = pendingContext;
        pendingContext = null;
        return context;
    }
}

public sealed record AssistantLaunchContext(
    string PromptText,
    BulkAnalysisPrompt? Prompt,
    IReadOnlyList<AssistantLaunchResultContext> Results,
    DateTimeOffset CreatedAt);

public sealed record AssistantLaunchResultContext(
    string ResultId,
    string DocumentId,
    string FolderName,
    string DocumentTitle,
    string OriginalFileName,
    string AnalysisType,
    string FileName,
    string ContentType,
    string FileExtension,
    byte[] Content);
