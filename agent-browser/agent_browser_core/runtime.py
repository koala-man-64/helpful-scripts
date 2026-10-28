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
        self.sessions = {"manual": {"pages": self.pages, "current": None, "downloads": self.downloads, "next_tab_seq": 0, "dialog_log": self.dialog_log, "new_pages": self.new_pages}}
        self.page_owners: dict[Any, str] = {}
        self.used_sessions: set[str] = set()
        self.noted_hosts: dict[str, set[str]] = {}
        self.session_counts: dict[str, dict[str, int]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.creating_session = False
        self._cmd_target_page: Any = None
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
        state = self.sessions.setdefault(owner, {"pages": [], "current": None, "downloads": [], "next_tab_seq": 0, "dialog_log": [], "new_pages": []})
        state["next_tab_seq"] = state.get("next_tab_seq", 0) + 1
        self.tab_ids[page] = f"t{state['next_tab_seq']}"
        previous, self.pages = self.pages, state["pages"]
        self.page_owners[page] = owner
        super()._track_page(page)
        self.pages = previous
        if state["current"] is None:
            state["current"] = page

    def _on_page(self, page: Any) -> None:
        self._track_page(page)
        owner = self.page_owners[page]
        explicit_tab = owner == self.session_id and self.current_cmd is not None and self.cmd_opts.get("tab") is not None
        if not explicit_tab:
            self.sessions[owner]["current"] = page
        if owner == self.session_id:
            if not explicit_tab:
                self.current = page
            if self.current_cmd:
                self.new_pages.append(page)

    def _on_page_closed(self, page: Any) -> None:
        owner = self.page_owners.pop(page, None)
        self.tab_ids.pop(page, None)
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
        self.sessions[self.session_id].update(pages=self.pages, current=self.current, downloads=self.downloads, dialog_log=self.dialog_log, new_pages=self.new_pages)
        state = self.sessions.setdefault(sid, {"pages": [], "current": None, "downloads": [], "next_tab_seq": 0, "dialog_log": [], "new_pages": []})
        state.setdefault("dialog_log", [])
        state.setdefault("new_pages", [])
        self.session_id = sid
        self.paths = common.ProfilePaths(self.profile, self.paths.root, sid)
        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        self.pages, self.current = state["pages"], state["current"]
        self.downloads = state["downloads"]
        self.dialog_log = state["dialog_log"]
        self.new_pages = state["new_pages"]
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
        dialog_page = getattr(dialog, "page", None)
        owner = self.page_owners.get(dialog_page, self.session_id)
        same_target = (dialog_page is None or self._cmd_target_page is None or
                       dialog_page is self._cmd_target_page or dialog_page in self.new_pages)
        if owner == self.session_id and same_target:
            return super()._on_dialog(dialog)
        kind = getattr(dialog, "type", "confirm")
        if kind == "alert":
            dialog.accept()
        else:
            dialog.dismiss()
        notices = self.sessions[owner].setdefault("dialogs", [])
        notices.append({"type": kind, "handled": "accepted" if kind == "alert" else "dismissed", "background": True})
        del notices[:-20]

    def _capture(self, command_id: str, when: str, page: Any = None) -> str:
        target = page if page is not None else self.page()
        self.paths.shots.mkdir(parents=True, exist_ok=True)
        path = self.paths.shots / f"{command_id}-{when}.png"
        target.screenshot(path=str(path), mask=self._secret_masks(target))
        return str(path)

    def _secret_masks(self, page: Any) -> list[Any]:
        # The broad selector also covers sign-in controls before labels settle.
        masks = []
        for frame in page.frames:
            masks.append(frame.locator('input[type="password"],input[autocomplete="one-time-code"],input[name*="password" i],input[name*="token" i]'))
            inputs = frame.locator('input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="checkbox"]):not([type="radio"]),textarea')
            if hasattr(inputs, "all"):
                masks.extend(loc for loc in inputs.all() if self._is_secret(page, loc))
        return masks

    def h_ping(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        result = super().h_ping(args, timeout)
        if not args.get("_session_id"):
            extra_tabs = []
            for sid, state in self.sessions.items():
                if sid == self.session_id:
                    continue
                for idx, page in enumerate((p for p in state.get("pages", []) if not getattr(p, "closed", False)), start=1):
                    extra_tabs.append({
                        "id": self._tab_id(page),
                        "index": idx,
                        "session": sid,
                        "url": common.redact_url(page.url),
                        "title": common.sanitize_text(self._safe_title(page), cap=120),
                    })
            if extra_tabs:
                result["tabs"] = list(result["tabs"]) + extra_tabs
                if result.get("url") in (None, "", "about:blank"):
                    result["url"] = extra_tabs[0]["url"]
                    result["title"] = extra_tabs[0]["title"]
        return result

    def dispatch(self, cmd: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        args = dict(args)
        # Only the commit operation below declares a commit. A caller's own _commit would pass
        # the verb gates in policy.authorize and turn a recipe preview into a submission.
        args.pop("_commit", None)
        raw_session = args.get("_session_id")
        sid = common.session_identity(raw_session or "manual")
        self._activate(sid, cmd not in policy.CONTROL)
        if cmd not in {"ping", "poll", "state", "journal"}:
            self.used_sessions.add(sid)
        if cmd == "poll":
            return self._poll(args)
        if cmd == "commit":
            cmd = str(args.pop("operation", ""))
            if cmd not in {"click", "fill", "type", "press", "select", "check", "uncheck", "upload", "fill-form", "api", "run", "journal", "state"}:
                raise common._tag(ValueError("Unsupported commit operation."), common.ERR_USAGE)
            args["_commit"] = True
        command_id = secrets.token_hex(16)
        before = dict(self.session_counts.get(sid, dict.fromkeys(self.telemetry, 0)))
        record_receipt = not (cmd in {"ping", "state", "journal"} and not args.get("resolve") and not args.get("clear"))
        if record_receipt:
            self.journal.start(command_id, sid, ("commit " if args.get("_commit") else "") + cmd, args, self.current.url if self.current else "")
        executed, screenshots = False, []
        self.action_attempted = False
        expectation_deadline = common._now() + timeout
        try:
            mutating = policy.authorize(self.config, cmd, args)
            if args.get("expect") and cmd in {"run", "wait"}:
                raise common._tag(ValueError("Recipes and wait have their own verification conditions; --expect applies to ordinary actions and API calls."), common.ERR_USAGE)
            # journal and state write only SQLite rows (a clear, a handoff resolution), so they work at quota.
            if mutating and cmd not in {"journal", "state"}:
                self.journal.require_space()
            if cmd == "goto":
                args["url"] = common._check_url(args.get("url", ""), self.paths, self.config)
            if cmd == "upload":
                args["files"] = [str(self._api_path(str(path), args)) for path in args.get("files", [])]
            target_page = self.target_page(args) if args.get("tab") is not None else self.current
            self._cmd_target_page = target_page
            if args.get("_commit") and target_page and cmd not in {"journal", "state"}:
                screenshots.append(self._capture(command_id, "before", target_page))
            args["_command_id"] = command_id
            args["_deadline"] = expectation_deadline
            specs = self._expect_specs(args.get("expect") or [], target_page or self.page()) if args.get("expect") else []
            expected_page = target_page
            expected_url, expected_cursor = (expected_page.url if expected_page else ""), self.response_cursor
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
            after_page = self.target_page(args) if args.get("tab") is not None and self.pages else self.current
            if args.get("_commit") and after_page and cmd not in {"journal", "state"}:
                screenshots.append(self._capture(command_id, "after", after_page))
                result["screenshots"] = screenshots
            result["executed"] = executed
            host_page = after_page or self.current
            host = (urllib.parse.urlsplit(host_page.url).hostname or "").lower() if host_page else ""
            noted = self.noted_hosts.setdefault(sid, set())
            if (host == "service-now.com" or host.endswith(".service-now.com")) and host not in noted:
                result["site_notes"] = ["ServiceNow: use the classic record form for sn/add-work-note; verify Number before committing. Preview with run, submit with commit run, and inspect its receipt before any retry. Never enter sign-in credentials."]
                noted.add(host)
            # A handoff finishes this command; its handoff row, not the receipt, carries the open human action.
            # The reply stays pending: true with status needs_human, which tells callers to stop polling.
            needs_human = result.get("status") == "needs_human"
            status = "unknown" if result.get("write_outcome") == "unknown" else "needs_human" if needs_human else "pending" if result.get("pending") else "completed"
            if record_receipt:
                self.journal.finish(command_id, status, {**result, "pending": False} if needs_human else result)
            if "snapshot" in result:
                result["snapshot"] = result.pop("snapshot")
            return result
        except Exception as exc:
            executed = executed or self.action_attempted or bool(getattr(exc, "executed", False))
            if record_receipt:
                self.journal.finish(command_id, "unknown" if executed else "failed", {
                    "error_class": getattr(exc, "error_class", "daemon"), "executed": executed, "screenshots": screenshots,
                    "completed_fields": (getattr(exc, "details", None) or {}).get("completed_fields", [])})
            details = dict(getattr(exc, "details", None) or {})
            details["executed"] = executed
            if record_receipt:  # ping and plain state/journal reads write no receipt to point at
                details.update(command_id=command_id, receipt="state --command " + command_id)
            exc.details = details
            raise
        finally:
            self._cmd_target_page = None

    def _wait_job(self, command_id: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        conditions = args.get("conditions") or {k: args[k] for k in ("seconds", "url_contains", "signed_in", "text", "ref", "selector") if args.get(k) not in (None, "", 0)}
        if not conditions:
            raise common._tag(ValueError("wait needs a condition."), common.ERR_USAGE)
        started = common._now()
        self.jobs[command_id] = {"session": self.session_id, "page": self.target_page(args), "conditions": conditions,
                                "started": started, "deadline": args.get("_deadline", started + timeout), "max_bytes": args.get("max_bytes")}
        return self._poll({"command_id": command_id})

    def _poll(self, args: dict[str, Any]) -> dict[str, Any]:
        command_id = str(args.get("command_id") or "")
        job = self.jobs.get(command_id)
        if job is None or job["session"] != self.session_id:
            raise common._tag(ValueError("Pending operation not found for this session; inspect its journal receipt."), common.ERR_NOT_FOUND)
        try:
            if "checker" in job:
                reply = job["checker"]()
            else:
                reply = self._poll_wait(command_id, job)
            if "before_counts" in job:
                reply["observed"] = {k: self.session_counts.get(self.session_id, {}).get(k, 0) - v for k, v in job["before_counts"].items()}
                if command_id in self.jobs:
                    self.jobs[command_id]["before_counts"] = job["before_counts"]
                else:
                    self.journal.finish(command_id, "unknown" if reply.get("write_outcome") == "unknown" else "completed", reply)
            return reply
        except Exception as exc:
            self.jobs.pop(command_id, None)
            # A poll observes the original job. A different command may have set
            # action_attempted since it started; that is not this job's execution.
            executed = bool(job.get("executed"))
            self.journal.finish(command_id, "unknown" if executed else "failed", {
                "error_class": getattr(exc, "error_class", "verification_unavailable"),
                "executed": executed, "pending": False})
            details = dict(getattr(exc, "details", None) or {})
            details.update(command_id=command_id, executed=executed, receipt="state --command " + command_id)
            exc.details = details
            raise

    def _poll_wait(self, command_id: str, job: dict[str, Any]) -> dict[str, Any]:
        def require_time() -> None:
            if common._now() >= job["deadline"]:
                raise common._tag(RuntimeError("Wait deadline expired; the command was not replayed."), common.ERR_TIMEOUT,
                                  hint=common._hint("wait_timeout", self.profile))

        require_time()
        page, satisfied = job["page"], []
        for key, value in job["conditions"].items():
            if key == "seconds": ok = common._now() - job["started"] >= float(value)
            elif key == "url_contains": ok = str(value).lower() in page.url.lower()
            elif key == "signed_in": ok = self._signed_in(page, str(value))
            elif key == "text": ok = self._page_has_text(page, str(value))
            elif key == "ref":
                try:
                    ok = self.resolve(page, str(value)).is_visible()
                except ValueError as exc:
                    if getattr(exc, "error_class", None) == common.ERR_VALIDATION:
                        raise
                    ok = False
            elif key == "selector":
                ok = False
                for scope in (page, *page.frames):
                    try:
                        loc = scope.locator(str(value))
                        if loc.count() > 0 and loc.first.is_visible():
                            ok = True
                            break
                    except Exception as exc:
                        classified = self._classify(exc, f"selector {value!r}")
                        if getattr(classified, "error_class", None) in {common.ERR_VALIDATION, common.ERR_BROWSER_CLOSED}:
                            raise classified from exc
            else: raise common._tag(ValueError("Unknown wait condition."), common.ERR_USAGE)
            if ok: satisfied.append(key)
        # Conditions can themselves take time. Success must have been observed
        # before the original deadline, including after another session ran.
        require_time()
        result = {"command_id": command_id, "session_id": self.session_id, "contract_version": common.CONTRACT_VERSION}
        if len(satisfied) == len(job["conditions"]):
            loading = self._settle(page, cap=2.0)
            result.update(self.snap_envelope(page, loading=loading, max_bytes=job.get("max_bytes") or common.DEFAULT_MAX_BYTES))
            result.update(pending=False, satisfied=satisfied, waited_s=round(common._now() - job["started"], 2))
            self.jobs.pop(command_id)
            self.journal.finish(command_id, "completed", result)
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
        if args.get("handoff_id") and not handoffs:
            raise common._tag(ValueError("No handoff with that ID in this session."), common.ERR_NOT_FOUND, hint=common._hint("handoff_not_found", self.profile))
        signed_in: dict[str, bool] = {}  # one page check per host, however many handoffs wait on it
        for item in handoffs:
            if item["status"] != "needs_human":
                continue
            condition = json.loads(item["condition"])
            host = condition.get("signed_in")
            navigated = not condition.get("require_navigation") or self.current and self.nav_counts.get(self.current.main_frame, 0) > condition.get("navigation", 0)
            observed = False
            if self.current and host and navigated:
                if host not in signed_in:
                    signed_in[host] = self._signed_in(self.current, host)
                observed = signed_in[host]
            if args.get("resolve") and condition and not observed:
                raise common._tag(ValueError("The handoff completion condition has not been observed. Complete the human action and poll its state."), common.ERR_GUARDED)
            if args.get("resolve") or observed:
                self.journal.resolve_handoff(self.session_id, item["id"])
                item["status"] = "resolved"
        # Handoffs and the command page share the inspection budget; handoffs get at most half,
        # open ones first, so retry loops cannot grow the reply.
        shown, used = [], 0
        for item in sorted(handoffs, key=lambda h: h["status"] != "needs_human"):
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if shown and used + size > evidence.INSPECTION_BUDGET // 2:
                break
            shown.append(item)
            used += size
        command_page = self.journal.page(self.session_id, args.get("command_id"), int(args.get("offset", 0)), evidence.INSPECTION_BUDGET - used)
        return {"profile": self.profile, "session_id": self.session_id, "tabs": self._tabs(),
                "commands": command_page["commands"], "command_next_offset": command_page["next_offset"],
                "handoffs": shown, "handoffs_omitted": len(handoffs) - len(shown),
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
        (self.paths.workspace / "last_output.json").unlink(missing_ok=True)
        for snap_path in self.paths.workspace.glob("last_snapshot*.txt"):
            snap_path.unlink(missing_ok=True)
        return {"cleared_commands": count}

    def h_needs_human(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        condition = args.get("condition") or {}
        if not isinstance(condition, dict) or set(condition) - {"signed_in"} or any(not isinstance(v, str) or not v or len(v) > 253 for v in condition.values()):
            raise common._tag(ValueError("Human handoff supports an optional signed_in host condition."), common.ERR_VALIDATION)
        handoff_id = self.open_handoff(str(args.get("reason") or "Human action required"), condition)
        return {"status": "needs_human", "handoff_id": handoff_id, "pending": True}

    def open_handoff(self, reason: str, condition: dict[str, Any]) -> str:
        """The one way to ask the human. An identical open handoff is reused, and only a new one takes focus,
        so a retry loop neither adds rows nor keeps stealing the window."""
        handoff_id, created = self.journal.open_handoff(self.session_id, reason, condition)
        if created:
            from .session import focus_window
            focus_window(self.paths)
        return handoff_id
