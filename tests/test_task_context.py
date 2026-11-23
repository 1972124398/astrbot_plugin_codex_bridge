import copy
import sys
import types
import unittest
from unittest.mock import patch

from task_context import prepare_task_context, restore_task_media


class Part(types.SimpleNamespace):
    @classmethod
    def model_validate(cls, value):
        return cls(**value) if isinstance(value, dict) else value


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        framework = types.ModuleType("astrbot.core.agent.message")
        framework.ContentPart = Part
        framework.TextPart = lambda **kwargs: Part(type="text", **kwargs)
        self.framework = patch.dict(sys.modules, {framework.__name__: framework})
        self.framework.start()
        self.addCleanup(self.framework.stop)

    def test_history_keeps_text_tools_checkpoints_and_order_without_media(self):
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "请看这张图"},
                {"type": "image_url", "image_url": {"url": "https://expired/image"}},
                {"type": "audio_url", "audio_url": {"url": "https://expired/audio"}},
            ]},
            {"role": "_checkpoint", "content": {"id": "checkpoint"}},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call"}]},
            {"role": "tool", "tool_call_id": "call", "content": "实际结果"},
        ]
        original = copy.deepcopy(history)
        text = Part(type="text", text="临时人格提示")
        extras = [text, Part(type="image_url", image_url={"url": "https://expired/current"})]
        request = types.SimpleNamespace(contexts=history, image_urls=["old"], audio_urls=["old"],
                                        extra_user_content_parts=extras, prompt="任务报告")
        prepare_task_context(request)
        self.assertEqual(history, original)
        self.assertEqual(len(extras), 2)
        self.assertEqual(request.contexts[1:], history[1:])
        parts = request.contexts[0]["content"]
        self.assertEqual(parts[0], history[0]["content"][0])
        self.assertEqual([part.type for part in parts[1:]], ["text", "text"])
        self.assertEqual(request.image_urls, [])
        self.assertEqual(request.audio_urls, [])
        self.assertEqual(request.extra_user_content_parts, [text])
        self.assertEqual(request.prompt, "任务报告")
        # AstrBot binds typed ContentPart objects, then saves runner messages.
        run_context = types.SimpleNamespace(messages=[Part(content=parts)])
        restore_task_media(run_context)
        restored = run_context.messages[0].content
        self.assertEqual(restored[1].image_url, original[0]["content"][1]["image_url"])
        self.assertEqual(restored[2].audio_url, original[0]["content"][2]["audio_url"])

    def test_restore_survives_copying_and_repeated_preparation(self):
        media = Part(type="image_url", image_url={"url": "data:image/png;base64,AA=="})
        request = types.SimpleNamespace(contexts=[{"role": "user", "content": [media]}],
                                        extra_user_content_parts=[], image_urls=[], audio_urls=[])
        prepare_task_context(request)
        prepare_task_context(request)
        run_context = types.SimpleNamespace(messages=[Part(content=copy.deepcopy(request.contexts[0]["content"]))])
        restore_task_media(run_context)
        restore_task_media(run_context)
        self.assertEqual(run_context.messages[0].content[0], media)


class AstrBotPersistenceTests(unittest.TestCase):
    def test_real_framework_binds_and_restores_media_before_saving(self):
        try:
            from astrbot.core.agent.message import (
                bind_checkpoint_messages, dump_messages_with_checkpoints,
            )
        except ImportError:
            self.skipTest("AstrBot runtime is optional in standalone CI")
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "图片问题"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "id": None}},
                {"type": "audio_url", "audio_url": {"url": "https://expired/audio", "id": None}},
            ]},
            {"role": "_checkpoint", "content": {"id": "saved"}},
        ]
        request = types.SimpleNamespace(contexts=history, extra_user_content_parts=[],
                                        image_urls=[], audio_urls=[])
        prepare_task_context(request)
        messages = bind_checkpoint_messages(request.contexts)
        provider_payload = [message.model_dump() for message in messages]
        self.assertNotIn("image_url", str(provider_payload))
        self.assertNotIn("audio_url", str(provider_payload))
        restore_task_media(types.SimpleNamespace(messages=messages))
        self.assertEqual(dump_messages_with_checkpoints(messages), history)


if __name__ == "__main__":
    unittest.main()
