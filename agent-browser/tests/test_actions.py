"""Daemon action handlers (h_goto, h_click, h_fill, ...) exercised via Daemon.dispatch on a fake page.

Every test here builds a real `Daemon` against a fake Playwright stack (see conftest.make_daemon)
and calls `daemon.dispatch(cmd, args, timeout)` directly - no socket, no `serve()` loop. That is
the layer these handlers actually live on; the socket/`_Handler` request plumbing around dispatch
is covered separately in test_daemon.py.
"""

from __future__ import annotations

import re

import pytest

import agent_browser as ab
from conftest import FakeDialog, FakeDownload, FakeElement, FakeLocator, FakePage

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
    assert result["untrusted"] == ab.UNTRUSTED_KEYS
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
    assert exc.value.error_class == ab.ERR_STALE_REF


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
    assert exc.value.error_class == ab.ERR_STALE_REF


def test_strict_mode_violation_becomes_ambiguous(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    from conftest import FakePlaywrightError

    page.elements["e4"].raise_on["click"] = FakePlaywrightError("strict mode violation: locator resolved to 2 elements")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert exc.value.error_class == ab.ERR_AMBIGUOUS


def test_not_a_checkbox_becomes_action_failed_with_hint(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    from conftest import FakePlaywrightError

    page.elements["e4"].raise_on["check"] = FakePlaywrightError("Not a checkbox or radio button")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("check", {"target": "e4"}, 5.0)
    assert exc.value.error_class == ab.ERR_ACTION_FAILED
    assert exc.value.hint == ab._hint("check_not_checkbox", daemon.profile)


# ---------------------------------------------------------------------------
# fill / type: secret guard, value_after, clear-before-type


def test_fill_on_password_field_is_guarded_and_never_leaks_the_text(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    el = page.elements["f1e3"]  # "Caller" textbox, repurposed as a password field for this test
    el.secret_info = {"type": "password"}

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("fill", {"target": "f1e3", "text": "hunter2"}, 5.0)
    assert exc.value.error_class == ab.ERR_GUARDED
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
    assert exc.value.error_class == ab.ERR_NOT_FOUND
    assert exc.value.details["options"] == ["Choice A"]


def test_select_on_a_non_select_element_is_action_failed(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["f1e3"].options = None
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("select", {"target": "f1e3", "values": ["a"]}, 5.0)
    assert exc.value.error_class == ab.ERR_ACTION_FAILED
    assert exc.value.hint == ab._hint("select_not_select", daemon.profile)


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
    assert exc.value.error_class == ab.ERR_GUARDED
    assert exc.value.hint == ab._hint("guarded_key", daemon.profile)

    result = daemon.dispatch("press", {"key": "Tab", "target": "f1e3"}, 5.0)
    assert result["ok"] is True
    assert result["value_after"] == "***"


# ---------------------------------------------------------------------------
# dialogs


def test_confirm_dialog_blocks_unless_accepted(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.elements["e4"].click_side_effect = lambda: ctx.emit("dialog", FakeDialog("confirm", "Are you sure?"))

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4"}, 5.0)
    assert exc.value.error_class == ab.ERR_DIALOG

    page.elements["e4"].click_side_effect = lambda: ctx.emit("dialog", FakeDialog("confirm", "Are you sure?"))
    result = daemon.dispatch("click", {"target": "e4", "accept_dialog": True}, 5.0)
    assert any(d["handled"] == "accepted" for d in result["dialogs"])


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
    assert exc.value.error_class == ab.ERR_UNSAVED

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

    assert result["new_tab"] == {"index": 2, "url": "http://x/home.html"}
    assert result["tab"] == 2
    assert daemon.current is popup


# ---------------------------------------------------------------------------
# text


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
    assert exc.value.error_class == ab.ERR_TIMEOUT


def test_wait_with_no_condition_is_usage_error(make_daemon) -> None:
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n")
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("wait", {}, 5.0)
    assert exc.value.error_class == ab.ERR_USAGE


# ---------------------------------------------------------------------------
# tabs


def test_tab_index_out_of_range_is_not_found(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("tab", {"index": 5}, 5.0)
    assert exc.value.error_class == ab.ERR_NOT_FOUND


def test_closing_the_last_tab_is_refused(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("tab", {"index": 1, "close": True}, 5.0)
    assert exc.value.error_class == ab.ERR_VALIDATION


# ---------------------------------------------------------------------------
# eval / downloads / screenshot / allowlist


def test_eval_disabled_by_config_is_guarded(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text, eval_enabled=False)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("eval", {"js": "1+1"}, 5.0)
    assert exc.value.error_class == ab.ERR_GUARDED
    assert exc.value.hint == ab._hint("eval_disabled")


def test_downloads_are_saved_under_the_profile_downloads_folder(make_daemon, outer_text: str) -> None:
    daemon, ctx, page = make_daemon(snapshot_text=outer_text)
    page.emit("download", FakeDownload("report.csv"))

    result = daemon.dispatch("downloads", {}, 5.0)

    assert result["downloads"][0]["file"] == "report.csv"
    assert result["downloads"][0]["state"] == "saved"
    saved_path = result["downloads"][0]["path"]
    assert saved_path.startswith(str(daemon.paths.downloads).replace("\\", "/"))


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
    assert exc.value.error_class == ab.ERR_GUARDED
    assert exc.value.hint == ab._hint("off_allowlist", daemon.profile)
