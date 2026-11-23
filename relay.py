"""Hand task events to AstrBot's normal event pipeline, without generating speech."""

try:
    from .protocol import event_payload, render_event
    from .interop import notice_ignored
except ImportError:
    from protocol import event_payload, render_event
    from interop import notice_ignored


def quiet_notice_tool_message(message):
    if getattr(message, "type", "") != "tool_call":
        return False
    for part in getattr(message, "chain", []):
        if "mood_ignore_codex_notice" in str(getattr(part, "text", "")):
            return True
        data = getattr(part, "data", None)
        if isinstance(data, dict) and data.get("name") == "mood_ignore_codex_notice":
            return True
    return False


def make_event(context, session, payload):
    # AstrBot's own background-event carrier supplies the normal response
    # transport. We do not invoke a model or send a message from this module.
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.platform.message_session import MessageSession

    class CodexTaskEvent(CronMessageEvent):
        async def send(self, message):
            if notice_ignored(self) or quiet_notice_tool_message(message):
                return
            await super().send(message)

    target = MessageSession.from_str(session)
    text = render_event(payload)
    event = CodexTaskEvent(
        context=context,
        session=target,
        message=text,
        sender_name="Codex Bridge",
        extras={"codex_bridge": payload, "w1_event": {
            "version": 1, "source": "codex_bridge", "type": "task_event",
            "is_user_message": False, "counts_as_user_activity": False,
            "recipient": "astrbot", "visibility": "internal", "user_has_seen_report": False,
            "event_id": payload["event_id"], "task_kind": payload["kind"],
        }},
        message_type=target.message_type,
    )
    event.message_obj.sender.user_id = "codex_bridge"
    event.message_obj.raw_message = payload
    event.message_obj.message_id = "proactive:codex_bridge:" + payload["event_id"]
    event.platform_meta.name = "codex_bridge"
    event.platform_meta.description = "Codex task event"
    event.event_kind = "codex_bridge"
    return event


class AstrBotRelay:
    def __init__(self, context, factory=None):
        self.context = context
        self.factory = factory or make_event
        self.last_error = None

    def owns(self, event):
        return getattr(event, "_codex_bridge_origin", None) is self

    async def publish(self, session, facts, event_id):
        try:
            payload = event_payload(event_id, facts)
            event = self.factory(self.context, session, payload)
            event._codex_bridge_origin = self
            await self.context.get_event_queue().put(event)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__
            raise
