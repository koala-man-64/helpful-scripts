#!/usr/bin/env python3
"""agent-browser - drive a real, visible Microsoft Edge (or Chrome) window from a shell.

Built for small AI models working through a Bash tool: one command per step, JSON on stdout,
structured errors on stderr, and elements addressed by refs (`e12`, `f2e5`) taken from the
newest snapshot. A per-profile background daemon owns a Playwright persistent context, so the
browser window, its cookies (sign-ins), and the element refs survive between commands.

Requirements:
  pip install "playwright>=1.61,<2"      (Edge or Chrome already installed; no `playwright install`)
  pip install pytest                      (tests only)

Commands (run `agent-browser --help` for full detail):
  start stop status goto snapshot click fill type press select check uncheck hover scroll
  text value screenshot wait back reload tabs tab upload downloads eval focus doctor clean

Environment (real env vars, read lazily - no .env loading):
  AGENT_BROWSER_HOME             state root (default %LOCALAPPDATA%/agent-browser, POSIX ~/.agent-browser)
  AGENT_BROWSER_PROFILE          default --profile
  AGENT_BROWSER_BROWSER          msedge | chrome (default msedge)
  AGENT_BROWSER_EXE              explicit browser executable
  AGENT_BROWSER_HEADLESS         "true" = headless by default (tests; a human cannot sign in headless)
  AGENT_BROWSER_TIMEOUT_SECONDS  default --timeout for actions
  AGENT_BROWSER_MAX_BYTES        snapshot byte budget per response (default 16000)
  AGENT_BROWSER_MAX_CHARS        default --max-chars for `text` (default 20000)
  AGENT_BROWSER_AUTOSTART        "false" = `goto` no longer starts a browser by itself
  AGENT_BROWSER_LAUNCHER         popen (default) | wmi   - how the daemon is detached on Windows
  AGENT_BROWSER_IDLE_SECONDS     daemon stops after this much inactivity (default 0 = never)
  AGENT_BROWSER_LIVE             "1" enables the live pytest suite

Human-written policy file (never created by the tool): <HOME>/config.json with any of
  {"eval": false, "allowed_hosts": ["dev12345.service-now.com"], "upload_roots": ["C:/exports"]}

Error convention (repo-wide): ValueError = bad input, fix the call (exit 2);
RuntimeError = environment or remote failure, fix config or retry (exit 1); 124 = timeout;
130 = Ctrl-C. Failures print {"error": {"class", "http_status", "message", "hint"}} on stderr.

Copied and adapted from edge-pyodide/edge_pyodide.py in this repository (same author): the
_tag/error contract, browser discovery, registry policy decoding, process helpers, doctor
shape, and the CLI skeleton. Nothing is imported across folders on purpose.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# Constants

TOOL_NAME = "agent-browser"
TOOL_VERSION = "0.1.0"
TESTED_PLAYWRIGHT = ("1.61",)          # aria_snapshot(mode="ai") + aria-ref engine verified on these lines
SESSION_SCHEMA = 1
DEFAULT_PROFILE = "default"
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
RESERVED_PROFILES = frozenset({"_doctor", "_live"})
REF_RE = re.compile(r"^(f\d+)?e\d+$")
MAX_PROFILE_PATH_CHARS = 180           # Chromium silently falls back to the default profile past MAX_PATH

DEFAULT_TIMEOUT = 15.0
GOTO_TIMEOUT = 30.0
START_TIMEOUT = 45.0
STOP_GRACE = 10.0
WAIT_DEFAULT = 60.0
WAIT_CAP = 100.0                       # the Claude Code Bash tool kills a call at 120 s by default
SETTLE_CAP = 8.0
DEFAULT_MAX_BYTES = 16000
STDOUT_BUDGET = 24000                  # Bash tool output is cut at 30,000 chars
DEFAULT_TEXT_CHARS = 20000
CHANGES_CAP = 40
REQUEST_CAP = 64 * 1024
CLIENT_GRACE = SETTLE_CAP + 7.0        # the daemon settles frames and snapshots after the action itself finishes
PING_TIMEOUT = 3.0
FIRST_READ_TIMEOUT = 5.0
HEARTBEAT_SECONDS = 5.0
SESSION_AGE_WARN_SECONDS = 8 * 3600
ARTIFACT_RETENTION_DAYS = 7
DEFAULT_WINDOW_SIZE = "1280,900"
LINE_CHAR_CAP = 300

ACTIONABLE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider",
    "spinbutton", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem", "iframe",
})
CONTEXT_ROLES = frozenset({"heading", "dialog", "alertdialog", "alert", "status", "tablist", "table", "listbox", "menu"})
NAMED_CONTEXT_ROLES = frozenset({"region", "form", "navigation", "main", "group"})
OPTION_PARENTS = frozenset({"listbox", "menu", "menubar", "tree"})
KEEP_ATTRS = ("active", "checked", "disabled", "expanded", "selected", "pressed", "readonly", "required", "invalid")

IDP_HOSTS = (
    "login.microsoftonline.com", "login.live.com", "login.windows.net", "login.microsoft.com",
    "accounts.google.com", "okta.com", "auth0.com", "duosecurity.com", "pingidentity.com",
    "onelogin.com", "adfs.",
)
SIGNIN_PATH_RE = re.compile(r"/(login|logout|auth_redirect|login_with_sso|saml_redirector|sso_login|signin|sign-in|oauth2|authorize|mfa)\b", re.I)
LOGIN_PAGE_RE = re.compile(r"login|signin|sign-in|sso|mfa|auth|verify", re.I)
SECRET_STRONG_RE = re.compile(r"pass(word|code|phrase)?\b|pwd|\botp\b|\bmfa\b|totp|secret|token|\bssn\b|\bcvv\b|card ?number|(verification|security|auth(entication)?) ?code", re.I)
SECRET_WEAK_RE = re.compile(r"\bcode\b|\bpin\b", re.I)
SECRET_AUTOCOMPLETE_RE = re.compile(r"password|one-time-code|cc-", re.I)
SAFE_KEYS_ON_SECRET = frozenset({"Enter", "Tab", "Escape"})
REDACT_QUERY_KEYS = frozenset({
    "code", "token", "id_token", "access_token", "refresh_token", "samlresponse", "session", "sid",
    "key", "secret", "password", "otp", "state", "nonce", "client_secret", "sysparm_ck",
    "sig", "signature", "x-amz-signature", "x-amz-credential", "x-goog-signature", "apikey", "api_key",
    "api-key", "jwt", "assertion", "ticket", "auth", "authorization", "sv", "se", "sp", "sr", "skoid",
})
ALLOWED_SCHEMES = frozenset({"http", "https"})
KEY_EXTENSIONS = frozenset({".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".jks"})
UNTRUSTED_KEYS = ["snapshot", "text", "title", "url", "details", "changes", "frames", "tabs", "dialogs", "target_line", "value_after", "value"]
OFF_ALLOWLIST_OK = frozenset({"ping", "stop", "back", "goto", "snapshot", "text", "tabs", "tab"})
BROWSER_BASENAMES = frozenset({
    "msedge.exe", "chrome.exe", "msedge", "chrome", "microsoft-edge", "microsoft-edge-stable",
    "google-chrome", "google-chrome-stable", "microsoft edge", "google chrome", "chromium", "chromium-browser",
})
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
LINE_RE = re.compile(
    r'^(?P<indent>\s*)- (?P<role>[A-Za-z/]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?: \[[^\]]*\])*)(?P<colon>:)?(?: (?P<value>.*))?$'
)

EDGE_CANDIDATES = (
    ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
    ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles(x86)", r"Microsoft\Edge Beta\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge Beta\Application\msedge.exe"),
)
CHROME_CANDIDATES = (
    ("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
    ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
)
POSIX_CANDIDATES = {
    "msedge": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable", "/opt/microsoft/msedge/msedge"),
    "chrome": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"),
}
BROWSER_EXE = {"msedge": "msedge.exe", "chrome": "chrome.exe"}
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
POLICY_KEYS = {"msedge": r"SOFTWARE\Policies\Microsoft\Edge", "chrome": r"SOFTWARE\Policies\Google\Chrome"}
VERSION_DIR_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
BROWSER_PROCESS_NAMES = frozenset({"msedge.exe", "chrome.exe", "node.exe", "python.exe", "pythonw.exe"})

# Error classes carried on exceptions via _tag.
ERR_USAGE = "usage"
ERR_VALIDATION = "validation"
ERR_AMBIGUOUS_PROFILE = "ambiguous_profile"
ERR_REF_NOT_FOUND = "ref_not_found"
ERR_STALE_REF = "stale_ref"
ERR_NOT_FOUND = "not_found"
ERR_AMBIGUOUS = "ambiguous"
ERR_ACTION_TIMEOUT = "action_timeout"
ERR_ACTION_FAILED = "action_failed"
ERR_GUARDED = "guarded"
ERR_DIALOG = "dialog"
ERR_UNSAVED = "unsaved_changes"
ERR_CONFIG = "config"
ERR_PLAYWRIGHT_MISSING = "playwright_missing"
ERR_BROWSER_NOT_FOUND = "browser_not_found"
ERR_POLICY = "edge_policy"
ERR_LAUNCH = "browser_launch"
ERR_PROFILE_IN_USE = "profile_in_use"
ERR_NOT_RUNNING = "not_running"
ERR_BROWSER_CLOSED = "browser_closed"
ERR_DAEMON = "daemon"
ERR_DAEMON_UNRESPONSIVE = "daemon_unresponsive"
ERR_TIMEOUT = "timeout"
EXIT_2_CLASSES = frozenset({
    ERR_USAGE, ERR_VALIDATION, ERR_AMBIGUOUS_PROFILE, ERR_REF_NOT_FOUND, ERR_STALE_REF, ERR_NOT_FOUND,
    ERR_AMBIGUOUS, ERR_ACTION_TIMEOUT, ERR_ACTION_FAILED, ERR_GUARDED, ERR_DIALOG, ERR_UNSAVED,
})
EXIT_TIMEOUT = 124
EXIT_INTERRUPT = 130

# Every hint is a static template: page-controlled text never reaches a hint.
HINTS = {
    "not_running": "Run: {tool} start --profile {profile}",
    "starting": "The browser is still starting. Run: {tool} status --profile {profile}",
    "stale_ref": "The page changed since the last snapshot. Run: {tool} snapshot --profile {profile}",
    "ref_not_found": "That ref is not on the current page. Run: {tool} snapshot --profile {profile}",
    "bad_ref": "Refs look like e12 or f2e5 and come from: {tool} snapshot --profile {profile}",
    "ambiguous_profile": "Several browsers are running. Add --profile NAME (see: {tool} status --all)",
    "guarded_secret": "This field takes a password or code. Ask the user to type it in the browser window, then run: {tool} wait --signed-in <app host> --profile {profile}",
    "guarded_key": "Only Enter, Tab, and Escape may be pressed on a password or code field.",
    "guarded_scheme": "Only http:// and https:// URLs can be opened.",
    "guarded_host": "That host is not in allowed_hosts (config.json). Ask the user before continuing.",
    "guarded_path": "The file must be under the current folder or the profile's downloads/shots folders.",
    "off_allowlist": "The page left the allowed hosts. Use: {tool} back --profile {profile}, or ask the user.",
    "dialog": "A browser dialog was dismissed. Re-run the same command with --accept-dialog to accept it.",
    "unsaved_changes": "The page has unsaved edits. Click its Save/Update button first, or re-run this command with --discard-changes.",
    "action_timeout": "The element is hidden, disabled, or covered. Run: {tool} snapshot --profile {profile} and pick a visible enabled element, or: {tool} wait --ref <ref> --profile {profile}",
    "action_failed": "Run: {tool} snapshot --profile {profile} and try a different element or verb.",
    "wait_timeout": "Still waiting. Run the same wait command again, or ask the user whether sign-in is finished.",
    "wait_cap": "wait runs at most 100 seconds per call. Use --timeout 100 and run the same wait again if needed.",
    "no_wait_condition": "Give at least one condition: --url-contains, --text, --ref, --selector, --signed-in, or --seconds.",
    "daemon_unresponsive": "Run: {tool} stop --force --profile {profile}",
    "profile_in_use": "Another browser holds this profile. Run: {tool} clean --profile {profile}",
    "playwright_missing": "Install it for the interpreter that runs {tool}: python -m pip install --user \"playwright>=1.61,<2\"",
    "browser_not_found": "Set AGENT_BROWSER_EXE or pass --exe pointing at msedge.exe or chrome.exe.",
    "stale_code": "agent_browser.py changed since the daemon started; run: {tool} stop --profile {profile} then {tool} start --profile {profile}",
    "select_not_found": "None of the options match. See details.options, then run select again with one of them.",
    "select_not_select": "This element is not a drop-down. Use: {tool} click <ref> then {tool} snapshot --profile {profile}",
    "check_not_checkbox": "This element is not a checkbox. Use: {tool} click <ref>",
    "fill_not_input": "This element is not a text field. Use: {tool} click <ref> then {tool} snapshot --profile {profile}",
    "eval_disabled": "eval is disabled by config.json on this machine.",
    "browser_closed": "The browser window was closed. Run: {tool} start --profile {profile} (sign-in is kept)",
    "timeout_client": "The browser did not answer in time. Run: {tool} status --profile {profile}",
    "last_tab": "This is the last tab. Use: {tool} stop --profile {profile} to close the browser.",
    "tab_index": "Run: {tool} tabs --profile {profile} and use one of the listed indexes.",
    "upload_target": "Give a file input or the button that opens the file chooser.",
    "sign_in": "This looks like a sign-in page. Do not fill anything. Tell the user: \"Please sign in in the Edge window (it may be behind this window)\", then run: {tool} wait --signed-in <app host> --profile {profile}",
    "frames_loading": "Some frames were still loading. Run: {tool} wait --text <expected label> --profile {profile}",
    "truncated": "Output was cut at the byte budget. Use: {tool} snapshot --find TEXT --profile {profile}, or read snapshot_file.",
    "window": "The window opened; it may be behind other windows. Look for the Edge icon on the taskbar.",
    "launch": "Read the daemon log named in the message, then: {tool} doctor",
    "profile_path": "Set AGENT_BROWSER_HOME to a short path inside your profile, such as %LOCALAPPDATA%\\ab.",
    "bad_exe": "--exe must point at msedge.exe or chrome.exe (or leave it unset to use the installed browser).",
    "guarded_focus": "The focused field takes a password or code. Ask the user to type it in the browser window.",
    "no_such_profile": "Run: {tool} status --all",
    "purge_running": "Run: {tool} stop --profile {profile} first.",
}

VERBOSE = False
_ARGV_NOTES: list[str] = []


def _log(message: str) -> None:
    if VERBOSE:
        print(f"{TOOL_NAME}: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Config helpers


def _tag(exc: Exception, error_class: str, hint: str | None = None, details: Any = None) -> Exception:
    """Ride error_class / hint / details on a builtin exception (no custom exception classes)."""
    exc.error_class = error_class  # type: ignore[attr-defined]
    exc.hint = hint  # type: ignore[attr-defined]
    if details is not None:
        exc.details = details  # type: ignore[attr-defined]
    return exc


def _hint(key: str, profile: str = DEFAULT_PROFILE) -> str:
    return HINTS[key].format(tool=TOOL_NAME, profile=profile)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise _tag(RuntimeError(f"{name} must be a number, not {value!r}"), ERR_CONFIG) from exc


def _is_windows() -> bool:
    return sys.platform == "win32"


_now = time.monotonic
_sleep = time.sleep


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _home() -> Path:
    explicit = os.getenv("AGENT_BROWSER_HOME")
    if explicit:
        return Path(explicit).expanduser()
    if _is_windows():
        base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / TOOL_NAME
    return Path.home() / f".{TOOL_NAME}"


def _is_profile_name(name: str) -> bool:
    return bool(PROFILE_RE.match(name or "")) or name in RESERVED_PROFILES


def _validate_profile(name: str) -> str:
    if not _is_profile_name(name):
        raise _tag(ValueError(f"Profile name {name!r} must match {PROFILE_RE.pattern}"), ERR_VALIDATION)
    return name


@dataclass(frozen=True)
class ProfilePaths:
    profile: str
    root: Path

    @property
    def dir(self) -> Path:
        return self.root / "profiles" / self.profile

    @property
    def user_data(self) -> Path:
        return self.dir / "user-data"

    @property
    def session(self) -> Path:
        return self.dir / "session.json"

    @property
    def lock(self) -> Path:
        return self.dir / "session.lock"

    @property
    def launch(self) -> Path:
        return self.dir / "launch.json"

    @property
    def log(self) -> Path:
        return self.dir / "daemon.log"

    @property
    def last_snapshot(self) -> Path:
        return self.dir / "last_snapshot.txt"

    @property
    def downloads(self) -> Path:
        return self.dir / "downloads"

    @property
    def shots(self) -> Path:
        return self.dir / "shots"


def _paths(profile: str) -> ProfilePaths:
    paths = ProfilePaths(_validate_profile(profile), _home())
    if len(str(paths.user_data)) > MAX_PROFILE_PATH_CHARS:
        raise _tag(
            RuntimeError(f"Profile path is too long for Chromium ({len(str(paths.user_data))} chars): {paths.user_data}"),
            ERR_CONFIG, hint=_hint("profile_path"),
        )
    return paths


def _load_config() -> dict[str, Any]:
    """Human-written policy file. Never created or modified by the tool."""
    path = _home() / "config.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _tag(RuntimeError(f"config.json is not valid JSON: {exc}"), ERR_CONFIG) from exc
    if not isinstance(data, dict):
        raise _tag(RuntimeError("config.json must contain a JSON object."), ERR_CONFIG)
    out: dict[str, Any] = {}
    if "eval" in data:
        if not isinstance(data["eval"], bool):
            raise _tag(RuntimeError(f"config.json eval must be the JSON boolean true or false, not {data['eval']!r}"), ERR_CONFIG)
        out["eval"] = data["eval"]
    for key in ("allowed_hosts", "upload_roots"):
        value = data.get(key)
        if value is not None:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise _tag(RuntimeError(f"config.json {key} must be a list of strings."), ERR_CONFIG)
            out[key] = [v.strip().lower() if key == "allowed_hosts" else v for v in value if v.strip()]
    return out


def _code_hash() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    except OSError:
        return "unknown"


# ---------------------------------------------------------------------------
# Text hygiene (page-derived strings never reach hints; they are sanitized and marked untrusted)


def sanitize_text(text: Any, cap: int = LINE_CHAR_CAP) -> str:
    cleaned = CONTROL_CHARS_RE.sub("", str(text))
    if cap and len(cleaned) > cap:
        cleaned = cleaned[:cap] + "..."
    return cleaned


def redact_url(url: Any) -> str:
    """Drop the fragment and mask bearer-like query values before a URL is printed or logged."""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return sanitize_text(url, 500)
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop user:pass@ userinfo
    if not parts.query:
        return sanitize_text(urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")), 500)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    masked = [(k, "***" if k.lower() in REDACT_QUERY_KEYS else v) for k, v in pairs]
    query = urllib.parse.urlencode(masked, safe="/:*")
    return sanitize_text(urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, "")), 500)


def _is_idp_host(host: str) -> bool:
    host = (host or "").lower()
    for idp in IDP_HOSTS:
        if idp.endswith("."):
            if host.startswith(idp) or ("." + idp) in host:
                return True
        elif host == idp or host.endswith("." + idp):
            return True
    return False


def _check_browser_exe(exe: str | os.PathLike[str] | None) -> str | None:
    """Only a real browser binary may be launched; the launch file is trusted no more than the flag."""
    if not exe:
        return None
    path = Path(exe)
    if not path.is_file():
        raise _tag(RuntimeError(f"Browser executable not found: {path}"), ERR_BROWSER_NOT_FOUND, hint=_hint("browser_not_found"))
    if path.name.lower() not in BROWSER_BASENAMES:
        raise _tag(ValueError(f"{path.name} is not a browser executable."), ERR_GUARDED, hint=_hint("bad_exe"))
    return str(path.resolve())


def safe_name(name: Any, fallback: str = "download") -> str:
    base = re.sub(r"[^\w.-]+", "_", Path(str(name or "")).name).strip("._")
    return (base or fallback)[:120]


def _unique_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


# ---------------------------------------------------------------------------
# Browser discovery and enterprise policies (from edge_pyodide.py)


def _registry_value(key_path: str, value_name: str | None, hive: str = "HKLM") -> Any:
    # Single winreg touchpoint (test seam). Returns None when the key/value is absent.
    if not _is_windows():
        return None
    import winreg  # noqa: PLC0415

    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, value_name if value_name is not None else "")
            return value
    except OSError:
        return None


def find_browser(kind: str = "msedge", explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate the browser executable: explicit > AGENT_BROWSER_EXE > PATH > known folders > App Paths."""
    if kind not in BROWSER_EXE:
        raise _tag(ValueError(f"--browser must be msedge or chrome, not {kind!r}"), ERR_VALIDATION)
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.getenv("AGENT_BROWSER_EXE")
    if env:
        candidates.append(Path(env))
    which = shutil.which(BROWSER_EXE[kind][:-4]) or shutil.which("microsoft-edge" if kind == "msedge" else "google-chrome")
    if which:
        candidates.append(Path(which))
    if _is_windows():
        for env_name, rel in (EDGE_CANDIDATES if kind == "msedge" else CHROME_CANDIDATES):
            base = os.getenv(env_name)
            if base:
                candidates.append(Path(base) / rel)
        app_path = _registry_value(APP_PATHS_KEY.format(exe=BROWSER_EXE[kind]), None)
        if app_path:
            candidates.append(Path(str(app_path)))
    else:
        candidates.extend(Path(p) for p in POSIX_CANDIDATES[kind])
    for path in candidates:
        if path.is_file():
            return path
    raise _tag(RuntimeError(f"{kind} was not found."), ERR_BROWSER_NOT_FOUND, hint=_hint("browser_not_found"))


