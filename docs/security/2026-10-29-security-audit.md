# Security audit: secrets, dependencies, and risky code (2026-10-29)

**Scope:** the `helpful-scripts` repository, covering the full git history, the pending local changes at audit time (mostly the `agent-browser` package split), and every dependency manifest.

**Verdict:** no real secrets were found in history or in pending changes, and no dependency has a known vulnerability. One repository hygiene gap was found and fixed. One risk in the developer machine's configuration remains open.

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| F1 | `.codex-remote-attachments/` (user photos) was untracked but not gitignored | Medium (privacy) | Fixed in [#327](https://github.com/koala-man-64/helpful-scripts/pull/327) |
| F2 | The global pip config adds a private feed as `extra-index-url`, which allows dependency confusion | Medium (supply chain, machine config) | Open; owner action |
| - | Secrets in history: 29 pattern hits, all synthetic test fixtures or placeholders | None | No action |
| - | Known CVEs in Python and NuGet dependencies | None | No action |
| - | Risky code patterns in pending changes | None | No action |

## Audited state

| Item | Value |
|------|-------|
| Date | 2026-10-29 |
| Local `main` HEAD | `4520932` plus uncommitted and staged changes (56 staged and 30 unstaged files) |
| History scanned | All local refs, 760 commits (`git log --all -p`) |
| Tools | git 2.53.0, pip-audit 2.10.0 (OSV/PyPI advisory data), .NET SDK 10.0.300 (`dotnet list package --vulnerable`), a custom regex scanner (described below) |
| Not available | gitleaks, trufflehog and detect-secrets are not installed, so the secret scan is pattern-based (see Limitations) |

## 1. Leaked secrets

### Method

A regex scanner read three inputs:

1. `git log --all -p --no-color`: every added and removed line in every commit on every local ref.
2. `git diff --cached` and `git diff`: the pending staged and unstaged changes.
3. The contents of untracked, non-ignored text files.

The scanner looked for these patterns: AWS access key IDs, GitHub tokens (classic and fine-grained), OpenAI and Anthropic API keys, Slack tokens, Google API keys, PEM private-key headers, Azure storage `AccountKey=` / SAS signatures, JWTs, Azure DevOps PATs in assignment context, generic `password|secret|api_key|client_secret|token = "<12+ chars>"` assignments, and `scheme://user:password@host` URLs. A match was suppressed when the matched text itself looked like a placeholder (`example`, `your_`, `<...>`, `${...}`, `redacted`, `test`, and similar).

Separately, every path ever committed was checked for sensitive filenames (`.env*`, `*.pem`, `*.pfx`, `*.key`, `id_rsa`, `credentials`, `secrets.*`, `appsettings*.json`, `.npmrc`, `.pypirc`, `config.local*`).

### Results

**Pending changes and untracked files: 0 hits.**

**History: 29 unique hits, all non-secrets.** Every hit falls into one of these groups:

| Location (file, commits) | What matched | Why it is not a secret |
|---|---|---|
| `agentic-ide-setup/tests/test_claude_setup.py` (`418882117920`, `d73a42ad355a`, removed in `458a93c53f1c`) | GitHub-token, Anthropic-key and proxy-URL formats | Synthetic fixtures for the setup script's own secret detector: the prefix followed by the alphabet and the digits 0-9, and a made-up proxy user and password. They exist to prove the detector rejects these formats. |
| `agentic-ide-setup/tests/test_setup.py` (`c31e3ca2a075`, `6d9d278a7c80`) | `api_key = "..."` assignment | The alphabet and digits as a negative test for `Test-SafeProfileContent`. |
| `scripts/Find-LaunchDarklySdkCredential.ps1` and `.README.md` (several commits) | `$KnownSdkSecret = '...'` | The literal placeholder `REPLACE_WITH_EXPOSED_SDK_SECRET`. |
| `terraform-token-investigator/Test-Investigate-TerraformToken.ps1` (`6c509ef3fe9c`, `b9f86f320c3c`) | `token = '...'` | Mock API responses with values such as `DO_NOT_OUTPUT`. |
| `codex-token-usage-audit/tests/test_amplification_report.py` (`b6f587d32a04`, `c0d5666a23ca`) | `secret = "..."` | A sentinel string used to prove a report does not keep tool bodies. |

**Sensitive filenames ever committed:** `.env`, `.env.example`, `appsettings.json`, `file-manager-api/appsettings.Development.json`, `file-manager-blazor/wwwroot/appsettings.json`, `lite-llm/.env.example`. Their committed contents were inspected at every relevant revision:

- `.env` only ever held template placeholders (`your_discover_username`, `AccountKey=your_key`, and so on). It was later untracked in favor of `.env.example`, and `.gitignore` now ignores `.env` and `.env.*` but keeps `!.env.example`.
- The `appsettings*.json` files hold only localhost URLs, CORS origins, cache settings, and an empty `ConnectionString`.

### Explanation

The repository contains secret-detection tooling, so its tests have to carry realistic-looking token formats. Those fixtures are why pattern scanners flag the repository. Every flagged value is low-entropy and obviously synthetic. No credential needs rotation.

## 2. Dependencies (bad or vulnerable packages)

### Inventory

| Manifest | Runtime dependencies |
|---|---|
| `agent-browser/pyproject.toml` | `playwright>=1.61,<2`; optional `mcp>=1.29,<2` |
| `mcp-chatbot/pyproject.toml` | `mcp`, `openai`, `azure-ai-projects`, `azure-identity`, `python-dotenv`, `numpy`, `pypdf`, `python-docx` (all with upper bounds) |
| `servicenow-client/pyproject.toml` | `python-dotenv` (HTTP through stdlib `urllib`) |
| `edge-pyodide/pyproject.toml` | none |
| `codex-workflow/requirements-host-evidence.txt` | `jsonschema==4.26.0` |
| `file-manager-*` `.csproj` (4 projects) | `Azure.Storage.Files.DataLake`, `Markdig`, `Microsoft.AspNetCore.Components.WebAssembly(.DevServer)`, xunit / coverlet / test SDK |
| `lite-llm/compose.yaml` | `ghcr.io/berriai/litellm-non_root:v1.99.1`, pinned by `sha256` digest |

### Method and results

```bash
# PyPI-only resolution; the machine's private extra index needs interactive auth
PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= PIP_NO_INPUT=1 pip-audit agent-browser
```

The same command was run for `mcp-chatbot` and `servicenow-client`, and `pip-audit -r codex-workflow/requirements-host-evidence.txt` for the jsonschema pin.

```bash
dotnet list <project>.csproj package --vulnerable --include-transitive
```

That command was run for each of the four `.csproj` projects.

| Check | Result |
|---|---|
| pip-audit `agent-browser` | No known vulnerabilities found |
| pip-audit `mcp-chatbot` | No known vulnerabilities found |
| pip-audit `servicenow-client` | No known vulnerabilities found |
| pip-audit `jsonschema==4.26.0` | No known vulnerabilities found |
| NuGet, 4 projects (including transitive dependencies) | "has no vulnerable packages given the current sources" (nuget.org) |
| Typosquatting review | Every package name is the canonical, well-known publisher package |

### Explanation

The Python dependencies use bounded ranges, so the audit resolved and checked today's newest allowed versions. A future release within those ranges could still introduce a vulnerability. The NuGet versions are exact pins. The container image is pinned by digest, so a re-tagged image cannot be swapped in silently.

## 3. Risky code patterns in pending changes

### Method

Added lines in the staged and unstaged diffs, and the whole tracked tree outside docs, were searched for `shell=True`, `eval(`, `exec(`, `pickle` / `marshal` loads, `yaml.load(`, `0.0.0.0` binds, `verify=False` / `CERT_NONE`, `Invoke-Expression` / `iex`, `DownloadString`, `curl ... | sh`, browser sandbox-disabling flags, `chmod 777`, and every `subprocess` call. Each match was read in context.

### Results

- **agent-browser daemon IPC** (`agent_browser_core/daemon.py`): binds only to `127.0.0.1` on an ephemeral port, rejects clients whose address is not `127.0.0.1` or `::1`, and authenticates every request with a per-launch token compared by `hmac.compare_digest`. Command IDs come from `secrets.token_hex`.
- **Subprocess use**: every call passes an argument list, none uses `shell=True`, and all have timeouts. The two PowerShell `-Command` scripts (the process lookup and the optional WMI launcher) build their single-quoted literals with `'` → `''` escaping, which is the correct quoting for PowerShell single-quoted strings.
- **Secret handling in the browser tool**: typing into or reading password, OTP and token fields is guarded, and screenshots mask those fields.
- **`lite-llm/compose.yaml`**: `--host 0.0.0.0` applies inside the container only. The host mapping is `127.0.0.1:4000:4000`, with `cap_drop: [ALL]`, `no-new-privileges`, and resource limits.
- **Remaining tree matches**: only the hook guard in `hooks/codex-workflow-hooks/.../guards.py`, which detects `eval` / `iex` in order to block it.

## 4. Findings

### F1: personal attachments could be committed (fixed)

- **Evidence:** `git status` listed `.codex-remote-attachments/` as untracked (`??`). It contained six JPEG photos from Codex remote sessions, and no `.gitignore` rule covered it.
- **Impact:** a broad `git add -A` or `git add .` would have published personal photos to this **public** repository.
- **Fix:** [#327](https://github.com/koala-man-64/helpful-scripts/pull/327) (squash commit `c014d2f`) adds `.codex-remote-attachments/` to `.gitignore`. Verified with `git check-ignore -v` on a nested file (matched `.gitignore:20`). The GitGuardian check passed.
- **Note:** each local checkout is protected only after it merges a `main` that includes `c014d2f`.

### F2: dependency confusion through `extra-index-url` (open, machine configuration)

- **Evidence:** the developer's global pip configuration adds a private Azure Artifacts feed as an **extra** index alongside PyPI. The feed URL is intentionally left out of this public document.
- **Impact:** with `extra-index-url`, pip treats both indexes as equals and installs the highest version it finds. If a private package name is unclaimed on PyPI, anyone can publish a higher version under that name and pip will install it in place of the private package.
- **Remediation:** make the private feed the **only** index (`index-url`) and enable PyPI as an upstream source on the feed. Alternatively, register placeholder packages for the private names on PyPI. This is a per-machine change and no repository change is needed. None of the manifests in this repository depend on the private feed.

## Limitations

- The secret scan is regex-based with placeholder suppression. It catches known token formats and assignment-shaped secrets, but not arbitrary high-entropy strings in unusual shapes. Running gitleaks or trufflehog would strengthen that coverage.
- History coverage is all **local** refs at audit time. Branches that exist only on the remote and were never fetched were not scanned.
- Vulnerability results reflect advisory databases on 2026-10-29. The Python ranges will resolve to newer versions over time.
- Binary files (images) were excluded from the content scan.
