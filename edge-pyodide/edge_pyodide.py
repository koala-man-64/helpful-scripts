#!/usr/bin/env python3
"""edgepy - run Python scripts, modules, and a REPL inside Microsoft Edge (Pyodide)
with nothing installed: no pip, no venv, and fully offline.

Edge is launched with remote debugging on a throwaway profile. A standard-library
http.server on 127.0.0.1 serves a vendored Pyodide distribution (CPython compiled to
WebAssembly), pre-downloaded pure-Python wheels, and your mounted source folders. The
Chrome DevTools Protocol drives the page: it runs your code, streams stdout/stderr back
byte-exact, and propagates the script's real exit code.

Requirements:
  none at runtime - standard library only (Python >= 3.10, Microsoft Edge >= 137)
  pip install pytest        (tests only)

Commands (run `python edge_pyodide.py --help` for full detail):
  run repl fetch doctor packages clean

Environment (real env vars, read lazily - no .env loading, the tool must run bare):
  EDGEPY_VENDOR_DIR        folder holding pyodide/ + wheels/  (default <script dir>/vendor)
  EDGEPY_EDGE_PATH         explicit msedge.exe path
  EDGEPY_RUN_DIR           root for per-run profiles/logs      (default %LOCALAPPDATA%/edgepy/run)
  EDGEPY_TIMEOUT_SECONDS   default --timeout for `run`
  EDGEPY_SHOW              "true" = visible Edge window by default
  EDGEPY_PYODIDE_VERSION   Pyodide version `fetch` downloads  (default 314.0.5)
  EDGEPY_CA_BUNDLE         PEM bundle for `fetch` behind TLS inspection
  HTTPS_PROXY              honored by `fetch` only; loopback traffic never uses a proxy

Error convention (repo-wide): ValueError = bad input, fix the call (exit 2);
RuntimeError = environment or remote failure, fix config or retry (exit 1).
A script's own exit code passes through verbatim; 124 = --timeout hit; 130 = Ctrl-C.
Tool failures print {"error": {...}} to stderr; a script's own stderr is passed through
untouched, so stderr starting with '{"error"' always means edgepy itself failed.
"""

from __future__ import annotations

import argparse
import base64
import codecs
import email.parser
import hashlib
import html
import http.server
import io
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# Constants

DEFAULT_PYODIDE_VERSION = "314.0.5"
PYODIDE_RELEASE_URL = "https://github.com/pyodide/pyodide/releases/download/{version}/{asset}"
PYODIDE_RELEASE_API = "https://api.github.com/repos/pyodide/pyodide/releases/tags/{version}"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "edgepy/1.0 (+https://github.com/rdprokes/helpful-scripts)"

# Files every usable Pyodide distribution folder must contain (314.x layout).
DIST_REQUIRED_FILES = (
    "pyodide.js",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)
MANIFEST_NAME = "edgepy-manifest.json"

BINDING_NAME = "__edgepy_out"
BOOT_TIMEOUT = 120.0        # antivirus scanning of the 9.6 MB wasm can be slow on first boot
LAUNCH_TIMEOUT = 30.0       # DevToolsActivePort must appear within this
TARGET_TIMEOUT = 10.0       # a page target must show up in /json/list within this
TERMINATE_GRACE = 3.0       # after Runtime.terminateExecution before hard kill
CLOSE_GRACE = 5.0           # after Browser.close before taskkill
RECV_SLICE = 0.5            # socket timeout slice so Ctrl-C and deadlines are observable
DEFAULT_WINDOW_SIZE = "1200,800"
MAX_RUN_DIR_CHARS = 180     # Chromium silently falls back to the default profile past MAX_PATH

MOUNT_CAP_BYTES = 64 * 1024 * 1024
CAPTURE_CAP_BYTES = 64 * 1024 * 1024
MOUNT_PRUNE = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vs", ".vscode",
})

# Pinned on the handler class: never trust the mimetypes module on Windows, where HKCR
# entries overwrite the built-in table. A wrong type for pyodide.asm.wasm makes
# loadPyodide() hang forever (Pyodide only uses instantiateStreaming in browsers).
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".map": "application/json",
    ".zip": "application/zip",
    ".whl": "application/octet-stream",
    ".data": "application/octet-stream",
    ".tar": "application/x-tar",
    ".metadata": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".css": "text/css",
    ".ts": "text/plain; charset=utf-8",
}

EDGE_CANDIDATES = (
    ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
    ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles(x86)", r"Microsoft\Edge Beta\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge Beta\Application\msedge.exe"),
    ("ProgramFiles(x86)", r"Microsoft\Edge Dev\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge Dev\Application\msedge.exe"),
    ("LOCALAPPDATA", r"Microsoft\Edge SxS\Application\msedge.exe"),
)
EDGE_POSIX_CANDIDATES = (
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
    "/opt/microsoft/msedge/msedge",
)
EDGE_POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Edge"
EDGE_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
EDGE_VERSION_DIR_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")

# Error classes carried on exceptions via _tag (see the module docstring for exit codes).
ERR_CONFIG = "config"
ERR_EDGE_NOT_FOUND = "edge_not_found"
ERR_EDGE_POLICY = "edge_policy"
ERR_EDGE_LAUNCH = "edge_launch"
ERR_CDP = "cdp"
ERR_BOOT = "pyodide_boot"
ERR_SANDBOX = "sandbox"
ERR_VENDOR_MISSING = "vendor_missing"
ERR_VENDOR_MISMATCH = "vendor_mismatch"
ERR_PACKAGE_MISSING = "package_missing"
ERR_MOUNT_TOO_LARGE = "mount_too_large"
ERR_TIMEOUT = "timeout"
ERR_VALIDATION = "validation"
ERR_FETCH = "fetch"

EXIT_TIMEOUT = 124
EXIT_INTERRUPT = 130

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

VERBOSE = False


def _log(message: str) -> None:
    # Diagnostics go to stderr so stdout stays the script's own output stream.
    if VERBOSE:
        print(f"edgepy: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Config helpers (all env reads are lazy - nothing touched at import)


def _tag(
    exc: Exception,
    error_class: str,
    http_status: int | None = None,
    hint: str | None = None,
) -> Exception:
    # No custom exception classes (repo convention) - the error class and hint ride on
    # the builtin exception instance so the CLI can emit a structured envelope.
    exc.error_class = error_class  # type: ignore[attr-defined]
    exc.http_status = http_status  # type: ignore[attr-defined]
    exc.hint = hint  # type: ignore[attr-defined]
    return exc


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _vendor_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    # Resolution order: --vendor-dir > EDGEPY_VENDOR_DIR > <script dir>/vendor >
    # %LOCALAPPDATA%/edgepy/vendor. The script-dir default is returned even when absent
    # so the error message names the folder the user most likely meant.
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.getenv("EDGEPY_VENDOR_DIR")
    if env:
        return Path(env).expanduser().resolve()
    beside = _script_dir() / "vendor"
    if beside.is_dir():
        return beside
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "edgepy" / "vendor"
        if candidate.is_dir():
            return candidate
    return beside


def _run_root() -> Path:
    env = os.getenv("EDGEPY_RUN_DIR")
    if env:
        return Path(env).expanduser().resolve()
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / "edgepy" / "run"
    return Path.home() / ".edgepy" / "run"


def _default_timeout() -> float | None:
    raw = os.getenv("EDGEPY_TIMEOUT_SECONDS")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise _tag(RuntimeError(f"EDGEPY_TIMEOUT_SECONDS is not a number: {raw!r}"), ERR_CONFIG) from exc
    return value if value > 0 else None


_sleep = time.sleep   # alias so tests can record waits instead of sleeping
_now = time.monotonic


# ---------------------------------------------------------------------------
# Vendor folder (pyodide/ distribution + wheels/ + manifest)

WHEEL_FILENAME_RE = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)(?:-(?P<build>\d[^-]*))?"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)


def normalize_name(name: str) -> str:
    # PEP 503 project-name normalization; also what micropip keys packages by.
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class WheelFile:
    path: Path
    name: str          # distribution name as written in the filename
    version: str
    python_tags: tuple[str, ...]
    abi_tags: tuple[str, ...]
    platform_tags: tuple[str, ...]

    @property
    def normalized(self) -> str:
        return normalize_name(self.name)

    @property
    def pure(self) -> bool:
        return "none" in self.abi_tags and "any" in self.platform_tags


def parse_wheel_filename(path: Path) -> WheelFile:
    match = WHEEL_FILENAME_RE.match(path.name)
    if not match:
        raise _tag(ValueError(f"Not a PEP 427 wheel filename: {path.name}"), ERR_VALIDATION)
    return WheelFile(
        path=path,
        name=match.group("name"),
        version=match.group("version"),
        python_tags=tuple(match.group("python").split(".")),
        abi_tags=tuple(match.group("abi").split(".")),
        platform_tags=tuple(match.group("platform").split(".")),
    )


@dataclass(frozen=True)
class VendorInfo:
    root: Path
    dist_dir: Path
    wheels_dir: Path
    pyodide_version: str
    python_version: str
    abi_version: str
    platform: str
    flavor: str                          # "full" when bundled package files are present, else "core"
    bundled: dict[str, dict[str, Any]]   # normalized name -> lockfile entry
    bundled_present: frozenset[str]      # normalized names whose wheel file exists on disk
    wheels: tuple[WheelFile, ...]
    manifest: dict[str, Any]

    def has_bundled(self, name: str) -> bool:
        return normalize_name(name) in self.bundled_present

    def has_wheel(self, name: str) -> bool:
        norm = normalize_name(name)
        return any(w.normalized == norm for w in self.wheels)


def _fetch_hint(root: Path) -> str:
    return (
        f"On a machine with internet run `py -3 edge_pyodide.py fetch --flavor full` and "
        f"copy the resulting vendor folder to {root} (or set EDGEPY_VENDOR_DIR)."
    )


