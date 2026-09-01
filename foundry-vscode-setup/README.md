# VS Code GLM orchestrator with Microsoft Foundry workers

This folder is a handoff package for configuring agentic development in VS Code without a GitHub Copilot license. It uses:

- VS Code's **Local** agent harness as the runtime that owns chat sessions, tools, approvals, workspace access, and subagents.
- A local, tool-capable **GLM** deployment as a coordinator model.
- Existing **Microsoft Foundry model deployments** as the models assigned to specialized worker agents.
- Workspace custom agents in `.github/agents/*.agent.md` to define the coordinator/worker topology.

The coordinator template has only the `agent` tool. It can delegate and synthesize results, but it cannot read, edit, search, or run commands itself.

> Important: as of 2026-09-01, the Preview **Agents window does not run the Local harness**. Run this design from the Chat view in the main VS Code window. The Agents window currently supports Agent Host harnesses such as Copilot, Claude, and Codex. This package does not require or enable GitHub Copilot.

## What this design can and cannot do

| Requirement | Result |
| --- | --- |
| Use VS Code agentic chat without a Copilot plan | Supported by BYOK models in the Local harness. |
| Use a local GLM as the main coordinator | Supported if the GLM endpoint and model implement reliable tool/function calling. |
| Route individual subagents to Foundry deployments | Supported through each custom agent's `model` property, subject to model availability and VS Code's subagent cost-tier gate. |
| Let worker agents read/edit/run tests | Supported through scoped VS Code tools in each worker definition. |
| Run this Local-harness design in the Agents window | Not currently supported; use the main-window Chat view. |
| Get Copilot-style ghost-text completion from BYOK | Not provided by native BYOK chat. Inline suggestions, semantic search, and embedding-backed features can still require GitHub/Copilot services. |
| Use a Foundry Prompt Agent or Hosted Agent as a worker `model` | Not directly. A custom-agent `model` must be a language model available in VS Code. Expose a hosted agent through MCP or an extension tool instead. |

## Package contents

- [`SETUP-PROMPT.md`](SETUP-PROMPT.md): copy/paste assignment for the agent on the target computer.
- [`templates/chatLanguageModels.example.json`](templates/chatLanguageModels.example.json): Custom Endpoint example for one local GLM and four Foundry worker deployments.
- [`templates/settings.example.json`](templates/settings.example.json): settings to merge, not overwrite, into the target VS Code profile.
- [`templates/agents/glm-foundry-orchestrator.agent.md`](templates/agents/glm-foundry-orchestrator.agent.md): delegation-only coordinator.
- [`templates/agents/foundry-planner.agent.md`](templates/agents/foundry-planner.agent.md): read-only planning worker.
- [`templates/agents/foundry-researcher.agent.md`](templates/agents/foundry-researcher.agent.md): read-only repository research worker.
- [`templates/agents/foundry-implementer.agent.md`](templates/agents/foundry-implementer.agent.md): the only file-writing worker.
- [`templates/agents/foundry-reviewer.agent.md`](templates/agents/foundry-reviewer.agent.md): read-only review and test worker.

## Prerequisites and policy gates

Do not begin configuration until all applicable gates pass.

1. Use a current VS Code release that includes the Local harness, BYOK Custom Endpoint provider, custom agents, and subagents.
2. Confirm the organization has not disabled VS Code AI features, BYOK, custom agents, subagents, or the required extensions through enterprise policy.
3. Confirm the target computer may send repository context to the selected Foundry deployments.
4. Confirm every coordinator and worker model supports tool/function calling. A model without tool calling is not shown for agent use or will fail when it tries to delegate or use workspace tools.
5. Collect the exact Foundry inference endpoint, deployment names, supported API type, context limits, authentication method, and data-handling classification. Do not guess any of them.
6. Decide where workspace writes will occur. The Local harness uses the active workspace; it does not automatically create an isolated Git worktree.
7. Keep approval prompts enabled for the first canary. Do not start with bypass approvals.

No Copilot license or GitHub sign-in is required for BYOK chat and Local agents. Company policy can still block the feature even when VS Code itself supports it.

