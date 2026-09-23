"""Stop hook: one nudge, from facts, before a turn that changed things ends.

It reads what the turn did (tool calls since the last real user prompt) and
the repository's state, never the wording of the closing message, and blocks
once, only when one of these facts holds:

- F1: the turn committed and did not push after its last commit, and the
  branch is ahead of its upstream or has none.
- F2: the turn pushed a task branch, and the session shows no pull request
  (no PR-create command, no PR link).
- F3: the turn edited source files in the repository and ran no test, build,
  lint or browser check after the last edit.
- F4: the turn changed a risky path (risky_paths.py) and no reviewer agent ran
  after the last such change.

A prompt that limits scope (no push, local only, read-only...) switches off F1
and F2. stop_hook_active makes the nudge one-shot: the model can finish the
work or say why not, and then stop.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import shell_parse
from hook_utils import (
    NO_REMOTE_FINISH_MARKERS,
    block,
    branch_header,
    contains_any_text,
    emit_json,
    extract_last_user_prompt,
    read_hook_input,
    repo_root,
    turn_did_work,
    workflow_scope_enabled,
)
from risky_paths import is_risky

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
# Files a test, build or lint can check. Docs, data records and templates
# (.md, approval .json files, .env.example) are not source.
SOURCE_SUFFIXES = frozenset({
    ".py", ".pyi", ".ps1", ".psm1", ".psd1", ".sh", ".bash", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".vue", ".svelte", ".cs", ".csproj", ".fs", ".go", ".rs", ".java", ".kt", ".scala", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".swift", ".sql", ".bicep", ".tf", ".yml", ".yaml", ".toml", ".html",
    ".css", ".scss", ".ipynb",
})
SOURCE_NAMES = frozenset({"dockerfile", "makefile", "package.json", "tsconfig.json"})
REVIEWERS = frozenset({
    "Explore", "Plan", "cloud-security-vulnerability-expert", "code-drift-sentinel",
    "maintainability-steward", "architecture-review-agent", "qa-release-gate-agent",
    "software-testing-validation-architect", "project-workflow-auditor-agent", "db-steward",
})
BROWSER_TOOL_PREFIXES = ("mcp__Claude_Browser__", "mcp__claude-in-chrome__")
PR_LINK = re.compile(r"/pullrequest/\d+|github\.com/[^/\s]+/[^/\s]+/pull/\d+", re.IGNORECASE)

VALIDATION_PROGRAMS = frozenset({
    "pytest", "tox", "nox", "ruff", "mypy", "pyright", "flake8", "pylint", "tsc", "eslint",
    "jest", "vitest", "playwright", "invoke-pester", "shellcheck", "actionlint",
})
VALIDATION_SUBCOMMANDS = {
    "npm": {"test", "run", "t", "ci"}, "pnpm": {"test", "run", "check", "lint", "build", "typecheck"},
    "yarn": {"test", "run", "lint", "build"}, "npx": {"tsc", "eslint", "vitest", "jest", "playwright"},
    "dotnet": {"test", "build"}, "go": {"test", "build", "vet"}, "cargo": {"test", "build", "check", "clippy"},
    "make": {"test", "check", "lint", "build"}, "terraform": {"validate", "plan"},
    "mvn": {"test", "verify"}, "gradle": {"test", "check", "build"},
}
GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"})
PYTHON_VALIDATION_MODULES = frozenset({"pytest", "unittest", "mypy", "ruff", "compileall", "py_compile", "pyright"})


@dataclass
class Facts:
    edits: list[tuple[int, str]] = field(default_factory=list)
    commits: list[int] = field(default_factory=list)
    pushes: list[int] = field(default_factory=list)
    validations: list[int] = field(default_factory=list)
    reviews: list[int] = field(default_factory=list)
    pr_in_session: bool = False


def _is_real_prompt(record: dict) -> bool:
    if record.get("type") != "user" or record.get("isSidechain") or record.get("isMeta"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return not content.startswith("Stop hook feedback:")
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in content)


def _is_validation(statement: shell_parse.Statement) -> bool:
    program, args = statement.program, [a.lower() for a in statement.argv[1:]]
    if program in VALIDATION_PROGRAMS:
        return True
    if program in {"py", "python", "python3"} and "-m" in args:
        index = args.index("-m")
        return index + 1 < len(args) and args[index + 1] in PYTHON_VALIDATION_MODULES
    if program in VALIDATION_SUBCOMMANDS and args:
        return args[0] in VALIDATION_SUBCOMMANDS[program]
    if program == "az" and args[:2] == ["bicep", "build"]:
        return True
    return False


def _git_subcommand(args: list[str]) -> str:
    """The subcommand after git's global options (`git -C repo -c k=v commit` is a commit)."""
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
        elif arg in GIT_VALUE_OPTIONS:
            skip_value = True
        elif not arg.startswith("-"):
            return arg
    return ""


