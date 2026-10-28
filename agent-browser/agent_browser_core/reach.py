"""Rendered tables, annotated evidence, local diagnostics and the classic ServiceNow recipe."""
from __future__ import annotations

import re
import struct
import urllib.parse
from pathlib import Path
from typing import Any, Callable
from . import common, api

OVERLAY = "__agent_browser_refs__"
# A row's own cells, in an HTML table or an ARIA grid.
CELL_SELECTOR = ":scope > td, :scope > th, :scope > [role=cell], :scope > [role=gridcell], :scope > [role=rowheader]"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
BLOCKED_HOSTS_CAP = 20
DIAGNOSE_LAYOUT_JS = r"""() => ({
    horizontal_overflow: document.documentElement.scrollWidth > innerWidth + 1,
    unlabeled_controls: [...document.querySelectorAll('input:not([type=hidden]),select,textarea,button')].filter(el => {
        const r=el.getBoundingClientRect(); if (!r.width || !r.height) return false;
        return !(el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby')||'').split(/\s+/).some(id=>document.getElementById(id)?.textContent.trim()) ||
                 [...(el.labels||[])].some(l=>l.textContent.trim()) || el.tagName==='BUTTON' && el.textContent.trim() || ['submit','button'].includes(el.type) && el.value);
    }).map(el=>({tag:el.tagName.toLowerCase(),type:el.type||null})).slice(0,40)
})"""


def _is_loopback_url(url: str, schemes: tuple[str, ...] = ("http", "https")) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        return parts.scheme in schemes and parts.hostname in LOOPBACK_HOSTS
    except ValueError:
        return False


def _block(blocked: dict[str, Any], url: str) -> None:
    """Record a refused URL's host, sanitized (the page chose it), once, and at most BLOCKED_HOSTS_CAP hosts."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or parts.scheme + ":"
    except ValueError:
        host = url.split("//", 1)[0]
    host = common.sanitize_text(host, 120, single_line=True)
    if host not in blocked["hosts"] and len(blocked["hosts"]) < BLOCKED_HOSTS_CAP:
        blocked["hosts"].append(host)


def _refuse(blocked: dict[str, Any], url: str, top_level: bool) -> None:
    """`navigation` turns true once a top-level navigation was refused: that is what makes the page load fail."""
    _block(blocked, url)
    blocked["navigation"] = blocked["navigation"] or top_level


def _loopback_route(blocked: dict[str, Any]) -> Callable[[Any], None]:
    """A `check URL` page route: loopback http(s) continues, everything else is aborted and recorded. Playwright applies
    it in out-of-process frames too, but only to a request's first hop: it lets redirects through unrouted."""
    def handle(route: Any) -> None:
        request = route.request
        if _is_loopback_url(request.url):
            route.continue_()
            return
        try:
            top_level = request.is_navigation_request() and request.frame.parent_frame is None
        except Exception:  # noqa: BLE001 - a navigation whose frame Playwright cannot name counts as top-level
            top_level = True
        _refuse(blocked, request.url, top_level)
        route.abort("blockedbyclient")
    return handle


def _loopback_fetch(session: Any, blocked: dict[str, Any], main_frame_id: str) -> Callable[[dict[str, Any]], None]:
    """The same allowlist on the page's own DevTools Fetch domain, which pauses every hop of a redirect chain (the
    main navigation's and its subresources') where page routes do not. data: and blob: URLs are never paused."""
    def handle(event: dict[str, Any]) -> None:
        url = str((event.get("request") or {}).get("url") or "")
        try:
            if _is_loopback_url(url):
                session.send("Fetch.continueRequest", {"requestId": event["requestId"]})
                return
            _refuse(blocked, url, event.get("resourceType") == "Document" and event.get("frameId") == main_frame_id)
            session.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})
        except Exception:  # noqa: BLE001 - the page closed under the paused request
            pass
    return handle


def _loopback_socket_route(blocked: dict[str, Any], held: list[Any]) -> Callable[[Any], None]:
    """A `check URL` WebSocket route: a loopback socket (a dev server's reload channel) connects. Any other is never
    connected, so nothing leaves; it is held for _close_held, because closing it inside this handler hangs the sync API."""
    def handle(socket: Any) -> None:
        if _is_loopback_url(socket.url, ("ws", "wss")):
            socket.connect_to_server()
            return
        _block(blocked, socket.url)
        held.append(socket)
    return handle


