"""LocalServer, the PEP 503 index, mount zips and wheel filename parsing.

The server really listens on 127.0.0.1:0 (in-process, loopback only) and is driven with
http.client / a raw socket so headers are observed exactly as Edge would see them.
"""

from __future__ import annotations

import http.client
import io
import mimetypes
import socket
import urllib.parse
import zipfile
from pathlib import Path
from typing import Iterator

import pytest

import edge_pyodide as ep

TOKEN = "tok-abc123"
SECRET = b"TOP SECRET - must never be served"
WASM_MAGIC = bytes([0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00])  # "\0asm" + version 1


# ---------------------------------------------------------------------------
# Helpers / fixtures


class Served:
    def __init__(self, server: ep.LocalServer, root: Path, wheels: Path) -> None:
        self.server = server
        self.root = root
        self.wheels = wheels

    def request(self, path: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        finally:
            conn.close()

    def raw(self, request_text: str) -> bytes:
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as sock:
            sock.sendall(request_text.encode("latin-1"))
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)


def _build_site(root: Path) -> Served:
    wheels = root / "wheels"
    wheels.mkdir(parents=True)
    (wheels / "a.wasm").write_bytes(WASM_MAGIC + b"w" * 100)
    (wheels / "b.js").write_bytes(b"console.log(1)")
    (wheels / "c.mjs").write_bytes(b"export default 1;")
    (wheels / "d-1.0-py3-none-any.whl").write_bytes(b"PK wheel")
    (wheels / "e.zip").write_bytes(b"PK zip")
    (wheels / "f.json").write_bytes(b"{}")
    (wheels / "g.unknownext").write_bytes(b"???")
    (wheels / "H.WASM").write_bytes(b"upper")
    (root / "secret.txt").write_bytes(SECRET)        # sibling of the served dir
    (wheels / "nested").mkdir()
    (wheels / "nested" / "inner.txt").write_bytes(b"nested file")
    (wheels / ".hidden").write_bytes(b"dotfile")

    server = ep.LocalServer(token=TOKEN)
    server.add_dir("wheels", wheels)
    server.add_bytes("index.html", b"<html>runner</html>", ep.CONTENT_TYPES[".html"])
    server.add_bytes("mount/m1.zip", b"PK mount", ep.CONTENT_TYPES[".zip"])
    server.start()
    return Served(server, root, wheels)


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """One server shared by the read-only tests: stop() costs a 0.5s serve_forever poll slice."""
    served = _build_site(tmp_path_factory.mktemp("site"))
    try:
        yield served
    finally:
        served.server.stop()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """A private server for tests that add routes or files."""
    served = _build_site(tmp_path / "site")
    try:
        yield served
    finally:
        served.server.stop()


