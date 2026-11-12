"""Semantic targets, form preflight and post-action observations."""
from __future__ import annotations

import fnmatch
import re
import weakref
from typing import Any, Callable
from . import common, snapshots

FORM_ELEMENT_IDENTITY_JS = "(element, candidates) => candidates.some(candidate => candidate === element)"
CHECKED_STATE_JS = "el => Boolean(el.checked)"


def _detach_cdp(session: Any) -> None:
    try:
        session.detach()
    except Exception:  # noqa: BLE001 - cleanup must not change a completed action result
        pass


class _DownloadExpectationWatch:
    """Non-blocking, target-page download completion watcher backed by public Playwright CDP APIs."""

    def __init__(self, page: Any, pattern: str):
        self.pattern = pattern
        self.states: dict[str, str] = {}
        self.closed = False
        session = page.context.new_cdp_session(page)
        self._session = session
        self._finalizer = weakref.finalize(self, _detach_cdp, session)
        watch_ref = weakref.ref(self)

        def download_started(event: dict[str, Any]) -> None:
            watch = watch_ref()
            if watch is None or watch.closed:
                return
            guid, filename = event.get("guid"), event.get("suggestedFilename", "")
            if isinstance(guid, str) and isinstance(filename, str) and fnmatch.fnmatchcase(filename, watch.pattern):
                watch.states[guid] = "inProgress"

        def download_progress(event: dict[str, Any]) -> None:
            watch = watch_ref()
            if watch is None or watch.closed:
                return
            guid, state = event.get("guid"), event.get("state")
            if isinstance(guid, str) and guid in watch.states and state in {"inProgress", "completed", "canceled"}:
                watch.states[guid] = state

        try:
            session.on("Page.downloadWillBegin", download_started)
            session.on("Page.downloadProgress", download_progress)
            session.send("Page.enable")
        except Exception:
            self.close()
            raise

    @property
    def passed(self) -> bool:
        return any(state == "completed" for state in self.states.values())

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._finalizer.alive:
            self._finalizer()


