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
from . import api, client, common


class Target(TypedDict, total=False):
    ref: str
    role: str
    name: str
    label: str


class Field(TypedDict, total=False):
    target: str | Target
    operation: Literal["fill", "select", "check", "uncheck"]
    value: str | list[str] | bool


REPLY_BUDGET = 7000                    # one tool reply: its text and structuredContent are both this compact JSON


MAX_BYTES = 4500                       # default snapshot budget; leaves room in REPLY_BUDGET for url, title, notes and the receipt


DIALOG_KEYS = ("accept_dialog", "dialog_text", "discard_changes")  # only browser_commit and browser_stop take them
NO_DIALOG_OPERATIONS = frozenset({"api", "run", "journal", "state"})  # their CLI verbs have no dialog flags


def _payload(command: str, arguments: dict[str, Any], profile: str | None) -> dict[str, Any]:
    """Tool arguments as the CLI's payload: the same keys and values, validated where the CLI validates them."""
    payload = {k: v for k, v in arguments.items() if not (k in ("tab", "index") and v is None)}
    for key in ("tab", "index"):
        if key in payload:
            payload[key] = common._parse_tab_spec(payload[key], profile or common.DEFAULT_PROFILE)
    if command == "tab" and "index" in payload and "tab" in payload and payload["index"] != payload["tab"]:
        raise common._tag(ValueError("Conflicting tab targets given as index and tab."), common.ERR_USAGE)
    content_type = payload.pop("content_type", None)
    if command == "api" and payload.get("body_file"):
        payload["headers"] = api.body_headers(content_type)
    elif content_type is not None:
        raise common._tag(ValueError("content_type needs operation api with a body_file."), common.ERR_USAGE)
    if payload.get("dialog_text") is not None and not payload.get("accept_dialog"):
        raise common._tag(ValueError("dialog_text answers a prompt only together with accept_dialog."), common.ERR_USAGE)
    if command in NO_DIALOG_OPERATIONS and any(payload.get(key) for key in DIALOG_KEYS):
        raise common._tag(ValueError("accept_dialog, dialog_text and discard_changes apply to page actions, not this operation."), common.ERR_USAGE)
    payload.setdefault("max_bytes", MAX_BYTES)
    return payload


def _call(command: str, arguments: dict[str, Any], session_id: str, profile: str | None, timeout: float = 15, committing: bool = False,
          wait: bool = False) -> dict[str, Any]:
    from .cli import _check_timeout, _error_payload, _json_line
    try:
        payload, seconds = _payload(command, arguments, profile), _check_timeout(timeout, "timeout", wait=wait)
        result = client.invoke(command, payload, session_id=session_id, profile=profile, timeout=seconds, committing=committing)
    except (ValueError, RuntimeError) as exc:
        result = _error_payload(exc)
    try:
        return json.loads(_json_line(result, profile, session_id))
    except Exception as exc:  # noqa: BLE001 - the command may have run: keep its receipt pointer
        return {**common._storage_error(result, exc, profile), "contract_version": common.CONTRACT_VERSION}


def browser_goto(url: str, session_id: str, profile: str | None = None, timeout: float = 30,
                 wait: Literal["load", "domcontentloaded"] = "load", new_tab: bool = False, tab: int | str | None = None) -> dict[str, Any]:
    """Navigate the caller's own tab. Sign in manually when requested."""
    return _call("goto", {"url": url, "wait": wait, "new_tab": new_tab, "tab": tab}, session_id, profile, timeout)


def browser_snapshot(session_id: str, profile: str | None = None, find: str | None = None, full: bool = False,
                     ref: str | None = None, tab: int | str | None = None) -> dict[str, Any]:
    return _call("snapshot", {"find": find, "full": full, "ref": ref, "tab": tab}, session_id, profile)


def browser_click(target: str | Target, session_id: str, profile: str | None = None, expect: list[str] | None = None,
                  tab: int | str | None = None) -> dict[str, Any]:
    return _call("click", {"target": target, "expect": expect or [], "tab": tab}, session_id, profile)


def browser_fill(target: str | Target, text: str, session_id: str, profile: str | None = None, expect: list[str] | None = None,
                 tab: int | str | None = None) -> dict[str, Any]:
    return _call("fill", {"target": target, "text": text, "expect": expect or [], "tab": tab}, session_id, profile)


def browser_fill_form(fields: list[Field], session_id: str, profile: str | None = None, tab: int | str | None = None) -> dict[str, Any]:
    return _call("fill-form", {"fields": fields, "tab": tab}, session_id, profile)


def browser_action(operation: Literal["type", "press", "select", "check", "uncheck", "hover", "scroll", "upload", "back", "reload", "tab"],
                   session_id: str, profile: str | None = None, target: str | Target | None = None,
                   text: str | None = None, key: str | None = None, values: list[str] | None = None,
                   files: list[str] | None = None, down: int = 0, up: int = 0, index: int | str | None = None,
                   close: bool = False, expect: list[str] | None = None, tab: int | str | None = None) -> dict[str, Any]:
    """index picks the tab for operation tab: a 1-based index (1, 2) or a stable id (t1, t2)."""
    return _call(operation, {"target": target, "text": text, "key": key, "values": values or [], "files": files or [],
                             "down": down, "up": up, "index": index, "close": close, "expect": expect or [], "tab": tab}, session_id, profile)


def browser_read(operation: Literal["text", "value", "table", "tabs", "downloads", "state", "journal"],
                 session_id: str, profile: str | None = None, target: str | None = None,
                 command_id: str | None = None, offset: int = 0, max_chars: int | None = None,
                 tab: int | str | None = None) -> dict[str, Any]:
    return _call(operation, {"target": target, "command_id": command_id, "offset": offset, "max_chars": max_chars, "tab": tab},
                 session_id, profile)


