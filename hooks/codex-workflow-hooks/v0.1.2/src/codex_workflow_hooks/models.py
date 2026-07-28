"""Stable internal models for Codex hook events and policy decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class HookEvent(StrEnum):
    SESSION_START = "SessionStart"
    SUBAGENT_START = "SubagentStart"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"
    SESSION_END = "SessionEnd"


class DeliveryState(StrEnum):
    SOURCE_MODIFIED = "source_modified"
    VALIDATED = "validated"
    COMMITTED = "committed"
    PUSHED = "pushed"
    PR_OPEN = "pr_open"
    PR_POLICY_READY = "pr_policy_ready"
    MERGED = "merged"
    ARTIFACT_PUBLISHED = "artifact_published"
    CONSUMER_ADOPTED = "consumer_adopted"
    RELEASED = "released"
    DEPLOYED = "deployed"
    RUNTIME_HEALTHY = "runtime_healthy"
    USER_PATH_VERIFIED = "user_path_verified"


@dataclass(frozen=True)
class EventEnvelope:
    session_id: str
    cwd: Path
    hook_event_name: str
    turn_id: str = ""
    permission_mode: str = "default"
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: Any = field(default_factory=dict)
    tool_response: Any = None
    stop_hook_active: bool = False
    last_assistant_message: str = ""
    start_source: str = ""
    agent_id: str = ""
    agent_type: str = ""
    end_reason: str = ""

    @classmethod
    def from_payload(cls, payload: Any) -> "EventEnvelope":
        if not isinstance(payload, dict):
            raise ValueError("Hook input must be a JSON object.")
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        event = payload.get("hook_event_name")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Missing session_id.")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("Missing cwd.")
        if not isinstance(event, str) or not event:
            raise ValueError("Missing hook_event_name.")
        return cls(
            session_id=session_id,
            cwd=Path(cwd),
            hook_event_name=event,
            turn_id=_string(payload.get("turn_id")),
            permission_mode=_string(payload.get("permission_mode"), "default"),
            tool_name=_string(payload.get("tool_name")),
            tool_use_id=_string(payload.get("tool_use_id")),
            tool_input=payload.get("tool_input") or {},
            tool_response=payload.get("tool_response"),
            stop_hook_active=bool(payload.get("stop_hook_active", False)),
            last_assistant_message=_string(payload.get("last_assistant_message")),
            start_source=_string(payload.get("source")),
            agent_id=_string(payload.get("agent_id")),
            agent_type=_string(payload.get("agent_type")),
            end_reason=_string(payload.get("reason")),
        )

    def command(self) -> str:
        if isinstance(self.tool_input, dict):
            value = self.tool_input.get("command", "")
            return value if isinstance(value, str) else ""
        return ""


@dataclass(frozen=True)
class RepoContext:
    cwd: Path
    repo_root: Path | None
    git_common_dir: Path | None
    origin: str
    branch: str
    head: str
    repository_id: str = ""
    managed: bool = False
    expected_managed: bool = False
    rollout_mode: str = "global"
    policy_error: str = ""

    @property
    def detached(self) -> bool:
        return bool(self.repo_root and not self.branch)


@dataclass(frozen=True)
class GuardDecision:
    deny: bool = False
    reason_code: str = ""
    message: str = ""
    action_type: str = "read"


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default
