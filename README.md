# AstrBot Codex Bridge

在 AstrBot 私聊中把任务交给本机 Codex。任务进入 SQLite 队列，支持延时执行、继续已有 Codex 会话和查询结果。完成后，插件将结果送回发起任务的会话。配置 MCP 后，Codex 也能向已配对的微信私聊发送结果。

## 安装

1. 在运行 AstrBot 的主机上安装并登录 Codex CLI，确认 AstrBot 进程使用的账户能启动 `codex app-server`。插件需要 Python 3.11 或更新版本。AstrBot 的 Python 环境需提供 `aiohttp`，使用 MCP 时还需提供 `mcp`。
2. 把本仓库克隆或解压到 AstrBot 根目录下的 `data/plugins/astrbot_plugin_codex_bridge`。如果 AstrBot 正在运行，完成下面的配置后再重启。
3. 在 AstrBot 根目录下创建 `data/plugin_data/astrbot_plugin_codex_bridge`。将仓库里的 `config.example.json` 复制到该目录并命名为 `config.json`。填入 Codex 可执行文件的绝对路径、工作目录和 WebChat 操作者 ID。Windows 上填写可直接执行的 `codex.exe` 路径。
4. 在同一运行数据目录放置 `operator.token` 和 `callback.token`，每个文件包含一个独立的随机令牌。运行两次 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 即可生成。该目录还会保存任务历史和微信配对信息。
5. 启动 AstrBot，在插件列表确认 **AstrBot Codex Bridge** 已加载。桥接 API 使用 `127.0.0.1:6196` 和 Bearer 认证。操作者令牌可管理任务和创建配对码，回传令牌可读取状态和发送通知。

`sandbox` 指定 Codex 沙箱，示例使用 `workspace-write`。后台任务采用非交互审批策略，由运行 AstrBot 的账户启动 Codex。仅将 WebChat 操作者和微信配对授权给可信账户。

## 使用

| 私聊命令 | 用途 |
| --- | --- |
| `/codex <任务说明>` | 提交任务，完成后在当前私聊回传 |
| `/codex_status [任务编号]` | 查看最近任务或指定任务的状态和结果 |
| `/codex_pair` | 由 WebChat 操作者生成微信配对码 |

微信需要安装 `astrbot_plugin_wechat_native` 并在 `config.json` 中设置 `wechat_platform_id` 为实际的 AstrBot 平台实例 ID。WebChat 中生成配对码后，在微信私聊机器人发送 `/codex_pair <配对码>`。WebChat 可独立使用。

AstrBot 的授权私聊也可使用 `w1_delegate_to_codex` 和 `w1_codex_job_status` 两个工具。操作者可经本地 API 向 `POST /jobs` 提交 `prompt`，并可附加 `cwd`、`thread_id`、带时区的 ISO 8601 `run_at`，或在微信配对后附加 `notify_wechat: true`。

## 可选的 Codex MCP 回传

在 Codex 的 `config.toml` 注册 MCP。将这里的三个绝对路径换成本机路径，并使用安装了 `mcp` 的 Python。

```toml
[mcp_servers.astrbot_codex_bridge]
command = '/absolute/path/to/python'
args = ['/absolute/path/to/astrbot_plugin_codex_bridge/codex_mcp.py']

[mcp_servers.astrbot_codex_bridge.env]
ASTRBOT_CODEX_BRIDGE_DATA_DIR = '/absolute/path/to/astrbot/data/plugin_data/astrbot_plugin_codex_bridge'
```

重新打开 Codex 后可以调用 `astrbot_status`。微信配对后，`astrbot_notify` 可向该私聊发送结果。若希望插件启动的后台任务自动批准这个 MCP 服务的工具，可在运行目录的 `config.json` 中把 `notification_mcp_server` 设为上面配置的服务名。

## 许可证

BSD Zero Clause License（[0BSD](LICENSE)）。
