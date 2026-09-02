"""Shared offline fixtures for the agent-browser test suite.

Nothing here starts a browser, imports playwright, opens a socket, or touches the real
AGENT_BROWSER_HOME. The `_isolated_env` fixture (autouse) points AGENT_BROWSER_HOME at a
fresh tmp_path for every test and strips every AGENT_BROWSER_* / MSYSTEM / EXEPATH variable
from the ambient shell first, so a Git Bash environment (which sets MSYSTEM) or a developer's
real installed profile can never leak into a test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_browser as ab  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "snapshots"

@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("AGENT_BROWSER_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MSYSTEM", raising=False)
    monkeypatch.delenv("EXEPATH", raising=False)
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(tmp_path / "home"))
    # The process-listing seam shells out to PowerShell CIM on Windows; offline tests never do that.
    # A test that needs specific processes re-patches it (a later monkeypatch wins).
    monkeypatch.setattr(ab, "_processes_using", lambda needle: [])
    ab._ARGV_NOTES.clear()
    ab.VERBOSE = False
    yield
    ab._ARGV_NOTES.clear()

def read_fixture(name: str) -> str:
    """Read a captured real-Playwright ai-mode snapshot (text mode: CRLF -> LF)."""
    return (FIXTURES / name).read_text(encoding="utf-8")

@pytest.fixture
def outer_text() -> str:
    return read_fixture("outer.txt")

@pytest.fixture
def data_page_text() -> str:
    return read_fixture("data_page.txt")

def error_envelope(err: str) -> dict[str, Any]:
    """Parse the stderr JSON `_emit_error` writes and assert its shape."""
    payload = json.loads(err)
    assert set(payload) == {"error"}
    assert set(payload["error"]) >= {"class", "http_status", "message", "hint"}
    return payload["error"]

@pytest.fixture
def capture_args(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], list[Any]]:
    """Patch a `_cmd_<verb>` handler to record its argparse.Namespace instead of running it."""
    calls: list[Any] = []

    def patch(verb: str) -> list[Any]:
        def fake(args: Any) -> dict[str, Any]:
            calls.append(args)
            return {"ok": True}

        monkeypatch.setattr(ab, f"_cmd_{verb}", fake)
        return calls

    return patch

# ===========================================================================
# Fakes for the Daemon / daemon-server layer (tests/test_daemon.py, tests/test_actions.py)
#
# These stand in for Playwright's sync API: FakePlaywright -> FakeChromium ->
# FakeContext -> FakePage -> FakeFrame, and FakeLocator/FakeElementHandle for
# `page.locator("aria-ref=...")`. Nothing here touches a real browser, socket, or
# the filesystem beyond what Daemon.start_browser() itself writes under tmp_path.
# ===========================================================================

import dataclasses
from types import SimpleNamespace


class FakePlaywrightError(Exception):
    """Stand-in for playwright.sync_api.Error."""


class FakePlaywrightTimeoutError(FakePlaywrightError):
    """Stand-in for playwright.sync_api.TimeoutError."""


PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (4).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"\x00" * 13


class _Emitter:
    """Minimal `.on(event, handler)` / `.emit(event, *args)` pair shared by fake Context/Page."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {}

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)


class FakeDialog:
    """Stand-in for a Playwright `dialog` event object (confirm/alert/beforeunload)."""

    def __init__(self, kind: str, message: str = "") -> None:
        self.type = kind
        self.message = message
        self.accepted_with: str | None = None
        self.dismissed = False

    def accept(self, prompt_text: str = "") -> None:
        self.accepted_with = prompt_text

    def dismiss(self) -> None:
        self.dismissed = True


