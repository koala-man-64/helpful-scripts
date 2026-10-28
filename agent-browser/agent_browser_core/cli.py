from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import common as core_common, daemon as core_daemon, session as core_session
from . import policy, evidence


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise core_common._tag(ValueError("Invalid command arguments. Use --help for command syntax."), core_common.ERR_USAGE)

    def parse_args(self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None) -> argparse.Namespace:  # type: ignore[override]
        # Sub-parsers write their namespace over the top-level one, so --expect after the verb is kept apart
        # (expect_tail) and appended here. Sub-parsers never call parse_args, so this merges exactly once.
        parsed = super().parse_args(args, namespace)
        tail = vars(parsed).pop("expect_tail", None)
        if tail:
            parsed.expect = list(getattr(parsed, "expect", None) or []) + tail
        return parsed


def _demangle_msys(argv: list[str]) -> list[str]:
    """Undo Git Bash (MSYS) path conversion of arguments that start with '/'."""
    if not os.getenv("MSYSTEM"):
        return argv
    roots = []
    exepath = os.getenv("EXEPATH")
    for root in (exepath, r"C:\Program Files\Git"):
        if root:
            roots.append(root.rstrip("\\/").replace("\\", "/").lower())
    out = []
    for arg in argv:
        fixed = arg
        for root in roots:
            lowered = fixed.replace("\\", "/")
            idx = lowered.lower().find(root + "/")
            if idx == 0 or (idx > 0 and lowered[idx - 1] == "="):
                fixed = lowered[:idx] + lowered[idx + len(root):]
                note = f"argument {arg!r} was rewritten by Git Bash; interpreted as {fixed!r}"
                cap = core_common.NOTE_CAP
                core_common._ARGV_NOTES.append(note if len(note) <= cap else note[:cap - 3] + "...")
                break
        out.append(fixed)
    return out


def _check_timeout(value: float, name: str = "--timeout", *, wait: bool = False) -> float:
    """The one timeout rule for CLI flags and MCP tool arguments: finite, 0 to MAX_TIMEOUT, and wait at most WAIT_CAP."""
    if wait and value > core_common.WAIT_CAP:
        raise core_common._tag(ValueError(f"{name} for wait is capped at {core_common.WAIT_CAP:g} seconds."), core_common.ERR_VALIDATION, hint=core_common._hint("wait_cap"))
    if not math.isfinite(value) or value < 0 or value > core_common.MAX_TIMEOUT:
        raise core_common._tag(ValueError(f"{name} must be finite and between 0 and {core_common.MAX_TIMEOUT:g} seconds"), core_common.ERR_VALIDATION)
    return float(value)


def _timeout_for(args: argparse.Namespace, default: float) -> float:
    value = getattr(args, "timeout", None)
    if value is None:
        value = core_common._env_float("AGENT_BROWSER_TIMEOUT_SECONDS", default) if default == core_common.DEFAULT_TIMEOUT else default
    return _check_timeout(value)


def _caller_paths(args: argparse.Namespace) -> tuple[core_common.ProfilePaths, dict[str, Any]]:
    profile = core_session._resolve_profile(getattr(args, "profile", None))
    paths = core_common.ProfilePaths(profile, core_common._home(), core_common.session_identity(getattr(args, "session", None)))
    return paths, policy.profile_config(core_common._load_config(), profile)


def _max_bytes(args: argparse.Namespace) -> int:
    value = getattr(args, "max_bytes", None)
    if value is None:
        value = int(core_common._env_float("AGENT_BROWSER_MAX_BYTES", core_common.DEFAULT_MAX_BYTES))
    return max(500, int(value))


def _session_for(args: argparse.Namespace, verb: str, autostart: bool = False) -> tuple[core_common.ProfilePaths, dict[str, Any]]:
    profile = core_session._resolve_profile(getattr(args, "profile", None))
    paths = core_common._paths(profile)
    state, session = core_session._transport_state(paths)
    if state != "running" and autostart and core_common._env_flag("AGENT_BROWSER_AUTOSTART", True) and state in ("none", "stale"):
        core_session.start_session(paths, argparse.Namespace(browser=None, exe=None, headless=False, window_size=None))
    session = core_session._require_running(paths, verb)
    return paths, session


def _merge_notes(result: dict[str, Any], notes: Iterable[str]) -> dict[str, Any]:
    """Append notes to result['note'] once; merging the same notes again changes nothing."""
    existing = result.get("note") if isinstance(result.get("note"), str) else ""
    fresh = list(dict.fromkeys(n for n in notes if n not in existing))
    if fresh:
        result["note"] = (existing + " " if existing else "") + " ".join(fresh)
    return result


