"""Session ownership, audit retention and cooperative scheduling contracts."""
import json
import time

import pytest
from agent_browser_core import common, evidence


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
    assert len(daemon.journal.rows("manual")) == 2


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
    assert not daemon.dispatch("state", {"handoff_id": handoff_id, "_session_id": "other"}, 1)["handoffs"]


@pytest.mark.parametrize("level", ["read", "act"])
def test_handoff_resolution_requires_commit_permission(make_daemon, level):
    daemon, ctx, page = make_daemon(config={"permission": level})
    handoff_id = daemon.journal.handoff("manual", "Inspect layout", {})
    with pytest.raises(ValueError, match="commit profile"):
        daemon.dispatch("commit", {"operation": "state", "handoff_id": handoff_id, "resolve": True}, 1)
    assert daemon.journal.handoffs("manual", handoff_id)[0]["status"] == "needs_human"


def test_explicit_resolution_cannot_bypass_auth_condition(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    daemon.dispatch("goto", {"url": "http://x/"}, 1)
    handoff_id = daemon.journal.handoff("manual", "Sign in", {"signed_in": "x", "require_navigation": True, "navigation": 0})
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
    daemon.dispatch("journal", {"clear": True}, 1)
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


