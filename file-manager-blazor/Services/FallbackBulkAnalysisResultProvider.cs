using FileManagerBlazor.Models;

namespace FileManagerBlazor.Services;

internal sealed class FallbackBulkAnalysisResultProvider(
    IBulkAnalysisResultProvider primary,
    IBulkAnalysisResultProvider fallback) : IBulkAnalysisResultProvider
{
    public async Task<IReadOnlyList<BulkAnalysisFolder>> GetFoldersAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            return await primary.GetFoldersAsync(cancellationToken);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested && CanUseFallback(ex))
        {
            return await fallback.GetFoldersAsync(cancellationToken);
        }
    }

    public async Task<BulkAnalysisRawFile?> GetRawFileAsync(string documentId, CancellationToken cancellationToken = default)
    {
        try
        {
            return await primary.GetRawFileAsync(documentId, cancellationToken);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested && CanUseFallback(ex))
        {
            return await fallback.GetRawFileAsync(documentId, cancellationToken);
        }
    }

    public async Task<BulkAnalysisResultFile?> GetResultFileAsync(string resultId, CancellationToken cancellationToken = default)
    {
        try
        {
            return await primary.GetResultFileAsync(resultId, cancellationToken);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested && CanUseFallback(ex))
        {
            return await fallback.GetResultFileAsync(resultId, cancellationToken);
        }
    }

    public async Task<BulkAnalysisResultPreview?> GetResultPreviewAsync(string resultId, CancellationToken cancellationToken = default)
    {
        try
        {
            return await primary.GetResultPreviewAsync(resultId, cancellationToken);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested && CanUseFallback(ex))
        {
            return await fallback.GetResultPreviewAsync(resultId, cancellationToken);
        }
    }

    private static bool CanUseFallback(Exception ex) =>
        ex is HttpRequestException or InvalidOperationException or TaskCanceledException;
}
