from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.host_census import build_partial_census, producer_digest  # noqa: E402
from benchmark.app_server_capture import AppServerCapture, CaptureFiles, RuntimePin, PINNED_PROTOCOL_SHA256  # noqa: E402
from benchmark.runner import main  # noqa: E402
from test_host_observations import request, response, event, collab, evidence  # noqa: E402


class HostCensusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pins = {"run_set_digest": "sha256:" + "1" * 64, "manifest_digest": "sha256:" + "2" * 64,
                     "producer_digest": producer_digest(), "runtime_digest": "sha256:" + "3" * 64,
                     "protocol_digest": "sha256:" + PINNED_PROTOCOL_SHA256}

    def capture(self, incoming=None, outgoing=None):
        frames = evidence(outgoing or [request(1, "thread/start")],
                          [response(1, thread={"id": "root"}), *(incoming or [])]).frames
        refs = {}
        for stream in ("outbound", "inbound", "stderr"):
            raw = b"".join(f.raw for f in frames if f.stream == stream)
            (self.root / (stream + ".raw")).write_bytes(raw)
            refs[stream] = {"path": stream + ".raw", "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
        metadata = {"schema_version": "codex-app-server-capture-v1", "adapter": "codex-app-server-stdio-v1",
                    "protocol_schema_digest": self.pins["protocol_digest"], "runtime": {"sha256": "3" * 64},
                    "run_context": {"run_id": "run"}, "raw_refs": refs}
        path = self.root / "capture.json"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        return path

    def build(self, source, **changes):
        return build_partial_census(source, self.root, **{
            "run_id": "run", "expected_pins": self.pins, "sealed_at": "2026-09-05T18:00:00Z", **changes})

    def test_partial_census_retains_observed_topology_without_inventing_request_or_terminal(self):
        source = self.capture([collab(), event("turn/completed", turn={"id": "turn", "status": "completed"})])
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        census = self.build(source)
        self.assertEqual([(a["thread_id"], a["role"]) for a in census["attempts"]], [("root", "root"), ("child", "child")])
        self.assertEqual(census["requests"], [])
        self.assertEqual(census["host_events"], [])
        self.assertTrue(all(a["terminal"] is None for a in census["attempts"]))
        self.assertFalse(census["segments"][0]["closed"])
        self.assertFalse(census["reconciliation"]["promotion_eligible"])
        self.assertIn("model_request_attribution_unavailable", census["reconciliation"]["partial_reasons"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_independent_pins_wrong_run_and_calendar_reject(self):
        source = self.capture()
        for name in ("producer_digest", "runtime_digest", "protocol_digest"):
            pins = {**self.pins, name: "sha256:" + "0" * 64}
            with self.subTest(pin=name), self.assertRaises(ValueError):
                self.build(source, expected_pins=pins)
        for kwargs in ({"run_id": "foreign"}, {"sealed_at": "2026-02-30T00:00:00Z"}, {"expected_pins": {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build(source, **kwargs)

    def test_corrupt_raw_and_duplicate_metadata_keys_reject(self):
        source = self.capture()
        (self.root / "inbound.raw").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.build(source)
        source = self.capture()
        source.write_bytes(b'{"runtime":{},' + source.read_bytes()[1:])
        with self.assertRaises(ValueError):
            self.build(source)

    def test_pending_rpc_retained_without_model_request_invention(self):
        census = self.build(self.capture(outgoing=[request(1, "thread/start"), request(2, "thread/read", threadId="root")]))
        self.assertEqual(census["reconciliation"]["pending_rpc_ids"], ["2"])
        self.assertEqual(census["requests"], [])

    def test_contradictory_terminals_reject_instead_of_hiding_failed_work(self):
        source = self.capture([event("turn/completed", turn={"id": "turn", "status": "failed"}),
                               event("turn/completed", turn={"id": "turn", "status": "completed"})])
        with self.assertRaisesRegex(ValueError, "contradictory terminal"):
            self.build(source)

    def test_numeric_and_text_pending_rpc_collision_rejects(self):
        source = self.capture(outgoing=[request(1, "thread/start"), request(2, "thread/read"),
                                        request("2", "thread/read")])
        with self.assertRaisesRegex(ValueError, "RPC IDs collide"):
            self.build(source)

    def test_actual_capture_metadata_and_streams_produce_partial_census(self):
        for digest in ("3" * 64, "sha256:" + "3" * 64):
            with self.subTest(runtime_digest=digest):
                incoming = io.BytesIO(
                    b'{"id":1,"result":{"codexHome":"C:/codex","platformFamily":"windows",'
                    b'"platformOs":"windows","userAgent":"codex-cli/0.153.4"}}\n'
                    + json.dumps(response(2, thread={"id": "root"})).encode() + b"\n")
                # Each case uses new caller-owned streams; no actual model process.
                directory = self.root / ("prefixed" if digest.startswith("sha") else "raw")
                directory.mkdir()
                capture = AppServerCapture(io.BytesIO(), incoming, io.BytesIO(),
                                           CaptureFiles(directory / "outbound.raw", directory / "inbound.raw",
                                                        directory / "stderr.raw"),
                                           runtime_pin=RuntimePin(self.root / "fixture.exe", sha256=digest),
                                           protocol_schema_version="0.153.4", run_context={"run_id": "run"})
                self.addCleanup(capture.close)
                capture.initialize("benchmark", "1")
                capture.receive()
                capture.initialized()
                capture.request("thread/start", {})
                capture.receive()
                capture.close()
                path = directory / "capture.json"
                path.write_text(json.dumps(capture.artifact_metadata()), encoding="utf-8")
                value = build_partial_census(path, directory, run_id="run", expected_pins=self.pins,
                                             sealed_at="2026-09-05T18:00:00Z")
                self.assertEqual(value["attempts"][0]["thread_id"], "root")
                self.assertFalse(value["reconciliation"]["complete_accounting"])

    def test_cli_writes_once_and_preserves_inputs(self):
        source = self.capture()
        pins_path = self.root / "pins.json"
        pins_path.write_text(json.dumps(self.pins), encoding="utf-8")
        target = self.root / "census.json"
        argv = ["runner", "produce-host-census", "--capture", str(source), "--artifact-root", str(self.root),
                "--run-id", "run", "--pins", str(pins_path), "--sealed-at", "2026-09-05T18:00:00Z",
                "--output", str(target)]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        before = target.read_bytes()
        with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main()
        self.assertEqual(before, target.read_bytes())

    @unittest.skipUnless(os.environ.get("CODEX_WORKFLOW_HOOKS_SOURCE"), "set hooks source for independent diagnostic replay")
    def test_pinned_central_verifier_retains_partial_and_rejects_forgery(self):
        # Source archive pins the independent consumer despite its active worktree.
        source = os.environ["CODEX_WORKFLOW_HOOKS_SOURCE"]
        archive = subprocess.check_output(["git", "-C", source, "archive", "--format=tar",
                                           "d6e85a7b533ca51afd9e4f21e5f39b11d521cca5"])
        consumer = self.root / "consumer"
        consumer.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as members:
            for member in members.getmembers():
                destination = (consumer / member.name).resolve()
                if not destination.is_relative_to(consumer) or not (member.isfile() or member.isdir()):
                    self.fail("unsafe independent consumer archive")
                members.extract(member, consumer, filter="data")
        value = self.build(self.capture([collab()]))
        census_path = self.root / "census.json"
        census_path.write_text(json.dumps(value), encoding="utf-8")
        pins_path = self.root / "pins.json"
        pins_path.write_text(json.dumps(self.pins), encoding="utf-8")
        program = '''
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "src"))
from codex_workflow_hooks.census import verify_census
root=Path(sys.argv[2]); contract=Path(sys.argv[3])
raw=(root/"census.json").read_bytes()
reference={"path":"census.json","digest":"sha256:"+hashlib.sha256(raw).hexdigest()}
pins=json.loads((root/"pins.json").read_bytes())
result=verify_census(reference,root,contract_root=contract,
 contract_manifest_digest="sha256:d0b9c38639d7b2dc630b063f7c9c1a375e9e5a081b2aa0fdd5eb2d082a264789",
 schema_digest="sha256:28854a8f019646b3cd51294c5b634a0fdddc375cd354c7b71b72edf009334302",expected_pins=pins)
print(json.dumps(result))
'''
        def verify():
            result = subprocess.check_output([sys.executable, "-B", "-c", program, str(consumer),
                                              str(self.root), str(ROOT / "benchmark/contracts/benchmark-host-census-v1")],
                                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            return json.loads(result)
        verified = verify()
        self.assertEqual(verified["disposition"], "partial", verified)
        self.assertFalse(verified["promotion_eligible"])
        self.assertEqual(verified["attempt_count"], 2)
        value["producer_digest"] = "sha256:" + "0" * 64
        census_path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(verify()["disposition"], "reject")


if __name__ == "__main__":
    unittest.main()
