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
  text value screenshot wait back reload tabs tab upload downloads eval doctor clean output
  commit state journal needs-human fill-form table api run diff

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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence




TOOL_NAME = "agent-browser"


TOOL_VERSION = "0.2.0"


CONTRACT_VERSION = 1
PROTOCOL_VERSION = 2


TESTED_PLAYWRIGHT = ("1.61", "1.64")   # aria_snapshot(mode="ai") + aria-ref engine verified on these lines


SESSION_SCHEMA = 1


DEFAULT_PROFILE = "default"


PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


RESERVED_PROFILES = frozenset({"_doctor", "_live"})
INTERNAL_DATA_PROFILE = "_doctor"


REF_RE = re.compile(r"^(f\d+)?e\d+$")


TAB_ID_RE = re.compile(r"^t[1-9]\d*$", re.I)


MAX_PROFILE_PATH_CHARS = 180           # Chromium silently falls back to the default profile past MAX_PATH


DEFAULT_TIMEOUT = 15.0


GOTO_TIMEOUT = 30.0


START_TIMEOUT = 45.0


STOP_GRACE = 10.0


WAIT_DEFAULT = 60.0


WAIT_CAP = 100.0                       # the Claude Code Bash tool kills a call at 120 s by default


DOWNLOADS_TIMEOUT = 60.0


MAX_TIMEOUT = 86400.0                  # the largest --timeout the CLI and the daemon accept


RECIPES = frozenset({"sn/add-work-note"})


RECORD_NUMBER_RE = re.compile(r"(?:INC|SCTASK|RITM|CHG|PRB)\d{6,12}")   # the record numbers a recipe accepts


SETTLE_CAP = 8.0


DEFAULT_MAX_BYTES = 16000


STDOUT_BUDGET = 24000                  # Bash tool output is cut at 30,000 chars


DEFAULT_TEXT_CHARS = 20000


CHANGES_CAP = 40


REQUEST_CAP = 2 * 1024 * 1024


CLIENT_GRACE = SETTLE_CAP + 7.0        # the daemon settles frames and snapshots after the action itself finishes


QUEUE_WAIT = 30.0                      # one daemon serves a profile serially; past this wait the daemon refuses to start a request


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


# The roles HTML gives a text-entry input or textarea. Playwright prints any input's value whatever its role, so a
# snapshot also checks lines that show the value of an input the page gave another role (daemon.INPUT_ROLES_JS).
INPUT_VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider"})


# Typed values in a snapshot: each valued input line costs one SECRET_JS check; past the cap or the budget it is masked.
SECRET_SCAN_CAP = 200
SECRET_SCAN_TIMEOUT_MS = 500
SECRET_SCAN_BUDGET = 2.0


MASK = "***"


# Where a masked value is removed from other lines' names: a confirmed credential's own value this long, from every
# line (at most SCRUB_PAGE_CAP values); any masked value, from the named lines around its field. Past
# SCRUB_WORK_CAP (line, value) pairs, the remaining named lines around masked fields show *** as their name instead,
# so a hostile page cannot make the scrub quadratic.
SCRUB_MIN = 3
SCRUB_PAGE_CAP = 64
SCRUB_WORK_CAP = 200_000


# Lines with a role the page put on a valued input: at most this many are checked per snapshot (they share the time
# budget); past it, such lines that may still be one of those inputs are masked.
REMAP_SCAN_CAP = 20


JS_SPACE_RE = re.compile(r"[\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+")  # JS \s


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


# Substring heuristics that fail safe. Concatenated and camelCase names (adminlogin, UserSignInGate, CorpSSOCallback,
# userauth, authorise) still match; only words that merely contain the letters (author, lessons, Associate, designing,
# signing) are excluded. The (?-i:...) parts read camelCase boundaries, so they are case-sensitive.
LOGIN_PAGE_RE = re.compile(r"log-?(in|on)|sign[-_]?(in|on)(?!g)|(?-i:[Ss]ign[-_]?(In|On)(?=[A-Z]))|(?<![a-z])sign (in|on)(?![a-z])"
                           r"|(?<![a-z])sso|sso(?![a-z])|(?-i:SSO(?=[A-Z][a-z])|Sso(?=[A-Z]))|mfa|saml|oauth|auth(?!or(?!i[sz]))|verif(y|ication)", re.I)


