using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

public sealed class ChatContextState
{
    public const int ContextLimitTokens = 100_000;

    public event Action? Changed;

    public IReadOnlyList<BulkAnalysisContextItem> LoadedItems { get; private set; } = [];

    public int LoadedTokenEstimate => LoadedItems.Sum(item => item.EstimatedTokens);

    public DateTime? LoadedAt { get; private set; }

    public bool HasLoadedContext => LoadedItems.Count > 0;

    public void Load(IEnumerable<BulkAnalysisResult> results)
    {
        LoadedItems = results
            .OrderBy(result => result.FolderName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(result => result.SopTitle, StringComparer.OrdinalIgnoreCase)
            .ThenBy(result => result.AnalysisType, StringComparer.OrdinalIgnoreCase)
            .Select(BulkAnalysisContextItem.FromResult)
            .ToArray();
        LoadedAt = DateTime.Now;
        NotifyChanged();
    }

    public void Clear()
    {
        LoadedItems = [];
        LoadedAt = null;
        NotifyChanged();
    }

    private void NotifyChanged() => Changed?.Invoke();
}
