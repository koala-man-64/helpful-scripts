from __future__ import annotations

import builtins
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark import census_contract  # noqa: E402


class CensusContractTests(unittest.TestCase):
    def test_retained_bundle_has_all_published_files_and_pins(self):
        files = census_contract._retained_files(census_contract.CONTRACT_ROOT)
        self.assertEqual(len(files), 63)
        schema = census_contract._load_retained_schema()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_all_shared_fixtures_match_only_their_json_and_schema_expectations(self):
        root = census_contract.CONTRACT_ROOT / "fixtures"
        index = census_contract.strict_json((root / "index.json").read_bytes())
        self.assertEqual(len(index["cases"]), 52)
        for case in index["cases"]:
            with self.subTest(case=case["id"]):
                raw = (root / case["census_file"]).read_bytes()
                if not case["json_valid"]:
                    with self.assertRaises(ValueError):
                        census_contract.strict_json(raw)
                    continue
                value = census_contract.strict_json(raw)
                if case["schema_valid"]:
                    self.assertIsNone(census_contract.validate_census(value))
                else:
                    with self.assertRaisesRegex(ValueError, "payload is invalid"):
                        census_contract.validate_census(value)

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(self):
        for raw in (
            b'{"key":1,"key":2}',
            b'{"key":NaN}',
            b'{"key":Infinity}',
            b'{"key":1e999}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    census_contract.strict_json(raw)

    def test_rejects_tampered_missing_and_extra_retained_files(self):
        for mutation, expected in (
            (lambda root: (root / "README.md").write_text("tampered", encoding="utf-8"), "inventory"),
            (lambda root: (root / "README.md").unlink(), "missing or unexpected"),
            (lambda root: (root / "extra.json").write_text("{}", encoding="utf-8"), "missing or unexpected"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                copied = Path(temporary) / "contract"
                shutil.copytree(census_contract.CONTRACT_ROOT, copied)
                mutation(copied)
                with patch.object(census_contract, "CONTRACT_ROOT", copied):
                    with self.assertRaisesRegex(ValueError, expected):
                        census_contract._load_retained_schema()

    def test_rejects_unsafe_inventory_paths_and_remote_schema_references(self):
        manifest = census_contract.strict_json(
            (census_contract.CONTRACT_ROOT / "manifest.json").read_bytes()
        )
        for unsafe in ("../escape.json", "./escape.json", "a//b.json"):
            with self.subTest(unsafe=unsafe):
                manifest["files"][0]["path"] = unsafe
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    census_contract._inventory(manifest)
        with self.assertRaisesRegex(ValueError, "remote reference"):
            census_contract._assert_local_refs({"$ref": "https://example.invalid/schema"})

    def test_uses_strict_utc_calendar_date_format(self):
        value = census_contract.strict_json(
            (census_contract.CONTRACT_ROOT / "fixtures" / "cases" / "valid-partial.json").read_bytes()
        )
        value["artifacts"]["capture"]["sealed_at"] = "2026-02-30T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "sealed_at"):
            census_contract.validate_census(value)
        value["artifacts"]["capture"]["sealed_at"] = "2026-02-01T12:00:00+01:00"
        with self.assertRaisesRegex(ValueError, "sealed_at"):
            census_contract.validate_census(value)

    def test_explains_the_optional_jsonschema_dependency(self):
        original_import = builtins.__import__

        def reject_jsonschema(name, *args, **kwargs):
            if name == "jsonschema":
                raise ImportError("test dependency absent")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_jsonschema):
            with self.assertRaisesRegex(ValueError, "optional census-evidence"):
                census_contract._draft202012_validator()

    def test_rejects_reparse_point_roots_before_reading_retained_data(self):
        with patch.object(census_contract, "_is_reparse", return_value=True):
            with self.assertRaisesRegex(ValueError, "root is unavailable or unsafe"):
                census_contract._retained_files(census_contract.CONTRACT_ROOT)


if __name__ == "__main__":
    unittest.main()
