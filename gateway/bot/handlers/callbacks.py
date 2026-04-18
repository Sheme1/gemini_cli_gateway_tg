import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from gateway.bot.keyboards import inline
from gateway.bot.ui import (
    APPROVAL_MODE_DESCRIPTIONS,
    APPROVAL_MODE_LABELS,
    RENDER_MODE_DESCRIPTIONS,
    RENDER_MODE_LABELS,
    build_settings_text,
    get_approval_mode_label,
    get_render_mode_label,
)
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

# Простой rate limiter (user_id -> timestamp последнего обновления)
_refresh_cooldown: dict[int, float] = {}
REFRESH_COOLDOWN_SECONDS = 3  # 3 секунды между обновлениями

# ======================== Модель ========================


@router.callback_query(F.data.startswith("model:"))
async def callback_model(
    callback: CallbackQuery, config: Config, session_manager: SessionManager
) -> None:
    """Изменение модели с перезапуском сессии."""
    new_model = callback.data.split(":")[1]

    if new_model == config.gemini_model:
        await callback.answer("Эта модель уже выбрана", show_alert=True)
        return

    object.__setattr__(config, "gemini_model", new_model)

    await callback.message.edit_text(
        f"🔄 Модель изменена на <b>{new_model}</b>.\nТекущий диалог сброшен.",
        reply_markup=None,
    )

    await session_manager.reset(callback.from_user.id)
    await callback.message.answer("✅ Готово! Контекст очищен, новая модель применена.")
    await callback.answer()


# ======================== Sessions ========================