@pytest.fixture
def lying_mimetypes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a Windows box whose HKCR entries poison the mimetypes module."""
    monkeypatch.setattr(mimetypes, "guess_type", lambda url, strict=True: ("text/plain", None))
    for suffix in (".wasm", ".js", ".mjs", ".whl", ".zip", ".json", ".html"):
        monkeypatch.setitem(mimetypes.types_map, suffix, "text/plain")


# ---------------------------------------------------------------------------
# Content types


@pytest.mark.usefixtures("lying_mimetypes")
@pytest.mark.parametrize("filename, expected", [
    ("a.wasm", "application/wasm"),
    ("b.js", "text/javascript"),
    ("c.mjs", "text/javascript"),
    ("d-1.0-py3-none-any.whl", "application/octet-stream"),
    ("e.zip", "application/zip"),
    ("f.json", "application/json"),
    ("g.unknownext", "application/octet-stream"),
    ("H.WASM", "application/wasm"),
])
def test_pinned_content_types_ignore_mimetypes_module(site: Served, filename: str, expected: str) -> None:
    status, headers, body = site.request(f"/{TOKEN}/wheels/{filename}")

    assert status == 200
    assert headers["content-type"] == expected
    assert body == (site.wheels / filename).read_bytes()
    assert mimetypes.guess_type(filename)[0] == "text/plain"  # the lie is in place, and ignored


@pytest.mark.usefixtures("lying_mimetypes")
def test_add_bytes_routes_use_their_declared_content_type(site: Served) -> None:
    status, headers, body = site.request(f"/{TOKEN}/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert body == b"<html>runner</html>"

    status, headers, body = site.request(f"/{TOKEN}/mount/m1.zip")
    assert status == 200
    assert headers["content-type"] == "application/zip"
    assert body == b"PK mount"


def test_root_path_serves_index_html_route(site: Served) -> None:
    assert site.request(f"/{TOKEN}/")[2] == b"<html>runner</html>"
    assert site.request(f"/{TOKEN}/index.html")[2] == b"<html>runner</html>"


def test_query_string_is_ignored_for_routing(site: Served) -> None:
    status, _, body = site.request(f"/{TOKEN}/wheels/b.js?v=123&cache=bust")
    assert status == 200
    assert body == b"console.log(1)"


def test_base_url_uses_loopback_port_and_token(site: Served) -> None:
    assert site.server.base_url == f"http://127.0.0.1:{site.server.port}/{TOKEN}"
    assert site.server.port > 0


def test_default_token_is_random_and_url_safe() -> None:
    a, b = ep.LocalServer(), ep.LocalServer()
    assert a.token != b.token
    assert urllib.parse.quote(a.token, safe="") == a.token


# ---------------------------------------------------------------------------
# Cache-Control / Content-Length / HEAD


@pytest.mark.parametrize("path, status, expected_body", [
    (f"/{TOKEN}/wheels/a.wasm", 200, WASM_MAGIC + b"w" * 100),
    (f"/{TOKEN}/", 200, b"<html>runner</html>"),
    (f"/{TOKEN}/wheels/missing.whl", 404, b"not found"),
    ("/wrong-token/wheels/a.wasm", 404, b"not found"),
])
def test_every_response_has_no_store_and_exact_content_length(site: Served, path: str, status: int, expected_body: bytes) -> None:
    got_status, headers, body = site.request(path)

    assert got_status == status
    assert headers["cache-control"] == "no-store"
    assert headers["content-length"] == str(len(expected_body))
    assert body == expected_body


def test_redirect_response_has_no_store_and_zero_length(served: Served) -> None:
    served.server.add_bytes("simple/", b"<html></html>", "text/html; charset=utf-8")

    status, headers, body = served.request(f"/{TOKEN}/simple")

    assert status == 301
    assert headers["cache-control"] == "no-store"
    assert headers["content-length"] == "0"
    assert body == b""


def test_head_returns_file_headers_without_body(site: Served) -> None:
    size = (site.wheels / "a.wasm").stat().st_size
    response = site.raw(f"HEAD /{TOKEN}/wheels/a.wasm HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")

    head, sep, rest = response.partition(b"\r\n\r\n")
    assert sep == b"\r\n\r\n"
    assert rest == b""                       # nothing after the header block
    assert head.startswith(b"HTTP/1.1 200 ")
    lines = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip() for line in head.split(b"\r\n")[1:]}
    assert lines[b"content-length"] == str(size).encode()
    assert lines[b"content-type"] == b"application/wasm"
    assert lines[b"cache-control"] == b"no-store"


def test_head_on_route_and_on_404_has_no_body(site: Served) -> None:
    for path, status, length in ((f"/{TOKEN}/", b"200", b"19"), (f"/{TOKEN}/nope", b"404", b"9")):
        response = site.raw(f"HEAD {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        head, _, rest = response.partition(b"\r\n\r\n")
        assert rest == b""
        assert head.split(b" ")[1] == status
        assert b"Content-Length: " + length in head
        assert b"Cache-Control: no-store" in head


def test_head_via_http_client_matches_get_headers(site: Served) -> None:
    get_status, get_headers, get_body = site.request(f"/{TOKEN}/wheels/b.js")
    head_status, head_headers, head_body = site.request(f"/{TOKEN}/wheels/b.js", method="HEAD")

    assert (get_status, head_status) == (200, 200)
    assert head_body == b""
    assert head_headers["content-length"] == get_headers["content-length"] == str(len(get_body))
    assert head_headers["content-type"] == get_headers["content-type"]


def test_keep_alive_connection_serves_consecutive_requests(site: Served) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", site.server.port, timeout=5)
    try:
        for path, expected in ((f"/{TOKEN}/wheels/b.js", b"console.log(1)"), (f"/{TOKEN}/nope", b"not found"), (f"/{TOKEN}/", b"<html>runner</html>")):
            conn.request("GET", path)
            resp = conn.getresponse()
            assert resp.read() == expected
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Token gate and traversal


@pytest.mark.parametrize("path", [
    "/",
    f"/{TOKEN}",                      # no trailing slash: not the token prefix
    f"/{TOKEN}x/wheels/a.wasm",
    "/wrong/wheels/a.wasm",
    f"/wheels/a.wasm",
    f"/x/{TOKEN}/wheels/a.wasm",
    f"/{TOKEN.upper()}/wheels/a.wasm",
])
def test_requests_outside_token_prefix_are_404(site: Served, path: str) -> None:
    status, headers, body = site.request(path)

    assert status == 404
    assert body == b"not found"
    assert headers["content-type"] == "text/plain"


def _secret_abs(served: Served) -> str:
    return (served.root / "secret.txt").resolve().as_posix()


@pytest.mark.parametrize("suffix", [
    "../secret.txt",
    "..%2fsecret.txt",
    "%2e%2e/secret.txt",
    "%2e%2e%2fsecret.txt",
    "..%5csecret.txt",
    "..\\secret.txt",
    "%2e%2e%5csecret.txt",
    "nested/inner.txt",
    "nested%2finner.txt",
    "nested\\inner.txt",
    "/secret.txt",
    "%2fsecret.txt",
    "..",
    ".hidden",
    "C:/secret.txt",
    "C:\\secret.txt",
    "a%20b.whl",
    "%00a.wasm",
    "a.wasm%00",
])
def test_directory_traversal_attempts_are_404(site: Served, suffix: str) -> None:
    status, _, body = site.request(f"/{TOKEN}/wheels/{suffix}")

    assert status == 404
    assert body == b"not found"
    assert SECRET not in body
    assert b"nested file" not in body
    assert b"dotfile" not in body


def test_absolute_looking_paths_never_escape_served_dir(site: Served) -> None:
    secret = _secret_abs(site)
    for path in (
        f"/{TOKEN}/wheels/{secret}",
        f"/{TOKEN}/wheels/{urllib.parse.quote(secret, safe='')}",
        f"/{TOKEN}/{secret}",
        f"/{TOKEN}//{secret}",
        f"/{TOKEN}/{urllib.parse.quote(secret, safe='')}",
    ):
        status, _, body = site.request(path)
        assert status == 404, path
        assert SECRET not in body


def test_unknown_dir_prefix_is_404(served: Served) -> None:
    (served.root / "pyodide").mkdir()
    (served.root / "pyodide" / "pyodide.js").write_bytes(b"js")

    status, _, _ = served.request(f"/{TOKEN}/pyodide/pyodide.js")   # dir exists on disk but was never added

    assert status == 404


def test_dir_prefix_without_filename_is_404(site: Served) -> None:
    assert site.request(f"/{TOKEN}/wheels")[0] == 404
    assert site.request(f"/{TOKEN}/wheels/")[0] == 404


def test_percent_encoded_safe_name_is_decoded_and_served(served: Served) -> None:
    (served.wheels / "pkg-1.0+local-py3-none-any.whl").write_bytes(b"plus wheel")

    status, _, body = served.request(f"/{TOKEN}/wheels/{urllib.parse.quote('pkg-1.0+local-py3-none-any.whl')}")

    assert status == 200
    assert body == b"plus wheel"


# ---------------------------------------------------------------------------
# build_simple_index


def _wheel(name: str) -> ep.WheelFile:
    return ep.parse_wheel_filename(Path("/vendor/wheels") / name)


def test_build_simple_index_root_lists_normalized_names_with_trailing_slash() -> None:
    pages = ep.build_simple_index([_wheel("Foo_Bar.baz-1.0-py3-none-any.whl"), _wheel("six-1.16.0-py2.py3-none-any.whl")])

    root = pages["simple/"].decode("utf-8")
    assert '<a href="foo-bar-baz/">foo-bar-baz</a>' in root
    assert '<a href="six/">six</a>' in root
    assert root.index("foo-bar-baz/") < root.index("six/")       # sorted
    assert 'pypi:repository-version' in root
    assert set(pages) == {"simple/", "simple/foo-bar-baz/", "simple/six/"}


def test_build_simple_index_project_page_links_with_and_without_hash() -> None:
    hashed = "Foo_Bar.baz-1.0-py3-none-any.whl"
    pages = ep.build_simple_index(
        [_wheel(hashed), _wheel("six-1.16.0-py2.py3-none-any.whl")],
        hashes={hashed: "ab" * 32},
    )

    project = pages["simple/foo-bar-baz/"].decode("utf-8")
    assert f'<a href="../../wheels/{hashed}#sha256={"ab" * 32}">{hashed}</a>' in project
    assert "<h1>Links for foo-bar-baz</h1>" in project

    six = pages["simple/six/"].decode("utf-8")
    assert '<a href="../../wheels/six-1.16.0-py2.py3-none-any.whl">six-1.16.0-py2.py3-none-any.whl</a>' in six
    assert "#sha256" not in six


def test_build_simple_index_groups_versions_of_one_project() -> None:
    pages = ep.build_simple_index([
        _wheel("Pkg-2.0-py3-none-any.whl"),
        _wheel("pkg-1.0-py3-none-any.whl"),
        _wheel("PKG-1.5-1-py3-none-any.whl"),
    ])

    assert set(pages) == {"simple/", "simple/pkg/"}
    project = pages["simple/pkg/"].decode("utf-8")
    assert project.count("../../wheels/") == 3
    assert project.index("PKG-1.5-1") < project.index("Pkg-2.0") < project.index("pkg-1.0")   # sorted by filename


def test_build_simple_index_quotes_filenames_in_hrefs() -> None:
    pages = ep.build_simple_index([_wheel("pkg-1.0+local-py3-none-any.whl")])

    project = pages["simple/pkg/"].decode("utf-8")
    assert 'href="../../wheels/pkg-1.0%2Blocal-py3-none-any.whl"' in project
    assert ">pkg-1.0+local-py3-none-any.whl</a>" in project


def test_build_simple_index_empty_wheel_list_has_only_root() -> None:
    pages = ep.build_simple_index([])

    assert list(pages) == ["simple/"]
    assert b"<a href" not in pages["simple/"]


def test_build_simple_index_hash_lookup_is_by_exact_filename() -> None:
    pages = ep.build_simple_index([_wheel("pkg-1.0-py3-none-any.whl")], hashes={"other-1.0-py3-none-any.whl": "cd" * 32})

    assert b"#sha256" not in pages["simple/pkg/"]


def test_simple_index_is_served_with_redirect_for_missing_slash(served: Served) -> None:
    wheels = [_wheel("Foo_Bar.baz-1.0-py3-none-any.whl"), _wheel("six-1.16.0-py2.py3-none-any.whl")]
    for path, body in ep.build_simple_index(wheels, {"six-1.16.0-py2.py3-none-any.whl": "ef" * 32}).items():
        served.server.add_bytes(path, body, "text/html; charset=utf-8")

    status, headers, body = served.request(f"/{TOKEN}/simple/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert b'<a href="foo-bar-baz/">' in body

    status, headers, body = served.request(f"/{TOKEN}/simple/foo-bar-baz/")
    assert status == 200
    assert b"../../wheels/Foo_Bar.baz-1.0-py3-none-any.whl" in body

    status, headers, body = served.request(f"/{TOKEN}/simple/six")
    assert status == 301
    assert headers["location"] == f"/{TOKEN}/simple/six/"
    assert headers["location"].endswith("/")
    assert body == b""

    status, headers, _ = served.request(f"/{TOKEN}/simple")
    assert status == 301
    assert headers["location"] == f"/{TOKEN}/simple/"

    assert served.request(f"/{TOKEN}/simple/unknown-project/")[0] == 404
    assert served.request(f"/{TOKEN}/simple/unknown-project")[0] == 404


def test_simple_index_redirect_drops_query_string_from_location(served: Served) -> None:
    served.server.add_bytes("simple/pkg/", b"<html></html>", "text/html; charset=utf-8")

    status, headers, _ = served.request(f"/{TOKEN}/simple/pkg?x=1")

    assert status == 301
    assert headers["location"] == f"/{TOKEN}/simple/pkg/"


def test_simple_index_wheel_hrefs_resolve_to_served_wheels(served: Served) -> None:
    wheel_name = "d-1.0-py3-none-any.whl"
    for path, body in ep.build_simple_index([_wheel(wheel_name)]).items():
        served.server.add_bytes(path, body, "text/html; charset=utf-8")
    project_url = f"/{TOKEN}/simple/d/"
    _, _, page = served.request(project_url)
    href = page.decode("utf-8").split('href="', 1)[1].split('"', 1)[0]

    resolved = urllib.parse.urljoin(project_url, href)

    assert resolved == f"/{TOKEN}/wheels/{wheel_name}"
    status, headers, body = served.request(resolved)
    assert status == 200
    assert headers["content-type"] == "application/octet-stream"
    assert body == b"PK wheel"


# ---------------------------------------------------------------------------
# build_mount_zip


def _make_tree(root: Path) -> None:
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "sub" / "mod.py").write_bytes(b"X = 1\n")  # bytes: no newline translation on Windows
    (root / "pkg" / "__pycache__").mkdir()
    (root / "pkg" / "__pycache__" / "mod.cpython-314.pyc").write_bytes(b"pyc")
    (root / "stale.pyc").write_bytes(b"pyc")
    (root / "old.pyo").write_bytes(b"pyo")
    (root / "data.txt").write_text("data", encoding="utf-8")
    for junk in (".git", ".venv", "node_modules", ".pytest_cache"):
        (root / junk).mkdir()
        (root / junk / "junk.py").write_text("", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def test_build_mount_zip_skips_prune_folders_and_bytecode_keeps_nested_packages(tmp_path: Path) -> None:
    _make_tree(tmp_path / "src")

    data, count = ep.build_mount_zip(tmp_path / "src")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert zf.read("pkg/sub/mod.py") == b"X = 1\n"
    assert names == {"pkg/__init__.py", "pkg/sub/__init__.py", "pkg/sub/mod.py", "data.txt"}
    assert count == 4
    assert all("\\" not in name for name in names)
    assert isinstance(data, bytes)


def test_build_mount_zip_honours_all_documented_prune_names(tmp_path: Path) -> None:
    src = tmp_path / "src"
    for junk in sorted(ep.MOUNT_PRUNE):
        (src / junk).mkdir(parents=True)
        (src / junk / "x.py").write_text("", encoding="utf-8")
    (src / "keep.py").write_text("", encoding="utf-8")

    data, count = ep.build_mount_zip(src)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["keep.py"]
    assert count == 1


def test_build_mount_zip_prune_is_by_folder_name_at_any_depth(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "a" / "b" / "__pycache__").mkdir(parents=True)
    (src / "a" / "b" / "__pycache__" / "m.pyc").write_bytes(b"")
    (src / "a" / "b" / "m.py").write_text("", encoding="utf-8")
    (src / "a" / ".git" / "objects").mkdir(parents=True)
    (src / "a" / ".git" / "objects" / "blob").write_bytes(b"")

    data, count = ep.build_mount_zip(src)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["a/b/m.py"]
    assert count == 1


def test_build_mount_zip_custom_prune_set_replaces_default(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "__pycache__").mkdir(parents=True)
    (src / "__pycache__" / "keep.txt").write_text("", encoding="utf-8")
    (src / "skipme").mkdir()
    (src / "skipme" / "x.py").write_text("", encoding="utf-8")

    data, count = ep.build_mount_zip(src, prune=frozenset({"skipme"}))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["__pycache__/keep.txt"]
    assert count == 1


def test_build_mount_zip_empty_directory_yields_valid_empty_zip(tmp_path: Path) -> None:
    src = tmp_path / "empty"
    src.mkdir()

    data, count = ep.build_mount_zip(src)

    assert count == 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == []


def test_build_mount_zip_cap_exceeded_names_no_mount(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "big.bin").write_bytes(b"x" * 2048)

    with pytest.raises(ValueError) as info:
        ep.build_mount_zip(src, cap_bytes=1024)

    exc = info.value
    assert exc.error_class == ep.ERR_MOUNT_TOO_LARGE
    assert "--no-mount" in str(exc)
    assert str(src.resolve()) in str(exc)


def test_build_mount_zip_cap_is_cumulative_across_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for i in range(4):
        (src / f"f{i}.bin").write_bytes(b"x" * 300)   # 1200 bytes total, each file under the cap

    with pytest.raises(ValueError) as info:
        ep.build_mount_zip(src, cap_bytes=1000)
    assert info.value.error_class == ep.ERR_MOUNT_TOO_LARGE

    data, count = ep.build_mount_zip(src, cap_bytes=1200)   # exactly at the cap is allowed
    assert count == 4


def test_build_mount_zip_pruned_and_bytecode_do_not_count_toward_cap(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "pack").write_bytes(b"x" * 5000)
    (src / "big.pyc").write_bytes(b"x" * 5000)
    (src / "small.py").write_text("ok", encoding="utf-8")

    data, count = ep.build_mount_zip(src, cap_bytes=100)

    assert count == 1


@pytest.mark.parametrize("kind", ["file", "missing"])
def test_build_mount_zip_non_directory_is_validation_error(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "thing"
    if kind == "file":
        target.write_text("not a dir", encoding="utf-8")

    with pytest.raises(ValueError) as info:
        ep.build_mount_zip(target)

    assert info.value.error_class == ep.ERR_VALIDATION
    assert "not a directory" in str(info.value)


def test_build_mount_zip_accepts_string_path(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("", encoding="utf-8")

    _, count = ep.build_mount_zip(str(src))

    assert count == 1


# ---------------------------------------------------------------------------
# normalize_name / parse_wheel_filename / WheelFile.pure


@pytest.mark.parametrize("raw, expected", [
    ("Foo_Bar.baz", "foo-bar-baz"),
    ("numpy", "numpy"),
    ("PyYAML", "pyyaml"),
    ("a--b__c..d", "a-b-c-d"),
    ("Zope.Interface", "zope-interface"),
    ("ruamel_yaml_clib", "ruamel-yaml-clib"),
])
def test_normalize_name_is_pep503(raw: str, expected: str) -> None:
    assert ep.normalize_name(raw) == expected


def test_parse_wheel_filename_basic_pure_wheel() -> None:
    wheel = ep.parse_wheel_filename(Path("wheels/tabulate-0.9.0-py3-none-any.whl"))

    assert wheel.name == "tabulate"
    assert wheel.version == "0.9.0"
    assert wheel.python_tags == ("py3",)
    assert wheel.abi_tags == ("none",)
    assert wheel.platform_tags == ("any",)
    assert wheel.path == Path("wheels/tabulate-0.9.0-py3-none-any.whl")
    assert wheel.normalized == "tabulate"
    assert wheel.pure is True


def test_parse_wheel_filename_with_build_tag() -> None:
    wheel = ep.parse_wheel_filename(Path("pkg-1.0-1-py3-none-any.whl"))

    assert (wheel.name, wheel.version) == ("pkg", "1.0")
    assert wheel.python_tags == ("py3",)
    assert wheel.abi_tags == ("none",)
    assert wheel.platform_tags == ("any",)
    assert wheel.pure is True


def test_parse_wheel_filename_with_alphanumeric_build_tag() -> None:
    wheel = ep.parse_wheel_filename(Path("pkg-1.0-12abc-py3-none-any.whl"))

    assert (wheel.name, wheel.version) == ("pkg", "1.0")
    assert wheel.python_tags == ("py3",)


def test_parse_wheel_filename_multi_python_tag() -> None:
    wheel = ep.parse_wheel_filename(Path("six-1.16.0-py2.py3-none-any.whl"))

    assert wheel.python_tags == ("py2", "py3")
    assert wheel.pure is True


def test_parse_wheel_filename_cp314_emscripten_wheel() -> None:
    wheel = ep.parse_wheel_filename(Path("numpy-2.4.6-cp314-cp314-pyemscripten_2026_0_wasm32.whl"))

    assert wheel.name == "numpy"
    assert wheel.version == "2.4.6"
    assert wheel.python_tags == ("cp314",)
    assert wheel.abi_tags == ("cp314",)
    assert wheel.platform_tags == ("pyemscripten_2026_0_wasm32",)
    assert wheel.pure is False


def test_parse_wheel_filename_multi_platform_and_abi_tags() -> None:
    wheel = ep.parse_wheel_filename(Path("x-1.0-cp314-abi3.cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"))

    assert wheel.abi_tags == ("abi3", "cp314")
    assert wheel.platform_tags == ("manylinux_2_17_x86_64", "manylinux2014_x86_64")
    assert wheel.pure is False


def test_parse_wheel_filename_normalizes_distribution_name() -> None:
    wheel = ep.parse_wheel_filename(Path("Foo_Bar.baz-1.0-py3-none-any.whl"))

    assert wheel.name == "Foo_Bar.baz"
    assert wheel.normalized == "foo-bar-baz"


def test_parse_wheel_filename_local_version_is_kept() -> None:
    wheel = ep.parse_wheel_filename(Path("pkg-1.0+local.3-py3-none-any.whl"))

    assert wheel.version == "1.0+local.3"


@pytest.mark.parametrize("name", [
    "notawheel.txt",
    "pkg-1.0.whl",
    "pkg-1.0-py3-none.whl",
    "pkg-1.0-py3-none-any.tar.gz",
    "pkg-1.0-py3-none-any.whl.part",
    "pkg.whl",
    "-1.0-py3-none-any.whl",
    "pkg-1.0-py3-none-any-extra-extra.whl",
    "pkg-1.0-py3-none-any.WHL",
])
def test_parse_wheel_filename_rejects_bad_names(name: str) -> None:
    with pytest.raises(ValueError) as info:
        ep.parse_wheel_filename(Path(name))

    assert info.value.error_class == ep.ERR_VALIDATION
    assert name in str(info.value)


@pytest.mark.parametrize("abi, platform, expected", [
    ("none", "any", True),
    ("none", "win_amd64", False),
    ("cp314", "any", False),
    ("abi3", "any", False),
    ("cp314", "pyemscripten_2026_0_wasm32", False),
    ("none.abi3", "any", True),
    ("none", "any.manylinux_2_17_x86_64", True),
])
def test_wheelfile_pure_only_for_none_any(abi: str, platform: str, expected: str) -> None:
    wheel = ep.parse_wheel_filename(Path(f"pkg-1.0-py3-{abi}-{platform}.whl"))

    assert wheel.pure is expected


def test_wheelfile_is_frozen() -> None:
    wheel = ep.parse_wheel_filename(Path("pkg-1.0-py3-none-any.whl"))

    with pytest.raises(Exception):
        wheel.name = "other"  # type: ignore[misc]


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"edgepy" * 1000)

    import hashlib

    assert ep.sha256_file(path) == hashlib.sha256(b"edgepy" * 1000).hexdigest()
