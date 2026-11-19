"""Per-host rules: inside one profile, a `hosts` entry sets the permission, eval and uploads for pages on that host."""
from __future__ import annotations

import json
from typing import Any

import pytest

from agent_browser_core import common, policy
from conftest import FakeLocator
from test_api_extension import Response, request_context

PAGE = '- generic [ref=e1]:\n  - button "Save" [ref=e2]\n  - textbox "Notes" [ref=e3]\n'
READ_EXCEPT_APP = {"permission": "read", "hosts": {"app.example": {"permission": "act"}}}


def _refused(daemon: Any, command: str, args: dict[str, Any]) -> Exception:
    with pytest.raises(ValueError) as raised:
        daemon.dispatch(command, args, 5.0)
    assert raised.value.error_class == common.ERR_GUARDED
    return raised.value


# ---------------------------------------------------------------------------
# Which rule applies


def test_the_most_specific_matching_host_wins_and_other_hosts_keep_the_profile_policy() -> None:
    config = policy.profile_config({"permission": "read", "hosts": {
        "App.Example": {"permission": "act", "eval": False}, "admin.app.example": {"permission": "read"}}}, "default")
    assert sorted(config["hosts"]) == ["admin.app.example", "app.example"]  # names are stored in lower case

    def level(host: str) -> tuple[str, str | None]:
        effective = policy.for_host(config, host)
        return effective.get("permission", "commit"), effective.get("_host_rule")

    assert level("app.example") == ("act", "app.example")
    assert level("sub.app.example") == ("act", "app.example")             # a rule covers subdomains
    assert level("admin.app.example") == ("read", "admin.app.example")    # the longer name wins
    assert level("deep.admin.app.example") == ("read", "admin.app.example")
    assert level("APP.EXAMPLE.") == ("act", "app.example")                # case and a trailing dot do not dodge a rule
    assert level("notapp.example") == ("read", None)                      # a lookalike suffix is another host
    assert level("other.test") == ("read", None) and level("") == ("read", None)
    assert policy.for_host(config, "other.test") is config


@pytest.mark.parametrize("hosts, needle", [
    ({"https://app.example": {}}, "bare host name"),
    ({"app.example:8443": {}}, "bare host name"),
    ({"*.app.example": {}}, "bare host name"),
    ({"app.example/path": {}}, "bare host name"),
    ({"": {}}, "bare host name"),
    ({"app.example": {"permision": "read"}}, "permision"),
    ({"app.example": {"allowed_hosts": ["x"]}}, "allowed_hosts"),     # only permission, eval and uploads are per host
    ({"app.example": {"permission": "write"}}, "permission must be"),
    ({"app.example": {"eval": "no"}}, "eval must be a boolean"),
    ({"app.example": "read"}, "must be an object"),
    ({"app.example": {}, "APP.example": {}}, "listed twice"),
    (["app.example"], "must map host names"),
])
def test_a_malformed_host_rule_is_a_config_error(hosts: Any, needle: str) -> None:
    for config in ({"hosts": hosts}, {"profiles": {"other": {"hosts": hosts}}}):  # in the profile in use and in another
        with pytest.raises(RuntimeError) as raised:
            policy.profile_config(config, "default")
        assert raised.value.error_class == common.ERR_CONFIG and needle in str(raised.value), str(raised.value)


def test_a_profiles_hosts_replace_the_top_level_ones() -> None:
    config = {"hosts": {"app.example": {"permission": "read"}}, "profiles": {"work": {"hosts": {}}}}
    assert policy.profile_config(config, "default")["hosts"] == {"app.example": {"permission": "read"}}
    assert policy.profile_config(config, "work")["hosts"] == {}


# ---------------------------------------------------------------------------
# What the daemon enforces


def test_read_everywhere_except_one_app(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="https://app.example/record")

    clicked = daemon.dispatch("click", {"target": "e2"}, 5.0)
    assert clicked["ok"] is True and page.elements["e2"].calls == [("click", "left")]

    page.url = "https://elsewhere.test/page"  # the same tab after a navigation to a host without a rule
    refused = _refused(daemon, "click", {"target": "e2"})
    assert "observation only" in str(refused) and "hosts rule" not in str(refused)
    assert page.elements["e2"].calls == [("click", "left")]
    assert daemon.dispatch("snapshot", {}, 5.0)["url"] == "https://elsewhere.test/page"  # looking is still allowed


def test_a_host_rule_can_narrow_a_permissive_profile_and_says_so(make_daemon: Any) -> None:
    config = {"hosts": {"prod.example": {"permission": "read"}}}
    daemon, _ctx, page = make_daemon(config=config, snapshot_text=PAGE, page_url="https://console.prod.example/users")

    refused = _refused(daemon, "fill", {"target": "e3", "text": "x"})
    assert "observation only" in str(refused) and "hosts rule for prod.example" in str(refused)
    assert page.elements["e3"].calls == []
    refused = _refused(daemon, "eval", {"js": "1", "reason": "test"})
    assert "requires a commit profile" in str(refused) and "hosts rule for prod.example" in str(refused)

    page.url = "https://staging.example/users"
    assert daemon.dispatch("fill", {"target": "e3", "text": "x"}, 5.0)["ok"] is True


