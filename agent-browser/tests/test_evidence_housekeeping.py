"""Evidence housekeeping: who holds evidence (journal --sessions), opt-in retention, and a bounded daemon.log."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_browser_core import common, daemon as core_daemon, evidence, mcp_server, policy
from test_daemon import _write_launch, serve_seams  # noqa: F401 - serve_seams is a fixture

DAY = 86400


def _receipt(journal: evidence.Journal, session: str, command_id: str, verb: str, status: str | None, outcome: dict[str, Any] | None = None,
             *, age_days: float = 0) -> None:
    """A receipt as the dispatcher leaves it, last touched `age_days` ago. No status leaves it running."""
    journal.start(command_id, session, verb, {}, "http://x/")
    if status:
        journal.finish(command_id, status, outcome or {})
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime(common.UTC_FORMAT)
    with journal.connect() as db:
        db.execute("UPDATE commands SET started=?, finished=CASE WHEN finished IS NULL THEN NULL ELSE ? END WHERE id=?", (stamp, stamp, command_id))


def _file(path: Path, *, age_days: float = 0, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    moment = time.time() - age_days * DAY
    os.utime(path, (moment, moment))
    return path


def _ids(*names: str) -> dict[str, str]:
    return {name: f"{number:032x}" for number, name in enumerate(names, start=1)}


def _workspace(daemon: Any, session: str) -> Path:
    return common.ProfilePaths(daemon.profile, daemon.paths.root, session).workspace


# ---------------------------------------------------------------------------
# journal --sessions


def test_sessions_lists_every_session_with_evidence_newest_first(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon()
    ids = _ids("old", "write", "recent")
    _receipt(daemon.journal, "chat-a", ids["old"], "click", "completed", age_days=40)
    _receipt(daemon.journal, "chat-a", ids["write"], "commit api", "unknown", {"executed": True}, age_days=39)
    _receipt(daemon.journal, "chat-b", ids["recent"], "goto", "completed", age_days=1)
    daemon.journal.open_handoff("chat-c", "Sign in", {})
    _file(_workspace(daemon, "chat-a") / "shots" / (ids["old"] + "-before.png"), size=3000)

    listed = daemon.dispatch("journal", {"sessions": True}, 5.0)

    by_id = {entry["session"]: entry for entry in listed["sessions"]}
    assert [entry["session"] for entry in listed["sessions"]][:2] == ["chat-b", "chat-a"]  # most recently active first
    assert by_id["chat-a"]["receipts"] == 2 and by_id["chat-a"]["unknown"] == 1 and by_id["chat-a"]["bytes"] == 3000
    assert by_id["chat-b"]["receipts"] == 1 and by_id["chat-b"]["bytes"] == 0 and by_id["chat-b"]["current"] is False
    assert by_id["chat-c"] == {"session": "chat-c", "receipts": 0, "unknown": 0, "open_handoffs": 1, "last": None, "bytes": 0, "current": False}
    assert listed["sessions_omitted"] == 0 and listed["evidence_retention_days"] is None
    assert listed["note"] == common._hint("sessions_clear", daemon.profile) and "--session" in listed["note"]
    # A read: it leaves no receipt of its own and needs no commit.
    assert "manual" not in by_id and "command_id" in listed and daemon.journal.rows("manual") == []


def test_sessions_counts_the_manual_session_without_the_shared_database(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon()
    _receipt(daemon.journal, "manual", "a" * 32, "click", "completed")
    _file(daemon.paths.dir / "shots" / ("a" * 32 + "-after.png"), size=500)
    _file(_workspace(daemon, "chat-a") / "outputs" / ("b" * 32 + ".json"), size=900)  # another session's file

    manual = next(entry for entry in daemon.dispatch("journal", {"sessions": True}, 5.0)["sessions"] if entry["session"] == "manual")

    assert manual["bytes"] == 500 and manual["current"] is True


def test_sessions_is_bounded_and_takes_no_other_journal_option(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_browser_core import runtime

    daemon, _ctx, _page = make_daemon()
    for number in range(5):
        _receipt(daemon.journal, f"chat-{number}", f"{number + 1:032x}", "goto", "completed", age_days=number)
    monkeypatch.setattr(runtime, "SESSIONS_SHOWN", 3)

    listed = daemon.dispatch("journal", {"sessions": True}, 5.0)
    assert [entry["session"] for entry in listed["sessions"]] == ["chat-0", "chat-1", "chat-2"] and listed["sessions_omitted"] == 2

    for extra in ({"clear": True}, {"command_id": "a" * 32}, {"include_unknown": True}):
        with pytest.raises(ValueError) as raised:
            daemon.dispatch("journal", {"sessions": True, **extra}, 5.0)
        assert raised.value.error_class in (common.ERR_USAGE, common.ERR_GUARDED)


def test_sessions_flag_reaches_the_daemon_from_the_cli_and_from_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_browser_core import cli

    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(cli, "_session_for", lambda args, verb, autostart=False: (None, {"token": "t", "port": 1, "profile": "default"}))
    monkeypatch.setattr(cli.core_session, "_request", lambda session, cmd, payload, timeout, **kw: sent.append((cmd, payload)) or {"sessions": []})
    assert cli.main(["journal", "--sessions"]) == 0
    assert sent[0][0] == "journal" and sent[0][1]["sessions"] is True

    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, arguments, **kw: calls.append((command, arguments)) or {"sessions": []})
    assert "error" not in mcp_server.browser_read("journal", "s", sessions=True)
    assert calls[0][0] == "journal" and calls[0][1]["sessions"] is True
    refused = mcp_server.browser_read("text", "s", sessions=True)
    assert refused["error"]["class"] == common.ERR_USAGE and len(calls) == 1


# ---------------------------------------------------------------------------
# evidence_retention_days


@pytest.mark.parametrize("value", [0, -3, 1.5, "30", True])
def test_retention_days_must_be_a_positive_integer(value: Any) -> None:
    with pytest.raises(RuntimeError) as raised:
        policy.profile_config({"evidence_retention_days": value}, "default")
    assert raised.value.error_class == common.ERR_CONFIG and "evidence_retention_days" in str(raised.value)


def test_retention_removes_old_settled_evidence_and_keeps_what_a_clear_keeps(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon(config={"evidence_retention_days": 30})
    journal = daemon.journal
    ids = _ids("old_done", "old_failed", "old_read_unknown", "old_write_unknown", "old_running", "recent_done", "old_other")
    _receipt(journal, "chat-a", ids["old_done"], "commit click", "completed", {"executed": True}, age_days=45)
    _receipt(journal, "chat-a", ids["old_failed"], "fill", "failed", {"executed": False}, age_days=45)
    _receipt(journal, "chat-a", ids["old_read_unknown"], "snapshot", "unknown", {"executed": False}, age_days=45)
    _receipt(journal, "chat-a", ids["old_write_unknown"], "commit api", "unknown", {"executed": True}, age_days=45)
    _receipt(journal, "chat-a", ids["old_running"], "click", None, age_days=45)
    _receipt(journal, "chat-a", ids["recent_done"], "click", "completed", {"executed": True}, age_days=2)
    _receipt(journal, "manual", ids["old_other"], "goto", "completed", age_days=45)
    open_handoff = journal.open_handoff("chat-a", "Sign in", {})[0]

    workspace = _workspace(daemon, "chat-a")
    doomed_shot = _file(workspace / "shots" / (ids["old_done"] + "-before.png"), age_days=45)
    kept_body = _file(workspace / "outputs" / (ids["old_write_unknown"] + ".body"), age_days=45)   # names a receipt that stays
    old_overflow = _file(workspace / "outputs" / ("f" * 32 + ".json"), age_days=45)                # names no receipt
    new_overflow = _file(workspace / "outputs" / ("e" * 32 + ".json"), age_days=2)
    manual_shot = _file(daemon.paths.dir / "shots" / (ids["old_other"] + ".png"), age_days=45)
    download = _file(workspace / "downloads" / "report.csv", age_days=45)                          # not evidence

    removed = daemon.apply_retention()

    left = {row["id"] for session in ("chat-a", "manual") for row in journal.rows(session, limit=100)}
    assert left == {ids["old_write_unknown"], ids["old_running"], ids["recent_done"]}
    assert removed == {"receipts": 4, "handoffs": 0, "files": 3}
    assert not doomed_shot.exists() and not old_overflow.exists() and not manual_shot.exists()
    assert kept_body.exists() and new_overflow.exists() and download.exists()
    assert [handoff["id"] for handoff in journal.handoffs("chat-a")] == [open_handoff]


def test_retention_forgets_a_session_nobody_has_used(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon(config={"evidence_retention_days": 7})
    journal = daemon.journal
    _receipt(journal, "chat-gone", "a" * 32, "click", "completed", age_days=20)
    resolved = journal.open_handoff("chat-gone", "Inspect layout", {})[0]
    journal.resolve_handoff("chat-gone", resolved)
    workspace = _workspace(daemon, "chat-gone")
    _file(workspace / "shots" / ("a" * 32 + "-after.png"), age_days=20)
    _file(workspace / "last_snapshot.txt", age_days=20)
    _file(workspace / "last_output.json", age_days=20)

    removed = daemon.apply_retention()

    assert removed == {"receipts": 1, "handoffs": 1, "files": 3}
    assert not workspace.exists()                                        # emptied, then removed
    assert "chat-gone" not in {entry["session"] for entry in journal.sessions()}
    assert daemon.paths.dir.is_dir() and (daemon.paths.dir / "journal.sqlite3").is_file()


def test_retention_never_follows_a_linked_evidence_folder(make_daemon: Any, tmp_path: Path) -> None:
    daemon, _ctx, _page = make_daemon(config={"evidence_retention_days": 7})
    outside = tmp_path / "outside"
    precious = _file(outside / "notes.txt", age_days=90)
    workspace = _workspace(daemon, "chat-a")
    workspace.mkdir(parents=True)
    try:
        os.symlink(outside, workspace / "outputs", target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symlinks")

    daemon.apply_retention()

    assert precious.exists()


def test_maintain_applies_retention_once_a_day_and_survives_a_failure(make_daemon: Any, fake_clock: Any, monkeypatch: pytest.MonkeyPatch,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    daemon, _ctx, _page = make_daemon(config={"evidence_retention_days": 7})
    runs: list[float] = []
    monkeypatch.setattr(daemon, "apply_retention", lambda: runs.append(fake_clock.value) or {"receipts": 2, "handoffs": 0, "files": 5})
    capsys.readouterr()

    daemon.maintain()
    daemon.maintain()
    assert len(runs) == 1 and "retention removed 2 receipts, 0 handoffs and 5 files" in capsys.readouterr().err

    fake_clock.sleep(DAY + 1)
    daemon.maintain()
    assert len(runs) == 2

    def broken() -> dict[str, int]:
        raise OSError("disk went away")

    monkeypatch.setattr(daemon, "apply_retention", broken)
    fake_clock.sleep(DAY + 1)
    daemon.maintain()  # logged, not raised
    assert "retention failed: OSError" in capsys.readouterr().err and daemon.stop_reason is None


def test_without_the_setting_nothing_is_ever_removed(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon, _ctx, _page = make_daemon()
    _receipt(daemon.journal, "chat-a", "a" * 32, "click", "completed", age_days=900)
    old_shot = _file(_workspace(daemon, "chat-a") / "shots" / ("a" * 32 + "-before.png"), age_days=900)
    monkeypatch.setattr(daemon, "apply_retention", lambda: pytest.fail("retention ran without evidence_retention_days"))

    daemon.maintain()

    assert old_shot.exists() and len(daemon.journal.rows("chat-a")) == 1


# ---------------------------------------------------------------------------
# daemon.log stays bounded


def test_a_log_past_the_limit_keeps_its_last_megabyte_from_a_line_start(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    line = b"2026-11-19T06:04:01Z poll ok 13ms\n"
    log.write_bytes(line * (core_daemon.LOG_TRIM_AT // len(line) + 500) + b"2026-11-19T06:05:11Z goto ok 10817ms\n")

    assert core_daemon._trim_log(log) is True

    kept = log.read_bytes()
    assert core_daemon.LOG_KEEP - len(line) <= len(kept) <= core_daemon.LOG_KEEP
    assert kept.startswith(b"2026-11-19T") and kept.endswith(b"goto ok 10817ms\n") and b"\x00" not in kept


def test_a_log_under_the_limit_is_left_alone(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    log.write_bytes(b"2026-11-19T06:04:01Z poll ok 13ms\n" * 100)
    before = log.read_bytes()

    assert core_daemon._trim_log(log) is False and core_daemon._trim_log(tmp_path / "missing.log") is False
    assert log.read_bytes() == before


def test_a_starting_daemon_applies_retention(serve_seams: list[bool]) -> None:
    paths = common._paths("default")
    journal = evidence.Journal(paths.dir)
    _receipt(journal, "chat-gone", "a" * 32, "click", "completed", age_days=30)
    _receipt(journal, "chat-gone", "b" * 32, "click", "completed", age_days=1)
    launch_file = _write_launch(paths, "daemon-token", idle_seconds=0.01, config={"evidence_retention_days": 7})

    assert core_daemon.serve(launch_file) == 0

    assert [row["id"] for row in journal.rows("chat-gone")] == ["b" * 32]
    assert "retention removed 1 receipts, 0 handoffs and 0 files" in paths.log.read_text(encoding="utf-8")


def test_a_starting_daemon_without_the_setting_removes_nothing(serve_seams: list[bool]) -> None:
    paths = common._paths("default")
    journal = evidence.Journal(paths.dir)
    _receipt(journal, "chat-gone", "a" * 32, "click", "completed", age_days=900)
    launch_file = _write_launch(paths, "daemon-token", idle_seconds=0.01)

    assert core_daemon.serve(launch_file) == 0

    assert [row["id"] for row in journal.rows("chat-gone")] == ["a" * 32]
    assert "retention" not in paths.log.read_text(encoding="utf-8")
