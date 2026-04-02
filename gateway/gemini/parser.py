import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Compiled once at module level for performance
_ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


@dataclass
class StreamEvent:
    text_chunk: str = ""
    is_done: bool = False
    approval_request: Optional[dict] = None
    created_file: Optional[str] = None
    session_id: Optional[str] = None
    event_type: str = ""


class GeminiStreamParser:
    """Парсер для --output-format stream-json Gemini CLI.

    Формат вывода (JSONL):
      {"type":"init","session_id":"...","model":"..."}
      {"type":"message","role":"user","content":"..."}
      {"type":"message","role":"assistant","content":"...","delta":true}
      {"type":"tool_use","name":"bash","args":{"command":"ls"}}
      {"type":"tool_result","output":"..."}
      {"type":"result","status":"success","stats":{...}}
      {"type":"error","message":"..."}
    """

    @staticmethod
    def parse_line(line: str) -> StreamEvent:
        """Парсит одну строку stream-json."""
        if not line or not line.strip():
            return StreamEvent()

        # Очистка ANSI escape-кодов
        clean_line = _ANSI_ESCAPE_RE.sub('', line).strip()
        if not clean_line:
            return StreamEvent()

        try:
            data = json.loads(clean_line)
        except json.JSONDecodeError:
            logger.debug(f"[RAW NON-JSON] {clean_line!r}")
            return StreamEvent()

        event_type = data.get("type", "")
        event = StreamEvent(event_type=event_type)

        # === init: метаданные сессии ===
        if event_type == "init":
            event.session_id = data.get("session_id")
            logger.info(
                f"Gemini session init: id={event.session_id}, "
                f"model={data.get('model')}"
            )
            return event

        # === message: текст от ассистента ===
        if event_type == "message":
            role = data.get("role", "")
            content = data.get("content", "")

            if role == "assistant" and content:
                event.text_chunk = content
            # Сообщения от user — пропускаем (это эхо промпта)
            return event

        # === tool_use: вызов инструмента ===
        if event_type == "tool_use":
            tool_name = data.get("name", "unknown")
            args = data.get("args", {})
            # Показываем пользователю что Gemini вызывает инструмент
            args_preview = json.dumps(args, ensure_ascii=False)
            if len(args_preview) > 200:
                args_preview = args_preview[:200] + "..."
            event.text_chunk = f"\n\n🔧 <b>Инструмент:</b> <code>{tool_name}</code>\n<pre>{args_preview}</pre>\n"
            return event

        # === tool_result: результат инструмента ===
        if event_type == "tool_result":
            output = data.get("output", "")
            if output:
                preview = output[:500] + "..." if len(output) > 500 else output
                event.text_chunk = f"\n📋 <i>Результат:</i>\n<pre>{preview}</pre>\n"
            return event

        # === result: завершение ===
        if event_type == "result":
            event.is_done = True
            stats = data.get("stats", {})
            total = stats.get("total_tokens", 0)
            duration = stats.get("duration_ms", 0)
            if total or duration:
                event.text_chunk = (
                    f"\n\n📊 <i>Токены: {total} | "
                    f"Время: {duration / 1000:.1f}с</i>"
                )
            return event

        # === error: ошибка ===
        if event_type == "error":
            error_msg = data.get("message", "Неизвестная ошибка")
            event.text_chunk = f"\n\n⚠️ <b>Ошибка:</b> {error_msg}"
            return event

        # Неизвестный тип — логируем для отладки
        logger.debug(f"[UNKNOWN EVENT] type={event_type}, data={data}")
        return event
