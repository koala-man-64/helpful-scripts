# Local agent tasks

LiteLLM routes model requests. Task creation uses each agent's own interface.

## Claude CLI

Run on Windows with the existing Python and Claude CLI installations:

```powershell
python .\lite-llm\claude_task.py --cwd C:\path\to\project --prompt "Describe this project without changing files."
claude agents
```

The launcher dispatches a background task using existing
Claude configuration, credentials, hooks, and permissions. It never adds
permission-bypass flags. `claude agents` is the native CLI task manager; this
does not create a Claude Desktop Code-tab session. Claude assigns the task ID
and prints its own attach, log, and stop commands. Use `claude attach <id>` to
continue interacting with the running task, `claude logs <id>` for output, and
`claude stop <id>` to stop it. Do not assign your own ID: this CLI ignores
`--session-id` in background mode.

Optional flags: `--model <model-or-alias>`, `--settings <existing-settings.json>`,
and `--dry-run`. Dry-run omits the prompt. The wrapper stores no transcripts.
Do not include credentials in task prompts.

For LiteLLM inference, supply a working settings file with the gateway settings
from README.md. Otherwise existing Claude settings determine the endpoint.
The Docker bootstrap has no model routes and cannot yet perform inference.
A successful launcher exit proves dispatch only. Check `claude agents --all --json`
and the task output for execution success. A dispatch timeout has an unknown
outcome; inspect native tasks before retrying to avoid duplicates.

## Antigravity native UI: pending project binding

Antigravity 2.12.2 is installed on the development machine. Its sidecar API
provides `agentapi new-conversation <prompt>` and
`agentapi send-message <conversation_id> <prompt>`. The app adds `agentapi`
to the sidecar environment; it is not assumed to be on the host PATH.
Creating conversations requires an enabled sidecar with a target `projectId`.
The user must identify that project before installation. No sidecar is installed
by this change.

## Codex native UI: pending Windows integration

Codex exposes task-creation tools inside Desktop, not an external HTTP endpoint
this launcher can call. On the development machine, the CLI shared-daemon command
returns `codex app-server daemon lifecycle is only supported on Unix platforms`.
A separate app-server or CLI transcript does not prove Desktop visibility.
External native dispatch remains unimplemented until its Windows path is verified.

## Ownership and checks

This local-only launcher runs as the current Windows user. Rudy owns host
configuration, agent permissions, model selection, and retirement. Claude owns
task execution and history. There is no listener, provisioning, credential
management, automatic endpoint fallback, or cross-agent conversation migration.
Removing the wrapper prevents new dispatch; stop existing tasks in `claude agents`.
No new runtime dependency is required.

Validated locally with Claude 2.1.236: six offline tests passed; a dispatched
background task replied `TASK_DISPATCH_FINAL_OK` in native logs and was stopped
using the native command. This test used the existing Claude Max configuration,
not LiteLLM. The full three-agent integration remains pending.

```powershell
python -m unittest discover -s lite-llm -p test_claude_task.py
```

Official references:

- [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Antigravity sidecars](https://antigravity.google/docs/sidecars)
- [Codex App Server](https://developers.openai.com/codex/app-server)
