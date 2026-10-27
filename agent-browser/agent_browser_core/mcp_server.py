"""Optional typed stdio tools over the same daemon; no Playwright access in this process."""
from __future__ import annotations

import json
from functools import wraps
from typing import Any, Literal
try:
    # Pydantic's Python 3.10/3.11 schemas require this SDK-provided backport.
    from typing_extensions import TypedDict
except ImportError:
    from typing import TypedDict
from . import client, common


class Target(TypedDict, total=False):
    ref: str
    role: str
    name: str
    label: str


class Field(TypedDict, total=False):
    target: str | Target
    operation: Literal["fill", "select", "check", "uncheck"]
    value: str | list[str] | bool


def _call(command: str, arguments: dict[str, Any], session_id: str, profile: str | None, timeout: float = 15, committing: bool = False) -> dict[str, Any]:
    from .cli import _json_line
    try:
        result = client.invoke(command, arguments, session_id=session_id, profile=profile, timeout=timeout, committing=committing)
    except (ValueError, RuntimeError) as exc:
        result = {"error": {"class": getattr(exc, "error_class", "daemon"), "message": str(exc),
                            "details": getattr(exc, "details", None)}}
    return json.loads(_json_line(result, profile, session_id))


def browser_goto(url: str, session_id: str, profile: str | None = None, timeout: float = 30) -> dict[str, Any]:
    """Navigate the caller's own tab. Sign in manually when requested."""
    return _call("goto", {"url": url}, session_id, profile, timeout)


def browser_snapshot(session_id: str, profile: str | None = None, find: str | None = None, full: bool = False) -> dict[str, Any]:
    return _call("snapshot", {"find": find, "full": full}, session_id, profile)


def browser_click(target: str | Target, session_id: str, profile: str | None = None, expect: list[str] | None = None) -> dict[str, Any]:
    return _call("click", {"target": target, "expect": expect or []}, session_id, profile)


def browser_fill(target: str | Target, text: str, session_id: str, profile: str | None = None, expect: list[str] | None = None) -> dict[str, Any]:
    return _call("fill", {"target": target, "text": text, "expect": expect or []}, session_id, profile)


def browser_fill_form(fields: list[Field], session_id: str, profile: str | None = None) -> dict[str, Any]:
    return _call("fill-form", {"fields": fields}, session_id, profile)


def browser_action(operation: Literal["type", "press", "select", "check", "uncheck", "hover", "scroll", "upload", "back", "reload", "tab"],
                   session_id: str, profile: str | None = None, target: str | Target | None = None,
                   text: str | None = None, key: str | None = None, values: list[str] | None = None,
                   files: list[str] | None = None, down: int = 0, up: int = 0, index: int = 0,
                   close: bool = False, expect: list[str] | None = None) -> dict[str, Any]:
    return _call(operation, {"target": target, "text": text, "key": key, "values": values or [], "files": files or [],
                             "down": down, "up": up, "index": index, "close": close, "expect": expect or []}, session_id, profile)


def browser_read(operation: Literal["text", "value", "table", "tabs", "downloads", "state", "journal"],
                 session_id: str, profile: str | None = None, target: str | None = None,
                 command_id: str | None = None, offset: int = 0) -> dict[str, Any]:
    return _call(operation, {"target": target, "command_id": command_id, "offset": offset}, session_id, profile)


def browser_wait(session_id: str, profile: str | None = None, text: str | None = None,
                 signed_in: str | None = None, url_contains: str | None = None, seconds: float | None = None,
                 timeout: float = 60) -> dict[str, Any]:
    return _call("wait", {"text": text, "signed_in": signed_in, "url_contains": url_contains, "seconds": seconds}, session_id, profile, timeout)


def browser_screenshot(session_id: str, profile: str | None = None, target: str | None = None,
                       annotate: bool = False, full_page: bool = False) -> dict[str, Any]:
    return _call("screenshot", {"target": target, "annotate": annotate, "full_page": full_page}, session_id, profile)


def browser_check_url(url: str, session_id: str, profile: str | None = None) -> dict[str, Any]:
    return _call("diagnose", {"url": url}, session_id, profile)


def browser_api(url: str, session_id: str, profile: str | None = None, servicenow: bool = False) -> dict[str, Any]:
    """Same-origin GET only. Writes use browser_commit."""
    return _call("api", {"url": url, "servicenow": servicenow}, session_id, profile)


def browser_eval(js: str, session_id: str, profile: str | None = None) -> dict[str, Any]:
    """Commit-capable arbitrary JavaScript; caller permission rules should treat it as such."""
    return _call("eval", {"js": js}, session_id, profile)