def browser_version_from_path(exe: Path) -> str | None:
    try:
        versions = [p.name for p in exe.parent.iterdir() if p.is_dir() and VERSION_DIR_RE.match(p.name)]
    except OSError:
        return None
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


POLICY_LABELS: dict[str, Callable[[Any], tuple[str, str]]] = {
    "RemoteDebuggingAllowed": lambda v: ("fail", "remote debugging BLOCKED by policy") if v in (0, "0") else ("ok", "allowed"),
    "DeveloperToolsAvailability": lambda v: {
        0: ("ok", "blocked only for force-installed extensions (default)"),
        1: ("ok", "allowed"),
        2: ("fail", "DevTools DISALLOWED - Playwright cannot attach to pages"),
    }.get(int(v) if str(v).isdigit() else -1, ("warn", f"unknown value {v!r}")),
    "HeadlessModeEnabled": lambda v: ("warn", "headless mode blocked by policy (headed sessions still work)") if v in (0, "0") else ("ok", "allowed"),
    "UserDataDir": lambda v: ("warn", f"profile relocated by policy to {v!r}; persistent profiles may not apply"),
    "ForceEphemeralProfiles": lambda v: ("fail", "profiles are wiped on exit; sign-ins cannot persist") if v in (1, "1") else ("ok", "off"),
}


def read_policies(kind: str = "msedge") -> dict[str, dict[str, Any]]:
    report: dict[str, dict[str, Any]] = {}
    key_path = POLICY_KEYS.get(kind, POLICY_KEYS["msedge"])
    for name, decode in POLICY_LABELS.items():
        for hive in ("HKLM", "HKCU"):
            value = _registry_value(key_path, name, hive=hive)
            if value is not None:
                status, label = decode(value)
                report[name] = {"hive": hive, "value": value, "status": status, "label": label}
                break
    return report


def policy_blockers(policies: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{name}={info['value']} ({info['label']})" for name, info in policies.items() if info["status"] == "fail"]


# ---------------------------------------------------------------------------
# Process seams (every OS edge is one function the tests replace)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows():
        try:
            import ctypes  # noqa: PLC0415

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int) -> None:
    if pid <= 0:
        return
    if _is_windows():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            _log(f"taskkill failed for pid {pid}: {exc}")
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _rmtree_retry(path: Path, attempts: int = 6) -> bool:
    delay = 0.2
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                _log(f"could not remove {path}: {exc}")
                return False
            _sleep(delay)
            delay = min(delay * 2, 3.0)
    return False


def _tail(path: Path, lines: int = 15) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def _processes_using(needle: str) -> list[dict[str, Any]]:
    """Processes whose command line contains `needle` (ordinal, case-insensitive). Windows CIM only."""
    if not _is_windows():
        return []
    quoted = needle.replace("'", "''")
    # $PID excludes the PowerShell running this query, whose own command line contains the needle.
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and "
        f"$_.CommandLine.IndexOf('{quoted}', [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }} | "
        "Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    rows = data if isinstance(data, list) else [data]
    return [{"pid": int(r.get("ProcessId") or 0), "name": str(r.get("Name") or ""), "cmd": str(r.get("CommandLine") or "")} for r in rows if r]


def _kill_browsers_using(user_data_dir: Path, dry_run: bool = False) -> list[int]:
    """Kill browser/driver/daemon processes holding this exact profile folder. Never a name fragment."""
    killed: list[int] = []
    for proc in _processes_using(str(user_data_dir)):
        if proc["name"].lower() not in BROWSER_PROCESS_NAMES or "--type=" in proc["cmd"]:
            continue
        if proc["pid"] == os.getpid():
            continue
        killed.append(proc["pid"])
        if not dry_run:
            _kill_tree(proc["pid"])
    return killed


def _job_info() -> dict[str, Any]:
    """Job Object facts for this process (Windows), with ctypes argtypes set so the answer is real."""
    if not _is_windows():
        return {"in_job": None}
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        in_job = wintypes.BOOL(0)
        kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job))
        flags = None
        if in_job.value:
            info = ExtendedLimit()
            if kernel32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):  # JobObjectExtendedLimitInformation
                flags = info.BasicLimitInformation.LimitFlags
        names = {0x0800: "BREAKAWAY_OK", 0x1000: "SILENT_BREAKAWAY_OK", 0x2000: "KILL_ON_JOB_CLOSE"}
        return {"in_job": bool(in_job.value), "limit_flags": hex(flags) if flags is not None else None,
                "flags": [n for bit, n in names.items() if flags and flags & bit],
                "kill_on_job_close": bool(flags and flags & 0x2000)}
    except Exception as exc:  # noqa: BLE001
        return {"in_job": None, "error": str(exc)}


def _spawn_daemon(argv: list[str], log_path: Path, cwd: Path) -> tuple[int, str]:
    """Start the daemon detached from this shell. Returns (pid, launcher)."""
    launcher = (os.getenv("AGENT_BROWSER_LAUNCHER") or "popen").strip().lower()
    if _is_windows() and launcher == "wmi":
        cmd = subprocess.list2cmdline(argv).replace("'", "''")
        script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='"
                  + cmd + "'; CurrentDirectory='" + str(cwd).replace("'", "''") + "'}; Write-Output $r.ProcessId")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60, check=False)
        pid = int(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() and out.stdout.strip().splitlines()[-1].isdigit() else 0
        if not pid:
            raise _tag(RuntimeError(f"WMI launcher failed: {out.stderr.strip()[:300]}"), ERR_LAUNCH)
        return pid, "wmi"
    log = open(log_path, "ab")  # noqa: SIM115 - handed to the child, closed here after spawn
    try:
        kwargs: dict[str, Any] = {}
        if _is_windows():
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            for creationflags in (flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, flags):
                try:
                    proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, creationflags=creationflags)
                    return proc.pid, "popen-breakaway" if creationflags & subprocess.CREATE_BREAKAWAY_FROM_JOB else "popen"
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 5 or not (creationflags & subprocess.CREATE_BREAKAWAY_FROM_JOB):
                        raise
            raise AssertionError("unreachable")
        kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, **kwargs)
        return proc.pid, "popen"
    finally:
        log.close()


