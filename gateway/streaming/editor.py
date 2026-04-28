from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from gateway.telegram_formatting import render_telegram_html

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiogram import Bot
else:
    Bot = Any

try:
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramNetworkError,
        TelegramRetryAfter,
    )
except Exception:  # pragma: no cover - fallback для локальных сред без aiogram

    class TelegramBadRequest(Exception):
        pass

    class TelegramNetworkError(Exception):
        pass

    class TelegramRetryAfter(Exception):
        retry_after = 1


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
        min_update_chars: int = 120,
        retry_max_delay: float = 30.0,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.interval = interval
        self.max_length = max_length
        self.min_update_chars = min_update_chars
        self.retry_max_delay = retry_max_delay

        self.current_message_id: Optional[int] = None
        self.text_buffer = ""
        self.last_sent_text = ""
        self._last_display_text = ""
        self.status_line = ""
        self.last_update_task: Optional[asyncio.Task] = None
        self.is_flushing = False
        self._first_chunk = True
        self._has_answer_text = False
        self._loading_task: Optional[asyncio.Task] = None
        self._message_order: list[int] = []
        self._message_texts: dict[int, str] = {}

    async def _loading_animation(self, chat_id: int, message_id: int) -> None:
        stages = [
            "⏳ [1/4] Инициализация Gemini CLI...",
            "🔐 [2/4] Проверка auth/trust/policy...",
            "🔌 [3/4] Загрузка MCP, skills и extensions...",
            "🧠 [4/4] Ожидание stream-json событий...",
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
                            parse_mode=None,
                        )
                    except TelegramBadRequest:
                        pass

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def initialize(self, initial_text: str = "⏳ Генерирую ответ...") -> None:
        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text=initial_text,
            parse_mode=None,
        )
        self.current_message_id = msg.message_id
        self._remember_message(msg.message_id, initial_text)
        self.last_sent_text = initial_text
        self._last_display_text = initial_text
        self.text_buffer = ""
        self.status_line = ""
        self._first_chunk = True
        self._has_answer_text = False
        self._loading_task = asyncio.create_task(
            self._loading_animation(self.chat_id, self.current_message_id)
        )

    def attach_to_message(self, message_id: int, initial_text: str = "") -> None:
        self.current_message_id = message_id
        self._remember_message(message_id, initial_text)
        self.last_sent_text = initial_text
        self._last_display_text = initial_text
        self.text_buffer = ""
        self.status_line = ""
        self._first_chunk = False
        self._has_answer_text = False

    async def append_text(self, text_chunk: str) -> None:
        if not text_chunk:
            return

        is_first_answer_chunk = not self._has_answer_text
        if is_first_answer_chunk:
            self._has_answer_text = True
            self._clear_initial_state()
            if self.last_update_task and not self.last_update_task.done():
                self.last_update_task.cancel()

        self.text_buffer += text_chunk

        if len(self.last_sent_text) + len(self.text_buffer) >= self.max_length:
            await self._flush_and_split()
        elif is_first_answer_chunk:
            await self._update_buffered()
        elif (self.last_update_task is None or self.last_update_task.done()) and (
            not self.last_sent_text or len(self.text_buffer) >= self.min_update_chars
        ):
            self.last_update_task = asyncio.create_task(self._throttled_update())

    async def set_status(self, status: str) -> None:
        self._clear_initial_state()

        self.status_line = status
        if self.last_update_task is None or self.last_update_task.done():
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
        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text="…",
            parse_mode=None,
        )
        self.current_message_id = msg.message_id
        self._remember_message(msg.message_id, "")
        self.last_sent_text = ""
        self._last_display_text = "…"

    async def _throttled_update(self) -> None:
        await asyncio.sleep(self.interval)
        await self._update_buffered()

    async def _update_buffered(self) -> None:
        if (not self.text_buffer and not self.status_line) or self.is_flushing:
            return

        full_text = self.last_sent_text + self.text_buffer
        if len(full_text) > self.max_length:
            await self._flush_and_split()
            return

        success = await self._raw_edit(self._with_status(full_text))
        if success:
            self.last_sent_text = full_text
            self.text_buffer = ""

    def _clear_initial_state(self) -> None:
        if not self._first_chunk:
            return
        self._first_chunk = False
        self.last_sent_text = ""
        if self._loading_task and not self._loading_task.done():
            self._loading_task.cancel()

    async def _raw_edit(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        message_id: int | None = None,
        update_segment: bool = True,
    ) -> bool:
        target_message_id = message_id or self.current_message_id
        if not target_message_id:
            return False

        network_delay = 1.0
        for _attempt in range(4):
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=target_message_id,
                    text=text or "…",
                    parse_mode=parse_mode,
                )
                if target_message_id == self.current_message_id:
                    self._last_display_text = text or "…"
                if update_segment:
                    self._set_message_text(target_message_id, text or "…")
                return True
            except TelegramRetryAfter as exc:
                retry_after = float(getattr(exc, "retry_after", 1) or 1)
                await asyncio.sleep(min(retry_after, self.retry_max_delay))
            except TelegramNetworkError as exc:
                logger.warning("Telegram network error while editing: %s", exc)
                await asyncio.sleep(min(network_delay, self.retry_max_delay))
                network_delay *= 2
            except TelegramBadRequest as exc:
                message = str(exc).lower()
                if "message is not modified" in message:
                    if target_message_id == self.current_message_id:
                        self._last_display_text = text or "…"
                    if update_segment:
                        self._set_message_text(target_message_id, text or "…")
                    return True
                if (
                    target_message_id == self.current_message_id
                    and self._should_fallback_to_new_message(message)
                ):
                    return await self._send_replacement_message(text)
                logger.warning("Error editing message: %s", exc)
                return False
        return False

    async def _send_replacement_message(self, text: str) -> bool:
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text or "…",
                parse_mode=None,
            )
        except Exception as exc:
            logger.warning("Error sending replacement message: %s", exc)
            return False
        self.current_message_id = msg.message_id
        self._remember_message(msg.message_id, text or "…")
        self._last_display_text = text or "…"
        return True

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
                success = await self._raw_edit(self._with_status(full_text))
                if not success:
                    break
                self.last_sent_text = full_text
                self.text_buffer = ""

        final_text = self._with_status(self.last_sent_text)
        if final_text != self._last_display_text:
            await self._raw_edit(final_text)

        if self._has_answer_text:
            await self._finalize_html_formatting()

        self.is_flushing = False

    def _split_text(self, text: str, limit: int) -> tuple[str, str]:
        if len(text) <= limit:
            return text, ""

        split_at = self._find_split_index(text, limit)
        chunk = text[:split_at]
        remainder = text[split_at:]

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
                split_at = index + len(separator)
                if split_at <= limit:
                    return split_at
                return index

        return limit

    def _with_status(self, text: str) -> str:
        if not self.status_line:
            return text
        candidate = f"{text}\n\n{self.status_line}" if text else self.status_line
        if len(candidate) <= self.max_length:
            return candidate
        return text

    @staticmethod
    def _should_fallback_to_new_message(error_message: str) -> bool:
        return any(
            marker in error_message
            for marker in (
                "message to edit not found",
                "message can't be edited",
                "message can't be modified",
                "message identifier is not specified",
            )
        )

    def _remember_message(self, message_id: int, text: str) -> None:
        if message_id not in self._message_texts:
            self._message_order.append(message_id)
        self._message_texts[message_id] = text

    def _set_message_text(self, message_id: int, text: str) -> None:
        self._remember_message(message_id, text)

    async def _finalize_html_formatting(self) -> None:
        for message_id in list(self._message_order):
            plain_text = self._message_texts.get(message_id, "")
            if not plain_text or plain_text == "…":
                continue

            rendered = render_telegram_html(plain_text)
            if not rendered.changed:
                continue

            ok = await self._raw_edit(
                rendered.html_text,
                parse_mode="HTML",
                message_id=message_id,
                update_segment=False,
            )
            if not ok:
                await self._raw_edit(
                    rendered.plain_text,
                    parse_mode=None,
                    message_id=message_id,
                    update_segment=False,
                )