def test_eval_can_be_switched_per_host_in_both_directions(make_daemon: Any) -> None:
    off_on_one_host = {"hosts": {"bank.example": {"eval": False}}}
    daemon, _ctx, page = make_daemon(config=off_on_one_host, snapshot_text=PAGE, page_url="https://bank.example/")
    page.evaluate = lambda js: "evaluated"  # type: ignore[method-assign]
    assert "eval is disabled" in str(_refused(daemon, "eval", {"js": "1", "reason": "test"}))
    page.url = "https://tools.example/"
    assert daemon.dispatch("eval", {"js": "1", "reason": "test"}, 5.0)["value"] == "evaluated"

    on_for_one_host = {"eval": False, "hosts": {"tools.example": {"eval": True}}}
    daemon, _ctx, page = make_daemon(profile="other", config=on_for_one_host, snapshot_text=PAGE, page_url="https://tools.example/")
    page.evaluate = lambda js: "evaluated"  # type: ignore[method-assign]
    assert daemon.dispatch("eval", {"js": "1", "reason": "test"}, 5.0)["value"] == "evaluated"
    page.url = "https://bank.example/"
    assert "eval is disabled" in str(_refused(daemon, "eval", {"js": "1", "reason": "test"}))


def test_a_write_api_call_is_judged_by_the_pages_host(make_daemon: Any) -> None:
    config = {"permission": "read", "hosts": {"app.example": {"permission": "commit"}}}
    daemon, ctx, page = make_daemon(config=config, snapshot_text=PAGE, page_url="https://app.example/record")
    calls = request_context(ctx, Response(status=201, body=b'{"ok":true}'))

    sent = daemon.dispatch("commit", {"operation": "api", "url": "/api/items", "method": "POST"}, 5.0)
    assert sent["status"] == 201 and calls[0][1]["method"] == "POST"

    page.url = "https://elsewhere.test/record"
    refused = _refused(daemon, "commit", {"operation": "api", "url": "/api/items", "method": "POST"})
    assert "requires a commit profile" in str(refused) and len(calls) == 1


def test_uploads_can_be_refused_for_one_host(make_daemon: Any, tmp_path: Any) -> None:
    config = {"hosts": {"partner.example": {"uploads": False}}}
    daemon, _ctx, page = make_daemon(config=config, snapshot_text='- button "Attach" [ref=e2]\n', page_url="https://partner.example/")
    page.elements["e2"].is_file_input = True
    report = tmp_path / "report.txt"
    report.write_text("quarterly numbers", encoding="utf-8")
    args = {"target": "e2", "files": [str(report)], "_cwd": str(tmp_path)}

    assert "uploads" in str(_refused(daemon, "upload", args))
    assert page.elements["e2"].calls == []
    page.url = "https://intranet.example/"
    assert daemon.dispatch("upload", args, 5.0)["ok"] is True


def test_session_verbs_are_not_subject_to_the_current_pages_host_rule(make_daemon: Any) -> None:
    # Clearing evidence and reading state act on the session, not on the page that happens to be in front.
    config = {"hosts": {"prod.example": {"permission": "read"}}}
    daemon, _ctx, _page = make_daemon(config=config, snapshot_text=PAGE, page_url="https://prod.example/")

    cleared = daemon.dispatch("commit", {"operation": "journal", "clear": True}, 5.0)
    assert "cleared_commands" in cleared
    state = daemon.dispatch("state", {}, 5.0)
    assert state["permission"] == "commit" and state["host_rules"] == ["prod.example"]
    assert state["page_policy"] == {"host": "prod.example", "permission": "read", "eval": False, "uploads": True}


def test_state_shows_no_page_policy_when_no_rule_applies(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="https://elsewhere.test/")
    state = daemon.dispatch("state", {}, 5.0)
    assert state["host_rules"] == ["app.example"] and "page_policy" not in state

    plain, _ctx, _page = make_daemon(profile="plain", snapshot_text=PAGE)
    assert "host_rules" not in plain.dispatch("state", {}, 5.0)


def test_a_refusal_under_a_host_rule_is_journaled_like_any_other(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="https://elsewhere.test/")
    refused = _refused(daemon, "fill", {"target": "e3", "text": "private note"})
    row = daemon.journal.rows("manual", refused.details["command_id"])[0]
    assert row["status"] == "failed" and json.loads(row["outcome"])["error_class"] == common.ERR_GUARDED
    assert "private note" not in json.dumps(row)


# ---------------------------------------------------------------------------
# Pages that are not simply "a page on a host"

LOWERED = {"hosts": {"prod.example": {"permission": "read"}}}