def _try_lock(path: Path) -> Any:
    """Exclusive, non-blocking lock on `path`. Returns a handle (keep it) or None if held elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115
    try:
        if _is_windows():
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _release_lock(handle: Any) -> None:
    if handle is None:
        return
    try:
        if _is_windows():
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def _lock_held(path: Path) -> bool:
    handle = _try_lock(path)
    if handle is None:
        return True
    _release_lock(handle)
    return False


def _drive_is_fixed(path: Path) -> bool:
    if not _is_windows():
        return True
    try:
        import ctypes  # noqa: PLC0415

        root = os.path.splitdrive(str(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 3  # type: ignore[attr-defined]  # DRIVE_FIXED
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Session store


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomic, owner-only write: the session file carries the daemon token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_windows():
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _read_session(paths: ProfilePaths) -> dict[str, Any] | None:
    try:
        data = json.loads(paths.session.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("port") and data.get("token") else None


def _ping(session: dict[str, Any], timeout: float = PING_TIMEOUT) -> dict[str, Any] | None:
    try:
        return _request(session, "ping", {}, timeout=timeout, grace=0.0)
    except (RuntimeError, ValueError, OSError):
        return None


def _liveness(paths: ProfilePaths) -> tuple[str, dict[str, Any] | None]:
    """running | unresponsive | starting | stale | none, with the session record when there is one."""
    held = _lock_held(paths.lock) if paths.lock.exists() else False
    session = _read_session(paths)
    if held:
        if session is None:
            return "starting", None
        return ("running", session) if _ping(session) is not None else ("unresponsive", session)
    if session is not None:
        return "stale", session
    return "none", None


def _running_profiles() -> list[str]:
    root = _home() / "profiles"
    if not root.is_dir():
        return []
    names = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and PROFILE_RE.match(entry.name) and entry.name not in RESERVED_PROFILES:
            lock = entry / "session.lock"
            if lock.exists() and _lock_held(lock) and (entry / "session.json").is_file():
                names.append(entry.name)
    return names


def _resolve_profile(explicit: str | None, sticky: bool = True) -> str:
    if explicit:
        return _validate_profile(explicit)
    env = os.getenv("AGENT_BROWSER_PROFILE")
    if env:
        return _validate_profile(env)
    if sticky:
        running = _running_profiles()
        if len(running) == 1:
            return running[0]
        if len(running) > 1:
            raise _tag(ValueError("Several profiles are running: " + ", ".join(running)), ERR_AMBIGUOUS_PROFILE,
                       hint=_hint("ambiguous_profile"), details={"profiles": running})
    return DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# Client protocol (one JSON line per request over a token-gated loopback socket)


def _request(session: dict[str, Any], cmd: str, args: dict[str, Any], timeout: float, grace: float = CLIENT_GRACE) -> dict[str, Any]:
    payload = json.dumps({"token": session["token"], "cmd": cmd, "args": args, "timeout": timeout}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", int(session["port"])), timeout=3.0) as sock:
            sock.settimeout(timeout + grace)
            sock.sendall(payload.encode("utf-8"))
            with sock.makefile("rb") as reader:
                line = reader.readline()
    except socket.timeout as exc:
        raise _tag(RuntimeError(f"The daemon did not answer within {timeout + grace:g}s."), ERR_TIMEOUT, hint=_hint("timeout_client", session.get("profile", DEFAULT_PROFILE))) from exc
    except OSError as exc:
        raise _tag(RuntimeError(f"Cannot reach the daemon on port {session.get('port')}: {exc}"), ERR_NOT_RUNNING, hint=_hint("not_running", session.get("profile", DEFAULT_PROFILE))) from exc
    if not line:
        raise _tag(RuntimeError("The daemon closed the connection without answering."), ERR_DAEMON, hint=_hint("daemon_unresponsive", session.get("profile", DEFAULT_PROFILE)))
    try:
        reply = json.loads(line.decode("utf-8"))
    except ValueError as exc:
        raise _tag(RuntimeError("The daemon sent a malformed reply."), ERR_DAEMON) from exc
    if reply.get("ok"):
        return reply.get("result") or {}
    _raise_remote(reply.get("error") or {}, session.get("profile", DEFAULT_PROFILE))
    raise AssertionError("unreachable")


def _raise_remote(error: dict[str, Any], profile: str) -> None:
    cls = str(error.get("class") or ERR_DAEMON)
    message = str(error.get("message") or "daemon error")
    hint = error.get("hint")
    details = error.get("details")
    exc: Exception = ValueError(message) if cls in EXIT_2_CLASSES else RuntimeError(message)
    raise _tag(exc, cls, hint=hint, details=details)


def _require_running(paths: ProfilePaths, verb: str) -> dict[str, Any]:
    state, session = _liveness(paths)
    if state == "running" and session is not None:
        return session
    if state == "starting":
        deadline = _now() + 10.0
        while _now() < deadline:
            _sleep(0.2)
            state, session = _liveness(paths)
            if state == "running" and session is not None:
                return session
        raise _tag(RuntimeError("The browser is still starting."), ERR_NOT_RUNNING, hint=_hint("starting", paths.profile))
    if state == "unresponsive":
        raise _tag(RuntimeError("The daemon holds the lock but does not answer."), ERR_DAEMON_UNRESPONSIVE, hint=_hint("daemon_unresponsive", paths.profile))
    if state == "stale":
        paths.session.unlink(missing_ok=True)
    raise _tag(RuntimeError(f"No browser is running for profile {paths.profile!r} ({verb} needs one)."), ERR_NOT_RUNNING, hint=_hint("not_running", paths.profile))


# ---------------------------------------------------------------------------
# Snapshot pure layer


@dataclass
class SnapLine:
    level: int
    role: str
    name: str | None
    ref: str | None
    attrs: list[str]
    value: str | None
    has_children: bool
    raw: str

    @property
    def prefix(self) -> str:
        m = REF_RE.match(self.ref or "")
        return (m.group(1) or "") if m else ""


def parse_ai_snapshot(text: str) -> list[SnapLine]:
    lines: list[SnapLine] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = LINE_RE.match(raw)
        if not m:
            level = (len(raw) - len(raw.lstrip(" "))) // 2
            lines.append(SnapLine(level, "text", None, None, [], raw.strip(), False, raw))
            continue
        attrs = re.findall(r"\[([^\]]*)\]", m.group("attrs") or "")
        ref = next((a[4:] for a in attrs if a.startswith("ref=")), None)
        attrs = [a for a in attrs if not a.startswith("ref=")]
        name = m.group("name")
        if name is not None:
            name = name.replace('\\"', '"')
        value = m.group("value")
        lines.append(SnapLine(len(m.group("indent")) // 2, m.group("role"), name, ref, attrs, value, bool(m.group("colon")) and value is None, raw))
    return lines


def is_ref(target: str) -> bool:
    return bool(REF_RE.match(normalize_target(target)))


def normalize_target(target: str) -> str:
    t = (target or "").strip()
    t = re.sub(r"^\[ref=(.+)\]$", r"\1", t)
    t = re.sub(r"^ref=", "", t)
    return t


def frame_prefixes(lines: Sequence[SnapLine]) -> dict[str, str]:
    """Map each iframe line's ref to the `f<N>` prefix its children carry."""
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        if line.role != "iframe" or not line.ref:
            continue
        for child in lines[i + 1:]:
            if child.level <= line.level:
                break
            if child.ref and child.prefix:
                out[line.ref] = child.prefix
                break
    return out


def _render(line: SnapLine, keep_ref: bool, frame_names: dict[str, str] | None, prefixes: dict[str, str]) -> str:
    attrs = [a for a in line.attrs if a.split("=")[0] in KEEP_ATTRS or a.startswith("level=")]
    parts = ["- " + line.role]
    if line.role == "iframe" and line.ref:
        label = (frame_names or {}).get(line.ref)
        if label:
            parts.append(f'"{sanitize_text(label, 80)}"')
    elif line.name is not None:
        parts.append('"' + sanitize_text(line.name, LINE_CHAR_CAP).replace('"', '\\"') + '"')
    parts.extend(f"[{a}]" for a in attrs)
    if keep_ref and line.ref:
        parts.append(f"[ref={line.ref}]")
    if line.role == "iframe" and line.ref and line.ref in prefixes:
        parts.append(f"(children {prefixes[line.ref]}e...)")
    text = " ".join(parts)
    if line.value is not None and line.role != "iframe":
        text += ": " + sanitize_text(line.value, LINE_CHAR_CAP)
    elif line.has_children:
        text += ":"
    return text


def filter_snapshot(text: str, *, full: bool = False, find: str | None = None, frame_names: dict[str, str] | None = None) -> list[str]:
    """Reduce Playwright's ai-mode tree to what a small model can act on. Pure function."""
    lines = parse_ai_snapshot(text)
    prefixes = frame_prefixes(lines)
    needle = (find or "").strip().lower()
    keep: list[tuple[bool, bool]] = []  # (keep line, keep ref)
    stack: list[tuple[int, str]] = []   # (level, role) of ancestors
    parent_roles: list[str | None] = []
    for line in lines:
        while stack and stack[-1][0] >= line.level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        parent_roles.append(parent)
        stack.append((line.level, line.role))
        if full:
            keep.append((True, bool(line.ref)))
            continue
        role = line.role
        actionable = role in ACTIONABLE_ROLES or (role == "generic" and "cursor=pointer" in line.attrs)
        context = role in CONTEXT_ROLES or (role in NAMED_CONTEXT_ROLES and bool(line.name))
        option = role == "option" and parent in OPTION_PARENTS
        if needle:
            match = needle in line.raw.lower()
            keep.append((match, actionable and match))
            continue
        keep.append((actionable or context or option, actionable))
    if needle and not full:
        # keep ancestors of every match so the model sees where it sits
        for i in range(len(lines) - 1, -1, -1):
            if keep[i][0]:
                level = lines[i].level
                for j in range(i - 1, -1, -1):
                    if lines[j].level < level:
                        if not keep[j][0]:
                            keep[j] = (True, lines[j].role in ACTIONABLE_ROLES or (lines[j].role == "generic" and "cursor=pointer" in lines[j].attrs))
                        level = lines[j].level
                        if level == 0:
                            break
    out: list[str] = []
    kept_levels: list[int] = []
    for line, (keep_line, keep_ref) in zip(lines, keep):
        while kept_levels and kept_levels[-1] >= line.level:
            kept_levels.pop()
        if not keep_line:
            continue
        if line.role in ("text", "/url", "paragraph") and not full and not needle:
            continue
        depth = len(kept_levels)
        kept_levels.append(line.level)
        if line.role == "generic" and line.level == 0 and not full:
            kept_levels.pop()  # the root wrapper never renders; its children stay at depth 0
            continue
        out.append("  " * depth + _render(line, keep_ref, frame_names, prefixes))
    # A trailing colon promises children; drop it when the filter removed them all.
    for i, text in enumerate(out):
        if text.endswith(":"):
            depth = len(text) - len(text.lstrip(" "))
            has_child = i + 1 < len(out) and (len(out[i + 1]) - len(out[i + 1].lstrip(" "))) > depth
            if not has_child:
                out[i] = text[:-1]
    return out


def budget_lines(lines: Sequence[str], max_bytes: int) -> tuple[list[str], bool]:
    used = 0
    kept: list[str] = []
    for line in lines:
        cost = len(json.dumps(line, ensure_ascii=False)) + 6
        if used + cost > max_bytes and kept:
            return kept, True
        used += cost
        kept.append(line)
    return kept, False


def diff_lines(before: Sequence[str] | None, after: Sequence[str]) -> dict[str, list[str]]:
    if before is None:
        return {"added": list(after)[:CHANGES_CAP], "removed": []}
    before_set, after_set = set(before), set(after)
    added = [l for l in after if l not in before_set][:CHANGES_CAP]
    removed = [l for l in before if l not in after_set][:CHANGES_CAP]
    return {"added": added, "removed": removed}


def find_line(lines: Sequence[str] | None, ref: str) -> str | None:
    marker = f"[ref={ref}]"
    for line in lines or []:
        if marker in line:
            return line.strip()
    return None


# ---------------------------------------------------------------------------
# Path and URL policy (client side)


def _check_url(url: str, paths: ProfilePaths, config: dict[str, Any]) -> str:
    raw = (url or "").strip()
    if not raw:
        raise _tag(ValueError("goto needs a URL."), ERR_USAGE)
    if "://" not in raw and not raw.lower().startswith(("about:", "data:", "javascript:", "file:", "blob:")):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme == "about" and parts.path == "blank":
        return raw
    if scheme == "data" and paths.profile in RESERVED_PROFILES:
        return raw
    if scheme not in ALLOWED_SCHEMES:
        raise _tag(ValueError(f"URL scheme {scheme or '(none)'!r} is not allowed."), ERR_GUARDED, hint=_hint("guarded_scheme"))
    host = (parts.hostname or "").lower()
    allowed = config.get("allowed_hosts")
    if allowed and not _host_allowed(host, allowed):
        raise _tag(ValueError(f"Host {host!r} is outside allowed_hosts."), ERR_GUARDED, hint=_hint("guarded_host"))
    return raw


