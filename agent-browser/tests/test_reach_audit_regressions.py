"""check URL stays on loopback, table finds a grid, and a recipe preview has no side effects."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from agent_browser_core import common as core_common, reach
from conftest import FakeCDPSession, FakeFrame, FakePage, FakePlaywrightError


# -- check URL -------------------------------------------------------------------------------

class _Route:
    """Stand-in for a Playwright Route as the check page's request handler sees it."""

    def __init__(self, url: str, navigation: bool = False, main_frame: bool = True) -> None:
        frame = SimpleNamespace(parent_frame=None if main_frame else object())
        self.request = SimpleNamespace(url=url, is_navigation_request=lambda: navigation, frame=frame)
        self.outcome: tuple[str, ...] | None = None

    def abort(self, error_code: str = "failed") -> None:
        self.outcome = ("abort", error_code)

    def continue_(self) -> None:
        self.outcome = ("continue",)


class _SocketRoute:
    """Stand-in for a Playwright WebSocketRoute."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.outcome: str | None = None

    def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.outcome = "closed"

    def connect_to_server(self) -> None:
        self.outcome = "connected"


LAYOUT = {"horizontal_overflow": False, "unlabeled_controls": []}


def _check_page(ctx: Any, monkeypatch: pytest.MonkeyPatch, on_goto: Callable[[FakePage], None]) -> FakePage:
    """The temporary page `check URL` opens; its goto lands on the URL, then runs on_goto (routes, popups, errors)."""
    temp = FakePage(ctx, url="about:blank")
    temp.custom_evaluators[reach.DIAGNOSE_LAYOUT_JS] = LAYOUT

    def goto(url: str, wait_until: str = "load", timeout: float | None = None) -> Any:
        temp.goto_calls.append(url)
        temp.url = url
        on_goto(temp)
        return SimpleNamespace(status=200)

    def new_page() -> FakePage:
        ctx.pages.append(temp)
        ctx.emit("page", temp)  # Playwright announces every new page of the context
        return temp

    temp.goto = goto
    monkeypatch.setattr(ctx, "new_page", new_page)
    return temp


def _request_handler(page: FakePage) -> Callable[[Any], None]:
    assert [pattern for pattern, _handler in page.routes] == ["**/*"]
    return page.routes[0][1]


def _paused(session: FakeCDPSession, url: str, request_id: str, *, resource: str = "Document", frame: str = "main-frame",
            redirected: bool = False) -> tuple[str, dict[str, Any]]:
    """Deliver one Fetch.requestPaused event, as Chromium does for every hop, and return the session's answer."""
    event = {"requestId": request_id, "request": {"url": url}, "resourceType": resource, "frameId": frame}
    if redirected:
        event["redirectedRequestId"] = request_id + "-from"
    before = len(session.sent)
    session.emit("Fetch.requestPaused", event)
    assert len(session.sent) == before + 1
    return session.sent[-1]


def test_loopback_fetch_continues_loopback_and_fails_every_other_hop() -> None:
    session = FakeCDPSession(page=None)  # type: ignore[arg-type]
    blocked: dict[str, Any] = {"hosts": [], "navigation": False}
    session.on("Fetch.requestPaused", reach._loopback_fetch(session, blocked, "main-frame"))

    assert _paused(session, "http://127.0.0.1:3000/login", "r1") == ("Fetch.continueRequest", {"requestId": "r1"})
    assert _paused(session, "http://[::1]:3000/img", "r2", resource="Image") == ("Fetch.continueRequest", {"requestId": "r2"})
    # A subresource's redirect hop and a subframe navigation are refused without failing the page load.
    refused = ("Fetch.failRequest", {"requestId": "r3", "errorReason": "BlockedByClient"})
    assert _paused(session, "https://cdn.example/x.png", "r3", resource="Image", redirected=True) == refused
    _paused(session, "https://frame.example/", "r4", frame="child-frame", redirected=True)
    assert blocked == {"hosts": ["cdn.example", "frame.example"], "navigation": False}
    # The main navigation's redirect hop off loopback is what makes check fail as guarded.
    _paused(session, "https://login.example/authorize", "r5", redirected=True)
    assert blocked["navigation"] is True and blocked["hosts"][-1] == "login.example"


