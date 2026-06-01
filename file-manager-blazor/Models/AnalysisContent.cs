namespace FileManagerBlazor.Models;

public sealed record AnalysisContent(
    string Original,
    string Summary,
    string KeyPoints,
    string Sentiment,
    string Entities)
{
    public string GetText(AnalysisType analysisType) =>
        analysisType switch
        {
            AnalysisType.Original => Original,
            AnalysisType.Summary => Summary,
            AnalysisType.KeyPoints => KeyPoints,
            AnalysisType.Sentiment => Sentiment,
            AnalysisType.Entities => Entities,
            _ => Original
        };
}
