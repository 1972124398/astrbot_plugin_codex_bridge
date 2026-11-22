import asyncio
from datetime import datetime
import hmac
import json
import secrets
from pathlib import Path
import time
import uuid

from aiohttp import web
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import ToolSet
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .bridge import BRIDGE_HOST, BRIDGE_PORT, CALLBACK_TOKEN_FILE, PLUGIN_NAME, CodexClient, JobStore

TOOL_NAMES = ("w1_delegate_to_codex", "w1_codex_job_status")

def field(data, name, limit, *, default=""):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    text = data.get(name, default)
    if not isinstance(text, str) or len(text) > limit:
        raise ValueError(f"Invalid {name}.")
    return text.strip()

class CodexBridge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.runner = None
        self.worker = None
        self.active = {}
        self.store = None
        self.pair_code = None

    async def initialize(self):
        data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.config_path = data_dir / "config.json"
        self.settings = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        self.key = (data_dir / "operator.token").read_text(encoding="ascii").strip()
        self.reverse_key = (data_dir / CALLBACK_TOKEN_FILE).read_text(encoding="ascii").strip()
        if not self.key or not self.reverse_key or self.key == self.reverse_key:
            raise ValueError("Configure two distinct, non-empty Codex Bridge tokens.")
        self.store = JobStore(data_dir)
        self.wake = asyncio.Event()
        app = web.Application(middlewares=[self.authorize], client_max_size=256 * 1024)
        app.add_routes([
            web.get("/status", self.status),
            web.get("/jobs", self.jobs),
            web.get("/jobs/{id}", self.job),
            web.post("/jobs", self.submit),
            web.post("/jobs/{id}/cancel", self.cancel),
            web.post("/pair", self.start_pairing),
            web.post("/notify", self.notify),
        ])
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, BRIDGE_HOST, BRIDGE_PORT).start()
        self.worker = asyncio.create_task(self.run_jobs(), name="astrbot-codex-bridge-jobs")
        self.logger.info(f"AstrBot Codex Bridge ready on {BRIDGE_HOST}:{BRIDGE_PORT}.")

    async def terminate(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
        if self.store:
            self.store.db.close()

    @web.middleware
    async def authorize(self, request, handler):
        token = request.headers.get("Authorization", "").encode("utf-8")
        owner = hmac.compare_digest(token, ("Bearer " + self.key).encode("ascii"))
        callback = hmac.compare_digest(token, ("Bearer " + self.reverse_key).encode("ascii"))
        if not owner and not callback:
            raise web.HTTPUnauthorized()
        if callback and request.path != "/status" and request.path not in ("/notify",):
            raise web.HTTPForbidden()
        try:
            return await handler(request)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def status(self, request):
        return web.json_response({
            "service": "astrbot-codex-bridge", "sandbox": self.settings["sandbox"],
            "queue_running": self.worker is not None and not self.worker.done(),
            "active_job": self.active.get("job_id"), "webchat_owner": self.settings["webchat_owner"],
            "wechat_notification_route": bool(self.settings.get("notification_session")),
            "wechat_operator_count": len(self.settings.get("wechat_operators", [])),
        })

    async def jobs(self, request):
        return web.json_response(self.store.recent())

    async def job(self, request):
        result = self.store.get(request.match_info["id"])
        if result is None:
            raise web.HTTPNotFound()
        return web.json_response(result)

    async def submit(self, request):
        data = await request.json()
        field(data, "prompt", 30000)
        reply_to = None
        if data.get("notify_wechat") is True:
            reply_to = self.settings.get("notification_session")
            if not reply_to:
                raise ValueError("No paired WeChat conversation.")
        return web.json_response(self.enqueue(data, reply_to=reply_to), status=202)

    def enqueue(self, data, *, reply_to=None):
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        prompt = field(data, "prompt", 30000)
        if not prompt:
            raise ValueError("Prompt is required.")
        cwd = Path(field(data, "cwd", 5000, default=self.settings["cwd"]))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("An existing absolute working directory is required.")
        thread_id = field(data, "thread_id", 80)
        if thread_id:
            thread_id = str(uuid.UUID(thread_id))
        when = field(data, "run_at", 80)
        timestamp = time.time()
        if when:
            dt = datetime.fromisoformat(when)
            if dt.tzinfo is None:
                raise ValueError("run_at must include a timezone offset.")
            timestamp = dt.timestamp()
        job_id = self.store.add(prompt, cwd.resolve(), thread_id or None, timestamp, reply_to)
        self.wake.set()
        return {"id": job_id, "state": "queued", "run_at": timestamp}

    async def cancel(self, request):
        job_id = request.match_info["id"]
        job = self.store.get(job_id)
        if job is None:
            raise web.HTTPNotFound()
        if job["state"] == "queued":
            self.store.finish(job_id, "cancelled", "")
            return web.json_response({"id": job_id, "state": "cancelled"})
        if job["state"] == "running" and self.active.get("job_id") == job_id and self.active.get("turn_id"):
            await self.active["client"].interrupt(self.active["thread_id"], self.active["turn_id"])
            return web.json_response({"id": job_id, "state": "interrupt_requested"})
        return web.json_response({"id": job_id, "state": job["state"]}, status=409)

    def new_pair_code(self):
        code = secrets.token_urlsafe(12)
        self.pair_code = (code, time.monotonic() + 900)
        return code

    async def start_pairing(self, request):
        return web.json_response({"code": self.new_pair_code(), "expires_in_seconds": 900})

    async def notify(self, request):
        data = await request.json()
        message = field(data, "message", 2500)
        paired = self.settings.get("notification_session", "")
        session = field(data, "session", 850, default=paired)
        if not message or not paired or session != paired:
            raise ValueError("Provide a message for the paired WeChat conversation.")
        try:
            if not await self.send_result(session, message):
                return web.json_response({"error": "WeChat did not confirm the send."}, status=502)
        except Exception as exc:
            self.logger.warning("Codex result forwarding failed with %s.", type(exc).__name__)
            return web.json_response({"error": "WeChat send could not be confirmed."}, status=502)
        return web.json_response({"confirmed_by_adapter": True})

    async def send_result(self, session, message):
        from astrbot.core.platform.message_session import MessageSession

        target = MessageSession.from_str(session)
        platform = self.context.get_platform_inst(target.platform_id)
        if platform is None:
            raise ValueError("The originating platform is unavailable.")
        chain = MessageChain().message(message)
        if platform.meta().name == "wechat_native":
            from ..astrbot_plugin_wechat_native.event import session_target

            thread = session_target(target.session_id, platform.native_config.account_id)
            return await platform.send_chain(thread, chain)
        if platform.meta().name == "webchat":
            return await self.context.send_message(target, chain)
        raise ValueError("The originating platform does not support Codex replies.")

    async def deliver(self, job):
        status = {
            "completed": "已完成", "failed": "执行失败", "cancelled": "已取消",
            "interrupted": "已中断", "timed_out": "执行超时",
        }.get(job["state"], job["state"])
        result = (job.get("result") or "").strip()
        if len(result) > 1800:
            result = f"{result[:1800]}…\n用 /codex_status {job['id']} 查看更多。"
        message = f"Codex {status} · {job['id']}"
        if result:
            message += f"\n\n{result}"
        try:
            async with asyncio.timeout(80):
                delivered = await self.send_result(job["reply_to"], message)
        except asyncio.CancelledError:
            self.store.record_delivery(job["id"], "unconfirmed")
            raise
        except Exception as exc:
            self.logger.warning("Codex result %s delivery unconfirmed (%s).", job["id"], type(exc).__name__)
            delivered = False
        self.store.record_delivery(job["id"], "sent" if delivered else "unconfirmed")

    async def run_jobs(self):
        while True:
            self.wake.clear()
            reply = self.store.next_reply()
            if reply:
                await self.deliver(reply)
                continue
            job = self.store.next()
            if job is None:
                try:
                    await asyncio.wait_for(self.wake.wait(), 2)
                except TimeoutError:
                    pass
                continue
            self.active = {"job_id": job["id"]}
            try:
                async with asyncio.timeout(self.settings["timeout_seconds"]):
                    async with CodexClient(self.settings) as client:
                        self.active["client"] = client
                        state, result = await client.run(job, self.store.record_turn, self.active)
                self.store.finish(job["id"], state, result)
            except asyncio.CancelledError:
                self.store.finish(job["id"], "interrupted", "AstrBot stopped during this job.")
                raise
            except TimeoutError:
                self.store.finish(job["id"], "timed_out", "Codex job exceeded the configured timeout.")
            except Exception as exc:
                self.store.finish(job["id"], "failed", str(exc)[:1200])
                self.logger.warning("Codex job %s failed with %s.", job["id"], type(exc).__name__)
            finally:
                self.active = {}

    def is_operator(self, event):
        if not event.is_private_chat():
            return False
        if event.get_platform_name() == "webchat":
            return event.get_sender_id() == self.settings["webchat_owner"]
        return (event.get_platform_id() == self.settings.get("wechat_platform_id") and
                event.get_sender_id() in self.settings.get("wechat_operators", []))

    @filter.llm_tool(name="w1_delegate_to_codex")
    async def delegate(self, event: AstrMessageEvent, prompt: str, cwd: str = "",
                       run_at: str = "", thread_id: str = "") -> str:
        """Send a user-requested task or message to local Codex. The result is delivered back to this conversation.

        Args:
            prompt(string): User-authorized task and context for Codex.
            cwd(string): Optional absolute project directory.
            run_at(string): Optional ISO-8601 time including the timezone offset.
            thread_id(string): Optional existing local Codex thread UUID.
        """
        if not self.is_operator(event):
            return "This service requires an authenticated operator."
        data = {"prompt": prompt, "run_at": run_at, "thread_id": thread_id}
        if cwd:
            data["cwd"] = cwd
        try:
            return json.dumps(self.enqueue(data, reply_to=event.unified_msg_origin))
        except ValueError as exc:
            return str(exc)

    @filter.llm_tool(name="w1_codex_job_status")
    async def job_status(self, event: AstrMessageEvent, job_id: str) -> str:
        """Read the current state and result of a job delegated to Codex.

        Args:
            job_id(string): Codex job identifier returned by the delegate tool.
        """
        if not self.is_operator(event):
            return "This service requires an authenticated operator."
        return json.dumps(self.store.get(job_id), ensure_ascii=False)[:24000]

    @filter.on_llm_request()
    async def scope_tools(self, event, request):
        if request.func_tool:
            for name in TOOL_NAMES:
                request.func_tool.remove_tool(name)
        if self.is_operator(event):
            if request.func_tool is None:
                request.func_tool = ToolSet()
            for name in TOOL_NAMES:
                tool = self.context.get_llm_tool_manager().get_func(name)
                if tool is not None:
                    request.func_tool.add_tool(tool)



    @filter.command("codex")
    async def command_codex(self, event: AstrMessageEvent):
        """Queue a job for local Codex."""
        if not self.is_operator(event):
            return
        prompt = event.message_str.partition(" ")[2].strip()
        if not prompt:
            yield event.plain_result("用法：/codex <任务说明>")
            return
        receipt = self.enqueue({"prompt": prompt}, reply_to=event.unified_msg_origin)
        yield event.plain_result(f"已交给 Codex。任务编号 {receipt['id']}。完成后会在这里回复，也可用 /codex_status 查询。")

    @filter.command("codex_status")
    async def command_status(self, event: AstrMessageEvent):
        """Read a Codex job from the persistent queue."""
        if not self.is_operator(event):
            return
        job_id = event.message_str.partition(" ")[2].strip()
        if job_id:
            job = self.store.get(job_id)
            if job is None:
                yield event.plain_result("没有找到这个任务。")
                return
            result = (job.get("result") or "")[:9000]
            delivery = {
                "pending": "待回传", "sending": "回传中", "sent": "已回传",
                "unconfirmed": "回传未确认",
            }.get(job.get("delivery"))
            summary = f"{job['id']} · {job['state']}"
            if delivery:
                summary += f" · {delivery}"
            yield event.plain_result(f"{summary}\n{result}")
            return
        lines = [f"{job['id']} · {job['state']}" for job in self.store.recent()[:10]]
        yield event.plain_result("\n".join(lines) or "目前没有 Codex 任务。")


    @filter.command("codex_pair")
    async def command_pair(self, event: AstrMessageEvent):
        """Pair a private WeChat account using a one-time code from the local operator."""
        if not event.is_private_chat():
            return
        if (event.get_platform_name() == "webchat" and self.is_operator(event)
                and not event.message_str.partition(" ")[2].strip()):
            yield event.plain_result(
                f"15 分钟内，在微信私聊机器人并发送 /codex_pair {self.new_pair_code()}"
            )
            return
        if event.get_platform_id() != self.settings.get("wechat_platform_id"):
            return
        candidate = event.message_str.partition(" ")[2].strip()
        if not self.pair_code or not candidate:
            return
        code, deadline = self.pair_code
        if time.monotonic() >= deadline or not hmac.compare_digest(candidate.encode("utf-8"), code.encode("ascii")):
            return
        sender = event.get_sender_id()
        operators = list(dict.fromkeys([*self.settings.get("wechat_operators", []), sender]))
        updated = dict(self.settings, wechat_operators=operators, notification_session=event.unified_msg_origin)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.config_path)
        self.settings = updated
        self.pair_code = None
        yield event.plain_result("已关联这条私聊。现在可以在这条私聊中委派 Codex 任务。")
