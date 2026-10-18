from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import common as core_common, daemon as core_daemon, session as core_session


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
                core_common._ARGV_NOTES.append(f"argument {arg!r} was rewritten by Git Bash; interpreted as {fixed!r}")
                break
        out.append(fixed)
    return out


def _timeout_for(args: argparse.Namespace, default: float) -> float:
    value = getattr(args, "timeout", None)
    if value is None:
        value = core_common._env_float("AGENT_BROWSER_TIMEOUT_SECONDS", default) if default == core_common.DEFAULT_TIMEOUT else default
    if value < 0:
        raise core_common._tag(ValueError("--timeout must be >= 0"), core_common.ERR_VALIDATION)
    return float(value)


def _max_bytes(args: argparse.Namespace) -> int:
    value = getattr(args, "max_bytes", None)
    if value is None:
        value = int(core_common._env_float("AGENT_BROWSER_MAX_BYTES", core_common.DEFAULT_MAX_BYTES))
    return max(500, int(value))


def _session_for(args: argparse.Namespace, verb: str, autostart: bool = False) -> tuple[core_common.ProfilePaths, dict[str, Any]]:
    profile = core_session._resolve_profile(getattr(args, "profile", None))
    paths = core_common._paths(profile)
    state, session = core_session._liveness(paths)
    if state != "running" and autostart and core_common._env_flag("AGENT_BROWSER_AUTOSTART", True) and state in ("none", "stale"):
        core_session.start_session(paths, argparse.Namespace(browser=None, exe=None, headless=False, window_size=None))
    session = core_session._require_running(paths, verb)
    return paths, session


def _finish(result: dict[str, Any], session: dict[str, Any] | None = None) -> dict[str, Any]:
    notes = list(core_common._ARGV_NOTES)
    if session is not None and session.get("code_hash") and session.get("code_hash") != core_common._code_hash():
        notes.append(core_common._hint("stale_code", session.get("profile", core_common.DEFAULT_PROFILE)))
    if notes:
        result["note"] = (result.get("note") + " " if result.get("note") else "") + " ".join(notes)
    return result


def _remote(args: argparse.Namespace, verb: str, cmd: str, payload: dict[str, Any], default_timeout: float = core_common.DEFAULT_TIMEOUT, autostart: bool = False) -> dict[str, Any]:
    paths, session = _session_for(args, verb, autostart=autostart)
    timeout = _timeout_for(args, default_timeout)
    payload.setdefault("max_bytes", _max_bytes(args))
    if getattr(args, "snapshot", False):
        payload["snapshot"] = True
    return _finish(core_session._request(session, cmd, payload, timeout), session)


def _cmd_start(args: argparse.Namespace) -> Any:
    paths = core_common._paths(core_session._resolve_profile(args.profile, sticky=False))
    result = core_session.start_session(paths, args, timeout=_timeout_for(args, core_common.START_TIMEOUT))
    if args.url:
        session = core_session._require_running(paths, "start")
        env = core_session._request(session, "goto", {"url": core_common._check_url(args.url, paths, core_common._load_config()), "max_bytes": _max_bytes(args)}, timeout=core_common.GOTO_TIMEOUT)
        keep = {"profile", "status", "pid", "port", "launcher", "log", "browser", "browser_version", "headless", "user_data_dir", "window"}
        result.update({k: v for k, v in env.items() if k not in keep})  # the navigation's url/title/snapshot win
    return _finish(result)


def _cmd_stop(args: argparse.Namespace) -> Any:
    if args.all:
        names = core_session._running_profiles()
        root = core_common._home() / "profiles"
        if root.is_dir():
            names = sorted({*names, *[e.name for e in root.iterdir() if e.is_dir() and (e / "core_session.json").is_file()]})
        return {"stopped": [core_session.stop_session(core_common._paths(n), force=args.force) for n in names]}
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
    profile = core_session._resolve_profile(args.profile)
    paths = core_common._paths(profile)
    url = core_common._check_url(args.url, paths, core_common._load_config())
    return _remote(args, "goto", "goto", {"url": url, "wait": args.wait, "new_tab": bool(args.new_tab), "discard_changes": bool(args.discard_changes)}, core_common.GOTO_TIMEOUT, autostart=True)


def _cmd_snapshot(args: argparse.Namespace) -> Any:
    return _remote(args, "snapshot", "snapshot", {"full": bool(args.full), "find": args.find, "ref": args.ref})


def _cmd_click(args: argparse.Namespace) -> Any:
    return _remote(args, "click", "click", {"target": args.target, "double": bool(args.double), "right": bool(args.right),
                                            "accept_dialog": bool(args.accept_dialog), "dialog_text": args.dialog_text,
                                            "discard_changes": bool(args.discard_changes)})


