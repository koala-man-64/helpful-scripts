"""Report drift between the checked-in Claude profile and an installed ~/.claude.

Hooks run from a pinned release clone of this repository, so their check is on
the clone itself: clean, at a commit reachable from origin/main, and the one
place settings.json runs hooks from. Agents, skills and CLAUDE.md are installed
copies, so they are compared with the profile, ignoring line endings.
Exit code 1 means drift was found.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

COPIED_TREES = ("agents", "skills")
IGNORED_PARTS = {"__pycache__", ".pytest_cache", "synced"}
INSTALLER_BACKUP = ".agentic-ide-setup-backup-"
CLONE_HOOKS = Path("agentic-ide-setup/profile/claude/hooks")
QUOTED_SCRIPT = re.compile(r'"([^"]+\.py)"')


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    found = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or IGNORED_PARTS.intersection(relative.parts):
            continue
        if INSTALLER_BACKUP in path.name:
            continue
        found[relative.as_posix()] = path
    return found


def copy_drift(profile: Path, home: Path) -> list[str]:
    problems = []
    for tree in COPIED_TREES:
        expected, installed = _files(profile / tree), _files(home / tree)
        for name in sorted(expected.keys() - installed.keys()):
            problems.append(f"missing from {home / tree}: {name}")
        for name in sorted(installed.keys() - expected.keys()):
            problems.append(f"not in the profile: {home / tree / name}")
        for name in sorted(expected.keys() & installed.keys()):
            if _normalized(expected[name]) != _normalized(installed[name]):
                problems.append(f"differs from the profile: {home / tree / name}")
    installed_md = home / "CLAUDE.md"
    if not installed_md.is_file() or _normalized(installed_md) != _normalized(profile / "CLAUDE.md"):
        problems.append(f"differs from the profile (export it, or reinstall): {installed_md}")
    return problems


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)


def release_drift(clone: Path, base: str = "origin/main") -> list[str]:
    if not (clone / ".git").exists():
        return [f"release clone missing: {clone}"]
    status = _git(clone, "status", "--porcelain")
    if status.returncode != 0:
        return [f"release clone unreadable: {status.stderr.strip()}"]
    problems = []
    if status.stdout.strip():
        # Live hooks edited in place: the exact failure the clone exists to expose.
        problems.append(f"release clone has local edits; upstream or discard them: {clone}")
    if _git(clone, "merge-base", "--is-ancestor", "HEAD", base).returncode != 0:
        problems.append(f"release clone HEAD is not on {base}: {clone}")
    return problems


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def wiring_drift(settings: Path, clone: Path) -> list[str]:
    try:
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError) as error:
        return [f"settings unreadable: {settings}: {error}"]
    stale_dir = _norm(str(settings.parent / "hooks")) + "/"
    clone_dir = _norm(str(clone / CLONE_HOOKS)) + "/"
    problems = []
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                for script in QUOTED_SCRIPT.findall(hook.get("command", "")):
                    normalized = _norm(script)
                    if normalized.startswith(stale_dir):
                        problems.append(f"{event} still runs a copied hook: {script}")
                    elif normalized.startswith(clone_dir) and not Path(script).is_file():
                        problems.append(f"{event} runs a missing release-clone hook: {script}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report drift between the Claude profile and ~/.claude.")
    parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "profile" / "claude")
    parser.add_argument("--home", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--release", type=Path, help="release clone (default: <home>/hooks-release)")
    parser.add_argument("--fetch", action="store_true", help="fetch origin in the release clone first")
    args = parser.parse_args(argv)
    clone = args.release or args.home / "hooks-release"
    if args.fetch:
        _git(clone, "fetch", "-q", "origin")
    problems = (
        release_drift(clone)
        + wiring_drift(args.home / "settings.json", clone)
        + copy_drift(args.profile, args.home)
    )
    for problem in problems:
        print(problem)
    print("no drift" if not problems else f"{len(problems)} drift finding(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
