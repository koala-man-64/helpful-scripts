"""Profile-scoped bookmarks across CLI sessions and concurrent callers."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_browser_core import bookmarks, cli, common, evidence


@pytest.fixture
def run_cli(capsys):
    def run(*args, code=0):
        actual = cli.main(list(args))
        streams = capsys.readouterr()
        assert actual == code, (streams.out, streams.err)
        return json.loads(streams.err if code else streams.out)
    return run


def test_bookmark_lifecycle_without_a_browser(run_cli):
    url = "https://example.com/issues?view=open%20issues#/team/3"
    name = "Team issues"
    run_cli("--session", "first", "bookmark", "add", name, url)
    run_cli("bookmark", "add", name, url)
    listed = run_cli("bookmark", "list", "--session", "second")
    assert listed["bookmarks"] == [{"name": name, "url": common.redact_url(url)}]
    assert listed["untrusted"] == ["bookmarks"]
    assert run_cli("bookmark", "list", "--profile", "other")["bookmarks"] == []
    conflict = run_cli("bookmark", "add", name, "example.com/new", code=2)
    assert conflict["error"]["class"] == "validation"
    store = bookmarks.BookmarkStore.for_caller(None, "third")
    assert store.resolve(name) == url
    run_cli("bookmark", "add", name, "example.com/new", "--replace")
    assert store.resolve(name) == "https://example.com/new"
    assert run_cli("bookmark", "remove", name)["removed"] == name
    assert run_cli("bookmark", "open", name, code=2)["error"]["class"] == "not_found"


@pytest.mark.parametrize("url", [
    "https://user:private@example.com/", "https://example.com/?access_token=private",
    "https://example.com/reset-password/Private01234567890123456789", "https://example.com/#access_token=private",
    "https://example.com/#/auth?access_token=private", "https://example.com/?next=https://user:private@example.org/",
    "https://example.com/?next=https%3A%2F%2Fexample.org%2F%23access_token%3Dprivate",
    "https://example.com/?next=https%253A%252F%252Fuser%253Aprivate%2540example.org%252F",
    "https://example.com/#next=https://user:private@example.org/", "file:///private", "about:blank",
])
def test_unsafe_bookmark_is_rejected_before_writing(run_cli, url):
    result = run_cli("bookmark", "add", "unsafe", url, code=2)
    assert "private" not in json.dumps(result).lower()
    assert run_cli("bookmark", "list")["bookmarks"] == []


def test_open_rechecks_the_current_profile_policy_before_starting(run_cli):
    run_cli("bookmark", "add", "work", "https://example.com", "--profile", "work")
    (common._home() / "config.json").write_text('{"profiles":{"work":{"allowed_hosts":["elsewhere.test"]}}}')
    result = run_cli("bookmark", "open", "work", "--profile", "work", code=2)
    assert result["error"]["class"] == "guarded"
    assert "allowed_hosts" in result["error"]["message"]


def test_open_preserves_expectations_around_the_subcommand():
    args = cli._build_parser().parse_args(["--expect", "text:A", "bookmark", "--expect", "text:B",
                                           "open", "home", "--expect", "text:C"])
    assert args.expect == ["text:A", "text:B", "text:C"]


def test_open_targets_the_selected_tab_through_goto(run_cli, monkeypatch):
    url = "https://example.com/reports#weekly"
    run_cli("bookmark", "add", "report", url)
    monkeypatch.setattr(cli, "_session_for", lambda *args, **kwargs: (None, {}))
    sent = []
    monkeypatch.setattr(cli.core_session, "_request", lambda state, cmd, payload, timeout: sent.append((cmd, payload)) or {})
    run_cli("bookmark", "open", "report", "--tab", "t2", "--session", "reader")
    command, payload = sent[0]
    assert (command, payload["url"], payload["tab"], payload["_session_id"]) == ("goto", url, "t2", "reader")


def test_save_current_uses_the_daemons_absolute_profile_home(make_daemon, monkeypatch, tmp_path):
    daemon, _, _ = make_daemon(page_url="https://example.com/report")
    (daemon.paths.root / "config.json").write_text('{"allowed_hosts":["elsewhere.test"]}')
    # A relative AGENT_BROWSER_HOME resolves differently after the daemon starts in its profile folder.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_BROWSER_HOME", "other")
    with pytest.raises(ValueError, match="allowed_hosts"):
        daemon.dispatch("bookmark-current", {"bookmark_name": "report"}, 5)
    assert not (daemon.paths.dir / common.BOOKMARKS_FILE).exists()


def test_concurrent_adds_survive_and_do_not_use_evidence_quota():
    store = bookmarks.BookmarkStore.for_caller(None, "first")
    names = [f"page-{i}" for i in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda name: store.add(name, f"https://example.com/{name}"), names))
    assert [item["name"] for item in store.list()["bookmarks"]] == sorted(names)
    assert evidence.evidence_usage(store.paths.dir) == 0


def test_damaged_store_is_preserved(run_cli):
    store = bookmarks.BookmarkStore.for_caller(None, "first")
    store.file.parent.mkdir(parents=True)
    store.file.write_text("incomplete", encoding="utf-8")
    assert run_cli("bookmark", "add", "new", "https://example.com", code=1)["error"]["class"] == "io"
    assert store.file.read_text(encoding="utf-8") == "incomplete"


def test_bookmarks_survive_evidence_cleanup_until_profile_purge(make_daemon, run_cli):
    daemon, _, _ = make_daemon(config={"evidence_retention_days": 1})
    run_cli("bookmark", "add", "docs", "https://example.com/")
    daemon.dispatch("commit", {"operation": "journal", "clear": True}, 5)
    daemon.apply_retention()
    run_cli("clean", "--profile", "default")
    assert run_cli("bookmark", "list")["bookmarks"] == [{"name": "docs", "url": "https://example.com/"}]
    run_cli("clean", "--purge-profile", "default")
    assert run_cli("bookmark", "list")["bookmarks"] == []


@pytest.mark.parametrize("args", [
    ["bookmark", "list", "--tab", "2"],
    ["bookmark", "add", "page", "example.com", "--tab", "2"],
    ["commit", "bookmark", "add", "page", "example.com"],
])
def test_invalid_operation_does_not_edit_the_store(run_cli, args):
    assert run_cli(*args, code=2)["error"]["class"] == "usage"
    assert run_cli("bookmark", "list")["bookmarks"] == []
