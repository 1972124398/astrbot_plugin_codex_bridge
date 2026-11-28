import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from bridge import CodexClient, JobStore
from desktop import DesktopClient, DesktopError, discover_thread, unwrap_tool
from host_agent import create_app
from reports import GlobalMonitor, ReportStore, digest, progress_view
from settings import DEFAULTS, load_settings, save_routing
from control import ChatControl
from relay import AstrBotRelay
from protocol import forward_prompt


def snapshot(
    text="Working", status="active", turn_status="inProgress", turn="t1", flags=None
):
    row = {
        "id": "external",
        "hostId": "local",
        "kind": "codex",
        "title": "Human chat",
        "status": {"type": status, "activeFlags": flags or []},
        "updatedAt": 100,
    }
    detail = {
        "thread": row,
        "turns": [
            {
                "id": turn,
                "status": turn_status,
                "items": [
                    {"type": "agentMessage", "text": text, "phase": "commentary"},
                    {"type": "reasoning", "text": "PRIVATE_REASONING"},
                    {
                        "type": "commandExecution",
                        "command": "SECRET_COMMAND",
                        "output": "SECRET_OUTPUT",
                    },
                ],
            }
        ],
    }
    return row, detail


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.reports = ReportStore(self.path)

    def tearDown(self):
        self.reports.db.close()

    def test_session_cleanup_leaves_shared_reports_and_active_jobs_available(self):
        jobs = JobStore(self.path)
        self.addCleanup(jobs.db.close)
        finished = jobs.add("finished", self.path, None, 0, reply_to="a")
        jobs.dispatch(finished, "owned-thread")
        jobs.finish(finished, "completed", "result")
        active = jobs.add("active", self.path, None, 0, reply_to="a")
        other = jobs.add("other", self.path, None, 0, reply_to="b")
        jobs.finish(other, "completed", "other result")
        jobs.clear_session_results("a")
        self.assertIsNone(jobs.get(finished))
        self.assertIsNotNone(jobs.get(active))
        self.assertIsNotNone(jobs.get(other))
        self.assertIn("owned-thread", jobs.owned_threads())

        notice = self.reports.add_notice("a", "session-only report")
        row, detail = snapshot()
        self.reports.observe(row, detail, ["a", "b"], 60)
        self.reports.clear_session_deliveries("a")
        self.assertNotIn(notice, [report["id"] for report in self.reports.recent()])
        self.assertEqual(self.reports.pending()["session"], "b")
        self.assertTrue(self.reports.settled("local:external", detail))

    def test_global_history_is_seeded_and_external_active_is_reported(self):
        row, detail = snapshot(status="idle", turn_status="completed")
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=100))
        row, detail = snapshot(text="New progress", turn="t2")
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=101))
        self.assertEqual(len(self.reports.recent()), 1)
        payload = self.reports.pending()["payload"]
        self.assertIn("New progress", payload)
        self.assertNotIn("PRIVATE_REASONING", payload)
        self.assertNotIn("SECRET_COMMAND", payload)
        self.assertNotIn("SECRET_OUTPUT", payload)

    def test_progress_throttle_and_immediate_completion_and_input(self):
        row, detail = snapshot()
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=100))
        row, detail = snapshot(text="More progress")
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=101))
        self.assertFalse(self.reports.settled("local:external", detail))
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=161))
        row, detail = snapshot(flags=["waitingOnUserInput"])
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=162))
        row, detail = snapshot(text="Done", status="idle", turn_status="completed")
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=163))
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=164))

    def test_inactive_status_changes_do_not_repeat_completion_after_upgrade(self):
        identity = "local:external"
        _, detail = snapshot(text="Done", status="idle", turn_status="completed")
        legacy = progress_view(detail)
        with self.reports.db:
            self.reports.db.execute(
                "INSERT INTO observations VALUES(?,?,?,?,?)",
                (identity, digest(legacy), json.dumps([legacy["status"], "t1", "completed", None]), 100, 100),
            )
        self.reports.db.close()
        self.reports = ReportStore(self.path)

        for now, status in ((170, "notLoaded"), (240, "idle")):
            with self.subTest(status=status):
                row, detail = snapshot(text="Done", status=status, turn_status="completed")
                self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=now))
                self.assertTrue(self.reports.settled(identity, detail))
        self.assertEqual(self.reports.recent(), [])

        row, detail = snapshot(text="Updated result", status="notLoaded", turn_status="completed")
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=241))
        row, detail = snapshot(text="Updated result", status="idle", turn_status="completed", turn="t2")
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=242))
        self.assertEqual(len(self.reports.recent()), 2)

    def test_empty_turn_start_waits_for_actual_commentary(self):
        row, detail = snapshot(text="")
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=10))
        self.assertTrue(self.reports.settled("local:external", detail))
        self.assertIsNone(self.reports.pending())
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=11))
        row, detail = snapshot(text="开始修复")
        self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, now=12))
        self.assertEqual(len(self.reports.recent()), 1)

    def test_empty_progress_after_previous_result_does_not_repeat_completion(self):
        row, detail = snapshot(text="Done", status="idle", turn_status="completed")
        self.reports.observe(row, detail, ["admin"], 60, now=100)
        row, detail = snapshot(text="  \n", turn="t2")
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, now=101))
        self.assertEqual(self.reports.recent(), [])
        self.assertTrue(self.reports.settled("local:external", detail))

    def test_empty_completion_failure_and_attention_still_report(self):
        for status, turn_status, flags, error in (
            ("idle", "completed", [], None),
            ("idle", "failed", [], {"message": "failed"}),
            ("idle", "interrupted", [], None),
            ("active", "inProgress", ["waitingOnUserInput"], None),
            ("active", "inProgress", ["waitingOnApproval"], None),
            ("systemError", "inProgress", [], None),
        ):
            with self.subTest(status=status, turn_status=turn_status, flags=flags):
                row, detail = snapshot(text="", status=status, turn_status=turn_status, flags=flags)
                row["id"] = f"case-{status}-{turn_status}-{flags}"
                detail["turns"][0]["error"] = error
                self.assertTrue(self.reports.observe(row, detail, ["admin"], 60, report_new=True))

    def test_outbox_survives_restart_and_retries_without_losing_report(self):
        row, detail = snapshot()
        self.reports.observe(row, detail, ["admin"], 60)
        delivery = self.reports.pending()
        self.reports.delivered(delivery["report_id"], "admin", False)
        self.assertIsNone(self.reports.pending())
        self.reports.db.close()
        self.reports = ReportStore(self.path)
        self.assertIsNotNone(self.reports.pending(now=10**12))
        for _ in range(7):
            self.reports.delivered(delivery["report_id"], "admin", False)
        self.assertEqual(self.reports.delivery_counts(), {"failed": 1})
        self.assertEqual(len(self.reports.recent()), 1)

    def test_owned_chats_suppressed_and_reports_available_before_binding(self):
        row, detail = snapshot()
        self.assertFalse(self.reports.observe(row, detail, ["admin"], 60, owned=True))
        row["id"] = "external2"
        self.assertTrue(self.reports.observe(row, detail, [], 60))
        self.assertEqual(len(self.reports.recent()), 1)
        self.assertIsNone(self.reports.pending())

    def test_settings_bootstrap_and_persistent_route(self):
        settings, tokens = load_settings(self.path)
        self.assertNotEqual(*tokens)
        self.assertEqual(load_settings(self.path)[1], tokens)
        settings["notification_session"] = "new-admin"
        save_routing(self.path, settings)
        self.assertEqual(
            load_settings(self.path, {"notification_session": "old-admin"})[0][
                "notification_session"
            ],
            "new-admin",
        )
        with self.assertRaises(ValueError):
            load_settings(self.path, {"bridge_port": True})
        with self.assertRaises(ValueError):
            load_settings(self.path, {"desktop_url": "http://localhost:6197"})

    def test_legacy_job_migration_and_dispatched_recovery(self):
        db = sqlite3.connect(self.path / "jobs.sqlite")
        db.execute("""CREATE TABLE jobs(id TEXT PRIMARY KEY,prompt TEXT NOT NULL,cwd TEXT NOT NULL,
            thread_id TEXT,turn_id TEXT,run_at REAL NOT NULL,state TEXT NOT NULL,result TEXT,created REAL NOT NULL)""")
        db.execute("INSERT INTO jobs VALUES('old','p','',NULL,NULL,0,'running',NULL,0)")
        db.commit()
        db.close()
        jobs = JobStore(self.path)
        self.assertEqual(jobs.get("old")["state"], "interrupted")
        job = jobs.add("p", "", None, 0, "admin")
        jobs.dispatch(job, "bot-created")
        jobs.db.close()
        jobs = JobStore(self.path)
        self.assertEqual(jobs.get(job)["state"], "dispatched")
        self.assertIn("bot-created", jobs.owned_threads())
        next_job = jobs.add("followup", "", "bot-created", 0)
        self.assertIsNone(jobs.next())
        jobs.finish(job, "completed", "Done")
        self.assertEqual(jobs.next()["id"], next_job)
        self.assertEqual(jobs.next_reply()["id"], job)
        jobs.record_delivery(job, "unconfirmed")
        self.assertEqual(jobs.get(job)["delivery"], "pending")
        jobs.db.close()

    def test_caller_discovery_reads_metadata_without_writes(self):
        db = sqlite3.connect(self.path / "state_5.sqlite")
        db.execute("CREATE TABLE threads(id TEXT,archived INTEGER,updated_at INTEGER)")
        db.executemany(
            "INSERT INTO threads VALUES(?,?,?)",
            [("older", 0, 1), ("archived", 1, 3), ("newest", 0, 2)],
        )
        db.commit()
        db.close()
        self.assertEqual(discover_thread(str(self.path)), "newest")


