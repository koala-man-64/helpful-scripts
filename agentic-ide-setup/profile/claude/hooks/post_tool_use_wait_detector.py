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

STATEMENT_SPLIT = re.compile(r"\|\||&&|[;\n|]")
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(command: str) -> str:
    """Remove heredoc bodies, which are data rather than commands.

    A body line beginning with `az` is indistinguishable from an invocation
    once the command is split on newlines.
    """
    lines = command.splitlines()
    kept: list[str] = []
    terminator: str | None = None
    for line in lines:
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        kept.append(line)
        match = HEREDOC_START.search(line)
        if match:
            terminator = match.group(2)
    return "\n".join(kept)


def invocations(command: str) -> list[str]:
    """Segments whose first token is the program being run.

    Matching the pattern anywhere in the command text is wrong: it fires on
    `grep "az repos pr create" file.py`, on a heredoc, and on any quoted string
    that merely mentions the command. Live diagnostics caught exactly that.
    Only a segment that actually invokes az or gh can have created a resource.
    """
    segments = []
    for segment in STATEMENT_SPLIT.split(strip_heredocs(command)):
        tokens = segment.strip().split()
        while tokens and ENV_ASSIGNMENT.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        program = tokens[0].strip("'\"").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].casefold()
        if program in {"az", "az.cmd", "gh", "gh.exe"}:
            segments.append(" ".join(tokens))
    return segments


def observed_failure(response: Any) -> bool:
    """True only for a failure the provider actually reported.

    An absent exit code means unknown, never failure. Codex conflated the two
    and dropped every single one of its 581 provider-write events before they
    reached detection.

    A stringified exit code still counts. Shells and wrappers marshal
    $LASTEXITCODE as text, and reading "1" as success would be the same
    type-brittle exit-code handling in the opposite direction.
    """
    if not isinstance(response, dict):
        return False
    if response.get("isError") is True or response.get("is_error") is True:
        return True
    for key in ("exit_code", "exitCode", "returncode", "status"):
        code = exit_code_value(response.get(key))
        if code is not None and code != 0:
            return True
    return False


def exit_code_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


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
    """Every top-level JSON object or array embedded in the command output.

    Scanning advances past each decoded value so nested objects are not
    returned as payloads in their own right. Without that, `definition.id`
    inside a pipeline run looks like a separate document and can be selected
    ahead of the run's own id.
    """
    found: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        match = re.search(r"[{\[]", text[index:])
        if not match:
            break
        start = index + match.start()
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        found.append(value)
        index = start + consumed
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


def last_identifier(text: str, *keys: str) -> str:
    """Identifier from the LAST matching JSON payload in the output.

    Commands are routinely chained, and the create we care about is the final
    segment. Taking the first match binds the wait to whatever earlier segment
    happened to emit a same-shaped key, which is worse than not registering:
    the wait tracks the wrong resource and the real one never gets registered
    at all, because dedupe is keyed on resource_id.
    """
    for payload in reversed(json_payloads(text)):
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
    invoked = invocations(command)
    if not invoked:
        return None
    command = " ; ".join(invoked)
    if AZ_PR_CREATE.search(command):
        return {
            "provider": "azure_devops",
            "operation_kind": "pull_request",
            "target_state": "merged",
            "resource_id": last_identifier(text, "pullRequestId", "pull_request_id"),
        }
    if AZ_PIPELINE_RUN.search(command):
        return {
            "provider": "azure_devops",
            "operation_kind": "pipeline",
            "target_state": "succeeded",
            "resource_id": last_identifier(text, "id", "buildId", "runId"),
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
    try:
        return _detect_and_register()
    except Exception:
        # This hook observes every Bash and PowerShell call. It must never take
        # down the tool call it is watching, whatever the registry, the
        # filesystem, or a malformed payload does.
        return emit_json(None)


def _detect_and_register() -> int:
    payload = read_hook_input()
    if payload.get("tool_name") not in {"Bash", "PowerShell"}:
        return emit_json(None)

    command = extract_command(payload)
    if not command or DRY_RUN.search(command):
        return emit_json(None)

    response = payload.get("tool_response")
    text = response_text(response)
    detected = detect(command, text)
    if detected is None:
        return emit_json(None)

    resource_id = detected.get("resource_id", "")
    kind = detected["operation_kind"]

    if observed_failure(response):
        if not resource_id:
            return emit_json(None)
        # The command reported failure, yet a resource id came back: the
        # provider-side create probably succeeded and a later step failed.
        # Registering on a reported failure would be presumptuous, but going
        # silent is the defect class that hid the Codex outage for two weeks.
        wait_registry.record_diagnostic(
            "WAIT_OPERATION_FAILED_WITH_RESOURCE",
            f"{detected['provider']} {kind} {resource_id}: command reported failure",
        )
        return emit_json(
            additional_context(
                "PostToolUse",
                f"The command reported failure but returned {kind} id {resource_id}, so the "
                "resource may exist. No wait was registered. Verify the resource and register "
                "a wait manually if it needs follow-up.",
            )
        )

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
