"""Client-side path/URL policy (_check_url, _check_local_path) and the static-hint contract.

_check_url and _check_local_path never touch the daemon or the network - they only look at
the string and the local filesystem. _drive_is_fixed is monkeypatched to True throughout the
_check_local_path tests so they behave the same on a network/removable drive as on C:.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agent_browser as ab

def _paths(profile: str = "default", home: Path | None = None) -> ab.ProfilePaths:
    return ab.ProfilePaths(profile, home or Path("C:/agent-browser-home"))

# ---------------------------------------------------------------------------
# _check_url

def test_check_url_normalizes_scheme_and_requires_a_url() -> None:
    assert ab._check_url("example.com", _paths(), {}) == "https://example.com"  # scheme-less -> https
    assert ab._check_url("https://example.com/a?b=1", _paths(), {}) == "https://example.com/a?b=1"  # unchanged
    with pytest.raises(ValueError) as exc:
        ab._check_url("", _paths(), {})
    assert exc.value.error_class == ab.ERR_USAGE

@pytest.mark.parametrize("scheme_url", ["javascript:alert(1)", "file:///C:/secrets.txt", "edge://settings", "ftp://x.com/f"])
def test_check_url_refuses_disallowed_schemes(scheme_url: str) -> None:
    with pytest.raises(ValueError) as exc:
        ab._check_url(scheme_url, _paths(), {})
    assert exc.value.error_class == ab.ERR_GUARDED
    assert exc.value.hint == ab._hint("guarded_scheme")

def test_check_url_about_blank_always_allowed_data_only_for_reserved_profiles() -> None:
    assert ab._check_url("about:blank", _paths("default"), {}) == "about:blank"
    assert ab._check_url("about:blank", _paths("_doctor"), {}) == "about:blank"
    raw = "data:text/html,<b>hi</b>"
    assert ab._check_url(raw, _paths("_doctor"), {}) == raw
    assert ab._check_url(raw, _paths("_live"), {}) == raw
    with pytest.raises(ValueError) as exc:
        ab._check_url(raw, _paths("default"), {})
    assert exc.value.error_class == ab.ERR_GUARDED

def test_check_url_allowed_hosts_including_subdomains_and_lookalike_rejection() -> None:
    config = {"allowed_hosts": ["example.com"]}
    assert ab._check_url("https://example.com/x", _paths(), config) == "https://example.com/x"
    assert ab._check_url("https://sub.example.com/x", _paths(), config) == "https://sub.example.com/x"
    assert ab._check_url("https://anything.example", _paths(), {}) == "https://anything.example"  # no allowlist configured
    for bad in ("https://evil.com/x", "https://notexample.com/x"):  # unrelated host + lookalike suffix
        with pytest.raises(ValueError) as exc:
            ab._check_url(bad, _paths(), config)
        assert exc.value.error_class == ab.ERR_GUARDED
        assert exc.value.hint == ab._hint("guarded_host")

@pytest.mark.parametrize(
    "host, allowed, expected",
    [
        ("example.com", ["example.com"], True),
        ("sub.example.com", ["example.com"], True),
        ("deep.sub.example.com", ["example.com"], True),
        ("notexample.com", ["example.com"], False),
        ("example.com", [], False),
        ("example.com", ["other.com", "example.com"], True),
    ],
)
def test_host_allowed_table(host: str, allowed: list[str], expected: bool) -> None:
    assert ab._host_allowed(host, allowed) is expected

# ---------------------------------------------------------------------------
# _check_local_path

@pytest.fixture(autouse=True)
def _fixed_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ab, "_drive_is_fixed", lambda path: True)

def test_check_local_path_requires_a_path() -> None:
    with pytest.raises(ValueError) as exc:
        ab._check_local_path("", _paths(), {})
    assert exc.value.error_class == ab.ERR_USAGE

def test_check_local_path_refuses_unc_hidden_and_out_of_root_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "elsewhere" / "f.txt"
    for candidate in (r"\\server\share\f.txt", str(tmp_path / ".secret" / "f.txt"), str(outside)):
        with pytest.raises(ValueError) as exc:
            ab._check_local_path(candidate, _paths(), {}, must_exist=False)
        assert exc.value.error_class == ab.ERR_GUARDED

@pytest.mark.parametrize("suffix", [".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".jks"])
def test_check_local_path_refuses_key_material(suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError) as exc:
        ab._check_local_path(str(tmp_path / f"secret{suffix}"), _paths(), {}, must_exist=False)
    assert exc.value.error_class == ab.ERR_GUARDED
    assert exc.value.hint == ab._hint("guarded_path")

def test_check_local_path_accepts_cwd_and_upload_root_must_exist_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "report.txt"
    target.write_text("hi", encoding="utf-8")
    assert ab._check_local_path("report.txt", _paths(), {}) == target.resolve()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    upload_target = uploads / "f.csv"
    upload_target.write_text("a,b", encoding="utf-8")
    resolved = ab._check_local_path(str(upload_target), _paths(), {"upload_roots": [str(uploads)]})
    assert resolved == upload_target.resolve()

    with pytest.raises(ValueError) as exc:
        ab._check_local_path("missing.txt", _paths(), {})  # must_exist=True (default)
    assert exc.value.error_class == ab.ERR_NOT_FOUND

    missing_ok = ab._check_local_path("missing.txt", _paths(), {}, must_exist=False)
    assert missing_ok == (tmp_path / "missing.txt").resolve() and not missing_ok.exists()

# ---------------------------------------------------------------------------
# HINTS / _hint: every hint is a fully-rendered static template

def test_every_hint_renders_without_leftover_placeholders() -> None:
    for key in ab.HINTS:
        rendered = ab._hint(key, "sample-profile")
        assert isinstance(rendered, str) and rendered
        assert "{" not in rendered and "}" not in rendered
    assert ab._hint("not_running", "acme") == f"Run: {ab.TOOL_NAME} start --profile acme"
    with pytest.raises(KeyError):
        ab._hint("no-such-hint-key")

def test_no_hint_keyword_argument_is_an_f_string() -> None:
    """Page-controlled text must never reach a hint: static scan of every `hint=` call site."""
    source = Path(ab.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=ab.__file__)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "hint" and isinstance(node.value, ast.JoinedStr)
    ]
    assert offenders == [], f"hint=f\"...\" found at line(s) {offenders}; hints must be static HINTS[...] templates"
