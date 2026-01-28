import json

import pytest

import mcp_ssh_server as m


@pytest.fixture(autouse=True)
def _reset_config_cache():
    m._config_cache = None
    yield
    m._config_cache = None


def _write_config(tmp_path):
    cfg = {
        "defaults": {"max_output_chars": 123},
        "profiles": {
            "prod": {
                "host": "example.com",
                "username": "user",
                "auth": {"type": "password", "password": "fallback"},
            }
        },
    }
    path = tmp_path / "ssh_profiles.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


def test_get_profile_merges_defaults(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("MCP_SSH_CONFIG", cfg_path)

    prof = m._get_profile("prod")
    assert prof["host"] == "example.com"
    assert prof["username"] == "user"
    assert prof["max_output_chars"] == 123


def test_validate_command_rejects_newline():
    prof = {"max_command_length": 4096}
    with pytest.raises(ValueError):
        m._validate_command(prof, "ls\n-a")


def test_allowed_by_config():
    prof = {
        "allow_any_command": False,
        "allowed_commands": ["ps w"],
        "allowed_prefixes": ["git"],
        "allowed_regexes": [r"^echo "],
    }
    assert m._allowed_by_config(prof, "ps w")
    assert m._allowed_by_config(prof, "git status")
    assert m._allowed_by_config(prof, "echo hi")
    assert not m._allowed_by_config(prof, "rm -rf /")


def test_password_env_precedence(monkeypatch):
    prof = {"auth": {"type": "password", "password_env": "SSH_PASS", "password": "fallback"}}
    monkeypatch.setenv("SSH_PASS", "secret")

    auth = m._resolve_auth(prof)
    assert auth["password"] == "secret"
