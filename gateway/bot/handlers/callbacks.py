import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery

from gateway.bot.keyboards import inline
from gateway.bot.sessions import build_sessions_page
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
from gateway.init_wizard import InitWizardStore
from gateway.model_presets import get_model_preset_label
from gateway.prompt_guard import PendingPromptStore
from gateway.usage import UsageLedger
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

# Простой rate limiter (user_id -> timestamp последнего обновления)
_refresh_cooldown: dict[int, float] = {}
REFRESH_COOLDOWN_SECONDS = 3  # 3 секунды между обновлениями

# ======================== Модель ========================


@router.callback_query(F.data.startswith("model:"))
async def callback_model(
    callback: CallbackQuery,
    config: Config,
    user_settings: UserSettingsStore,
) -> None:
    """Изменение модели без сброса текущей сессии."""
    new_model = callback.data.split(":")[1]
    current_preset = user_settings.get_model_preset(callback.from_user.id)

    if new_model == current_preset:
        await callback.answer("Этот пресет уже выбран", show_alert=True)
        return

    selected_preset = user_settings.set_model_preset(callback.from_user.id, new_model)
    effective_model = user_settings.get_effective_model(
        callback.from_user.id,
        config.gemini_model,
    )

    await callback.message.edit_text(
        "🔄 Модель изменена.\n\n"
        f"<b>Пресет:</b> {get_model_preset_label(selected_preset)}\n"
        f"<b>Модель:</b> <code>{effective_model}</code>\n\n"
        "Текущий диалог сохранён.",
        reply_markup=None,
    )

    await callback.message.answer(
        "✅ Готово! Новая модель применена без сброса контекста."
    )
    await callback.answer()


# ======================== Sessions ========================


