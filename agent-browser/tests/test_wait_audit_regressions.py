"""Cooperative waits retain their original deadlines and terminal receipts."""
import json

import pytest

from agent_browser_core import common as core_common


def test_cooperative_wait_is_marked_busy_only_while_evaluating(make_daemon, fake_clock, monkeypatch):
    daemon, _, _ = make_daemon()
    seen = {}
    real_job, real_poll = daemon._wait_job, daemon._poll_wait

    def job(*args, **kwargs):
        seen["job"] = daemon.current_cmd
        return real_job(*args, **kwargs)

    def poll(*args, **kwargs):
        seen["poll"] = daemon.current_cmd
        return real_poll(*args, **kwargs)

    monkeypatch.setattr(daemon, "_wait_job", job)
    monkeypatch.setattr(daemon, "_poll_wait", poll)
    pending = daemon.dispatch("wait", {"seconds": 1, "_cooperative": True}, 30)
    assert pending["pending"] is True and seen["job"] == "wait" and daemon.current_cmd is None

    # A pending wait is not busy: the 0.2 s polls that observe it run as control requests.
    del seen["poll"]
    daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)
    assert seen["poll"] is None and daemon.current_cmd is None


@pytest.mark.parametrize("cooperative", [True, False])
def test_wait_on_a_page_outside_allowed_hosts_is_refused_on_both_paths(make_daemon, cooperative):
    daemon, _, _ = make_daemon(config={"allowed_hosts": ["allowed.example"]}, page_url="http://other.example/x")
    args = {"seconds": 1, **({"_cooperative": True} if cooperative else {})}

    with pytest.raises(ValueError) as raised:
        daemon.dispatch("wait", args, 30)

    assert raised.value.error_class == core_common.ERR_GUARDED
    assert raised.value.hint == core_common._hint("off_allowlist_wait", daemon.profile)
    assert "state --handoff" in raised.value.hint
    assert daemon.jobs == {} and daemon.current_cmd is None
    row = daemon.journal.rows("manual", raised.value.details["command_id"])[0]
    assert row["status"] == "failed" and json.loads(row["outcome"])["error_class"] == core_common.ERR_GUARDED


def test_off_allowlist_message_lists_the_verbs_the_constant_allows(make_daemon, monkeypatch):
    daemon, _, _ = make_daemon(config={"allowed_hosts": ["allowed.example"]}, page_url="http://other.example/x")
    monkeypatch.setattr(core_common, "OFF_ALLOWLIST_OK", core_common.OFF_ALLOWLIST_OK | {"value"})

    with pytest.raises(ValueError) as raised:
        daemon.dispatch("click", {"target": "e1"}, 5)

    message = str(raised.value)
    assert all(verb in message for verb in sorted(core_common.OFF_ALLOWLIST_OK - {"ping"}))
    assert "ping" not in message and raised.value.hint == core_common._hint("off_allowlist", daemon.profile)


@pytest.mark.parametrize("stage", ["settle", "snapshot", "ref"])
def test_wait_poll_failure_finishes_original_receipt(make_daemon, fake_clock, monkeypatch, stage):
    daemon, _, page = make_daemon()
    pending = daemon.dispatch("wait", {"seconds": 1, "_cooperative": True}, 30)
    command_id = pending["command_id"]
    fake_clock.value += 2

    def fail(*args, **kwargs):
        raise RuntimeError("Wait observation unavailable")

    if stage == "ref":
        daemon.jobs[command_id]["conditions"] = {"ref": "e1"}
        monkeypatch.setattr(daemon, "resolve", fail)
    else:
        monkeypatch.setattr(daemon, "_settle" if stage == "settle" else "snap_envelope", fail)

    with pytest.raises(RuntimeError, match="observation unavailable") as raised:
        daemon.dispatch("poll", {"command_id": command_id}, 2)

    assert raised.value.details["command_id"] == command_id
    assert raised.value.details["executed"] is False
    assert raised.value.details["receipt"] == "state --command " + command_id
    assert command_id not in daemon.jobs
    row = daemon.journal.rows("manual", command_id)[0]
    assert row["status"] == "failed"
    assert json.loads(row["outcome"])["pending"] is False


