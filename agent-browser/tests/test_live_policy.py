"""Live checks of config.json policy: what a real browser does under a host allowlist and a permission level.

Each test starts its own daemon in a private home, because the daemon reads config.json once, when it starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_browser_core import common
from test_live_expansion import site
import live_support


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run the live policy checks"),
]

TOOL = Path(__file__).resolve().parents[1] / "agent_browser.py"
PROFILE = "policy_live"


def _env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_BROWSER_")}
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update({"AGENT_BROWSER_HOME": str(home), "AGENT_BROWSER_HEADLESS": "1", "PYTHONUTF8": "1"})
    return env


class PolicyBrowser:
    """One private home: write its config.json, then drive the CLI under it."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def configure(self, config: dict[str, Any]) -> None:
        (self.home / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def __call__(self, *args: str, timeout: float = 120) -> tuple[int, dict[str, Any], dict[str, Any]]:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args, "--profile", PROFILE], capture_output=True,
                              text=True, encoding="utf-8", env=_env(self.home), timeout=timeout)
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        err = json.loads(proc.stderr).get("error", {}) if proc.stderr.strip().startswith("{") else {"raw": proc.stderr}
        return proc.returncode, out, err

    def start(self, config: dict[str, Any]) -> None:
        self.configure(config)
        code, out, err = self("start", "--headless")
        assert code == 0 and out.get("status") == "started", (out, err)

    def snapshot_at(self, url: str, attempts: int = 40) -> dict[str, Any]:
        """The snapshot once the tab shows `url`; a page that navigates itself takes a moment to arrive."""
        reply: dict[str, Any] = {}
        for _ in range(attempts):
            code, reply, _err = self("snapshot")
            if code == 0 and reply.get("url") == url:
                return reply
            time.sleep(0.25)
        raise AssertionError(f"the tab never showed {url}; last reply url {reply.get('url')!r}")


@pytest.fixture
def policy_browser(request: pytest.FixtureRequest):
    home = Path(tempfile.mkdtemp(prefix="abp", dir=os.getenv("TEMP") or None))
    failures = live_support.FailureWatch(request)
    try:
        yield PolicyBrowser(home)
    finally:
        subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "stop", "--all", "--force", "--profile", PROFILE],
                       env=_env(home), capture_output=True, timeout=60, check=False)
        blocking = live_support.finish_home(home, failures, request.node.name)
        assert not blocking, f"browser processes survived the policy test teardown: {blocking}"


def _ref(reply: dict[str, Any], pattern: str) -> str:
    for line in reply.get("snapshot") or []:
        if re.search(pattern, line):
            found = re.search(r"\[ref=([^\]]+)\]", line)
            assert found, line
            return found.group(1)
    raise AssertionError(f"no line matches {pattern!r} in {reply.get('snapshot')}")


def test_goto_cannot_reach_a_host_outside_allowed_hosts(policy_browser: PolicyBrowser, site) -> None:
    cli, port = policy_browser, urlsplit(site.url).port
    cli.start({"allowed_hosts": ["127.0.0.1"]})

    code, opened, err = cli("goto", f"{site.url}/home.html")
    assert code == 0 and opened["url"] == f"{site.url}/home.html" and not opened.get("off_allowlist"), (opened, err)

    # localhost is the same fixture server under a host name that is not on the list.
    code, _out, err = cli("goto", f"http://localhost:{port}/form.html")
    assert code == 2 and err["class"] == "guarded" and "allowed_hosts" in err["message"], err

    # urlsplit reads the host after the @, which is the allowed 127.0.0.1. A browser reads the backslash as a slash,
    # so it would open localhost: before the fix this request was sent and the page loaded.
    address = f"http://localhost:{port}" + chr(92) + "@" + "127.0.0.1/form.html"
    code, _out, err = cli("goto", address)
    assert code == 2 and err["class"] == "guarded", err
    assert err["hint"] == common._hint("guarded_url")

    code, tabs, err = cli("tabs")
    assert code == 0 and [tab["url"] for tab in tabs["tabs"]] == [f"{site.url}/home.html"], (tabs, err)


def test_a_page_that_leaves_the_allowlist_is_read_only(policy_browser: PolicyBrowser, site) -> None:
    cli, port = policy_browser, urlsplit(site.url).port
    cli.start({"allowed_hosts": ["127.0.0.1"]})
    code, _opened, err = cli("goto", f"{site.url}/home.html")
    assert code == 0, err

    # The page navigates itself to another host, as a link or a redirect would.
    outside = f"http://localhost:{port}/form.html"
    code, _moved, err = cli("eval", f"location.href = '{outside}'")
    assert code == 0, err
    arrived = cli.snapshot_at(outside)
    assert arrived["off_allowlist"] is True

    code, text, err = cli("text")
    assert code == 0 and "Incident form" in text["text"], err
    code, _out, err = cli("fill", "--label", "Short description", "not typed")
    assert code == 2 and err["class"] == "guarded" and "outside allowed_hosts" in err["message"], err


def test_a_read_profile_can_look_but_not_act(policy_browser: PolicyBrowser, site) -> None:
    cli = policy_browser
    cli.start({"profiles": {PROFILE: {"permission": "read", "eval": False}}})

    code, opened, err = cli("goto", f"{site.url}/home.html")
    assert code == 0 and opened["title"] == "Home", (opened, err)
    link = _ref(opened, r'link "Form"')

    code, _out, err = cli("click", link)
    assert code == 2 and err["class"] == "guarded" and "observation only" in err["message"], err
    code, _out, err = cli("eval", "document.title")
    assert code == 2 and err["class"] == "guarded", err

    code, state, err = cli("state")
    assert code == 0 and state["permission"] == "read" and state["eval"] is False, (state, err)
    code, after, err = cli("snapshot")
    assert code == 0 and after["url"] == f"{site.url}/home.html", (after, err)  # the refused click did not navigate


def test_a_misspelled_policy_key_stops_start_before_a_browser_opens(policy_browser: PolicyBrowser) -> None:
    cli = policy_browser
    cli.configure({"profiles": {PROFILE: {"permision": "read"}}})

    code, out, err = cli("start", "--headless")

    assert code == 1 and out == {} and err["class"] == "config" and "permision" in err["message"], (out, err)
    code, status, _err = cli("status")
    assert code == 0 and status["running"] is False
