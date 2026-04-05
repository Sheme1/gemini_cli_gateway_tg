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
        self._first_chunk = True  # Флаг: ещё не было реального текста
        self._loading_task: Optional[asyncio.Task] = None

    async def _loading_animation(self, chat_id: int, message_id: int) -> None:
        """Показывает анимацию стадий загрузки агента."""
        stages = [
            "⏳ <i>[1/4] Инициализация Gemini CLI...</i>",
            "🔐 <i>[2/4] Подключение к Keychain...</i>",
            "🔌 <i>[3/4] Прогрев MCP серверов (обычно 8-10 сек)...</i>",
            "🧠 <i>[4/4] Ожидание ответа модели...</i>",
        ]
        times = [0, 2, 5, 9]  # секунды
        
        try:
            start_time = asyncio.get_event_loop().time()
            stage_idx = 0
            
            while True:
                current_time = asyncio.get_event_loop().time()
                elapsed = current_time - start_time
                
                # Ищем активную стадию
                new_idx = stage_idx
                for i, t in enumerate(times):
                    if elapsed >= t:
                        new_idx = i
                
                if new_idx > stage_idx:
                    stage_idx = new_idx
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=stages[stage_idx],
                            parse_mode="HTML"
                        )
                    except TelegramBadRequest:
                        pass # Игнорируем если не изменилось
                
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def initialize(self, initial_text: str = "⏳ <i>Генерирую ответ...</i>") -> None:
        """Отправляет первое сообщение-плейсхолдер, которое будет обновлено."""
        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text=initial_text,
            parse_mode="HTML",
        )
        self.current_message_id = msg.message_id
        self.last_sent_text = initial_text
        self.text_buffer = ""
        self._first_chunk = True
        
        # Запускаем анимацию
        self._loading_task = asyncio.create_task(
            self._loading_animation(self.chat_id, self.current_message_id)
        )

    async def append_text(self, text_chunk: str) -> None:
        """Добавляет текст в буфер и планирует обновление, если нужно."""
        # При первом чанке — заменяем плейсхолдер, а не дописываем к нему
        if self._first_chunk:
            self._first_chunk = False
            self.last_sent_text = ""
            if self._loading_task and not self._loading_task.done():
                self._loading_task.cancel()

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
                chat_id=self.chat_id,
                text=self.text_buffer[: self.max_length],
                parse_mode="HTML",
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
        """Вызов editMessageText с parse_mode=HTML."""
        if not self.current_message_id:
            return False

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.current_message_id,
                text=text,
                parse_mode="HTML",
            )
            return True
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return True  # Игнорируем, это не ошибка
            if "can't parse entities" in str(e).lower():
                # Fallback: если HTML невалидный, шлём без парсинга
                logger.warning(f"HTML parse failed, fallback to plain text: {e}")
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.current_message_id,
                        text=text,
                        parse_mode=None,
                    )
                    return True
                except TelegramBadRequest:
                    return False
            logger.warning(f"Error editing message: {e}")
            return False

    async def flush(self) -> None:
        """Принудительно отправляет всё, что осталось в буфере (вызывать в конце)."""
        self.is_flushing = True
        
        if self._loading_task and not self._loading_task.done():
            self._loading_task.cancel()

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