def test_closed_wait_tab_does_not_leave_a_pending_job(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon()
    pending = daemon.dispatch("wait", {"selector": ".pending", "_cooperative": True}, 30)
    command_id = pending["command_id"]
    page.close()

    def closed(*args, **kwargs):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(page, "locator", closed)
    with pytest.raises(RuntimeError, match="has been closed") as raised:
        daemon.dispatch("poll", {"command_id": command_id}, 2)

    assert raised.value.error_class == "browser_closed"
    assert raised.value.details["command_id"] == command_id
    assert command_id not in daemon.jobs
    assert daemon.journal.rows("manual", command_id)[0]["status"] == "failed"


def test_initial_wait_capture_failure_removes_job(make_daemon, monkeypatch):
    daemon, _, page = make_daemon()

    def fail(*args, **kwargs):
        raise RuntimeError("Snapshot unavailable")

    monkeypatch.setattr(daemon, "snap_envelope", fail)
    with pytest.raises(RuntimeError, match="Snapshot unavailable") as raised:
        daemon.dispatch("wait", {"url_contains": "outer", "_cooperative": True}, 30)

    command_id = raised.value.details["command_id"]
    assert command_id not in daemon.jobs
    assert daemon.journal.rows("manual", command_id)[0]["status"] == "failed"


def test_failed_wait_does_not_inherit_another_actions_execution(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e1]\n')
    pending = daemon.dispatch("wait", {"seconds": 2, "_cooperative": True}, 30)
    daemon.dispatch("click", {"target": "e1"}, 5)
    assert daemon.action_attempted is True
    fake_clock.value += 3

    def fail(*args, **kwargs):
        raise RuntimeError("Snapshot unavailable")

    monkeypatch.setattr(daemon, "snap_envelope", fail)
    with pytest.raises(RuntimeError) as raised:
        daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)

    assert raised.value.details["executed"] is False
    assert daemon.journal.rows("manual", pending["command_id"])[0]["status"] == "failed"
    assert page.elements["e1"].calls == [("click", "left")]


def test_failed_action_expectation_keeps_unknown_execution_without_replay(make_daemon, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e1]\n')
    pending = daemon.dispatch("click", {"target": "e1", "expect": ["text:Saved"]}, 30)
    command_id = pending["command_id"]

    def fail(*args, **kwargs):
        raise RuntimeError("Verification unavailable")

    monkeypatch.setattr(daemon, "_text_counts", fail)
    with pytest.raises(RuntimeError) as raised:
        daemon.dispatch("poll", {"command_id": command_id}, 2)

    assert raised.value.details["command_id"] == command_id
    assert raised.value.details["executed"] is True
    assert command_id not in daemon.jobs
    row = daemon.journal.rows("manual", command_id)[0]
    assert row["status"] == "unknown"
    assert json.loads(row["outcome"])["pending"] is False
    assert page.elements["e1"].calls == [("click", "left")]


def test_wait_rejects_late_success_after_other_session_delay(make_daemon, fake_clock):
    daemon, _, _ = make_daemon()
    pending = daemon.dispatch("wait", {"seconds": 20, "_cooperative": True, "_session_id": "a"}, 5)
    daemon.dispatch("goto", {"url": "http://x/b", "_session_id": "b"}, 5)
    fake_clock.value += 30

    with pytest.raises(RuntimeError, match="deadline expired") as raised:
        daemon.dispatch("poll", {"command_id": pending["command_id"], "_session_id": "a"}, 2)

    assert raised.value.details["command_id"] == pending["command_id"]
    assert pending["command_id"] not in daemon.jobs
    assert daemon.journal.rows("a", pending["command_id"])[0]["status"] == "failed"


def test_wait_rejects_observation_that_finishes_after_deadline(make_daemon, fake_clock, monkeypatch):
    daemon, _, _ = make_daemon()
    pending = daemon.dispatch("wait", {"text": "Saved", "_cooperative": True}, 5)

    def slow_observation(*args, **kwargs):
        fake_clock.value += 10
        return True

    monkeypatch.setattr(daemon, "_page_has_text", slow_observation)
    with pytest.raises(RuntimeError, match="deadline expired"):
        daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)

    assert pending["command_id"] not in daemon.jobs
    assert daemon.journal.rows("manual", pending["command_id"])[0]["status"] == "failed"