# Bare "pass" stays a match after any prefix (userpass, txtpass, userPass) except the few English words ending in it.
# Short forms are bounded by non-letters, not \b, so ssn_last4, mfa_code, otpInput and cvv2 match; no English word
# ends in "pw".
SECRET_STRONG_RE = re.compile(r"pass(word|code|phrase|wd|wrd)|(?<!com)(?<!by)(?<!sur)(?<!tres)(?<!over)(?<!under)pass\b|pwd|pw(?![a-z])"
                              r"|(?<![a-z])(otp|mfa|ssn)(?-i:(?![a-z]))|totp|secret|token|social[-_ ]?security|(?<![a-z])cv[vcn]\d?(?![a-z])"
                              r"|card[-_ ]?(number|num|no)\b|\bcc[-_ ]?(number|num|no)\b"
                              r"|(verification|security|auth(entication)?|recovery|backup|sms|2fa|one[-_ ]?time)[-_ ]?code"
                              r"|(api|recovery|private|access)[-_ ]?keys?(?![a-z])", re.I)


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


HOME_DOT_FOLDERS_ALLOWED = frozenset({".claude"})  # every other dot-folder directly under the user's home is refused


UNTRUSTED_KEYS = ["snapshot", "text", "title", "url", "details", "changes", "frames", "tabs", "dialogs", "target_line", "value_after", "value"]


OFF_ALLOWLIST_OK = frozenset({"ping", "stop", "back", "goto", "snapshot", "text", "tabs", "tab"})


BROWSER_BASENAMES = frozenset({
    "msedge.exe", "chrome.exe", "msedge", "chrome", "microsoft-edge", "microsoft-edge-stable",
    "google-chrome", "google-chrome-stable", "microsoft edge", "google chrome", "chromium", "chromium-browser",
})


CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


# A snapshot key is role, name and [attrs]. Playwright prints a name that starts and ends with / (a lone / too) bare
# instead of JSON-quoted, and single-quotes the whole key ('' for ') when it holds ": ", " #", braces or a backtick.
# A value only ever follows ": ", so a name cannot pass forged [ref=...] text off as the line's attrs.
SNAP_KEY = (r'(?P<role>[A-Za-z/]+)(?: (?:"(?P<name>(?:[^"\\]|\\.)*)"|(?P<rname>/(?= \[|:|$)|/.*?/(?= \[|:|$))))?'
            r'(?P<attrs>(?: \[[^\[\]]*\])*)')  # Playwright's attrs never hold a bracket; a bare name's " [x" is not one


LINE_RE = re.compile(r'^(?P<indent>\s*)- ' + SNAP_KEY + r'(?:(?P<colon>:)(?: (?P<value>.*))?)?$')


QUOTED_LINE_RE = re.compile(r"^(?P<indent>\s*)- '(?P<key>(?:[^']|'')*)'(?:(?P<colon>:)(?: (?P<value>.*))?)?$")


SNAP_KEY_RE = re.compile("^" + SNAP_KEY + "$")


# Everything str.splitlines() breaks on; a rendered snapshot line must hold none of them.
LINE_BREAKS = str.maketrans(dict.fromkeys("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029", " "))


# YAML double-quoted escapes (Playwright emits \\ \" \b \f \n \r \t and \xNN; the rest are decoded for completeness).
YAML_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
                "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}


YAML_ESCAPE_RE = re.compile(r"\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)", re.S)


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


ERR_BUSY = "busy"


ERR_IO = "io"


ERR_OUTPUT_STORAGE = "output_storage"


ERR_JOURNAL_FULL = "journal_full"


EXIT_2_CLASSES = frozenset({
    ERR_USAGE, ERR_VALIDATION, ERR_AMBIGUOUS_PROFILE, ERR_REF_NOT_FOUND, ERR_STALE_REF, ERR_NOT_FOUND,
    ERR_AMBIGUOUS, ERR_ACTION_TIMEOUT, ERR_ACTION_FAILED, ERR_GUARDED, ERR_DIALOG, ERR_UNSAVED,
})


EXIT_TIMEOUT = 124


EXIT_INTERRUPT = 130