@router.callback_query(F.data.startswith("resume_"))
async def callback_resume_session(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Выбор старой сессии из списка /sessions."""
    session_id = callback.data.split("resume_")[1]

    await session_manager.set_active_session(callback.from_user.id, session_id)

    await callback.message.edit_text(
        f"✅ <b>Диалог выбран:</b> <code>{session_id}</code>\n"
        "Все последующие запросы будут отправлены в этот контекст.",
        reply_markup=None,
    )
    await callback.answer()


# ======================== Approval ========================


@router.callback_query(F.data.startswith("approve:"))
async def callback_interactive_approve(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Ответ на интерактивный аппрув от Gemini."""
    action = callback.data.split(":")[1]

    if action == "yolo":
        answer = "yes"
    else:
        answer = action

    await session_manager.answer_approval(answer)

    await callback.message.edit_reply_markup(reply_markup=None)

    if answer == "yes":
        await callback.message.reply("✅ Действие одобрено.")
    else:
        await callback.message.reply("❌ Действие отклонено.")

    await callback.answer()


# ======================== Settings ========================


@router.callback_query(F.data == "settings:main")
@router.callback_query(F.data == "settings")
async def callback_settings_main(
    callback: CallbackQuery, config: Config, user_settings: UserSettingsStore
) -> None:
    """Главное меню настроек."""
    render_mode = user_settings.get_render_mode(callback.from_user.id)
    text = build_settings_text(config, render_mode)
    kb = inline.get_settings_keyboard(
        render_mode=render_mode,
        approval_mode=config.gemini_approval_mode,
    )

    if callback.message.text:
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "settings:render")
async def callback_settings_render(
    callback: CallbackQuery, user_settings: UserSettingsStore
) -> None:
    """Меню выбора режима отображения."""
    current_mode = user_settings.get_render_mode(callback.from_user.id)
    descriptions = "\n".join(
        f"• <b>{RENDER_MODE_LABELS[mode]}</b> — {RENDER_MODE_DESCRIPTIONS[mode]}"
        for mode in RENDER_MODE_LABELS
    )
    await callback.message.edit_text(
        "Выберите режим отображения:\n\n" + descriptions,
        reply_markup=inline.get_render_modes_keyboard(current_mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_render:"))
async def callback_set_render(
    callback: CallbackQuery, config: Config, user_settings: UserSettingsStore
) -> None:
    """Установка режима отображения для пользователя."""
    new_mode = callback.data.split(":")[1]
    current_mode = user_settings.get_render_mode(callback.from_user.id)

    if new_mode == current_mode:
        await callback.answer("Этот режим уже выбран.")
        return

    user_settings.set_render_mode(callback.from_user.id, new_mode)
    render_mode = user_settings.get_render_mode(callback.from_user.id)
    await callback.message.edit_text(
        build_settings_text(config, render_mode),
        reply_markup=inline.get_settings_keyboard(
            render_mode=render_mode,
            approval_mode=config.gemini_approval_mode,
        ),
    )
    await callback.answer(
        f"Режим отображения: {get_render_mode_label(render_mode)}."
    )


@router.callback_query(F.data == "settings:approval")
async def callback_settings_approval(callback: CallbackQuery, config: Config) -> None:
    """Меню выбора режима approval."""
    descriptions = "\n".join(
        f"• <b>{APPROVAL_MODE_LABELS[mode]}</b> — {APPROVAL_MODE_DESCRIPTIONS[mode]}"
        for mode in APPROVAL_MODE_LABELS
    )
    await callback.message.edit_text(
        "Выберите режим подтверждений:\n\n" + descriptions,
        reply_markup=inline.get_approval_modes_keyboard(config.gemini_approval_mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_approval:"))
async def callback_set_approval(
    callback: CallbackQuery, config: Config, session_manager: SessionManager
) -> None:
    """Установка нового approval_mode."""
    new_mode = callback.data.split(":")[1]

    if new_mode == config.gemini_approval_mode:
        await callback.answer("Этот режим уже выбран.")
        return

    object.__setattr__(config, "gemini_approval_mode", new_mode)

    await callback.message.edit_text(
        f"🔄 Режим подтверждений изменён: <b>{get_approval_mode_label(new_mode)}</b>.\n"
        "Текущий диалог сброшен.",
        reply_markup=None,
    )
    await session_manager.reset(callback.from_user.id)
    await callback.message.answer("✅ Готово. Новый режим применён.")
    await callback.answer()


# ======================== MCP & Skills ========================


@router.callback_query(F.data.startswith("mcp_toggle:"))
async def callback_mcp_toggle(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Включение/выключение MCP сервера."""
    _, name, action = callback.data.split(":")
    enable = action == "enable"

    await callback.answer(
        f"⏳ {'Включаю' if enable else 'Выключаю'} {name}...", show_alert=False
    )

    success = await session_manager.toggle_mcp(name, enable)
    if success:
        # Обновляем клавиатуру
        servers = await session_manager.get_mcp_list()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=inline.get_mcp_list_keyboard(servers)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "mcp_refresh")
async def callback_mcp_refresh(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    user_id = callback.from_user.id
    now = time.time()

    # Rate limiting
    if user_id in _refresh_cooldown:
        time_since_last = now - _refresh_cooldown[user_id]
        if time_since_last < REFRESH_COOLDOWN_SECONDS:
            remaining = int(REFRESH_COOLDOWN_SECONDS - time_since_last)
            await callback.answer(
                f"⏳ Подожди {remaining} сек. перед следующим обновлением",
                show_alert=False,
            )
            return

    _refresh_cooldown[user_id] = now

    servers = await session_manager.get_mcp_list()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=inline.get_mcp_list_keyboard(servers)
        )
        await callback.answer("🔄 Список обновлён")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            await callback.answer("✅ Список актуален", show_alert=False)
        else:
            raise


@router.callback_query(F.data.startswith("skill_toggle:"))
async def callback_skill_toggle(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Включение/выключение Skill."""
    _, name, action = callback.data.split(":")
    enable = action == "enable"

    await callback.answer(
        f"⏳ {'Включаю' if enable else 'Выключаю'} {name}...", show_alert=False
    )

    success = await session_manager.toggle_skill(name, enable)
    if success:
        skills = await session_manager.get_skills_list()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=inline.get_skills_list_keyboard(skills)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "skill_refresh")
async def callback_skill_refresh(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    user_id = callback.from_user.id
    now = time.time()

    # Rate limiting
    if user_id in _refresh_cooldown:
        time_since_last = now - _refresh_cooldown[user_id]
        if time_since_last < REFRESH_COOLDOWN_SECONDS:
            remaining = int(REFRESH_COOLDOWN_SECONDS - time_since_last)
            await callback.answer(
                f"⏳ Подожди {remaining} сек. перед следующим обновлением",
                show_alert=False,
            )
            return

    _refresh_cooldown[user_id] = now

    skills = await session_manager.get_skills_list()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=inline.get_skills_list_keyboard(skills)
        )
        await callback.answer("🔄 Список обновлён")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            await callback.answer("✅ Список актуален", show_alert=False)
        else:
            raise
