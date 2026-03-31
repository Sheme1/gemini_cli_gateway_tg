import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class StreamEvent:
    text_chunk: str = ""
    is_done: bool = False
    approval_request: Optional[dict] = None
    created_file: Optional[str] = None

class GeminiStreamParser:
    """Парсер для --output=stream-json Gemini CLI."""
    
    @staticmethod
    def parse_line(line: str) -> StreamEvent:
        """Парсит одну строку stream-json."""
        if not line.strip():
            return StreamEvent()
            
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Fallback для не-JSON строк (иногда CLI может плюнуть ошибку текстом)
            return StreamEvent(text_chunk=f"\n{line}\n")
            
        event = StreamEvent()
        
        # Зависит от формата cli, предполагаем базовые ключи
        # Если есть поле chunk или text:
        if "text" in data:
            event.text_chunk = data["text"]
        elif "chunk" in data:
            event.text_chunk = data["chunk"]
        elif "message" in data and isinstance(data["message"], str):
            event.text_chunk = data["message"]
            event.is_done = True # Разовое сообщение
            
        # Завершение ответа
        if data.get("done") is True or data.get("type") == "done":
            event.is_done = True
            
        # Запрос подтверждения (интерактивный аппрув)
        if "approval" in data or data.get("type") == "approval_needed":
            event.approval_request = data.get("approval", data)
            
        return event
