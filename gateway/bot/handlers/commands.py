import aiogram
from collections.abc import Awaitable, Callable
from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.bot.sessions import build_sessions_page
from gateway.bot.ui import build_settings_text
from gateway.config import Config
from gateway.doctor import format_doctor_text, run_doctor
from gateway.gemini.session import SessionManager
from gateway.init_wizard import InitWizardStore
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
        f"📂 /sessions [фильтр|latest] — список прошлых диалогов\n"
        f"⏹ /cancel — остановить текущий запрос\n"
        f"⚙️ /settings — настройки отображения и режима работы\n"
        f"🧩 /init — настроить личный GEMINI.md\n"
        f"🧭 /context — текущая модель и контекст\n"
        f"📊 /usage — расход токенов за день\n"
        f"🩺 /doctor — проверка окружения\n"
        f"🟢 /status — состояние шлюза\n"
        f"ℹ️ /help — справка"
    )
    active_session = session_manager.get_active_session(message.from_user.id)
    if active_session:
        text += (
            "\n\n"
            f"Текущий диалог: <code>{html.quote(active_session)}</code>\n"
            "Команда /start не сбрасывает контекст."
        )
    await message.answer(text)


@router.message(Command("new"))
async def command_new_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /new — сброс контекста Gemini."""
    await session_manager.reset(message.from_user.id, reason="/new")
    await message.answer(
        "✅ Контекст полностью очищен. Следующее сообщение начнет новый диалог."
    )


@router.message(Command("init"))
async def command_init_handler(
    message: Message,
    config: Config,
    init_wizard: InitWizardStore,
) -> None:
    """Запускает wizard создания личного GEMINI.md."""
    if not config.gateway_experimental_multi_user_workspaces:
        await message.answer(
            "🧩 /init доступен только в experimental multi-user workspace режиме.\n\n"
            "Включите в .env:\n"
            "<code>GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES=true</code>\n"
            "и перезапустите сервис."
        )
        return

    args = message.text.split(maxsplit=1) if message.text else ["/init"]
    action = args[1].strip().lower() if len(args) > 1 else ""
    user_id = message.from_user.id
    prefix = ""
    if action == "reset":
        init_wizard.reset(user_id)
        prefix = (
            "♻️ Анкета сброшена. Текущий GEMINI.md останется активным "
            "до подтверждения нового preview.\n\n"
        )
    elif init_wizard.has_pending(user_id):
        await message.answer(
            "🧩 Анкета уже начата.\n\n"
            f"<b>Текущий вопрос:</b>\n{html.quote(init_wizard.current_question(user_id))}"
        )
        return

    question = init_wizard.start(user_id)
    await message.answer(
        prefix + "🧩 <b>Инициализация личного Gemini-профиля</b>\n\n"
        "Ответьте на несколько вопросов. После этого я покажу preview GEMINI.md "
        "и запишу файл только после подтверждения.\n\n"
        f"<b>Вопрос 1:</b> {html.quote(question)}"
    )


@router.message(Command("sessions"))
async def command_sessions_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /sessions."""
    args = message.text.split(maxsplit=1) if message.text else ["/sessions"]
    query = args[1].strip() if len(args) > 1 else ""
    status_message = await message.answer(
        "⏳ <i>Запрашиваю список диалогов...</i>", parse_mode="HTML"
    )
    try:
        sessions = await session_manager.get_sessions_list(message.from_user.id)
        if not sessions:
            await status_message.edit_text("📂 Сохранённые диалоги не найдены.")
            return

        if query.lower() == "latest":
            await session_manager.set_active_session(
                message.from_user.id,
                "latest",
            )
            await status_message.edit_text(
                "✅ <b>Диалог latest выбран.</b>\n"
                "Gemini CLI сам откроет самый свежий сохранённый диалог "
                "через <code>--resume latest</code>."
            )
            return

        if query:
            lowered_query = query.lower()
            sessions = [
                session
                for session in sessions
                if lowered_query in session.title.lower()
                or lowered_query in session.session_id.lower()
                or lowered_query == str(session.source_index)
            ]
            if not sessions:
                await status_message.edit_text(
                    "📂 По фильтру ничего не найдено.\n"
                    f"Фильтр: <code>{html.quote(query)}</code>"
                )
                return

        text, reply_markup = build_sessions_page(sessions)
        if query:
            text = f"🔎 Фильтр: <code>{html.quote(query)}</code>\n\n{text}"
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
        servers = await _load_capability_list(
            message,
            loader=session_manager.get_mcp_list,
            loading_text="⏳ <i>Загружаю список MCP-серверов...</i>",
        )
        await _answer_capability_list(
            message,
            config=config,
            items=servers,
            icon="🔌",
            title="Установленные MCP-серверы",
            empty_title="MCP-серверы не найдены",
            install_command="gemini mcp install",
            shared_label="MCP-конфигурация",
            keyboard_builder=inline.get_mcp_list_keyboard,
            usage_hint=(
                "Чтобы задействовать MCP в запросе, напишите: "
                "<code>/mcp имя_сервера запрос</code>\n"
                "Или просто упомяните <code>@имя_сервера</code> в сообщении."
            ),
        )
        return

    prefix, prompt = _split_capability_payload(args[1])
    await _run_prefixed_prompt(
        bot,
        message,
        prefix,
        prompt,
        session_manager,
        config,
        user_settings,
        usage_ledger,
        prompt_guard,
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
        skills = await _load_capability_list(
            message,
            loader=session_manager.get_skills_list,
            loading_text="⏳ <i>Запрашиваю список навыков...</i>",
        )
        await _answer_capability_list(
            message,
            config=config,
            items=skills,
            icon="🧠",
            title="Установленные навыки",
            empty_title="Навыки не найдены",
            install_command="gemini skills install",
            shared_label="skills-конфигурация",
            keyboard_builder=inline.get_skills_list_keyboard,
            usage_hint=(
                "Чтобы принудительно запустить навык, напишите: "
                "<code>/skills имя_навыка запрос</code>"
            ),
        )
        return

    prefix, prompt = _split_capability_payload(args[1])
    await _run_prefixed_prompt(
        bot,
        message,
        prefix,
        prompt,
        session_manager,
        config,
        user_settings,
        usage_ledger,
        prompt_guard,
    )


async def _load_capability_list(
    message: Message,
    *,
    loader: Callable[[], Awaitable[list[tuple[str, bool]]]],
    loading_text: str,
) -> list[tuple[str, bool]]:
    await message.answer(loading_text, parse_mode="HTML")
    return await loader()


async def _answer_capability_list(
    message: Message,
    *,
    config: Config,
    items: list[tuple[str, bool]],
    icon: str,
    title: str,
    empty_title: str,
    install_command: str,
    shared_label: str,
    keyboard_builder: Callable[..., object],
    usage_hint: str,
) -> None:
    if not items:
        await message.answer(
            f"{icon} <b>{empty_title}</b>\n\n"
            f"Установите их через <code>{install_command}</code>.",
            parse_mode="HTML",
        )
        return

    is_shared = config.gateway_experimental_multi_user_workspaces
    text = f"{icon} <b>{title}:</b>\n\n"
    if is_shared:
        text += (
            "⚠️ Experimental multi-user mode включён: "
            f"{shared_label} общая для всего systemd-пользователя. "
            "Переключатели скрыты.\n\n"
        )
    text += _format_capability_items(items)
    text += _capability_footer(allow_toggle=not is_shared, usage_hint=usage_hint)

    await message.answer(
        text,
        reply_markup=keyboard_builder(items, allow_toggle=not is_shared),
        parse_mode="HTML",
    )


def _format_capability_items(items: list[tuple[str, bool]]) -> str:
    lines = []
    for name, enabled in items:
        icon = "🟢" if enabled else "🔴"
        status_text = "" if enabled else " <i>(отключен)</i>"
        lines.append(f"{icon} <code>{html.quote(name)}</code>{status_text}")
    return "\n".join(lines) + "\n"


def _capability_footer(*, allow_toggle: bool, usage_hint: str) -> str:
    action_line = (
        "💡 Включайте и выключайте кнопками ниже."
        if allow_toggle
        else "💡 Кнопка reload перечитывает список."
    )
    return (
        f"\n{action_line}\n"
        "Reload в интерактивном CLI недоступен как headless subcommand; "
        "кнопка перечитывает список.\n"
        f"{usage_hint}"
    )


def _split_capability_payload(payload: str) -> tuple[str, str]:
    parts = payload.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


async def _run_prefixed_prompt(
    bot: aiogram.Bot,
    message: Message,
    prefix: str,
    prompt: str,
    session_manager: SessionManager,
    config: Config,
    user_settings: UserSettingsStore,
    usage_ledger: UsageLedger,
    prompt_guard: PendingPromptStore,
) -> None:
    from gateway.bot.handlers.messages import process_gemini_prompt

    await process_gemini_prompt(
        bot,
        message.chat.id,
        message.from_user.id,
        f"@{prefix} {prompt}",
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
    runtime_state: GatewayRuntimeState,
) -> None:
    """Показывает текущий контекст пользователя."""
    user_id = message.from_user.id
    preset = user_settings.get_model_preset(user_id)
    model = user_settings.get_effective_model(user_id, config.gemini_model)
    preset_label = get_model_preset_label(preset)
    active_session = session_manager.get_active_session(user_id) or "новый диалог"
    active_session_source = session_manager.get_active_session_source(user_id)
    environment = session_manager.user_environments.describe_for(user_id)
    include_dirs = (
        ", ".join(config.gemini_include_directories)
        if config.gemini_include_directories
        else "нет"
    )
    trust_mode = "--skip-trust" if config.gemini_skip_trust else "external/env"
    policy_paths = (
        ", ".join(config.gemini_policy_paths) if config.gemini_policy_paths else "нет"
    )
    admin_policy_paths = (
        ", ".join(config.gemini_admin_policy_paths)
        if config.gemini_admin_policy_paths
        else "нет"
    )
    extensions = (
        ", ".join(config.gemini_extensions) if config.gemini_extensions else "все"
    )
    mcp_allowlist = (
        ", ".join(config.gemini_allowed_mcp_server_names)
        if config.gemini_allowed_mcp_server_names
        else "нет"
    )
    current_gemini = (
        runtime_state.gemini_probe.version
        if runtime_state.gemini_probe
        else "не проверено"
    )
    active_prompt = "да" if session_manager.has_active_prompt(user_id) else "нет"
    await message.answer(
        "🧭 <b>Текущий контекст</b>\n\n"
        f"<b>Модель:</b> <code>{html.quote(model)}</code>\n"
        f"<b>Пресет:</b> {html.quote(preset_label)}\n"
        f"<b>Gemini CLI:</b> <code>{html.quote(current_gemini)}</code> "
        f"(target <code>{html.quote(config.gemini_target_version)}</code>)\n"
        f"<b>Session:</b> <code>{html.quote(active_session)}</code>\n"
        f"<b>Session source:</b> <code>{html.quote(active_session_source)}</code>\n"
        f"<b>Isolation:</b> <code>{html.quote(environment['mode'])}</code>\n"
        f"<b>Shared auth/HOME:</b> <code>{html.quote(environment['shared_auth'])}</code>\n"
        f"<b>Working dir:</b> <code>{html.quote(environment['working_dir'])}</code>\n"
        f"<b>Artifacts:</b> <code>{html.quote(environment['artifact_roots'])}</code>\n"
        + (
            f"<b>GEMINI.md:</b> <code>{html.quote(environment.get('gemini_md', ''))}</code>\n"
            if environment["mode"] == "multi-user"
            else ""
        )
        + f"<b>Include dirs:</b> <code>{html.quote(include_dirs)}</code>\n"
        f"<b>Approval:</b> <code>{html.quote(config.gemini_approval_mode)}</code>\n"
        f"<b>Trust:</b> <code>{html.quote(trust_mode)}</code>\n"
        f"<b>Policy:</b> <code>{html.quote(policy_paths)}</code>\n"
        f"<b>Admin policy:</b> <code>{html.quote(admin_policy_paths)}</code>\n"
        f"<b>Extensions:</b> <code>{html.quote(extensions)}</code>\n"
        f"<b>MCP allowlist:</b> <code>{html.quote(mcp_allowlist)}</code>\n"
        f"<b>Screen reader:</b> {'да' if config.gemini_screen_reader else 'нет'}\n"
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
            f"thinking={last_request.get('thoughts_tokens', 0)}, "
            f"{last_request.get('duration_ms', 0)}ms, "
            f"{last_request.get('model', 'unknown')}, "
            f"status={last_request.get('result_status') or 'unknown'}"
        )
    )
    stats_line = _format_last_stats(last_request.get("stats") if last_request else None)
    await message.answer(
        "📊 <b>Usage за сегодня</b>\n\n"
        f"<b>Дата:</b> {snapshot.date}\n"
        f"<b>Вы:</b> {user_limit}\n"
        f"<b>Всего:</b> {global_limit}\n"
        f"<b>Последний запрос:</b> <code>{html.quote(last_line)}</code>"
        f"{stats_line}"
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
        "/sessions [фильтр|latest] — открыть один из прошлых диалогов\n"
        "/mcp — просмотреть MCP-серверы\n"
        "/skills — просмотреть навыки\n"
        "/init — настроить личный GEMINI.md\n"
        "/init reset — сбросить анкету и сделать новый preview\n"
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


def _format_last_stats(stats) -> str:
    if not isinstance(stats, dict):
        return ""
    candidates = []
    for key in ("models", "per_model", "perModel", "model_usage", "modelUsage"):
        value = stats.get(key)
        if isinstance(value, dict):
            candidates.extend(
                f"{name}: {_format_model_stats(payload)}"
                for name, payload in value.items()
                if isinstance(payload, dict)
            )
        elif isinstance(value, list):
            for payload in value:
                if not isinstance(payload, dict):
                    continue
                name = payload.get("model") or payload.get("name") or "model"
                candidates.append(f"{name}: {_format_model_stats(payload)}")
    if not candidates:
        return ""
    return (
        "\n<b>По моделям:</b> <code>"
        + html.quote(", ".join(candidates[:5]))
        + "</code>"
    )


def _format_model_stats(payload: dict) -> str:
    parts = []
    token_keys = (
        ("total", "total_tokens", "totalTokens"),
        ("in", "input_tokens", "inputTokens"),
        ("out", "output_tokens", "outputTokens"),
        ("think", "thoughts_tokens", "thoughtsTokens"),
    )
    for label, snake_key, camel_key in token_keys:
        value = payload.get(snake_key, payload.get(camel_key))
        if value is not None:
            parts.append(f"{label}={value}")
    return "/".join(parts) if parts else "?"
