"""Deterministic guards for destructive and authority-sensitive actions."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable

from .models import EventEnvelope, GuardDecision, RepoContext
from .utils import SECRET_NAME_RE, canonical_path, is_within


MUTATING_TOOLS = {"apply_patch", "edit", "write", "multiedit"}
SHELL_TOOLS = {"bash", "shell", "shell_command", "powershell", "cmd"}
SECRET_READ_PATTERNS = (
    re.compile(r"\baz\s+keyvault\s+secret\s+show\b", re.I),
    re.compile(r"\bget-azkeyvaultsecret\b", re.I),
    re.compile(r"\b(?:op|bw)\s+(?:read|get)\b", re.I),
    re.compile(r"\bgh\s+secret\s+list\b.*--json\b", re.I),
)
NESTED_SHELLS = {
    "bash",
    "cmd",
    "dash",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "zsh",
}
FILESYSTEM_MUTATION_COMMANDS = {
    "ac": "write",
    "add-content": "write",
    "ci": "copy",
    "clc": "write",
    "clear-content": "write",
    "copy": "copy",
    "copy-item": "copy",
    "cp": "copy",
    "del": "remove",
    "erase": "remove",
    "md": "create",
    "mi": "move",
    "mkdir": "create",
    "move": "move",
    "move-item": "move",
    "mv": "move",
    "new-item": "create",
    "ni": "create",
    "out-file": "write",
    "remove-item": "remove",
    "ren": "move",
    "rename-item": "move",
    "ri": "remove",
    "rmdir": "remove",
    "rm": "remove",
    "rni": "move",
    "sc": "write",
    "set-content": "write",
    "set-item": "write",
    "si": "write",
    "tee": "write",
    "tee-object": "write",
    "touch": "create",
}


@dataclass(frozen=True)
class _CommandUnit:
    segment: str
    cwd: Path | None


def assess_pre_tool(
    event: EventEnvelope,
    context: RepoContext,
    policy: dict[str, Any],
) -> GuardDecision:
    tool = event.tool_name.lower()
    if tool in MUTATING_TOOLS:
        if _is_plan_mode(event.permission_mode):
            return _deny("PLAN_MODE_MUTATION", "Plan mode does not permit file mutations.")
        if context.detached:
            return _deny(
                "GIT_DETACHED_HEAD", "Mutations from detached or unreadable HEAD are blocked."
            )
        if context.expected_managed and not context.managed:
            return _deny(
                "MANAGED_REPO_UNREGISTERED",
                "Managed repository mutation requires an immutable registration.",
            )
        path_decision = _tool_mutation_guard(event, context)
        if path_decision:
            return path_decision
        return GuardDecision(action_type="mutation")

    command = event.command()
    if tool and tool not in SHELL_TOOLS and not command:
        return GuardDecision()
    if not command:
        return GuardDecision()

    secret = _secret_exposure(command)
    if secret:
        return secret

    units, analysis_decision = _analyze_shell_script(command, context.cwd)
    if analysis_decision:
        return analysis_decision
    mutating_units = [unit for unit in units if classify_segment(unit.segment) == "mutation"]
    mutating = bool(mutating_units)
    if mutating and _is_plan_mode(event.permission_mode):
        return _deny("PLAN_MODE_MUTATION", "Plan mode does not permit mutating commands.")
    if mutating and context.detached:
        if any(_unit_git_subcommand(unit) == "commit" for unit in mutating_units):
            return _deny("GIT_DETACHED_COMMIT", "Commit from detached HEAD is blocked.")
        return _deny("GIT_DETACHED_HEAD", "Mutations from detached or unreadable HEAD are blocked.")
    if mutating and context.expected_managed and not context.managed:
        return _deny(
            "MANAGED_REPO_UNREGISTERED",
            "Mutation denied until this Git common directory is registered.",
        )

    root = canonical_path(context.repo_root or context.cwd)
    for unit in units:
        tokens = tokenize(unit.segment)
        if not tokens:
            continue
        action_type = classify_segment(unit.segment)
        effective_cwd = unit.cwd
        if action_type == "mutation" and effective_cwd is None:
            return _deny(
                "SHELL_CONTEXT_OPAQUE",
                "Mutation denied because the effective shell location is not deterministic.",
            )
        if effective_cwd is None:
            effective_cwd = context.cwd

        is_git = _command_name([token.lower() for token in tokens]) == "git"
        shell_cwd = effective_cwd
        git_cwd = _git_effective_cwd(tokens, shell_cwd)
        if is_git:
            if git_cwd is None and action_type == "mutation":
                return _deny(
                    "SHELL_CONTEXT_OPAQUE",
                    "Git mutation denied because its effective -C location is not deterministic.",
                )
            if git_cwd is not None:
                effective_cwd = git_cwd

        if action_type == "mutation" and not is_within(effective_cwd, root):
            return _deny(
                "FILESYSTEM_OUTSIDE_REPO",
                "Mutating commands must remain within the active repository.",
            )
        if action_type == "mutation" and is_git:
            explicit_paths = _git_explicit_context_paths(tokens, shell_cwd)
            if explicit_paths is None:
                return _deny(
                    "SHELL_CONTEXT_OPAQUE",
                    "Git mutation denied because an explicit repository path is dynamic.",
                )
            if any(not is_within(path, root) for path in explicit_paths):
                return _deny(
                    "FILESYSTEM_OUTSIDE_REPO",
                    "Explicit Git repository and worktree paths must remain in the active repository.",
                )
            if _crosses_nested_git_boundary(effective_cwd, root):
                return _deny(
                    "MANAGED_REPO_UNREGISTERED",
                    "A nested Git repository requires its own verified repository context.",
                )

        effective_context = replace(context, cwd=effective_cwd)
        decision = _git_guard(tokens, effective_context, policy)
        if decision:
            return decision
        decision = _filesystem_guard(tokens, effective_context)
        if decision:
            return decision
        decision = _redirection_guard(unit.segment, effective_context)
        if decision:
            return decision
        decision = _azure_guard(tokens, unit.segment)
        if decision:
            return decision
    return GuardDecision(action_type="mutation" if mutating else "read")


def split_command_segments(command: str) -> list[str]:
    """Split shell pipelines without treating quoted separators as commands."""
    return [segment for segment, _separator in _split_command_steps(command)]


def _split_command_steps(command: str) -> list[tuple[str, str]]:
    """Return shell segments together with the separator that follows each one."""
    steps: list[tuple[str, str]] = []
    buffer: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            buffer.append(char)
            escaped = False
            index += 1
            continue
        if char in ("\\", "`") and quote != "'":
            buffer.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue
        separator_length = 0
        if command[index : index + 2] in ("&&", "||"):
            separator_length = 2
        elif char in (";", "|", "\n", "\r"):
            separator_length = 1
        if separator_length:
            value = "".join(buffer).strip()
            if value:
                steps.append((value, command[index : index + separator_length]))
            buffer.clear()
            index += separator_length
            continue
        buffer.append(char)
        index += 1
    value = "".join(buffer).strip()
    if value:
        steps.append((value, ""))
    return steps


def tokenize(segment: str) -> list[str]:
    try:
        lexer = shlex.shlex(segment, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return [_strip_quotes(token) for token in lexer]
    except ValueError:
        return [_strip_quotes(token) for token in re.findall(r""""[^"]*"|'[^']*'|\S+""", segment)]