def _cmd_fill(args: argparse.Namespace) -> Any:
    return _remote(args, "fill", "fill", {"target": args.target, "text": _input_text(args, "text"), "enter": bool(args.enter)})


def _cmd_type(args: argparse.Namespace) -> Any:
    return _remote(args, "type", "type", {"target": args.target, "text": _input_text(args, "text"), "delay": args.delay, "append": bool(args.append), "enter": bool(args.enter)})


def _cmd_press(args: argparse.Namespace) -> Any:
    return _remote(args, "press", "press", {"key": args.key, "target": args.target})


def _cmd_select(args: argparse.Namespace) -> Any:
    return _remote(args, "select", "select", {"target": args.target, "values": args.values})


def _cmd_check(args: argparse.Namespace) -> Any:
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
        path = str(core_common._check_local_path(args.path, paths, core_common._load_config(), must_exist=False))
    return _remote(args, "screenshot", "screenshot", {"target": args.target, "path": path, "full_page": bool(args.full_page)})


def _cmd_wait(args: argparse.Namespace) -> Any:
    timeout = args.timeout if args.timeout is not None else core_common.WAIT_DEFAULT
    if timeout > core_common.WAIT_CAP:
        raise core_common._tag(ValueError(f"--timeout for wait is capped at {core_common.WAIT_CAP:g} seconds."), core_common.ERR_VALIDATION, hint=core_common._hint("wait_cap"))
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
    return _remote(args, "tab", "tab", {"index": args.index, "close": bool(args.close), "discard_changes": bool(args.discard_changes)})


def _cmd_upload(args: argparse.Namespace) -> Any:
    profile = core_session._resolve_profile(args.profile)
    paths = core_common._paths(profile)
    config = core_common._load_config()
    files = [str(core_common._check_local_path(f, paths, config)) for f in args.files]
    return _remote(args, "upload", "upload", {"target": args.target, "files": files})


def _cmd_downloads(args: argparse.Namespace) -> Any:
    return _remote(args, "downloads", "downloads", {}, 60.0)


def _cmd_eval(args: argparse.Namespace) -> Any:
    return _remote(args, "eval", "eval", {"js": _input_text(args, "js"), "target": args.ref, "frame": args.frame})


def _input_text(args: argparse.Namespace, name: str) -> str:
    value, filename = getattr(args, name, None), getattr(args, name + "_file", None)
    if (value is None) == (filename is None):
        raise core_common._tag(ValueError(f"Provide exactly one of {name} or --{name}-file."), core_common.ERR_USAGE)
    if filename is None:
        return value
    path = core_common._check_local_path(filename, core_common._paths(core_session._resolve_profile(args.profile)), core_common._load_config())
    if path.stat().st_size > core_common.REQUEST_CAP // 2:
        raise core_common._tag(ValueError("Input file exceeds the command payload budget."), core_common.ERR_VALIDATION)
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise core_common._tag(ValueError("Input file must be readable UTF-8 text."), core_common.ERR_VALIDATION) from exc


def _cmd_output(args: argparse.Namespace) -> dict[str, Any]:
    paths = core_common._paths(core_session._resolve_profile(args.profile))
    if args.offset < 0:
        raise core_common._tag(ValueError("--offset must be >= 0."), core_common.ERR_VALIDATION)
    if args.id and not re.fullmatch(r"[a-f0-9]{32}", args.id):
        raise core_common._tag(ValueError("Invalid output ID."), core_common.ERR_VALIDATION)
    path = paths.outputs / (args.id + ".json") if args.id else paths.dir / "last_output.json"
    try:
        data = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise core_common._tag(ValueError("Output not found."), core_common.ERR_NOT_FOUND) from exc
    chunk = data[args.offset:args.offset + 3000]
    end = args.offset + len(chunk)
    return {"offset": args.offset, "next_offset": end if end < len(data) else None,
            "total_chars": len(data), "chunk": chunk}


def _json_line(payload: Any, profile: str | None = None) -> str:
    result = dict(payload) if isinstance(payload, dict) else {"result": payload}
    result["contract_version"] = core_common.CONTRACT_VERSION
    encoded = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) + 1 <= core_common.STDOUT_BUDGET:
        return encoded
    paths = core_common._paths(profile or result.get("profile") or core_session._resolve_profile(None))
    paths.outputs.mkdir(parents=True, exist_ok=True)
    output_id = secrets.token_hex(16)
    output_file = paths.outputs / (output_id + ".json")
    output_file.write_text(encoded, encoding="utf-8")
    core_session._write_json_atomic(paths.dir / "last_output.json", result)
    bounded = {k: result[k] for k in ("ok", "action", "effect", "observed", "command_id") if k in result}
    bounded.update(contract_version=core_common.CONTRACT_VERSION, truncated=True, output_id=output_id,
                   output_file=str(output_file), next_offset=0)
    if "error" in result:
        bounded["error"] = {"class": result["error"].get("class"), "http_status": None,
                            "message": "Error details exceeded the reply budget; read output by ID.", "hint": None}
    return json.dumps(bounded, ensure_ascii=False, default=str, separators=(",", ":"))


