"""Passive, version-pinned evidence capture for Codex app-server JSON-RPC."""

from __future__ import annotations

import hashlib
import copy
import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Mapping

PINNED_VERSION = "0.153.4"
PINNED_SHA256 = "a1cf6360ca71918d5466bc3a32d9f18b7044c9128756d1949e715d277b88c9b6"
PINNED_PROTOCOL_SHA256 = (
    "e8284c5cb8157554a3dd1e035aadbd4325aea501af56887e9c2e12eb1b9b9448"
)
PINNED_PROTOCOL_FILENAME = "codex_app_server_protocol.schemas.json"
ALLOWED_METHODS = frozenset(
    {
        "model/list",
        "thread/start",
        "turn/start",
        "thread/read",
        "thread/compact/start",
        "review/start",
        "turn/steer",
    }
)
MUTATING_METHODS = frozenset(
    {"thread/start", "turn/start", "thread/compact/start", "review/start", "turn/steer"}
)
FORBIDDEN_PARAMETERS = frozenset(
    {
        "approvalPolicy",
        "approvalsReviewer",
        "config",
        "sandbox",
        "sandboxPolicy",
        "cwd",
        "developerInstructions",
        "baseInstructions",
    }
)


@dataclass(frozen=True)
class RuntimePin:
    binary: Path
    version: str = PINNED_VERSION
    sha256: str = PINNED_SHA256


def verify_runtime_pin(pin: RuntimePin, *, timeout: float = 30.0) -> Path:
    """Verify one absolute binary through a shell-free, bounded version probe."""
    if not pin.binary.is_absolute():
        raise ValueError("runtime pin binary must be absolute")
    if (
        pin.version != PINNED_VERSION
        or pin.sha256.removeprefix("sha256:").lower() != PINNED_SHA256
    ):
        raise ValueError("runtime pin must use the approved 0.153.4 binary identity")
    binary = pin.binary.resolve(strict=True)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != PINNED_SHA256:
        raise RuntimeError("app-server binary digest differs from the pinned digest")
    completed = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    actual = completed.stdout.strip().removeprefix("codex-cli ").removeprefix("codex ")
    if actual != PINNED_VERSION:
        raise RuntimeError(
            f"requires codex-cli {PINNED_VERSION}, got {completed.stdout.strip()!r}"
        )
    return binary


def verify_model_catalog(
    reference: Mapping[str, str], pin: RuntimePin, model: str, effort: str
) -> dict:
    """Check an independently captured catalog against this exact runtime and route."""
    path = Path(reference["path"]).resolve(strict=True)
    raw = path.read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != reference.get("digest"):
        raise ValueError("model catalog evidence changed")
    catalog = json.loads(raw)
    if (
        catalog.get("version") != pin.version
        or catalog.get("executable_sha256") != pin.sha256.removeprefix("sha256:")
        or Path(catalog.get("executable", "")).resolve() != pin.binary.resolve()
        or catalog.get("catalog_complete") is not True
    ):
        raise ValueError("model catalog does not bind the pinned executable")
    matches = [
        entry for entry in catalog.get("models", []) if entry.get("model") == model
    ]
    if len(matches) != 1 or effort not in {
        item.get("reasoningEffort")
        for item in matches[0].get("supportedReasoningEfforts", [])
    }:
        raise ValueError(
            "requested model and effort are not advertised by the pinned catalog"
        )
    return {"path": str(path), "digest": reference["digest"]}


@dataclass(frozen=True)
class CaptureFiles:
    outbound: Path
    inbound: Path
    stderr: Path

    def open(self) -> tuple[BinaryIO, BinaryIO, BinaryIO]:
        opened: list[tuple[Path, BinaryIO]] = []
        try:
            for path in (self.outbound, self.inbound, self.stderr):
                opened.append((path, path.open("xb")))
        except BaseException:
            for path, stream in opened:
                stream.close()
                path.unlink(missing_ok=True)
            raise
        return tuple(stream for _, stream in opened)  # type: ignore[return-value]


@dataclass
class Census:
    thread_ids: set[str] = field(default_factory=set)
    turn_ids: set[str] = field(default_factory=set)
    collaboration_edges: set[tuple[str, str]] = field(default_factory=set)
    detached_review_thread_ids: set[str] = field(default_factory=set)
    pending_server_requests: list[dict] = field(default_factory=list)
    partial_census: bool = False
    complete_accounting: bool = False


