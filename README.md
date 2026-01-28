# mcp-ssh-server

[English](README.md) | [简体中文](README.zh-CN.md)

MCP server that runs remote commands over SSH using allowlists and session approvals.

## Install (runtime)
- Create venv and install deps:
  - pip install mcp asyncssh

## Configure
- Copy ssh_profiles.example.json to ssh_profiles.json
- Set MCP_SSH_CONFIG if you want a different path
- Set SSH key path env vars referenced by your profile

## Run (stdio)
- python mcp_ssh_server.py

## Package to a single file (PyInstaller)
- pip install pyinstaller
- pyinstaller -F --name mcp-ssh-server mcp_ssh_server.py
- Output binary is in dist/

## Package via GitHub Actions
- Go to Actions -> Package -> Run workflow
- Download artifact: mcp-ssh-server-<OS>
- The zip contains:
  - mcp-ssh-server(.exe)
  - ssh_profiles.json (default allowlist)

## Use the packaged zip
- Unzip the artifact
- Edit ssh_profiles.json (set host/username/auth)
- Run:
  - Windows: .\mcp-ssh-server.exe
  - Linux/macOS: ./mcp-ssh-server
- Optionally set MCP_SSH_CONFIG to point to another path

## Tools
- ssh_list_profiles: list configured profiles
- ssh_run_command: run an allowlisted command by profile
- ssh_approve_and_run: approve a pending command and run it
- ssh_clear_session_allowlist: clear session prefixes

## Approval flow
1) Call ssh_run_command
2) If the command is not in the JSON allowlist, the tool returns approval_required with:
   - approval_id
   - choices: allow_once or allow_prefix
   - suggested_prefix (e.g., "git pull" instead of "git")
3) Ask the user, then call ssh_approve_and_run with approval_id and decision

Session prefixes are stored in memory and cleared when the server process exits or when you call ssh_clear_session_allowlist.

## License
Apache-2.0. See LICENSE.
