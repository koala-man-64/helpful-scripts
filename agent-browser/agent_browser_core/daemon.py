from __future__ import annotations

import hmac
import json
import os
import socketserver
import struct
import sys
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import common as core_common, session as core_session, snapshots as core_snapshots
from . import runtime, policy
from . import actions
from . import api
from . import reach


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
          contenteditable: !!el.isContentEditable, context_url: el.ownerDocument.URL, context_title: el.ownerDocument.title};
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
    if core_common.SECRET_AUTOCOMPLETE_RE.search(info.get("autocomplete") or ""):
        return True
    text = info.get("text") or ""
    if core_common.SECRET_STRONG_RE.search(text):
        return True
    login_page = bool(core_common.LOGIN_PAGE_RE.search(page_url or "") or core_common.LOGIN_PAGE_RE.search(page_title or ""))
    if login_page:
        if core_common.SECRET_WEAK_RE.search(text):
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


class BrowserEngine:
    """Owns one Playwright persistent context. Single-threaded: only the main thread touches Playwright."""

    def __init__(self, launch: dict[str, Any]) -> None:
        self.launch = launch
        self.profile = launch["profile"]
        self.paths = core_common.ProfilePaths(self.profile, Path(launch["home"]))
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
        self.last_activity = core_common._now()
        self.errors: tuple[type, type] = (Exception, Exception)
        self.browser_version: str | None = None
        self.log_handle: Any = None
        self.telemetry = dict.fromkeys(("console_errors", "page_errors", "failed_requests", "http_errors"), 0)

    # -- logging -------------------------------------------------------------------------

    def dlog(self, message: str) -> None:
        line = f"{core_common._utc_now()} {message}"
        try:
            print(line, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

    # -- lifecycle -----------------------------------------------------------------------

    def start_browser(self) -> None:
        self.pw = core_common._playwright_factory()
        self.errors = core_common._playwright_errors()
        kind = self.launch.get("browser") or "msedge"
        exe = core_common._check_browser_exe(self.launch.get("exe"))
        options: dict[str, Any] = {
            "headless": bool(self.launch.get("headless")),
            "no_viewport": True,
            "args": [
                f"--window-size={self.launch.get('window_size') or core_common.DEFAULT_WINDOW_SIZE}",
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
                    raise core_common._tag(RuntimeError("The profile folder is already in use by another browser."), core_common.ERR_PROFILE_IN_USE, hint=core_common._hint("profile_in_use", self.profile)) from exc
                core_common._sleep(1.0)
        if self.ctx is None:
            raise core_common._tag(RuntimeError(f"The browser did not start: {str(last_exc)[:300]}"), core_common.ERR_LAUNCH, hint=core_common._hint("launch", self.profile))
        try:
            self.browser_version = self.ctx.browser.version if self.ctx.browser else None
        except Exception:  # noqa: BLE001
            self.browser_version = None
        self.ctx.set_default_timeout(core_common.DEFAULT_TIMEOUT * 1000)
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
        page.on("download", lambda download: self._on_download(download, page))
        page.on("close", lambda p: self._on_page_closed(p))
        page.on("console", lambda message: self._observe("console_errors", page) if message.type == "error" else None)
        page.on("pageerror", lambda error: self._observe("page_errors", page))
        page.on("requestfailed", lambda request: self._observe("failed_requests", page))
        page.on("response", lambda response: self._observe("http_errors", page) if response.status >= 400 else None)

    def _observe(self, kind: str, page: Any = None) -> None:
        self.telemetry[kind] += 1

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

    def _on_download(self, download: Any, page: Any = None) -> None:
        try:
            suggested = download.suggested_filename
        except Exception:  # noqa: BLE001
            suggested = "download"
        self.downloads.append(DownloadRecord(download, core_common.safe_name(suggested), core_common._utc_now()))

    def _on_dialog(self, dialog: Any) -> None:
        kind = getattr(dialog, "type", "confirm")
        record = {"type": kind, "message": core_common.sanitize_text(getattr(dialog, "message", ""), 200), "handled": ""}
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
                raise core_common._tag(RuntimeError("The browser has no open tabs."), core_common.ERR_BROWSER_CLOSED, hint=core_common._hint("browser_closed", self.profile))
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
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_STALE_REF, hint=core_common._hint("stale_ref", p))
        if "has been closed" in text or "Target closed" in text or "browser has been closed" in text.lower():
            return core_common._tag(RuntimeError(core_common.sanitize_text(message)), core_common.ERR_BROWSER_CLOSED, hint=core_common._hint("browser_closed", p))
        if "Not a checkbox or radio button" in text:
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_ACTION_FAILED, hint=core_common._hint("check_not_checkbox", p))
        if "not a <select> element" in text:
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_ACTION_FAILED, hint=core_common._hint("select_not_select", p))
        if "Element is not an <input>" in text:
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_ACTION_FAILED, hint=core_common._hint("fill_not_input", p))
        if "strict mode violation" in text:
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_AMBIGUOUS, hint=core_common._hint("action_failed", p))
        if self._is_timeout(exc):
            return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_ACTION_TIMEOUT, hint=core_common._hint("action_timeout", p))
        calllog = [l.strip() for l in text.splitlines()[1:6] if l.strip()]
        return core_common._tag(ValueError(core_common.sanitize_text(message)), core_common.ERR_ACTION_FAILED, hint=core_common._hint("action_failed", p), details={"log": [core_common.sanitize_text(l, 200) for l in calllog]})

    def _ref_target(self, page: Any, ref: str) -> Any:
        m = core_common.REF_RE.match(ref)
        if not m:
            raise core_common._tag(ValueError(f"Bad ref {ref!r}."), core_common.ERR_VALIDATION, hint=core_common._hint("bad_ref", self.profile))
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
            raise core_common._tag(ValueError(f"Ref {ref} belongs to a frame that is no longer in the snapshot."), core_common.ERR_STALE_REF, hint=core_common._hint("stale_ref", self.profile))
        if self.nav_counts.get(frame, 0) != served.nav_at_serve.get(frame, 0):
            raise core_common._tag(ValueError(f"Ref {ref} is stale: its frame navigated since the last snapshot."), core_common.ERR_STALE_REF, hint=core_common._hint("stale_ref", self.profile))
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
            raise core_common._tag(ValueError(f"Ref {ref} is not on the current page."), core_common.ERR_REF_NOT_FOUND, hint=core_common._hint("ref_not_found", self.profile))
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
                    raise core_common._tag(ValueError(f"Bad selector {selector!r}."), core_common.ERR_VALIDATION, hint=core_common._hint("bad_ref", self.profile)) from exc
        if not matches:
            raise core_common._tag(ValueError(f"Nothing matches {selector!r}."), core_common.ERR_NOT_FOUND, hint=core_common._hint("ref_not_found", self.profile))
        if len(matches) > 1:
            raise core_common._tag(ValueError(f"{selector!r} matches in {len(matches)} frames; use a ref."), core_common.ERR_AMBIGUOUS, hint=core_common._hint("stale_ref", self.profile))
        loc = matches[0][1]
        if loc.count() > 1:
            raise core_common._tag(ValueError(f"{selector!r} matches {loc.count()} elements; use a ref."), core_common.ERR_AMBIGUOUS, hint=core_common._hint("stale_ref", self.profile))
        return loc

    def resolve(self, page: Any, target: str) -> Any:
        t = core_snapshots.normalize_target(target)
        if not t:
            raise core_common._tag(ValueError("A target ref is required."), core_common.ERR_USAGE, hint=core_common._hint("bad_ref", self.profile))
        return self._ref_target(page, t) if core_common.REF_RE.match(t) else self._selector_target(page, t)

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
        info = self._secret_info(locator)
        return is_secret_field(info, info.get("context_url", page.url), info.get("context_title", self._safe_title(page)))

    def _focused_is_secret(self, page: Any) -> bool:
        """The element that would receive a targetless key press, in whichever frame holds focus."""
        for frame in page.frames:
            try:
                info = frame.evaluate(ACTIVE_SECRET_JS)
            except Exception:  # noqa: BLE001
                continue
            if info and is_secret_field(info, info.get("context_url", frame.url), info.get("context_title", self._safe_title(page))):
                return True
        return False

    def _settle(self, page: Any, cap: float = core_common.SETTLE_CAP) -> list[str]:
        deadline = core_common._now() + cap
        try:
            page.wait_for_load_state("load", timeout=min(3000, max(100, int((deadline - core_common._now()) * 1000))))
        except Exception:  # noqa: BLE001
            pass
        last: tuple | None = None
        stable_since: float | None = None
        while core_common._now() < deadline:
            try:
                frames = list(page.frames)
            except Exception:  # noqa: BLE001
                break
            for frame in frames:
                remaining = int((deadline - core_common._now()) * 1000)
                if remaining <= 0:
                    break
                try:
                    frame.wait_for_load_state("load", timeout=min(1500, remaining))
                except Exception:  # noqa: BLE001
                    pass
            signature = tuple((f.url, f.name) for f in frames)
            if signature == last:
                if stable_since is not None and core_common._now() - stable_since >= 0.25:
                    break
            else:
                last, stable_since = signature, core_common._now()
            try:
                page.wait_for_timeout(100)
            except Exception:  # noqa: BLE001
                break
        loading = []
        try:
            for frame in page.frames:
                try:
                    if frame.evaluate("() => document.readyState") != "complete":
                        loading.append(frame.name or core_common.redact_url(frame.url))
                except Exception:  # noqa: BLE001 - a frame mid-navigation has no context yet
                    loading.append(frame.name or "frame")
        except Exception:  # noqa: BLE001
            pass
        return loading

    def _frame_map(self, page: Any, lines: Sequence[core_snapshots.SnapLine]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str], dict[str, str]]:
        prefixes = core_snapshots.frame_prefixes(lines)
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
            frames.append({"prefix": prefix, "ref": line.ref, "name": core_common.sanitize_text(label, 80),
                           "url": core_common.redact_url(frame.url) if frame is not None else "",
                           "same_origin": (_origin(frame.url) == page_origin) if frame is not None else None})
        # Newer Playwright versions can prefix main-frame refs too. Resolve each
        # remaining prefix through its actual element owner, including scoped trees.
        seen = set(by_prefix)
        for line in lines:
            if not line.prefix or line.prefix in seen:
                continue
            seen.add(line.prefix)
            try:
                frame = page.locator("aria-ref=" + line.ref).element_handle(timeout=1000).owner_frame()
                if frame in page.frames:
                    by_prefix[line.prefix] = frame
            except Exception:  # noqa: BLE001 - absent/moving elements remain stale
                pass
        return by_prefix, frames, names, prefixes

    def _serve_snapshot(self, page: Any, *, full: bool = False, find: str | None = None, scope: str | None = None) -> Served:
        try:
            if scope:
                raw = self.resolve(page, scope).aria_snapshot(mode="ai")
            else:
                raw = page.aria_snapshot(mode="ai")
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "snapshot") from exc
        parsed = core_snapshots.parse_ai_snapshot(raw)
        by_prefix, frames, names, prefixes = self._frame_map(page, parsed)
        lines = core_snapshots.filter_snapshot(raw, full=full, find=find, frame_names=names)
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
        if core_common._is_idp_host(host):
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
        return not core_common._host_allowed(host, allowed)

    def _tab_index(self, page: Any) -> int:
        return self.pages.index(page) + 1 if page in self.pages else 0

    def _tabs(self) -> list[dict[str, Any]]:
        out = []
        for i, p in enumerate(self.pages, 1):
            try:
                out.append({"index": i, "url": core_common.redact_url(p.url), "title": core_common.sanitize_text(p.title(), 120), "current": p is self.current})
            except Exception:  # noqa: BLE001
                out.append({"index": i, "url": "", "title": "", "current": p is self.current})
        return out

    def _base_fields(self, page: Any) -> dict[str, Any]:
        return {"profile": self.profile, "url": core_common.redact_url(page.url), "title": core_common.sanitize_text(self._safe_title(page), 200),
                "tab": self._tab_index(page), "tabs": len(self.pages)}

    def snap_envelope(self, page: Any, *, full: bool = False, find: str | None = None, scope: str | None = None,
                      navigated: bool = False, requested_host: str | None = None, extra: dict[str, Any] | None = None,
                      loading: Sequence[str] = (), max_bytes: int = core_common.DEFAULT_MAX_BYTES) -> dict[str, Any]:
        served = self._serve_snapshot(page, full=full, find=find, scope=scope)
        lines, truncated = core_snapshots.budget_lines(served.lines, max_bytes)
        env = self._base_fields(page)
        env.update({"frames": served.frames, "lines": len(served.lines), "truncated": truncated,
                    "snapshot_file": str(self.paths.last_snapshot).replace("\\", "/")})
        notes = []
        if truncated:
            notes.append(core_common._hint("truncated", self.profile))
        if loading:
            env["frames_loading"] = [core_common.sanitize_text(l, 80) for l in loading]
            notes.append(core_common._hint("frames_loading", self.profile))
        reasons = self._sign_in_reasons(page, requested_host)
        env["sign_in_suspected"] = bool(reasons)
        if reasons:
            env["sign_in_reasons"] = reasons
            notes.append(core_common._hint("sign_in", self.profile))
        if self._off_allowlist(page):
            env["off_allowlist"] = True
            notes.append(core_common._hint("off_allowlist", self.profile))
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
        env["untrusted"] = core_common.UNTRUSTED_KEYS
        env["snapshot"] = lines
        return env

    def act_envelope(self, page: Any, action: str, target_line: str | None, before: Sequence[str] | None, *,
                     navigated: bool, loading: Sequence[str], value_after: Any = None, max_bytes: int = core_common.DEFAULT_MAX_BYTES,
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
        changes = core_snapshots.diff_lines(before, served.lines)
        added, cut_a = core_snapshots.budget_lines(changes["added"], max_bytes // 2)
        removed, cut_r = core_snapshots.budget_lines(changes["removed"], max_bytes // 2)
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
            env["frames_loading"] = [core_common.sanitize_text(l, 80) for l in loading]
        if self._off_allowlist(page):
            env["off_allowlist"] = True
            env["note"] = core_common._hint("off_allowlist", self.profile)
        if extra:
            env.update(extra)
        env["untrusted"] = core_common.UNTRUSTED_KEYS
        return env

    def _new_tab_info(self) -> dict[str, Any] | None:
        if not self.new_pages:
            return None
        page = self.new_pages[-1]
        self.new_pages = []
        try:
            return {"index": self._tab_index(page), "url": core_common.redact_url(page.url)}
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
        self.cmd_started = core_common._utc_now()
        self.dialog_blocked = False
        self.unsaved_blocked = False
        self.discarded_unsaved = False
        self.new_pages = []
        self.last_activity = core_common._now()
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
            raise core_common._tag(ValueError("The page has unsaved changes; the navigation was cancelled."), core_common.ERR_UNSAVED, hint=core_common._hint("unsaved_changes", self.profile))
        if self.dialog_blocked:
            raise core_common._tag(ValueError("A dialog was dismissed during the action."), core_common.ERR_DIALOG, hint=core_common._hint("dialog", self.profile), details={"dialogs": self.dialog_log[-3:]})

    def _nav_signature(self, page: Any) -> dict[Any, int]:
        return {frame: self.nav_counts.get(frame, 0) for frame in page.frames}

    def _navigated_since(self, before: dict[Any, int]) -> bool:
        return self._nav_signature(self.page()) != before

    def _before_lines(self, page: Any) -> list[str] | None:
        served = self.served.get(page)
        return list(served.lines) if served else None

    def _value_of(self, locator: Any, masked: bool) -> Any:
        if masked:
            return "***"
        try:
            return core_common.sanitize_text(locator.evaluate(VALUE_JS, timeout=2000), 200)
        except Exception:  # noqa: BLE001
            return None

    # -- request dispatch ----------------------------------------------------------------

    def dispatch(self, cmd: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        handler = getattr(self, "h_" + cmd.replace("-", "_"), None)
        if handler is None:
            raise core_common._tag(ValueError(f"Unknown daemon command {cmd!r}."), core_common.ERR_USAGE)
        if cmd not in core_common.OFF_ALLOWLIST_OK and cmd not in policy.CONTROL and self.current is not None and self._off_allowlist(self.current):
            raise core_common._tag(ValueError("The page is outside allowed_hosts; only back, goto, snapshot, text, tabs, and stop are allowed."),
                       core_common.ERR_GUARDED, hint=core_common._hint("off_allowlist", self.profile))
        observed_before = dict(self.telemetry)
        self._begin(cmd, args)
        try:
            result = handler(args, timeout)
            result["contract_version"] = core_common.CONTRACT_VERSION
            result["observed"] = {k: self.telemetry[k] - v for k, v in observed_before.items()}
            if "action" in result and "effect" not in result:
                result["effect"] = ("navigated" if result.get("navigated") else "tab_opened" if result.get("new_tab")
                                    else "downloaded" if result.get("downloads") else "value_set" if "value_after" in result
                                    else "dom_changed" if any(result.get("changes", {}).values()) else "none")
            if "snapshot" in result:
                result["snapshot"] = result.pop("snapshot")
            return result
        finally:
            self._end()

    def _timeout_ms(self, timeout: float) -> int:
        return int(max(0.5, timeout) * 1000)

    def h_ping(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.current
        url = title = ""
        if page is not None:
            try:
                url, title = core_common.redact_url(page.url), core_common.sanitize_text(page.title(), 120)
            except Exception:  # noqa: BLE001
                pass
        return {"pong": True, "profile": self.profile, "browser_version": self.browser_version, "tabs": self._tabs(),
                "current_tab": self._tab_index(page) if page is not None else 0, "url": url, "title": title}

    def h_stop(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        self._mark_stop("stop")
        return {"stopping": True}

    def _drain_events(self, page: Any, budget: float = 0.6) -> None:
        """Let queued Playwright events (a beforeunload dialog answered after ERR_ABORTED) reach the handlers."""
        deadline = core_common._now() + budget
        while core_common._now() < deadline and not (self.unsaved_blocked or self.dialog_blocked or self.discarded_unsaved):
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
                raise core_common._tag(RuntimeError(f"{verb} did not finish within {timeout:g}s."), core_common.ERR_TIMEOUT, hint=core_common._hint("wait_timeout", self.profile)) from exc
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
        return self.snap_envelope(page, navigated=True, requested_host=host, loading=loading, max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def h_snapshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        return self.snap_envelope(page, full=bool(args.get("full")), find=args.get("find"), scope=args.get("ref"),
                                  max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def _do_action(self, args: dict[str, Any], timeout: float, action: str, run: Callable[[Any, Any], Any], *,
                   guard: bool = False, value: bool = False, allow_keys: Sequence[str] | None = None) -> dict[str, Any]:
        page = self.page()
        before_lines = self._before_lines(page)
        target = args.get("target")
        locator = self.resolve(page, target) if target else None
        target_line = (core_snapshots.find_line(before_lines, core_snapshots.normalize_target(target)) if isinstance(target, str)
                       else core_common.sanitize_text(json.dumps(target), 300)) if target else None
        masked = False
        if guard:
            secret = self._is_secret(page, locator) if locator is not None else (allow_keys is not None and self._focused_is_secret(page))
            if secret:
                if allow_keys is not None and args.get("key") in core_common.SAFE_KEYS_ON_SECRET:
                    masked = True
                else:
                    raise core_common._tag(ValueError("This field takes a password or one-time code; the tool never types those."), core_common.ERR_GUARDED,
                               hint=core_common._hint("guarded_key" if allow_keys is not None else "guarded_secret", self.profile))
        nav_before = self._nav_signature(page)
        try:
            self.action_attempted = True
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
                                 value_after=value_after, max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES,
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
            raise core_common._tag(ValueError("press needs a key name such as Enter, Tab, or Control+a."), core_common.ERR_USAGE)

        def run(page: Any, loc: Any) -> None:
            if loc is not None:
                loc.press(key, timeout=ms)
            else:
                page.keyboard.press(key)
        return self._do_action(args, timeout, "press", run, guard=True, allow_keys=list(core_common.SAFE_KEYS_ON_SECRET), value=bool(args.get("target")))

    def h_select(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        ms = self._timeout_ms(timeout)
        wanted = [str(v) for v in (args.get("values") or []) if str(v).strip()]
        if not wanted:
            raise core_common._tag(ValueError("select needs at least one value."), core_common.ERR_USAGE)
        chosen_holder: dict[str, Any] = {}

        def run(page: Any, loc: Any) -> None:
            try:
                options = loc.evaluate(OPTIONS_JS, timeout=ms)
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc, "select") from exc
            if options is None:
                raise core_common._tag(ValueError("The target is not a <select> element."), core_common.ERR_ACTION_FAILED, hint=core_common._hint("select_not_select", self.profile))
            values = []
            labels = []
            for want in wanted:
                match = next((o for o in options if str(o[0]).strip().lower() == want.strip().lower()), None) or \
                    next((o for o in options if str(o[1]).strip().lower() == want.strip().lower()), None)
                if match is None:
                    raise core_common._tag(ValueError(f"No option matches {want!r}."), core_common.ERR_NOT_FOUND, hint=core_common._hint("select_not_found", self.profile),
                               details={"options": [core_common.sanitize_text(o[0], 120) for o in options][:100]})
                values.append(str(match[1]))
                labels.append(core_common.sanitize_text(match[0], 120))
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
        max_chars = int(args.get("max_chars") or core_common.DEFAULT_TEXT_CHARS)
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
                    label = frame.name or ("(main)" if frame is page.main_frame else core_common.redact_url(frame.url))
                    chunks.append(f"--- frame: {core_common.sanitize_text(label, 80)} ---\n{body}" if frame is not page.main_frame else body)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "text") from exc
        text = core_common.CONTROL_CHARS_RE.sub("", "\n\n".join(chunks))
        truncated = len(text) > max_chars
        env = self._base_fields(page)
        env.update({"chars": len(text), "truncated": truncated, "untrusted": core_common.UNTRUSTED_KEYS, "text": text[:max_chars]})
        return env

    def h_value(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        loc = self.resolve(page, args.get("target") or "")
        masked = self._is_secret(page, loc)
        env = self._base_fields(page)
        env.update({"target": core_snapshots.normalize_target(args.get("target") or ""), "masked": masked, "value": self._value_of(loc, masked), "untrusted": core_common.UNTRUSTED_KEYS})
        return env

    def h_screenshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        ms = self._timeout_ms(timeout)
        path = Path(args.get("path") or core_common._unique_path(self.paths.shots, f"{self.profile}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"))
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
            raise core_common._tag(ValueError("wait needs a condition."), core_common.ERR_USAGE, hint=core_common._hint("no_wait_condition", self.profile))
        started = core_common._now()
        deadline = started + timeout
        satisfied: list[str] = []
        while True:
            page = self.page()
            satisfied = []
            ok = True
            if "seconds" in conditions:
                if core_common._now() - started >= float(conditions["seconds"]):
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
            if core_common._now() >= deadline:
                raise core_common._tag(RuntimeError(f"Still waiting after {timeout:g}s (url {core_common.redact_url(page.url)})."), core_common.ERR_TIMEOUT, hint=core_common._hint("wait_timeout", self.profile),
                           details={"pending": [k for k in conditions if k not in satisfied]})
            try:
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                core_common._sleep(0.5)
            self.last_activity = core_common._now()
        loading = self._settle(page, cap=2.0)
        return self.snap_envelope(page, loading=loading, extra={"waited_s": round(core_common._now() - started, 1), "satisfied": satisfied},
                                  max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def _signed_in(self, page: Any, host: str) -> bool:
        parts = urllib.parse.urlsplit(page.url)
        current = (parts.hostname or "").lower()
        host = host.lower().strip()
        if not current or not (current == host or current.endswith("." + host)):
            return False
        if core_common.SIGNIN_PATH_RE.search(parts.path or "") or core_common._is_idp_host(current):
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
        return self.snap_envelope(page, navigated=True, loading=loading, max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def h_reload(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        ms = self._timeout_ms(timeout)
        self._navigate(page, "reload", lambda: page.reload(timeout=ms, wait_until="load"), timeout)
        loading = self._settle(page)
        return self.snap_envelope(page, navigated=True, loading=loading, max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def h_tabs(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        return {"profile": self.profile, "current": self._tab_index(self.current), "tabs": self._tabs(), "untrusted": core_common.UNTRUSTED_KEYS}

    def h_tab(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        index = int(args.get("index") or 0)
        if index < 1 or index > len(self.pages):
            raise core_common._tag(ValueError(f"No tab {index}; there are {len(self.pages)}."), core_common.ERR_NOT_FOUND, hint=core_common._hint("tab_index", self.profile))
        page = self.pages[index - 1]
        if args.get("close"):
            if len(self.pages) == 1:
                raise core_common._tag(ValueError("Refusing to close the last tab."), core_common.ERR_VALIDATION, hint=core_common._hint("last_tab", self.profile))
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
                pass  # focus is reserved for needs-human handoffs
            except Exception:  # noqa: BLE001
                pass
        return self.snap_envelope(page, max_bytes=args.get("max_bytes") or core_common.DEFAULT_MAX_BYTES)

    def h_downloads(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        out = []
        for rec in self.downloads:
            if rec.saved is None:
                try:
                    dest = core_common._unique_path(self.paths.downloads, rec.suggested)
                    rec.download.save_as(str(dest))
                    rec.saved = str(dest).replace("\\", "/")
                except Exception as exc:  # noqa: BLE001
                    out.append({"file": rec.suggested, "state": "failed", "error": core_common.sanitize_text(str(exc).splitlines()[0] if str(exc) else "", 200)})
                    continue
            out.append({"file": rec.suggested, "state": "saved", "path": rec.saved, "seen": rec.seen})
        return {"profile": self.profile, "downloads": out, "folder": str(self.paths.downloads).replace("\\", "/")}

    def h_eval(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self.config.get("eval") is False:
            raise core_common._tag(ValueError("eval is disabled by config.json."), core_common.ERR_GUARDED, hint=core_common._hint("eval_disabled"))
        page = self.page()
        js = args.get("js") or ""
        if not js.strip():
            raise core_common._tag(ValueError("eval needs a JavaScript expression."), core_common.ERR_USAGE)
        ms = self._timeout_ms(timeout)
        try:
            if args.get("target"):
                locator = self.resolve(page, args["target"])
                self.action_attempted = True
                value = locator.evaluate(js, timeout=ms)
            elif args.get("frame"):
                frame = page.frame(name=args["frame"])
                if frame is None:
                    raise core_common._tag(ValueError(f"No frame named {args['frame']!r}."), core_common.ERR_NOT_FOUND)
                self.action_attempted = True
                value = frame.evaluate(js)
            else:
                self.action_attempted = True
                value = page.evaluate(js)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "eval") from exc
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            value = core_common.sanitize_text(repr(value), 2000)
        env = self._base_fields(page)
        env.update({"value": value, "untrusted": core_common.UNTRUSTED_KEYS})
        return env

    # -- serve loop ----------------------------------------------------------------------

    def write_session(self, port: int) -> None:
        existing = core_session._read_session(self.paths) or {}  # the client adds `launcher` once; heartbeats must keep it
        core_session._write_json_atomic(self.paths.session, {
            "schema": core_common.SESSION_SCHEMA, "protocol_version": core_common.PROTOCOL_VERSION, "profile": self.profile, "pid": os.getpid(), "port": port, "token": self.token,
            "launcher": existing.get("launcher"),
            "browser": self.launch.get("browser"), "browser_version": self.browser_version, "headless": bool(self.launch.get("headless")),
            "user_data_dir": str(self.paths.user_data), "log": str(self.paths.log), "started": self.started_at,
            "heartbeat": core_common._utc_now(), "busy": ({"cmd": self.current_cmd, "since": self.cmd_started} if self.current_cmd else None),
            "code_hash": self.launch.get("code_hash"), "tool_version": core_common.TOOL_VERSION, "python": sys.executable,
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
        core_session._write_json_atomic(prefs, data)
    except OSError:
        pass


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _write_startup_error(paths: core_common.ProfilePaths, error_class: str, message: str, hint: str | None) -> None:
    """The client reads this instead of guessing from an append-mode log that may carry old runs."""
    try:
        core_session._write_json_atomic(paths.dir / "startup_error.json", {"class": error_class, "message": core_common.sanitize_text(message, 600), "hint": hint})
    except OSError:
        pass


def _take_startup_error(paths: core_common.ProfilePaths) -> dict[str, Any] | None:
    path = paths.dir / "startup_error.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else None


class Daemon(runtime.OperatingMixin, actions.VerifiedActionsMixin, api.ApiMixin, reach.ReachMixin, BrowserEngine):
    """One browser owner thread, with a separate logical page set per caller session."""


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # noqa: D102
        daemon: Daemon = self.server.daemon  # type: ignore[attr-defined]
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return
        self.request.settimeout(core_common.FIRST_READ_TIMEOUT)
        try:
            line = self.rfile.readline(core_common.REQUEST_CAP + 1)
        except (OSError, ValueError):
            return
        if not line or len(line) > core_common.REQUEST_CAP:
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            self._reply({"ok": False, "error": {"class": core_common.ERR_USAGE, "message": "Malformed request."}})
            return
        if not isinstance(message, dict):
            self._reply({"ok": False, "error": {"class": core_common.ERR_USAGE, "message": "Request must be an object."}})
            return
        token = str(message.get("token") or "")
        if not hmac.compare_digest(token, daemon.token):
            daemon.dlog("rejected a request with a bad token")
            return
        if message.get("protocol_version") != core_common.PROTOCOL_VERSION:
            self._reply({"ok": False, "error": {"class": "protocol_mismatch", "message": "Client and daemon protocols differ; restart the daemon explicitly."}})
            return
        cmd = str(message.get("cmd") or "")
        args = message.get("args") or {}
        if not isinstance(args, dict):
            self._reply({"ok": False, "error": {"class": "validation", "message": "Command arguments must be an object."}})
            return
        args["_session_id"] = message.get("session_id") or "manual"
        args["_cooperative"] = True
        import math
        raw_timeout = message.get("timeout", core_common.DEFAULT_TIMEOUT)
        if type(raw_timeout) not in (float, int) or not math.isfinite(raw_timeout) or not 0 <= raw_timeout <= 86400:
            self._reply({"ok": False, "error": {"class": core_common.ERR_VALIDATION, "message": "Timeout must be finite and between 0 and 86400 seconds."}})
            return
        timeout = float(raw_timeout)
        self.request.settimeout(timeout + core_common.CLIENT_GRACE + 30)
        started = core_common._now()
        try:
            result = daemon.dispatch(cmd, args, timeout)
            reply: dict[str, Any] = {"ok": True, "result": result}
            daemon.dlog(f"{cmd} ok {round((core_common._now() - started) * 1000)}ms")
        except (ValueError, RuntimeError) as exc:
            reply = {"ok": False, "error": {"class": getattr(exc, "error_class", core_common.ERR_DAEMON), "message": str(exc),
                                            "hint": getattr(exc, "hint", None), "details": getattr(exc, "details", None)}}
            daemon.dlog(f"{cmd} error {reply['error']['class']}")
        except Exception as exc:  # noqa: BLE001 - never let a handler take the daemon down
            daemon.dlog(f"{cmd} crashed: {type(exc).__name__}")
            reply = {"ok": False, "error": {"class": core_common.ERR_DAEMON, "message": "Command failed unexpectedly; inspect its journal receipt before repeating an action.",
                                            "hint": core_common._hint("daemon_unresponsive", daemon.profile)}}
        self._reply(reply)

    def _reply(self, reply: dict[str, Any]) -> None:
        reply["protocol_version"] = core_common.PROTOCOL_VERSION
        reply["contract_version"] = core_common.CONTRACT_VERSION
        try:
            encoded = json.dumps(reply, ensure_ascii=False, default=str, separators=(",", ":"))
            if len(encoded.encode("utf-8")) + 1 > core_common.STDOUT_BUDGET:
                from .cli import _json_line
                daemon = self.server.daemon
                try:
                    if reply.get("ok"):
                        reply["result"] = json.loads(_json_line(reply["result"], daemon.profile, daemon.session_id,
                                                              byte_budget=core_common.STDOUT_BUDGET - 256))
                    else:
                        bounded = json.loads(_json_line({"error": reply["error"]}, daemon.profile, daemon.session_id,
                                                       byte_budget=core_common.STDOUT_BUDGET - 256))
                        reply["error"] = bounded["error"]
                        if bounded.get("output_id"):
                            reply["error"]["details"] = {"output_id": bounded["output_id"], "next_offset": 0}
                except (OSError, ValueError, RuntimeError) as exc:
                    result = reply.get("result", {})
                    reply = {"ok": False, "protocol_version": core_common.PROTOCOL_VERSION, "contract_version": core_common.CONTRACT_VERSION,
                             "error": {"class": getattr(exc, "error_class", "output_storage"), "message": "Cannot preserve oversized reply; inspect the journal before repeating an action.",
                                       "details": {"command_id": result.get("command_id"), "executed": result.get("executed", False)}}}
                encoded = json.dumps(reply, ensure_ascii=False, default=str, separators=(",", ":"))
            self.wfile.write((encoded + "\n").encode("utf-8"))
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
    lock = core_common._try_lock(daemon.paths.lock)
    if lock is None:
        daemon.dlog("another daemon holds the session lock; exiting")
        return 1
    server: _Server | None = None
    exit_code = 0
    try:
        daemon.started_at = core_common._utc_now()
        daemon.start_browser()
        server = _Server(("127.0.0.1", 0), _Handler)
        server.daemon = daemon
        server.timeout = 1.0
        port = server.server_address[1]
        daemon.port = port
        daemon.write_session(port)
        daemon.dlog(f"listening on 127.0.0.1:{port} browser {daemon.browser_version}")
        last_heartbeat = core_common._now()
        idle_limit = float(launch.get("idle_seconds") or 0)
        while daemon.stop_reason is None:
            server.handle_request()
            daemon.pump()
            if core_common._now() - last_heartbeat >= core_common.HEARTBEAT_SECONDS:
                daemon.write_session(port)
                last_heartbeat = core_common._now()
            if idle_limit and core_common._now() - daemon.last_activity > idle_limit:
                daemon._mark_stop("idle")
        daemon.dlog(f"stopping: {daemon.stop_reason}")
    except (ValueError, RuntimeError) as exc:
        daemon.dlog(f"fatal {getattr(exc, 'error_class', '')}: {exc}")
        _write_startup_error(daemon.paths, getattr(exc, "error_class", core_common.ERR_LAUNCH), str(exc), getattr(exc, "hint", None))
        exit_code = 1
    except Exception as exc:  # noqa: BLE001
        daemon.dlog("fatal: " + traceback.format_exc()[-1500:])
        _write_startup_error(daemon.paths, core_common.ERR_LAUNCH, f"{type(exc).__name__}: {exc}", None)
        exit_code = 1
    finally:
        daemon.close()
        if server is not None:
            try:
                server.server_close()
            except OSError:
                pass
        daemon.paths.session.unlink(missing_ok=True)
        core_common._release_lock(lock)
        daemon.dlog("exited")
    return exit_code
