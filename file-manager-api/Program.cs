using Azure;
using FileManagerApi.Services;

var builder = WebApplication.CreateBuilder(args);
AddSharedAppSettings(builder.Configuration, builder.Environment, args);

var adlsOptions = ReadBulkAnalysisAdlsOptions(builder.Configuration);
var renderingOptions = ReadBulkAnalysisRenderingOptions(builder.Configuration);
builder.Services.AddSingleton(adlsOptions);
builder.Services.AddSingleton(renderingOptions);
builder.Services.AddMemoryCache();
builder.Services.AddSingleton<IBulkAnalysisDocumentConverter, LibreOfficeBulkAnalysisDocumentConverter>();
builder.Services.AddSingleton<IBulkAnalysisResultPreviewBuilder, BulkAnalysisResultPreviewBuilder>();
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

app.Use(async (context, next) =>
{
    try
    {
        await next(context);
    }
    catch (Exception ex) when (IsBulkAnalysisRoute(context) && IsBulkAnalysisServiceException(ex))
    {
        app.Logger.LogWarning(ex, "Bulk Analysis catalog is unavailable.");
        await Results.Problem(
            title: "Bulk Analysis catalog unavailable",
            detail: GetBulkAnalysisErrorDetail(ex),
            statusCode: StatusCodes.Status503ServiceUnavailable)
            .ExecuteAsync(context);
    }
});

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

bulkAnalysis.MapGet("/results/file", async (
    string resultId,
    IBulkAnalysisCatalogService catalogService,
    CancellationToken cancellationToken) =>
{
    var resultFile = await catalogService.GetResultFileAsync(resultId, cancellationToken);
    return resultFile is null
        ? Results.NotFound()
        : Results.Ok(resultFile);
});

bulkAnalysis.MapGet("/results/preview", async (
    string resultId,
    IBulkAnalysisCatalogService catalogService,
    CancellationToken cancellationToken) =>
{
    var preview = await catalogService.GetResultPreviewAsync(resultId, cancellationToken);
    return preview is null
        ? Results.NotFound()
        : Results.Ok(preview);
});

app.Run();

static BulkAnalysisAdlsOptions ReadBulkAnalysisAdlsOptions(IConfiguration configuration)
{
    var section = configuration.GetSection(BulkAnalysisAdlsOptions.SectionName);

    return new BulkAnalysisAdlsOptions
    {
        ConnectionString = FirstConfiguredValue(section["ConnectionString"], configuration["ADLS_CONNECTION_STRING"]),
        FileSystemName = FirstConfiguredValue(section["FileSystemName"], configuration["ADLS_FILE_SYSTEM"]),
        CatalogCacheMinutes = int.TryParse(
            FirstConfiguredValue(section["CatalogCacheMinutes"], configuration["ADLS_CATALOG_CACHE_MINUTES"]),
            out var cacheMinutes)
            ? cacheMinutes
            : 5
    };
}

static BulkAnalysisRenderingOptions ReadBulkAnalysisRenderingOptions(IConfiguration configuration)
{
    var section = configuration.GetSection(BulkAnalysisRenderingOptions.SectionName);

    return new BulkAnalysisRenderingOptions
    {
        LibreOfficePath = section["LibreOfficePath"] ?? string.Empty
    };
}

static bool IsBulkAnalysisRoute(HttpContext context) =>
    context.Request.Path.StartsWithSegments("/api/bulk-analysis", StringComparison.OrdinalIgnoreCase);

static bool IsBulkAnalysisServiceException(Exception ex) =>
    ex is InvalidOperationException or FormatException or RequestFailedException;

static string GetBulkAnalysisErrorDetail(Exception ex) =>
    ex is InvalidOperationException invalidOperationException
        ? invalidOperationException.Message == "Bulk Analysis ADLS options are incomplete."
            ? "Configure BulkAnalysisAdls__ConnectionString and BulkAnalysisAdls__FileSystemName, or ADLS_CONNECTION_STRING and ADLS_FILE_SYSTEM, before starting the API."
            : invalidOperationException.Message
        : "The API could not read the configured ADLS catalog. Check the storage connection string, file system name, and account permissions.";

static string FirstConfiguredValue(params string?[] values) =>
    values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? string.Empty;

static void AddSharedAppSettings(
    ConfigurationManager configuration,
    IWebHostEnvironment environment,
    string[] args)
{
    var sharedSettingsPath = Path.GetFullPath(Path.Combine(environment.ContentRootPath, "..", "appsettings.json"));
    configuration
        .AddJsonFile(sharedSettingsPath, optional: true, reloadOnChange: true)
        .AddEnvironmentVariables();

    if (args.Length > 0)
    {
        configuration.AddCommandLine(args);
    }
}

public partial class Program
{
}
