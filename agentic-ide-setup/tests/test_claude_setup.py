"""Offline checks for claude_setup.py: export, private bundle and restore.

Git runs for real against local bare repositories; Claude, Docker and pip calls go
through a recording fake. No live ~/.claude, network or PowerShell is needed, except
for the one test that CLAUDE_SETUP_LIVE=1 opts into.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude_setup.py"
spec = importlib.util.spec_from_file_location("claude_setup", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

SOURCE = Path(r"C:\Users\alice")
TARGET = Path(r"C:\Users\bob")
ENV = {"ProgramData": r"C:\ProgramData", "ProgramFiles": r"C:\Program Files",
       "ProgramFiles(x86)": r"C:\Program Files (x86)", "SystemRoot": r"C:\WINDOWS"}
KEYS = ["model", "permissions", "hooks", "statusLine", "enabledPlugins", "extraKnownMarketplaces", "env"]
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths and junctions")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
                            cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_origin(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    work = tmp_path / f"{name}-work"
    work.mkdir()
    git(work, "init", "-q")
    for relative, text in files.items():
        write(work / relative, text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    bare = tmp_path / f"{name}.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    git(bare, "config", "uploadpack.allowFilter", "true")
    return bare


class FakeRunner:
    """Records commands; a command containing any word in `fail` exits 1."""

    def __init__(self, fail=()):
        self.calls, self.cwds, self.fail = [], [], set(fail)

    def __call__(self, args, env=None, cwd=None):
        self.calls.append((list(args), env))
        self.cwds.append(cwd)
        failed = bool(self.fail & set(args))
        stdout = "3.13" if args[1:3] == ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"] else ""
        return subprocess.CompletedProcess(args, 1 if failed else 0, stdout=stdout, stderr="boom" if failed else "")

    def commands(self):
        return [args[1:] for args, _ in self.calls if args[1:2] != ["-c"]]


def fake_which(*available):
    return lambda name: f"C:/bin/{name}.exe" if name in available else None


def build_source(tmp_path: Path):
    """A source home with a hook release clone, a plugin repository, an MCP gateway
    and auto-memory, exported into a fresh snapshot. Returns (home, profile, snapshot, origins)."""
    home, profile, snapshot = tmp_path / "source-home", tmp_path / "profile", tmp_path / "snapshot"
    hooks_origin = make_origin(tmp_path, "hooks", {"hooks/guard.py": "print('guard')\n", "docs/readme.md": "docs\n"})
    tool_origin = make_origin(tmp_path, "tool", {"line.py": "print('line')\n", ".claude-plugin/marketplace.json": "{}\n"})
    clone = home / ".claude" / "hooks-release"
    clone.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--no-checkout", str(hooks_origin), str(clone))
    git(clone, "sparse-checkout", "set", "--cone", "hooks")
    git(clone, "switch", "-q", "--detach", "origin/main")
    git(clone, "config", "remote.origin.partialclonefilter", "blob:none")
    git(clone, "remote", "set-url", "origin", "https://example.test/hooks.git")
    tool = home / "Projects" / "tool"
    git(tmp_path, "clone", "-q", str(tool_origin), str(tool))
    git(tool, "remote", "set-url", "origin", "https://example.test/tool.git")
    posix = setup.home_forms(home)[setup.POSIX_MARKER]
    settings = {
        "model": "opus",
        "apiKeyHelper": "never captured",
        "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        "permissions": {"allow": [f"Read(/{posix}/**)", f"Edit({home}\\Projects\\**)"],
                        "additionalDirectories": [str(home / "Projects")]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": f'py "{clone}\\hooks\\guard.py"', "timeout": 10}]}],
                  "SessionEnd": []},
        "statusLine": {"type": "command", "command": f'py "{tool}\\line.py" statusline'},
        "enabledPlugins": {"tool@local": True},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": str(tool)}}},
    }
    write(home / ".claude" / "settings.json", json.dumps(settings, indent=2))
    write(home / ".claude.json", json.dumps({
        "oauthAccount": {"emailAddress": "alice@example.test"},
        "mcpServers": {"MCP_DOCKER": {"type": "stdio", "command": "docker", "args": ["mcp", "gateway", "run"],
                                      "env": {"LOCALAPPDATA": str(home / "AppData" / "Local")}}},
    }))
    write(home / ".docker" / "mcp" / "registry.yaml", 'registry:\n  github:\n    ref: ""\n  playwright:\n    ref: ""\n')
    write(home / ".claude" / "CLAUDE.md", "# rules\nlatest\n")
    write(home / ".claude" / "history.jsonl", "{}\n")
    write(home / ".claude" / "notes.txt", "loose file\n")
    for root in (profile, home / ".claude"):
        write(root / "agents" / "a.md", "agent\n")
        write(root / "skills" / "s" / "SKILL.md", "skill\n")
    write(profile / "CLAUDE.md", "# rules\nold\n")
    write(profile / "hooks" / "guard.py", "repository source\n")
    write(profile / "settings.template.json", json.dumps({"permissions": {"deny": ["Bash(rm -rf *)"], "ask": ["Bash(git push *)"]}}))
    project = setup.encode_project(str(home)) + "-Projects-tool"
    write(home / ".claude" / "projects" / project / "memory" / "MEMORY.md", "- [note](note.md)\n")
    write(home / ".claude" / "projects" / project / "memory" / "note.md", "remember this\n")
    write(home / ".claude" / "projects" / project / "session.jsonl", "{}\n")
    setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    return home, profile, snapshot, {"https://example.test/hooks.git": hooks_origin, "https://example.test/tool.git": tool_origin}


def restore_context(home, profile, snapshot, **overrides):
    lines = []
    options = dict(home=home, apply=True, staged=True, environ=ENV, run=FakeRunner(),
                   which=fake_which("git", "claude", "py", "docker"), out=lines.append,
                   profile=profile, snapshot=snapshot, repo=ROOT.parent)
    options.update(overrides)
    return setup.Restore(**options), lines


def run_restore(ctx, **options):
    return setup.restore(ctx, tools=[], settings_keys=KEYS, **options)


def offline_git(tmp_path: Path, origins: dict[str, Path]):
    """Run real git with the snapshot's https URLs rewritten to the local bare repositories."""
    config = tmp_path / "gitconfig"
    config.write_text("".join(f'[url "{bare.as_uri()}"]\n\tinsteadOf = {url}\n' for url, bare in origins.items()))

    def run(args, env=None, cwd=None):
        merged = dict(env or os.environ, GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(args, env=merged, cwd=cwd, capture_output=True, text=True)
    return run


def git_and_fake_claude(tmp_path, origins):
    real, claude = offline_git(tmp_path, origins), FakeRunner()

    def run(args, env=None, cwd=None):
        return real(args, env, cwd) if Path(args[0]).stem.lower() == "git" else claude(args, env, cwd)
    return run, claude


# --- portable paths -------------------------------------------------------------------

@windows_only
def test_every_home_spelling_round_trips_to_another_home():
    rules = ["Read(//c/Users/alice/**)", "Read(C:/Users/alice/**)", r"Edit(C:\Users\alice\.claude\**)",
             r'py "C:\Users\alice\.claude\hooks-release\x.py"', r"c:\users\ALICE\Projects"]
    portable = [setup.portable_text(rule, SOURCE, ENV) for rule in rules]
    assert not any("alice" in text.lower() for text in portable)
    assert [setup.expand_text(text, TARGET, ENV) for text in portable] == [
        "Read(//c/Users/bob/**)", "Read(C:/Users/bob/**)", r"Edit(C:\Users\bob\.claude\**)",
        r'py "C:\Users\bob\.claude\hooks-release\x.py"', r"C:\Users\bob\Projects"]


@windows_only
def test_only_whole_home_paths_are_replaced():
    assert setup.portable_text(r"C:\Users\alice2\x", SOURCE, ENV) == r"C:\Users\alice2\x"
    assert setup.portable_text(r"C:\Users\alice.old\x", SOURCE, ENV) == r"C:\Users\alice.old\x"


@windows_only
def test_system_folders_follow_the_restoring_machine():
    portable = setup.portable_text(r"C:\Program Files (x86)\a;C:\Program Files\b", SOURCE, ENV)
    assert portable == r"%ProgramFiles(x86)%\a;%ProgramFiles%\b"
    elsewhere = dict(ENV, ProgramFiles=r"E:\Apps", **{"ProgramFiles(x86)": r"E:\Apps32"})
    assert setup.expand_text(portable, TARGET, elsewhere) == r"E:\Apps32\a;E:\Apps\b"
    with pytest.raises(setup.SetupError, match="ProgramData"):
        setup.expand_text("%ProgramData%", TARGET, {})
    # dict(os.environ) on Windows upper-cases every name.
    upper = {name.upper(): value for name, value in ENV.items()}
    assert setup.expand_text("%ProgramData%\\x", TARGET, upper) == r"C:\ProgramData\x"
    assert setup.portable_text(r"C:\ProgramData\x", SOURCE, upper) == r"%ProgramData%\x"


def test_wsl_and_cygwin_paths_are_machine_specific():
    problems = setup.unportable({"a": "/mnt/c/tools/x", "b": "/cygdrive/d/x", "c": "//tmp/**"}, "s")
    assert problems == ["s.a: machine-specific path", "s.b: machine-specific path"]


@windows_only
def test_profile_markers_expand_like_the_powershell_installer(tmp_path):
    data = b'{"p": "__USERPROFILE__\\\\x", "q": "__LOCALAPPDATA__\\\\y"}'
    expanded = json.loads(setup.expand_profile_bytes(data, tmp_path, json_escape=True))
    assert expanded == {"p": str(tmp_path / "x"), "q": str(tmp_path / "AppData" / "Local" / "y")}
    assert setup.expand_profile_bytes(b"no markers \xff", tmp_path, json_escape=False) == b"no markers \xff"
    with pytest.raises(setup.SetupError, match="not UTF-8"):
        setup.expand_profile_bytes(b"__USERPROFILE__ \xff", tmp_path, json_escape=False)
    assert setup.to_profile_text(r'C:\Users\alice\a C:\\Users\\alice\\b C:/Users/alice/c', SOURCE) == \
        r"__USERPROFILE__\a __USERPROFILE__\\b __USERPROFILE__/c"


# --- credential checks ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["GITHUB_TOKEN", "authToken", "accessToken", "clientSecret", "GH_PAT",
                                  "AZURE_DEVOPS_EXT_PAT", "X-Functions-Key", "Ocp-Apim-Subscription-Key",
                                  "ANTHROPIC_API_KEY", "Authorization", "OAuth", "db_password"])
