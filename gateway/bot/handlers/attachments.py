from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from gateway.attachments import (
    AttachmentError,
    AttachmentService,
    caption_prompt,
)
from gateway.bot.handlers.messages import process_gemini_prompt
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.prompt_guard import PendingPromptStore
from gateway.usage import UsageLedger
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="attachments")


@dataclass
class _AlbumDependencies:
    bot: Any
    session_manager: SessionManager
    config: Config
    user_settings: UserSettingsStore
    usage_ledger: UsageLedger
    prompt_guard: PendingPromptStore


@dataclass
class _PendingAlbum:
    messages: list[Message] = field(default_factory=list)
    version: int = 0
    dependencies: _AlbumDependencies | None = None


class AttachmentAlbumCoordinator:
    def __init__(self) -> None:
        self._albums: dict[tuple[int, int, str], _PendingAlbum] = {}
        self._lock = asyncio.Lock()

    async def add(self, message: Message, dependencies: _AlbumDependencies) -> None:
        media_group_id = getattr(message, "media_group_id", None)
        if not media_group_id:
            await process_attachment_messages([message], dependencies)
            return

        key = (message.chat.id, message.from_user.id, str(media_group_id))
        async with self._lock:
            pending = self._albums.setdefault(key, _PendingAlbum())
            pending.messages.append(message)
            pending.dependencies = dependencies
            pending.version += 1
            version = pending.version

        asyncio.create_task(
            self._flush_after(
                key,
                version,
                dependencies.config.attachment_album_debounce_seconds,
            )
        )

    async def _flush_after(
        self,
        key: tuple[int, int, str],
        version: int,
        delay_seconds: float,
    ) -> None:
        await asyncio.sleep(max(0.01, delay_seconds))
        async with self._lock:
            pending = self._albums.get(key)
            if pending is None or pending.version != version:
                return
            self._albums.pop(key, None)

        if pending.dependencies is None:
            return
        try:
            await process_attachment_messages(pending.messages, pending.dependencies)
        except Exception as exc:
            logger.error("Album attachment processing failed: %s", exc, exc_info=True)


_album_coordinator = AttachmentAlbumCoordinator()


@router.message(F.document | F.photo | F.video | F.audio | F.animation)
async def attachment_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    bot: Any,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
) -> None:
    """Download Telegram attachments and pass local file paths to Gemini CLI."""
    await _album_coordinator.add(
        message,
        _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,
            config=config,
            user_settings=user_settings,
            usage_ledger=usage_ledger,
            prompt_guard=prompt_guard,
        ),
    )


async def process_attachment_messages(
    messages: list[Message],
    dependencies: _AlbumDependencies,
) -> None:
    if not messages:
        return

    first_message = messages[0]
    chat_id = first_message.chat.id
    user_id = first_message.from_user.id
    status_message = await dependencies.bot.send_message(
        chat_id=chat_id,
        text="📎 Скачиваю вложения...",
    )

    service = AttachmentService(dependencies.config)
    try:
        bundle = await service.prepare_bundle(
            bot=dependencies.bot,
            user_id=user_id,
            messages=messages,
            user_prompt=caption_prompt(messages),
        )
    except AttachmentError as exc:
        await dependencies.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=f"❌ {exc.user_message}",
        )
        return
    except Exception as exc:
        logger.error("Attachment handler error: %s", exc, exc_info=True)
        await dependencies.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=f"❌ Ошибка обработки вложения: {exc}",
        )
        return

    initial_text = "📎 Вложения загружены. Запускаю Gemini..."
    await dependencies.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_message.message_id,
        text=initial_text,
    )
    await process_gemini_prompt(
        bot=dependencies.bot,
        chat_id=chat_id,
        user_id=user_id,
        prompt=bundle.prompt_text,
        session_manager=dependencies.session_manager,
        config=dependencies.config,
        user_settings=dependencies.user_settings,
        usage_ledger=dependencies.usage_ledger,
        prompt_guard=dependencies.prompt_guard,
        initial_message_id=status_message.message_id,
        initial_text=initial_text,
        extra_include_directories=bundle.include_dirs,
    )
