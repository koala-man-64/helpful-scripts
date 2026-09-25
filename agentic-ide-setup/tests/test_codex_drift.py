"""Codex source drift and duplicate discovery behavior."""

import importlib.util
import os
from pathlib import Path
import subprocess
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_codex_drift.py"
SPEC = importlib.util.spec_from_file_location("check_codex_drift", SCRIPT)
drift = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drift)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return path


def test_owned_files_only_and_line_endings(tmp_path):
    home = tmp_path / "user"
    profile = tmp_path / "profile"
    codex = home / ".codex"
    write(profile / "AGENTS.md", "hello\n")
    write(codex / "AGENTS.md", "hello\r\n")
    write(profile / "skills" / "x" / "SKILL.md", "owned\n")
    write(codex / "skills" / "x" / "SKILL.md", "owned\n")
    write(codex / "skills" / "local" / "SKILL.md", "local\n")
    write(codex / "config.toml", "personality = 'plain'\n")
    assert drift.owned_file_drift(profile, codex, home) == []
    write(codex / "skills" / "x" / "SKILL.md", "changed\n")
    assert len(drift.owned_file_drift(profile, codex, home)) == 1


def test_duplicate_discovery_and_preserve_unrelated_config(tmp_path):
    home = tmp_path / "user"
    codex, agents = home / ".codex", home / ".agents"
    write(codex / "skills" / "shared" / "SKILL.md", "codex")
    write(agents / "skills" / "shared" / "SKILL.md", "other client")
    write(agents / "skills" / "unique" / "SKILL.md", "other client")
    duplicates = drift.duplicate_paths(codex, agents)
    assert len(duplicates) == 1
    config = "personality = 'plain'\n\n[features]\nmulti_agent = true\n"
    updated = drift.disabled_config(config, duplicates)
    assert updated.startswith(config)
    assert 'unique/SKILL.md' not in updated
    assert drift.enabled_duplicates(updated, duplicates) == []
    assert drift.disabled_config(updated, duplicates) == updated


