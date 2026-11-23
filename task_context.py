"""Keep textual task notices independent of old conversation media downloads."""

MEDIA_TYPES = {"image_url": "图片", "audio_url": "音频"}
ORIGINAL_MEDIA = "_codex_bridge_original_media"


def part_type(part):
    return part.get("type") if isinstance(part, dict) else getattr(part, "type", None)


def prepare_task_context(request):
    # Copy on write: requests can share lists with the stored conversation or
    # another plugin. Keep text, tool calls, checkpoints and their ordering.
    from astrbot.core.agent.message import ContentPart, TextPart

    contexts = []
    for message in request.contexts or []:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            contexts.append(message)
            continue
        content = []
        for part in message["content"]:
            kind = part_type(part)
            if kind in MEDIA_TYPES:
                placeholder = TextPart(text=f"[{MEDIA_TYPES[kind]}附件：本轮后台任务播报未加载]")
                # AstrBot binds ContentPart instances without recreating them.
                # This private attribute survives model_copy/deepcopy but is
                # never serialized into the provider payload or history.
                setattr(placeholder, ORIGINAL_MEDIA, ContentPart.model_validate(part))
                content.append(placeholder)
            else:
                content.append(part)
        contexts.append({**message, "content": content})
    request.contexts = contexts
    request.image_urls = []
    request.audio_urls = []
    request.extra_user_content_parts = [
        part for part in (request.extra_user_content_parts or [])
        if part_type(part) not in MEDIA_TYPES
    ]


def restore_task_media(run_context):
    # on_agent_done runs before AstrBot persists runner messages. Restore any
    # retained historical media so a task notice cannot erase user attachments.
    for message in getattr(run_context, "messages", []):
        content = getattr(message, "content", None)
        if isinstance(content, list):
            message.content = [getattr(part, ORIGINAL_MEDIA, part) for part in content]