@router.callback_query(F.data.startswith("session:open:"))
async def callback_resume_session(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Выбор старой сессии из списка /sessions."""
    session_id = callback.data.split(":", maxsplit=2)[2]

    await session_manager.set_active_session(callback.from_user.id, session_id)

    await callback.message.edit_text(
        f"✅ <b>Диалог выбран:</b> <code>{session_id}</code>\n"
        "Все последующие запросы будут отправлены в этот контекст.",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "session:open-latest")
async def callback_resume_latest_session(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    sessions = await session_manager.get_sessions_list(callback.from_user.id)
    if not sessions:
        await callback.answer("Список пуст", show_alert=True)
        return

    await session_manager.set_active_session(callback.from_user.id, "latest")
    await callback.message.edit_text(
        "✅ <b>Диалог latest выбран.</b>\n"
        "Gemini CLI сам откроет самый свежий сохранённый диалог "
        "через <code>--resume latest</code>.",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("session:page:"))
async def callback_sessions_page(
    callback: CallbackQuery,
    session_manager: SessionManager,
) -> None:
    page = _parse_session_page(callback.data, prefix="session:page:")
    await _edit_sessions_page(callback, session_manager, page)


@router.callback_query(F.data.startswith("session:refresh:"))
async def callback_sessions_refresh(
    callback: CallbackQuery,
    session_manager: SessionManager,
) -> None:
    page = _parse_session_page(callback.data, prefix="session:refresh:")
    await _edit_sessions_page(callback, session_manager, page, refreshed=True)


@router.callback_query(F.data.startswith("session:delete:"))
async def callback_delete_session(
    callback: CallbackQuery,
    session_manager: SessionManager,
) -> None:
    session_id = callback.data.split(":", maxsplit=2)[2]
    deleted = await session_manager.delete_session_by_id(
        session_id,
        user_id=callback.from_user.id,
    )
    if not deleted:
        await callback.answer("Сессия уже не найдена", show_alert=True)
        return

    sessions = await session_manager.get_sessions_list(callback.from_user.id)
    if not sessions:
        await callback.message.edit_text("📂 Сохранённые диалоги не найдены.")
    else:
        text, reply_markup = build_sessions_page(sessions)
        await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer("Сессия удалена")


@router.callback_query(F.data == "session:export")
async def callback_export_sessions(
    callback: CallbackQuery,
    session_manager: SessionManager,
) -> None:
    sessions = await session_manager.get_sessions_list(callback.from_user.id)
    if not sessions:
        await callback.answer("Список пуст", show_alert=True)
        return
    lines = ["Gemini CLI sessions", ""]
    for index, session in enumerate(sessions, start=1):
        current = " current" if session.is_current else ""
        lines.append(
            f"{index}. {session.title} ({session.relative_time}{current}) "
            f"[{session.session_id}]"
        )
    payload = "\n".join(lines).encode("utf-8")
    await callback.message.answer_document(
        BufferedInputFile(payload, filename="gemini-sessions.txt"),
        caption="Экспорт списка сессий Gemini CLI",
    )
    await callback.answer("Экспорт готов")


@router.callback_query(F.data.startswith("resume_"))
async def callback_resume_session_legacy(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Поддержка старых inline-кнопок, отправленных до обновления."""
    session_id = callback.data.split("resume_", maxsplit=1)[1]
    await session_manager.set_active_session(callback.from_user.id, session_id)
    await callback.message.edit_text(
        f"✅ <b>Диалог выбран:</b> <code>{session_id}</code>\n"
        "Все последующие запросы будут отправлены в этот контекст.",
        reply_markup=None,
    )
    await callback.answer()


async def _edit_sessions_page(
    callback: CallbackQuery,
    session_manager: SessionManager,
    page: int,
    *,
    refreshed: bool = False,
) -> None:
    sessions = await session_manager.get_sessions_list(callback.from_user.id)
    if not sessions:
        await callback.message.edit_text("📂 Сохранённые диалоги не найдены.")
        await callback.answer("Список пуст")
        return

    text, reply_markup = build_sessions_page(sessions, page=page)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer("🔄 Список обновлён" if refreshed else None)


def _parse_session_page(data: str, prefix: str) -> int:
    try:
        return max(0, int(data.removeprefix(prefix)))
    except ValueError:
        return 0


# ======================== Approval ========================


@router.callback_query(F.data.startswith("approve:"))
async def callback_interactive_approve(
    callback: CallbackQuery, session_manager: SessionManager
) -> None:
    """Поддержка старых approval-кнопок, отправленных до headless-обновления."""
    del session_manager

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(
        "⚠️ Интерактивное подтверждение недоступно в headless stream-json. "
        "Настройте GEMINI_APPROVAL_MODE или policy rules и повторите запрос."
    )

    await callback.answer("Headless approval не поддерживается", show_alert=True)


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
    await callback.answer(f"Режим отображения: {get_render_mode_label(render_mode)}.")


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
async def callback_set_approval(callback: CallbackQuery, config: Config) -> None:
    """Установка нового approval_mode."""
    new_mode = callback.data.split(":")[1]

    if new_mode == config.gemini_approval_mode:
        await callback.answer("Этот режим уже выбран.")
        return

    object.__setattr__(config, "gemini_approval_mode", new_mode)

    await callback.message.edit_text(
        f"🔄 Режим подтверждений изменён: <b>{get_approval_mode_label(new_mode)}</b>.\n"
        "Текущий диалог сохранён.",
        reply_markup=None,
    )
    await callback.message.answer(
        "✅ Готово. Новый режим применён без сброса контекста."
    )
    await callback.answer()


# ======================== Prompt guard ========================


@router.callback_query(F.data.startswith("prompt:confirm:"))
async def callback_prompt_confirm(
    callback: CallbackQuery,
    bot,
    session_manager: SessionManager,
    config: Config,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
) -> None:
    token = callback.data.split(":", maxsplit=2)[2]
    item = prompt_guard.get(token)
    if item is None:
        await callback.message.edit_text(
            "⌛ Подтверждение истекло. Отправьте запрос ещё раз."
        )
        await callback.answer("Подтверждение истекло", show_alert=True)
        return
    if item.user_id != callback.from_user.id:
        await callback.answer(
            "Это подтверждение для другого пользователя", show_alert=True
        )
        return

    prompt_guard.discard(token)
    initial_text = "✅ Запрос подтверждён. Запускаю Gemini..."
    await callback.message.edit_text(initial_text, reply_markup=None)
    await callback.answer()

    from gateway.bot.handlers.messages import process_gemini_prompt

    await process_gemini_prompt(
        bot=bot,
        chat_id=item.chat_id,
        user_id=item.user_id,
        prompt=item.prompt,
        session_manager=session_manager,
        config=config,
        user_settings=user_settings,
        usage_ledger=usage_ledger,
        prompt_guard=prompt_guard,
        initial_message_id=callback.message.message_id,
        initial_text=initial_text,
        skip_prompt_guard=True,
        extra_include_directories=item.extra_include_directories,
    )


@router.callback_query(F.data.startswith("prompt:cancel:"))
async def callback_prompt_cancel(
    callback: CallbackQuery,
    prompt_guard: PendingPromptStore,
) -> None:
    token = callback.data.split(":", maxsplit=2)[2]
    item = prompt_guard.get(token)
    if item is not None and item.user_id != callback.from_user.id:
        await callback.answer(
            "Это подтверждение для другого пользователя", show_alert=True
        )
        return
    prompt_guard.discard(token)
    await callback.message.edit_text("❌ Запрос отменён.", reply_markup=None)
    await callback.answer("Отменено")


# ======================== Init wizard ========================


@router.callback_query(F.data == "init:confirm")
async def callback_init_confirm(
    callback: CallbackQuery,
    init_wizard: InitWizardStore,
) -> None:
    try:
        gemini_md_path = init_wizard.confirm_preview(callback.from_user.id)
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        "✅ Личный <code>GEMINI.md</code> записан.\n\n"
        f"<code>{gemini_md_path}</code>\n\n"
        "Следующие запросы будут выполняться в вашем личном workspace.",
        reply_markup=None,
    )
    await callback.answer("GEMINI.md сохранён")


