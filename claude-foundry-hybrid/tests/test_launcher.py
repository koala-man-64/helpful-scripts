from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = PACKAGE_ROOT / "scripts" / "launcher.py"
PROFILE_PATH = PACKAGE_ROOT / "config.example.json"

spec = importlib.util.spec_from_file_location("cfh_launcher", LAUNCHER_PATH)
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def profile() -> dict:
    return launcher.load_profile(PROFILE_PATH)


def test_example_profile_is_valid_and_normalized(profile) -> None:
    assert profile["version"] == 1
    assert profile["foundry"]["claude"]["baseUrl"].endswith("/anthropic")
    assert profile["foundry"]["openai"]["baseUrl"].endswith("/openai/v1/")


def test_profile_rejects_unknown_or_secret_bearing_fields(profile) -> None:
    unsafe = copy.deepcopy(profile)
    unsafe["foundry"]["openai"]["apiKey"] = "must-not-be-configured"
    with pytest.raises(launcher.ConfigError, match="unsupported fields"):
        launcher.validate_profile(unsafe)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://unit.services.ai.azure.com/openai/v1/", "HTTPS"),
        ("https://example.com/openai/v1/", "Microsoft Foundry"),
        ("https://unit.services.ai.azure.com/models/", "openai/v1"),
        ("https://user:pass@unit.services.ai.azure.com/openai/v1/", "credential-free"),
    ],
)
def test_profile_rejects_unsafe_openai_urls(profile, url, message) -> None:
    unsafe = copy.deepcopy(profile)
    unsafe["foundry"]["openai"]["baseUrl"] = url
    with pytest.raises(launcher.ConfigError, match=message):
        launcher.validate_profile(unsafe)


def test_claude_endpoint_rejects_legacy_openai_host(profile) -> None:
    unsafe = copy.deepcopy(profile)
    unsafe["foundry"]["claude"]["baseUrl"] = (
        "https://unit.openai.azure.com/anthropic"
    )
    with pytest.raises(launcher.ConfigError, match="Microsoft Foundry"):
        launcher.validate_profile(unsafe)


def test_profile_requires_default_and_gateway_deployments_in_allowlist(profile) -> None:
    missing_default = copy.deepcopy(profile)
    missing_default["foundry"]["openai"]["allowedDeployments"] = [
        missing_default["gatewayLab"]["deployment"]
    ]
    with pytest.raises(launcher.ConfigError, match="defaultDeployment"):
        launcher.validate_profile(missing_default)

    missing_gateway = copy.deepcopy(profile)
    missing_gateway["foundry"]["openai"]["allowedDeployments"] = [
        missing_gateway["foundry"]["openai"]["defaultDeployment"]
    ]
    with pytest.raises(launcher.ConfigError, match="gatewayLab.deployment"):
        launcher.validate_profile(missing_gateway)


def test_gateway_profile_rejects_python_310(profile) -> None:
    unsafe = copy.deepcopy(profile)
    unsafe["gatewayLab"]["pythonVersion"] = "3.10"
    with pytest.raises(launcher.ConfigError, match="3.11 through 3.13"):
        launcher.validate_profile(unsafe)


def test_native_environment_is_allowlisted_and_pins_models(profile) -> None:
    ambient = {
        "PATH": "C:/safe",
        "USERPROFILE": "C:/Users/test",
        "AZURE_DEVOPS_EXT_PAT": "ambient-pat-must-not-pass",
        "ANTHROPIC_API_KEY": "ambient-anthropic-key-must-not-pass",
        "CFH_GATEWAY_API_KEY": "ambient-gateway-key-must-not-pass",
        "GIT_SSH_COMMAND": "ambient-command-must-not-pass",
    }
    env = launcher.build_native_environment(profile, ambient, "native-key")

    assert env["PATH"] == "C:/safe"
    assert env["CLAUDE_CODE_USE_FOUNDRY"] == "1"
    assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "native-key"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "your-sonnet-deployment"
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert "AZURE_DEVOPS_EXT_PAT" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CFH_GATEWAY_API_KEY" not in env
    assert "CFH_MCP_API_KEY" not in env
    assert "CFH_ALLOWED_DEPLOYMENTS_JSON" not in env
    assert "GIT_SSH_COMMAND" not in env


