"""Vendor folder loading, package routing, and manifest verification (pure filesystem)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import edge_pyodide as ep
from conftest import make_vendor


def _error_class(exc: BaseException) -> str | None:
    return getattr(exc, "error_class", None)


# ---------------------------------------------------------------------------
# normalize_name / parse_wheel_filename


def test_normalize_name_collapses_separators_and_case() -> None:
    assert ep.normalize_name("PyYAML") == "pyyaml"
    assert ep.normalize_name("Some__Pkg.Name-x") == "some-pkg-name-x"


def test_parse_wheel_filename_extracts_tags() -> None:
    wheel = ep.parse_wheel_filename(Path("six-1.16.0-py2.py3-none-any.whl"))
    assert wheel.name == "six"
    assert wheel.version == "1.16.0"
    assert wheel.python_tags == ("py2", "py3")
    assert wheel.abi_tags == ("none",)
    assert wheel.platform_tags == ("any",)
    assert wheel.pure is True
    assert wheel.normalized == "six"


def test_parse_wheel_filename_with_build_tag_and_binary_platform() -> None:
    wheel = ep.parse_wheel_filename(Path("numpy-2.4.6-1-cp314-cp314-pyemscripten_2026_0_wasm32.whl"))
    assert wheel.version == "2.4.6"
    assert wheel.python_tags == ("cp314",)
    assert wheel.pure is False


def test_parse_wheel_filename_rejects_non_wheel() -> None:
    with pytest.raises(ValueError) as info:
        ep.parse_wheel_filename(Path("notes.txt"))
    assert _error_class(info.value) == ep.ERR_VALIDATION


# ---------------------------------------------------------------------------
# load_vendor


def test_load_vendor_full_fixture(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert vendor.root == vendor_dir
    assert vendor.dist_dir == vendor_dir / "pyodide"
    assert vendor.wheels_dir == vendor_dir / "wheels"
    # The 314.x lockfile info block has no version; it comes from package.json.
    assert vendor.pyodide_version == "314.0.5"
    assert vendor.python_version == "3.14.2"
    assert vendor.abi_version == "2026_0"
    assert vendor.platform == "emscripten_5_0_3"
    assert vendor.flavor == "full"


def test_load_vendor_normalizes_bundled_keys(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert "pyyaml" in vendor.bundled
    assert "PyYAML" not in vendor.bundled
    assert vendor.bundled["pyyaml"]["name"] == "PyYAML"
    assert set(vendor.bundled) == {"numpy", "pandas", "micropip", "pyyaml"}


def test_load_vendor_bundled_present_reflects_files_on_disk(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert "numpy" in vendor.bundled_present
    assert vendor.bundled_present == frozenset({"numpy", "pandas", "micropip", "pyyaml"})
    assert vendor.has_bundled("NumPy") is True
    assert vendor.has_bundled("requests") is False


def test_load_vendor_parses_both_wheels_sorted(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert [w.path.name for w in vendor.wheels] == [
        "six-1.16.0-py2.py3-none-any.whl",
        "tabulate-0.9.0-py3-none-any.whl",
    ]
    assert vendor.has_wheel("tabulate") is True
    assert vendor.has_wheel("TABULATE") is True
    assert vendor.has_wheel("numpy") is False


def test_load_vendor_reads_manifest(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert vendor.manifest["tool"] == "edgepy"
    assert vendor.manifest["flavor"] == "full"
    assert [w["file"] for w in vendor.manifest["wheels"]] == [
        "tabulate-0.9.0-py3-none-any.whl",
        "six-1.16.0-py2.py3-none-any.whl",
    ]


def test_load_vendor_core_flavor(core_vendor_dir: Path) -> None:
    vendor = ep.load_vendor(core_vendor_dir)
    assert vendor.flavor == "core"
    assert vendor.bundled_present == frozenset({"micropip"})
    assert set(vendor.bundled) == {"numpy", "pandas", "micropip", "pyyaml"}


def test_load_vendor_without_manifest_yields_empty_dict(tmp_path: Path) -> None:
    root = make_vendor(tmp_path / "v", with_manifest=False)
    vendor = ep.load_vendor(root)
    assert vendor.manifest == {}


def test_load_vendor_ignores_unreadable_manifest(vendor_dir: Path) -> None:
    (vendor_dir / ep.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    vendor = ep.load_vendor(vendor_dir)
    assert vendor.manifest == {}


def test_load_vendor_ignores_non_wheel_files_in_wheels_dir(vendor_dir: Path) -> None:
    (vendor_dir / "wheels" / "README.whl").write_bytes(b"not a wheel name")
    (vendor_dir / "wheels" / "notes.txt").write_bytes(b"")
    vendor = ep.load_vendor(vendor_dir)
    assert len(vendor.wheels) == 2


def test_load_vendor_without_wheels_dir(vendor_dir: Path) -> None:
    for path in (vendor_dir / "wheels").iterdir():
        path.unlink()
    (vendor_dir / "wheels").rmdir()
    vendor = ep.load_vendor(vendor_dir)
    assert vendor.wheels == ()
    assert vendor.has_wheel("tabulate") is False


def test_load_vendor_prefers_lockfile_version_when_present(vendor_dir: Path) -> None:
    lock_path = vendor_dir / "pyodide" / "pyodide-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["info"]["version"] = "315.0.0"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    assert ep.load_vendor(vendor_dir).pyodide_version == "315.0.0"


def test_load_vendor_version_unknown_without_package_json(vendor_dir: Path) -> None:
    (vendor_dir / "pyodide" / "package.json").unlink()
    assert ep.load_vendor(vendor_dir).pyodide_version == "unknown"


def test_load_vendor_missing_dist_dir_raises_vendor_missing(tmp_path: Path) -> None:
    root = tmp_path / "nothing-here"
    with pytest.raises(RuntimeError) as info:
        ep.load_vendor(root)
    exc = info.value
    assert _error_class(exc) == ep.ERR_VENDOR_MISSING
    assert str(root / "pyodide") in str(exc)
    assert "fetch --flavor full" in exc.hint  # type: ignore[attr-defined]
    assert str(root) in exc.hint  # type: ignore[attr-defined]


def test_load_vendor_missing_wasm_raises_vendor_mismatch(vendor_dir: Path) -> None:
    (vendor_dir / "pyodide" / "pyodide.asm.wasm").unlink()
    with pytest.raises(RuntimeError) as info:
        ep.load_vendor(vendor_dir)
    assert _error_class(info.value) == ep.ERR_VENDOR_MISMATCH
    assert "pyodide.asm.wasm" in str(info.value)
    assert "incomplete" in str(info.value)
    assert "fetch --flavor full" in info.value.hint  # type: ignore[attr-defined]


def test_load_vendor_lists_every_missing_required_file(vendor_dir: Path) -> None:
    (vendor_dir / "pyodide" / "pyodide.asm.wasm").unlink()
    (vendor_dir / "pyodide" / "python_stdlib.zip").unlink()
    with pytest.raises(RuntimeError) as info:
        ep.load_vendor(vendor_dir)
    assert "pyodide.asm.wasm, python_stdlib.zip" in str(info.value)


def test_load_vendor_corrupt_lockfile_raises_vendor_mismatch(vendor_dir: Path) -> None:
    (vendor_dir / "pyodide" / "pyodide-lock.json").write_text("{corrupt", encoding="utf-8")
    with pytest.raises(RuntimeError) as info:
        ep.load_vendor(vendor_dir)
    assert _error_class(info.value) == ep.ERR_VENDOR_MISMATCH
    assert "pyodide-lock.json" in str(info.value)


# ---------------------------------------------------------------------------
# split_packages


def test_split_packages_routes_bundled_to_load_package(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert ep.split_packages(vendor, ["numpy"]) == (["numpy"], [])


def test_split_packages_routes_vendored_wheel_to_micropip(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert ep.split_packages(vendor, ["tabulate"]) == ([], ["tabulate"])


def test_split_packages_normalizes_case_but_keeps_spelling(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert ep.split_packages(vendor, ["Tabulate"]) == ([], ["Tabulate"])
    assert ep.split_packages(vendor, ["PyYAML", "NumPy"]) == (["PyYAML", "NumPy"], [])


def test_split_packages_mixed_and_skips_blank_names(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    assert ep.split_packages(vendor, ["", "  numpy ", "   ", "six"]) == (["numpy"], ["six"])


def test_split_packages_bundled_without_wheel_hints_full_flavor(core_vendor_dir: Path) -> None:
    vendor = ep.load_vendor(core_vendor_dir)
    with pytest.raises(RuntimeError) as info:
        ep.split_packages(vendor, ["numpy"])
    exc = info.value
    assert _error_class(exc) == ep.ERR_PACKAGE_MISSING
    assert "'numpy'" in str(exc)
    assert "'core'" in str(exc)
    assert "--flavor full" in exc.hint  # type: ignore[attr-defined]


def test_split_packages_micropip_is_present_in_core(core_vendor_dir: Path) -> None:
    vendor = ep.load_vendor(core_vendor_dir)
    assert ep.split_packages(vendor, ["micropip"]) == (["micropip"], [])


def test_split_packages_unknown_name_hints_fetch_pkg(vendor_dir: Path) -> None:
    vendor = ep.load_vendor(vendor_dir)
    with pytest.raises(RuntimeError) as info:
        ep.split_packages(vendor, ["requests"])
    exc = info.value
    assert _error_class(exc) == ep.ERR_PACKAGE_MISSING
    assert "'requests'" in str(exc)
    assert str(vendor.wheels_dir) in str(exc)
    assert "edgepy fetch --pkg requests" in exc.hint  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# verify_vendor


def test_verify_vendor_ok_for_fixture(vendor_dir: Path) -> None:
    report = ep.verify_vendor(vendor_dir)
    assert report["ok"] is True
    assert report["problems"] == []
    assert report["pyodide_version"] == "314.0.5"
    assert report["flavor"] == "full"
    assert report["bundled_present"] == 4
    assert report["bundled_total"] == 4
    assert report["vendor_dir"] == str(vendor_dir)
    assert sorted(report["wheels"]) == ["six-1.16.0-py2.py3-none-any.whl", "tabulate-0.9.0-py3-none-any.whl"]


def test_verify_vendor_reports_missing_wheel(vendor_dir: Path) -> None:
    (vendor_dir / "wheels" / "tabulate-0.9.0-py3-none-any.whl").unlink()
    report = ep.verify_vendor(vendor_dir)
    assert report["ok"] is False
    assert report["problems"] == ["missing wheel tabulate-0.9.0-py3-none-any.whl"]
    assert report["wheels"] == ["six-1.16.0-py2.py3-none-any.whl"]


def test_verify_vendor_reports_sha256_mismatch(vendor_dir: Path) -> None:
    path = vendor_dir / "wheels" / "six-1.16.0-py2.py3-none-any.whl"
    path.write_bytes(path.read_bytes() + b"corruption")
    report = ep.verify_vendor(vendor_dir)
    assert report["ok"] is False
    assert report["problems"] == ["sha256 mismatch for six-1.16.0-py2.py3-none-any.whl"]


def test_verify_vendor_ok_without_manifest(tmp_path: Path) -> None:
    root = make_vendor(tmp_path / "v", with_manifest=False)
    report = ep.verify_vendor(root)
    assert report["ok"] is True
    assert len(report["wheels"]) == 2


def test_verify_vendor_core_flavor_counts(core_vendor_dir: Path) -> None:
    report = ep.verify_vendor(core_vendor_dir)
    assert report["ok"] is True
    assert report["flavor"] == "core"
    assert (report["bundled_present"], report["bundled_total"]) == (1, 4)


def test_verify_vendor_propagates_missing_dist(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as info:
        ep.verify_vendor(tmp_path / "absent")
    assert _error_class(info.value) == ep.ERR_VENDOR_MISSING
