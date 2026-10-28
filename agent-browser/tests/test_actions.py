"""Daemon action handlers (h_goto, h_click, h_fill, ...) exercised via Daemon.dispatch on a fake page.

Every test here builds a real `Daemon` against a fake Playwright stack (see conftest.make_daemon)
and calls `daemon.dispatch(cmd, args, timeout)` directly - no socket, no `serve()` loop. That is
the layer these handlers actually live on; the socket/`_Handler` request plumbing around dispatch
is covered separately in test_daemon.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import agent_browser as ab
from agent_browser_core import common as core_common, daemon as core_daemon
from conftest import FakeDialog, FakeDownload, FakeElement, FakeLocator, FakePage, FakePlaywrightError, FakePlaywrightTimeoutError

# ---------------------------------------------------------------------------
# goto / snapshot envelope shape


def test_goto_exposes_iframe_refs_and_snapshot_is_the_last_key(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    result = daemon.dispatch("goto", {"url": "http://x/outer.html"}, 5.0)

    assert result["navigated"] is True
    assert result["frames"][0]["name"] == "gsft_main"
    prefix = result["frames"][0]["prefix"]
    assert prefix == "f1"
    frame_refs = [l for l in result["snapshot"] if re.search(r"\[ref=f\d+e\d+\]", l)]
    assert frame_refs and all(f"[ref={prefix}e" in l for l in frame_refs)
    assert any(l.startswith('- iframe "gsft_main"') for l in result["snapshot"])
    assert result["untrusted"] == core_common.UNTRUSTED_KEYS
    assert list(result.keys())[-1] == "snapshot"
    # the filter re-indents: the root wrapper is dropped, so top-level lines start at depth 0
    assert result["snapshot"][0].startswith("- ") and not result["snapshot"][0].startswith("  -")


def _ref(env: dict, pattern: str) -> str:
    for line in env["snapshot"]:
        if re.search(pattern, line):
            return re.search(r"\[ref=([^\]]+)\]", line).group(1)  # type: ignore[union-attr]
    raise AssertionError(f"no line matches {pattern!r}: {env['snapshot']}")


# ---------------------------------------------------------------------------
# click: compact ACT envelope, stale refs, healing


def test_click_returns_compact_act_envelope(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    env = daemon.dispatch("goto", {"url": "http://x/outer.html"}, 5.0)
    ref = _ref(env, r'button "Outer button"')

    result = daemon.dispatch("click", {"target": ref}, 5.0)

    assert result["ok"] is True and result["action"] == "click"
    assert result["target_line"] and f"[ref={ref}]" in result["target_line"]
    assert result["changes"] == {"added": [], "removed": []}
    assert result["refs_valid"] is True
    assert "frames" not in result  # compact form: no full snapshot re-sent


def test_click_after_frame_navigation_is_stale_ref(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    env = daemon.dispatch("goto", {"url": "http://x/outer.html"}, 5.0)
    save_ref = _ref(env, r'button "Save"')
    inner_frame = page.frames[1]

    page.emit("framenavigated", inner_frame)

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": save_ref}, 5.0)
    assert exc.value.error_class == core_common.ERR_STALE_REF


def _navigate_during_frame_map(daemon, monkeypatch, *, once: bool) -> None:
    """The iframe commits a new document after the snapshot read it and before the serve finishes."""
    original, fired = daemon._frame_map, []

    def racing(page, lines):
        if not (once and fired):
            fired.append(True)
            page.emit("framenavigated", page.frames[1])
        return original(page, lines)
    monkeypatch.setattr(daemon, "_frame_map", racing)


def test_frame_that_navigates_while_the_snapshot_is_taken_leaves_its_refs_stale(make_daemon, outer_text: str, monkeypatch) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    _navigate_during_frame_map(daemon, monkeypatch, once=True)

    env = daemon.dispatch("snapshot", {}, 5.0)

    assert env["refs_valid"] is False and env["navigated"] is True
    assert core_common._hint("refs_moved") in env["note"]
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": _ref(env, r'button "Save"')}, 5.0)
    assert exc.value.error_class == core_common.ERR_STALE_REF
    assert page.elements["f1e4"].calls == []
    # Only the moved frame's refs are stale; the main frame's still resolve.
    assert daemon.dispatch("click", {"target": "e4"}, 5.0)["refs_valid"] is True


def test_action_reply_reports_stale_refs_when_a_frame_navigates_during_its_snapshot(make_daemon, outer_text: str, monkeypatch) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    daemon.dispatch("snapshot", {}, 5.0)
    _navigate_during_frame_map(daemon, monkeypatch, once=False)

    result = daemon.dispatch("click", {"target": "e4"}, 5.0)

    assert "changes" in result  # the compact reply, which used to claim refs_valid: true
    assert result["refs_valid"] is False and result["navigated"] is True and result["effect"] == "navigated"
    assert core_common._hint("refs_moved") in result["note"]


@pytest.mark.parametrize("belongs_to_page", [True, False])
def test_prefixed_main_frame_refs_use_the_element_owner(make_daemon, monkeypatch, belongs_to_page):
    from types import SimpleNamespace
    daemon, ctx, page = make_daemon(snapshot_text='- generic [ref=f9e1]:\n  - button "Save" [ref=f9e2]')
    owner = page.main_frame if belongs_to_page else object()
    monkeypatch.setattr(FakeLocator, "element_handle", lambda self, timeout=None: SimpleNamespace(owner_frame=lambda: owner))
    daemon.dispatch("snapshot", {}, 1)
    if belongs_to_page:
        assert daemon.dispatch("click", {"target": "f9e2"}, 1)["ok"]
        page.emit("framenavigated", page.main_frame)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "f9e2"}, 1)
    assert exc.value.error_class == core_common.ERR_STALE_REF


def test_click_heals_a_zero_count_ref_before_giving_up(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    daemon.dispatch("snapshot", {}, 5.0)
    el = page.elements["e4"]  # "Outer button"
    el.exists = False
    el.heal_on_next_snapshot = True

    result = daemon.dispatch("click", {"target": "e4"}, 5.0)

    assert result["ok"] is True
    # initial snapshot + the heal inside _ref_target + act_envelope's post-action re-serve for the diff
    assert page.snapshot_calls == 3


def test_invalid_frame_error_text_becomes_stale_ref(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    from conftest import FakePlaywrightError

    page.elements["f1e4"].raise_on["count"] = FakePlaywrightError(
        "Invalid frame in aria-ref selector 'f1e4' does not match any element"
    )
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "f1e4"}, 5.0)
    assert exc.value.error_class == core_common.ERR_STALE_REF


def test_strict_mode_violation_becomes_ambiguous(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    from conftest import FakePlaywrightError

    page.elements["e4"].raise_on["click"] = FakePlaywrightError("strict mode violation: locator resolved to 2 elements")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert exc.value.error_class == core_common.ERR_AMBIGUOUS


def test_not_a_checkbox_becomes_action_failed_with_hint(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    from conftest import FakePlaywrightError

    page.elements["e4"].raise_on["check"] = FakePlaywrightError("Not a checkbox or radio button")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("check", {"target": "e4"}, 5.0)
    assert exc.value.error_class == core_common.ERR_ACTION_FAILED
    assert exc.value.hint == core_common._hint("check_not_checkbox", daemon.profile)


# ---------------------------------------------------------------------------
# fill / type: secret guard, value_after, clear-before-type


def test_fill_on_password_field_is_guarded_and_never_leaks_the_text(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]  # "Caller" textbox, repurposed as a password field for this test
    el.secret_info = {"type": "password"}

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("fill", {"target": "f1e3", "text": "hunter2"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert "hunter2" not in str(exc.value)
    assert el.value == ""  # fill() was never called


def test_fill_on_a_normal_field_returns_value_after(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    result = daemon.dispatch("fill", {"target": "f1e3", "text": "Abel"}, 5.0)
    assert result["value_after"] == "Abel"
    assert page.elements["f1e3"].value == "Abel"


def test_type_clears_first_unless_append(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    daemon.dispatch("type", {"target": "f1e3", "text": "hi"}, 5.0)
    assert page.elements["f1e3"].calls == [("fill", ""), ("press_sequentially", "hi")]

    page.elements["f1e3"].calls.clear()
    daemon.dispatch("type", {"target": "f1e3", "text": "!", "append": True}, 5.0)
    assert page.elements["f1e3"].calls == [("press_sequentially", "!")]


# ---------------------------------------------------------------------------
# select


def test_select_matches_label_case_insensitively_then_value(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    el.options = [("Choice A", "a", False), ("Choice B", "b", True)]

    result = daemon.dispatch("select", {"target": "f1e3", "values": ["choice a"]}, 5.0)
    assert result["selected"] == ["Choice A"]
    assert el.calls[-1] == ("select_option", ["a"])

    result2 = daemon.dispatch("select", {"target": "f1e3", "values": ["b"]}, 5.0)  # matches by value
    assert result2["selected"] == ["Choice B"]


def test_select_unknown_value_is_not_found_with_options_in_details(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["f1e3"].options = [("Choice A", "a", False)]
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("select", {"target": "f1e3", "values": ["zzz"]}, 5.0)
    assert exc.value.error_class == core_common.ERR_NOT_FOUND
    assert exc.value.details["options"] == ["Choice A"]


def test_select_on_a_non_select_element_is_action_failed(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["f1e3"].options = None
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("select", {"target": "f1e3", "values": ["a"]}, 5.0)
    assert exc.value.error_class == core_common.ERR_ACTION_FAILED
    assert exc.value.hint == core_common._hint("select_not_select", daemon.profile)


# ---------------------------------------------------------------------------
# press


def test_press_with_no_target_uses_page_keyboard(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    daemon.dispatch("press", {"key": "Enter"}, 5.0)
    assert page.keyboard.presses == ["Enter"]


def test_press_on_secret_target_guards_unsafe_keys_but_allows_tab(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["f1e3"].secret_info = {"type": "password"}

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("press", {"key": "a", "target": "f1e3"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_key", daemon.profile)

    result = daemon.dispatch("press", {"key": "Tab", "target": "f1e3"}, 5.0)
    assert result["ok"] is True
    assert result["value_after"] == "***"


# ---------------------------------------------------------------------------
# credential guard: a field that cannot be checked is treated as a credential field


@pytest.mark.parametrize("cmd, extra", [("fill", {"text": "Abel"}), ("type", {"text": "Abel"}),
                                        ("select", {"values": ["a"]}), ("press", {"key": "a"})])
def test_guarded_verbs_fail_closed_when_the_secret_check_errors(make_daemon, outer_text: str, cmd: str, extra: dict) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    el.options = [("Choice A", "a", False)]
    el.raise_on["secret_check"] = FakePlaywrightError("Execution context was destroyed")

    with pytest.raises(ValueError) as exc:
        daemon.dispatch(cmd, {"target": "f1e3", **extra}, 5.0)
    assert exc.value.error_class == core_common.ERR_ACTION_FAILED
    assert exc.value.hint == core_common._hint("secret_unverified", daemon.profile)
    assert exc.value.details["executed"] is False
    assert el.calls == [] and el.value == ""


def test_unverifiable_field_still_takes_safe_keys_and_reads_back_masked(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    el.value = "typed-by-the-user"
    el.raise_on["secret_check"] = FakePlaywrightError("Execution context was destroyed")

    result = daemon.dispatch("press", {"key": "Tab", "target": "f1e3"}, 5.0)
    assert el.calls == [("press", "Tab")] and result["value_after"] == "***"
    read = daemon.dispatch("value", {"target": "f1e3"}, 5.0)
    assert read["masked"] is True and read["value"] == "***"


def test_targetless_press_fails_closed_when_a_frame_check_errors(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    inner = page.frames[1]
    inner.active_info = FakePlaywrightError("Execution context was destroyed")

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("press", {"key": "a"}, 5.0)
    assert exc.value.error_class == core_common.ERR_ACTION_FAILED
    assert exc.value.details["executed"] is False
    assert page.keyboard.presses == []

    daemon.dispatch("press", {"key": "Enter"}, 5.0)  # safe keys still go through
    assert page.keyboard.presses == ["Enter"]

    # A stale active element in a document without focus proves nothing about the frame that errored...
    page.main_frame.active_info = {"tag": "INPUT", "type": "text", "text": "Caller", "has_focus": False}
    with pytest.raises(ValueError) as stale:
        daemon.dispatch("press", {"key": "a"}, 5.0)
    assert stale.value.error_class == core_common.ERR_ACTION_FAILED

    # ...but only one document has focus, so a normal field in it rules the errored frame out.
    page.main_frame.active_info = {"tag": "INPUT", "type": "text", "text": "Caller", "has_focus": True}
    daemon.dispatch("press", {"key": "a"}, 5.0)
    assert page.keyboard.presses == ["Enter", "a"]

    # A detached frame cannot hold focus, so its error does not count.
    page.main_frame.active_info = None
    inner.detached = True
    daemon.dispatch("press", {"key": "b"}, 5.0)
    assert page.keyboard.presses == ["Enter", "a", "b"]


def test_targetless_press_sees_a_credential_inside_an_open_shadow_root(make_daemon, outer_text: str) -> None:
    # The fakes cannot run page JS; test_live drives the shadow-root walk in a real browser.
    assert "shadowRoot.activeElement" in core_daemon.ACTIVE_SECRET_JS
    assert all(f"'{tag}'" in core_daemon.ACTIVE_SECRET_JS for tag in ("IFRAME", "FRAME", "OBJECT", "EMBED"))
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.main_frame.active_info = {"tag": "INPUT", "type": "password", "text": ""}

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("press", {"key": "a"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_key", daemon.profile)
    assert page.keyboard.presses == []

    # Focus inside a closed shadow root cannot be inspected (ACTIVE_SECRET_JS reports it as unknown).
    page.main_frame.active_info = {"unknown": True, "has_focus": True}
    with pytest.raises(ValueError) as closed:
        daemon.dispatch("press", {"key": "a"}, 5.0)
    assert closed.value.error_class == core_common.ERR_ACTION_FAILED
    assert page.keyboard.presses == []


@pytest.mark.parametrize("cmd, extra, hint", [("fill", {"text": "Abel"}, "guarded_focus"), ("type", {"text": "Abel", "append": True}, "guarded_focus"),
                                              ("press", {"key": "a"}, "guarded_key")])
def test_keys_never_land_in_a_focused_credential_through_a_clear_target(make_daemon, outer_text: str, cmd: str, extra: dict, hint: str) -> None:
    # Playwright focuses the target and then types into whatever holds focus; a target that cannot take
    # focus (a heading, a disabled field) leaves the keys on the previously focused password field.
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    page.frames[1].active_info = {"tag": "INPUT", "type": "password", "text": "", "has_focus": True}

    with pytest.raises(ValueError) as exc:
        daemon.dispatch(cmd, {"target": "f1e3", **extra}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint(hint, daemon.profile)
    assert exc.value.details["executed"] is False
    assert el.calls == []


def test_safe_keys_may_still_land_on_a_focused_credential(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.frames[1].active_info = {"tag": "INPUT", "type": "password", "text": "", "has_focus": True}
    result = daemon.dispatch("press", {"key": "Tab", "target": "f1e3"}, 5.0)
    assert page.elements["f1e3"].calls == [("press", "Tab")] and result["value_after"] == "***"


def test_fill_form_reports_an_unverifiable_field_as_retryable(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    el.raise_on["secret_check"] = FakePlaywrightError("Execution context was destroyed")

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("fill-form", {"fields": [{"target": "f1e3", "value": "Abel"}]}, 5.0)
    assert exc.value.error_class == core_common.ERR_ACTION_FAILED
    assert exc.value.hint == core_common._hint("secret_unverified", daemon.profile)
    assert exc.value.details["executed"] is False
    assert el.calls == [] and el.value == ""


# ---------------------------------------------------------------------------
# typed values and link targets in snapshots

PASSWORD_PAGE = (
    "- generic [active] [ref=e1]:\n"
    '  - textbox "Field A" [ref=e2]: hunter2\n'
    '  - textbox "Name" [ref=e3]: Abel\n'
    '  - button "Show" [ref=e4]\n'
)
PASSWORD_INFO = {"tag": "INPUT", "type": "password", "autocomplete": "", "inputmode": "", "maxlength": -1, "text": "Field A", "contenteditable": False}


def test_snapshot_masks_password_value_everywhere_it_is_emitted(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=PASSWORD_PAGE)
    page.elements["e2"].secret_info = PASSWORD_INFO  # a neutral name: the type is what makes it a credential
    monkeypatch.setattr(FakeLocator, "aria_snapshot", lambda locator, mode="ai": page.snapshot_text, raising=False)
    for args in ({}, {"full": True}, {"find": "Field"}, {"ref": "e1"}):
        env = daemon.dispatch("snapshot", args, 5.0)
        assert "hunter2" not in json.dumps(env), args
        assert "hunter2" not in daemon.paths.last_snapshot.read_text(encoding="utf-8"), args
        assert '- textbox "Field A" [ref=e2]: ***' in [line.strip() for line in env["snapshot"]], args
    assert '- textbox "Name" [ref=e3]: Abel' in daemon.dispatch("snapshot", {}, 5.0)["snapshot"]
    assert daemon.dispatch("snapshot", {"find": "hunter2"}, 5.0)["snapshot"] == []
    # A person types more while the agent clicks elsewhere: the change list stays clean.
    page.elements["e4"].click_side_effect = lambda: setattr(page, "snapshot_text", PASSWORD_PAGE.replace("hunter2", "hunter2-more"))
    daemon.dispatch("snapshot", {}, 5.0)
    result = daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert result["changes"] == {"added": [], "removed": []} and "hunter2" not in json.dumps(result)
    # Clicking the field itself is not guarded; its target line is masked too.
    clicked = daemon.dispatch("click", {"target": "e2"}, 5.0)
    assert clicked["target_line"] == '- textbox "Field A" [ref=e2]: ***' and "hunter2" not in json.dumps(clicked)


def test_snapshot_removes_a_password_from_the_table_row_around_it(make_daemon) -> None:
    layout = ("- table [ref=e1]:\n  - rowgroup [ref=e2]:\n    - row \"PIN hunter2\" [ref=e3]:\n      - cell \"PIN\" [ref=e4]\n"
              "      - cell \"hunter2\" [ref=e5]:\n        - textbox [ref=e6]: hunter2\n- textbox \"Repeat hunter2\" [ref=e7]: x\n")
    daemon, ctx, page = make_daemon(snapshot_text=layout)
    page.elements["e6"].secret_info = PASSWORD_INFO
    env = daemon.dispatch("snapshot", {"full": True}, 5.0)
    assert "hunter2" not in json.dumps(env) and "hunter2" not in daemon.paths.last_snapshot.read_text(encoding="utf-8")
    assert '    - row "PIN ***" [ref=e3]:' in env["snapshot"] and '- textbox "Repeat ***" [ref=e7]: x' in env["snapshot"]


def test_snapshot_overflow_output_carries_the_masked_text(make_daemon, monkeypatch) -> None:
    from agent_browser_core import cli
    padding = "".join(f'  - button "Padding button {i}" [ref=e{100 + i}]\n' for i in range(900))
    daemon, ctx, page = make_daemon(snapshot_text=PASSWORD_PAGE + padding)
    page.elements["e2"].secret_info = PASSWORD_INFO
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(daemon.paths.root))
    env = daemon.dispatch("snapshot", {"full": True, "max_bytes": 200000}, 5.0)
    bounded = json.loads(cli._json_line(env))
    assert bounded["truncated"] is True and bounded["output_id"]
    saved = Path(bounded["output_file"]).read_text(encoding="utf-8")
    assert "Padding button 899" in saved and '[ref=e2]: ***' in saved and "hunter2" not in saved


def test_snapshot_masks_values_it_cannot_clear(make_daemon, monkeypatch) -> None:
    fields = '- textbox "First" [ref=e2]: one\n- textbox "Second" [ref=e3]: two\n- textbox "Third" [ref=e4]: three\n'
    daemon, ctx, page = make_daemon(snapshot_text=fields)
    del page.elements["e4"]  # a ref that no longer resolves cannot be checked
    timeouts: list[int] = []
    original = daemon._secret_info
    monkeypatch.setattr(daemon, "_secret_info", lambda locator, timeout_ms=3000: timeouts.append(timeout_ms) or original(locator, timeout_ms))
    assert daemon.dispatch("snapshot", {}, 5.0)["snapshot"] == [
        '- textbox "First" [ref=e2]: one', '- textbox "Second" [ref=e3]: two', '- textbox "Third" [ref=e4]: ***']
    assert timeouts and all(0 < t <= core_common.SECRET_SCAN_TIMEOUT_MS for t in timeouts)
    monkeypatch.setattr(core_common, "SECRET_SCAN_CAP", 1)  # past the cap, values are masked
    assert daemon.dispatch("snapshot", {}, 5.0)["snapshot"][:2] == ['- textbox "First" [ref=e2]: one', '- textbox "Second" [ref=e3]: ***']
    monkeypatch.setattr(core_common, "SECRET_SCAN_CAP", 200)
    monkeypatch.setattr(core_common, "SECRET_SCAN_BUDGET", 0.0)  # and past the time budget
    assert all(line.endswith(": ***") for line in daemon.dispatch("snapshot", {}, 5.0)["snapshot"])
    monkeypatch.setattr(core_common, "SECRET_SCAN_BUDGET", 2.0)
    monkeypatch.setattr(daemon, "_secret_info", lambda locator, timeout_ms=3000: {"unknown": True})
    assert all(line.endswith(": ***") for line in daemon.dispatch("snapshot", {}, 5.0)["snapshot"])


def test_snapshot_checks_inputs_the_page_gives_another_role(make_daemon) -> None:
    # Playwright prints an input's value whatever its role: <input type=password role=button> is a valued button line.
    daemon, ctx, page = make_daemon(snapshot_text='- button "Key" [ref=e2]: rolesecret\n- button "Caption" [ref=e3]: shown\n')
    page.elements["e2"].secret_info = PASSWORD_INFO
    assert daemon.dispatch("snapshot", {"full": True}, 5.0)["snapshot"][0].endswith(": rolesecret")  # a plain button caption
    page.main_frame.input_roles = [["button link", "rolesecret"]]
    assert daemon.dispatch("snapshot", {"full": True}, 5.0)["snapshot"] == ['- button "Key" [ref=e2]: ***', '- button "Caption" [ref=e3]: shown']
    page.main_frame.input_roles = FakePlaywrightError("Execution context was destroyed")  # a frame mid-navigation
    assert daemon.dispatch("snapshot", {"full": True}, 5.0)["snapshot"][0].endswith(": rolesecret")


def test_a_page_given_role_only_makes_lines_that_show_an_inputs_value_fields(make_daemon, monkeypatch) -> None:
    # One hidden <input role=listitem value=x> must not turn 400 list items into fields to check and mask.
    items = "".join(f'- listitem [ref=e{10 + i}]:\n  - link "Item {i}" [ref=e{1000 + i}]\n' for i in range(400))
    daemon, ctx, page = make_daemon(snapshot_text=items + '- listitem [ref=e5]: x\n')
    page.main_frame.input_roles = [["listitem", "x"], ["text", "Item 1"], [7, "y"], "junk"]
    calls: list[int] = []
    original = daemon._secret_info
    monkeypatch.setattr(daemon, "_secret_info", lambda locator, timeout_ms=3000: calls.append(timeout_ms) or original(locator, timeout_ms))
    env = daemon.dispatch("snapshot", {"full": True, "max_bytes": 200000}, 5.0)
    assert len(calls) == 1 and '- link "Item 399" [ref=e1399]' in [line.strip() for line in env["snapshot"]]
    assert not any("***" in line for line in env["snapshot"])


def test_a_decoy_input_cannot_spend_the_budget_on_page_text(make_daemon, monkeypatch) -> None:
    # A visible <input role=listitem value=Buy> placed after 400 <div role=listitem>Buy</div>.
    items = "".join(f"- listitem [ref=e{10 + i}]: Buy\n" for i in range(400))
    daemon, ctx, page = make_daemon(snapshot_text=items)
    page.main_frame.input_roles = [["listitem", "Buy"]]
    calls: list[int] = []
    original = daemon._secret_info
    monkeypatch.setattr(daemon, "_secret_info", lambda locator, timeout_ms=3000: calls.append(timeout_ms) or original(locator, timeout_ms))
    lines = daemon.dispatch("snapshot", {"full": True, "max_bytes": 200000}, 5.0)["snapshot"]
    assert len(calls) == core_common.REMAP_SCAN_CAP  # page text found by its tag costs a check, but only this many
    assert lines[:core_common.REMAP_SCAN_CAP] == [f"- listitem [ref=e{10 + i}]: Buy" for i in range(core_common.REMAP_SCAN_CAP)]
    assert lines[-1] == "- listitem [ref=e409]: ***"  # past the cap, a line that may still be the input is masked
    # Once every such input is found, the remaining lines with its text are page text and are not checked.
    calls.clear()
    page.elements["e11"].secret_info = {**PASSWORD_INFO, "type": "text", "text": "Basket"}  # the input, and not a credential
    lines = daemon.dispatch("snapshot", {"full": True, "max_bytes": 200000}, 5.0)["snapshot"]
    assert len(calls) == 2 and not any(line.endswith("***") for line in lines)


def test_a_role_attribute_cannot_hide_playwright_text_lines(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text='- paragraph [ref=e2]:\n  - text: visible\n- iframe [ref=e3]\n')
    page.main_frame.input_roles = [["text /url iframe", "visible"]]
    assert daemon.dispatch("snapshot", {"full": True}, 5.0)["snapshot"] == ["- paragraph [ref=e2]:", "  - text: visible", '- iframe "gsft_main" [ref=e3]']


def test_snapshot_full_never_writes_href_secrets_to_last_snapshot(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text='- link "Go" [ref=e1]:\n  - /url: https://x.com/cb?code=SECRET&keep=1\n')
    for args in ({"full": True}, {"find": "cb"}):
        env = daemon.dispatch("snapshot", args, 5.0)
        assert "SECRET" not in json.dumps(env) and "SECRET" not in daemon.paths.last_snapshot.read_text(encoding="utf-8")
        assert "  - /url: https://x.com/cb?code=***&keep=1" in env["snapshot"]


# ---------------------------------------------------------------------------
# dialogs


def test_confirm_dialog_blocks_unless_accepted(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["e4"].click_side_effect = lambda: ctx.emit("dialog", FakeDialog("confirm", "Are you sure?"))

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert exc.value.error_class == core_common.ERR_DIALOG

    page.elements["e4"].click_side_effect = lambda: ctx.emit("dialog", FakeDialog("confirm", "Are you sure?"))
    result = daemon.dispatch("click", {"target": "e4", "accept_dialog": True}, 5.0)
    assert any(d["handled"] == "accepted" for d in result["dialogs"])


def test_confirm_raised_by_eval_fails_with_dialog_after_running_and_is_not_repeated(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    raised: list[FakeDialog] = []

    def script_confirm() -> bool:
        raised.append(FakeDialog("confirm", "Delete record?"))
        ctx.emit("dialog", raised[-1])
        return raised[-1].accepted_with is not None
    page.custom_evaluators["confirm('Delete record?')"] = script_confirm

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("eval", {"js": "confirm('Delete record?')"}, 5.0)
    assert exc.value.error_class == core_common.ERR_DIALOG and exc.value.hint == core_common._hint("dialog")
    assert exc.value.details["executed"] is True
    assert [(d["message"], d["handled"]) for d in exc.value.details["dialogs"]] == [("Delete record?", "dismissed")]
    assert raised[0].dismissed and daemon.dialog_log == []

    # The error reported the dialog, so the next action is the compact change list rather than a full snapshot.
    after = daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert "changes" in after and "frames" not in after and after["dialogs"] == []

    accepted = daemon.dispatch("eval", {"js": "confirm('Delete record?')", "accept_dialog": True}, 5.0)
    assert accepted["value"] is True and raised[-1].accepted_with == ""
    assert [d["handled"] for d in accepted["dialogs"]] == ["accepted"] and "note" not in accepted


def test_dialog_between_commands_is_reported_by_the_next_page_command_without_failing_it(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    timer = FakeDialog("confirm", "Leave this page?")
    ctx.emit("dialog", timer)  # a page timer fires while no command runs
    assert timer.dismissed
    daemon.dispatch("ping", {}, 1.0)  # control commands leave it for the next page command
    assert [d["message"] for d in daemon.dialog_log] == ["Leave this page?"]

    text = daemon.dispatch("text", {}, 5.0)
    assert [(d["message"], d["handled"]) for d in text["dialogs"]] == [("Leave this page?", "dismissed")]
    assert text["note"] == core_common._hint("dialog_dismissed") and "dialogs" in text["untrusted"]
    assert daemon.dialog_log == []

    ctx.emit("dialog", FakeDialog("alert", "Saved"))
    downloads = daemon.dispatch("downloads", {}, 5.0)
    assert [d["handled"] for d in downloads["dialogs"]] == ["accepted"] and "note" not in downloads
    assert "dialogs" in downloads["untrusted"]  # dialog messages are page text


def test_fill_enter_confirm_is_accepted_only_with_the_flag(make_daemon, outer_text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    press = FakeLocator.press
    raised: list[FakeDialog] = []

    def press_and_confirm(self: FakeLocator, key: str, timeout: float | None = None) -> None:
        press(self, key, timeout)
        if key == "Enter":
            raised.append(FakeDialog("confirm", "Submit the form?"))
            ctx.emit("dialog", raised[-1])
    monkeypatch.setattr(FakeLocator, "press", press_and_confirm)

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("fill", {"target": "f1e3", "text": "Abel", "enter": True}, 5.0)
    assert exc.value.error_class == core_common.ERR_DIALOG and exc.value.details["executed"] is True
    assert raised[0].dismissed

    result = daemon.dispatch("fill", {"target": "f1e3", "text": "Abel", "enter": True, "accept_dialog": True}, 5.0)
    assert raised[1].accepted_with == "" and [d["handled"] for d in result["dialogs"]] == ["accepted"]


def test_alert_is_always_accepted(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["e4"].click_side_effect = lambda: ctx.emit("dialog", FakeDialog("alert", "Saved!"))
    result = daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert any(d["type"] == "alert" and d["handled"] == "accepted" for d in result["dialogs"])


def test_beforeunload_during_goto_blocks_then_can_be_discarded(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.pending_dialog = FakeDialog("beforeunload", "Leave?")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("goto", {"url": "http://x/home.html"}, 5.0)
    assert exc.value.error_class == core_common.ERR_UNSAVED

    page.pending_dialog = FakeDialog("beforeunload", "Leave?")
    result = daemon.dispatch("goto", {"url": "http://x/home.html", "discard_changes": True}, 5.0)
    assert result["navigated"] is True
    assert result["url"].endswith("home.html")
    assert any(d["handled"] == "accepted" for d in result["dialogs"])


# ---------------------------------------------------------------------------
# popups


def test_popup_becomes_the_new_current_tab(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    popup = FakePage(ctx, url="http://x/home.html", title="Home page")
    page.elements["e4"].click_side_effect = lambda: ctx.emit("page", popup)

    result = daemon.dispatch("click", {"target": "e4"}, 5.0)

    assert result["new_tab"] == {"id": "t2", "index": 2, "url": "http://x/home.html"}
    assert result["tab"] == 2
    assert result["tab_id"] == "t2"
    assert daemon.current is popup


# ---------------------------------------------------------------------------
# text


@pytest.mark.parametrize("observation", ["settle", "snapshot"])
def test_popup_arriving_during_observation_is_reported_without_replaying_click(make_daemon, outer_text, monkeypatch, observation):
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    popup = FakePage(ctx, url="http://x/home.html", title="Home page")
    daemon.dispatch("snapshot", {}, 1)
    settled = []
    def settle(candidate, cap):
        settled.append(candidate)
        if observation == "settle" and candidate is page:
            ctx.emit("page", popup)
        return []
    original = daemon._serve_snapshot
    def snapshot(candidate, **kwargs):
        if observation == "snapshot" and candidate is page:
            ctx.emit("page", popup)
        return original(candidate, **kwargs)
    monkeypatch.setattr(daemon, "_settle", settle)
    monkeypatch.setattr(daemon, "_serve_snapshot", snapshot)
    result = daemon.dispatch("click", {"target": "e4"}, 5)
    assert result["url"] == popup.url and result["tab"] == 2
    assert result["new_tab"]["url"] == popup.url
    assert settled == [page, popup]
    assert page.elements["e4"].calls == [("click", "left")]


def test_text_concatenates_frames_with_a_header(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.main_frame.body_text = "Outer body"
    page.frames[1].body_text = "Inner body"

    result = daemon.dispatch("text", {}, 5.0)

    assert "Outer body" in result["text"]
    assert "--- frame: gsft_main ---\nInner body" in result["text"]


# ---------------------------------------------------------------------------
# wait


def test_wait_seconds_is_satisfied(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n  - button \"Go\" [ref=e2]\n")
    result = daemon.dispatch("wait", {"seconds": 0.01}, 5.0)
    assert result["satisfied"] == ["seconds"]


def test_wait_text_condition_polls_until_satisfied(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n")
    seen = {"n": 0}

    def locator(selector: str) -> FakeLocator:
        assert selector == "body"
        seen["n"] += 1
        text = "ready now" if seen["n"] >= 3 else "loading"
        return FakeLocator(None, FakeElement(ref="b", value=text))

    page.main_frame.locator = locator  # type: ignore[assignment]
    result = daemon.dispatch("wait", {"text": "ready now"}, 5.0)
    assert result["satisfied"] == ["text"]
    assert seen["n"] >= 3


def test_wait_timeout_raises_timeout_class(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n")
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("wait", {"text": "never-there"}, 0.2)
    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert "(url " in str(exc.value) and exc.value.untrusted == ["message"]  # the message quotes the page's URL


def test_wait_with_no_condition_is_usage_error(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("wait", {}, 5.0)
    assert exc.value.error_class == core_common.ERR_USAGE


# ---------------------------------------------------------------------------
# tabs


def test_tab_index_out_of_range_is_not_found(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("tab", {"index": 5}, 5.0)
    assert exc.value.error_class == core_common.ERR_NOT_FOUND


def test_closing_the_last_tab_is_refused(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("tab", {"index": 1, "close": True}, 5.0)
    assert exc.value.error_class == core_common.ERR_VALIDATION


def test_per_command_tab_targeting_preserves_current_tab_and_serves_isolated_refs(make_daemon, outer_text: str) -> None:
    daemon, ctx, page1 = make_daemon(snapshot_text=outer_text, page_url="http://x/tab1")
    page2 = FakePage(ctx, url="http://x/tab2", title="Tab Two", snapshot_text='- document [ref=e1]:\n  - textbox "Note" [ref=e2]: initial\n  - button "Save" [ref=e3]\n')
    page2.elements["e2"] = FakeElement(ref="e2", tag="INPUT", value="initial")
    page2.elements["e3"] = FakeElement(ref="e3", tag="BUTTON")
    page2.main_frame.body_text = "Second tab body"
    page1.main_frame.body_text = "First tab body"
    daemon._track_page(page2)

    assert daemon.current is page1
    tabs_res = daemon.dispatch("tabs", {}, 5.0)
    assert tabs_res["current"] == 1
    assert tabs_res["current_id"] == "t1"
    assert [t["id"] for t in tabs_res["tabs"]] == ["t1", "t2"]

    snap2 = daemon.dispatch("snapshot", {"tab": "t2"}, 5.0)
    assert snap2["tab"] == 2
    assert snap2["tab_id"] == "t2"
    assert snap2["url"] == "http://x/tab2"
    assert snap2["snapshot_file"].endswith("last_snapshot_t2.txt")
    assert (daemon.paths.workspace / "last_snapshot_t2.txt").is_file()
    assert daemon.current is page1

    fill2 = daemon.dispatch("fill", {"target": "e2", "text": "updated in background", "tab": "t2"}, 5.0)
    assert fill2["tab"] == 2
    assert fill2["tab_id"] == "t2"
    assert fill2["value_after"] == "updated in background"
    assert daemon.current is page1

    val2 = daemon.dispatch("value", {"target": "e2", "tab": 2}, 5.0)
    assert val2["value"] == "updated in background"
    assert val2["tab_id"] == "t2"
    assert daemon.current is page1

    txt1 = daemon.dispatch("text", {"tab": "t1"}, 5.0)
    txt2 = daemon.dispatch("text", {"tab": "t2"}, 5.0)
    assert "First tab body" in txt1["text"] and txt1["tab_id"] == "t1"
    assert "Second tab body" in txt2["text"] and txt2["tab_id"] == "t2"

    page2.custom_evaluators["1+1"] = 2
    ev2 = daemon.dispatch("eval", {"js": "1+1", "tab": "t2"}, 5.0)
    assert ev2["tab"] == 2 and ev2["tab_id"] == "t2" and ev2["value"] == 2
    assert daemon.current is page1


def test_stable_tab_ids_survive_closing_earlier_tabs(make_daemon, outer_text: str) -> None:
    daemon, ctx, page1 = make_daemon(snapshot_text=outer_text, page_url="http://x/1")
    page2 = FakePage(ctx, url="http://x/2", title="Two", snapshot_text=outer_text)
    page3 = FakePage(ctx, url="http://x/3", title="Three", snapshot_text=outer_text)
    daemon._track_page(page2)
    daemon._track_page(page3)

    assert [t["id"] for t in daemon.dispatch("tabs", {}, 5.0)["tabs"]] == ["t1", "t2", "t3"]

    daemon.dispatch("tab", {"index": "t1", "close": True}, 5.0)
    tabs_after = daemon.dispatch("tabs", {}, 5.0)["tabs"]
    assert [(t["id"], t["index"], t["url"]) for t in tabs_after] == [
        ("t2", 1, "http://x/2"),
        ("t3", 2, "http://x/3"),
    ]

    snap3 = daemon.dispatch("snapshot", {"tab": "t3"}, 5.0)
    assert snap3["url"] == "http://x/3"
    assert snap3["tab"] == 2
    assert snap3["tab_id"] == "t3"

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("snapshot", {"tab": "t1"}, 5.0)
    assert exc.value.error_class == core_common.ERR_NOT_FOUND

    page4 = FakePage(ctx, url="http://x/4", title="Four", snapshot_text=outer_text)
    daemon._track_page(page4)
    assert daemon._tab_id(page4) == "t4"


def test_popup_from_background_tab_does_not_steal_current_tab(make_daemon, outer_text: str) -> None:
    daemon, ctx, page1 = make_daemon(snapshot_text=outer_text, page_url="http://x/1")
    page2 = FakePage(ctx, url="http://x/2", title="Two", snapshot_text=outer_text)
    page2.elements["e4"] = FakeElement(ref="e4", tag="BUTTON")
    daemon._track_page(page2)
    popup = FakePage(ctx, url="http://x/popup", title="Popup", snapshot_text=outer_text)
    page2.elements["e4"].click_side_effect = lambda: ctx.emit("page", popup)

    assert daemon.current is page1
    res = daemon.dispatch("click", {"target": "e4", "tab": "t2"}, 5.0)
    assert res["tab_id"] == "t2"
    assert res["new_tab"] == {"id": "t3", "index": 3, "url": "http://x/popup"}
    assert daemon.current is page1


def test_goto_on_background_tab_and_allowlist_per_tab(make_daemon, outer_text: str) -> None:
    daemon, ctx, page1 = make_daemon(
        snapshot_text=outer_text,
        page_url="http://allowed.example/one",
        config={"allowed_hosts": ["allowed.example"]},
    )
    page2 = FakePage(ctx, url="http://other.example/off", title="Off", snapshot_text=outer_text)
    daemon._track_page(page2)

    snap1 = daemon.dispatch("snapshot", {"tab": "t1"}, 5.0)
    assert snap1["url"] == "http://allowed.example/one"
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4", "tab": "t2"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED

    nav2 = daemon.dispatch("goto", {"url": "http://allowed.example/two", "tab": "t2"}, 5.0)
    assert nav2["tab_id"] == "t2"
    assert nav2["url"] == "http://allowed.example/two"
    assert daemon.current is page1
    assert page1.url == "http://allowed.example/one"

    with pytest.raises(ValueError) as exc2:
        daemon.dispatch("goto", {"url": "http://allowed.example/three", "new_tab": True, "tab": "t1"}, 5.0)
    assert exc2.value.error_class == core_common.ERR_USAGE


# ---------------------------------------------------------------------------
# eval / downloads / screenshot / allowlist


def test_eval_disabled_by_config_is_guarded(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text, eval_enabled=False)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("eval", {"js": "1+1"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("eval_disabled")


def test_eval_on_a_password_ref_is_guarded_and_never_evaluated(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]
    el.secret_info = {"type": "password"}
    ran: list[str] = []
    el.custom_evaluators["el => el.value"] = lambda: ran.append("eval") or "typed-by-the-user"

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("eval", {"js": "el => el.value", "target": "f1e3"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_eval_secret")
    assert exc.value.details["executed"] is False
    assert "typed-by-the-user" not in str(exc.value)

    el.secret_info = None
    el.raise_on["secret_check"] = FakePlaywrightError("Execution context was destroyed")
    with pytest.raises(ValueError) as unverified:
        daemon.dispatch("eval", {"js": "el => el.value", "target": "f1e3"}, 5.0)
    assert unverified.value.error_class == core_common.ERR_ACTION_FAILED
    assert unverified.value.details["executed"] is False
    assert ran == []


def test_eval_on_a_normal_ref_still_returns_its_value(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["f1e3"].custom_evaluators["el => el.value"] = "Abel"

    result = daemon.dispatch("eval", {"js": "el => el.value", "target": "f1e3"}, 5.0)
    assert result["value"] == "Abel" and result["executed"] is True


def test_downloads_are_saved_under_the_profile_downloads_folder(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.emit("download", FakeDownload("report.csv"))

    result = daemon.dispatch("downloads", {}, 5.0)

    assert result["downloads"][0]["file"] == "report.csv"
    assert result["downloads"][0]["state"] == "saved"
    saved_path = result["downloads"][0]["path"]
    assert saved_path.startswith(str(daemon.paths.downloads).replace("\\", "/"))


def test_text_drops_invisible_instruction_characters(make_daemon) -> None:
    daemon, ctx, page = make_daemon()
    page.main_frame.body_text = "ok" + chr(0xE0049) + chr(0x2060) + chr(0x2028) + "next" + chr(0x202E)
    assert daemon.dispatch("text", {}, 5.0)["text"] == "ok\nnext"
    from conftest import FakeFrame
    page.frames = [page.main_frame, FakeFrame(name="a\n--- frame: forged ---" + chr(0x2028) + "b", body_text="inner")]
    assert daemon.dispatch("text", {}, 5.0)["text"] == "ok\nnext\n\n--- frame: a --- frame: forged --- b ---\ninner"


def test_screenshot_writes_a_png_under_shots_with_forward_slashes(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    result = daemon.dispatch("screenshot", {}, 5.0)
    assert result["width"] == 4 and result["height"] == 3
    assert "\\" not in result["path"]
    assert result["path"].startswith(str(daemon.paths.shots).replace("\\", "/"))


def test_off_allowlist_blocks_actions_before_resolving_a_target(make_daemon) -> None:
    daemon, ctx, page = make_daemon(config={"allowed_hosts": ["allowed.example"]}, page_url="http://other.example/x")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e1"}, 5.0)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("off_allowlist", daemon.profile)


# ---------------------------------------------------------------------------
# Navigation timeouts (remediation PR 5)

@pytest.mark.parametrize("verb", ["goto", "back", "reload"])
def test_navigation_timeout_does_not_use_the_wait_hint(make_daemon, verb: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n")
    def time_out(*args, **kwargs):
        raise FakePlaywrightTimeoutError("Timeout 1000ms exceeded.")
    if verb == "goto":
        page.goto_error = FakePlaywrightTimeoutError("Timeout 1000ms exceeded.")
    else:
        setattr(page, "go_back" if verb == "back" else "reload", time_out)
    args = {"url": "https://example.com/next"} if verb == "goto" else {}
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch(verb, args, 1.0)
    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert exc.value.hint == core_common._hint("nav_timeout", daemon.profile) != core_common._hint("wait_timeout", daemon.profile)
