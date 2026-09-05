from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark import host_protocol  # noqa: E402


SOURCE_EXPORT = Path(
    "C:/Users/rdpro/Documents/Codex/2026-09-04/repo/work/"
    "codex-app-server-schema-0.153.4/codex_app_server_protocol.schemas.json"
)


def turn(*, status: str = "completed") -> dict:
    return {"id": "turn-1", "items": [], "status": status}


def thread() -> dict:
    return {
        "cliVersion": "0.153.4",
        "createdAt": 1,
        "cwd": "C:/work",
        "ephemeral": False,
        "id": "thread-1",
        "modelProvider": "openai",
        "preview": "hello",
        "projectId": None,
        "sessionId": "session-1",
        "source": "cli",
        "status": {"type": "idle"},
        "turns": [],
        "updatedAt": 2,
    }


def fixtures() -> dict[str, dict]:
    return {
        "ThreadStartParams": {},
        "ReviewStartParams": {"threadId": "thread-1", "target": {"type": "uncommittedChanges"}},
        "ThreadStartResponse": {
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "cwd": "C:/work",
            "model": "gpt-5.6-terra",
            "modelProvider": "openai",
            "sandbox": {"type": "readOnly"},
            "thread": thread(),
        },
        "ReviewStartResponse": {"reviewThreadId": "review-1", "turn": turn()},
        "TurnStartedNotification": {
            "threadId": "thread-1",
            "turn": turn(status="inProgress"),
        },
        "TurnCompletedNotification": {"threadId": "thread-1", "turn": turn()},
        "ItemCompletedNotification": {
            "completedAtMs": 3,
            "item": {"id": "item-1", "text": "done", "type": "agentMessage"},
            "threadId": "thread-1",
            "turnId": "turn-1",
        },
    }


class HostProtocolTests(unittest.TestCase):
    def test_validates_realistic_provider_payloads(self):
        for schema_name, value in fixtures().items():
            with self.subTest(schema_name=schema_name):
                self.assertIsNone(host_protocol.validate_host_payload(schema_name, value))

    def test_rejects_omission_of_each_required_top_level_field(self):
        required = {
            "ReviewStartParams": ("threadId", "target"),
            "ThreadStartResponse": (
                "approvalPolicy",
                "approvalsReviewer",
                "cwd",
                "model",
                "modelProvider",
                "sandbox",
                "thread",
            ),
            "ReviewStartResponse": ("reviewThreadId", "turn"),
            "TurnStartedNotification": ("threadId", "turn"),
            "TurnCompletedNotification": ("threadId", "turn"),
            "ItemCompletedNotification": (
                "completedAtMs",
                "item",
                "threadId",
                "turnId",
            ),
        }
        for schema_name, fields in required.items():
            for field in fields:
                with self.subTest(schema_name=schema_name, field=field):
                    value = copy.deepcopy(fixtures()[schema_name])
                    del value[field]
                    with self.assertRaisesRegex(ValueError, "is invalid"):
                        host_protocol.validate_host_payload(schema_name, value)

    def test_rejects_wrong_nested_provider_shapes(self):
        invalid_turn = {"threadId": "thread-1", "turn": {"id": "turn-1", "items": "no", "status": "completed"}}
        invalid_item = {
            "completedAtMs": 3,
            "item": {"id": "item-1", "text": 1, "type": "agentMessage"},
            "threadId": "thread-1",
            "turnId": "turn-1",
        }
        for schema_name, value in (
            ("TurnCompletedNotification", invalid_turn),
            ("ItemCompletedNotification", invalid_item),
        ):
            with self.subTest(schema_name=schema_name):
                with self.assertRaisesRegex(ValueError, "is invalid"):
                    host_protocol.validate_host_payload(schema_name, value)

    def test_rejects_unknown_schema_and_tampered_projection(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            host_protocol.validate_host_payload("Unknown", {})
        with tempfile.TemporaryDirectory() as temporary:
            tampered = Path(temporary) / "projection.json"
            tampered.write_bytes(host_protocol.PROJECTION_PATH.read_bytes() + b" ")
            with patch.object(host_protocol, "PROJECTION_PATH", tampered):
                with self.assertRaisesRegex(ValueError, "pinned digest"):
                    host_protocol.validate_host_payload("ReviewStartResponse", fixtures()["ReviewStartResponse"])

    def test_rejects_remote_references_even_with_a_matching_test_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            projection = json.loads(host_protocol.PROJECTION_PATH.read_text(encoding="utf-8"))
            projection["definitions"]["v2"]["ReviewStartResponse"] = {
                "$ref": "https://example.invalid/schema.json"
            }
            path = Path(temporary) / "projection.json"
            raw = json.dumps(projection, sort_keys=True).encode("utf-8")
            path.write_bytes(raw)
            with (
                patch.object(host_protocol, "PROJECTION_PATH", path),
                patch.object(
                    host_protocol,
                    "PINNED_PROJECTION_SHA256",
                    hashlib.sha256(raw).hexdigest(),
                ),
                self.assertRaisesRegex(ValueError, "remote \\$ref"),
            ):
                host_protocol.validate_host_payload(
                    "ReviewStartResponse", fixtures()["ReviewStartResponse"]
                )

    def test_explains_the_optional_dependency_when_jsonschema_is_unavailable(self):
        original_import = builtins.__import__

        def reject_jsonschema(name, *args, **kwargs):
            if name == "jsonschema":
                raise ImportError("test dependency absent")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_jsonschema):
            with self.assertRaisesRegex(ValueError, "optional host-evidence"):
                host_protocol._draft7_validator()

    def test_projection_regenerates_exactly_from_the_pinned_source_when_available(self):
        if not SOURCE_EXPORT.is_file():
            self.skipTest("pinned provider source export is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "projection.json"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "tools" / "extract_host_protocol.py"),
                    "--source",
                    str(SOURCE_EXPORT),
                    "--output",
                    str(output),
                ],
                check=True,
                env=environment,
            )
            self.assertEqual(output.read_bytes(), host_protocol.PROJECTION_PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
