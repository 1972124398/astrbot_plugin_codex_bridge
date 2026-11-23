# 工具与 API

[首页](../README.md) · [配置](CONFIGURATION.md) · [部署](DEPLOYMENT.md)

## 职责

AstrBot 负责理解用户、选择操作和任务内容、决定后续工作和发言。桥接插件负责连接 Codex、按调用执行、记录状态并转交事件。

任务正文不被插件重新生成。发往 Codex 的消息附带 AstrBot 代用户转交的说明。返回 AstrBot 的结果附带 `_bridge` 来源与交接提示：这是 Codex 任务资料，需结合当前对话、按原有人格表达，不应把原文直接当作自己的发言。

## AstrBot 工具

| 工具 | 用途 |
| --- | --- |
| `w1_codex_chat(action, target, text, project)` | 常用聊天操作，接收 AstrBot 选定的聊天和任务 |
| `w1_codex_app(action, arguments_json)` | 调用当前桌面工具；`tools` 列目录，带 name 返回精确 Schema |
| `w1_delegate_to_codex(prompt, cwd, run_at, thread_id)` | 提交可持久跟踪的任务，支持延时 |
| `w1_codex_job_status(job_id)` | 读取任务原始状态、结果和转交状态 |
| `w1_codex_reports(after)` | 分页读取已记录的全局事件 |

常用 action 为 list、read、use、send、new、rename、archive、unarchive、pin、unpin、open。target 可用名称、列表序号或 ID；留空使用明确选中的聊天。只有 use 改选目标；new/send 不自动选中新聊天。创建项目任务可传项目名称或已保存路径。

涉及 create_thread、send_message_to_thread、handoff_thread 的后续消息及 automation_update 的任务正文，均在传输时附加转交来源说明。代理会识别已有包装，不会重复添加。后台 app-server 模式同样附带来源。

## 后台事件

插件把事件交给 AstrBot 原生后台事件载体和正常事件队列。事件标记为 codex_bridge，发送来源为 Codex Bridge，明确不是用户新消息，不伪装成管理员发言。

```json
{
  "event_id": "codex-report:...",
  "source": "codex_bridge",
  "kind": "task_progress",
  "bridge_note": {
    "source": "codex",
    "meaning": "Codex 的任务状态、结果或回执",
    "response": "由 AstrBot 决定表达与后续动作，按已有对话和人格理解"
  },
  "data": {
    "thread_id": "...",
    "progress": {"turn_status": "inProgress"}
  }
}
```

插件不调用 llm_generate，不选择模型或人设，不改系统提示词，不自己发送事件对应的用户回复。AstrBot 处理事件时可继续使用桥接工具，也可以不调用 Codex。是否回复和如何回复由原有流程控制。

## 本地 HTTP

默认 http://127.0.0.1:6196，使用 Authorization: Bearer <令牌>。

| 方法 | 路径 | 用途 | 令牌 |
| --- | --- | --- | --- |
| GET | /status | 桌面、队列、监控和事件转交状态 | 两种 |
| GET | /app/tools | 桌面工具与 Schema | operator |
| POST | /app/call | 调用桌面工具 | operator |
| GET | /threads | 可发现主机的聊天列表 | operator |
| GET | /reports?after=0 | 按递增 ID 读取全局事件 | operator |
| GET | /jobs | 最近任务 |
| GET | /jobs/{id} | 指定任务 |
| POST | /jobs | 提交任务 |
| POST | /jobs/{id}/cancel | 取消排队任务或中断独立后台任务 |
| POST | /pair | 创建额外操作者配对码 |
| POST | /notify | 将 Codex 资料加入 AstrBot 事件待转交队列 | 两种 |

未单独注明的任务/配对接口使用 operator。`/notify` 返回 HTTP 202 和 accepted_by_bridge/report_id，不代表消息已发送。任务与报告的 handed_off 表示 AstrBot 队列已接收；原有 sent 为旧版历史记录。

提交任务可提供 prompt、thread_id、cwd、run_at 和 notify_wechat。桌面 thread_id 可用名称或 ID；cwd 可用已保存项目路径。run_at 无时区时采用运行主机的本地时间。

事件转交为至少一次语义；崩溃边界可能重复，event_id 可用于识别。报告读取方可保存 after 游标。失败只重试交接，不另起一套发言流程。

## 宿主机代理与 MCP

host_agent 默认 127.0.0.1:6197，独立令牌认证，提供 GET /tools 与 POST /call。仅负责桌面传输，AstrBot 插件负责事件记录和转交。

可选 MCP 回传需安装 requirements-mcp.txt：

```toml
[mcp_servers.astrbot_codex_bridge]
command = '/absolute/path/to/python'
args = ['/absolute/path/to/astrbot_plugin_codex_bridge/codex_mcp.py']

[mcp_servers.astrbot_codex_bridge.env]
ASTRBOT_CODEX_BRIDGE_DATA_DIR = '/absolute/path/to/astrbot/data/plugin_data/astrbot_plugin_codex_bridge'
ASTRBOT_CODEX_BRIDGE_PORT = '6196'
```

MCP 只读取 callback.token，提供 astrbot_status 和 astrbot_notify。notify 向已绑定会话交回后台资料，由 AstrBot 处理，不能把接收回执当作管理员已收到消息。
