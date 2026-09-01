# Sign-in and connector checklist

Run this after installing the profile files. None of these account details are stored in this repository.

## Codex

1. Install and sign in to the Codex desktop app or CLI.
2. Restart Codex after copying skills, rules, hooks, and `AGENTS.md`.
3. Reconnect each desired plugin from Codex settings; use `profile/codex/plugins.txt` as the inventory.
4. Recreate any MCP server configuration from its documented provider setup. Review every command and environment value before enabling it.

## Claude Code

1. Install Claude Code and complete its sign-in flow.
2. Restart Claude Code after copying `CLAUDE.md`, settings, agents, skills, and hooks.
3. Review hook command paths and permission rules before first use in a new environment.

## VS Code, ChatGPT, and Copilot Chat

1. Install VS Code, then run the bundle installer with the `VSCode` component.
2. Sign in to the ChatGPT extension, Claude Code extension, and GitHub Copilot separately.
3. Enable Settings Sync only after reviewing its account and sync scope.
4. Open `mcp.json`, replace every `__REVIEW_REQUIRED__` entry with a locally supplied value, and then enable only the servers you intend to use.
