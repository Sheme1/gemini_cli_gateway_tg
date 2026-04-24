from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from gateway.artifacts import ArtifactManager
from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.renderer import render_event
from gateway.gemini.session import SessionManager
from gateway.prompt_guard import PendingPromptStore
from gateway.streaming.editor import StreamEditor
from gateway.usage import UsageLedger
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="messages")


@router.message(F.text)
async def message_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    bot: Any,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
) -> None:
    """Обрабатывает текстовые сообщения и отправляет их в Gemini CLI."""
    prompt = message.text
    if not prompt:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if message.reply_to_message and message.reply_to_message.text:
        reply_username = (
            "бота"
            if message.reply_to_message.from_user.is_bot
            else message.reply_to_message.from_user.full_name
        )
        prompt = (
            f"Контекст: это ответ на сообщение от {reply_username}:\n"
            f"> {message.reply_to_message.text}\n\n"
            f"Текущий ответ от {message.from_user.full_name}:\n{prompt}"
        )

    await process_gemini_prompt(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        prompt=prompt,
        session_manager=session_manager,
        config=config,
        user_settings=user_settings,
        usage_ledger=usage_ledger,
        prompt_guard=prompt_guard,
    )


async def process_gemini_prompt(
    bot: Any,
    chat_id: int,
    user_id: int,
    prompt: str,
    session_manager: SessionManager,
    config: Config,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger | None = None,
    prompt_guard: PendingPromptStore | None = None,
    initial_message_id: int | None = None,
    initial_text: str = "",
    skip_prompt_guard: bool = False,
) -> None:
    """Общий pipeline потокового вывода для любых входящих запросов."""
    if usage_ledger is not None:
        allowed, reason = usage_ledger.can_start_request(
            user_id,
            user_limit=config.user_daily_token_limit,
            global_limit=config.global_daily_token_limit,
        )
        if not allowed:
            await bot.send_message(chat_id=chat_id, text=f"⛔ {reason}")
            return

    if prompt_guard is not None and not skip_prompt_guard:
        prompt_length = len(prompt)
        if prompt_length > config.prompt_max_chars:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⛔ Запрос слишком большой.\n\n"
                    f"Размер: {prompt_length} символов.\n"
                    f"Максимум: {config.prompt_max_chars} символов.\n\n"
                    "Сократите текст или отправьте задачу несколькими сообщениями."
                ),
            )
            return
        if prompt_length > config.prompt_warn_chars:
            pending = prompt_guard.put(
                user_id=user_id,
                chat_id=chat_id,
                prompt=prompt,
                ttl_seconds=config.prompt_confirm_timeout,
            )
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ Запрос большой и может долго выполняться или потратить "
                    "много токенов.\n\n"
                    f"Размер: {prompt_length} символов.\n"
                    f"Порог предупреждения: {config.prompt_warn_chars} символов.\n"
                    f"Подтверждение действует {config.prompt_confirm_timeout} сек."
                ),
                reply_markup=inline.get_prompt_guard_keyboard(pending.token),
            )
            return

    if session_manager.has_active_prompt(user_id):
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⏳ Предыдущий запрос ещё выполняется.\n"
                "Дождитесь ответа или отправьте /cancel."
            ),
        )
        return

    streamer = StreamEditor(
        bot=bot,
        chat_id=chat_id,
        interval=config.stream_update_interval,
        max_length=config.stream_max_message_length,
        min_update_chars=config.stream_min_update_chars,
        retry_max_delay=config.stream_retry_max_delay,
    )
    artifact_manager = ArtifactManager(config)
    render_mode = user_settings.get_render_mode(user_id)
    effective_model = getattr(
        user_settings,
        "get_effective_model",
        lambda _user_id, fallback_model: fallback_model,
    )(user_id, config.gemini_model)
    started_at = time.time()
    last_event_at = time.monotonic()
    saw_tool_activity = False
    saw_result = False
    soft_finalized = False

    if initial_message_id is None:
        await streamer.initialize("⏳ Генерирую ответ...")
    else:
        streamer.attach_to_message(initial_message_id, initial_text)

    async def on_event(event) -> None:
        nonlocal last_event_at, saw_tool_activity, saw_result
        last_event_at = time.monotonic()
        if event.event_type in {"tool_use", "tool_result"}:
            saw_tool_activity = True
        if event.event_type == "result_stats":
            saw_result = True
            if usage_ledger is not None:
                usage_ledger.record_request(
                    user_id,
                    model=effective_model,
                    total_tokens=event.total_tokens,
                    duration_ms=event.duration_ms,
                )
        artifact_manager.register_event(event)
        rendered = render_event(event, render_mode)
        if rendered:
            if render_mode == "compact" and event.event_type in {
                "heartbeat",
                "tool_use",
                "tool_result",
            }:
                await streamer.set_status(rendered.strip())
            else:
                await streamer.append_text(rendered)

    async def on_approval(req: dict) -> None:
        logger.info("Получен запрос на подтверждение: %s", req)
        tool_name = (
            req.get("tool")
            or req.get("name")
            or req.get("action")
            or "неизвестное действие"
        )
        await streamer.flush()
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Gemini запрашивает подтверждение действия.\n"
                f"Действие: {tool_name}\n\n"
                "Что делать?"
            ),
            reply_markup=inline.get_interactive_approval_keyboard(),
        )

    async def watch_artifacts_and_soft_finalize(prompt_task) -> None:
        nonlocal soft_finalized
        try:
            while not prompt_task.done():
                await artifact_manager.send_ready_artifacts(
                    bot=bot,
                    chat_id=chat_id,
                    started_at=started_at,
                )

                idle_seconds = time.monotonic() - last_event_at
                if (
                    not saw_result
                    and saw_tool_activity
                    and artifact_manager.has_sent_artifacts
                    and idle_seconds >= config.gemini_soft_finalize_idle_seconds
                ):
                    logger.info(
                        "Soft finalize triggered for user %s after %.1fs idle.",
                        user_id,
                        idle_seconds,
                    )
                    soft_finalized = True
                    await streamer.append_text(
                        "\n\n⚠️ Gemini CLI не прислал финальный result, "
                        "но итоговый файл уже готов. Завершаю ответ мягко."
                    )
                    await streamer.set_status("")
                    await streamer.flush()
                    await session_manager.cancel_active_prompt(
                        user_id,
                        reason="soft finalize after artifact delivery",
                    )
                    return

                await asyncio.sleep(config.artifact_watch_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ошибка в watcher артефактов: %s", exc, exc_info=True)

    prompt_task = None
    watcher_task = None
    try:
        try:
            prompt_coro = session_manager.send_prompt(
                prompt=prompt,
                user_id=user_id,
                on_event=on_event,
                on_approval=on_approval,
                model=effective_model,
            )
        except TypeError as exc:
            if "model" not in str(exc):
                raise
            prompt_coro = session_manager.send_prompt(
                prompt=prompt,
                user_id=user_id,
                on_event=on_event,
                on_approval=on_approval,
            )
        prompt_task = asyncio.create_task(prompt_coro)
        watcher_task = asyncio.create_task(
            watch_artifacts_and_soft_finalize(prompt_task)
        )
        await prompt_task
    except Exception as exc:
        logger.error("Ошибка при обработке запроса: %s", exc, exc_info=True)
        await streamer.append_text(f"\n\n⚠️ Ошибка: {exc}")
    finally:
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        await streamer.set_status("")
        await streamer.flush()
        try:
            await artifact_manager.send_artifacts(
                bot=bot,
                chat_id=chat_id,
                started_at=started_at,
            )
        except Exception as exc:
            logger.error("Ошибка при отправке артефактов: %s", exc, exc_info=True)
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Не удалось отправить сгенерированный файл: {exc}",
            )
        if soft_finalized and prompt_task and not prompt_task.done():
            try:
                await prompt_task
            except Exception as exc:
                logger.error(
                    "Ошибка после мягкого завершения запроса: %s",
                    exc,
                    exc_info=True,
                )