def _finish(result: dict[str, Any], session: dict[str, Any] | None = None) -> dict[str, Any]:
    notes = list(core_common._ARGV_NOTES)
    if session is not None and session.get("code_hash") and session.get("code_hash") != core_common._code_hash():
        notes.append(core_common._hint("stale_code", session.get("profile", core_common.DEFAULT_PROFILE)))
    return _merge_notes(result, notes)


def _remote(args: argparse.Namespace, verb: str, cmd: str, payload: dict[str, Any], default_timeout: float = core_common.DEFAULT_TIMEOUT, autostart: bool = False) -> dict[str, Any]:
    if getattr(args, "tab", None) is not None:
        payload.setdefault("tab", core_common._parse_tab_spec(args.tab, getattr(args, "profile", None) or core_common.DEFAULT_PROFILE))
    paths, session = _session_for(args, verb, autostart=autostart)
    timeout = _timeout_for(args, default_timeout)
    payload.setdefault("max_bytes", _max_bytes(args))
    payload["_session_id"] = core_common.session_identity(getattr(args, "session", None))
    payload["_cwd"] = str(Path.cwd())
    if getattr(args, "annotate", False):
        payload["annotate"] = True
    if any(getattr(args, k, None) for k in ("role", "name", "label")):
        if payload.get("target"):
            raise core_common._tag(ValueError("Use a ref/selector or semantic targeting, not both."), core_common.ERR_USAGE)
        payload["target"] = {k: getattr(args, k) for k in ("role", "name", "label") if getattr(args, k, None)}
    if getattr(args, "expect", None):
        payload["expect"] = args.expect
    if getattr(args, "committing", False):
        payload["operation"], cmd = cmd, "commit"
    if getattr(args, "snapshot", False):
        payload["snapshot"] = True
    result = core_session._request(session, cmd, payload, timeout)
    while result.get("pending") and result.get("status") != "needs_human":
        core_common._sleep(0.2)
        result = core_session._request(session, "poll", {"command_id": result["command_id"], "_session_id": payload["_session_id"]}, 2.0)
    return _finish(result, session)


def _cmd_start(args: argparse.Namespace) -> Any:
    paths = core_common._paths(core_session._resolve_profile(args.profile, sticky=False))
    result = core_session.start_session(paths, args, timeout=_timeout_for(args, core_common.START_TIMEOUT))
    if args.url:
        session = core_session._require_running(paths, "start")
        env = core_session._request(session, "goto", {"url": core_common._check_url(args.url, paths, policy.profile_config(core_common._load_config(), paths.profile)), "max_bytes": _max_bytes(args), "_session_id": core_common.session_identity(getattr(args, "session", None))}, timeout=core_common.GOTO_TIMEOUT)
        keep = {"profile", "status", "pid", "port", "launcher", "log", "browser", "browser_version", "headless", "user_data_dir", "window"}
        result.update({k: v for k, v in env.items() if k not in keep})  # the navigation's url/title/snapshot win
    return _finish(result)


def _cmd_stop(args: argparse.Namespace) -> Any:
    if not args.all and not args.force:
        paths = core_common._paths(core_session._resolve_profile(args.profile))
        state, metadata = core_session._liveness(paths)
        if state in {"none", "stale"}:
            return core_session.stop_session(paths)
        result = _remote(args, "stop", "stop", {}, core_common.STOP_GRACE)
        if result.get("stopping") and metadata:
            deadline = core_common._now() + core_common.STOP_GRACE
            while core_common._pid_alive(int(metadata["pid"])):
                if core_common._now() >= deadline:
                    raise core_common._tag(RuntimeError("Session released; browser shutdown has not finished. Inspect status before forcing a profile stop."), core_common.ERR_TIMEOUT)
                core_common._sleep(.1)
        return result
    paths = core_common._paths(core_session._resolve_profile(args.profile))
    return core_session.stop_session(paths, force=args.force)


def _cmd_status(args: argparse.Namespace) -> Any:
    several = not args.profile and not os.getenv("AGENT_BROWSER_PROFILE") and len(core_session._running_profiles()) > 1
    if args.all or several:  # status never fails: with several browsers running it lists them all
        root = core_common._home() / "profiles"
        names = sorted(e.name for e in root.iterdir() if e.is_dir() and core_common._is_profile_name(e.name)) if root.is_dir() else []
        return {"home": str(core_common._home()), "profiles": [core_session.status_session(core_common._paths(n)) for n in names]}
    return core_session.status_session(core_common._paths(core_session._resolve_profile(args.profile)))