class FakeDownload:
    """Stand-in for a Playwright `download` event object."""

    def __init__(self, suggested_filename: str = "download.bin") -> None:
        self.suggested_filename = suggested_filename
        self.saved_to: str | None = None

    def save_as(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake-download-bytes")
        self.saved_to = path


@dataclasses.dataclass
class FakeElement:
    """A resolvable element: what `page.locator("aria-ref=<ref>")` returns behind a FakeLocator."""

    ref: str
    role: str = "generic"
    name: str = ""
    tag: str = "DIV"
    value: Any = ""
    checked: bool = False
    exists: bool = True
    heal_on_next_snapshot: bool = False
    secret_info: dict[str, Any] | None = None
    options: list[Any] | None = None
    is_file_input: bool = False
    frame: "FakeFrame | None" = None
    frame_label: str = ""
    visible: bool = True
    custom_evaluators: dict[str, Any] = dataclasses.field(default_factory=dict)
    click_side_effect: Callable[[], None] | None = None
    raise_on: dict[str, Exception] = dataclasses.field(default_factory=dict)
    calls: list[tuple[Any, ...]] = dataclasses.field(default_factory=list)


class FakeElementHandle:
    def __init__(self, element: FakeElement | None) -> None:
        self.element = element

    def evaluate(self, js: str) -> Any:
        if self.element is None:
            return ""
        if js == ab.FRAME_LABEL_JS:
            return self.element.frame_label
        raise AssertionError(f"FakeElementHandle.evaluate: no fake for js={js[:80]!r}")

    def content_frame(self) -> "FakeFrame | None":
        return self.element.frame if self.element else None


class FakeLocator:
    def __init__(self, page: "FakePage", element: FakeElement | None, count_override: int | None = None) -> None:
        self.page = page
        self.element = element
        self._count_override = count_override

    def _raise_if_configured(self, action: str) -> None:
        if self.element and action in self.element.raise_on:
            raise self.element.raise_on[action]

    def count(self) -> int:
        self._raise_if_configured("count")
        if self._count_override is not None:
            return self._count_override
        if self.element is None:
            return 0
        return 1 if self.element.exists else 0

    @property
    def first(self) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return bool(self.element and self.element.visible)

    def click(self, timeout: float | None = None, button: str = "left") -> None:
        self._raise_if_configured("click")
        self.element.calls.append(("click", button))
        if self.element.click_side_effect:
            self.element.click_side_effect()

    def dblclick(self, timeout: float | None = None) -> None:
        self._raise_if_configured("click")
        self.element.calls.append(("dblclick",))
        if self.element.click_side_effect:
            self.element.click_side_effect()

    def fill(self, text: str, timeout: float | None = None) -> None:
        self._raise_if_configured("fill")
        self.element.value = text
        self.element.calls.append(("fill", text))

    def press(self, key: str, timeout: float | None = None) -> None:
        self._raise_if_configured("press")
        self.element.calls.append(("press", key))

    def press_sequentially(self, text: str, delay: int = 0, timeout: float | None = None) -> None:
        self._raise_if_configured("press_sequentially")
        self.element.value = (self.element.value or "") + text
        self.element.calls.append(("press_sequentially", text))

    def select_option(self, value: list[str] | None = None, timeout: float | None = None) -> None:
        self._raise_if_configured("select_option")
        self.element.value = value
        self.element.calls.append(("select_option", value))

    def check(self, timeout: float | None = None) -> None:
        self._raise_if_configured("check")
        self.element.checked = True

    def uncheck(self, timeout: float | None = None) -> None:
        self._raise_if_configured("uncheck")
        self.element.checked = False

    def hover(self, timeout: float | None = None) -> None:
        self._raise_if_configured("hover")
        self.element.calls.append(("hover",))

    def scroll_into_view_if_needed(self, timeout: float | None = None) -> None:
        self._raise_if_configured("scroll")
        self.element.calls.append(("scroll",))

    def set_input_files(self, files: list[str], timeout: float | None = None) -> None:
        self._raise_if_configured("upload")
        self.element.calls.append(("set_input_files", files))

    def inner_text(self, timeout: float | None = None) -> str:
        self._raise_if_configured("text")
        return str(self.element.value) if self.element else ""

    def input_value(self, timeout: float | None = None) -> str:
        return str(self.element.value) if self.element else ""

    def evaluate(self, js: str, timeout: float | None = None) -> Any:
        el = self.element
        if el is None:
            raise AssertionError("FakeLocator.evaluate called with no element bound")
        if js == ab.SECRET_JS:
            if el.secret_info is not None:
                return el.secret_info
            return {"tag": el.tag, "type": "", "autocomplete": "", "inputmode": "", "maxlength": -1, "text": el.name, "contenteditable": False}
        if js == ab.OPTIONS_JS:
            return el.options
        if js == ab.VALUE_JS:
            return el.checked if el.role in ("checkbox", "radio") else el.value
        if "type === 'file'" in js:
            return el.is_file_input
        if js in el.custom_evaluators:
            value = el.custom_evaluators[js]
            return value() if callable(value) else value
        raise AssertionError(f"FakeLocator.evaluate: no fake for js={js[:80]!r}")

    def element_handle(self, timeout: float | None = None) -> FakeElementHandle:
        return FakeElementHandle(self.element)

    def screenshot(self, path: str | None = None, timeout: float | None = None) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(PNG_HEADER)


class FakeFrame:
    def __init__(self, name: str = "", url: str = "http://x/inner.html", body_text: str = "") -> None:
        self.name = name
        self.url = url
        self.body_text = body_text
        self.ready_state = "complete"
        self.selector_matches: dict[str, int] = {}

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(None, FakeElement(ref="__body__", value=self.body_text))  # type: ignore[arg-type]
        count = self.selector_matches.get(selector, 0)
        return FakeLocator(None, FakeElement(ref="__match__") if count else None, count_override=count)  # type: ignore[arg-type]

    def wait_for_load_state(self, state: str = "load", timeout: float | None = None) -> None:
        pass

    def evaluate(self, js: str, timeout: float | None = None) -> Any:
        if "readyState" in js:
            return self.ready_state
        if js == ab.VISIBLE_SECRET_JS:
            return False
        raise AssertionError(f"FakeFrame.evaluate: no fake for js={js[:80]!r}")


class _FakeKeyboard:
    def __init__(self) -> None:
        self.presses: list[str] = []

    def press(self, key: str) -> None:
        self.presses.append(key)


class _FakeMouse:
    def __init__(self) -> None:
        self.wheels: list[tuple[int, int]] = []

    def wheel(self, dx: int, dy: int) -> None:
        self.wheels.append((dx, dy))


class FakePage(_Emitter):
    def __init__(self, ctx: "FakeContext", url: str = "http://x/outer.html", title: str = "Outer page") -> None:
        super().__init__()
        self.ctx = ctx
        self.url = url
        self._title = title
        self.snapshot_text = ""
        self.main_frame = FakeFrame(name="", url=url)
        self.frames: list[FakeFrame] = [self.main_frame]
        self.elements: dict[str, FakeElement] = {}
        self.goto_calls: list[str] = []
        self.pending_dialog: Any = None
        self.goto_error: Exception | None = None
        self.snapshot_calls = 0
        self.closed = False
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self.custom_evaluators: dict[str, Any] = {}
        self.selector_matches: dict[str, int] = {}

    def title(self) -> str:
        return self._title

    def aria_snapshot(self, mode: str = "ai") -> str:
        self.snapshot_calls += 1
        for el in self.elements.values():
            if el.heal_on_next_snapshot:
                el.exists = True
        return self.snapshot_text

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith("aria-ref="):
            ref = selector[len("aria-ref="):]
            return FakeLocator(self, self.elements.get(ref))
        if selector == "body":
            return FakeLocator(self, FakeElement(ref="__body__", value=getattr(self, "body_text", "")))
        count = self.selector_matches.get(selector, 0)
        return FakeLocator(self, FakeElement(ref="__match__") if count else None, count_override=count)

    def wait_for_load_state(self, state: str = "load", timeout: float | None = None) -> None:
        pass

    def wait_for_timeout(self, ms: float) -> None:
        pass

    def goto(self, url: str, wait_until: str = "load", timeout: float | None = None) -> None:
        self.goto_calls.append(url)
        if self.pending_dialog is not None:
            dialog, self.pending_dialog = self.pending_dialog, None
            self.ctx.emit("dialog", dialog)
            raise FakePlaywrightError(f"net::ERR_ABORTED at {url}")
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url

    def go_back(self, timeout: float | None = None, wait_until: str = "load") -> None:
        pass

    def reload(self, timeout: float | None = None, wait_until: str = "load") -> None:
        pass

    def close(self, run_before_unload: bool = False) -> None:
        self.closed = True
        self.emit("close", self)

    def bring_to_front(self) -> None:
        pass

    def frame(self, name: str | None = None) -> FakeFrame | None:
        for f in self.frames:
            if f.name == name:
                return f
        return None

    def evaluate(self, js: str, timeout: float | None = None) -> Any:
        if js in self.custom_evaluators:
            value = self.custom_evaluators[js]
            return value() if callable(value) else value
        raise AssertionError(f"FakePage.evaluate: no fake for js={js[:80]!r}")

    def screenshot(self, path: str | None = None, full_page: bool = False, timeout: float | None = None, scale: str | None = None) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(PNG_HEADER)


class FakeContext(_Emitter):
    def __init__(self) -> None:
        super().__init__()
        self.pages: list[FakePage] = []
        self.browser: Any = SimpleNamespace(version="119.0.5845.0")
        self.default_timeout: float | None = None
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    def set_default_timeout(self, ms: float) -> None:
        self.default_timeout = ms

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, ctx_factory: Callable[[str, dict[str, Any]], FakeContext]) -> None:
        self._ctx_factory = ctx_factory
        self.launch_calls: list[tuple[str, dict[str, Any]]] = []

    def launch_persistent_context(self, user_data_dir: str, **options: Any) -> FakeContext:
        self.launch_calls.append((user_data_dir, options))
        return self._ctx_factory(user_data_dir, options)