def test_existing_enabled_entry_changed_without_changing_other_sections(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "shared" / "SKILL.md"
    key = drift.skill_path(str(skill))
    config = (f'[[skills.config]]\npath = "{skill.as_posix()}"\nenabled = true # review\n'
              '\n[[skills.config]]\npath = "other/SKILL.md"\nenabled = true\n'
              '\n[desktop]\ntheme = "dark"\n')
    updated = drift.disabled_config(config, {key: skill})
    assert 'enabled = false # review' in updated
    assert 'path = "other/SKILL.md"\nenabled = true' in updated
    assert '[desktop]\ntheme = "dark"' in updated
    assert drift.disabled_config(updated, {key: skill}) == updated


def test_existing_disabled_duplicate_and_new_duplicate(tmp_path):
    first = tmp_path / "first" / "SKILL.md"
    second = tmp_path / "second" / "SKILL.md"
    duplicates = {drift.skill_path(str(path)): path for path in (first, second)}
    config = f'[[skills.config]]\npath = "{first.as_posix()}"\nenabled = false\n'
    updated = drift.disabled_config(config, duplicates)
    assert updated.count("[[skills.config]]") == 2
    assert drift.enabled_duplicates(updated, duplicates) == []


def test_main_previews_without_writing(tmp_path, capsys):
    home = tmp_path / "user"
    profile = tmp_path / "profile"
    write(profile / "AGENTS.md", "current\n")
    (profile / "skills").mkdir()
    write(home / ".codex" / "AGENTS.md", "current\n")
    write(home / ".codex" / "skills" / "shared" / "SKILL.md", "skill")
    write(home / ".agents" / "skills" / "shared" / "SKILL.md", "skill")
    config = write(home / ".codex" / "config.toml", "personality = 'plain'\n")
    assert drift.main(["--profile", str(profile), "--home", str(home)]) == 1
    assert config.read_text() == "personality = 'plain'\n"
    assert "duplicate skill enabled" in capsys.readouterr().out
    assert drift.main(["--profile", str(profile), "--home", str(home), "--apply-dedup"]) == 0
    assert list(config.parent.glob("config.toml.agentic-ide-setup-backup-*"))


def setup_home(tmp_path):
    profile, home = tmp_path / "profile", tmp_path / "user"
    write(profile / "AGENTS.md", "current\n")
    write(profile / "skills/shared/SKILL.md", "skill\n")
    write(home / ".codex/AGENTS.md", "current\n")
    write(home / ".codex/skills/shared/SKILL.md", "skill\n")
    write(home / ".agents/skills/shared/SKILL.md", "old skill\n")
    config = write(home / ".codex/config.toml", "model='unchanged'\n")
    return profile, home, config, ["--profile", str(profile), "--home", str(home), "--apply-dedup"]


@pytest.mark.parametrize("relative", ["AGENTS.md", "skills/shared/SKILL.md"])
@pytest.mark.parametrize("missing", [True, False])
def test_apply_refuses_canonical_drift_without_writes(tmp_path, relative, missing):
    profile, home, config, args = setup_home(tmp_path)
    target = home / ".codex" / relative
    if missing:
        target.unlink()
    else:
        target.write_text("drift")
    original = config.read_bytes()
    assert drift.main(args) == 2
    assert config.read_bytes() == original
    assert not list(config.parent.glob("config.toml.*"))


def test_canonical_missing_from_profile_is_error(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    (profile / "AGENTS.md").unlink()
    assert drift.main(args) == 2
    assert not list(config.parent.glob("config.toml.*"))


@pytest.mark.parametrize("change", ["config", "guidance", "identity"])
def test_rechecks_immediately_before_replace(tmp_path, monkeypatch, change):
    profile, home, config, args = setup_home(tmp_path)
    original = config.read_bytes()
    actual_fsync = drift.os.fsync
    calls = 0
    def race(fd):
        nonlocal calls
        actual_fsync(fd)
        calls += 1
        if calls == 1:
            if change == "config":
                config.write_bytes(original + b"[hooks.state]\ntrusted_hash='fresh-desktop-state'\n")
            elif change == "guidance":
                (home / ".codex/AGENTS.md").write_text("concurrent change")
            else:
                replacement = config.with_suffix(".replacement")
                replacement.write_bytes(original)
                drift.os.replace(replacement, config)
    monkeypatch.setattr(drift.os, "fsync", race)
    assert drift.main(args) == 2
    assert config.read_bytes() == (original + b"[hooks.state]\ntrusted_hash='fresh-desktop-state'\n" if change == "config" else original)
    assert not list(config.parent.glob("config.toml.*"))


def test_backup_preserves_exact_bom_and_crlf_bytes(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    original = b"\xef\xbb\xbfmodel = 'unchanged'\r\n[features]\r\nmulti_agent = true\r\n"
    config.write_bytes(original)
    assert drift.main(args) == 0
    backup, = config.parent.glob("config.toml.agentic-ide-setup-backup-*")
    assert backup.read_bytes() == original
    assert config.read_bytes().startswith(original.rstrip(b"\r\n"))
    assert drift.main(args) == 0
    assert len(list(config.parent.glob("config.toml.agentic-ide-setup-backup-*"))) == 1


def test_non_owned_differing_duplicate_is_never_disabled(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    (profile / "skills/shared/SKILL.md").unlink()
    assert drift.main(args) == 2
    assert config.read_text() == "model='unchanged'\n"
    assert not list(config.parent.glob("config.toml.*"))


def test_disabled_codex_and_agents_settings_are_preserved(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    disabled = "".join(f"[[skills.config]]\npath='{(home / root / 'skills/shared/SKILL.md').as_posix()}'\nenabled=false\n" for root in (".codex", ".agents"))
    config.write_text(disabled)
    assert drift.main(args) == 0
    assert config.read_text() == disabled
    assert not list(config.parent.glob("config.toml.*"))


def test_candidate_cannot_modify_multiline_string_contents(tmp_path):
    path = tmp_path / "SKILL.md"
    config = f'''note = """\n[[skills.config]]\npath = '{path.as_posix()}'\nenabled = true\n"""\n'''
    with pytest.raises(ValueError):
        drift.disabled_config(config, {drift.skill_path(str(path)): path})


def test_reparse_ancestor_refused(tmp_path, monkeypatch):
    profile, home, config, args = setup_home(tmp_path)
    actual = Path.lstat
    class Reparse:
        st_mode = 0o40755
        st_file_attributes = 0x400
    def lstat(path):
        return Reparse() if path == config.parent else actual(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    assert drift.main(args) == 2
    assert config.read_text() == "model='unchanged'\n"


def test_replace_failure_retains_original_and_backup(tmp_path, monkeypatch):
    profile, home, config, args = setup_home(tmp_path)
    original = config.read_bytes()
    def fail(*args):
        raise OSError("replacement failed")
    monkeypatch.setattr(drift.os, "replace", fail)
    assert drift.main(args) == 2
    assert config.read_bytes() == original
    assert not list(config.parent.glob("*.tmp-*"))
    backup, = config.parent.glob("config.toml.agentic-ide-setup-backup-*")
    assert backup.read_bytes() == original


def test_portable_markers_match_installer_expansion(tmp_path):
    text = '__USERPROFILE__/tool\n__APPDATA__/tool\n__LOCALAPPDATA__/tool\r\n'
    assert drift.normalized(text, tmp_path) == f'{tmp_path}/tool\n{tmp_path / "AppData/Roaming"}/tool\n{tmp_path / "AppData/Local"}/tool\n'


def test_unexpanded_installed_marker_is_drift(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    for root in (profile, home / ".codex"):
        write(root / "AGENTS.md", "__USERPROFILE__/tool\n")
    assert drift.main(args) == 2
    assert not list(config.parent.glob("config.toml.*"))
    write(home / ".codex/AGENTS.md", f"{home}/tool\n")
    assert drift.main(args) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive filesystem")
def test_windows_mixed_case_duplicate_names(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    write(home / ".codex/skills/UPPER/SKILL.md", "identical")
    write(home / ".agents/skills/upper/SKILL.md", "identical")
    assert drift.main(args) == 0
    assert '/upper/SKILL.md' in config.read_text()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL preservation")
def test_protected_windows_acl_is_preserved(tmp_path):
    profile, home, config, args = setup_home(tmp_path)
    user = subprocess.check_output(["whoami"], text=True).strip()
    subprocess.run(["icacls", str(config), "/inheritance:r", "/grant:r", f"{user}:(F)"], check=True, capture_output=True)
    permissions = drift.file_permissions(config)
    assert drift.main(args) == 0
    backup, = config.parent.glob("config.toml.agentic-ide-setup-backup-*")
    assert drift.file_permissions(config) == permissions
    assert drift.file_permissions(backup) == permissions


def test_post_replace_permission_mismatch_is_not_success(tmp_path, monkeypatch, capsys):
    profile, home, config, args = setup_home(tmp_path)
    actual_replace, actual_permissions = drift.os.replace, drift.file_permissions
    replaced = False
    def replace(*args):
        nonlocal replaced
        actual_replace(*args)
        replaced = True
    def permissions(path):
        return b"unexpected permissions" if replaced and path == config else actual_permissions(path)
    monkeypatch.setattr(drift.os, "replace", replace)
    monkeypatch.setattr(drift, "file_permissions", permissions)
    assert drift.main(args) == 2
    assert "post-replacement verification failed" in capsys.readouterr().err
    assert list(config.parent.glob("config.toml.agentic-ide-setup-backup-*"))
