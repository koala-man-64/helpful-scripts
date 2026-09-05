"""Temporary fake releases exercise the Windows recovery helper."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "Repair-CodexHookBytecode.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
VERSION = "0.6.3+sha.3a7c21839d428f2240a21c238b85947bb62b1b17"
DIGEST = "9912fe39549f8673564dcb5ca45d1f767b86fed1bbe96d3f5fcd7710bde1ee5b"
NAMES = (
    "__init__.cpython-314.pyc", "models.cpython-314.pyc",
    "subagent_routing.cpython-314.pyc", "utils.cpython-314.pyc",
)
DOCTOR = """
import json
import os
from pathlib import Path

state = json.loads(Path(os.environ["RECOVERY_STATE"]).read_text())
cache = Path(state["release_path"]) / "src/codex_workflow_hooks/__pycache__"
bad = cache.is_dir()
prefix = "src/codex_workflow_hooks/__pycache__/"
names = json.loads(os.environ["RECOVERY_NAMES"])
print(json.dumps({
    "diagnostic_failures": [],
    "installation_state": {"valid": True, "failures": []},
    "release": {
        "failures": [], "invalid_manifest_entries": [],
        "version": state["version"], "manifest_digest": state["manifest_digest"],
        "unexpected_paths": [prefix] + [prefix + name for name in names] if bad else [],
        "valid": not bad,
    },
    "owned_events": ["SessionStart", "UserPromptSubmit", "SubagentStart",
                     "PreToolUse", "PostToolUse", "Stop", "SessionEnd"],
    "healthy": not bad, "hook_configuration": {"valid": not bad},
    "storage": {"valid": True, "sqlite_quick_check": "ok"},
}))
"""


@unittest.skipUnless(os.name == "nt" and SHELL, "Windows PowerShell recovery helper")
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / "release"
        self.cache = self.release / "src" / "codex_workflow_hooks" / "__pycache__"
        self.cache.mkdir(parents=True)
        self.original = {name: name.encode() for name in NAMES}
        for name, content in self.original.items():
            (self.cache / name).write_bytes(content)
        self.installed_entry = self.release / "hookctl.py"
        self.installed_entry.write_text(
            "import json\nprint(json.dumps({'ok': True, 'entrypoint': __file__}))\n",
            encoding="utf-8",
        )
        data = self.root / "data"
        data.mkdir()
        self.state = data / "install.json"
        self.state.write_text(json.dumps({
            "schema_version": 1, "version": VERSION, "manifest_digest": DIGEST,
            "release_path": str(self.release), "data_dir": str(data),
        }), encoding="utf-8")
        self.source = self.root / "source"
        (self.source / "src").mkdir(parents=True)
        (self.source / "hookctl.py").write_text(DOCTOR, encoding="utf-8")
        self.environment = {
            **os.environ, "RECOVERY_STATE": str(self.state),
            "RECOVERY_NAMES": json.dumps(NAMES), "PYTHONDONTWRITEBYTECODE": "1",
        }

    def invoke(self, *extra, quarantine=None):
        command = [
            SHELL, "-NoProfile", "-File", str(HELPER),
            "-SourceRoot", str(self.source), "-InstallStatePath", str(self.state),
            "-PythonExecutable", sys.executable,
            "-QuarantineRoot", str(quarantine or self.root / "quarantine"), *extra,
        ]
        return subprocess.run(command, capture_output=True, text=True, env=self.environment)

    def assert_refused(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertTrue(self.cache.is_dir())

    def test_preview_and_apply_preserve_exact_bytes_and_installed_proof(self):
        original_state = self.state.read_bytes()
        preview = self.invoke()
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["status"], "preview")
        self.assertFalse((self.root / "quarantine").exists())
        self.assertEqual({path.name: path.read_bytes() for path in self.cache.iterdir()}, self.original)

        result = self.invoke("-Apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "verified")
        self.assertTrue(payload["quarantine_verified"])
        self.assertFalse(self.cache.exists())
        destination = Path(payload["quarantine"])
        self.assertEqual({path.name: path.read_bytes() for path in destination.iterdir()}, self.original)
        self.assertEqual(payload["files"], {
            name: hashlib.sha256(content).hexdigest() for name, content in self.original.items()
        })
        self.assertEqual(Path(payload["self_test"]["path"]), self.installed_entry)
        self.assertEqual(payload["self_test"]["sha256"], hashlib.sha256(self.installed_entry.read_bytes()).hexdigest())
        self.assertTrue(json.loads(payload["self_test"]["output"])["ok"])
        self.assertEqual(payload["self_test"]["exit_code"], 0)
        self.assertTrue(payload["doctor"]["healthy"])
        self.assertEqual(payload["doctor"]["storage"]["sqlite_quick_check"], "ok")
        self.assertEqual(self.state.read_bytes(), original_state)
        receipt = json.loads((destination.parent / "recovery.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(receipt, payload)

    def test_extra_cache_file_is_rejected_before_quarantine(self):
        (self.cache / "extra.pyc").write_bytes(b"extra")
        self.assert_refused(self.invoke("-Apply"), "Unexpected cache filenames")
        self.assertFalse((self.root / "quarantine").exists())

    def test_already_clean_still_runs_the_installed_self_test(self):
        for path in self.cache.iterdir():
            path.unlink()
        self.cache.rmdir()
        result = self.invoke("-Apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "already_clean")
        self.assertFalse(payload["applied"])
        self.assertEqual(Path(payload["self_test"]["path"]), self.installed_entry)
        self.assertFalse((self.root / "quarantine").exists())

    def test_version_and_manifest_mismatches_are_rejected(self):
        original = json.loads(self.state.read_bytes())
        for field, replacement in (("version", "wrong"), ("manifest_digest", "0" * 64)):
            with self.subTest(field=field):
                self.state.write_text(json.dumps({**original, field: replacement}), encoding="utf-8")
                self.assert_refused(self.invoke("-Apply"), "Install-state schema, version, or manifest binding differs")
        self.assertFalse((self.root / "quarantine").exists())

    def test_quarantine_inside_release_is_rejected(self):
        self.assert_refused(self.invoke("-Apply", quarantine=self.release / "quarantine"), "Quarantine must be outside")
        self.assertFalse((self.release / "quarantine").exists())

    def test_reparse_file_is_rejected(self):
        original_file = self.cache / NAMES[0]
        target = self.root / "linked-bytes"
        target.write_bytes(original_file.read_bytes())
        original_file.unlink()
        try:
            original_file.symlink_to(target)
        except OSError as error:
            self.skipTest(f"Symlink privilege unavailable: {error}")
        self.assert_refused(self.invoke("-Apply"), "Non-regular cache entry rejected")
        self.assertEqual(target.read_bytes(), self.original[NAMES[0]])


if __name__ == "__main__":
    unittest.main()
