"""Shared offline fakes for the edgepy test suite.

Nothing here touches the network, Edge, or the registry. Every I/O edge of the tool is a
module-level seam that the `fake_edge` fixture replaces:

  _ws_connect      -> FakeWebSocket wired to a FakePage (scripted CDP peer)
  _launch_process  -> FakeProcess that writes DevToolsActivePort like Chromium does
  _open_url        -> canned HTTP bodies for /json/list, /json/version, PyPI, GitHub
  _sleep / _kill_tree / _attach_job_object / _registry_value -> recorded no-ops
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import edge_pyodide as ep  # noqa: E402


# ---------------------------------------------------------------------------
# Websocket / CDP fakes


class FakeWebSocket:
    """Scripted websocket: `inbound` items are returned by recv_text in order.

    An item of None simulates a recv timeout slice. When a `responder` is given, every
    sent message is passed to it and the messages it returns are queued for delivery.
    """

    def __init__(self, inbound: list[str | None] | None = None,
                 responder: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None) -> None:
        self.inbound: list[str | None] = list(inbound or [])
        self.sent: list[dict[str, Any]] = []
        self.responder = responder
        self.closed = False

    def send_text(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        if self.responder is not None:
            for reply in self.responder(message):
                self.inbound.append(json.dumps(reply))

    def recv_text(self, timeout: float | None = 0.5) -> str | None:
        if not self.inbound:
            return None
        return self.inbound.pop(0)

    def close(self) -> None:
        self.closed = True


def binding_event(stream: str, text: str) -> dict[str, Any]:
    payload = json.dumps({"s": stream, "b": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    return {"method": "Runtime.bindingCalled", "params": {"name": ep.BINDING_NAME, "payload": payload}}


def binding_event_bytes(stream: str, raw: bytes) -> dict[str, Any]:
    payload = json.dumps({"s": stream, "b": base64.b64encode(raw).decode("ascii")})
    return {"method": "Runtime.bindingCalled", "params": {"name": ep.BINDING_NAME, "payload": payload}}


class FakePage:
    """Simulates the runner page behind Runtime.evaluate.

    `sandbox` maps request names ("run", "info", ...) to callables returning the result
    dict (or raising to produce an {"ok": false} envelope). Stream output is produced by
    calling `emit` from inside a sandbox handler.
    """

    def __init__(self) -> None:
        self.state = "ready"
        self.error: str | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.methods: list[str] = []
        self.pending_events: list[dict[str, Any]] = []
        self.stdin_lines: list[str] = []
        self.sandbox: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "info": lambda arg: {"pyodide": "314.0.5", "python": "3.14.2", "jspi": True, "loaded": []},
            "load": lambda arg: {"loaded": list(arg.get("names") or [])},
            "install": lambda arg: {"installed": list(arg.get("names") or [])},
            "mount": lambda arg: {"dest": arg["dest"], "files": 1},
            "run": lambda arg: {"exit_code": 0, "traceback": None},
            "eval": lambda arg: {"value": 2, "repr": None},
            "repl_push": lambda arg: {"status": "complete", "error": None, "exit_code": None},
        }

    def emit(self, stream: str, text: str) -> None:
        self.pending_events.append(binding_event(stream, text))

    def respond(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        method = message["method"]
        self.methods.append(method)
        msg_id = message["id"]
        params = message.get("params") or {}
        events: list[dict[str, Any]] = []
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if "window.edgepy.state" in expression:
                value = json.dumps({"s": self.state, "e": self.error})
                return [{"id": msg_id, "result": {"result": {"type": "string", "value": value}}}]
            if expression.startswith("window.edgepy.call("):
                inner = expression[len("window.edgepy.call("):-1]
                name_json, _, arg_json = inner.partition(", ")
                name = json.loads(name_json)
                arg = json.loads(arg_json)
                self.calls.append((name, arg))
                try:
                    result = self.sandbox[name](arg)
                    envelope = {"ok": True, "result": result}
                except Exception as exc:  # noqa: BLE001 - mirrors the in-page dispatch()
                    envelope = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": "tb"}
                events, self.pending_events = self.pending_events, []
                return [*events, {"id": msg_id, "result": {"result": {"type": "string", "value": json.dumps(envelope)}}}]
            if expression.startswith("window.edgepy.setStdinLines("):
                self.stdin_lines = json.loads(expression[len("window.edgepy.setStdinLines("):-1])
                return [{"id": msg_id, "result": {"result": {"type": "undefined"}}}]
            return [{"id": msg_id, "result": {"result": {"type": "undefined"}}}]
        if method == "Browser.close":
            return [{"id": msg_id, "result": {}}]
        return [{"id": msg_id, "result": {}}]


# ---------------------------------------------------------------------------
# Process / HTTP fakes


class FakeProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def make_vendor(root: Path, flavor: str = "full", with_manifest: bool = True) -> Path:
    """Create a synthetic vendor folder: fake dist files, lockfile, two pure wheels."""
    dist = root / "pyodide"
    dist.mkdir(parents=True, exist_ok=True)
    for name in ep.DIST_REQUIRED_FILES:
        if name != "pyodide-lock.json":
            (dist / name).write_bytes(b"fake " + name.encode())
    packages = {
        "numpy": {"name": "numpy", "version": "2.4.6", "file_name": "numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                  "sha256": "00" * 32, "depends": []},
        "pandas": {"name": "pandas", "version": "3.0.0", "file_name": "pandas-3.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                   "sha256": "11" * 32, "depends": ["numpy"]},
        "micropip": {"name": "micropip", "version": "0.11.1", "file_name": "micropip-0.11.1-py3-none-any.whl",
                     "sha256": "22" * 32, "depends": []},
        "PyYAML": {"name": "PyYAML", "version": "6.0.2", "file_name": "pyyaml-6.0.2-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                   "sha256": "33" * 32, "depends": []},
    }
    lock = {"info": {"abi_version": "2026_0", "arch": "wasm32", "platform": "emscripten_5_0_3", "python": "3.14.2"},
            "packages": packages}
    (dist / "pyodide-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (dist / "package.json").write_text(json.dumps({"name": "pyodide", "version": "314.0.5"}), encoding="utf-8")
    present = list(packages.values()) if flavor == "full" else [packages["micropip"]]
    for entry in present:
        (dist / entry["file_name"]).write_bytes(b"wheel " + entry["file_name"].encode())
    wheels = root / "wheels"
    wheels.mkdir(exist_ok=True)
    manifest_wheels = []
    for filename in ("tabulate-0.9.0-py3-none-any.whl", "six-1.16.0-py2.py3-none-any.whl"):
        path = wheels / filename
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("dummy/__init__.py", "")
        manifest_wheels.append({"name": filename.split("-")[0], "version": filename.split("-")[1], "file": filename,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "requires": []})
    if with_manifest:
        (root / ep.MANIFEST_NAME).write_text(json.dumps({
            "tool": "edgepy", "pyodide_version": "314.0.5", "python": "3.14.2", "flavor": flavor,
            "integrity": True, "wheels": manifest_wheels, "unresolved": [],
        }), encoding="utf-8")
    return root


@pytest.fixture
def vendor_dir(tmp_path: Path) -> Path:
    return make_vendor(tmp_path / "vendor")


@pytest.fixture
def core_vendor_dir(tmp_path: Path) -> Path:
    return make_vendor(tmp_path / "vendor-core", flavor="core")


class FakeEdgeEnv:
    """Everything the `fake_edge` fixture records for assertions."""

    def __init__(self) -> None:
        self.page = FakePage()
        self.page_ws: FakeWebSocket | None = None
        self.browser_ws: FakeWebSocket | None = None
        self.launch_argv: list[str] | None = None
        self.process: FakeProcess | None = None
        self.sleeps: list[float] = []
        self.killed: list[int] = []
        self.http: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.port = 9229


@pytest.fixture
def fake_edge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeEdgeEnv:
    env = FakeEdgeEnv()
    monkeypatch.setenv("EDGEPY_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("EDGEPY_VENDOR_DIR", raising=False)
    monkeypatch.delenv("EDGEPY_EDGE_PATH", raising=False)
    monkeypatch.delenv("EDGEPY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("EDGEPY_SHOW", raising=False)
    fake_exe = tmp_path / "Edge" / "Application" / "msedge.exe"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"MZ")
    (fake_exe.parent / "151.0.4129.101").mkdir()
    monkeypatch.setenv("EDGEPY_EDGE_PATH", str(fake_exe))

    def launch(argv: list[str], cwd: Path, log_path: Path) -> FakeProcess:
        env.launch_argv = list(argv)
        profile = next(a for a in argv if a.startswith("--user-data-dir=")).split("=", 1)[1]
        Path(profile).mkdir(parents=True, exist_ok=True)
        (Path(profile) / "DevToolsActivePort").write_text(f"{env.port}\n/devtools/browser/abc-123", encoding="utf-8")
        log_path.write_text("DevTools listening on ws://127.0.0.1\n", encoding="utf-8")
        env.process = FakeProcess()
        return env.process

    def ws_connect(host: str, port: int, path: str, timeout: float = 10.0) -> FakeWebSocket:
        assert host == "127.0.0.1" and port == env.port
        if path.startswith("/devtools/browser/"):
            env.browser_ws = FakeWebSocket(responder=env.page.respond)
            return env.browser_ws
        env.page_ws = FakeWebSocket(responder=env.page.respond)
        return env.page_ws

    env.http[f"http://127.0.0.1:{env.port}/json/list"] = json.dumps([
        {"type": "background_page", "webSocketDebuggerUrl": f"ws://127.0.0.1:{env.port}/devtools/page/BG"},
        {"type": "page", "url": "about:blank", "webSocketDebuggerUrl": f"ws://127.0.0.1:{env.port}/devtools/page/PAGE1"},
    ]).encode()
    env.http[f"http://127.0.0.1:{env.port}/json/version"] = json.dumps({"Browser": "Edg/151.0.4129.101"}).encode()

    class Response(io.BytesIO):
        status = 200
        headers: dict[str, str] = {}

    def open_url(request: Any, timeout: float, loopback: bool) -> Response:
        url = request.full_url
        env.requests.append(url)
        if url not in env.http:
            raise ep.urllib.error.URLError(f"no canned response for {url}")
        return Response(env.http[url])

    monkeypatch.setattr(ep, "_launch_process", launch)
    monkeypatch.setattr(ep, "_ws_connect", ws_connect)
    monkeypatch.setattr(ep, "_open_url", open_url)
    monkeypatch.setattr(ep, "_sleep", lambda s: env.sleeps.append(s))
    monkeypatch.setattr(ep, "_kill_tree", lambda pid: env.killed.append(pid))
    monkeypatch.setattr(ep, "_attach_job_object", lambda proc: None)
    monkeypatch.setattr(ep, "_registry_value", lambda *a, **k: None)
    monkeypatch.setattr(ep, "VERBOSE", False)
    return env


@pytest.fixture
def runtime_factory(fake_edge: FakeEdgeEnv, vendor_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., ep.EdgeRuntime]:
    """Builds EdgeRuntime instances bound to the fake Edge and the synthetic vendor dir."""

    def factory(**kwargs: Any) -> ep.EdgeRuntime:
        if kwargs.get("vendor_dir") is None:  # CLI handlers pass vendor_dir=None explicitly
            kwargs["vendor_dir"] = vendor_dir
        kwargs.setdefault("on_stdout", None)
        kwargs.setdefault("on_stderr", None)
        return ep.EdgeRuntime(**kwargs)

    monkeypatch.setattr(ep, "_runtime_factory", factory)
    return factory
