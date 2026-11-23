import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

try:
    from .protocol import forward_prompt
except ImportError:
    from protocol import forward_prompt

PLUGIN_NAME = "astrbot_plugin_codex_bridge"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 6196
CALLBACK_TOKEN_FILE = "callback.token"


class JobStore:
    def __init__(self, data_dir: Path):
        self.db = sqlite3.connect(data_dir / "jobs.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, cwd TEXT NOT NULL,
            thread_id TEXT, turn_id TEXT, run_at REAL NOT NULL, state TEXT NOT NULL,
            result TEXT, created REAL NOT NULL
        )""")
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
        with self.db:
            if "reply_to" not in columns:
                self.db.execute("ALTER TABLE jobs ADD COLUMN reply_to TEXT")
            if "delivery" not in columns:
                self.db.execute("ALTER TABLE jobs ADD COLUMN delivery TEXT")
            if "host_id" not in columns:
                self.db.execute(
                    "ALTER TABLE jobs ADD COLUMN host_id TEXT DEFAULT 'local'"
                )
            if "dispatched_at" not in columns:
                self.db.execute("ALTER TABLE jobs ADD COLUMN dispatched_at REAL")
            if "target_json" not in columns:
                self.db.execute("ALTER TABLE jobs ADD COLUMN target_json TEXT")
            if "owned" not in columns:
                self.db.execute(
                    "ALTER TABLE jobs ADD COLUMN owned INTEGER NOT NULL DEFAULT 0"
                )
                self.db.execute("UPDATE jobs SET owned=1 WHERE thread_id IS NULL")
            if "delivery_attempts" not in columns:
                self.db.execute(
                    "ALTER TABLE jobs ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "next_delivery" not in columns:
                self.db.execute(
                    "ALTER TABLE jobs ADD COLUMN next_delivery REAL NOT NULL DEFAULT 0"
                )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS owned_threads(id TEXT PRIMARY KEY)"
            )
            self.db.execute("""UPDATE jobs SET state='interrupted',
                result='AstrBot 在任务执行期间停止，执行结果未确认。'
                WHERE state='running'""")
            self.db.execute(
                "UPDATE jobs SET delivery='pending' WHERE delivery='sending'"
            )

    def add(
        self,
        prompt,
        cwd,
        thread_id,
        run_at,
        reply_to=None,
        *,
        host_id="local",
        target=None,
    ):
        job_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute(
                """INSERT INTO jobs(id,prompt,cwd,thread_id,run_at,state,created,reply_to,delivery,owned,host_id,target_json)
                VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?)""",
                (
                    job_id,
                    prompt,
                    str(cwd),
                    thread_id,
                    run_at,
                    time.time(),
                    reply_to,
                    "pending" if reply_to else None,
                    int(thread_id is None),
                    host_id,
                    json.dumps(target) if target else None,
                ),
            )
        return job_id

    def next_reply(self):
        with self.db:
            row = self.db.execute(
                """SELECT * FROM jobs
                WHERE delivery='pending' AND state NOT IN ('queued','running','dispatched')
                AND next_delivery<=? ORDER BY created LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE jobs SET delivery='sending' WHERE id=?", (row["id"],)
                )
        return dict(row) if row else None

    def record_delivery(self, job_id, state):
        with self.db:
            if state in ("sent", "handed_off"):
                self.db.execute(
                    "UPDATE jobs SET delivery=? WHERE id=?", (state, job_id)
                )
            else:
                self.db.execute(
                    """UPDATE jobs SET delivery_attempts=delivery_attempts+1,
                    next_delivery=?,delivery=CASE WHEN delivery_attempts>=7 THEN 'unconfirmed' ELSE 'pending' END
                    WHERE id=?""",
                    (time.time() + 30, job_id),
                )

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recent(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT id,prompt,cwd,thread_id,run_at,state,created FROM jobs ORDER BY created DESC LIMIT 25"
            )
        ]

    def thread_jobs(self):
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM jobs WHERE thread_id IS NOT NULL")
        ]

    def owned_threads(self):
        return {
            r[0]
            for r in self.db.execute(
                "SELECT id FROM owned_threads UNION SELECT thread_id FROM jobs WHERE owned=1 AND thread_id IS NOT NULL"
            )
        }

    def own_thread(self, thread_id):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO owned_threads VALUES(?)", (thread_id,)
            )

    def dispatched(self):
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM jobs WHERE state='dispatched'")
        ]

    def dispatch(self, job_id, thread_id, host_id="local"):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state='dispatched',thread_id=?,host_id=?,dispatched_at=? WHERE id=?",
                (thread_id, host_id, time.time(), job_id),
            )

    def next(self):
        with self.db:
            row = self.db.execute(
                """SELECT * FROM jobs j WHERE state='queued' AND run_at<=?
                AND (thread_id IS NULL OR NOT EXISTS
                    (SELECT 1 FROM jobs d WHERE d.thread_id=j.thread_id AND d.state='dispatched'))
                ORDER BY run_at,created LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE jobs SET state='running' WHERE id=?", (row["id"],)
                )
        return dict(row) if row else None

    def finish(self, job_id, state, result):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state=?,result=? WHERE id=?", (state, result, job_id)
            )

    def record_turn(self, job_id, thread_id, turn_id):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET thread_id=?,turn_id=? WHERE id=?",
                (thread_id, turn_id, job_id),
            )


class CodexClient:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.request_id = 0
        self.staged = []
        self.pending = {}
        self.events = asyncio.Queue(maxsize=4096)
        self.reader_task = None
        self.write_lock = asyncio.Lock()

    async def __aenter__(self):
        args = [self.settings["codex"], "app-server", "--listen", "stdio://"]
        if mcp_server := self.settings.get("notification_mcp_server"):
            if (
                not mcp_server.isascii()
                or not mcp_server.replace("_", "").replace("-", "").isalnum()
            ):
                raise ValueError("Invalid notification_mcp_server.")
            args.extend(
                [
                    "-c",
                    f'mcp_servers.{mcp_server}.default_tools_approval_mode="approve"',
                ]
            )
        self.process = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.settings["cwd"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            limit=8 * 1024 * 1024,
        )
        self.reader_task = asyncio.create_task(self.read_loop())
        try:
            await asyncio.wait_for(
                self.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "astrbot-codex-bridge",
                            "version": "2.3.0",
                        },
                    },
                ),
                15,
            )
            await self.send({"method": "initialized"})
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        if not self.process:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            if self.process.returncode is None:
                self.process.kill()
            await self.process.wait()
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
        self.process = None

    async def send(self, payload):
        async with self.write_lock:
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()

    async def read_loop(self):
        failure = RuntimeError("Codex App Server closed the connection.")
        try:
            while raw := await self.process.stdout.readline():
                packet = json.loads(raw)
                if "id" in packet and "method" in packet:
                    await self.send(
                        {
                            "id": packet["id"],
                            "error": {
                                "code": -32601,
                                "message": "Interactive requests are unavailable in background mode.",
                            },
                        }
                    )
                elif "id" in packet:
                    future = self.pending.get(packet["id"])
                    if future is not None and not future.done():
                        if "error" in packet:
                            future.set_exception(
                                RuntimeError(str(packet["error"])[:500])
                            )
                        else:
                            future.set_result(packet.get("result"))
                elif packet.get("method") in ("item/completed", "turn/completed"):
                    self.events.put_nowait(packet)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            # A terminal event must wake an idle run loop, including on overflow.
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(failure)

    async def receive(self):
        packet = await self.events.get()
        if isinstance(packet, BaseException):
            raise packet
        return packet

    async def request(self, method, params):
        self.request_id += 1
        request_id = self.request_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 30)
        finally:
            self.pending.pop(request_id, None)

    async def run(self, job, record, active):
        params = {
            "cwd": job["cwd"],
            "sandbox": self.settings["sandbox"],
            "approvalPolicy": "never",
        }
        method = "thread/start"
        if job["thread_id"]:
            method = "thread/resume"
            params.update(threadId=job["thread_id"], excludeTurns=True)
        if not job["thread_id"] and job["prompt"].startswith("[W1 integration test]"):
            params["ephemeral"] = True
        thread = (await self.request(method, params))["thread"]
        turn = (
            await self.request(
                "turn/start",
                {
                    "threadId": thread["id"],
                    "input": [{"type": "text", "text": forward_prompt(job["prompt"])}],
                },
            )
        )["turn"]
        record(job["id"], thread["id"], turn["id"])
        active.update(thread_id=thread["id"], turn_id=turn["id"])
        replies = []
        while True:
            packet = self.staged.pop(0) if self.staged else await self.receive()
            details = packet.get("params") or {}
            if details.get("threadId") != thread["id"]:
                continue
            if details.get("turnId") and details["turnId"] != turn["id"]:
                continue
            match packet.get("method"):
                case "item/completed":
                    item = details.get("item", {})
                    if item.get("type") == "agentMessage":
                        replies.append(item.get("text", ""))
                case "turn/completed":
                    completed = details["turn"]
                    if completed["id"] != turn["id"]:
                        continue
                    status = completed["status"]
                    result = "\n\n".join(replies).strip()
                    if status != "completed" and completed.get("error"):
                        result = str(completed["error"].get("message", ""))[:1000]
                    return status, result[:80000]

    async def interrupt(self, thread_id, turn_id):
        await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
