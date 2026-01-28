# mcp-ssh-server

[English](README.md) | [简体中文](README.zh-CN.md)

通过 SSH 运行远端命令的 MCP 服务器，支持 JSON 白名单与会话内审批。

## 安装（运行时）
- 创建 venv 并安装依赖：
  - pip install mcp asyncssh

## 配置
- 复制 ssh_profiles.example.json 为 ssh_profiles.json
- 如需指定配置路径，设置 MCP_SSH_CONFIG
- 设置配置文件中引用的 SSH 密钥路径环境变量

## 运行（stdio）
- python mcp_ssh_server.py

## 打包成单文件（PyInstaller）
- pip install pyinstaller
- pyinstaller -F --name mcp-ssh-server mcp_ssh_server.py
- 输出文件位于 dist/

## 通过 GitHub Actions 打包
- 进入 Actions -> Package -> Run workflow
- 下载产物：mcp-ssh-server-<OS>
- 压缩包包含：
  - mcp-ssh-server(.exe)
  - ssh_profiles.json（默认白名单）

## 使用打包产物
- 解压下载的压缩包
- 编辑 ssh_profiles.json（填写 host/username/auth）
- 运行：
  - Windows：.\mcp-ssh-server.exe
  - Linux/macOS：./mcp-ssh-server
- 如需使用其它路径的配置，设置 MCP_SSH_CONFIG

## 工具
- ssh_list_profiles：列出配置的 profile
- ssh_run_command：按 profile 执行允许的命令
- ssh_approve_and_run：审批挂起命令并执行
- ssh_clear_session_allowlist：清空会话前缀白名单

## 审批流程
1) 调用 ssh_run_command
2) 若命令不在 JSON 白名单中，会返回 approval_required，其中包含：
   - approval_id
   - choices：allow_once 或 allow_prefix
   - suggested_prefix（例如 “git pull” 而不是 “git”）
3) 询问用户后，用 approval_id 和 decision 调用 ssh_approve_and_run

会话前缀白名单存于内存中，进程退出或调用 ssh_clear_session_allowlist 时清空。

## 许可证
Apache-2.0，详见 LICENSE。