def _classify_command(command: str, dialect: str, step: int, facts: Facts, in_turn: bool) -> None:
    for statement in shell_parse.parse(command, dialect).statements:
        text = " ".join(statement.argv).lower()
        if re.match(r"^(?:az repos pr create|gh pr create)\b", text):
            facts.pr_in_session = True
        if not in_turn:
            continue
        if statement.program == "git":
            subcommand = _git_subcommand(statement.argv[1:])
            if subcommand == "commit":
                facts.commits.append(step)
            elif subcommand == "push":
                facts.pushes.append(step)
        if _is_validation(statement):
            facts.validations.append(step)


def collect_facts(transcript_path: str) -> Facts:
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return Facts()
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return facts_from_records(records)


def facts_from_records(records: list[dict]) -> Facts:
    facts = Facts()
    last_prompt = max((i for i, r in enumerate(records) if _is_real_prompt(r)), default=-1)
    step = 0
    for index, record in enumerate(records):
        in_turn = index > last_prompt
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list) or record.get("isSidechain"):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_result" and PR_LINK.search(json.dumps(item.get("content"))):
                facts.pr_in_session = True
            if item.get("type") != "tool_use":
                continue
            step += 1
            name, tool_input = str(item.get("name") or ""), item.get("input") or {}
            if name in SHELL_TOOLS:
                dialect = "powershell" if name == "PowerShell" else "bash"
                _classify_command(str(tool_input.get("command") or ""), dialect, step, facts, in_turn)
            if not in_turn:
                continue
            if name in EDIT_TOOLS:
                path = tool_input.get("file_path") or tool_input.get("notebook_path")
                if isinstance(path, str):
                    facts.edits.append((step, path))
            elif name in {"Agent", "Task"} and tool_input.get("subagent_type") in REVIEWERS:
                facts.reviews.append(step)
            elif name.startswith(BROWSER_TOOL_PREFIXES):
                facts.validations.append(step)
    return facts


def _repo_relative(path: str, root: Path) -> str | None:
    """Repository-relative path for an edit inside the repository, else None (scratch, plans...)."""
    try:
        resolved = Path(path).resolve(strict=False)
        temp = Path(tempfile.gettempdir()).resolve(strict=False)
        if temp == resolved or temp in resolved.parents:
            return None
        return resolved.relative_to(root.resolve(strict=False)).as_posix()
    except (OSError, ValueError):
        return None


def _is_source(relative_path: str) -> bool:
    path = Path(relative_path)
    if "docs" in path.parts:
        return False
    return path.suffix.lower() in SOURCE_SUFFIXES or path.name.lower() in SOURCE_NAMES


def findings(facts: Facts, root: Path, header: str, scope_limited: bool) -> list[str]:
    found = []
    if not scope_limited:
        last_commit, last_push = max(facts.commits, default=0), max(facts.pushes, default=0)
        unpushed = "[ahead" in header or ("..." not in header and header.startswith("## "))
        if facts.commits and last_push < last_commit and unpushed:
            found.append("F1: this turn committed but did not push the branch")
        protected = re.search(r"^## (?:main|master|trunk|develop|staging|production)(?:\.\.\.|$)", header)
        if facts.pushes and not facts.pr_in_session and not protected:
            found.append("F2: this turn pushed the branch, and this session shows no pull request for it")
    in_repo = [(step, rel) for step, path in facts.edits if (rel := _repo_relative(path, root))]
    source = [(step, rel) for step, rel in in_repo if _is_source(rel)]
    if source and max(facts.validations, default=0) < max(step for step, _ in source):
        found.append(f"F3: no test, build, lint or browser check ran after the last edit to {source[-1][1]}")
    risky = [(step, rel) for step, rel in in_repo if is_risky(rel, root.name)]
    if risky and max(facts.reviews, default=0) < max(step for step, _ in risky):
        found.append(f"F4: {risky[-1][1]} is a risky path, and no reviewer agent ran after it changed")
    return found


def main() -> int:
    payload = read_hook_input()
    if payload.get("stop_hook_active") or not workflow_scope_enabled() or not turn_did_work(payload):
        return 0
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return 0
    root = repo_root()
    scope_limited = contains_any_text(extract_last_user_prompt(payload), NO_REMOTE_FINISH_MARKERS)
    found = findings(collect_facts(transcript), root, branch_header(root), scope_limited)
    if not found:
        return 0
    reason = "Before stopping: " + "; ".join(found) + ". Finish it, or say in one line why not."
    return emit_json(block(reason))


if __name__ == "__main__":
    raise SystemExit(main())