def browser_commit(operation: Literal["click", "fill", "type", "press", "select", "check", "uncheck", "upload", "fill-form", "api", "run", "journal", "state"],
                   session_id: str, profile: str | None = None, target: str | Target | None = None,
                   text: str | None = None, key: str | None = None, values: list[str] | None = None,
                   files: list[str] | None = None, fields: list[dict[str, Any]] | None = None,
                   url: str | None = None, method: Literal["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"] = "GET",
                   body_file: str | None = None, servicenow: bool = False, recipe: Literal["sn/add-work-note"] | None = None,
                   number: str | None = None, clear: bool = False, handoff_id: str | None = None,
                   resolve: bool = False, expect: list[str] | None = None) -> dict[str, Any]:
    """Explicit committing operation. Require caller approval when the workflow requires human approval."""
    return _call(operation, {"target": target, "text": text, "key": key, "values": values or [], "files": files or [],
                             "fields": fields or [], "url": url, "method": method, "body_file": body_file, "servicenow": servicenow,
                             "recipe": recipe, "number": number, "clear": clear, "handoff_id": handoff_id,
                             "resolve": resolve, "expect": expect or []}, session_id, profile, committing=True)


def browser_recipe(number: str, text: str, session_id: str, profile: str | None = None) -> dict[str, Any]:
    """Preview the classic ServiceNow work-note recipe without editing."""
    return _call("run", {"recipe": "sn/add-work-note", "number": number, "text": text}, session_id, profile)


def browser_needs_human(reason: str, session_id: str, profile: str | None = None, signed_in: str | None = None) -> dict[str, Any]:
    return _call("needs-human", {"reason": reason, "condition": {"signed_in": signed_in} if signed_in else {}}, session_id, profile)


def browser_output(session_id: str, profile: str | None = None, output_id: str | None = None, offset: int = 0) -> dict[str, Any]:
    import argparse
    from .cli import _cmd_output, _json_line
    return json.loads(_json_line(_cmd_output(argparse.Namespace(session=session_id, profile=profile, id=output_id, offset=offset, page_size=900)), profile, session_id))


def browser_start(session_id: str, profile: str | None = None) -> dict[str, Any]:
    import argparse
    from . import session
    session_id = common.session_identity(session_id)
    result = session.start_session(common._paths(session._resolve_profile(profile)), argparse.Namespace(browser=None, exe=None, headless=False, window_size=None))
    return {**result, "contract_version": common.CONTRACT_VERSION, "session_id": session_id}


def browser_stop(session_id: str, profile: str | None = None) -> dict[str, Any]:
    return _call("stop", {}, session_id, profile)


def make_server():
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations, CallToolResult, TextContent
    except ImportError as exc:
        raise RuntimeError('Install the optional MCP extra: pip install -e "agent-browser[mcp]"') from exc
    def envelope(result, session_id=None, profile=None):
        from .cli import _json_line
        # The SDK sends both text and structuredContent. Budget their complete compact envelope.
        try:
            data = json.loads(_json_line(result, profile, session_id, byte_budget=7000))
        except (OSError, ValueError, RuntimeError):
            data = {"contract_version": common.CONTRACT_VERSION, "error": {"class": "output_storage", "message": "Cannot preserve the reply; inspect state before repeating an action."}}
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return CallToolResult(content=[TextContent(type="text", text=compact)], structuredContent=data, isError="error" in data)

    class BoundedServer(FastMCP):
        async def call_tool(self, name, arguments):
            try:
                return await super().call_tool(name, arguments)
            except Exception:
                # SDK argument errors may echo arbitrary text/scripts and can exceed the contract.
                return envelope({"error": {"class": "validation", "message": "Invalid tool arguments. Inspect the typed tool schema."}})

    def bounded(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
            except (ValueError, RuntimeError, OSError) as exc:
                result = {"error": {"class": getattr(exc, "error_class", "daemon"), "message": str(exc), "details": getattr(exc, "details", None)}}
            return envelope(result, kwargs.get("session_id"), kwargs.get("profile"))
        return invoke

    server = BoundedServer("agent-browser")
    read_only = {"browser_snapshot", "browser_read", "browser_wait", "browser_screenshot",
                 "browser_check_url", "browser_api", "browser_recipe", "browser_output"}
    additive = {"browser_start", "browser_needs_human"}
    for name, function in list(globals().items()):
        if name.startswith("browser_") and callable(function):
            server.tool(annotations=ToolAnnotations(readOnlyHint=name in read_only,
                        destructiveHint=name not in read_only and name not in additive),
                        structured_output=True)(bounded(function))
    return server


def main() -> None:
    make_server().run(transport="stdio")


if __name__ == "__main__":
    main()
