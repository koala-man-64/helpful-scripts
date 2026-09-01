# Handoff prompt for the target-system agent

Copy the prompt below into the agent that will configure the other computer. Attach or clone this folder so the agent can read the templates.

```text
Set up VS Code agentic development on this computer by following foundry-vscode-setup/README.md and using the templates in foundry-vscode-setup/templates.

Target architecture:
- VS Code main-window Chat view with Session Target = Local.
- A local, tool-capable GLM model is the delegation-only coordinator.
- Existing Microsoft Foundry model deployments are assigned to the Planner, Researcher, Implementer, and Reviewer custom subagents.
- The coordinator must have only the VS Code agent tool.
- Foundry Implementer is the only worker allowed to edit files.
- Nested subagents remain disabled.
- GitHub Copilot is disabled and must not be installed, enabled, or treated as a prerequisite.

Guardrails:
- Do not provision, redeploy, resize, replace, or change permissions on any Foundry resource.
- Do not store or print secrets in repositories, settings files, commands, logs, transcripts, or your response. Use VS Code secret storage or approved Entra authentication.
- Do not bypass enterprise policy or approval prompts.
- Do not overwrite existing agents or VS Code settings. Inspect, back up, and merge only the required entries.
- Do not edit a real project until read-only model and delegation canaries pass.
- Run the first write canary only in a disposable repository or task-owned clean worktree.
- Stop and report the exact blocker if Local/BYOK/custom agents/subagents are policy-disabled, if a model lacks tool calling, if the child model exceeds the parent cost tier, or if the endpoint/authentication facts cannot be verified.

Execution:
1. Record VS Code, local model server, and relevant extension versions.
2. Confirm enterprise-policy gates and that repository data may be sent to the selected Foundry deployments.
3. Inventory the exact local GLM tag, endpoint, context limit, and tool support.
4. Inventory the exact Foundry endpoint, deployment names, supported API type, context limits, authentication method, and tool support.
5. Install only organization-approved official extensions. Foundry Toolkit is ms-windows-ai-studio.windows-ai-studio; the official Ollama provider is Ollama.ollama when Ollama is used.
6. Register the models through extension-provided providers when possible; otherwise use VS Code's Custom Endpoint flow and merge chatLanguageModels.example.json after replacing verified placeholders.
7. Copy the custom-agent templates into the target repository's .github/agents directory, preserving any existing files. Replace model labels with the exact labels shown in VS Code.
8. Merge settings.example.json without replacing unrelated settings.
9. In the main VS Code window, use Chat with Session Target = Local. Do not use the Preview Agents window for the Local harness.
10. Run README validation stages 1 through 4 in order. Expand subagent calls and use Chat diagnostics to prove which endpoint/model handled parent and child requests.
11. Remove the disposable canary after evidence is recorded.

Closeout report:
- versions and extensions observed;
- policy gates passed or blocked;
- models registered, using display labels only and no secrets;
- agent files/settings added or merged;
- each canary's exact pass/fail evidence;
- whether the GLM-parent/Foundry-worker cost-tier gate passed;
- changed files and git status;
- remaining risks or manual actions.

Do not claim success from configuration alone. Success requires an observed GLM parent agent/runSubagent call and a Foundry-backed worker tool call in a disposable or read-only canary.
```
