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