def test_check_guards_every_hop_before_navigating_and_releases_the_session_after_closing(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()
    order: list[str] = []

    def load(temp: FakePage) -> None:
        session = ctx.cdp_sessions[-1]
        assert session.page is temp and ("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}) in session.sent
        assert temp.routes and session.detached is False
        order.append("goto")

    temp = _check_page(ctx, monkeypatch, load)
    real_close = temp.close
    monkeypatch.setattr(temp, "close", lambda run_before_unload=False: (order.append("close"), real_close()))
    daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)

    assert order == ["goto", "close"] and ctx.cdp_sessions[-1].detached is True


def test_loopback_route_continues_only_loopback_http_and_records_every_other_host() -> None:
    blocked: dict[str, Any] = {"hosts": [], "navigation": False}
    handle = reach._loopback_route(blocked)
    for url in ("http://127.0.0.1:3000/x", "http://localhost/", "http://[::1]:8080/", "https://LOCALHOST/a.js"):
        route = _Route(url)
        handle(route)
        assert route.outcome == ("continue",), url
    for url in ("https://cdn.example/a.js", "https://cdn.example/b.css", "http://127.0.0.2/", "http://localhost.evil.example/",
                "ftp://localhost/x", "http://[bad/"):
        route = _Route(url)
        handle(route)
        assert route.outcome == ("abort", "blockedbyclient"), url
    assert blocked == {"hosts": ["cdn.example", "127.0.0.2", "localhost.evil.example", "localhost", "http:"], "navigation": False}

    # An iframe's navigation does not fail the page load; only a top-level one counts.
    handle(_Route("http://frame.example/", navigation=True, main_frame=False))
    assert blocked["navigation"] is False
    handle(_Route("http://evil.example/", navigation=True))
    assert blocked["navigation"] is True and blocked["hosts"][-2:] == ["frame.example", "evil.example"]


def test_loopback_route_caps_and_sanitizes_recorded_hosts() -> None:
    blocked: dict[str, Any] = {"hosts": [], "navigation": False}
    handle = reach._loopback_route(blocked)
    for index in range(reach.BLOCKED_HOSTS_CAP + 5):
        handle(_Route(f"https://h{index}.example/"))
    handle(_Route("http://evil.example/", navigation=True))
    assert len(blocked["hosts"]) == reach.BLOCKED_HOSTS_CAP and blocked["navigation"] is True


def test_check_blocks_a_non_loopback_subresource_and_reports_it_untrusted(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()
    seen: list[_Route] = []

    def load(temp: FakePage) -> None:
        handle = _request_handler(temp)
        for url in ("http://127.0.0.1:3000/app.js", "https://cdn.example/lib.js", "https://cdn.example/font.woff2"):
            seen.append(_Route(url))
            handle(seen[-1])

    temp = _check_page(ctx, monkeypatch, load)
    result = daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)

    assert [route.outcome for route in seen] == [("continue",), ("abort", "blockedbyclient"), ("abort", "blockedbyclient")]
    assert result["blocked_external"] == ["cdn.example"] and "blocked_external" in result["untrusted"]
    assert result["status"] == 200 and temp.viewport == {"width": 390, "height": 844}
    assert temp.closed and daemon.current is page


def test_check_reports_an_empty_block_list_for_a_self_contained_page(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()
    _check_page(ctx, monkeypatch, lambda temp: _request_handler(temp)(_Route("http://localhost:3000/app.js")))
    result = daemon.dispatch("diagnose", {"url": "http://localhost:3000/"}, 5)
    assert result["blocked_external"] == [] and "blocked_external" in result["untrusted"]


def test_check_redirect_off_loopback_is_guarded_and_the_page_closed(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()

    def redirect(temp: FakePage) -> None:
        # Playwright routes only the first hop; the DevTools session sees the 302's next hop.
        first = _Route("http://127.0.0.1:3000/", navigation=True)
        _request_handler(temp)(first)
        session = ctx.cdp_sessions[-1]
        assert _paused(session, "http://127.0.0.1:3000/", "r1")[0] == "Fetch.continueRequest"
        assert _paused(session, "https://login.example/authorize?client_id=x", "r1", redirected=True)[0] == "Fetch.failRequest"
        raise FakePlaywrightError("net::ERR_BLOCKED_BY_CLIENT at http://127.0.0.1:3000/")

    temp = _check_page(ctx, monkeypatch, redirect)
    with pytest.raises(ValueError) as raised:
        daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)

    assert raised.value.error_class == core_common.ERR_GUARDED
    assert raised.value.details["blocked_external"] == ["login.example"]
    assert temp.closed and daemon.current is page and daemon.sessions["manual"]["current"] is page


def test_check_reports_a_blocked_subresource_redirect_and_still_succeeds(make_daemon, monkeypatch) -> None:
    daemon, ctx, _page = make_daemon()

    def load(temp: FakePage) -> None:
        session = ctx.cdp_sessions[-1]
        _paused(session, "http://localhost:3000/", "r1")
        _paused(session, "http://localhost:3000/avatar", "r2", resource="Image")
        _paused(session, "https://avatars.example/u/1.png", "r2", resource="Image", redirected=True)

    _check_page(ctx, monkeypatch, load)
    result = daemon.dispatch("diagnose", {"url": "http://localhost:3000/"}, 5)
    assert result["blocked_external"] == ["avatars.example"] and result["status"] == 200


def test_check_rejects_a_final_url_off_loopback(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()

    def wander(temp: FakePage) -> None:
        temp.url = "http://evil.example/"

    temp = _check_page(ctx, monkeypatch, wander)
    with pytest.raises(ValueError) as raised:
        daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)
    assert raised.value.error_class == core_common.ERR_GUARDED and temp.closed


def test_check_closes_popups_their_popups_and_external_websockets(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()
    popup, grandchild = FakePage(ctx, url="http://127.0.0.1:3000/child"), FakePage(ctx, url="http://127.0.0.1:3000/grandchild")
    sockets = [_SocketRoute("ws://127.0.0.1:3000/hmr"), _SocketRoute("wss://socket.example/feed")]

    def load(temp: FakePage) -> None:
        assert [pattern for pattern, _handler in temp.ws_routes] == ["**/*"]
        for socket in sockets:
            temp.ws_routes[0][1](socket)
        # Closing a socket inside its route handler hangs Playwright's sync API: the outside one is only held here.
        assert [socket.outcome for socket in sockets] == ["connected", None]
        for opener, opened in ((temp, popup), (popup, grandchild)):
            ctx.pages.append(opened)
            ctx.emit("page", opened)
            opener.emit("popup", opened)

    temp = _check_page(ctx, monkeypatch, load)
    result = daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)

    assert popup.closed and grandchild.closed and result["popups_closed"] == 2
    assert [socket.outcome for socket in sockets] == ["connected", "closed"]
    assert result["blocked_external"] == ["socket.example"]
    assert temp.closed and daemon.current is page and daemon.pages == [page]


def test_check_still_runs_where_playwright_cannot_route_websockets(make_daemon, monkeypatch) -> None:
    daemon, ctx, _page = make_daemon()
    temp = _check_page(ctx, monkeypatch, lambda temp: None)
    temp.route_web_socket = None  # an older Playwright without page.route_web_socket
    assert daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)["blocked_external"] == []


def test_check_classifies_connection_refused_instead_of_crashing(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()

    def refused(temp: FakePage) -> None:
        raise FakePlaywrightError("Page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:3000/\nCall log:\n  - navigating")

    temp = _check_page(ctx, monkeypatch, refused)
    with pytest.raises(ValueError) as raised:
        daemon.dispatch("diagnose", {"url": "http://127.0.0.1:3000/"}, 5)

    assert raised.value.error_class == core_common.ERR_ACTION_FAILED
    assert "ERR_CONNECTION_REFUSED" in str(raised.value) and "blocked_external" not in raised.value.details
    assert temp.closed and daemon.current is page


# -- table -----------------------------------------------------------------------------------

class _Cell:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self) -> str:
        return self.text

    def is_visible(self) -> bool:
        return True


class _Row:
    def __init__(self, ref: str, cells: list[str]) -> None:
        self.ref = ref
        self.cells = cells
        self.selectors: list[str] = []

    def is_visible(self) -> bool:
        return True

    def get_by_role(self, role: str) -> Any:
        return SimpleNamespace(count=lambda: 0)

    def locator(self, selector: str) -> Any:
        self.selectors.append(selector)
        return SimpleNamespace(all=lambda: [_Cell(text) for text in self.cells])

    def aria_snapshot(self, mode: str = "ai") -> str:
        return f'- row "{" ".join(self.cells)}" [ref={self.ref}]'


class _Table:
    """A visible table or grid as `resolve` returns it: what h_table reads from it."""

    def __init__(self, page: FakePage, header: str, row: _Row) -> None:
        self.page, self.header, self.row = page, header, row

    def is_visible(self) -> bool:
        return True

    def evaluate(self, js: str) -> bool:
        return False  # it is a table or grid

    def get_by_role(self, role: str) -> Any:
        return SimpleNamespace(all=lambda: [_Cell(self.header)] if role == "columnheader" else [self.row])

    def element_handle(self) -> Any:
        return SimpleNamespace(owner_frame=lambda: self.page.main_frame)

    def aria_snapshot(self, mode: str = "ai") -> str:
        return "- table"


def _roles(monkeypatch: pytest.MonkeyPatch, by_role: dict[str, list[Any]]) -> None:
    class Matches:
        def __init__(self, items: list[Any]) -> None:
            self.items = items

        def count(self) -> int:
            return len(self.items)

        def nth(self, index: int) -> Any:
            return self.items[index]

    monkeypatch.setattr(FakeFrame, "get_by_role", lambda frame, role, **_kw: Matches(by_role.get(role, [])), raising=False)


def test_table_without_a_ref_finds_a_grid_only_page(make_daemon, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon()
    row = _Row("e9", ["Review", "Rudy"])
    _roles(monkeypatch, {"grid": [_Table(page, "Owner", row)]})

    result = daemon.dispatch("table", {}, 5)

    assert result["columns"] == ["Owner"] and result["rows"] == [{"ref": "e9", "cells": ["Review", "Rudy"]}]
    assert row.selectors == [reach.CELL_SELECTOR] and "[role=gridcell]" in reach.CELL_SELECTOR


def test_table_without_a_ref_prefers_the_table_over_a_grid(make_daemon, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon()
    _roles(monkeypatch, {"table": [_Table(page, "Owner", _Row("e5", ["Queue"]))],
                         "grid": [_Table(page, "Mon", _Row("e7", ["1"])), _Table(page, "Tue", _Row("e8", ["2"]))]})

    result = daemon.dispatch("table", {}, 5)

    assert result["columns"] == ["Owner"] and result["rows"][0]["cells"] == ["Queue"]


@pytest.mark.parametrize("grids,error_class", [(0, core_common.ERR_NOT_FOUND), (2, core_common.ERR_AMBIGUOUS)])
def test_table_without_a_ref_needs_one_visible_table_or_grid(make_daemon, monkeypatch, grids, error_class) -> None:
    daemon, _ctx, page = make_daemon()
    _roles(monkeypatch, {"grid": [_Table(page, f"Day {index}", _Row(f"e{index + 7}", [str(index)])) for index in range(grids)]})

    with pytest.raises(ValueError) as raised:
        daemon.dispatch("table", {}, 5)

    assert raised.value.error_class == error_class


# -- recipe preview --------------------------------------------------------------------------

RECORD = "http://x/incident.do?sys_id=" + "a" * 32


def _recipe_page(make_daemon, monkeypatch, *, frame_url: str | None = RECORD, fields: dict[str, Any] | None = None,
                 displayed: bool = False) -> tuple[Any, FakePage]:
    """A classic record form inside gsft_main, or no form at all when frame_url is None."""
    daemon, _ctx, page = make_daemon(page_url="http://x/nav_to.do")
    if frame_url is not None:
        form = FakeFrame(name="gsft_main", url=frame_url)
        page.frames = [page.main_frame, form]
        note = SimpleNamespace(is_visible=lambda: True, evaluate=lambda js: True)  # visible, outside any input
        form.get_by_text = lambda text, exact=False: SimpleNamespace(all=lambda: [note] if displayed else [])
    values = {"Number": "INC0012345", "Work notes": "", **(fields or {})}

    def resolve(_page: Any, target: Any) -> Any:
        label = target.get("label") if isinstance(target, dict) else None
        if isinstance(values.get(label), Exception):
            raise values[label]
        return SimpleNamespace(input_value=lambda: values.get(label, ""))

    monkeypatch.setattr(daemon, "resolve", resolve)
    return daemon, page


PREVIEW = {"recipe": "sn/add-work-note", "number": "INC0012345", "text": "note"}
MISSING = core_common._tag(ValueError("Semantic target matched 0 visible elements."), core_common.ERR_NOT_FOUND)


@pytest.mark.parametrize("setup,reason", [
    ({"frame_url": None}, "classic gsft_main record form"),
    ({"frame_url": "http://x/incident.do?sys_id=new"}, "stable classic record URL"),
    ({"fields": {"Work notes": MISSING}}, "controls are missing or ambiguous"),
    ({"fields": {"Work notes": "an unsaved draft"}}, "already contains an unsaved draft"),
])
def test_recipe_preview_opens_no_handoff_and_focuses_nothing(make_daemon, monkeypatch, setup, reason) -> None:
    from agent_browser_core import session
    daemon, _page = _recipe_page(make_daemon, monkeypatch, **setup)
    focused: list[Any] = []
    monkeypatch.setattr(session, "focus_window", lambda paths: focused.append(paths) or {"focused": True})

    result = daemon.dispatch("run", PREVIEW, 5)

    assert result["blocked"] is True and reason in result["blocked_reason"]
    assert result["requires_commit"] is False and result["preview"] == {"number": "INC0012345", "work_note": "note"}
    assert "handoff_id" not in result and "pending" not in result
    assert daemon.journal.handoffs("manual") == [] and focused == []
    assert daemon.journal.rows("manual", result["command_id"])[0]["status"] == "completed"

    committed = daemon.dispatch("commit", {"operation": "run", **PREVIEW}, 5)

    assert committed["status"] == "needs_human" and committed["pending"] is True
    handoffs = daemon.journal.handoffs("manual")
    assert [h["id"] for h in handoffs] == [committed["handoff_id"]] and reason in handoffs[0]["reason"]
    assert len(focused) == 1


def test_recipe_preview_rejects_an_identical_displayed_note(make_daemon, monkeypatch) -> None:
    daemon, _page = _recipe_page(make_daemon, monkeypatch, displayed=True)

    with pytest.raises(ValueError, match="identical note") as raised:
        daemon.dispatch("run", PREVIEW, 5)

    assert raised.value.error_class == core_common.ERR_GUARDED


def test_recipe_preview_returns_the_preview_when_it_can_run(make_daemon, monkeypatch) -> None:
    daemon, _page = _recipe_page(make_daemon, monkeypatch)

    result = daemon.dispatch("run", PREVIEW, 5)

    assert result["requires_commit"] is True and result["preview"] == {"number": "INC0012345", "work_note": "note"}
    assert "blocked" not in result and daemon.journal.handoffs("manual") == []
