using Microsoft.AspNetCore.Components.Web;
using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using FileManagerBlazor;
using FileManagerBlazor.Services;

var builder = WebAssemblyHostBuilder.CreateDefault(args);
builder.RootComponents.Add<App>("#app");
builder.RootComponents.Add<HeadOutlet>("head::after");

builder.Services.AddScoped(sp => new HttpClient { BaseAddress = new Uri(builder.HostEnvironment.BaseAddress) });

var bulkAnalysisAdlsOptions = ReadBulkAnalysisAdlsOptions(builder.Configuration);
builder.Services.AddSingleton(bulkAnalysisAdlsOptions);

if (bulkAnalysisAdlsOptions.IsConfigured)
{
    builder.Services.AddSingleton<IBulkAnalysisResultProvider, AdlsBulkAnalysisResultProvider>();
}
else
{
    builder.Services.AddSingleton<IBulkAnalysisResultProvider, MockBulkAnalysisResultProvider>();
}

await builder.Build().RunAsync();

static BulkAnalysisAdlsOptions ReadBulkAnalysisAdlsOptions(IConfiguration configuration)
{
    var section = configuration.GetSection(BulkAnalysisAdlsOptions.SectionName);

    return new BulkAnalysisAdlsOptions
    {
        Enabled = bool.TryParse(section["Enabled"], out var enabled) && enabled,
        AccountName = section["AccountName"] ?? string.Empty,
        FileSystemName = section["FileSystemName"] ?? string.Empty,
        SasToken = section["SasToken"] ?? string.Empty
    };
}
