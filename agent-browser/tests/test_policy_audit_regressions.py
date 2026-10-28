"""Regression tests for fail-closed host policy and internal diagnostic URLs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_browser_core import cli, client, common, daemon, mcp_server, policy, session


@pytest.mark.parametrize("hosts", [[], ["", "  "]])
def test_profile_config_rejects_empty_effective_host_allowlist(hosts: list[str]) -> None:
    with pytest.raises(RuntimeError) as exc:
        policy.profile_config({"allowed_hosts": hosts}, "default")
    assert exc.value.error_class == common.ERR_CONFIG


@pytest.mark.parametrize("hosts", [None, [], ["", "  "]])
def test_config_file_rejects_present_empty_host_allowlist(
    hosts: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"allowed_hosts": hosts}), encoding="utf-8")
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(home))

    with pytest.raises(RuntimeError) as exc:
        common._load_config()
    assert exc.value.error_class == common.ERR_CONFIG


@pytest.mark.parametrize("hosts", [None, [], ["", "  "]])
def test_direct_url_check_rejects_present_empty_host_allowlist(hosts: Any, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as exc:
        common._check_url("https://outside.example/path", common.ProfilePaths("default", tmp_path),
                          {"allowed_hosts": hosts})
    assert exc.value.error_class == common.ERR_CONFIG


@pytest.mark.parametrize("hosts", [None, [], ["", "  "]])
def test_direct_off_allowlist_check_rejects_present_empty_host_allowlist(hosts: Any) -> None:
    subject = SimpleNamespace(config={"allowed_hosts": hosts})
    page = SimpleNamespace(url="https://outside.example/path")

    with pytest.raises(RuntimeError) as exc:
        daemon.Daemon._off_allowlist(subject, page)
    assert exc.value.error_class == common.ERR_CONFIG


def test_user_profile_resolver_rejects_internal_doctor_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError) as explicit:
        session._resolve_profile("_doctor")
    assert explicit.value.error_class == common.ERR_VALIDATION

    monkeypatch.setenv("AGENT_BROWSER_PROFILE", "_doctor")
    with pytest.raises(ValueError) as from_environment:
        session._resolve_profile(None)
    assert from_environment.value.error_class == common.ERR_VALIDATION


def test_public_cli_and_mcp_entrypoints_reject_internal_doctor_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_start(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("reserved profile reached browser startup")

    def unexpected_require_running(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("reserved profile reached daemon lookup")

    monkeypatch.setattr(session, "start_session", unexpected_start)
    monkeypatch.setattr(session, "_require_running", unexpected_require_running)
    for argv in (["start", "--profile", "_doctor"],
                 ["goto", "https://allowed.example", "--profile", "_doctor"]):
        args = cli._build_parser().parse_args(list(argv))
        with pytest.raises(ValueError) as exc:
            args.func(args)
        assert exc.value.error_class == common.ERR_VALIDATION

    with pytest.raises(ValueError) as exc:
        mcp_server.browser_start("mcp-session", "_doctor")
    assert exc.value.error_class == common.ERR_VALIDATION
    with pytest.raises(ValueError) as exc:
        client.invoke("goto", {"url": "https://allowed.example"}, profile="_doctor")
    assert exc.value.error_class == common.ERR_VALIDATION


def test_live_remains_selectable_but_does_not_enable_data_urls() -> None:
    assert session._resolve_profile("_live") == "_live"


def test_reserved_profile_sets_are_split_and_documented() -> None:
    tests = Path(__file__).resolve().parent
    assert common.TEST_PROFILES == {"_live"} and common.INTERNAL_DATA_PROFILE not in common.TEST_PROFILES
    assert common.RESERVED_PROFILES == common.TEST_PROFILES | {common.INTERNAL_DATA_PROFILE}
    live_profile = re.search(r'^PROFILE = "([^"]+)"', (tests / "test_live.py").read_text(encoding="utf-8"), re.M)
    assert live_profile and live_profile.group(1) in common.TEST_PROFILES
    readme = " ".join((tests.parent / "README.md").read_text(encoding="utf-8").split())
    assert "`_live` is the live test suite's profile (`TEST_PROFILES`)" in readme


def test_data_scheme_is_not_enabled_by_public_live_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        common._check_url("data:text/html,<b>fixture</b>", common.ProfilePaths("_live", tmp_path), {})
    assert exc.value.error_class == common.ERR_GUARDED


def test_internal_doctor_probe_retains_data_fixture_support(tmp_path: Path) -> None:
    raw = "data:text/html,<title>doctor</title><button>ok</button>"
    assert common._check_url(raw, common.ProfilePaths("_doctor", tmp_path), {}) == raw


def test_internal_data_exception_does_not_hide_invalid_empty_allowlist(tmp_path: Path) -> None:
    raw = "data:text/html,<title>doctor</title>"
    with pytest.raises(RuntimeError) as exc:
        common._check_url(raw, common.ProfilePaths("_doctor", tmp_path), {"allowed_hosts": []})
    assert exc.value.error_class == common.ERR_CONFIG


def test_raw_goto_arguments_cannot_select_internal_data_profile(make_daemon: Any) -> None:
    browser, _ctx, _page = make_daemon(profile="default")
    with pytest.raises(ValueError) as exc:
        browser.dispatch("goto", {"url": "data:text/html,<b>blocked</b>",
                                   "profile": "_doctor", "_profile": "_doctor"}, 1)
    assert exc.value.error_class == common.ERR_GUARDED


def test_doctor_live_probe_keeps_internal_data_fixture_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(tmp_path / "home"))
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(session, "stop_session", lambda *args, **kwargs: {})
    monkeypatch.setattr(session, "start_session", lambda paths, ns, timeout: {"browser_version": "test", "launcher": "fake"})
    monkeypatch.setattr(session, "_require_running", lambda paths, verb: {"pid": 0})
    monkeypatch.setattr(common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(common, "_processes_using", lambda user_data: [])

    def request(_session: dict[str, Any], command: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls.append((command, args))
        if command == "goto":
            return {"snapshot": ['- button "ok" [ref=e1]', '- button "inner" [ref=f1e1]']}
        return {}

    monkeypatch.setattr(session, "_request", request)
    checks: list[dict[str, Any]] = []
    session._live_probe(checks)

    assert calls[0][0] == "goto" and calls[0][1]["url"].startswith("data:")
    assert calls[1] == ("click", {"target": "e1"})
    assert next(check for check in checks if check["id"] == "live_click")["status"] == "ok"


def test_journal_clear_needs_both_the_commit_verb_and_a_commit_profile(make_daemon: Any) -> None:
    acting, _ctx, _page = make_daemon(config={"permission": "act"})
    acting.journal.start("seed", "manual", "goto", {}, "http://x/")
    with pytest.raises(ValueError, match="commit profile") as exc:
        acting.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert exc.value.error_class == common.ERR_GUARDED
    assert "seed" in {row["id"] for row in acting.journal.rows("manual")}
    with pytest.raises(ValueError, match="commit journal --clear"):
        policy.authorize({"permission": "read"}, "journal", {"clear": True})
    with pytest.raises(ValueError, match="commit journal --clear"):
        policy.authorize({"permission": "commit"}, "journal", {"clear": True})
    assert policy.authorize({"permission": "commit"}, "journal", {"clear": True, "_commit": True}) is True
