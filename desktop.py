"""Codex desktop App Tools adapter. No Electron patching or database writes.

The native protocol is private and version dependent. Discover the catalog at
runtime, fail explicitly on incompatibility, and never retry a mutation.
"""

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import struct
import uuid

import aiohttp

try:
    from .protocol import forward_arguments
except ImportError:
    from protocol import forward_arguments

MAX_FRAME = 8 * 1024 * 1024


class DesktopError(RuntimeError):
    pass


def discover_pipe():
    if configured := os.environ.get("CODEX_APP_TOOLS_PIPE_PATH"):
        return configured
    if os.name == "nt":
        paths = [
            "\\\\.\\pipe\\" + name
            for name in os.listdir("\\\\.\\pipe\\")
            if name.startswith("codex-app-tools-")
        ]
    else:
        roots = [
            Path("/tmp/codex-app-tools"),
            Path(f"/tmp/codex-app-tools-{os.getuid()}"),
        ]
        paths = [
            str(p)
            for root in roots
            if root.exists()
            for p in root.rglob("*")
            if p.is_socket()
        ]
    if len(paths) != 1:
        raise DesktopError(
            "未发现唯一的 Codex App Tools 通道。请打开 Codex，或配置 desktop_pipe。"
        )
    return paths[0]


def discover_thread(codex_home):
    """Resolve a real caller identity using metadata only, with SQLite read-only."""
    root = Path(codex_home or os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    for path in sorted(root.glob("state_*.sqlite"), reverse=True):
        try:
            with closing(
                sqlite3.connect(
                    path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
                )
            ) as db:
                row = db.execute(
                    "SELECT id FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    return row[0]
        except sqlite3.Error:
            continue
    raise DesktopError("请先在 Codex 中创建一条本地聊天，或填写 controller_thread_id。")


def unwrap_tool(result):
    items = result.get("contentItems", [])
    text = "\n".join(
        item.get("text", "") for item in items if item.get("type") == "inputText"
    )
    if not result.get("success"):
        raise DesktopError(text or "Codex App 工具执行失败。")
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text, "contentItems": items}


class DesktopClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog = {}
        self.caller = settings.get("controller_thread_id", "")
        self.last_error = None
        self.connected = False

    async def _native_request(self, method, params):
        pipe = self.settings.get("desktop_pipe") or await asyncio.to_thread(
            discover_pipe
        )
        writer = None
        try:
            async with asyncio.timeout(
                self.settings.get("desktop_timeout_seconds", 45)
            ):
                if os.name == "nt":
                    loop = asyncio.get_running_loop()
                    if not hasattr(loop, "create_pipe_connection"):
                        raise DesktopError(
                            "Windows 需要 Proactor 事件循环；可改用本机 host_agent 服务。"
                        )
                    reader = asyncio.StreamReader(limit=MAX_FRAME)
                    protocol = asyncio.StreamReaderProtocol(reader)
                    transport, _ = await loop.create_pipe_connection(
                        lambda: protocol, pipe
                    )
                    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
                else:
                    reader, writer = await asyncio.open_unix_connection(
                        pipe, limit=MAX_FRAME
                    )
                payload = json.dumps(
                    {"id": 1, "jsonrpc": "2.0", "method": method, "params": params}
                ).encode()
                if len(payload) > MAX_FRAME:
                    raise ValueError("Desktop request is too large.")
                writer.write(struct.pack("<I", len(payload)) + payload)
                await writer.drain()
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                if size > MAX_FRAME:
                    raise DesktopError("Desktop response is too large.")
                packet = json.loads(await reader.readexactly(size))
                if "error" in packet:
                    raise DesktopError(
                        str(packet["error"].get("message", "Desktop RPC failed."))
                    )
                return packet["result"]
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, TimeoutError):
                    pass

    async def _remote(self, route, body=None):
        url = self.settings["desktop_url"].rstrip("/") + route
        headers = {"Authorization": "Bearer " + self.settings.get("desktop_token", "")}
        timeout = aiohttp.ClientTimeout(
            total=self.settings.get("desktop_timeout_seconds", 45) + 5
        )
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.request(
                "GET" if body is None else "POST",
                url,
                json=body,
                headers=headers,
                allow_redirects=False,
            ) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    raw.extend(chunk)
                    if len(raw) > MAX_FRAME:
                        raise DesktopError("Host response is too large.")
                result = json.loads(raw)
                if response.status != 200:
                    raise DesktopError(
                        result.get("error", f"Host HTTP {response.status}")
                    )
                return result

    async def tools(self):
        try:
            result = (
                await self._remote("/tools")
                if self.settings.get("desktop_url")
                else await self._native_request(
                    "tools/list", {"threadStartKind": "all"}
                )
            )
            self.catalog = {
                item["name"]: item
                for item in result["tools"]
                if item.get("namespace") == "codex_app"
            }
            if not self.catalog:
                raise DesktopError("Codex 尚未提供桌面操作，请确认 App 已打开。")
            self.connected, self.last_error = True, None
            return list(self.catalog.values())
        except Exception as exc:
            self.connected, self.last_error = False, str(exc)
            raise

    async def call(self, name, arguments=None):
        if not self.catalog:
            await self.tools()
        if name not in self.catalog:
            # Refresh after desktop updates; never invent or emulate an action.
            await self.tools()
        if name not in self.catalog:
            raise ValueError(
                f"当前桌面版本没有提供工具：{name}。用 tools 查看可用操作。"
            )
        arguments = arguments if arguments is not None else {}
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象。")
        arguments = forward_arguments(name, arguments)
        try:
            if self.settings.get("desktop_url"):
                result = await self._remote(
                    "/call", {"tool": name, "arguments": arguments}
                )
            else:
                if not self.caller:
                    self.caller = await asyncio.to_thread(
                        discover_thread, self.settings.get("codex_home")
                    )
                result = unwrap_tool(
                    await self._native_request(
                        "tools/call",
                        {
                            "namespace": "codex_app",
                            "tool": name,
                            "arguments": arguments,
                            "callerSource": "codex",
                            "hostId": "local",
                            "threadId": self.caller,
                            "turnId": "astrbot-control",
                            "callId": "astrbot-" + uuid.uuid4().hex,
                        },
                    )
                )
            self.connected, self.last_error = True, None
            if name == "delete_thread" and arguments.get("threadId") == self.caller:
                self.caller = ""
            return result
        except Exception as exc:
            self.connected, self.last_error = False, str(exc)
            raise

    async def all_threads(self):
        recent = await self.call("list_threads", {"limit": 50})
        rows = recent.get("pinnedThreads", []) + recent.get("threads", [])
        # Discover connected hosts through both sidebar entries and saved projects.
        hosts = {
            "local",
            *[r.get("hostId", "local") for r in rows if r.get("kind") == "codex"],
        }
        unavailable = list(recent.get("unavailableHosts", []))
        if "list_projects" in self.catalog:
            try:
                projects = await self.call("list_projects", {})
                for project in projects.get("projects", []):
                    if project.get("hostId"):
                        hosts.add(project["hostId"])
            except (DesktopError, OSError, TimeoutError) as exc:
                unavailable.append({"scope": "projects", "error": str(exc)[:200]})
        for host in sorted(hosts):
            for archived in (False, True):
                cursor = None
                seen = set()
                while True:
                    args = {
                        "mode": "history",
                        "hostId": host,
                        "limit": 50,
                        "archived": archived,
                    }
                    if cursor:
                        args["cursor"] = cursor
                    try:
                        page = await self.call("list_threads", args)
                    except (DesktopError, OSError, TimeoutError) as exc:
                        unavailable.append({"hostId": host, "error": str(exc)[:200]})
                        break
                    rows.extend(
                        dict(row, archived=archived) for row in page.get("threads", [])
                    )
                    unavailable.extend(page.get("unavailableHosts", []))
                    cursor = page.get("nextCursor")
                    if not cursor or cursor in seen:
                        break
                    seen.add(cursor)
        return {
            "threads": list(
                {(r.get("hostId", "local"), r["id"]): r for r in rows}.values()
            ),
            "unavailableHosts": unavailable,
        }
