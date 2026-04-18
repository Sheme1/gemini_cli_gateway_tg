from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiogram import Bot
else:
    Bot = Any

try:
    from aiogram.exceptions import TelegramBadRequest
except Exception:  # pragma: no cover - fallback для локальных сред без aiogram
    class TelegramBadRequest(Exception):
        pass


class StreamEditor:
    """
    Потоково доставляет текст в Telegram без HTML-разметки и безопасно
    разбивает длинные ответы на несколько сообщений.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        interval: float = 1.5,
        max_length: int = 4096,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.interval = interval
        self.max_length = max_length

        self.current_message_id: Optional[int] = None
        self.text_buffer = ""
        self.last_sent_text = ""
        self.last_update_task: Optional[asyncio.Task] = None
        self.is_flushing = False
        self._first_chunk = True
        self._loading_task: Optional[asyncio.Task] = None

    async def _loading_animation(self, chat_id: int, message_id: int) -> None:
        stages = [
            "⏳ [1/4] Инициализация Gemini CLI...",
            "🔐 [2/4] Подключение к хранилищу ключей...",
            "🔌 [3/4] Прогрев MCP-серверов...",
            "🧠 [4/4] Ожидание ответа модели...",
        ]
        times = [0, 2, 5, 9]

        try:
            start_time = asyncio.get_event_loop().time()
            stage_idx = 0

            while True:
                current_time = asyncio.get_event_loop().time()
                elapsed = current_time - start_time
                new_idx = stage_idx

                for idx, threshold in enumerate(times):
                    if elapsed >= threshold:
                        new_idx = idx

                if new_idx > stage_idx:
                    stage_idx = new_idx
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=stages[stage_idx],
                        )
                    except TelegramBadRequest:
                        pass

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def initialize(self, initial_text: str = "⏳ Генерирую ответ...") -> None:
        msg = await self.bot.send_message(chat_id=self.chat_id, text=initial_text)
        self.current_message_id = msg.message_id
        self.last_sent_text = initial_text
        self.text_buffer = ""
        self._first_chunk = True
        self._loading_task = asyncio.create_task(
            self._loading_animation(self.chat_id, self.current_message_id)
        )

    def attach_to_message(self, message_id: int, initial_text: str = "") -> None:
        self.current_message_id = message_id
        self.last_sent_text = initial_text
        self.text_buffer = ""
        self._first_chunk = False

    async def append_text(self, text_chunk: str) -> None:
        if not text_chunk:
            return

        if self._first_chunk:
            self._first_chunk = False
            self.last_sent_text = ""
            if self._loading_task and not self._loading_task.done():
                self._loading_task.cancel()

        self.text_buffer += text_chunk

        if len(self.last_sent_text) + len(self.text_buffer) >= self.max_length:
            await self._flush_and_split()
        elif self.last_update_task is None or self.last_update_task.done():
            self.last_update_task = asyncio.create_task(self._throttled_update())

    async def _flush_and_split(self) -> None:
        while self.text_buffer:
            available_space = self.max_length - len(self.last_sent_text)
            if available_space <= 0:
                await self._start_new_message()
                continue

            chunk, remainder = self._split_text(self.text_buffer, available_space)
            if not chunk:
                await self._start_new_message()
                continue

            updated_text = self.last_sent_text + chunk
            success = await self._raw_edit(updated_text)
            if not success:
                break

            self.last_sent_text = updated_text
            self.text_buffer = remainder

            if self.text_buffer:
                await self._start_new_message()

    async def _start_new_message(self) -> None:
        msg = await self.bot.send_message(chat_id=self.chat_id, text="…")
        self.current_message_id = msg.message_id
        self.last_sent_text = ""

    async def _throttled_update(self) -> None:
        await asyncio.sleep(self.interval)
        if not self.text_buffer or self.is_flushing:
            return

        full_text = self.last_sent_text + self.text_buffer
        if len(full_text) > self.max_length:
            await self._flush_and_split()
            return

        success = await self._raw_edit(full_text)
        if success:
            self.last_sent_text = full_text
            self.text_buffer = ""

    async def _raw_edit(self, text: str) -> bool:
        if not self.current_message_id:
            return False

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.current_message_id,
                text=text or "…",
            )
            return True
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return True
            logger.warning("Error editing message: %s", exc)
            return False

    async def flush(self) -> None:
        self.is_flushing = True

        if self._loading_task and not self._loading_task.done():
            self._loading_task.cancel()

        if self.last_update_task and not self.last_update_task.done():
            self.last_update_task.cancel()

        while self.text_buffer:
            full_text = self.last_sent_text + self.text_buffer
            if len(full_text) > self.max_length:
                await self._flush_and_split()
            else:
                success = await self._raw_edit(full_text)
                if not success:
                    break
                self.last_sent_text = full_text
                self.text_buffer = ""

        self.is_flushing = False

    def _split_text(self, text: str, limit: int) -> tuple[str, str]:
        if len(text) <= limit:
            return text, ""

        split_at = self._find_split_index(text, limit)
        chunk = text[:split_at].rstrip()
        remainder = text[split_at:].lstrip()

        if not chunk:
            chunk = text[:limit]
            remainder = text[limit:]

        return chunk, remainder

    @staticmethod
    def _find_split_index(text: str, limit: int) -> int:
        search_space = text[: limit + 1]
        minimum = max(1, limit // 2)

        for separator in ("\n\n", "\n", ". ", " "):
            index = search_space.rfind(separator)
            if index >= minimum:
                return index + (0 if separator == " " else len(separator))

        return limit
