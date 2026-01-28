from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
import uuid
from typing import Any, Dict, Optional

import asyncssh
from mcp.server.fastmcp import FastMCP

CONFIG_ENV = "MCP_SSH_CONFIG"
DEFAULT_CONFIG_PATH = "ssh_profiles.json"

mcp = FastMCP("SSH Remote Runner", json_response=True)

_config_cache: Optional[Dict[str, Any]] = None
_session_prefix_allowlist: Dict[str, set[str]] = {}
_pending_approvals: Dict[str, Dict[str, Any]] = {}
_pending_ttl_sec = 300


def _load_config() -> Dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    path = os.getenv(CONFIG_ENV, DEFAULT_CONFIG_PATH)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Config file not found: {path}. Set {CONFIG_ENV} or create {DEFAULT_CONFIG_PATH}."
        )
    with open(path, "r", encoding="utf-8") as f:
        _config_cache = json.load(f)
    return _config_cache


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _get_profiles(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("hosts") or cfg.get("profiles") or {}


def _get_profile(name: str) -> Dict[str, Any]:
    cfg = _load_config()
    profiles = _get_profiles(cfg)
    if name not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    defaults = cfg.get("defaults", {})
    return _deep_merge(defaults, profiles[name])


def _validate_command(profile: Dict[str, Any], command: str) -> str:
    cmd = command.strip()
    if not cmd:
        raise ValueError("Command is empty.")
    if any(ch in cmd for ch in "\r\n"):
        raise ValueError("Command must be a single line.")
    max_len = int(profile.get("max_command_length", 4096))
    if len(cmd) > max_len:
        raise ValueError(f"Command too long (max {max_len}).")

    for rx in profile.get("deny_regexes", []):
        if re.search(rx, cmd):
            raise ValueError("Command blocked by deny_regexes.")

    return cmd


def _prefix_match(prefix: str, command: str) -> bool:
    if not prefix:
        return False
    return command == prefix or command.startswith(prefix + " ")


def _allowed_by_config(profile: Dict[str, Any], command: str) -> bool:
    if profile.get("allow_any_command", False):
        return True

    if command in profile.get("allowed_commands", []):
        return True

    for prefix in profile.get("allowed_prefixes", []):
        if _prefix_match(prefix, command):
            return True

    for rx in profile.get("allowed_regexes", []):
        if re.search(rx, command):
            return True

    return False


def _get_session_allowlist(session_id: str) -> set[str]:
    return _session_prefix_allowlist.setdefault(session_id, set())


def _allowed_by_session(session_id: str, command: str) -> bool:
    for prefix in _get_session_allowlist(session_id):
        if _prefix_match(prefix, command):
            return True
    return False


def _extract_prefix(command: str, profile: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    tokens = shlex.split(command)
    if not tokens:
        return ""
    program = tokens[0]
    default_programs = ["git"]
    subcmd_programs = profile.get(
        "subcommand_prefix_programs",
        cfg.get("subcommand_prefix_programs", default_programs),
    )

    if program in subcmd_programs:
        subcmd = ""
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            subcmd = token
            break
        if subcmd:
            return f"{program} {subcmd}"
    return program


def _apply_working_dir(profile: Dict[str, Any], command: str) -> str:
    working_dir = profile.get("working_dir")
    if not working_dir:
        return command
    return f"cd {shlex.quote(working_dir)} && {command}"


def _resolve_timeout(cfg: Dict[str, Any], profile: Dict[str, Any], timeout_sec: Optional[int]) -> int:
    default_timeout = int(profile.get("default_timeout_sec", cfg.get("default_timeout_sec", 30)))
    max_timeout = int(profile.get("max_timeout_sec", cfg.get("max_timeout_sec", 120)))
    if timeout_sec is None:
        return max(1, min(default_timeout, max_timeout))
    return max(1, min(int(timeout_sec), max_timeout))


def _truncate(text: str, max_chars: int) -> str:
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


def _resolve_auth(profile: Dict[str, Any]) -> Dict[str, Any]:
    auth = profile.get("auth") or {}
    if not auth:
        return {}
    auth_type = auth.get("type", "key")
    out: Dict[str, Any] = {}

    if auth_type == "key":
        key_path = auth.get("private_key_path")
        env_name = auth.get("private_key_path_env")
        if env_name:
            key_path = os.getenv(env_name) or key_path
        if not key_path:
            raise ValueError("SSH key path not configured.")
        out["client_keys"] = [key_path]

        pass_env = auth.get("passphrase_env")
        if pass_env:
            out["passphrase"] = os.getenv(pass_env)

    elif auth_type == "password":
        pw = auth.get("password")
        env_name = auth.get("password_env")
        if env_name:
            pw = os.getenv(env_name) or pw
        if not pw:
            raise ValueError("SSH password not configured.")
        out["password"] = pw
    else:
        raise ValueError(f"Unsupported auth type: {auth_type}")

    return out


def _new_approval(session_id: str, profile: str, command: str, prefix: str) -> Dict[str, Any]:
    approval_id = str(uuid.uuid4())
    _pending_approvals[approval_id] = {
        "session_id": session_id,
        "profile": profile,
        "command": command,
        "prefix": prefix,
        "created_at": time.time(),
    }
    return {
        "ok": False,
        "approval_required": True,
        "approval_id": approval_id,
        "choices": ["allow_once", "allow_prefix"],
        "suggested_prefix": prefix,
        "message": "Command not in allowlist. Ask user to approve once or allow the prefix for this session.",
    }


def _get_approval(approval_id: str) -> Dict[str, Any]:
    if approval_id not in _pending_approvals:
        raise ValueError("Unknown approval_id.")
    record = _pending_approvals[approval_id]
    if time.time() - record["created_at"] > _pending_ttl_sec:
        del _pending_approvals[approval_id]
        raise ValueError("Approval expired.")
    return record


async def _run_ssh(profile: Dict[str, Any], command: str, timeout: int) -> Dict[str, Any]:
    host = profile.get("host")
    username = profile.get("username")
    port = int(profile.get("port", 22))
    if not host or not username:
        raise ValueError("Profile must define host and username.")

    cmd = _apply_working_dir(profile, command)

    connect_kwargs: Dict[str, Any] = {
        "host": host,
        "port": port,
        "username": username,
    }

    if profile.get("allow_unknown_host_keys", False):
        connect_kwargs["known_hosts"] = None
    elif "known_hosts" in profile:
        connect_kwargs["known_hosts"] = profile["known_hosts"]

    connect_kwargs.update(_resolve_auth(profile))

    async with asyncssh.connect(**connect_kwargs) as conn:
        try:
            result = await asyncio.wait_for(
                conn.run(cmd, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Command timed out after {timeout} seconds.")

    return {
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "exit_status": result.exit_status,
        "signal": result.exit_signal,
    }


@mcp.tool()
def ssh_list_profiles() -> Dict[str, Any]:
    cfg = _load_config()
    profiles = _get_profiles(cfg)
    out: Dict[str, Any] = {}
    for name, p in profiles.items():
        out[name] = {
            "description": p.get("description", ""),
            "host": p.get("host", ""),
            "username": p.get("username", ""),
        }
    return out


@mcp.tool()
async def ssh_run_command(
    profile: str,
    command: str,
    session_id: str = "default",
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    start = time.monotonic()
    cfg = _load_config()
    prof = _get_profile(profile)

    cmd = _validate_command(prof, command)
    timeout = _resolve_timeout(cfg, prof, timeout_sec)

    max_output = int(prof.get("max_output_chars", cfg.get("max_output_chars", 20000)))

    if not (_allowed_by_config(prof, cmd) or _allowed_by_session(session_id, cmd)):
        prefix = _extract_prefix(cmd, prof, cfg)
        return _new_approval(session_id, profile, cmd, prefix)

    try:
        result = await _run_ssh(prof, cmd, timeout)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": True,
            "stdout": _truncate(result["stdout"], max_output),
            "stderr": _truncate(result["stderr"], max_output),
            "exit_status": result["exit_status"],
            "signal": result["signal"],
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
        }


@mcp.tool()
async def ssh_approve_and_run(
    approval_id: str,
    decision: str,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    record = _get_approval(approval_id)
    del _pending_approvals[approval_id]

    session_id = record["session_id"]
    profile_name = record["profile"]
    command = record["command"]
    prefix = record["prefix"]

    cfg = _load_config()
    prof = _get_profile(profile_name)

    cmd = _validate_command(prof, command)
    timeout = _resolve_timeout(cfg, prof, timeout_sec)
    max_output = int(prof.get("max_output_chars", cfg.get("max_output_chars", 20000)))

    if decision == "allow_prefix":
        _get_session_allowlist(session_id).add(prefix)
    elif decision != "allow_once":
        raise ValueError("Invalid decision. Use allow_once or allow_prefix.")

    try:
        result = await _run_ssh(prof, cmd, timeout)
        return {
            "ok": True,
            "approved": decision,
            "stdout": _truncate(result["stdout"], max_output),
            "stderr": _truncate(result["stderr"], max_output),
            "exit_status": result["exit_status"],
            "signal": result["signal"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "approved": decision,
            "error": str(exc),
        }


@mcp.tool()
def ssh_clear_session_allowlist(session_id: str = "default") -> Dict[str, Any]:
    _session_prefix_allowlist.pop(session_id, None)
    return {"ok": True, "session_id": session_id}


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
