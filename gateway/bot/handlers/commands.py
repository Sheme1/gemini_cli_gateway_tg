import aiogram
from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.bot.sessions import build_sessions_page
from gateway.bot.ui import build_settings_text
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.runtime import (
    GatewayRuntimeState,
    build_diagnostics_text,
    build_status_text,
)
from gateway.user_settings import UserSettingsStore

router = Router(name="commands")


@router.message(CommandStart())
async def command_start_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /start"""
    text = (
        f"🤖 Добро пожаловать, {html.bold(message.from_user.full_name)}!\n\n"
        f"Это Telegram-шлюз к Gemini CLI.\n"
        f"Отправьте любое сообщение, чтобы начать диалог.\n\n"
        f"Команды:\n"
        f"🔄 /new — начать новый диалог (очистить контекст)\n"
        f"📂 /sessions — список прошлых диалогов\n"
        f"⏹ /cancel — остановить текущий запрос\n"
        f"⚙️ /settings — настройки отображения и режима работы\n"
        f"🟢 /status — состояние шлюза\n"
        f"ℹ️ /help — справка"
    )
    # Начинаем новую сессию
    await session_manager.reset(message.from_user.id)
    await message.answer(text)


@router.message(Command("new"))
async def command_new_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /new — сброс контекста Gemini."""
    await session_manager.reset(message.from_user.id)
    await message.answer(
        "✅ Контекст полностью очищен. Следующее сообщение начнет новый диалог."
    )


