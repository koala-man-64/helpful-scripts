"""Semantic targets, form preflight and post-action observations."""
from __future__ import annotations

import fnmatch
import json
import re
from collections import deque
from typing import Any
from . import common


class VerifiedActionsMixin:
    def __init__(self, launch: dict[str, Any]):
        super().__init__(launch)
        self.response_events = deque(maxlen=1000)
        self.response_cursor = 0

    def _track_page(self, page: Any) -> None:
        super()._track_page(page)
        page.on("response", lambda response: self._response(page, response))

    def _response(self, page: Any, response: Any) -> None:
        self.response_cursor += 1
        request = getattr(response, "request", None)
        self.response_events.append((self.response_cursor, page, getattr(request, "method", ""),
                                     getattr(response, "url", ""), response.status))

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
                for element, frame in matches[:10]:
                    snapshot = element.aria_snapshot(mode="ai")
                    ref = re.search(r"\[ref=([^\]]+)\]", snapshot)
                    prefix = next((p for p, f in self.served[page].frame_by_prefix.items() if f is frame), "")
                    value = ref.group(1) if ref else None
                    if value and prefix and not value.startswith("f"): value = prefix + value
                    candidates.append({"ref": value, "frame": common.sanitize_text(frame.name, 80),
                                       "description": common.sanitize_text(snapshot.splitlines()[0], 200)})
                self._serve_snapshot(page)  # restore the full ref map after scoped candidate snapshots
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
            if self._is_secret(page, locator):
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
            if kind == "gone":
                spec["element"] = self.resolve(page, value).element_handle()
                if spec["element"] is None or not spec["element"].is_visible():
                    raise common._tag(ValueError("Disappearance expectation requires an initially visible element."), common.ERR_NOT_FOUND)
            if kind == "response":
                match = re.fullmatch(r"([A-Z]+) (.+):([1-5][0-9x]{2})", value)
                if not match: raise common._tag(ValueError("Response expectation needs METHOD URL_GLOB:STATUS, e.g. POST **/table/**:2xx."), common.ERR_USAGE)
                spec.update(method=match[1], glob=match[2], status=match[3].replace("x", "?"))
            parsed.append(spec)
        return parsed

    def _do_action(self, args, timeout, action, run, **kwargs):
        return super()._do_action(args, timeout, action, run, **kwargs)

    def _verify_result(self, command_id, result, specs, page, url, cursor, deadline):
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
                    ok = self._page_has_text(page, spec["value"]) or (check_page is not page and self._page_has_text(check_page, spec["value"]))
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
                    ok = any(seq > cursor and (p is page or p is check_page) and method == spec["method"] and fnmatch.fnmatchcase(request_url, spec["glob"]) and fnmatch.fnmatchcase(str(status), spec["status"])
                             for seq, p, method, request_url, status in self.response_events)
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
            self.jobs[command_id] = {"session": self.session_id, "checker": check, "executed": bool(result.get("executed", self.action_attempted))}
            reply["pending"] = True
            return reply
        return check()