def _cmd_goto(args: argparse.Namespace) -> Any:
    if getattr(args, "new_tab", False) and getattr(args, "tab", None) is not None:
        raise core_common._tag(ValueError("Cannot combine --new-tab with --tab."), core_common.ERR_USAGE)
    profile = core_session._resolve_profile(args.profile)
    paths = core_common._paths(profile)
    url = core_common._check_url(args.url, paths, policy.profile_config(core_common._load_config(), profile))
    return _remote(args, "goto", "goto", {"url": url, "wait": args.wait, "new_tab": bool(args.new_tab), "discard_changes": bool(args.discard_changes)}, core_common.GOTO_TIMEOUT, autostart=True)


def _cmd_snapshot(args: argparse.Namespace) -> Any:
    return _remote(args, "snapshot", "snapshot", {"full": bool(args.full), "find": args.find, "ref": args.ref})


def _cmd_click(args: argparse.Namespace) -> Any:
    return _remote(args, "click", "click", {"target": args.target, "double": bool(args.double), "right": bool(args.right),
                                            "accept_dialog": bool(args.accept_dialog), "dialog_text": args.dialog_text,
                                            "discard_changes": bool(args.discard_changes)})


def _cmd_fill(args: argparse.Namespace) -> Any:
    if (getattr(args, "label", None) or getattr(args, "role", None)) and args.text is None and args.target is not None:
        args.text, args.target = args.target, None
    return _remote(args, "fill", "fill", {"target": args.target, "text": _input_text(args, "text"), "enter": bool(args.enter)})


def _cmd_type(args: argparse.Namespace) -> Any:
    if (getattr(args, "label", None) or getattr(args, "role", None)) and args.text is None and args.target is not None:
        args.text, args.target = args.target, None
    return _remote(args, "type", "type", {"target": args.target, "text": _input_text(args, "text"), "delay": args.delay, "append": bool(args.append), "enter": bool(args.enter)})


def _cmd_press(args: argparse.Namespace) -> Any:
    return _remote(args, "press", "press", {"key": args.key, "target": args.target})


def _cmd_select(args: argparse.Namespace) -> Any:
    return _remote(args, "select", "select", {"target": args.target, "values": args.values})


def _cmd_check(args: argparse.Namespace) -> Any:
    if args.target.lower().startswith(("http://", "https://")):
        return _remote(args, "check", "diagnose", {"url": args.target}, autostart=True)
    return _remote(args, "check", "check", {"target": args.target})


def _cmd_uncheck(args: argparse.Namespace) -> Any:
    return _remote(args, "uncheck", "uncheck", {"target": args.target})


def _cmd_hover(args: argparse.Namespace) -> Any:
    return _remote(args, "hover", "hover", {"target": args.target})


def _cmd_scroll(args: argparse.Namespace) -> Any:
    return _remote(args, "scroll", "scroll", {"target": args.target, "down": args.down, "up": args.up})


def _cmd_text(args: argparse.Namespace) -> Any:
    max_chars = args.max_chars if args.max_chars is not None else int(core_common._env_float("AGENT_BROWSER_MAX_CHARS", core_common.DEFAULT_TEXT_CHARS))
    return _remote(args, "text", "text", {"target": args.target, "max_chars": max_chars})


def _cmd_value(args: argparse.Namespace) -> Any:
    return _remote(args, "value", "value", {"target": args.target})


def _cmd_screenshot(args: argparse.Namespace) -> Any:
    profile = core_session._resolve_profile(args.profile)
    paths = core_common._paths(profile)
    path = None
    if args.path:
        candidate = Path(args.path)
        if candidate.suffix.lower() != ".png":
            raise core_common._tag(ValueError("--path must end with .png"), core_common.ERR_VALIDATION)
        paths, config = _caller_paths(args)
        path = str(core_common._check_local_path(args.path, paths, config, must_exist=False))
    return _remote(args, "screenshot", "screenshot", {"target": args.target, "path": path, "full_page": bool(args.full_page)})


def _cmd_wait(args: argparse.Namespace) -> Any:
    timeout = _check_timeout(args.timeout if args.timeout is not None else core_common.WAIT_DEFAULT, wait=True)
    args.timeout = timeout
    payload = {"url_contains": args.url_contains, "text": args.text, "ref": args.ref, "selector": args.selector, "signed_in": args.signed_in, "seconds": args.seconds}
    if not any(v not in (None, "", 0) for v in payload.values()):
        raise core_common._tag(ValueError("wait needs a condition."), core_common.ERR_USAGE, hint=core_common._hint("no_wait_condition"))
    return _remote(args, "wait", "wait", payload, timeout)