class AppServerCapture:
    """Observer only: it never approves requests, retries, reconnects, or computes usage."""

    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        capture: CaptureFiles,
        *,
        process: subprocess.Popen[bytes] | None = None,
        runtime_pin: RuntimePin | None = None,
        protocol_schema_version: str | None = None,
        run_context: Mapping[str, object] | None = None,
    ) -> None:
        self._stdin, self._stdout, self._stderr, self._process, self._capture = (
            stdin,
            stdout,
            stderr,
            process,
            capture,
        )
        self._out_file, self._in_file, self._err_file = capture.open()
        self.runtime_pin, self.protocol_schema_version, self.run_context = (
            runtime_pin,
            protocol_schema_version,
            copy.deepcopy(dict(run_context or {})),
        )
        self.pending: dict[int, tuple[str, dict]] = {}
        self.request_records: dict[int, tuple[str, dict]] = {}
        self.responses: dict[int, dict] = {}
        (
            self.census,
            self._next_id,
            self._initialize_succeeded,
            self._initialized_notified,
            self._closed,
            self._blocked,
        ) = Census(), 1, False, False, False, False
        self._known_threads: set[str] = set()
        self._queue: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=256)
        self._readers: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._direct_reader: threading.Thread | None = None
        self._direct_result: list[bytes] = []
        self._cleanup_complete = False
        self.cleanup_errors: list[str] = []
        if process is not None:
            self._start_pumps()

    @classmethod
    def start(
        cls,
        capture: CaptureFiles,
        *,
        authorized_run_context: Mapping[str, object],
        runtime_pin: RuntimePin,
        protocol_schema_version: str,
        protocol_schema_path: Path,
        timeout: float = 30.0,
    ) -> "AppServerCapture":
        required_context = {"run_id", "workspace", "model", "effort"}
        if (
            not required_context <= set(authorized_run_context)
            or protocol_schema_version != PINNED_VERSION
            or any(
                not isinstance(authorized_run_context[key], str)
                or not authorized_run_context[key]
                for key in required_context
            )
        ):
            raise ValueError(
                "explicit run context and protocol schema version are required"
            )
        workspace = Path(str(authorized_run_context["workspace"])).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("run workspace must be a directory")
        if (
            hashlib.sha256(protocol_schema_path.read_bytes()).hexdigest()
            != PINNED_PROTOCOL_SHA256
        ):
            raise ValueError(
                "protocol schema bytes do not match the pinned runtime export"
            )
        for path in (capture.outbound, capture.inbound, capture.stderr):
            if not path.is_absolute() or path.resolve().is_relative_to(workspace):
                raise ValueError(
                    "raw capture paths must be absolute and outside the model workspace"
                )
        binary = verify_runtime_pin(runtime_pin, timeout=timeout)
        process = subprocess.Popen(
            [str(binary), "app-server", "--listen", "stdio://"],
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            raise RuntimeError("app-server did not expose stdio streams")
        try:
            return cls(
                process.stdin,
                process.stdout,
                process.stderr,
                capture,
                process=process,
                runtime_pin=runtime_pin,
                protocol_schema_version=protocol_schema_version,
                run_context=authorized_run_context,
            )
        except BaseException:
            try:
                process.kill()
                process.wait(timeout=5)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            raise

    def _start_pumps(self) -> None:
        for name, stream, target in (
            ("stdout", self._stdout, self._in_file),
            ("stderr", self._stderr, self._err_file),
        ):

            def pump(
                label: str = name,
                source: BinaryIO = stream,
                destination: BinaryIO = target,
            ) -> None:
                try:
                    while True:
                        raw = source.readline()
                        if not raw:
                            if label == "stdout":
                                self._queue.put((label, None), timeout=1)
                            return
                        with self._lock:
                            destination.write(raw)
                            destination.flush()
                        if label == "stdout":
                            try:
                                self._queue.put_nowait((label, raw))
                            except queue.Full:
                                # Continue draining raw output even when semantic
                                # event delivery overflows; completeness is lost.
                                self._partial()
                except BaseException:
                    self._partial()

            reader = threading.Thread(
                target=pump, daemon=True, name=f"app-server-{name}-capture"
            )
            reader.start()
            self._readers.append(reader)

    def close(self) -> None:
        if self._cleanup_complete:
            return
        self._closed = True
        if (
            self.pending
            or self.census.pending_server_requests
            or not self._queue.empty()
        ):
            self._partial()
        errors: list[Exception] = []
        try:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._partial()
                    self._process.kill()
                    self._process.wait(timeout=5)
        except Exception as error:
            errors.append(error)
        finally:
            readers = list(self._readers)
            if self._direct_reader is not None:
                readers.append(self._direct_reader)
            for reader in readers:
                reader.join(timeout=1)
            alive = any(reader.is_alive() for reader in readers)
            # Closing a buffered pipe while its reader holds the lock can block
            # indefinitely. Leave those owned handles for a later close retry.
            streams = [self._stdin, self._out_file]
            if not alive:
                streams.extend(
                    (self._stdout, self._stderr, self._in_file, self._err_file)
                )
            else:
                errors.append(RuntimeError("capture readers did not stop"))
            for stream in streams:
                try:
                    stream.close()
                except Exception as error:
                    errors.append(error)
        if self._process is not None and self._process.poll() is None:
            errors.append(RuntimeError("app-server process did not stop"))
        if errors:
            self._partial()
            self.cleanup_errors.extend(
                f"{type(error).__name__}: {error}" for error in errors
            )
            raise RuntimeError(
                "app-server cleanup failed; owned process or handles may remain"
            ) from errors[0]
        self._cleanup_complete = True

    def initialize(self, client_name: str, client_version: str) -> int:
        if any(method == "initialize" for method, _ in self.request_records.values()):
            raise RuntimeError("initialize request was already sent")
        return self._request(
            "initialize",
            {"clientInfo": {"name": client_name, "version": client_version}},
        )

    def initialized(self) -> None:
        if not self._initialize_succeeded:
            raise RuntimeError(
                "initialize must succeed before initialized notification"
            )
        if self._initialized_notified:
            raise RuntimeError("initialized notification was already sent")
        self._write({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        self._initialized_notified = True

    def request(self, method: str, params: Mapping[str, object]) -> int:
        if method not in ALLOWED_METHODS:
            raise ValueError(f"unsupported app-server method: {method}")
        if not self._initialized_notified:
            raise RuntimeError("initialize handshake is incomplete")
        if self._blocked and method in MUTATING_METHODS:
            raise RuntimeError(
                "server request is pending; dependent mutation is stopped"
            )
        if any(key in params for key in FORBIDDEN_PARAMETERS):
            raise ValueError("policy or sandbox override is not permitted")
        self._validate_run_bound_params(method, params)
        if method == "thread/read" and params.get("includeTurns") is not True:
            raise ValueError("thread/read must request includeTurns=true")
        thread_id = params.get("threadId")
        if method not in {"model/list", "thread/start"} and (
            not isinstance(thread_id, str) or thread_id not in self._known_threads
        ):
            raise ValueError("request thread scope is unknown")
        return self._request(method, dict(params))

    def _validate_run_bound_params(
        self, method: str, params: Mapping[str, object]
    ) -> None:
        if method == "thread/start":
            if params.get("model") != self.run_context.get("model"):
                raise ValueError(
                    "thread/start model must equal the authorized run model"
                )
            return
        if method == "turn/start":
            if params.get("model") != self.run_context.get("model") or params.get(
                "effort"
            ) != self.run_context.get("effort"):
                raise ValueError(
                    "turn/start model and effort must equal the authorized run context"
                )
        if method == "review/start":
            target, delivery = params.get("target"), params.get("delivery")
            if not isinstance(target, Mapping) or target.get("type") not in {
                "uncommittedChanges",
                "baseBranch",
                "commit",
                "custom",
            }:
                raise ValueError("review/start requires a supported target")
            if delivery not in {"inline", "detached"}:
                raise ValueError("review/start requires delivery inline or detached")

    def _request(self, method: str, params: dict) -> int:
        params = copy.deepcopy(params)
        request_id = self._next_id
        self._next_id += 1
        self.pending[request_id] = (method, copy.deepcopy(params))
        self.request_records[request_id] = (method, copy.deepcopy(params))
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        return request_id

    def receive(self, timeout: float = 30.0) -> dict | None:
        if not isinstance(timeout, (float, int)) or not 0 <= timeout <= 60:
            raise ValueError("receive timeout must be between zero and 60 seconds")
        if self._closed:
            self._partial()
            return None
        raw = self._read(timeout)
        if raw is None:
            self._partial()
            return None
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self._partial()
            raise ValueError("app-server emitted invalid JSON-RPC bytes") from None
        if not isinstance(message, dict):
            self._partial()
            raise ValueError("app-server emitted a non-object JSON-RPC frame")
        self._observe(message)
        return message

    def _read(self, timeout: float | None) -> bytes | None:
        if self._process is None:
            if timeout is not None and timeout <= 0:
                return None
            if self._direct_reader is None:
                self._direct_reader = threading.Thread(
                    target=lambda: self._direct_result.append(self._stdout.readline()),
                    daemon=True,
                )
                self._direct_reader.start()
            self._direct_reader.join(timeout)
            if self._direct_reader.is_alive():
                return None
            raw = self._direct_result[0] if self._direct_result else b""
            self._direct_reader = None
            self._direct_result.clear()
            if raw:
                self._in_file.write(raw)
                self._in_file.flush()
            return raw or None
        try:
            label, raw = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return raw if label == "stdout" else None

    def _write(self, message: dict) -> None:
        if self._closed:
            raise RuntimeError("capture is closed")
        raw = (
            json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
            + b"\n"
        )
        self._stdin.write(raw)
        self._stdin.flush()
        self._out_file.write(raw)
        self._out_file.flush()

    def artifact_metadata(self) -> dict:
        """Reference completed capture bytes; this observer never grades them."""
        if not self._cleanup_complete:
            raise RuntimeError(
                "close and finish capture before freezing artifact references"
            )

        def reference(path: Path) -> dict[str, str]:
            return {
                "path": str(path),
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        return {
            "schema_version": "codex-app-server-capture-v1",
            "adapter": "codex-app-server-stdio-v1",
            "runtime": None
            if self.runtime_pin is None
            else {
                "binary": str(self.runtime_pin.binary),
                "version": self.runtime_pin.version,
                "sha256": self.runtime_pin.sha256,
            },
            "protocol_schema_version": self.protocol_schema_version,
            "protocol_schema_digest": "sha256:" + PINNED_PROTOCOL_SHA256,
            "protocol_schema_filename": PINNED_PROTOCOL_FILENAME,
            "run_context": copy.deepcopy(self.run_context),
            "requests": {
                str(key): {"method": method, "params": copy.deepcopy(params)}
                for key, (method, params) in self.request_records.items()
            },
            "responses": copy.deepcopy(self.responses),
            "pending_request_ids": sorted(self.pending),
            "observed_thread_ids": sorted(self.census.thread_ids),
            "observed_turn_ids": sorted(self.census.turn_ids),
            "collaboration_edges": sorted(self.census.collaboration_edges),
            "detached_review_thread_ids": sorted(
                self.census.detached_review_thread_ids
            ),
            "pending_server_requests": copy.deepcopy(
                self.census.pending_server_requests
            ),
            "partial_census": self.census.partial_census,
            "cleanup_errors": list(self.cleanup_errors),
            "host_semantics_verified": False,
            "raw_refs": {
                "outbound": reference(self._capture.outbound),
                "inbound": reference(self._capture.inbound),
                "stderr": reference(self._capture.stderr),
            },
            "complete_accounting": False,
        }

    def _partial(self) -> None:
        self.census.partial_census = True
        self.census.complete_accounting = False

    def _observe(self, message: dict) -> None:
        if message.get("method") == "error":
            # ErrorNotification is asynchronous and has no matching request ID.
            # A willRetry notification cannot prove an attempt was reconciled.
            self._partial()
        if "id" in message and "method" in message:
            self.census.pending_server_requests.append(copy.deepcopy(message))
            self._blocked = True
            self._partial()
            return
        request_id = message.get("id")
        if request_id is not None:
            if (
                type(request_id) is not int
                or request_id not in self.pending
                or request_id in self.responses
            ):
                self._partial()
                return
            method, params = self.pending.pop(request_id)
            self.responses[request_id] = copy.deepcopy(message)
            if "error" in message:
                self._partial()
                return
            result = message.get("result")
            if method == "initialize":
                if not isinstance(result, dict) or not all(
                    isinstance(result.get(key), str) and result[key]
                    for key in (
                        "codexHome",
                        "platformFamily",
                        "platformOs",
                        "userAgent",
                    )
                ):
                    self._partial()
                    return
                self._initialize_succeeded = True
            self._observe_payload(
                result,
                parent_thread=params.get("threadId"),
                review=(method == "review/start"),
                allow_new=(method in {"thread/start", "review/start"}),
            )
        self._observe_payload(message.get("params"), allow_new=False)

    def _observe_payload(
        self,
        payload: object,
        *,
        parent_thread: object = None,
        review: bool = False,
        allow_new: bool = False,
    ) -> None:
        if not isinstance(payload, dict):
            return
        declared_thread = payload.get("threadId")
        nested = payload.get("thread")
        if isinstance(nested, dict):
            declared_thread = nested.get("id", declared_thread)
        if (
            isinstance(declared_thread, str)
            and not allow_new
            and declared_thread not in self._known_threads
        ):
            self._partial()
            return
        if (
            declared_thread is None
            and not allow_new
            and parent_thread not in self._known_threads
        ):
            if any(key in payload for key in ("turn", "turnId", "item", "items")):
                self._partial()
            return
        thread = payload.get("thread")
        if isinstance(thread, dict):
            self._observe_thread(thread, parent_thread, review, allow_new)
        thread_id = payload.get("threadId")
        if isinstance(thread_id, str):
            self._observe_known_thread(thread_id, allow_new)
        review_id = payload.get("reviewThreadId")
        if isinstance(review_id, str):
            self._observe_known_thread(review_id, allow_new)
            if review and isinstance(parent_thread, str) and review_id != parent_thread:
                self.census.detached_review_thread_ids.add(review_id)
                self.census.collaboration_edges.add((parent_thread, review_id))
        self._observe_turn(payload.get("turn"))
        self._observe_items(payload.get("items"))
        self._observe_items(payload.get("item"))
        turn_id = payload.get("turnId")
        if isinstance(turn_id, str):
            self.census.turn_ids.add(turn_id)

    def _observe_thread(
        self,
        thread: Mapping[str, object],
        parent_thread: object,
        review: bool,
        allow_new: bool,
    ) -> None:
        thread_id = thread.get("id")
        if isinstance(thread_id, str):
            self._observe_known_thread(thread_id, allow_new)
            if review and isinstance(parent_thread, str) and thread_id != parent_thread:
                self.census.detached_review_thread_ids.add(thread_id)
                self.census.collaboration_edges.add((parent_thread, thread_id))
        self._observe_turn(thread.get("turns"))

    def _observe_known_thread(self, thread_id: str, allow_new: bool = False) -> None:
        if thread_id not in self._known_threads and not allow_new:
            self._partial()
            return
        self._known_threads.add(thread_id)
        self.census.thread_ids.add(thread_id)

    def _observe_turn(self, turn: object) -> None:
        if isinstance(turn, list):
            for value in turn:
                self._observe_turn(value)
            return
        if not isinstance(turn, dict):
            return
        if isinstance(turn.get("id"), str):
            self.census.turn_ids.add(turn["id"])
        self._observe_items(turn.get("items"))

    def _observe_items(self, items: object) -> None:
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("turnId"), str):
                self.census.turn_ids.add(item["turnId"])
            if item.get("type") == "collabAgentToolCall":
                sender, receivers = (
                    item.get("senderThreadId"),
                    item.get("receiverThreadIds"),
                )
                if isinstance(sender, str) and isinstance(receivers, list):
                    self._observe_known_thread(sender)
                    for receiver in receivers:
                        if isinstance(receiver, str):
                            if sender in self._known_threads:
                                self._observe_known_thread(receiver, allow_new=True)
                                self.census.collaboration_edges.add((sender, receiver))
                            else:
                                self._partial()
            self._observe_turn(item.get("turn"))
