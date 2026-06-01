# File Manager Blazor

Standalone Blazor WebAssembly copy of the React file manager mock app.

## Run

```powershell
$env:PATH = "$env:USERPROFILE\.dotnet;$env:PATH"
dotnet run --project .\FileManagerBlazor.csproj
```

The app preserves the original demo behavior with mock file data, document analysis modes, and simulated chat responses. It does not read local files or call an AI/backend service.
