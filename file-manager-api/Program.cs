using FileManagerApi.Services;

var builder = WebApplication.CreateBuilder(args);

var adlsOptions = ReadBulkAnalysisAdlsOptions(builder.Configuration);
builder.Services.AddSingleton(adlsOptions);
builder.Services.AddMemoryCache();
builder.Services.AddSingleton<IBulkAnalysisCatalogService, AdlsBulkAnalysisCatalogService>();

var allowedOrigins = builder.Configuration
    .GetSection("Cors:AllowedOrigins")
    .GetChildren()
    .Select(origin => origin.Value)
    .Where(origin => !string.IsNullOrWhiteSpace(origin))
    .Select(origin => origin!)
    .ToArray();

builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
    {
        if (allowedOrigins.Length > 0)
        {
            policy.WithOrigins(allowedOrigins).AllowAnyHeader().AllowAnyMethod();
        }
        else if (builder.Environment.IsDevelopment())
        {
            policy.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod();
        }
    });
});

var app = builder.Build();

app.UseCors();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

var bulkAnalysis = app.MapGroup("/api/bulk-analysis");

bulkAnalysis.MapGet("/folders", async (
    IBulkAnalysisCatalogService catalogService,
    CancellationToken cancellationToken) =>
{
    var folders = await catalogService.GetFoldersAsync(cancellationToken);
    return Results.Ok(folders);
});

bulkAnalysis.MapGet("/raw", async (
    string documentId,
    IBulkAnalysisCatalogService catalogService,
    CancellationToken cancellationToken) =>
{
    var rawFile = await catalogService.GetRawFileAsync(documentId, cancellationToken);
    return rawFile is null ? Results.NotFound() : Results.Ok(rawFile);
});

bulkAnalysis.MapGet("/results/markdown", async (
    string resultId,
    IBulkAnalysisCatalogService catalogService,
    CancellationToken cancellationToken) =>
{
    var markdown = await catalogService.GetResultMarkdownAsync(resultId, cancellationToken);
    return markdown is null
        ? Results.NotFound()
        : Results.Text(markdown, "text/markdown; charset=utf-8");
});

app.Run();

static BulkAnalysisAdlsOptions ReadBulkAnalysisAdlsOptions(IConfiguration configuration)
{
    var section = configuration.GetSection(BulkAnalysisAdlsOptions.SectionName);

    return new BulkAnalysisAdlsOptions
    {
        ConnectionString = section["ConnectionString"] ?? string.Empty,
        FileSystemName = section["FileSystemName"] ?? string.Empty,
        CatalogCacheMinutes = int.TryParse(section["CatalogCacheMinutes"], out var cacheMinutes)
            ? cacheMinutes
            : 5
    };
}

public partial class Program
{
}