## 1. Install the allowed extensions

Install Microsoft Foundry Toolkit when company policy permits it:

```powershell
code --install-extension ms-windows-ai-studio.windows-ai-studio
```

The toolkit is useful for signing in to Azure, browsing Foundry resources, testing deployments, and exposing supported models to VS Code. It is not the coding-agent harness; VS Code's Local harness owns the coding loop.

If GLM runs through Ollama, install Ollama's official VS Code model-provider extension:

```powershell
code --install-extension Ollama.ollama
```

Alternatively, skip the Ollama extension and register the local server through VS Code's Custom Endpoint provider using the supplied template. Do not install an unapproved third-party coding extension merely to get a model picker.

Verify installed extensions:

```powershell
code --list-extensions --show-versions |
  Select-String -Pattern 'ms-windows-ai-studio.windows-ai-studio|Ollama.ollama'
```

Expected result: the allowed extension IDs appear. If an ID is absent, stop and resolve installation or enterprise-policy errors before continuing.

## 2. Prepare and test the local GLM endpoint

The local GLM may be served by Ollama, vLLM, LM Studio, or another OpenAI-compatible server. The rest of this guide uses Ollama's OpenAI-compatible endpoint as the concrete example.

1. Install and start Ollama through the organization's approved software process.
2. Pull an approved GLM tag that explicitly advertises tool support. Do not assume every model named GLM supports tools.
3. Record the exact model tag and its real context limit.

```powershell
ollama pull <APPROVED_GLM_TAG>
ollama show <APPROVED_GLM_TAG>
ollama list
```

Expected result: the model is listed and its metadata includes tool support. If tool support is absent, use a different approved GLM build.

Confirm the OpenAI-compatible endpoint responds:

```powershell
$localGlmRequest = @{
  model = '<APPROVED_GLM_TAG>'
  messages = @(
    @{ role = 'user'; content = 'Reply with exactly: glm-ready' }
  )
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:11434/v1/chat/completions' `
  -ContentType 'application/json' `
  -Body $localGlmRequest
```

Expected result: the assistant response contains `glm-ready`. This proves basic inference only; the VS Code delegation canary later proves tool calling.

## 3. Inventory and test Foundry deployments

For each worker role, record a deployment that supports the selected API and tool calling:

| VS Code label used by templates | Exact Foundry deployment name | Intended role | Required tools |
| --- | --- | --- | --- |
| `Foundry Planner` | `<PLANNER_DEPLOYMENT>` | Plan tasks | read, search |
| `Foundry Researcher` | `<RESEARCH_DEPLOYMENT>` | Inspect repository | read, search |
| `Foundry Implementer` | `<IMPLEMENTER_DEPLOYMENT>` | Edit and validate | read, search, edit, execute |
| `Foundry Reviewer` | `<REVIEWER_DEPLOYMENT>` | Review and run checks | read, search, execute |

The four labels may point to the same Foundry deployment for the first canary. Separate deployments only when there is a real quality, latency, cost, or governance reason.

Prefer Entra ID and the Foundry Toolkit when that route exposes the deployments in VS Code's Language Models editor. For a controlled development canary, the Custom Endpoint fallback can use a Foundry resource key, but a resource key is broad and manually rotated. Follow company policy and never put it in this repository, `settings.json`, an agent file, a shell command, or a transcript.

Use the current OpenAI v1-compatible endpoint copied from Foundry. New integrations should not use the retired `/models` inference route. Depending on the resource, the inference base resembles one of these:

```text
https://<RESOURCE>.openai.azure.com/openai/v1/
https://<RESOURCE>.services.ai.azure.com/openai/v1/
```

The supplied example uses an explicit `/chat/completions` path to avoid URL-resolution ambiguity. If a deployment supports Responses but not Chat Completions, change both `apiType` and the path consistently.

Before configuring VS Code, test every deployment in the Foundry Toolkit playground or another approved client. The canary must demonstrate a tool call, not only a text response.

## 4. Register models in VS Code

