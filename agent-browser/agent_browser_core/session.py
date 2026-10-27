from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import common as core_common, daemon as core_daemon


def _write_json_atomic(path: Path, data: dict[str, Any], *, compact: bool = False) -> None:
    """Atomic, owner-only write: the session file carries the daemon token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not core_common._is_windows():
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + "." + secrets.token_hex(8) + ".tmp")
    write_lock = None
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=not compact, indent=None if compact else 1,
                                    separators=(",", ":") if compact else None))
        deadline = core_common._now() + 2
        while write_lock is None:
            write_lock = core_common._try_lock(path.with_suffix(path.suffix + ".write.lock"))
            if write_lock is None:
                if core_common._now() >= deadline:
                    raise OSError("Timed out preserving the latest output pointer.")
                core_common._sleep(.02)
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if core_common._now() >= deadline:
                    raise
                core_common._sleep(.02)
    finally:
        core_common._release_lock(write_lock)
        tmp.unlink(missing_ok=True)


def _read_session(paths: core_common.ProfilePaths) -> dict[str, Any] | None:
    try:
        data = json.loads(paths.session.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("port") and data.get("token") else None


def _ping(session: dict[str, Any], timeout: float = core_common.PING_TIMEOUT) -> dict[str, Any] | None:
    try:
        return _request(session, "ping", {}, timeout=timeout, grace=0.0)
    except (RuntimeError, ValueError, OSError) as exc:
        if getattr(exc, "error_class", None) == "protocol_mismatch":
            raise
        return None


def _transport_state(paths: core_common.ProfilePaths) -> tuple[str, dict[str, Any] | None]:
    """Locate a recorded endpoint; the command itself establishes responsiveness."""
    held = core_common._lock_held(paths.lock) if paths.lock.exists() else False
    session = _read_session(paths)
    if held:
        if session is None:
            return "starting", None
        if session.get("protocol_version") != core_common.PROTOCOL_VERSION:
            return "incompatible", session
        return "running", session
    if session is not None:
        return "stale", session
    return "none", None


def _liveness(paths: core_common.ProfilePaths) -> tuple[str, dict[str, Any] | None]:
    """Health observation for status/doctor, separate from command dispatch."""
    state, session = _transport_state(paths)
    if state != "running":
        return state, session
    try:
        return ("running", session) if _ping(session) is not None else ("unresponsive", session)
    except RuntimeError as exc:
        if getattr(exc, "error_class", None) == "protocol_mismatch":
            return "incompatible", session
        raise


def _running_profiles() -> list[str]:
    root = core_common._home() / "profiles"
    if not root.is_dir():
        return []
    names = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and core_common.PROFILE_RE.match(entry.name) and entry.name not in core_common.RESERVED_PROFILES:
            lock = entry / "session.lock"
            if lock.exists() and core_common._lock_held(lock) and (entry / "session.json").is_file():
                names.append(entry.name)
    return names


def _resolve_profile(explicit: str | None, sticky: bool = True) -> str:
    if explicit:
        return core_common._validate_profile(explicit)
    env = os.getenv("AGENT_BROWSER_PROFILE")
    if env:
        return core_common._validate_profile(env)
    return core_common.DEFAULT_PROFILE


def _request(session: dict[str, Any], cmd: str, args: dict[str, Any], timeout: float, grace: float = core_common.CLIENT_GRACE) -> dict[str, Any]:
    payload = json.dumps({"token": session["token"], "cmd": cmd, "args": args, "timeout": timeout, "protocol_version": core_common.PROTOCOL_VERSION, "session_id": core_common.session_identity(args.get("_session_id"))}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", int(session["port"])), timeout=3.0) as sock:
            sock.settimeout(timeout + grace)
            sock.sendall(payload.encode("utf-8"))
            with sock.makefile("rb") as reader:
                line = reader.readline()
    except socket.timeout as exc:
        raise core_common._tag(RuntimeError(f"The daemon did not answer within {timeout + grace:g}s."), core_common.ERR_TIMEOUT, hint=core_common._hint("timeout_client", session.get("profile", core_common.DEFAULT_PROFILE))) from exc
    except OSError as exc:
        raise core_common._tag(RuntimeError(f"Cannot reach the daemon on port {session.get('port')}: {exc}"), core_common.ERR_NOT_RUNNING, hint=core_common._hint("not_running", session.get("profile", core_common.DEFAULT_PROFILE))) from exc
    if not line:
        raise core_common._tag(RuntimeError("The daemon closed the connection without answering."), core_common.ERR_DAEMON, hint=core_common._hint("daemon_unresponsive", session.get("profile", core_common.DEFAULT_PROFILE)))
    try:
        reply = json.loads(line.decode("utf-8"))
    except ValueError as exc:
        raise core_common._tag(RuntimeError("The daemon sent a malformed reply."), core_common.ERR_DAEMON) from exc
    if reply.get("protocol_version") != core_common.PROTOCOL_VERSION:
        raise core_common._tag(RuntimeError("Client and daemon protocols differ; restart explicitly."), "protocol_mismatch")
    if reply.get("ok"):
        return reply.get("result") or {}
    _raise_remote(reply.get("error") or {}, session.get("profile", core_common.DEFAULT_PROFILE))
    raise AssertionError("unreachable")


def _raise_remote(error: dict[str, Any], profile: str) -> None:
    cls = str(error.get("class") or core_common.ERR_DAEMON)
    message = str(error.get("message") or "daemon error")
    hint = error.get("hint")
    details = error.get("details")
    exc: Exception = ValueError(message) if cls in core_common.EXIT_2_CLASSES else RuntimeError(message)
    raise core_common._tag(exc, cls, hint=hint, details=details)


def _require_running(paths: core_common.ProfilePaths, verb: str) -> dict[str, Any]:
    state, session = _transport_state(paths)
    if state == "incompatible":
        raise _restart_required(paths)
    if state == "running" and session is not None:
        return session
    if state == "starting":
        deadline = core_common._now() + 10.0
        while core_common._now() < deadline:
            core_common._sleep(0.2)
            state, session = _transport_state(paths)
            if state == "running" and session is not None:
                return session
        raise core_common._tag(RuntimeError("The browser is still starting."), core_common.ERR_NOT_RUNNING, hint=core_common._hint("starting", paths.profile))
    if state == "unresponsive":
        raise core_common._tag(RuntimeError("The daemon holds the lock but does not answer."), core_common.ERR_DAEMON_UNRESPONSIVE, hint=core_common._hint("daemon_unresponsive", paths.profile))
    if state == "stale":
        paths.session.unlink(missing_ok=True)
    raise core_common._tag(RuntimeError(f"No browser is running for profile {paths.profile!r} ({verb} needs one)."), core_common.ERR_NOT_RUNNING, hint=core_common._hint("not_running", paths.profile))


def _restart_required(paths: core_common.ProfilePaths) -> RuntimeError:
    return core_common._tag(RuntimeError("Client and daemon protocols differ; an explicit profile restart is required."), "protocol_mismatch",
                            hint=core_common._hint("protocol_mismatch", paths.profile))


def _launch_record(paths: core_common.ProfilePaths, args: argparse.Namespace, token: str) -> dict[str, Any]:
    browser = (getattr(args, "browser", None) or os.getenv("AGENT_BROWSER_BROWSER") or "msedge").lower()
    if browser not in core_common.BROWSER_EXE:
        raise core_common._tag(ValueError(f"--browser must be msedge or chrome, not {browser!r}"), core_common.ERR_VALIDATION)
    exe = core_common._check_browser_exe(getattr(args, "exe", None) or os.getenv("AGENT_BROWSER_EXE") or None)
    headless = bool(getattr(args, "headless", False)) or core_common._env_flag("AGENT_BROWSER_HEADLESS")
    return {
        "profile": paths.profile, "home": str(paths.root), "log": str(paths.log), "token": token,
        "browser": browser, "exe": exe, "headless": headless,
        "window_size": getattr(args, "window_size", None) or core_common.DEFAULT_WINDOW_SIZE,
        "code_hash": core_common._code_hash(), "config": core_common._load_config(),
        "idle_seconds": core_common._env_float("AGENT_BROWSER_IDLE_SECONDS", 0.0),
        "tool_version": core_common.TOOL_VERSION,
    }


def start_session(paths: core_common.ProfilePaths, args: argparse.Namespace, timeout: float = core_common.START_TIMEOUT) -> dict[str, Any]:
    state, session = _transport_state(paths)
    if state == "incompatible":
        raise _restart_required(paths)
    if state == "running" and session is not None:
        pong = _ping(session, timeout=timeout)
        if pong is None:
            raise core_common._tag(RuntimeError("The existing daemon did not answer within the start deadline."), core_common.ERR_DAEMON_UNRESPONSIVE,
                                   hint=core_common._hint("daemon_unresponsive", paths.profile))
        return {"profile": paths.profile, "status": "already_running", "pid": session.get("pid"), "port": session.get("port"),
                "browser": session.get("browser"), "browser_version": session.get("browser_version"), "headless": session.get("headless"),
                "current_tab": pong.get("current_tab"), "url": pong.get("url"), "launcher": session.get("launcher"), "log": session.get("log")}
    if state == "unresponsive":
        raise core_common._tag(RuntimeError("A daemon holds the lock but does not answer."), core_common.ERR_DAEMON_UNRESPONSIVE, hint=core_common._hint("daemon_unresponsive", paths.profile))
    if state == "starting":
        session = _require_running(paths, "start")
        return start_session(paths, args, timeout)
    if state == "stale":
        paths.session.unlink(missing_ok=True)
        killed = core_common._kill_browsers_using(paths.user_data)
        if killed:
            core_common._log(f"killed stale processes {killed}")
    paths.dir.mkdir(parents=True, exist_ok=True)
    (paths.dir / "startup_error.json").unlink(missing_ok=True)
    token = secrets.token_hex(32)
    launch = _launch_record(paths, args, token)
    _write_json_atomic(paths.launch, launch)
    argv = [sys.executable, "-X", "utf8", str(core_common.ENTRY_PATH), "serve", "--launch-file", str(paths.launch)]
    pid, launcher = core_common._spawn_daemon(argv, paths.log, paths.dir)
    deadline = core_common._now() + timeout
    while core_common._now() < deadline:
        core_common._sleep(0.1)
        session = _read_session(paths)
        if session is not None and _ping(session) is not None:
            session["launcher"] = launcher
            _write_json_atomic(paths.session, session)
            return {"profile": paths.profile, "status": "started", "pid": session.get("pid"), "port": session.get("port"),
                    "browser": session.get("browser"), "browser_version": session.get("browser_version"), "headless": session.get("headless"),
                    "user_data_dir": str(paths.user_data), "launcher": launcher, "log": str(paths.log), "window": core_common._hint("window")}
        if not core_common._pid_alive(pid):
            tail = core_common._tail(paths.log)
            reported = core_daemon._take_startup_error(paths) or {}
            cls = str(reported.get("class") or "")
            # startup_error.json is authoritative; the log tail is the fallback for a daemon that died before writing it.
            if cls == core_common.ERR_PLAYWRIGHT_MISSING or "No module named 'playwright'" in tail or "Playwright is not installed" in tail:
                raise core_common._tag(RuntimeError("The daemon could not import Playwright."), core_common.ERR_PLAYWRIGHT_MISSING, hint=core_common._hint("playwright_missing"), details={"log": tail})
            if cls == core_common.ERR_PROFILE_IN_USE or (not cls and "already in use" in tail):
                raise core_common._tag(RuntimeError("The profile folder is already in use."), core_common.ERR_PROFILE_IN_USE, hint=core_common._hint("profile_in_use", paths.profile), details={"log": tail})
            if cls in (core_common.ERR_GUARDED, core_common.ERR_BROWSER_NOT_FOUND, core_common.ERR_CONFIG):
                exc: Exception = ValueError(reported.get("message") or "The daemon refused to start.") if cls == core_common.ERR_GUARDED else RuntimeError(reported.get("message") or "The daemon could not start.")
                raise core_common._tag(exc, cls, hint=reported.get("hint"), details={"log": tail})
            blockers = core_common.policy_blockers(core_common.read_policies(launch["browser"]))
            if blockers:
                raise core_common._tag(RuntimeError("Browser policy blocks automation: " + "; ".join(blockers)), core_common.ERR_POLICY, details={"log": tail})
            message = reported.get("message") or f"The daemon exited before the browser was ready (log: {paths.log})."
            raise core_common._tag(RuntimeError(message), core_common.ERR_LAUNCH, hint=core_common._hint("launch", paths.profile), details={"log": tail})
    core_common._kill_tree(pid)
    raise core_common._tag(RuntimeError(f"The browser did not become ready within {timeout:g}s (log: {paths.log})."), core_common.ERR_LAUNCH, hint=core_common._hint("launch", paths.profile), details={"log": core_common._tail(paths.log)})


def stop_session(paths: core_common.ProfilePaths, force: bool = False) -> dict[str, Any]:
    state, session = _liveness(paths)
    if state == "incompatible" and not force:
        raise _restart_required(paths)
    result: dict[str, Any] = {"profile": paths.profile, "stopped": False, "killed_pids": []}
    pid = int(session.get("pid") or 0) if session else 0
    if state == "running" and session is not None and not force:
        try:
            _request(session, "stop", {"all": True}, timeout=core_common.STOP_GRACE)
        except (RuntimeError, ValueError) as exc:
            core_common._log(f"stop request failed: {exc}")
        deadline = core_common._now() + core_common.STOP_GRACE
        while core_common._now() < deadline and core_common._pid_alive(pid):
            core_common._sleep(0.2)
        result["stopped"] = True
    # The recorded pid is only trusted while the daemon holds the lock (running/unresponsive/starting);
    # a stale record may name a pid that the OS has since given to an unrelated process.
    if pid and state in ("running", "incompatible", "unresponsive", "starting") and core_common._pid_alive(pid):
        core_common._kill_tree(pid)
        result["killed_pids"].append(pid)
        result["stopped"] = True
    if state in ("running", "incompatible", "unresponsive", "stale", "starting"):
        # Chromium's helper processes (--type=...) follow the main process out; give them a moment so a
        # `status`/`clean` right after `stop` does not see a browser that is still winding down.
        deadline = core_common._now() + 4.0
        while core_common._now() < deadline and any(p["name"].lower() in ("msedge.exe", "chrome.exe") for p in core_common._processes_using(str(paths.user_data))):
            core_common._sleep(0.3)
        result["killed_pids"].extend(k for k in core_common._kill_browsers_using(paths.user_data) if k not in result["killed_pids"])
        result["stopped"] = True
    paths.session.unlink(missing_ok=True)
    return result


def status_session(paths: core_common.ProfilePaths) -> dict[str, Any]:
    state, session = _liveness(paths)
    if state != "running" or session is None:
        reason = {"none": "no_session", "stale": "pid_dead", "unresponsive": "unresponsive", "starting": "starting", "incompatible": "protocol_mismatch"}[state]
        hint = _restart_required(paths).hint if state == "incompatible" else core_common._hint("daemon_unresponsive" if state == "unresponsive" else "not_running", paths.profile)
        return {"profile": paths.profile, "running": False, "reason": reason, "hint": hint}
    pong = _ping(session, timeout=1.0) or {}
    started = session.get("started")
    uptime = None
    if started:
        try:
            uptime = int((datetime.now(timezone.utc) - datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds())
        except ValueError:
            uptime = None
    out = {"profile": paths.profile, "running": True, "pid": session.get("pid"), "port": session.get("port"), "browser": session.get("browser"),
           "browser_version": session.get("browser_version"), "headless": session.get("headless"), "started": started, "uptime_s": uptime,
           "busy": session.get("busy"), "stale_code": session.get("code_hash") != core_common._code_hash(), "launcher": session.get("launcher"),
           "current_tab": pong.get("current_tab"), "url": pong.get("url"), "title": pong.get("title"), "tabs": pong.get("tabs", []),
           "log": session.get("log"), "untrusted": core_common.UNTRUSTED_KEYS}
    if uptime and uptime > core_common.SESSION_AGE_WARN_SECONDS:
        out["note"] = f"This browser has been open for {uptime // 3600} hours. Run: {core_common.TOOL_NAME} stop --profile {paths.profile} when the task is done."
    if out["stale_code"]:
        out["note"] = core_common._hint("stale_code", paths.profile)
    return out


def clean(profile: str | None = None, purge: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    root = core_common._home() / "profiles"
    out: dict[str, Any] = {"home": str(core_common._home()), "stale_sessions_removed": [], "killed_pids": [], "artifacts_removed": 0, "profiles_purged": [], "dry_run": dry_run}
    if not root.is_dir():
        return out
    cutoff = time.time() - core_common.ARTIFACT_RETENTION_DAYS * 86400
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not core_common._is_profile_name(entry.name):
            continue
        if profile and entry.name != profile and purge != entry.name:
            continue
        paths = core_common.ProfilePaths(entry.name, core_common._home())
        state, _session = _liveness(paths)
        if state == "stale":
            out["stale_sessions_removed"].append(entry.name)
            if not dry_run:
                paths.session.unlink(missing_ok=True)
        if state in ("stale", "none"):
            out["killed_pids"].extend(core_common._kill_browsers_using(paths.user_data, dry_run=dry_run))
        download_dirs = [paths.downloads]
        sessions_dir = paths.dir / "sessions"
        if sessions_dir.is_dir():
            download_dirs.extend(child / "downloads" for child in sorted(sessions_dir.iterdir()) if child.is_dir())
        for folder in download_dirs:
            if folder.is_dir():
                for item in folder.iterdir():
                    try:
                        if item.is_file() and item.stat().st_mtime < cutoff:
                            out["artifacts_removed"] += 1
                            if not dry_run:
                                item.unlink()
                    except OSError:
                        pass
        if purge == entry.name:
            if state in ("running", "incompatible", "starting", "unresponsive"):
                raise core_common._tag(ValueError(f"Profile {entry.name!r} is running; refusing to purge."), core_common.ERR_VALIDATION, hint=core_common._hint("purge_running", entry.name))
            out["profiles_purged"].append(entry.name)
            if not dry_run:
                resolved = paths.dir.resolve()
                intended = (core_common._home() / "profiles").resolve()
                if resolved.parent != intended or resolved.name != entry.name:
                    raise core_common._tag(ValueError("Unsafe profile purge path."), core_common.ERR_GUARDED)
                core_common._rmtree_retry(paths.dir)
    if purge and purge not in out["profiles_purged"]:
        raise core_common._tag(ValueError(f"No profile named {purge!r}."), core_common.ERR_NOT_FOUND, hint=core_common._hint("no_such_profile"))
    return out


def _check(checks: list[dict[str, Any]], cid: str, status: str, detail: str, hint: str | None = None) -> None:
    checks.append({"id": cid, "status": status, "detail": detail, "hint": hint})


def doctor(browser: str | None = None, exe: str | None = None, live: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    _check(checks, "python", "ok", f"{sys.version.split()[0]} at {sys.executable}")
    version = core_daemon._playwright_version()
    if version:
        line = ".".join(version.split(".")[:2])
        _check(checks, "playwright_import", "ok", f"playwright {version}")
        _check(checks, "playwright_version", "ok" if line in core_common.TESTED_PLAYWRIGHT else "warn", version,
               None if line in core_common.TESTED_PLAYWRIGHT else f"Tested with {', '.join(core_common.TESTED_PLAYWRIGHT)}; run doctor --live after an upgrade.")
    else:
        _check(checks, "playwright_import", "fail", f"playwright is not installed for {sys.executable}", core_common._hint("playwright_missing"))
    script = shutil.which(core_common.TOOL_NAME)
    all_scripts = []
    if core_common._is_windows():
        try:
            out = subprocess.run(["where.exe", core_common.TOOL_NAME], capture_output=True, text=True, timeout=10, check=False).stdout
            all_scripts = [l.strip() for l in out.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            all_scripts = []
    if script:
        dup = len({Path(p).parent.resolve() for p in all_scripts}) > 1
        _check(checks, "console_script", "warn" if dup else "ok", script + (f" (+{len(all_scripts) - 1} more on PATH)" if dup else ""),
               "Two agent-browser executables are on PATH (the Vercel npm CLI shares the name); check `where agent-browser`." if dup else None)
    else:
        _check(checks, "console_script", "warn", "agent-browser is not on PATH", f"Use `python {core_common.ENTRY_PATH.name} <verb>` or install: python -m pip install --user -e agent-browser")
    kind = (browser or os.getenv("AGENT_BROWSER_BROWSER") or "msedge").lower()
    try:
        found = core_common.find_browser(kind, exe or os.getenv("AGENT_BROWSER_EXE"))
        _check(checks, "browser_found", "ok", f"{kind}: {found}")
        _check(checks, "browser_version", "ok", core_common.browser_version_from_path(found) or "unknown")
    except (RuntimeError, ValueError) as exc:
        _check(checks, "browser_found", "fail", str(exc), getattr(exc, "hint", None))
    policies = core_common.read_policies(kind)
    if not policies:
        _check(checks, "policies", "ok", f"no {kind} policies configured")
    for name, info in policies.items():
        _check(checks, f"policy_{name}", info["status"], f"{name}={info['value']!r} ({info['hive']}): {info['label']}")
    try:
        home = core_common._home()
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _check(checks, "home_writable", "ok", str(home))
    except OSError as exc:
        _check(checks, "home_writable", "fail", str(exc), core_common._hint("profile_path"))
    length = len(str(core_common._home() / "profiles" / "default" / "user-data"))
    _check(checks, "profile_path_length", "ok" if length <= core_common.MAX_PROFILE_PATH_CHARS else "fail", f"{length} chars", None if length <= core_common.MAX_PROFILE_PATH_CHARS else core_common._hint("profile_path"))
    if core_common._is_windows():
        lp = core_common._registry_value(r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled")
        _check(checks, "long_paths_enabled", "ok" if lp in (1, "1") else "warn", str(lp), None if lp in (1, "1") else "Long paths are off; keep AGENT_BROWSER_HOME short.")
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            _check(checks, "port_bind", "ok", f"127.0.0.1:{sock.getsockname()[1]}")
    except OSError as exc:
        _check(checks, "port_bind", "fail", str(exc))
    job = core_common._job_info()
    # pip's console-script launcher puts python in a Job with KILL_ON_JOB_CLOSE, but also SILENT_BREAKAWAY_OK,
    # so a detached daemon leaves that Job on its own. Only a kill-on-close Job without any breakaway right is a problem.
    trapped = bool(job.get("kill_on_job_close")) and not any(f in (job.get("flags") or []) for f in ("BREAKAWAY_OK", "SILENT_BREAKAWAY_OK"))
    _check(checks, "job_object", "warn" if trapped else "ok", json.dumps(job),
           "This shell's Job kills children on close and forbids breakaway; set AGENT_BROWSER_LAUNCHER=wmi." if trapped else None)
    config = core_common._home() / "config.json"
    if config.is_file():
        try:
            from . import policy as core_policy
            loaded = core_common._load_config()
            core_policy.profile_config(loaded, core_common.DEFAULT_PROFILE)
            if isinstance(loaded.get("profiles"), dict):
                for prof_name in loaded["profiles"]:
                    core_policy.profile_config(loaded, prof_name)
            _check(checks, "config_json", "ok", json.dumps(loaded))
        except RuntimeError as exc:
            _check(checks, "config_json", "fail", str(exc))
    else:
        _check(checks, "config_json", "ok", "absent (eval on, no host allowlist, uploads from cwd/downloads/shots)")
    skill = Path.home() / ".claude" / "skills" / core_common.TOOL_NAME / "SKILL.md"
    repo_skill = core_common.ENTRY_PATH.resolve().parents[1] / "agentic-ide-setup" / "profile" / "claude" / "skills" / core_common.TOOL_NAME / "SKILL.md"
    if skill.is_file():
        installed = hashlib.sha256(skill.read_bytes()).hexdigest()[:12]
        if repo_skill.is_file():
            source = hashlib.sha256(repo_skill.read_bytes()).hexdigest()[:12]
            _check(checks, "skill_installed", "ok" if installed == source else "warn", f"{skill} sha {installed}",
                   None if installed == source else "Installed skill differs from the repo copy; rerun agentic-ide-setup\\scripts\\Install-AgenticIdeSetup.ps1 -Components Claude -Apply -Overwrite.")
        else:
            _check(checks, "skill_installed", "ok", f"{skill} sha {installed}")
    else:
        _check(checks, "skill_installed", "warn", f"{skill} missing", "Install the profile: agentic-ide-setup\\scripts\\Install-AgenticIdeSetup.ps1 -Components Claude -Apply")
    running = _running_profiles()
    _check(checks, "sessions", "ok", ", ".join(running) if running else "none running")
    if live:
        _live_probe(checks)
    failed = [c["id"] for c in checks if c["status"] == "fail"]
    return {"ok": not failed, "failed": failed, "checks": checks}


def _live_probe(checks: list[dict[str, Any]]) -> None:
    paths = core_common.ProfilePaths("_doctor", core_common._home())
    ns = argparse.Namespace(browser=None, exe=None, headless=True, window_size=None)
    try:
        stop_session(paths, force=True)
        started = start_session(paths, ns, timeout=core_common.START_TIMEOUT)
        _check(checks, "live_boot", "ok", f"{started.get('browser_version')} via {started.get('launcher')}")
        session = _require_running(paths, "doctor")
        env = _request(session, "goto", {"url": 'data:text/html,<title>doctor</title><button id="b">ok</button><iframe name="gsft_main" srcdoc="<button>inner</button>"></iframe>'}, timeout=core_common.GOTO_TIMEOUT)
        lines = env.get("snapshot") or []
        has_main = any("[ref=e" in l for l in lines)
        has_frame = any(re.search(r"\[ref=f\d+e", l) for l in lines)
        _check(checks, "live_snapshot", "ok" if has_main and has_frame else "fail", f"{len(lines)} lines; main refs {has_main}; frame refs {has_frame}",
               None if has_main and has_frame else "Playwright's ai snapshot did not produce refs; check the Playwright version.")
        ref = next((re.search(r"\[ref=(e\d+)\]", l).group(1) for l in lines if 'button "ok"' in l), None)  # type: ignore[union-attr]
        if ref:
            _request(session, "click", {"target": ref}, timeout=core_common.DEFAULT_TIMEOUT)
            _check(checks, "live_click", "ok", f"clicked {ref}")
        pid = int(session.get("pid") or 0)
        stop_session(paths)
        alive = core_common._pid_alive(pid) if pid else False
        leftovers = [p for p in core_common._processes_using(str(paths.user_data)) if p["name"].lower() in core_common.BROWSER_PROCESS_NAMES and p["pid"] != os.getpid()]
        _check(checks, "live_teardown", "ok" if not alive and not leftovers else "warn",
               "daemon and browser exited" if not alive and not leftovers else f"daemon alive={alive}, leftovers={[p['pid'] for p in leftovers]}",
               None if not alive and not leftovers else f"Run: {core_common.TOOL_NAME} clean --profile _doctor")
    except (RuntimeError, ValueError) as exc:
        booted = any(c["id"] == "live_boot" for c in checks)
        _check(checks, "live_probe" if booted else "live_boot", "fail", str(exc), getattr(exc, "hint", None))
        try:
            stop_session(paths, force=True)
        except (RuntimeError, ValueError):
            pass


def focus_window(paths: core_common.ProfilePaths) -> dict[str, Any]:
    if not core_common._is_windows():
        return {"profile": paths.profile, "focused": False, "reason": "windows only"}
    pids = {p["pid"] for p in core_common._processes_using(str(paths.user_data)) if p["name"].lower() in ("msedge.exe", "chrome.exe")}
    if not pids:
        return {"profile": paths.profile, "focused": False, "reason": "no browser process"}
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum(hwnd: int, _lparam: int) -> bool:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindow(hwnd, 4) == 0:
                found.append(hwnd)
            return True

        user32.EnumWindows(enum, 0)
        if not found:
            return {"profile": paths.profile, "focused": False, "reason": "no visible window"}
        user32.ShowWindow(found[0], 9)  # SW_RESTORE
        ok = bool(user32.SetForegroundWindow(found[0]))
        flashed = bool(user32.FlashWindow(found[0], True))
        return {"profile": paths.profile, "focused": ok, "flashed": flashed, "windows": len(found)}
    except Exception as exc:  # noqa: BLE001
        return {"profile": paths.profile, "focused": False, "reason": core_common.sanitize_text(str(exc), 200)}
