namespace FileManagerBlazor.Models;

public enum AnalysisType
{
    Original,
    Summary,
    KeyPoints,
    Sentiment,
    Entities
}

public static class AnalysisTypeExtensions
{
    public static string Label(this AnalysisType analysisType) =>
        analysisType switch
        {
            AnalysisType.Original => "Original",
            AnalysisType.Summary => "Summary",
            AnalysisType.KeyPoints => "Key Points",
            AnalysisType.Sentiment => "Sentiment",
            AnalysisType.Entities => "Entities",
            _ => analysisType.ToString()
        };
}
