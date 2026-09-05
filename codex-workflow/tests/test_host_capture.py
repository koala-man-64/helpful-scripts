from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.app_server_capture import AppServerCapture, CaptureFiles  # noqa: E402
from benchmark.host_capture import read_capture  # noqa: E402


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _request(identifier: str | int, *, emoji: str = "") -> bytes:
    return (
        f'{{"jsonrpc":"2.0","id":{identifier!r},"method":"thread/start",'
        f'"params":{{"note":"{emoji}"}}}}'.replace("'", '"').encode("utf-8")
    )


def _response(identifier: str | int) -> bytes:
    return f'{{"jsonrpc":"2.0","id":{identifier!r},"result":{{}}}}'.replace(
        "'", '"'
    ).encode("utf-8")


class HostCaptureTests(unittest.TestCase):
    def capture(self, outbound: bytes, inbound: bytes, stderr: bytes = b"stderr\n"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        root, outside_root = base / "artifact", base / "outside"
        root.mkdir()
        outside_root.mkdir()
        paths = {}
        for name, raw in (("outbound", outbound), ("inbound", inbound), ("stderr", stderr)):
            path = root / f"{name}.raw"
            path.write_bytes(raw)
            paths[name] = {"path": str(path), "digest": _digest(raw)}
        return root, outside_root, {"raw_refs": paths}

    def test_retains_exact_line_bytes_offsets_and_decodes_a_fresh_message(self):
        outbound = _request(1, emoji="é") + b"\r\n" + _request("two") + b"\n"
        inbound = _response(1) + b"\r\n" + _response("two")
        root, _, metadata = self.capture(outbound, inbound, b"not json\x00\n")

        evidence = read_capture(metadata, root)

        self.assertEqual([frame.offset for frame in evidence.frames], [0, len(_request(1, emoji="é")) + 2, 0, len(_response(1)) + 2])
        self.assertEqual(evidence.frames[0].raw, _request(1, emoji="é") + b"\r\n")
        decoded = evidence.frames[0].message()
        decoded["params"]["note"] = "changed"
        self.assertEqual(evidence.frames[0].message()["params"]["note"], "é")
        self.assertIn("metadata:complete_accounting_not_true", evidence.partial_reasons)

    def test_rejects_unsealed_paths_digest_corruption_and_symlink_escape(self):
        root, outside_root, metadata = self.capture(
            _request(1) + b"\n", _response(1) + b"\n"
        )
        metadata["raw_refs"]["outbound"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "do not match"):
            read_capture(metadata, root)

        root, outside_root, metadata = self.capture(
            _request(1) + b"\n", _response(1) + b"\n"
        )
        outside = outside_root / "outside.raw"
        outside.write_bytes(_request(1))
        metadata["raw_refs"]["outbound"] = {
            "path": str(outside),
            "digest": _digest(outside.read_bytes()),
        }
        with self.assertRaisesRegex(ValueError, "inside artifact root"):
            read_capture(metadata, root)

        root, outside_root, metadata = self.capture(
            _request(1) + b"\n", _response(1) + b"\n"
        )
        outside = outside_root / "escaped.raw"
        outside.write_bytes(_request(1) + b"\n")
        link = root / "outbound-link.raw"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        metadata["raw_refs"]["outbound"] = {
            "path": str(link),
            "digest": _digest(outside.read_bytes()),
        }
        with self.assertRaisesRegex(ValueError, "inside artifact root"):
            read_capture(metadata, root)

    def test_requires_exact_raw_stream_references(self):
        root, _, metadata = self.capture(_request(1) + b"\n", _response(1) + b"\n")
        del metadata["raw_refs"]["stderr"]
        with self.assertRaisesRegex(ValueError, "exactly outbound, inbound, and stderr"):
            read_capture(metadata, root)

    def test_rejects_malformed_duplicate_nonfinite_and_wrong_shapes(self):
        cases = (
            b'{"jsonrpc":"2.0","id":1,"method":"x"',
            b'{"jsonrpc":"2.0","id":1,"id":2,"method":"x"}',
            b'{"jsonrpc":"2.0","id":1,"method":"x","params":{"n":NaN}}',
            b'{"jsonrpc":"2.0","id":1,"method":"x","params":{"n":1e999}}',
            b'{"jsonrpc":"1.0","id":1,"method":"x"}',
            b'{"jsonrpc":"2.0","id":1,"method":"x","result":{}}',
        )
        for outbound in cases:
            with self.subTest(outbound=outbound):
                root, _, metadata = self.capture(
                    outbound + b"\n", _response(1) + b"\n"
                )
                with self.assertRaises(ValueError):
                    read_capture(metadata, root)

    def test_rejects_duplicate_and_unknown_response_ids(self):
        for outbound, inbound, expected in (
            (_request(1) + b"\n" + _request(1) + b"\n", _response(1) + b"\n", "duplicate outbound"),
            (_request(1) + b"\n", _response(2) + b"\n", "unknown request"),
            (_request(1) + b"\n", _response(1) + b"\n" + _response(1) + b"\n", "duplicate inbound"),
        ):
            with self.subTest(expected=expected):
                root, _, metadata = self.capture(outbound, inbound)
                with self.assertRaisesRegex(ValueError, expected):
                    read_capture(metadata, root)

    def test_records_each_missing_response_as_partial_evidence(self):
        root, _, metadata = self.capture(_request(1) + b"\n", b"")

        evidence = read_capture(metadata, root)

        self.assertIn("inbound:missing_response:1", evidence.partial_reasons)

    def test_records_partial_reasons_without_claiming_host_or_promotion_semantics(self):
        root, _, metadata = self.capture(
            _request(1) + b"\n",
            _response(1)
            + b"\n"
            + b'{"jsonrpc":"2.0","method":"error","params":{"willRetry":true}}\n'
            + b'{"jsonrpc":"2.0","id":"server","method":"approval","params":{}}\n',
        )
        metadata.update(
            {
                "partial_census": True,
                "cleanup_errors": ["reader timeout"],
                "pending_request_ids": [3],
                "pending_server_requests": [{"id": "server"}],
                "host_semantics_verified": True,
            }
        )

        evidence = read_capture(metadata, root)

        self.assertEqual(
            evidence.partial_reasons,
            (
                "inbound:async_error",
                "inbound:server_request",
                "metadata:cleanup_errors",
                "metadata:complete_accounting_not_true",
                "metadata:host_semantics_untrusted",
                "metadata:partial_census",
                "metadata:pending_request_ids",
                "metadata:pending_server_requests",
            ),
        )

    def test_reads_actual_capture_output_with_notification_and_error_response(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        incoming = io.BytesIO(
            b'{"id":1,"result":{"codexHome":"C:/codex","platformFamily":"windows",'
            b'"platformOs":"windows","userAgent":"codex-cli/0.153.4"}}\n'
            b'{"id":2,"error":{"code":-32000,"message":"retry","willRetry":true}}\n'
        )
        capture = AppServerCapture(
            io.BytesIO(),
            incoming,
            io.BytesIO(b"opaque stderr\x00\n"),
            CaptureFiles(root / "outbound.raw", root / "inbound.raw", root / "stderr.raw"),
            run_context={
                "run_id": "run-1",
                "workspace": str(root),
                "model": "gpt-5.6-terra",
                "effort": "medium",
            },
        )
        self.addCleanup(capture.close)

        capture.initialize("benchmark", "1")
        capture.receive()
        capture.initialized()
        capture.request("thread/start", {"model": "gpt-5.6-terra"})
        capture.receive()
        capture.close()

        evidence = read_capture(capture.artifact_metadata(), root)

        self.assertEqual(
            [frame.stream for frame in evidence.frames],
            ["outbound"] * 3 + ["inbound"] * 2,
        )
        outbound = [frame.message() for frame in evidence.frames[:3]]
        self.assertEqual(outbound[0]["id"], 1)
        self.assertEqual(outbound[1]["method"], "initialized")
        self.assertNotIn("id", outbound[1])
        self.assertEqual(outbound[2]["id"], 2)
        self.assertNotIn("jsonrpc", evidence.frames[3].message())
        self.assertIn("inbound:error_response", evidence.partial_reasons)
        self.assertIn("metadata:partial_census", evidence.partial_reasons)


if __name__ == "__main__":
    unittest.main()