Open the Language Models editor:

1. In the main VS Code window, open the Chat view.
2. Open the model picker and select **Manage Language Models**, or run **Chat: Manage Language Models** from the Command Palette.
3. Use one of the following routes.

### Route A: extension-provided models

Use this route when the official Ollama and Foundry Toolkit extensions expose the required models.

1. Complete the extension sign-in or local-server setup.
2. Reload VS Code.
3. Confirm all five model labels appear in the Language Models editor and are selectable for Agent mode.
4. Update every `model:` field in `templates/agents` to the exact labels displayed by VS Code. Agent model names are literal; a mismatch can cause fallback to the parent model.

### Route B: Custom Endpoint provider

Use this route when extension-provided discovery does not expose the models.

1. In **Manage Language Models**, select **Add Models** and then **Custom Endpoint**.
2. Add the Local GLM provider. Enter a placeholder key such as `ollama` when the local server requires a syntactic key but ignores it.
3. Add the Foundry provider. Let VS Code prompt for the real key so it is stored in secret storage.
4. VS Code opens its managed `chatLanguageModels.json`. Merge the entries from [`templates/chatLanguageModels.example.json`](templates/chatLanguageModels.example.json).
5. Replace every angle-bracket placeholder with verified values.
6. Set token limits from the provider's documentation. The example values are conservative placeholders, not claims about your deployments.
7. Save and reload VS Code.
8. Confirm the five display names appear exactly as written in the agent templates.

Do not copy `chatLanguageModels.json` to arbitrary profile paths. Let the command create or open the correct profile-managed file.

## 5. Install the custom agents in the target workspace

From the root of the repository that the agents will modify:

```powershell
New-Item -ItemType Directory -Force -Path '.github\agents' | Out-Null
Copy-Item `
  -LiteralPath '<PATH-TO-THIS-FOLDER>\templates\agents\glm-foundry-orchestrator.agent.md' `
  -Destination '.github\agents\glm-foundry-orchestrator.agent.md'
Copy-Item `
  -LiteralPath '<PATH-TO-THIS-FOLDER>\templates\agents\foundry-planner.agent.md' `
  -Destination '.github\agents\foundry-planner.agent.md'
Copy-Item `
  -LiteralPath '<PATH-TO-THIS-FOLDER>\templates\agents\foundry-researcher.agent.md' `
  -Destination '.github\agents\foundry-researcher.agent.md'
Copy-Item `
  -LiteralPath '<PATH-TO-THIS-FOLDER>\templates\agents\foundry-implementer.agent.md' `
  -Destination '.github\agents\foundry-implementer.agent.md'
Copy-Item `
  -LiteralPath '<PATH-TO-THIS-FOLDER>\templates\agents\foundry-reviewer.agent.md' `
  -Destination '.github\agents\foundry-reviewer.agent.md'
```

Review the copied files before committing them. If the target repository already has agents with these names, reconcile deliberately; do not overwrite them blindly.

To reuse the agents across repositories, create them as user-level custom agents through **Chat: Open Customizations** instead. Workspace agents are preferable for the first canary because they are reviewable with the repository and can carry repository-specific constraints.

Merge the settings in [`templates/settings.example.json`](templates/settings.example.json) into the target profile or workspace settings. Keep nested subagent invocation disabled initially.

## 6. Start the correct VS Code session

1. Open the target repository in the main VS Code window.
2. Open Chat (`Ctrl+Alt+I`).
3. Start a new chat.
4. Set **Session Target** to **Local**.
5. Select the custom agent **GLM Foundry Orchestrator**.
6. Confirm the selected model is **GLM Orchestrator (Local)**, or the exact local label substituted in the template.
7. Use **Default Approvals** for the first canary.

Do not use the Preview Agents window for this flow. Seeing only Copilot, Claude, or Codex harnesses there is expected and is not evidence that BYOK failed.

## 7. Validate in increasing-risk stages

Run these stages in order. Stop at the first failure.

### Stage 1: model availability

In a normal Local Agent chat, select each configured model and ask:

