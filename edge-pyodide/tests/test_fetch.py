"""The `fetch` building blocks: PEP 508/440 helpers, the PyPI resolver, and the download
and extraction helpers. Everything is offline - PyPI/GitHub are canned dicts and
`ep._open_url` is monkeypatched to serve bytes from memory."""

from __future__ import annotations

import email.message
import hashlib
import io
import json
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import pytest

import edge_pyodide as ep

PY = "3.14.2"


def _error_class(exc: BaseException) -> str | None:
    return getattr(exc, "error_class", None)


# ---------------------------------------------------------------------------
# parse_requirement


def test_parse_requirement_bare_name() -> None:
    req = ep.parse_requirement("tabulate")
    assert req == ep.Requirement("tabulate", (), "", None)
    assert req.normalized == "tabulate"


def test_parse_requirement_with_specifier() -> None:
    req = ep.parse_requirement("PyYAML>=6")
    assert req.name == "PyYAML"
    assert req.specifier == ">=6"
    assert req.extras == ()
    assert req.marker is None
    assert req.normalized == "pyyaml"


def test_parse_requirement_extras_parenthesised_spec_and_marker() -> None:
    req = ep.parse_requirement('foo[bar,baz] (>=1.0,<2) ; python_version >= "3.8"')
    assert req.name == "foo"
    assert req.extras == ("bar", "baz")
    assert req.specifier == ">=1.0,<2"
    assert req.marker == 'python_version >= "3.8"'


def test_parse_requirement_pin_with_extra_marker() -> None:
    req = ep.parse_requirement('pkg==1.2.3; extra == "dev"')
    assert req.name == "pkg"
    assert req.specifier == "==1.2.3"
    assert req.marker == 'extra == "dev"'


def test_parse_requirement_strips_and_lowercases_extras() -> None:
    req = ep.parse_requirement("foo[ Bar , BAZ ]")
    assert req.extras == ("bar", "baz")


@pytest.mark.parametrize("garbage", ["", "   ", "==1.0", "[x]", "-bad", ";"])
def test_parse_requirement_garbage_raises_validation(garbage: str) -> None:
    with pytest.raises(ValueError) as info:
        ep.parse_requirement(garbage)
    assert _error_class(info.value) == ep.ERR_VALIDATION


# ---------------------------------------------------------------------------
# version_key / is_prerelease


def test_version_key_orders_releases() -> None:
    keys = [ep.version_key(v) for v in ("1.0", "1.0.1", "1.1")]
    assert keys == sorted(keys)
    assert keys[0] < keys[1] < keys[2]


def test_version_key_prerelease_before_final() -> None:
    assert ep.version_key("1.0rc1") < ep.version_key("1.0")
    assert ep.version_key("1.0a1") < ep.version_key("1.0b1") < ep.version_key("1.0rc1")
    assert ep.version_key("1.0.dev1") < ep.version_key("1.0")
    assert ep.version_key("1.0") < ep.version_key("1.0.post1")


def test_version_key_dev_release_before_alpha() -> None:
    assert ep.version_key("1.0.dev1") < ep.version_key("1.0a1")


def test_version_key_handles_v_prefix_and_alias_spellings() -> None:
    assert ep.version_key("v2.0") == ep.version_key("2.0")
    assert ep.version_key("1.0alpha1") == ep.version_key("1.0a1")
    assert ep.version_key("1.0c1") == ep.version_key("1.0rc1")


def test_version_key_rejects_garbage() -> None:
    assert ep.version_key("latest") is None
    assert ep.version_key("1.0-beta") is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0a1", True), ("1.0b2", True), ("1.0rc1", True), ("1.0.dev3", True), ("1.0", False), ("1.0.post1", False)],
)
def test_is_prerelease(version: str, expected: bool) -> None:
    assert ep.is_prerelease(version) is expected


# ---------------------------------------------------------------------------
# specifier_allows


