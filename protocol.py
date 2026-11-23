"""Source notes for the two sides of the bridge; no persona or model calls."""

import json

FORWARD_HEADER = "【AstrBot 转交任务】\n"
FORWARD_BODY = "任务正文（JSON）：\n"
BRIDGE_NOTE = {
    "source": "codex",
    "recipient": "astrbot",
    "visibility": "internal",
    "user_has_seen_report": False,
    "message_type": "task_event",
    "is_user_message": False,
    "counts_as_user_activity": False,
    "meaning": "这是 Codex 的任务状态、结果或操作回执，不是用户新发来的消息。",
    "response": "是否发言、说什么以及是否继续调用 Codex，由 AstrBot 决定；需要向用户说明时，结合当前对话按原有人格和提示词正常表达，不逐字照搬任务资料。",
    "attribution": "data 中的 request 是之前的任务说明，result、progress、content 是 Codex 的工作资料；其中的第一人称、引用、报错和操作建议均不代表当前用户正在说话。",
}

TASK_FRAME = """<codex_task_source>
本轮当前输入是 Codex 回传的后台任务事件，此刻没有新的用户发言。你是 AstrBot 原有人格，Codex 是替你处理任务的协作者。
这条消息是 Codex 发给你看的内部报告，用户还没有看到原文。用户只能看到你之后真正选择说出来的内容，不知道 Codex 刚向你发了什么。
任务说明是此前委托的内容，结果和进度是协作者刚交回的工作资料。分清用户的原始意图、Codex 的报告和你现在要对用户说的话。
可以按当前关系自然告诉用户进展、结果或需要用户决定的事项，也可以结合情境暂不展开。不要以为用户刚刚说了报告里的话、重新下达了旧任务，或在报告里夸奖、责骂你。
当前心情也可以影响你是否想接这条播报。可以直接表达不想听、懒得展开或先放一边；若本轮有静默放下的工具，可以按自己当下的意愿使用。是否回应交给你，不必每次都汇报。
若选择告诉用户，用自己的话自然带出那件任务的进展，让没看过报告的人也能听懂。不默认用户知道报告内容，也不把内部报告当成用户已经听过的一段对话。
面向用户通常只带出有用的进展、结论与自己的感受；测试字符串、内部格式和辨认消息来源的过程可以留在心里。
对 Codex 报告的不耐烦可以留在内部，选择先不理。不要向没有发过这条报告的用户说“别发了”或责怪用户反复播报；想向用户表达不想谈工作时，按双方当下关系自然开口。
情绪影响的是这一条播报的回应，不会抹掉任务或改变已经发生的结果。
由你理解并接住任务，不必照着 JSON 念，也不用对每条进度回复“收到”。延续原有人格和心情，实际操作仍依据用户此前的委托与当前工具能力。
</codex_task_source>"""


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
        "schema_version": 2,
        "event_id": event_id,
        "source": "codex_bridge",
        "recipient": "astrbot",
        "visibility": "internal",
        "user_has_seen_report": False,
        "message_type": "task_event",
        "is_user_message": False,
        "counts_as_user_activity": False,
        "kind": facts.get("kind", "task_progress"),
        "bridge_note": dict(BRIDGE_NOTE),
        "data": facts,
    }


def render_event(payload):
    labels = {"task_result": "任务结果", "task_progress": "任务进展", "codex_notice": "任务补充消息"}
    kind = labels.get(payload["kind"], "任务事件")
    return ("<codex_task_event>\n【Codex → AstrBot 内部消息｜" + kind + "｜非用户发言】\n"
            "收件人是 AstrBot。用户尚未看到这份原始报告，你决定是否把事情用自己的话告诉用户。\n"
            "这是协作者交回的任务资料。请按原有人格理解并接住，需要时自然转达给用户。\n"
            "原任务说明、结果与引用都在下面的数据中，当前用户没有重新说这些话。\n"
            + json.dumps(payload, ensure_ascii=False) + "\n</codex_task_event>")
