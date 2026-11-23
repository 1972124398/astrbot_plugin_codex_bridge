# MCP、工具和本地 API

[返回首页](../README.md) · [配置参考](CONFIGURATION.md) · [更新记录](../CHANGELOG.md)

## 自然语言工具

授权私聊可以由聊天模型调用以下工具。

| 工具 | 用途 |
| --- | --- |
| `w1_delegate_to_codex` | 提交任务，可指定 `cwd`、`run_at`、`thread_id` |
| `w1_codex_job_status` | 按任务编号查看状态和结果 |

`run_at` 使用带时区偏移的 ISO 8601，例如 `2026-12-01T20:00:00+08:00`。`thread_id` 为已有 Codex 会话 UUID，通过 API 或工具可继续该会话。

## 可选的 MCP 回传

在 Codex 的配置文件中注册服务。将以下三个绝对路径替换为自己的路径，Python 环境需要提供 `mcp` 包。在仓库根目录可使用 `python -m pip install -r requirements-mcp.txt` 安装 MCP 所需依赖。

```toml
[mcp_servers.astrbot_codex_bridge]
command = '/absolute/path/to/python'
args = ['/absolute/path/to/astrbot_plugin_codex_bridge/codex_mcp.py']

[mcp_servers.astrbot_codex_bridge.env]
ASTRBOT_CODEX_BRIDGE_DATA_DIR = '/absolute/path/to/astrbot/data/plugin_data/astrbot_plugin_codex_bridge'
```

Windows 可在 TOML 单引号字符串中填写完整的 Windows 路径。重新加载 Codex 的 MCP 配置后，将提供两个工具。

| 工具 | 用途 |
| --- | --- |
| `astrbot_status` | 查看桥接服务状态 |
| `astrbot_notify` | 将执行结果发送到已配对的微信私聊 |

`astrbot_notify` 需要已经完成微信配对，并由用户授权发送相应结果。使用插件任务内的 MCP 时，可在插件 `config.json` 中填写 `notification_mcp_server`，值为注册服务的名称。

[Codex MCP 官方文档](https://developers.openai.com/codex/mcp/)

## 本地 API

地址固定为 `http://127.0.0.1:6196`，请求采用 `Authorization: Bearer <令牌>`。JSON 请求使用 `Content-Type: application/json`。

| 方法 | 路径 | 用途 | 令牌 |
| --- | --- | --- | --- |
| `GET` | `/status` | 服务与队列状态 | 两种令牌均可 |
| `GET` | `/jobs` | 最近任务 | operator |
| `GET` | `/jobs/{id}` | 指定任务 | operator |
| `POST` | `/jobs` | 提交任务 | operator |
| `POST` | `/jobs/{id}/cancel` | 取消排队任务或请求中断运行任务 | operator |
| `POST` | `/pair` | 生成微信配对码 | operator |
| `POST` | `/notify` | 向已配对微信私聊发送结果 | 两种令牌均可 |

### 提交任务

请求体至少提供 `prompt`。其他字段按需填写。

```json
{
  "prompt": "检查工作目录中的项目入口，并整理运行说明",
  "cwd": "/absolute/path/to/project",
  "run_at": "2026-12-01T20:00:00+08:00",
  "notify_wechat": true
}
```

即时任务可省略 `run_at`。`notify_wechat=true` 需要已经配对微信会话。继续已有会话时增加 `thread_id`。

### 查看回传结果

任务完成状态与消息送达状态分别保存。`sent` 表示适配器确认发送，`unconfirmed` 表示回传未确认，可通过任务查询或 `/codex_status <编号>` 读取结果。

[返回使用说明](../README.md) · [配置参考](CONFIGURATION.md)
