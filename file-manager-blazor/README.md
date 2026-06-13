# File Manager Blazor

Standalone Blazor WebAssembly copy of the file manager mock, reskinned to match the assistant-shell visual language used in the React demo.

## Run

Start the cached ADLS API first:

```powershell
$env:BulkAnalysisAdls__ConnectionString = "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
$env:BulkAnalysisAdls__FileSystemName = "bulk-analysis"
$env:BulkAnalysisAdls__CatalogCacheMinutes = "5"
dotnet run --project ..\file-manager-api\file-manager-api.csproj
```

Then start the Blazor WebAssembly app:

```powershell
$env:PATH = "$env:USERPROFILE\.dotnet;$env:PATH"
dotnet run --project .\FileManagerBlazor.csproj
```

The bulk analysis page reads ADLS categories and documents through the API configured by `wwwroot/appsettings.json` under `BulkAnalysisApi:BaseUrl`. The ADLS connection string stays server-side in the API process.

## Styling Notes

- The page keeps the Blazor split-pane file manager layout visible by default.
- The left rail, typography, cards, borders, and controls use the newer assistant-shell token set.
- Internal `?state=...` routes exist for documentation screenshots and seeded demo captures.

## Screenshots

The refreshed screenshot gallery lives in [docs/screenshots/README.md](docs/screenshots/README.md).
