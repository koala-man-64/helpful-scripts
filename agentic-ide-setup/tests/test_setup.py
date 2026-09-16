"""Offline integration checks for portable export and isolated installation."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def ps(script, *args, ok=True):
    command = ["pwsh", "-NoProfile", "-File", str(SCRIPTS / script), *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def command(text):
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", text], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def quote(path):
    return "'" + str(path).replace("'", "''") + "'"


def test_checked_in_profile():
    ps("Test-AgenticIdeSetup.ps1")


def test_plugin_tables_and_allowlist(tmp_path):
    spec = importlib.util.spec_from_file_location("codex_config", SCRIPTS / "codex_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "config.toml"
    source.write_text('model="local-only"\nnotify=["private-command"]\n[plugins."one@test"]\nenabled=true\n[plugins."two@test"]\nenabled=false\n[hooks.state]\ntrusted_hash="private"\n[features]\nmulti_agent=true\n', encoding="utf-8")
    output = tmp_path / "out"
    module.export(source, output, {"model": "reviewed"})
    config = tomllib.loads((output / "config.template.toml").read_text())
    assert config == {"model": "reviewed", "features": {"multi_agent": True}}
    assert (output / "plugins.txt").read_text().splitlines() == ["one@test", "two@test"]


def test_path_roundtrip_uses_selected_destination(tmp_path):
    output = command(f"""Import-Module {quote(SCRIPTS / 'ProfileTools.psm1')}
$text = '{{"path":"C:\\\\Users\\\\source\\\\AppData\\\\Local\\\\tool"}}'
$portable = ConvertTo-PortableText -Text $text -SourceHome 'C:\\Users\\source' -Json
ConvertFrom-PortableText -Text $portable -DestinationRoot {quote(tmp_path)} -Json
""")
    value = json.loads(output)["path"]
    assert Path(value) == tmp_path / "AppData/Local/tool"
    assert "source" not in value


def test_install_isolated_and_repeat_preserves_existing(tmp_path):
    destination = tmp_path / "alternate home"
    ps("Install-AgenticIdeSetup.ps1", "-DestinationRoot", destination)
    assert not destination.exists()  # preview is side-effect free
    ps("Install-AgenticIdeSetup.ps1", "-DestinationRoot", destination, "-Apply")
    config = destination / ".codex/config.toml"
    assert tomllib.loads(config.read_text())["model_reasoning_effort"] == "medium"
    assert (destination / ".claude/skills/agent-browser/SKILL.md").exists()
    assert not (destination / ".codex/hooks.json").exists()
    assert not (destination / ".claude/hooks").exists()
    config.write_text("# keep this\n")
    guidance = destination / ".codex/AGENTS.md"
    guidance.write_text("old guidance\n")
    ps("Install-AgenticIdeSetup.ps1", "-DestinationRoot", destination, "-Apply")
    assert config.read_text() == "# keep this\n"
    ps("Install-AgenticIdeSetup.ps1", "-DestinationRoot", destination, "-Apply", "-Overwrite")
    assert config.read_text() == "# keep this\n"  # --Overwrite never removes host hooks/trust.
    backups = list(config.parent.glob("AGENTS.md.agentic-ide-setup-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == "old guidance\n"


def test_staging_refuses_extension_side_effect_before_writes(tmp_path):
    dest = tmp_path / "home"
    result = ps("Install-AgenticIdeSetup.ps1", "-DestinationRoot", dest, "-Apply", "-InstallExtensions", ok=False)
    assert "staged installs are files-only" in result.stderr
    assert not dest.exists()


@pytest.mark.parametrize("change", ["missing_skill", "machine_path", "invalid_toml", "unknown_marker"])
def test_invalid_profile_fails_before_install(tmp_path, change):
    profile = tmp_path / "profile"
    shutil.copytree(ROOT / "profile", profile)
    if change == "missing_skill":
        (profile / "codex/skills/workflow-router/SKILL.md").unlink()
    elif change == "machine_path":
        (profile / "codex/config.template.toml").write_text('notify = ["C:\\\\Users\\\\private\\\\tool.exe"]')
    elif change == "invalid_toml":
        (profile / "codex/config.template.toml").write_text('broken = [')
    else:
        (profile / "claude/settings.template.json").write_text('{"model":"__UNKNOWN__"}')
    dest = tmp_path / "home"
    ps("Install-AgenticIdeSetup.ps1", "-ProfileRoot", profile, "-DestinationRoot", dest, "-Apply", ok=False)
    assert not dest.exists()


def test_export_failure_keeps_prior_profile(tmp_path):
    # A copied bundle gives the exporter its own allowed replacement directory.
    bundle = tmp_path / "bundle"
    shutil.copytree(SCRIPTS, bundle / "scripts")
    shutil.copyfile(ROOT / "setup-manifest.json", bundle / "setup-manifest.json")
    profile = bundle / "profile"
    profile.mkdir()
    sentinel = profile / "previous.txt"
    sentinel.write_text("preserve")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(bundle / "scripts/Export-AgenticIdeSetup.ps1"), "-Force", "-SourceHome", str(tmp_path / "missing-home")], capture_output=True, text=True)
    assert result.returncode != 0
    assert sentinel.read_text() == "preserve"


def test_secret_filter_rejects_without_echoing_value():
    output = command(f"""Import-Module {quote(SCRIPTS / 'ProfileTools.psm1')}
