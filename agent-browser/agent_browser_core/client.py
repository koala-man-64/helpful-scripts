"""Typed repo-private bridge for MCP and the ServiceNow client; never exports browser credentials."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any
from . import common, session


def invoke(command: str, arguments: dict[str, Any], *, profile: str | None = None,
           session_id: str | None = None, timeout: float = 15, committing: bool = False) -> dict[str, Any]:
    paths = common._paths(session._resolve_profile(profile))
    state = session._require_running(paths, command)
    arguments = {**arguments, "_session_id": common.session_identity(session_id), "_cwd": str(Path.cwd())}
    if committing:
        arguments["operation"], command = command, "commit"
    result = session._request(state, command, arguments, timeout)
    while result.get("pending") and result.get("status") != "needs_human":
        common._sleep(.2)
        result = session._request(state, "poll", {"command_id": result["command_id"], "_session_id": arguments["_session_id"]}, 2)
    return result


def browser_request(*, method: str, url: str, headers: dict[str, str], body: bytes | None,
                    timeout: float, max_bytes: int, profile: str | None = None,
                    session_id: str | None = None, servicenow: bool = True) -> dict[str, Any]:
    profile = session._resolve_profile(profile)
    sid = common.session_identity(session_id)
    paths = common.ProfilePaths(profile, common._home(), sid)
    request_file = None
    result = None
    args: dict[str, Any] = {"method": method.upper(), "url": url, "headers": headers,
                            "max_response_bytes": max_bytes, "save_response": True, "servicenow": servicenow}
    try:
        if body is not None:
            paths.downloads.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="request-", suffix=".body", dir=paths.downloads)
            request_file = Path(name)
            with os.fdopen(fd, "wb") as handle: handle.write(body)
            args["body_file"] = str(request_file)
        result = invoke("api", args, profile=profile, session_id=sid, timeout=timeout,
                        committing=method.upper() not in {"GET", "HEAD"})
        saved = Path(result["body_file"]).resolve()
        if not saved.is_relative_to(paths.outputs.resolve()) or saved.suffix != ".body":
            raise common._tag(RuntimeError("Daemon returned an invalid response artifact."), common.ERR_GUARDED)
        if saved.stat().st_size > max_bytes:
            raise common._tag(RuntimeError("Response exceeded requested byte limit."), "response_too_large")
        return {"status": result["status"], "headers": result["headers"], "body": saved.read_bytes(),
                "command_id": result.get("command_id"), "receipt": "state --command " + str(result.get("command_id", "")),
                "executed": bool(result.get("executed")), "write_outcome": result.get("write_outcome")}
    except (ValueError, RuntimeError, OSError) as exc:
        if result and result.get("command_id"):
            exc.details = {**(getattr(exc, "details", None) or {}), "command_id": result["command_id"],
                           "executed": method.upper() not in {"GET", "HEAD"}, "receipt": "state --command " + result["command_id"]}
        raise
    finally:
        if request_file is not None: request_file.unlink(missing_ok=True)