def _cmd_back(args: argparse.Namespace) -> Any:
    return _remote(args, "back", "back", {"discard_changes": bool(args.discard_changes)}, core_common.GOTO_TIMEOUT)


def _cmd_reload(args: argparse.Namespace) -> Any:
    return _remote(args, "reload", "reload", {"discard_changes": bool(args.discard_changes)}, core_common.GOTO_TIMEOUT)


def _cmd_tabs(args: argparse.Namespace) -> Any:
    return _remote(args, "tabs", "tabs", {})


def _cmd_tab(args: argparse.Namespace) -> Any:
    raw = args.index if args.index is not None else getattr(args, "tab", None)
    if raw is None:
        raise core_common._tag(ValueError("tab needs a tab index (1, 2) or id (t1, t2)."), core_common.ERR_USAGE, hint=core_common._hint("tab_index", getattr(args, "profile", None) or core_common.DEFAULT_PROFILE))
    if args.index is not None and getattr(args, "tab", None) is not None:
        idx_spec = core_common._parse_tab_spec(args.index, getattr(args, "profile", None) or core_common.DEFAULT_PROFILE)
        tab_spec = core_common._parse_tab_spec(args.tab, getattr(args, "profile", None) or core_common.DEFAULT_PROFILE)
        if idx_spec != tab_spec:
            raise core_common._tag(ValueError("Conflicting tab targets given as positional argument and --tab."), core_common.ERR_USAGE)
    target = core_common._parse_tab_spec(raw, getattr(args, "profile", None) or core_common.DEFAULT_PROFILE)
    return _remote(args, "tab", "tab", {"index": target, "close": bool(args.close), "discard_changes": bool(args.discard_changes)})


def _cmd_upload(args: argparse.Namespace) -> Any:
    paths, config = _caller_paths(args)
    files = [str(core_common._check_local_path(f, paths, config)) for f in args.files]
    return _remote(args, "upload", "upload", {"target": args.target, "files": files})


def _cmd_downloads(args: argparse.Namespace) -> Any:
    return _remote(args, "downloads", "downloads", {}, core_common.DOWNLOADS_TIMEOUT)


def _cmd_eval(args: argparse.Namespace) -> Any:
    return _remote(args, "eval", "eval", {"js": _input_text(args, "js"), "target": args.ref, "frame": args.frame})


def _input_text(args: argparse.Namespace, name: str) -> str:
    value, filename = getattr(args, name, None), getattr(args, name + "_file", None)
    if (value is None) == (filename is None):
        raise core_common._tag(ValueError(f"Provide exactly one of {name} or --{name}-file."), core_common.ERR_USAGE)
    if filename is None:
        return value
    paths, config = _caller_paths(args)
    path = core_common._check_local_path(filename, paths, config)
    if path.stat().st_size > core_common.REQUEST_CAP // 2:
        raise core_common._tag(ValueError("Input file exceeds the command payload budget."), core_common.ERR_VALIDATION)
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise core_common._tag(ValueError("Input file must be readable UTF-8 text."), core_common.ERR_VALIDATION) from exc


def _cmd_output(args: argparse.Namespace) -> dict[str, Any]:
    paths = core_common._paths(core_session._resolve_profile(args.profile))
    paths = core_common.ProfilePaths(paths.profile, paths.root, core_common.session_identity(getattr(args, "session", None)))
    if args.offset < 0:
        raise core_common._tag(ValueError("--offset must be >= 0."), core_common.ERR_VALIDATION)
    if args.id and not re.fullmatch(r"r?[a-f0-9]{32}", args.id):
        raise core_common._tag(ValueError("Invalid output ID."), core_common.ERR_VALIDATION)
    is_receipt = bool(args.id and args.id.startswith("r"))
    path = paths.outputs / (args.id + ".json") if args.id else paths.workspace / "last_output.json"
    data = None
    if not is_receipt:
        try:
            data = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise core_common._tag(ValueError("Output not found."), core_common.ERR_NOT_FOUND) from exc
    if data is None:
        command_id = args.id[1:] if is_receipt else args.id
        receipt = evidence.read_receipt(paths.dir, core_common.session_identity(getattr(args, "session", None)), command_id) if command_id else None
        if receipt is None:
            raise core_common._tag(ValueError("Output not found or receipt is not complete."), core_common.ERR_NOT_FOUND)
        data = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    page_size = min(3000, max(1, getattr(args, "page_size", 3000)))
    chunk = data[args.offset:args.offset + page_size]
    end = args.offset + len(chunk)
    return {"offset": args.offset, "next_offset": end if end < len(data) else None,
            "total_chars": len(data), "chunk": chunk}