HINTS = {
    "not_running": "Run: {tool} start --profile {profile}",
    "starting": "The browser is still starting. Run: {tool} status --profile {profile}",
    "stale_ref": "The page changed since the last snapshot. Run: {tool} snapshot --profile {profile}",
    "refs_moved": "A frame navigated while this reply's snapshot was taken, so its refs are stale. Run: {tool} snapshot --profile {profile}",
    "expect_text_baseline": "The page text could not be read before the action, so text: had nothing to compare with and nothing was done. Run: {tool} snapshot --profile {profile} and repeat the command, or expect response:, gone: or url:changed instead.",
    "ref_not_found": "That ref is not on the current page. Run: {tool} snapshot --profile {profile}",
    "bad_ref": "Refs look like e12 or f2e5 and come from: {tool} snapshot --profile {profile}",
    "ambiguous_profile": "Several browsers are running. Add --profile NAME (see: {tool} status --all)",
    "guarded_secret": "This field takes a password or code. Ask the user to type it in the browser window, then run: {tool} wait --signed-in <app host> --profile {profile}",
    "guarded_key": "Only Enter, Tab, and Escape may be pressed on a password or code field.",
    "guarded_eval_secret": "eval cannot target a password or code field. Pick a ref to another element; never read or set credential values with eval.",
    "secret_unverified": "Could not confirm the field is not a password or code field. Run: {tool} snapshot --profile {profile} and retry with a fresh ref; if a press without a ref keeps failing, give it a ref.",
    "guarded_scheme": "Only http:// and https:// URLs can be opened.",
    "guarded_host": "That host is not in allowed_hosts (config.json). Ask the user before continuing.",
    "guarded_path": "The file must be under the current folder, this session's downloads/shots folders, or a configured upload root.",
    "off_allowlist": "The page left the allowed hosts. Use: {tool} back --profile {profile}, or ask the user.",
    "dialog": "A browser dialog was dismissed. Re-run the same command with --accept-dialog to accept it.",
    "unsaved_changes": "The page has unsaved edits. Click its Save/Update button first, or re-run this command with --discard-changes.",
    "action_timeout": "The element is hidden, disabled, or covered. Run: {tool} snapshot --profile {profile} and pick a visible enabled element, or: {tool} wait --ref <ref> --profile {profile}",
    "action_failed": "Run: {tool} snapshot --profile {profile} and try a different element or verb.",
    "wait_timeout": "Still waiting. Run the same wait command again, or ask the user whether sign-in is finished.",
    "nav_timeout": "The page did not finish loading in time and the navigation may still complete. Run: {tool} snapshot --profile {profile} before repeating it.",
    "wait_cap": "wait runs at most 100 seconds per call. Use --timeout 100 and run the same wait again if needed.",
    "no_wait_condition": "Give at least one condition: --url-contains, --text, --ref, --selector, --signed-in, or --seconds.",
    "daemon_unresponsive": "Run: {tool} stop --force --profile {profile}",
    "busy": "Another command held the browser, so this one was not run. Repeat it, or check: {tool} status --profile {profile}",
    "busy_status": "The browser is running another command, not hung. Do not force-stop it. Check again: {tool} status --profile {profile}",
    "profile_in_use": "Another browser holds this profile. Run: {tool} clean --profile {profile}",
    "playwright_missing": "Install it for the interpreter that runs {tool}: python -m pip install --user \"playwright>=1.61,<2\"",
    "browser_not_found": "Set AGENT_BROWSER_EXE or pass --exe pointing at msedge.exe or chrome.exe.",
    "stale_code": "Implementation changed since daemon startup. Explicitly restart the shared profile: {tool} stop --profile {profile} --all, then {tool} start --profile {profile}. This closes every session in that profile.",
    "protocol_mismatch": "Client and daemon are incompatible. Run {tool} stop --profile {profile} --all --force, then {tool} start --profile {profile}. This closes every session in that profile.",
    "select_not_found": "None of the options match. See details.options, then run select again with one of them.",
    "select_not_select": "This element is not a drop-down. Use: {tool} click <ref> then {tool} snapshot --profile {profile}",
    "check_not_checkbox": "This element is not a checkbox. Use: {tool} click <ref>",
    "fill_not_input": "This element is not a text field. Use: {tool} click <ref> then {tool} snapshot --profile {profile}",
    "eval_disabled": "eval is disabled by config.json on this machine.",
    "browser_closed": "The browser window was closed. Run: {tool} start --profile {profile} (sign-in is kept)",
    "timeout_client": "The browser did not answer in time. Run: {tool} status --profile {profile}",
    "timeout_client_action": "The daemon did not answer in time and the command may have run. Run: {tool} state --profile {profile} (this session's receipts) before repeating it.",
    "last_tab": "This is the last tab. Use: {tool} stop --profile {profile} to close the browser.",
    "tab_index": "Run: {tool} tabs --profile {profile} and use one of the listed tab ids (t1, t2) or indexes (1, 2).",
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
    "inspect_receipt": "Inspect the receipt before repeating an action. Run: {tool} journal --profile {profile}",
    "commit_clear": "Clearing deletes this session's receipts, screenshots and saved responses. With the user's approval, run: {tool} commit journal --clear",
    "handoff_not_found": "Handoffs belong to the session that created them, and clearing evidence removes them. List this session's with: {tool} state --profile {profile}",
    "api_not_auth": "Not treated as a sign-in, so no handoff was opened (redirects are not followed). Check the endpoint and the account's access; if the session may have expired, run: {tool} needs-human --signed-in <app host> --profile {profile}",
    "api_auth_unavailable": "If the user must sign in, run: {tool} needs-human --signed-in <app host> --profile {profile}; otherwise open a classic ServiceNow form page in this tab and retry.",
    "tool_arguments": "Check the tool's inputSchema from tools/list and call it again with valid arguments.",
}


