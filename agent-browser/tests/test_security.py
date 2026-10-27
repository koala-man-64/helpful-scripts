"""Client-side path/URL policy (_check_url, _check_local_path) and the static-hint contract.

_check_url and _check_local_path never touch the daemon or the network - they only look at
the string and the local filesystem. _drive_is_fixed is monkeypatched to True throughout the
_check_local_path tests so they behave the same on a network/removable drive as on C:.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import agent_browser as ab
from agent_browser_core import common as core_common

def _paths(profile: str = "default", home: Path | None = None) -> core_common.ProfilePaths:
    return core_common.ProfilePaths(profile, home or Path("C:/agent-browser-home"))

# ---------------------------------------------------------------------------
# _check_url

def test_check_url_normalizes_scheme_and_requires_a_url() -> None:
    assert core_common._check_url("example.com", _paths(), {}) == "https://example.com"  # scheme-less -> https
    assert core_common._check_url("https://example.com/a?b=1", _paths(), {}) == "https://example.com/a?b=1"  # unchanged
    with pytest.raises(ValueError) as exc:
        core_common._check_url("", _paths(), {})
    assert exc.value.error_class == core_common.ERR_USAGE

@pytest.mark.parametrize("scheme_url", ["javascript:alert(1)", "file:///C:/secrets.txt", "edge://settings", "ftp://x.com/f"])
def test_check_url_refuses_disallowed_schemes(scheme_url: str) -> None:
    with pytest.raises(ValueError) as exc:
        core_common._check_url(scheme_url, _paths(), {})
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_scheme")

def test_check_url_about_blank_is_public_but_data_is_for_internal_doctor_profile_only() -> None:
    assert core_common._check_url("about:blank", _paths("default"), {}) == "about:blank"
    assert core_common._check_url("about:blank", _paths("_doctor"), {}) == "about:blank"
    raw = "data:text/html,<b>hi</b>"
    assert core_common._check_url(raw, _paths("_doctor"), {}) == raw
    for profile in ("_live", "default"):
        with pytest.raises(ValueError) as exc:
            core_common._check_url(raw, _paths(profile), {})
        assert exc.value.error_class == core_common.ERR_GUARDED

def test_check_url_allowed_hosts_including_subdomains_and_lookalike_rejection() -> None:
    config = {"allowed_hosts": ["example.com"]}
    assert core_common._check_url("https://example.com/x", _paths(), config) == "https://example.com/x"
    assert core_common._check_url("https://sub.example.com/x", _paths(), config) == "https://sub.example.com/x"
    assert core_common._check_url("https://anything.example", _paths(), {}) == "https://anything.example"  # no allowlist configured
    for bad in ("https://evil.com/x", "https://notexample.com/x"):  # unrelated host + lookalike suffix
        with pytest.raises(ValueError) as exc:
            core_common._check_url(bad, _paths(), config)
        assert exc.value.error_class == core_common.ERR_GUARDED
        assert exc.value.hint == core_common._hint("guarded_host")

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
    assert core_common._host_allowed(host, allowed) is expected

# ---------------------------------------------------------------------------
# _check_local_path

@pytest.fixture(autouse=True)
def _fixed_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "_drive_is_fixed", lambda path: True)

def test_check_local_path_requires_a_path() -> None:
    with pytest.raises(ValueError) as exc:
        core_common._check_local_path("", _paths(), {})
    assert exc.value.error_class == core_common.ERR_USAGE

def test_check_local_path_refuses_unc_hidden_and_out_of_root_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "elsewhere" / "f.txt"
    for candidate in (r"\\server\share\f.txt", str(tmp_path / ".secret" / "f.txt"), str(outside)):
        with pytest.raises(ValueError) as exc:
            core_common._check_local_path(candidate, _paths(), {}, must_exist=False)
        assert exc.value.error_class == core_common.ERR_GUARDED

@pytest.mark.parametrize("suffix", [".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".jks"])
def test_check_local_path_refuses_key_material(suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError) as exc:
        core_common._check_local_path(str(tmp_path / f"secret{suffix}"), _paths(), {}, must_exist=False)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_path")

def test_check_local_path_accepts_cwd_and_upload_root_must_exist_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "report.txt"
    target.write_text("hi", encoding="utf-8")
    assert core_common._check_local_path("report.txt", _paths(), {}) == target.resolve()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    upload_target = uploads / "f.csv"
    upload_target.write_text("a,b", encoding="utf-8")
    resolved = core_common._check_local_path(str(upload_target), _paths(), {"upload_roots": [str(uploads)]})
    assert resolved == upload_target.resolve()

    with pytest.raises(ValueError) as exc:
        core_common._check_local_path("missing.txt", _paths(), {})  # must_exist=True (default)
    assert exc.value.error_class == core_common.ERR_NOT_FOUND

    missing_ok = core_common._check_local_path("missing.txt", _paths(), {}, must_exist=False)
    assert missing_ok == (tmp_path / "missing.txt").resolve() and not missing_ok.exists()

def test_upload_roots_add_to_cwd_and_session_roots_instead_of_replacing_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd, other = tmp_path / "cwd", tmp_path / "exports"
    for folder in (cwd, other):
        folder.mkdir()
        (folder / "f.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(cwd)
    paths = _paths(home=tmp_path / "home")
    for config in ({"upload_roots": [str(other)]}, {"upload_roots": []}):
        assert core_common._check_local_path("f.txt", paths, config) == (cwd / "f.txt").resolve()
    assert core_common._check_local_path(str(other / "f.txt"), paths, {"upload_roots": [str(other)]}) == (other / "f.txt").resolve()

def test_check_local_path_refuses_non_fixed_drive_with_guided_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_common, "_drive_is_fixed", lambda path: False)
    _refused(tmp_path / "f.txt", _paths(), {}, text="network and removable drives are refused", must_exist=False)

# ---------------------------------------------------------------------------
# _check_local_path: the profile store, the caller base and the hidden-name rule

STORE_TEXT = "agent-browser state folder"

def _store(tmp_path: Path, session_id: str | None = None) -> core_common.ProfilePaths:
    """A profile store holding the token-bearing files and a cookie DB, as a running daemon leaves it."""
    paths = core_common.ProfilePaths("default", tmp_path / "home", session_id)
    (paths.user_data / "Default").mkdir(parents=True)
    for target in _store_files(paths):
        target.write_text('{"token": "daemon-token"}', encoding="utf-8")
    return paths

def _store_files(paths: core_common.ProfilePaths) -> list[Path]:
    return [paths.session, paths.launch, paths.user_data / "Default" / "Cookies", paths.root / "config.json"]

def _refused(candidate: str | Path, paths: core_common.ProfilePaths, config: dict, text: str = STORE_TEXT, **kwargs) -> None:
    with pytest.raises(ValueError) as exc:
        core_common._check_local_path(str(candidate), paths, config, **kwargs)
    assert exc.value.error_class == core_common.ERR_GUARDED
    assert exc.value.hint == core_common._hint("guarded_path")
    assert text in str(exc.value)

def _dir_link(link: Path, target: Path) -> None:
    """A directory junction on Windows (no privilege needed), a symlink elsewhere."""
    try:
        if os.name == "nt":
            import _winapi  # noqa: PLC0415
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
    except (OSError, ImportError, AttributeError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")

def test_check_local_path_refuses_profile_store_even_when_cwd_is_inside_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _store(tmp_path)
    monkeypatch.chdir(paths.dir)  # where the daemon runs
    for target in _store_files(paths):
        _refused(target, paths, {})
        _refused(target, paths, {"upload_roots": [str(paths.dir)]})
    _refused("session.json", paths, {})

def test_check_local_path_refuses_profile_store_when_caller_cwd_is_an_ancestor_of_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The default home sits under %LOCALAPPDATA% and an MCP server often runs from the user's home:
    # nothing on that route is dot-named, so the store deny is the only protection.
    paths = _store(tmp_path)
    monkeypatch.chdir(tmp_path)
    for target in _store_files(paths):
        _refused(target, paths, {})
        _refused(target, paths, {}, base=tmp_path)
    _refused(Path("home") / "profiles" / "default" / "launch.json", paths, {})
    _refused(tmp_path / "home" / "PROFILES" / "default" / "session.json", paths, {}, must_exist=False)  # case-insensitive filesystems

def test_check_local_path_allows_only_this_sessions_downloads_and_shots_inside_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for session_id in (None, "agent-a"):
        paths = core_common.ProfilePaths("default", tmp_path / "home", session_id)
        for own in (paths.downloads / "report.csv", paths.shots / "page.png"):
            own.parent.mkdir(parents=True, exist_ok=True)
            own.write_text("x", encoding="utf-8")
            assert core_common._check_local_path(str(own), paths, {}) == own.resolve()
            assert core_common._check_local_path(str(own), paths, {}, base=None) == own.resolve()
    paths = core_common.ProfilePaths("default", tmp_path / "home", "agent-a")
    manual = core_common.ProfilePaths("default", tmp_path / "home")
    other = core_common.ProfilePaths("default", tmp_path / "home", "agent-b")
    for foreign in (manual.downloads / "report.csv", other.shots / "x.png", paths.outputs / "c.body", paths.last_snapshot):
        _refused(foreign, paths, {}, must_exist=False)

def test_check_local_path_explicit_base_replaces_process_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _store(tmp_path)
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "mine.txt").write_text("x", encoding="utf-8")
    (paths.dir / "only-in-daemon-cwd.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(paths.dir)
    assert core_common._check_local_path("mine.txt", paths, {}, base=caller) == (caller / "mine.txt").resolve()
    with pytest.raises(ValueError) as exc:
        core_common._check_local_path("only-in-daemon-cwd.txt", paths, {}, base=caller)
    assert exc.value.error_class == core_common.ERR_NOT_FOUND
    _refused(paths.session, paths, {}, base=caller)
    _refused(paths.dir / "only-in-daemon-cwd.txt", paths, {}, base=caller)

def test_check_local_path_without_base_refuses_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = core_common.ProfilePaths("default", tmp_path / "home")
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for relative_base in (None, Path("relative")):
        with pytest.raises(ValueError) as exc:
            core_common._check_local_path("payload.json", paths, {}, base=relative_base)
        assert exc.value.error_class == core_common.ERR_VALIDATION
    shot = paths.shots / "a.png"
    assert core_common._check_local_path(str(shot), paths, {}, must_exist=False, base=None) == shot.resolve()
    # No caller cwd means no cwd root at all, not the process cwd.
    _refused(tmp_path / "payload.json", paths, {}, text="outside the allowed folders", base=None)

@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative paths")
def test_check_local_path_refuses_other_drive_relative_path_after_the_join(tmp_path: Path) -> None:
    drive = "E:" if tmp_path.drive.upper() == "D:" else "D:"
    with pytest.raises(ValueError) as exc:
        core_common._check_local_path(f"{drive}payload.json", _paths(home=tmp_path / "home"), {}, base=tmp_path)
    assert exc.value.error_class == core_common.ERR_VALIDATION

def _device_prefixed(path: Path) -> list[str]:
    if os.name == "nt":
        return ["\\\\?\\" + str(path), "//?/" + path.as_posix()]
    return ["/" + path.as_posix()]  # POSIX keeps a leading // through resolve()

def test_check_local_path_refuses_device_prefixed_base_and_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # resolve() keeps a \\?\ prefix, so a prefixed cwd or root never matched the unprefixed store.
    paths = _store(tmp_path)
    user = tmp_path / "user"
    (user / ".aws" / "cli").mkdir(parents=True)
    (user / ".aws" / "cli" / "x.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core_common.Path, "home", staticmethod(lambda: user))
    monkeypatch.chdir(tmp_path)
    for base in _device_prefixed(paths.root):
        for candidate in ("profiles/default/launch.json", "profiles/default/evil.png", "config.json.new"):
            _refused(candidate, paths, {}, text="UNC and device paths", base=base, must_exist=False)
    for base in _device_prefixed(user / ".aws" / "cli"):
        _refused("x.json", paths, {}, text="UNC and device paths", base=base)
    for root in _device_prefixed(paths.dir):
        _refused(root + "/launch.json", paths, {"upload_roots": [root]}, text="UNC and device paths")

@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate data streams")
def test_check_local_path_refuses_alternate_data_streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for candidate in ("srv.pem:x", "report.txt:hidden", "shot.png:x"):
        _refused(candidate, _paths(home=tmp_path / "home"), {}, text="Alternate data streams", must_exist=False)

def test_check_local_path_home_dot_rule_fails_closed_without_a_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")
    keys = tmp_path / "user" / ".ssh" / "keys"
    keys.mkdir(parents=True)
    (keys / "id_rsa").write_text("x", encoding="utf-8")
    paths = core_common.ProfilePaths("default", tmp_path / ".agent-browser")
    monkeypatch.setattr(core_common.Path, "home", staticmethod(no_home))
    monkeypatch.chdir(keys)
    _refused("id_rsa", paths, {}, text="Hidden")
    shot = paths.shots / "a.png"
    assert core_common._check_local_path(str(shot), paths, {}, must_exist=False) == shot.resolve()

def test_check_local_path_refuses_links_into_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _store(tmp_path)
    caller = tmp_path / "caller"
    caller.mkdir()
    _dir_link(caller / "linked", paths.dir)
    monkeypatch.chdir(caller)
    _refused(Path("linked") / "session.json", paths, {})
    file_link = caller / "innocent.json"
    try:
        file_link.symlink_to(paths.launch)
    except OSError:
        return  # file symlinks need Developer Mode or admin rights on Windows; the junction above covers links
    _refused("innocent.json", paths, {})

def test_check_local_path_allows_cwd_below_hidden_ancestor_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / ".claude" / "worktrees" / "w"
    worktree.mkdir(parents=True)
    readme = worktree / "README.md"
    readme.write_text("x", encoding="utf-8")
    monkeypatch.chdir(worktree)
    paths = _paths(home=tmp_path / "home")
    assert core_common._check_local_path("README.md", paths, {}) == readme.resolve()
    assert core_common._check_local_path(str(readme), paths, {}) == readme.resolve()
    assert core_common._check_local_path("README.md", paths, {}, base=worktree) == readme.resolve()

def test_check_local_path_still_refuses_hidden_names_at_and_below_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = tmp_path / ".claude" / "worktrees" / "w"
    names = (".env", ".git/config", "sub/.hidden/x.txt", ".SSH/id")
    for name in names:
        (worktree / name).parent.mkdir(parents=True, exist_ok=True)
        (worktree / name).write_text("x", encoding="utf-8")
    paths = _paths(home=tmp_path / "home")
    monkeypatch.chdir(worktree)
    for name in names:
        _refused(name, paths, {}, text="Hidden")
    dotroot = tmp_path / ".dotroot"
    dotroot.mkdir()
    (dotroot / "ok.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(dotroot)
    _refused("ok.txt", paths, {}, text="Hidden")  # the root's own name counts

def test_check_local_path_hidden_rule_counts_only_names_at_or_below_a_matching_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    stage = project / ".stage" / "in"
    stage.mkdir(parents=True)
    (stage / "f.txt").write_text("x", encoding="utf-8")
    (stage / ".x").write_text("x", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    paths = _paths(home=tmp_path / "home")
    config = {"upload_roots": [str(stage)]}
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert core_common._check_local_path(str(stage / "f.txt"), paths, config) == (stage / "f.txt").resolve()
    _refused(stage / ".x", paths, config, text="Hidden")
    # cwd sees .stage below it, the upload root does not: one clean matching root is enough.
    monkeypatch.chdir(project)
    _refused(stage / "f.txt", paths, {}, text="Hidden")
    assert core_common._check_local_path(str(stage / "f.txt"), paths, config) == (stage / "f.txt").resolve()

def test_check_local_path_allows_default_shots_path_under_a_dot_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = core_common.ProfilePaths("default", tmp_path / ".agent-browser", "agent-a")  # the POSIX layout
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    shot = paths.shots / "abc.png"
    assert core_common._check_local_path(str(shot), paths, {}, must_exist=False) == shot.resolve()
    assert core_common._check_local_path(str(shot), paths, {}, must_exist=False, base=None) == shot.resolve()

def test_check_local_path_refuses_dot_folders_directly_under_the_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = tmp_path / "user"
    monkeypatch.setattr(core_common.Path, "home", staticmethod(lambda: user))
    paths = core_common.ProfilePaths("default", user / ".agent-browser")
    for folder, name in ((".ssh/keys", "id_rsa"), (".config/gh", "hosts.yml"), (".AWS/cli", "credentials")):
        secret_dir = user / folder
        secret_dir.mkdir(parents=True)
        (secret_dir / name).write_text("x", encoding="utf-8")
        monkeypatch.chdir(secret_dir)
        _refused(name, paths, {}, text="Hidden")
        _refused(secret_dir / name, paths, {"upload_roots": [str(secret_dir)]}, text="Hidden")
    notes = user / ".claude" / "task-notes"
    notes.mkdir(parents=True)
    (notes / "n.md").write_text("x", encoding="utf-8")
    monkeypatch.chdir(notes)
    assert core_common._check_local_path("n.md", paths, {}) == (notes / "n.md").resolve()
    shot = paths.shots / "a.png"
    assert core_common._check_local_path(str(shot), paths, {}, must_exist=False) == shot.resolve()
    # A cwd or upload root reached through a link: resolve() follows it, so the rule sees ~/.ssh.
    caller = tmp_path / "caller"
    caller.mkdir()
    _dir_link(caller / "keys", user / ".ssh" / "keys")
    _refused(caller / "keys" / "id_rsa", paths, {"upload_roots": [str(caller / "keys")]}, text="Hidden")
    monkeypatch.chdir(caller / "keys")
    _refused("id_rsa", paths, {}, text="Hidden")

# ---------------------------------------------------------------------------
# HINTS / _hint: every hint is a fully-rendered static template

def test_every_hint_renders_without_leftover_placeholders() -> None:
    for key in core_common.HINTS:
        rendered = core_common._hint(key, "sample-profile")
        assert isinstance(rendered, str) and rendered
        assert "{" not in rendered and "}" not in rendered
    assert core_common._hint("not_running", "acme") == f"Run: {core_common.TOOL_NAME} start --profile acme"
    with pytest.raises(KeyError):
        core_common._hint("no-such-hint-key")

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