def is_git_dry_run(segment: str, subcommand: str) -> bool:
    """Return whether a commit or push command explicitly requests a no-op."""
    lowered = [token.lower() for token in tokenize(segment)]
    if _command_name(lowered) != "git" or _git_subcommand(lowered) != subcommand:
        return False
    if "--dry-run" in lowered:
        return True
    return subcommand == "push" and "-n" in lowered


def _analyze_shell_script(
    command: str,
    initial_cwd: Path,
    *,
    depth: int = 0,
) -> tuple[list[_CommandUnit], GuardDecision | None]:
    if depth > 6:
        return [], _opaque_shell_decision("Nested shell depth exceeds the safe parser limit.")

    units: list[_CommandUnit] = []
    current_cwd: Path | None = canonical_path(initial_cwd)
    location_stack: list[Path | None] = []
    for segment, separator in _split_command_steps(command):
        substitutions = _command_substitutions(segment)
        if substitutions is None:
            return [], _opaque_shell_decision(
                "Shell command substitution could not be parsed deterministically."
            )
        for substitution in substitutions:
            nested_cwd = current_cwd or canonical_path(initial_cwd)
            nested_units, nested_decision = _analyze_shell_script(
                substitution,
                nested_cwd,
                depth=depth + 1,
            )
            if nested_decision:
                return [], nested_decision
            units.extend(nested_units)

        tokens = tokenize(segment)
        if not tokens:
            continue
        if _top_level_shell_construct_is_opaque(segment, tokens):
            return [], _opaque_shell_decision(
                "Shell control flow or an indirect execution wrapper cannot be inspected safely."
            )

        nested, recognized, opaque = _nested_shell_payload(tokens)
        if recognized:
            if opaque or nested is None:
                return [], _opaque_shell_decision(
                    "Nested shell payload cannot be inspected deterministically."
                )
            nested_cwd = current_cwd or canonical_path(initial_cwd)
            nested_units, nested_decision = _analyze_shell_script(
                nested,
                nested_cwd,
                depth=depth + 1,
            )
            if nested_decision:
                return [], nested_decision
            units.extend(nested_units)
            continue

        location_command, target, stack_action = _location_transition(
            tokens,
            current_cwd,
            location_stack,
        )
        if location_command:
            if separator == "||":
                continue
            if separator == "|":
                current_cwd = None
                continue
            if stack_action == "push":
                location_stack.append(current_cwd)
            elif stack_action == "pop":
                current_cwd = location_stack.pop() if location_stack else current_cwd
                continue
            current_cwd = target
            continue

        if depth and _nested_segment_is_opaque(tokens, segment):
            return [], _opaque_shell_decision(
                "Nested shell mutation contains dynamic or indirect command content."
            )
        units.append(_CommandUnit(segment=segment, cwd=current_cwd))
    return units, None