VERBOSE = False


_ARGV_NOTES: list[str] = []


NOTE_CAP = 300                         # characters per note; a note quotes caller arguments


def _log(message: str) -> None:
    if VERBOSE:
        print(f"{TOOL_NAME}: {message}", file=sys.stderr, flush=True)


def _tag(exc: Exception, error_class: str, hint: str | None = None, details: Any = None) -> Exception:
    """Ride error_class / hint / details on a builtin exception (no custom exception classes)."""
    exc.error_class = error_class  # type: ignore[attr-defined]
    exc.hint = hint  # type: ignore[attr-defined]
    if details is not None:
        exc.details = details  # type: ignore[attr-defined]
    return exc


def _hint(key: str, profile: str | None = DEFAULT_PROFILE) -> str:
    # A hint is a command to run next; never render a caller-supplied name that is not a valid profile.
    profile = profile if isinstance(profile, str) and _is_profile_name(profile) else DEFAULT_PROFILE
    return HINTS[key].format(tool=TOOL_NAME, profile=profile)


def _preservation_details(source: Any) -> dict[str, Any]:
    """The receipt pointer a reply keeps when it cannot be stored: from a result or an error's details."""
    error = source.get("error") if isinstance(source, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    origins = [o for o in (source, details) if isinstance(o, dict)]
    command_id = next((o["command_id"] for o in origins if o.get("command_id") is not None), None)
    executed = next((o["executed"] for o in origins if o.get("executed") is not None), None)
    if not (isinstance(command_id, str) and re.fullmatch(r"[0-9a-f]{32}", command_id)):
        command_id = None
    return {"command_id": command_id, "executed": executed if isinstance(executed, bool) else None,
            "receipt": "state --command " + command_id if command_id else "journal"}


def _storage_error(source: Any, exc: BaseException | None = None, profile: str | None = None) -> dict[str, Any]:
    """Error object for a reply that could not be preserved; the class says why (quota or storage)."""
    cls = ERR_JOURNAL_FULL if getattr(exc, "error_class", None) == ERR_JOURNAL_FULL else ERR_OUTPUT_STORAGE
    return {"error": {"class": cls, "http_status": None,
                      "message": "Cannot preserve the oversized reply; inspect its receipt before repeating an action.",
                      "hint": _hint("inspect_receipt", profile), "details": _preservation_details(source)}}


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


_wall = time.time                      # comparable across the client and daemon processes, unlike _now


_sleep = time.sleep


UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(UTC_FORMAT)


def _utc_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(UTC_FORMAT)


def _home() -> Path:
    explicit = os.getenv("AGENT_BROWSER_HOME")
    if explicit:
        home = Path(explicit).expanduser()
    elif _is_windows():
        home = Path(os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / TOOL_NAME
    else:
        home = Path.home() / f".{TOOL_NAME}"
    return _absolute(home)


def _absolute(path: Path) -> Path:
    # The daemon's cwd is the profile folder, so a relative home would resolve somewhere else there.
    # abspath also resolves a Windows drive-relative D:x against that drive's cwd.
    return path if path.is_absolute() else Path(os.path.abspath(path))


def _is_profile_name(name: str) -> bool:
    return bool(PROFILE_RE.match(name or "")) or name in RESERVED_PROFILES


def _validate_profile(name: str) -> str:
    if not _is_profile_name(name):
        raise _tag(ValueError(f"Profile name {name!r} must match {PROFILE_RE.pattern}"), ERR_VALIDATION)
    return name


def _parse_tab_spec(value: Any, profile: str = DEFAULT_PROFILE) -> int | str:
    if isinstance(value, bool) or value is None:
        raise _tag(ValueError(f"Invalid tab {value!r}; use a 1-based index (1, 2) or tab id (t1, t2)."), ERR_VALIDATION, hint=_hint("tab_index", profile))
    if isinstance(value, int):
        if value < 1:
            raise _tag(ValueError(f"Invalid tab {value!r}; tab index must be >= 1."), ERR_VALIDATION, hint=_hint("tab_index", profile))
        return value
    text = str(value).strip()
    if text.isdigit():
        idx = int(text)
        if idx < 1:
            raise _tag(ValueError(f"Invalid tab {value!r}; tab index must be >= 1."), ERR_VALIDATION, hint=_hint("tab_index", profile))
        return idx
    if TAB_ID_RE.match(text):
        return text.lower()
    raise _tag(ValueError(f"Invalid tab {value!r}; use a 1-based index (1, 2) or tab id (t1, t2)."), ERR_VALIDATION, hint=_hint("tab_index", profile))


@dataclass(frozen=True)
class ProfilePaths:
    profile: str
    root: Path
    session_id: str | None = None

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
        return self.workspace / "last_snapshot.txt"

    @property
    def downloads(self) -> Path:
        return self.workspace / "downloads"

    @property
    def shots(self) -> Path:
        return self.workspace / "shots"

    @property
    def outputs(self) -> Path:
        return self.workspace / "outputs"

    @property
    def workspace(self) -> Path:
        if not self.session_id or self.session_id == "manual":
            return self.dir
        return self.dir / "sessions" / hashlib.sha256(self.session_id.encode()).hexdigest()[:32]


def _paths(profile: str) -> ProfilePaths:
    paths = ProfilePaths(_validate_profile(profile), _home())
    _check_profile_path_length(paths)
    return paths


def _check_profile_path_length(paths: ProfilePaths) -> None:
    if len(str(paths.user_data)) > MAX_PROFILE_PATH_CHARS:
        raise _tag(
            RuntimeError(f"Profile path is too long for Chromium ({len(str(paths.user_data))} chars): {paths.user_data}"),
            ERR_CONFIG, hint=_hint("profile_path"),
        )


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
    if "allowed_hosts" in data:
        out["allowed_hosts"] = _normalize_allowed_hosts(data["allowed_hosts"])
    if "upload_roots" in data:
        value = data["upload_roots"]
        if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
            raise _tag(RuntimeError("config.json upload_roots must be a list of strings."), ERR_CONFIG)
        if value is not None:
            out["upload_roots"] = [v for v in value if v.strip()]
    for key in ("profiles", "permission", "evidence_max_bytes", "uploads"):
        if key in data:
            out[key] = data[key]
    return out


def _normalize_allowed_hosts(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _tag(RuntimeError("allowed_hosts must be a list of strings."), ERR_CONFIG)
    hosts = [v.strip().lower() for v in value if v.strip()]
    if not hosts:
        raise _tag(RuntimeError("allowed_hosts must contain at least one non-blank host; omit it to allow unrestricted navigation."), ERR_CONFIG)
    return hosts


def session_identity(explicit: str | None = None) -> str:
    value = explicit or os.getenv("CODEX_THREAD_ID") or os.getenv("CLAUDE_CODE_SESSION_ID") or "manual"
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise _tag(ValueError("Invalid session identifier."), ERR_VALIDATION)
    return value


def _code_hash() -> str:
    try:
        digest = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]
    except OSError:
        return "unknown"


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
    except ValueError:  # a malformed host ([..., NFKC tricks) hides where userinfo and the query start
        return MASK
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop user:pass@ userinfo
    if not parts.query:
        return sanitize_text(urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")), 500)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    masked = [(k, "***" if k.lower() in REDACT_QUERY_KEYS else v) for k, v in pairs]
    query = urllib.parse.urlencode(masked, safe="/:*")
    return sanitize_text(urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, "")), 500)


def _yaml_escape(m: re.Match[str]) -> str:
    esc = m.group(1)
    if len(esc) > 1 and esc[0] in "xuU":
        code = int(esc[1:], 16)
        return m.group(0) if 0xD800 <= code <= 0xDFFF or code > 0x10FFFF else chr(code)  # no lone surrogates
    return YAML_ESCAPES.get(esc, m.group(0))  # an unknown escape stays as written


def yaml_unquote(value: str) -> str:
    """A YAML scalar as written: double-quoted with backslash escapes, single-quoted with '' for ', or plain."""
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return YAML_ESCAPE_RE.sub(_yaml_escape, value[1:-1])
    return value


def js_normalize_space(text: str) -> str:
    """Playwright's normalizeWhiteSpace: drop U+200B and U+00AD, trim, collapse JavaScript whitespace."""
    return JS_SPACE_RE.sub(" ", text.replace("\u200b", "").replace("\xad", "")).strip(" ")


def redact_href(value: Any) -> str:
    """A snapshot /url value as shown: unquoted, then redacted like any URL. A same-page #fragment shows as #."""
    href = yaml_unquote(str(value or ""))
    redacted = redact_url(href)
    return "#" if not redacted and href.startswith("#") else redacted


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


def _check_url(url: str, paths: ProfilePaths, config: dict[str, Any]) -> str:
    raw = (url or "").strip()
    if not raw:
        raise _tag(ValueError("goto needs a URL."), ERR_USAGE)
    if "://" not in raw and not raw.lower().startswith(("about:", "data:", "javascript:", "file:", "blob:")):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    scheme = parts.scheme.lower()
    allowed = _normalize_allowed_hosts(config["allowed_hosts"]) if "allowed_hosts" in config else None
    if scheme == "about" and parts.path == "blank":
        return raw
    if scheme == "data" and paths.profile == INTERNAL_DATA_PROFILE:
        return raw
    if scheme not in ALLOWED_SCHEMES:
        raise _tag(ValueError(f"URL scheme {scheme or '(none)'!r} is not allowed."), ERR_GUARDED, hint=_hint("guarded_scheme"))
    host = (parts.hostname or "").lower()
    if allowed is not None and not _host_allowed(host, allowed):
        raise _tag(ValueError(f"Host {host!r} is outside allowed_hosts."), ERR_GUARDED, hint=_hint("guarded_host"))
    return raw


def _host_allowed(host: str, allowed: Sequence[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed if a)


_PROCESS_CWD: Any = object()


def _check_local_path(candidate: str, paths: ProfilePaths, config: dict[str, Any], must_exist: bool = True, *, base: Any = _PROCESS_CWD) -> Path:
    """Resolve a caller-named file and refuse anything outside the caller's allowed folders.

    `base` is the caller's working directory. The CLI omits it: its own cwd is the caller's.
    The daemon's cwd is the profile folder, so daemon code passes the caller's `_cwd`, or None
    when none was sent (then only absolute paths under downloads/shots/upload_roots pass).
    """
    text = (candidate or "").strip()
    if not text:
        raise _tag(ValueError("A file path is required."), ERR_USAGE)
    if text.startswith(("\\\\", "//")):
        raise _tag(ValueError("UNC and device paths are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    caller = Path.cwd() if base is _PROCESS_CWD else None if base is None else Path(base)
    if caller is not None and not caller.is_absolute():
        raise _tag(ValueError("Caller cwd must be absolute."), ERR_VALIDATION)
    path = Path(text).expanduser()
    if not path.is_absolute():
        if caller is None:
            raise _tag(ValueError("A relative path needs the caller's working directory; give an absolute path."), ERR_VALIDATION)
        path = caller / path
        if not path.is_absolute():  # a drive-relative D:x joined to C:\caller stays relative
            raise _tag(ValueError("File path cannot be resolved against the caller cwd."), ERR_VALIDATION)
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise _tag(ValueError(f"Cannot resolve {text!r}: {exc}"), ERR_VALIDATION) from exc
    # A \\?\ or //?/ base survives resolve() and would never match the unprefixed store and home below.
    if str(resolved).startswith(("\\\\", "//")):
        raise _tag(ValueError("UNC and device paths are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    if _is_windows() and ":" in str(resolved)[2:]:
        raise _tag(ValueError("Alternate data streams are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    if resolved.suffix.lower() in KEY_EXTENSIONS:
        raise _tag(ValueError("Key material cannot be uploaded."), ERR_GUARDED, hint=_hint("guarded_path"))
    if not _drive_is_fixed(resolved):
        raise _tag(ValueError("Only files on a fixed local drive are allowed (network and removable drives are refused); copy the file to a local folder."),
                   ERR_GUARDED, hint=_hint("guarded_path"))
    if _in_profile_store(resolved, paths):
        raise _tag(ValueError("The agent-browser state folder (session and launch files, browser profile, saved outputs, config) is not available as a file input or output."),
                   ERR_GUARDED, hint=_hint("guarded_path"))
    roots = ([caller.resolve()] if caller is not None else []) + [paths.downloads.resolve(), paths.shots.resolve()]
    roots.extend(Path(r).expanduser().resolve() for r in config.get("upload_roots", []) if r)
    matching = [root for root in roots if _is_under(resolved, root)]
    if not matching:
        raise _tag(ValueError(f"{resolved} is outside the allowed folders."), ERR_GUARDED, hint=_hint("guarded_path"))
    if all(_hidden_below(resolved, root) for root in matching) or _in_home_dot_folder(resolved, paths):
        raise _tag(ValueError("Hidden files and folders are not allowed."), ERR_GUARDED, hint=_hint("guarded_path"))
    if must_exist and not resolved.is_file():
        raise _tag(ValueError(f"File not found: {resolved}"), ERR_NOT_FOUND)
    return resolved


def _in_profile_store(resolved: Path, paths: ProfilePaths) -> bool:
    # profiles/ holds tokens, cookies and receipts; config.json is policy. The home itself is not
    # protected (AGENT_BROWSER_HOME=C:\ would cover the drive). Only this session's downloads/shots pass.
    root = paths.root.resolve()
    if not any(_is_under(resolved, entry, fold=True) for entry in (root / "profiles", root / "config.json")):
        return False
    return not any(_is_under(resolved, own.resolve()) for own in (paths.downloads, paths.shots))


def _hidden_below(resolved: Path, root: Path) -> bool:
    # The root's own name and everything below it. A dotted ancestor (a worktree's .claude) does not count.
    return any(part.startswith(".") for part in resolved.parts[max(len(root.parts) - 1, 1):])


def _in_home_dot_folder(resolved: Path, paths: ProfilePaths) -> bool:
    # Dot-folders directly under the user's home hold credentials (.ssh, .aws, .config/gh) whatever the cwd.
    try:
        home = Path.home().resolve()
    except (RuntimeError, KeyError, OSError):
        # Unknown home (a stripped daemon environment): any dot-folder outside the agent-browser home counts.
        return not _is_under(resolved, paths.root.resolve()) and any(part.startswith(".") for part in resolved.parts[1:])
    if not _is_under(resolved, home, fold=True) or len(resolved.parts) <= len(home.parts):
        return False
    name = resolved.parts[len(home.parts)]
    return name.startswith(".") and name.lower() not in HOME_DOT_FOLDERS_ALLOWED and not _is_under(resolved, paths.root.resolve())


def _is_under(path: Path, root: Path, *, fold: bool = False) -> bool:
    if fold:  # resolve() does not normalise case on case-insensitive POSIX filesystems (macOS)
        parts, prefix = [p.casefold() for p in path.parts], [p.casefold() for p in root.parts]
        return parts[:len(prefix)] == prefix
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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

ENTRY_PATH = Path(__file__).resolve().parents[1] / "agent_browser.py"