def test_credential_names_are_recognized(name):
    assert setup.credential_name(name)


@pytest.mark.parametrize("name", ["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                                  "LOCALAPPDATA", "ProgramData", "statusMessage", "author", "keybindings",
                                  "extraKnownMarketplaces", "agentPushNotifEnabled"])
def test_ordinary_names_are_not_credentials(name):
    assert not setup.credential_name(name)


@windows_only
@pytest.mark.parametrize("live", [
    {"permissions": {"allow": ['Bash(curl -H "Authorization: Bearer abcdefgh12345678" https://x.test)']}},
    {"permissions": {"allow": ["Bash(az login --password hunter2)"]}},
    {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": 'curl -H "X-Api-Token: abc" https://x.test'}]}]}},
    {"env": {"HTTPS_PROXY": "http://svc:Winter2024@proxy:8080"}},
    {"env": {"authToken": "x"}},
    {"env": {"AZURE_DEVOPS_EXT_PAT": "x"}},
    {"env": {"OPENAI": "sk-abcdefghijklmnopqrstuvwxyz"}},
    {"statusLine": {"type": "command", "command": r"C:\Users\alice\srv.js --apikey Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9"}},
    {"extraKnownMarketplaces": {"m": {"source": {"source": "url", "url": "https://x.test/m.json?sig=abc"}}}},
])
def test_export_settings_scans_every_captured_field(live):
    _, _, problems = setup.export_settings(live, KEYS, {}, SOURCE, ENV)
    assert problems
    assert not any(secret in " ".join(problems) for secret in ("hunter2", "Winter2024", "abcdefgh1234", "Ab1Cd2"))


@windows_only
def test_secret_scan_leaves_ordinary_settings_alone():
    live = {
        "env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"},
        "permissions": {"allow": ["Bash(git push *--force-*)", "Bash(docker login --password-stdin)",
                                  "Bash(tool --path ./x)", "Skill(update-config:*)",
                                  "mcp__cf514a11-bd65-4fe6-932b-647c42e01df3__send_message",
                                  "Bash(git -C * push *:refs/heads/production*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "statusMessage": "Checking team closeout",
                                       "command": r'py "C:\Users\alice\.claude\hooks-release\x\stop_team_closeout.py"'}]}]},
    }
    _, _, problems = setup.export_settings(live, KEYS, {}, SOURCE, ENV)
    assert problems == []


