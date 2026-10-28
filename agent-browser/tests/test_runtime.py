"""Session ownership, audit retention and cooperative scheduling contracts."""
import json
import time
from types import SimpleNamespace

import pytest
from agent_browser_core import common, evidence
from conftest import FakeDialog, FakeFrame, FakePage, FakePlaywrightError


def test_session_identity_precedence(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex")
    assert common.session_identity() == "codex"
    assert common.session_identity("explicit") == "explicit"


def test_sessions_own_tabs_and_pending_wait_does_not_block(make_daemon):
    daemon, ctx, original = make_daemon()
    a = daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    first = daemon.page()
    pending = daemon.dispatch("wait", {"seconds": 100, "_session_id": "a", "_cooperative": True}, 100)
    assert pending["pending"]
    began = time.monotonic()
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    assert time.monotonic() - began < 2
    assert daemon.page() is not first
    daemon.dispatch("state", {"_session_id": "a"}, 1)
    assert daemon.page() is first and first.url == "http://x/a"
    with pytest.raises(ValueError, match="this session"):
        daemon.dispatch("poll", {"command_id": pending["command_id"], "_session_id": "b"}, 1)


def test_stop_releases_only_its_session(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    second = daemon.page()
    daemon.dispatch("stop", {"_session_id": "a"}, 1)
    assert daemon.stop_reason is None
    daemon.dispatch("state", {"_session_id": "b"}, 1)
    assert daemon.page() is second


def _beforeunload_on_close(daemon, page, *, close_later=False):
    """Make page.close(run_before_unload=True) raise a beforeunload: dismissed, the tab stays open; accepted, it closes,
    with close_later only once stop waits for it, as Playwright's close() returns before the tab is gone."""
    raised = []

    def close(run_before_unload=False):
        raised.append(FakeDialog("beforeunload"))
        raised[-1].page = page
        daemon.ctx.emit("dialog", raised[-1])
        if raised[-1].accepted_with is None:
            return
        if not close_later:
            return FakePage.close(page)
        page.wait_for_timeout = lambda ms: page.closed or FakePage.close(page)
    page.close = close
    return raised


def test_stop_keeps_tabs_with_unsaved_edits_and_the_session_until_discarded(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    dirty = daemon.page()
    raised = _beforeunload_on_close(daemon, dirty)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("stop", {"_session_id": "a"}, 5)
    assert exc.value.error_class == common.ERR_UNSAVED and exc.value.hint == common._hint("unsaved_changes")
    assert exc.value.details["tabs"] == [daemon.tab_ids[dirty]] and exc.value.details["closed"] == 0
    assert exc.value.details["executed"] is False and exc.value.details["dialogs"][0]["handled"] == "dismissed"
    assert raised[0].dismissed and not dirty.closed
    assert "a" in daemon.used_sessions and daemon.sessions["a"]["pages"] == [dirty] and daemon.stop_reason is None

    released = daemon.dispatch("stop", {"_session_id": "a", "discard_changes": True}, 5)
    assert released["stopped"] is True and raised[1].accepted_with == "" and dirty.closed
    assert [d["handled"] for d in released["dialogs"]] == ["accepted"] and daemon.dialog_log == []
    assert "a" not in daemon.used_sessions


def test_stop_with_discard_changes_waits_for_the_tab_to_close(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    dirty = daemon.sessions["a"]["current"]
    raised = _beforeunload_on_close(daemon, dirty, close_later=True)
    released = daemon.dispatch("stop", {"_session_id": "a", "discard_changes": True}, 5)
    assert released["stopped"] is True and raised[0].accepted_with == ""
    assert dirty.closed and daemon.sessions["a"]["pages"] == [] and daemon.stop_reason is None


def test_stop_answers_every_own_tab_not_just_the_current_one(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    dirty = daemon.page()
    daemon.dispatch("goto", {"url": "http://x/c", "new_tab": True, "_session_id": "a"}, 5)
    clean = daemon.page()
    assert daemon.sessions["a"]["current"] is clean
    _beforeunload_on_close(daemon, dirty)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("stop", {"_session_id": "a"}, 5)
    # The clean tab closed before the refusal, so the receipt says the command did something.
    assert exc.value.details["tabs"] == [daemon.tab_ids[dirty]] and exc.value.details["closed"] == 1
    assert exc.value.details["executed"] is True and clean.closed and daemon.sessions["a"]["pages"] == [dirty]
    assert not daemon.sessions["a"].get("dialogs")  # answered by stop, not as another tab's background dialog


def test_stop_keeps_every_open_tab_when_a_dismissed_beforeunload_names_no_tab(make_daemon):
    daemon, ctx, page = make_daemon()
    waits = []
    page.close = lambda run_before_unload=False: ctx.emit("dialog", FakeDialog("beforeunload"))
    page.wait_for_timeout = waits.append
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("stop", {}, 5)
    assert exc.value.error_class == common.ERR_UNSAVED and exc.value.details["tabs"] == [daemon.tab_ids[page]]
    assert waits == []  # the unmatched dismissal already decides, so stop does not wait out its deadline


def test_a_dismissal_naming_no_tab_does_not_cut_short_the_next_tabs_close(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    dirty = daemon.page()
    daemon.dispatch("goto", {"url": "http://x/c", "new_tab": True, "_session_id": "a"}, 5)
    slow = daemon.page()
    dirty.close = lambda run_before_unload=False: ctx.emit("dialog", FakeDialog("beforeunload"))
    waits = []
    slow.close = lambda run_before_unload=False: None
    slow.wait_for_timeout = lambda ms: waits.append(ms) if len(waits) < 2 else FakePage.close(slow)
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("stop", {"_session_id": "a"}, 5)
    # The clean tab closed while stop waited for it, so only the dirty tab is reported as kept.
    assert exc.value.details["tabs"] == [daemon.tab_ids[dirty]] and exc.value.details["closed"] == 1 and slow.closed


def test_stop_waits_past_two_seconds_for_a_busy_tab_to_answer_its_beforeunload(make_daemon, fake_clock):
    # A prompt after stop returns would be answered between commands, where beforeunload is accepted: edits lost.
    daemon, ctx, page = make_daemon()
    started = []

    def late_prompt(ms):
        if fake_clock.value - started[0] > 3.0 and not page.closed:
            page.wait_for_timeout = lambda ms: None
            dialog = FakeDialog("beforeunload")
            dialog.page = page
            ctx.emit("dialog", dialog)
    page.close = lambda run_before_unload=False: started.append(fake_clock.value)
    page.wait_for_timeout = late_prompt
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("stop", {}, 10)
    assert exc.value.error_class == common.ERR_UNSAVED and exc.value.details["tabs"] == [daemon.tab_ids[page]]


def test_stop_that_fails_after_closing_a_tab_says_it_ran(make_daemon):
    daemon, ctx, original = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    first = daemon.page()
    daemon.dispatch("goto", {"url": "http://x/c", "new_tab": True, "_session_id": "a"}, 5)
    second = daemon.page()

    def broken(run_before_unload=False):
        raise FakePlaywrightError("Protocol error (Page.close): Internal error")
    second.close = broken
    with pytest.raises(Exception) as exc:
        daemon.dispatch("stop", {"_session_id": "a"}, 5)
    assert first.closed and exc.value.details["executed"] is True and "a" in daemon.used_sessions


def test_stop_does_not_fail_a_clean_tab_that_is_slow_to_close(make_daemon):
    daemon, ctx, page = make_daemon()
    page.close = lambda run_before_unload=False: None  # neither closed nor a beforeunload within the wait
    assert daemon.dispatch("stop", {}, 5)["stopped"] is True


def test_stop_all_still_stops_the_profile_without_asking(make_daemon):
    daemon, ctx, page = make_daemon()
    raised = _beforeunload_on_close(daemon, page)
    assert daemon.dispatch("stop", {"all": True}, 5)["stopping"] is True
    assert daemon.stop_reason == "stop" and raised == []


def test_read_permission_denial_is_journaled_without_values(make_daemon):
    daemon, ctx, page = make_daemon(config={"permission": "read"})
    with pytest.raises(ValueError):
        daemon.dispatch("fill", {"target": "e1", "text": "private work note"}, 1)
    rows = daemon.journal.rows("manual")
    assert rows[0]["status"] == "failed"
    assert "private work note" not in json.dumps(rows)


def test_eval_default_preserved_and_act_denies_eval(make_daemon):
    daemon, ctx, page = make_daemon(config={"permission": "act"})
    with pytest.raises(ValueError, match="commit"):
        daemon.dispatch("eval", {"js": "fetch('/write')"}, 1)
    assert daemon.config.get("eval", True) is True


def test_quota_blocks_edit_without_erasing_receipts(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("ping", {}, 1)
    daemon.journal.quota = 0
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("fill", {"target": "e1", "text": "value"}, 1)
    assert exc.value.error_class == "journal_full"
    assert len(daemon.journal.rows("manual")) == 1


def test_restart_marks_unfinished_commands_unknown_and_never_replays(tmp_path):
    journal = evidence.Journal(tmp_path)
    journal.start("one", "a", "commit api", {}, "http://x/")
    restored = evidence.Journal(tmp_path)
    assert restored.rows("a")[0]["status"] == "unknown"


def test_telemetry_from_another_session_is_not_attributed(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    first = daemon.page()
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    first.emit("requestfailed", object())
    assert daemon.session_counts["a"]["failed_requests"] == 1
    assert daemon.session_counts.get("b", {}).get("failed_requests", 0) == 0


def test_generic_handoff_is_durable_and_explicitly_resolved(make_daemon, monkeypatch):
    from agent_browser_core import session
    daemon, ctx, page = make_daemon()
    monkeypatch.setattr(session, "focus_window", lambda paths: {"focused": False})
    result = daemon.dispatch("needs-human", {"reason": "Inspect unfamiliar layout"}, 1)
    handoff_id = result["handoff_id"]
    assert daemon.dispatch("state", {"handoff_id": handoff_id}, 1)["handoffs"][0]["status"] == "needs_human"
    with pytest.raises(ValueError, match="commit state"):
        daemon.dispatch("state", {"handoff_id": handoff_id, "resolve": True}, 1)
    assert daemon.dispatch("commit", {"operation": "state", "handoff_id": handoff_id, "resolve": True}, 1)["handoffs"][0]["status"] == "resolved"
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("state", {"handoff_id": handoff_id, "_session_id": "other"}, 1)
    assert exc.value.error_class == common.ERR_NOT_FOUND


@pytest.mark.parametrize("level", ["read", "act"])
def test_handoff_resolution_requires_commit_permission(make_daemon, level):
    daemon, ctx, page = make_daemon(config={"permission": level})
    handoff_id = daemon.journal.open_handoff("manual", "Inspect layout", {})[0]
    with pytest.raises(ValueError, match="commit profile"):
        daemon.dispatch("commit", {"operation": "state", "handoff_id": handoff_id, "resolve": True}, 1)
    assert daemon.journal.handoffs("manual", handoff_id)[0]["status"] == "needs_human"


def test_explicit_resolution_cannot_bypass_auth_condition(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/"}, 1)
    handoff_id = daemon.journal.open_handoff("manual", "Sign in", {"signed_in": "x", "require_navigation": True, "navigation": 0})[0]
    monkeypatch.setattr(daemon, "_signed_in", lambda page, host: True)
    daemon.nav_counts[daemon.page().main_frame] = 0
    with pytest.raises(ValueError, match="not been observed"):
        daemon.dispatch("commit", {"operation": "state", "handoff_id": handoff_id, "resolve": True}, 1)
    daemon.nav_counts[daemon.page().main_frame] = 1
    assert daemon.dispatch("state", {"handoff_id": handoff_id}, 1)["handoffs"][0]["status"] == "resolved"


def test_background_dialog_cannot_consume_another_sessions_accept_option(make_daemon):
    from types import SimpleNamespace
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 1)
    first = daemon.page()
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 1)
    handled = []
    daemon.cmd_opts = {"accept_dialog": True}
    dialog = SimpleNamespace(page=first, type="confirm", accept=lambda: handled.append("accepted"), dismiss=lambda: handled.append("dismissed"))
    daemon._on_dialog(dialog)
    assert handled == ["dismissed"]
    assert daemon.sessions["a"]["dialogs"][0]["background"] is True


def test_clear_cancels_only_own_pending_operations(make_daemon):
    daemon, ctx, page = make_daemon()
    wait = daemon.dispatch("wait", {"seconds": 100, "_cooperative": True}, 100)
    daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    with pytest.raises(ValueError):
        daemon.dispatch("poll", {"command_id": wait["command_id"]}, 1)


@pytest.mark.parametrize("command,args", [("upload", {"target": "e1", "files": ["anything.txt"]}),
                                        ("commit", {"operation": "api", "url": "/api/items", "method": "POST", "body_file": "anything.json"})])
def test_disabled_uploads_are_enforced_before_file_or_network_access(make_daemon, command, args):
    daemon, ctx, page = make_daemon(config={"uploads": False})
    with pytest.raises(ValueError, match="disabled by profile policy"):
        daemon.dispatch(command, args, 1)
    assert daemon.journal.rows("manual")[0]["status"] == "failed"


def test_full_quota_still_allows_explicit_clear_and_reclaims_journal_space(make_daemon):
    daemon, ctx, page = make_daemon()
    for index in range(300):
        daemon.journal.start(str(index), "manual", "read", {"target": "x" * 160}, "http://x/")
        daemon.journal.finish(str(index), "completed", {"effect": "none"})
    before = daemon.journal.path.stat().st_size
    daemon.journal.quota = 65536
    assert before > daemon.journal.quota
    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert result["cleared_commands"] == 300
    assert daemon.journal.path.stat().st_size < before
    assert daemon.journal.usage() < daemon.journal.quota


def test_full_quota_keeps_large_receipts_readable_without_new_artifacts(make_daemon, monkeypatch):
    from argparse import Namespace
    from agent_browser_core import cli
    daemon, ctx, page = make_daemon()
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(daemon.paths.root))
    for index in range(30):
        daemon.journal.start(f"{index:032x}", "manual", "read", {"target": "x" * 160}, "http://x/")
        daemon.journal.finish(f"{index:032x}", "completed", {"effect": "none"})
    command_id = "a" * 32
    daemon.journal.start(command_id, "manual", "commit click", {}, "http://x/")
    daemon.journal.finish(command_id, "failed", {"executed": True, "expectations": [{"kind": "text", "passed": False}] * 2000})
    daemon.journal.quota = 0
    listing = daemon.dispatch("journal", {}, 1)
    assert listing["next_offset"] is not None
    receipt = next(row for row in listing["commands"] if row["id"] == command_id)
    assert receipt["output_id"] == "r" + command_id and receipt["truncated"]
    assert len(cli._json_line(listing).encode()) < common.STDOUT_BUDGET
    chunks, offset = [], 0
    while offset is not None:
        result = cli._cmd_output(Namespace(profile=daemon.profile, session="manual", id=receipt["output_id"], offset=offset))
        chunks.append(result["chunk"])
        offset = result["next_offset"]
        daemon.dispatch("ping", {}, 1)  # concurrent receipts cannot change the terminal one
    recovered = json.loads("".join(chunks))
    assert recovered["id"] == command_id
    assert json.loads(recovered["outcome"])["executed"] is True
    assert not daemon.paths.outputs.exists()


def test_receipt_paging_is_session_scoped_and_does_not_change_pending_state(make_daemon, monkeypatch):
    from argparse import Namespace
    from agent_browser_core import cli
    daemon, ctx, page = make_daemon()
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(daemon.paths.root))
    daemon.journal.start("b" * 32, "manual", "commit api", {}, "http://x/")
    daemon.journal.start("c" * 32, "other", "read", {}, "http://x/")
    daemon.journal.finish("c" * 32, "completed", {"effect": "none"})
    for command_id in ("b" * 32, "c" * 32):
        with pytest.raises(ValueError, match="not found"):
            cli._cmd_output(Namespace(profile=daemon.profile, session="manual", id=command_id, offset=0))
    assert daemon.journal.rows("manual", "b" * 32)[0]["status"] == "running"


def test_terminal_receipt_id_cannot_return_a_prior_pending_artifact(make_daemon, monkeypatch):
    from argparse import Namespace
    from agent_browser_core import cli
    daemon, ctx, page = make_daemon()
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(daemon.paths.root))
    command_id = "d" * 32
    daemon.journal.start(command_id, "manual", "commit click", {}, "http://x/")
    daemon.journal.finish(command_id, "completed", {"executed": True})
    daemon.paths.outputs.mkdir(parents=True)
    (daemon.paths.outputs / (command_id + ".json")).write_text('{"pending":true}', encoding="utf-8")
    result = cli._cmd_output(Namespace(profile=daemon.profile, session="manual", id="r" + command_id, offset=0))
    assert json.loads(result["chunk"])["status"] == "completed"


def test_cooperative_wait_on_background_tab_polls_targeted_tab_while_other_tabs_run(make_daemon):
    daemon, ctx, page1 = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/one"}, 5)
    daemon.dispatch("goto", {"url": "http://x/two", "new_tab": True}, 5)
    assert daemon.current is not page1

    # Start a cooperative wait on background tab t1 while t2 remains the active tab
    pending = daemon.dispatch("wait", {"url_contains": "done", "tab": "t1", "_cooperative": True}, 30)
    assert pending["pending"] is True

    # Operate on t2 while t1 wait is pending
    snap2 = daemon.dispatch("snapshot", {"tab": "t2"}, 5)
    assert snap2["tab_id"] == "t2" and snap2["url"] == "http://x/two"

    # Poll before t1 satisfies the condition
    still_pending = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)
    assert still_pending["pending"] is True

    # Navigate t1 to satisfy the wait condition without changing active tab t2
    daemon.dispatch("goto", {"url": "http://x/one-done", "tab": "t1"}, 5)
    assert daemon.dispatch("tabs", {}, 5)["current_id"] == "t2"

    done = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)
    assert done["pending"] is False
    assert done["satisfied"] == ["url_contains"]
    assert done["tab_id"] == "t1"
    assert done["url"] == "http://x/one-done"


def test_state_and_journal_reads_do_not_pollute_journal(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/one"}, 5)
    assert len(daemon.journal.rows("manual")) == 1

    state_res = daemon.dispatch("state", {}, 1)
    journal_res = daemon.dispatch("journal", {}, 1)
    assert len(state_res["commands"]) == 1
    assert len(journal_res["commands"]) == 1
    assert len(daemon.journal.rows("manual")) == 1


def test_ping_reports_tabs_across_sessions_for_status(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/session-a", "_session_id": "a"}, 5)
    pong = daemon.dispatch("ping", {}, 1)
    urls = [t["url"] for t in pong["tabs"]]
    assert "http://x/session-a" in urls
    assert any(t.get("session") == "a" for t in pong["tabs"])


def test_cooperative_wait_selector_multi_match_and_max_bytes(make_daemon):
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n  - button \"A\" [ref=e2]\n  - button \"B\" [ref=e3]\n")
    page.selector_matches[".row-item"] = 3
    res = daemon.dispatch("wait", {"selector": ".row-item", "max_bytes": 12, "_cooperative": True}, 5)
    assert res["pending"] is False
    assert res["satisfied"] == ["selector"]
    assert res["truncated"] is True


def test_expect_gone_succeeds_when_handle_disposed_on_navigation(make_daemon, monkeypatch):
    from types import SimpleNamespace
    daemon, ctx, page = make_daemon(snapshot_text="- generic [ref=e1]:\n  - button \"Submit\" [ref=e2]\n")
    daemon.dispatch("snapshot", {}, 1)
    disposed = {"after_click": False}

    def is_visible():
        if disposed["after_click"]:
            raise Exception("Execution context was destroyed, most likely because of a navigation")
        return True

    page.elements["e2"].click_side_effect = lambda: disposed.update(after_click=True)
    monkeypatch.setattr(daemon.resolve(page, "e2").__class__, "element_handle", lambda self, timeout=None: SimpleNamespace(is_visible=is_visible))

    res = daemon.dispatch("click", {"target": "e2", "expect": ["gone:e2"]}, 5)
    assert res["pending"] is False
    assert res["expectations"] == [{"kind": "gone", "passed": True}]


def test_table_with_css_selector_before_snapshot(make_daemon, monkeypatch):
    from types import SimpleNamespace
    daemon, ctx, page = make_daemon()
    assert page not in daemon.served

    cell = SimpleNamespace(inner_text=lambda: "Rudy", is_visible=lambda: True)
    row = SimpleNamespace(
        is_visible=lambda: True,
        get_by_role=lambda role: SimpleNamespace(count=lambda: 0),
        locator=lambda sel: SimpleNamespace(all=lambda: [cell]),
        aria_snapshot=lambda mode="ai": '- row "Rudy" [ref=e9]',
    )
    header = SimpleNamespace(inner_text=lambda: "Owner", is_visible=lambda: True)
    fake_table = SimpleNamespace(
        evaluate=lambda js: False,
        get_by_role=lambda role: SimpleNamespace(all=lambda: [header] if role == "columnheader" else [row]),
        element_handle=lambda: SimpleNamespace(owner_frame=lambda: page.main_frame),
    )
    monkeypatch.setattr(daemon, "resolve", lambda p, t: fake_table)

    res = daemon.dispatch("table", {"target": "table.grid"}, 5)
    assert res["columns"] == ["Owner"]
    assert res["rows"] == [{"ref": "e9", "cells": ["Rudy"]}]


def test_screenshot_cleans_up_empty_file_on_failure_and_passes_timeout(make_daemon, tmp_path, monkeypatch):
    daemon, ctx, page = make_daemon()
    out = tmp_path / "shot.png"
    seen_opts = {}

    def fail_shot(**kwargs):
        seen_opts.update(kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(page, "screenshot", fail_shot)
    with pytest.raises(RuntimeError, match="boom"):
        daemon.dispatch("screenshot", {"path": str(out), "_cwd": str(tmp_path)}, 7.5)
    assert not out.exists()
    assert seen_opts["timeout"] == 7500
    assert seen_opts["scale"] == "css"


def test_profile_policy_normalizes_uppercase_allowed_hosts_and_doctor_validates(tmp_path, monkeypatch):
    from agent_browser_core import policy, session
    cfg = policy.profile_config({"profiles": {"work": {"allowed_hosts": ["  Sub.Example.COM  ", ""]}}}, "work")
    assert cfg["allowed_hosts"] == ["sub.example.com"]

    config_file = common._home() / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps({"profiles": {"bad_prof": {"permission": "invalid"}}}), encoding="utf-8")
    res = session.doctor()
    assert "config_json" in res["failed"]


def test_commit_forwards_outer_flags_and_supports_fill_form(monkeypatch):
    from argparse import Namespace
    from agent_browser_core import cli, commands

    captured = []
    monkeypatch.setattr(cli, "_remote", lambda args, verb, cmd, payload, *a, **kw: captured.append((verb, cmd, payload, getattr(args, "committing", False), getattr(args, "snapshot", False), getattr(args, "expect", []), getattr(args, "max_bytes", None))) or {"ok": True})
    parser = cli._build_parser()
    ns = parser.parse_args(["commit", "--snapshot", "--max-bytes", "9000", "--expect", "url:changed", "click", "e2", "--expect", "gone:e2"])
    ns.func(ns)
    verb, cmd, payload, committing, snap, exp, max_b = captured[-1]
    assert verb == "click" and committing is True and snap is True and max_b == 9000
    assert exp == ["url:changed", "gone:e2"]


def test_clean_prunes_session_downloads_and_evidence_usage_excludes_downloads(tmp_path):
    import os
    from agent_browser_core import session
    sess_paths = common.ProfilePaths("default", common._home(), "worker-1")
    sess_paths.downloads.mkdir(parents=True, exist_ok=True)
    old_dl = sess_paths.downloads / "report.csv"
    old_dl.write_bytes(b"x" * 4096)
    old_time = time.time() - (common.ARTIFACT_RETENTION_DAYS + 1) * 86400
    os.utime(old_dl, (old_time, old_time))

    assert evidence.evidence_usage(sess_paths.dir) == 0
    cleaned = session.clean(profile="default")
    assert cleaned["artifacts_removed"] == 1
    assert not old_dl.exists()




def test_bare_journal_clear_requires_commit_verb_and_is_journaled(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a"}, 5)
    seeded = daemon.journal.rows("manual")[0]["id"]
    with pytest.raises(ValueError, match="commit journal --clear") as exc:
        daemon.dispatch("journal", {"clear": True}, 1)
    assert exc.value.error_class == common.ERR_GUARDED and "commit journal --clear" in exc.value.hint
    denied = daemon.journal.rows("manual")[0]
    assert denied["verb"] == "journal" and denied["status"] == "failed"
    assert seeded in {row["id"] for row in daemon.journal.rows("manual")}
    assert daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)["cleared_commands"] == 2


@pytest.mark.parametrize("cmd,args,message", [("journal", {"clear": True}, "commit journal --clear"),
                                              ("state", {"resolve": True}, "commit state"),
                                              ("api", {"url": "/api/items", "method": "POST"}, "commit api")])
def test_forged_commit_flag_cannot_pass_the_commit_gate(make_daemon, cmd, args, message):
    from types import SimpleNamespace
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    sent = []
    ctx.request = SimpleNamespace(fetch=lambda url, **kwargs: sent.append(url))
    handoff_id = daemon.journal.open_handoff("manual", "Inspect layout", {})[0]
    with pytest.raises(ValueError, match=message) as exc:
        daemon.dispatch(cmd, {**args, "handoff_id": handoff_id, "_commit": True}, 1)
    assert exc.value.error_class == common.ERR_GUARDED
    assert sent == []
    assert daemon.journal.handoffs("manual", handoff_id)[0]["status"] == "needs_human"
    receipt = daemon.journal.rows("manual")[0]
    assert receipt["verb"] == cmd and receipt["status"] == "failed"


def test_handoff_resolution_works_at_full_quota_without_new_artifacts(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a"}, 5)
    handoff_id = daemon.journal.open_handoff("manual", "Confirm the change", {})[0]
    daemon.journal.quota = 0
    result = daemon.dispatch("commit", {"operation": "state", "handoff_id": handoff_id, "resolve": True}, 1)
    assert result["handoffs"][0]["status"] == "resolved" and "screenshots" not in result
    assert daemon.dispatch("commit", {"operation": "journal"}, 1)["commands"]
    assert not daemon.paths.shots.exists() or not list(daemon.paths.shots.glob("*.png"))
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("fill", {"target": "e1", "text": "value"}, 1)
    assert exc.value.error_class == "journal_full"


def test_unknown_handoff_id_is_not_found_for_queries_and_resolution(make_daemon):
    daemon, ctx, page = make_daemon()
    other = daemon.journal.open_handoff("other", "Inspect layout", {})[0]
    for cmd, args in (("state", {"handoff_id": "f" * 32}),
                      ("commit", {"operation": "state", "handoff_id": other, "resolve": True})):
        with pytest.raises(ValueError, match="No handoff") as exc:
            daemon.dispatch(cmd, args, 1)
        assert exc.value.error_class == common.ERR_NOT_FOUND and "state --profile" in exc.value.hint
    assert daemon.journal.handoffs("other", other)[0]["status"] == "needs_human"
    rows = daemon.journal.rows("manual")
    assert len(rows) == 1 and rows[0]["status"] == "failed"
    assert exc.value.details["receipt"] == "state --command " + rows[0]["id"]


def test_state_lists_open_handoffs_first_within_the_inspection_budget(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    monkeypatch.setattr(daemon, "_signed_in", lambda page, host: False)
    ids = [daemon.journal.open_handoff("manual", f"Sign in to app {n} " + "x" * 200, {"signed_in": f"app{n}.example"})[0] for n in range(60)]
    for handoff_id in ids[30:]:
        daemon.journal.resolve_handoff("manual", handoff_id)
    for n in range(40):
        daemon.journal.start(f"{n:032x}", "manual", "click", {"target": "e1"}, "http://x/")
        daemon.journal.finish(f"{n:032x}", "completed", {"effect": "clicked", "executed": True})
    result = daemon.dispatch("state", {}, 1)
    shown = result["handoffs"]
    assert 0 < len(shown) < 60 and result["handoffs_omitted"] == 60 - len(shown)
    assert [h["id"] for h in shown] == ids[29::-1][:len(shown)]  # open ones, newest first
    size = lambda rows: len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    assert size(shown) <= evidence.INSPECTION_BUDGET // 2
    assert size(shown) + size(result["commands"]) <= evidence.INSPECTION_BUDGET
    assert result["commands"] and result["command_next_offset"]
    assert daemon.dispatch("state", {"handoff_id": ids[0]}, 1)["handoffs_omitted"] == 0


def test_unrecorded_command_errors_point_at_no_receipt(make_daemon):
    daemon, ctx, page = make_daemon()
    with pytest.raises(ValueError) as exc:
        daemon.dispatch("state", {"handoff_id": "f" * 32}, 1)
    assert exc.value.details == {"executed": False}
    assert daemon.journal.rows("manual") == []


def test_needs_human_receipt_is_terminal_and_survives_restart(make_daemon, monkeypatch):
    from agent_browser_core import session
    daemon, ctx, page = make_daemon()
    monkeypatch.setattr(session, "focus_window", lambda paths: {"focused": False})
    result = daemon.dispatch("needs-human", {"reason": "Inspect unfamiliar layout"}, 1)
    assert result["pending"] is True and result["status"] == "needs_human"
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert row["status"] == "needs_human" and json.loads(row["outcome"])["pending"] is False
    restored = evidence.Journal(daemon.paths.dir)
    assert restored.rows("manual", result["command_id"])[0]["status"] == "needs_human"
    assert restored.handoffs("manual", result["handoff_id"])[0]["status"] == "needs_human"
    assert evidence.read_receipt(daemon.paths.dir, "manual", result["command_id"])["status"] == "needs_human"


def test_recipe_handoff_receipt_is_needs_human_not_pending(make_daemon, monkeypatch):
    from agent_browser_core import session
    daemon, ctx, page = make_daemon()
    monkeypatch.setattr(session, "focus_window", lambda paths: {"focused": False})
    result = daemon.dispatch("commit", {"operation": "run", "recipe": "sn/add-work-note", "number": "INC0012345", "text": "private note"}, 1)
    assert result["pending"] is True and result["status"] == "needs_human" and result["executed"] is False
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert row["verb"] == "commit run" and row["status"] == "needs_human"


def test_restart_restores_needs_human_receipts_left_by_older_daemons(tmp_path):
    journal = evidence.Journal(tmp_path)
    old = {"status": "needs_human", "handoff_id": "h", "pending": True, "executed": False}
    for command_id, status in (("pending-one", "pending"), ("swept-one", "unknown")):
        journal.start(command_id, "a", "needs-human", {}, "http://x/")
        journal.finish(command_id, status, old)
    journal.start("swept-write", "a", "commit api", {}, "http://x/")
    journal.finish("swept-write", "unknown", {**old, "executed": True})
    journal.start("wait", "a", "wait", {}, "http://x/")
    journal.finish("wait", "pending", {"pending": True, "executed": False})
    restored = {row["id"]: row for row in evidence.Journal(tmp_path).rows("a")}
    assert restored["pending-one"]["status"] == restored["swept-one"]["status"] == "needs_human"
    assert json.loads(restored["pending-one"]["outcome"])["pending"] is False
    assert restored["swept-write"]["status"] == restored["wait"]["status"] == "unknown"


def test_ping_does_not_write_receipts(make_daemon):
    daemon, ctx, page = make_daemon()
    for _ in range(3):
        daemon.dispatch("ping", {}, 1)
    assert daemon.journal.rows("manual") == []
    daemon.dispatch("goto", {"url": "http://x/a"}, 5)
    assert len(daemon.journal.rows("manual")) == 1


def test_receipt_records_api_path_and_recipe_number_but_not_values(tmp_path):
    journal = evidence.Journal(tmp_path)
    journal.start("c1", "manual", "commit api", {"url": "/api/now/table/incident?sysparm_query=secret", "method": "POST", "body_file": "x.json"}, "http://x/")
    journal.start("c2", "manual", "api", {"url": "https://x/api/items/42?token=secret#frag"}, "http://x/")
    journal.start("c3", "manual", "commit run", {"recipe": "sn/add-work-note", "number": "INC0012345", "text": "private"}, "http://x/")
    journal.start("c4", "manual", "run", {"recipe": "sn/add-work-note", "number": "INC0012345 private words", "text": "private"}, "http://x/")
    journal.start("c5", "manual", "run", {"recipe": "privaterecipe", "number": "ABCprivate0123456789", "text": "x"}, "http://x/")
    journal.start("c6", "manual", "run", {"recipe": ["sn/add-work-note"], "number": 12345}, "http://x/")
    journal.finish("c3", "completed", {"number": "INC0012345", "persisted": True})
    stored = {row["id"]: row for row in journal.rows("manual")}
    args = {key: json.loads(row["arguments"]) for key, row in stored.items()}
    assert args["c1"]["path"] == "/api/now/table/incident" and args["c2"]["path"] == "/api/items/42"
    assert args["c3"]["number"] == "INC0012345" and args["c3"]["recipe"] == "sn/add-work-note"
    assert args["c4"]["number"] == "<omitted>"
    assert args["c5"] == args["c6"] == {"recipe": "<omitted>", "number": "<omitted>"}
    assert "secret" not in json.dumps(stored) and "private" not in json.dumps(stored) and "frag" not in json.dumps(stored)
    assert json.loads(stored["c3"]["outcome"])["number"] == "INC0012345"


def test_replies_carrying_page_data_are_marked_untrusted_centrally(make_daemon):
    from agent_browser_core import daemon as core_daemon
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a"}, 5)
    assert {"commands", "downloads", "new_tab", "frames_loading", "selected", "background_dialogs"} <= set(common.UNTRUSTED_KEYS)
    assert {"tabs", "commands", "background_dialogs"} <= set(daemon.dispatch("state", {}, 1)["untrusted"])
    assert {"tabs", "url", "title"} <= set(daemon.dispatch("ping", {}, 1)["untrusted"])
    assert "commands" in daemon.dispatch("journal", {}, 1)["untrusted"]
    assert daemon.dispatch("snapshot", {}, 5)["untrusted"] == common.UNTRUSTED_KEYS  # a handler's own list is kept
    daemon.h_echo = lambda args, timeout: {"url": "http://x/a", "count": 1}
    daemon.h_plain = lambda args, timeout: {"count": 1}
    assert core_daemon.BrowserEngine.dispatch(daemon, "echo", {}, 1)["untrusted"] == ["url"]
    assert "untrusted" not in core_daemon.BrowserEngine.dispatch(daemon, "plain", {}, 1)


def _seed(journal, session, command_id, verb, status=None, outcome=None):
    """A receipt as the dispatcher leaves it; no status leaves it running, as an interrupted command does."""
    journal.start(command_id, session, verb, {}, "http://x/")
    if status:
        journal.finish(command_id, status, outcome or {})


def test_clear_keeps_unknown_writes_and_open_handoffs_but_clears_reads(make_daemon):
    daemon, ctx, page = make_daemon()
    journal = daemon.journal
    ids = {name: f"{n:032x}" for n, name in enumerate(
        ["swept_goto", "swept_click", "swept_commit_api", "commit_api", "upload", "eval", "fill_ran", "swept_wait", "snapshot",
         "completed", "failed", "needs_human", "other_session", "failed_clear", "failed_resolve"])}
    for name, verb in (("swept_goto", "goto"), ("swept_click", "click"), ("swept_commit_api", "commit api")):
        _seed(journal, "manual", ids[name], verb)
    _seed(journal, "manual", ids["swept_wait"], "wait", "pending", {"pending": True, "executed": False})
    evidence.Journal(daemon.paths.dir)  # the restart sweep marks every interrupted receipt unknown
    _seed(journal, "manual", ids["commit_api"], "commit api", "unknown", {"executed": True, "write_outcome": "unknown"})
    _seed(journal, "manual", ids["upload"], "upload", "unknown", {"executed": False})
    _seed(journal, "manual", ids["eval"], "eval", "unknown", {"executed": False})
    _seed(journal, "manual", ids["fill_ran"], "fill", "unknown", {"executed": True})
    _seed(journal, "manual", ids["snapshot"], "snapshot", "unknown", {"executed": False})
    _seed(journal, "manual", ids["completed"], "click", "completed", {"executed": True})
    _seed(journal, "manual", ids["failed"], "fill", "failed", {"executed": False})
    _seed(journal, "manual", ids["needs_human"], "needs-human", "needs_human", {"executed": False})
    _seed(journal, "other", ids["other_session"], "goto", "completed", {"executed": False})
    # A clear or resolution that failed partway changed only this journal; repeating it is harmless.
    _seed(journal, "manual", ids["failed_clear"], "commit journal", "unknown", {"executed": True})
    _seed(journal, "manual", ids["failed_resolve"], "commit state", "unknown", {"executed": True})
    open_id = journal.open_handoff("manual", "Confirm the change", {})[0]
    resolved_id = journal.open_handoff("manual", "Inspect layout", {})[0]
    journal.resolve_handoff("manual", resolved_id)
    other_handoff = journal.open_handoff("other", "Inspect layout", {})[0]
    swept = {row["id"]: row for row in journal.rows("manual", limit=100)}
    assert {swept[ids[name]]["status"] for name in ("swept_goto", "swept_click", "swept_commit_api", "swept_wait")} == {"unknown"}

    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)

    kept = {"swept_click", "swept_commit_api", "commit_api", "upload", "eval", "fill_ran"}
    survivors = {row["id"] for row in journal.rows("manual", limit=100)} - {result["command_id"]}
    assert survivors == {ids[name] for name in kept}
    assert result["cleared_commands"] == 8 and result["retained_commands"] == len(kept)
    assert result["cleared_handoffs"] == 1 and result["retained_handoffs"] == 1
    assert [h["id"] for h in journal.handoffs("manual")] == [open_id]
    assert result["note"] == common._hint("clear_retained", "default")
    assert journal.rows("other")[0]["id"] == ids["other_session"] and journal.handoffs("other")[0]["id"] == other_handoff
    receipt = json.loads(journal.rows("manual", result["command_id"])[0]["outcome"])
    assert (receipt["cleared_commands"], receipt["retained_commands"], receipt["cleared_handoffs"], receipt["retained_handoffs"]) == (8, len(kept), 1, 1)

    everything = daemon.dispatch("commit", {"operation": "journal", "clear": True, "include_unknown": True}, 1)
    assert everything["cleared_commands"] == len(kept) + 1 and everything["retained_commands"] == 0
    assert everything["cleared_handoffs"] == 1 and everything["retained_handoffs"] == 0 and "note" not in everything
    assert [row["id"] for row in journal.rows("manual")] == [everything["command_id"]]
    assert journal.handoffs("manual") == [] and journal.handoffs("other")[0]["id"] == other_handoff


def test_include_unknown_needs_commit_and_a_clear(make_daemon):
    daemon, ctx, page = make_daemon()
    _seed(daemon.journal, "manual", "a" * 32, "commit api", "unknown", {"executed": True})
    with pytest.raises(ValueError, match="commit journal --clear") as exc:
        daemon.dispatch("journal", {"clear": True, "include_unknown": True}, 1)
    assert exc.value.error_class == common.ERR_GUARDED
    for cmd, args in (("journal", {"include_unknown": True}), ("commit", {"operation": "journal", "include_unknown": True})):
        with pytest.raises(ValueError, match="--include-unknown") as exc:
            daemon.dispatch(cmd, args, 1)
        assert exc.value.error_class == common.ERR_USAGE
    assert "a" * 32 in {row["id"] for row in daemon.journal.rows("manual")}


def test_include_unknown_flag_reaches_the_clear_payload(monkeypatch):
    from agent_browser_core import cli
    captured = []
    monkeypatch.setattr(cli, "_remote", lambda args, verb, cmd, payload, *a, **kw: captured.append((cmd, payload, getattr(args, "committing", False))) or {"ok": True})
    for argv in (["commit", "journal", "--clear", "--include-unknown"], ["journal", "--clear"]):
        ns = cli._build_parser().parse_args(argv)
        ns.func(ns)
    (cmd, payload, committing), (_, plain, plain_committing) = captured
    assert cmd == "journal" and committing is True and payload["clear"] is True and payload["include_unknown"] is True
    assert plain_committing is False and plain["include_unknown"] is False


def test_clear_keeps_the_files_of_receipts_it_keeps(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    paths = daemon.paths
    kept, gone = "b" * 32, "c" * 32
    _seed(daemon.journal, "a", kept, "commit click", "unknown", {"executed": True})
    _seed(daemon.journal, "a", gone, "commit click", "completed", {"executed": True})
    paths.shots.mkdir(parents=True, exist_ok=True)
    paths.outputs.mkdir(parents=True, exist_ok=True)
    files = {}
    for command_id in (kept, gone):
        files[command_id] = [paths.shots / f"{command_id}-before.png", paths.shots / f"{command_id}-after.png", paths.outputs / f"{command_id}.body"]
        for path in files[command_id]:
            path.write_bytes(b"evidence")
    overflow = paths.outputs / ("d" * 32 + ".json")  # a random-ID overflow file names no receipt
    overflow.write_text("{}")
    (paths.shots / "nested").mkdir()
    (paths.shots / "nested" / "x.png").write_bytes(b"x")
    (paths.workspace / "last_output.json").write_text("{}")
    daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)  # the manual session's clear leaves sessions/ alone
    assert all(path.exists() for path in files[kept] + files[gone]) and overflow.exists()

    result = daemon.dispatch("commit", {"operation": "journal", "clear": True, "_session_id": "a"}, 1)

    assert result["retained_commands"] == 1
    assert all(path.exists() for path in files[kept])
    assert not any(path.exists() for path in files[gone])
    assert not overflow.exists() and not (paths.shots / "nested").exists()
    assert not (paths.workspace / "last_output.json").exists()
    daemon.dispatch("commit", {"operation": "journal", "clear": True, "include_unknown": True, "_session_id": "a"}, 1)
    assert not paths.shots.exists() and not paths.outputs.exists()


def test_clear_reports_files_it_could_not_remove_and_a_later_clear_removes_them(make_daemon, monkeypatch):
    from pathlib import Path
    daemon, ctx, page = make_daemon()
    done = "c" * 32
    _seed(daemon.journal, "manual", done, "commit click", "completed", {"executed": True})
    daemon.paths.shots.mkdir(parents=True, exist_ok=True)
    held = daemon.paths.shots / f"{done}-before.png"
    held.write_bytes(b"evidence")
    unlink = Path.unlink

    def in_use(path, missing_ok=False):
        if path == held:
            raise PermissionError("in use by another process")
        return unlink(path, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", in_use)

    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)

    assert result["files_not_removed"] == 1 and common._hint("clear_files_left", "default") in result["note"]
    assert held.exists() and daemon.journal.rows("manual", done) == []
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert row["status"] == "completed" and json.loads(row["outcome"])["files_not_removed"] == 1
    monkeypatch.setattr(Path, "unlink", unlink)
    again = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert "files_not_removed" not in again and again["retained_commands"] == 0 and not daemon.paths.shots.exists()


def test_clear_counts_an_unreadable_evidence_folder_instead_of_skipping_it(make_daemon, monkeypatch):
    from pathlib import Path
    daemon, ctx, page = make_daemon()
    daemon.paths.shots.mkdir(parents=True, exist_ok=True)
    shots, iterdir = daemon.paths.shots, Path.iterdir

    def denied(path):
        if path == shots.resolve():
            raise PermissionError("access denied")
        return iterdir(path)
    monkeypatch.setattr(Path, "iterdir", denied)
    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert result["files_not_removed"] == 1 and shots.exists()


try:
    from _winapi import CreateJunction
except ImportError:  # junctions are Windows-only
    CreateJunction = None


@pytest.mark.skipif(CreateJunction is None, reason="Windows junctions")
@pytest.mark.parametrize("keep_one", [False, True])
def test_clear_removes_a_junction_in_an_evidence_folder_without_following_it(make_daemon, tmp_path, keep_one):
    daemon, ctx, page = make_daemon()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not evidence")
    daemon.paths.shots.mkdir(parents=True, exist_ok=True)
    CreateJunction(str(outside), str(daemon.paths.shots / "linked"))
    if keep_one:
        _seed(daemon.journal, "manual", "b" * 32, "commit click", "unknown", {"executed": True})
        (daemon.paths.shots / ("b" * 32 + "-before.png")).write_bytes(b"evidence")
    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert "files_not_removed" not in result
    assert (outside / "keep.txt").exists() and not (daemon.paths.shots / "linked").exists()
    assert (daemon.paths.shots / ("b" * 32 + "-before.png")).exists() is keep_one


@pytest.mark.skipif(CreateJunction is None, reason="Windows junctions")
def test_linked_evidence_folder_is_refused_before_any_row_or_job_changes(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    other_shots = daemon.paths.shots
    other_shots.mkdir(parents=True, exist_ok=True)
    (other_shots / ("d" * 32 + "-before.png")).write_bytes(b"session b evidence")
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    CreateJunction(str(other_shots), str(daemon.paths.shots))
    wait = daemon.dispatch("wait", {"seconds": 100, "_cooperative": True, "_session_id": "a"}, 100)
    rows = {row["id"] for row in daemon.journal.rows("a")}
    with pytest.raises(ValueError, match="Unsafe evidence path") as exc:
        daemon.dispatch("commit", {"operation": "journal", "clear": True, "_session_id": "a"}, 1)
    assert exc.value.error_class == common.ERR_GUARDED
    assert (other_shots / ("d" * 32 + "-before.png")).exists()
    assert rows <= {row["id"] for row in daemon.journal.rows("a")}
    assert daemon.dispatch("poll", {"command_id": wait["command_id"], "_session_id": "a"}, 1)["pending"] is True


def test_clear_finishes_abandoned_jobs_instead_of_dropping_them(make_daemon):
    daemon, ctx, page = make_daemon()
    wait = daemon.dispatch("wait", {"seconds": 100, "_cooperative": True}, 100)
    other = daemon.dispatch("wait", {"seconds": 100, "_cooperative": True, "_session_id": "b"}, 100)
    ran = "e" * 32
    _seed(daemon.journal, "manual", ran, "click", "pending", {"executed": True, "pending": True})
    daemon.jobs[ran] = {"session": "manual", "checker": lambda: {"pending": True}, "executed": True}

    result = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)

    with pytest.raises(ValueError):
        daemon.dispatch("poll", {"command_id": wait["command_id"]}, 1)
    assert daemon.journal.rows("manual", wait["command_id"]) == []  # finished as failed, then cleared
    row = daemon.journal.rows("manual", ran)[0]
    outcome = json.loads(row["outcome"])
    assert row["status"] == "unknown" and result["retained_commands"] == 1
    assert (outcome["error_class"], outcome["executed"], outcome["pending"]) == ("cleared", True, False)
    assert ran not in daemon.jobs and other["command_id"] in daemon.jobs
    assert daemon.dispatch("poll", {"command_id": other["command_id"], "_session_id": "b"}, 1)["pending"] is True
    assert daemon.journal.rows("b", other["command_id"])[0]["status"] == "pending"


@pytest.mark.parametrize("cmd,args", [("fill", {"target": "e1", "text": "value"}), ("screenshot", {}),
                                      ("diagnose", {"url": "http://localhost:3000/"})])
def test_quota_error_splits_this_sessions_bytes_from_other_sessions(make_daemon, cmd, args):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    daemon.paths.outputs.mkdir(parents=True, exist_ok=True)
    (daemon.paths.outputs / ("a" * 32 + ".body")).write_bytes(b"x" * 70000)
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    daemon.journal.quota = 65536

    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch(cmd, {**args, "_session_id": "b"}, 1)

    details = exc.value.details
    assert exc.value.error_class == "journal_full" and details["quota"] == 65536
    assert details["other_sessions_bytes"] >= 70000 and details["session_bytes"] < 1000 and details["journal_bytes"] > 0
    assert details["evidence_bytes"] == details["session_bytes"] + details["other_sessions_bytes"] + details["journal_bytes"]
    assert "Other sessions of this profile hold" in str(exc.value) and exc.value.hint == common._hint("journal_full_other", "default")
    with pytest.raises(RuntimeError) as own:
        daemon.dispatch(cmd, {**args, "_session_id": "a"}, 1)
    assert own.value.details["session_bytes"] >= 70000 and own.value.details["other_sessions_bytes"] < 1000
    assert "Other sessions" not in str(own.value) and own.value.hint == common._hint("journal_full", "default")


def test_manual_session_bytes_leave_out_other_sessions_and_the_shared_journal(make_daemon):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/a", "_session_id": "a"}, 5)
    daemon.paths.outputs.mkdir(parents=True, exist_ok=True)
    (daemon.paths.outputs / ("a" * 32 + ".body")).write_bytes(b"x" * 70000)
    daemon.journal.quota = 65536
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("fill", {"target": "e1", "text": "value"}, 1)
    details = exc.value.details
    assert details["other_sessions_bytes"] >= 70000 and details["session_bytes"] < 1000
    assert details["journal_bytes"] == daemon.journal.path.stat().st_size


def _response(method, url, status=200):
    return SimpleNamespace(request=SimpleNamespace(method=method), url=url, status=status)


def _other_session_page(daemon):
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    return daemon.sessions["b"]["pages"][0]


def test_response_match_survives_unrelated_traffic_from_this_and_other_sessions(make_daemon):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    other = _other_session_page(daemon)
    pending = daemon.dispatch("click", {"target": "e2", "expect": ["response:POST **/api/items:2xx"]}, 30)
    assert pending["pending"] is True

    page.emit("response", _response("POST", "http://x/api/items", 201))
    for index in range(1100):  # more than any bounded history would keep
        page.emit("response", _response("GET", f"http://x/asset{index}.js"))
        other.emit("response", _response("GET", f"http://x/other{index}.js"))

    done = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)
    assert done["pending"] is False and done["expectations"] == [{"kind": "response", "passed": True}]


def test_response_on_another_sessions_page_never_satisfies_an_expectation(make_daemon):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    other = _other_session_page(daemon)
    pending = daemon.dispatch("click", {"target": "e2", "expect": ["response:POST **/api/items:2xx"]}, 30)
    other.emit("response", _response("POST", "http://x/api/items", 201))
    still = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)
    assert still["pending"] is True and still["expectations"] == [{"kind": "response", "passed": False}]


def test_recipe_persistence_check_survives_unrelated_traffic(make_daemon, monkeypatch):
    record = "http://x/incident.do?sys_id=" + "a" * 32
    daemon, _, page = make_daemon(page_url="http://x/nav_to.do")
    other = _other_session_page(daemon)
    form = FakeFrame(name="gsft_main", url=record)
    page.frames = [page.main_frame, form]
    reloaded = []
    note = SimpleNamespace(is_visible=lambda: True, evaluate=lambda js: True)
    form.get_by_text = lambda text, exact=False: SimpleNamespace(all=lambda: [note] if reloaded else [])
    form.get_by_label = lambda label, exact=False: SimpleNamespace(count=lambda: 1, input_value=lambda: "INC0012345")
    form.goto = lambda url, **kwargs: reloaded.append(url)
    fields = {"Number": "INC0012345", "Work notes": ""}
    monkeypatch.setattr(daemon, "resolve", lambda _page, target: SimpleNamespace(
        input_value=lambda: fields.get(target.get("label"), "") if isinstance(target, dict) else ""))
    monkeypatch.setattr(daemon, "h_fill", lambda args, timeout: {})

    def submit(args, timeout):
        page.emit("response", _response("POST", record))
        for index in range(1100):
            page.emit("response", _response("GET", f"http://x/asset{index}.js"))
            other.emit("response", _response("POST", record))  # another session's page: never this record's evidence
        return {}
    monkeypatch.setattr(daemon, "h_click", submit)

    result = daemon.dispatch("commit", {"operation": "run", "recipe": "sn/add-work-note", "number": "INC0012345", "text": "note"}, 30)

    assert result["persisted"] is True and result["pending"] is False and reloaded == [record]


def test_every_request_refreshes_the_idle_clock(make_daemon):
    daemon, _, _ = make_daemon()
    assert daemon.idle_expired(0) is False  # AGENT_BROWSER_IDLE_SECONDS=0 never expires
    daemon.last_activity = 0.0
    assert daemon.idle_expired(60) is True
    pending = daemon.dispatch("wait", {"seconds": 100, "_session_id": "a", "_cooperative": True}, 100)
    assert pending["pending"] and not daemon.idle_expired(60)
    daemon.last_activity = 0.0
    daemon.dispatch("poll", {"command_id": pending["command_id"], "_session_id": "a"}, 1)
    assert not daemon.idle_expired(60)
    daemon.last_activity = 0.0
    with pytest.raises(ValueError):
        daemon.dispatch("poll", {"command_id": "unknown", "_session_id": "a"}, 1)
    assert not daemon.idle_expired(60)


def test_a_long_command_does_not_end_with_the_idle_clock_expired(make_daemon, fake_clock, monkeypatch):
    daemon, _, _ = make_daemon()
    real_tabs = daemon.h_tabs

    def slow_tabs(args, timeout):
        fake_clock.sleep(120)
        return real_tabs(args, timeout)

    monkeypatch.setattr(daemon, "h_tabs", slow_tabs)
    daemon.dispatch("tabs", {"_session_id": "a"}, 200)
    assert not daemon.idle_expired(60)
