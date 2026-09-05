`%USERPROFILE%\.claude\settings.json` — merge these keys into your existing file:

```json
{
  "model": "sonnet",
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_AUTH_TOKEN": "<LITELLM_KEY>",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "foundry-sonnet",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "foundry-opus",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "foundry-sonnet"
  }
}
```

`<LiteLLM config.yaml>` — merge these entries into its existing `model_list`:

```yaml
model_list:
  - model_name: foundry-sonnet
    litellm_params:
      model: azure_ai/<FOUNDRY_MODEL>
      api_base: "<FOUNDRY_ENDPOINT>"
      api_key: os.environ/AZURE_AI_API_KEY
  - model_name: foundry-opus
    litellm_params:
      model: azure_ai/<FOUNDRY_MODEL>
      api_base: "<FOUNDRY_ENDPOINT>"
      api_key: os.environ/AZURE_AI_API_KEY
```

Assumption: both aliases use the one working Foundry deployment you described.
Selecting Opus still calls that deployment; an alias does not turn Sonnet into
Opus. If you have separate deployments, copy each deployment's working
`litellm_params` into its corresponding entry.

This folder contains templates and commands only. Keep your existing LiteLLM
`general_settings`, key validation, and Foundry authentication. The `api_key`
lines illustrate existing key-based auth: preserve your actual credential
variable or Azure AD token configuration if different. Do not replace an entire
working settings file with a template or add a second `model_list` key.

What each setting does:

| Setting | Purpose |
| --- | --- |
| `model: sonnet` in JSON | Default Claude Code selection. |
| `env` | Environment variables applied to Claude Code. On Windows the default user file is `%USERPROFILE%\.claude\settings.json`, also reachable as `$env:USERPROFILE\.claude\settings.json` in PowerShell. A configured `CLAUDE_CONFIG_DIR` changes the directory. |
| `ANTHROPIC_BASE_URL` | LiteLLM's unified endpoint: **`http://localhost:4000`**, without `/v1` or `/anthropic`. Claude Code calls `/v1/messages` beneath it. |
| `ANTHROPIC_AUTH_TOKEN` | Your existing LiteLLM master or virtual key, sent as `Authorization: Bearer ...`. It is not a Foundry or public Anthropic key. A virtual key must already allow the two new aliases. |
| `ANTHROPIC_API_KEY: ""` | Clears the alternative `x-api-key` credential in this configuration, leaving the LiteLLM bearer token as the intended credential. |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Resolves Claude Code's `sonnet` selection to the exact request model `foundry-sonnet`. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Resolves `opus` to `foundry-opus`. |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Sends Haiku/background work to `foundry-sonnet`, too. No separate Haiku deployment is required. |
| `model_list` / `model_name` | LiteLLM's configured routes and client-visible aliases. These must match Claude Code's resolved names exactly. |
| `litellm_params.model` | Actual upstream route. Use `azure_ai/` followed by your already-working `<FOUNDRY_MODEL>`, without duplicating the prefix. |
| `api_base` | Your working `<FOUNDRY_ENDPOINT>`, normally `https://YOUR-RESOURCE.services.ai.azure.com/anthropic`. LiteLLM appends `/v1/messages`; use the API base, not a project URL or the complete messages URL. |
| `api_key` | Existing server-side Foundry credential reference. Claude Code never needs this key. |

These values follow the official [Claude Code gateway instructions](https://code.claude.com/docs/en/llm-gateway-connect),
[environment variable reference](https://code.claude.com/docs/en/env-vars),
[LiteLLM Claude Code quickstart](https://docs.litellm.ai/docs/tutorials/claude_responses_api),
and [LiteLLM Foundry Claude provider guide](https://docs.litellm.ai/docs/providers/azure_ai#usage---azure-anthropic-azure-foundry-claude).
Checked September 5, 2026.

Model selection precedence is `/model` during a session, then startup `--model`,
then `ANTHROPIC_MODEL`, then the `model` setting. The family variables above
resolve the selected alias; they do not select the session model themselves.
See [model configuration](https://code.claude.com/docs/en/model-config#setting-your-model).

To keep inference on this path, keep the base URL and gateway token active,
ensure project/managed settings do not override them, and leave
`CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_BEDROCK`, and
`CLAUDE_CODE_USE_VERTEX` unset for this gateway session. Foundry is selected by
LiteLLM. Keep these LiteLLM aliases and any fallback targets Foundry-only; do not
add a public Anthropic route or wildcard pass-through. A proxy error should
remain an error on the local endpoint. A saved Claude login is unused while the
gateway token is active. Check `/status` for the local API base URL and token
credential source. User settings are configuration, not a network enforcement
boundary; an absolute ban requires an outbound network rule. See
[gateway authentication](https://code.claude.com/docs/en/llm-gateway-connect)
and [settings precedence](https://code.claude.com/docs/en/settings#settings-precedence).

If you also want to suppress nonessential Anthropic traffic, optionally add
`"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"` to `env`. This disables
auto-updates and telemetry, among other checks; it is not an inference-routing
control or a blanket network block. See the
[environment variable reference](https://code.claude.com/docs/en/env-vars).

PowerShell startup commands, after merging the aliases into your working proxy
configuration (the first terminal uses the same environment as your existing
LiteLLM process):

```powershell
# Terminal 1: from the directory containing the working config.yaml.
# Restart your existing proxy with these options; do not start a second copy.
litellm --config .\config.yaml --host 127.0.0.1 --port 4000 --detailed_debug
```

```powershell
# Terminal 2: start a fresh Claude Code session.
claude --model sonnet
# Select Opus inside that session with: /model opus
```

One end-to-end routing test, covering both aliases:

```powershell
'sonnet', 'opus' | ForEach-Object {
    claude --model $_ -p "Do not use tools. Reply exactly ROUTE_OK."
    if ($LASTEXITCODE -ne 0) { throw "Routing test failed for $_" }
}
```

Expect `ROUTE_OK` for each request. In the LiteLLM debug output, correlate each
request's incoming model (`foundry-sonnet` or `foundry-opus`) with the selected
`azure_ai/<FOUNDRY_MODEL>` route and the outbound Foundry URL ending in
`/anthropic/v1/messages`. The inbound `POST /v1/messages` should finish with
HTTP 200; a streaming request must also finish without an error. Background
requests may add entries. Exact log labels vary by LiteLLM version.

A 200 access-log line alone proves only that Claude Code reached LiteLLM.
The outbound Azure URL and selected deployment establish the second hop;
`api.anthropic.com/v1/messages` as the outbound destination is wrong for this
path. LiteLLM logs cannot see requests that bypass LiteLLM. The test needs an
actual upstream call, not a cached response. `--detailed_debug` is documented in
the [LiteLLM proxy startup example](https://docs.litellm.ai/#step-2-run-docker-image).
Use it briefly with this harmless prompt, then remove it for normal operation;
debug output can contain request content or credentials.

Live Foundry routing is not verified by these templates. Run the test after
substituting your existing values. Keep secrets in your local working files;
`config.yaml`, `settings.json`, and `*.log` are ignored in this folder.
