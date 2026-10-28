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
    for cls in ("stale_ref", "ref_not_found", "not_found", "action_timeout", "action_failed", "guarded", "dialog", "unsaved_changes", "not_running", "timeout", "busy"):
        assert f"`{cls}`" in text, f"errors table lacks {cls}"


def test_the_never_raised_ambiguous_profile_class_is_gone() -> None:
    # Profile selection never fails for ambiguity (explicit, then AGENT_BROWSER_PROFILE, then default), so nothing raised it.
    readme = (REPO / "agent-browser" / "README.md").read_text(encoding="utf-8")
    for text in (_skill(), readme):
        assert "ambiguous_profile" not in text
    assert "ambiguous_profile" not in core_common.EXIT_2_CLASSES and "ambiguous_profile" not in core_common.HINTS
    assert not hasattr(core_common, "ERR_AMBIGUOUS_PROFILE")


TEMPLATE = SKILL_DIR.parents[1] / "settings.template.json"


def _rule_matches(pattern: str, command: str) -> bool:
    """Claude Code's documented Bash/PowerShell rule shape (code.claude.com/docs/en/permissions, "Wildcard patterns"):
    `*` stands for any text, spaces included; a trailing ` *` that is the rule's only wildcard also matches the bare command."""
    if pattern.endswith(" *") and pattern.count("*") == 1:
        regex = re.escape(pattern[:-2]) + "(?: .*)?"
    else:
        regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, command, re.S) is not None


@pytest.mark.parametrize(("pattern", "matches", "misses"), [  # the rows of that docs table
    ("npm run *", ("npm run build", "npm run test --watch", "npm run"), ("npm install",)),
    ("git log * main", ("git log --oneline main", "git log -5 main"), ("git log main", "git push origin main")),
    ("git * main", ("git merge main", "git push origin main"), ("git log",)),
    ("* --version", ("node --version",), ("node -v",)),
    ("ls *", ("ls -la", "ls"), ("lsof",)),
    ("ls*", ("ls -la", "lsof"), ()),
    ("* --help *", ("npm --help x",), ("npm --help",)),
])
def test_rule_matcher_follows_the_documented_examples(pattern: str, matches: tuple[str, ...], misses: tuple[str, ...]) -> None:
    assert all(_rule_matches(pattern, c) for c in matches) and not any(_rule_matches(pattern, c) for c in misses)


def _rules(entries, shell: str) -> list[str]:
    return [m.group(1) for m in (re.fullmatch(rf"{shell}\((.*)\)", e.strip()) for e in entries) if m]


def _preapproved(command: str, shell: str = "Bash") -> bool:
    head = _skill().split("---")[1]
    line = re.search(r"^allowed-tools: (.*)$", head, re.M).group(1)  # type: ignore[union-attr]
    return any(_rule_matches(p, command) for p in _rules(re.findall(r"\w+\([^)]*\)", line), shell))


def _asked(command: str, shell: str) -> bool:
    import json
    rules = json.loads(TEMPLATE.read_text(encoding="utf-8"))["permissions"]["ask"]
    return any(_rule_matches(p, command) for p in _rules(rules, shell))


def _committing(command: str) -> bool:
    """Forms a caller must approve: declared commits, eval, evidence clearing, dialog acceptance, discarding edits, shared stops."""
    words = command.split()
    valued = {"--profile", "--session", "--tab", "--timeout", "--max-bytes", "--expect"}  # global flags that take a value
    verb = next((w for i, w in enumerate(words[1:], 1) if not w.startswith("-") and words[i - 1] not in valued), "")
    return (verb in ("commit", "eval") or "--accept-dialog" in words or "--discard-changes" in words
            or verb == "journal" and ("--clear" in words or "--include-unknown" in words)
            or verb == "stop" and ("--all" in words or "--force" in words))


COMMITTING_FORMS = (
    "agent-browser commit click e12", "agent-browser commit api https://example.com/api/items --method POST --json-file p.json",
    "agent-browser commit run sn/add-work-note --number INC0010023 --text-file note.txt", "agent-browser commit state --handoff h --resolve",
    "agent-browser --profile work commit journal --clear", "agent-browser eval 1", "agent-browser --session s eval --js-file x.js",
    "agent-browser journal --clear", "agent-browser journal --command 0123 --clear", "agent-browser --profile work journal --clear",
    "agent-browser journal --clear --include-unknown", "agent-browser stop --all", "agent-browser stop --force",
    "agent-browser --profile work stop --all", "agent-browser stop --discard-changes", "agent-browser click e5 --accept-dialog",
    "agent-browser fill e5 x --accept-dialog --dialog-text yes", "agent-browser goto https://example.com --discard-changes",
    "agent-browser fill-form --file f.json --discard-changes", "agent-browser tab 2 --close --discard-changes",
    "agent-browser --profile state journal --clear", "agent-browser --session stop stop --all",  # a flag value that names a verb
)


def test_every_taught_command_is_preapproved_or_ask_gated() -> None:
    """Claude Code evaluates deny, then ask, then allow, and its skills page says ask rules still override allowed-tools.
    So a committing form of a preapproved verb must match an ask rule, in Bash and in PowerShell (where a caller's own
    allow rule could preapprove it), and every other taught command must be preapproved (the skill lists Bash only)."""
    interfaces = (SKILL_DIR / "references" / "interfaces.md").read_text(encoding="utf-8")
    taught = [f"{core_common.TOOL_NAME} {verb}{rest}".strip()
              for verb, rest in _example_commands(_skill() + REFERENCE.read_text(encoding="utf-8") + interfaces)]
    assert {"journal", "api", "run", "eval", "commit"} <= {c.split()[1] for c in taught}
    for command in taught:
        if _committing(command):
            assert _asked(command, "Bash") and _asked(command, "PowerShell"), command
        else:
            assert _preapproved(command) and not _asked(command, "Bash"), command
    for command in COMMITTING_FORMS:
        assert _committing(command), command
        assert _asked(command, "Bash") and _asked(command, "PowerShell"), command
    # The ask rule is load-bearing: the bare verb of these committing forms is itself preapproved.
    assert all(_preapproved(c) for c in ("agent-browser journal --clear", "agent-browser stop --all", "agent-browser click e5 --accept-dialog"))


def test_preapproved_verbs_exist_and_never_include_a_committing_verb() -> None:
    head = _skill().split("---")[1]
    line = re.search(r"^allowed-tools: (.*)$", head, re.M).group(1)  # type: ignore[union-attr]
    verbs = [re.fullmatch(rf"{core_common.TOOL_NAME} ([a-z-]+) \*", p).group(1)  # type: ignore[union-attr]
             for p in _rules(re.findall(r"\w+\([^)]*\)", line), "Bash")]
    assert set(verbs) <= set(_parser_verbs()) and len(verbs) == len(set(verbs))
    assert {"journal", "api", "run"} <= set(verbs) and not {"commit", "eval", "serve", "start", "clean"} & set(verbs)


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
