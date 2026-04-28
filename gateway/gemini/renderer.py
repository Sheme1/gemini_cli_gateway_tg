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
    renderer = {
        "assistant_text": _render_assistant_text,
        "heartbeat": _render_heartbeat,
        "warning": _render_warning,
        "error": _render_error,
        "invalid_stream": _render_invalid_stream,
        "tool_use": lambda item: _render_tool_use(item, mode),
        "tool_result": lambda item: _render_tool_result(item, mode),
        "result_stats": _render_result_stats,
    }.get(event.event_type)
    return renderer(event) if renderer else ""


def _render_assistant_text(event: StreamEvent) -> str:
    return event.assistant_text


def _render_heartbeat(event: StreamEvent) -> str:
    return f"\n⏳ {event.status_message}"


def _render_warning(event: StreamEvent) -> str:
    return f"\n\n⚠️ {event.warning_message}"


def _render_error(event: StreamEvent) -> str:
    return f"\n\n⚠️ Ошибка: {event.error_message}"


def _render_invalid_stream(event: StreamEvent) -> str:
    return f"\n\n⚠️ Проблема со стримом: {event.invalid_stream_reason}"


def _render_tool_use(event: StreamEvent, mode: str) -> str:
    tool_name = _tool_progress_name(event.tool_name)
    if mode == "compact":
        return f"\n🛠 Выполняю: {tool_name}"
    if mode == "summary" or not event.tool_args_preview:
        return f"\n\n🛠 Инструмент: {tool_name}"
    return (
        f"\n\n🛠 Инструмент: {tool_name}\nПараметры: {_preview(event.tool_args_preview)}"
    )


def _render_tool_result(event: StreamEvent, mode: str) -> str:
    tool_name = _tool_progress_name(event.tool_name)
    is_error = event.tool_status == "error"
    if mode == "compact":
        prefix = "⚠️ Ошибка инструмента" if is_error else "📋 Готово"
        return f"\n{prefix}: {tool_name}"
    if mode == "summary":
        return _render_summary_tool_result(tool_name, is_error)
    if is_error and not event.tool_output_preview:
        return f"\n⚠️ Ошибка инструмента: {tool_name}"
    if not event.tool_output_preview:
        return f"\n📋 Результат: {tool_name}"
    prefix = "⚠️ Ошибка" if is_error else "📋 Результат"
    return (
        f"\n{prefix}: {tool_name}\n"
        f"Детали: {_preview(event.tool_output_preview, limit=500)}"
    )


def _render_summary_tool_result(tool_name: str, is_error: bool) -> str:
    if is_error:
        return f"\n⚠️ Ошибка инструмента: {tool_name}"
    return f"\n📋 Результат: {tool_name}"


def _render_result_stats(event: StreamEvent) -> str:
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
