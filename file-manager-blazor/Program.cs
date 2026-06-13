using Microsoft.AspNetCore.Components.Web;
using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using FileManagerBlazor;
using FileManagerBlazor.Services;

var builder = WebAssemblyHostBuilder.CreateDefault(args);
builder.RootComponents.Add<App>("#app");
builder.RootComponents.Add<HeadOutlet>("head::after");

var bulkAnalysisApiOptions = ReadBulkAnalysisApiOptions(builder.Configuration);
builder.Services.AddSingleton(bulkAnalysisApiOptions);

builder.Services.AddScoped(sp => new HttpClient
{
    BaseAddress = ResolveApiBaseAddress(builder.HostEnvironment.BaseAddress, bulkAnalysisApiOptions.BaseUrl)
});
builder.Services.AddScoped<IBulkAnalysisResultProvider>(sp =>
{
    var apiProvider = new ApiBulkAnalysisResultProvider(sp.GetRequiredService<HttpClient>());
    if (!string.Equals(builder.HostEnvironment.Environment, "Development", StringComparison.OrdinalIgnoreCase))
    {
        return apiProvider;
    }

    return new FallbackBulkAnalysisResultProvider(apiProvider, new MockBulkAnalysisResultProvider());
});

await builder.Build().RunAsync();

static BulkAnalysisApiOptions ReadBulkAnalysisApiOptions(IConfiguration configuration)
{
    var section = configuration.GetSection(BulkAnalysisApiOptions.SectionName);

    return new BulkAnalysisApiOptions
    {
        BaseUrl = section["BaseUrl"] ?? string.Empty
    };
}

static Uri ResolveApiBaseAddress(string hostBaseAddress, string configuredBaseUrl)
{
    if (string.IsNullOrWhiteSpace(configuredBaseUrl))
    {
        return new Uri(hostBaseAddress);
    }

    return Uri.TryCreate(configuredBaseUrl, UriKind.Absolute, out var absoluteUri)
        ? EnsureTrailingSlash(absoluteUri)
        : new Uri(new Uri(hostBaseAddress), configuredBaseUrl);
}

static Uri EnsureTrailingSlash(Uri uri)
{
    var value = uri.ToString();
    return value.EndsWith("/", StringComparison.Ordinal) ? uri : new Uri($"{value}/");
}