@windows_only
def test_export_settings_keeps_listed_keys_and_adds_reviewed_restrictions():
    live = {"model": "opus", "apiKeyHelper": r"C:\x\helper.cmd",
            "permissions": {"deny": ["a"], "allow": ["Read(//c/Users/alice/**)"]}}
    template, uncaptured, problems = setup.export_settings(
        live, ["model", "permissions"], {"deny": ["a", "b", "b"], "ask": ["c"]}, SOURCE, ENV)
    assert uncaptured == ["apiKeyHelper"]
    assert template == {"model": "opus",
                        "permissions": {"deny": ["a", "b"], "allow": ["Read(/__USERPROFILE_POSIX__/**)"], "ask": ["c"]}}
    assert problems == []


@windows_only
def test_export_settings_refuses_credentials_and_machine_paths_without_echoing_them():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    live = {"env": {"GITHUB_TOKEN": "x", "OTHER": secret, "MAX_OUTPUT_TOKENS": "64000"},
            "statusLine": {"command": r"D:\tools\line.exe"}}
    _, _, problems = setup.export_settings(live, ["env", "statusLine"], {}, SOURCE, ENV)
    assert "settings.env.GITHUB_TOKEN: credential-named entry" in problems
    assert "settings.env.OTHER: a known token format" in problems
    assert "settings.statusLine.command: machine-specific path" in problems
    assert not any("MAX_OUTPUT_TOKENS" in p for p in problems)
    assert not any(secret in p for p in problems)


@windows_only
def test_export_mcp_makes_paths_portable_and_refuses_credentials():
    config = {
        "mcpServers": {
            "gateway": {"type": "stdio", "command": "docker", "args": ["mcp", "gateway", "run"],
                        "env": {"LOCALAPPDATA": r"C:\Users\alice\AppData\Local", "ProgramData": r"C:\ProgramData"}},
            "remote": {"type": "http", "url": "https://example.test/mcp",
                       "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz0123"}},
        },
        "projects": {r"C:\Users\alice\proj": {"mcpServers": {"local-one": {}}}},
    }
    servers, notes, problems = setup.export_mcp(config, SOURCE, ENV)
    assert servers["mcpServers"] == {"gateway": {
        "type": "stdio", "command": "docker", "args": ["mcp", "gateway", "run"],
        "env": {"LOCALAPPDATA": r"__USERPROFILE__\AppData\Local", "ProgramData": "%ProgramData%"}}}
    assert problems == ["mcpServers.remote.headers.Authorization: credential-named entry"]
    assert any("local-one" in note for note in notes)
    servers, notes, problems = setup.export_mcp(config, SOURCE, ENV, exclude=["remote", "missing"])
    assert list(servers["mcpServers"]) == ["gateway"]
    assert problems == ["--exclude-mcp missing: no such user-scope MCP server"]
    assert setup.uses_docker_gateway(servers["mcpServers"]["gateway"])