@router.message(Command("sessions"))
async def command_sessions_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /sessions."""
    status_message = await message.answer(
        "⏳ <i>Запрашиваю список диалогов...</i>", parse_mode="HTML"
    )
    try:
        sessions = await session_manager.get_sessions_list()
        if not sessions:
            await status_message.edit_text("📂 Сохранённые диалоги не найдены.")
            return

        text, reply_markup = build_sessions_page(sessions)
        await status_message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        await status_message.edit_text(
            f"❌ Ошибка при получении сессий: {html.quote(str(e))}"
        )


@router.message(Command("mcp"))
async def command_mcp_handler(
    message: Message,
    session_manager: SessionManager,
    bot: aiogram.Bot,
    config: Config,
    user_settings: UserSettingsStore,
) -> None:
    """Обработчик команды /mcp."""
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        # Просто показывает список
        await message.answer(
            "⏳ <i>Загружаю список MCP-серверов...</i>", parse_mode="HTML"
        )
        servers = await session_manager.get_mcp_list()

        # Fallback для пустого списка
        if not servers:
            await message.answer(
                "🔌 <b>MCP-серверы не найдены</b>\n\n"
                "Установите их через <code>gemini mcp install</code>.",
                parse_mode="HTML",
            )
            return

        text = "🔌 <b>Установленные MCP-серверы:</b>\n\n"
        for name, enabled in servers:
            icon = "🟢" if enabled else "🔴"
            status_text = "" if enabled else " <i>(отключен)</i>"
            text += f"{icon} <code>{name}</code>{status_text}\n"

        text += (
            "\n💡 Включайте и выключайте их кнопками ниже.\n"
            "Чтобы задействовать MCP в запросе, напишите: "
            "<code>/mcp имя_сервера запрос</code>\n"
            "Или просто упомяните <code>@имя_сервера</code> в сообщении."
        )
        await message.answer(
            text, reply_markup=inline.get_mcp_list_keyboard(servers), parse_mode="HTML"
        )
        return

    # Вызов сервера с параметрами
    payload = args[1].split(maxsplit=1)
    server_name = payload[0]
    prompt = payload[1] if len(payload) > 1 else ""

    gemini_prompt = f"@{server_name} {prompt}"
    from gateway.bot.handlers.messages import process_gemini_prompt

    await process_gemini_prompt(
        bot,
        message.chat.id,
        message.from_user.id,
        gemini_prompt,
        session_manager,
        config,
        user_settings,
    )


@router.message(Command("skills"))
async def command_skills_handler(
    message: Message,
    session_manager: SessionManager,
    bot: aiogram.Bot,
    config: Config,
    user_settings: UserSettingsStore,
) -> None:
    """Обработчик команды /skills."""
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        await message.answer(
            "⏳ <i>Запрашиваю список навыков...</i>", parse_mode="HTML"
        )
        skills = await session_manager.get_skills_list()

        # Fallback для пустого списка
        if not skills:
            await message.answer(
                "🧠 <b>Навыки не найдены</b>\n\n"
                "Установите их через <code>gemini skills install</code>.",
                parse_mode="HTML",
            )
            return

        text = "🧠 <b>Установленные навыки:</b>\n\n"
        for name, enabled in skills:
            icon = "🟢" if enabled else "🔴"
            status_text = "" if enabled else " <i>(отключен)</i>"
            text += f"{icon} <code>{name}</code>{status_text}\n"

        text += (
            "\n💡 Включайте и выключайте навыки кнопками ниже.\n"
            "Чтобы принудительно запустить навык, напишите: "
            "<code>/skills имя_навыка запрос</code>"
        )
        await message.answer(
            text,
            reply_markup=inline.get_skills_list_keyboard(skills),
            parse_mode="HTML",
        )
        return

    payload = args[1].split(maxsplit=1)
    skill_name = payload[0]
    prompt = payload[1] if len(payload) > 1 else ""

    gemini_prompt = f"@{skill_name} {prompt}"
    from gateway.bot.handlers.messages import process_gemini_prompt

    await process_gemini_prompt(
        bot,
        message.chat.id,
        message.from_user.id,
        gemini_prompt,
        session_manager,
        config,
        user_settings,
    )


@router.message(Command("status"))
async def command_status_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    bot: aiogram.Bot,
    runtime_state: GatewayRuntimeState,
) -> None:
    """Обработчик команды /status."""
    status_text = await build_status_text(
        config,
        runtime_state,
        session_manager,
        bot=bot,
        refresh_webhook=True,
    )
    await message.answer(status_text)


@router.message(Command("diagnostics"))
async def command_diagnostics_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    runtime_state: GatewayRuntimeState,
) -> None:
    """Обработчик команды /diagnostics."""
    await message.answer(build_diagnostics_text(config, runtime_state, session_manager))


@router.message(Command("cancel"))
async def command_cancel_handler(
    message: Message,
    session_manager: SessionManager,
) -> None:
    """Останавливает активный запрос Gemini для текущего пользователя."""
    cancelled = await session_manager.cancel_active_prompt(
        message.from_user.id,
        reason="user requested /cancel",
    )
    if cancelled:
        await message.answer("⏹ Активный запрос остановлен.")
    else:
        await message.answer("Активного запроса нет.")


@router.message(Command("model"))
async def command_model_handler(message: Message, config: Config) -> None:
    """Обработчик команды /model — выбор модели Gemini."""
    await message.answer(
        "Выберите модель Gemini:",
        reply_markup=inline.get_models_keyboard(config.gemini_model),
    )


@router.message(Command("settings"))
async def command_settings_handler(
    message: Message, config: Config, user_settings: UserSettingsStore
) -> None:
    """Обработчик команды /settings — настройки CLI."""
    render_mode = user_settings.get_render_mode(message.from_user.id)
    await message.answer(
        build_settings_text(config, render_mode),
        reply_markup=inline.get_settings_keyboard(
            render_mode=render_mode,
            approval_mode=config.gemini_approval_mode,
        ),
    )


@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """Обработчик команды /help."""
    text = (
        "📖 <b>Справка по Gemini Gateway</b>\n\n"
        "Бот транслирует ваши сообщения в Gemini CLI.\n"
        "Контекст переписки сохраняется автоматически.\n\n"
        "<b>Доступные команды:</b>\n"
        "/new — очистить историю и начать новый диалог\n"
        "/sessions — открыть один из прошлых диалогов\n"
        "/mcp — просмотреть MCP-серверы\n"
        "/skills — просмотреть навыки\n"
        "/cancel — остановить текущий запрос\n"
        "/settings — открыть настройки отображения и режима работы\n"
        "/status — проверить состояние шлюза\n"
        "/diagnostics — показать диагностический отчёт\n"
    )
    await message.answer(text)
