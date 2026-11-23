# 配置参考

[返回首页](../README.md) · [MCP、工具和本地 API](INTEGRATION.md) · [更新记录](../CHANGELOG.md)

本插件在 AstrBot 数据目录中使用独立的 `data/plugin_data/astrbot_plugin_codex_bridge/config.json`。将仓库根目录的 `config.example.json` 复制到这里，再按运行环境修改。

## 配置字段

| 字段 | 用途 | 示例或说明 |
| --- | --- | --- |
| `codex` | Codex 可执行文件 | 绝对路径，Windows 使用可直接执行的 `codex.exe` |
| `cwd` | 默认工作目录 | 已存在的绝对目录路径 |
| `sandbox` | Codex 沙箱 | 示例为 `workspace-write`，按使用场景选择 Codex 支持的值 |
| `timeout_seconds` | 单项任务执行超时 | 默认示例为 `1800` 秒 |
| `webchat_owner` | 获准提交任务的 WebChat 用户 ID | 与 WebChat 消息的发送者 ID 一致 |
| `wechat_platform_id` | WeChat Native 平台实例 ID | 使用微信时填写 AstrBot 中的实际平台实例 ID |
| `wechat_operators` | 获准提交任务的微信用户 ID | 配对成功时自动追加 |
| `notification_session` | 微信通知会话 | 配对成功时自动记录 |
| `notification_mcp_server` | 可选的 MCP 服务名称 | 与 Codex 中配置的 MCP 名称一致，例如 `astrbot_codex_bridge` |

`notification_mcp_server` 为可选字段。填写后，插件启动的 Codex App Server 会为该 MCP 服务设置工具审批配置，便于任务使用通知工具。具体效果还取决于 Codex 的配置与授权。

## Windows 示例

下面所有路径和用户 ID 都需要替换为自己的实际值。

```json
{
  "codex": "C:/path/to/codex.exe",
  "cwd": "D:/your-workspace",
  "sandbox": "workspace-write",
  "timeout_seconds": 1800,
  "webchat_owner": "your-webchat-user-id",
  "wechat_platform_id": "",
  "wechat_operators": [],
  "notification_session": ""
}
```

Linux 等环境可参考仓库中的 [通用配置示例](../config.example.json)。使用容器部署时，Codex 可执行文件和工作目录须在 AstrBot 容器内可访问。

## 令牌文件

同一运行数据目录还需放置两个不同的令牌文件。

| 文件 | 用途 |
| --- | --- |
| `operator.token` | 授权本地 API 管理任务与创建配对码 |
| `callback.token` | 授权 MCP 或回传客户端读取状态、向已配对微信会话发送结果 |

每个文件只保存一行独立的 ASCII 随机令牌。可以分别运行以下命令生成。

```shell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

令牌保存在插件运行数据目录。分享配置时使用 `config.example.json` 中的占位内容。

## 配置完成后的检查

1. 单独重载插件，在日志中确认 `AstrBot Codex Bridge ready on 127.0.0.1:6196`。
2. 从指定 WebChat 操作者的私聊提交一项简短任务。
3. 使用 `/codex_status` 查看结果。如果需要微信回传，再按首页步骤配对。

后台任务使用 `approvalPolicy=never`，需要交互确认的操作可能无法完成。插件通过配置的沙箱约束任务，使用时为操作者选择合适的工作目录与权限。

[返回使用说明](../README.md) · [MCP 与 API](INTEGRATION.md)
