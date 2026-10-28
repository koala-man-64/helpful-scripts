"""Semantic targets, form preflight and post-action observations."""
from __future__ import annotations

import fnmatch
import json
import re
from typing import Any, Callable
from . import common, snapshots


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
        seen = set()
        for field in fields:
            if not isinstance(field, dict) or set(field) - {"target", "operation", "value"}:
                raise common._tag(ValueError("Each field needs target, operation and value."), common.ERR_VALIDATION)
            operation = field.get("operation", "fill")
            if operation not in {"fill", "select", "check", "uncheck"}:
                raise common._tag(ValueError("Unsupported form operation."), common.ERR_USAGE)
            key = json.dumps(field.get("target"), sort_keys=True)
            if key in seen:
                raise common._tag(ValueError("Duplicate form target."), common.ERR_VALIDATION)
            seen.add(key)
            locator = self.resolve(page, field.get("target"))
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
                    match = next((o for o in options if str(o["label"]).strip().lower() == item.strip().lower()), None) or next((o for o in options if str(o["value"]).strip().lower() == item.strip().lower()), None)
                    if not match or match.get("disabled"):
                        raise common._tag(ValueError("A select value is missing or disabled; nothing was filled."), common.ERR_VALIDATION)
            if operation in {"check", "uncheck"} and "value" in field and field["value"] is not (operation == "check"):
                raise common._tag(ValueError("Checkbox values must agree with the check/uncheck operation."), common.ERR_VALIDATION)
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

    def _expect_specs(self, specs: list[Any], page: Any) -> list[dict[str, Any]]:
        parsed = []
        for raw in specs:
            if not isinstance(raw, str): raise common._tag(ValueError("Expectations must be strings."), common.ERR_VALIDATION)
            kind, sep, value = raw.partition(":")
            if not sep or kind not in {"text", "gone", "url", "response"} or not value:
                raise common._tag(ValueError("Use text:TEXT, gone:REF, url:changed, or response:METHOD URL_GLOB:STATUS."), common.ERR_USAGE)
            spec = {"kind": kind, "value": value}
            if kind == "url" and value != "changed": raise common._tag(ValueError("URL expectation must be url:changed."), common.ERR_USAGE)
            if kind == "text":
                # Text already shown does not count. A frame that cannot be read has no baseline, and 0 would let
                # its existing text pass, so the action does not run.
                spec["baseline"] = self._text_counts(page, value)
                if None in spec["baseline"].values():
                    raise common._tag(ValueError("Could not read the page text before the action, so a text: check has no baseline; nothing was done."),
                                      common.ERR_ACTION_FAILED, hint=common._hint("expect_text_baseline", self.profile))
            if kind == "gone":
                spec["element"] = self.resolve(page, value).element_handle()
                if spec["element"] is None or not spec["element"].is_visible():
                    raise common._tag(ValueError("Disappearance expectation requires an initially visible element."), common.ERR_NOT_FOUND)
            if kind == "response":
                match = re.fullmatch(r"([A-Z]+) (.+):([1-5][0-9x]{2})", value)
                if not match: raise common._tag(ValueError("Response expectation needs METHOD URL_GLOB:STATUS, e.g. POST **/table/**:2xx."), common.ERR_USAGE)
                spec.update(method=match[1], glob=match[2], status=match[3].replace("x", "?"))
                spec["watch"] = self._watch(lambda method, url, status, s=spec: method == s["method"] and fnmatch.fnmatchcase(url, s["glob"])
                                            and fnmatch.fnmatchcase(str(status), s["status"]))
            parsed.append(spec)
        return parsed

    def _do_action(self, args, timeout, action, run, **kwargs):
        return super()._do_action(args, timeout, action, run, **kwargs)

    def _verify_result(self, command_id, result, specs, page, url, deadline):
        def expired(satisfied):
            self.jobs.pop(command_id, None)
            self.journal.finish(command_id, "failed", {"error_class": "expectation_failed", "executed": bool(result.get("executed"))})
            raise common._tag(RuntimeError("Post-action expectations were not observed before the deadline; inspect the receipt before another action."), common.ERR_TIMEOUT,
                              details={"command_id": command_id, "executed": bool(result.get("executed")), "expectations": satisfied})

        def check():
            if common._now() >= deadline:
                expired([{"kind": spec["kind"], "passed": False} for spec in specs])
            satisfied = []
            check_page = self.current if self.current is not None and result.get("new_tab") else page
            for spec in specs:
                kind = spec["kind"]
                if kind == "text":
                    ok = self._text_appeared(spec, page, check_page)
                elif kind == "url":
                    ok = page.url != url or (check_page is not page and check_page.url != url)
                elif kind == "gone":
                    try:
                        ok = not spec["element"].is_visible()
                    except Exception as exc:
                        msg = str(exc).lower()
                        if any(token in msg for token in (
                            "execution context was destroyed",
                            "element is not attached",
                            "frame was detached",
                            "cannot find context",
                            "object doesn't exist",
                            "disposed",
                        )):
                            ok = True
                        else:
                            raise common._tag(RuntimeError("The disappearance check became unavailable; inspect the receipt before repeating an action."), "verification_unavailable") from None
                else:
                    ok = page in spec["watch"]["pages"] or check_page in spec["watch"]["pages"]
                satisfied.append({"kind": kind, "passed": ok})
            reply = {**result, "command_id": command_id, "session_id": self.session_id,
                     "contract_version": common.CONTRACT_VERSION, "executed": bool(result.get("executed")), "expectations": satisfied}
            if common._now() >= deadline:
                expired(satisfied)
            if all(item["passed"] for item in satisfied):
                self.jobs.pop(command_id, None)
                reply["pending"] = False
                self.journal.finish(command_id, "completed", reply)
                return reply
            self.jobs[command_id] = {"session": self.session_id, "checker": check, "executed": bool(result.get("executed", self.action_attempted)),
                                     "watches": [spec["watch"] for spec in specs if "watch" in spec]}
            reply["pending"] = True
            return reply
        return check()
