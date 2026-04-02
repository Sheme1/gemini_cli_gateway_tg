import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.session import SessionManager

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

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
        f"✅ <b>Сессия выбрана:</b> <code>{session_id}</code>\n"
        f"Все последующие запросы будут отправлены в этот контекст.",
        reply_markup=None
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
        await callback.message.reply("✅ Действие одобрено")
    else:
        await callback.message.reply("❌ Действие отклонено")

    await callback.answer()


# ======================== Settings ========================


@router.callback_query(F.data == "settings:main")
@router.callback_query(F.data == "settings")
async def callback_settings_main(callback: CallbackQuery, config: Config) -> None:
    """Главное меню настроек."""
    text = "⚙️ <b>Настройки Gemini CLI</b>"
    kb = inline.get_settings_keyboard(
        approval_mode=config.gemini_approval_mode,
        timeout=config.gemini_cli_timeout,
        sandbox=config.gemini_sandbox,
    )

    if callback.message.text:
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "settings:approval")
async def callback_settings_approval(callback: CallbackQuery, config: Config) -> None:
    """Меню выбора режима approval."""
    kb = inline.get_approval_modes_keyboard(config.gemini_approval_mode)
    await callback.message.edit_text(
        "Выберите режим подтверждения действий (--approval-mode):", reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_approval:"))
async def callback_set_approval(
    callback: CallbackQuery, config: Config, session_manager: SessionManager
) -> None:
    """Установка нового approval_mode."""
    new_mode = callback.data.split(":")[1]

    if new_mode == config.gemini_approval_mode:
        await callback.answer("Этот режим уже установлен")
        return

    object.__setattr__(config, "gemini_approval_mode", new_mode)

    await callback.message.edit_text(
        f"🔄 Режим установлен: <b>{new_mode}</b>.\nТекущий диалог сброшен.",
        reply_markup=None,
    )
    await session_manager.reset(callback.from_user.id)
    await callback.message.answer("✅ Готово. Новый режим применен.")
    await callback.answer()