try {{ Test-SafeProfileContent -Path 'config.json' -Text 'api_key = "abcdefghijklmnopqrstuvwxyz1234567890"'; throw 'accepted' }}
catch {{ if ($_.Exception.Message -eq 'accepted') {{ throw }}; $_.Exception.Message }}
""")
    assert "Restricted value" in output
    assert "abcdefghijklmnopqrstuvwxyz" not in output


@pytest.mark.parametrize("relative", ["../escape", "nested/../../escape", "C:/outside", "file:stream"])
def test_manifest_path_containment(relative, tmp_path):
    output = command(f"""Import-Module {quote(SCRIPTS / 'ProfileTools.psm1')}
try {{ Resolve-ProfileChild -Root {quote(tmp_path)} -Relative {quote(relative)}; throw 'accepted' }}
catch {{ if ($_.Exception.Message -eq 'accepted') {{ throw }}; 'rejected' }}
""")
    assert output.strip() == "rejected"


def test_reproducible_export_from_fixture(tmp_path):
    bundle = tmp_path / "repo/agentic-ide-setup"
    shutil.copytree(SCRIPTS, bundle / "scripts")
    shutil.copytree(ROOT / "profile", bundle / "profile")
    shutil.copyfile(ROOT / "setup-manifest.json", bundle / "setup-manifest.json")
    manifest = json.loads((bundle / "setup-manifest.json").read_text())
    home = tmp_path / "home"
    for skill in manifest["skills"]:
        source = (home if skill["root"] == "home" else bundle.parent) / skill["source"]
        source.mkdir(parents=True, exist_ok=True)
        if not (source / "SKILL.md").exists():
            (source / "SKILL.md").write_text("# Fixture skill\n")
    (home / ".codex/config.toml").write_text('[plugins."example@test"]\nenabled=true\n')
    (home / ".codex/AGENTS.md").write_text("# Fixture guidance\n")
    (home / ".codex/keybindings.json").write_text("{}")
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude/CLAUDE.md").write_text("# Fixture guidance\n")
    outputs = [tmp_path / "first", tmp_path / "second"]
    for destination in outputs:
        result = subprocess.run(["pwsh", "-NoProfile", "-File", str(bundle / "scripts/Export-AgenticIdeSetup.ps1"), "-SourceHome", str(home), "-DestinationRoot", str(destination)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    def contents(path):
        return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    assert contents(outputs[0]) == contents(outputs[1])
    assert (outputs[0] / "codex/plugins.txt").read_text().strip() == "example@test"


def test_preflight_checks_actual_destination_directory_name(tmp_path):
    profile = tmp_path / "profile"
    shutil.copytree(ROOT / "profile", profile)
    source = profile / "codex/skills/config.template.toml"
    source.mkdir()
    (source / "sample.md").write_text("must not escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "home"
    target = home / ".codex/skills/config.template.toml"
    target.parent.mkdir(parents=True)
    command(f"New-Item -ItemType Junction -Path {quote(target)} -Target {quote(outside)} | Out-Null")
    result = ps("Install-AgenticIdeSetup.ps1", "-ProfileRoot", profile, "-DestinationRoot", home, "-Apply", ok=False)
    assert "Reparse point" in result.stderr
    assert not (outside / "sample.md").exists()
    assert not (home / ".codex/config.toml").exists()
