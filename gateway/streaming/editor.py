from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


class StreamEditor:
    """
    Управляет стримингом длинных текстов в Telegram сообщения,
    избегая лимита запросов (429 Too Many Requests) за счёт буферизации
    и ограничения частоты обновлений (throttling), а также авто-разбиения
    на сообщения по 4096 символов.
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

    async def initialize(self, initial_text: str = "⏳ Генерирую ответ...") -> None:
        """Отправляет первое сообщение, которое будет обновляться."""
        self.text_buffer = initial_text
        msg = await self.bot.send_message(chat_id=self.chat_id, text=initial_text)
        self.current_message_id = msg.message_id
        self.last_sent_text = initial_text
        self.text_buffer = ""  # очищаем после успешной отправки

    async def append_text(self, text_chunk: str) -> None:
        """Добавляет текст в буфер и планирует обновление, если нужно."""
        self.text_buffer += text_chunk

        # Если превысили лимит Телеграма, нужно переключиться на новое сообщение
        if len(self.last_sent_text) + len(self.text_buffer) >= self.max_length:
            await self._flush_and_split()
        elif self.last_update_task is None or self.last_update_task.done():
            self.last_update_task = asyncio.create_task(self._throttled_update())

    async def _flush_and_split(self) -> None:
        """Завершает текущее сообщение и начинает новое из-за ограничения длины."""
        # Сначала доотправляем всё, что может влезть в текущее (если есть остаток)
        available_space = self.max_length - len(self.last_sent_text)
        if available_space > 0 and self.text_buffer:
            fit_text = self.text_buffer[:available_space]
            self.text_buffer = self.text_buffer[available_space:]
            self.last_sent_text += fit_text
            await self._raw_edit(self.last_sent_text)

        # Отправляем новое сообщение с остатком
        if self.text_buffer:
            msg = await self.bot.send_message(
                chat_id=self.chat_id, text=self.text_buffer[: self.max_length]
            )
            self.current_message_id = msg.message_id
            self.last_sent_text = self.text_buffer[: self.max_length]
            self.text_buffer = self.text_buffer[self.max_length :]

    async def _throttled_update(self) -> None:
        """Обновляет сообщение не чаще чем раз в self.interval."""
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
        """Вызов editMessageText с обработкой 'message is not modified'."""
        if not self.current_message_id:
            return False

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.current_message_id,
                text=text,
                parse_mode=None,  # На этапе стриминга лучше без парсера ломающего частичный маркдаун
            )
            return True
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return True  # Игнорируем, это не ошибка
            logger.warning(f"Error editing message: {e}")
            return False

    async def flush(self) -> None:
        """Принудительно отправляет всё, что осталось в буфере (вызывать в конце)."""
        self.is_flushing = True

        # Отменяем ждущий апдейт
        if self.last_update_task and not self.last_update_task.done():
            self.last_update_task.cancel()

        while self.text_buffer:
            full_text = self.last_sent_text + self.text_buffer
            if len(full_text) > self.max_length:
                await self._flush_and_split()
            else:
                await self._raw_edit(full_text)
                self.last_sent_text = full_text
                self.text_buffer = ""

        self.is_flushing = False
