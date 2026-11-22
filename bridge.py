import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

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
            self.db.execute("""UPDATE jobs SET state='interrupted',
                result='AstrBot 在任务执行期间停止，执行结果未确认。'
                WHERE state='running'""")
            self.db.execute("UPDATE jobs SET delivery='unconfirmed' WHERE delivery='sending'")

    def add(self, prompt, cwd, thread_id, run_at, reply_to=None):
        job_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute(
                """INSERT INTO jobs(id,prompt,cwd,thread_id,run_at,state,created,reply_to,delivery)
                VALUES(?,?,?,?,?,'queued',?,?,?)""",
                (job_id, prompt, str(cwd), thread_id, run_at, time.time(), reply_to,
                 "pending" if reply_to else None),
            )
        return job_id

    def next_reply(self):
        with self.db:
            row = self.db.execute("""SELECT * FROM jobs
                WHERE delivery='pending' AND state NOT IN ('queued','running')
                ORDER BY created LIMIT 1""").fetchone()
            if row:
                self.db.execute("UPDATE jobs SET delivery='sending' WHERE id=?", (row["id"],))
        return dict(row) if row else None

    def record_delivery(self, job_id, state):
        with self.db:
            self.db.execute("UPDATE jobs SET delivery=? WHERE id=?", (state, job_id))

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recent(self):
        return [dict(row) for row in self.db.execute(
            "SELECT id,cwd,thread_id,run_at,state,created FROM jobs ORDER BY created DESC LIMIT 25"
        )]

    def next(self):
        with self.db:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE state='queued' AND run_at<=? ORDER BY run_at,created LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row:
                self.db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
        return dict(row) if row else None

    def finish(self, job_id, state, result):
        with self.db:
            self.db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (state, result, job_id))

    def record_turn(self, job_id, thread_id, turn_id):
        with self.db:
            self.db.execute("UPDATE jobs SET thread_id=?,turn_id=? WHERE id=?", (thread_id, turn_id, job_id))


class CodexClient:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.request_id = 0
        self.staged = []

    async def __aenter__(self):
        args = [self.settings["codex"], "app-server", "--listen", "stdio://"]
        if mcp_server := self.settings.get("notification_mcp_server"):
            if not mcp_server.isascii() or not mcp_server.replace("_", "").replace("-", "").isalnum():
                raise ValueError("Invalid notification_mcp_server.")
            args.extend(["-c", f'mcp_servers.{mcp_server}.default_tools_approval_mode="approve"'])
        self.process = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.settings["cwd"], stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), limit=8 * 1024 * 1024,
        )
        try:
            await asyncio.wait_for(self.request("initialize", {
                "clientInfo": {"name": "astrbot-codex-bridge", "version": "1.2.0"},
            }), 15)
            await self.send({"method": "initialized"})
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            if self.process.returncode is None:
                self.process.kill()
            await self.process.wait()

    async def send(self, payload):
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self):
        while raw := await self.process.stdout.readline():
            packet = json.loads(raw)
            if "id" in packet and "method" in packet:
                # The service uses its configured permission profile without an interactive reviewer.
                await self.send({"id": packet["id"], "error": {
                    "code": -32601, "message": "Interactive requests require an interactive Codex client.",
                }})
                continue
            return packet
        raise RuntimeError("Codex App Server closed the connection.")

    async def request(self, method, params):
        self.request_id += 1
        request_id = self.request_id
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            packet = await self.receive()
            if packet.get("id") == request_id:
                if "error" in packet:
                    raise RuntimeError(f"Codex {method} error {packet['error'].get('code')}: {str(packet['error'].get('message',''))[:300]}")
                return packet["result"]
            if "method" in packet:
                self.staged.append(packet)

    async def run(self, job, record, active):
        params = {
            "cwd": job["cwd"], "sandbox": self.settings["sandbox"],
            "approvalPolicy": "never",
        }
        method = "thread/start"
        if job["thread_id"]:
            method = "thread/resume"
            params.update(threadId=job["thread_id"], excludeTurns=True)
        if not job["thread_id"] and job["prompt"].startswith("[W1 integration test]"):
            params["ephemeral"] = True
        thread = (await self.request(method, params))["thread"]
        turn = (await self.request("turn/start", {
            "threadId": thread["id"], "input": [{"type": "text", "text": job["prompt"]}],
        }))["turn"]
        record(job["id"], thread["id"], turn["id"])
        active.update(thread_id=thread["id"], turn_id=turn["id"])
        replies = []
        while True:
            packet = self.staged.pop(0) if self.staged else await self.receive()
            details = packet.get("params") or {}
            if details.get("threadId") != thread["id"]:
                continue
            match packet.get("method"):
                case "item/completed":
                    item = details.get("item", {})
                    if item.get("type") == "agentMessage":
                        replies.append(item.get("text", ""))
                case "turn/completed":
                    completed = details["turn"]
                    status = completed["status"]
                    result = "\n\n".join(replies).strip()
                    if status != "completed" and completed.get("error"):
                        result = str(completed["error"].get("message", ""))[:1000]
                    return status, result[:80000]

    async def interrupt(self, thread_id, turn_id):
        # The worker is the sole reader. Its receive loop discards this RPC acknowledgement.
        await self.send({"id": 1000000, "method": "turn/interrupt",
                         "params": {"threadId": thread_id, "turnId": turn_id}})
