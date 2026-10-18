"""Session ownership, receipts, cooperative waits and human handoffs."""
from __future__ import annotations

import json
import shutil
import secrets
import urllib.parse
from typing import Any
from . import common, evidence, policy


class OperatingMixin:
    def __init__(self, launch: dict[str, Any]):
        super().__init__(launch)
        self.config = policy.profile_config(self.config, self.profile)
        self.session_id = "manual"
        self.sessions = {"manual": {"pages": self.pages, "current": None, "downloads": self.downloads}}
        self.page_owners: dict[Any, str] = {}
        self.used_sessions: set[str] = set()
        self.noted_hosts: dict[str, set[str]] = {}
        self.session_counts: dict[str, dict[str, int]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.creating_session = False
        self.journal = evidence.Journal(self.paths.dir, self.config.get("evidence_max_bytes", evidence.DEFAULT_QUOTA))

    def start_browser(self) -> None:
        super().start_browser()
        self.sessions["manual"].update(pages=self.pages, current=self.current)

    def _track_page(self, page: Any) -> None:
        if page in self.page_owners:
            return
        owner = self.session_id
        opener = page.opener() if hasattr(page, "opener") else None
        if opener is not None:
            owner = self.page_owners.get(opener, owner)
        elif not self.creating_session and self.current_cmd is None:
            owner = "manual"
        state = self.sessions.setdefault(owner, {"pages": [], "current": None, "downloads": []})
        previous, self.pages = self.pages, state["pages"]
        self.page_owners[page] = owner
        super()._track_page(page)
        self.pages = previous
        if state["current"] is None:
            state["current"] = page

    def _on_page(self, page: Any) -> None:
        self._track_page(page)
        owner = self.page_owners[page]
        self.sessions[owner]["current"] = page
        if owner == self.session_id:
            self.current = page
            if self.current_cmd:
                self.new_pages.append(page)

    def _on_page_closed(self, page: Any) -> None:
        owner = self.page_owners.pop(page, None)
        if owner is None:
            return
        state = self.sessions[owner]
        if page in state["pages"]:
            state["pages"].remove(page)
        if state["current"] is page:
            state["current"] = state["pages"][-1] if state["pages"] else None
        if owner == self.session_id:
            self.current = state["current"]
        self.served.pop(page, None)
        if hasattr(self, "table_rows"):
            self.table_rows.pop(page, None)
            self.table_row_epochs.pop(page, None)

    def _activate(self, sid: str, create_page: bool) -> None:
        self.sessions[self.session_id].update(pages=self.pages, current=self.current, downloads=self.downloads)
        state = self.sessions.setdefault(sid, {"pages": [], "current": None, "downloads": []})
        self.session_id = sid
        self.paths = common.ProfilePaths(self.profile, self.paths.root, sid)
        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        self.pages, self.current = state["pages"], state["current"]
        self.downloads = state["downloads"]
        if create_page and self.current is None:
            self.creating_session = True
            try:
                page = self.ctx.new_page()
                self._track_page(page)
                self.current = state["current"] = page
            finally:
                self.creating_session = False

    def _observe(self, kind: str, page: Any = None) -> None:
        super()._observe(kind, page)
        owner = self.page_owners.get(page, self.session_id)
        counts = self.session_counts.setdefault(owner, dict.fromkeys(self.telemetry, 0))
        counts[kind] += 1

    def _on_download(self, download: Any, page: Any = None) -> None:
        owner = self.page_owners.get(page, self.session_id)
        previous, self.downloads = self.downloads, self.sessions[owner]["downloads"]
        try:
            super()._on_download(download, page)
        finally:
            self.downloads = previous

    def _on_dialog(self, dialog: Any) -> None:
        owner = self.page_owners.get(getattr(dialog, "page", None), self.session_id)
        if owner == self.session_id:
            return super()._on_dialog(dialog)
        kind = getattr(dialog, "type", "confirm")
        if kind == "alert":
            dialog.accept()
        else:
            dialog.dismiss()
        notices = self.sessions[owner].setdefault("dialogs", [])
        notices.append({"type": kind, "handled": "accepted" if kind == "alert" else "dismissed", "background": True})
        del notices[:-20]

    def _capture(self, command_id: str, when: str) -> str:
        self.paths.shots.mkdir(parents=True, exist_ok=True)
        path = self.paths.shots / f"{command_id}-{when}.png"
        self.page().screenshot(path=str(path), mask=self._secret_masks(self.page()))
        return str(path)

    def _secret_masks(self, page: Any) -> list[Any]:
        # The broad selector also covers sign-in controls before labels settle.
        masks = []
        for frame in page.frames:
            masks.append(frame.locator('input[type="password"],input[autocomplete="one-time-code"],input[name*="password" i],input[name*="token" i]'))
            inputs = frame.locator("input,textarea")
            if hasattr(inputs, "all"):
                masks.extend(loc for loc in inputs.all() if self._is_secret(page, loc))
        return masks

    def dispatch(self, cmd: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        args = dict(args)
        sid = common.session_identity(args.get("_session_id") or "manual")
        self._activate(sid, cmd not in policy.CONTROL)
        if cmd not in {"ping", "poll", "state", "journal"}:
            self.used_sessions.add(sid)
        if cmd == "poll":
            return self._poll(args)
        if cmd == "commit":
            cmd = str(args.pop("operation", ""))
            if cmd not in {"click", "fill", "type", "press", "select", "check", "uncheck", "upload", "api", "run", "journal", "state"}:
                raise common._tag(ValueError("Unsupported commit operation."), common.ERR_USAGE)
            args["_commit"] = True
        command_id = secrets.token_hex(16)
        before = dict(self.session_counts.get(sid, dict.fromkeys(self.telemetry, 0)))
        self.journal.start(command_id, sid, ("commit " if args.get("_commit") else "") + cmd, args, self.current.url if self.current else "")
        executed, screenshots = False, []
        self.action_attempted = False
        expectation_deadline = common._now() + timeout
        try:
            mutating = policy.authorize(self.config, cmd, args)
            if args.get("expect") and cmd in {"run", "wait"}:
                raise common._tag(ValueError("Recipes and wait have their own verification conditions; --expect applies to ordinary actions and API calls."), common.ERR_USAGE)
            if mutating and not (cmd == "journal" and args.get("clear")):
                self.journal.require_space()
            if cmd == "goto":
                args["url"] = common._check_url(args.get("url", ""), self.paths, self.config)
            if cmd == "upload":
                args["files"] = [str(self._api_path(str(path), args)) for path in args.get("files", [])]
            if args.get("_commit") and self.current and cmd != "journal":
                screenshots.append(self._capture(command_id, "before"))
            args["_command_id"] = command_id
            args["_deadline"] = expectation_deadline
            specs = self._expect_specs(args.get("expect") or [], self.page()) if args.get("expect") else []
            expected_page = self.current
            expected_url, expected_cursor = (self.current.url if self.current else ""), self.response_cursor
            if cmd == "wait" and args.get("_cooperative"):
                result = self._wait_job(command_id, args, timeout)
            else:
                result = super().dispatch(cmd, args, timeout)
                executed = self.action_attempted
            result["executed"] = executed
            if specs:
                result = self._verify_result(command_id, result, specs, expected_page, expected_url, expected_cursor, expectation_deadline)
            if result.get("pending") and command_id in self.jobs:
                self.jobs[command_id]["before_counts"] = before
            result.update(command_id=command_id, session_id=sid, contract_version=common.CONTRACT_VERSION)
            result["observed"] = {k: self.session_counts.get(sid, {}).get(k, 0) - v for k, v in before.items()}
            if args.get("_commit") and self.current and cmd != "journal":
                screenshots.append(self._capture(command_id, "after"))
                result["screenshots"] = screenshots
            result["executed"] = executed
            host = (urllib.parse.urlsplit(self.current.url).hostname or "").lower() if self.current else ""
            noted = self.noted_hosts.setdefault(sid, set())
            if (host == "service-now.com" or host.endswith(".service-now.com")) and host not in noted:
                result["site_notes"] = ["ServiceNow: use the classic record form for sn/add-work-note; verify Number before committing. Preview with run, submit with commit run, and inspect its receipt before any retry. Never enter sign-in credentials."]
                noted.add(host)
            status = "unknown" if result.get("write_outcome") == "unknown" else "pending" if result.get("pending") else "completed"
            self.journal.finish(command_id, status, result)
            if "snapshot" in result:
                result["snapshot"] = result.pop("snapshot")
            return result
        except Exception as exc:
            executed = executed or self.action_attempted or bool(getattr(exc, "executed", False))
            self.journal.finish(command_id, "unknown" if executed else "failed", {
                "error_class": getattr(exc, "error_class", "daemon"), "executed": executed, "screenshots": screenshots,
                "completed_fields": (getattr(exc, "details", None) or {}).get("completed_fields", [])})
            details = dict(getattr(exc, "details", None) or {})
            details.update(command_id=command_id, executed=executed, receipt="state --command " + command_id)
            exc.details = details
            raise

    def _wait_job(self, command_id: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        conditions = args.get("conditions") or {k: args[k] for k in ("seconds", "url_contains", "signed_in", "text", "ref", "selector") if args.get(k) not in (None, "", 0)}
        if not conditions:
            raise common._tag(ValueError("wait needs a condition."), common.ERR_USAGE)
        self.jobs[command_id] = {"session": self.session_id, "page": self.page(), "conditions": conditions,
                                "started": common._now(), "deadline": common._now() + timeout}
        return self._poll({"command_id": command_id})

    def _poll(self, args: dict[str, Any]) -> dict[str, Any]:
        command_id = str(args.get("command_id") or "")
        job = self.jobs.get(command_id)
        if job is None or job["session"] != self.session_id:
            raise common._tag(ValueError("Pending operation not found for this session; inspect its journal receipt."), common.ERR_NOT_FOUND)
        if "checker" in job:
            try:
                reply = job["checker"]()
                if "before_counts" in job:
                    reply["observed"] = {k: self.session_counts.get(self.session_id, {}).get(k, 0) - v for k, v in job["before_counts"].items()}
                    if command_id in self.jobs:
                        self.jobs[command_id]["before_counts"] = job["before_counts"]
                    else:
                        self.journal.finish(command_id, "unknown" if reply.get("write_outcome") == "unknown" else "completed", reply)
                return reply
            except Exception as exc:
                self.jobs.pop(command_id, None)
                self.journal.finish(command_id, "unknown" if job.get("executed") else "failed", {
                    "error_class": getattr(exc, "error_class", "verification_unavailable"), "executed": bool(job.get("executed"))})
                details = dict(getattr(exc, "details", None) or {})
                details.update(command_id=command_id, executed=bool(job.get("executed")), receipt="state --command " + command_id)
                exc.details = details
                raise
        page, satisfied = job["page"], []
        for key, value in job["conditions"].items():
            if key == "seconds": ok = common._now() - job["started"] >= float(value)
            elif key == "url_contains": ok = str(value).lower() in page.url.lower()
            elif key == "signed_in": ok = self._signed_in(page, str(value))
            elif key == "text": ok = self._page_has_text(page, str(value))
            elif key in {"ref", "selector"}:
                try:
                    ok = self.resolve(page, str(value)).is_visible()
                except ValueError as exc:
                    if getattr(exc, "error_class", None) == common.ERR_VALIDATION:
                        raise
                    ok = False
            else: raise common._tag(ValueError("Unknown wait condition."), common.ERR_USAGE)
            if ok: satisfied.append(key)
        result = {"command_id": command_id, "session_id": self.session_id, "contract_version": common.CONTRACT_VERSION}
        if len(satisfied) == len(job["conditions"]):
            result.update(self.snap_envelope(page, loading=[]))
            result.update(pending=False, satisfied=satisfied, waited_s=round(common._now() - job["started"], 2))
            self.jobs.pop(command_id)
            self.journal.finish(command_id, "completed", result)
        elif common._now() >= job["deadline"]:
            self.jobs.pop(command_id)
            self.journal.finish(command_id, "failed", {"error_class": "timeout", "executed": False})
            raise common._tag(RuntimeError("Wait deadline expired; the command was not replayed."), common.ERR_TIMEOUT,
                              hint=common._hint("wait_timeout", self.profile), details={"command_id": command_id, "executed": False})
        else:
            result.update(pending=True, satisfied=satisfied)
        return result

    def h_stop(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if args.get("all"):
            return super().h_stop(args, timeout)
        for page in list(self.pages):
            page.close(run_before_unload=True)
            self._check_blocks()
        self.used_sessions.discard(self.session_id)
        for command_id, job in list(self.jobs.items()):
            if job["session"] == self.session_id:
                self.journal.finish(command_id, "unknown" if job.get("executed") else "failed", {"error_class": "session_released", "executed": bool(job.get("executed"))})
                self.jobs.pop(command_id)
        if not self.used_sessions:
            self._mark_stop("stop")
        return {"released": self.session_id, "stopped": True, "stopping": self.stop_reason is not None}

    def h_state(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if args.get("resolve") and not args.get("handoff_id"):
            raise common._tag(ValueError("Resolving a handoff requires its ID and explicit human completion."), common.ERR_USAGE)
        handoffs = self.journal.handoffs(self.session_id, args.get("handoff_id"))
        for item in handoffs:
            condition = json.loads(item["condition"])
            navigated = not condition.get("require_navigation") or self.current and self.nav_counts.get(self.current.main_frame, 0) > condition.get("navigation", 0)
            observed = self.current and condition.get("signed_in") and navigated and self._signed_in(self.current, condition["signed_in"])
            if args.get("resolve") and condition and not observed:
                raise common._tag(ValueError("The handoff completion condition has not been observed. Complete the human action and poll its state."), common.ERR_GUARDED)
            if args.get("resolve") or observed:
                self.journal.resolve_handoff(self.session_id, item["id"])
                item["status"] = "resolved"
        command_page = self.journal.page(self.session_id, args.get("command_id"), int(args.get("offset", 0)))
        return {"profile": self.profile, "session_id": self.session_id, "tabs": self._tabs(),
                "commands": command_page["commands"], "command_next_offset": command_page["next_offset"], "handoffs": handoffs,
                "evidence_bytes": self.journal.usage(), "evidence_max_bytes": self.journal.quota,
                "background_dialogs": self.sessions[self.session_id].get("dialogs", []),
                "permission": self.config.get("permission", "commit"), "eval": self.config.get("eval", True) and self.config.get("permission", "commit") == "commit"}

    def h_journal(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not args.get("clear"):
            return self.journal.page(self.session_id, args.get("command_id"), int(args.get("offset", 0)))
        for command_id, job in list(self.jobs.items()):
            if job["session"] == self.session_id:
                self.jobs.pop(command_id)
        self.action_attempted = True
        count = self.journal.clear(self.session_id, args.get("_command_id", ""))
        root = self.paths.dir.resolve()
        for target in (self.paths.outputs, self.paths.shots):
            resolved = target.resolve()
            if not resolved.is_relative_to(root) or resolved == root:
                raise common._tag(ValueError("Unsafe evidence path."), common.ERR_GUARDED)
            if resolved.is_dir(): shutil.rmtree(resolved)
        for name in ("last_output.json", "last_snapshot.txt"):
            (self.paths.workspace / name).unlink(missing_ok=True)
        return {"cleared_commands": count}

    def h_needs_human(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        condition = args.get("condition") or {}
        if not isinstance(condition, dict) or set(condition) - {"signed_in"} or any(not isinstance(v, str) or not v or len(v) > 253 for v in condition.values()):
            raise common._tag(ValueError("Human handoff supports an optional signed_in host condition."), common.ERR_VALIDATION)
        handoff_id = self.journal.handoff(self.session_id, str(args.get("reason") or "Human action required"), condition)
        from .session import focus_window
        focus_window(self.paths)
        return {"status": "needs_human", "handoff_id": handoff_id, "pending": True}
