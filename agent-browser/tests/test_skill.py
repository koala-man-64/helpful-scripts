"""Docs-code consistency: the Claude Code skill must only teach commands and flags the parser really has.

The skill lives in the repo's machine profile (agentic-ide-setup/profile/claude/skills/agent-browser); these tests
are skipped when the module is used outside the repository.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_browser as ab
from agent_browser_core import cli as core_cli, common as core_common

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "agentic-ide-setup" / "profile" / "claude" / "skills" / core_common.TOOL_NAME
SKILL = SKILL_DIR / "SKILL.md"
REFERENCE = SKILL_DIR / "references" / "servicenow.md"

pytestmark = pytest.mark.skipif(not SKILL.is_file(), reason="skill not present (module used outside the repo)")

PRINT_COMMANDS = ("echo", "cat", "type", "set", "printenv", "env")


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def _parser_verbs() -> dict[str, set[str]]:
    parser = core_cli._build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices and "goto" in a.choices)
    verbs: dict[str, set[str]] = {}
    for name, sp in sub.choices.items():
        flags = set()
        for action in sp._actions:
            flags.update(o for o in action.option_strings)
        verbs[name] = flags
    return verbs


def _example_commands(text: str) -> list[str]:
    return re.findall(rf"{core_common.TOOL_NAME} ([a-z-]+)((?: [^`\n|]*)?)", text)


def test_frontmatter_names_the_tool_and_preapproves_bash() -> None:
    text = _skill()
    assert text.startswith("---\n")
    head = text.split("---")[1]
    assert f"name: {core_common.TOOL_NAME}" in head
    assert "description:" in head
    assert f"allowed-tools: Bash({core_common.TOOL_NAME} goto *)" in head
    assert f"Bash({core_common.TOOL_NAME} *)" not in head
    assert "Bash(agent-browser commit" not in head and "Bash(agent-browser eval" not in head
    description = re.search(r'description: "(.*)"', head).group(1)  # type: ignore[union-attr]
    assert len(description) < 1536


def test_skill_is_short_enough_for_a_small_model() -> None:
    assert len(_skill().splitlines()) <= 170


def test_shared_shutdown_and_commit_operations_require_caller_approval() -> None:
    import json
    settings = json.loads((SKILL_DIR.parents[1] / "settings.template.json").read_text(encoding="utf-8"))
    rules = settings["permissions"]["ask"]
    for shell in ("Bash", "PowerShell"):
        for verb in ("stop *--all*", "stop *--force*", "commit *", "* commit *", "journal *--clear*", "* journal *--clear*",
                     "journal *--include-unknown*", "* journal *--include-unknown*", "*--accept-dialog*", "*--discard-changes*"):
            assert f"{shell}(agent-browser {verb})" in rules
    # MCP rules match tool names only: browser_commit (a discarding stop is its operation stop) asks, a plain browser_stop does not.
    assert "mcp__*__browser_commit" in rules and not any("browser_stop" in rule for rule in rules)


def test_every_example_verb_and_flag_exists_in_the_parser() -> None:
    verbs = _parser_verbs()
    interfaces = (SKILL_DIR / "references" / "interfaces.md").read_text(encoding="utf-8")
    for verb, rest in _example_commands(_skill() + REFERENCE.read_text(encoding="utf-8") + interfaces):
        assert verb in verbs, f"SKILL mentions unknown verb {verb!r}"
        if verb == "commit":
            inner = rest.strip().split()[0]
            assert inner in verbs
            verb = inner
        for flag in re.findall(r"--[a-z-]+", rest):
            assert flag in verbs[verb] or flag in {"--profile", "--timeout", "--snapshot", "--max-bytes"}, f"{verb} has no flag {flag}"


def test_examples_never_start_an_argument_with_a_slash_or_chain_commands() -> None:
    for verb, rest in _example_commands(_skill()):
        for arg in rest.split():
            assert not (arg.startswith("/") and not arg.startswith("//")), f"{verb} {rest!r}: Git Bash would rewrite {arg!r}"
        assert not re.search(r"[;|&]", rest), f"{verb} {rest!r}: one command per Bash call"
    for line in _skill().splitlines():
        stripped = line.strip().lstrip("`").split()
        if stripped and stripped[0] in PRINT_COMMANDS:
            raise AssertionError(f"example starts with a print-like command that the bash guard denies: {line!r}")


def test_the_loop_and_the_sign_in_handshake_are_present() -> None:
    text = _skill()
    assert "## The loop" in text
    assert f"{core_common.TOOL_NAME} wait --signed-in" in text
    assert "sign_in_suspected" in text
    assert "Never type passwords" in text


def test_errors_table_covers_every_exit_2_class_the_model_can_hit() -> None:
    text = _skill()
    for cls in ("stale_ref", "ref_not_found", "not_found", "action_timeout", "action_failed", "guarded", "dialog", "unsaved_changes", "ambiguous_profile", "not_running", "timeout", "busy"):
        assert f"`{cls}`" in text, f"errors table lacks {cls}"


ACTION_VERBS = ("click", "fill", "type", "press", "select", "check", "uncheck", "hover", "scroll", "upload", "eval", "fill-form")


def test_dialog_hints_only_promise_flags_the_failing_verbs_have() -> None:
    verbs = _parser_verbs()
    dialog_flags = set(re.findall(r"--[a-z-]+", core_common.HINTS["dialog"]))
    unsaved_flags = set(re.findall(r"--[a-z-]+", core_common.HINTS["unsaved_changes"]))
    assert dialog_flags == {"--accept-dialog", "--dialog-text"} and unsaved_flags == {"--discard-changes"}
    for verb in ACTION_VERBS:
        assert dialog_flags | unsaved_flags <= verbs[verb], verb
    for verb in ("goto", "back", "reload", "tab", "stop"):  # navigation and stop can only meet unsaved edits
        assert unsaved_flags <= verbs[verb], verb


def test_hints_only_name_real_verbs() -> None:
    verbs = set(_parser_verbs())
    for key, template in core_common.HINTS.items():
        for verb in re.findall(rf"{core_common.TOOL_NAME} ([a-z-]+)", template):
            assert verb in verbs, f"hint {key!r} names unknown verb {verb!r}"
        rendered = core_common._hint(key, "work")
        assert "{" not in rendered and "}" not in rendered
