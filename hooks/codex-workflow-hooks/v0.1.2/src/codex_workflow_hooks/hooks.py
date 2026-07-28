"""Codex hook event handlers. Event-time handlers never access the network."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .evidence import EvidenceLedger
from .guards import (
    assess_pre_tool,
    classify_segment,
    is_git_dry_run,
    split_command_segments,
    tokenize,
)
from .models import DeliveryState, EventEnvelope, HookEvent, RepoContext
from .policy import (
    default_data_dir,
    effective_policy,
    resolve_repo_context,
)
from .runtime_log import write_log
from .utils import load_json, parse_exit_code, run_git, sha256_text


def handle_event(
    event_name: str,
    payload: Any,
    *,
    data_dir: Path | None = None,
) -> dict[str, Any] | None:
    event = EventEnvelope.from_payload(payload)
    if event.hook_event_name != event_name:
        raise ValueError(
            f"Hook event mismatch: expected {event_name}, received {event.hook_event_name}."
        )
    event_kind = HookEvent(event_name)
    storage = data_dir or default_data_dir()
    context = resolve_repo_context(event.cwd, storage)
    ledger = EvidenceLedger(storage)
    ledger.start_session(event, context)

    if event_kind is HookEvent.SESSION_START:
        return _session_context(context)
    if event_kind is HookEvent.SUBAGENT_START:
        return _subagent_context(context)
    if event_kind is HookEvent.PRE_TOOL_USE:
        return _pre_tool(event, context, storage, ledger)
    if event_kind is HookEvent.POST_TOOL_USE:
        _post_tool(event, context, storage, ledger)
        return None
    if event_kind is HookEvent.STOP:
        return _stop(event, context, storage, ledger)
    if event_kind is HookEvent.SESSION_END:
        ledger.end_session(event.session_id, context)
        return None
    return None


def _session_context(context: RepoContext) -> dict[str, Any]:
    registration = (
        f"managed={str(context.managed).lower()}, mode={context.rollout_mode}"
        if context.repo_root
        else "outside Git; global safety core only"
    )
    text = (
        "Central Codex workflow hooks active. "
        f"Repository policy: {registration}. "
        "Delivery evidence states are independent; local changes, merge, release, deployment, "
        "runtime health, and user-path proof are never interchangeable. "
        "Hooks are guardrails; provider policies remain authoritative."
    )
    if context.policy_error:
        text += f" Policy status: {context.policy_error}."
    return _additional_context(HookEvent.SESSION_START.value, text)


def _subagent_context(context: RepoContext) -> dict[str, Any]:
    text = (
        "Use the central safety core. Do not self-approve, directly complete a PR, force-push, "
        "bypass provider policies, or approve production gates. "
        "Treat validation and every delivery state as separate evidence."
    )
    if context.expected_managed and not context.managed:
        text += " This managed repository is unregistered; mutations will be denied."
    return _additional_context(HookEvent.SUBAGENT_START.value, text)


def _pre_tool(
    event: EventEnvelope,
    context: RepoContext,
    data_dir: Path,
    ledger: EvidenceLedger,
) -> dict[str, Any] | None:
    try:
        policy = effective_policy(context)
        decision = assess_pre_tool(event, context, policy)
    except (OSError, ValueError) as exc:
        write_log(data_dir, "policy_error", error=exc.__class__.__name__)
        if _looks_mutating(event):
            decision_code = "POLICY_UNAVAILABLE"
            ledger.record_event(
                event,
                context,
                action_type="mutation",
                reason_code=decision_code,
            )
            if _is_enforcing(context, data_dir):
                return _deny_pre_tool(
                    decision_code,
                    "Hook policy could not be verified; mutation is denied fail-closed.",
                )
        return None

    ledger.record_event(
        event,
        context,
        action_type=decision.action_type,
        reason_code=decision.reason_code,
    )
    if not decision.deny:
        return None
    write_log(
        data_dir,
        "guard_decision",
        reason_code=decision.reason_code,
        action_type=decision.action_type,
        enforced=_is_enforcing(context, data_dir),
    )
    if not _is_enforcing(context, data_dir):
        return None
    return _deny_pre_tool(decision.reason_code, decision.message)


def _post_tool(
    event: EventEnvelope,
    context: RepoContext,
    data_dir: Path,
    ledger: EvidenceLedger,
) -> None:
    exit_code = parse_exit_code(event.tool_response)
    explicit_success = exit_code == 0
    segments = split_command_segments(event.command())
    facts: set[tuple[str, str]] = set()
    if event.tool_name.lower() in {"apply_patch", "edit", "write", "multiedit"}:
        facts.add(("mutation", ""))
    for segment in segments:
        if _is_provider_write(segment):
            facts.add(("provider_write", _provider_evidence_key(segment, event.tool_response)))
            continue
        if _is_provider_readback(segment):
            facts.add(("provider_readback", _provider_evidence_key(segment, event.tool_response)))
            continue
        if classify_segment(segment) == "mutation":
            facts.add(("mutation", ""))
    policy: dict[str, Any] = {}
    try:
        policy = effective_policy(context)
    except (OSError, ValueError):
        write_log(data_dir, "validation_policy_error", reason_code="POLICY_UNAVAILABLE")
    profile_matches = (
        _validation_matches(event.command(), context, policy) if explicit_success else []
    )
    if (
        explicit_success
        and len(segments) == 1
        and (_is_validation_command(segments[0]) or profile_matches)
    ):
        facts.add(("validation", ""))
    if not facts:
        facts.add(("read", ""))
    for action, reason_code in sorted(facts):
        ledger.record_event(
            event,
            context,
            action_type=action,
            reason_code=reason_code,
        )
    if not explicit_success:
        write_log(data_dir, "post_tool", action_type=",".join(sorted(a for a, _ in facts)))
        return None

    command_hash = sha256_text(event.command()) if event.command() else event.tool_use_id
    if any(action == "mutation" for action, _ in facts):
        ledger.mark_state(
            event.session_id,
            context,
            DeliveryState.SOURCE_MODIFIED,
            evidence_key=event.tool_use_id or command_hash,
            source=event.tool_name or "tool",
        )
    if any(action == "validation" for action, _ in facts):
        ledger.mark_state(
            event.session_id,
            context,
            DeliveryState.VALIDATED,
            evidence_key=event.tool_use_id or command_hash,
            source="local_command",
        )
        for profile_id, step_index in profile_matches:
            ledger.mark_state(
                event.session_id,
                context,
                DeliveryState.VALIDATED,
                evidence_key=f"validation:{profile_id}:{step_index}",
                source="repository_validation_profile",
            )

    command = event.command().lower()
    single_segment = len(segments) == 1
    if (
        single_segment
        and re.search(
            r"\bgit(?:\s+-c\s+\S+|\s+-C\s+\S+)*\s+commit\b",
            command,
            re.I,
        )
        and not is_git_dry_run(segments[0], "commit")
    ):
        ledger.mark_state(
            event.session_id,
            context,
            DeliveryState.COMMITTED,
            evidence_key=event.tool_use_id or command_hash,
            source="git",
            digest=context.head,
            metadata={"branch": context.branch},
        )
    if (
        single_segment
        and re.search(
            r"\bgit(?:\s+-c\s+\S+|\s+-C\s+\S+)*\s+push\b",
            command,
            re.I,
        )
        and not is_git_dry_run(segments[0], "push")
    ):
        ledger.mark_state(
            event.session_id,
            context,
            DeliveryState.PUSHED,
            evidence_key=event.tool_use_id or command_hash,
            source="git",
            digest=context.head,
            metadata={"branch": context.branch},
        )
    write_log(
        data_dir,
        "post_tool",
        action_type=",".join(sorted(action for action, _ in facts)),
        success=True,
    )
    return None


def _stop(
    event: EventEnvelope,
    context: RepoContext,
    data_dir: Path,
    ledger: EvidenceLedger,
) -> dict[str, Any] | None:
    if event.stop_hook_active:
        return None
    summary = ledger.summary(event.session_id, context)
    reasons: list[str] = []
    mutation_count = int(summary.get("mutation_count", 0))
    if mutation_count > 0:
        required_steps = _required_validation_steps(
            context,
            effective_policy(context),
            str(summary.get("baseline_head", "")),
        )
        completed = ledger.evidence_keys(
            event.session_id,
            context,
            DeliveryState.VALIDATED,
        )
        missing_profiles = [
            profile_id
            for profile_id, step_indexes in required_steps.items()
            if any(f"validation:{profile_id}:{index}" not in completed for index in step_indexes)
        ]
        if missing_profiles:
            reasons.append(
                "required validation profiles are incomplete: "
                + ", ".join(sorted(missing_profiles))
            )
        elif not required_steps and int(summary.get("validation_count", 0)) == 0:
            reasons.append(
                "mutation evidence exists but no successful validation evidence was recorded"
            )
    unmatched_provider_writes = ledger.unmatched_provider_writes(event.session_id, context)
    if unmatched_provider_writes:
        reasons.append(
            f"{len(unmatched_provider_writes)} provider write target(s) have no matching read-back"
        )
    reasons.extend(_unsupported_claims(event.last_assistant_message, summary.get("states", [])))
    if not reasons:
        return None
    write_log(data_dir, "stop_evidence_gap", reason_code="STOP_EVIDENCE_GAP")
    if not _is_enforcing(context, data_dir):
        return None
    if not ledger.issue_continuation_once(event.session_id, context):
        return None
    reason = (
        "Central hook evidence gate: "
        + "; ".join(reasons)
        + ". Continue once to validate or report the exact blocker. "
        "A final prose assertion does not create delivery evidence."
    )
    return {"decision": "block", "reason": reason}


def _is_validation_command(command: str) -> bool:
    tokens = tokenize(command)
    if not tokens:
        return False
    lowered = [token.casefold() for token in tokens]
    executable = _executable_name(lowered[0])
    raw_arguments = tokens[1:]
    arguments = lowered[1:]

    # A nested shell can hide a failed validation command behind its own zero
    # exit status. Repository profiles match the full command separately; this
    # generic fallback only accepts validation processes invoked directly.
    if executable in {
        "bash",
        "cmd",
        "dash",
        "env",
        "ksh",
        "powershell",
        "pwsh",
        "sh",
        "sudo",
        "zsh",
    }:
        return False

    if executable == "py" or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", executable):
        if _validation_is_nonqualifying(raw_arguments, "python"):
            return False
        if "-m" in arguments:
            module_index = arguments.index("-m") + 1
            if module_index >= len(arguments):
                return False
            module = arguments[module_index]
            module_arguments = raw_arguments[module_index + 1 :]
            return module in {
                "compileall",
                "mypy",
                "pytest",
                "ruff",
                "unittest",
            } and not _validation_is_nonqualifying(module_arguments, module)
        script = next((token for token in arguments if not token.startswith("-")), "")
        script_name = _executable_name(script)
        return script_name in {
            "benchmark.py",
            "run_quality_gate.py",
        } and not _validation_is_nonqualifying(raw_arguments, script_name)

    if executable in {"mypy", "pytest", "ruff", "unittest"}:
        return not _validation_is_nonqualifying(raw_arguments, executable)
    if executable == "dotnet":
        return (
            bool(arguments)
            and arguments[0] == "test"
            and not _validation_is_nonqualifying(raw_arguments, "dotnet")
        )
    if executable == "pnpm":
        return (
            bool(arguments)
            and arguments[0]
            in {
                "build",
                "format:check",
                "lint",
                "test",
                "typecheck",
            }
            and not _validation_is_nonqualifying(raw_arguments, "pnpm")
        )
    if executable == "npm":
        return (
            bool(arguments)
            and (
                arguments[0] == "test"
                or (
                    len(arguments) >= 2
                    and arguments[0] == "run"
                    and arguments[1] in {"build", "lint", "typecheck"}
                )
            )
            and not _validation_is_nonqualifying(raw_arguments, "npm")
        )
    if executable in {"benchmark.py", "check-fast", "run_quality_gate.py"}:
        return not _validation_is_nonqualifying(raw_arguments, executable)
    return (
        executable == "azure-pipelines"
        and any(argument in {"run", "show"} for argument in arguments)
        and not _validation_is_nonqualifying(raw_arguments, executable)
    )


def _validation_is_nonqualifying(arguments: list[str], validator: str) -> bool:
    lowered = [argument.casefold() for argument in arguments]
    options = {argument.split("=", 1)[0] for argument in lowered if argument.startswith("-")}
    if any(re.fullmatch(r"-V+", argument) for argument in arguments):
        return True
    if options.intersection(
        {
            "-h",
            "--collect-only",
            "--collectonly",
            "--co",
            "--dry-run",
            "--help",
            "--if-present",
            "--list",
            "--list-tests",
            "--version",
        }
    ):
        return True
    if validator == "pytest" and options.intersection(
        {
            "--fixtures",
            "--fixtures-per-test",
            "--markers",
            "--setup-plan",
            "--trace-config",
        }
    ):
        return True
    if validator == "dotnet" and "-t" in options:
        return True
    if validator == "ruff":
        subcommand = next(
            (argument for argument in lowered if not argument.startswith("-")),
            "",
        )
        if subcommand == "check":
            return False
        return subcommand != "format" or "--check" not in options
    return validator in {"npm", "pnpm"} and "-v" in options


def _executable_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_provider_write(command: str) -> bool:
    if _direct_azure_rest_target(command):
        return _provider_http_method(command) not in {"get", "head"}
    if re.search(r"\baz(?:\.cmd)?\s+devops\s+invoke\b", command, re.I):
        return _provider_http_method(command) not in {"get", "head"}
    if not re.search(r"\baz(?:\.cmd)?\b", command, re.I):
        return False
    writes = r"\b(?:create|update|delete|set|add|remove|queue|run|invoke)\b"
    return bool(re.search(writes, command, re.I) and not _is_provider_readback(command))


def _is_provider_readback(command: str) -> bool:
    if _direct_azure_rest_target(command):
        return _provider_http_method(command) in {"get", "head"}
    if re.search(r"\baz(?:\.cmd)?\s+devops\s+invoke\b", command, re.I):
        return _provider_http_method(command) in {"get", "head"}
    return bool(
        re.search(r"\baz(?:\.cmd)?\b", command, re.I)
        and re.search(r"\b(?:show|list)\b", command, re.I)
        and not re.search(r"\b(?:create|update|delete|set-vote|add|remove)\b", command, re.I)
    )


def _provider_evidence_key(segment: str, response: Any) -> str:
    arguments = tokenize(segment)
    lowered = [value.casefold() for value in arguments]
    rest_target = _direct_azure_rest_target(segment)
    if rest_target:
        return "provider:" + sha256_text(f"azure-rest:{rest_target}")[:32]
    az_index = next(
        (index for index, value in enumerate(lowered) if value in {"az", "az.cmd"}),
        -1,
    )
    if az_index < 0:
        return "provider:" + sha256_text("unknown")[:32]
    family_parts: list[str] = []
    action_words = {
        "add",
        "create",
        "delete",
        "invoke",
        "list",
        "remove",
        "run",
        "set",
        "show",
        "update",
    }
    for argument in lowered[az_index + 1 :]:
        if argument.startswith("-"):
            break
        if family_parts == ["pipelines"] and argument in {"run", "runs"}:
            family_parts.append("run")
            break
        if argument in action_words and family_parts:
            break
        family_parts.append(argument)
        if len(family_parts) == 2 or argument == "rest":
            break
    family = ":".join(family_parts) or "unknown"
    identifier = _provider_identifier(arguments, response)
    return "provider:" + sha256_text(f"{family}:{identifier}")[:32]


def _provider_identifier(arguments: list[str], response: Any) -> str:
    lowered = [value.casefold() for value in arguments]
    response_id = _safe_response_id(response)
    if response_id:
        return response_id
    for option in (
        "--id",
        "--pull-request-id",
        "--work-item-id",
        "--run-id",
        "--build-id",
        "--policy-id",
    ):
        for index, argument in enumerate(lowered):
            if argument == option and index + 1 < len(arguments):
                return str(arguments[index + 1]).casefold()
            if argument.startswith(option + "="):
                return argument.split("=", 1)[1].casefold()
    for option in ("--url", "--name", "--repository", "--pipeline-name"):
        for index, argument in enumerate(lowered):
            if argument == option and index + 1 < len(arguments):
                return sha256_text(str(arguments[index + 1]).casefold())[:24]
            if argument.startswith(option + "="):
                return sha256_text(argument.split("=", 1)[1].casefold())[:24]
    invoke_parts: list[str] = []
    for option in ("--area", "--resource", "--route-parameters"):
        for index, argument in enumerate(lowered):
            if argument == option and index + 1 < len(arguments):
                invoke_parts.extend((option, str(arguments[index + 1]).casefold()))
            elif argument.startswith(option + "="):
                invoke_parts.extend((option, argument.split("=", 1)[1].casefold()))
    if invoke_parts:
        return sha256_text("\0".join(invoke_parts))[:24]
    return "unresolved:" + sha256_text("\0".join(lowered))[:24]


def _direct_azure_rest_target(command: str) -> str:
    lowered = command.casefold()
    if not re.search(r"(?:dev\.azure\.com|[\w.-]+\.visualstudio\.com)", lowered):
        return ""
    if not re.search(r"(?:_apis|pullrequestreviewers|approvalsandchecks)", lowered):
        return ""
    arguments = tokenize(command)
    for index, argument in enumerate(arguments):
        value = argument.strip("'\"")
        lower = value.casefold()
        if lower in {"--url", "--uri", "-uri"} and index + 1 < len(arguments):
            value = arguments[index + 1].strip("'\"")
        elif not lower.startswith(("https://", "http://")):
            continue
        parsed = urlsplit(value)
        if parsed.scheme.casefold() in {"https", "http"} and parsed.netloc:
            return sha256_text(
                f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path.casefold()}"
            )[:24]
    return ""


def _provider_http_method(command: str) -> str:
    arguments = tokenize(command)
    lowered = [value.casefold() for value in arguments]
    for option in ("--method", "--http-method", "--request", "-method", "-x"):
        for index, argument in enumerate(lowered):
            if argument == option and index + 1 < len(arguments):
                return lowered[index + 1]
            if argument.startswith(option + "="):
                return argument.split("=", 1)[1]
            if option == "-x" and argument.startswith("-x") and len(argument) > 2:
                return argument[2:]
    if any(
        argument in {"-d", "--data", "--data-raw", "--data-binary"}
        or argument.startswith(("-d", "--data="))
        for argument in lowered
    ):
        return "post"
    return "get"


def _safe_response_id(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("pullRequestId", "workItemId", "policyId", "runId", "id"):
            value = response.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value).casefold()
        for value in response.values():
            nested = _safe_response_id(value)
            if nested:
                return nested
    if isinstance(response, list):
        for value in response:
            nested = _safe_response_id(value)
            if nested:
                return nested
    if isinstance(response, str) and len(response) <= 200_000:
        match = re.search(
            r'"(?:pullRequestId|workItemId|policyId|runId|id)"\s*:\s*"?([0-9]+)"?',
            response,
            re.I,
        )
        if match:
            return match.group(1)
    return ""


def _validation_matches(
    command: str,
    context: RepoContext,
    policy: dict[str, Any],
) -> list[tuple[str, int]]:
    repository = policy.get("repository", {})
    if not isinstance(repository, dict):
        return []
    profiles = repository.get("validation_profiles", [])
    if not isinstance(profiles, list):
        return []
    actual_segments = split_command_segments(command)
    if len(actual_segments) != 1:
        return []
    actual = _normalize_command(actual_segments[0])
    matches: list[tuple[str, int]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id", ""))
        working_directory = profile.get("working_directory")
        commands = profile.get("commands", [])
        if (
            not profile_id
            or not isinstance(working_directory, str)
            or not isinstance(commands, list)
            or context.repo_root is None
        ):
            continue
        expected_cwd = (context.repo_root / working_directory).resolve(strict=False)
        if context.cwd.resolve(strict=False) != expected_cwd:
            continue
        for index, expected in enumerate(commands):
            if not isinstance(expected, str):
                continue
            expected_parts = split_command_segments(expected)
            if len(expected_parts) == 1 and actual == _normalize_command(expected_parts[0]):
                matches.append((profile_id, index))
    return matches


def _required_validation_steps(
    context: RepoContext,
    policy: dict[str, Any],
    baseline_head: str,
) -> dict[str, list[int]]:
    repository = policy.get("repository", {})
    if not isinstance(repository, dict):
        return {}
    required = repository.get("required_validation", [])
    profiles = repository.get("validation_profiles", [])
    if not isinstance(required, list) or not isinstance(profiles, list):
        return {}
    changed_paths = _changed_paths(context, baseline_head)
    result: dict[str, list[int]] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id", ""))
        if profile_id not in required:
            continue
        globs = profile.get("path_globs", [])
        if isinstance(globs, list) and globs:
            relevant = any(
                fnmatchcase(path, str(pattern)) for path in changed_paths for pattern in globs
            )
            if not relevant:
                continue
        commands = profile.get("commands", [])
        if isinstance(commands, list):
            result[profile_id] = list(range(len(commands)))
    return result


def _changed_paths(context: RepoContext, baseline_head: str) -> set[str]:
    if context.repo_root is None:
        return set()
    paths: set[str] = set()
    if baseline_head:
        code, output, _ = run_git(
            context.repo_root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            baseline_head,
            "--",
            timeout=4,
        )
        if code == 0:
            paths.update(value.strip().replace("\\", "/") for value in output.splitlines())
    code, output, _ = run_git(
        context.repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        timeout=4,
    )
    if code == 0:
        for line in output.splitlines():
            value = line[3:] if len(line) > 3 else ""
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            if value:
                paths.add(value.strip('"').replace("\\", "/"))
    return {value for value in paths if value}


def _normalize_command(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _unsupported_claims(message: str, states: list[str]) -> list[str]:
    if not message:
        return []
    available = set(states)
    checks = (
        (
            DeliveryState.SOURCE_MODIFIED,
            r"\b(?:(?:source|code|files?|changes?)\s+"
            r"(?:is\s+|are\s+|was\s+|were\s+)?(?:modified|changed|updated)|"
            r"(?:modified|changed|updated|edited)\s+"
            r"(?:(?:the|a|an|my|our|this|that)\s+)?"
            r"(?:source|code|files?|changes?))\b",
        ),
        (
            DeliveryState.VALIDATED,
            r"\b(?:(?:validation|tests?|checks?)\s+(?:all\s+)?"
            r"(?:is\s+|are\s+|was\s+|were\s+)?"
            r"(?:passed|complete|completed|succeeded)|"
            r"validated\s+(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:source|code|changes?|implementation)|"
            r"passed\s+(?:all\s+)?(?:validation|tests?|checks?))\b",
        ),
        (
            DeliveryState.COMMITTED,
            r"\b(?:(?:changes?|work|code)\s+(?:is\s+|was\s+|were\s+)?committed|"
            r"commit\s+(?:is\s+|was\s+)?(?:created|complete|completed)|"
            r"committed\s+(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:changes?|work|code)|"
            r"(?:created|made)\s+(?:(?:the|a|my|our|this|that)\s+)?commit)\b",
        ),
        (
            DeliveryState.PUSHED,
            r"\b(?:(?:branch|commit|changes?)\s+(?:is\s+|was\s+|were\s+)?pushed|"
            r"pushed\s+(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:branch|commit|changes?))\b",
        ),
        (
            DeliveryState.PR_OPEN,
            r"\b(?:(?:pr|pull request)\s+(?:is\s+|was\s+)?"
            r"(?:open|opened|created)|"
            r"(?:opened|created|submitted)\s+"
            r"(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:pr|pull request)(?:\s+#?\d+)?)\b",
        ),
        (
            DeliveryState.PR_POLICY_READY,
            r"\b(?:(?:pr|pull request)\s+(?:is\s+|was\s+)?"
            r"(?:policy[- ]ready|policies\s+(?:passed|succeeded))|"
            r"(?:passed|satisfied)\s+(?:all\s+)?"
            r"(?:(?:pr|pull request)\s+)?policies)\b",
        ),
        (
            DeliveryState.MERGED,
            r"\b(?:(?:pr|pull request)\s+(?:is\s+|was\s+)?merged|"
            r"merged\s+(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:pr|pull request)(?:\s+#?\d+)?)\b",
        ),
        (
            DeliveryState.ARTIFACT_PUBLISHED,
            r"\b(?:artifact\s+(?:is\s+|was\s+)?published|"
            r"(?:published|uploaded)\s+"
            r"(?:(?:the|a|an|my|our|this|that)\s+)?artifact)\b",
        ),
        (
            DeliveryState.CONSUMER_ADOPTED,
            r"\bconsumer\s+(?:has\s+|was\s+)?adopted\b",
        ),
        (
            DeliveryState.RELEASED,
            r"\b(?:release\s+(?:is\s+|was\s+)?(?:published|available|complete|completed)|"
            r"version\s+\S+\s+(?:is\s+|was\s+)?released|"
            r"(?:released|cut)\s+(?:version\s+\S+|v\d[\w.+-]*|"
            r"(?:(?:the|a|my|our|this|that)\s+)?release)|"
            r"published\s+(?:(?:the|a|my|our|this|that)\s+)?release)\b",
        ),
        (
            DeliveryState.DEPLOYED,
            r"\b(?:(?:successfully\s+)?deployed(?:\s+(?:to|in)\s+\S+)?|"
            r"(?:shipped|promoted)\s+"
            r"(?:(?:(?:the|a|my|our|this|that)\s+)?"
            r"(?:release|build|version\s+\S+)\s+)?"
            r"(?:to|into)\s+(?:production|prod))\b",
        ),
        (
            DeliveryState.RUNTIME_HEALTHY,
            r"\b(?:(?:production\s+)?runtime\s+(?:is\s+|was\s+)?healthy|"
            r"verified\s+(?:the\s+)?(?:production\s+)?runtime\s+health)\b",
        ),
        (
            DeliveryState.USER_PATH_VERIFIED,
            r"\b(?:user(?:-facing)?\s+path\s+(?:is\s+|was\s+)?verified|"
            r"verified\s+(?:the\s+)?user(?:-facing)?\s+path)\b",
        ),
    )
    missing: list[str] = []
    lowered = message.lower()
    for state, pattern in checks:
        match = re.search(pattern, lowered, re.I)
        if match and not _negated_nearby(lowered, match.start()) and state.value not in available:
            missing.append(f"claim for {state.value} exceeds recorded evidence")
    return missing


def _negated_nearby(value: str, start: int) -> bool:
    prefix = value[max(0, start - 48) : start]
    return bool(
        re.search(
            r"\b(?:not|never|unverified|cannot|can't|didn't|wasn't|isn't|aren't|"
            r"haven't|hasn't|hadn't|won't|pending|blocked)\b",
            prefix,
        )
    )


def _looks_mutating(event: EventEnvelope) -> bool:
    if event.tool_name.lower() in {"apply_patch", "edit", "write", "multiedit"}:
        return True
    return any(
        classify_segment(value) == "mutation" for value in split_command_segments(event.command())
    )


def _is_enforcing(context: RepoContext, data_dir: Path) -> bool:
    if context.rollout_mode in {"enforce", "canary"}:
        return True
    install = load_json(data_dir / "install.json", {})
    return (
        isinstance(install, dict)
        and str(install.get("default_mode", "shadow")).lower() == "enforce"
    )


def _additional_context(event_name: str, text: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": _ascii(text),
        }
    }


def _deny_pre_tool(reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": HookEvent.PRE_TOOL_USE.value,
            "permissionDecision": "deny",
            "permissionDecisionReason": _ascii(f"{reason_code}: {reason}"),
        }
    }


def _ascii(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii")
