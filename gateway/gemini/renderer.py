from __future__ import annotations

from gateway.gemini.parser import StreamEvent
from gateway.user_settings import DEFAULT_RENDER_MODE

_PREVIEW_LIMIT = 280


def _preview(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _tool_name(tool_name: str) -> str:
    normalized = tool_name.strip()
    if not normalized or normalized.lower() == "unknown":
        return ""
    return normalized


def render_event(event: StreamEvent, render_mode: str = DEFAULT_RENDER_MODE) -> str:
    mode = render_mode if render_mode in {"compact", "summary", "detailed"} else DEFAULT_RENDER_MODE

    if event.event_type == "assistant_text":
        return event.assistant_text

    if event.event_type == "heartbeat":
        return f"\n⏳ {event.status_message}"

    if event.event_type == "warning":
        return f"\n\n⚠️ {event.warning_message}"

    if event.event_type == "error":
        return f"\n\n⚠️ Ошибка: {event.error_message}"

    if event.event_type == "invalid_stream":
        return f"\n\n⚠️ Проблема со стримом: {event.invalid_stream_reason}"

    if event.event_type == "tool_use":
        if mode == "compact":
            return ""
        tool_name = _tool_name(event.tool_name) or "внутренний шаг"
        if mode == "summary":
            return f"\n\n🛠 Инструмент: {tool_name}"
        if not event.tool_args_preview:
            return f"\n\n🛠 Инструмент: {tool_name}"
        return (
            f"\n\n🛠 Инструмент: {tool_name}\n"
            f"Параметры: {_preview(event.tool_args_preview)}"
        )

    if event.event_type == "tool_result":
        if mode == "compact":
            return ""
        if mode == "summary":
            if not event.tool_output_preview:
                return ""
            return "\n📋 Результат инструмента получен."
        if not event.tool_output_preview:
            return ""
        return f"\n📋 Результат: {_preview(event.tool_output_preview, limit=500)}"

    if event.event_type == "result_stats":
        if event.total_tokens or event.duration_ms:
            return f"\n\n📊 Токены: {event.total_tokens} · {event.duration_ms / 1000:.1f}с"
        return ""

    return ""
