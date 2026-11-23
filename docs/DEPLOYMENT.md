# 部署与排障

[首页](../README.md) · [配置](CONFIGURATION.md) · [工具与 API](INTEGRATION.md)

## 同机

使用同一系统账户打开已登录的 Codex App，至少有一条本地聊天。安装插件，保留 backend=desktop，设置 AstrBot 管理员，然后在管理员私聊发送 /codex_threads 即可开始，首次使用自动接收进度；需要更换接收私聊时再用 /codex_bind。

Python 3.11+，AstrBot 4.29.4+。默认桌面模式只需要 requirements.txt 中的 aiohttp，无需额外安装 Codex CLI、Node.js、MCP 包或 API Key。

## Docker / 异机

在 **Codex 所在的宿主机** 下载本仓库并运行代理。Windows 可以直接运行 `scripts/start_host.ps1`，自动准备虚拟环境。手动方式：

```shell
python -m venv .venv
```

Windows：

```powershell
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python host_agent.py --host 0.0.0.0 --token-file data/host.token
```

Linux：

```shell
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python host_agent.py --host 0.0.0.0 --token-file data/host.token
```

首次自动生成 `data/host.token`，后续保留。代理与 Codex 使用同一个系统账户和桌面会话。保持代理进程运行，在 AstrBot 插件面板设置：

```json
{
  "backend": "desktop",
  "desktop_url": "http://host.docker.internal:6197",
  "desktop_token": "填写宿主机 data/host.token 的内容"
}
```

Windows/macOS Docker Desktop 通常自带 host.docker.internal。Linux Docker 可在 Compose 服务中增加：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

同机代理默认只监听 127.0.0.1，无需 --host 参数。容器访问时 --host 0.0.0.0 配合防火墙仅放行 AstrBot 网络。异机使用可信专网、VPN 或 TLS 反向代理，不将完整控制入口公开到公网。

## 额外微信账号配对

管理员微信已能直接使用时无需配对。额外可信账号的配对方式：配置 wechat_platform_id，在授权 WebChat 私聊发送 /codex_pair，再于 15 分钟内在目标微信私聊发送完整配对命令。成功后获得完整权限并成为全局报告目标。

配对码仅使用一次，内存保存，重载后失效。已有 WeChat Native 适配器使用发送确认，其他平台使用 AstrBot 标准主动消息接口。所有控制入口拒绝群聊。

## 升级

备份数据目录，更新插件，不删除数据库和令牌。新默认连接桌面；旧后台模式需要在旧配置文件中保留 backend=app_server、codex 和 cwd；高级字段在面板默认隐藏。旧 config.json 继续读取并优先于面板，迁移到面板后将其改名备份。重载后检查绑定与状态。

## 诊断

在 Codex 所在账户运行：

```shell
python scripts/doctor.py
```

只读发现通道、统计能力和聊天、读取一条聊天验证连接，输出不含正文或令牌。可通过 --pipe、--codex-home、--controller-thread-id 指定发现参数。

| 现象 | 检查 |
| --- | --- |
| 找不到唯一通道 | 打开兼容 Codex，多实例时填写 desktop_pipe |
| 调用身份不存在 | 与 App 同账户，至少一条本地聊天，检查 codex_home |
| Windows Selector 事件循环 | 使用本机 host_agent 代理 |
| macOS 拒绝连接 | App 可能启用进程认证；插件不绕过认证，暂不能保证兼容 |
| 代理 401 | desktop_token 填代理令牌内容，不是路径或 callback 令牌 |
| 没有全局报告 | 管理员私聊发 /codex_bind，检查 monitor 开关和 AstrBot 事件处理情况 |
| 没有旧完成报告 | 首次历史只建立基线，新活动及变化才报告 |
| 某主机无进度 | 检查 unavailable_hosts，主机恢复后继续扫描 |
| 写请求超时 | 先查询目标聊天，避免重复提交 |
| 端口占用 | 修改 bridge_port，MCP 同步修改端口 |
| 桌面升级后报错 | 内部协议可能变化，附版本与脱敏诊断结果反馈 |

当前实测 Windows Codex 26.1117.4957.0 的原生只读接入。单元测试使用 AstrBot 框架桩验证插件，不替代真实微信适配器联调；正式发布前应在目标 AstrBot 环境验证任务提交和私聊发送。

2.3 起使用 AstrBot 正常后台事件流程。handed_off 表示交给 AstrBot，是否向用户发言由 AstrBot 处理。升级包不包含旧 speaker.py，旧文件不再导入；既有人格设置无需迁移。
