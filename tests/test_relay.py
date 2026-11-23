import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, Mock

from protocol import (
    bridge_result,
    event_payload,
    forward_arguments,
    forward_prompt,
    original_prompt,
)
from relay import AstrBotRelay


class ProtocolTests(unittest.TestCase):
    def test_both_directions_identify_source_without_changing_task_content(self):
        content = '修复登录。\n用户附件里有 "引号" 和代码，不要丢失。'
        forwarded = forward_prompt(content)
        self.assertIn("AstrBot 代表用户", forwarded)
        self.assertEqual(original_prompt(forwarded), content)
        self.assertEqual(forward_prompt(forwarded), forwarded)
        incoming = bridge_result({"state": "completed", "result": "## 完整结果\n内容"})
        self.assertEqual(incoming["result"], "## 完整结果\n内容")
        self.assertIn("原有人格", incoming["_bridge"]["response"])
        self.assertIn("不逐字", incoming["_bridge"]["response"])

    def test_raw_desktop_calls_and_host_proxy_wrap_once(self):
        original = {"threadId": "existing", "prompt": "继续工作"}
        once = forward_arguments("send_message_to_thread", original)
        twice = forward_arguments("send_message_to_thread", once)
        self.assertEqual(once, twice)
        self.assertEqual(original["prompt"], "继续工作")
        self.assertEqual(original_prompt(twice["prompt"]), original["prompt"])
        self.assertEqual(
            forward_arguments("read_thread", {"threadId": "existing"}),
            {"threadId": "existing"},
        )

    def test_scheduled_tasks_and_handoff_followups_also_have_source_note(self):
        for name, field in (
            ("create_thread", "prompt"),
            ("automation_update", "prompt"),
            ("handoff_thread", "followUpPrompt"),
        ):
            outgoing = forward_arguments(name, {field: "用户要求的工作"})
            self.assertIn("AstrBot", outgoing[field])
            self.assertEqual(original_prompt(outgoing[field]), "用户要求的工作")


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_uses_astrbot_queue_without_model_or_direct_send(self):
        context = Mock()
        queue = asyncio.Queue()
        context.get_event_queue.return_value = queue
        relay = AstrBotRelay(
            context,
            lambda ctx, session, payload: types.SimpleNamespace(
                session=session, payload=payload
            ),
        )
        facts = {"kind": "task_result", "state": "completed", "result": "原始结果"}
        self.assertTrue(
            await relay.publish("wx:FriendMessage:admin", facts, "task-one")
        )
        event = queue.get_nowait()
        self.assertTrue(relay.owns(event))
        self.assertEqual(event.payload, event_payload("task-one", facts))
        self.assertEqual(event.payload["data"]["result"], "原始结果")
        self.assertEqual(event.session, "wx:FriendMessage:admin")
        context.llm_generate.assert_not_called()
        context.send_message.assert_not_called()
        self.assertFalse(relay.owns(types.SimpleNamespace(raw_message=event.payload)))

    async def test_queue_failure_is_reported_without_fallback_speech(self):
        context = Mock()
        context.get_event_queue.return_value.put = AsyncMock(
            side_effect=RuntimeError("queue unavailable")
        )
        relay = AstrBotRelay(context, lambda *args: types.SimpleNamespace())
        with self.assertRaises(RuntimeError):
            await relay.publish("wx:FriendMessage:admin", {"state": "failed"}, "failed")
        self.assertEqual(relay.last_error, "RuntimeError")
        context.llm_generate.assert_not_called()
        context.send_message.assert_not_called()

    async def test_envelope_is_background_data_with_no_user_impersonation(self):
        payload = event_payload(
            "id", {"kind": "task_progress", "progress": {"turn_status": "inProgress"}}
        )
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("不是用户新发来的消息", encoded)
        self.assertIn("由 AstrBot 决定", encoded)
        self.assertEqual(payload["kind"], "task_progress")