@pytest.mark.parametrize(
    ("spec", "version", "expected"),
    [
        (">=1.0,<2", "1.5", True),
        (">=1.0,<2", "2.0", False),
        ("==1.2.*", "1.2.9", True),
        ("==1.2.*", "1.3.0", False),
        ("!=1.2.*", "1.2.9", False),
        ("~=1.4", "1.9", True),
        ("~=1.4", "2.0", False),
        ("~=1.4", "1.3", False),
        ("!=1.0", "1.0", False),
        ("!=1.0", "1.1", True),
        ("", "0.1", True),
        ("   ", "0.1", True),
        (">=3.8", "3.14.2", True),    # numeric, not lexicographic ("3.14" > "3.8")
        ("<3.9", "3.14.2", False),
        (">=1.0", "1.0rc1", False),   # pre-release is below the final it precedes
        ("==1.0", "1.0", True),
        ("===1.0", "1.0", True),
        ("^1.0", "0.1", True),        # unknown operator never blocks
    ],
)
def test_specifier_allows(spec: str, version: str, expected: bool) -> None:
    assert ep.specifier_allows(spec, version) is expected


def test_specifier_allows_equality_zero_pads() -> None:
    assert ep.specifier_allows("==1.0", "1.0.0") is True


# ---------------------------------------------------------------------------
# evaluate_marker / target_env


@pytest.fixture
def env() -> dict[str, str]:
    return ep.target_env(PY)


def test_target_env_describes_the_sandbox_not_the_host(env: dict[str, str]) -> None:
    assert env["python_version"] == "3.14"
    assert env["python_full_version"] == "3.14.2"
    assert env["implementation_name"] == "cpython"
    assert env["implementation_version"] == "3.14.2"
    assert env["sys_platform"] == "emscripten"
    assert env["platform_machine"] == "wasm32"
    assert env["platform_system"] == "Emscripten"
    assert env["platform_python_implementation"] == "CPython"
    assert env["os_name"] == "posix"
    assert env["platform_release"] == ""
    assert env["platform_version"] == ""


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ('python_version >= "3.8"', True),
        ('python_version < "3.8"', False),
        ('"3.8" <= python_version', True),
        ('python_full_version >= "3.14.1"', True),
        ('sys_platform == "win32"', False),
        ('sys_platform == "emscripten"', True),
        ('sys_platform != "win32"', True),
        ('platform_machine == "wasm32"', True),
        ('implementation_name == "cpython"', True),
        ('os_name == "nt" and python_version >= "3.8"', False),
        ('os_name == "posix" and python_version >= "3.8"', True),
        ('sys_platform == "win32" or sys_platform == "emscripten"', True),
        ('sys_platform == "win32" or sys_platform == "darwin"', False),
        ('python_version in "3.13 3.14"', True),
        ('python_version not in "3.13 3.14"', False),
        ('extra == "widechars"', False),
        ('extra != "x"', True),
    ],
)
def test_evaluate_marker(marker: str, expected: bool, env: dict[str, str]) -> None:
    assert ep.evaluate_marker(marker, env) is expected


def test_evaluate_marker_extras_enable_extra_clauses(env: dict[str, str]) -> None:
    assert ep.evaluate_marker('extra == "widechars"', env, extras=("widechars",)) is True
    assert ep.evaluate_marker('extra == "Wide_Chars"', env, extras=("wide-chars",)) is True
    assert ep.evaluate_marker('extra != "widechars"', env, extras=("widechars",)) is False


def test_evaluate_marker_none_or_empty_is_true(env: dict[str, str]) -> None:
    assert ep.evaluate_marker(None, env) is True
    assert ep.evaluate_marker("", env) is True


@pytest.mark.parametrize(
    "marker",
    [
        '(sys_platform == "win32")',
        'python_version >= "3.8" and (extra == "a" or extra == "b")',
        'foo == "bar"',
        "extra == bar",
        'extra >= "bar"',
        "sys_platform",
    ],
)
def test_evaluate_marker_unsupported_returns_none(marker: str, env: dict[str, str]) -> None:
    assert ep.evaluate_marker(marker, env) is None