def _json_line(payload: Any, profile: str | None = None, session_id: str | None = None, *, byte_budget: int = core_common.STDOUT_BUDGET) -> str:
    result = dict(payload) if isinstance(payload, dict) else {"result": payload}
    result["contract_version"] = core_common.CONTRACT_VERSION
    encoded = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) + 1 <= byte_budget:
        return encoded
    paths = core_common._paths(profile or result.get("profile") or core_session._resolve_profile(None))
    paths = core_common.ProfilePaths(paths.profile, paths.root, result.get("session_id") or core_common.session_identity(session_id))
    quota = policy.profile_config(core_common._load_config(), paths.profile).get("evidence_max_bytes", evidence.DEFAULT_QUOTA)
    evidence.require_space(paths.dir, quota, 2 * len(encoded.encode("utf-8")))
    paths.outputs.mkdir(parents=True, exist_ok=True)
    output_id = result.get("command_id") or secrets.token_hex(16)
    if not isinstance(output_id, str) or not re.fullmatch(r"[0-9a-f]{32}", output_id):
        output_id = secrets.token_hex(16)
    output_file = paths.outputs / (output_id + ".json")
    # A receipt may be rendered again after polling. Never replace an earlier artifact.
    while True:
        try:
            with output_file.open("x", encoding="utf-8", newline="") as handle:
                handle.write(encoded)
            break
        except FileExistsError:
            if output_file.read_text(encoding="utf-8") == encoded:
                break
            output_id = secrets.token_hex(16)
            output_file = paths.outputs / (output_id + ".json")
    core_session._write_json_atomic(paths.workspace / "last_output.json", result, compact=True)
    # url and title say which page the stored reply describes; untrusted keeps marking them as page content.
    bounded = {k: result[k] for k in ("ok", "action", "effect", "observed", "command_id", "session_id", "pending", "status", "executed", "receipt", "note",
                                      "url", "title", "untrusted") if k in result}
    bounded.update(contract_version=core_common.CONTRACT_VERSION, truncated=True, output_id=output_id,
                   output_file=str(output_file), next_offset=0)
    if "error" in result:
        original = result["error"].get("details") or {}
        bounded["error"] = {"class": result["error"].get("class"), "http_status": None,
                            "message": "Error details exceeded the reply budget; read output by ID.", "hint": None,
                            "details": {k: original[k] for k in ("command_id", "executed", "receipt") if k in original}}
        if result["error"].get("note"):
            bounded["error"]["note"] = result["error"]["note"]
    line = json.dumps(bounded, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(line.encode("utf-8")) + 1 > byte_budget:
        bounded = {k: bounded[k] for k in ("contract_version", "truncated", "output_id", "next_offset")}
        if "error" in result:
            bounded["error"] = {"class": "oversized_error", "message": "Read full error details by output ID."}
        line = json.dumps(bounded, separators=(",", ":"))
    return line


def _cmd_doctor(args: argparse.Namespace) -> Any:
    return core_session.doctor(browser=args.browser, exe=args.exe, live=bool(args.live))


def _cmd_clean(args: argparse.Namespace) -> Any:
    return core_session.clean(profile=args.profile, purge=args.purge_profile, dry_run=bool(args.dry_run))


def _cmd_serve(args: argparse.Namespace) -> int:
    return core_daemon.serve(Path(args.launch_file))


_EPILOG = f"""\
examples:
  {core_common.TOOL_NAME} goto https://example.com            open (or reuse) the window; prints the page as refs
  {core_common.TOOL_NAME} click e12                            act on a ref from the newest snapshot
  {core_common.TOOL_NAME} fill f2e16 "new text"                iframe refs (f2e16) work exactly like e12
  {core_common.TOOL_NAME} select f2e14 "2 - High"
  {core_common.TOOL_NAME} wait --signed-in dev12345.service-now.com   pause while a human signs in
  {core_common.TOOL_NAME} text                                 readable page text (all frames)
  {core_common.TOOL_NAME} screenshot                           PNG under the profile folder; Read the printed path
  {core_common.TOOL_NAME} stop                                 close the window (the sign-in is kept)

output contract:
  success -> JSON on stdout, exit 0. Failure -> {{"error": {{"class", "message", "hint"}}}} on stderr:
  exit 2 = fix the call (stale ref, bad target, guarded), exit 1 = fix the environment or start the
  browser, 124 = timed out and the command may already have run: read error.details.receipt (or run
  state) before repeating it; only a timed-out wait is safe to run again. 130 = interrupted (same caution).
"""

_TIMEOUT_HELP = (f"seconds, 0-{core_common.MAX_TIMEOUT:g}. Defaults: {core_common.DEFAULT_TIMEOUT:g} for actions "
                 f"(AGENT_BROWSER_TIMEOUT_SECONDS), {core_common.GOTO_TIMEOUT:g} goto/back/reload, {core_common.START_TIMEOUT:g} start "
                 f"(browser boot only), {core_common.STOP_GRACE:g} stop, {core_common.DOWNLOADS_TIMEOUT:g} downloads, "
                 f"{core_common.WAIT_DEFAULT:g} wait. Only wait is capped, at {core_common.WAIT_CAP:g}.")


def _build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog=core_common.TOOL_NAME, description="Drive a real, visible Edge/Chrome window; JSON in and out, refs from snapshots.",
                                     epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{core_common.TOOL_NAME} {core_common.TOOL_VERSION}")
    parser.add_argument("--profile", metavar="NAME", default=None, help="browser profile (cookies persist per profile; default: 'default')")
    parser.add_argument("--tab", metavar="TAB", default=None, help="target a specific tab by id (t1, t2) or 1-based index without switching the active tab")
    parser.add_argument("--timeout", type=float, metavar="SECONDS", default=None, help=_TIMEOUT_HELP)
    parser.add_argument("--max-bytes", type=int, metavar="N", default=None, help="snapshot byte budget per response (default 16000)")
    parser.add_argument("--snapshot", action="store_true", default=False, help="return a full snapshot after an action instead of the compact change list")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", metavar="NAME", default=argparse.SUPPRESS)
    common.add_argument("--tab", metavar="TAB", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, metavar="SECONDS", default=argparse.SUPPRESS, help=_TIMEOUT_HELP)
    common.add_argument("--max-bytes", type=int, metavar="N", default=argparse.SUPPRESS)
    common.add_argument("--snapshot", action="store_true", default=argparse.SUPPRESS)

    def add(name: str, func: Callable[[argparse.Namespace], Any], help_text: str, **kw: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=help_text, description=help_text, **kw)
        p.set_defaults(func=func)
        return p

    p = add("start", _cmd_start, "open the browser window for a profile (idempotent; never a second window)")
    p.add_argument("--url", help="navigate after starting")
    p.add_argument("--browser", choices=("msedge", "chrome"), default=None)
    p.add_argument("--exe", help="explicit browser executable")
    p.add_argument("--headless", action="store_true", help="no window (tests; a human cannot sign in)")
    p.add_argument("--window-size", metavar="W,H")
    p = add("stop", _cmd_stop, "close the browser (cookies and sign-in stay on disk)")
    p.add_argument("--all", action="store_true", help="stop the whole profile: every session and the browser (this profile only; other profiles keep running)")
    p.add_argument("--force", action="store_true", help="kill without asking the daemon")
    p = add("status", _cmd_status, "is the browser running; tabs; staleness (exit 0 whether or not a browser runs; 2 for an invalid or reserved --profile)")
    p.add_argument("--all", action="store_true", help="list every profile under the home folder instead of one")
    p = add("goto", _cmd_goto, "open a URL (starts the browser if needed) and print the page as refs")
    p.add_argument("url")
    p.add_argument("--wait", choices=("load", "domcontentloaded"), default="load")
    p.add_argument("--new-tab", action="store_true")
    p.add_argument("--discard-changes", action="store_true", help="leave a page that has unsaved edits")
    p = add("snapshot", _cmd_snapshot, "print the current page as refs")
    p.add_argument("--full", action="store_true", help="the raw accessibility tree")
    p.add_argument("--find", metavar="TEXT", help="only lines containing TEXT (plus their parents)")
    p.add_argument("--ref", metavar="REF", help="only the subtree under REF")
    p = add("click", _cmd_click, "click a ref")
    p.add_argument("target")
    p.add_argument("--double", action="store_true")
    p.add_argument("--right", action="store_true")
    p.add_argument("--accept-dialog", action="store_true", help="accept a confirm()/prompt() the click opens")
    p.add_argument("--dialog-text", metavar="TEXT", help="answer for a prompt() when accepting")
    p.add_argument("--discard-changes", action="store_true", help="leave a page that has unsaved edits")
    p = add("fill", _cmd_fill, "replace the text of a field (refuses password/code fields)")
    p.add_argument("target")
    p.add_argument("text", nargs="?")
    p.add_argument("--text-file")
    p.add_argument("--enter", action="store_true")
    p = add("type", _cmd_type, "type into a field key by key (typeahead fields); clears it first unless --append")
    p.add_argument("target")
    p.add_argument("text", nargs="?")
    p.add_argument("--text-file")
    p.add_argument("--delay", type=int, default=30, metavar="MS")
    p.add_argument("--append", action="store_true")
    p.add_argument("--enter", action="store_true")
    p = add("press", _cmd_press, "press a key (Enter, Tab, Escape, Control+a) on the page or a ref")
    p.add_argument("key")
    p.add_argument("target", nargs="?")
    p = add("select", _cmd_select, "choose option(s) in a drop-down by label or value")
    p.add_argument("target")
    p.add_argument("values", nargs="+")
    p = add("check", _cmd_check, "tick a checkbox or radio")
    p.add_argument("target")
    p = add("uncheck", _cmd_uncheck, "untick a checkbox")
    p.add_argument("target")
    p = add("hover", _cmd_hover, "hover a ref")
    p.add_argument("target")
    p = add("scroll", _cmd_scroll, "scroll a ref into view, or the page by pixels")
    p.add_argument("target", nargs="?")
    p.add_argument("--down", type=int, metavar="PX")
    p.add_argument("--up", type=int, metavar="PX")
    p = add("text", _cmd_text, "readable text of the page (all frames) or of a ref")
    p.add_argument("target", nargs="?")
    p.add_argument("--max-chars", type=int, default=None)
    p = add("value", _cmd_value, "current value of a field (masked for password/code fields)")
    p.add_argument("target")
    p = add("screenshot", _cmd_screenshot, "PNG of the page or a ref; Read the printed path")
    p.add_argument("target", nargs="?")
    p.add_argument("--path", metavar="FILE.png")
    p.add_argument("--full-page", action="store_true")
    p = add("wait", _cmd_wait, f"wait until every given condition holds (default {core_common.WAIT_DEFAULT:g} s, max {core_common.WAIT_CAP:g} s per call)")
    p.add_argument("--url-contains", metavar="TEXT")
    p.add_argument("--text", metavar="TEXT")
    p.add_argument("--ref", metavar="REF")
    p.add_argument("--selector", metavar="SEL")
    p.add_argument("--signed-in", metavar="HOST", help="page is on HOST, not a sign-in page, no password field")
    p.add_argument("--seconds", type=float, metavar="N")
    p = add("back", _cmd_back, "browser back")
    p.add_argument("--discard-changes", action="store_true", help="leave a page that has unsaved edits")
    p = add("reload", _cmd_reload, "reload the page")
    p.add_argument("--discard-changes", action="store_true", help="reload even if the page has unsaved edits")
    add("tabs", _cmd_tabs, "list open tabs")
    p = add("tab", _cmd_tab, "switch to (or close) a tab by index or id (t1, t2)")
    p.add_argument("index", nargs="?", default=None)
    p.add_argument("--close", action="store_true")
    p.add_argument("--discard-changes", action="store_true", help="close even if the tab has unsaved edits")
    p = add("upload", _cmd_upload, "attach local file(s) to a file input or a button that opens a chooser")
    p.add_argument("target")
    p.add_argument("files", nargs="+")
    add("downloads", _cmd_downloads, "save and list files downloaded during this session")
    p = add("eval", _cmd_eval, "run JavaScript on the page (last resort; never for credentials)")
    p.add_argument("js", nargs="?")
    p.add_argument("--js-file")
    p.add_argument("--ref", metavar="REF", help="run with the element as `el`")
    p.add_argument("--frame", metavar="NAME")
    p = add("doctor", _cmd_doctor, "preflight checks (always exit 0)")
    p.add_argument("--live", action="store_true", help="also boot a headless browser and click a ref")
    p.add_argument("--browser", choices=("msedge", "chrome"), default=None)
    p.add_argument("--exe")
    p = add("clean", _cmd_clean, "remove stale sessions, orphaned browsers, and old downloads")
    p.add_argument("--purge-profile", metavar="NAME", help="delete a profile folder (sign-in lost)")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("serve", help=argparse.SUPPRESS)
    p.add_argument("--launch-file", required=True)
    p.set_defaults(func=_cmd_serve)
    p = add("output", _cmd_output, "page a saved overflow result without repeating its command")
    p.add_argument("--id")
    p.add_argument("--offset", type=int, default=0)
    from . import commands
    commands.configure(parser, sub, add)
    return parser


def _error_payload(exc: BaseException) -> dict[str, Any]:
    """The one error object: CLI stderr and MCP replies both carry it."""
    default_class = core_common.ERR_VALIDATION if isinstance(exc, ValueError) else core_common.ERR_DAEMON
    payload: dict[str, Any] = {"error": {"class": getattr(exc, "error_class", default_class), "http_status": None,
                                         "message": str(exc), "hint": getattr(exc, "hint", None)}}
    details = getattr(exc, "details", None)
    if details is not None:
        payload["error"]["details"] = details
    return payload


def _normalize_exception(exc: Exception, profile: str | None = None) -> Exception:
    """Tagged errors (a tagged OSError too) pass unchanged; anything else gets a class and a message that echoes nothing."""
    if getattr(exc, "error_class", None):
        return exc
    if isinstance(exc, OSError):
        return core_common._tag(RuntimeError("Local file or socket access failed."), core_common.ERR_IO, details=getattr(exc, "details", None))
    if isinstance(exc, (ValueError, RuntimeError)):
        return exc
    return core_common._tag(RuntimeError("Command failed unexpectedly; inspect its receipt before repeating an action."), core_common.ERR_DAEMON,
                            hint=core_common._hint("inspect_receipt", profile), details={"exception": type(exc).__name__})


def _exit_code(exc: BaseException) -> int:
    if isinstance(exc, ValueError):
        return 2
    return core_common.EXIT_TIMEOUT if getattr(exc, "error_class", None) == core_common.ERR_TIMEOUT else 1


def _emit_error(exc: BaseException, profile: str | None = None) -> None:
    _emit_payload(_error_payload(exc), profile)


def _emit_payload(payload: dict[str, Any], profile: str | None = None) -> None:
    """Write one error envelope to stderr, with the note on any argument Git Bash rewrote."""
    if core_common._ARGV_NOTES:
        payload["error"]["note"] = " ".join(dict.fromkeys(core_common._ARGV_NOTES))
    try:
        line = _json_line(payload)
    except Exception as exc:  # noqa: BLE001 - the error must still reach stderr, with its receipt pointer
        line = json.dumps({**core_common._storage_error(payload, exc, profile), "contract_version": core_common.CONTRACT_VERSION},
                          ensure_ascii=False, separators=(",", ":"))
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass  # stderr is closed as well; the exit code is all that is left


def _print_result(result: Any, profile: str | None, session_id: str | None) -> int:
    """Print a success reply. Not preserving it and not writing it are different errors; both keep the receipt."""
    result = _merge_notes(result if isinstance(result, dict) else {"result": result}, core_common._ARGV_NOTES)
    try:
        line = _json_line(result, profile, session_id)
    except Exception as exc:  # noqa: BLE001 - the command already ran
        _emit_payload(core_common._storage_error(result, exc, profile), profile)
        return 1
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        _emit_error(core_common._tag(RuntimeError("Cannot write the reply to stdout; inspect its receipt before repeating an action."), core_common.ERR_IO,
                                     hint=core_common._hint("inspect_receipt", profile), details=core_common._preservation_details(result)), profile)
        return 1
    return 0


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if not stream.isatty():
                reconfigure(encoding="utf-8", errors="backslashreplace")
            else:
                reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    core_common._ARGV_NOTES.clear()
    try:
        args = _build_parser().parse_args(_demangle_msys(raw))
    except Exception as failure:  # noqa: BLE001 - SystemExit (--help, --version) is not an Exception
        exc = _normalize_exception(failure)
        _emit_error(exc)
        return _exit_code(exc)
    core_common.VERBOSE = False
    _reconfigure_streams()
    profile = getattr(args, "profile", None)
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        _emit_error(core_common._tag(RuntimeError("Interrupted; inspect the receipt before repeating an action."), "interrupted",
                                     hint=core_common._hint("inspect_receipt", profile)), profile)
        return core_common.EXIT_INTERRUPT
    except Exception as failure:  # noqa: BLE001 - every failure is one JSON envelope, never a traceback
        exc = _normalize_exception(failure, profile)
        _emit_error(exc, profile)
        return _exit_code(exc)
    if isinstance(result, bool) or not isinstance(result, int):
        return _print_result(result, profile, getattr(args, "session", None))
    return result