def test_hybrid_environment_passes_only_mcp_specific_configuration(profile) -> None:
    env = launcher.build_native_environment(
        profile, {"PATH": "C:/safe"}, "native-key", "mcp-key"
    )

    assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "native-key"
    assert env["CFH_MCP_API_KEY"] == "mcp-key"
    assert env["CFH_FOUNDRY_OPENAI_BASE_URL"].endswith("/openai/v1/")
    assert env["CFH_DEFAULT_DEPLOYMENT"] == "your-default-nonclaude-deployment"
    assert json.loads(env["CFH_ALLOWED_DEPLOYMENTS_JSON"]) == [
        "your-default-nonclaude-deployment",
        "your-gateway-lab-deployment",
    ]
    assert "FOUNDRY_NONCLAUDE_API_KEY" not in env
    assert "LITELLM_MASTER_KEY" not in env


def test_gateway_separates_upstream_key_from_claude_token(profile) -> None:
    claude_env = launcher.build_gateway_claude_environment(
        profile, {"PATH": "C:/safe"}, 45678, "local-token"
    )
    server_env = launcher.build_gateway_server_environment(
        profile, {"PATH": "C:/safe"}, "upstream-key", "local-token"
    )

    assert claude_env["ANTHROPIC_AUTH_TOKEN"] == "local-token"
    assert claude_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:45678"
    assert claude_env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "0"
    assert "FOUNDRY_NONCLAUDE_API_KEY" not in claude_env
    assert "LITELLM_MASTER_KEY" not in claude_env

    assert server_env["FOUNDRY_NONCLAUDE_API_KEY"] == "upstream-key"
    assert server_env["LITELLM_MASTER_KEY"] == "local-token"
    assert server_env["NO_DOCS"] == "True"
    assert server_env["DISABLE_ADMIN_UI"] == "True"
    assert "ANTHROPIC_AUTH_TOKEN" not in server_env
    assert "ANTHROPIC_FOUNDRY_API_KEY" not in server_env
    assert "CLAUDE_CODE_USE_FOUNDRY" not in claude_env


def test_gateway_config_is_one_model_and_contains_only_environment_references(profile) -> None:
    rendered = launcher.render_gateway_config(profile)

    assert "model: \"openai/your-gateway-lab-deployment\"" in rendered
    assert "model_name: \"foundry-nonclaude\"" in rendered
    assert "api_key: os.environ/FOUNDRY_NONCLAUDE_API_KEY" in rendered
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in rendered
    assert "model_name: \"*\"" not in rendered
    assert "upstream-key" not in rendered
    assert "local-token" not in rendered


def test_environment_description_never_contains_values(profile) -> None:
    env = launcher.build_native_environment(
        profile, {"PATH": "sentinel-path"}, "sentinel-native", "sentinel-mcp"
    )
    rendered = json.dumps(launcher.describe_environment(env))

    assert "sentinel-native" not in rendered
    assert "sentinel-mcp" not in rendered
    assert "sentinel-path" not in rendered
    assert "ANTHROPIC_FOUNDRY_API_KEY" in rendered
    assert '"valuesIncluded": false' in rendered


def test_gateway_alias_rejects_yaml_injection(profile) -> None:
    unsafe = copy.deepcopy(profile)
    unsafe["gatewayLab"]["modelAlias"] = "safe\nmodel_list: ['*']"
    with pytest.raises(launcher.ConfigError, match="unsafe characters"):
        launcher.validate_profile(unsafe)


def test_executable_resolution_uses_the_windows_command_wrapper(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher.shutil, "which", lambda value: r"C:\Tools\code.CMD" if value == "code" else None
    )

    assert launcher._resolve_executable("code", "VS Code executable") == r"C:\Tools\code.CMD"


def test_vscode_surface_launches_the_resolved_wrapper(profile, tmp_path, monkeypatch) -> None:
    recorded = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(
        launcher,
        "_resolve_executable",
        lambda value, context: r"C:\Tools\code.CMD",
    )
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    result = launcher._run_surface(
        "VSCode", {"PATH": "C:/safe"}, tmp_path, "claude", "code", []
    )

    assert result == 0
    assert recorded["command"] == [
        r"C:\Tools\code.CMD",
        "--new-window",
        "--wait",
        str(tmp_path),
    ]
    assert recorded["kwargs"]["env"] == {"PATH": "C:/safe"}


def test_gateway_command_is_loopback_and_single_worker(tmp_path) -> None:
    command = launcher._gateway_command("litellm.exe", tmp_path / "config.yaml", 45678)

    assert command == [
        "litellm.exe",
        "--config",
        str(tmp_path / "config.yaml"),
        "--host",
        "127.0.0.1",
        "--port",
        "45678",
        "--num_workers",
        "1",
        "--limit_concurrency",
        "1",
    ]