# ---------------------------------------------------------------------------
# wheel_compatible


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("x-1.0-py3-none-any.whl", True),
        ("x-1.0-py2.py3-none-any.whl", True),
        ("x-1.0-cp314-none-any.whl", True),
        ("x-1.0-py314-none-any.whl", True),
        ("x-1.0-cp313-none-any.whl", False),
        ("x-1.0-cp314-cp314-win_amd64.whl", False),
        ("x-1.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl", False),
        ("x-1.0-py3-none-manylinux2014_x86_64.whl", False),
        ("x-1.0-py2-none-any.whl", False),
        ("x-1.0.tar.gz", False),
    ],
)
def test_wheel_compatible(filename: str, expected: bool) -> None:
    assert ep.wheel_compatible(filename, PY) is expected


# ---------------------------------------------------------------------------
# resolve_wheels (canned PyPI JSON)


def _whl(filename: str, *, yanked: bool = False, requires_python: str | None = None, sha: str | None = None) -> dict[str, Any]:
    return {
        "packagetype": "bdist_wheel",
        "filename": filename,
        "url": f"https://files.pythonhosted.org/packages/{filename}",
        "yanked": yanked,
        "requires_python": requires_python,
        "digests": {"sha256": sha or hashlib.sha256(filename.encode()).hexdigest()},
    }


def _sdist(name: str, version: str) -> dict[str, Any]:
    return {"packagetype": "sdist", "filename": f"{name}-{version}.tar.gz", "url": "", "yanked": False, "digests": {}}


def _project(name: str, releases: dict[str, list[dict[str, Any]]], latest: str, requires_dist: list[str] | None = None) -> dict[str, Any]:
    return {"info": {"name": name, "version": latest, "requires_dist": requires_dist or []}, "releases": releases}