def _layout(raw: Any) -> dict[str, Any]:
    """DIAGNOSE_LAYOUT_JS runs in the page's own world, where the page can rewrite what it returns: keep only its two
    keys, typed and sanitized, so it can neither add reply keys nor overwrite `status` or `viewport`."""
    raw = raw if isinstance(raw, dict) else {}
    controls = raw.get("unlabeled_controls") if isinstance(raw.get("unlabeled_controls"), list) else []
    return {"horizontal_overflow": raw.get("horizontal_overflow") is True,
            "unlabeled_controls": [{"tag": common.sanitize_text(item.get("tag", ""), 40, single_line=True),
                                    "type": None if item.get("type") is None else common.sanitize_text(item["type"], 40, single_line=True)}
                                   for item in controls[:40] if isinstance(item, dict)]}


def _close_held(held: list[Any]) -> None:
    while held:
        _close_quietly(held.pop())


def _close_quietly(target: Any) -> None:
    try:
        target.close()
    except Exception:  # noqa: BLE001 - already closed
        pass


class ReachMixin:
    def h_click(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        target = args.get("target")
        ref = target.get("ref") if isinstance(target, dict) else target
        page = self.target_page(args)
        if not isinstance(ref, str) or ref not in getattr(self, "table_rows", {}).get(page, set()):
            return super().h_click(args, timeout)
        if self.table_row_epochs.get(page) != self._nav_signature(page):
            self.table_rows.pop(page, None)
            self.table_row_epochs.pop(page, None)
            return super().h_click(args, timeout)
        def run(page, row):
            if not row.evaluate("el => el.tagName==='TR' || el.getAttribute('role')==='row'"):
                raise common._tag(ValueError("Table row ref changed; read table again."), common.ERR_STALE_REF)
            # Native table border-spacing can put the row center between cells. Use an
            # ordinary cell click so Playwright still checks visibility and hit targets.
            for cell in row.locator(CELL_SELECTOR).all():
                if cell.is_visible() and not cell.locator("a,button,input,select,textarea,[role=button],[role=link],[contenteditable=true]").count():
                    options = {"timeout": max(timeout, .1) * 1000, "button": "right" if args.get("right") else "left"}
                    if args.get("double"): cell.dblclick(**options)
                    else: cell.click(**options)
                    return
            raise common._tag(ValueError("Row has no safe plain cell; target the desired control explicitly."), common.ERR_GUARDED)
        return self._do_action(args, timeout, "click", run)

    def h_screenshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.journal.require_space(workspace=self.paths.workspace)
        page = self.target_page(args)
        self.paths.shots.mkdir(parents=True, exist_ok=True)
        filename = args.get("path") or str(self.paths.shots / (args.get("_command_id", common._utc_now().replace(":", "")) + ".png"))
        path = self._api_path(filename, args, must_exist=False)
        if path.suffix.lower() != ".png":
            raise common._tag(ValueError("Screenshot path must end in .png."), common.ERR_VALIDATION)
        target = self.resolve(page, args["target"]) if args.get("target") else page
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb"):
                pass
        except FileExistsError as exc:
            raise common._tag(ValueError("Screenshot destination already exists; choose a new path."), common.ERR_GUARDED) from exc
        annotated = 0
        try:
            if args.get("annotate"):
                served = self._serve_snapshot(page)
                labels = []
                for ref in dict.fromkeys(re.findall(r"\[ref=([^\]]+)\]", "\n".join(served.lines))):
                    if len(labels) >= 200: break
                    try:
                        locator = self.resolve(page, ref)
                        box = locator.bounding_box(timeout=500)
                    except Exception:
                        continue
                    if box: labels.append({"ref": ref, **box})
                page.evaluate("""labels => {
                    const root = document.createElement('div'); root.id = '__agent_browser_refs__';
                    root.setAttribute('aria-hidden','true'); root.style.cssText='position:absolute;left:0;top:0;pointer-events:none;z-index:2147483647';
                    for (const box of labels) {
                        const tag = document.createElement('span'); tag.textContent=box.ref;
                        tag.style.cssText=`position:absolute;left:${box.x+scrollX}px;top:${box.y+scrollY}px;background:#ffe34a;color:#111;font:12px monospace;padding:1px 3px;white-space:nowrap`;
                        root.append(tag);
                    }
                    document.documentElement.append(root);
                }""", labels)
                annotated = len(labels)
            options = {"path": str(path), "mask": self._secret_masks(page), "timeout": self._timeout_ms(timeout)}
            if target is page:
                options["full_page"] = bool(args.get("full_page"))
                options["scale"] = "css"
            target.screenshot(**options)
        except (ValueError, RuntimeError):
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise self._classify(exc, "screenshot") from exc
        finally:
            if args.get("annotate"):
                page.evaluate("() => document.getElementById('__agent_browser_refs__')?.remove()")
        data = path.read_bytes()
        width, height = struct.unpack(">II", data[16:24]) if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" else (None, None)
        return {"path": path.as_posix(), "file": path.as_posix(), "bytes": len(data), "width": width, "height": height, "annotated": annotated, "tab": self._tab_index(page), "tab_id": self._tab_id(page)}

    def h_table(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.target_page(args)
        table = self.resolve(page, args["target"]) if args.get("target") else self._only_table(page)
        if table.evaluate("el => el.tagName.toLowerCase() !== 'table' && !['table','grid'].includes(el.getAttribute('role'))"):
            raise common._tag(ValueError("Target is not a table or grid."), common.ERR_VALIDATION)
        columns = [common.sanitize_text(loc.inner_text(), 500) for loc in table.get_by_role("columnheader").all() if loc.is_visible()]
        frame = table.element_handle().owner_frame()
        if page not in self.served:
            self._serve_snapshot(page)
        served = self.served[page]
        prefix = next((p for p, f in served.frame_by_prefix.items() if f is frame), "")
        rows = []
        for row in table.get_by_role("row").all():
            if not row.is_visible() or row.get_by_role("columnheader").count(): continue
            cells = row.locator(CELL_SELECTOR)
            snapshot = row.aria_snapshot(mode="ai")
            match = re.search(r"\[ref=([^\]]+)\]", snapshot)
            ref = match[1] if match else None
            if ref and prefix and not ref.startswith("f"): ref = prefix + ref
            rows.append({"ref": ref, "cells": [common.sanitize_text(loc.inner_text(), 2000) for loc in cells.all()]})
        self._serve_snapshot(page)
        if not hasattr(self, "table_rows"):
            self.table_rows, self.table_row_epochs = {}, {}
        self.table_rows[page] = {row["ref"] for row in rows if row["ref"]}
        self.table_row_epochs[page] = self._nav_signature(page)
        return {"columns": columns, "rows": rows, "tab": self._tab_index(page), "tab_id": self._tab_id(page), "untrusted": ["columns", "rows"]}

    def _only_table(self, page: Any) -> Any:
        """The page's one visible table. An ARIA grid counts only when there is no table, so a date picker or
        another widget grid beside a real table never makes it ambiguous."""
        try:
            return self.resolve(page, {"role": "table"})
        except ValueError as exc:
            if getattr(exc, "error_class", None) != common.ERR_NOT_FOUND:
                raise
        try:
            return self.resolve(page, {"role": "grid"})
        except ValueError as exc:
            if getattr(exc, "error_class", None) != common.ERR_NOT_FOUND:
                raise
            raise common._tag(ValueError("No visible table or grid on the page; pass the table's ref."), common.ERR_NOT_FOUND) from exc

    def h_diagnose(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = common._check_url(str(args.get("url") or ""), self.paths, self.config)
        if not _is_loopback_url(url):
            raise common._tag(ValueError("check URL is restricted to loopback development servers."), common.ERR_GUARDED)
        self.journal.require_space(workspace=self.paths.workspace)
        previous = self.current
        page = self.ctx.new_page()
        errors = {"console_errors": [], "page_errors": [], "failed_requests": [], "http_errors": []}
        blocked: dict[str, Any] = {"hosts": [], "navigation": False}
        page.on("console", lambda msg: errors["console_errors"].append(common.sanitize_text(msg.text, 500)) if msg.type == "error" else None)
        page.on("pageerror", lambda error: errors["page_errors"].append(common.sanitize_text(str(error), 500)))
        page.on("requestfailed", lambda req: errors["failed_requests"].append(common.redact_url(req.url)))
        page.on("response", lambda response: errors["http_errors"].append({"status": response.status, "url": common.redact_url(response.url)}) if response.status >= 400 else None)
        # The page shares the signed-in profile, so nothing it loads may reach another host. Its DevTools Fetch domain
        # checks every hop of every request this page and its dedicated workers make; its route also covers
        # out-of-process (cross-site) frames, but only a request's first hop. The page's WebSockets have their own
        # route (a worker's do not). A popup is a new page none of these cover, so it is closed, and so are its own
        # popups. Service-worker fetches bypass all of them.
        popups: list[Any] = []
        sockets: list[Any] = []

        def close_popup(popup: Any) -> None:
            popups.append(popup)
            popup.on("popup", close_popup)
            _close_quietly(popup)

        page.on("popup", close_popup)
        session = None

        def stay_on_loopback() -> None:
            # A refused top-level navigation leaves an error page, not the loopback page the result would describe.
            if not _is_loopback_url(page.url):
                raise refused()

        def refused() -> ValueError:
            return common._tag(ValueError("check URL reached a non-loopback host; the request was blocked."), common.ERR_GUARDED,
                               details={"blocked_external": list(blocked["hosts"])})

        try:
            session = self.ctx.new_cdp_session(page)
            session.on("Fetch.requestPaused", _loopback_fetch(session, blocked, session.send("Page.getFrameTree")["frameTree"]["frame"]["id"]))
            session.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
            page.route("**/*", _loopback_route(blocked))
            page.route_web_socket("**/*", _loopback_socket_route(blocked, sockets))
            page.set_viewport_size({"width": 390, "height": 844})
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=max(timeout, .5) * 1000)
            except Exception as exc:
                if blocked["navigation"]:
                    raise refused() from exc
                raise
            stay_on_loopback()
            _close_held(sockets)
            page.wait_for_timeout(300)
            _close_held(sockets)
            layout = _layout(page.evaluate(DIAGNOSE_LAYOUT_JS))
            stay_on_loopback()
            self.current = page
            shot = self.h_screenshot({"_command_id": args["_command_id"]}, timeout)
            return {"status": response.status if response else None, "viewport": {"width": 390, "height": 844},
                    **layout, **{k: v[:20] for k, v in errors.items()}, "blocked_external": list(blocked["hosts"]),
                    "popups_closed": len(popups), "screenshot": shot["file"],
                    "untrusted": ["unlabeled_controls", "console_errors", "page_errors", "failed_requests", "http_errors", "blocked_external"]}
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise self._classify(exc, "check") from exc
        finally:
            for popup in popups:  # one whose close failed in the handler must not stay open as a session tab
                if not popup.is_closed():
                    _close_quietly(popup)
            page.close()  # before the session goes: detaching it first would release requests it holds paused
            if session is not None:
                try:
                    session.detach()
                except Exception:  # noqa: BLE001 - closing the page already detached it
                    pass
            self.current = previous
            self.sessions[self.session_id]["current"] = previous

    def _check_page_url(self, url: str) -> None:
        """common._check_url on a URL the page chose, such as a frame's: its refusal quotes the page's host."""
        try:
            common._check_url(url, self.paths, self.config)
        except ValueError as exc:
            exc.untrusted = ["message"]  # type: ignore[attr-defined]
            raise

    def h_run(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        recipe = args.get("recipe")
        if not isinstance(recipe, str) or recipe not in common.RECIPES or not common.RECORD_NUMBER_RE.fullmatch(str(args.get("number", ""))):
            raise common._tag(ValueError("Unsupported recipe or record number."), common.ERR_VALIDATION)
        page, number, text = self.target_page(args), args["number"], args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise common._tag(ValueError("Work note must be nonempty text."), common.ERR_VALIDATION)

        def hand_off(reason: str) -> dict[str, Any]:
            # A preview is a read: it says why the recipe cannot run, and opens no handoff and takes no focus.
            if not args.get("_commit"):
                return {"preview": {"number": number, "work_note": text}, "requires_commit": False, "blocked": True, "blocked_reason": reason}
            return {"status": "needs_human", "handoff_id": self.open_handoff(reason, {}), "pending": True}

        frame = next((f for f in page.frames if f.name == "gsft_main"), None)
        if frame is None:
            return hand_off("Recipe requires the classic gsft_main record form")
        record_url = frame.url
        self._check_page_url(record_url)
        if api.origin(record_url) != api.origin(page.url):
            raise common._tag(ValueError("Classic record form must share the working tab's origin."), common.ERR_GUARDED)
        record_id = urllib.parse.parse_qs(urllib.parse.urlsplit(record_url).query).get("sys_id", [""])[0]
        if not re.fullmatch(r"[a-fA-F0-9]{32}", record_id):
            return hand_off("Recipe could not establish a stable classic record URL")
        try:
            identity = self.resolve(page, {"label": "Number"})
            notes = self.resolve(page, {"label": "Work notes"})
            self.resolve(page, "button#sysverb_update")
        except ValueError as exc:
            if getattr(exc, "error_class", None) not in {common.ERR_NOT_FOUND, common.ERR_AMBIGUOUS, common.ERR_REF_NOT_FOUND}:
                raise
            return hand_off("Classic record controls are missing or ambiguous; inspect this layout")
        if identity.input_value().strip() != number:
            raise common._tag(ValueError("Displayed record identity does not match the requested number."), common.ERR_GUARDED)
        if notes.input_value().strip():
            return hand_off("The work-note field already contains an unsaved draft")
        if any(loc.is_visible() and loc.evaluate("el=>!el.closest('input,textarea,[contenteditable=true]')")
               for loc in frame.get_by_text(text, exact=True).all()):
            raise common._tag(ValueError("An identical note is already displayed. Inspect the record and prior receipt before another submission."), common.ERR_GUARDED)
        if not args.get("_commit"):
            return {"preview": {"number": number, "work_note": text}, "requires_commit": True}
        record_path = urllib.parse.urlsplit(record_url).path
        posts = self._watch(lambda method, url, status: method in {"POST", "PATCH"} and 200 <= status < 400
                            and api.origin(url) == api.origin(record_url) and urllib.parse.urlsplit(url).path == record_path)
        self.h_fill({"target": {"label": "Work notes"}, "text": text, "enter": False, "tab": args.get("tab")}, timeout)
        self.h_click({"target": "button#sysverb_update", "tab": args.get("tab")}, timeout)
        command_id, deadline = args["_command_id"], args.get("_deadline", common._now() + timeout)
        reloaded = False

        def verify():
            nonlocal reloaded
            posted = page in posts["pages"]
            if posted and not reloaded:
                self._check_page_url(record_url)
                current = next((f for f in page.frames if f.name == "gsft_main"), None)
                if current is not None:
                    current.goto(record_url, wait_until="domcontentloaded", timeout=min(2, max(.1, deadline-common._now()))*1000)
                    reloaded = True
            current = next((f for f in page.frames if f.name == "gsft_main"), None)
            verified = False
            if reloaded and current:
                current_id = urllib.parse.parse_qs(urllib.parse.urlsplit(current.url).query).get("sys_id", [""])[0]
                identities = current.get_by_label("Number", exact=True)
                if current_id != record_id or identities.count() != 1 or identities.input_value().strip() != number:
                    raise common._tag(RuntimeError("Record identity changed while verifying persistence; inspect the receipt before any retry."), "persistence_unverified")
                matches = current.get_by_text(text, exact=True)
                verified = any(loc.is_visible() and loc.evaluate("el=>!el.closest('input,textarea,[contenteditable=true]')") for loc in matches.all())
            result = {"command_id": command_id, "session_id": self.session_id, "contract_version": common.CONTRACT_VERSION,
                      "executed": True, "number": number, "persisted": verified, "pending": not verified}
            if verified:
                self.jobs.pop(command_id, None)
                self.journal.finish(command_id, "completed", result)
            elif common._now() >= deadline:
                self.jobs.pop(command_id, None)
                self.journal.finish(command_id, "unknown", {"executed": True, "error_class": "persistence_unverified"})
                raise common._tag(RuntimeError("Work-note persistence was not verified after reload. Inspect the record; do not replay the write."), common.ERR_TIMEOUT,
                                  details={"command_id": command_id, "executed": True})
            else:
                self.jobs[command_id] = {"session": self.session_id, "checker": verify, "executed": True, "watches": [posts]}
            return result
        return verify()
