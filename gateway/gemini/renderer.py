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


def _tool_progress_name(tool_name: str) -> str:
    return _tool_name(tool_name) or "внутренний шаг"


def render_event(event: StreamEvent, render_mode: str = DEFAULT_RENDER_MODE) -> str:
    mode = (
        render_mode
        if render_mode in {"compact", "summary", "detailed"}
        else DEFAULT_RENDER_MODE
    )

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
        tool_name = _tool_progress_name(event.tool_name)
        if mode == "compact":
            return f"\n🛠 Выполняю: {tool_name}"
        if mode == "summary":
            return f"\n\n🛠 Инструмент: {tool_name}"
        if not event.tool_args_preview:
            return f"\n\n🛠 Инструмент: {tool_name}"
        return (
            f"\n\n🛠 Инструмент: {tool_name}\n"
            f"Параметры: {_preview(event.tool_args_preview)}"
        )

    if event.event_type == "tool_result":
        tool_name = _tool_progress_name(event.tool_name)
        is_error = event.tool_status == "error"
        if mode == "compact":
            prefix = "⚠️ Ошибка инструмента" if is_error else "📋 Готово"
            return f"\n{prefix}: {tool_name}"
        if mode == "summary":
            if is_error:
                return f"\n⚠️ Ошибка инструмента: {tool_name}"
            return f"\n📋 Результат: {tool_name}"
        if is_error and not event.tool_output_preview:
            return f"\n⚠️ Ошибка инструмента: {tool_name}"
        if not event.tool_output_preview:
            return f"\n📋 Результат: {tool_name}"
        prefix = "⚠️ Ошибка" if is_error else "📋 Результат"
        return (
            f"\n{prefix}: {tool_name}\n"
            f"Детали: {_preview(event.tool_output_preview, limit=500)}"
        )

    if event.event_type == "result_stats":
        parts: list[str] = []
        if event.total_tokens:
            parts.append(f"токены: {event.total_tokens}")
        if event.thoughts_tokens:
            parts.append(f"thinking: {event.thoughts_tokens}")
        if event.duration_ms:
            parts.append(f"{event.duration_ms / 1000:.1f}с")
        if event.result_status and event.result_status not in {"success", "ok"}:
            parts.append(f"status: {event.result_status}")
        if parts:
            return "\n\n📊 " + " · ".join(parts)
        return ""

    return ""