class FakePyPI:
    """`fetch_json` stand-in: canned responses keyed by URL; unknown URLs act like a 404."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.urls: list[str] = []

    def add(self, name: str, data: dict[str, Any], version: str | None = None) -> None:
        path = f"{name}/{version}" if version else name
        self.responses[ep.PYPI_JSON_URL.format(name=path)] = data

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if url not in self.responses:
            raise ep._tag(RuntimeError(f"GET {url} failed: HTTP 404"), ep.ERR_FETCH, http_status=404)
        return self.responses[url]


@pytest.fixture
def pypi() -> FakePyPI:
    fake = FakePyPI()
    fake.add("tabulate", _project(
        "tabulate",
        {
            "0.8.0": [_whl("tabulate-0.8.0-py3-none-any.whl")],
            "0.9.0": [_sdist("tabulate", "0.9.0"), _whl("tabulate-0.9.0-py3-none-any.whl", sha="ab" * 32)],
            "0.9.1": [_whl("tabulate-0.9.1-py3-none-any.whl", yanked=True)],
            "1.0.0": [_whl("tabulate-1.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl")],
            "1.1.0rc1": [_whl("tabulate-1.1.0rc1-py3-none-any.whl")],
            "junk": [_whl("tabulate-junk-py3-none-any.whl")],
        },
        latest="0.9.0",
        requires_dist=["wcwidth>=0.2", "PyYAML>=6", 'extras-only; extra == "widechars"'],
    ))
    fake.add("tabulate", {"info": {"name": "tabulate", "version": "1.1.0rc1", "requires_dist": ["wcwidth"]}}, version="1.1.0rc1")
    fake.add("wcwidth", _project("wcwidth", {"0.2.13": [_whl("wcwidth-0.2.13-py2.py3-none-any.whl")]}, latest="0.2.13"))
    fake.add("extras-only", _project("extras-only", {"1.0": [_whl("extras_only-1.0-py3-none-any.whl")]}, latest="1.0"))
    fake.add("fussy", _project(
        "fussy",
        {"1.0": [_whl("fussy-1.0-py3-none-any.whl")]},
        latest="1.0",
        requires_dist=['weird; (sys_platform == "win32")', "ghost", "==broken", 'wcwidth; python_version >= "3.8"'],
    ))
    fake.add("newonly", _project(
        "newonly",
        {
            "1.0": [_whl("newonly-1.0-py3-none-any.whl", requires_python=">=3.8")],
            "2.0": [_whl("newonly-2.0-py3-none-any.whl", requires_python=">=3.15")],
        },
        latest="2.0",
    ))
    fake.add("newonly", {"info": {"name": "newonly", "version": "1.0", "requires_dist": []}}, version="1.0")
    fake.add("futureonly", _project(
        "futureonly", {"2.0": [_whl("futureonly-2.0-py3-none-any.whl", requires_python=">=3.15")]}, latest="2.0",
    ))
    fake.add("latestcp", _project(
        "latestcp",
        {
            "1.0.0": [_whl("latestcp-1.0.0-py3-none-any.whl")],
            "2.0.0": [_whl("latestcp-2.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl")],
        },
        latest="2.0.0",
        requires_dist=["should-not-be-used"],
    ))
    fake.add("latestcp", {"info": {"name": "latestcp", "version": "1.0.0", "requires_dist": ["wcwidth"]}}, version="1.0.0")
    fake.add("nodetail", _project(
        "nodetail",
        {"1.0": [_whl("nodetail-1.0-py3-none-any.whl")], "2.0": [_whl("nodetail-2.0-py3-none-any.whl", yanked=True)]},
        latest="2.0",
    ))
    return fake


def _resolve(pypi: FakePyPI, *reqs: str | ep.Requirement, bundled: set[str] | None = None) -> tuple[dict[str, ep.ResolvedWheel], list[dict[str, str]]]:
    parsed = [r if isinstance(r, ep.Requirement) else ep.parse_requirement(r) for r in reqs]
    return ep.resolve_wheels(parsed, PY, bundled or {"pyyaml"}, fetch_json=pypi)


def test_resolve_picks_newest_non_yanked_final_with_pure_wheel(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate")
    wheel = chosen["tabulate"]
    # 1.1.0rc1 (pre-release), 1.0.0 (cp wheel only), 0.9.1 (yanked), "junk" (unparseable) are all skipped.
    assert wheel.version == "0.9.0"
    assert wheel.filename == "tabulate-0.9.0-py3-none-any.whl"
    assert wheel.url == "https://files.pythonhosted.org/packages/tabulate-0.9.0-py3-none-any.whl"
    assert wheel.sha256 == "ab" * 32
    assert wheel.name == "tabulate"
    assert wheel.requires == ("wcwidth>=0.2", "PyYAML>=6", 'extras-only; extra == "widechars"')
    assert unresolved == []


def test_resolve_follows_requires_dist_recursively(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate")
    assert set(chosen) == {"tabulate", "wcwidth"}
    assert chosen["wcwidth"].version == "0.2.13"
    assert chosen["wcwidth"].filename == "wcwidth-0.2.13-py2.py3-none-any.whl"
    assert unresolved == []


def test_resolve_skips_bundled_names_without_fetching(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate", "PyYAML", bundled={"pyyaml"})
    assert "pyyaml" not in chosen
    assert unresolved == []
    assert not any("pyyaml" in url for url in pypi.urls)


def test_resolve_uses_normalized_name_for_the_pypi_url(pypi: FakePyPI) -> None:
    chosen, _ = _resolve(pypi, "Tabulate")
    assert pypi.urls[0] == "https://pypi.org/pypi/tabulate/json"
    assert "tabulate" in chosen and "Tabulate" not in chosen


def test_resolve_extra_deps_are_dropped_without_the_extra(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate")
    assert "extras-only" not in chosen
    assert all(entry["name"] != "extras-only" for entry in unresolved)
    assert not any("extras-only" in url for url in pypi.urls)


def test_resolve_extra_deps_are_followed_when_the_extra_is_requested(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate[widechars]")
    assert "extras-only" in chosen
    assert chosen["extras-only"].filename == "extras_only-1.0-py3-none-any.whl"
    assert unresolved == []


def test_resolve_honors_exact_pin(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate==0.8.0")
    assert chosen["tabulate"].version == "0.8.0"
    assert unresolved == []


def test_resolve_pin_allows_a_prerelease(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate==1.1.0rc1")
    assert chosen["tabulate"].version == "1.1.0rc1"
    assert chosen["tabulate"].filename == "tabulate-1.1.0rc1-py3-none-any.whl"
    # The pinned version is not info.version, so its metadata comes from the version endpoint.
    assert "https://pypi.org/pypi/tabulate/1.1.0rc1/json" in pypi.urls
    assert chosen["tabulate"].requires == ("wcwidth",)
    assert "wcwidth" in chosen
    assert unresolved == []


def test_resolve_pin_to_cp_only_version_is_unresolved(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "tabulate==1.0.0")
    assert chosen == {}
    assert unresolved == [{"name": "tabulate", "reason": f"no pure-Python wheel for Python {PY} matching ==1.0.0"}]


def test_resolve_respects_file_level_requires_python(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "newonly")
    assert chosen["newonly"].version == "1.0"
    assert unresolved == []


def test_resolve_reports_when_requires_python_excludes_every_file(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "futureonly")
    assert chosen == {}
    assert unresolved == [{"name": "futureonly", "reason": f"no pure-Python wheel for Python {PY}"}]


def test_resolve_records_unsupported_marker_with_parent_name(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "fussy")
    entry = next(e for e in unresolved if e["name"] == "weird")
    assert entry["reason"] == "unsupported marker '(sys_platform == \"win32\")' in fussy (use --wheel to add it by hand)"
    assert "weird" not in chosen
    assert not any("weird" in url for url in pypi.urls)


def test_resolve_records_unparseable_requirement(pypi: FakePyPI) -> None:
    _, unresolved = _resolve(pypi, "fussy")
    assert {"name": "==broken", "reason": "unparseable requirement (use --wheel to add it by hand)"} in unresolved


def test_resolve_supported_marker_dep_is_followed(pypi: FakePyPI) -> None:
    chosen, _ = _resolve(pypi, "fussy")
    assert "wcwidth" in chosen


def test_resolve_404_lands_in_unresolved_with_reason(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "nonexistent")
    assert chosen == {}
    assert unresolved == [{"name": "nonexistent", "reason": "GET https://pypi.org/pypi/nonexistent/json failed: HTTP 404"}]


def test_resolve_404_dependency_is_reported_once(pypi: FakePyPI) -> None:
    _, unresolved = _resolve(pypi, "fussy", "ghost")
    ghosts = [e for e in unresolved if e["name"] == "ghost"]
    assert len(ghosts) == 1
    assert "HTTP 404" in ghosts[0]["reason"]


def test_resolve_uses_version_endpoint_when_picked_is_not_latest(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "latestcp")
    assert chosen["latestcp"].version == "1.0.0"
    assert "https://pypi.org/pypi/latestcp/1.0.0/json" in pypi.urls
    assert chosen["latestcp"].requires == ("wcwidth",)
    assert "wcwidth" in chosen
    assert "should-not-be-used" not in chosen
    assert unresolved == []


def test_resolve_tolerates_missing_version_endpoint(pypi: FakePyPI) -> None:
    chosen, unresolved = _resolve(pypi, "nodetail")
    assert chosen["nodetail"].version == "1.0"
    assert chosen["nodetail"].requires == ()
    assert unresolved == []


def test_resolve_does_not_refetch_shared_dependencies(pypi: FakePyPI) -> None:
    _resolve(pypi, "tabulate", "fussy", "wcwidth")
    assert pypi.urls.count("https://pypi.org/pypi/wcwidth/json") == 1


def test_resolve_empty_input(pypi: FakePyPI) -> None:
    assert ep.resolve_wheels([], PY, set(), fetch_json=pypi) == ({}, [])
    assert pypi.urls == []


# ---------------------------------------------------------------------------
# download_file / release_asset_info (monkeypatched ep._open_url)


class FakeResponse:
    """Minimal urllib response: read(n), .status, .headers, context manager."""

    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._buf.close()


class FakeHttp:
    """Records every request; `handler(request)` returns a FakeResponse or raises."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[urllib.request.Request] = []
        self.handler: Callable[[urllib.request.Request], FakeResponse] = lambda req: FakeResponse(b"")
        monkeypatch.setattr(ep, "_open_url", self._open)

    def _open(self, request: urllib.request.Request, timeout: float, loopback: bool) -> FakeResponse:
        assert loopback is False, "fetch traffic must never use the loopback opener"
        self.requests.append(request)
        return self.handler(request)

    def serve(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.handler = lambda req: FakeResponse(body, status, headers)


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", email.message.Message(), None)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    return FakeHttp(monkeypatch)


BODY = bytes(range(256)) * 40  # 10240 bytes, larger than a single small read
BODY_SHA = hashlib.sha256(BODY).hexdigest()
URL = "https://example.invalid/pkg-1.0-py3-none-any.whl"


def test_download_file_writes_dest_and_verifies_hash(http: FakeHttp, tmp_path: Path) -> None:
    http.serve(BODY)
    dest = tmp_path / "wheels" / "pkg-1.0-py3-none-any.whl"
    result = ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert result == dest
    assert dest.read_bytes() == BODY
    assert not dest.with_name(dest.name + ".part").exists()
    assert len(http.requests) == 1
    assert http.requests[0].full_url == URL
    assert http.requests[0].get_header("Range") is None
    assert http.requests[0].get_header("User-agent") == ep.USER_AGENT


def test_download_file_without_size_or_hash_uses_content_length(http: FakeHttp, tmp_path: Path) -> None:
    http.serve(BODY, headers={"Content-Length": str(len(BODY))})
    dest = tmp_path / "file.bin"
    ep.download_file(URL, dest)
    assert dest.read_bytes() == BODY


def test_download_file_sha_mismatch_raises_and_removes_part(http: FakeHttp, tmp_path: Path) -> None:
    http.serve(BODY)
    dest = tmp_path / "file.bin"
    with pytest.raises(RuntimeError) as info:
        ep.download_file(URL, dest, sha256="00" * 32)
    assert _error_class(info.value) == ep.ERR_FETCH
    assert "sha256 mismatch" in str(info.value)
    assert BODY_SHA in str(info.value)
    assert not dest.exists()
    assert not dest.with_name("file.bin.part").exists()


def test_download_file_size_mismatch_raises_and_keeps_part_for_resume(http: FakeHttp, tmp_path: Path) -> None:
    http.serve(BODY)
    dest = tmp_path / "file.bin"
    with pytest.raises(RuntimeError) as info:
        ep.download_file(URL, dest, expected_size=len(BODY) + 5)
    assert _error_class(info.value) == ep.ERR_FETCH
    assert "Downloaded size" in str(info.value)
    assert dest.with_name("file.bin.part").read_bytes() == BODY
    assert not dest.exists()


def test_download_file_skips_when_dest_already_matches(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    dest.write_bytes(BODY)

    def refuse(_req: urllib.request.Request) -> FakeResponse:
        raise AssertionError("must not hit the network")

    http.handler = refuse
    assert ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA) == dest
    assert http.requests == []


def test_download_file_redownloads_when_existing_hash_differs(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"x" * len(BODY))
    http.serve(BODY)
    ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert dest.read_bytes() == BODY
    assert len(http.requests) == 1


def test_download_file_resumes_part_with_range_header(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    have = 4000
    part.write_bytes(BODY[:have])

    def serve_tail(req: urllib.request.Request) -> FakeResponse:
        assert req.get_header("Range") == f"bytes={have}-"
        return FakeResponse(BODY[have:], status=206)

    http.handler = serve_tail
    ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert dest.read_bytes() == BODY
    assert not part.exists()
    assert http.requests[0].get_header("Range") == f"bytes={have}-"


def test_download_file_restarts_when_server_ignores_range(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    part.write_bytes(b"stale" * 100)
    http.serve(BODY, status=200)  # full body, no 206 - must overwrite, not append
    ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert dest.read_bytes() == BODY
    assert http.requests[0].get_header("Range") == "bytes=500-"


def test_download_file_no_range_when_part_is_already_full_size(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    dest.with_name("file.bin.part").write_bytes(BODY)
    http.serve(BODY)
    ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert http.requests[0].get_header("Range") is None
    assert dest.read_bytes() == BODY


def test_download_file_416_discards_part_and_retries_from_scratch(http: FakeHttp, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    part.write_bytes(b"garbage")

    def handler(req: urllib.request.Request) -> FakeResponse:
        if req.get_header("Range"):
            raise _http_error(URL, 416)
        return FakeResponse(BODY)

    http.handler = handler
    ep.download_file(URL, dest, expected_size=len(BODY), sha256=BODY_SHA)
    assert dest.read_bytes() == BODY
    assert [r.get_header("Range") for r in http.requests] == ["bytes=7-", None]


def test_download_file_http_error_is_tagged_fetch(http: FakeHttp, tmp_path: Path) -> None:
    def handler(_req: urllib.request.Request) -> FakeResponse:
        raise _http_error(URL, 404)

    http.handler = handler
    with pytest.raises(RuntimeError) as info:
        ep.download_file(URL, tmp_path / "file.bin")
    assert _error_class(info.value) == ep.ERR_FETCH
    assert info.value.http_status == 404  # type: ignore[attr-defined]
    assert "HTTP 404" in str(info.value)


def test_download_file_url_error_is_tagged_fetch_with_hint(http: FakeHttp, tmp_path: Path) -> None:
    def handler(_req: urllib.request.Request) -> FakeResponse:
        raise urllib.error.URLError("name resolution failed")

    http.handler = handler
    with pytest.raises(RuntimeError) as info:
        ep.download_file(URL, tmp_path / "file.bin")
    assert _error_class(info.value) == ep.ERR_FETCH
    assert "proxy" in info.value.hint  # type: ignore[attr-defined]


def test_release_asset_info_parses_size_and_sha256(http: FakeHttp) -> None:
    http.serve(json.dumps({
        "tag_name": "314.0.5",
        "assets": [
            {"name": "pyodide-core-314.0.5.tar.bz2", "size": 6_000_000, "digest": "sha256:" + "cc" * 32},
            {"name": "pyodide-314.0.5.tar.bz2", "size": 350_000_000, "digest": "sha256:" + "dd" * 32},
        ],
    }).encode())
    assert ep.release_asset_info("314.0.5", "pyodide-314.0.5.tar.bz2") == (350_000_000, "dd" * 32)
    assert http.requests[0].full_url == ep.PYODIDE_RELEASE_API.format(version="314.0.5")
    assert http.requests[0].get_header("Accept") == "application/json"


def test_release_asset_info_without_sha256_digest(http: FakeHttp) -> None:
    http.serve(json.dumps({"assets": [{"name": "a.tar.bz2", "size": 10, "digest": "md5:abc"}]}).encode())
    assert ep.release_asset_info("1", "a.tar.bz2") == (10, None)


def test_release_asset_info_zero_size_becomes_none(http: FakeHttp) -> None:
    http.serve(json.dumps({"assets": [{"name": "a.tar.bz2", "size": 0}]}).encode())
    assert ep.release_asset_info("1", "a.tar.bz2") == (None, None)


def test_release_asset_info_missing_asset_raises_fetch(http: FakeHttp) -> None:
    http.serve(json.dumps({"assets": [{"name": "other.tar.bz2", "size": 1}]}).encode())
    with pytest.raises(RuntimeError) as info:
        ep.release_asset_info("314.0.5", "pyodide-314.0.5.tar.bz2")
    assert _error_class(info.value) == ep.ERR_FETCH
    assert "no asset named pyodide-314.0.5.tar.bz2" in str(info.value)
    assert "github.com/pyodide/pyodide/releases" in info.value.hint  # type: ignore[attr-defined]


def test_release_asset_info_returns_none_pair_when_api_fails(http: FakeHttp) -> None:
    def handler(req: urllib.request.Request) -> FakeResponse:
        raise _http_error(req.full_url, 403)

    http.handler = handler
    assert ep.release_asset_info("314.0.5", "pyodide-314.0.5.tar.bz2") == (None, None)


def test_release_asset_info_returns_none_pair_on_non_json(http: FakeHttp) -> None:
    http.serve(b"<html>rate limited</html>")
    assert ep.release_asset_info("314.0.5", "pyodide-314.0.5.tar.bz2") == (None, None)


# ---------------------------------------------------------------------------
# extract_dist (real .tar.bz2 built in tmp_path)


def _add_file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_dir(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    tar.addfile(info)


def test_extract_dist_flattens_members_by_basename_inside_dist_dir(tmp_path: Path) -> None:
    archive = tmp_path / "pyodide-314.0.5.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        _add_dir(tar, "pyodide")
        _add_file(tar, "pyodide/pyodide.js", b"js")
        _add_dir(tar, "pyodide/sub")
        _add_file(tar, "pyodide/sub/ignored.txt", b"nested")
        _add_file(tar, "pyodide/../evil.txt", b"evil")
        _add_file(tar, "pyodide/.hidden", b"dot")
    dist_dir = tmp_path / "vendor" / "pyodide"
    count = ep.extract_dist(archive, dist_dir)
    # Every regular file lands flat under dist_dir by basename: the nested member is NOT
    # ignored (it becomes dist_dir/ignored.txt) and the traversal member is neutralised
    # to dist_dir/evil.txt. Directory members and dot-files are skipped.
    assert count == 3
    assert sorted(p.name for p in dist_dir.iterdir()) == ["evil.txt", "ignored.txt", "pyodide.js"]
    assert (dist_dir / "pyodide.js").read_bytes() == b"js"
    assert (dist_dir / "ignored.txt").read_bytes() == b"nested"
    assert (dist_dir / "evil.txt").read_bytes() == b"evil"
    assert not (dist_dir / "sub").exists()
    assert not (tmp_path / "vendor" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and p != archive and dist_dir not in p.parents]
    assert outside == []


def test_extract_dist_overwrites_existing_files(tmp_path: Path) -> None:
    archive = tmp_path / "dist.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        _add_file(tar, "pyodide/pyodide.js", b"new")
    dist_dir = tmp_path / "pyodide"
    dist_dir.mkdir()
    (dist_dir / "pyodide.js").write_bytes(b"old")
    assert ep.extract_dist(archive, dist_dir) == 1
    assert (dist_dir / "pyodide.js").read_bytes() == b"new"


def test_extract_dist_corrupt_archive_raises_fetch(tmp_path: Path) -> None:
    archive = tmp_path / "broken.tar.bz2"
    archive.write_bytes(b"definitely not bzip2")
    with pytest.raises(RuntimeError) as info:
        ep.extract_dist(archive, tmp_path / "out")
    assert _error_class(info.value) == ep.ERR_FETCH
    assert "Delete the archive" in info.value.hint  # type: ignore[attr-defined]
