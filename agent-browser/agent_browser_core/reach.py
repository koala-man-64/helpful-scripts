"""Rendered tables, annotated evidence, local diagnostics and the classic ServiceNow recipe."""
from __future__ import annotations

import re
import struct
import urllib.parse
from pathlib import Path
from typing import Any
from . import common, api

OVERLAY = "__agent_browser_refs__"


class ReachMixin:
    def h_click(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        target = args.get("target")
        ref = target.get("ref") if isinstance(target, dict) else target
        page = self.page()
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
            for cell in row.locator(":scope > td, :scope > th, :scope > [role=cell], :scope > [role=rowheader]").all():
                if cell.is_visible() and not cell.locator("a,button,input,select,textarea,[role=button],[role=link],[contenteditable=true]").count():
                    options = {"timeout": max(timeout, .1) * 1000, "button": "right" if args.get("right") else "left"}
                    if args.get("double"): cell.dblclick(**options)
                    else: cell.click(**options)
                    return
            raise common._tag(ValueError("Row has no safe plain cell; target the desired control explicitly."), common.ERR_GUARDED)
        return self._do_action(args, timeout, "click", run)

    def h_screenshot(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.journal.require_space()
        page = self.page()
        self.paths.shots.mkdir(parents=True, exist_ok=True)
        filename = args.get("path") or str(self.paths.shots / (args.get("_command_id", common._utc_now().replace(":", "")) + ".png"))
        config = dict(self.config)
        if args.get("_cwd"): config["upload_roots"] = list(config.get("upload_roots", [])) + [args["_cwd"]]
        path = common._check_local_path(filename, self.paths, config, must_exist=False)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise common._tag(ValueError("Screenshot path must end in .png, .jpg or .jpeg."), common.ERR_VALIDATION)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb"):
                pass
        except FileExistsError as exc:
            raise common._tag(ValueError("Screenshot destination already exists; choose a new path."), common.ERR_GUARDED) from exc
        target = self.resolve(page, args["target"]) if args.get("target") else page
        annotated = 0
        try:
            if args.get("annotate"):
                served = self._serve_snapshot(page)
                labels = []
                for ref in dict.fromkeys(re.findall(r"\[ref=([^\]]+)\]", "\n".join(served.lines))):
                    if len(labels) >= 200: break
                    locator = self.resolve(page, ref)
                    box = locator.bounding_box(timeout=500)
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
            options = {"path": str(path), "mask": self._secret_masks(page)}
            if target is page: options["full_page"] = bool(args.get("full_page"))
            target.screenshot(**options)
        finally:
            if args.get("annotate"):
                page.evaluate("() => document.getElementById('__agent_browser_refs__')?.remove()")
        data = path.read_bytes()
        width, height = struct.unpack(">II", data[16:24]) if len(data) >= 24 else (None, None)
        return {"path": path.as_posix(), "file": path.as_posix(), "width": width, "height": height, "annotated": annotated}

    def h_table(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.page()
        table = self.resolve(page, args["target"]) if args.get("target") else self.resolve(page, {"role": "table"})
        if table.evaluate("el => el.tagName.toLowerCase() !== 'table' && !['table','grid'].includes(el.getAttribute('role'))"):
            raise common._tag(ValueError("Target is not a table or grid."), common.ERR_VALIDATION)
        columns = [common.sanitize_text(loc.inner_text(), 500) for loc in table.get_by_role("columnheader").all() if loc.is_visible()]
        frame = table.element_handle().owner_frame()
        served = self.served[page]
        prefix = next((p for p, f in served.frame_by_prefix.items() if f is frame), "")
        rows = []
        for row in table.get_by_role("row").all():
            if not row.is_visible() or row.get_by_role("columnheader").count(): continue
            cells = row.locator(":scope > td, :scope > th, :scope > [role=cell], :scope > [role=rowheader]")
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
        return {"columns": columns, "rows": rows, "untrusted": ["columns", "rows"]}

    def h_diagnose(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = common._check_url(str(args.get("url") or ""), self.paths, self.config)
        if urllib.parse.urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise common._tag(ValueError("check URL is restricted to loopback development servers."), common.ERR_GUARDED)
        self.journal.require_space()
        previous = self.current
        page = self.ctx.new_page()
        errors = {"console_errors": [], "page_errors": [], "failed_requests": [], "http_errors": []}
        page.on("console", lambda msg: errors["console_errors"].append(common.sanitize_text(msg.text, 500)) if msg.type == "error" else None)
        page.on("pageerror", lambda error: errors["page_errors"].append(common.sanitize_text(str(error), 500)))
        page.on("requestfailed", lambda req: errors["failed_requests"].append(common.redact_url(req.url)))
        page.on("response", lambda response: errors["http_errors"].append({"status": response.status, "url": common.redact_url(response.url)}) if response.status >= 400 else None)
        try:
            page.set_viewport_size({"width": 390, "height": 844})
            response = page.goto(url, wait_until="domcontentloaded", timeout=max(timeout, .5) * 1000)
            page.wait_for_timeout(300)
            layout = page.evaluate(r"""() => ({
                horizontal_overflow: document.documentElement.scrollWidth > innerWidth + 1,
                unlabeled_controls: [...document.querySelectorAll('input:not([type=hidden]),select,textarea,button')].filter(el => {
                    const r=el.getBoundingClientRect(); if (!r.width || !r.height) return false;
                    return !(el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby')||'').split(/\s+/).some(id=>document.getElementById(id)?.textContent.trim()) ||
                             [...(el.labels||[])].some(l=>l.textContent.trim()) || el.tagName==='BUTTON' && el.textContent.trim() || ['submit','button'].includes(el.type) && el.value);
                }).map(el=>({tag:el.tagName.toLowerCase(),type:el.type||null})).slice(0,40)
            })""")
            self.current = page
            shot = self.h_screenshot({"_command_id": args["_command_id"]}, timeout)
            return {"status": response.status if response else None, "viewport": {"width": 390, "height": 844},
                    **layout, **{k: v[:20] for k, v in errors.items()}, "screenshot": shot["file"],
                    "untrusted": ["console_errors", "page_errors", "failed_requests", "http_errors"]}
        finally:
            page.close()
            self.current = previous
            self.sessions[self.session_id]["current"] = previous

    def h_run(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if args.get("recipe") != "sn/add-work-note" or not re.fullmatch(r"(?:INC|SCTASK|RITM|CHG|PRB)\d{6,12}", str(args.get("number", ""))):
            raise common._tag(ValueError("Unsupported recipe or record number."), common.ERR_VALIDATION)
        page, number, text = self.page(), args["number"], args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise common._tag(ValueError("Work note must be nonempty text."), common.ERR_VALIDATION)
        frame = next((f for f in page.frames if f.name == "gsft_main"), None)
        if frame is None:
            return self.h_needs_human({"reason": "Recipe requires the classic gsft_main record form"}, timeout)
        record_url = frame.url
        common._check_url(record_url, self.paths, self.config)
        if api.origin(record_url) != api.origin(page.url):
            raise common._tag(ValueError("Classic record form must share the working tab's origin."), common.ERR_GUARDED)
        record_id = urllib.parse.parse_qs(urllib.parse.urlsplit(record_url).query).get("sys_id", [""])[0]
        if not re.fullmatch(r"[a-fA-F0-9]{32}", record_id):
            return self.h_needs_human({"reason": "Recipe could not establish a stable classic record URL"}, timeout)
        try:
            identity = self.resolve(page, {"label": "Number"})
            notes = self.resolve(page, {"label": "Work notes"})
            self.resolve(page, "button#sysverb_update")
        except ValueError as exc:
            if getattr(exc, "error_class", None) not in {common.ERR_NOT_FOUND, common.ERR_AMBIGUOUS, common.ERR_REF_NOT_FOUND}:
                raise
            return self.h_needs_human({"reason": "Classic record controls are missing or ambiguous; inspect this layout"}, timeout)
        if identity.input_value().strip() != number:
            raise common._tag(ValueError("Displayed record identity does not match the requested number."), common.ERR_GUARDED)
        if notes.input_value().strip():
            return self.h_needs_human({"reason": "The work-note field already contains an unsaved draft"}, timeout)
        if args.get("_commit") and any(loc.is_visible() and loc.evaluate("el=>!el.closest('input,textarea,[contenteditable=true]')")
                                       for loc in frame.get_by_text(text, exact=True).all()):
            raise common._tag(ValueError("An identical note is already displayed. Inspect the record and prior receipt before another submission."), common.ERR_GUARDED)
        if not args.get("_commit"):
            return {"preview": {"number": number, "work_note": text}, "requires_commit": True}
        cursor = self.response_cursor
        self.h_fill({"target": {"label": "Work notes"}, "text": text, "enter": False}, timeout)
        self.h_click({"target": "button#sysverb_update"}, timeout)
        command_id, deadline = args["_command_id"], args.get("_deadline", common._now() + timeout)
        reloaded = False

        def verify():
            nonlocal reloaded
            posted = any(seq > cursor and p is page and method in {"POST", "PATCH"} and 200 <= status < 400
                         and api.origin(url) == api.origin(record_url)
                         and urllib.parse.urlsplit(url).path == urllib.parse.urlsplit(record_url).path
                         for seq, p, method, url, status in self.response_events)
            if posted and not reloaded:
                common._check_url(record_url, self.paths, self.config)
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
                self.jobs[command_id] = {"session": self.session_id, "checker": verify, "executed": True}
            return result
        return verify()
