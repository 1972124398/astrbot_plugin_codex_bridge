# 配置参考

[首页](../README.md) · [部署](DEPLOYMENT.md) · [工具与 API](INTEGRATION.md)

同机使用通常不需要修改配置。面板只显示自动汇报和可选宿主机连接，高级字段默认隐藏，原有配置值仍然生效。运行数据目录为 `data/plugin_data/astrbot_plugin_codex_bridge/`，自动创建目录、令牌和数据库。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `backend` | `desktop` | `desktop` 控制真实 App；`app_server` 独立后台 |
| `desktop_pipe` | 空 | 自动发现唯一 App Tools 通道；多实例时指定完整管道/socket 路径 |
| `controller_thread_id` | 空 | 自动从本地元数据发现真实调用身份；也可指定一条现有本地聊天 UUID |
| `codex_home` | 空 | 默认 CODEX_HOME 或 ~/.codex，通常无需填写 |
| `desktop_url` | 空 | 代理地址，例如 http://host.docker.internal:6197 |
| `desktop_token` | 空 | 代理令牌内容；填写 URL 时必填 |
| `desktop_timeout_seconds` | 45 | 单次桌面调用等待时间 |
| `webchat_owner` | 空 | 额外可信 WebChat 用户 ID；AstrBot 管理员私聊默认获准 |
| `wechat_platform_id` | 空 | 额外微信配对使用的实际平台实例 ID |
| `wechat_operators` | [] | 与平台实例共同验证的额外微信账号 |
| `notification_session` | 空 | 首次授权使用自动绑定，可用 /codex_bind 更换 |
| `monitor_enabled` | true | 全局监控开关，与执行后端独立 |
| `monitor_interval_seconds` | 15 | 元数据扫描间隔，通常保持默认 |
| `progress_interval_seconds` | 60 | 公开进度合并间隔，状态变化会尽快汇报 |
| `bridge_port` | 6196 | 本地 API 端口，仅监听 127.0.0.1 |
| `codex` | codex | 仅后台使用，Windows 建议真实 codex.exe 绝对路径 |
| `cwd` | 空 | 后台必填，已存在的绝对工作目录；桌面忽略此默认值 |
| `sandbox` | danger-full-access | 后台主机完整权限，也支持 workspace-write/read-only |
| `timeout_seconds` | 1800 | 后台超时；桌面仅用作未匹配执行轮次的跟踪时限 |
| `notification_mcp_server` | 空 | 可选通知 MCP 名称，供后台启动时使用 |

桌面模式继承 App 的权限、模型和工具环境。`controller_thread_id` 只提供真实调用身份，不限制操作范围，也不会给身份聊天追加控制提示词；该聊天被删除时需要改配置或留空重载。独立后台使用 approvalPolicy=never。

监控分页发现所有可访问的 Codex 来源，包含归档与已连接主机。活动聊天每轮读取公开进度；闲置聊天只在元数据变化时读取。首次闲置历史只建立基线。离线主机和读取错误可在 /status 查看。未绑定私聊时报告仍可通过工具/API 查询，绑定前报告不自动补发。

当前聊天按私聊分别保存在 `routing.json`，重载后保留；列表序号对应最近一次展示的列表。

绑定和配对保存在 `routing.json`，绑定目标优先于面板和旧配置，配对账号与配置账号合并。撤销配对时停止插件，从 routing.json 及配置的 wechat_operators 中删除账号，再重载。AstrBot 管理员权限在 AstrBot 中撤销。

```text
operator.token   完整本地 API 权限
callback.token   仅 /status、/notify
jobs.sqlite      队列、桌面回执和结果发送状态
reports.sqlite   全局进度基线、报告和发送队列
routing.json     配对账号和绑定目标
```

令牌不存在时自动生成，已有令牌保留；两个令牌相同或为空会拒绝启动。已交给 AstrBot 的全局报告保留 30 天，待发送与失败报告保留供排查。备份运行数据目录可保留队列、绑定和发送状态。

旧 `config.json` 同名字段覆盖面板。迁移时先把所需字段填入面板，再将旧文件改名备份。示例见 [config.example.json](../config.example.json)。修改 API 端口后，MCP 同步设置 ASTRBOT_CODEX_BRIDGE_PORT。

时间参数填写为零或负数时会回到默认值，不再因自定义范围阻止启动。延时任务省略时区时按运行主机的本地时间处理。


后台事件进入 AstrBot 的正常流程，不单独配置模型、人设或话术。report_delivery 中 handed_off 只表示 AstrBot 已接收。旧版 speech 表可随备份保留，新版不再使用话术缓存。

## 拟人插件联动

`interop_enabled` 默认开启，自动发现可用同伴；关闭后独立使用。`interop_gap_minutes` 默认 10 分钟，参考所有同伴最近主动开口的时间，0 表示不额外等待。前台只读取状态与排队，感受评估在后台完成。详见[四插件联动](LINKING.md)。