def _host_allowed(host: str, allowed: Sequence[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed if a)


def _check_local_path(candidate: str, paths: ProfilePaths, config: dict[str, Any], must_exist: bool = True) -> Path:
    text = (candidate or "").strip()
    if not text:
        raise _tag(ValueError("A file path is required."), ERR_USAGE)
    if text.startswith(("\\\\", "//")):
        raise _tag(ValueError("UNC paths are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise _tag(ValueError(f"Cannot resolve {text!r}: {exc}"), ERR_VALIDATION) from exc
    if any(part.startswith(".") for part in resolved.parts[1:]):
        raise _tag(ValueError("Hidden files and folders are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    if resolved.suffix.lower() in KEY_EXTENSIONS:
        raise _tag(ValueError("Key material cannot be uploaded."), ERR_GUARDED, hint=_hint("guarded_path"))
    if not _drive_is_fixed(resolved):
        raise _tag(ValueError("Only files on a fixed local drive are allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    roots = [Path.cwd().resolve(), paths.downloads.resolve(), paths.shots.resolve()]
    roots.extend(Path(r).expanduser().resolve() for r in config.get("upload_roots", []) if r)
    if not any(_is_under(resolved, root) for root in roots):
        raise _tag(ValueError(f"{resolved} is outside the allowed folders."), ERR_GUARDED, hint=_hint("guarded_path"))
    if must_exist and not resolved.is_file():
        raise _tag(ValueError(f"File not found: {resolved}"), ERR_NOT_FOUND)
    return resolved


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Daemon


def _playwright_factory() -> Any:
    # The only import site for Playwright besides doctor. Seam replaced by the offline tests.
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise _tag(RuntimeError(f"Playwright is not installed for {sys.executable}: {exc}"), ERR_PLAYWRIGHT_MISSING, hint=_hint("playwright_missing")) from exc
    return sync_playwright().start()


def _playwright_errors() -> tuple[type, type]:
    try:
        from playwright.sync_api import Error, TimeoutError  # noqa: PLC0415
    except ImportError:
        return (Exception, Exception)
    return (Error, TimeoutError)


def _playwright_version() -> str | None:
    try:
        import importlib.metadata as md  # noqa: PLC0415

        return md.version("playwright")
    except Exception:  # noqa: BLE001
        return None


SECRET_JS = """el => {
  const a = n => (el.getAttribute && el.getAttribute(n)) || '';
  const labels = Array.from(el.labels || []).map(l => l.textContent || '').join(' ');
  const by = (a('aria-labelledby') || '').split(/\\s+/).map(id => (document.getElementById(id) || {}).textContent || '').join(' ');
  return {tag: el.tagName, type: String(el.type || '').toLowerCase(), autocomplete: String(el.autocomplete || a('autocomplete')).toLowerCase(),
          inputmode: String(el.inputMode || a('inputmode')).toLowerCase(), maxlength: typeof el.maxLength === 'number' ? el.maxLength : -1,
          text: [a('name'), el.id || '', a('aria-label'), a('placeholder'), a('title'), labels, by, String(el.className || ''), Object.keys(el.dataset || {}).join(' ')].join(' '),
          contenteditable: !!el.isContentEditable};
}"""
ACTIVE_SECRET_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body || el.tagName === 'IFRAME') return null;
  const f = %s;
  return f(el);
}""" % SECRET_JS
VISIBLE_SECRET_JS = """() => {
  const els = document.querySelectorAll('input[type=password], input[autocomplete=one-time-code]');
  for (const e of els) { if (e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden') return true; }
  return false;
}"""
OPTIONS_JS = "el => el.tagName === 'SELECT' ? Array.from(el.options).map(o => [o.label, o.value, o.selected]) : null"
VALUE_JS = "el => ('value' in el && el.tagName !== 'BUTTON') ? String(el.value) : ('checked' in el ? el.checked : (el.textContent || ''))"
FRAME_LABEL_JS = "e => e.name || e.title || e.id || ''"


def is_secret_field(info: dict[str, Any], page_url: str = "", page_title: str = "") -> bool:
    if not info:
        return False
    if info.get("type") == "password":
        return True
    if SECRET_AUTOCOMPLETE_RE.search(info.get("autocomplete") or ""):
        return True
    text = info.get("text") or ""
    if SECRET_STRONG_RE.search(text):
        return True
    login_page = bool(LOGIN_PAGE_RE.search(page_url or "") or LOGIN_PAGE_RE.search(page_title or ""))
    if login_page:
        if SECRET_WEAK_RE.search(text):
            return True
        maxlength = info.get("maxlength") or -1
        if info.get("inputmode") == "numeric" and 0 < maxlength <= 8:
            return True
    return False


@dataclass
class Served:
    raw: str
    lines: list[str]
    frame_by_prefix: dict[str, Any]
    nav_at_serve: dict[Any, int]
    frames: list[dict[str, Any]]
    frame_names: dict[str, str]
    prefixes: dict[str, str]


@dataclass
class DownloadRecord:
    download: Any
    suggested: str
    seen: str
    saved: str | None = None


class Daemon:
    """Owns one Playwright persistent context. Single-threaded: only the main thread touches Playwright."""

    def __init__(self, launch: dict[str, Any]) -> None:
        self.launch = launch
        self.profile = launch["profile"]
        self.paths = ProfilePaths(self.profile, Path(launch["home"]))
        self.token = launch["token"]
        self.config = launch.get("config") or {}
        self.pw: Any = None
        self.ctx: Any = None
        self.pages: list[Any] = []
        self.current: Any = None
        self.nav_counts: dict[Any, int] = {}
        self.served: dict[Any, Served] = {}
        self.dialog_log: list[dict[str, Any]] = []
        self.downloads: list[DownloadRecord] = []
        self.new_pages: list[Any] = []
        self.current_cmd: str | None = None
        self.cmd_opts: dict[str, Any] = {}
        self.dialog_blocked = False
        self.unsaved_blocked = False
        self.discarded_unsaved = False
        self.stop_reason: str | None = None
        self.last_activity = _now()
        self.errors: tuple[type, type] = (Exception, Exception)
        self.browser_version: str | None = None
        self.log_handle: Any = None

    # -- logging -------------------------------------------------------------------------

    def dlog(self, message: str) -> None:
        line = f"{_utc_now()} {message}"
        try:
            print(line, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

    # -- lifecycle -----------------------------------------------------------------------

    def start_browser(self) -> None:
        self.pw = _playwright_factory()
        self.errors = _playwright_errors()
        kind = self.launch.get("browser") or "msedge"
        exe = _check_browser_exe(self.launch.get("exe"))
        options: dict[str, Any] = {
            "headless": bool(self.launch.get("headless")),
            "no_viewport": True,
            "args": [
                f"--window-size={self.launch.get('window_size') or DEFAULT_WINDOW_SIZE}",
                "--disable-sync",
                "--disable-features=msImplicitSignin,msSeamlessWebToBrowserSignIn",
                "--hide-crash-restore-bubble",
            ],
            "accept_downloads": True,
            "downloads_path": str(self.paths.downloads),
        }
        if exe:
            options["executable_path"] = exe
        else:
            options["channel"] = kind
        self.paths.downloads.mkdir(parents=True, exist_ok=True)
        self.paths.shots.mkdir(parents=True, exist_ok=True)
        _seed_preferences(self.paths.user_data)
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(str(self.paths.user_data), **options)
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                last_exc = exc
                self.dlog(f"launch attempt {attempt + 1} failed: {str(exc)[:300]}")
                if "already in use" in str(exc).lower() or "user data directory" in str(exc).lower():
                    raise _tag(RuntimeError("The profile folder is already in use by another browser."), ERR_PROFILE_IN_USE, hint=_hint("profile_in_use", self.profile)) from exc
                _sleep(1.0)
        if self.ctx is None:
            raise _tag(RuntimeError(f"The browser did not start: {str(last_exc)[:300]}"), ERR_LAUNCH, hint=_hint("launch", self.profile))
        try:
            self.browser_version = self.ctx.browser.version if self.ctx.browser else None
        except Exception:  # noqa: BLE001
            self.browser_version = None
        self.ctx.set_default_timeout(DEFAULT_TIMEOUT * 1000)
        self.ctx.on("page", self._on_page)
        self.ctx.on("dialog", self._on_dialog)
        self.ctx.on("close", lambda _ctx: self._mark_stop("browser_closed"))
        for page in list(self.ctx.pages):
            self._track_page(page)
        if not self.pages:
            self._track_page(self.ctx.new_page())
        self.current = self.pages[0]

    def _track_page(self, page: Any) -> None:
        if page in self.pages:
            return
        self.pages.append(page)
        page.on("framenavigated", self._on_frame_navigated)
        page.on("download", self._on_download)
        page.on("close", lambda p: self._on_page_closed(p))

    def _on_page(self, page: Any) -> None:
        self._track_page(page)
        self.new_pages.append(page)
        if self.current_cmd is not None:
            self.current = page

    def _on_page_closed(self, page: Any) -> None:
        if page in self.pages:
            self.pages.remove(page)
        self.served.pop(page, None)
        if self.current is page:
            self.current = self.pages[-1] if self.pages else None
        if not self.pages and self.current_cmd != "stop":
            self._mark_stop("browser_closed")

    def _on_frame_navigated(self, frame: Any) -> None:
        self.nav_counts[frame] = self.nav_counts.get(frame, 0) + 1

    def _on_download(self, download: Any) -> None:
        try:
            suggested = download.suggested_filename
        except Exception:  # noqa: BLE001
            suggested = "download"
        self.downloads.append(DownloadRecord(download, safe_name(suggested), _utc_now()))

    def _on_dialog(self, dialog: Any) -> None:
        kind = getattr(dialog, "type", "confirm")
        record = {"type": kind, "message": sanitize_text(getattr(dialog, "message", ""), 200), "handled": ""}
        in_cmd = self.current_cmd is not None
        try:
            if kind == "alert":
                dialog.accept()
                record["handled"] = "accepted"
            elif kind == "beforeunload":
                if in_cmd and self.current_cmd != "stop" and not self.cmd_opts.get("discard_changes"):
                    dialog.dismiss()
                    record["handled"] = "dismissed"
                    self.unsaved_blocked = True
                else:
                    dialog.accept()
                    record["handled"] = "accepted"
                    self.discarded_unsaved = True
            elif in_cmd and self.cmd_opts.get("accept_dialog"):
                dialog.accept(self.cmd_opts.get("dialog_text") or "")
                record["handled"] = "accepted"
            else:
                dialog.dismiss()
                record["handled"] = "dismissed"
                if in_cmd:
                    self.dialog_blocked = True
        except Exception as exc:  # noqa: BLE001 - dialog already gone
            record["handled"] = f"error: {str(exc)[:100]}"
        self.dialog_log.append(record)

    def _mark_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason

    def close(self) -> None:
        try:
            if self.ctx is not None:
                self.ctx.close()
        except Exception as exc:  # noqa: BLE001
            self.dlog(f"context close: {str(exc)[:200]}")
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception as exc:  # noqa: BLE001
            self.dlog(f"playwright stop: {str(exc)[:200]}")
        self.ctx = None
        self.pw = None

    # -- helpers -------------------------------------------------------------------------

    def page(self) -> Any:
        if self.current is None or self.current not in self.pages:
            if not self.pages:
                raise _tag(RuntimeError("The browser has no open tabs."), ERR_BROWSER_CLOSED, hint=_hint("browser_closed", self.profile))
            self.current = self.pages[-1]
        return self.current

    def _is_timeout(self, exc: Exception) -> bool:
        return isinstance(exc, self.errors[1]) or "Timeout" in type(exc).__name__ or "exceeded" in str(exc)

    def _classify(self, exc: Exception, verb: str) -> Exception:
        """Map a Playwright error to the repo's error contract."""
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        text = str(exc)
        p = self.profile
        if "Invalid frame in aria-ref selector" in text or "does not match any element" in text:
            return _tag(ValueError(sanitize_text(message)), ERR_STALE_REF, hint=_hint("stale_ref", p))
        if "has been closed" in text or "Target closed" in text or "browser has been closed" in text.lower():
            return _tag(RuntimeError(sanitize_text(message)), ERR_BROWSER_CLOSED, hint=_hint("browser_closed", p))
        if "Not a checkbox or radio button" in text:
            return _tag(ValueError(sanitize_text(message)), ERR_ACTION_FAILED, hint=_hint("check_not_checkbox", p))
        if "not a <select> element" in text:
            return _tag(ValueError(sanitize_text(message)), ERR_ACTION_FAILED, hint=_hint("select_not_select", p))
        if "Element is not an <input>" in text:
            return _tag(ValueError(sanitize_text(message)), ERR_ACTION_FAILED, hint=_hint("fill_not_input", p))
        if "strict mode violation" in text:
            return _tag(ValueError(sanitize_text(message)), ERR_AMBIGUOUS, hint=_hint("action_failed", p))
        if self._is_timeout(exc):
            return _tag(ValueError(sanitize_text(message)), ERR_ACTION_TIMEOUT, hint=_hint("action_timeout", p))
        calllog = [l.strip() for l in text.splitlines()[1:6] if l.strip()]
        return _tag(ValueError(sanitize_text(message)), ERR_ACTION_FAILED, hint=_hint("action_failed", p), details={"log": [sanitize_text(l, 200) for l in calllog]})

    def _ref_target(self, page: Any, ref: str) -> Any:
        m = REF_RE.match(ref)
        if not m:
            raise _tag(ValueError(f"Bad ref {ref!r}."), ERR_VALIDATION, hint=_hint("bad_ref", self.profile))
        try:
            page.wait_for_timeout(1)  # apply queued frame-navigation events before reading the counters
        except Exception:  # noqa: BLE001
            pass
        served = self.served.get(page)
        if served is None:
            self._serve_snapshot(page)
            served = self.served[page]
        prefix = m.group(1) or ""
        frame = served.frame_by_prefix.get(prefix) if prefix else page.main_frame
        if prefix and frame is None:
            raise _tag(ValueError(f"Ref {ref} belongs to a frame that is no longer in the snapshot."), ERR_STALE_REF, hint=_hint("stale_ref", self.profile))
        if self.nav_counts.get(frame, 0) != served.nav_at_serve.get(frame, 0):
            raise _tag(ValueError(f"Ref {ref} is stale: its frame navigated since the last snapshot."), ERR_STALE_REF, hint=_hint("stale_ref", self.profile))
        locator = page.locator("aria-ref=" + ref)
        try:
            count = locator.count()
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "resolve") from exc
        if count == 0:
            page.aria_snapshot(mode="ai")  # heal once: refs are cached per element, so numbering is preserved
            try:
                count = locator.count()
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc, "resolve") from exc
        if count == 0:
            raise _tag(ValueError(f"Ref {ref} is not on the current page."), ERR_REF_NOT_FOUND, hint=_hint("ref_not_found", self.profile))
        return locator

    def _selector_target(self, page: Any, selector: str) -> Any:
        matches = []
        for frame in [page.main_frame] + [f for f in page.frames if f is not page.main_frame]:
            try:
                loc = frame.locator(selector)
                if loc.count() > 0:
                    matches.append((frame, loc))
            except Exception as exc:  # noqa: BLE001
                if "not a valid selector" in str(exc) or "Unexpected token" in str(exc):
                    raise _tag(ValueError(f"Bad selector {selector!r}."), ERR_VALIDATION, hint=_hint("bad_ref", self.profile)) from exc
        if not matches:
            raise _tag(ValueError(f"Nothing matches {selector!r}."), ERR_NOT_FOUND, hint=_hint("ref_not_found", self.profile))
        if len(matches) > 1:
            raise _tag(ValueError(f"{selector!r} matches in {len(matches)} frames; use a ref."), ERR_AMBIGUOUS, hint=_hint("stale_ref", self.profile))
        loc = matches[0][1]
        if loc.count() > 1:
            raise _tag(ValueError(f"{selector!r} matches {loc.count()} elements; use a ref."), ERR_AMBIGUOUS, hint=_hint("stale_ref", self.profile))
        return loc

    def resolve(self, page: Any, target: str) -> Any:
        t = normalize_target(target)
        if not t:
            raise _tag(ValueError("A target ref is required."), ERR_USAGE, hint=_hint("bad_ref", self.profile))
        return self._ref_target(page, t) if REF_RE.match(t) else self._selector_target(page, t)

    def _secret_info(self, locator: Any) -> dict[str, Any]:
        try:
            return locator.evaluate(SECRET_JS, timeout=3000) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _safe_title(self, page: Any) -> str:
        try:
            return page.title()
        except Exception:  # noqa: BLE001
            return ""

    def _is_secret(self, page: Any, locator: Any) -> bool:
        return is_secret_field(self._secret_info(locator), page.url, self._safe_title(page))

    def _focused_is_secret(self, page: Any) -> bool:
        """The element that would receive a targetless key press, in whichever frame holds focus."""
        for frame in page.frames:
            try:
                info = frame.evaluate(ACTIVE_SECRET_JS)
            except Exception:  # noqa: BLE001
                continue
            if info and is_secret_field(info, page.url, self._safe_title(page)):
                return True
        return False

    def _settle(self, page: Any, cap: float = SETTLE_CAP) -> list[str]:
        deadline = _now() + cap
        try:
            page.wait_for_load_state("load", timeout=min(3000, max(100, int((deadline - _now()) * 1000))))
        except Exception:  # noqa: BLE001
            pass
        last: tuple | None = None
        stable_since: float | None = None
        while _now() < deadline:
            try:
                frames = list(page.frames)
            except Exception:  # noqa: BLE001
                break
            for frame in frames:
                remaining = int((deadline - _now()) * 1000)
                if remaining <= 0:
                    break
                try:
                    frame.wait_for_load_state("load", timeout=min(1500, remaining))
                except Exception:  # noqa: BLE001
                    pass
            signature = tuple((f.url, f.name) for f in frames)
            if signature == last:
                if stable_since is not None and _now() - stable_since >= 0.25:
                    break
            else:
                last, stable_since = signature, _now()
            try:
                page.wait_for_timeout(100)
            except Exception:  # noqa: BLE001
                break
        loading = []
        try:
            for frame in page.frames:
                try:
                    if frame.evaluate("() => document.readyState") != "complete":
                        loading.append(frame.name or redact_url(frame.url))
                except Exception:  # noqa: BLE001 - a frame mid-navigation has no context yet
                    loading.append(frame.name or "frame")
        except Exception:  # noqa: BLE001
            pass
        return loading

    def _frame_map(self, page: Any, lines: Sequence[SnapLine]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str], dict[str, str]]:
        prefixes = frame_prefixes(lines)
        by_prefix: dict[str, Any] = {}
        frames: list[dict[str, Any]] = []
        names: dict[str, str] = {}
        page_origin = _origin(page.url)
        for line in lines:
            if line.role != "iframe" or not line.ref:
                continue
            frame = None
            label = ""
            try:
                handle = page.locator("aria-ref=" + line.ref).element_handle(timeout=1000)
                label = handle.evaluate(FRAME_LABEL_JS) or ""
                frame = handle.content_frame()
            except Exception:  # noqa: BLE001
                frame = None
            prefix = prefixes.get(line.ref, "")
            if frame is not None and prefix:
                by_prefix[prefix] = frame
            if label:
                names[line.ref] = label
            frames.append({"prefix": prefix, "ref": line.ref, "name": sanitize_text(label, 80),
                           "url": redact_url(frame.url) if frame is not None else "",
                           "same_origin": (_origin(frame.url) == page_origin) if frame is not None else None})
        return by_prefix, frames, names, prefixes

    def _serve_snapshot(self, page: Any, *, full: bool = False, find: str | None = None, scope: str | None = None) -> Served:
        try:
            if scope:
                raw = self.resolve(page, scope).aria_snapshot(mode="ai")
            else:
                raw = page.aria_snapshot(mode="ai")
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "snapshot") from exc
        parsed = parse_ai_snapshot(raw)
        by_prefix, frames, names, prefixes = self._frame_map(page, parsed)
        lines = filter_snapshot(raw, full=full, find=find, frame_names=names)
        nav_at = {f: self.nav_counts.get(f, 0) for f in [page.main_frame] + list(by_prefix.values())}
        served = Served(raw, lines, by_prefix, nav_at, frames, names, prefixes)
        self.served[page] = served
        try:
            self.paths.last_snapshot.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass
        return served

    def _sign_in_reasons(self, page: Any, requested_host: str | None = None) -> list[str]:
        reasons: list[str] = []
        host = (urllib.parse.urlsplit(page.url).hostname or "").lower()
        if requested_host and host and not (host == requested_host or host.endswith("." + requested_host) or requested_host.endswith("." + host)):
            reasons.append("host_changed")
        if _is_idp_host(host):
            reasons.append("idp_host")
        try:
            for frame in page.frames:
                try:
                    if frame.evaluate(VISIBLE_SECRET_JS):
                        reasons.append("password_field")
                        break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return reasons

    def _off_allowlist(self, page: Any) -> bool:
        allowed = self.config.get("allowed_hosts")
        if not allowed:
            return False
        host = (urllib.parse.urlsplit(page.url).hostname or "").lower()
        if not host and page.url.startswith("about:"):
            return False
        return not _host_allowed(host, allowed)

    def _tab_index(self, page: Any) -> int:
        return self.pages.index(page) + 1 if page in self.pages else 0

    def _tabs(self) -> list[dict[str, Any]]:
        out = []
        for i, p in enumerate(self.pages, 1):
            try:
                out.append({"index": i, "url": redact_url(p.url), "title": sanitize_text(p.title(), 120), "current": p is self.current})
            except Exception:  # noqa: BLE001
                out.append({"index": i, "url": "", "title": "", "current": p is self.current})
        return out

    def _base_fields(self, page: Any) -> dict[str, Any]:
        return {"profile": self.profile, "url": redact_url(page.url), "title": sanitize_text(self._safe_title(page), 200),
                "tab": self._tab_index(page), "tabs": len(self.pages)}

    def snap_envelope(self, page: Any, *, full: bool = False, find: str | None = None, scope: str | None = None,
                      navigated: bool = False, requested_host: str | None = None, extra: dict[str, Any] | None = None,
                      loading: Sequence[str] = (), max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
        served = self._serve_snapshot(page, full=full, find=find, scope=scope)
        lines, truncated = budget_lines(served.lines, max_bytes)
        env = self._base_fields(page)
        env.update({"frames": served.frames, "lines": len(served.lines), "truncated": truncated,
                    "snapshot_file": str(self.paths.last_snapshot).replace("\\", "/")})
        notes = []
        if truncated:
            notes.append(_hint("truncated", self.profile))
        if loading:
            env["frames_loading"] = [sanitize_text(l, 80) for l in loading]
            notes.append(_hint("frames_loading", self.profile))
        reasons = self._sign_in_reasons(page, requested_host)
        env["sign_in_suspected"] = bool(reasons)
        if reasons:
            env["sign_in_reasons"] = reasons
            notes.append(_hint("sign_in", self.profile))
        if self._off_allowlist(page):
            env["off_allowlist"] = True
            notes.append(_hint("off_allowlist", self.profile))
        if find and not lines:
            notes.append("No line matched --find; tabs or sections may hide it. Try a shorter word or click a tab.")
        env["navigated"] = navigated
        env["new_tab"] = self._new_tab_info()
        env["dialogs"] = self._take_dialogs()
        env["downloads"] = self._downloads_seen()
        if extra:
            env.update(extra)
        if notes:
            env["note"] = " ".join(notes)
        env["untrusted"] = UNTRUSTED_KEYS
        env["snapshot"] = lines
        return env

    def act_envelope(self, page: Any, action: str, target_line: str | None, before: Sequence[str] | None, *,
                     navigated: bool, loading: Sequence[str], value_after: Any = None, max_bytes: int = DEFAULT_MAX_BYTES,
                     force_full: bool = False, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        dialogs_pending = self.dialog_log
        if navigated or force_full or dialogs_pending:
            env = self.snap_envelope(page, navigated=navigated, loading=loading, max_bytes=max_bytes)
            env = {"ok": True, "action": action, "target_line": target_line, **env}
            if value_after is not None:
                env["value_after"] = value_after
            if extra:
                env.update(extra)
            return env
        served = self._serve_snapshot(page)
        changes = diff_lines(before, served.lines)
        added, cut_a = budget_lines(changes["added"], max_bytes // 2)
        removed, cut_r = budget_lines(changes["removed"], max_bytes // 2)
        env: dict[str, Any] = {"ok": True, "action": action, "target_line": target_line}
        env.update(self._base_fields(page))
        if value_after is not None:
            env["value_after"] = value_after
        env["navigated"] = False
        env["new_tab"] = self._new_tab_info()
        env["dialogs"] = []
        env["downloads"] = self._downloads_seen()
        env["truncated"] = cut_a or cut_r
        env["changes"] = {"added": added, "removed": removed}
        env["refs_valid"] = True
        if loading:
            env["frames_loading"] = [sanitize_text(l, 80) for l in loading]
        if self._off_allowlist(page):
            env["off_allowlist"] = True
            env["note"] = _hint("off_allowlist", self.profile)
        if extra:
            env.update(extra)
        env["untrusted"] = UNTRUSTED_KEYS
        return env

    def _new_tab_info(self) -> dict[str, Any] | None:
        if not self.new_pages:
            return None
        page = self.new_pages[-1]
        self.new_pages = []
        try:
            return {"index": self._tab_index(page), "url": redact_url(page.url)}
        except Exception:  # noqa: BLE001
            return {"index": self._tab_index(page), "url": ""}

    def _take_dialogs(self) -> list[dict[str, Any]]:
        out, self.dialog_log = self.dialog_log, []
        return out

    def _downloads_seen(self) -> list[dict[str, Any]]:
        since = self.cmd_started
        return [{"file": d.suggested, "saved": d.saved, "state": "saved" if d.saved else "started"} for d in self.downloads if d.seen >= since]

    cmd_started: str = ""

    port: int = 0

    def _begin(self, cmd: str, opts: dict[str, Any]) -> None:
        self.current_cmd = cmd
        self.cmd_opts = opts
        self.cmd_started = _utc_now()
        self.dialog_blocked = False
        self.unsaved_blocked = False
        self.discarded_unsaved = False
        self.new_pages = []
        self.last_activity = _now()
        if cmd != "ping" and self.port:
            self.write_session(self.port)  # `busy` is visible to `status` while a long action runs

    def _end(self) -> None:
        was = self.current_cmd
        self.current_cmd = None
        self.cmd_opts = {}
        if was != "ping" and self.port:
            self.write_session(self.port)

    def _check_blocks(self) -> None:
        if self.unsaved_blocked:
            raise _tag(ValueError("The page has unsaved changes; the navigation was cancelled."), ERR_UNSAVED, hint=_hint("unsaved_changes", self.profile))
        if self.dialog_blocked:
            raise _tag(ValueError("A dialog was dismissed during the action."), ERR_DIALOG, hint=_hint("dialog", self.profile), details={"dialogs": self.dialog_log[-3:]})

    def _nav_signature(self, page: Any) -> dict[Any, int]:
        return dict(self.nav_counts)

    def _navigated_since(self, before: dict[Any, int]) -> bool:
        return any(self.nav_counts.get(f, 0) != n for f, n in before.items()) or any(f not in before for f in self.nav_counts)

    def _before_lines(self, page: Any) -> list[str] | None:
        served = self.served.get(page)
        return list(served.lines) if served else None

    def _value_of(self, locator: Any, masked: bool) -> Any:
        if masked:
            return "***"
        try:
            return sanitize_text(locator.evaluate(VALUE_JS, timeout=2000), 200)
        except Exception:  # noqa: BLE001
            return None

    # -- request dispatch ----------------------------------------------------------------

    def dispatch(self, cmd: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        handler = getattr(self, "h_" + cmd.replace("-", "_"), None)
        if handler is None:
            raise _tag(ValueError(f"Unknown daemon command {cmd!r}."), ERR_USAGE)
        if cmd not in OFF_ALLOWLIST_OK and self.current is not None and self._off_allowlist(self.current):
            raise _tag(ValueError("The page is outside allowed_hosts; only back, goto, snapshot, text, tabs, and stop are allowed."),
                       ERR_GUARDED, hint=_hint("off_allowlist", self.profile))
        self._begin(cmd, args)
        try:
            return handler(args, timeout)
        finally:
            self._end()

    def _timeout_ms(self, timeout: float) -> int:
        return int(max(0.5, timeout) * 1000)

    def h_ping(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.current
        url = title = ""
        if page is not None:
            try:
                url, title = redact_url(page.url), sanitize_text(page.title(), 120)
            except Exception:  # noqa: BLE001
                pass
        return {"pong": True, "profile": self.profile, "browser_version": self.browser_version, "tabs": self._tabs(),
                "current_tab": self._tab_index(page) if page is not None else 0, "url": url, "title": title}

    def h_stop(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        self._mark_stop("stop")
        return {"stopping": True}

    def _drain_events(self, page: Any, budget: float = 0.6) -> None:
        """Let queued Playwright events (a beforeunload dialog answered after ERR_ABORTED) reach the handlers."""
        deadline = _now() + budget
        while _now() < deadline and not (self.unsaved_blocked or self.dialog_blocked or self.discarded_unsaved):
            try:
                page.wait_for_timeout(50)
            except Exception:  # noqa: BLE001
                break

    def _navigate(self, page: Any, verb: str, run: Callable[[], Any], timeout: float, target_url: str | None = None) -> None:
        """Run a navigation; a dismissed beforeunload becomes unsaved_changes, an accepted one is waited for."""
        try:
            run()
        except Exception as exc:  # noqa: BLE001 - classified below
            self._drain_events(page)
            self._check_blocks()
            if self.discarded_unsaved:
                # Chromium cancelled the first request, showed the dialog, and restarted the navigation on accept.
                try:
                    page.wait_for_load_state("load", timeout=self._timeout_ms(timeout))
                except Exception:  # noqa: BLE001
                    pass
                if target_url and page.url != target_url:
                    try:
                        page.goto(target_url, wait_until="load", timeout=self._timeout_ms(timeout))
                    except Exception as retry_exc:  # noqa: BLE001
                        raise self._classify(retry_exc, verb) from retry_exc
                return
            if self._is_timeout(exc):
                raise _tag(RuntimeError(f"{verb} did not finish within {timeout:g}s."), ERR_TIMEOUT, hint=_hint("wait_timeout", self.profile)) from exc
            raise self._classify(exc, verb) from exc
        self._drain_events(page, budget=0.15)
        self._check_blocks()

    def h_goto(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = args["url"]
        page = self.ctx.new_page() if args.get("new_tab") else self.page()
        if args.get("new_tab"):
            self._track_page(page)
            self.current = page
        ms = self._timeout_ms(timeout)
        self._navigate(page, "goto", lambda: page.goto(url, wait_until=args.get("wait") or "load", timeout=ms), timeout, target_url=url)
        loading = self._settle(page)
        host = (urllib.parse.urlsplit(url).hostname or "").lower() or None
        return self.snap_envelope(page, navigated=True, requested_host=host, loading=loading, max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def h_snapshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        return self.snap_envelope(page, full=bool(args.get("full")), find=args.get("find"), scope=args.get("ref"),
                                  max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def _do_action(self, args: dict[str, Any], timeout: float, action: str, run: Callable[[Any, Any], Any], *,
                   guard: bool = False, value: bool = False, allow_keys: Sequence[str] | None = None) -> dict[str, Any]:
        page = self.page()
        before_lines = self._before_lines(page)
        target = args.get("target")
        locator = self.resolve(page, target) if target else None
        target_line = find_line(before_lines, normalize_target(target)) if target else None
        masked = False
        if guard:
            secret = self._is_secret(page, locator) if locator is not None else (allow_keys is not None and self._focused_is_secret(page))
            if secret:
                if allow_keys is not None and args.get("key") in SAFE_KEYS_ON_SECRET:
                    masked = True
                else:
                    raise _tag(ValueError("This field takes a password or one-time code; the tool never types those."), ERR_GUARDED,
                               hint=_hint("guarded_key" if allow_keys is not None else "guarded_secret", self.profile))
        nav_before = self._nav_signature(page)
        try:
            run(page, locator)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            self._check_blocks()
            raise self._classify(exc, action) from exc
        self._check_blocks()
        page = self.page()  # a popup may have become current; settle and report that page
        loading = self._settle(page, cap=3.0)
        navigated = self._navigated_since(nav_before)
        value_after = self._value_of(locator, masked) if (value and locator is not None) else None
        return self.act_envelope(page, action, target_line, before_lines, navigated=navigated, loading=loading,
                                 value_after=value_after, max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES,
                                 force_full=bool(args.get("snapshot")))

    def h_click(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)

        def run(page: Any, loc: Any) -> None:
            if args.get("double"):
                loc.dblclick(timeout=ms)
            else:
                loc.click(timeout=ms, button="right" if args.get("right") else "left")
        return self._do_action(args, timeout, "click", run)

    def h_fill(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)

        def run(page: Any, loc: Any) -> None:
            loc.fill(args.get("text") or "", timeout=ms)
            if args.get("enter"):
                loc.press("Enter", timeout=ms)
        return self._do_action(args, timeout, "fill", run, guard=True, value=True)

    def h_type(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)

        def run(page: Any, loc: Any) -> None:
            if not args.get("append"):
                loc.fill("", timeout=ms)
            loc.press_sequentially(args.get("text") or "", delay=int(args.get("delay") or 30), timeout=ms)
            if args.get("enter"):
                loc.press("Enter", timeout=ms)
        return self._do_action(args, timeout, "type", run, guard=True, value=True)

    def h_press(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        key = args.get("key") or ""
        if not key:
            raise _tag(ValueError("press needs a key name such as Enter, Tab, or Control+a."), ERR_USAGE)

        def run(page: Any, loc: Any) -> None:
            if loc is not None:
                loc.press(key, timeout=ms)
            else:
                page.keyboard.press(key)
        return self._do_action(args, timeout, "press", run, guard=True, allow_keys=list(SAFE_KEYS_ON_SECRET), value=bool(args.get("target")))

    def h_select(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        wanted = [str(v) for v in (args.get("values") or []) if str(v).strip()]
        if not wanted:
            raise _tag(ValueError("select needs at least one value."), ERR_USAGE)
        chosen_holder: dict[str, Any] = {}

        def run(page: Any, loc: Any) -> None:
            try:
                options = loc.evaluate(OPTIONS_JS, timeout=ms)
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc, "select") from exc
            if options is None:
                raise _tag(ValueError("The target is not a <select> element."), ERR_ACTION_FAILED, hint=_hint("select_not_select", self.profile))
            values = []
            labels = []
            for want in wanted:
                match = next((o for o in options if str(o[0]).strip().lower() == want.strip().lower()), None) or \
                    next((o for o in options if str(o[1]).strip().lower() == want.strip().lower()), None)
                if match is None:
                    raise _tag(ValueError(f"No option matches {want!r}."), ERR_NOT_FOUND, hint=_hint("select_not_found", self.profile),
                               details={"options": [sanitize_text(o[0], 120) for o in options][:100]})
                values.append(str(match[1]))
                labels.append(sanitize_text(match[0], 120))
            loc.select_option(value=values, timeout=ms)
            chosen_holder["selected"] = labels
        env = self._do_action(args, timeout, "select", run, guard=True, value=True)
        env["selected"] = chosen_holder.get("selected", [])
        return env

    def h_check(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        return self._do_action(args, timeout, "check", lambda page, loc: loc.check(timeout=ms), value=True)

    def h_uncheck(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        return self._do_action(args, timeout, "uncheck", lambda page, loc: loc.uncheck(timeout=ms), value=True)

    def h_hover(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        return self._do_action(args, timeout, "hover", lambda page, loc: loc.hover(timeout=ms))

    def h_scroll(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        delta = int(args.get("down") or 0) - int(args.get("up") or 0)

        def run(page: Any, loc: Any) -> None:
            if loc is not None:
                loc.scroll_into_view_if_needed(timeout=ms)
            else:
                page.mouse.wheel(0, delta or 600)
        return self._do_action(args, timeout, "scroll", run)

    def h_upload(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        files = list(args.get("files") or [])

        def run(page: Any, loc: Any) -> None:
            try:
                is_file_input = loc.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'", timeout=ms)
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc, "upload") from exc
            if is_file_input:
                loc.set_input_files(files, timeout=ms)
            else:
                with page.expect_file_chooser(timeout=ms) as chooser:
                    loc.click(timeout=ms)
                chooser.value.set_files(files)
        env = self._do_action(args, timeout, "upload", run)
        env["files"] = [str(f).replace("\\", "/") for f in files]
        return env

    def h_text(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        max_chars = int(args.get("max_chars") or DEFAULT_TEXT_CHARS)
        ms = self._timeout_ms(timeout)
        chunks: list[str] = []
        try:
            if args.get("target"):
                chunks.append(self.resolve(page, args["target"]).inner_text(timeout=ms))
            else:
                for frame in page.frames:
                    try:
                        body = frame.locator("body").inner_text(timeout=ms)
                    except Exception:  # noqa: BLE001
                        continue
                    label = frame.name or ("(main)" if frame is page.main_frame else redact_url(frame.url))
                    chunks.append(f"--- frame: {sanitize_text(label, 80)} ---\n{body}" if frame is not page.main_frame else body)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "text") from exc
        text = CONTROL_CHARS_RE.sub("", "\n\n".join(chunks))
        truncated = len(text) > max_chars
        env = self._base_fields(page)
        env.update({"chars": len(text), "truncated": truncated, "untrusted": UNTRUSTED_KEYS, "text": text[:max_chars]})
        return env

    def h_value(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        loc = self.resolve(page, args.get("target") or "")
        masked = self._is_secret(page, loc)
        env = self._base_fields(page)
        env.update({"target": normalize_target(args.get("target") or ""), "masked": masked, "value": self._value_of(loc, masked), "untrusted": UNTRUSTED_KEYS})
        return env

    def h_screenshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        ms = self._timeout_ms(timeout)
        path = Path(args.get("path") or _unique_path(self.paths.shots, f"{self.profile}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if args.get("target"):
                self.resolve(page, args["target"]).screenshot(path=str(path), timeout=ms)
            else:
                page.screenshot(path=str(path), full_page=bool(args.get("full_page")), timeout=ms, scale="css")
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "screenshot") from exc
        width = height = None
        try:
            head = path.read_bytes()[:24]
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", head[16:24])
        except OSError:
            pass
        env = self._base_fields(page)
        env.update({"path": str(path).replace("\\", "/"), "bytes": path.stat().st_size if path.exists() else 0, "width": width, "height": height})
        return env

    def h_wait(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        conditions = {k: args.get(k) for k in ("url_contains", "text", "ref", "selector", "signed_in", "seconds") if args.get(k) not in (None, "", 0)}
        if not conditions:
            raise _tag(ValueError("wait needs a condition."), ERR_USAGE, hint=_hint("no_wait_condition", self.profile))
        started = _now()
        deadline = started + timeout
        satisfied: list[str] = []
        while True:
            page = self.page()
            satisfied = []
            ok = True
            if "seconds" in conditions:
                if _now() - started >= float(conditions["seconds"]):
                    satisfied.append("seconds")
                else:
                    ok = False
            if ok and "url_contains" in conditions:
                if str(conditions["url_contains"]).lower() in page.url.lower():
                    satisfied.append("url_contains")
                else:
                    ok = False
            if ok and "signed_in" in conditions:
                if self._signed_in(page, str(conditions["signed_in"])):
                    satisfied.append("signed_in")
                else:
                    ok = False
            if ok and "text" in conditions:
                if self._page_has_text(page, str(conditions["text"])):
                    satisfied.append("text")
                else:
                    ok = False
            if ok and "ref" in conditions:
                try:
                    loc = self.resolve(page, str(conditions["ref"]))
                    if loc.count() > 0 and loc.first.is_visible():
                        satisfied.append("ref")
                    else:
                        ok = False
                except (ValueError, RuntimeError):
                    ok = False
                except Exception:  # noqa: BLE001
                    ok = False
            if ok and "selector" in conditions:
                try:
                    if any(f.locator(str(conditions["selector"])).count() > 0 for f in page.frames):
                        satisfied.append("selector")
                    else:
                        ok = False
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                break
            if _now() >= deadline:
                raise _tag(RuntimeError(f"Still waiting after {timeout:g}s (url {redact_url(page.url)})."), ERR_TIMEOUT, hint=_hint("wait_timeout", self.profile),
                           details={"pending": [k for k in conditions if k not in satisfied]})
            try:
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                _sleep(0.5)
            self.last_activity = _now()
        loading = self._settle(page, cap=2.0)
        return self.snap_envelope(page, loading=loading, extra={"waited_s": round(_now() - started, 1), "satisfied": satisfied},
                                  max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def _signed_in(self, page: Any, host: str) -> bool:
        parts = urllib.parse.urlsplit(page.url)
        current = (parts.hostname or "").lower()
        host = host.lower().strip()
        if not current or not (current == host or current.endswith("." + host)):
            return False
        if SIGNIN_PATH_RE.search(parts.path or "") or _is_idp_host(current):
            return False
        return "password_field" not in self._sign_in_reasons(page)

    def _page_has_text(self, page: Any, text: str) -> bool:
        needle = text.lower()
        for frame in page.frames:
            try:
                if needle in frame.locator("body").inner_text(timeout=1000).lower():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def h_back(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        ms = self._timeout_ms(timeout)
        self._navigate(page, "back", lambda: page.go_back(timeout=ms, wait_until="load"), timeout)
        loading = self._settle(page)
        return self.snap_envelope(page, navigated=True, loading=loading, max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def h_reload(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        ms = self._timeout_ms(timeout)
        self._navigate(page, "reload", lambda: page.reload(timeout=ms, wait_until="load"), timeout)
        loading = self._settle(page)
        return self.snap_envelope(page, navigated=True, loading=loading, max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def h_tabs(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        return {"profile": self.profile, "current": self._tab_index(self.current), "tabs": self._tabs(), "untrusted": UNTRUSTED_KEYS}

    def h_tab(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        index = int(args.get("index") or 0)
        if index < 1 or index > len(self.pages):
            raise _tag(ValueError(f"No tab {index}; there are {len(self.pages)}."), ERR_NOT_FOUND, hint=_hint("tab_index", self.profile))
        page = self.pages[index - 1]
        if args.get("close"):
            if len(self.pages) == 1:
                raise _tag(ValueError("Refusing to close the last tab."), ERR_VALIDATION, hint=_hint("last_tab", self.profile))
            try:
                page.close(run_before_unload=True)
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc, "tab") from exc
            self._drain_events(self.page(), budget=0.3)
            self._check_blocks()
            page = self.page()
        else:
            self.current = page
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        return self.snap_envelope(page, max_bytes=args.get("max_bytes") or DEFAULT_MAX_BYTES)

    def h_downloads(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        out = []
        for rec in self.downloads:
            if rec.saved is None:
                try:
                    dest = _unique_path(self.paths.downloads, rec.suggested)
                    rec.download.save_as(str(dest))
                    rec.saved = str(dest).replace("\\", "/")
                except Exception as exc:  # noqa: BLE001
                    out.append({"file": rec.suggested, "state": "failed", "error": sanitize_text(str(exc).splitlines()[0] if str(exc) else "", 200)})
                    continue
            out.append({"file": rec.suggested, "state": "saved", "path": rec.saved, "seen": rec.seen})
        return {"profile": self.profile, "downloads": out, "folder": str(self.paths.downloads).replace("\\", "/")}

    def h_eval(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self.config.get("eval") is False:
            raise _tag(ValueError("eval is disabled by config.json."), ERR_GUARDED, hint=_hint("eval_disabled"))
        page = self.page()
        js = args.get("js") or ""
        if not js.strip():
            raise _tag(ValueError("eval needs a JavaScript expression."), ERR_USAGE)
        ms = self._timeout_ms(timeout)
        try:
            if args.get("target"):
                value = self.resolve(page, args["target"]).evaluate(js, timeout=ms)
            elif args.get("frame"):
                frame = page.frame(name=args["frame"])
                if frame is None:
                    raise _tag(ValueError(f"No frame named {args['frame']!r}."), ERR_NOT_FOUND)
                value = frame.evaluate(js)
            else:
                value = page.evaluate(js)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "eval") from exc
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            value = sanitize_text(repr(value), 2000)
        env = self._base_fields(page)
        env.update({"value": value, "untrusted": UNTRUSTED_KEYS})
        return env

    # -- serve loop ----------------------------------------------------------------------

    def write_session(self, port: int) -> None:
        existing = _read_session(self.paths) or {}  # the client adds `launcher` once; heartbeats must keep it
        _write_json_atomic(self.paths.session, {
            "schema": SESSION_SCHEMA, "profile": self.profile, "pid": os.getpid(), "port": port, "token": self.token,
            "launcher": existing.get("launcher"),
            "browser": self.launch.get("browser"), "browser_version": self.browser_version, "headless": bool(self.launch.get("headless")),
            "user_data_dir": str(self.paths.user_data), "log": str(self.paths.log), "started": self.started_at,
            "heartbeat": _utc_now(), "busy": ({"cmd": self.current_cmd, "since": self.cmd_started} if self.current_cmd else None),
            "code_hash": self.launch.get("code_hash"), "tool_version": TOOL_VERSION, "python": sys.executable,
            "playwright": _playwright_version(),
        })

    started_at: str = ""

    def pump(self) -> None:
        page = self.current
        if page is None:
            return
        try:
            page.wait_for_timeout(1)
        except Exception as exc:  # noqa: BLE001
            if "closed" in str(exc).lower():
                self._on_page_closed(page)


PREFERENCE_DEFAULTS: dict[str, Any] = {
    "credentials_enable_service": False,
    "profile": {"password_manager_enabled": False},
    "autofill": {"credit_card_enabled": False, "profile_enabled": False},
    "download": {"prompt_for_download": False},
}


def _seed_preferences(user_data: Path) -> None:
    """Keep Edge/Chrome credential and autofill storage off in the profile; mark a clean exit on every launch."""
    default = user_data / "Default"
    prefs = default / "Preferences"
    data: dict[str, Any] = {}
    if prefs.is_file():
        try:
            loaded = json.loads(prefs.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}
    for key, value in PREFERENCE_DEFAULTS.items():
        if isinstance(value, dict):
            section = data.setdefault(key, {})
            if isinstance(section, dict):
                for inner, inner_value in value.items():
                    section.setdefault(inner, inner_value)
        else:
            data.setdefault(key, value)
    profile = data.setdefault("profile", {})
    profile["exit_type"] = "Normal"
    profile["exited_cleanly"] = True
    try:
        default.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(prefs, data)
    except OSError:
        pass


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _write_startup_error(paths: ProfilePaths, error_class: str, message: str, hint: str | None) -> None:
    """The client reads this instead of guessing from an append-mode log that may carry old runs."""
    try:
        _write_json_atomic(paths.dir / "startup_error.json", {"class": error_class, "message": sanitize_text(message, 600), "hint": hint})
    except OSError:
        pass


def _take_startup_error(paths: ProfilePaths) -> dict[str, Any] | None:
    path = paths.dir / "startup_error.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # noqa: D102
        daemon: Daemon = self.server.daemon  # type: ignore[attr-defined]
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return
        self.request.settimeout(FIRST_READ_TIMEOUT)
        try:
            line = self.rfile.readline(REQUEST_CAP + 1)
        except (OSError, ValueError):
            return
        if not line or len(line) > REQUEST_CAP:
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            self._reply({"ok": False, "error": {"class": ERR_USAGE, "message": "Malformed request."}})
            return
        token = str(message.get("token") or "")
        if not hmac.compare_digest(token, daemon.token):
            daemon.dlog("rejected a request with a bad token")
            return
        cmd = str(message.get("cmd") or "")
        args = message.get("args") or {}
        timeout = float(message.get("timeout") or DEFAULT_TIMEOUT)
        self.request.settimeout(timeout + CLIENT_GRACE + 30)
        started = _now()
        try:
            result = daemon.dispatch(cmd, args, timeout)
            reply: dict[str, Any] = {"ok": True, "result": result}
            daemon.dlog(f"{cmd} ok {round((_now() - started) * 1000)}ms")
        except (ValueError, RuntimeError) as exc:
            reply = {"ok": False, "error": {"class": getattr(exc, "error_class", ERR_DAEMON), "message": str(exc),
                                            "hint": getattr(exc, "hint", None), "details": getattr(exc, "details", None)}}
            daemon.dlog(f"{cmd} error {reply['error']['class']}: {str(exc)[:200]}")
        except Exception as exc:  # noqa: BLE001 - never let a handler take the daemon down
            daemon.dlog(f"{cmd} crashed: {traceback.format_exc()[-800:]}")
            reply = {"ok": False, "error": {"class": ERR_DAEMON, "message": sanitize_text(f"{type(exc).__name__}: {exc}", 300),
                                            "hint": _hint("daemon_unresponsive", daemon.profile)}}
        self._reply(reply)

    def _reply(self, reply: dict[str, Any]) -> None:
        try:
            self.wfile.write((json.dumps(reply, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (OSError, ValueError):
            pass


class _Server(socketserver.TCPServer):
    allow_reuse_address = False
    daemon: Daemon


def serve(launch_file: Path) -> int:
    launch = json.loads(launch_file.read_text(encoding="utf-8"))
    daemon = Daemon(launch)
    log_path = Path(launch["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115 - lives as long as the daemon
    sys.stdout = sys.stderr = log
    daemon.log_handle = log
    daemon.dlog(f"daemon starting pid {os.getpid()} profile {daemon.profile} python {sys.executable}")
    lock = _try_lock(daemon.paths.lock)
    if lock is None:
        daemon.dlog("another daemon holds the session lock; exiting")
        return 1
    server: _Server | None = None
    exit_code = 0
    try:
        daemon.started_at = _utc_now()
        daemon.start_browser()
        server = _Server(("127.0.0.1", 0), _Handler)
        server.daemon = daemon
        server.timeout = 1.0
        port = server.server_address[1]
        daemon.port = port
        daemon.write_session(port)
        daemon.dlog(f"listening on 127.0.0.1:{port} browser {daemon.browser_version}")
        last_heartbeat = _now()
        idle_limit = float(launch.get("idle_seconds") or 0)
        while daemon.stop_reason is None:
            server.handle_request()
            daemon.pump()
            if _now() - last_heartbeat >= HEARTBEAT_SECONDS:
                daemon.write_session(port)
                last_heartbeat = _now()
            if idle_limit and _now() - daemon.last_activity > idle_limit:
                daemon._mark_stop("idle")
        daemon.dlog(f"stopping: {daemon.stop_reason}")
    except (ValueError, RuntimeError) as exc:
        daemon.dlog(f"fatal {getattr(exc, 'error_class', '')}: {exc}")
        _write_startup_error(daemon.paths, getattr(exc, "error_class", ERR_LAUNCH), str(exc), getattr(exc, "hint", None))
        exit_code = 1
    except Exception as exc:  # noqa: BLE001
        daemon.dlog("fatal: " + traceback.format_exc()[-1500:])
        _write_startup_error(daemon.paths, ERR_LAUNCH, f"{type(exc).__name__}: {exc}", None)
        exit_code = 1
    finally:
        daemon.close()
        if server is not None:
            try:
                server.server_close()
            except OSError:
                pass
        daemon.paths.session.unlink(missing_ok=True)
        _release_lock(lock)
        daemon.dlog("exited")
    return exit_code


# ---------------------------------------------------------------------------
# Client verbs: start / stop / status / clean / doctor / focus


def _launch_record(paths: ProfilePaths, args: argparse.Namespace, token: str) -> dict[str, Any]:
    browser = (getattr(args, "browser", None) or os.getenv("AGENT_BROWSER_BROWSER") or "msedge").lower()
    if browser not in BROWSER_EXE:
        raise _tag(ValueError(f"--browser must be msedge or chrome, not {browser!r}"), ERR_VALIDATION)
    exe = _check_browser_exe(getattr(args, "exe", None) or os.getenv("AGENT_BROWSER_EXE") or None)
    headless = bool(getattr(args, "headless", False)) or _env_flag("AGENT_BROWSER_HEADLESS")
    return {
        "profile": paths.profile, "home": str(paths.root), "log": str(paths.log), "token": token,
        "browser": browser, "exe": exe, "headless": headless,
        "window_size": getattr(args, "window_size", None) or DEFAULT_WINDOW_SIZE,
        "code_hash": _code_hash(), "config": _load_config(),
        "idle_seconds": _env_float("AGENT_BROWSER_IDLE_SECONDS", 0.0),
        "tool_version": TOOL_VERSION,
    }


def start_session(paths: ProfilePaths, args: argparse.Namespace, timeout: float = START_TIMEOUT) -> dict[str, Any]:
    state, session = _liveness(paths)
    if state == "running" and session is not None:
        pong = _ping(session) or {}
        return {"profile": paths.profile, "status": "already_running", "pid": session.get("pid"), "port": session.get("port"),
                "browser": session.get("browser"), "browser_version": session.get("browser_version"), "headless": session.get("headless"),
                "current_tab": pong.get("current_tab"), "url": pong.get("url"), "launcher": session.get("launcher"), "log": session.get("log")}
    if state == "unresponsive":
        raise _tag(RuntimeError("A daemon holds the lock but does not answer."), ERR_DAEMON_UNRESPONSIVE, hint=_hint("daemon_unresponsive", paths.profile))
    if state == "starting":
        session = _require_running(paths, "start")
        return start_session(paths, args, timeout)
    if state == "stale":
        paths.session.unlink(missing_ok=True)
        killed = _kill_browsers_using(paths.user_data)
        if killed:
            _log(f"killed stale processes {killed}")
    paths.dir.mkdir(parents=True, exist_ok=True)
    (paths.dir / "startup_error.json").unlink(missing_ok=True)
    token = secrets.token_hex(32)
    launch = _launch_record(paths, args, token)
    _write_json_atomic(paths.launch, launch)
    argv = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "serve", "--launch-file", str(paths.launch)]
    pid, launcher = _spawn_daemon(argv, paths.log, paths.dir)
    deadline = _now() + timeout
    while _now() < deadline:
        _sleep(0.1)
        session = _read_session(paths)
        if session is not None and _ping(session) is not None:
            session["launcher"] = launcher
            _write_json_atomic(paths.session, session)
            return {"profile": paths.profile, "status": "started", "pid": session.get("pid"), "port": session.get("port"),
                    "browser": session.get("browser"), "browser_version": session.get("browser_version"), "headless": session.get("headless"),
                    "user_data_dir": str(paths.user_data), "launcher": launcher, "log": str(paths.log), "window": _hint("window")}
        if not _pid_alive(pid):
            tail = _tail(paths.log)
            reported = _take_startup_error(paths) or {}
            cls = str(reported.get("class") or "")
            # startup_error.json is authoritative; the log tail is the fallback for a daemon that died before writing it.
            if cls == ERR_PLAYWRIGHT_MISSING or "No module named 'playwright'" in tail or "Playwright is not installed" in tail:
                raise _tag(RuntimeError("The daemon could not import Playwright."), ERR_PLAYWRIGHT_MISSING, hint=_hint("playwright_missing"), details={"log": tail})
            if cls == ERR_PROFILE_IN_USE or (not cls and "already in use" in tail):
                raise _tag(RuntimeError("The profile folder is already in use."), ERR_PROFILE_IN_USE, hint=_hint("profile_in_use", paths.profile), details={"log": tail})
            if cls in (ERR_GUARDED, ERR_BROWSER_NOT_FOUND, ERR_CONFIG):
                exc: Exception = ValueError(reported.get("message") or "The daemon refused to start.") if cls == ERR_GUARDED else RuntimeError(reported.get("message") or "The daemon could not start.")
                raise _tag(exc, cls, hint=reported.get("hint"), details={"log": tail})
            blockers = policy_blockers(read_policies(launch["browser"]))
            if blockers:
                raise _tag(RuntimeError("Browser policy blocks automation: " + "; ".join(blockers)), ERR_POLICY, details={"log": tail})
            message = reported.get("message") or f"The daemon exited before the browser was ready (log: {paths.log})."
            raise _tag(RuntimeError(message), ERR_LAUNCH, hint=_hint("launch", paths.profile), details={"log": tail})
    _kill_tree(pid)
    raise _tag(RuntimeError(f"The browser did not become ready within {timeout:g}s (log: {paths.log})."), ERR_LAUNCH, hint=_hint("launch", paths.profile), details={"log": _tail(paths.log)})


def stop_session(paths: ProfilePaths, force: bool = False) -> dict[str, Any]:
    state, session = _liveness(paths)
    result: dict[str, Any] = {"profile": paths.profile, "stopped": False, "killed_pids": []}
    pid = int(session.get("pid") or 0) if session else 0
    if state == "running" and session is not None and not force:
        try:
            _request(session, "stop", {}, timeout=STOP_GRACE)
        except (RuntimeError, ValueError) as exc:
            _log(f"stop request failed: {exc}")
        deadline = _now() + STOP_GRACE
        while _now() < deadline and _pid_alive(pid):
            _sleep(0.2)
        result["stopped"] = True
    # The recorded pid is only trusted while the daemon holds the lock (running/unresponsive/starting);
    # a stale record may name a pid that the OS has since given to an unrelated process.
    if pid and state in ("running", "unresponsive", "starting") and _pid_alive(pid):
        _kill_tree(pid)
        result["killed_pids"].append(pid)
        result["stopped"] = True
    if state in ("running", "unresponsive", "stale", "starting"):
        # Chromium's helper processes (--type=...) follow the main process out; give them a moment so a
        # `status`/`clean` right after `stop` does not see a browser that is still winding down.
        deadline = _now() + 4.0
        while _now() < deadline and any(p["name"].lower() in ("msedge.exe", "chrome.exe") for p in _processes_using(str(paths.user_data))):
            _sleep(0.3)
        result["killed_pids"].extend(k for k in _kill_browsers_using(paths.user_data) if k not in result["killed_pids"])
        result["stopped"] = True
    paths.session.unlink(missing_ok=True)
    return result


def status_session(paths: ProfilePaths) -> dict[str, Any]:
    state, session = _liveness(paths)
    if state != "running" or session is None:
        reason = {"none": "no_session", "stale": "pid_dead", "unresponsive": "unresponsive", "starting": "starting"}[state]
        hint = _hint("daemon_unresponsive" if state == "unresponsive" else "not_running", paths.profile)
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
           "busy": session.get("busy"), "stale_code": session.get("code_hash") != _code_hash(), "launcher": session.get("launcher"),
           "current_tab": pong.get("current_tab"), "url": pong.get("url"), "title": pong.get("title"), "tabs": pong.get("tabs", []),
           "log": session.get("log"), "untrusted": UNTRUSTED_KEYS}
    if uptime and uptime > SESSION_AGE_WARN_SECONDS:
        out["note"] = f"This browser has been open for {uptime // 3600} hours. Run: {TOOL_NAME} stop --profile {paths.profile} when the task is done."
    if out["stale_code"]:
        out["note"] = _hint("stale_code", paths.profile)
    return out


def clean(profile: str | None = None, purge: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    root = _home() / "profiles"
    out: dict[str, Any] = {"home": str(_home()), "stale_sessions_removed": [], "killed_pids": [], "artifacts_removed": 0, "profiles_purged": [], "dry_run": dry_run}
    if not root.is_dir():
        return out
    cutoff = time.time() - ARTIFACT_RETENTION_DAYS * 86400
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not _is_profile_name(entry.name):
            continue
        if profile and entry.name != profile and purge != entry.name:
            continue
        paths = ProfilePaths(entry.name, _home())
        state, _session = _liveness(paths)
        if state == "stale":
            out["stale_sessions_removed"].append(entry.name)
            if not dry_run:
                paths.session.unlink(missing_ok=True)
        if state in ("stale", "none"):
            out["killed_pids"].extend(_kill_browsers_using(paths.user_data, dry_run=dry_run))
        for folder in (paths.shots, paths.downloads):
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
            if state in ("running", "starting", "unresponsive"):
                raise _tag(ValueError(f"Profile {entry.name!r} is running; refusing to purge."), ERR_VALIDATION, hint=_hint("purge_running", entry.name))
            out["profiles_purged"].append(entry.name)
            if not dry_run:
                _rmtree_retry(paths.dir)
    if purge and purge not in out["profiles_purged"]:
        raise _tag(ValueError(f"No profile named {purge!r}."), ERR_NOT_FOUND, hint=_hint("no_such_profile"))
    return out


def _check(checks: list[dict[str, Any]], cid: str, status: str, detail: str, hint: str | None = None) -> None:
    checks.append({"id": cid, "status": status, "detail": detail, "hint": hint})


def doctor(browser: str | None = None, exe: str | None = None, live: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    _check(checks, "python", "ok", f"{sys.version.split()[0]} at {sys.executable}")
    version = _playwright_version()
    if version:
        line = ".".join(version.split(".")[:2])
        _check(checks, "playwright_import", "ok", f"playwright {version}")
        _check(checks, "playwright_version", "ok" if line in TESTED_PLAYWRIGHT else "warn", version,
               None if line in TESTED_PLAYWRIGHT else f"Tested with {', '.join(TESTED_PLAYWRIGHT)}; run doctor --live after an upgrade.")
    else:
        _check(checks, "playwright_import", "fail", f"playwright is not installed for {sys.executable}", _hint("playwright_missing"))
    script = shutil.which(TOOL_NAME)
    all_scripts = []
    if _is_windows():
        try:
            out = subprocess.run(["where.exe", TOOL_NAME], capture_output=True, text=True, timeout=10, check=False).stdout
            all_scripts = [l.strip() for l in out.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            all_scripts = []
    if script:
        dup = len({Path(p).parent.resolve() for p in all_scripts}) > 1
        _check(checks, "console_script", "warn" if dup else "ok", script + (f" (+{len(all_scripts) - 1} more on PATH)" if dup else ""),
               "Two agent-browser executables are on PATH (the Vercel npm CLI shares the name); check `where agent-browser`." if dup else None)
    else:
        _check(checks, "console_script", "warn", "agent-browser is not on PATH", f"Use `python {Path(__file__).name} <verb>` or install: python -m pip install --user -e agent-browser")
    kind = (browser or os.getenv("AGENT_BROWSER_BROWSER") or "msedge").lower()
    try:
        found = find_browser(kind, exe or os.getenv("AGENT_BROWSER_EXE"))
        _check(checks, "browser_found", "ok", f"{kind}: {found}")
        _check(checks, "browser_version", "ok", browser_version_from_path(found) or "unknown")
    except (RuntimeError, ValueError) as exc:
        _check(checks, "browser_found", "fail", str(exc), getattr(exc, "hint", None))
    policies = read_policies(kind)
    if not policies:
        _check(checks, "policies", "ok", f"no {kind} policies configured")
    for name, info in policies.items():
        _check(checks, f"policy_{name}", info["status"], f"{name}={info['value']!r} ({info['hive']}): {info['label']}")
    try:
        home = _home()
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _check(checks, "home_writable", "ok", str(home))
    except OSError as exc:
        _check(checks, "home_writable", "fail", str(exc), _hint("profile_path"))
    length = len(str(_home() / "profiles" / "default" / "user-data"))
    _check(checks, "profile_path_length", "ok" if length <= MAX_PROFILE_PATH_CHARS else "fail", f"{length} chars", None if length <= MAX_PROFILE_PATH_CHARS else _hint("profile_path"))
    if _is_windows():
        lp = _registry_value(r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled")
        _check(checks, "long_paths_enabled", "ok" if lp in (1, "1") else "warn", str(lp), None if lp in (1, "1") else "Long paths are off; keep AGENT_BROWSER_HOME short.")
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            _check(checks, "port_bind", "ok", f"127.0.0.1:{sock.getsockname()[1]}")
    except OSError as exc:
        _check(checks, "port_bind", "fail", str(exc))
    job = _job_info()
    # pip's console-script launcher puts python in a Job with KILL_ON_JOB_CLOSE, but also SILENT_BREAKAWAY_OK,
    # so a detached daemon leaves that Job on its own. Only a kill-on-close Job without any breakaway right is a problem.
    trapped = bool(job.get("kill_on_job_close")) and not any(f in (job.get("flags") or []) for f in ("BREAKAWAY_OK", "SILENT_BREAKAWAY_OK"))
    _check(checks, "job_object", "warn" if trapped else "ok", json.dumps(job),
           "This shell's Job kills children on close and forbids breakaway; set AGENT_BROWSER_LAUNCHER=wmi." if trapped else None)
    config = _home() / "config.json"
    if config.is_file():
        try:
            _check(checks, "config_json", "ok", json.dumps(_load_config()))
        except RuntimeError as exc:
            _check(checks, "config_json", "fail", str(exc))
    else:
        _check(checks, "config_json", "ok", "absent (eval on, no host allowlist, uploads from cwd/downloads/shots)")
    skill = Path.home() / ".claude" / "skills" / TOOL_NAME / "SKILL.md"
    repo_skill = Path(__file__).resolve().parents[1] / "agentic-ide-setup" / "profile" / "claude" / "skills" / TOOL_NAME / "SKILL.md"
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
    paths = ProfilePaths("_doctor", _home())
    ns = argparse.Namespace(browser=None, exe=None, headless=True, window_size=None)
    try:
        stop_session(paths, force=True)
        started = start_session(paths, ns, timeout=START_TIMEOUT)
        _check(checks, "live_boot", "ok", f"{started.get('browser_version')} via {started.get('launcher')}")
        session = _require_running(paths, "doctor")
        env = _request(session, "goto", {"url": 'data:text/html,<title>doctor</title><button id="b">ok</button><iframe name="gsft_main" srcdoc="<button>inner</button>"></iframe>'}, timeout=GOTO_TIMEOUT)
        lines = env.get("snapshot") or []
        has_main = any("[ref=e" in l for l in lines)
        has_frame = any(re.search(r"\[ref=f\d+e", l) for l in lines)
        _check(checks, "live_snapshot", "ok" if has_main and has_frame else "fail", f"{len(lines)} lines; main refs {has_main}; frame refs {has_frame}",
               None if has_main and has_frame else "Playwright's ai snapshot did not produce refs; check the Playwright version.")
        ref = next((re.search(r"\[ref=(e\d+)\]", l).group(1) for l in lines if 'button "ok"' in l), None)  # type: ignore[union-attr]
        if ref:
            _request(session, "click", {"target": ref}, timeout=DEFAULT_TIMEOUT)
            _check(checks, "live_click", "ok", f"clicked {ref}")
        pid = int(session.get("pid") or 0)
        stop_session(paths)
        alive = _pid_alive(pid) if pid else False
        leftovers = [p for p in _processes_using(str(paths.user_data)) if p["name"].lower() in BROWSER_PROCESS_NAMES and p["pid"] != os.getpid()]
        _check(checks, "live_teardown", "ok" if not alive and not leftovers else "warn",
               "daemon and browser exited" if not alive and not leftovers else f"daemon alive={alive}, leftovers={[p['pid'] for p in leftovers]}",
               None if not alive and not leftovers else f"Run: {TOOL_NAME} clean --profile _doctor")
    except (RuntimeError, ValueError) as exc:
        booted = any(c["id"] == "live_boot" for c in checks)
        _check(checks, "live_probe" if booted else "live_boot", "fail", str(exc), getattr(exc, "hint", None))
        try:
            stop_session(paths, force=True)
        except (RuntimeError, ValueError):
            pass


def focus_window(paths: ProfilePaths) -> dict[str, Any]:
    if not _is_windows():
        return {"profile": paths.profile, "focused": False, "reason": "windows only"}
    pids = {p["pid"] for p in _processes_using(str(paths.user_data)) if p["name"].lower() in ("msedge.exe", "chrome.exe")}
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
        return {"profile": paths.profile, "focused": ok, "windows": len(found)}
    except Exception as exc:  # noqa: BLE001
        return {"profile": paths.profile, "focused": False, "reason": sanitize_text(str(exc), 200)}


# ---------------------------------------------------------------------------
# CLI


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
                _ARGV_NOTES.append(f"argument {arg!r} was rewritten by Git Bash; interpreted as {fixed!r}")
                break
        out.append(fixed)
    return out


def _timeout_for(args: argparse.Namespace, default: float) -> float:
    value = getattr(args, "timeout", None)
    if value is None:
        value = _env_float("AGENT_BROWSER_TIMEOUT_SECONDS", default) if default == DEFAULT_TIMEOUT else default
    if value < 0:
        raise _tag(ValueError("--timeout must be >= 0"), ERR_VALIDATION)
    return float(value)


def _max_bytes(args: argparse.Namespace) -> int:
    value = getattr(args, "max_bytes", None)
    if value is None:
        value = int(_env_float("AGENT_BROWSER_MAX_BYTES", DEFAULT_MAX_BYTES))
    return max(500, int(value))


def _session_for(args: argparse.Namespace, verb: str, autostart: bool = False) -> tuple[ProfilePaths, dict[str, Any]]:
    profile = _resolve_profile(getattr(args, "profile", None))
    paths = _paths(profile)
    state, session = _liveness(paths)
    if state != "running" and autostart and _env_flag("AGENT_BROWSER_AUTOSTART", True) and state in ("none", "stale"):
        start_session(paths, argparse.Namespace(browser=None, exe=None, headless=False, window_size=None))
    session = _require_running(paths, verb)
    return paths, session


def _finish(result: dict[str, Any], session: dict[str, Any] | None = None) -> dict[str, Any]:
    notes = list(_ARGV_NOTES)
    if session is not None and session.get("code_hash") and session.get("code_hash") != _code_hash():
        notes.append(_hint("stale_code", session.get("profile", DEFAULT_PROFILE)))
    if notes:
        result["note"] = (result.get("note") + " " if result.get("note") else "") + " ".join(notes)
    return result


def _remote(args: argparse.Namespace, verb: str, cmd: str, payload: dict[str, Any], default_timeout: float = DEFAULT_TIMEOUT, autostart: bool = False) -> dict[str, Any]:
    paths, session = _session_for(args, verb, autostart=autostart)
    timeout = _timeout_for(args, default_timeout)
    payload.setdefault("max_bytes", _max_bytes(args))
    if getattr(args, "snapshot", False):
        payload["snapshot"] = True
    return _finish(_request(session, cmd, payload, timeout), session)


def _cmd_start(args: argparse.Namespace) -> Any:
    paths = _paths(_resolve_profile(args.profile, sticky=False))
    result = start_session(paths, args, timeout=_timeout_for(args, START_TIMEOUT))
    if args.url:
        session = _require_running(paths, "start")
        env = _request(session, "goto", {"url": _check_url(args.url, paths, _load_config()), "max_bytes": _max_bytes(args)}, timeout=GOTO_TIMEOUT)
        keep = {"profile", "status", "pid", "port", "launcher", "log", "browser", "browser_version", "headless", "user_data_dir", "window"}
        result.update({k: v for k, v in env.items() if k not in keep})  # the navigation's url/title/snapshot win
    return _finish(result)


def _cmd_stop(args: argparse.Namespace) -> Any:
    if args.all:
        names = _running_profiles()
        root = _home() / "profiles"
        if root.is_dir():
            names = sorted({*names, *[e.name for e in root.iterdir() if e.is_dir() and (e / "session.json").is_file()]})
        return {"stopped": [stop_session(_paths(n), force=args.force) for n in names]}
    paths = _paths(_resolve_profile(args.profile))
    return stop_session(paths, force=args.force)


def _cmd_status(args: argparse.Namespace) -> Any:
    several = not args.profile and not os.getenv("AGENT_BROWSER_PROFILE") and len(_running_profiles()) > 1
    if args.all or several:  # status never fails: with several browsers running it lists them all
        root = _home() / "profiles"
        names = sorted(e.name for e in root.iterdir() if e.is_dir() and _is_profile_name(e.name)) if root.is_dir() else []
        return {"home": str(_home()), "profiles": [status_session(_paths(n)) for n in names]}
    return status_session(_paths(_resolve_profile(args.profile)))


def _cmd_goto(args: argparse.Namespace) -> Any:
    profile = _resolve_profile(args.profile)
    paths = _paths(profile)
    url = _check_url(args.url, paths, _load_config())
    return _remote(args, "goto", "goto", {"url": url, "wait": args.wait, "new_tab": bool(args.new_tab), "discard_changes": bool(args.discard_changes)}, GOTO_TIMEOUT, autostart=True)


def _cmd_snapshot(args: argparse.Namespace) -> Any:
    return _remote(args, "snapshot", "snapshot", {"full": bool(args.full), "find": args.find, "ref": args.ref})


def _cmd_click(args: argparse.Namespace) -> Any:
    return _remote(args, "click", "click", {"target": args.target, "double": bool(args.double), "right": bool(args.right),
                                            "accept_dialog": bool(args.accept_dialog), "dialog_text": args.dialog_text,
                                            "discard_changes": bool(args.discard_changes)})


def _cmd_fill(args: argparse.Namespace) -> Any:
    return _remote(args, "fill", "fill", {"target": args.target, "text": args.text, "enter": bool(args.enter)})


def _cmd_type(args: argparse.Namespace) -> Any:
    return _remote(args, "type", "type", {"target": args.target, "text": args.text, "delay": args.delay, "append": bool(args.append), "enter": bool(args.enter)})


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
    max_chars = args.max_chars if args.max_chars is not None else int(_env_float("AGENT_BROWSER_MAX_CHARS", DEFAULT_TEXT_CHARS))
    return _remote(args, "text", "text", {"target": args.target, "max_chars": max_chars})


def _cmd_value(args: argparse.Namespace) -> Any:
    return _remote(args, "value", "value", {"target": args.target})


def _cmd_screenshot(args: argparse.Namespace) -> Any:
    profile = _resolve_profile(args.profile)
    paths = _paths(profile)
    path = None
    if args.path:
        candidate = Path(args.path)
        if candidate.suffix.lower() != ".png":
            raise _tag(ValueError("--path must end with .png"), ERR_VALIDATION)
        path = str(_check_local_path(args.path, paths, _load_config(), must_exist=False))
    return _remote(args, "screenshot", "screenshot", {"target": args.target, "path": path, "full_page": bool(args.full_page)})


def _cmd_wait(args: argparse.Namespace) -> Any:
    timeout = args.timeout if args.timeout is not None else WAIT_DEFAULT
    if timeout > WAIT_CAP:
        raise _tag(ValueError(f"--timeout for wait is capped at {WAIT_CAP:g} seconds."), ERR_VALIDATION, hint=_hint("wait_cap"))
    args.timeout = timeout
    payload = {"url_contains": args.url_contains, "text": args.text, "ref": args.ref, "selector": args.selector, "signed_in": args.signed_in, "seconds": args.seconds}
    if not any(v not in (None, "", 0) for v in payload.values()):
        raise _tag(ValueError("wait needs a condition."), ERR_USAGE, hint=_hint("no_wait_condition"))
    return _remote(args, "wait", "wait", payload, timeout)


def _cmd_back(args: argparse.Namespace) -> Any:
    return _remote(args, "back", "back", {"discard_changes": bool(args.discard_changes)}, GOTO_TIMEOUT)


def _cmd_reload(args: argparse.Namespace) -> Any:
    return _remote(args, "reload", "reload", {"discard_changes": bool(args.discard_changes)}, GOTO_TIMEOUT)


def _cmd_tabs(args: argparse.Namespace) -> Any:
    return _remote(args, "tabs", "tabs", {})


def _cmd_tab(args: argparse.Namespace) -> Any:
    return _remote(args, "tab", "tab", {"index": args.index, "close": bool(args.close), "discard_changes": bool(args.discard_changes)})


def _cmd_upload(args: argparse.Namespace) -> Any:
    profile = _resolve_profile(args.profile)
    paths = _paths(profile)
    config = _load_config()
    files = [str(_check_local_path(f, paths, config)) for f in args.files]
    return _remote(args, "upload", "upload", {"target": args.target, "files": files})


def _cmd_downloads(args: argparse.Namespace) -> Any:
    return _remote(args, "downloads", "downloads", {}, 60.0)


def _cmd_eval(args: argparse.Namespace) -> Any:
    return _remote(args, "eval", "eval", {"js": args.js, "target": args.ref, "frame": args.frame})


def _cmd_focus(args: argparse.Namespace) -> Any:
    paths = _paths(_resolve_profile(args.profile))
    _require_running(paths, "focus")
    return focus_window(paths)


def _cmd_doctor(args: argparse.Namespace) -> Any:
    return doctor(browser=args.browser, exe=args.exe, live=bool(args.live))


def _cmd_clean(args: argparse.Namespace) -> Any:
    return clean(profile=args.profile, purge=args.purge_profile, dry_run=bool(args.dry_run))


def _cmd_serve(args: argparse.Namespace) -> int:
    return serve(Path(args.launch_file))


_EPILOG = f"""\
examples:
  {TOOL_NAME} goto https://example.com            open (or reuse) the window; prints the page as refs
  {TOOL_NAME} click e12                            act on a ref from the newest snapshot
  {TOOL_NAME} fill f2e16 "new text"                iframe refs (f2e16) work exactly like e12
  {TOOL_NAME} select f2e14 "2 - High"
  {TOOL_NAME} wait --signed-in dev12345.service-now.com   pause while a human signs in
  {TOOL_NAME} text                                 readable page text (all frames)
  {TOOL_NAME} screenshot                           PNG under the profile folder; Read the printed path
  {TOOL_NAME} stop                                 close the window (the sign-in is kept)

output contract:
  success -> JSON on stdout, exit 0. Failure -> {{"error": {{"class", "message", "hint"}}}} on stderr:
  exit 2 = fix the call (stale ref, bad target, guarded), exit 1 = fix the environment or start the
  browser, 124 = still waiting (run the same command again), 130 = interrupted.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description="Drive a real, visible Edge/Chrome window; JSON in and out, refs from snapshots.",
                                     epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
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
    p.add_argument("text")
    p.add_argument("--enter", action="store_true")
    p = add("type", _cmd_type, "type into a field key by key (typeahead fields); clears it first unless --append")
    p.add_argument("target")
    p.add_argument("text")
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
    p.add_argument("js")
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
    return parser


def _emit_error(exc: Exception) -> None:
    default_class = ERR_VALIDATION if isinstance(exc, ValueError) else ERR_DAEMON
    payload: dict[str, Any] = {"error": {"class": getattr(exc, "error_class", default_class), "http_status": None,
                                         "message": str(exc), "hint": getattr(exc, "hint", None)}}
    details = getattr(exc, "details", None)
    if details is not None:
        payload["error"]["details"] = details
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


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
    _ARGV_NOTES.clear()
    args = _build_parser().parse_args(_demangle_msys(raw))
    global VERBOSE
    VERBOSE = bool(getattr(args, "verbose", False))
    _reconfigure_streams()
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        print(f"\n{TOOL_NAME}: interrupted", file=sys.stderr, flush=True)
        return EXIT_INTERRUPT
    except ValueError as exc:
        _emit_error(exc)
        return 2
    except RuntimeError as exc:
        _emit_error(exc)
        return EXIT_TIMEOUT if getattr(exc, "error_class", None) == ERR_TIMEOUT else 1
    if isinstance(result, bool) or not isinstance(result, int):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    return result


if __name__ == "__main__":
    raise SystemExit(main())
