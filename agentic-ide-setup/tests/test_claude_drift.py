"""Unit checks for check_claude_drift.py; no PowerShell or live ~/.claude needed."""
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_claude_drift", ROOT / "scripts" / "check_claude_drift.py")
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)


def write(path: Path, text: str, newline: str = "\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def profile_and_home(tmp_path: Path) -> tuple[Path, Path]:
    profile, home = tmp_path / "profile", tmp_path / "home"
    for root, newline in ((profile, "\n"), (home, "\r\n")):
        write(root / "agents" / "a.md", "agent\nbody\n", newline)
        write(root / "skills" / "s" / "SKILL.md", "skill\n", newline)
        write(root / "CLAUDE.md", "rules\n", newline)
    return profile, home


def test_line_endings_alone_are_not_drift(tmp_path):
    profile, home = profile_and_home(tmp_path)
    assert drift.copy_drift(profile, home) == []


def test_copy_drift_reports_missing_extra_and_changed(tmp_path):
    profile, home = profile_and_home(tmp_path)
    write(profile / "agents" / "new.md", "not installed\n")
    write(home / "agents" / "local.md", "edited in place\n")
    write(home / "skills" / "s" / "SKILL.md", "changed\n")
    write(home / "CLAUDE.md", "changed rules\n")
    problems = drift.copy_drift(profile, home)
    assert any("missing from" in p and p.endswith("new.md") for p in problems)
    assert any("not in the profile" in p and p.endswith("local.md") for p in problems)
    assert any("differs from the profile" in p and "SKILL.md" in p for p in problems)
    assert any("CLAUDE.md" in p for p in problems)
    assert len(problems) == 4


def test_caches_synced_skills_and_installer_backups_are_ignored(tmp_path):
    profile, home = profile_and_home(tmp_path)
    write(home / "agents" / "__pycache__" / "x.pyc", "cache")
    write(home / "skills" / "synced" / "doc" / "SKILL.md", "account-synced skill\n")
    write(home / "agents" / "a.md.agentic-ide-setup-backup-0123abcd", "old copy\n")
    assert drift.copy_drift(profile, home) == []


def make_release_clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    write(origin / "agentic-ide-setup/profile/claude/hooks/guard.py", "print('ok')\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "hooks")
    clone = tmp_path / "home" / "hooks-release"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "switch", "-q", "--detach", "origin/main")
    return clone


def test_clean_release_clone_on_main_has_no_drift(tmp_path):
    assert drift.release_drift(make_release_clone(tmp_path)) == []


def test_release_clone_edit_and_off_main_commit_are_drift(tmp_path):
    clone = make_release_clone(tmp_path)
    write(clone / "agentic-ide-setup/profile/claude/hooks/guard.py", "print('hot fix')\n")
    assert any("local edits" in p for p in drift.release_drift(clone))
    git(clone, "commit", "-q", "-am", "local hot fix")
    problems = drift.release_drift(clone)
    assert any("not on origin/main" in p for p in problems)
    assert not any("local edits" in p for p in problems)


def test_missing_release_clone_is_reported(tmp_path):
    assert drift.release_drift(tmp_path / "nowhere") == [f"release clone missing: {tmp_path / 'nowhere'}"]


def settings_with(home: Path, *scripts: Path) -> Path:
    groups = [{"hooks": [{"type": "command", "command": f'py "{script}"'}]} for script in scripts]
    agentcoord = {"hooks": [{"type": "command", "command": "C:\\agentcoord\\python.exe"}]}
    return write(home / "settings.json", json.dumps({"hooks": {"Stop": groups + [agentcoord]}}))


def test_wiring_accepts_release_clone_and_flags_copied_or_missing_hooks(tmp_path):
    home = tmp_path / "home"
    clone = home / "hooks-release"
    live = write(clone / drift.CLONE_HOOKS / "guard.py", "print('ok')\n")
    assert drift.wiring_drift(settings_with(home, live), clone) == []

    copied = home / "hooks" / "guard.py"
    missing = clone / drift.CLONE_HOOKS / "gone.py"
    problems = drift.wiring_drift(settings_with(home, live, copied, missing), clone)
    assert any("still runs a copied hook" in p for p in problems)
    assert any("missing release-clone hook" in p for p in problems)
    assert len(problems) == 2


def test_main_reports_and_fails_on_drift(tmp_path, capsys):
    profile, home = profile_and_home(tmp_path)
    clone = make_release_clone(tmp_path)
    settings_with(home, clone / drift.CLONE_HOOKS / "guard.py")
    assert drift.main(["--profile", str(profile), "--home", str(home)]) == 0
    assert "no drift" in capsys.readouterr().out
    write(home / "agents" / "a.md", "edited\n")
    assert drift.main(["--profile", str(profile), "--home", str(home)]) == 1
    assert "1 drift finding(s)" in capsys.readouterr().out
