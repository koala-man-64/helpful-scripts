"""Action deadlines cover preflight, the browser action and post-action settling."""

import json
import pytest

from agent_browser_core import common


def test_zero_timeout_snapshot_keeps_instant_read_behavior(make_daemon):
    daemon, _, _ = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')

    result = daemon.dispatch("snapshot", {}, 0.0)

    assert result["snapshot"] == ['- button "Save" [ref=e2]']


def test_deadline_expired_during_expectation_preflight_refuses_the_action(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    original = daemon._expect_specs

    def preflight_then_expire(specs, target_page, deadline=None):
        parsed = original(specs, target_page, deadline=deadline)
        fake_clock.value = deadline + 0.01
        return parsed

    monkeypatch.setattr(daemon, "_expect_specs", preflight_then_expire)
    with pytest.raises(RuntimeError) as error:
        daemon.dispatch("click", {"target": "e2", "expect": ["text:Saved"]}, 1.0)

    assert error.value.error_class == common.ERR_TIMEOUT
    assert error.value.details["executed"] is False
    assert page.elements["e2"].calls == []
    receipt = daemon.journal.rows("manual", error.value.details["command_id"])[0]
    assert receipt["status"] == "failed"


def test_deadline_expired_during_settling_reports_the_attempted_click(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    button = page.elements["e2"]
    button.click_side_effect = lambda: setattr(page.main_frame, "body_text", "Saved")

    def slow_settle(_page, cap=3.0):
        assert cap > 0
        fake_clock.value += 2.0
        return []

    monkeypatch.setattr(daemon, "_settle", slow_settle)
    with pytest.raises(RuntimeError) as error:
        daemon.dispatch("click", {"target": "e2", "expect": ["text:Saved"]}, 1.0)

    assert error.value.error_class == common.ERR_TIMEOUT
    assert error.value.details["executed"] is True
    assert button.calls == [("click", "left")]
    receipt = daemon.journal.rows("manual", error.value.details["command_id"])[0]
    assert receipt["status"] == "unknown"
    assert json.loads(receipt["outcome"])["executed"] is True
