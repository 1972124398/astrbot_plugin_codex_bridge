<div align="center">

<h1>AstrBot Codex 任务桥接</h1>
<p>在私聊中提交 Codex 任务，让执行结果回到原会话。</p>

<p>
  <a href="https://github.com/1972124398/astrbot_plugin_codex_bridge/releases/latest"><img src="https://img.shields.io/github/v/release/1972124398/astrbot_plugin_codex_bridge?style=flat-square&color=2563eb" alt="最新版本"></a>
  <a href="https://github.com/1972124398/astrbot_plugin_codex_bridge/actions/workflows/check.yml"><img src="https://github.com/1972124398/astrbot_plugin_codex_bridge/actions/workflows/check.yml/badge.svg?branch=main" alt="检查状态"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-0BSD-64748b?style=flat-square" alt="0BSD 许可证"></a>
</p>

<p>
  <a href="https://github.com/1972124398/astrbot_plugin_codex_bridge/releases/latest/download/astrbot_plugin_codex_bridge.zip"><strong>下载 ZIP</strong></a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="docs/CONFIGURATION.md">配置文档</a> ·
  <a href="https://github.com/1972124398/astrbot_plugin_codex_bridge/issues/new/choose">问题反馈</a>
</p>

</div>

## 功能

| 能力 | 使用方式 |
| --- | --- |
| 后台任务 | 通过 `/codex` 提交，任务进入持久化队列 |
| 结果回传 | 完成后送回 WebChat 或已配对的微信私聊 |
| 进度查询 | 查看任务状态、结果和回传状态 |
| 进阶接入 | 支持延时执行、会话续接、本地 API 与 MCP |

## 快速开始

已验证环境为 **AstrBot 4.29.4、Python 3.12**。先在 AstrBot 所在环境安装并登录 Codex，确认同一运行账户能够启动 `codex app-server`。

1. 在 AstrBot 插件管理中使用下方仓库地址安装，或下载 ZIP 后从文件安装。
2. 创建运行数据目录 `data/plugin_data/astrbot_plugin_codex_bridge/`。
3. 将 [配置示例](config.example.json) 复制为该目录中的 `config.json`，填写 `codex`、`cwd` 和 `webchat_owner`。
4. 准备两个独立的令牌文件 `operator.token` 和 `callback.token`。
5. 单独重载插件，在指定操作者的 WebChat 私聊中提交任务。

```text
https://github.com/1972124398/astrbot_plugin_codex_bridge
```

<details>
<summary><strong>配置文件与令牌怎么准备？</strong></summary>

运行目录的结构如下。

```text
data/plugin_data/astrbot_plugin_codex_bridge/
├── config.json
├── operator.token
└── callback.token
```

将下面的命令运行两次，把两次结果分别保存到两个令牌文件中，每个文件只放一行令牌。

```shell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows 的 `codex` 字段填写可直接执行的 `codex.exe` 绝对路径。`cwd` 填写已存在的工作目录。首次安装若提示缺少配置或令牌文件，完成配置后再单独重载。

后台任务按配置的沙箱执行，采用非交互审批策略。将操作者授权给可以使用本机 Codex 的可信账户。

[完整配置参考](docs/CONFIGURATION.md) · [Codex CLI 官方文档](https://developers.openai.com/codex/cli/)

</details>

## 使用

```text
/codex 请检查当前项目结构，并简要说明入口文件
/codex_status
```

| 命令 | 用途 |
| --- | --- |
| `/codex <任务说明>` | 提交任务，完成后自动回传 |
| `/codex_status [任务编号]` | 查询最近任务或指定任务 |
| `/codex_pair` | 在授权 WebChat 私聊生成微信配对码 |

<details>
<summary><strong>如何在微信私聊中使用？</strong></summary>

先接入 WeChat Native 适配器，在 `config.json` 中填写实际的 `wechat_platform_id`，然后单独重载插件。

在授权 WebChat 私聊中发送 `/codex_pair`，再于 15 分钟内，在目标微信私聊中发送返回的完整配对命令。成功后，该私聊即可使用 `/codex` 并接收结果。

配对会自动记录微信操作者和结果回传会话。WebChat 可以独立使用。

[WeChat Native 适配器](https://github.com/neozhu/astrbot-plugin-wechat-native)

</details>

## 常见问题

<details>
<summary>插件初始化失败，或命令没有响应</summary>

检查运行数据目录中的配置和两个令牌文件。确认当前为私聊，WebChat 发送者 ID 与 `webchat_owner` 一致，或微信已完成配对。

</details>

<details>
<summary>无法启动 Codex，或任务执行超时</summary>

用运行 AstrBot 的同一账户验证 Codex 登录状态、可执行文件路径和工作目录，再查看 `timeout_seconds` 配置。需要交互确认的操作可能无法在后台完成。

</details>

<details>
<summary>执行成功，但没有收到回传</summary>

使用 `/codex_status <任务编号>` 查看回传状态，检查原会话的平台连接。桥接 API 固定监听 `127.0.0.1:6196`，重复加载实例时也需检查端口占用。

</details>

## 文档与反馈

| 文档 | 内容 |
| --- | --- |
| [配置参考](docs/CONFIGURATION.md) | 配置字段、Windows 示例、令牌和运行目录 |
| [MCP 与 API](docs/INTEGRATION.md) | 自然语言工具、延时任务、会话续接和通知 |
| [更新记录](CHANGELOG.md) | 版本变化与发布内容 |

反馈时请附上插件与 AstrBot 版本、系统、复现步骤和相关日志，并隐藏令牌及个人信息。

[提交问题](https://github.com/1972124398/astrbot_plugin_codex_bridge/issues/new/choose) · [查看发布版本](https://github.com/1972124398/astrbot_plugin_codex_bridge/releases)

## 相关插件

| 插件 | 用途 |
| --- | --- |
| [微信原生表情转图片](https://github.com/1972124398/astrbot_plugin_wechat_sticker_receiver) | 将微信表情交给视觉模型理解 |
| [短期共享情绪](https://github.com/1972124398/astrbot_plugin_short_term_mood) | 为 AI 增加跨对话共享的短期心境 |

采用 [0BSD 许可证](LICENSE)，允许使用、修改、分发和商用，无需保留署名。
