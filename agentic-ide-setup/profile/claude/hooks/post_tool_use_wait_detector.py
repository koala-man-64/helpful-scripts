"""PostToolUse hook: register a wait when a monitorable operation is observed.

Triggers on the command that just ran, not on a reconstructed model of
repository delivery state. See wait_registry for why that distinction is the
whole point of this hook.
"""

import json
import re
from pathlib import Path
from typing import Any

import wait_registry
from hook_utils import (
    additional_context,
    current_branch,
    emit_json,
    extract_command,
    read_hook_input,
    repo_name,
    repo_root,
    run_git,
)

AZ_PR_CREATE = re.compile(r"\baz(?:\.cmd)?\s+repos\s+pr\s+create\b", re.IGNORECASE)
AZ_PIPELINE_RUN = re.compile(r"\baz(?:\.cmd)?\s+pipelines\s+run\b", re.IGNORECASE)
GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b", re.IGNORECASE)
GH_PR_URL = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")

AZ_ORGANIZATION = re.compile(r"--organization[= ]+(\S+)", re.IGNORECASE)
AZ_PROJECT = re.compile(r"--project[= ]+(\S+)", re.IGNORECASE)
GH_REPO = re.compile(r"--repo[= ]+(\S+)", re.IGNORECASE)

DRY_RUN = re.compile(r"--(?:dry-run|what-if|help)\b|\s-h\b", re.IGNORECASE)


def observed_failure(response: Any) -> bool:
    """True only for a failure the provider actually reported.

    An absent exit code means unknown, never failure. Codex conflated the two
    and dropped every single one of its 581 provider-write events before they
    reached detection.
    """
    if not isinstance(response, dict):
        return False
    if response.get("isError") is True or response.get("is_error") is True:
        return True
    for key in ("exit_code", "exitCode", "returncode", "status"):
        value = response.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return True
    return False


def response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""
    parts = []
    for key in ("stdout", "output", "text", "stderr", "result"):
        value = response.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (dict, list)):
            parts.append(json.dumps(value))
    content = response.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
    return "\n".join(parts)


def json_payloads(text: str) -> list[Any]:
    """Every JSON object or array embedded in the command output."""
    found: list[Any] = []
    for match in re.finditer(r"[{\[]", text):
        chunk = text[match.start() :]
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(chunk)
        except ValueError:
            continue
        found.append(value)
    return found


def find_identifier(payload: Any, *keys: str) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, str)) and str(value).strip():
                return str(value)
        for value in payload.values():
            found = find_identifier(value, *keys)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_identifier(value, *keys)
            if found:
                return found
    return ""


def first_identifier(text: str, *keys: str) -> str:
    for payload in json_payloads(text):
        found = find_identifier(payload, *keys)
        if found:
            return found
    return ""


def head_commit(root: Path) -> str:
    code, output = run_git(["rev-parse", "HEAD"], root)
    return output if code == 0 else ""


def capture(pattern: re.Pattern[str], command: str) -> str:
    match = pattern.search(command)
    return match.group(1).strip("'\"") if match else ""


def detect(command: str, text: str) -> dict[str, str] | None:
    """Classify the command, or return None when nothing is monitorable."""
    if AZ_PR_CREATE.search(command):
        return {
            "provider": "azure_devops",
            "operation_kind": "pull_request",
            "target_state": "merged",
            "resource_id": first_identifier(text, "pullRequestId", "pull_request_id"),
        }
    if AZ_PIPELINE_RUN.search(command):
        return {
            "provider": "azure_devops",
            "operation_kind": "pipeline",
            "target_state": "succeeded",
            "resource_id": first_identifier(text, "id", "buildId", "runId"),
        }
    if GH_PR_CREATE.search(command):
        match = GH_PR_URL.search(text)
        return {
            "provider": "github",
            "operation_kind": "pull_request",
            "target_state": "merged",
            "resource_id": match.group(2) if match else "",
            "repo_slug": match.group(1) if match else "",
        }
    return None


def main() -> int:
    payload = read_hook_input()
    if payload.get("tool_name") not in {"Bash", "PowerShell"}:
        return emit_json(None)

    command = extract_command(payload)
    if not command or DRY_RUN.search(command):
        return emit_json(None)

    response = payload.get("tool_response")
    if observed_failure(response):
        return emit_json(None)

    text = response_text(response)
    detected = detect(command, text)
    if detected is None:
        return emit_json(None)

    resource_id = detected.get("resource_id", "")
    kind = detected["operation_kind"]
    if not resource_id:
        # The operation ran but no resource id could be read back. Codex
        # returned None here silently; record it so the outage is visible.
        wait_registry.record_diagnostic(
            "WAIT_TRIGGER_UNBOUND",
            f"{detected['provider']} {kind}: no resource id in command output",
        )
        return emit_json(
            additional_context(
                "PostToolUse",
                f"A {kind} was created but no resource id could be read from the output, "
                "so no wait was registered. Capture the id and register the wait manually "
                "if this operation needs follow-up.",
            )
        )

    root = repo_root()
    wait = wait_registry.register(
        provider=detected["provider"],
        operation_kind=kind,
        resource_id=resource_id,
        repository=repo_name(root),
        branch=current_branch(root),
        commit=head_commit(root),
        target_state=detected["target_state"],
        session_id=str(payload.get("session_id", "")),
        organization=capture(AZ_ORGANIZATION, command),
        project=capture(AZ_PROJECT, command),
        repo_slug=detected.get("repo_slug") or capture(GH_REPO, command),
    )

    hours = int(wait_registry.timeout_for(kind).total_seconds() // 3600)
    return emit_json(
        additional_context(
            "PostToolUse",
            "\n".join(
                [
                    f"Wait registered: {kind} {resource_id} ({wait['wait_id']}).",
                    f"Poll it with: py \"{Path.home()}\\.claude\\hooks\\wait_poll.py\" "
                    f"poll {wait['wait_id']}",
                    "Do not treat registration as delivery evidence. Before yielding, either "
                    "poll to a terminal status, or arm a check that outlives this turn: "
                    "Monitor for a session-length watch, or a scheduled task when the wait may "
                    f"outlive the session. This wait times out after {hours}h.",
                ]
            ),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
