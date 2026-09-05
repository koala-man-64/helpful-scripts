"""Optional integration with the actual hook-owned importer, never a copied one.

Set CODEX_USAGE_HOOKS_SOURCE to the explicitly selected hooks source checkout.
The ledger exists only in a test temporary directory, not installed user state.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_codex_token_usage_audit import ROOT_ID, audit, root_entries, write_rollout


@unittest.skipUnless(os.environ.get("CODEX_USAGE_HOOKS_SOURCE"), "set explicit hooks source for interoperability")
class HooksInteropTests(unittest.TestCase):
    def test_actual_ledger_import_is_idempotent_and_rejects_conflicts(self):
        source = Path(os.environ["CODEX_USAGE_HOOKS_SOURCE"]).resolve(strict=True)
        schema = source / "schemas" / "usage-observations-v1.schema.json"
        committed_schema = subprocess.run(
            ["git", "show", "HEAD:schemas/usage-observations-v1.schema.json"],
            cwd=source, capture_output=True, check=True,
        ).stdout
        self.assertEqual(hashlib.sha256(committed_schema).hexdigest(), audit.USAGE_OBSERVATIONS_SCHEMA_SHA256)
        # Windows checkout normalization must not change the immutable contract
        # pin. Still reject any local schema edits beyond CRLF conversion.
        self.assertEqual(schema.read_bytes().replace(b"\r\n", b"\n"), committed_schema)
        sys.path.insert(0, str(source / "src"))
        self.addCleanup(sys.path.remove, str(source / "src"))
        from codex_workflow_hooks.evidence import EvidenceLedger

        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            home = scratch / "synthetic-rollouts"
            write_rollout(home, ROOT_ID, root_entries())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = audit.main(["--root", str(home), "--no-state-db", "--root-session", ROOT_ID, "--observations", "-"])
            self.assertEqual(status, 0)
            payload = json.loads(output.getvalue())
            ledger = EvidenceLedger(scratch / "temporary-test-ledger")
            count = len(payload["observations"])
            self.assertGreater(count, 0)
            self.assertEqual(ledger.import_usage(payload), {"inserted": count, "duplicates": 0})
            self.assertEqual(ledger.import_usage(payload), {"inserted": 0, "duplicates": count})
            summary = ledger.usage_summary()
            self.assertEqual(set(summary), {"request", "cumulative"})
            # A same-provenance event with a different model is a conflicting
            # replay, even if all counts are identical.
            payload["observations"][0]["model"] = "gpt-different"
            with self.assertRaisesRegex(ValueError, "Conflicting usage event replay"):
                ledger.import_usage(payload)
            self.assertEqual(ledger.usage_summary(), summary)


if __name__ == "__main__":
    unittest.main()