class DesktopTests(unittest.IsolatedAsyncioTestCase):
    async def test_names_numbers_and_selected_chat_survive_routing_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            settings, _ = load_settings(Path(temp))
            rows = [
                {
                    "id": "chat-a",
                    "title": "修复登录",
                    "hostId": "remote",
                    "updatedAt": 1000,
                },
                {"id": "chat-b", "title": "整理文档", "updatedAt": 2000},
            ]
            desktop = Mock()
            desktop.all_threads = AsyncMock(return_value={"threads": rows})
            chats = ChatControl(
                desktop, settings, lambda: save_routing(Path(temp), settings)
            )
            listing = await chats.listing("admin")
            self.assertEqual(listing["threads"][0]["title"], "整理文档")
            # A numeric target refers to the displayed list, even if activity changes.
            rows[0]["updatedAt"] = 3000
            chosen = await chats.resolve("admin", "2")
            self.assertEqual(chosen["id"], "chat-a")
            chats.select("admin", chosen)
            reloaded = ChatControl(desktop, load_settings(Path(temp))[0], lambda: None)
            self.assertEqual((await reloaded.resolve("admin"))["hostId"], "remote")
            self.assertEqual((await chats.resolve("admin", "登录"))["id"], "chat-a")

    async def test_ambiguous_names_do_not_send_to_arbitrary_chat(self):
        desktop = Mock()
        desktop.all_threads = AsyncMock(
            return_value={
                "threads": [
                    {"id": "a", "title": "登录前端"},
                    {"id": "b", "title": "登录后端"},
                ]
            }
        )
        chats = ChatControl(desktop, {}, lambda: None)
        with self.assertRaisesRegex(ValueError, "几条相近"):
            await chats.resolve("admin", "登录")
        desktop.call.assert_not_called()

    async def test_project_name_or_windows_path_selects_saved_project(self):
        desktop = Mock()
        desktop.call = AsyncMock(
            return_value={
                "projects": [
                    {"id": "project-a", "name": "登录服务", "cwd": "D:/Projects/Login"}
                ]
            }
        )
        chats = ChatControl(desktop, {}, lambda: None)
        by_name = await chats.project_target("登录服务")
        by_path = await chats.project_target("d:\\projects\\login\\")
        self.assertEqual(by_name, by_path)
        self.assertEqual(by_name["projectId"], "project-a")

    async def test_catalog_and_exact_call_unwrap_and_no_retry(self):
        client = DesktopClient({"controller_thread_id": "caller"})
        catalog = [
            {"name": n, "namespace": "codex_app"}
            for n in ("list_threads", "read_thread", "send_message_to_thread")
        ]
        client._native_request = AsyncMock(
            side_effect=[
                {"tools": catalog},
                {
                    "success": True,
                    "contentItems": [{"type": "inputText", "text": '{"ok":true}'}],
                },
                TimeoutError("Uncertain send"),
            ]
        )
        self.assertEqual(await client.call("list_threads", {}), {"ok": True})
        with self.assertRaises(TimeoutError):
            await client.call(
                "send_message_to_thread", {"threadId": "external", "prompt": "task"}
            )
        outgoing = client._native_request.call_args.args[1]["arguments"]["prompt"]
        self.assertIn("AstrBot 代表用户", outgoing)
        self.assertEqual(client._native_request.await_count, 3)
        self.assertFalse(client.connected)
        with self.assertRaises(DesktopError):
            unwrap_tool(
                {
                    "success": False,
                    "contentItems": [{"type": "inputText", "text": "Denied"}],
                }
            )

    async def test_all_history_pages_including_archives_and_remote(self):
        client = DesktopClient({})
        client.catalog = {"list_threads": {}, "list_projects": {}}

        async def call(name, args):
            if name == "list_projects":
                return {"projects": [{"hostId": "remote"}]}
            if args.get("mode") != "history":
                return {
                    "threads": [{"id": "human", "hostId": "local", "kind": "codex"}]
                }
            if args["hostId"] == "remote":
                raise DesktopError("offline")
            if args.get("archived"):
                return {"threads": [{"id": "archive", "hostId": "local"}]}
            if args.get("cursor"):
                return {"threads": [{"id": "old", "hostId": "local"}]}
            return {
                "threads": [{"id": "human", "hostId": "local"}],
                "nextCursor": "page2",
            }

        client.call = AsyncMock(side_effect=call)
        result = await client.all_threads()
        self.assertEqual(
            {r["id"] for r in result["threads"]}, {"human", "old", "archive"}
        )
        self.assertTrue(result["unavailableHosts"])

    @unittest.skipIf(
        os.name == "nt",
        "Unix transport test; Windows transport has a real local smoke check",
    )
    async def test_native_frame_fragmentation_and_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "desktop.sock")

            async def peer(reader, writer):
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                packet = json.loads(await reader.readexactly(size))
                body = json.dumps({"id": packet["id"], "result": {"ok": True}}).encode()
                frame = struct.pack("<I", len(body)) + body
                for chunk in (frame[:2], frame[2:7], frame[7:]):
                    writer.write(chunk)
                    await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_unix_server(peer, path)
            try:
                self.assertEqual(
                    await DesktopClient({"desktop_pipe": path})._native_request(
                        "tools/list", {}
                    ),
                    {"ok": True},
                )
            finally:
                server.close()
                await server.wait_closed()

    async def test_host_agent_auth_and_remote_adapter(self):
        desktop = Mock()
        desktop.tools = AsyncMock(
            return_value=[
                {"namespace": "codex_app", "name": n}
                for n in ("list_threads", "read_thread", "send_message_to_thread")
            ]
        )
        desktop.call = AsyncMock(return_value={"threads": [{"id": "external"}]})
        async with TestClient(TestServer(create_app(desktop, "secret"))) as http:
            self.assertEqual((await http.get("/tools")).status, 401)
            remote = DesktopClient(
                {"desktop_url": str(http.make_url("/")), "desktop_token": "secret"}
            )
            self.assertEqual(
                (await remote.call("list_threads"))["threads"][0]["id"], "external"
            )
            self.assertEqual(desktop.call.await_count, 1)

    async def test_monitor_does_not_read_every_idle_history_each_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            store, jobs = ReportStore(Path(temp)), JobStore(Path(temp))
            desktop = Mock()
            row, detail = snapshot(status="idle", turn_status="completed")
            desktop.all_threads = AsyncMock(return_value={"threads": [row]})
            desktop.call = AsyncMock(return_value=detail)
            monitor = GlobalMonitor(desktop, store, jobs, DEFAULTS, logging.getLogger())
            try:
                await monitor.scan()
                await monitor.scan()
                desktop.call.assert_not_awaited()
                row["updatedAt"] = 101
                await monitor.scan()
                desktop.call.assert_awaited_once()
                self.assertEqual(len(store.recent()), 1)
            finally:
                store.db.close()
                jobs.db.close()

    async def test_new_task_finished_between_scans_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            store, jobs = ReportStore(Path(temp)), JobStore(Path(temp))
            desktop = Mock()
            row, detail = snapshot(status="idle", turn_status="completed")
            desktop.all_threads = AsyncMock(
                side_effect=[{"threads": []}, {"threads": [row]}]
            )
            desktop.call = AsyncMock(return_value=detail)
            monitor = GlobalMonitor(desktop, store, jobs, DEFAULTS, logging.getLogger())
            try:
                await monitor.scan()
                await monitor.scan()
                self.assertEqual(len(store.recent()), 1)
                desktop.call.assert_awaited_once()
            finally:
                store.db.close()
                jobs.db.close()

    async def test_rpc_concurrent_replies_are_correlated_by_id(self):
        client = CodexClient({})
        client.process = Mock()
        client.process.stdout = asyncio.StreamReader()
        requests = []

        async def send(packet):
            requests.append(packet)
            if len(requests) == 2:
                for request in reversed(requests):
                    client.process.stdout.feed_data(
                        (
                            json.dumps(
                                {"id": request["id"], "result": request["method"]}
                            )
                            + "\n"
                        ).encode()
                    )

        client.send = send
        reader = asyncio.create_task(client.read_loop())
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    client.request("first", {}), client.request("second", {})
                ),
                2,
            )
            self.assertEqual(results, ["first", "second"])
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def test_background_task_marks_astrbot_as_forwarder(self):
        client = CodexClient({"sandbox": "danger-full-access"})
        client.request = AsyncMock(
            side_effect=[{"thread": {"id": "thread"}}, {"turn": {"id": "turn"}}]
        )
        await client.events.put(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "turn", "status": "completed"},
                },
            }
        )
        state, _ = await client.run(
            {"id": "job", "cwd": "", "thread_id": None, "prompt": "按用户要求整理文件"},
            Mock(),
            {},
        )
        self.assertEqual(state, "completed")
        actual_input = client.request.call_args_list[1].args[1]["input"][0]["text"]
        self.assertEqual(actual_input, forward_prompt("按用户要求整理文件"))


