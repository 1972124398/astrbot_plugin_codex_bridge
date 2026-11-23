# AstrBot Codex 任务桥接

[![版本](https://img.shields.io/github/v/release/1972124398/astrbot_plugin_codex_bridge)](https://github.com/1972124398/astrbot_plugin_codex_bridge/releases/latest)
[![许可证](https://img.shields.io/badge/license-0BSD-blue)](LICENSE)

在 AstrBot 私聊中把任务交给本机 Codex，任务完成后将结果送回原会话。支持 WebChat 和已配对的微信私聊。

[快速开始](#快速开始) · [配置参考](docs/CONFIGURATION.md) · [MCP 与 API](docs/INTEGRATION.md) · [常见问题](#常见问题) · [更新记录](CHANGELOG.md)

## 可以做什么

- 用 `/codex` 提交任务，在后台排队执行。
- 查看任务状态、执行结果和回传状态。
- 延时执行任务，或通过工具、API 继续已有 Codex 会话。
- 配对微信私聊，在微信里提交任务和接收结果。
- 可选接入 MCP，让 Codex 查询桥接状态并回传结果。

## 适用环境

| 项目 | 要求或已验证范围 |
| --- | --- |
| AstrBot | 已验证 4.29.4 |
| Python | 使用 AstrBot 运行环境，已验证 3.12 |
| Codex | 在 AstrBot 所在主机安装并登录，能够运行 `codex app-server` |
| 消息平台 | WebChat；微信需要额外安装 WeChat Native 适配器 |
| 配置方式 | 运行数据目录中的 `config.json` 与两个令牌文件 |

本插件使用独立配置文件。配置位置和字段见下文及配置参考。

## 快速开始

1. 在 AstrBot 插件管理中通过下面的仓库地址安装，也可以下载发布页的插件 ZIP 后从文件安装。
2. 在 AstrBot 的 `data/plugin_data/astrbot_plugin_codex_bridge/` 下创建配置文件和两个令牌文件。
3. 从仓库复制 `config.example.json` 为 `config.json`，至少填写 `codex`、`cwd` 和 `webchat_owner`。
4. 分别生成两个不同的随机令牌，将它们保存为 `operator.token` 和 `callback.token`。每个文件只放一个令牌。
5. 配置完成后单独重载插件，在指定操作者的 WebChat 私聊里发送第一条任务。

首次安装时，如果日志提示缺少 `config.json` 或令牌文件，先完成第 2～4 步，再从插件管理重载该插件。

```text
https://github.com/1972124398/astrbot_plugin_codex_bridge
```

生成令牌时运行下面的命令两次，将两次结果分别保存。

```shell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

运行目录的结构如下。

```text
data/plugin_data/astrbot_plugin_codex_bridge/
├── config.json
├── operator.token
└── callback.token
```

Windows 的 `codex` 字段填写可直接执行的 `codex.exe` 绝对路径。`cwd` 填写已存在的工作目录。后台任务按配置的沙箱执行，并采用非交互审批策略。将操作者授权给可以使用本机 Codex 的可信账户。

[下载安装包](https://github.com/1972124398/astrbot_plugin_codex_bridge/releases/latest/download/astrbot_plugin_codex_bridge.zip) · [完整配置示例](config.example.json) · [Codex CLI 官方文档](https://developers.openai.com/codex/cli/)

## 第一次使用

在配置的 WebChat 操作者私聊中发送以下命令。

```text
/codex 请检查当前工作目录的项目结构，并简要说明入口文件
/codex_status
```

提交后会收到任务编号。任务完成后，结果自动送回发起任务的会话。

| 命令 | 用途 |
| --- | --- |
| `/codex <任务说明>` | 提交后台任务 |
| `/codex_status` | 查看最近任务 |
| `/codex_status <任务编号>` | 查看指定任务的状态、结果和回传状态 |
| `/codex_pair` | 在授权 WebChat 私聊生成 15 分钟有效的微信配对码 |

### 在微信里使用

先在 `config.json` 中填写实际的 `wechat_platform_id`，然后重载插件。在 WebChat 中发送 `/codex_pair`，再在目标微信私聊发送返回的完整配对命令。配对成功后，可以在该私聊使用 `/codex`。

配对会自动记录微信操作者和结果回传会话。WebChat 可以独立使用。

[WeChat Native 适配器](https://github.com/neozhu/astrbot-plugin-wechat-native)

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 插件初始化失败 | 检查运行数据目录中的配置与两个令牌文件，确认 JSON 格式有效 |
| `/codex` 没有响应 | 确认是私聊，且 WebChat 发送者 ID 与 `webchat_owner` 一致，或微信已完成配对 |
| 无法启动 Codex | 用运行 AstrBot 的同一账户验证 Codex 登录状态、可执行文件路径和工作目录 |
| 任务执行成功但没有回传 | 用 `/codex_status <任务编号>` 查看回传状态，并检查原会话对应的平台连接 |
| 地址被占用 | 桥接 API 固定监听 `127.0.0.1:6196`，检查是否有重复加载的实例 |
| 想用 MCP、API 或延时执行 | 查看 [MCP 与 API 接入说明](docs/INTEGRATION.md) |

## 问题反馈与相关插件

反馈时请提供插件版本、AstrBot 版本、系统、配置方式、复现步骤和相关日志。配置中的身份信息以及日志中的令牌请先隐藏。

[提交问题](https://github.com/1972124398/astrbot_plugin_codex_bridge/issues/new/choose) · [查看更新记录](CHANGELOG.md)

同一作者维护的插件还有 [微信原生表情转图片](https://github.com/1972124398/astrbot_plugin_wechat_sticker_receiver) 和 [短期共享情绪](https://github.com/1972124398/astrbot_plugin_short_term_mood)。

## 许可证

采用 [0BSD](LICENSE)，允许使用、修改、分发和商用，无需保留署名。