def _command_substitutions(segment: str) -> list[str] | None:
    """Extract executable $(...) payloads while ignoring single-quoted literals."""
    substitutions: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(segment):
        char = segment[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char in {"\\", "`"} and quote != "'":
            escaped = True
            index += 1
            continue
        if char == "'" and quote != '"':
            quote = "" if quote == "'" else "'"
            index += 1
            continue
        if char == '"' and quote != "'":
            quote = "" if quote == '"' else '"'
            index += 1
            continue
        if quote != "'" and char == "$" and index + 1 < len(segment) and segment[index + 1] == "(":
            depth = 1
            payload_start = index + 2
            cursor = payload_start
            inner_quote = ""
            inner_escaped = False
            while cursor < len(segment):
                inner = segment[cursor]
                if inner_escaped:
                    inner_escaped = False
                    cursor += 1
                    continue
                if inner in {"\\", "`"} and inner_quote != "'":
                    inner_escaped = True
                    cursor += 1
                    continue
                if inner == "'" and inner_quote != '"':
                    inner_quote = "" if inner_quote == "'" else "'"
                    cursor += 1
                    continue
                if inner == '"' and inner_quote != "'":
                    inner_quote = "" if inner_quote == '"' else '"'
                    cursor += 1
                    continue
                if not inner_quote and inner == "(":
                    depth += 1
                elif not inner_quote and inner == ")":
                    depth -= 1
                    if depth == 0:
                        payload = segment[payload_start:cursor].strip()
                        if not payload:
                            return None
                        substitutions.append(payload)
                        index = cursor + 1
                        break
                cursor += 1
            else:
                return None
            continue
        index += 1
    return substitutions


def _top_level_shell_construct_is_opaque(segment: str, tokens: list[str]) -> bool:
    masked = _mask_quoted_content(segment)
    if re.match(
        r"^\s*(?:if|for|foreach|while|until|case|function|switch|try|catch|finally|do)\b",
        masked,
        re.I,
    ):
        return True
    if "{" in masked or "}" in masked:
        return True
    if re.match(r"^\s*\(", masked) or re.search(r"[<>]\s*\(", masked):
        return True
    if re.search(r"(?:^|[;&|])\s*[&.]\s*(?:\$|%|\()", masked):
        return True
    if _has_unquoted_backtick_pair(segment):
        return True

    lowered = [token.lower() for token in tokens]
    raw_index = next(
        (
            index
            for index, token in enumerate(lowered)
            if not re.fullmatch(r"[a-z_][a-z0-9_]*=.*", token)
        ),
        len(lowered),
    )
    raw_command = _executable_name(lowered[raw_index]) if raw_index < len(lowered) else ""
    if raw_command == "env" and any(
        token in {"-s", "--split-string"} or token.startswith("--split-string=")
        for token in lowered[raw_index + 1 :]
    ):
        return True
    direct_command = _command_name(lowered)
    opaque_wrappers = {
        "invoke-command",
        "parallel",
        "start-job",
        "start-process",
        "timeout",
        "wsl",
        "xargs",
    }
    command_index = _command_index(lowered)
    if command_index < len(tokens) and _looks_like_script_file(tokens[command_index]):
        script_command = _executable_name(tokens[command_index])
        recognized_commands = {
            "az",
            "git",
            *NESTED_SHELLS,
            *FILESYSTEM_MUTATION_COMMANDS,
        }
        if script_command not in recognized_commands:
            return True
    if direct_command not in opaque_wrappers:
        return False
    executable_tokens = {_executable_name(token) for token in lowered[1:]}
    return bool(
        {"az", "git"}.intersection(executable_tokens)
        or set(FILESYSTEM_MUTATION_COMMANDS).intersection(executable_tokens)
    )


def _has_unquoted_backtick_pair(value: str) -> bool:
    quote = ""
    backticks = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'" and quote != '"':
            quote = "" if quote == "'" else "'"
        elif char == '"' and quote != "'":
            quote = "" if quote == '"' else '"'
        elif char == "`" and quote != "'":
            backticks += 1
            if backticks >= 2:
                return True
        elif char == "\\" and quote != "'" and index + 1 < len(value):
            index += 1
        index += 1
    return False


def _mask_quoted_content(value: str) -> str:
    result: list[str] = []
    quote = ""
    escaped = False
    for char in value:
        if escaped:
            result.append(" ")
            escaped = False
            continue
        if char in {"\\", "`"} and quote != "'":
            result.append(" ")
            escaped = True
            continue
        if quote:
            result.append(" ")
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            continue
        result.append(char)
    return "".join(result)


def _nested_shell_payload(tokens: list[str]) -> tuple[str | None, bool, bool]:
    """Return (payload, recognized wrapper, opaque payload)."""
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    if command not in NESTED_SHELLS:
        return None, False, False
    command_index = _command_index(lowered)
    arguments = tokens[command_index + 1 :]
    lowered_arguments = lowered[command_index + 1 :]

    if command in {"powershell", "pwsh"}:
        if any(
            token in {"-encodedcommand", "-enc", "-e", "-file", "-f"}
            or token.startswith("-encodedcommand:")
            for token in lowered_arguments
        ):
            return None, True, True
        for index, token in enumerate(lowered_arguments):
            if token in {"-command", "-c"}:
                payload = " ".join(arguments[index + 1 :]).strip()
                return payload or None, True, _shell_payload_is_opaque(payload)
        return None, False, False

    if command == "cmd":
        for index, token in enumerate(lowered_arguments):
            if token in {"/c", "/k"}:
                payload = " ".join(arguments[index + 1 :]).strip()
                opaque = _shell_payload_is_opaque(payload)
                if payload and _looks_like_script_file(tokenize(payload)[0]):
                    opaque = True
                return payload or None, True, opaque
        return None, False, False

    command_argument_index: int | None = None
    for index, token in enumerate(lowered_arguments):
        if token == "-c" or (
            token.startswith("-") and not token.startswith("--") and "c" in token[1:]
        ):
            command_argument_index = index + 1
            break
    if command_argument_index is not None:
        if command_argument_index >= len(arguments):
            return None, True, True
        payload = arguments[command_argument_index].strip()
        return payload or None, True, _shell_payload_is_opaque(payload)

    script = next((token for token in arguments if not token.startswith("-")), "")
    if script:
        return None, True, True
    return None, False, False


def _shell_payload_is_opaque(payload: str) -> bool:
    if not payload.strip():
        return True
    if re.search(r"\$\(|`[^`]+`", payload, re.I):
        return True
    if re.search(r"\b(?:eval|invoke-expression|iex)\b", payload, re.I):
        return True
    if re.search(r"(?:^|[;&|])\s*[&.]\s*", payload):
        return True
    if re.search(r"(?:^|[;&|])\s*(?:if|for|while|until|case|function|\{|\()", payload, re.I):
        return True
    return False


def _nested_segment_is_opaque(tokens: list[str], segment: str) -> bool:
    classification = classify_segment(segment)
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    dynamic = re.compile(r"\$\(|`[^`]+`|%\w+%|\$\{?\w+\}?|\$env:", re.I)
    if re.search(r"\$\(|`[^`]+`", segment):
        return True
    if classification == "mutation" and any(dynamic.search(token) for token in tokens):
        return True
    if command in {"eval", "invoke-expression", "iex"}:
        return True
    if tokens and _looks_like_script_file(tokens[_command_index(lowered)]):
        return True
    if tokens and dynamic.search(tokens[_command_index(lowered)]):
        return True
    if command in {"git", "az"} and any(dynamic.search(token) for token in tokens):
        return True
    return bool(re.search(r"(?:^|[;&|])\s*[&.]\s*(?:\$|%|\()", segment))


def _location_transition(
    tokens: list[str],
    cwd: Path | None,
    location_stack: list[Path | None],
) -> tuple[bool, Path | None, str]:
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    if command in {"pop-location", "popd"}:
        return True, location_stack[-1] if location_stack else cwd, "pop"
    if command not in {"cd", "chdir", "set-location", "sl", "push-location", "pushd"}:
        return False, cwd, ""

    command_index = _command_index(lowered)
    arguments = tokens[command_index + 1 :]
    target_value = _location_argument(arguments)
    if not target_value or _path_expression_is_dynamic(target_value):
        target = None
    else:
        expanded = os.path.expanduser(os.path.expandvars(_strip_quotes(target_value)))
        candidate = Path(expanded)
        if not candidate.is_absolute():
            if cwd is None:
                target = None
            else:
                candidate = cwd / candidate
                target = canonical_path(candidate) if candidate.is_dir() else None
        else:
            target = canonical_path(candidate) if candidate.is_dir() else None
    stack_action = "push" if command in {"push-location", "pushd"} else ""
    return True, target, stack_action


def _location_argument(arguments: list[str]) -> str:
    options_with_value = {"-path", "-literalpath"}
    index = 0
    while index < len(arguments):
        token = arguments[index]
        lower = token.lower()
        if lower == "/d":
            index += 1
            continue
        if lower in options_with_value:
            return arguments[index + 1] if index + 1 < len(arguments) else ""
        if token.startswith("-"):
            index += 1
            continue
        return token
    return ""


def _path_expression_is_dynamic(value: str) -> bool:
    return bool(re.search(r"\$\(|`|%\w+%|\$\{?\w+\}?|\$env:", value, re.I))


def _filesystem_path_is_dynamic(value: str) -> bool:
    stripped = _strip_quotes(value).strip()
    return _path_expression_is_dynamic(stripped) or bool(
        re.match(r"^(?:@?\(|\[[^\]]+\]\s*::)", stripped)
    )


def _looks_like_script_file(value: str) -> bool:
    return Path(_strip_quotes(value)).suffix.lower() in {".bat", ".cmd", ".ps1", ".sh"}


def _opaque_shell_decision(message: str) -> GuardDecision:
    return _deny("SHELL_CONTEXT_OPAQUE", message)


def classify_segment(segment: str) -> str:
    tokens = tokenize(segment)
    if not tokens:
        return "read"
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    _redirects, has_file_redirection, _opaque_redirection = _output_redirections(segment)
    if has_file_redirection or command in FILESYSTEM_MUTATION_COMMANDS:
        return "mutation"
    if command == "git":
        subcommand = _git_subcommand(lowered)
        if subcommand in {"commit", "push"} and is_git_dry_run(segment, subcommand):
            return "read"
        if subcommand == "apply":
            subcommand_index = lowered.index(subcommand)
            tail = lowered[subcommand_index + 1 :]
            if "--apply" not in tail and any(
                flag in tail for flag in {"--check", "--numstat", "--stat", "--summary"}
            ):
                return "read"
            return "mutation"
        if subcommand == "worktree":
            subcommand_index = lowered.index(subcommand)
            action = _git_worktree_action(lowered[subcommand_index + 1 :])
            return "read" if action in {"", "list"} else "mutation"
        if subcommand in {
            "add",
            "am",
            "branch",
            "checkout",
            "cherry-pick",
            "clean",
            "commit",
            "merge",
            "mv",
            "pull",
            "push",
            "rebase",
            "reset",
            "restore",
            "revert",
            "rm",
            "switch",
            "tag",
            "update-ref",
        }:
            return "mutation"
    if command == "az":
        read_markers = {"show", "list", "get", "query"}
        return (
            "read"
            if read_markers.intersection(lowered) and not _azure_write_tokens(lowered)
            else "mutation"
        )
    if _is_direct_azure_devops_rest(command, " ".join(lowered)):
        return "read" if _rest_method(command, lowered) in {"get", "head"} else "mutation"
    return "read"


def _git_guard(
    tokens: list[str],
    context: RepoContext,
    policy: dict[str, Any],
) -> GuardDecision | None:
    lowered = [token.lower() for token in tokens]
    if _command_name(lowered) != "git":
        return None
    subcommand = _git_subcommand(lowered)
    if subcommand is None:
        return None
    subcommand_index = lowered.index(subcommand)
    tail = lowered[subcommand_index + 1 :]
    original_tail = tokens[subcommand_index + 1 :]
    protected = {str(item).lower() for item in policy.get("protected_branches", [])}
    protected.update({"main", "master", "trunk", "develop", "staging", "production"})

    hard_flag = "-" * 2 + "hard"
    if subcommand == "reset" and hard_flag in tail:
        return _deny("GIT_DESTRUCTIVE_RESET", "Hard history/worktree reset is blocked.")
    if subcommand == "clean" and any(
        token == "--force" or (token.startswith("-") and "f" in token[1:]) for token in tail
    ):
        return _deny("GIT_DESTRUCTIVE_CLEAN", "Forced untracked-file cleanup is blocked.")
    if subcommand == "checkout" and "--" in tail:
        return _deny("GIT_DESTRUCTIVE_CHECKOUT", "Broad checkout restore can discard work.")
    if subcommand == "restore":
        staged_only = any(
            (token == "--staged" or token.startswith("--staged="))
            or (token.startswith("-") and not token.startswith("--") and "S" in token[1:])
            for token in original_tail
        )
        restores_worktree = any(
            (token == "--worktree" or token.startswith("--worktree="))
            or (token.startswith("-") and not token.startswith("--") and "W" in token[1:])
            for token in original_tail
        )
        if "." in tail or ":/" in tail or restores_worktree or not staged_only:
            return _deny("GIT_DESTRUCTIVE_RESTORE", "Broad or worktree restore is blocked.")
    if subcommand == "branch":
        deletes = any(_short_flag_contains(token, "d") for token in original_tail) or (
            "--delete" in tail
        )
        forces = any(_short_flag_contains(token, "f") for token in original_tail) or any(
            token == "--force" or token.startswith("--force=") for token in tail
        )
        if "-D" in original_tail or (deletes and forces):
            return _deny("GIT_FORCE_BRANCH_DELETE", "Forced branch deletion is blocked.")
        protected_targets = [
            value
            for value in _git_branch_update_targets(original_tail)
            if _is_protected_branch(value, protected)
        ]
        if protected_targets:
            return _deny(
                "GIT_DIRECT_REF_UPDATE",
                "Creating, deleting, renaming, or resetting protected local branches is blocked.",
            )
    if subcommand in {"checkout", "switch"}:
        target = _git_created_branch_target(subcommand, original_tail)
        if target and _is_protected_branch(target, protected):
            return _deny(
                "GIT_DIRECT_REF_UPDATE",
                "Creating or resetting a protected local branch is blocked.",
            )
    if subcommand == "commit":
        if context.detached:
            return _deny("GIT_DETACHED_COMMIT", "Commit from detached HEAD is blocked.")
        if _is_protected_branch(context.branch, protected):
            return _deny("GIT_PROTECTED_BRANCH_COMMIT", "Commit on a protected branch is blocked.")
        if any(_is_commit_all_flag(token) for token in tail):
            return _deny("GIT_COMMIT_ALL", "Commit-all flags can capture unrelated changes.")
    if subcommand == "push":
        if "--mirror" in tail:
            return _deny("GIT_FORCE_PUSH", "Mirror pushes can rewrite and delete remote refs.")
        if "--all" in tail:
            return _deny(
                "GIT_PROTECTED_REF_PUSH",
                "Push-all can update protected refs outside the task branch.",
            )
        if any(
            _short_flag_contains(original, "f")
            or token in {"--force", "--force-with-lease", "--force-if-includes"}
            or token.startswith("--force=")
            or token.startswith("--force-with-lease=")
            or token.startswith("--force-if-includes=")
            or token.startswith("+")
            for token, original in zip(tail, original_tail, strict=True)
        ):
            return _deny("GIT_FORCE_PUSH", "History-rewriting pushes are blocked.")
        if (
            any(_short_flag_contains(token, "p") for token in original_tail)
            or "--prune" in tail
            or "--prune-tags" in tail
        ):
            return _deny("GIT_REMOTE_DELETE", "Remote pruning can delete refs.")
        if (
            "--delete" in tail
            or "-d" in tail
            or any(token.startswith(":") and len(token) > 1 for token in tail)
        ):
            return _deny("GIT_REMOTE_DELETE", "Remote ref deletion is blocked.")
        if ":" in tail:
            return _deny(
                "GIT_PROTECTED_REF_PUSH",
                "Matching-ref pushes can update protected branches outside the task branch.",
            )
        if any(
            any(marker in token for marker in ("*", "?", "["))
            for token in tail
            if not token.startswith("-")
        ):
            return _deny(
                "GIT_PROTECTED_REF_PUSH",
                "Wildcard refspecs can update protected branches outside the task branch.",
            )
        if any(_targets_protected_ref(token, protected) for token in tail):
            return _deny("GIT_PROTECTED_REF_PUSH", "Direct protected-ref push is blocked.")
        if _is_protected_branch(context.branch, protected) and not any(
            "head:" in token for token in tail
        ):
            return _deny("GIT_PROTECTED_REF_PUSH", "Push from a protected branch is blocked.")
    if subcommand == "update-ref":
        return _deny(
            "GIT_DIRECT_REF_UPDATE",
            "Direct ref database updates bypass branch and worktree safeguards.",
        )
    if subcommand == "worktree":
        action = _git_worktree_action(original_tail)
        for value in _git_worktree_paths(action, original_tail):
            if _filesystem_path_is_dynamic(value):
                return _deny(
                    "SHELL_CONTEXT_OPAQUE",
                    "Worktree mutation target cannot be resolved deterministically.",
                )
            worktree_target = _resolve_command_path(value, context.cwd)
            if not is_within(worktree_target, context.repo_root or context.cwd):
                return _deny(
                    "FILESYSTEM_OUTSIDE_REPO",
                    "Worktree mutation targets must stay within the active repository.",
                )
    if subcommand == "apply":
        if "--unsafe-paths" in tail:
            return _deny(
                "FILESYSTEM_OUTSIDE_REPO",
                "Applying patches with unsafe paths is blocked.",
            )
        output_paths = [
            value
            for option in ("--build-fake-ancestor", "--directory")
            if (value := _case_insensitive_option_value(original_tail, option))
        ]
        for output_path in output_paths:
            if _filesystem_path_is_dynamic(output_path):
                return _deny(
                    "SHELL_CONTEXT_OPAQUE",
                    "Git apply output path cannot be resolved deterministically.",
                )
            apply_target = _resolve_command_path(output_path, context.cwd)
            if not is_within(apply_target, context.repo_root or context.cwd):
                return _deny(
                    "FILESYSTEM_OUTSIDE_REPO",
                    "Git apply output must stay within the active repository.",
                )
    return None


def _filesystem_guard(tokens: list[str], context: RepoContext) -> GuardDecision | None:
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    operation = FILESYSTEM_MUTATION_COMMANDS.get(command)
    if operation is None:
        return None
    root = context.repo_root or context.cwd
    recursive = any(
        token in {"-r", "-rf", "-fr", "--recursive", "-recurse"} or "recurse" in token
        for token in lowered
    )
    paths = _path_arguments(tokens, command, operation)
    if not paths:
        return _deny("FILESYSTEM_TARGET_UNKNOWN", "Destructive target could not be resolved.")
    if any(_filesystem_path_is_dynamic(value) for value in paths):
        return _deny(
            "FILESYSTEM_TARGET_UNKNOWN",
            "Filesystem target uses a dynamic expression that cannot be resolved safely.",
        )
    resolved = [_resolve_command_path(value, context.cwd) for value in paths]
    if any(_is_broad_target(path) for path in resolved):
        return _deny("FILESYSTEM_BROAD_TARGET", "Broad filesystem targets are blocked.")
    if any(not is_within(path, root) for path in resolved):
        return _deny(
            "FILESYSTEM_OUTSIDE_REPO", "Delete/move targets must stay within the repository."
        )
    if recursive and any(path == canonical_path(root) for path in resolved):
        return _deny(
            "FILESYSTEM_RECURSIVE_REPO_DELETE", "Recursive repository deletion is blocked."
        )
    return None


def _redirection_guard(segment: str, context: RepoContext) -> GuardDecision | None:
    targets, has_file_redirection, opaque = _output_redirections(segment)
    if not has_file_redirection:
        return None
    if opaque or not targets:
        return _deny(
            "FILESYSTEM_TARGET_UNKNOWN",
            "Output-redirection target could not be resolved deterministically.",
        )
    root = context.repo_root or context.cwd
    resolved = [_resolve_command_path(value, context.cwd) for value in targets]
    if any(_is_broad_target(path) for path in resolved):
        return _deny("FILESYSTEM_BROAD_TARGET", "Broad filesystem targets are blocked.")
    if any(not is_within(path, root) for path in resolved):
        return _deny(
            "FILESYSTEM_OUTSIDE_REPO",
            "Output-redirection targets must stay within the active repository.",
        )
    return None


def _output_redirections(segment: str) -> tuple[list[str], bool, bool]:
    targets: list[str] = []
    quote = ""
    escaped = False
    found = False
    index = 0
    while index < len(segment):
        char = segment[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char in {"\\", "`"} and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char != ">":
            index += 1
            continue

        cursor = index + 1
        if cursor < len(segment) and segment[cursor] == ">":
            cursor += 1
        while cursor < len(segment) and segment[cursor].isspace():
            cursor += 1
        if cursor < len(segment) and segment[cursor] == "&":
            descriptor = segment[cursor + 1 : cursor + 2]
            if descriptor.isdigit() or descriptor == "-":
                index = cursor + 2
                continue
            cursor += 1
        tail_tokens = tokenize(segment[cursor:])
        if not tail_tokens:
            return targets, True, True
        target = tail_tokens[0]
        if target.lower() in {"/dev/null", "nul"}:
            index = cursor + len(target)
            continue
        found = True
        if _filesystem_path_is_dynamic(target):
            return targets, found, True
        targets.append(target)
        index = cursor + len(target)
    return targets, found, False


def _azure_guard(tokens: list[str], segment: str) -> GuardDecision | None:
    lowered = [token.lower() for token in tokens]
    command = _command_name(lowered)
    text = " ".join(lowered)
    if command == "az":
        if " repos pr set-vote " in f" {text} ":
            return _deny("ADO_SELF_APPROVAL", "Automation must not cast pull-request votes.")
        if " repos pr update " in f" {text} " and _option_value(lowered, "--status") in {
            "completed",
            "abandoned",
        }:
            return _deny(
                "ADO_DIRECT_COMPLETION",
                "Use policy-gated auto-complete; direct completion is blocked.",
            )
        if any("bypass" in token for token in lowered):
            return _deny("ADO_POLICY_BYPASS", "Azure DevOps policy bypass is blocked.")
        if re.search(r"\b(?:approve|approval|checks? approve)\b", segment, re.I) and re.search(
            r"\b(?:prod|production|environment)\b", segment, re.I
        ):
            return _deny(
                "PRODUCTION_APPROVAL_USER_OWNED",
                "Production and protected-environment approvals remain user-owned.",
            )

    if not _is_direct_azure_devops_rest(command, text):
        return None
    method = _rest_method(command, lowered)
    if method in {"get", "head"}:
        return None

    if re.search(
        r"""(?:["']?bypass(?:policy|reason)["']?\s*[:=]|[?&]bypass(?:policy|reason)=)""",
        segment,
        re.I,
    ):
        return _deny("ADO_POLICY_BYPASS", "Azure DevOps policy bypass is blocked.")

    reviewer_or_vote = bool(
        re.search(
            r"(?:pullrequests?/[^?\s]+/reviewers?|pullrequestreviewers|\bvote\b)", segment, re.I
        )
    )
    pr_completion = bool(
        re.search(r"pullrequests?", segment, re.I)
        and (
            re.search(
                r"""["']?status["']?\s*[:=]\s*["']?(?:completed|abandoned)\b""",
                segment,
                re.I,
            )
            or re.search(r"\bstatus(?:=|%3d)(?:completed|abandoned)\b", segment, re.I)
        )
    )
    protected_approval = bool(
        re.search(
            r"(?:approvalsandchecks|pipelines?/(?:approvals?|checks?)|"
            r"environments?/[^?\s]+/(?:approvals?|checks?))",
            segment,
            re.I,
        )
    )
    pull_request_mutation = bool(re.search(r"\bpullrequests?\b", segment, re.I))
    if protected_approval:
        return _deny(
            "PRODUCTION_APPROVAL_USER_OWNED",
            "Protected-environment approvals and checks remain user-owned.",
        )
    if reviewer_or_vote or pr_completion:
        return _deny(
            "ADO_REST_AUTHORITY_BYPASS",
            "Direct provider API authority bypass is blocked.",
        )
    if pull_request_mutation and not _safe_pull_request_metadata_body(tokens, segment):
        return _deny(
            "ADO_REST_AUTHORITY_BYPASS",
            "Pull-request REST writes require an inline title/description-only JSON body.",
        )
    return None


def _safe_pull_request_metadata_body(tokens: list[str], segment: str) -> bool:
    body = _rest_body_value(tokens, segment)
    if body is None:
        return False
    value = _strip_quotes(body.strip())
    if not value.startswith("{") or _path_expression_is_dynamic(value) or value.startswith("@"):
        return False
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict) or not parsed:
        return False
    allowed = {"description", "title"}
    if not set(parsed).issubset(allowed):
        return False
    return all(isinstance(item, str) for item in parsed.values())


def _rest_body_value(tokens: list[str], segment: str) -> str | None:
    quoted = re.search(
        r"""(?:--body|--data(?:-binary|-raw)?|-body|-d)(?:\s+|=)?(["'])(.*?)\1""",
        segment,
        re.I,
    )
    if quoted:
        return quoted.group(2)
    body_options = {
        "--body",
        "--data",
        "--data-binary",
        "--data-raw",
        "--in-file",
        "-body",
        "-d",
    }
    lowered = [token.lower() for token in tokens]
    for index, token in enumerate(lowered):
        if token in body_options:
            return tokens[index + 1] if index + 1 < len(tokens) else None
        for option in body_options:
            if token.startswith(option + "="):
                return tokens[index].split("=", 1)[1]
        if token.startswith("-d") and not token.startswith("--") and len(token) > 2:
            return tokens[index][2:]
    return None


def _is_direct_azure_devops_rest(command: str, text: str) -> bool:
    if command == "az" and (" rest " in f" {text} " or " devops invoke " in f" {text} "):
        return True
    if command not in {
        "curl",
        "invoke-restmethod",
        "invoke-webrequest",
        "irm",
        "iwr",
    }:
        return False
    return bool(
        re.search(r"(?:dev\.azure\.com|[\w.-]+\.visualstudio\.com)", text, re.I)
        and re.search(r"(?:_apis|pullrequestreviewers|approvalsandchecks)", text, re.I)
    )


def _rest_method(command: str, lowered: list[str]) -> str:
    for option in ("--method", "--http-method", "--request", "-method", "-x"):
        value = _option_value(lowered, option)
        if value:
            return value.lower()
    if command == "curl":
        attached = next(
            (
                token[2:]
                for token in lowered
                if token.startswith("-x") and not token.startswith("--") and len(token) > 2
            ),
            "",
        )
        if attached:
            return attached
    if command == "curl" and any(
        token in {"-d", "--data", "--data-raw", "--data-binary"}
        or token.startswith("--data=")
        or (token.startswith("-d") and not token.startswith("--") and len(token) > 2)
        for token in lowered
    ):
        return "post"
    return "get"


def _secret_exposure(command: str) -> GuardDecision | None:
    if any(pattern.search(command) for pattern in SECRET_READ_PATTERNS):
        return _deny("SECRET_STORE_READ", "Commands that return secret values are blocked.", "read")
    if _direct_secret_environment_read(command):
        return _deny(
            "SECRET_ENV_READ",
            "Direct reads of secret-bearing environment values are blocked.",
            "read",
        )
    references = set(
        re.findall(r"\$env:([A-Za-z_][A-Za-z0-9_]*)", command, re.I)
        + re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", command)
        + re.findall(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)", command)
        + re.findall(r"%([A-Za-z_][A-Za-z0-9_]*)%", command)
        + re.findall(r"!([A-Za-z_][A-Za-z0-9_]*)!", command)
    )
    if any(_is_secret_environment_name(name) for name in references):
        return _deny(
            "SECRET_ENV_EXPANSION",
            "Secret-bearing environment expansion is blocked.",
            "read",
        )
    return None


def _direct_secret_environment_read(command: str) -> bool:
    if _dotnet_environment_read(command):
        return True
    provider_commands = {
        "cat",
        "dir",
        "gc",
        "gci",
        "get-childitem",
        "get-content",
        "get-item",
        "get-itemproperty",
        "gi",
        "gp",
        "ls",
        "type",
    }
    for segment, _separator in _split_command_steps(command):
        tokens = tokenize(segment)
        if not tokens:
            continue
        lowered = [token.lower() for token in tokens]
        command_name = _command_name(lowered)
        command_index = _command_index(lowered)
        arguments = tokens[command_index + 1 :]
        values = [token for token in arguments if not token.startswith("-")]

        if _python_environment_read(command_name, segment):
            return True
        if command_name == "printenv":
            if not values or any(
                _is_secret_environment_name(_environment_name(value)) for value in values
            ):
                return True
        if command_name == "env" and not values:
            return True
        if command_name == "set":
            if not values:
                return True
            candidate = values[0]
            if "=" not in candidate and _is_secret_environment_name(_environment_name(candidate)):
                return True
        if command_name not in provider_commands:
            continue
        for token in arguments:
            match = re.match(r"^(?:env:|environment::)[\\/]*(.*)$", _strip_quotes(token), re.I)
            if not match:
                continue
            name = match.group(1).strip()
            if not name or any(marker in name for marker in ("*", "?")):
                return True
            if _is_secret_environment_name(_environment_name(name)):
                return True
    return False


def _dotnet_environment_read(command: str) -> bool:
    pattern = re.compile(
        r"""\[(?:System\.)?Environment\]\s*::\s*GetEnvironmentVariable\s*"""
        r"""\(\s*(["'])([A-Za-z_][A-Za-z0-9_]*)\1""",
        re.I,
    )
    return any(_is_secret_environment_name(match.group(2)) for match in pattern.finditer(command))


def _python_environment_read(command_name: str, segment: str) -> bool:
    if command_name != "py" and not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", command_name):
        return False
    patterns = (
        re.compile(
            r"""os\.environ\s*\[\s*(["'])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]""",
            re.I,
        ),
        re.compile(
            r"""os\.environ\.get\s*\(\s*(["'])([A-Za-z_][A-Za-z0-9_]*)\1""",
            re.I,
        ),
        re.compile(
            r"""os\.getenv\s*\(\s*(["'])([A-Za-z_][A-Za-z0-9_]*)\1""",
            re.I,
        ),
    )
    return any(
        _is_secret_environment_name(match.group(2))
        for pattern in patterns
        for match in pattern.finditer(segment)
    )


def _is_secret_environment_name(value: str) -> bool:
    name = _environment_name(value)
    return bool(SECRET_NAME_RE.search(name))


def _environment_name(value: str) -> str:
    return _strip_quotes(value).strip().removeprefix("$").split("=", 1)[0]


def _tool_mutation_guard(
    event: EventEnvelope,
    context: RepoContext,
) -> GuardDecision | None:
    root = context.repo_root or context.cwd
    values: list[str] = []
    if isinstance(event.tool_input, dict):
        for key in ("path", "file_path", "target", "destination"):
            value = event.tool_input.get(key)
            if isinstance(value, str) and value:
                values.append(value)
        patch = event.tool_input.get("patch")
        if not isinstance(patch, str):
            patch = event.tool_input.get("input")
        if isinstance(patch, str):
            values.extend(
                re.findall(
                    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
                    patch,
                    re.MULTILINE,
                )
            )
    if not values:
        return None
    for value in values:
        target = _resolve_command_path(value.strip(), context.cwd)
        if not is_within(target, root):
            return _deny(
                "FILESYSTEM_OUTSIDE_REPO",
                "Mutation tool targets must stay within the active repository.",
            )
    return None


def _command_name(lowered: list[str]) -> str:
    index = _command_index(lowered)
    return _executable_name(lowered[index]) if index < len(lowered) else ""


def _command_index(lowered: list[str]) -> int:
    index = 0
    while index < len(lowered):
        assignment_end = _inline_environment_assignment_end(lowered, index)
        if assignment_end is not None:
            index = assignment_end
            continue
        command = _executable_name(lowered[index])
        if command in {"&", "call", "command", "exec", "nohup", "time"}:
            index += 1
            while index < len(lowered) and lowered[index] == "--":
                index += 1
            continue
        if command == "env":
            candidate = index + 1
            while candidate < len(lowered):
                token = lowered[candidate]
                if token == "--":
                    candidate += 1
                    break
                if token in {"-u", "--unset", "-c", "--chdir"}:
                    candidate += 2
                    continue
                assignment_end = _inline_environment_assignment_end(lowered, candidate)
                if assignment_end is not None:
                    candidate = assignment_end
                    continue
                if token.startswith("-"):
                    candidate += 1
                    continue
                break
            if candidate >= len(lowered):
                return index
            index = candidate
            continue
        if command == "nice":
            candidate = index + 1
            if candidate < len(lowered) and lowered[candidate] in {"-n", "--adjustment"}:
                candidate += 2
            elif candidate < len(lowered) and re.fullmatch(r"-\d+", lowered[candidate]):
                candidate += 1
            if candidate >= len(lowered):
                return index
            index = candidate
            continue
        if command == "sudo":
            candidate = index + 1
            sudo_value_options = {
                "-c",
                "-g",
                "-h",
                "-p",
                "-r",
                "-t",
                "-u",
                "--chdir",
                "--group",
                "--host",
                "--prompt",
                "--role",
                "--type",
                "--user",
            }
            while candidate < len(lowered):
                token = lowered[candidate]
                if token == "--":
                    candidate += 1
                    break
                if token in sudo_value_options:
                    candidate += 2
                    continue
                if token.startswith("-"):
                    candidate += 1
                    continue
                break
            if candidate >= len(lowered):
                return index
            index = candidate
            continue
        break
    return index


def _inline_environment_assignment_end(tokens: list[str], index: int) -> int | None:
    match = re.fullmatch(r"[a-z_][a-z0-9_]*(?:\+)?=(.*)", tokens[index])
    if not match:
        return None
    value = match.group(1)
    end = index + 1
    for quote in ("'", '"'):
        if value.count(quote) % 2 == 0:
            continue
        while end < len(tokens):
            if tokens[end].count(quote) % 2:
                return end + 1
            end += 1
        return end
    while value.endswith("\\") and end < len(tokens):
        value = tokens[end]
        end += 1
    substitution_depth = value.count("$(") - value.count(")")
    while substitution_depth > 0 and end < len(tokens):
        substitution_depth += tokens[end].count("(") - tokens[end].count(")")
        end += 1
    return end


def _executable_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _git_subcommand(lowered: list[str]) -> str | None:
    try:
        index = next(
            index for index, token in enumerate(lowered) if _executable_name(token) == "git"
        )
    except StopIteration:
        return None
    index += 1
    options_with_value = {"-c", "--git-dir", "--work-tree", "--namespace"}
    while index < len(lowered):
        token = lowered[index]
        if token in options_with_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def _git_worktree_action(tokens: list[str]) -> str:
    actions = {"add", "list", "lock", "move", "prune", "remove", "repair", "unlock"}
    return next((token.lower() for token in tokens if token.lower() in actions), "")


def _git_worktree_paths(action: str, tokens: list[str]) -> list[str]:
    if action not in {"add", "lock", "move", "remove", "repair", "unlock"}:
        return []
    lowered = [token.lower() for token in tokens]
    try:
        action_index = lowered.index(action)
    except ValueError:
        return []
    options_with_value = {"-b", "--expire", "--reason"}
    positional: list[str] = []
    index = action_index + 1
    while index < len(tokens):
        token = tokens[index]
        lower = lowered[index]
        if lower in options_with_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1
    if action in {"add", "lock", "remove", "unlock"}:
        return positional[:1]
    if action == "move":
        return positional[:2]
    return positional


def _git_branch_update_targets(tokens: list[str]) -> list[str]:
    lowered = [token.lower() for token in tokens]
    options_with_value = {
        "--color",
        "--contains",
        "--format",
        "--merged",
        "--no-contains",
        "--no-merged",
        "--points-at",
        "--sort",
    }
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lower = lowered[index]
        if lower in options_with_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if not positionals:
        return []

    long_mutation = {"--copy", "--delete", "--force", "--move"}
    short_mutation = any(
        _short_flag_contains(token, flag) for token in tokens for flag in ("c", "d", "f", "m")
    )
    mutating = short_mutation or any(
        token in long_mutation or any(token.startswith(option + "=") for option in long_mutation)
        for token in lowered
    )
    read_only = any(
        token
        in {
            "--all",
            "--contains",
            "--format",
            "--list",
            "--merged",
            "--no-contains",
            "--no-merged",
            "--points-at",
            "--remotes",
            "--show-current",
            "-a",
            "-r",
            "-v",
            "-vv",
        }
        for token in lowered
    ) or any(
        token.startswith("-")
        and not token.startswith("--")
        and bool(token[1:])
        and set(token[1:]).issubset({"a", "r", "v"})
        for token in lowered
    )
    config_only = any(
        token in {"--edit-description", "--set-upstream-to", "--unset-upstream"}
        or token.startswith("--set-upstream-to=")
        for token in lowered
    )
    if config_only or (read_only and not mutating):
        return []
    renames_copies_or_deletes = any(
        token in {"--copy", "--delete", "--move"}
        or _short_flag_contains(original, "c")
        or _short_flag_contains(original, "d")
        or _short_flag_contains(original, "m")
        for token, original in zip(lowered, tokens, strict=True)
    )
    return positionals if renames_copies_or_deletes else positionals[:1]


def _git_created_branch_target(subcommand: str, tokens: list[str]) -> str:
    checkout_options = {"-b", "-B", "--orphan"}
    switch_options = {"-c", "-C", "--create", "--force-create", "--orphan"}
    options = checkout_options if subcommand == "checkout" else switch_options
    for index, token in enumerate(tokens):
        if token in options:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        lower = token.lower()
        for option in {value for value in options if value.startswith("--")}:
            if lower.startswith(option + "="):
                return token.split("=", 1)[1]
        if token[:2] in {value for value in options if len(value) == 2} and len(token) > 2:
            return token[2:]
    return ""


def _case_insensitive_option_value(tokens: list[str], option: str) -> str:
    for index, token in enumerate(tokens):
        lower = token.lower()
        if lower == option:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        if lower.startswith(option + "="):
            return token.split("=", 1)[1]
    return ""


def _unit_git_subcommand(unit: _CommandUnit) -> str | None:
    return _git_subcommand([token.lower() for token in tokenize(unit.segment)])


def _git_effective_cwd(tokens: list[str], cwd: Path) -> Path | None:
    lowered = [token.lower() for token in tokens]
    try:
        index = next(
            index for index, token in enumerate(lowered) if _executable_name(token) == "git"
        )
    except StopIteration:
        return cwd
    index += 1
    effective = canonical_path(cwd)
    while index < len(tokens):
        token = tokens[index]
        lower = lowered[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return None
            value = tokens[index + 1]
            if _path_expression_is_dynamic(value):
                return None
            candidate = Path(os.path.expanduser(os.path.expandvars(_strip_quotes(value))))
            if not candidate.is_absolute():
                candidate = effective / candidate
            effective = canonical_path(candidate)
            index += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            value = token[2:]
            if _path_expression_is_dynamic(value):
                return None
            candidate = Path(os.path.expanduser(os.path.expandvars(_strip_quotes(value))))
            if not candidate.is_absolute():
                candidate = effective / candidate
            effective = canonical_path(candidate)
            index += 1
            continue
        if not lower.startswith("-"):
            break
        if lower in {"-c", "--git-dir", "--work-tree", "--namespace"}:
            index += 2
        else:
            index += 1
    return effective


def _git_explicit_context_paths(tokens: list[str], cwd: Path) -> list[Path] | None:
    lowered = [token.lower() for token in tokens]
    try:
        index = next(
            index for index, token in enumerate(lowered) if _executable_name(token) == "git"
        )
    except StopIteration:
        return []
    index += 1
    effective = canonical_path(cwd)
    explicit_paths: list[Path] = []
    while index < len(tokens):
        token = tokens[index]
        lower = lowered[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return None
            resolved = _resolve_git_context_path(tokens[index + 1], effective)
            if resolved is None:
                return None
            effective = resolved
            index += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            resolved = _resolve_git_context_path(token[2:], effective)
            if resolved is None:
                return None
            effective = resolved
            index += 1
            continue
        if lower in {"--git-dir", "--work-tree"}:
            if index + 1 >= len(tokens):
                return None
            path = _resolve_git_context_path(tokens[index + 1], effective)
            if path is None:
                return None
            explicit_paths.append(path)
            index += 2
            continue
        if lower.startswith("--git-dir=") or lower.startswith("--work-tree="):
            path = _resolve_git_context_path(token.split("=", 1)[1], effective)
            if path is None:
                return None
            explicit_paths.append(path)
            index += 1
            continue
        if not lower.startswith("-"):
            break
        if lower in {"-c", "--namespace", "--config-env"}:
            index += 2
        else:
            index += 1
    return explicit_paths


def _resolve_git_context_path(value: str, cwd: Path) -> Path | None:
    if _path_expression_is_dynamic(value):
        return None
    expanded = Path(os.path.expanduser(os.path.expandvars(_strip_quotes(value))))
    return canonical_path(expanded if expanded.is_absolute() else cwd / expanded)


def _crosses_nested_git_boundary(cwd: Path, root: Path) -> bool:
    current = canonical_path(cwd)
    root = canonical_path(root)
    if not is_within(current, root):
        return False
    while current != root:
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


def _path_arguments(tokens: list[str], command: str, operation: str) -> list[str]:
    try:
        command_index = next(
            index
            for index, token in enumerate(tokens)
            if Path(token.lower()).name.removesuffix(".exe") == command
        )
    except StopIteration:
        return []
    path_options = {
        "-destination",
        "-filepath",
        "-literalpath",
        "-name",
        "-newname",
        "-path",
    }
    non_path_options_with_value = {
        "-credential",
        "-encoding",
        "-erroraction",
        "-exclude",
        "-path",
        "-filter",
        "-include",
        "-inputobject",
        "-itemtype",
        "-stream",
        "-value",
    }
    paths: list[str] = []
    positional: list[str] = []
    index = command_index + 1
    while index < len(tokens):
        token = tokens[index]
        lower = token.lower()
        if lower in path_options:
            if index + 1 < len(tokens):
                paths.append(tokens[index + 1])
            index += 2
            continue
        if lower in non_path_options_with_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1
    if operation in {"write", "create"}:
        if not paths and positional:
            paths.append(positional[0])
    elif operation in {"copy", "move"}:
        paths.extend(positional[:2])
    else:
        paths.extend(positional)
    return paths


def _resolve_command_path(value: str, cwd: Path) -> Path:
    expanded = os.path.expandvars(_strip_quotes(value))
    path = Path(expanded)
    return canonical_path(path if path.is_absolute() else cwd / path)


def _is_broad_target(path: Path) -> bool:
    resolved = canonical_path(path)
    anchors = {canonical_path(Path(resolved.anchor))} if resolved.anchor else set()
    anchors.add(canonical_path(Path.home()))
    return resolved in anchors


def _targets_protected_ref(token: str, protected: Iterable[str]) -> bool:
    lower = token.lower()
    if lower.startswith("-"):
        return False
    if lower.startswith("+"):
        return True
    destination = lower.rsplit(":", 1)[-1].removeprefix("refs/heads/")
    return _is_protected_branch(destination, protected)


def _is_commit_all_flag(token: str) -> bool:
    if token == "--all":
        return True
    return (
        token.startswith("-")
        and not token.startswith("--")
        and "=" not in token
        and "a" in token[1:]
    )


def _short_flag_contains(token: str, flag: str) -> bool:
    return (
        token.startswith("-")
        and not token.startswith("--")
        and "=" not in token
        and flag.lower() in token[1:].lower()
    )


def _is_protected_branch(branch: str, protected: Iterable[str]) -> bool:
    value = branch.lower().removeprefix("refs/heads/")
    return any(fnmatchcase(value, pattern.lower()) for pattern in protected)


def _option_value(tokens: list[str], option: str) -> str:
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(option + "="):
            return token.split("=", 1)[1]
    return ""


def _azure_write_tokens(tokens: list[str]) -> bool:
    writes = {"create", "update", "delete", "set-vote", "run", "queue", "invoke"}
    return bool(writes.intersection(tokens))


def _is_plan_mode(mode: str) -> bool:
    return mode.strip().lower() in {"plan", "plan mode", "readonly", "read-only"}


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _deny(code: str, message: str, action: str = "mutation") -> GuardDecision:
    return GuardDecision(deny=True, reason_code=code, message=message, action_type=action)