def plugin_module():
    """Only the AstrBot framework surface is stubbed; plugin HTTP and storage are real."""
    names = [
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.star",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.tool",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
    ]
    modules = {name: types.ModuleType(name) for name in names}
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("test")

    class Filter:
        def __getattr__(self, name):
            return lambda *a, **kw: lambda f: f

    modules["astrbot.api.event"].filter = Filter()
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageChain = Mock

    class Star:
        def __init__(self, context):
            self.context = context

    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = object
    modules["astrbot.core.agent.tool"].ToolSet = Mock
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_plugin_data_path = Mock()
    with patch.dict(sys.modules, modules):
        package = types.ModuleType("test_plugin")
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        sys.modules["test_plugin"] = package
        spec = importlib.util.spec_from_file_location(
            "test_plugin.main", Path(package.__path__[0]) / "main.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def hobby_peer(self):
        self.plugin.link.config = self.plugin.settings
        self.plugin.worker = types.SimpleNamespace(done=lambda: False)
        self.plugin.wake = asyncio.Event()
        peer = types.SimpleNamespace(
            config={"activities": ["codex"]}, origins=lambda: [self.event.unified_msg_origin],
            link=self.plugin.link,
        )
        self.plugin.context.get_registered_star.return_value = types.SimpleNamespace(
            activated=True, star_cls=peer, plugin_id="astrbot_plugin_self_amusement")
        self.plugin.context.get_config.return_value = {"plugin_set": ["*"]}
        return peer

    def start_hobby(self):
        token = self.plugin.link.claim_idle()
        return self.plugin.start_leisure_from_companion(
            self.event.unified_msg_origin, "做一个猫猫计时器", {"persona": "自然的朋友"}, token)

    async def test_leisure_jobs_use_new_isolated_target_and_distinct_provenance(self):
        self.hobby_peer()
        self.plugin.settings["chat_sessions"] = {self.event.unified_msg_origin: {"id": "human-chat"}}
        receipt = self.start_hobby()
        job = self.plugin.store.get(receipt["id"])
        self.assertEqual(job["source"], "self_amusement")
        self.assertIsNone(job["thread_id"])
        self.assertEqual(json.loads(job["target_json"])["type"], "projectless")
        self.assertEqual(job["reply_to"], self.event.unified_msg_origin)
        self.assertIn("不是用户刚刚提出的要求", forward_prompt(job["prompt"]))
        self.assertEqual(self.plugin.settings["chat_sessions"][self.event.unified_msg_origin]["id"], "human-chat")

    async def test_leisure_cooldown_and_results_survive_database_reopen(self):
        self.hobby_peer()
        receipt = self.start_hobby()
        self.plugin.store.finish(receipt["id"], "completed", "真实成果路径 cat.html")
        self.assertIsNone(self.plugin.store.next_reply())
        self.plugin.store.db.close()
        self.plugin.store = JobStore(self.plugin.data_dir)
        state = self.plugin.companion_snapshot(self.event.unified_msg_origin)
        self.assertEqual(state["leisure_result"]["result"], "真实成果路径 cat.html")
        self.assertFalse(state["can_start_leisure"])
        self.assertNotIn("leisure_result", self.plugin.companion_snapshot("other-session"))
        self.plugin.finish_leisure_from_companion("other-session", receipt["id"], "sent")
        self.assertEqual(self.plugin.store.get(receipt["id"])["delivery"], "pending")
        self.plugin.finish_leisure_from_companion(self.event.unified_msg_origin, receipt["id"], "sent")
        self.assertNotIn("leisure_result", self.plugin.companion_snapshot(self.event.unified_msg_origin))
        self.assertFalse(self.plugin.leisure_available(self.event.unified_msg_origin))
        self.plugin.settings["leisure_cooldown_minutes"] = 0
        self.assertTrue(self.plugin.leisure_available(self.event.unified_msg_origin))
        self.plugin.settings["leisure_daily_limit"] = 1
        self.assertFalse(self.plugin.leisure_available(self.event.unified_msg_origin))

    async def test_normal_work_disabling_targets_and_invalidated_idle_prevent_hobby(self):
        peer = self.hobby_peer()
        origin = self.event.unified_msg_origin
        manual = self.plugin.enqueue({"prompt": "用户工作"}, reply_to=origin)
        self.assertFalse(self.plugin.leisure_available(origin))
        self.plugin.store.finish(manual["id"], "completed", "完成")
        self.assertIsNotNone(self.plugin.store.next_reply())
        self.plugin.settings["leisure_enabled"] = False
        self.assertFalse(self.plugin.leisure_available(origin))
        self.plugin.settings["leisure_enabled"] = True
        self.assertFalse(self.plugin.leisure_available("outside-targets"))
        peer.config["activities"] = ["chat"]
        self.assertFalse(self.plugin.leisure_available(origin))
        peer.config["activities"] = ["codex"]
        token = self.plugin.link.claim_idle()
        self.plugin.link.human(origin)
        self.assertIsNone(self.plugin.start_leisure_from_companion(origin, "小东西", {}, token))

    async def test_app_server_hobby_uses_its_own_directory(self):
        self.hobby_peer()
        self.plugin.settings.update(backend="app_server", cwd=str(self.plugin.data_dir))
        receipt = self.start_hobby()
        job = self.plugin.store.get(receipt["id"])
        self.assertEqual(Path(job["cwd"]).parent, self.plugin.data_dir / "leisure")
        self.assertTrue(Path(job["cwd"]).is_dir())
        self.assertIsNone(job["thread_id"])

    async def test_hobby_delivery_reserved_before_crash_is_not_replayed(self):
        self.hobby_peer()
        receipt = self.start_hobby()
        self.plugin.store.finish(receipt["id"], "completed", "小作品")
        origin = self.event.unified_msg_origin
        self.assertTrue(self.plugin.finish_leisure_from_companion(origin, receipt["id"], "sending"))
        self.assertFalse(self.plugin.finish_leisure_from_companion(origin, receipt["id"], "sending"))
        self.plugin.store.db.close()
        self.plugin.store = JobStore(self.plugin.data_dir)
        self.assertEqual(self.plugin.store.get(receipt["id"])["delivery"], "unconfirmed")
        self.assertNotIn("leisure_result", self.plugin.companion_snapshot(origin))

    async def test_text_only_preparation_is_limited_to_owned_task_events(self):
        request = types.SimpleNamespace(system_prompt="原有人格")
        human = types.SimpleNamespace()
        forged = types.SimpleNamespace(event_kind="codex_bridge")
        internal = types.SimpleNamespace(_codex_bridge_origin=self.plugin.relay,
                                         unified_msg_origin="admin", get_extra=lambda _: None)
        with patch.object(self.module, "prepare_task_context") as prepare, \
                patch.object(self.plugin.link, "peer", return_value=None):
            await self.plugin.prepare_notice_context(human, request)
            await self.plugin.prepare_notice_context(forged, request)
            prepare.assert_not_called()
            await self.plugin.prepare_notice_context(internal, request)
            prepare.assert_called_once_with(request)
            self.assertTrue(request.system_prompt.startswith("原有人格"))
            self.assertIn("本轮未启用短期情绪联动", request.system_prompt)

    async def test_notice_policy_uses_live_mood_capabilities_without_overriding_persona(self):
        for mood, skipped, expected in (
            (None, False, "本轮未启用短期情绪联动"),
            (types.SimpleNamespace(can_decline_notice=lambda _: True), False, "并允许拒绝任务播报"),
            (types.SimpleNamespace(can_decline_notice=lambda _: False), False, "本轮未开放拒播能力"),
            (types.SimpleNamespace(), False, "本轮未开放拒播能力"),
            (types.SimpleNamespace(can_decline_notice=lambda _: True), True, "本轮未启用短期情绪联动"),
        ):
            with self.subTest(mood=mood, skipped=skipped):
                request = types.SimpleNamespace(system_prompt="原有人格与关系设定")
                event = types.SimpleNamespace(_codex_bridge_origin=self.plugin.relay,
                                              unified_msg_origin="admin",
                                              get_extra=lambda key: skipped if key == "short_term_mood_skip" else None)
                with patch.object(self.module, "prepare_task_context"), \
                        patch.object(self.plugin.link, "peer", return_value=mood):
                    await self.plugin.prepare_notice_context(event, request)
                    await self.plugin.prepare_notice_context(event, request)
                self.assertTrue(request.system_prompt.startswith("原有人格与关系设定"))
                self.assertIn("人格与拟人感优先于播报功能", request.system_prompt)
                self.assertIn(expected, request.system_prompt)
                self.assertEqual(request.system_prompt.count("<codex_task_source>"), 1)

    async def test_unloading_mood_capability_does_not_break_notice(self):
        mood = types.SimpleNamespace(can_decline_notice=Mock(side_effect=RuntimeError("unloading")))
        request = types.SimpleNamespace(system_prompt="人格")
        event = types.SimpleNamespace(_codex_bridge_origin=self.plugin.relay,
                                      unified_msg_origin="admin", get_extra=lambda _: None)
        with patch.object(self.module, "prepare_task_context"), \
                patch.object(self.plugin.link, "peer", return_value=mood):
            await self.plugin.prepare_notice_context(event, request)
        self.assertIn("本轮未开放拒播能力", request.system_prompt)

    async def test_media_is_restored_when_an_ignored_task_turn_finishes(self):
        event = Mock(_codex_bridge_origin=self.plugin.relay)
        event.get_extra.side_effect = lambda key: {"action": "ignore"} if key == "w1_notice_disposition" else None
        run_context = types.SimpleNamespace(messages=[])
        with patch.object(self.module, "restore_task_media") as restore:
            await self.plugin.companion_done(event, run_context, None)
            restore.assert_called_once_with(run_context)

    async def asyncSetUp(self):
        self.module = plugin_module()
        self.temp = tempfile.TemporaryDirectory()
        self.module.get_astrbot_plugin_data_path.return_value = self.temp.name
        self.plugin = self.module.CodexBridge(Mock())
        self.plugin.data_dir = Path(self.temp.name)
        self.plugin.settings, tokens = load_settings(self.plugin.data_dir)
        self.plugin.key, self.plugin.reverse_key = tokens
        self.plugin.store = JobStore(self.plugin.data_dir)
        self.plugin.reports = ReportStore(self.plugin.data_dir)
        self.plugin.desktop = Mock(connected=True, last_error=None)
        self.plugin.desktop.tools = AsyncMock(return_value=[])
        self.plugin.chats = ChatControl(
            self.plugin.desktop,
            self.plugin.settings,
            lambda: save_routing(self.plugin.data_dir, self.plugin.settings),
        )
        self.plugin.context.get_event_queue.return_value = asyncio.Queue()
        self.plugin.relay = AstrBotRelay(
            self.plugin.context,
            lambda context, session, payload: types.SimpleNamespace(
                session=session, payload=payload
            ),
        )
        self.plugin.monitor = Mock(
            last_scan=None, last_error=None, thread_count=0, unavailable_hosts=[]
        )
        self.event = Mock()
        self.event.is_private_chat.return_value = True
        self.event.is_admin.return_value = True
        self.event.unified_msg_origin = "wechat:FriendMessage:admin"

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.temp.cleanup()

    async def test_administrator_private_only_and_pair_scope(self):
        self.assertTrue(self.plugin.is_operator(self.event))
        self.event.is_private_chat.return_value = False
        self.assertFalse(self.plugin.is_operator(self.event))
        self.event.is_private_chat.return_value = True
        self.event.is_admin.return_value = False
        self.event.get_platform_name.return_value = "wechat_native"
        self.event.get_platform_id.return_value = "other"
        self.event.get_sender_id.return_value = "paired"
        self.plugin.settings.update(
            wechat_operators=["paired"], wechat_platform_id="wechat"
        )
        self.assertFalse(self.plugin.is_operator(self.event))
        self.event.get_platform_id.return_value = "wechat"
        self.assertTrue(self.plugin.is_operator(self.event))

    async def test_callback_token_cannot_control_app_or_read_reports(self):
        from aiohttp import web

        app = web.Application(middlewares=[self.plugin.authorize])
        app.add_routes(
            [
                web.get("/status", self.plugin.status),
                web.get("/app/tools", self.plugin.app_tools),
                web.get("/reports", self.plugin.report_list),
            ]
        )
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get("/status")).status, 401)
            callback = {"Authorization": "Bearer " + self.plugin.reverse_key}
            self.assertEqual(
                (await client.get("/status", headers=callback)).status, 200
            )
            self.assertEqual(
                (await client.get("/app/tools", headers=callback)).status, 403
            )
            self.assertEqual(
                (await client.get("/reports", headers=callback)).status, 403
            )
            operator = {"Authorization": "Bearer " + self.plugin.key}
            self.assertEqual(
                (await client.get("/app/tools", headers=operator)).status, 200
            )
            self.assertEqual(
                (await client.get("/reports?after=-1", headers=operator)).status, 400
            )

    async def test_unauthorized_tool_never_reaches_desktop(self):
        self.event.is_private_chat.return_value = False
        result = await self.plugin.app_control(
            self.event, "delete_thread", '{"threadId":"human"}'
        )
        self.assertIn("authenticated", result)
        self.plugin.desktop.call.assert_not_called()

    async def test_desktop_enqueue_ignores_legacy_default_cwd(self):
        self.plugin.settings["cwd"] = "D:/legacy-default"
        self.plugin.wake = asyncio.Event()
        receipt = self.plugin.enqueue({"prompt": "Task"})
        self.assertEqual(self.plugin.store.get(receipt["id"])["cwd"], "")
        receipt = self.plugin.enqueue(
            {"prompt": "Task", "run_at": "2026-12-01T10:00:00"}
        )
        self.assertEqual(receipt["state"], "queued")

    async def test_select_by_name_then_send_uses_remote_chat_and_binds_reports(self):
        self.plugin.wake = asyncio.Event()
        self.plugin.desktop.all_threads = AsyncMock(
            return_value={
                "threads": [
                    {"id": "outside-chat", "title": "修复登录", "hostId": "remote"}
                ]
            }
        )
        await self.plugin.chat_action(self.event, "use", "修复登录")
        result = await self.plugin.chat_action(self.event, "send", text="继续补充测试")
        job = self.plugin.store.get(result["id"])
        self.assertEqual(job["thread_id"], "outside-chat")
        self.assertEqual(job["host_id"], "remote")
        self.assertEqual(
            self.plugin.settings["notification_session"], self.event.unified_msg_origin
        )
        self.assertEqual(job["owned"], 0)

    async def test_new_task_does_not_change_selected_chat(self):
        self.plugin.wake = asyncio.Event()
        self.plugin.chats.select(
            self.event.unified_msg_origin, {"id": "old", "title": "旧聊天"}
        )
        result = await self.plugin.chat_action(self.event, "new", text="新的工作")
        self.assertEqual(
            self.plugin.chats.selected(self.event.unified_msg_origin)["id"], "old"
        )
        self.plugin.desktop.call = AsyncMock(return_value={"threadId": "new-chat"})
        worker = asyncio.create_task(self.plugin.run_jobs())
        try:
            for _ in range(30):
                if self.plugin.store.get(result["id"])["state"] == "dispatched":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(
                self.plugin.chats.selected(self.event.unified_msg_origin)["id"],
                "old",
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_read_progress_is_human_readable_and_existing_route_is_kept(self):
        self.plugin.settings["notification_session"] = "previous-admin"
        self.plugin.chats.select(
            self.event.unified_msg_origin, {"id": "human", "title": "当前项目"}
        )
        self.plugin.desktop.call = AsyncMock(
            return_value={
                "thread": {
                    "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]}
                },
                "turns": [
                    {
                        "id": "turn",
                        "status": "inProgress",
                        "items": [{"type": "agentMessage", "text": "选择哪个方案？"}],
                    }
                ],
            }
        )
        result = await self.plugin.chat_action(self.event, "read")
        self.assertIn("等待回复", result["message"])
        self.assertIn("选择哪个方案", result["message"])
        self.assertEqual(self.plugin.settings["notification_session"], "previous-admin")

    async def test_desktop_dispatch_and_reconcile_completed_turn(self):
        self.plugin.wake = asyncio.Event()
        thread_id = "00000000-0000-4000-8000-000000000001"
        receipt = self.plugin.enqueue({"prompt": "Complete test"}, reply_to="admin")
        self.plugin.desktop.call = AsyncMock(
            return_value={"threadId": thread_id, "hostId": "local"}
        )
        worker = asyncio.create_task(self.plugin.run_jobs())
        try:
            for _ in range(30):
                if self.plugin.store.get(receipt["id"])["state"] == "dispatched":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(
                self.plugin.store.get(receipt["id"])["state"], "dispatched"
            )
            self.assertIn(thread_id, self.plugin.store.owned_threads())
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.plugin.desktop.call = AsyncMock(
            return_value={
                "thread": {"id": thread_id},
                "turns": [
                    {
                        "id": "turn",
                        "status": "completed",
                        "items": [
                            {
                                "type": "userMessage",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": forward_prompt("Complete test"),
                                    }
                                ],
                            },
                            {
                                "type": "agentMessage",
                                "phase": "commentary",
                                "text": "Working",
                            },
                            {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": "Finished",
                            },
                        ],
                    }
                ],
            }
        )
        reconcile = asyncio.create_task(self.plugin.reconcile_jobs())
        try:
            for _ in range(30):
                if self.plugin.store.get(receipt["id"])["state"] == "completed":
                    break
                await asyncio.sleep(0.01)
            job = self.plugin.store.get(receipt["id"])
            self.assertEqual(job["state"], "completed")
            self.assertEqual(job["result"], "Finished")
            self.assertEqual(job["turn_id"], "turn")
        finally:
            reconcile.cancel()
            await asyncio.gather(reconcile, return_exceptions=True)

    async def test_completed_job_is_handed_to_astrbot_with_original_result(self):
        job_id = self.plugin.store.add("用户的任务", "", None, 0, "admin")
        self.plugin.store.finish(job_id, "completed", "## 原始结果\n详情完整保留")
        await self.plugin.deliver(self.plugin.store.get(job_id))
        event = self.plugin.context.get_event_queue().get_nowait()
        self.assertEqual(event.payload["data"]["result"], "## 原始结果\n详情完整保留")
        self.assertEqual(self.plugin.store.get(job_id)["delivery"], "handed_off")
        self.plugin.context.llm_generate.assert_not_called()
        self.plugin.context.send_message.assert_not_called()

    async def test_normal_request_prompt_is_untouched_and_no_provider_is_called(self):
        request = types.SimpleNamespace(
            func_tool=Mock(), prompt="原话", system_prompt="原有人设", contexts=[]
        )
        await self.plugin.scope_tools(self.event, request)
        self.assertEqual(request.system_prompt, "原有人设")
        self.assertEqual(request.prompt, "原话")
        self.assertEqual(request.contexts, [])
        self.plugin.context.llm_generate.assert_not_called()

    async def test_callback_is_stored_and_accepted_without_sending(self):
        from aiohttp import web

        self.plugin.settings["notification_session"] = "admin"
        app = web.Application(middlewares=[self.plugin.authorize])
        app.add_routes([web.post("/notify", self.plugin.notify)])
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/notify",
                json={"message": "Codex 返回的原始消息"},
                headers={"Authorization": "Bearer " + self.plugin.reverse_key},
            )
            self.assertEqual(response.status, 202)
            receipt = await response.json()
            self.assertTrue(receipt["accepted_by_bridge"])
            self.assertNotIn("confirmed_by_adapter", receipt)
        payload = json.loads(self.plugin.reports.pending()["payload"])
        self.assertEqual(payload["content"], "Codex 返回的原始消息")
        self.plugin.context.send_message.assert_not_called()
        self.plugin.context.llm_generate.assert_not_called()

    async def test_uncertain_desktop_write_is_not_resubmitted(self):
        self.plugin.wake = asyncio.Event()
        receipt = self.plugin.enqueue({"prompt": "Uncertain test"})
        self.plugin.desktop.call = AsyncMock(
            side_effect=TimeoutError("Unknown outcome")
        )
        worker = asyncio.create_task(self.plugin.run_jobs())
        try:
            for _ in range(30):
                if self.plugin.store.get(receipt["id"])["state"] == "unconfirmed":
                    break
                await asyncio.sleep(0.01)
            self.plugin.desktop.call.assert_awaited_once()
            self.assertEqual(
                self.plugin.store.get(receipt["id"])["state"], "unconfirmed"
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_initialize_creates_storage_and_shutdown_releases_http(self):
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.plugin.panel_config = {"bridge_port": port, "monitor_enabled": False}
        # Replace setup storage with initialize's actual data directory lifecycle.
        await self.plugin.terminate()
        await self.plugin.initialize()
        self.assertTrue(self.plugin.worker and not self.plugin.worker.done())
        import aiohttp

        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": "Bearer " + self.plugin.key}
            async with session.get(
                f"http://127.0.0.1:{port}/status", headers=headers
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["backend"], "desktop")


if __name__ == "__main__":
    unittest.main()