@windows_only
@pytest.mark.parametrize("server", [
    {"command": "srv", "args": ["--token", "hunter2"]},
    {"type": "http", "url": "https://user:pw@mcp.example/x"},
    {"type": "http", "url": "https://mcp.example/x?api_key=short1"},
    {"type": "http", "url": "https://mcp.example/x", "oauth": {"clientSecret": "s3cr3t-ab"}},
    {"command": r"C:\Users\alice\srv.js", "args": ["--apikey", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9"]},
])
def test_export_mcp_scans_every_field_and_argument_list(server):
    servers, _, problems = setup.export_mcp({"mcpServers": {"s": server}}, SOURCE, ENV)
    assert problems and servers["mcpServers"] == {}


def test_docker_registry_names(tmp_path):
    registry = write(tmp_path / "registry.yaml", 'registry:\n  azure:\n    ref: ""\n  github:\n    ref: ""\nother:\n  x:\n')
    assert setup.docker_servers(registry) == ["azure", "github"]
    assert setup.docker_servers(tmp_path / "missing.yaml") is None
    assert setup.docker_servers(write(tmp_path / "empty.yaml", "registry: {}\n")) == []
    assert setup.docker_servers(write(tmp_path / "other.yaml", "servers:\n  - azure\n")) is None


def test_referenced_paths_cover_code_not_patterns_or_data():
    settings = {
        "permissions": {"allow": ["Read(__USERPROFILE__\\**)"]},
        "hooks": {"Stop": [{"hooks": [{"command": 'py "__USERPROFILE__\\My Tools\\guard.py" --flag'}]}]},
        "statusLine": {"command": "py __USERPROFILE__\\line.py statusline"},
        "extraKnownMarketplaces": {"m": {"source": {"source": "directory", "path": "__USERPROFILE__\\Projects\\m"}}},
    }
    servers = {"s": {"command": "__USERPROFILE__\\bin\\server.exe", "env": {"DATA": "__USERPROFILE__\\data"}}}
    assert setup.referenced_paths(settings, servers) == {
        "__USERPROFILE__\\My Tools\\guard.py", "__USERPROFILE__\\line.py",
        "__USERPROFILE__\\Projects\\m", "__USERPROFILE__\\bin\\server.exe"}


@windows_only
def test_repositories_need_a_shareable_origin(tmp_path):
    home = tmp_path / "home"
    origin = make_origin(tmp_path, "r", {"a.py": "x\n"})
    clone = home / "r"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    _, _, problems = setup.locate_repositories({"__USERPROFILE__\\r\\a.py"}, home, {})
    assert problems == ["__USERPROFILE__\\r: origin is a local path that another machine cannot clone"]
    for url in ("https://user:hunter2@example.test/r.git", "https://ghp_abcdefghijklmnop1234@github.com/o/r.git"):
        git(clone, "remote", "set-url", "origin", url)
        _, _, problems = setup.locate_repositories({"__USERPROFILE__\\r\\a.py"}, home, {})
        assert problems == ["__USERPROFILE__\\r: the origin URL embeds a user name or credential"]
    write(home / "loose" / "tool.py", "x\n")
    (home / "folder").mkdir()
    repos, notes, problems = setup.locate_repositories(
        {"__USERPROFILE__\\loose\\tool.py", "__USERPROFILE__\\folder", "__USERPROFILE__\\gone.py"}, home, {})
    assert repos == []
    assert problems == ["__USERPROFILE__\\loose\\tool.py: a file outside any git clone, so restore cannot recreate it"]
    assert len(notes) == 2


# --- export end to end ----------------------------------------------------------------

@windows_only
def test_export_writes_a_portable_snapshot_and_refreshes_guidance(tmp_path):
    home, profile, snapshot, _ = build_source(tmp_path)
    settings = json.loads((snapshot / setup.SETTINGS_FILE).read_text(encoding="utf-8"))
    texts = [(snapshot / name).read_text(encoding="utf-8") for name in setup.SNAPSHOT_FILES]
    assert not any("pytest" in text or "alice@" in text for text in texts)
    assert setup.unportable(settings, "settings") == []
    assert "apiKeyHelper" not in settings
    assert settings["permissions"]["deny"] == ["Bash(rm -rf *)"] and settings["permissions"]["ask"] == ["Bash(git push *)"]
    assert settings["permissions"]["allow"][0] == "Read(/__USERPROFILE_POSIX__/**)"
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == 'py "__USERPROFILE__\\.claude\\hooks-release\\hooks\\guard.py"'
    mcp = json.loads((snapshot / setup.MCP_FILE).read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["MCP_DOCKER"]["env"] == {"LOCALAPPDATA": "__USERPROFILE__\\AppData\\Local"}
    pinned = git(home / ".claude" / "hooks-release", "rev-parse", "HEAD")
    assert json.loads((snapshot / setup.REPOS_FILE).read_text(encoding="utf-8")) == [
        {"path": "__USERPROFILE__\\.claude\\hooks-release", "url": "https://example.test/hooks.git",
         "filter": "blob:none", "sparse": ["hooks"], "commit": pinned},
        {"path": "__USERPROFILE__\\Projects\\tool", "url": "https://example.test/tool.git", "branch": "main"},
    ]
    assert (snapshot / setup.DOCKER_FILE).read_text(encoding="utf-8") == "github\nplaywright\n"
    assert (profile / "CLAUDE.md").read_text(encoding="utf-8") == "# rules\nlatest\n"
    again = setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    assert again.written == []
    assert again.unknown == ["notes.txt"] and "history.jsonl" in again.left_behind
    assert again.uncaptured_settings == ["apiKeyHelper"]


@windows_only
def test_export_stops_on_drift_or_credentials_and_writes_nothing(tmp_path):
    home, profile, snapshot, _ = build_source(tmp_path)
    before = {path: path.read_bytes() for path in [*snapshot.iterdir(), profile / "CLAUDE.md"]}
    write(home / ".claude" / "CLAUDE.md", "# rules\nnewer\n")
    write(home / ".claude" / "agents" / "extra.md", "local only\n")
    with pytest.raises(setup.SetupError, match="not in the profile"):
        setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    (home / ".claude" / "agents" / "extra.md").unlink()
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    settings["env"]["ANTHROPIC_API_KEY"] = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    write(home / ".claude" / "settings.json", json.dumps(settings))
    with pytest.raises(setup.SetupError) as error:
        setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    assert "ANTHROPIC_API_KEY" in str(error.value) and "abcdefghij" not in str(error.value)
    assert {path: path.read_bytes() for path in before} == before


@windows_only
@pytest.mark.parametrize("guidance, reason", [
    ("# rules\nuse ghp_abcdefghijklmnopqrstuvwxyz0123456789 for the API\n", "CLAUDE.md: line 2: a known token format"),
    ("# rules\nnotes live in /mnt/c/Users/source-home/notes\n", "CLAUDE.md: still contains a path with your user name"),
])
def test_export_scans_the_guidance_it_publishes(tmp_path, guidance, reason):
    home, profile, snapshot, _ = build_source(tmp_path)
    write(home / ".claude" / "CLAUDE.md", guidance)
    with pytest.raises(setup.SetupError) as error:
        setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    assert reason in str(error.value) and "ghp_abc" not in str(error.value)
    assert (profile / "CLAUDE.md").read_text(encoding="utf-8") == "# rules\nlatest\n"


@windows_only
def test_export_keeps_the_docker_list_when_the_registry_is_unreadable(tmp_path):
    home, profile, snapshot, _ = build_source(tmp_path)
    write(home / ".docker" / "mcp" / "registry.yaml", "servers:\n  - something\n")
    result = setup.export(home, profile=profile, snapshot=snapshot, settings_keys=KEYS, environ={})
    assert (snapshot / setup.DOCKER_FILE).read_text(encoding="utf-8") == "github\nplaywright\n"
    assert any("keeps its previous list" in note for note in result.notes)


# --- private bundle -------------------------------------------------------------------

@windows_only
def test_bundle_round_trip_remaps_projects_to_the_new_home(tmp_path):
    source, target = tmp_path / "Users" / "alice", tmp_path / "Users" / "bob"
    prefix = setup.encode_project(str(source))
    projects = source / ".claude" / "projects"
    write(projects / f"{prefix}-Projects-x" / "memory" / "MEMORY.md", "upper\n")
    write(projects / ("c" + prefix[1:] + "-Projects-y") / "memory" / "a.md", "lower drive\n")
    write(projects / "D--work-z" / "memory" / "b.md", "elsewhere\n")
    write(projects / f"{prefix}-Projects-x" / "memory" / "leak.md", 'api_token = "abcdefghijklmnopqrstuvwxyz"\n')
    write(projects / f"{prefix}-Projects-x" / "transcript.jsonl", "{}\n")
    count, left_out = setup.write_bundle(source, tmp_path / "out" / "bundle.zip")
    assert count == 3 and len(left_out) == 1 and "leak.md" in left_out[0]
    with zipfile.ZipFile(tmp_path / "out" / "bundle.zip") as archive:
        assert not any("transcript" in name or "leak" in name for name in archive.namelist())

    ctx, lines = restore_context(target, tmp_path / "p", tmp_path / "s")
    setup.step_memory(ctx, tmp_path / "out" / "bundle.zip")
    remapped = setup.encode_project(str(target))
    restored = target / ".claude" / "projects"
    assert (restored / f"{remapped}-Projects-x" / "memory" / "MEMORY.md").read_text() == "upper\n"
    assert (restored / ("c" + remapped[1:] + "-Projects-y") / "memory" / "a.md").read_text() == "lower drive\n"
    assert (restored / "D--work-z" / "memory" / "b.md").read_text() == "elsewhere\n"
    write(restored / f"{remapped}-Projects-x" / "memory" / "MEMORY.md", "edited on the new machine\n")
    setup.step_memory(ctx, tmp_path / "out" / "bundle.zip")
    assert lines[-1].strip() == "0 added, 0 replaced, 2 unchanged, 1 kept (differ; --overwrite replaces)"
    ctx.overwrite = True
    setup.step_memory(ctx, tmp_path / "out" / "bundle.zip")
    memory = restored / f"{remapped}-Projects-x" / "memory"
    assert (memory / "MEMORY.md").read_text() == "upper\n"
    assert [p.read_text() for p in memory.glob("MEMORY.md" + setup.BACKUP_TAG + "*")] == ["edited on the new machine\n"]


def test_bundle_refuses_existing_or_git_tracked_destinations(tmp_path):
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    with pytest.raises(setup.SetupError, match="git work tree"):
        setup.write_bundle(tmp_path / "home", repo / "nested" / "bundle.zip")
    assert not (repo / "nested").exists()
    # A .git that git itself cannot read (another owner, a broken gitfile) still counts.
    write(tmp_path / "outer" / ".git", "gitdir: nowhere\n")
    with pytest.raises(setup.SetupError, match="git work tree"):
        setup.write_bundle(tmp_path / "home", tmp_path / "outer" / "x" / "bundle.zip")
    write(tmp_path / "taken.zip", "x")
    with pytest.raises(setup.SetupError, match="already exists"):
        setup.write_bundle(tmp_path / "home", tmp_path / "taken.zip")


def bundle_with(tmp_path: Path, members: dict[str, bytes], listed: dict[str, bytes] | None = None) -> Path:
    path = tmp_path / "bundle.zip"
    listed = members if listed is None else listed
    meta = {"format": 1, "sourceHome": r"C:\Users\alice",
            "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in listed.items()]}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bundle.json", json.dumps(meta))
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("name", [
    "projects/x/memory/../../evil.md", "projects/x/notes/a.md", "/projects/x/memory/a.md",
    "projects/x/memory/CON", "projects/x/memory/nul.txt", "projects/x/memory/con .md", "projects/x/memory/NUL .txt",
    "projects/C:/memory/a.md", "projects/x/memory/trailing.", "projects/../memory/a.md",
    # zipfile turns backslashes into "/" on Windows; elsewhere safe_member refuses them.
    "projects/x/memory/..\\..\\evil.md",
])
def test_read_bundle_rejects_unsafe_members(tmp_path, name):
    with pytest.raises(setup.SetupError, match="unsafe path"):
        setup.read_bundle(bundle_with(tmp_path, {name: b"x"}))


def test_read_bundle_rejects_tampering(tmp_path):
    with pytest.raises(setup.SetupError, match="checksum"):
        setup.read_bundle(bundle_with(tmp_path, {"projects/x/memory/a.md": b"new"}, {"projects/x/memory/a.md": b"old"}))
    with pytest.raises(setup.SetupError, match="do not match"):
        setup.read_bundle(bundle_with(tmp_path, {}, {"projects/x/memory/a.md": b"old"}))
    write(tmp_path / "plain.zip", "not a zip")
    with pytest.raises(setup.SetupError, match="cannot read"):
        setup.read_bundle(tmp_path / "plain.zip")


@pytest.mark.parametrize("name, expected", [
    ("C--Users-alice", "C--Users-bob"),
    ("C--Users-alice-Projects-x", "C--Users-bob-Projects-x"),
    ("c--Users-alice-Projects-x", "c--Users-bob-Projects-x"),
    ("C--Users-alice2-Projects-x", "C--Users-alice2-Projects-x"),
    ("D--work-x", "D--work-x"),
])
def test_remap_project(name, expected):
    assert setup.remap_project(name, r"C:\Users\alice", Path(r"C:\Users\bob")) == expected


# --- settings merge -------------------------------------------------------------------

def test_merge_settings_unions_lists_and_the_template_wins_values():
    existing = {"theme": "light", "permissions": {"allow": ["A"], "deny": ["X"]}, "custom": 1}
    template = {"theme": "dark", "permissions": {"allow": ["A", "B", "B"]}, "env": {"K": "1"}}
    merged = setup.merge_settings(existing, template)
    assert merged == {"theme": "dark", "permissions": {"allow": ["A", "B"], "deny": ["X"]}, "custom": 1, "env": {"K": "1"}}
    assert setup.merge_settings(merged, template) == merged
    assert existing["permissions"]["allow"] == ["A"]  # inputs are not mutated


def test_merge_hooks_replaces_the_same_group_and_never_registers_twice():
    def group(command, **extra):
        return {"hooks": [{"type": "command", "command": command, **extra}]}
    existing = {"hooks": {"Stop": [group("py a.py", timeout=5), group("py mine.py")]}}
    template = {"hooks": {"Stop": [group("py a.py", timeout=10)], "SessionEnd": []}}
    merged = setup.merge_settings(existing, template)
    assert merged["hooks"] == {"Stop": [group("py a.py", timeout=10), group("py mine.py")], "SessionEnd": []}
    assert setup.merge_settings(merged, template) == merged


def test_describe_changes_prints_everything_in_full_except_long_restriction_lists():
    long_rule = "Bash(" + "x" * 200 + " && payload)"
    lines = setup.describe_changes({"theme": "light"}, {"theme": "dark", "permissions": {
        "allow": [f"rule {n}" for n in range(65)] + [long_rule], "deny": [f"rule {n}" for n in range(61)]}})
    assert lines[0] == "~ theme: light -> dark"
    assert f"+ permissions.allow: {long_rule}" in lines and len(lines) == 1 + 66 + 1
    assert lines[-1] == "+ permissions.deny: 61 entries (restrictions)"


# --- restore --------------------------------------------------------------------------

@windows_only
def test_restore_preview_changes_nothing_and_shows_what_it_would_run(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    target = tmp_path / "new-home"
    ctx, lines = restore_context(target, profile, snapshot, apply=False)
    assert run_restore(ctx) == 0, "\n".join(lines)
    assert not target.exists()
    assert ctx.run.commands() == []
    output = "\n".join(lines)
    clone = target / ".claude" / "hooks-release"
    assert f'hook     Stop: py "{clone}\\hooks\\guard.py"' in output
    assert f"clone    https://example.test/hooks.git at commit " in output
    assert f"would run: git clone --filter=blob:none --no-checkout -- https://example.test/hooks.git {clone}" in output
    assert "would run: claude plugin marketplace add -- " in output
    assert "would run: claude mcp add-json --scope user -- MCP_DOCKER" in output
    assert "Preview only: nothing changed." in output


@windows_only
def test_restore_staged_home_end_to_end_with_real_git(tmp_path):
    _, profile, snapshot, origins = build_source(tmp_path)
    target = tmp_path / "Users" / "bob"
    run, claude_calls = git_and_fake_claude(tmp_path, origins)
    which = lambda name: {"git": "git", "claude": "C:/bin/claude.exe"}.get(name)  # noqa: E731
    ctx, lines = restore_context(target, profile, snapshot, run=run, which=which)
    assert run_restore(ctx) == 0, "\n".join(lines)

    clone = target / ".claude" / "hooks-release"
    pinned = json.loads((snapshot / setup.REPOS_FILE).read_text(encoding="utf-8"))[0]["commit"]
    assert (clone / "hooks" / "guard.py").is_file() and not (clone / "docs").exists()  # sparse
    assert git(clone, "config", "--get", "remote.origin.partialclonefilter") == "blob:none"
    assert git(clone, "rev-parse", "HEAD") == pinned
    assert not list(clone.parent.glob("hooks-release.restore-*"))
    assert git(target / "Projects" / "tool", "symbolic-ref", "--short", "HEAD") == "main"
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == f'py "{clone}\\hooks\\guard.py"'
    assert settings["permissions"]["allow"][0] == f"Read(/{setup.home_forms(target)[setup.POSIX_MARKER]}/**)"
    assert settings["permissions"]["additionalDirectories"] == [str(target / "Projects")]
    assert (target / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == "# rules\nlatest\n"
    assert (target / ".claude" / "agents" / "a.md").is_file()
    assert not (target / ".claude" / "hooks").exists()
    assert not (target / ".claude" / setup.SETTINGS_FILE).exists()
    tool = str(target / "Projects" / "tool")
    mcp = {"type": "stdio", "command": "docker", "args": ["mcp", "gateway", "run"],
           "env": {"LOCALAPPDATA": str(target / "AppData" / "Local")}}
    assert claude_calls.commands() == [
        ["plugin", "marketplace", "add", "--", tool],
        ["plugin", "install", "--scope", "user", "--", "tool@local"],
        ["mcp", "add-json", "--scope", "user", "--", "MCP_DOCKER", json.dumps(mcp)],
    ]
    assert all(env["CLAUDE_CONFIG_DIR"] == str(target / ".claude") for _, env in claude_calls.calls)
    assert claude_calls.cwds == [target] * 3  # never the project settings of the caller's directory
    output = "\n".join(lines)
    assert "Docker Desktop's servers are machine-wide" in output and "no drift" in output

    # A rerun changes nothing and finds the clones in place.
    write(target / ".claude" / ".claude.json", json.dumps({"mcpServers": {"MCP_DOCKER": mcp}}))
    write(target / ".claude" / "plugins" / "known_marketplaces.json", json.dumps({"local": {}}))
    write(target / ".claude" / "plugins" / "installed_plugins.json", json.dumps({"plugins": {"tool@local": []}}))
    before = (target / ".claude" / "settings.json").read_bytes()
    rerun, rerun_lines = restore_context(target, profile, snapshot, run=run, which=which)
    assert run_restore(rerun) == 0, "\n".join(rerun_lines)
    assert (target / ".claude" / "settings.json").read_bytes() == before
    assert len(claude_calls.calls) == 3
    assert not list((target / ".claude").glob("settings.json" + setup.BACKUP_TAG + "*"))

    # A hook edited in place in the release clone fails verification.
    write(clone / "hooks" / "guard.py", "print('edited in place')\n")
    check, _ = restore_context(target, profile, snapshot, run=run, which=which)
    setup.step_verify(check, setup.load_snapshot(check, KEYS).repos, setup.load_snapshot(check, KEYS).settings)
    assert any("local edits" in failure for failure in check.failures)


@windows_only
def test_a_failed_clone_step_leaves_settings_alone_and_a_rerun_repairs_it(tmp_path):
    _, profile, snapshot, origins = build_source(tmp_path)
    target = tmp_path / "Users" / "bob"
    real = offline_git(tmp_path, origins)

    def sparse_fails(args, env=None, cwd=None):
        if "sparse-checkout" in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated failure")
        return real(args, env, cwd)
    which = lambda name: {"git": "git"}.get(name)  # noqa: E731
    ctx, lines = restore_context(target, profile, snapshot, run=sparse_fails, which=which)
    assert run_restore(ctx) == 1
    clone = target / ".claude" / "hooks-release"
    assert not clone.exists()
    assert any("partial clone is left at" in failure for failure in ctx.failures)
    assert any("settings.json left unchanged" in failure for failure in ctx.failures)
    assert not (target / ".claude" / "settings.json").exists()  # no hook can point at a missing script

    rerun, rerun_lines = restore_context(target, profile, snapshot, run=real, which=which)
    assert run_restore(rerun) == 0, "\n".join(rerun_lines)
    assert (clone / "hooks" / "guard.py").is_file()
    assert (target / ".claude" / "settings.json").is_file()


@windows_only
def test_restore_real_home_runs_machine_wide_steps(tmp_path):
    home, profile, snapshot, _ = build_source(tmp_path)
    write(profile / "skills" / "agent-browser" / "SKILL.md", "browser skill\n")
    write(home / ".docker" / "mcp" / "registry.yaml", 'registry:\n  github:\n    ref: ""\n')
    config = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    config["mcpServers"]["MCP_DOCKER"]["env"]["LOCALAPPDATA"] = "changed by hand"
    write(home / ".claude.json", json.dumps(config))
    ctx, lines = restore_context(home, profile, snapshot, staged=False,
                                 which=fake_which("git", "claude", "py", "docker"))
    assert run_restore(ctx, skip=["repositories", "verify"]) == 0, "\n".join(lines)
    commands = ctx.run.commands()
    assert ["mcp", "server", "enable", "playwright"] in commands
    pip = next(env for args, env in ctx.run.calls if args[1:4] == ["-m", "pip", "install"])
    assert pip["PIP_INDEX_URL"] == "https://pypi.org/simple" and pip["PIP_EXTRA_INDEX_URL"] == ""
    assert not any(args[:2] == ["mcp", "add-json"] for args in commands)  # a differing server is left alone
    assert any("differs  MCP_DOCKER" in line for line in lines)
    assert all(env is None for args, env in ctx.run.calls if args[1:2] in (["plugin"], ["mcp"]) and "server" not in args)


@windows_only
def test_restore_keeps_differing_files_unless_asked_and_backs_them_up(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    target = tmp_path / "new-home"
    write(target / ".claude" / "agents" / "a.md", "my local agent\n")
    write(target / ".claude" / "skills" / "s" / "SKILL.md", "skill\r\n")  # line endings alone are not a difference
    ctx, lines = restore_context(target, profile, snapshot)
    setup.step_files(ctx)
    assert (target / ".claude" / "agents" / "a.md").read_text() == "my local agent\n"
    assert any("kept     agents/a.md" in line for line in lines)
    assert not list((target / ".claude" / "skills" / "s").glob("*" + setup.BACKUP_TAG + "*"))
    ctx.overwrite = True
    setup.step_files(ctx)
    assert (target / ".claude" / "agents" / "a.md").read_text() == "agent\n"
    backups = list((target / ".claude" / "agents").glob("a.md" + setup.BACKUP_TAG + "*"))
    assert [path.read_text() for path in backups] == ["my local agent\n"]


@windows_only
def test_restore_reports_a_bad_file_and_keeps_installing_the_rest(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    (profile / "agents" / "bad.md").write_bytes(b"__USERPROFILE__ \xff")
    target = tmp_path / "new-home"
    ctx, _ = restore_context(target, profile, snapshot)
    setup.step_files(ctx)
    assert len(ctx.failures) == 1 and "agents/bad.md" in ctx.failures[0] and "not UTF-8" in ctx.failures[0]
    assert (target / ".claude" / "agents" / "a.md").is_file()
    settings = write(target / ".claude" / "settings.json", "null")
    setup.step_settings(ctx, setup.load_snapshot(ctx, KEYS).settings, [])
    assert "does not hold a JSON object" in ctx.failures[-1] and settings.read_text() == "null"


@windows_only
def test_restore_refuses_to_write_through_a_junction(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    target, outside = tmp_path / "new-home", tmp_path / "outside"
    outside.mkdir()
    (target / ".claude").mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(target / ".claude" / "agents"), str(outside)],
                   check=True, capture_output=True)
    ctx, lines = restore_context(target, profile, snapshot)
    assert run_restore(ctx, skip=["repositories", "plugins", "mcp", "verify"]) == 1
    assert any("reparse point" in failure for failure in ctx.failures)
    assert list(outside.iterdir()) == []


@windows_only
def test_restore_settings_notices_a_concurrent_writer(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    target = tmp_path / "new-home"
    settings = write(target / ".claude" / "settings.json", '{"theme": "light"}')

    def out(line):
        if line.startswith("  +") and settings.read_text() == '{"theme": "light"}':
            settings.write_text('{"theme": "another writer"}')
    ctx, _ = restore_context(target, profile, snapshot, out=out)
    setup.step_settings(ctx, setup.load_snapshot(ctx, KEYS).settings, [])
    assert settings.read_text() == '{"theme": "another writer"}'
    assert ctx.failures and "changed while restore was running" in ctx.failures[0]


@windows_only
def test_restore_refuses_batch_shims_with_cmd_metacharacters(tmp_path):
    ctx, _ = restore_context(tmp_path / "home", tmp_path / "p", tmp_path / "s",
                             which=lambda name: "C:/npm/claude.cmd")
    assert not ctx.command(["claude", "mcp", "add-json", "x", '{"args": ["a&b"]}'])
    assert "batch shim" in ctx.failures[0] and ctx.run.calls == []


def test_plugin_install_waits_for_its_marketplace(tmp_path):
    ctx, lines = restore_context(tmp_path / "home", tmp_path / "p", tmp_path / "s")
    missing = tmp_path / "missing"
    setup.step_plugins(ctx, {
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": str(missing)}}},
        "enabledPlugins": {"tool@local": True, "other@official": True, "off@local": False}})
    assert ctx.failures == [f"marketplace local: {missing} is missing"]
    assert any("skipped  plugin tool@local: marketplace local was not added" in line for line in lines)
    assert ctx.run.commands() == [["plugin", "install", "--scope", "user", "--", "other@official"]]


def test_one_unwritable_memory_file_does_not_stop_the_rest(tmp_path):
    bundle = bundle_with(tmp_path, {"projects/C--x/memory/a.md": b"a", "projects/C--x/memory/b.md": b"b"})
    memory = tmp_path / "home" / ".claude" / "projects" / "C--x" / "memory"
    (memory / "a.md").mkdir(parents=True)  # a folder where the file belongs
    ctx, _ = restore_context(tmp_path / "home", tmp_path / "p", tmp_path / "s")
    setup.step_memory(ctx, bundle)
    assert len(ctx.failures) == 1 and "a.md" in ctx.failures[0]
    assert (memory / "b.md").read_bytes() == b"b"


@windows_only
def test_restore_rejects_a_bad_bundle_or_snapshot_before_writing(tmp_path):
    _, profile, snapshot, _ = build_source(tmp_path)
    target = tmp_path / "new-home"
    write(tmp_path / "bad.zip", "not a zip")
    ctx, _ = restore_context(target, profile, snapshot)
    with pytest.raises(setup.SetupError, match="cannot read private bundle"):
        run_restore(ctx, bundle=tmp_path / "bad.zip")
    assert not target.exists()
    write(snapshot / setup.REPOS_FILE, json.dumps([{"path": r"C:\Users\alice\x", "url": "https://example.test/x.git"}]))
    with pytest.raises(setup.SetupError, match="not portable"):
        run_restore(ctx)
    assert not target.exists()


def _set(path, value):
    def change(tree):
        node = tree
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return change


@windows_only
@pytest.mark.parametrize("file_name, change", [
    (setup.REPOS_FILE, _set([0, "path"], "__USERPROFILE__\\..\\..\\Windows\\x")),
    (setup.REPOS_FILE, _set([1, "path"], "__USERPROFILE__\\.ssh")),
    (setup.REPOS_FILE, _set([1, "path"], "__USERPROFILE__\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\x")),
    (setup.REPOS_FILE, _set([1, "path"], "__USERPROFILE__\\Projects\\x:ads")),
    (setup.REPOS_FILE, _set([1, "path"], "__USERPROFILE__\\Projects\\a\\b")),
    (setup.REPOS_FILE, _set([0, "url"], "--upload-pack=calc.exe")),
    (setup.REPOS_FILE, _set([0, "url"], "https://ghp_abcdefghijklmnop1234@github.com/o/r.git")),
    (setup.REPOS_FILE, _set([1, "branch"], "--orphan")),
    (setup.REPOS_FILE, _set([0, "commit"], "HEAD~1")),
    (setup.REPOS_FILE, _set([1, "commit"], "0" * 40)),
    (setup.REPOS_FILE, _set([0, "sparse"], ["../outside"])),
    (setup.MCP_FILE, _set(["mcpServers", "--scope"], {})),
    (setup.SETTINGS_FILE, _set(["apiKeyHelper"], "evil.cmd")),
    (setup.SETTINGS_FILE, _set(["permissions", "defaultMode"], "bypassPermissions")),
    (setup.SETTINGS_FILE, _set(["permissions", "something"], [])),
    (setup.SETTINGS_FILE, _set(["enabledPlugins", "-x@local"], True)),
    (setup.SETTINGS_FILE, _set(["extraKnownMarketplaces", "local", "source", "path"], "__USERPROFILE__\\Downloads\\m")),
    (setup.SETTINGS_FILE, _set(["extraKnownMarketplaces", "gh"], {"source": {"source": "github", "repo": "-x/y"}})),
])
def test_restore_refuses_a_tampered_snapshot_before_running_anything(tmp_path, file_name, change):
    _, profile, snapshot, _ = build_source(tmp_path)
    data = json.loads((snapshot / file_name).read_text(encoding="utf-8"))
    change(data)
    write(snapshot / file_name, json.dumps(data))
    target = tmp_path / "new-home"
    ctx, _ = restore_context(target, profile, snapshot)
    with pytest.raises(setup.SetupError, match="not safe to run"):
        run_restore(ctx)
    assert ctx.run.calls == [] and not target.exists()


def test_inventory_classifies_every_entry(tmp_path):
    claude = tmp_path / ".claude"
    for name in ("CLAUDE.md", "settings.json", ".credentials.json", "settings.json.routing-backup-1", ".last-cleanup", "mine.txt"):
        write(claude / name, "x")
    captured, left, unknown = setup.classify_home(claude)
    assert [line.split(":")[0] for line in captured] == ["CLAUDE.md", "settings.json"]
    assert left == [".credentials.json", ".last-cleanup", "settings.json.routing-backup-1"]
    assert unknown == ["mine.txt"]


def test_cli_restore_preview_leaves_the_target_alone(tmp_path):
    target = tmp_path / "preview-home"
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "restore", "--home", str(target)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preview" in result.stdout and not target.exists()


def test_cli_apply_without_a_terminal_needs_yes(tmp_path):
    target = tmp_path / "apply-home"
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "restore", "--home", str(target), "--apply"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--apply --yes" in result.stderr and not target.exists()


@pytest.mark.skipif(os.environ.get("CLAUDE_SETUP_LIVE") != "1" or not shutil.which("claude"),
                    reason="set CLAUDE_SETUP_LIVE=1 to run against the real claude CLI")
def test_live_claude_config_dir_keeps_the_real_config_untouched(tmp_path):
    real = Path.home() / ".claude.json"
    before = json.loads(real.read_text(encoding="utf-8")).get("mcpServers")
    home = tmp_path / "home"
    home.mkdir()
    ctx, lines = restore_context(home, tmp_path / "p", tmp_path / "s", run=setup.run_command, which=shutil.which)
    assert ctx.claude(["mcp", "add-json", "--scope", "user", "--", "isolation-probe",
                       json.dumps({"type": "stdio", "command": "echo"})]), lines
    staged = json.loads((home / ".claude" / ".claude.json").read_text(encoding="utf-8"))
    assert "isolation-probe" in staged["mcpServers"]
    assert json.loads(real.read_text(encoding="utf-8")).get("mcpServers") == before
