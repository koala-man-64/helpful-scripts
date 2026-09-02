"""doctor(): every seam (registry, PATH, subprocess, browser discovery, policies, job info,
running profiles, Path.home) is monkeypatched. Nothing here touches the real registry, PATH,
or filesystem outside the isolated AGENT_BROWSER_HOME / tmp_path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import agent_browser as ab

REPO_SKILL = Path(ab.__file__).resolve().parents[1] / "agentic-ide-setup" / "profile" / "claude" / "skills" / ab.TOOL_NAME / "SKILL.md"


def _install_happy_path_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, skill_home: Path | None = None) -> None:
    monkeypatch.setattr(ab, "_playwright_version", lambda: "1.61.0")
    monkeypatch.setattr(ab.shutil, "which", lambda name: str(tmp_path / "agent-browser.exe"))
    monkeypatch.setattr(ab.subprocess, "run", lambda cmd, **kw: type("R", (), {"stdout": str(tmp_path / "agent-browser.exe") + "\n"})())
    monkeypatch.setattr(ab, "find_browser", lambda kind, explicit=None: tmp_path / "msedge.exe")
    monkeypatch.setattr(ab, "browser_version_from_path", lambda exe: "119.0.5845.0")
    monkeypatch.setattr(ab, "read_policies", lambda kind: {})
    monkeypatch.setattr(ab, "_registry_value", lambda key_path, value_name, hive="HKLM": 1)
    monkeypatch.setattr(ab, "_job_info", lambda: {"in_job": False, "limit_flags": None, "flags": [], "kill_on_job_close": False})
    monkeypatch.setattr(ab, "_processes_using", lambda needle: [])
    monkeypatch.setattr(ab, "_running_profiles", lambda: ["default"])
    home = skill_home if skill_home is not None else (tmp_path / "no-home")
    monkeypatch.setattr(ab.Path, "home", staticmethod(lambda: home))


# ---------------------------------------------------------------------------
# The full happy path: ok True, and the check ids appear in the documented order


def test_doctor_happy_path_ok_true_and_check_ids_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)  # no ~/.claude/skills/agent-browser -> "missing"

    result = ab.doctor()

    assert result["ok"] is True
    assert result["failed"] == []
    ids = [c["id"] for c in result["checks"]]
    expected = [
        "python", "playwright_import", "playwright_version", "console_script",
        "browser_found", "browser_version", "policies", "home_writable", "profile_path_length",
        "long_paths_enabled", "port_bind", "job_object", "config_json", "skill_installed", "sessions",
    ]
    assert ids == expected


def test_doctor_sessions_row_lists_running_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(ab, "_running_profiles", lambda: ["alpha", "beta"])
    result = ab.doctor()
    row = next(c for c in result["checks"] if c["id"] == "sessions")
    assert row["detail"] == "alpha, beta"

    monkeypatch.setattr(ab, "_running_profiles", lambda: [])
    row_none = next(c for c in ab.doctor()["checks"] if c["id"] == "sessions")
    assert row_none["detail"] == "none running"


# ---------------------------------------------------------------------------
# playwright_import: missing vs present


def test_doctor_playwright_missing_fails_that_row_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(ab, "_playwright_version", lambda: None)

    result = ab.doctor()

    assert result["ok"] is False
    assert "playwright_import" in result["failed"]
    row = next(c for c in result["checks"] if c["id"] == "playwright_import")
    assert row["status"] == "fail"
    assert row["hint"] == ab._hint("playwright_missing")
    assert not any(c["id"] == "playwright_version" for c in result["checks"])  # only checked when a version is present


# ---------------------------------------------------------------------------
# policy failures propagate to ok=False and show up as policy_<name> rows


def test_doctor_policy_failure_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(ab, "read_policies", lambda kind: {
        "RemoteDebuggingAllowed": {"hive": "HKLM", "value": 0, "status": "fail", "label": "remote debugging BLOCKED by policy"},
    })

    result = ab.doctor()

    assert result["ok"] is False
    assert "policy_RemoteDebuggingAllowed" in result["failed"]
    ids = [c["id"] for c in result["checks"]]
    assert "policy_RemoteDebuggingAllowed" in ids
    assert "policies" not in ids  # the "no policies" row only appears when the dict is empty


# ---------------------------------------------------------------------------
# job_object: warns only when kill_on_job_close is set without any breakaway flag


@pytest.mark.parametrize(
    "job, expected_status",
    [
        ({"in_job": True, "kill_on_job_close": True, "flags": []}, "warn"),
        ({"in_job": True, "kill_on_job_close": True, "flags": ["BREAKAWAY_OK"]}, "ok"),
        ({"in_job": True, "kill_on_job_close": True, "flags": ["SILENT_BREAKAWAY_OK"]}, "ok"),
        ({"in_job": True, "kill_on_job_close": False, "flags": []}, "ok"),
        ({"in_job": False, "kill_on_job_close": False, "flags": []}, "ok"),
    ],
)
def test_doctor_job_object_warns_only_when_trapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job: dict[str, Any], expected_status: str) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(ab, "_job_info", lambda: {"limit_flags": None, **job})

    result = ab.doctor()

    row = next(c for c in result["checks"] if c["id"] == "job_object")
    assert row["status"] == expected_status
    if expected_status == "warn":
        assert "AGENT_BROWSER_LAUNCHER=wmi" in row["hint"]


# ---------------------------------------------------------------------------
# skill_installed: ok (matches the repo copy) / warn (differs) / warn (missing)


def test_doctor_skill_installed_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "no-skill-home"
    home.mkdir()
    _install_happy_path_seams(monkeypatch, tmp_path, skill_home=home)

    row = next(c for c in ab.doctor()["checks"] if c["id"] == "skill_installed")
    assert row["status"] == "warn"
    assert "missing" in row["detail"]


@pytest.mark.skipif(not REPO_SKILL.is_file(), reason="repo does not carry the installable SKILL.md copy")
def test_doctor_skill_installed_matches_repo_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "matching-skill-home"
    skill_path = home / ".claude" / "skills" / ab.TOOL_NAME / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_bytes(REPO_SKILL.read_bytes())
    _install_happy_path_seams(monkeypatch, tmp_path, skill_home=home)

    row = next(c for c in ab.doctor()["checks"] if c["id"] == "skill_installed")
    assert row["status"] == "ok"


@pytest.mark.skipif(not REPO_SKILL.is_file(), reason="repo does not carry the installable SKILL.md copy")
def test_doctor_skill_installed_differs_from_repo_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "stale-skill-home"
    skill_path = home / ".claude" / "skills" / ab.TOOL_NAME / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_bytes(REPO_SKILL.read_bytes() + b"\n# locally edited\n")
    _install_happy_path_seams(monkeypatch, tmp_path, skill_home=home)

    row = next(c for c in ab.doctor()["checks"] if c["id"] == "skill_installed")
    assert row["status"] == "warn"
    assert "differs" in row["hint"]


# ---------------------------------------------------------------------------
# browser_found failure surfaces without crashing the rest of the checks


def test_doctor_browser_not_found_fails_that_row_but_doctor_still_completes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_happy_path_seams(monkeypatch, tmp_path)

    def missing(kind: str, explicit: str | None = None) -> Path:
        raise ab._tag(RuntimeError(f"{kind} was not found."), ab.ERR_BROWSER_NOT_FOUND, hint=ab._hint("browser_not_found"))

    monkeypatch.setattr(ab, "find_browser", missing)

    result = ab.doctor()

    assert result["ok"] is False
    assert "browser_found" in result["failed"]
    ids = [c["id"] for c in result["checks"]]
    assert "browser_version" not in ids  # never reached
    assert "sessions" in ids  # the rest of the checks still ran