def test_an_address_matches_only_itself_never_a_longer_one() -> None:
    assert common._host_allowed("127.0.0.1", ["127.0.0.1"]) and not common._host_allowed("127.0.0.1", ["0.0.1"])
    assert common._host_allowed("sub.example.com", ["example.com"]) and not common._host_allowed("::1", ["1"])
    config = policy.profile_config({"hosts": {"10.0.0.5": {"permission": "read"}}}, "default")
    assert policy.for_host(config, "10.0.0.5").get("_host_rule") == "10.0.0.5"
    assert policy.for_host(config, "110.0.0.5") is config
    for partial in ("0.5", "10.0.0", "999.1.1.1"):
        with pytest.raises(RuntimeError) as raised:
            policy.profile_config({"hosts": {partial: {}}}, "default")
        assert raised.value.error_class == common.ERR_CONFIG and "IPv4" in str(raised.value)


@pytest.mark.parametrize("url", ["about:blank", "data:text/html,<button>Delete</button>"])
def test_a_page_with_no_host_gets_the_tightest_rule_of_the_profile(make_daemon: Any, url: str) -> None:
    # A page on a lowered host can open a blank window and write a form into it. With no host of its own, that
    # window must not fall back to the profile's level: that would be a way around the rule.
    daemon, _ctx, page = make_daemon(config=LOWERED, snapshot_text=PAGE, page_url=url)

    refused = _refused(daemon, "click", {"target": "e2"})

    assert "observation only" in str(refused) and "no host of its own" in str(refused)
    assert page.elements["e2"].calls == []
    state = daemon.dispatch("state", {}, 5.0)
    assert state["page_policy"] == {"host": "", "permission": "read", "eval": False, "uploads": True}


def test_the_tightest_policy_takes_every_switch_any_rule_turns_off() -> None:
    config = policy.profile_config({"permission": "act", "hosts": {"a.example": {"permission": "commit", "eval": False},
                                                                    "b.example": {"uploads": False}}}, "default")
    tightest = policy.most_restrictive(config)
    assert (tightest["permission"], tightest["eval"], tightest["uploads"]) == ("act", False, False)
    assert policy.most_restrictive(policy.profile_config({"hosts": {"a.example": {"permission": "read"}}}, "default"))["permission"] == "read"


def test_a_blank_window_takes_the_host_of_the_page_that_opened_it(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="about:blank")
    opener_url = {"value": "https://app.example/record"}
    page.opener = lambda: type("Opener", (), {"url": opener_url["value"], "opener": staticmethod(lambda: None)})()  # type: ignore[attr-defined]

    assert daemon.dispatch("click", {"target": "e2"}, 5.0)["ok"] is True      # opened by the app the rule raises

    opener_url["value"] = "https://elsewhere.test/"
    assert "observation only" in str(_refused(daemon, "click", {"target": "e2"}))


def test_a_blob_page_is_judged_by_the_host_inside_its_address(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(config=LOWERED, snapshot_text=PAGE, page_url="blob:https://console.prod.example/1f0e")
    assert "hosts rule for prod.example" in str(_refused(daemon, "click", {"target": "e2"}))
    page.url = "blob:https://staging.example/1f0e"
    assert daemon.dispatch("click", {"target": "e2"}, 5.0)["ok"] is True


def test_a_rule_follows_the_tab_a_command_names(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="https://app.example/record")
    opened = daemon.dispatch("goto", {"url": "https://elsewhere.test/", "new_tab": True}, 5.0)
    other = daemon.current
    other.snapshot_text, other.elements = page.snapshot_text, {ref: element for ref, element in page.elements.items()}
    assert opened["tab_id"] == "t2" and daemon._tab_id(page) == "t1"

    assert "observation only" in str(_refused(daemon, "click", {"target": "e2", "tab": "t2"}))   # the current tab
    assert daemon.dispatch("click", {"target": "e2", "tab": "t1"}, 5.0)["ok"] is True             # the app's tab


def test_the_page_is_judged_again_just_before_the_action(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # A commit takes a screenshot before it acts, and a page can redirect itself at any time. If the page has moved
    # to a host with a tighter rule since the command was accepted, the action must not run there.
    config = {"permission": "read", "hosts": {"app.example": {"permission": "commit"}}}
    daemon, _ctx, page = make_daemon(config=config, snapshot_text=PAGE, page_url="https://app.example/record")
    daemon.dispatch("snapshot", {}, 5.0)
    real_capture = daemon._capture

    def capture_while_the_page_redirects(command_id: str, when: str, target: Any = None) -> str:
        shot = real_capture(command_id, when, target)
        if when == "before":
            page.url = "https://elsewhere.test/landing"
        return shot

    monkeypatch.setattr(daemon, "_capture", capture_while_the_page_redirects)

    refused = _refused(daemon, "commit", {"operation": "click", "target": "e2"})

    assert "requires a commit profile" in str(refused) and refused.details["executed"] is False
    assert page.elements["e2"].calls == []


def test_fill_form_is_judged_by_the_pages_host(make_daemon: Any) -> None:
    fields = [{"target": "e3", "operation": "fill", "value": "Investigating"}]
    daemon, _ctx, page = make_daemon(config=READ_EXCEPT_APP, snapshot_text=PAGE, page_url="https://elsewhere.test/")
    page.elements["e3"].tag = "INPUT"
    page.elements["e3"].custom_evaluators = {}

    assert "observation only" in str(_refused(daemon, "fill-form", {"fields": fields}))
    assert page.elements["e3"].calls == []