def load_vendor(root: Path) -> VendorInfo:
    dist_dir = root / "pyodide"
    wheels_dir = root / "wheels"
    if not dist_dir.is_dir():
        raise _tag(
            RuntimeError(f"No Pyodide distribution at {dist_dir}."),
            ERR_VENDOR_MISSING,
            hint=_fetch_hint(root),
        )
    missing = [name for name in DIST_REQUIRED_FILES if not (dist_dir / name).is_file()]
    if missing:
        raise _tag(
            RuntimeError(f"Pyodide distribution at {dist_dir} is incomplete; missing {', '.join(missing)}."),
            ERR_VENDOR_MISMATCH,
            hint="The folder must hold an unpacked pyodide-<version>.tar.bz2 (Pyodide >= 314.0). " + _fetch_hint(root),
        )
    try:
        lock = json.loads((dist_dir / "pyodide-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _tag(RuntimeError(f"Cannot read {dist_dir / 'pyodide-lock.json'}: {exc}"), ERR_VENDOR_MISMATCH) from exc
    info = lock.get("info") or {}
    pyodide_version = str(info.get("version") or "")
    if not pyodide_version:
        # The 314.x lockfile "info" block carries no version; package.json does.
        try:
            pyodide_version = str(json.loads((dist_dir / "package.json").read_text(encoding="utf-8")).get("version") or "")
        except (OSError, ValueError):
            pyodide_version = ""
    packages = lock.get("packages") or {}
    bundled: dict[str, dict[str, Any]] = {}
    present: set[str] = set()
    for name, entry in packages.items():
        norm = normalize_name(name)
        bundled[norm] = entry
        file_name = entry.get("file_name")
        if file_name and (dist_dir / file_name).is_file():
            present.add(norm)
    flavor = "full" if len(present) >= max(1, len(bundled) // 2) else "core"
    wheels: list[WheelFile] = []
    if wheels_dir.is_dir():
        for path in sorted(wheels_dir.glob("*.whl")):
            try:
                wheels.append(parse_wheel_filename(path))
            except ValueError:
                _log(f"ignoring non-wheel file in {wheels_dir}: {path.name}")
    manifest: dict[str, Any] = {}
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log(f"ignoring unreadable manifest {manifest_path}: {exc}")
    return VendorInfo(
        root=root,
        dist_dir=dist_dir,
        wheels_dir=wheels_dir,
        pyodide_version=pyodide_version or "unknown",
        python_version=str(info.get("python") or "unknown"),
        abi_version=str(info.get("abi_version") or "unknown"),
        platform=str(info.get("platform") or "unknown"),
        flavor=flavor,
        bundled=bundled,
        bundled_present=frozenset(present),
        wheels=tuple(wheels),
        manifest=manifest,
    )


def split_packages(vendor: VendorInfo, names: Iterable[str]) -> tuple[list[str], list[str]]:
    # Bundled (lockfile) packages load through pyodide.loadPackage; everything else goes
    # through micropip and must exist in wheels/ because there is no network.
    bundled: list[str] = []
    wheels: list[str] = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        norm = normalize_name(name)
        if norm in vendor.bundled:
            if norm not in vendor.bundled_present:
                raise _tag(
                    RuntimeError(
                        f"Package {name!r} is part of Pyodide but its wheel is not in {vendor.dist_dir} "
                        f"(vendor flavor is {vendor.flavor!r})."
                    ),
                    ERR_PACKAGE_MISSING,
                    hint="Fetch the full distribution: `edgepy fetch --flavor full`.",
                )
            bundled.append(name)
        elif vendor.has_wheel(name):
            wheels.append(name)
        else:
            raise _tag(
                RuntimeError(f"Package {name!r} is neither bundled with Pyodide nor present in {vendor.wheels_dir}."),
                ERR_PACKAGE_MISSING,
                hint=f"On a machine with internet run `edgepy fetch --pkg {name}` and copy vendor/wheels here.",
            )
    return bundled, wheels


# ---------------------------------------------------------------------------
# WebSocket client (RFC 6455, client side only, loopback, no extensions)

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def _ws_accept(key: str) -> str:
    # The GUID is appended to the base64 *text* of the key, not to the decoded nonce.
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _mask_payload(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return b""
    # XOR via big integers: orders of magnitude faster than a per-byte Python loop.
    repeated = (mask * (len(payload) // 4 + 1))[: len(payload)]
    value = int.from_bytes(payload, "big") ^ int.from_bytes(repeated, "big")
    return value.to_bytes(len(payload), "big")


def _encode_frame(opcode: int, payload: bytes, mask: bytes | None = None, fin: bool = True) -> bytes:
    header = bytearray([(0x80 if fin else 0) | (opcode & 0x0F)])
    length = len(payload)
    mask_bit = 0x80 if mask is not None else 0
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack(">H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack(">Q", length)
    if mask is not None:
        header += mask
        payload = _mask_payload(payload, mask)
    return bytes(header) + payload


def _parse_frame_header(buf: bytes) -> tuple[bool, int, bool, int, int] | None:
    # Returns (fin, opcode, masked, payload_length, header_length) or None if more bytes
    # are needed before the header can be read.
    if len(buf) < 2:
        return None
    fin = bool(buf[0] & 0x80)
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    length = buf[1] & 0x7F
    offset = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = struct.unpack(">H", buf[2:4])[0]
        offset = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = struct.unpack(">Q", buf[2:10])[0]
        offset = 10
    if masked:
        offset += 4
    return fin, opcode, masked, length, offset


class WebSocket:
    """Minimal text-frame websocket over a connected socket. Not thread-safe."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = bytearray()
        self._fragments: list[bytes] = []   # CDP only ever sends text; fragments are joined as UTF-8
        self._closed = False

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float = 10.0) -> "WebSocket":
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        # Deliberately no Origin (would need --remote-allow-origins) and no
        # Sec-WebSocket-Extensions (would let Edge enable permessage-deflate).
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise _tag(RuntimeError(f"Cannot connect to DevTools at {host}:{port}: {exc}"), ERR_CDP) from exc
        try:
            sock.sendall(request.encode("ascii"))
            raw = bytearray()
            while b"\r\n\r\n" not in raw:
                chunk = sock.recv(4096)
                if not chunk:
                    raise _tag(RuntimeError("DevTools closed the connection during the websocket handshake."), ERR_CDP)
                raw += chunk
                if len(raw) > 65536:
                    raise _tag(RuntimeError("DevTools websocket handshake response is too large."), ERR_CDP)
        except OSError as exc:
            sock.close()
            raise _tag(RuntimeError(f"DevTools websocket handshake failed: {exc}"), ERR_CDP) from exc
        except RuntimeError:
            sock.close()
            raise
        head, _, rest = bytes(raw).partition(b"\r\n\r\n")
        status_line, _, header_text = head.decode("latin-1").partition("\r\n")
        headers = email.parser.Parser().parsestr(header_text)
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or parts[1] != "101":
            sock.close()
            raise _tag(
                RuntimeError(f"DevTools refused the websocket upgrade: {status_line.strip()!r}"),
                ERR_CDP,
                hint="If the status is 403 the Host header was rejected (must be 127.0.0.1) or a policy blocks DevTools.",
            )
        expected = _ws_accept(key)
        if (headers.get("Sec-WebSocket-Accept") or "").strip() != expected:
            sock.close()
            raise _tag(RuntimeError("DevTools websocket handshake returned a bad Sec-WebSocket-Accept."), ERR_CDP)
        ws = cls(sock)
        ws._buf += rest
        return ws

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise _tag(RuntimeError("DevTools websocket is closed."), ERR_CDP)
        try:
            self._sock.sendall(_encode_frame(opcode, payload, mask=os.urandom(4)))
        except OSError as exc:
            self._closed = True
            raise _tag(RuntimeError(f"DevTools websocket send failed: {exc}"), ERR_CDP) from exc

    def recv_text(self, timeout: float | None = RECV_SLICE) -> str | None:
        """Return the next complete text message, or None if `timeout` elapsed first."""
        while True:
            header = _parse_frame_header(bytes(self._buf[:14]))
            if header is not None:
                fin, opcode, masked, length, header_len = header
                total = header_len + length
                if len(self._buf) >= total:
                    frame = bytes(self._buf[:total])
                    del self._buf[:total]
                    payload = frame[header_len:]
                    if masked:  # servers must not mask, but be forgiving
                        payload = _mask_payload(payload, frame[header_len - 4:header_len])
                    message = self._handle_frame(fin, opcode, payload)
                    if message is not None:
                        return message
                    continue
            if not self._fill(timeout):
                return None

    def _handle_frame(self, fin: bool, opcode: int, payload: bytes) -> str | None:
        if opcode == OP_PING:
            self._send_frame(OP_PONG, payload)
            return None
        if opcode == OP_PONG:
            return None
        if opcode == OP_CLOSE:
            code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
            try:
                self._send_frame(OP_CLOSE, payload[:2])
            except RuntimeError:
                pass
            self._closed = True
            raise _tag(
                RuntimeError(f"Edge closed the DevTools connection (close code {code})."),
                ERR_CDP,
                hint="The page or browser went away - the window was closed, Edge crashed, or a policy detached DevTools.",
            )
        if opcode in (OP_TEXT, OP_BINARY):
            if not fin:
                self._fragments = [payload]
                return None
            return payload.decode("utf-8", "replace")
        if opcode == OP_CONT:
            self._fragments.append(payload)
            if not fin:
                return None
            data = b"".join(self._fragments)
            self._fragments = []
            return data.decode("utf-8", "replace")
        return None  # reserved opcode - ignore

    def _fill(self, timeout: float | None) -> bool:
        if self._closed:
            raise _tag(RuntimeError("DevTools websocket is closed."), ERR_CDP)
        self._sock.settimeout(timeout)
        try:
            chunk = self._sock.recv(1 << 16)
        except socket.timeout:
            return False
        except OSError as exc:
            self._closed = True
            raise _tag(RuntimeError(f"DevTools websocket receive failed: {exc}"), ERR_CDP) from exc
        if not chunk:
            self._closed = True
            raise _tag(RuntimeError("Edge dropped the DevTools connection."), ERR_CDP)
        self._buf += chunk
        return True

    def close(self) -> None:
        if not self._closed:
            try:
                self._send_frame(OP_CLOSE, struct.pack(">H", 1000))
            except RuntimeError:
                pass
            self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


# Module-level seam so tests can substitute a scripted fake websocket.
def _ws_connect(host: str, port: int, path: str, timeout: float = 10.0) -> WebSocket:
    return WebSocket.connect(host, port, path, timeout=timeout)


# ---------------------------------------------------------------------------
# CDP session (single-threaded: send, read frames, dispatch events until the reply shows up)


class CdpSession:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._next_id = 1
        self._results: dict[int, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.detached: str | None = None
        self.on("Inspector.detached", self._on_detached)
        self.on("Inspector.targetCrashed", lambda _p: self._mark_detached("target crashed"))

    def on(self, event: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers[event] = handler

    def _on_detached(self, params: dict[str, Any]) -> None:
        self._mark_detached(str(params.get("reason") or "detached"))

    def _mark_detached(self, reason: str) -> None:
        self.detached = reason

    def call(self, method: str, params: dict[str, Any] | None = None, deadline: float | None = None) -> dict[str, Any]:
        """Send a command and return its result dict. Raises TimeoutError at `deadline`."""
        msg_id = self._next_id
        self._next_id += 1
        self._ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        return self._wait(msg_id, method, deadline)

    def _wait(self, msg_id: int, method: str, deadline: float | None) -> dict[str, Any]:
        while True:
            if msg_id in self._results:
                reply = self._results.pop(msg_id)
                if "error" in reply:
                    err = reply["error"]
                    raise _tag(
                        RuntimeError(f"DevTools {method} failed: {err.get('message')} (code {err.get('code')})"),
                        ERR_CDP,
                    )
                return reply.get("result") or {}
            if self.detached:
                raise _tag(
                    RuntimeError(f"DevTools detached from the page ({self.detached})."),
                    ERR_CDP,
                    hint="The tab was closed or crashed. With --show, keep the edgepy window open until the run ends.",
                )
            if deadline is not None and _now() >= deadline:
                raise TimeoutError(method)
            self.pump_once()

    def pump_once(self, timeout: float | None = RECV_SLICE) -> bool:
        """Read and dispatch at most one message; False if nothing arrived within `timeout`."""
        text = self._ws.recv_text(timeout)
        if text is None:
            return False
        try:
            message = json.loads(text)
        except ValueError:
            _log(f"ignoring non-JSON DevTools message: {text[:120]!r}")
            return True
        self._dispatch(message)
        return True

    def pump(self, duration: float) -> None:
        end = _now() + duration
        while _now() < end:
            remaining = max(0.0, end - _now())
            self.pump_once(min(RECV_SLICE, remaining) or 0.01)

    def _dispatch(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            _log(f"ignoring non-object DevTools message: {str(message)[:120]!r}")
            return
        if "id" in message:
            self._results[int(message["id"])] = message
            return
        handler = self._handlers.get(str(message.get("method")))
        if handler is not None:
            handler(message.get("params") or {})

    def close(self) -> None:
        self._ws.close()


# ---------------------------------------------------------------------------
# Edge process (discovery, policies, launch, DevToolsActivePort, teardown)


def _is_windows() -> bool:
    return sys.platform == "win32"


def find_edge(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate msedge.exe: explicit > EDGEPY_EDGE_PATH > PATH > known folders > App Paths."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.getenv("EDGEPY_EDGE_PATH")
    if env:
        candidates.append(Path(env))
    which = shutil.which("msedge") or shutil.which("microsoft-edge")
    if which:
        candidates.append(Path(which))
    if _is_windows():
        for env_name, rel in EDGE_CANDIDATES:
            base = os.getenv(env_name)
            if base:
                candidates.append(Path(base) / rel)
        app_path = _registry_value(EDGE_APP_PATHS_KEY, None)
        if app_path:
            candidates.append(Path(str(app_path)))
    else:
        candidates.extend(Path(p) for p in EDGE_POSIX_CANDIDATES)
    for path in candidates:
        if path.is_file():
            return path
    raise _tag(
        RuntimeError("Microsoft Edge was not found."),
        ERR_EDGE_NOT_FOUND,
        hint="Set EDGEPY_EDGE_PATH or pass --edge-path pointing at msedge.exe.",
    )


def edge_version_from_path(exe: Path) -> str | None:
    # Chromium installs keep the binaries in a subfolder named after the version.
    try:
        versions = [p.name for p in exe.parent.iterdir() if p.is_dir() and EDGE_VERSION_DIR_RE.match(p.name)]
    except OSError:
        return None
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def _registry_value(key_path: str, value_name: str | None, hive: str = "HKLM") -> Any:
    # Single winreg touchpoint (test seam). Returns None when the key/value is absent.
    if not _is_windows():
        return None
    import winreg  # noqa: PLC0415 - Windows-only import kept local on purpose

    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, value_name if value_name is not None else "")
            return value
    except OSError:
        return None


POLICY_LABELS: dict[str, Callable[[Any], tuple[str, str]]] = {
    # policy name -> (status, label)
    "RemoteDebuggingAllowed": lambda v: ("fail", "remote debugging BLOCKED by policy") if v in (0, "0") else ("ok", "allowed"),
    "DeveloperToolsAvailability": lambda v: {
        0: ("ok", "blocked only for force-installed extensions (default)"),
        1: ("ok", "allowed"),
        2: ("fail", "DevTools DISALLOWED - attaching to pages is refused"),
    }.get(int(v) if str(v).isdigit() else -1, ("warn", f"unknown value {v!r}")),
    "HeadlessModeEnabled": lambda v: ("fail", "headless mode BLOCKED by policy (use --show)") if v in (0, "0") else ("ok", "allowed"),
    "UserDataDir": lambda v: ("warn", f"profile relocated by policy to {v!r}; DevToolsActivePort lands there"),
}


def read_policies() -> dict[str, dict[str, Any]]:
    """Edge enterprise policies that can break remote debugging, decoded for humans."""
    report: dict[str, dict[str, Any]] = {}
    for name, decode in POLICY_LABELS.items():
        for hive in ("HKLM", "HKCU"):
            value = _registry_value(EDGE_POLICY_KEY, name, hive=hive)
            if value is not None:
                status, label = decode(value)
                report[name] = {"hive": hive, "value": value, "status": status, "label": label}
                break
    return report


def policy_blockers(policies: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{name}={info['value']} ({info['label']})" for name, info in policies.items() if info["status"] == "fail"]


def edge_argv(
    exe: Path,
    profile_dir: Path,
    headless: bool,
    devtools: bool,
    window_size: str = DEFAULT_WINDOW_SIZE,
    url: str = "about:blank",
) -> list[str]:
    argv = [
        str(exe),
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-proxy-server",          # everything is loopback; a corporate PAC must not see it
        f"--window-size={window_size}",
    ]
    if headless:
        argv.append("--headless")     # plain form: Chromium >= 132 ignores the =new/=old value
    if devtools:
        argv.append("--auto-open-devtools-for-tabs")
    argv.append(url)
    return argv


@dataclass
class EdgeProcess:
    exe: Path
    proc: Any                      # subprocess.Popen (or a fake in tests)
    run_dir: Path
    profile_dir: Path
    log_path: Path
    port: int = 0
    browser_path: str = ""         # line 2 of DevToolsActivePort, e.g. /devtools/browser/<uuid>
    job_handle: Any = None
    version: str | None = None

    @property
    def pid(self) -> int:
        return int(getattr(self.proc, "pid", 0) or 0)


def _make_run_dir() -> Path:
    root = _run_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _tag(
            RuntimeError(f"Cannot create the run directory root {root}: {exc}"),
            ERR_EDGE_LAUNCH,
            hint="Point EDGEPY_RUN_DIR at a short, writable folder.",
        ) from exc
    run_dir = root / f"r{secrets.token_hex(4)}"
    profile = run_dir / "profile"
    if len(str(profile)) > MAX_RUN_DIR_CHARS:
        raise _tag(
            RuntimeError(f"Run directory path is too long for Chromium ({len(str(profile))} chars): {profile}"),
            ERR_EDGE_LAUNCH,
            hint="Set EDGEPY_RUN_DIR to a short path such as C:\\edgepy-run.",
        )
    probe = profile / "edgepy-write-test"
    try:
        profile.mkdir(parents=True, exist_ok=False)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        _rmtree_retry(run_dir, attempts=1)
        raise _tag(RuntimeError(f"Run directory {profile} is not writable: {exc}"), ERR_EDGE_LAUNCH) from exc
    try:
        (run_dir / "owner.json").write_text(json.dumps({"pid": os.getpid(), "created": time.time()}), encoding="utf-8")
    except OSError as exc:
        _rmtree_retry(run_dir, attempts=1)
        raise _tag(RuntimeError(f"Cannot write into the run directory {run_dir}: {exc}"), ERR_EDGE_LAUNCH) from exc
    return run_dir


# Module-level seam: tests replace this with a fake that writes DevToolsActivePort.
def _launch_process(argv: list[str], cwd: Path, log_path: Path) -> Any:
    log = open(log_path, "ab")  # noqa: SIM115 - handed to the child, closed with it
    kwargs: dict[str, Any] = {}
    if _is_windows():
        # Own process group so the terminal's Ctrl-C reaches edgepy only; teardown is ours.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    finally:
        log.close()


def _attach_job_object(proc: Any) -> Any:
    """Best effort: put Edge in a Job Object that kills the tree when edgepy exits."""
    if not _is_windows() or not hasattr(proc, "_handle"):
        return None
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):
            kernel32.CloseHandle(job)
            _log("job object assignment refused (already in a job?) - relying on taskkill")
            return None
        return job
    except Exception as exc:  # noqa: BLE001 - purely best effort
        _log(f"job object unavailable: {exc}")
        return None


def _tail(path: Path, lines: int = 15) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def parse_active_port(text: str) -> tuple[int, str]:
    # Chromium writes "%d\n%s" - no trailing newline; line 2 is a path, not a URL.
    lines = text.split("\n")
    if not lines or not lines[0].strip().isdigit():
        raise _tag(RuntimeError(f"Malformed DevToolsActivePort content: {text!r}"), ERR_EDGE_LAUNCH)
    port = int(lines[0].strip())
    browser_path = lines[1].strip() if len(lines) > 1 else ""
    return port, browser_path


def wait_for_active_port(edge: EdgeProcess, timeout: float = LAUNCH_TIMEOUT) -> None:
    port_file = edge.profile_dir / "DevToolsActivePort"
    deadline = _now() + timeout
    while True:
        if port_file.is_file():
            try:
                text = port_file.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if text.strip():
                edge.port, edge.browser_path = parse_active_port(text)
                return
        code = edge.proc.poll()
        if code is not None:
            blockers = policy_blockers(read_policies())
            hint = "Edge log tail:\n" + (_tail(edge.log_path) or "(empty)")
            if blockers:
                hint = "Blocking policies: " + "; ".join(blockers) + ". " + hint
            raise _tag(
                RuntimeError(f"Edge exited with code {code} before DevTools came up."),
                ERR_EDGE_POLICY if blockers else ERR_EDGE_LAUNCH,
                hint=hint,
            )
        if _now() >= deadline:
            blockers = policy_blockers(read_policies())
            hint = (
                "Check policies RemoteDebuggingAllowed / HeadlessModeEnabled / UserDataDir "
                "(`edgepy doctor`), try --show, and read " + str(edge.log_path) + "."
            )
            if blockers:
                hint = "Blocking policies: " + "; ".join(blockers) + ". " + hint
            raise _tag(
                RuntimeError(f"Edge did not write DevToolsActivePort within {timeout:g}s."),
                ERR_EDGE_POLICY if blockers else ERR_EDGE_LAUNCH,
                hint=hint,
            )
        _sleep(0.05)


def _loopback_opener() -> urllib.request.OpenerDirector:
    # Never let HTTPS_PROXY / a PAC route 127.0.0.1 through a corporate proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# Module-level seam for every HTTP request the tool makes (DevTools discovery and fetch).
def _urlopen(request: urllib.request.Request, timeout: float, loopback: bool = True) -> bytes:
    with _open_url(request, timeout, loopback) as resp:
        return resp.read()


def _devtools_json(port: int, path: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": f"127.0.0.1:{port}"})
    try:
        body = _urlopen(req, timeout)
    except (urllib.error.URLError, OSError) as exc:
        raise _tag(RuntimeError(f"DevTools HTTP endpoint {path} failed: {exc}"), ERR_CDP) from exc
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise _tag(RuntimeError(f"DevTools {path} returned non-JSON."), ERR_CDP) from exc


def find_page_target(port: int, timeout: float = TARGET_TIMEOUT) -> dict[str, Any]:
    deadline = _now() + timeout
    last_error: Exception | None = None
    while _now() < deadline:
        try:
            targets = _devtools_json(port, "/json/list")
        except RuntimeError as exc:
            last_error = exc
            targets = []
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return target
        _sleep(0.1)
    raise _tag(
        RuntimeError("No debuggable page target appeared in /json/list."),
        ERR_EDGE_POLICY if last_error is None else ERR_CDP,
        hint="DeveloperToolsAvailability=2 lets the port open but hides page targets; check `edgepy doctor`.",
    )


def launch_edge(
    exe: Path | None = None,
    headless: bool = True,
    devtools: bool = False,
    window_size: str = DEFAULT_WINDOW_SIZE,
    retries: int = 1,
) -> EdgeProcess:
    exe = exe or find_edge()
    for attempt in range(retries + 1):
        try:
            return _launch_edge_once(exe, headless, devtools, window_size)
        except RuntimeError as exc:
            # Edge's own updater can swap the binary under us and exit 0 immediately;
            # one relaunch on a fresh profile is cheap and fixes that race.
            transient = getattr(exc, "error_class", None) == ERR_EDGE_LAUNCH and "exited with code 0" in str(exc)
            if not transient or attempt >= retries:
                raise
            _log(f"Edge exited immediately; retrying launch ({attempt + 1}/{retries})")
            _sleep(1.0)
    raise AssertionError("unreachable")


def _launch_edge_once(exe: Path, headless: bool, devtools: bool, window_size: str) -> EdgeProcess:
    run_dir = _make_run_dir()
    profile_dir = run_dir / "profile"
    log_path = run_dir / "edge.log"
    argv = edge_argv(exe, profile_dir, headless=headless, devtools=devtools, window_size=window_size)
    _log("launching " + " ".join(argv))
    try:
        proc = _launch_process(argv, run_dir, log_path)
    except OSError as exc:
        _rmtree_retry(run_dir, attempts=1)
        raise _tag(RuntimeError(f"Cannot start Edge ({exe}): {exc}"), ERR_EDGE_LAUNCH) from exc
    edge = EdgeProcess(exe=exe, proc=proc, run_dir=run_dir, profile_dir=profile_dir, log_path=log_path)
    edge.job_handle = _attach_job_object(proc)
    try:
        wait_for_active_port(edge)
        try:
            version = _devtools_json(edge.port, "/json/version")
            edge.version = str(version.get("Browser") or "")
        except RuntimeError:
            edge.version = None
    except BaseException:
        close_edge(edge)
        raise
    _log(f"edge {edge.version or '?'} pid {edge.pid} devtools port {edge.port}")
    return edge


def _kill_tree(pid: int) -> None:
    if pid <= 0:
        return
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _log(f"taskkill failed for pid {pid}: {exc}")
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _rmtree_retry(path: Path, attempts: int = 6) -> bool:
    delay = 0.2
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                _log(f"could not remove {path}: {exc}")
                return False
            _sleep(delay)
            delay = min(delay * 2, 3.0)
    return False


def close_edge(edge: EdgeProcess, keep_run_dir: bool = False) -> None:
    proc = edge.proc
    if proc.poll() is None and edge.port and edge.browser_path:
        try:
            ws = _ws_connect("127.0.0.1", edge.port, edge.browser_path, timeout=2.0)
            try:
                ws.send_text(json.dumps({"id": 1, "method": "Browser.close"}))
                deadline = _now() + 2.0
                while _now() < deadline:
                    if ws.recv_text(0.2) is not None:
                        break
            finally:
                ws.close()
        except (RuntimeError, OSError) as exc:
            _log(f"Browser.close unavailable: {exc}")
    if proc.poll() is None:
        try:
            proc.wait(timeout=CLOSE_GRACE)
        except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired or fakes
            pass
    if proc.poll() is None:
        _log("Edge still running after Browser.close - killing the process tree")
        _kill_tree(edge.pid)
        try:
            proc.wait(timeout=CLOSE_GRACE)
        except Exception:  # noqa: BLE001
            pass
    if edge.job_handle is not None and _is_windows():
        try:
            import ctypes  # noqa: PLC0415

            ctypes.windll.kernel32.CloseHandle(edge.job_handle)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        edge.job_handle = None
    if not keep_run_dir:
        _rmtree_retry(edge.run_dir)


# ---------------------------------------------------------------------------
# Local HTTP server (runner page, vendored dist, wheels, PEP 503 index, mount zips)


def build_simple_index(wheels: Sequence[WheelFile], hashes: dict[str, str] | None = None) -> dict[str, bytes]:
    """PEP 503 pages keyed by path: 'simple/' root plus 'simple/<name>/' per project."""
    hashes = hashes or {}
    projects: dict[str, list[WheelFile]] = {}
    for wheel in wheels:
        projects.setdefault(wheel.normalized, []).append(wheel)
    pages: dict[str, bytes] = {}
    root = ["<!DOCTYPE html><html><head><meta name=\"pypi:repository-version\" content=\"1.0\"><title>edgepy index</title></head><body>"]
    for norm in sorted(projects):
        root.append(f'<a href="{norm}/">{html.escape(norm)}</a><br>')
        links = ["<!DOCTYPE html><html><head><meta name=\"pypi:repository-version\" content=\"1.0\">"
                 f"<title>Links for {html.escape(norm)}</title></head><body><h1>Links for {html.escape(norm)}</h1>"]
        for wheel in sorted(projects[norm], key=lambda w: w.path.name):
            href = f"../../wheels/{urllib.parse.quote(wheel.path.name)}"
            digest = hashes.get(wheel.path.name)
            if digest:
                href += f"#sha256={digest}"
            links.append(f'<a href="{href}">{html.escape(wheel.path.name)}</a><br>')
        links.append("</body></html>")
        pages[f"simple/{norm}/"] = "\n".join(links).encode("utf-8")
    root.append("</body></html>")
    pages["simple/"] = "\n".join(root).encode("utf-8")
    return pages


def build_mount_zip(directory: Path, prune: frozenset[str] = MOUNT_PRUNE, cap_bytes: int | None = None) -> tuple[bytes, int]:
    """Zip a directory tree in memory (skipping junk folders). Returns (zip_bytes, file_count)."""
    if cap_bytes is None:
        cap_bytes = MOUNT_CAP_BYTES  # resolved at call time so the module constant can be tuned
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise _tag(ValueError(f"Mount path is not a directory: {directory}"), ERR_VALIDATION)
    buf = io.BytesIO()
    total = 0
    count = 0
    skipped: list[str] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs if d not in prune)
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = Path(root) / name
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                total += size
                if total > cap_bytes:
                    raise _tag(
                        ValueError(
                            f"Mount {directory} exceeds {cap_bytes // (1024 * 1024)} MB; "
                            "use --no-mount or --mount a smaller folder."
                        ),
                        ERR_MOUNT_TOO_LARGE,
                    )
                try:
                    zf.write(full, full.relative_to(directory).as_posix())
                except OSError as exc:
                    # stat() succeeds on exclusively locked files on Windows (Office ~$ owner
                    # files, OneDrive placeholders); skip them rather than fail the mount.
                    skipped.append(f"{full}: {exc}")
                    continue
                count += 1
    if skipped:
        _log(f"mount {directory}: skipped {len(skipped)} unreadable file(s): " + "; ".join(skipped[:5]))
    return buf.getvalue(), count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class LocalServer:
    """Serves the runner page and vendor assets from 127.0.0.1 on an ephemeral port."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(12)
        self.routes: dict[str, tuple[bytes, str]] = {}      # "path" -> (body, content type)
        self.dirs: dict[str, Path] = {}                      # "prefix" -> flat directory
        self.port = 0
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}"

    def add_bytes(self, path: str, body: bytes, content_type: str) -> None:
        self.routes[path] = (body, content_type)

    def add_dir(self, prefix: str, directory: Path) -> None:
        self.dirs[prefix.strip("/")] = Path(directory)

    def start(self) -> None:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                _log("http " + fmt % args)

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve(head=True)

            def do_GET(self) -> None:  # noqa: N802
                self._serve(head=False)

            def _send(self, status: int, body: bytes, content_type: str, head: bool, extra: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def _serve(self, head: bool) -> None:
                path = urllib.parse.urlsplit(self.path).path
                prefix = f"/{server.token}/"
                if not path.startswith(prefix):
                    self._send(404, b"not found", "text/plain", head)
                    return
                rel = urllib.parse.unquote(path[len(prefix):])
                if rel == "":
                    rel = "index.html"
                if rel in server.routes:
                    body, ctype = server.routes[rel]
                    self._send(200, body, ctype, head)
                    return
                if rel + "/" in server.routes:
                    self._send(301, b"", "text/plain", head, {"Location": path + "/"})
                    return
                top, _, rest = rel.partition("/")
                directory = server.dirs.get(top)
                if directory is None or not rest or "/" in rest or "\\" in rest or not SAFE_BASENAME_RE.match(rest):
                    self._send(404, b"not found", "text/plain", head)
                    return
                file_path = directory / rest
                if not file_path.is_file():
                    self._send(404, b"not found", "text/plain", head)
                    return
                ctype = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
                try:
                    size = file_path.stat().st_size
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(size))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if not head:
                        with open(file_path, "rb") as fh:
                            shutil.copyfileobj(fh, self.wfile, 1 << 20)
                except (OSError, ConnectionError):
                    return

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request: Any, client_address: Any) -> None:
                # Aborted connections (Edge cancelling a fetch) must not spray tracebacks
                # on the user's stderr; keep them behind --verbose like log_message.
                _log(f"http handler error from {client_address}: {traceback.format_exc().strip().splitlines()[-1]}")

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, name="edgepy-http", daemon=True
        )
        self._thread.start()
        _log(f"local server on {self.base_url}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ---------------------------------------------------------------------------
# Runner page (served at /<token>/) and the in-browser boot shim

# Plain-stdlib Python that runs inside Pyodide as module `_edgepy`. It is also exec'd
# under local CPython by the tests with a stub `pyodide_js`, so keep it free of
# Pyodide-only imports at module level.
_BOOT_PY = r'''
import io
import json
import os
import runpy
import sys
import traceback

_console = None
_STATE = {"mounts": [], "index_url": None}


def _js():
    import pyodide_js  # provided by Pyodide

    return pyodide_js


def _pkg_callbacks():
    # Keep Pyodide's "Loading numpy" chatter out of the script's stdout: progress goes to
    # the browser console (shown by edgepy --verbose), failures go to stderr.
    from pyodide.code import run_js

    message = run_js("(m) => console.log('edgepy pkg: ' + m)")
    return {"messageCallback": message, "errorCallback": lambda m: sys.stderr.write("edgepy pkg: %s\n" % (m,))}


def _exit_code(exc):
    # CPython semantics for SystemExit: None -> 0, int -> int, anything else -> print + 1.
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    sys.stderr.write(str(code) + "\n")
    return 1


def _format_user_traceback(exc):
    # Drop the shim/runpy frames above the user's code so tracebacks read like python's.
    tb = exc.__traceback__
    skip = ("<edgepy>", "runpy.py", "<frozen runpy>", "<frozen importlib._bootstrap>")
    while tb is not None and any(tb.tb_frame.f_code.co_filename.endswith(s) for s in skip):
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb))


def _flush():
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass


async def _preload(source):
    # Auto-load Pyodide-bundled packages the code imports (no-op for stdlib imports).
    if not source:
        return
    try:
        await _js().loadPackagesFromImports(source, **_pkg_callbacks())
    except Exception as exc:  # missing wheel in a core-flavor vendor dir, etc.
        sys.stderr.write("edgepy: package preload failed: %s\n" % (exc,))


async def _info(arg):
    import platform

    jspi = False
    try:
        from pyodide.ffi import can_run_sync

        jspi = bool(can_run_sync())
    except Exception:
        pass
    try:
        import pyodide

        version = pyodide.__version__
    except Exception:
        version = "unknown"
    loaded = []
    try:
        from pyodide.code import run_js

        loaded = list(run_js("Object.keys(pyodide.loadedPackages)").to_py())
    except Exception:
        pass
    return {"pyodide": version, "python": platform.python_version(), "jspi": jspi, "loaded": sorted(loaded)}


async def _load(arg):
    names = list(arg.get("names") or [])
    if names:
        options = arg.get("options") or {}
        # Keyword arguments to a JS function become a trailing options object.
        await _js().loadPackage(names, **options, **_pkg_callbacks())
    return {"loaded": names}


async def _install(arg):
    if arg.get("check_integrity", True):
        await _js().loadPackage("micropip", **_pkg_callbacks())
    else:
        await _js().loadPackage("micropip", checkIntegrity=False, **_pkg_callbacks())
    import micropip

    index_url = arg.get("index_url")
    if index_url:
        micropip.set_index_urls([index_url])
        _STATE["index_url"] = index_url
    names = list(arg.get("names") or [])
    try:
        await micropip.install(names)
    except KeyError as exc:
        # micropip only falls back to the lockfile on ValueError; a KeyError here means the
        # index served the wrong content type or a malformed page.
        raise RuntimeError("micropip could not parse the local package index: %r" % (exc,))
    installed = sorted(str(k) for k in micropip.list().keys())
    return {"installed": installed}


async def _mount(arg):
    from pyodide.http import pyfetch

    dest = arg["dest"]
    resp = await pyfetch(arg["url"])
    if not resp.ok:
        raise RuntimeError("mount download failed: HTTP %s" % resp.status)
    data = await resp.bytes()
    return _unpack_mount(data, dest)


def _unpack_mount(data, dest):
    import zipfile

    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        zf.extractall(dest)
    if dest not in sys.path:
        sys.path.insert(0, dest)
    _STATE["mounts"].append(dest)
    return {"dest": dest, "files": len(names)}


async def _run(arg):
    kind = arg["kind"]
    argv = list(arg.get("argv") or [])
    cwd = arg.get("cwd")
    if cwd:
        os.chdir(cwd)
    await _preload(arg.get("source") or "")
    code = 0
    tb_text = None
    import __main__

    main_globals = __main__.__dict__
    try:
        if kind == "file":
            path = arg["path"]
            sys.argv = [path] + argv
            runpy.run_path(path, run_name="__main__")
        elif kind == "module":
            module = arg["module"]
            sys.argv = [module] + argv
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        else:
            sys.argv = [arg.get("argv0") or "-c"] + argv
            filename = arg.get("filename") or "<string>"
            main_globals.setdefault("__name__", "__main__")
            main_globals["__file__"] = filename
            exec(compile(arg["code"], filename, "exec"), main_globals)
    except SystemExit as exc:
        code = _exit_code(exc)
    except BaseException as exc:
        tb_text = _format_user_traceback(exc)
        sys.stderr.write(tb_text)
        code = 1
    finally:
        _flush()
    return {"exit_code": code, "traceback": tb_text}


async def _eval(arg):
    import __main__

    value = eval(arg["expression"], __main__.__dict__)
    _flush()
    try:
        json.dumps(value)
        return {"value": value, "repr": None}
    except (TypeError, ValueError):
        return {"value": None, "repr": repr(value)}


async def _repl_push(arg):
    global _console
    if _console is None:
        import __main__

        if arg.get("full", True):
            from pyodide.console import PyodideConsole as _Console
        else:
            from pyodide.console import Console as _Console
        _console = _Console(__main__.__dict__)
    fut = _console.push(arg["line"])
    status = fut.syntax_check
    result = {"status": status, "error": None, "exit_code": None}
    if status == "syntax-error":
        result["error"] = fut.formatted_error
    elif status == "complete":
        try:
            value = await fut
        except SystemExit as exc:
            result["exit_code"] = _exit_code(exc)
        except BaseException:
            result["error"] = fut.formatted_error or traceback.format_exc()
        else:
            # Like the interactive interpreter: echo the value of an expression statement.
            if value is not None:
                try:
                    from pyodide.console import repr_shorten

                    text = repr_shorten(value)
                except Exception:
                    text = repr(value)
                sys.stdout.write(text + "\n")
    _flush()
    return result


_HANDLERS = {
    "info": _info,
    "load": _load,
    "install": _install,
    "mount": _mount,
    "run": _run,
    "eval": _eval,
    "repl_push": _repl_push,
}


async def dispatch(req_json):
    req = json.loads(req_json)
    name = req.get("name")
    arg = req.get("arg") or {}
    try:
        handler = _HANDLERS[name]
    except KeyError:
        return json.dumps({"ok": False, "error": "unknown request %r" % (name,), "traceback": None})
    try:
        result = await handler(arg)
        return json.dumps({"ok": True, "result": result})
    except BaseException as exc:
        _flush()
        return json.dumps({
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
        })
'''

_RUNNER_HTML = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>edgepy</title>
<style>body{font:13px/1.4 Consolas,monospace;background:#111;color:#ddd;margin:1em}pre{white-space:pre-wrap}</style>
</head><body>
<div id="status">edgepy: loading Pyodide...</div>
<pre id="log"></pre>
<script src="pyodide/pyodide.js"></script>
<script>
(async () => {
  const statusEl = document.getElementById("status");
  const logEl = document.getElementById("log");
  const b64 = (u8) => {
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    return btoa(s);
  };
  const emit = (obj) => {
    const text = JSON.stringify(obj);
    if (typeof window.__BINDING__ === "function") {
      window.__BINDING__(text);
    } else {
      logEl.textContent += text + "\n";
    }
  };
  window.edgepy = { state: "booting", error: null, call: null, setStdinLines: null };
  window.__edgepy_lines = [];
  try {
    const indexURL = new URL("pyodide/", location.href).href;
    const stdinMode = __STDIN_MODE__;
    const pyodide = await loadPyodide({ indexURL });
    window.pyodide = pyodide;
    // Byte-exact streams: every write() becomes one binding event carrying base64 bytes.
    pyodide.setStdout({ write: (buf) => { emit({ s: "o", b: b64(buf) }); return buf.length; }, isatty: true });
    pyodide.setStderr({ write: (buf) => { emit({ s: "e", b: b64(buf) }); return buf.length; }, isatty: true });
    if (stdinMode === "none") {
      pyodide.setStdin({ error: true });
    } else if (stdinMode === "lines") {
      const nextLine = () => {
        const q = window.__edgepy_lines;
        return q.length ? q.shift() : null;
      };
      pyodide.setStdin({ stdin: nextLine, autoEOF: false, isatty: false });
    }
    // default "prompt" mode keeps Pyodide's own window.prompt() stdin handler
    pyodide.globals.set("_edgepy_src", __BOOT_PY__);
    await pyodide.runPythonAsync(
      "import sys, types\n" +
      "_m = types.ModuleType('_edgepy')\n" +
      "exec(compile(_edgepy_src, '<edgepy>', 'exec'), _m.__dict__)\n" +
      "sys.modules['_edgepy'] = _m\n" +
      "del _m, _edgepy_src\n"
    );
    window.edgepy.call = async (name, arg) => {
      pyodide.globals.set("_edgepy_req", JSON.stringify({ name, arg }));
      return await pyodide.runPythonAsync("import _edgepy\nawait _edgepy.dispatch(_edgepy_req)");
    };
    window.edgepy.setStdinLines = (lines) => { window.__edgepy_lines = lines.slice(); };
    window.edgepy.state = "ready";
    statusEl.textContent = "edgepy: Pyodide " + pyodide.version + " ready (window.pyodide is available in DevTools)";
  } catch (e) {
    window.edgepy.error = String((e && e.stack) || e);
    window.edgepy.state = "failed";
    statusEl.textContent = "edgepy: boot failed - " + window.edgepy.error;
  }
})();
</script>
</body></html>
'''


def render_runner_page(stdin_mode: str) -> bytes:
    page = _RUNNER_HTML.replace("__BINDING__", BINDING_NAME)
    page = page.replace("__STDIN_MODE__", json.dumps(stdin_mode))
    page = page.replace("__BOOT_PY__", json.dumps(_BOOT_PY))
    return page.encode("utf-8")


# ---------------------------------------------------------------------------
# EdgeRuntime (the importable API; every CLI verb is a thin layer over it)


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    traceback: str | None = None
    truncated: bool = False
    packages_loaded: list[str] = field(default_factory=list)


class _StreamSink:
    """Decodes base64 UTF-8 chunks incrementally, forwards them, and keeps a capped copy."""

    def __init__(self, forward: Callable[[str], None] | None, cap: int = CAPTURE_CAP_BYTES) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._forward = forward
        self._parts: list[str] = []
        self._size = 0
        self._cap = cap
        self.truncated = False

    def feed(self, raw: bytes) -> None:
        text = self._decoder.decode(raw)
        if not text:
            return
        if self._forward is not None:
            self._forward(text)
        if self._size < self._cap:
            self._parts.append(text)
            self._size += len(raw)
        else:
            self.truncated = True

    def take(self) -> tuple[str, bool]:
        """Return (captured_text, truncated) for the run so far and reset for the next one."""
        tail = self._decoder.decode(b"", final=True)
        if tail and self._forward is not None:
            self._forward(tail)
        text = "".join(self._parts) + tail
        truncated = self.truncated
        self._parts = []
        self._size = 0
        self.truncated = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        return text, truncated


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def _read_tty_line() -> str | None:
    # Local terminal line for the in-page input() bridge; None means EOF.
    line = sys.stdin.readline()
    if line == "":
        return None
    return line.rstrip("\r\n")


class EdgeRuntime:
    """A Pyodide sandbox inside a private Edge instance, driven over CDP.

    Use as a context manager. Output streams are forwarded live to `on_stdout` /
    `on_stderr` (default: the real sys.stdout / sys.stderr) and also captured into
    each RunResult.
    """

    def __init__(
        self,
        vendor_dir: str | os.PathLike[str] | None = None,
        headless: bool = True,
        devtools: bool = False,
        timeout: float | None = None,
        stdin: str = "auto",
        on_stdout: Callable[[str], None] | None = _write_stdout,
        on_stderr: Callable[[str], None] | None = _write_stderr,
        edge_path: str | os.PathLike[str] | None = None,
        window_size: str = DEFAULT_WINDOW_SIZE,
        stdin_reader: Callable[[], str | None] | None = None,
        keep_run_dir: bool = False,
    ) -> None:
        if stdin not in ("auto", "prompt", "lines", "none"):
            raise _tag(ValueError(f"stdin must be auto|prompt|lines|none, not {stdin!r}"), ERR_VALIDATION)
        self._vendor_path = vendor_dir
        self.headless = headless
        self.devtools = devtools
        self.timeout = timeout
        self._stdin_choice = stdin
        self.stdin_mode = "none"
        self._stdin_reader = stdin_reader or _read_tty_line
        self._on_stdout = on_stdout
        self._on_stderr = on_stderr
        self._edge_path = edge_path
        self.window_size = window_size
        self.keep_run_dir = keep_run_dir
        self.vendor: VendorInfo | None = None
        self.server: LocalServer | None = None
        self.edge: EdgeProcess | None = None
        self.cdp: CdpSession | None = None
        self.pyodide_version: str | None = None
        self.python_version: str | None = None
        self.jspi: bool | None = None
        self.edge_version: str | None = None
        self.boot_seconds: float | None = None
        self._out = _StreamSink(self._on_stdout)
        self._err = _StreamSink(self._on_stderr)
        self._boot_console_errors: list[str] = []
        self._mounts: dict[str, str] = {}            # dest -> resolved source directory
        self._mount_specs: list[tuple[Path, str | None]] = []   # replayed by restart()
        self._package_specs: list[tuple[str, list[str]]] = []   # ("load"|"install", names)
        self._loaded: list[str] = []
        self._busy = False
        self._poisoned = False

    # -- lifecycle ---------------------------------------------------------------------

    def __enter__(self) -> "EdgeRuntime":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _resolve_stdin_mode(self) -> str:
        # "prompt" reads one local line per input() on demand (works for terminals and
        # pipes alike and never blocks a script that does not read stdin). "lines" pre-reads
        # the whole pipe, which is only needed when a DevTools frontend could answer the
        # prompt() dialog before we do.
        if self._stdin_choice != "auto":
            return self._stdin_choice
        return "lines" if self.devtools else "prompt"

    def start(self) -> None:
        if self.cdp is not None:
            return
        started = _now()
        self.vendor = load_vendor(_vendor_dir(self._vendor_path))
        _log(f"vendor {self.vendor.root} pyodide {self.vendor.pyodide_version} ({self.vendor.flavor}), "
             f"{len(self.vendor.wheels)} wheels")
        self.stdin_mode = self._resolve_stdin_mode()
        self.server = LocalServer()
        self.server.add_dir("pyodide", self.vendor.dist_dir)
        self.server.add_dir("wheels", self.vendor.wheels_dir)
        hashes = {w["file"]: w["sha256"] for w in self.vendor.manifest.get("wheels", []) if w.get("file") and w.get("sha256")}
        for path, body in build_simple_index(self.vendor.wheels, hashes).items():
            self.server.add_bytes(path, body, "text/html; charset=utf-8")
        self.server.add_bytes("index.html", render_runner_page(self.stdin_mode), CONTENT_TYPES[".html"])
        self.server.start()
        try:
            exe = find_edge(self._edge_path)
            self.edge = launch_edge(exe, headless=self.headless, devtools=self.devtools, window_size=self.window_size)
            self.edge_version = self.edge.version
            target = find_page_target(self.edge.port)
            ws_url = urllib.parse.urlsplit(target["webSocketDebuggerUrl"])
            ws = _ws_connect("127.0.0.1", self.edge.port, ws_url.path)
            self.cdp = CdpSession(ws)
            self.cdp.on("Runtime.bindingCalled", self._on_binding)
            self.cdp.on("Runtime.consoleAPICalled", self._on_console)
            self.cdp.on("Runtime.exceptionThrown", self._on_exception)
            self.cdp.on("Page.javascriptDialogOpening", self._on_dialog)
            self.cdp.call("Runtime.enable")
            self.cdp.call("Page.enable")
            self.cdp.call("Runtime.addBinding", {"name": BINDING_NAME})
            self._navigate_and_boot()
        except BaseException:
            self.close()
            raise
        self.boot_seconds = _now() - started
        _log(f"ready in {self.boot_seconds:.1f}s: pyodide {self.pyodide_version} python {self.python_version} jspi={self.jspi}")

    def _navigate_and_boot(self) -> None:
        assert self.cdp is not None and self.server is not None
        self._boot_console_errors = []
        self._poisoned = False
        self.cdp.call("Page.navigate", {"url": self.server.base_url + "/"})
        deadline = _now() + BOOT_TIMEOUT
        while True:
            state = self._evaluate(
                "JSON.stringify({s: (window.edgepy && window.edgepy.state) || 'loading', "
                "e: window.edgepy && window.edgepy.error})",
                await_promise=False,
            )
            try:
                parsed = json.loads(state) if isinstance(state, str) else {}
            except ValueError:
                parsed = {}
            if parsed.get("s") == "ready":
                break
            if parsed.get("s") == "failed":
                raise _tag(
                    RuntimeError(f"Pyodide failed to boot: {parsed.get('e')}"),
                    ERR_BOOT,
                    hint="Check the vendor folder matches Pyodide >= 314 and that nothing blocks 127.0.0.1.",
                )
            if self._boot_console_errors:
                raise _tag(
                    RuntimeError("Pyodide failed to boot: " + " | ".join(self._boot_console_errors)[:500]),
                    ERR_BOOT,
                    hint="'wasm instantiation failed' means pyodide.asm.wasm was served with the wrong "
                         "Content-Type or is corrupt; re-run `edgepy fetch` and `edgepy doctor`.",
                )
            if _now() >= deadline:
                raise _tag(
                    RuntimeError(f"Pyodide did not become ready within {BOOT_TIMEOUT:g}s."),
                    ERR_BOOT,
                    hint="First boot can be slow under antivirus scanning; try again or exclude the vendor folder.",
                )
            self.cdp.pump(0.25)
        info = self.call_sandbox("info", {})
        self.pyodide_version = str(info.get("pyodide"))
        self.python_version = str(info.get("python"))
        self.jspi = bool(info.get("jspi"))
        self._loaded = list(info.get("loaded") or [])
        self._mounts = {}

    def restart(self) -> None:
        """Terminate whatever runs, reload the page, boot a fresh interpreter, and replay
        the session's mounts and package loads (interpreter state itself is lost)."""
        assert self.cdp is not None
        try:
            self.cdp.call("Runtime.terminateExecution", deadline=_now() + TERMINATE_GRACE)
        except (RuntimeError, TimeoutError) as exc:
            _log(f"terminateExecution: {exc}")
        self._busy = False
        self._navigate_and_boot()
        mount_specs, package_specs = list(self._mount_specs), list(self._package_specs)
        self._mount_specs, self._package_specs = [], []
        for directory, name in mount_specs:
            self.mount(directory, name)
        for kind, names in package_specs:
            (self.load_packages if kind == "load" else self.install)(names)

    def close(self) -> None:
        if self.cdp is not None:
            if self._busy:
                try:
                    self.cdp.call("Runtime.terminateExecution", deadline=_now() + 1.0)
                except (RuntimeError, TimeoutError):
                    pass
            try:
                self.cdp.close()
            except Exception:  # noqa: BLE001
                pass
            self.cdp = None
        if self.edge is not None:
            close_edge(self.edge, keep_run_dir=self.keep_run_dir)
            self.edge = None
        if self.server is not None:
            self.server.stop()
            self.server = None

    # -- event handlers ----------------------------------------------------------------

    def _on_binding(self, params: dict[str, Any]) -> None:
        if params.get("name") != BINDING_NAME:
            return
        try:
            payload = json.loads(params.get("payload") or "{}")
            raw = base64.b64decode(payload.get("b") or "")
        except (ValueError, TypeError):
            return
        (self._err if payload.get("s") == "e" else self._out).feed(raw)

    def _on_console(self, params: dict[str, Any]) -> None:
        text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args") or [])
        level = params.get("type")
        if "wasm instantiation failed" in text or (level == "error" and not self.pyodide_version):
            self._boot_console_errors.append(text)
        _log(f"console.{level}: {text[:300]}")

    def _on_exception(self, params: dict[str, Any]) -> None:
        details = params.get("exceptionDetails") or {}
        exc = details.get("exception") or {}
        text = exc.get("description") or details.get("text") or "unknown"
        if not self.pyodide_version:
            self._boot_console_errors.append(str(text)[:300])
        _log(f"page exception: {str(text)[:300]}")

    def _on_dialog(self, params: dict[str, Any]) -> None:
        assert self.cdp is not None
        dtype = params.get("type")
        reply: dict[str, Any]
        if dtype == "prompt" and self.stdin_mode == "prompt":
            line = self._stdin_reader()
            reply = {"accept": False} if line is None else {"accept": True, "promptText": line}
        elif dtype in ("alert", "beforeunload"):
            reply = {"accept": True}
        else:
            reply = {"accept": False}
        try:
            self.cdp.call("Page.handleJavaScriptDialog", reply, deadline=_now() + 5.0)
        except (RuntimeError, TimeoutError) as exc:
            # "No dialog is showing" is a normal race (the browser or another client closed it).
            _log(f"dialog reply skipped: {exc}")

    # -- CDP helpers -------------------------------------------------------------------

    def _require(self) -> CdpSession:
        if self.cdp is None:
            raise _tag(RuntimeError("EdgeRuntime is not started; use it as a context manager."), ERR_CDP)
        if self._poisoned:
            raise _tag(
                RuntimeError("The sandbox was terminated (timeout or interrupt) and must be restarted."),
                ERR_CDP,
                hint="Call restart() or start a new EdgeRuntime.",
            )
        return self.cdp

    def _evaluate(self, expression: str, await_promise: bool = True, deadline: float | None = None) -> Any:
        cdp = self._require()
        result = cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": await_promise, "returnByValue": True},
            deadline=deadline,
        )
        details = result.get("exceptionDetails")
        if details:
            exc = details.get("exception") or {}
            message = exc.get("description") or details.get("text") or "JavaScript exception"
            raise _tag(RuntimeError(f"Runner page error: {str(message)[:800]}"), ERR_CDP)
        return (result.get("result") or {}).get("value")

    def call_sandbox(self, name: str, arg: dict[str, Any], deadline: float | None = None) -> dict[str, Any]:
        expression = f"window.edgepy.call({json.dumps(name)}, {json.dumps(arg)})"
        self._require()  # refuse before touching _busy so a poisoned session keeps its flag
        self._busy = True
        try:
            raw = self._evaluate(expression, deadline=deadline)
        finally:
            self._busy = False
        try:
            response = json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            response = {}
        if not response.get("ok"):
            message = response.get("error") or f"no response from the sandbox for {name!r}"
            tb = response.get("traceback")
            if tb:
                _log(tb)
            raise _tag(RuntimeError(str(message)), ERR_SANDBOX, hint=(tb or "")[-600:] or None)
        return response.get("result") or {}

    def _deadline(self, timeout: float | None) -> float | None:
        effective = self.timeout if timeout is None else timeout
        return None if not effective else _now() + float(effective)

    def _timed_call(self, name: str, arg: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        deadline = self._deadline(timeout)
        try:
            return self.call_sandbox(name, arg, deadline=deadline)
        except TimeoutError as exc:
            self._poisoned = True
            self._busy = True  # close() will terminateExecution before detaching
            limit = self.timeout if timeout is None else timeout
            raise _tag(
                RuntimeError(f"Timed out after {limit:g}s while running {name}."),
                ERR_TIMEOUT,
                hint="Raise --timeout, or fix the script; the sandbox was terminated.",
            ) from exc

    # -- public API --------------------------------------------------------------------

    def load_packages(self, names: Sequence[str]) -> list[str]:
        assert self.vendor is not None
        names = [n for n in names if n.strip()]
        if not names:
            return []
        bundled, _ = split_packages(self.vendor, names)
        options: dict[str, Any] = {}
        if self.vendor.manifest.get("integrity") is False:
            options["checkIntegrity"] = False
        result = self._timed_call("load", {"names": bundled, "options": options})
        self._loaded.extend(result.get("loaded") or [])
        self._package_specs.append(("load", list(names)))
        return list(result.get("loaded") or [])

    def install(self, names: Sequence[str]) -> list[str]:
        assert self.vendor is not None and self.server is not None
        names = [n for n in names if n.strip()]
        if not names:
            return []
        _, wheels = split_packages(self.vendor, names)
        if self.vendor.flavor == "core" and not self.vendor.has_bundled("micropip"):
            raise _tag(
                RuntimeError("micropip is not available in this core-flavor vendor folder."),
                ERR_PACKAGE_MISSING,
                hint="Run `edgepy fetch --flavor core` again (it vendors micropip) or use --flavor full.",
            )
        result = self._timed_call("install", {
            "names": wheels,
            "index_url": self.server.base_url + "/simple",
            "check_integrity": self.vendor.manifest.get("integrity", True) is not False,
        })
        self._loaded.extend(wheels)
        self._package_specs.append(("install", list(names)))
        return list(result.get("installed") or [])

    def mount(self, directory: str | os.PathLike[str], name: str | None = None) -> str:
        """Copy a local folder into the sandbox at /mnt/<name> and return that path.

        The same folder under the same name is a no-op. Two different folders cannot share
        an explicit name; an implicit (basename) collision gets a short hash suffix so an
        earlier --mount can never hide the script's own folder.
        """
        assert self.server is not None
        directory = Path(directory).resolve()
        mount_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or directory.name or "root")
        dest = f"/mnt/{mount_name}"
        owner = self._mounts.get(dest)
        if owner == str(directory):
            return dest
        if owner is not None:
            if name is not None:
                raise _tag(
                    ValueError(f"Mount name {mount_name!r} is already used by {owner}; cannot also mount {directory} there."),
                    ERR_VALIDATION,
                    hint="Pass --mount DIR=NAME with a different NAME.",
                )
            suffix = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()[:6]
            dest = f"/mnt/{mount_name}_{suffix}"
            if self._mounts.get(dest) == str(directory):
                return dest
        data, count = build_mount_zip(directory)
        mount_id = secrets.token_hex(4)
        self.server.add_bytes(f"mount/{mount_id}.zip", data, CONTENT_TYPES[".zip"])
        _log(f"mount {directory} -> {dest} ({count} files, {len(data)} bytes zipped)")
        self._timed_call("mount", {"url": f"{self.server.base_url}/mount/{mount_id}.zip", "dest": dest})
        self._mounts[dest] = str(directory)
        self._mount_specs.append((directory, name))
        return dest

    def _run(self, arg: dict[str, Any], timeout: float | None = None) -> RunResult:
        started = _now()
        self._out.take()
        self._err.take()
        try:
            result = self._timed_call("run", arg, timeout=timeout)
        except RuntimeError as exc:
            # Keep whatever the script printed before the failure (e.g. a --timeout) so the
            # CLI can still report it in --json mode.
            exc.stdout, _ = self._out.take()  # type: ignore[attr-defined]
            exc.stderr, _ = self._err.take()  # type: ignore[attr-defined]
            exc.duration_s = _now() - started  # type: ignore[attr-defined]
            raise
        stdout, out_truncated = self._out.take()
        stderr, err_truncated = self._err.take()
        return RunResult(
            exit_code=int(result.get("exit_code", 1)),
            stdout=stdout,
            stderr=stderr,
            duration_s=_now() - started,
            traceback=result.get("traceback"),
            truncated=out_truncated or err_truncated,
            packages_loaded=sorted(set(self._loaded)),
        )

    def run_file(self, path: str | os.PathLike[str], argv: Sequence[str] = (), mount_parent: bool = True, timeout: float | None = None) -> RunResult:
        script = Path(path).resolve()
        if not script.is_file():
            raise _tag(ValueError(f"Script not found: {script}"), ERR_VALIDATION)
        source = script.read_text(encoding="utf-8", errors="replace")
        if not mount_parent:
            # No folder mounted: execute the source directly with a faithful filename.
            remote = f"/tmp/{script.name}"
            return self._run({"kind": "code", "code": source, "filename": remote, "argv0": remote,
                              "argv": list(argv), "cwd": None, "source": source}, timeout=timeout)
        dest = self.mount(script.parent)
        remote = f"{dest}/{script.name}"
        return self._run({"kind": "file", "path": remote, "argv": list(argv), "cwd": dest, "source": source}, timeout=timeout)

    def run_module(self, module: str, argv: Sequence[str] = (), timeout: float | None = None) -> RunResult:
        if not re.fullmatch(r"[A-Za-z_][\w.]*", module):
            raise _tag(ValueError(f"Not a valid module name: {module!r}"), ERR_VALIDATION)
        return self._run({"kind": "module", "module": module, "argv": list(argv), "cwd": None, "source": f"import {module.split('.')[0]}"}, timeout=timeout)

    def run_code(self, code: str, argv: Sequence[str] = (), filename: str = "<string>", timeout: float | None = None) -> RunResult:
        return self._run({"kind": "code", "code": code, "filename": filename, "argv": list(argv), "cwd": None, "source": code}, timeout=timeout)

    def eval(self, expression: str, timeout: float | None = None) -> Any:  # noqa: A003
        result = self._timed_call("eval", {"expression": expression}, timeout=timeout)
        return result["value"] if result.get("repr") is None else result["repr"]

    def repl_push(self, line: str) -> dict[str, Any]:
        assert self.vendor is not None
        return self.call_sandbox("repl_push", {"line": line, "full": self.vendor.flavor == "full"})

    def set_stdin_lines(self, lines: Sequence[str]) -> None:
        self._evaluate(f"window.edgepy.setStdinLines({json.dumps(list(lines))})", await_promise=False)

    @property
    def loaded_packages(self) -> list[str]:
        return sorted(set(self._loaded))


# ---------------------------------------------------------------------------
# fetch: build the vendor folder on a machine with internet (stdlib only, no pip)


def _fetch_opener() -> urllib.request.OpenerDirector:
    # Honors HTTPS_PROXY etc. (urllib default) and an explicit CA bundle for TLS inspection.
    handlers: list[urllib.request.BaseHandler] = []
    bundle = os.getenv("EDGEPY_CA_BUNDLE")
    if bundle:
        import ssl  # noqa: PLC0415

        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=bundle)))
    return urllib.request.build_opener(*handlers)


# Module-level seam returning a file-like response (tests substitute canned bodies).
def _open_url(request: urllib.request.Request, timeout: float, loopback: bool) -> Any:
    opener = _loopback_opener() if loopback else _fetch_opener()
    return opener.open(request, timeout=timeout)


def _http_get(url: str, timeout: float = 60.0, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with _open_url(req, timeout, loopback=False) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise _tag(RuntimeError(f"GET {url} failed: HTTP {exc.code}"), ERR_FETCH, http_status=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise _tag(
            RuntimeError(f"GET {url} failed: {exc}"),
            ERR_FETCH,
            hint="fetch needs internet access; set HTTPS_PROXY / EDGEPY_CA_BUNDLE behind a corporate proxy.",
        ) from exc


def _http_json(url: str, timeout: float = 60.0) -> Any:
    body = _http_get(url, timeout, headers={"Accept": "application/json"})
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise _tag(RuntimeError(f"{url} returned non-JSON."), ERR_FETCH) from exc


def _progress(label: str, done: int, total: int | None) -> None:
    if total:
        print(f"\redgepy: {label} {done / 1048576:.1f}/{total / 1048576:.1f} MiB ({100 * done // total}%)", end="", file=sys.stderr, flush=True)
    else:
        print(f"\redgepy: {label} {done / 1048576:.1f} MiB", end="", file=sys.stderr, flush=True)


def download_file(url: str, dest: Path, expected_size: int | None = None, sha256: str | None = None, label: str | None = None) -> Path:
    """Stream `url` to `dest` (resuming a .part file), then verify sha256 when known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and (expected_size is None or dest.stat().st_size == expected_size):
        if sha256 is None or sha256_file(dest) == sha256:
            _log(f"already downloaded: {dest}")
            return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": USER_AGENT}
    if have and (expected_size is None or have < expected_size):
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = _open_url(req, 120.0, loopback=False)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:  # range not satisfiable - the partial file is complete or stale
            part.unlink(missing_ok=True)
            return download_file(url, dest, expected_size, sha256, label)
        raise _tag(RuntimeError(f"Download failed: HTTP {exc.code} for {url}"), ERR_FETCH, http_status=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise _tag(RuntimeError(f"Download failed for {url}: {exc}"), ERR_FETCH, hint="Check internet/proxy settings.") from exc
    with resp:
        status = getattr(resp, "status", 200)
        mode = "ab" if status == 206 and have else "wb"
        done = have if mode == "ab" else 0
        total = expected_size
        if total is None:
            length = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
            if length and str(length).isdigit():
                total = int(length) + done
        with open(part, mode) as fh:
            last = 0.0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if time.time() - last > 0.5:
                    _progress(label or dest.name, done, total)
                    last = time.time()
    print("", file=sys.stderr)
    if expected_size is not None and part.stat().st_size != expected_size:
        raise _tag(
            RuntimeError(f"Downloaded size {part.stat().st_size} != expected {expected_size} for {dest.name}."),
            ERR_FETCH,
            hint="Run fetch again; the partial file is resumed.",
        )
    if sha256 is not None:
        actual = sha256_file(part)
        if actual != sha256:
            part.unlink(missing_ok=True)
            raise _tag(RuntimeError(f"sha256 mismatch for {dest.name}: got {actual}, expected {sha256}."), ERR_FETCH)
    if dest.exists():
        dest.unlink()
    part.replace(dest)
    return dest


def release_asset_info(version: str, asset: str) -> tuple[int | None, str | None]:
    """(size, sha256) of a GitHub release asset, or (None, None) when the API is unavailable."""
    try:
        data = _http_json(PYODIDE_RELEASE_API.format(version=version))
    except RuntimeError as exc:
        _log(f"release metadata unavailable ({exc}); downloading without size/hash verification")
        return None, None
    for item in data.get("assets") or []:
        if item.get("name") == asset:
            digest = str(item.get("digest") or "")
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return int(item.get("size") or 0) or None, sha
    raise _tag(
        RuntimeError(f"Release {version} has no asset named {asset}."),
        ERR_FETCH,
        hint="Check the version on https://github.com/pyodide/pyodide/releases.",
    )


def extract_dist(archive: Path, dist_dir: Path) -> int:
    """Stream-extract the flat pyodide/ folder out of the release tarball (basenames only)."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with tarfile.open(archive, mode="r|bz2") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = PurePosixPath(member.name).name
                if not name or not SAFE_BASENAME_RE.match(name):
                    continue
                source = tar.extractfile(member)
                if source is None:
                    continue
                with open(dist_dir / name, "wb") as fh:
                    shutil.copyfileobj(source, fh, 1 << 20)
                count += 1
                if count % 50 == 0:
                    print(f"\redgepy: extracted {count} files", end="", file=sys.stderr, flush=True)
    except (tarfile.TarError, OSError) as exc:
        raise _tag(RuntimeError(f"Cannot extract {archive}: {exc}"), ERR_FETCH, hint="Delete the archive and fetch again.") from exc
    print(f"\redgepy: extracted {count} files into {dist_dir}", file=sys.stderr)
    return count


# -- minimal PEP 508 / PEP 440 handling (no `packaging` dependency) ------------------------

REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
    r"(?:\[(?P<extras>[^\]]*)\])?\s*"
    r"(?P<spec>\(?[^;]*\)?)?\s*"
    r"(?:;\s*(?P<marker>.*))?$"
)
VERSION_RE = re.compile(r"^v?(?P<release>\d+(?:\.\d+)*)(?P<pre>(?:a|b|rc|c|alpha|beta|pre|preview)\d*)?(?P<post>\.post\d+)?(?P<dev>\.dev\d+)?$", re.I)


@dataclass(frozen=True)
class Requirement:
    name: str
    extras: tuple[str, ...]
    specifier: str
    marker: str | None

    @property
    def normalized(self) -> str:
        return normalize_name(self.name)


def parse_requirement(text: str) -> Requirement:
    match = REQUIREMENT_RE.match(text.strip())
    if not match or not match.group("name"):
        raise _tag(ValueError(f"Cannot parse requirement: {text!r}"), ERR_VALIDATION)
    extras = tuple(e.strip().lower() for e in (match.group("extras") or "").split(",") if e.strip())
    spec = (match.group("spec") or "").strip().strip("()").strip()
    marker = (match.group("marker") or "").strip() or None
    return Requirement(match.group("name"), extras, spec, marker)


def version_key(version: str) -> tuple[Any, ...] | None:
    match = VERSION_RE.match(version.strip())
    if not match:
        return None
    release = tuple(int(p) for p in match.group("release").split("."))
    pre = match.group("pre")
    pre_key: tuple[int, int]
    if pre:
        tag = re.match(r"([A-Za-z]+)(\d*)", pre)
        assert tag is not None
        letter = tag.group(1).lower()
        order = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2, "c": 2, "pre": 2, "preview": 2}.get(letter, 0)
        pre_key = (order, int(tag.group(2) or 0))
        is_final = 0
    elif match.group("dev"):
        # PEP 440: 1.0.dev1 < 1.0a1 < 1.0b1 < 1.0rc1 < 1.0
        pre_key = (-1, 0)
        is_final = 0
    else:
        pre_key = (9, 0)
        is_final = 1
    dev = match.group("dev")
    dev_key = int(dev[4:]) if dev else 1 << 30
    post = match.group("post")
    post_key = int(post[5:]) if post else 0
    return release, is_final, pre_key, dev_key, post_key


def is_prerelease(version: str) -> bool:
    match = VERSION_RE.match(version.strip())
    return bool(match and (match.group("pre") or match.group("dev")))


def _release_tuple(version: str) -> tuple[int, ...]:
    key = version_key(version)
    return key[0] if key else ()


def _cmp_release(a: str, b: str) -> int:
    ra, rb = list(_release_tuple(a)), list(_release_tuple(b))
    width = max(len(ra), len(rb))
    ra += [0] * (width - len(ra))
    rb += [0] * (width - len(rb))
    return (ra > rb) - (ra < rb)


def specifier_allows(specifier: str, version: str) -> bool:
    """Best-effort PEP 440 specifier check (==, !=, >=, <=, >, <, ~=, wildcards)."""
    specifier = specifier.strip()
    if not specifier:
        return True
    for clause in specifier.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*(.+)$", clause)
        if not match:
            return True  # unknown operator - do not block
        op, target = match.group(1), match.group(2).strip()
        if target.endswith(".*") and op in ("==", "!="):
            prefix = _release_tuple(target[:-2])
            actual = _release_tuple(version)[: len(prefix)]
            hit = actual == prefix
            if (op == "==") != hit:
                return False
            continue
        if version_key(target) is None or version_key(version) is None:
            hit = version.strip() == target
            if (op in ("==", "===")) != hit and op in ("==", "===", "!="):
                return False
            continue
        cmp = _cmp_release(version, target)
        if cmp == 0:
            # Same release once zero-padded (1.0 == 1.0.0); compare the pre/dev/post tail only.
            ka, kb = version_key(version), version_key(target)
            if ka and kb:
                cmp = (ka[1:] > kb[1:]) - (ka[1:] < kb[1:])
        ok = {
            "==": cmp == 0, "===": cmp == 0, "!=": cmp != 0,
            ">=": cmp >= 0, "<=": cmp <= 0, ">": cmp > 0, "<": cmp < 0,
        }[op] if op != "~=" else (cmp >= 0 and _release_tuple(version)[: max(1, len(_release_tuple(target)) - 1)] == _release_tuple(target)[:-1])
        if not ok:
            return False
    return True


MARKER_CLAUSE_RE = re.compile(
    r"""^\s*(?P<lhs>[A-Za-z_.]+|"[^"]*"|'[^']*')\s*(?P<op>===|==|!=|~=|>=|<=|>|<|not\s+in|in)\s*(?P<rhs>[A-Za-z_.]+|"[^"]*"|'[^']*')\s*$"""
)


def evaluate_marker(marker: str | None, env: dict[str, str], extras: Sequence[str] = ()) -> bool | None:
    """Evaluate a PEP 508 marker against `env`. Returns None for unsupported syntax."""
    if not marker:
        return True
    if "(" in marker or ")" in marker:
        return None
    for or_part in re.split(r"\s+or\s+", marker):
        and_ok = True
        for clause in re.split(r"\s+and\s+", or_part):
            value = _evaluate_clause(clause, env, extras)
            if value is None:
                return None
            and_ok = and_ok and value
        if and_ok:
            return True
    return False


def _marker_operand(token: str, env: dict[str, str]) -> tuple[str, bool] | None:
    token = token.strip()
    if token[:1] in ("'", '"'):
        return token[1:-1], False
    if token in env:
        return env[token], True
    return None


def _evaluate_clause(clause: str, env: dict[str, str], extras: Sequence[str]) -> bool | None:
    match = MARKER_CLAUSE_RE.match(clause)
    if not match:
        return None
    op = re.sub(r"\s+", " ", match.group("op"))
    lhs_token, rhs_token = match.group("lhs").strip(), match.group("rhs").strip()
    if "extra" in (lhs_token, rhs_token):
        other = rhs_token if lhs_token == "extra" else lhs_token
        if other[:1] not in ("'", '"'):
            return None
        hit = normalize_name(other[1:-1]) in {normalize_name(e) for e in extras}
        return hit if op == "==" else (not hit if op == "!=" else None)
    lhs = _marker_operand(lhs_token, env)
    rhs = _marker_operand(rhs_token, env)
    if lhs is None or rhs is None:
        return None
    (lv, l_is_var), (rv, r_is_var) = lhs, rhs
    var = lhs_token if l_is_var else (rhs_token if r_is_var else "")
    if op in ("in", "not in"):
        hit = lv in rv
        return hit if op == "in" else not hit
    if var in ("python_version", "python_full_version", "implementation_version") and version_key(lv) and version_key(rv):
        cmp = _cmp_release(lv, rv)
        return {"==": cmp == 0, "===": cmp == 0, "!=": cmp != 0, ">=": cmp >= 0, "<=": cmp <= 0, ">": cmp > 0, "<": cmp < 0, "~=": cmp >= 0}[op]
    return {"==": lv == rv, "===": lv == rv, "!=": lv != rv, ">=": lv >= rv, "<=": lv <= rv, ">": lv > rv, "<": lv < rv, "~=": lv == rv}[op]


def target_env(python_version: str) -> dict[str, str]:
    # Markers are evaluated for the *sandbox* (Emscripten/wasm32), never the host.
    parts = python_version.split(".")
    return {
        "python_version": ".".join(parts[:2]),
        "python_full_version": python_version,
        "implementation_name": "cpython",
        "implementation_version": python_version,
        "sys_platform": "emscripten",
        "platform_machine": "wasm32",
        "platform_system": "Emscripten",
        "platform_release": "",
        "platform_version": "",
        "platform_python_implementation": "CPython",
        "os_name": "posix",
    }


def wheel_compatible(filename: str, python_version: str) -> bool:
    try:
        wheel = parse_wheel_filename(Path(filename))
    except ValueError:
        return False
    if not wheel.pure:
        return False
    major, minor = python_version.split(".")[:2]
    accepted = {"py3", f"py{major}", f"cp{major}{minor}", f"py{major}{minor}"}
    return any(tag in accepted for tag in wheel.python_tags)


@dataclass
class ResolvedWheel:
    name: str
    version: str
    filename: str
    url: str
    sha256: str | None
    requires: tuple[str, ...] = ()


def _pick_release_file(files: Sequence[dict[str, Any]], python_version: str) -> dict[str, Any] | None:
    for item in files:
        if item.get("packagetype") != "bdist_wheel" or item.get("yanked"):
            continue
        if not wheel_compatible(str(item.get("filename")), python_version):
            continue
        requires_python = item.get("requires_python") or ""
        if requires_python and not specifier_allows(requires_python, python_version):
            continue
        return item
    return None


def resolve_wheels(
    requirements: Sequence[Requirement],
    python_version: str,
    bundled: set[str],
    fetch_json: Callable[[str], Any] = _http_json,
) -> tuple[dict[str, ResolvedWheel], list[dict[str, str]]]:
    """Resolve pure-Python wheels from PyPI (best effort; see README for the subset)."""
    env = target_env(python_version)
    chosen: dict[str, ResolvedWheel] = {}
    unresolved: list[dict[str, str]] = []
    queue: list[Requirement] = list(requirements)
    seen: set[str] = set()
    extras_seen: dict[str, set[str]] = {}

    def enqueue_deps(parent: Requirement, requires: Sequence[str], extras: set[str]) -> None:
        for dep_text in requires:
            try:
                dep = parse_requirement(dep_text)
            except ValueError:
                unresolved.append({"name": dep_text, "reason": "unparseable requirement (use --wheel to add it by hand)"})
                continue
            verdict = evaluate_marker(dep.marker, env, sorted(extras))
            if verdict is None:
                unresolved.append({"name": dep.name, "reason": f"unsupported marker {dep.marker!r} in {parent.name} (use --wheel to add it by hand)"})
                continue
            if verdict:
                queue.append(dep)

    while queue:
        req = queue.pop(0)
        norm = req.normalized
        if norm in bundled:
            continue
        wanted_extras = {normalize_name(e) for e in req.extras}
        if norm in seen:
            # Already resolved (or failed): a second requirement can still add extras or
            # reveal a constraint the chosen version does not satisfy. No backtracking.
            picked_wheel = chosen.get(norm)
            if picked_wheel is not None and req.specifier and not specifier_allows(req.specifier, picked_wheel.version):
                unresolved.append({"name": req.name, "reason": f"conflicting constraint {req.specifier!r}; {picked_wheel.version} was already chosen"})
            new_extras = wanted_extras - extras_seen.get(norm, set())
            if picked_wheel is not None and new_extras:
                extras_seen[norm] |= new_extras
                enqueue_deps(req, picked_wheel.requires, extras_seen[norm])
            continue
        seen.add(norm)
        extras_seen[norm] = set(wanted_extras)
        try:
            data = fetch_json(PYPI_JSON_URL.format(name=norm))
        except RuntimeError as exc:
            unresolved.append({"name": req.name, "reason": str(exc)})
            continue
        releases: dict[str, list[dict[str, Any]]] = data.get("releases") or {}
        versions = [v for v in releases if version_key(v) is not None and specifier_allows(req.specifier, v)]
        pinned = req.specifier.startswith("==")
        if not pinned:
            finals = [v for v in versions if not is_prerelease(v)]
            versions = finals or versions
        versions.sort(key=lambda v: version_key(v) or (), reverse=True)
        picked: tuple[str, dict[str, Any]] | None = None
        for version in versions:
            item = _pick_release_file(releases[version], python_version)
            if item is not None:
                picked = (version, item)
                break
        if picked is None:
            unresolved.append({"name": req.name, "reason": f"no pure-Python wheel for Python {python_version}" + (f" matching {req.specifier}" if req.specifier else "")})
            continue
        version, item = picked
        requires_dist: list[str] = []
        info = data.get("info") or {}
        if str(info.get("version")) == version:
            requires_dist = list(info.get("requires_dist") or [])
        else:
            try:
                detail = fetch_json(PYPI_JSON_URL.format(name=f"{norm}/{version}"))
                requires_dist = list((detail.get("info") or {}).get("requires_dist") or [])
            except RuntimeError as exc:
                _log(f"dependency metadata for {norm}=={version} unavailable: {exc}")
        chosen[norm] = ResolvedWheel(
            name=str(info.get("name") or req.name),
            version=version,
            filename=str(item["filename"]),
            url=str(item["url"]),
            sha256=(item.get("digests") or {}).get("sha256"),
            requires=tuple(requires_dist),
        )
        enqueue_deps(req, requires_dist, extras_seen[norm])
    return chosen, unresolved


def read_requirements_file(path: Path, unresolved: list[dict[str, str]], _seen: set[Path] | None = None) -> list[Requirement]:
    """Parse a requirements file: plain requirements and nested -r includes; every other
    pip directive (-e, -c, --index-url, --hash, ...) is reported in `unresolved`."""
    path = path.resolve()
    seen = _seen if _seen is not None else set()
    if path in seen:
        return []
    seen.add(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _tag(ValueError(f"Cannot read requirements file {path}: {exc}"), ERR_VALIDATION) from exc
    requirements: list[Requirement] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            match = re.match(r"^(?:-r|--requirement)\s+(.+)$", line)
            if match:
                requirements.extend(read_requirements_file(path.parent / match.group(1).strip(), unresolved, seen))
            else:
                unresolved.append({"name": line, "reason": f"pip directive in {path.name} is not supported (use --pkg/--wheel to add it by hand)"})
                print(f"edgepy: ignoring directive in {path.name}: {line}", file=sys.stderr)
            continue
        requirements.append(parse_requirement(line))
    return requirements


def fetch(
    vendor_root: Path,
    version: str | None = None,
    flavor: str = "full",
    packages: Sequence[str] = (),
    requirement_files: Sequence[str] = (),
    wheels: Sequence[str] = (),
    verify_only: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Download the Pyodide distribution and pure-Python wheels into `vendor_root`."""
    if flavor not in ("full", "core"):
        raise _tag(ValueError("--flavor must be full or core"), ERR_VALIDATION)
    version = version or os.getenv("EDGEPY_PYODIDE_VERSION") or DEFAULT_PYODIDE_VERSION
    vendor_root = Path(vendor_root).resolve()
    dist_dir = vendor_root / "pyodide"
    wheels_dir = vendor_root / "wheels"
    manifest_path = vendor_root / MANIFEST_NAME
    if verify_only:
        return verify_vendor(vendor_root)

    asset = f"pyodide-core-{version}.tar.bz2" if flavor == "core" else f"pyodide-{version}.tar.bz2"
    existing = None
    if not force:
        try:
            existing = load_vendor(vendor_root)
        except RuntimeError:
            existing = None
        if existing is not None and existing.pyodide_version != version:
            existing = None
    if existing is not None and (existing.flavor == flavor or flavor == "core"):
        print(f"edgepy: Pyodide {version} ({existing.flavor}) already present in {dist_dir}", file=sys.stderr)
    else:
        size, digest = release_asset_info(version, asset)
        if size:
            print(f"edgepy: downloading {asset} ({size / 1048576:.0f} MiB) - this can take a while", file=sys.stderr)
        archive = download_file(PYODIDE_RELEASE_URL.format(version=version, asset=asset), vendor_root / "downloads" / asset, size, digest, label=asset)
        if dist_dir.is_dir():
            # A different version or flavor is being installed: never mix two distributions.
            shutil.rmtree(dist_dir, ignore_errors=True)
        extract_dist(archive, dist_dir)
    vendor = load_vendor(vendor_root)
    if len(str(dist_dir)) > 120:
        print(f"edgepy: warning - vendor path is long ({len(str(dist_dir))} chars); prefer a short folder like C:\\edgepy", file=sys.stderr)

    integrity = True
    if vendor.flavor == "core":
        integrity = _vendor_micropip(vendor) and integrity
        vendor = load_vendor(vendor_root)

    requirements: list[Requirement] = [parse_requirement(p) for p in packages]
    unresolved: list[dict[str, str]] = []
    for req_file in requirement_files:
        requirements.extend(read_requirements_file(Path(req_file), unresolved))
    resolved: dict[str, ResolvedWheel] = {}
    if requirements:
        bundled = set(vendor.bundled) if vendor.flavor == "full" else {"micropip"}
        resolved, resolver_unresolved = resolve_wheels(requirements, vendor.python_version, bundled)
        unresolved.extend(resolver_unresolved)
    wheels_dir.mkdir(parents=True, exist_ok=True)
    manifest_wheels: list[dict[str, Any]] = list(vendor.manifest.get("wheels") or [])
    by_file = {w.get("file"): w for w in manifest_wheels}
    for norm, wheel in resolved.items():
        for old in wheels_dir.glob("*.whl"):
            try:
                parsed = parse_wheel_filename(old)
            except ValueError:
                continue
            if parsed.normalized == norm and old.name != wheel.filename:
                _log(f"removing superseded wheel {old.name}")
                old.unlink()
                by_file.pop(old.name, None)
        dest = download_file(wheel.url, wheels_dir / wheel.filename, None, wheel.sha256, label=wheel.filename)
        by_file[wheel.filename] = {
            "name": wheel.name, "version": wheel.version, "file": wheel.filename,
            "sha256": wheel.sha256 or sha256_file(dest), "requires": list(wheel.requires),
        }
    for extra in wheels:
        source = str(extra)
        if re.match(r"^https?://", source):
            name = source.rsplit("/", 1)[-1].split("#", 1)[0]
            dest = download_file(source, wheels_dir / name, label=name)
        else:
            src_path = Path(source)
            if not src_path.is_file():
                raise _tag(ValueError(f"--wheel file not found: {src_path}"), ERR_VALIDATION)
            dest = wheels_dir / src_path.name
            shutil.copyfile(src_path, dest)
        parsed = parse_wheel_filename(dest)
        by_file[dest.name] = {"name": parsed.name, "version": parsed.version, "file": dest.name, "sha256": sha256_file(dest), "requires": []}
    # Keep only wheels that still exist on disk.
    manifest_wheels = [w for f, w in sorted(by_file.items()) if f and (wheels_dir / f).is_file()]
    manifest = {
        "tool": "edgepy",
        "pyodide_version": vendor.pyodide_version,
        "python": vendor.python_version,
        "flavor": vendor.flavor,
        "integrity": integrity,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wheels": manifest_wheels,
        "unresolved": unresolved,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "vendor_dir": str(vendor_root),
        "pyodide_version": vendor.pyodide_version,
        "python": vendor.python_version,
        "flavor": vendor.flavor,
        "bundled_packages": len(vendor.bundled_present),
        "wheels": [w["file"] for w in manifest_wheels],
        "unresolved": unresolved,
        "manifest": str(manifest_path),
    }


def _vendor_micropip(vendor: VendorInfo) -> bool:
    """core flavor ships no wheels at all; fetch micropip from PyPI into the dist folder."""
    entry = vendor.bundled.get("micropip")
    if not entry or not entry.get("file_name"):
        return True
    dest = vendor.dist_dir / str(entry["file_name"])
    expected = str(entry.get("sha256") or "")
    if dest.is_file() and (not expected or sha256_file(dest) == expected):
        return True
    version = str(entry.get("version") or "")
    try:
        data = _http_json(PYPI_JSON_URL.format(name=f"micropip/{version}" if version else "micropip"))
    except RuntimeError as exc:
        print(f"edgepy: warning - could not fetch micropip from PyPI ({exc}); --pkg installs will be unavailable", file=sys.stderr)
        return True
    for item in data.get("urls") or []:
        if item.get("packagetype") == "bdist_wheel" and not item.get("yanked"):
            download_file(str(item["url"]), dest, None, (item.get("digests") or {}).get("sha256"), label=dest.name)
            break
    else:
        print("edgepy: warning - no micropip wheel found on PyPI", file=sys.stderr)
        return True
    if expected and sha256_file(dest) != expected:
        print("edgepy: note - PyPI micropip wheel differs from the lockfile hash; integrity checks are disabled for it", file=sys.stderr)
        return False
    return True


def verify_vendor(vendor_root: Path) -> dict[str, Any]:
    vendor = load_vendor(vendor_root)
    problems: list[str] = []
    for entry in vendor.manifest.get("wheels") or []:
        path = vendor.wheels_dir / str(entry.get("file"))
        if not path.is_file():
            problems.append(f"missing wheel {path.name}")
        elif entry.get("sha256") and sha256_file(path) != entry["sha256"]:
            problems.append(f"sha256 mismatch for {path.name}")
    for name in DIST_REQUIRED_FILES:
        if not (vendor.dist_dir / name).is_file():
            problems.append(f"missing {name}")
    return {
        "vendor_dir": str(vendor_root),
        "pyodide_version": vendor.pyodide_version,
        "flavor": vendor.flavor,
        "wheels": [w.path.name for w in vendor.wheels],
        "bundled_present": len(vendor.bundled_present),
        "bundled_total": len(vendor.bundled),
        "ok": not problems,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# doctor / packages / clean


def _check(checks: list[dict[str, Any]], cid: str, status: str, detail: str, hint: str | None = None) -> None:
    checks.append({"id": cid, "status": status, "detail": detail, "hint": hint})


def doctor(vendor_dir: str | os.PathLike[str] | None = None, edge_path: str | os.PathLike[str] | None = None, live: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    exe: Path | None = None
    try:
        exe = find_edge(edge_path)
        _check(checks, "edge_found", "ok", str(exe))
        version = edge_version_from_path(exe)
        if version:
            major = int(version.split(".")[0])
            _check(checks, "edge_version", "ok" if major >= 137 else "warn", version,
                   None if major >= 137 else "Edge >= 137 is needed for JSPI (asyncio.run / time.sleep stack switching).")
        else:
            _check(checks, "edge_version", "warn", "unknown")
    except RuntimeError as exc:
        _check(checks, "edge_found", "fail", str(exc), getattr(exc, "hint", None))
    policies = read_policies()
    if not policies:
        _check(checks, "policies", "ok", "no Edge policies configured")
    for name, info in policies.items():
        _check(checks, f"policy_{name}", info["status"], f"{name}={info['value']!r} ({info['hive']}): {info['label']}")
    try:
        run_dir = _make_run_dir()
        _check(checks, "run_dir_writable", "ok", str(run_dir))
        _rmtree_retry(run_dir)
    except RuntimeError as exc:
        _check(checks, "run_dir_writable", "fail", str(exc), getattr(exc, "hint", None))
    root = _vendor_dir(vendor_dir)
    try:
        vendor = load_vendor(root)
        _check(checks, "vendor_present", "ok", f"{root} (Pyodide {vendor.pyodide_version}, Python {vendor.python_version})")
        _check(checks, "vendor_flavor", "ok" if vendor.flavor == "full" else "warn",
               f"{vendor.flavor}: {len(vendor.bundled_present)}/{len(vendor.bundled)} bundled wheels present",
               None if vendor.flavor == "full" else "core flavor cannot load numpy/pandas; run `edgepy fetch --flavor full`.")
        _check(checks, "vendor_wheels", "ok", f"{len(vendor.wheels)} pure-Python wheels in {vendor.wheels_dir}")
        _check(checks, "vendor_manifest", "ok" if vendor.manifest else "warn",
               "manifest present" if vendor.manifest else "no edgepy-manifest.json (hash fragments unavailable)")
    except RuntimeError as exc:
        _check(checks, "vendor_present", "fail", str(exc), getattr(exc, "hint", None))
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            _check(checks, "port_bind", "ok", f"127.0.0.1:{sock.getsockname()[1]}")
    except OSError as exc:
        _check(checks, "port_bind", "fail", str(exc))
    import mimetypes  # noqa: PLC0415

    shadowed = []
    for ext, expected in ((".js", ("text/javascript", "application/javascript")), (".wasm", ("application/wasm",)), (".zip", ("application/zip",))):
        guessed = mimetypes.guess_type("x" + ext)[0]
        if guessed not in expected:
            shadowed.append(f"{ext}->{guessed}")
    _check(checks, "mimetypes", "ok", "server pins its own Content-Type table" + (f"; registry shadows {', '.join(shadowed)}" if shadowed else ""))
    proxies = {k: v for k, v in os.environ.items() if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
    _check(checks, "proxy_env", "ok", "loopback traffic bypasses proxies" + (f" (env: {', '.join(proxies)})" if proxies else ""))
    _check(checks, "python", "ok", f"{sys.version.split()[0]} at {sys.executable}")

    if live:
        try:
            with EdgeRuntime(vendor_dir=vendor_dir, headless=True, stdin="prompt", stdin_reader=lambda: "probe",
                             on_stdout=None, on_stderr=None, edge_path=edge_path, timeout=60) as rt:
                _check(checks, "live_boot", "ok", f"booted in {rt.boot_seconds:.1f}s; Edge {rt.edge_version}; Pyodide {rt.pyodide_version}")
                _check(checks, "live_jspi", "ok" if rt.jspi else "warn", f"stack switching available: {rt.jspi}",
                       None if rt.jspi else "asyncio.run()/time.sleep stack switching unavailable in this Edge.")
                res = rt.run_code("print(input())")
                _check(checks, "live_stdin_prompt", "ok" if res.stdout.strip() == "probe" else "warn",
                       f"input() round-trip via prompt(): {res.stdout.strip()!r}", None if res.stdout.strip() == "probe" else "Use --stdin none or piped stdin.")
                res = rt.run_code("import sys; sys.exit(7)")
                _check(checks, "live_exit_code", "ok" if res.exit_code == 7 else "fail", f"exit code {res.exit_code}")
                pid = rt.edge.pid if rt.edge else 0
            alive = _pid_alive(pid) if pid else False
            _check(checks, "live_teardown", "ok" if not alive else "warn", "Edge exited cleanly" if not alive else f"msedge pid {pid} still alive",
                   None if not alive else "Run `edgepy clean`.")
        except RuntimeError as exc:
            # Distinct id when boot itself succeeded but a later probe failed, so a consumer
            # keying rows by id never sees two live_boot entries.
            booted = any(c["id"] == "live_boot" for c in checks)
            _check(checks, "live_probe" if booted else "live_boot", "fail", str(exc), getattr(exc, "hint", None))
    failed = [c["id"] for c in checks if c["status"] == "fail"]
    return {"ok": not failed, "failed": failed, "checks": checks}


def list_packages(vendor_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    vendor = load_vendor(_vendor_dir(vendor_dir))
    return {
        "vendor_dir": str(vendor.root),
        "pyodide_version": vendor.pyodide_version,
        "python": vendor.python_version,
        "flavor": vendor.flavor,
        "bundled": sorted(vendor.bundled_present),
        "bundled_missing": sorted(set(vendor.bundled) - vendor.bundled_present) if vendor.flavor == "full" else [],
        "wheels": [{"name": w.name, "version": w.version, "file": w.path.name} for w in vendor.wheels],
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows():
        try:
            import ctypes  # noqa: PLC0415

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def clean_runs() -> dict[str, Any]:
    root = _run_root()
    removed: list[str] = []
    kept: list[str] = []
    killed: list[int] = []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            owner = 0
            try:
                owner = int((json.loads((entry / "owner.json").read_text(encoding="utf-8")) or {}).get("pid") or 0)
            except (OSError, ValueError):
                owner = 0
            if owner and owner != os.getpid() and _pid_alive(owner):
                kept.append(str(entry))
                continue
            if _is_windows():
                killed.extend(_kill_edge_using(entry))
            if _rmtree_retry(entry, attempts=3):
                removed.append(str(entry))
            else:
                kept.append(str(entry))
    return {"run_root": str(root), "removed": removed, "kept": kept, "killed_pids": killed}


def _kill_edge_using(run_dir: Path) -> list[int]:
    # Orphaned msedge.exe processes launched with this run dir (best effort, PowerShell CIM).
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{run_dir.name}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = [int(p) for p in out.split() if p.strip().isdigit()]
    for pid in pids:
        _kill_tree(pid)
    return pids


# ---------------------------------------------------------------------------
# CLI

# Seam so tests can inject a fake runtime into the command handlers.
_runtime_factory: Callable[..., EdgeRuntime] = EdgeRuntime


def _parse_mounts(specs: Sequence[str] | None) -> list[tuple[Path, str | None]]:
    mounts: list[tuple[Path, str | None]] = []
    for spec in specs or []:
        directory, sep, name = spec.partition("=")
        path = Path(directory).expanduser()
        if not path.is_dir():
            raise _tag(ValueError(f"--mount directory not found: {path}"), ERR_VALIDATION)
        mounts.append((path.resolve(), name.strip() if sep else None))
    return mounts


def _run_envelope(result: RunResult, rt: EdgeRuntime) -> dict[str, Any]:
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "truncated": result.truncated,
        "duration_s": round(result.duration_s, 3),
        "packages_loaded": result.packages_loaded,
        "pyodide_version": rt.pyodide_version,
        "edge_version": rt.edge_version,
    }


def _wait_keep_open(rt: EdgeRuntime) -> None:
    print("edgepy: Edge left open (--keep-open); press Enter here or close the window to finish.", file=sys.stderr, flush=True)
    proc = rt.edge.proc if rt.edge else None
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            sys.stdin.readline()
            return
    except (AttributeError, ValueError):
        pass
    while proc is not None and proc.poll() is None:
        _sleep(0.5)


def _read_stdin_text() -> str:
    # Pipes carry UTF-8 (like the files run_file reads), not the console code page.
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # e.g. tests patching sys.stdin with io.StringIO
        return sys.stdin.read()
    return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace").read()


def _stdin_mode_for(args: argparse.Namespace, consumed_stdin: bool) -> str:
    if consumed_stdin:
        return "none"
    return args.stdin


def _cmd_run(args: argparse.Namespace) -> int:
    target = list(args.target or [])
    if args.module and args.code is not None:
        raise _tag(ValueError("Use either -m MODULE or -c CODE, not both."), ERR_VALIDATION)
    module: str | None = None
    code: str | None = None
    script: Path | None = None
    script_args: list[str] = []
    consumed_stdin = False
    if args.module:
        module, script_args = args.module, target
    elif args.code is not None:
        code, script_args = args.code, target
    elif target:
        first, script_args = target[0], target[1:]
        if first == "-":
            code = _read_stdin_text()
            consumed_stdin = True
        else:
            script = Path(first)
            if not script.is_file():
                raise _tag(ValueError(f"Script not found: {script}"), ERR_VALIDATION)
    else:
        raise _tag(ValueError("Nothing to run: give a script path, -m MODULE, -c CODE, or - for stdin."), ERR_VALIDATION)

    mounts = _parse_mounts(args.mount)
    show = args.show or args.devtools or _env_flag("EDGEPY_SHOW")
    if args.timeout is not None and args.timeout < 0:
        raise _tag(ValueError("--timeout must be a number of seconds >= 0 (0 disables the timeout)."), ERR_VALIDATION)
    timeout = args.timeout if args.timeout is not None else _default_timeout()
    quiet = bool(args.json)
    rt = _runtime_factory(
        vendor_dir=args.vendor_dir,
        headless=not show,
        devtools=bool(args.devtools),
        timeout=timeout,
        stdin=_stdin_mode_for(args, consumed_stdin),
        on_stdout=None if quiet else _write_stdout,
        on_stderr=None if quiet else _write_stderr,
        edge_path=args.edge_path,
        window_size=args.window_size or DEFAULT_WINDOW_SIZE,
    )
    with rt:
        if rt.stdin_mode == "lines" and not consumed_stdin:
            try:
                piped = sys.stdin is not None and not sys.stdin.isatty()
            except (AttributeError, ValueError):
                piped = False
            if piped:
                # Pre-reading blocks until the writer closes the pipe; say so, because a
                # parent that keeps stdin open would otherwise look like a hang.
                print("edgepy: reading stdin until EOF (--stdin lines)", file=sys.stderr, flush=True)
                rt.set_stdin_lines(_read_stdin_text().splitlines(keepends=True))
        for directory, name in mounts:
            rt.mount(directory, name)
        if args.pkg:
            assert rt.vendor is not None
            bundled, wheels = split_packages(rt.vendor, args.pkg)
            if bundled:
                rt.load_packages(bundled)
            if wheels:
                rt.install(wheels)
        try:
            if module:
                if not args.no_mount:
                    rt.mount(Path.cwd())
                result = rt.run_module(module, script_args)
            elif code is not None:
                result = rt.run_code(code, script_args, filename="<stdin>" if consumed_stdin else "<string>")
            else:
                assert script is not None
                result = rt.run_file(script, script_args, mount_parent=not args.no_mount)
        except RuntimeError as exc:
            if quiet and getattr(exc, "error_class", None) == ERR_TIMEOUT:
                # Still hand back what the script printed before the deadline.
                partial = RunResult(
                    exit_code=EXIT_TIMEOUT,
                    stdout=getattr(exc, "stdout", ""),
                    stderr=getattr(exc, "stderr", ""),
                    duration_s=float(getattr(exc, "duration_s", 0.0)),
                    packages_loaded=rt.loaded_packages,
                )
                print(json.dumps(_run_envelope(partial, rt), indent=2, ensure_ascii=False))
            raise
        if args.keep_open:
            _wait_keep_open(rt)
        if quiet:
            print(json.dumps(_run_envelope(result, rt), indent=2, ensure_ascii=False))
    return result.exit_code


def _cmd_repl(args: argparse.Namespace) -> int:
    mounts = _parse_mounts(args.mount)
    show = args.show or args.devtools or _env_flag("EDGEPY_SHOW")
    rt = _runtime_factory(
        vendor_dir=args.vendor_dir,
        headless=not show,
        devtools=bool(args.devtools),
        timeout=None,
        stdin=args.stdin,
        edge_path=args.edge_path,
        window_size=args.window_size or DEFAULT_WINDOW_SIZE,
    )
    with rt:
        for directory, name in mounts:
            rt.mount(directory, name)
        if not args.no_mount:
            rt.mount(Path.cwd())
        if args.pkg:
            assert rt.vendor is not None
            bundled, wheels = split_packages(rt.vendor, args.pkg)
            if bundled:
                rt.load_packages(bundled)
            if wheels:
                rt.install(wheels)
        return repl_loop(rt)


def repl_loop(rt: EdgeRuntime, read_line: Callable[[str], str] = input) -> int:
    print(f"Python {rt.python_version} (Pyodide {rt.pyodide_version}) inside Edge {rt.edge_version or ''} - edgepy REPL", file=sys.stderr)
    print("Ctrl-D / exit() to leave. Ctrl-C while code runs restarts the interpreter.", file=sys.stderr, flush=True)
    prompt = ">>> "
    while True:
        try:
            line = read_line(prompt)
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt")
            prompt = ">>> "
            continue
        try:
            result = rt.repl_push(line.rstrip("\r\n"))
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt - restarting the sandbox (state lost)", file=sys.stderr, flush=True)
            rt.restart()
            prompt = ">>> "
            continue
        if result.get("exit_code") is not None:
            return int(result["exit_code"])
        if result.get("error"):
            sys.stderr.write(str(result["error"]))
            if not str(result["error"]).endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()
        prompt = "... " if result.get("status") == "incomplete" else ">>> "


def _cmd_fetch(args: argparse.Namespace) -> Any:
    return fetch(
        vendor_root=_vendor_dir(args.vendor_dir),
        version=args.pyodide_version,
        flavor=args.flavor,
        packages=args.pkg or [],
        requirement_files=args.requirements or [],
        wheels=args.wheel or [],
        verify_only=bool(args.verify_only),
        force=bool(args.force),
    )


def _cmd_doctor(args: argparse.Namespace) -> Any:
    return doctor(vendor_dir=args.vendor_dir, edge_path=args.edge_path, live=bool(args.live))


def _cmd_packages(args: argparse.Namespace) -> Any:
    return list_packages(args.vendor_dir)


def _cmd_clean(_args: argparse.Namespace) -> Any:
    return clean_runs()


_EPILOG = """\
examples:
  edgepy run script.py -- --flag value        run a file (its folder is mounted; sys.argv set)
  edgepy run -m mylib.cli -- --name Rudy         run a module from the (auto-mounted) cwd
  edgepy run -c "import sys; print(sys.version)"
  edgepy run --pkg numpy --pkg tabulate analysis.py
  echo hello | edgepy run -c "print(input())"  piped stdin feeds input()
  edgepy repl                                   interactive REPL in the sandbox
  edgepy run --devtools --keep-open script.py   visible Edge + DevTools for inspection
  edgepy fetch --flavor full --pkg tabulate     (online) build vendor/ with Pyodide + wheels
  edgepy doctor --live                          preflight, including a real headless boot

environment (real env vars, read lazily):
  EDGEPY_VENDOR_DIR   EDGEPY_EDGE_PATH   EDGEPY_RUN_DIR   EDGEPY_TIMEOUT_SECONDS
  EDGEPY_SHOW         EDGEPY_PYODIDE_VERSION   EDGEPY_CA_BUNDLE   HTTPS_PROXY (fetch only)

output contract:
  run/repl: the script's stdout and stderr pass through unchanged and its exit code is
  returned verbatim; --json prints one envelope instead. fetch/doctor/packages/clean print
  JSON. Tool failures print {"error": {"class", "http_status", "message", "hint"}} on
  stderr: exit 2 = fix the call, exit 1 = fix the environment (Edge, policies, vendor),
  124 = --timeout hit, 130 = interrupted.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edgepy",
        description="Run Python inside Microsoft Edge (Pyodide) - no pip, no venv, fully offline.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", action="store_true", help="diagnostics to stderr")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", action="store_true", help="diagnostics to stderr")
    common.add_argument("--vendor-dir", help="folder holding pyodide/ and wheels/ (default: EDGEPY_VENDOR_DIR or <script dir>/vendor)")
    common.add_argument("--edge-path", help="explicit msedge.exe path")

    session = argparse.ArgumentParser(add_help=False)
    session.add_argument("--pkg", action="append", metavar="NAME", help="package to load before running (repeatable)")
    session.add_argument("--mount", action="append", metavar="DIR[=NAME]", help="local folder to expose at /mnt/NAME (repeatable)")
    session.add_argument("--no-mount", action="store_true", help="do not auto-mount the script folder / cwd")
    session.add_argument("--show", action="store_true", help="visible Edge window instead of headless")
    session.add_argument("--devtools", action="store_true", help="visible window with DevTools auto-opened (implies --show)")
    session.add_argument("--stdin", choices=("auto", "prompt", "lines", "none"), default="auto",
                         help="how input() is fed: auto = terminal prompt bridge or piped lines; none = input() raises OSError")
    session.add_argument("--window-size", metavar="W,H", help=f"Edge window size (default {DEFAULT_WINDOW_SIZE})")

    def add(name: str, func: Callable[[argparse.Namespace], Any], help_text: str,
            parents: list[argparse.ArgumentParser], description: str | None = None) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common, *parents], help=help_text,
                           description=description or help_text, formatter_class=argparse.RawDescriptionHelpFormatter)
        p.set_defaults(func=func)
        return p

    p = add("run", _cmd_run, "run a script, module (-m), or code (-c / -) inside the sandbox", [session],
            description="Run Python inside Edge. Arguments after the target (use -- before options) become sys.argv[1:].")
    p.add_argument("target", nargs="*", help="SCRIPT [args...] | - (stdin) | args for -m/-c")
    p.add_argument("-m", dest="module", metavar="MODULE", help="run a module as __main__ (runpy)")
    p.add_argument("-c", dest="code", metavar="CODE", help="run inline code")
    p.add_argument("--timeout", type=float, metavar="SECONDS", help="abort the run after this many seconds (exit 124)")
    p.add_argument("--json", action="store_true", help="print one JSON envelope instead of passing output through")
    p.add_argument("--keep-open", action="store_true", help="leave Edge open after the run until Enter / window closed")

    add("repl", _cmd_repl, "interactive Python prompt running inside the sandbox", [session])

    p = add("fetch", _cmd_fetch, "(online) download Pyodide and pure-Python wheels into the vendor folder", [],
            description="Build the offline vendor folder: the Pyodide distribution plus PyPI wheels resolved for Pyodide's Python.")
    p.add_argument("--pyodide-version", metavar="VERSION", help=f"Pyodide release (default {DEFAULT_PYODIDE_VERSION} or EDGEPY_PYODIDE_VERSION)")
    p.add_argument("--flavor", choices=("full", "core"), default="full", help="full = all bundled packages (~334 MiB); core = stdlib only (~6 MiB)")
    p.add_argument("--pkg", action="append", metavar="NAME[SPEC]", help="PyPI package to vendor (repeatable; e.g. tabulate, pyyaml==6.0.2)")
    p.add_argument("--requirements", action="append", metavar="FILE", help="requirements file(s) to vendor")
    p.add_argument("--wheel", action="append", metavar="PATH|URL", help="wheel file or URL to copy in verbatim (repeatable)")
    p.add_argument("--verify-only", action="store_true", help="check the vendor folder against its manifest and exit")
    p.add_argument("--force", action="store_true", help="re-download and re-extract the distribution")

    p = add("doctor", _cmd_doctor, "preflight checks: Edge, policies, vendor folder, ports (always exit 0)", [])
    p.add_argument("--live", action="store_true", help="also boot a headless sandbox and probe stdin/exit codes/teardown")

    add("packages", _cmd_packages, "list bundled Pyodide packages and vendored wheels", [])
    add("clean", _cmd_clean, "remove stale run folders and orphaned edgepy Edge processes", [])
    return parser


def _emit_error(exc: Exception) -> None:
    default_class = ERR_VALIDATION if isinstance(exc, ValueError) else ERR_CDP
    payload = {
        "error": {
            "class": getattr(exc, "error_class", default_class),
            "http_status": getattr(exc, "http_status", None),
            "message": str(exc),
            "hint": getattr(exc, "hint", None),
        }
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr, flush=True)


def _reconfigure_streams() -> None:
    # Windows consoles/pipes may use a legacy code page; never die on a script's unicode.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    global VERBOSE
    VERBOSE = bool(getattr(args, "verbose", False))
    _reconfigure_streams()
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        print("\nedgepy: interrupted", file=sys.stderr, flush=True)
        return EXIT_INTERRUPT
    except ValueError as exc:
        _emit_error(exc)
        return 2
    except RuntimeError as exc:
        _emit_error(exc)
        return EXIT_TIMEOUT if getattr(exc, "error_class", None) == ERR_TIMEOUT else 1
    if isinstance(result, bool) or not isinstance(result, int):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    return result


if __name__ == "__main__":
    raise SystemExit(main())
