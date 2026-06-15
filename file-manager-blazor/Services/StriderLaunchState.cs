using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class StriderLaunchState
{
    private StriderLaunchContext? pendingContext;

    public void SetPending(StriderLaunchContext context)
    {
        pendingContext = context;
    }

    public StriderLaunchContext? ConsumePending()
    {
        var context = pendingContext;
        pendingContext = null;
        return context;
    }
}

public sealed record StriderLaunchContext(
    string PromptText,
    BulkAnalysisPrompt? Prompt,
    IReadOnlyList<StriderLaunchResultContext> Results,
    DateTimeOffset CreatedAt);

public sealed record StriderLaunchResultContext(
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
