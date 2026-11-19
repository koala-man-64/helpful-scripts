"""config.json keys: a key the tool does not know is an error, never a restriction that is silently not applied."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_browser_core import common, policy, session


@pytest.mark.parametrize("config, misspelled", [
    ({"allow_hosts": ["dev12345.service-now.com"]}, "allow_hosts"),
    ({"evals": False}, "evals"),
    ({"profiles": {"snow-read": {"permision": "read"}}}, "permision"),
    # A typo in a profile that is not the one in use is still a restriction someone meant to apply.
    ({"profiles": {"snow-read": {"permission": "read"}, "other": {"upload": False}}}, "upload"),
])
def test_a_misspelled_policy_key_is_a_config_error(config: dict[str, Any], misspelled: str) -> None:
    with pytest.raises(RuntimeError) as exc:
        policy.profile_config(config, "snow-read")
    assert exc.value.error_class == common.ERR_CONFIG
    assert repr(misspelled) in str(exc.value) and "Known keys" in str(exc.value)


def test_a_misspelled_restriction_used_to_leave_full_access() -> None:
    # What the check prevents: this profile was meant to be read-only with eval off.
    intended = policy.profile_config({"profiles": {"snow-read": {"permission": "read", "eval": False}}}, "snow-read")
    assert intended["permission"] == "read" and intended["eval"] is False
    with pytest.raises(RuntimeError):
        policy.profile_config({"profiles": {"snow-read": {"permision": "read", "evals": False}}}, "snow-read")


def _write_config(config: dict[str, Any]) -> Path:
    path = common._home() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_config_file_with_a_misspelled_key_does_not_load() -> None:
    _write_config({"allow_hosts": ["dev12345.service-now.com"], "eval": False})
    with pytest.raises(RuntimeError) as exc:
        common._load_config()
    assert exc.value.error_class == common.ERR_CONFIG
    assert "config.json has an unknown key: 'allow_hosts'" in str(exc.value)


def test_every_documented_key_is_accepted_at_both_levels() -> None:
    _write_config({
        "eval": False, "allowed_hosts": ["dev12345.service-now.com"], "upload_roots": ["C:/exports"], "permission": "act",
        "evidence_max_bytes": 65536, "uploads": False, "max_chars": 5000,
        "profiles": {"snow-read": {"permission": "read", "eval": False, "uploads": False, "max_chars": 0,
                                   "allowed_hosts": ["dev12345.service-now.com"], "upload_roots": [],
                                   "evidence_max_bytes": 131072}},
    })
    loaded = common._load_config()
    effective = policy.profile_config(loaded, "snow-read")
    assert effective["permission"] == "read" and effective["evidence_max_bytes"] == 131072
    assert policy.profile_config(loaded, "default")["permission"] == "act"


def test_every_validated_key_is_documented() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for key in common.CONFIG_KEYS:
        assert f"`{key}`" in readme or f'"{key}"' in readme, f"README does not document the {key} key"


def test_doctor_fails_the_config_row_for_a_misspelled_key() -> None:
    _write_config({"profiles": {"snow-read": {"permision": "read"}}})
    result = session.doctor()
    row = next(check for check in result["checks"] if check["id"] == "config_json")
    assert row["status"] == "fail" and "permision" in row["detail"]
    assert "config_json" in result["failed"]


def test_start_refuses_a_misspelled_key_before_launching_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    _write_config({"allow_hosts": ["dev12345.service-now.com"]})
    spawned: list[Any] = []
    monkeypatch.setattr(common, "_spawn_daemon", lambda *args, **kwargs: spawned.append(args) or (0, "popen"))
    with pytest.raises(RuntimeError) as exc:
        session.start_session(common._paths("default"), argparse.Namespace(browser=None, exe=None, headless=True, window_size=None, max_chars=None))
    assert exc.value.error_class == common.ERR_CONFIG and spawned == []