def browser_wait(session_id: str, profile: str | None = None, text: str | None = None,
                 signed_in: str | None = None, url_contains: str | None = None, seconds: float | None = None,
                 timeout: float = 60, ref: str | None = None, selector: str | None = None,
                 tab: int | str | None = None) -> dict[str, Any]:
    """timeout is at most 100 seconds per call; call again to keep waiting."""
    return _call("wait", {"text": text, "signed_in": signed_in, "url_contains": url_contains, "seconds": seconds,
                          "ref": ref, "selector": selector, "tab": tab}, session_id, profile, timeout, wait=True)


def browser_screenshot(session_id: str, profile: str | None = None, target: str | None = None,
                       annotate: bool = False, full_page: bool = False, tab: int | str | None = None) -> dict[str, Any]:
    return _call("screenshot", {"target": target, "annotate": annotate, "full_page": full_page, "tab": tab}, session_id, profile)


def browser_check_url(url: str, session_id: str, profile: str | None = None) -> dict[str, Any]:
    return _call("diagnose", {"url": url}, session_id, profile)


def browser_api(url: str, session_id: str, profile: str | None = None, servicenow: bool = False,
                tab: int | str | None = None) -> dict[str, Any]:
    """Same-origin GET only. Writes use browser_commit."""
    return _call("api", {"url": url, "method": "GET", "servicenow": servicenow, "tab": tab}, session_id, profile)


def browser_eval(js: str, session_id: str, profile: str | None = None, ref: str | None = None, frame: str | None = None,
                 tab: int | str | None = None) -> dict[str, Any]:
    """Commit-capable arbitrary JavaScript; caller permission rules should treat it as such. ref runs it with the element as el."""
    return _call("eval", {"js": js, "target": ref, "frame": frame, "tab": tab}, session_id, profile)


def browser_commit(operation: Literal["click", "fill", "type", "press", "select", "check", "uncheck", "upload", "fill-form", "api", "run", "journal", "state"],
                   session_id: str, profile: str | None = None, target: str | Target | None = None,
                   text: str | None = None, key: str | None = None, values: list[str] | None = None,
                   files: list[str] | None = None, fields: list[dict[str, Any]] | None = None,
                   url: str | None = None, method: Literal["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"] = "GET",
                   body_file: str | None = None, servicenow: bool = False, recipe: Literal["sn/add-work-note"] | None = None,
                   number: str | None = None, clear: bool = False, handoff_id: str | None = None,
                   resolve: bool = False, expect: list[str] | None = None, content_type: str | None = None,
                   tab: int | str | None = None, include_unknown: bool = False, accept_dialog: bool = False,
                   dialog_text: str | None = None, discard_changes: bool = False) -> dict[str, Any]:
    """Explicit committing operation. Require caller approval when the workflow requires human approval.
    An api body_file is sent as application/json unless content_type names another type.
    journal with clear keeps unknown write receipts and open handoffs; include_unknown removes them too.
    accept_dialog (with dialog_text for a prompt) and discard_changes answer a confirm or an unsaved-edits
    prompt for page actions; pass them only with the user's authorization."""
    return _call(operation, {"target": target, "text": text, "key": key, "values": values or [], "files": files or [],
                             "fields": fields or [], "url": url, "method": method, "body_file": body_file, "servicenow": servicenow,
                             "recipe": recipe, "number": number, "clear": clear, "handoff_id": handoff_id,
                             "resolve": resolve, "expect": expect or [], "content_type": content_type, "tab": tab,
                             "include_unknown": include_unknown, "accept_dialog": accept_dialog, "dialog_text": dialog_text,
                             "discard_changes": discard_changes},
                 session_id, profile, committing=True)


def browser_recipe(number: str, text: str, session_id: str, profile: str | None = None, tab: int | str | None = None) -> dict[str, Any]:
    """Preview the classic ServiceNow work-note recipe without editing."""
    return _call("run", {"recipe": "sn/add-work-note", "number": number, "text": text, "tab": tab}, session_id, profile)


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


def browser_stop(session_id: str, profile: str | None = None, discard_changes: bool = False) -> dict[str, Any]:
    """Release this session. Tabs with unsaved edits stay open and the session is kept (unsaved_changes) unless
    discard_changes, which loses those edits: pass it only with the user's authorization."""
    return _call("stop", {"discard_changes": discard_changes}, session_id, profile)


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
            data = json.loads(_json_line(result, profile, session_id, byte_budget=REPLY_BUDGET))
        except Exception as exc:  # noqa: BLE001 - keep the receipt pointer when the reply cannot be stored
            data = {**common._storage_error(result, exc, profile), "contract_version": common.CONTRACT_VERSION}
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return CallToolResult(content=[TextContent(type="text", text=compact)], structuredContent=data, isError="error" in data)

    class BoundedServer(FastMCP):
        async def call_tool(self, name, arguments):
            try:
                return await super().call_tool(name, arguments)
            except Exception:
                # Tools never raise (see bounded), so this is a bad argument or an unknown tool.
                # SDK argument errors may echo arbitrary text/scripts and can exceed the contract.
                return envelope({"error": {"class": common.ERR_VALIDATION, "http_status": None,
                                           "message": "Invalid tool arguments. Inspect the typed tool schema.",
                                           "hint": common._hint("tool_arguments")}})

    def bounded(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            from .cli import _error_payload, _normalize_exception
            try:
                result = function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - one envelope for every failure, never the SDK's
                result = _error_payload(_normalize_exception(exc, kwargs.get("profile")))
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
