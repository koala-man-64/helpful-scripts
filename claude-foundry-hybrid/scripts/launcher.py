#!/usr/bin/env python3
"""Secret-minimizing process launcher for the Claude Code + Foundry kit."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised for an invalid nonsecret profile or unsafe launch request."""


BASE_ENV_ALLOWLIST = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "CLAUDE_CONFIG_DIR",
    "COMPUTERNAME",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PSMODULEPATH",
    "REQUESTS_CA_BUNDLE",
    "SESSIONNAME",
    "SHELL",
    "SSH_AUTH_SOCK",
    "SSL_CERT_FILE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
}

SECRET_INPUT_NAMES = {
    "CFH_NATIVE_API_KEY",
    "CFH_MCP_API_KEY",
    "CFH_GATEWAY_API_KEY",
}

SECRET_CHILD_NAMES = {
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "CFH_MCP_API_KEY",
    "FOUNDRY_NONCLAUDE_API_KEY",
    "LITELLM_MASTER_KEY",
}

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a JSON object")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise ConfigError(f"{context} contains unsupported fields: {', '.join(extras)}")


def _required_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError(f"{context} must be a non-empty string")
    return value.strip()


def _bounded_int(value: Any, context: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{context} must be an integer from {minimum} through {maximum}")
    return value


def _foundry_url(
    value: Any,
    context: str,
    expected_path: str,
    allowed_host_suffixes: tuple[str, ...],
) -> str:
    url = _required_text(value, context)
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError(f"{context} must be a credential-free HTTPS URL")
    if not hostname.endswith(allowed_host_suffixes):
        raise ConfigError(f"{context} must target a Microsoft Foundry resource host")
    if parsed.path.rstrip("/") != expected_path:
        raise ConfigError(f"{context} must end in {expected_path}/")
    return url.rstrip("/") + "/"


def validate_profile(raw: Any) -> dict[str, Any]:
    profile = _require_object(raw, "profile")
    _only_keys(profile, {"version", "foundry", "gatewayLab"}, "profile")
    if profile.get("version") != 1:
        raise ConfigError("profile.version must be 1")

    foundry = _require_object(profile.get("foundry"), "profile.foundry")
    _only_keys(foundry, {"claude", "openai"}, "profile.foundry")

    claude = _require_object(foundry.get("claude"), "profile.foundry.claude")
    _only_keys(claude, {"baseUrl", "resource", "deployments"}, "profile.foundry.claude")
    has_url = claude.get("baseUrl") is not None
    has_resource = claude.get("resource") is not None
    if has_url == has_resource:
        raise ConfigError(
            "profile.foundry.claude must set exactly one of baseUrl or resource"
        )
    if has_url:
        claude["baseUrl"] = _foundry_url(
            claude["baseUrl"],
            "profile.foundry.claude.baseUrl",
            "/anthropic",
            (".services.ai.azure.com",),
        ).rstrip("/")
    else:
        resource = _required_text(claude["resource"], "profile.foundry.claude.resource")
        if not _RESOURCE_RE.fullmatch(resource):
            raise ConfigError("profile.foundry.claude.resource is not a valid resource name")
        claude["resource"] = resource

    deployments = _require_object(
        claude.get("deployments"), "profile.foundry.claude.deployments"
    )
    _only_keys(deployments, {"opus", "sonnet", "haiku"}, "Claude deployments")
    for tier in ("opus", "sonnet", "haiku"):
        deployments[tier] = _required_text(deployments.get(tier), f"Claude {tier} deployment")

    openai = _require_object(foundry.get("openai"), "profile.foundry.openai")
    _only_keys(
        openai,
        {"baseUrl", "defaultDeployment", "allowedDeployments"},
        "profile.foundry.openai",
    )
    openai["baseUrl"] = _foundry_url(
        openai.get("baseUrl"),
        "profile.foundry.openai.baseUrl",
        "/openai/v1",
        (".services.ai.azure.com", ".openai.azure.com"),
    )
    openai["defaultDeployment"] = _required_text(
        openai.get("defaultDeployment"), "profile.foundry.openai.defaultDeployment"
    )
    allowed = openai.get("allowedDeployments")
    if not isinstance(allowed, list) or not allowed:
        raise ConfigError("profile.foundry.openai.allowedDeployments must be a non-empty array")
    normalized_allowed = [
        _required_text(item, "allowed deployment") for item in allowed
    ]
    if len({item.casefold() for item in normalized_allowed}) != len(normalized_allowed):
        raise ConfigError("allowedDeployments contains duplicates")
    if openai["defaultDeployment"].casefold() not in {
        item.casefold() for item in normalized_allowed
    }:
        raise ConfigError("defaultDeployment must appear in allowedDeployments")
    openai["allowedDeployments"] = normalized_allowed

    gateway = _require_object(profile.get("gatewayLab"), "profile.gatewayLab")
    _only_keys(
        gateway,
        {"modelAlias", "deployment", "timeoutSeconds", "requestsPerMinute", "pythonVersion"},
        "profile.gatewayLab",
    )
    gateway["modelAlias"] = _required_text(
        gateway.get("modelAlias"), "profile.gatewayLab.modelAlias"
    )
    if not _ALIAS_RE.fullmatch(gateway["modelAlias"]):
        raise ConfigError("gatewayLab.modelAlias contains unsafe characters")
    gateway["deployment"] = _required_text(
        gateway.get("deployment"), "profile.gatewayLab.deployment"
    )
    if gateway["deployment"].casefold() not in {
        item.casefold() for item in normalized_allowed
    }:
        raise ConfigError("gatewayLab.deployment must appear in allowedDeployments")
    gateway["timeoutSeconds"] = _bounded_int(
        gateway.get("timeoutSeconds"), "gatewayLab.timeoutSeconds", 1, 600
    )
    gateway["requestsPerMinute"] = _bounded_int(
        gateway.get("requestsPerMinute"), "gatewayLab.requestsPerMinute", 1, 120
    )
    python_version = _required_text(
        gateway.get("pythonVersion"), "gatewayLab.pythonVersion"
    )
    if not re.fullmatch(r"3\.(11|12|13)", python_version):
        raise ConfigError("gatewayLab.pythonVersion must be 3.11 through 3.13")
    gateway["pythonVersion"] = python_version
    return profile


def load_profile(path: str | os.PathLike[str]) -> dict[str, Any]:
    profile_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Profile not found: {profile_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Profile is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    return validate_profile(raw)


def safe_base_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Copy only the OS/tooling variables a clean child process needs."""
    return {
        key: value
        for key, value in source.items()
        if key.upper() in BASE_ENV_ALLOWLIST and "\x00" not in value
    }


def _secret(value: str | None, name: str) -> str:
    if not value or "\x00" in value:
        raise ConfigError(f"{name} is required in the launcher environment")
    return value


def build_native_environment(
    profile: Mapping[str, Any],
    source: Mapping[str, str],
    native_key: str,
    mcp_key: str | None = None,
) -> dict[str, str]:
    native_key = _secret(native_key, "CFH_NATIVE_API_KEY")
    env = safe_base_environment(source)
    claude = profile["foundry"]["claude"]
    deployments = claude["deployments"]
    env.update(
        {
            "CLAUDE_CODE_USE_FOUNDRY": "1",
            "ANTHROPIC_FOUNDRY_API_KEY": native_key,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": deployments["opus"],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": deployments["sonnet"],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": deployments["haiku"],
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    if claude.get("baseUrl"):
        env["ANTHROPIC_FOUNDRY_BASE_URL"] = claude["baseUrl"]
    else:
        env["ANTHROPIC_FOUNDRY_RESOURCE"] = claude["resource"]

    if mcp_key is not None:
        env.update(
            {
                "CFH_MCP_API_KEY": _secret(mcp_key, "CFH_MCP_API_KEY"),
                "CFH_FOUNDRY_OPENAI_BASE_URL": profile["foundry"]["openai"]["baseUrl"],
                "CFH_DEFAULT_DEPLOYMENT": profile["foundry"]["openai"][
                    "defaultDeployment"
                ],
                "CFH_ALLOWED_DEPLOYMENTS_JSON": json.dumps(
                    profile["foundry"]["openai"]["allowedDeployments"],
                    separators=(",", ":"),
                ),
                "CFH_FOUNDRY_TIMEOUT_SECONDS": str(
                    profile["gatewayLab"]["timeoutSeconds"]
                ),
            }
        )
    return env


def build_gateway_claude_environment(
    profile: Mapping[str, Any], source: Mapping[str, str], port: int, local_token: str
) -> dict[str, str]:
    token = _secret(local_token, "local gateway token")
    alias = profile["gatewayLab"]["modelAlias"]
    env = safe_base_environment(source)
    env.update(
        {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": alias,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": alias,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": alias,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": alias,
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
        }
    )
    return env


def build_gateway_server_environment(
    profile: Mapping[str, Any], source: Mapping[str, str], upstream_key: str, local_token: str
) -> dict[str, str]:
    env = safe_base_environment(source)
    env.update(
        {
            "FOUNDRY_OPENAI_BASE_URL": profile["foundry"]["openai"]["baseUrl"],
            "FOUNDRY_NONCLAUDE_API_KEY": _secret(
                upstream_key, "CFH_GATEWAY_API_KEY"
            ),
            "LITELLM_MASTER_KEY": _secret(local_token, "local gateway token"),
            "LITELLM_MODE": "PRODUCTION",
            "LITELLM_LOG": "ERROR",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "LITELLM_DONT_SHOW_FEEDBACK_BOX": "True",
            "LITELLM_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "OTEL_SDK_DISABLED": "true",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "DISABLE_ADMIN_UI": "True",
            "NO_DOCS": "True",
            "NO_REDOC": "True",
            "NO_OPENAPI": "True",
        }
    )
    return env


def render_gateway_config(profile: Mapping[str, Any]) -> str:
    gateway = profile["gatewayLab"]
    # JSON string literals are valid YAML scalars and safely quote user values.
    alias = json.dumps(gateway["modelAlias"])
    model = json.dumps(f"openai/{gateway['deployment']}")
    return textwrap.dedent(
        f"""
        model_list:
          - model_name: {alias}
            litellm_params:
              model: {model}
              api_base: os.environ/FOUNDRY_OPENAI_BASE_URL
              api_key: os.environ/FOUNDRY_NONCLAUDE_API_KEY
              timeout: {gateway['timeoutSeconds']}
              max_retries: 0
              rpm: {gateway['requestsPerMinute']}

        litellm_settings:
          drop_params: false
          num_retries: 0
          request_timeout: {gateway['timeoutSeconds']}

        general_settings:
          master_key: os.environ/LITELLM_MASTER_KEY
          store_model_in_db: false
        """
    ).lstrip()


def describe_environment(env: Mapping[str, str]) -> dict[str, Any]:
    names = sorted(env)
    return {
        "variables": names,
        "secretVariables": [name for name in names if name in SECRET_CHILD_NAMES],
        "valuesIncluded": False,
    }


def _resolve_executable(value: str, context: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        if not candidate.is_file():
            raise ConfigError(f"{context} not found: {candidate}")
        return str(candidate.resolve())
    resolved = shutil.which(value)
    if not resolved:
        raise ConfigError(f"{context} not found on PATH: {value}")
    return resolved


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_listener(process: subprocess.Popen[Any], port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"LiteLLM exited during startup with code {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("LiteLLM did not open its loopback listener within 30 seconds")


def _stop_exact_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _drain_stderr(stream: Any, lines: deque[str]) -> None:
    try:
        for line in stream:
            lines.append(line)
    finally:
        stream.close()


def _redacted_log_tail(lines: Sequence[str], secret_values: Sequence[str]) -> str:
    content = "".join(lines)
    for value in secret_values:
        if value:
            content = content.replace(value, "<redacted>")
    content = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", content)
    lines = content.splitlines()[-20:]
    tail = " | ".join(line.strip() for line in lines if line.strip())
    return tail[-4000:] if tail else "empty"


def _gateway_command(executable: str, config_path: Path, port: int) -> list[str]:
    return [
        executable,
        "--config",
        str(config_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--num_workers",
        "1",
        "--limit_concurrency",
        "1",
    ]


def smoke_gateway(
    profile: Mapping[str, Any], working_directory: Path, gateway_executable: str
) -> None:
    """Boot the local gateway and close it without making an upstream request."""
    executable = _resolve_executable(gateway_executable, "pinned LiteLLM executable")
    port = _free_loopback_port()
    env = build_gateway_server_environment(
        profile, os.environ, "offline-smoke-upstream-key", "offline-smoke-local-token"
    )
    with tempfile.TemporaryDirectory(prefix="claude-foundry-hybrid-smoke-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "litellm.yaml"
        config_path.write_text(render_gateway_config(profile), encoding="utf-8")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        gateway = subprocess.Popen(
            _gateway_command(executable, config_path, port),
            cwd=working_directory,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        assert gateway.stderr is not None
        stderr_lines: deque[str] = deque(maxlen=20)
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(gateway.stderr, stderr_lines), daemon=True
        )
        stderr_thread.start()
        try:
            _wait_for_listener(gateway, port, 30)
        except RuntimeError as exc:
            _stop_exact_process(gateway)
            stderr_thread.join(timeout=1)
            detail = _redacted_log_tail(
                stderr_lines,
                ("offline-smoke-upstream-key", "offline-smoke-local-token"),
            )
            raise RuntimeError(f"{exc}; gateway stderr: {detail}") from exc
        finally:
            _stop_exact_process(gateway)
            stderr_thread.join(timeout=1)


def _run_surface(
    surface: str,
    env: Mapping[str, str],
    working_directory: Path,
    claude_executable: str,
    vscode_executable: str,
    claude_args: Sequence[str],
) -> int:
    if surface == "Cli":
        command = [_resolve_executable(claude_executable, "Claude Code executable")]
        command.extend(claude_args)
    else:
        command = [
            _resolve_executable(vscode_executable, "VS Code executable"),
            "--new-window",
            "--wait",
            str(working_directory),
        ]
    return subprocess.run(command, cwd=working_directory, env=dict(env), check=False).returncode


def launch(
    profile: Mapping[str, Any],
    mode: str,
    surface: str,
    working_directory: Path,
    claude_executable: str,
    vscode_executable: str,
    gateway_executable: str,
    enable_agent_tools: bool,
    claude_args: Sequence[str],
) -> int:
    source = dict(os.environ)
    if mode in {"Native", "Hybrid"}:
        env = build_native_environment(
            profile,
            source,
            _secret(source.get("CFH_NATIVE_API_KEY"), "CFH_NATIVE_API_KEY"),
            _secret(source.get("CFH_MCP_API_KEY"), "CFH_MCP_API_KEY")
            if mode == "Hybrid"
            else None,
        )
        return _run_surface(
            surface,
            env,
            working_directory,
            claude_executable,
            vscode_executable,
            claude_args,
        )

    if surface != "Cli":
        raise ConfigError("GatewayLab is CLI-only until its live compatibility gates pass")

    upstream_key = _secret(source.get("CFH_GATEWAY_API_KEY"), "CFH_GATEWAY_API_KEY")
    litellm = _resolve_executable(gateway_executable, "pinned LiteLLM executable")
    claude = _resolve_executable(claude_executable, "Claude Code executable")
    port = _free_loopback_port()
    local_token = "sk-cfh-" + secrets.token_urlsafe(32)
    gateway_env = build_gateway_server_environment(profile, source, upstream_key, local_token)
    claude_env = build_gateway_claude_environment(profile, source, port, local_token)

    with tempfile.TemporaryDirectory(prefix="claude-foundry-hybrid-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "litellm.yaml"
        empty_mcp_path = temp / "empty-mcp.json"
        config_path.write_text(render_gateway_config(profile), encoding="utf-8")
        empty_mcp_path.write_text('{"mcpServers":{}}\n', encoding="utf-8")

        command = _gateway_command(litellm, config_path, port)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        gateway = subprocess.Popen(
            command,
            cwd=working_directory,
            env=gateway_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        assert gateway.stderr is not None
        stderr_lines = deque(maxlen=20)
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(gateway.stderr, stderr_lines), daemon=True
        )
        stderr_thread.start()
        try:
            try:
                _wait_for_listener(gateway, port, 30)
            except RuntimeError as exc:
                _stop_exact_process(gateway)
                stderr_thread.join(timeout=1)
                detail = _redacted_log_tail(stderr_lines, (upstream_key, local_token))
                raise RuntimeError(f"{exc}; gateway stderr: {detail}") from exc
            safe_args = [
                "--bare",
                "--no-chrome",
                "--strict-mcp-config",
                "--mcp-config",
                str(empty_mcp_path),
                "--permission-mode",
                "manual",
            ]
            if not enable_agent_tools:
                safe_args.extend(["--tools", "Read,Grep,Glob"])
            safe_args.extend(claude_args)
            return subprocess.run(
                [claude, *safe_args],
                cwd=working_directory,
                env=claude_env,
                check=False,
            ).returncode
        finally:
            _stop_exact_process(gateway)
            stderr_thread.join(timeout=1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "describe-env", "render-gateway-config", "smoke-gateway", "launch"):
        child = subparsers.add_parser(name)
        child.add_argument("--profile", required=True)
        if name in {"describe-env", "launch"}:
            child.add_argument("--mode", choices=("Native", "Hybrid", "GatewayLab"), required=True)
        if name == "describe-env":
            child.add_argument("--surface", choices=("Cli", "VSCode"), default="Cli")
        if name == "smoke-gateway":
            child.add_argument("--working-directory", required=True)
            child.add_argument("--gateway-executable", required=True)
        if name == "launch":
            child.add_argument("--surface", choices=("Cli", "VSCode"), required=True)
            child.add_argument("--working-directory", required=True)
            child.add_argument("--claude-executable", default="claude")
            child.add_argument("--vscode-executable", default="code")
            child.add_argument("--gateway-executable", default="litellm")
            child.add_argument("--enable-agent-tools", action="store_true")
            child.add_argument("claude_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = load_profile(args.profile)
        if args.command == "validate":
            print(json.dumps({"valid": True, "version": profile["version"]}, indent=2))
            return 0
        if args.command == "render-gateway-config":
            print(render_gateway_config(profile), end="")
            return 0
        if args.command == "describe-env":
            if args.mode == "GatewayLab":
                env = build_gateway_claude_environment(
                    profile, os.environ, 4010, "description-only-token"
                )
            else:
                env = build_native_environment(
                    profile,
                    os.environ,
                    "description-only-native-key",
                    "description-only-mcp-key" if args.mode == "Hybrid" else None,
                )
            print(json.dumps(describe_environment(env), indent=2))
            return 0

        if args.command == "smoke-gateway":
            working_directory = Path(args.working_directory).expanduser().resolve()
            if not working_directory.is_dir():
                raise ConfigError(f"Working directory not found: {working_directory}")
            smoke_gateway(profile, working_directory, args.gateway_executable)
            print(json.dumps({"gatewayStarted": True, "upstreamRequestMade": False}))
            return 0

        working_directory = Path(args.working_directory).expanduser().resolve()
        if not working_directory.is_dir():
            raise ConfigError(f"Working directory not found: {working_directory}")
        claude_args = list(args.claude_args)
        if claude_args and claude_args[0] == "--":
            claude_args.pop(0)
        return launch(
            profile,
            args.mode,
            args.surface,
            working_directory,
            args.claude_executable,
            args.vscode_executable,
            args.gateway_executable,
            args.enable_agent_tools,
            claude_args,
        )
    except (ConfigError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