def _cmd_focus(args: argparse.Namespace) -> Any:
    paths = core_common._paths(core_session._resolve_profile(args.profile))
    core_session._require_running(paths, "focus")
    return core_session.focus_window(paths)


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
  browser, 124 = still waiting (run the same command again), 130 = interrupted.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=core_common.TOOL_NAME, description="Drive a real, visible Edge/Chrome window; JSON in and out, refs from snapshots.",
                                     epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{core_common.TOOL_NAME} {core_common.TOOL_VERSION}")
    parser.add_argument("--profile", metavar="NAME", default=None, help="browser profile (cookies persist per profile; default: the running one, else 'default')")
    parser.add_argument("--timeout", type=float, metavar="SECONDS", default=None, help="per-action timeout (default 15; goto 30; wait 60, max 100)")
    parser.add_argument("--max-bytes", type=int, metavar="N", default=None, help="snapshot byte budget per response (default 16000)")
    parser.add_argument("--snapshot", action="store_true", default=False, help="return a full snapshot after an action instead of the compact change list")
    parser.add_argument("--verbose", action="store_true", default=False, help="diagnostics to stderr")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", metavar="NAME", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, metavar="SECONDS", default=argparse.SUPPRESS)
    common.add_argument("--max-bytes", type=int, metavar="N", default=argparse.SUPPRESS)
    common.add_argument("--snapshot", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)

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
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true", help="kill without asking the daemon")
    p = add("status", _cmd_status, "is the browser running; tabs; staleness (always exit 0)")
    p.add_argument("--all", action="store_true")
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
    p = add("wait", _cmd_wait, "wait until every given condition holds (max 100 s per call)")
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
    p = add("tab", _cmd_tab, "switch to (or close) a tab by index")
    p.add_argument("index", type=int)
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
    add("focus", _cmd_focus, "bring the browser window to the front (best effort)")
    p = add("doctor", _cmd_doctor, "preflight checks (always exit 0)")
    p.add_argument("--live", action="store_true", help="also boot a headless browser and click a ref")
    p.add_argument("--browser", choices=("msedge", "chrome"), default=None)
    p.add_argument("--exe")
    p = add("clean", _cmd_clean, "remove stale sessions, orphaned browsers, old screenshots/downloads")
    p.add_argument("--purge-profile", metavar="NAME", help="delete a profile folder (sign-in lost)")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("serve", help=argparse.SUPPRESS)
    p.add_argument("--launch-file", required=True)
    p.set_defaults(func=_cmd_serve)
    p = add("output", _cmd_output, "page a saved overflow result without repeating its command")
    p.add_argument("--id")
    p.add_argument("--offset", type=int, default=0)
    return parser


def _emit_error(exc: Exception) -> None:
    default_class = core_common.ERR_VALIDATION if isinstance(exc, ValueError) else core_common.ERR_DAEMON
    payload: dict[str, Any] = {"error": {"class": getattr(exc, "error_class", default_class), "http_status": None,
                                         "message": str(exc), "hint": getattr(exc, "hint", None)}}
    details = getattr(exc, "details", None)
    if details is not None:
        payload["error"]["details"] = details
    try:
        line = _json_line(payload)
    except OSError:
        line = json.dumps({"contract_version": core_common.CONTRACT_VERSION, "error": {"class": "output_storage", "message": "Cannot preserve oversized error output."}}, separators=(",", ":"))
    print(line, file=sys.stderr, flush=True)


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
    args = _build_parser().parse_args(_demangle_msys(raw))
    pass
    core_common.VERBOSE = bool(getattr(args, "verbose", False))
    _reconfigure_streams()
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        print(f"\n{core_common.TOOL_NAME}: interrupted", file=sys.stderr, flush=True)
        return core_common.EXIT_INTERRUPT
    except ValueError as exc:
        _emit_error(exc)
        return 2
    except RuntimeError as exc:
        _emit_error(exc)
        return core_common.EXIT_TIMEOUT if getattr(exc, "error_class", None) == core_common.ERR_TIMEOUT else 1
    if isinstance(result, bool) or not isinstance(result, int):
        try:
            print(_json_line(result, getattr(args, "profile", None)))
        except OSError as exc:
            _emit_error(core_common._tag(RuntimeError("Cannot preserve oversized output."), "output_storage"))
            return 1
        return 0
    return result
