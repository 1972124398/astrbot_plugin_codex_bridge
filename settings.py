import json
import copy
from pathlib import Path
import secrets
from urllib.parse import urlsplit

DEFAULTS = {
    "backend": "desktop",
    "codex": "codex",
    "cwd": "",
    "sandbox": "danger-full-access",
    "timeout_seconds": 1800,
    "webchat_owner": "",
    "wechat_platform_id": "",
    "wechat_operators": [],
    "notification_session": "",
    "notification_mcp_server": "",
    "desktop_pipe": "",
    "controller_thread_id": "",
    "codex_home": "",
    "desktop_url": "",
    "desktop_token": "",
    "desktop_timeout_seconds": 45,
    "monitor_enabled": True,
    "monitor_interval_seconds": 15,
    "progress_interval_seconds": 60,
    "bridge_port": 6196,
    "interop_enabled": True,
    "interop_gap_minutes": 10,
}


def load_settings(data_dir, panel=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = copy.deepcopy(DEFAULTS)
    if panel is not None:
        settings.update(dict(panel))
    legacy = data_dir / "config.json"
    if legacy.exists():
        old = json.loads(legacy.read_text(encoding="utf-8-sig"))
        # An explicit legacy backend is honored; otherwise upgrade to desktop.
        settings.update(old)
    runtime = data_dir / "routing.json"
    if runtime.exists():
        routing = json.loads(runtime.read_text(encoding="utf-8"))
        settings["wechat_operators"] = list(
            dict.fromkeys(
                [*settings["wechat_operators"], *routing.get("wechat_operators", [])]
            )
        )
        settings["notification_session"] = routing.get(
            "notification_session", settings["notification_session"]
        )
        settings["chat_sessions"] = routing.get("chat_sessions", {})
    for name, default in DEFAULTS.items():
        value = settings[name]
        if type(value) is not type(default):
            raise ValueError(f"配置 {name} 类型错误。")
    if settings["backend"] not in ("desktop", "app_server"):
        raise ValueError("backend 必须为 desktop 或 app_server。")
    if settings["sandbox"] not in (
        "read-only",
        "workspace-write",
        "danger-full-access",
    ):
        raise ValueError("无效的 sandbox。")
    for name in (
        "timeout_seconds",
        "desktop_timeout_seconds",
        "monitor_interval_seconds",
        "progress_interval_seconds",
    ):
        if settings[name] <= 0:
            settings[name] = DEFAULTS[name]
    if any(not isinstance(x, str) or not x for x in settings["wechat_operators"]):
        raise ValueError("wechat_operators 必须是用户 ID 字符串列表。")
    if url := settings["desktop_url"]:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("desktop_url 必须是无凭据、查询参数的 HTTP(S) 地址。")
        if not settings["desktop_token"]:
            raise ValueError("配置 desktop_url 时必须填写 desktop_token。")
    if settings["backend"] == "app_server" and not Path(settings["cwd"]).is_dir():
        raise ValueError("app_server 模式需要已存在的 cwd 工作目录。")
    tokens = []
    for name in ("operator.token", "callback.token"):
        path = data_dir / name
        if not path.exists():
            with path.open("x", encoding="ascii") as f:
                f.write(secrets.token_urlsafe(32) + "\n")
            path.chmod(0o600)
        token = path.read_text(encoding="ascii").strip()
        if not token or not token.isascii():
            raise ValueError(f"{name} 需要非空 ASCII 令牌。")
        tokens.append(token)
    if tokens[0] == tokens[1]:
        raise ValueError("operator.token 与 callback.token 必须不同。")
    return settings, tokens


def save_routing(data_dir, settings):
    path = data_dir / "routing.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                key: settings.get(key, {}) if key == "chat_sessions" else settings[key]
                for key in ("wechat_operators", "notification_session", "chat_sessions")
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