class FakePlaywright:
    def __init__(self, ctx_factory: Callable[[str, dict[str, Any]], FakeContext]) -> None:
        self.chromium = FakeChromium(ctx_factory)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def build_elements_from_snapshot(text: str, frame_name: str = "gsft_main") -> tuple[dict[str, FakeElement], "FakeFrame | None"]:
    """Auto-generate one FakeElement per ref in a captured ai-mode snapshot; wire the first iframe to a FakeFrame."""
    lines = ab.parse_ai_snapshot(text)
    elements: dict[str, FakeElement] = {}
    inner_frame: FakeFrame | None = None
    for line in lines:
        if not line.ref:
            continue
        elements[line.ref] = FakeElement(ref=line.ref, role=line.role, name=line.name or "", value=line.value or "")
    for line in lines:
        if line.role == "iframe" and line.ref:
            inner_frame = FakeFrame(name=frame_name, url="http://x/inner.html")
            elements[line.ref].frame = inner_frame
            elements[line.ref].frame_label = frame_name
            break
    return elements, inner_frame


class FakeClock:
    """A deterministic stand-in for time.monotonic/time.sleep: every call advances virtual time."""

    def __init__(self, start: float = 1_000.0, step: float = 0.05) -> None:
        self.value = start
        self.step = step

    def now(self) -> float:
        self.value += self.step
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(seconds, 0.0)


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Patch ab._now / ab._sleep so timing loops (settle, wait, start/stop polling) resolve instantly."""
    clock = FakeClock()
    monkeypatch.setattr(ab, "_now", clock.now)
    monkeypatch.setattr(ab, "_sleep", clock.sleep)
    return clock


@pytest.fixture
def make_daemon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock: FakeClock) -> Callable[..., tuple[Any, FakeContext, FakePage]]:
    """Build a real Daemon against a fake Playwright stack: start_browser() runs for real, nothing else does.

    Returns (daemon, ctx, page) where `page` is the single page seeded into the context
    (ctx.pages[0], also daemon.current). Pass snapshot_text to auto-populate page.elements
    (see build_elements_from_snapshot) and wire an iframe named `frame_name`.
    """

    def build(
        profile: str = "default",
        config: dict[str, Any] | None = None,
        headless: bool = True,
        snapshot_text: str | None = None,
        frame_name: str = "gsft_main",
        page_url: str = "http://x/outer.html",
        page_title: str = "Outer page",
        eval_enabled: bool | None = None,
    ) -> tuple[Any, FakeContext, FakePage]:
        home = tmp_path / "home"
        paths = ab.ProfilePaths(profile, home)
        cfg = dict(config or {})
        if eval_enabled is not None:
            cfg["eval"] = eval_enabled
        launch = {
            "profile": profile, "home": str(home), "log": str(paths.log), "token": "test-token",
            "browser": "msedge", "exe": None, "headless": headless, "window_size": None,
            "code_hash": "deadbeef", "config": cfg, "idle_seconds": 0.0, "tool_version": ab.TOOL_VERSION,
        }
        holder: dict[str, Any] = {}

        def ctx_factory(user_data_dir: str, options: dict[str, Any]) -> FakeContext:
            ctx = FakeContext()
            page = FakePage(ctx, url=page_url, title=page_title)
            if snapshot_text is not None:
                page.snapshot_text = snapshot_text
                elements, inner_frame = build_elements_from_snapshot(snapshot_text, frame_name=frame_name)
                page.elements = elements
                if inner_frame is not None:
                    page.frames = [page.main_frame, inner_frame]
            ctx.pages.append(page)
            holder["ctx"] = ctx
            holder["page"] = page
            return ctx

        monkeypatch.setattr(ab, "_playwright_factory", lambda: FakePlaywright(ctx_factory))
        monkeypatch.setattr(ab, "_playwright_errors", lambda: (FakePlaywrightError, FakePlaywrightTimeoutError))
        daemon = ab.Daemon(launch)
        daemon.start_browser()
        return daemon, holder["ctx"], holder["page"]

    return build