@router.callback_query(F.data == "init:cancel")
async def callback_init_cancel(
    callback: CallbackQuery,
    init_wizard: InitWizardStore,
) -> None:
    init_wizard.cancel_preview(callback.from_user.id)
    await callback.message.edit_text(
        "❌ Preview отменён. Текущий GEMINI.md не изменён.",
        reply_markup=None,
    )
    await callback.answer("Отменено")


# ======================== MCP & Skills ========================


@router.callback_query(F.data.startswith("mcp_toggle:"))
async def callback_mcp_toggle(
    callback: CallbackQuery, session_manager: SessionManager, config: Config
) -> None:
    """Включение/выключение MCP сервера."""
    if config.gateway_experimental_multi_user_workspaces:
        await callback.answer(
            "MCP-конфигурация общая; переключатели отключены в multi-user режиме.",
            show_alert=True,
        )
        return

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
                reply_markup=inline.get_mcp_list_keyboard(servers, allow_toggle=True)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "mcp_refresh")
@router.callback_query(F.data == "mcp_reload")
async def callback_mcp_refresh(
    callback: CallbackQuery, session_manager: SessionManager, config: Config
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
            reply_markup=inline.get_mcp_list_keyboard(
                servers,
                allow_toggle=not config.gateway_experimental_multi_user_workspaces,
            )
        )
        if callback.data == "mcp_reload":
            await callback.answer("♻️ Headless reload недоступен; список перечитан")
        else:
            await callback.answer("🔄 Список обновлён")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            if callback.data == "mcp_reload":
                await callback.answer(
                    "♻️ Headless reload недоступен; список уже актуален",
                    show_alert=False,
                )
            else:
                await callback.answer("✅ Список актуален", show_alert=False)
        else:
            raise


@router.callback_query(F.data.startswith("skill_toggle:"))
async def callback_skill_toggle(
    callback: CallbackQuery, session_manager: SessionManager, config: Config
) -> None:
    """Включение/выключение Skill."""
    if config.gateway_experimental_multi_user_workspaces:
        await callback.answer(
            "Skills-конфигурация общая; переключатели отключены в multi-user режиме.",
            show_alert=True,
        )
        return

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
                reply_markup=inline.get_skills_list_keyboard(skills, allow_toggle=True)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data == "skill_refresh")
@router.callback_query(F.data == "skill_reload")
async def callback_skill_refresh(
    callback: CallbackQuery, session_manager: SessionManager, config: Config
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
            reply_markup=inline.get_skills_list_keyboard(
                skills,
                allow_toggle=not config.gateway_experimental_multi_user_workspaces,
            )
        )
        if callback.data == "skill_reload":
            await callback.answer("♻️ Headless reload недоступен; список перечитан")
        else:
            await callback.answer("🔄 Список обновлён")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            if callback.data == "skill_reload":
                await callback.answer(
                    "♻️ Headless reload недоступен; список уже актуален",
                    show_alert=False,
                )
            else:
                await callback.answer("✅ Список актуален", show_alert=False)
        else:
            raise