```text
List the names of the files in the workspace root. Do not edit anything.
```

Pass criteria:

- The model appears in Agent mode.
- It invokes a read/search tool rather than fabricating a list.
- The request uses the expected endpoint in Chat Debug diagnostics.

### Stage 2: coordinator delegation

Select **GLM Foundry Orchestrator** and send:

```text
Ask Foundry Researcher to read the workspace README and return only its title and the exact path read. Do not edit any file.
```

Pass criteria:

- The parent invokes `agent/runSubagent`.
- The expanded subagent call identifies **Foundry Researcher**.
- Chat diagnostics show the worker used its configured Foundry model, not the GLM parent.
- No files changed.

### Stage 3: plan and review without writes

Send:

```text
Have Foundry Planner propose a three-step documentation-only change. Then have Foundry Reviewer critique the plan. Do not invoke Foundry Implementer and do not edit files.
```

Pass criteria:

- Only the allowlisted workers run.
- Planner and Reviewer remain read-only.
- Each subagent receives a bounded task and returns a concise result.

### Stage 4: disposable write canary

Use a disposable repository or a task-owned Git worktree with a clean status. Send:

```text
Create a file named agent-canary.txt containing one line: foundry-worker-ready. Use Foundry Implementer as the only writer, then use Foundry Reviewer to verify the exact content and report git diff. Do not commit or push.
```

Pass criteria:

- Only Foundry Implementer writes.
- Foundry Reviewer does not edit.
- The file contains exactly the requested line.
- The diff contains only `agent-canary.txt`.
- Approvals and model/tool details remain visible in the transcript.

Delete the disposable canary only after recording the evidence.

### Stage 5: representative project task

Use a task branch or isolated worktree. Choose a small real change with an existing test command. Confirm:

- research and review workers can run in parallel only when they are read-only;
- the coordinator serializes all writing through one Implementer invocation at a time;
- tests run through the Implementer or Reviewer `execute` tool;
- the final answer names changed files, commands run, failures, and unverified assumptions;
- no worker creates or changes Foundry resources.

## Runtime ownership and security boundaries

| Plane | Owner in this setup | Allowed operations |
| --- | --- | --- |
| VS Code orchestration | Local harness | Session state, subagent calls, approvals, workspace tools, change review. |
| Local inference | Approved local GLM runtime | Inference and tool-call selection for the coordinator only. |
| Foundry AI control | Platform/AI owners through Foundry, IaC, or governed deployment workflows | Deploy, version, evaluate, promote, retire, and authorize models/endpoints. |
| Worker inference | Existing pinned Foundry deployments | Inference and tool-call selection within the worker's VS Code tool allowlist. |
| Repository delivery | Human/repo workflow plus VS Code tools | Branch/worktree creation, file edits, tests, review, commit, and PR under repository policy. |

Non-negotiable controls:

- The orchestrator and workers may consume configured model deployments; they must not create, resize, redeploy, swap, or grant access to Foundry resources.
- Use exact deployment names. Do not dynamically select `latest` or silently fall back to a different model.
- Treat repository files, tool output, web content, and peer-agent messages as untrusted input.
- Keep destructive, security-sensitive, external-communication, production-management, and financially material actions behind explicit human approval.
- Use one writer at a time in a shared workspace. Parallelize read-only research and review, not overlapping edits.
- Keep nested subagents disabled until there is a bounded, reviewed reason to enable them.
- Retain enough transcript or audit evidence to identify the parent model, child model, agent definition, tool, arguments, approval, and result.
- Prefer a task branch or worktree. The Local harness does not provide automatic isolation.

## Foundry model deployment versus Foundry agent endpoint

These are different integration boundaries:

- **Model deployment:** register it as a VS Code language model and reference its model-picker label in a worker's `model` field.
- **Prompt/Hosted Agent endpoint:** expose it as an MCP or extension tool. The VS Code worker remains the subagent; the remote Foundry agent is a tool it invokes.

