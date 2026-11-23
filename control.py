"""Everyday chat actions, with names and a remembered current conversation."""

import ntpath

try:
    from .reports import progress_view
except ImportError:
    from reports import progress_view


STATES = {
    "active": "进行中",
    "inProgress": "进行中",
    "idle": "空闲",
    "notLoaded": "空闲",
    "completed": "已完成",
    "failed": "执行失败",
    "interrupted": "已停止",
    "systemError": "需要处理",
    "queued": "等待执行",
    "running": "执行中",
    "dispatched": "已交给桌面",
    "cancelled": "已取消",
    "timed_out": "执行超时",
    "unconfirmed": "等待确认",
}


def status_text(status):
    if isinstance(status, dict):
        flags = status.get("activeFlags", [])
        if "waitingOnApproval" in flags:
            return "等待批准"
        if "waitingOnUserInput" in flags:
            return "等待回复"
        status = status.get("type")
    return STATES.get(status, status or "状态待更新")


def updated(row):
    timestamp = row.get("updatedAt") or row.get("createdAt") or 0
    return timestamp / 1000 if timestamp > 10**11 else timestamp


def title_of(row):
    return row.get("title") or row.get("name") or row.get("preview") or "未命名聊天"


def brief(row):
    return {
        "id": row["id"],
        "hostId": row.get("hostId") or "local",
        "title": title_of(row),
        "status": status_text(row.get("status")),
        "archived": row.get("archived", False),
    }


def path_key(path):
    return ntpath.normcase(ntpath.normpath(str(path))).rstrip("\\/")


class ChatControl:
    def __init__(self, desktop, settings, save):
        self.desktop, self.settings, self.save = desktop, settings, save
        self.lists = {}

    def selected(self, session):
        return self.settings.get("chat_sessions", {}).get(session)

    def select(self, session, row):
        self.settings.setdefault("chat_sessions", {})[session] = brief(row)
        self.save()
        return self.selected(session)

    def clear(self, session):
        self.settings.setdefault("chat_sessions", {}).pop(session, None)
        self.save()

    async def listing(self, session, search=""):
        listing = await self.desktop.all_threads()
        rows = [
            row for row in listing["threads"] if row.get("kind", "codex") == "codex"
        ]
        if search:
            rows = [
                row for row in rows if search.casefold() in title_of(row).casefold()
            ]
        rows.sort(key=lambda row: (bool(row.get("archived")), -updated(row)))
        self.lists[session] = rows
        return {
            "threads": [dict(number=i, **brief(row)) for i, row in enumerate(rows, 1)],
            "current": self.selected(session),
            "unavailableHosts": listing.get("unavailableHosts", []),
        }

    async def resolve(self, session, target=""):
        target = target.strip()
        if not target or target.casefold() in ("current", "当前", "当前聊天"):
            row = self.selected(session)
            if row:
                return row
            raise ValueError(
                "先用 /codex_threads 看聊天，再用 /codex_use 名称或序号选择；也可以直接指定聊天名称。"
            )
        if target.isdecimal():
            rows = self.lists.get(session)
            if rows is None:
                raise ValueError("先用 /codex_threads 看列表，再使用其中的序号。")
            number = int(target)
            if 1 <= number <= len(rows):
                return brief(rows[number - 1])
            raise ValueError("这个序号不在刚才的聊天列表中。")
        listing = await self.desktop.all_threads()
        rows = [r for r in listing["threads"] if r.get("kind", "codex") == "codex"]
        if target in ("最近", "最新", "latest") and rows:
            return brief(max(rows, key=updated))
        matches = [r for r in rows if r["id"] == target]
        if not matches:
            matches = [r for r in rows if title_of(r).casefold() == target.casefold()]
        if not matches:
            matches = [
                r
                for r in rows
                if target.casefold() in title_of(r).casefold()
                or r["id"].startswith(target)
            ]
        if not matches:
            raise ValueError(f"没找到“{target}”对应的聊天。")
        if len(matches) > 1:
            choices = "\n".join(f"{title_of(r)}（{r['id'][:8]}）" for r in matches[:8])
            raise ValueError(f"找到几条相近聊天，请补充名称或使用列表序号：\n{choices}")
        return brief(matches[0])

    async def read(self, session, target=""):
        row = await self.resolve(session, target)
        detail = await self.desktop.call(
            "read_thread",
            {
                "threadId": row["id"],
                "hostId": row["hostId"],
                "turnLimit": 1,
                "maxOutputCharsPerItem": 20000,
            },
        )
        view = progress_view(detail)
        return {
            "chat": row,
            "progress": view,
            "message": f"{row['title']} · {status_text(view['status'])}\n\n"
            + ("\n\n".join(view["messages"]) or "暂时还没有新的回复。"),
        }

    async def project_target(self, query):
        if not query:
            return {"type": "projectless"}
        projects = (await self.desktop.call("list_projects", {})).get("projects", [])
        matches = [
            p
            for p in projects
            if query in (p.get("id"), p.get("projectId"))
            or path_key(query)
            in [
                path_key(v)
                for v in (p.get("cwd"), p.get("path"), p.get("rootPath"))
                if v
            ]
            or query.casefold() == str(p.get("name") or p.get("title") or "").casefold()
        ]
        if not matches:
            matches = [
                p
                for p in projects
                if query.casefold()
                in str(p.get("name") or p.get("title") or "").casefold()
            ]
        if len(matches) != 1:
            raise ValueError(
                "没找到唯一的 Codex 项目。请用项目名称或在 Codex 中已保存的目录路径。"
            )
        return {
            "type": "project",
            "projectId": matches[0].get("id") or matches[0]["projectId"],
            "environment": {"type": "local"},
        }
