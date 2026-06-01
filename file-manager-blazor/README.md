# File Manager Blazor

Standalone Blazor WebAssembly copy of the file manager mock, reskinned to match the assistant-shell visual language used in the React demo.

## Run

```powershell
$env:PATH = "$env:USERPROFILE\.dotnet;$env:PATH"
dotnet run --project .\FileManagerBlazor.csproj
```

The app preserves the original demo behavior with mock file data, document analysis modes, and simulated chat responses. It does not read local files or call an AI/backend service.

## Styling Notes

- The page keeps the Blazor split-pane file manager layout visible by default.
- The left rail, typography, cards, borders, and controls use the newer assistant-shell token set.
- Internal `?state=...` routes exist for documentation screenshots and seeded demo captures.

## Screenshots

The refreshed screenshot gallery lives in [docs/screenshots/README.md](docs/screenshots/README.md).
