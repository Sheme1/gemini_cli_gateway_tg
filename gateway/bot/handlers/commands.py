import aiogram
from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.bot.sessions import build_sessions_page
from gateway.bot.ui import build_settings_text
from gateway.config import Config
from gateway.doctor import format_doctor_text, run_doctor
from gateway.gemini.session import SessionManager
from gateway.model_presets import MODEL_PRESETS, get_model_preset_label
from gateway.prompt_guard import PendingPromptStore
from gateway.runtime import (
    GatewayRuntimeState,
    build_diagnostics_text,
    build_status_text,
)
from gateway.usage import UsageLedger
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
        f"🧭 /context — текущая модель и контекст\n"
        f"📊 /usage — расход токенов за день\n"
        f"🩺 /doctor — проверка окружения\n"
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
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
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
        usage_ledger=usage_ledger,
        prompt_guard=prompt_guard,
    )


@router.message(Command("skills"))
async def command_skills_handler(
    message: Message,
    session_manager: SessionManager,
    bot: aiogram.Bot,
    config: Config,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
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
        usage_ledger=usage_ledger,
        prompt_guard=prompt_guard,
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


@router.message(Command("doctor"))
async def command_doctor_handler(message: Message) -> None:
    """Обработчик команды /doctor."""
    status_message = await message.answer("🩺 Проверяю окружение gateway...")
    report = await run_doctor()
    await status_message.edit_text(format_doctor_text(report, html=True))


@router.message(Command("context"))
async def command_context_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    user_settings: UserSettingsStore,
) -> None:
    """Показывает текущий контекст пользователя."""
    user_id = message.from_user.id
    preset = user_settings.get_model_preset(user_id)
    model = user_settings.get_effective_model(user_id, config.gemini_model)
    preset_label = get_model_preset_label(preset)
    active_session = session_manager.get_active_session(user_id) or "новый диалог"
    include_dirs = (
        ", ".join(config.gemini_include_directories)
        if config.gemini_include_directories
        else "нет"
    )
    trust_mode = "--skip-trust" if config.gemini_skip_trust else "external/env"
    active_prompt = "да" if session_manager.has_active_prompt(user_id) else "нет"
    await message.answer(
        "🧭 <b>Текущий контекст</b>\n\n"
        f"<b>Модель:</b> <code>{html.quote(model)}</code>\n"
        f"<b>Пресет:</b> {html.quote(preset_label)}\n"
        f"<b>Session:</b> <code>{html.quote(active_session)}</code>\n"
        f"<b>Working dir:</b> <code>{html.quote(config.gemini_working_dir)}</code>\n"
        f"<b>Include dirs:</b> <code>{html.quote(include_dirs)}</code>\n"
        f"<b>Trust:</b> <code>{html.quote(trust_mode)}</code>\n"
        f"<b>Активный запрос:</b> {active_prompt}\n"
        f"<b>Prompt warn/max:</b> {config.prompt_warn_chars}/{config.prompt_max_chars} chars"
    )


@router.message(Command("usage"))
async def command_usage_handler(
    message: Message,
    config: Config,
    usage_ledger: UsageLedger,
) -> None:
    """Показывает расход токенов за день."""
    snapshot = usage_ledger.snapshot(
        message.from_user.id,
        user_limit=config.user_daily_token_limit,
        global_limit=config.global_daily_token_limit,
    )
    user_limit = _format_limit(snapshot.user_tokens, snapshot.user_limit)
    global_limit = _format_limit(snapshot.global_tokens, snapshot.global_limit)
    last_request = snapshot.last_request or {}
    last_line = (
        "нет данных"
        if not last_request
        else (
            f"{last_request.get('total_tokens', 0)} tokens, "
            f"{last_request.get('duration_ms', 0)}ms, "
            f"{last_request.get('model', 'unknown')}"
        )
    )
    await message.answer(
        "📊 <b>Usage за сегодня</b>\n\n"
        f"<b>Дата:</b> {snapshot.date}\n"
        f"<b>Вы:</b> {user_limit}\n"
        f"<b>Всего:</b> {global_limit}\n"
        f"<b>Последний запрос:</b> <code>{html.quote(last_line)}</code>"
    )


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
async def command_model_handler(
    message: Message, config: Config, user_settings: UserSettingsStore
) -> None:
    """Обработчик команды /model — выбор модели Gemini."""
    preset = user_settings.get_model_preset(message.from_user.id)
    model = user_settings.get_effective_model(message.from_user.id, config.gemini_model)
    preset_lines = "\n".join(
        f"• <b>{preset_info.label}</b> — <code>{preset_info.model}</code>. "
        f"{preset_info.description}"
        for preset_info in MODEL_PRESETS.values()
    )
    await message.answer(
        "Выберите модель Gemini для ваших запросов:\n\n" + preset_lines,
        reply_markup=inline.get_models_keyboard(
            current_model=model,
            current_preset=preset,
            fallback_model=config.gemini_model,
        ),
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
        "/model — выбрать per-user модельный пресет\n"
        "/context — показать текущую модель, session_id и рабочие папки\n"
        "/usage — показать дневной расход токенов\n"
        "/doctor — проверить окружение без запуска polling\n"
        "/status — проверить состояние шлюза\n"
        "/diagnostics — показать диагностический отчёт\n"
    )
    await message.answer(text)


def _format_limit(used: int, limit: int) -> str:
    if limit <= 0:
        return f"{used} tokens (лимит выключен)"
    return f"{used}/{limit} tokens"
