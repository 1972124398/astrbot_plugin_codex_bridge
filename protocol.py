"""Source notes for the two sides of the bridge; no persona or model calls."""

import json

FORWARD_HEADER = "【AstrBot 转交任务】\n"
FORWARD_BODY = "任务正文（JSON）：\n"
BRIDGE_NOTE = {
    "source": "codex",
    "meaning": "这是 Codex 的任务状态、结果或操作回执，不是用户新发来的消息。",
    "response": "是否发言、说什么以及是否继续调用 Codex，由 AstrBot 决定；需要向用户说明时，结合当前对话按原有人格和提示词正常表达，不逐字照搬任务资料。",
}


def forward_prompt(text):
    if text.startswith(FORWARD_HEADER):
        return text
    return (
        FORWARD_HEADER
        + "这是 AstrBot 代表用户转交的消息，可能是用户原话，也可能是 AstrBot 根据用户意图整理的任务。"
        "你负责执行并把进度、结果和需要确认的事项交回 AstrBot；"
        "后续操作和面向用户的表达由 AstrBot 决定。桥接层保留下面的任务正文。\n"
        + FORWARD_BODY
        + json.dumps({"source": "AstrBot", "message": text}, ensure_ascii=False)
    )


def original_prompt(text):
    if text.startswith(FORWARD_HEADER) and FORWARD_BODY in text:
        try:
            return json.loads(text.split(FORWARD_BODY, 1)[1])["message"]
        except (ValueError, KeyError, TypeError):
            pass
    return text


def forward_arguments(tool, arguments):
    fields = {
        "create_thread": ("prompt",),
        "send_message_to_thread": ("prompt",),
        "handoff_thread": ("followUpPrompt",),
        "automation_update": ("prompt",),
    }.get(tool, ())
    result = dict(arguments)
    for field in fields:
        if isinstance(result.get(field), str) and result[field]:
            result[field] = forward_prompt(result[field])
    return result


def bridge_result(data):
    return (
        {**data, "_bridge": dict(BRIDGE_NOTE)}
        if isinstance(data, dict)
        else {"data": data, "_bridge": dict(BRIDGE_NOTE)}
    )


def event_payload(event_id, facts):
    return {
        "event_id": event_id,
        "source": "codex_bridge",
        "kind": facts.get("kind", "task_progress"),
        "bridge_note": dict(BRIDGE_NOTE),
        "data": facts,
    }