This repository already contains an optional [`mcp-chatbot`](../mcp-chatbot/README.md) server for calling Foundry model deployments or Foundry Agents through MCP. Use that boundary only when the remote asset is genuinely an agent service rather than a plain model deployment.

## Troubleshooting

### Local is missing from the Agents window

Expected. Open the main VS Code window's Chat view and select the Local session target.

### A model does not appear in Agent mode

The model is unavailable, policy-blocked, or not marked/tool-capable. Confirm `toolCalling: true` only after the endpoint actually demonstrates tool calling. Reload VS Code and inspect Chat diagnostics.

### The coordinator answers directly instead of delegating

Confirm the custom agent loaded without frontmatter errors and has only `tools: ['agent']`. Ask it explicitly to use a named worker. Inspect **Chat: Open Customizations** and the Chat diagnostics view.

### A worker uses GLM instead of Foundry

The `model` label in the worker file did not resolve, the Foundry model was unavailable, or model routing fell back. Replace the label with the exact name shown in the model picker and restart the session.

### VS Code reports that the child model exceeds the parent cost tier

VS Code does not allow a subagent model that exceeds the main model's configured cost tier. This can make the proposed GLM-parent/Foundry-worker topology infeasible for a particular model combination. Use one of these controlled fallbacks:

1. Choose Foundry worker models in the same or lower tier.
2. Use the same GLM model for parent and child while retaining role/tool isolation.
3. Use a sufficiently tiered Foundry model as the parent coordinator.

Do not bypass the gate or claim the original topology works until the delegation canary passes.

### Foundry returns 401 or 403

Verify the exact endpoint, authentication header, secret source, RBAC/key validity, network policy, and resource access. Custom Endpoint normally infers `api-key` for Azure OpenAI URLs; the template sets it explicitly. Do not broaden runtime permissions as the first fix.

### Foundry returns 400 Model not supported

The deployment may not support the selected API. Match `apiType` and URL path to a supported API, then rerun the tool-calling canary.

### No Copilot inline suggestions or semantic search

Expected for pure BYOK. This setup provides chat and agentic tools, not Copilot ghost text or every embedding-backed feature.

### Multiple workers produce conflicting edits

Stop the session, restore from source control or VS Code's change-review controls, and rerun with one Implementer at a time in an isolated branch/worktree.

## Rollback and removal

1. Stop active Local agent sessions.
2. Remove the five copied `.github/agents/*.agent.md` files, or revert the commit that added them.
3. Remove only the settings merged from `settings.example.json` if they are no longer wanted.
4. In **Manage Language Models**, remove the Local GLM and Foundry providers and their stored secrets.
5. Uninstall `Ollama.ollama` or Foundry Toolkit only if no other workflow uses them.
6. Revoke or rotate any Foundry resource key used for the canary according to company policy.
7. Confirm the repository is clean and no canary file, endpoint, key, transcript export, or temporary worktree remains.

## Evidence and current references

The instructions were checked against these sources on 2026-09-01:

- [AI language models in VS Code](https://code.visualstudio.com/docs/agent-customization/language-models)
- [Choose and use an agent harness](https://code.visualstudio.com/docs/agents/run/agent-harnesses)
- [Use the Agents window (Preview)](https://code.visualstudio.com/docs/agents/run/agents-window)
- [Custom agents in VS Code](https://code.visualstudio.com/docs/agent-customization/custom-agents)
- [Subagents in VS Code](https://code.visualstudio.com/docs/agents/run/subagents)
- [AI features cheat sheet](https://code.visualstudio.com/docs/agents/reference/ai-features-cheat-sheet)
- [Foundry Toolkit for VS Code](https://marketplace.visualstudio.com/items?itemName=ms-windows-ai-studio.windows-ai-studio)
- [Foundry model endpoints](https://learn.microsoft.com/azure/ai-studio/ai-services/concepts/endpoints)
- [Integrate Microsoft Foundry with applications](https://learn.microsoft.com/azure/foundry/how-to/integrate-with-other-apps)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)

VS Code's Agents window, subagents, and portions of the customization surface are Preview features. Recheck the official docs before deploying this setup on the other computer.