class VerifiedActionsMixin:
    def __init__(self, launch: dict[str, Any]):
        super().__init__(launch)
        # Response matchers of the command in flight; a pending job keeps its own under "watches".
        self.watching: list[dict[str, Any]] = []

    def _track_page(self, page: Any) -> None:
        super()._track_page(page)
        page.on("response", lambda response: self._response(page, response))

    def _response(self, page: Any, response: Any) -> None:
        request = getattr(response, "request", None)
        self._record_response(page, getattr(request, "method", ""), getattr(response, "url", ""), response.status)

    def _watch(self, match: Callable[[str, str, int], bool]) -> dict[str, Any]:
        """Match each later response on this session's pages as it arrives, so no other traffic can push a match out."""
        watch = {"session": self.session_id, "match": match, "pages": set()}
        self.watching.append(watch)
        return watch

    def _record_response(self, page: Any, method: str, url: str, status: int) -> None:
        owner = self.page_owners.get(page, self.session_id)
        pending = [watch for job in list(self.jobs.values()) if job["session"] == owner for watch in job.get("watches", ())]
        for watch in [*self.watching, *pending]:
            if watch["session"] != owner or page in watch["pages"]:
                continue
            try:
                hit = watch["match"](method, url, status)
            except Exception:  # noqa: BLE001 - a response the matcher cannot read is not evidence; event delivery goes on
                hit = False
            if hit:
                watch["pages"].add(page)

    def _text_counts(self, page: Any, text: str) -> dict[Any, int | None]:
        """Case-insensitive occurrences of text in each frame's rendered body text; None where it cannot be read."""
        needle, counts = text.lower(), {}
        for frame in page.frames:
            try:
                counts[frame] = frame.locator("body").inner_text(timeout=1000).lower().count(needle)
            except Exception:  # noqa: BLE001 - a detached or navigating frame
                counts[frame] = None
        return counts

    def _text_appeared(self, spec: dict[str, Any], *pages: Any) -> bool:
        """Some frame shows the text more often than before the action. A frame or tab the action opened starts at 0;
        a frame keeps its baseline when it navigates, so a reloaded page that still shows the text does not count."""
        for page in dict.fromkeys(pages):
            for frame, count in self._text_counts(page, spec["value"]).items():
                if count is not None and count > spec["baseline"].get(frame, 0):
                    return True
        return False

    def resolve(self, page: Any, target: Any) -> Any:
        if not isinstance(target, dict):
            return super().resolve(page, target)
        if set(target) == {"ref"}:
            return super().resolve(page, target["ref"])
        if not (set(target) == {"label"} or "role" in target and set(target) <= {"role", "name"}):
            raise common._tag(ValueError("Target must contain ref, label, or role with optional name."), common.ERR_USAGE)
        if any(not isinstance(v, str) or not v for v in target.values()):
            raise common._tag(ValueError("Semantic target values must be nonempty strings."), common.ERR_VALIDATION)
        matches: list[ tuple[Any, Any] ] = []
        for frame in page.frames:
            locator = (frame.get_by_label(target["label"], exact=True) if "label" in target else
                       frame.get_by_role(target["role"], **({"name": target["name"], "exact": True} if "name" in target else {})))
            for index in range(locator.count()):
                element = locator.nth(index)
                if element.is_visible():
                    matches.append((element, frame))
        if len(matches) != 1:
            candidates = []
            if matches:
                if page not in self.served:
                    self._serve_snapshot(page)
                found = []
                for element, frame in matches[:10]:
                    # The element's own line, parsed, so [ref=] text in a name is not taken for its ref.
                    own = next(iter(snapshots.parse_ai_snapshot(element.aria_snapshot(mode="ai"))), None)
                    prefix = next((p for p, f in self.served[page].frame_by_prefix.items() if f is frame), "")
                    value = own.ref if own else None
                    if value and prefix and not value.startswith("f"): value = prefix + value
                    found.append((value, frame))
                served = self._serve_snapshot(page)  # restore the full ref map after scoped candidate snapshots
                # Describe each candidate from the masked page snapshot: a name can repeat a field from anywhere on it.
                described = snapshots.describe_refs(served.raw, {ref for ref, _frame in found if ref}, mask_refs=served.mask_refs,
                                                    input_values=served.input_values, secret_refs=served.secret_refs)
                candidates = [{"ref": ref, "frame": common.sanitize_text(frame.name, 80), "description": described.get(ref, "")}
                              for ref, frame in found]
            raise common._tag(ValueError(f"Semantic target matched {len(matches)} visible elements."),
                              common.ERR_AMBIGUOUS if matches else common.ERR_NOT_FOUND, details={"candidates": candidates})
        return matches[0][0]

    def h_fill_form(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        deadline = args.get("_deadline", common._now() + timeout)
        page = self.target_page(args)
        tab_id = self._tab_id(page)
        fields = args.get("fields")
        if isinstance(fields, dict) and set(fields) == {"fields"}: fields = fields["fields"]
        if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
            raise common._tag(ValueError("Form must be a list of 1 to 100 fields."), common.ERR_VALIDATION)
        seen_refs: set[str] = set()
        handles_by_frame: list[tuple[Any, list[Any]]] = []
        handles: list[Any] = []
        try:
            for field in fields:
                if not isinstance(field, dict) or set(field) - {"target", "operation", "value"}:
                    raise common._tag(ValueError("Each field needs target, operation and value."), common.ERR_VALIDATION)
                operation = field.get("operation", "fill")
                if operation not in {"fill", "select", "check", "uncheck"}:
                    raise common._tag(ValueError("Unsupported form operation."), common.ERR_USAGE)
                target = field.get("target")
                raw_ref = target if isinstance(target, str) else target.get("ref") if isinstance(target, dict) and set(target) == {"ref"} else None
                ref_key = snapshots.normalize_target(raw_ref) if isinstance(raw_ref, str) and snapshots.is_ref(raw_ref) else None
                if ref_key is not None:
                    if ref_key in seen_refs:
                        raise common._tag(ValueError("Duplicate form target."), common.ERR_VALIDATION)
                    seen_refs.add(ref_key)

                locator = self.resolve(page, target)
                # Canonical refs catch string/{ref: ...} aliases without browser work. For
                # semantic targets, compare element handles within their owner frame so a
                # label/role target and a ref to the same control also count as duplicates.
                handle = locator.element_handle(timeout=1000)
                if handle is None:
                    raise common._tag(ValueError("A form target disappeared during preflight; nothing was filled."), common.ERR_ACTION_FAILED)
                handles.append(handle)
                frame = handle.owner_frame()
                if frame is None:
                    raise common._tag(ValueError("Could not confirm a form target's frame; nothing was filled."), common.ERR_ACTION_FAILED)
                prior = next((items for prior_frame, items in handles_by_frame if prior_frame is frame), None)
                if prior is not None:
                    duplicate = locator.evaluate(FORM_ELEMENT_IDENTITY_JS, prior, timeout=1000)
                    if duplicate:
                        raise common._tag(ValueError("Duplicate form target."), common.ERR_VALIDATION)
                    prior.append(handle)
                else:
                    handles_by_frame.append((frame, [handle]))

                verdict = self._secret_check(page, locator)
                if verdict == "unknown":
                    raise common._tag(ValueError("Could not confirm a form field is not a password or one-time-code field; nothing was filled."),
                                      common.ERR_ACTION_FAILED, hint=common._hint("secret_unverified", self.profile))
                if verdict == "secret":
                    raise common._tag(ValueError("Form contains a credential field; nothing was filled."), common.ERR_GUARDED)
                info = locator.evaluate("""el => ({tag:el.tagName.toLowerCase(),type:el.type,disabled:el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true',readOnly:el.readOnly,
                    multiple:el.multiple,options:el.options ? Array.from(el.options).map(o=>({label:o.label,value:o.value,disabled:o.disabled || (o.parentElement.tagName==='OPTGROUP' && o.parentElement.disabled)})) : []})""")
                tag, kind = info.get("tag"), info.get("type")
                valid = ((operation == "fill" and tag in {"input", "textarea"} and kind not in {"checkbox", "radio", "file", "submit", "button", "hidden", "range", "color", "image", "reset"}) or
                         operation == "select" and tag == "select" or operation == "check" and kind in {"checkbox", "radio"} or operation == "uncheck" and kind == "checkbox")
                if not valid or info.get("disabled") or info.get("readOnly"):
                    raise common._tag(ValueError("A form field is incompatible, disabled or read-only."), common.ERR_VALIDATION)
                value = field.get("value")
                if operation == "fill" and not isinstance(value, str):
                    raise common._tag(ValueError("Fill values must be strings."), common.ERR_VALIDATION)
                if operation == "select" and not (isinstance(value, str) or isinstance(value, list) and all(isinstance(v, str) for v in value)):
                    raise common._tag(ValueError("Select values must be strings or string lists."), common.ERR_VALIDATION)
                if operation == "select":
                    wanted = value if isinstance(value, list) else [value]
                    options = info.get("options", [])
                    if not wanted or not info.get("multiple") and len(wanted) > 1:
                        raise common._tag(ValueError("Select values do not match the control's multiplicity."), common.ERR_VALIDATION)
                    for item in wanted:
                        if not item.strip():
                            match = next((o for o in options if str(o["value"]) == item), None)
                        else:
                            match = next((o for o in options if str(o["label"]).strip().lower() == item.strip().lower()), None) or next((o for o in options if str(o["value"]).strip().lower() == item.strip().lower()), None)
                        if not match or match.get("disabled"):
                            raise common._tag(ValueError("A select value is missing or disabled; nothing was filled."), common.ERR_VALIDATION)
                if operation in {"check", "uncheck"} and "value" in field and field["value"] is not (operation == "check"):
                    raise common._tag(ValueError("Checkbox values must agree with the check/uncheck operation."), common.ERR_VALIDATION)
        finally:
            for handle in handles:
                try:
                    handle.dispose()
                except Exception:  # noqa: BLE001 - disposal is cleanup; preflight remains authoritative
                    pass
        completed = []
        try:
            for index, field in enumerate(fields):
                if page not in self.pages:
                    raise common._tag(RuntimeError("The original form tab closed; remaining fields were not changed."), common.ERR_BROWSER_CLOSED)
                remaining = deadline - common._now()
                if remaining <= 0: raise common._tag(RuntimeError("Form deadline expired."), common.ERR_TIMEOUT)
                operation = field.get("operation", "fill")
                options = {"target": field["target"], "text": field.get("value"), "enter": False, "tab": tab_id,
                           "values": field.get("value") if isinstance(field.get("value"), list) else [field.get("value")]}
                getattr(self, "h_" + operation)(options, remaining)
                completed.append(index)
                if page not in self.pages:
                    raise common._tag(RuntimeError("The original form tab closed; remaining fields were not changed."), common.ERR_BROWSER_CLOSED)
        except Exception as exc:
            details = dict(getattr(exc, "details", None) or {})
            details.update(completed_fields=completed, executed=bool(completed) or self.action_attempted)
            exc.details = details
            raise
        return {"ok": True, "action": "fill-form", "effect": "value_set", "completed_fields": completed, "tab": self._tab_index(page), "tab_id": self._tab_id(page)}

    def _cleanup_expectation_watches(self, specs: list[dict[str, Any]]) -> None:
        """Release external watchers; safe for explicit completion and runtime abort cleanup alike."""
        response_watches = {id(spec["watch"]) for spec in specs if "watch" in spec}
        self.watching = [watch for watch in self.watching if id(watch) not in response_watches]
        for spec in specs:
            watcher = spec.get("download_watch")
            if watcher is not None:
                try:
                    watcher.close()
                except Exception:  # noqa: BLE001 - cleanup must never replace an action/verification result
                    pass

    def _expectation_target(self, page: Any, target: str, timeout_ms: int = 1000) -> tuple[Any, dict[str, Any]]:
        locator = self.resolve(page, target)
        info = self._secret_info(locator, timeout_ms=timeout_ms)
        verdict = self._verdict(page, info, page.url)
        if verdict == "secret":
            raise common._tag(ValueError("An expectation cannot inspect a password or one-time-code field; nothing was done."), common.ERR_GUARDED)
        if verdict != "clear":
            raise common._tag(ValueError("Could not confirm an expectation target is not a credential field; nothing was done."),
                              common.ERR_ACTION_FAILED, hint=common._hint("secret_unverified", self.profile))
        return locator, info

    @staticmethod
    def _value_control(info: dict[str, Any]) -> bool:
        return info.get("tag") in {"INPUT", "TEXTAREA", "SELECT"} and not (
            info.get("tag") == "INPUT" and info.get("type") == "file")

    def _count_selector(self, page: Any, selector: str) -> int | None:
        try:
            frames = list(page.frames)
            if not frames:
                return None
            total = 0
            for frame in frames:
                total += frame.locator(selector).count()
            return total
        except Exception:  # noqa: BLE001 - a detached frame or invalidated selector is not evidence of a count
            return None

    def _expect_specs(self, specs: list[Any], page: Any) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        allowed = {"text", "gone", "url", "response", "value", "checked", "unchecked", "count", "download", "persisted"}
        grammar = ("Use text:TEXT, gone:REF, url:changed, response:METHOD URL_GLOB:STATUS, "
                   "value:TARGET=VALUE, checked:TARGET, unchecked:TARGET, count:CSS_SELECTOR=N, "
                   "download:GLOB, or persisted:#id=VALUE.")
        try:
            for raw in specs:
                if not isinstance(raw, str):
                    raise common._tag(ValueError("Expectations must be strings."), common.ERR_VALIDATION)
                kind, sep, value = raw.partition(":")
                if not sep or kind not in allowed or not value:
                    raise common._tag(ValueError(grammar), common.ERR_USAGE)
                spec: dict[str, Any] = {"kind": kind, "value": value}
                if kind == "url" and value != "changed":
                    raise common._tag(ValueError("URL expectation must be url:changed."), common.ERR_USAGE)
                if kind == "text":
                    # Text already shown does not count. A frame that cannot be read has no baseline, and 0 would let
                    # its existing text pass, so the action does not run.
                    spec["baseline"] = self._text_counts(page, value)
                    if None in spec["baseline"].values():
                        raise common._tag(ValueError("Could not read the page text before the action, so a text: check has no baseline; nothing was done."),
                                          common.ERR_ACTION_FAILED, hint=common._hint("expect_text_baseline", self.profile))
                elif kind == "gone":
                    spec["element"] = self.resolve(page, value).element_handle()
                    if spec["element"] is None or not spec["element"].is_visible():
                        raise common._tag(ValueError("Disappearance expectation requires an initially visible element."), common.ERR_NOT_FOUND)
                elif kind in {"value", "persisted"}:
                    target, delimiter, expected = value.partition("=")
                    if not delimiter or not target:
                        raise common._tag(ValueError(f"{kind}: needs TARGET=VALUE."), common.ERR_USAGE)
                    if kind == "persisted" and not re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_-]*", target):
                        raise common._tag(ValueError("persisted: requires a simple stable CSS #id target."), common.ERR_USAGE)
                    locator, info = self._expectation_target(page, target)
                    if not self._value_control(info):
                        raise common._tag(ValueError("Value expectations require a scalar input, textarea or select field."), common.ERR_VALIDATION)
                    try:
                        locator.input_value(timeout=1000)
                    except Exception:
                        raise common._tag(ValueError("Value expectation target cannot be read as a scalar form control; nothing was done."),
                                          common.ERR_VALIDATION) from None
                    spec.update(target=target, expected=expected, locator=locator)
                elif kind in {"checked", "unchecked"}:
                    locator, info = self._expectation_target(page, value)
                    if info.get("tag") != "INPUT" or info.get("type") not in {"checkbox", "radio"}:
                        raise common._tag(ValueError("Checked expectations require a native checkbox or radio input."), common.ERR_VALIDATION)
                    try:
                        locator.evaluate(CHECKED_STATE_JS, timeout=1000)
                    except Exception:
                        raise common._tag(ValueError("Checked expectation target cannot be inspected; nothing was done."), common.ERR_ACTION_FAILED) from None
                    spec.update(target=value, locator=locator)
                elif kind == "count":
                    selector, delimiter, expected = value.rpartition("=")
                    if not delimiter or not selector or not re.fullmatch(r"\d{1,9}", expected):
                        raise common._tag(ValueError("count: needs CSS_SELECTOR=NONNEGATIVE_INTEGER."), common.ERR_USAGE)
                    if self._count_selector(page, selector) is None:
                        raise common._tag(ValueError("Count selector cannot be evaluated before the action; nothing was done."), common.ERR_VALIDATION)
                    spec.update(selector=selector, expected_count=int(expected))
                elif kind == "download":
                    if not value.strip() or len(value) > 255:
                        raise common._tag(ValueError("download: needs a nonempty filename glob of at most 255 characters."), common.ERR_USAGE)
                    try:
                        spec["download_watch"] = _DownloadExpectationWatch(page, value)
                    except Exception:
                        raise common._tag(ValueError("Could not watch downloads on the target page; nothing was done."), common.ERR_ACTION_FAILED) from None
                elif kind == "response":
                    match = re.fullmatch(r"([A-Z]+) (.+):([1-5][0-9x]{2})", value)
                    if not match:
                        raise common._tag(ValueError("Response expectation needs METHOD URL_GLOB:STATUS, e.g. POST **/table/**:2xx."), common.ERR_USAGE)
                    spec.update(method=match[1], glob=match[2], status=match[3].replace("x", "?"))
                    spec["watch"] = self._watch(lambda method, url, status, s=spec: method == s["method"] and fnmatch.fnmatchcase(url, s["glob"])
                                                and fnmatch.fnmatchcase(str(status), s["status"]))
                parsed.append(spec)

            if any(spec["kind"] == "persisted" for spec in parsed):
                has_success_response = any(spec["kind"] == "response" and spec["status"].startswith("2") for spec in parsed)
                has_success_text = any(spec["kind"] == "text" for spec in parsed)
                if not has_success_response and not has_success_text:
                    raise common._tag(ValueError("persisted: requires a paired 2xx response or new text success expectation."), common.ERR_USAGE)
            return parsed
        except Exception:
            self._cleanup_expectation_watches(parsed)
            raise

    def _do_action(self, args, timeout, action, run, **kwargs):
        return super()._do_action(args, timeout, action, run, **kwargs)

    def _verify_result(self, command_id, result, specs, page, url, deadline):
        persisted = [spec for spec in specs if spec["kind"] == "persisted"]
        reload_state = {"attempted": False, "completed": False, "latched_outcomes": None}

        def expired(satisfied):
            self.jobs.pop(command_id, None)
            self._cleanup_expectation_watches(specs)
            self.journal.finish(command_id, "failed", {"error_class": "expectation_failed", "executed": bool(result.get("executed"))})
            raise common._tag(RuntimeError("Post-action expectations were not observed before the deadline; inspect the receipt before another action."), common.ERR_TIMEOUT,
                              details={"command_id": command_id, "executed": bool(result.get("executed")), "expectations": satisfied})

        def reload_for_persistence() -> None:
            remaining = deadline - common._now()
            if remaining <= 0:
                expired([{"kind": spec["kind"], "passed": False} for spec in specs])
            method = self._main_document_method(page)
            if method not in {"GET", "HEAD"}:
                raise common._tag(RuntimeError(
                    "Persistence verification cannot reload because the current main document request is not confirmed as GET or HEAD; inspect the receipt before repeating the action."
                ), "verification_unavailable", details={"executed": bool(result.get("executed"))})
            reload_state["attempted"] = True
            # Treat the verification reload as a normal guarded navigation. In particular, a beforeunload dialog
            # is dismissed so unsaved form state is not discarded just to make an expectation pass.
            new_pages = self.new_pages
            self._begin("reload", {"tab": self._tab_id(page), "discard_changes": False})
            try:
                ms = self._timeout_ms(remaining)
                self._navigate(page, "reload", lambda: page.reload(timeout=ms, wait_until="load"), remaining)
                reload_state["completed"] = True
                # The original action envelope has refs from before this reload. Replace its page state so callers
                # receive only refs minted from the current DOM while retaining the original command/receipt fields.
                for key in ("target_line", "changes", "value_after", "new_tab", "frames_loading", "note"):
                    result.pop(key, None)
                result.update(self.snap_envelope(page, navigated=True))
            finally:
                self._end()
                self.new_pages = new_pages

        def checked(spec: dict[str, Any], check_page: Any, remaining: float) -> bool:
            kind = spec["kind"]
            if kind == "text":
                return self._text_appeared(spec, page, check_page)
            if kind == "url":
                return page.url != url or (check_page is not page and check_page.url != url)
            if kind == "gone":
                try:
                    return not spec["element"].is_visible()
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(token in msg for token in (
                        "execution context was destroyed", "element is not attached", "frame was detached",
                        "cannot find context", "object doesn't exist", "disposed",
                    )):
                        return True
                    raise common._tag(RuntimeError("The disappearance check became unavailable; inspect the receipt before repeating an action."),
                                      "verification_unavailable") from None
            if kind == "response":
                return page in spec["watch"]["pages"] or check_page in spec["watch"]["pages"]
            if kind == "count":
                return self._count_selector(page, spec["selector"]) == spec["expected_count"]
            if kind == "download":
                return spec["download_watch"].passed
            timeout_ms = max(1, min(1000, int(remaining * 1000)))
            try:
                locator = spec["locator"]
                if kind == "persisted":
                    # A reload replaces documents and iframe objects. Re-resolve the stable CSS id each poll so a
                    # newly attached frame/control can satisfy this check after asynchronous hydration.
                    locator, info = self._expectation_target(page, spec["target"], timeout_ms=timeout_ms)
                    if not self._value_control(info):
                        return False
                elif self._secret_check(page, locator, timeout_ms=timeout_ms) != "clear":
                    return False
                if kind in {"value", "persisted"}:
                    actual = locator.input_value(timeout=timeout_ms)
                    return actual == spec["expected"]
                actual_checked = locator.evaluate(CHECKED_STATE_JS, timeout=timeout_ms)
                return bool(actual_checked) is (kind == "checked")
            except Exception:  # noqa: BLE001 - a transient detached target remains pending until the deadline
                return False

        def check():
            try:
                if common._now() >= deadline:
                    expired([{"kind": spec["kind"], "passed": False} for spec in specs])
                check_page = self.current if self.current is not None and result.get("new_tab") else page
                latched = reload_state["latched_outcomes"]
                if persisted and latched is not None:
                    outcomes = dict(latched)
                else:
                    outcomes = {}
                    for index, spec in enumerate(specs):
                        if spec["kind"] == "persisted":
                            continue
                        outcomes[index] = {"kind": spec["kind"], "passed": checked(spec, check_page, deadline - common._now())}
                other_passed = all(item["passed"] for item in outcomes.values())
                if persisted and other_passed and not reload_state["attempted"]:
                    # These success signals belong to the original action. Keep them latched after reload, when
                    # their message/refs may disappear as a natural result of navigation.
                    reload_state["latched_outcomes"] = dict(outcomes)
                    reload_for_persistence()
                for index, spec in enumerate(specs):
                    if spec["kind"] == "persisted":
                        outcomes[index] = {"kind": spec["kind"],
                                           "passed": reload_state["completed"] and checked(spec, page, deadline - common._now())}
                satisfied = [outcomes[index] for index in range(len(specs))]
                reply = {**result, "command_id": command_id, "session_id": self.session_id,
                         "contract_version": common.CONTRACT_VERSION, "executed": bool(result.get("executed")), "expectations": satisfied}
                if common._now() >= deadline:
                    expired(satisfied)
                if all(item["passed"] for item in satisfied):
                    self.jobs.pop(command_id, None)
                    self._cleanup_expectation_watches(specs)
                    reply["pending"] = False
                    self.journal.finish(command_id, "completed", reply)
                    return reply

                def cleanup() -> None:
                    try:
                        self._cleanup_expectation_watches(specs)
                    except Exception:  # noqa: BLE001 - runtime cancellation must remain authoritative
                        pass

                self.jobs[command_id] = {"session": self.session_id, "checker": check, "executed": bool(result.get("executed", self.action_attempted)),
                                         "watches": [spec["watch"] for spec in specs if "watch" in spec], "cleanup": cleanup}
                reply["pending"] = True
                return reply
            except Exception:
                self._cleanup_expectation_watches(specs)
                raise
        return check()
