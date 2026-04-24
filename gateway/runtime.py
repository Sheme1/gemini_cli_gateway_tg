from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from gateway.config import Config

if TYPE_CHECKING:
    from aiogram import Bot

    from gateway.gemini.session import SessionManager

logger = logging.getLogger(__name__)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


@dataclass
class CommandProbe:
    command: str
    path: str
    version: str


@dataclass
class PromptLatencySnapshot:
    user_id: int
    started_at: float
    process_spawn_ms: int | None = None
    init_ms: int | None = None
    first_text_ms: int | None = None
    total_ms: int | None = None
    returncode: int | None = None


@dataclass
class GatewayRuntimeState:
    started_at: float = field(default_factory=time.time)
    bot_id: int | None = None
    bot_username: str = ""
    bot_full_name: str = ""
    gemini_probe: CommandProbe | None = None
    node_probe: CommandProbe | None = None
    home_dir: str = ""
    state_dir: str = ""
    webhook_url: str = ""
    webhook_pending_updates: int | None = None
    last_error: str = ""
    last_error_context: str = ""
    last_error_at: float | None = None
    last_prompt_latency: PromptLatencySnapshot | None = None

    def record_error(self, exc: BaseException, context: str = "") -> None:
        self.last_error = _sanitize_error(f"{type(exc).__name__}: {exc}")
        self.last_error_context = context
        self.last_error_at = time.time()

    def record_prompt_latency(self, snapshot: PromptLatencySnapshot) -> None:
        self.last_prompt_latency = snapshot

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)


async def probe_command(
    command: str, *args: str, cwd: str | None = None
) -> CommandProbe:
    executable = shutil.which(command) or command
    process = await asyncio.create_subprocess_exec(
        executable,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{command} did not answer within 10 seconds") from exc

    output = (stdout + stderr).decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(
            f"{command} exited with code {process.returncode}: {output[:500]}"
        )

    first_line = output.splitlines()[0].strip() if output else "unknown"
    return CommandProbe(command=command, path=executable, version=first_line)


async def startup_preflight(
    config: Config,
    bot: Bot,
    runtime_state: GatewayRuntimeState,
) -> None:
    """Проверить окружение до старта polling, чтобы сервис не молчал в фоне."""
    logger.info("Runtime config: %s", config.redacted_dict())

    working_dir = Path(config.gemini_working_dir)
    if not working_dir.exists() or not working_dir.is_dir():
        raise RuntimeError(f"GEMINI_WORKING_DIR is not a directory: {working_dir}")

    home_dir = Path.home()
    if not home_dir.exists() or not home_dir.is_dir():
        raise RuntimeError(f"HOME is not a directory: {home_dir}")
    runtime_state.home_dir = str(home_dir)

    state_dir = Path(config.gateway_state_dir)
    _check_state_dir(state_dir)
    runtime_state.state_dir = str(state_dir)

    runtime_state.gemini_probe = await probe_command(
        config.gemini_bin,
        "--version",
        cwd=config.gemini_working_dir,
    )
    runtime_state.node_probe = await probe_command("node", "--version")

    me = await bot.get_me(request_timeout=config.polling_timeout)
    runtime_state.bot_id = me.id
    runtime_state.bot_username = me.username or ""
    runtime_state.bot_full_name = me.full_name
    logger.info(
        "Telegram bot identity: id=%s username=%s name=%s",
        runtime_state.bot_id,
        runtime_state.bot_username or "<none>",
        runtime_state.bot_full_name,
    )

    await refresh_webhook_state(bot, config, runtime_state)
    if runtime_state.webhook_url:
        logger.warning(
            "Telegram webhook is set. Deleting it before long polling: %s",
            runtime_state.webhook_url,
        )
        await bot.delete_webhook(
            drop_pending_updates=False,
            request_timeout=config.polling_timeout,
        )
        await refresh_webhook_state(bot, config, runtime_state)


async def refresh_webhook_state(
    bot: Bot,
    config: Config,
    runtime_state: GatewayRuntimeState,
) -> None:
    webhook_info = await bot.get_webhook_info(request_timeout=config.polling_timeout)
    runtime_state.webhook_url = getattr(webhook_info, "url", "") or ""
    runtime_state.webhook_pending_updates = getattr(
        webhook_info,
        "pending_update_count",
        None,
    )


async def build_status_text(
    config: Config,
    runtime_state: GatewayRuntimeState,
    session_manager: SessionManager,
    *,
    bot: Bot | None = None,
    refresh_webhook: bool = False,
) -> str:
    if bot and refresh_webhook:
        try:
            await refresh_webhook_state(bot, config, runtime_state)
        except Exception as exc:  # pragma: no cover - defensive status fallback
            runtime_state.record_error(exc, context="/status webhook refresh")

    webhook_state = "выключен" if not runtime_state.webhook_url else "включен"
    gemini = _probe_line(runtime_state.gemini_probe)
    node = _probe_line(runtime_state.node_probe)
    last_error = _last_error_line(runtime_state)
    latency = _latency_line(runtime_state.last_prompt_latency)

    return (
        "🟢 <b>Статус шлюза</b>\n\n"
        f"<b>Uptime:</b> {_format_duration(runtime_state.uptime_seconds)}\n"
        f"<b>Bot:</b> {escape(_bot_label(runtime_state))}\n"
        f"<b>Gemini:</b> {escape(gemini)}\n"
        f"<b>Node.js:</b> {escape(node)}\n"
        f"<b>Working dir:</b> <code>{escape(config.gemini_working_dir)}</code>\n"
        f"<b>Активных запросов:</b> {session_manager.active_prompt_count()}\n"
        f"<b>Последний запрос:</b> {escape(latency)}\n"
        f"<b>Webhook:</b> {escape(webhook_state)}"
        f" ({runtime_state.webhook_pending_updates or 0} pending)\n"
        f"<b>Последняя ошибка:</b> {escape(last_error)}"
    )


def build_diagnostics_text(
    config: Config,
    runtime_state: GatewayRuntimeState,
    session_manager: SessionManager,
) -> str:
    config_lines = "\n".join(
        f"{key}={value}" for key, value in sorted(config.redacted_dict().items())
    )
    return (
        "🧪 <b>Diagnostics</b>\n\n"
        f"<b>Runtime:</b>\n"
        f"<code>{escape(_runtime_snapshot(runtime_state, session_manager))}</code>\n\n"
        f"<b>Config:</b>\n<code>{escape(config_lines)}</code>"
    )


def _check_state_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _probe_line(probe: CommandProbe | None) -> str:
    if probe is None:
        return "не проверено"
    return f"{probe.version} ({probe.path})"


def _bot_label(runtime_state: GatewayRuntimeState) -> str:
    if runtime_state.bot_username:
        return f"@{runtime_state.bot_username} ({runtime_state.bot_id})"
    if runtime_state.bot_full_name:
        return f"{runtime_state.bot_full_name} ({runtime_state.bot_id})"
    return "не проверено"


def _last_error_line(runtime_state: GatewayRuntimeState) -> str:
    if not runtime_state.last_error:
        return "нет"
    age = int(time.time() - (runtime_state.last_error_at or time.time()))
    context = (
        f" [{runtime_state.last_error_context}]"
        if runtime_state.last_error_context
        else ""
    )
    return f"{runtime_state.last_error}{context}, {age}s ago"


def _runtime_snapshot(
    runtime_state: GatewayRuntimeState,
    session_manager: SessionManager,
) -> str:
    lines = [
        f"uptime={_format_duration(runtime_state.uptime_seconds)}",
        f"bot={_bot_label(runtime_state)}",
        f"gemini={_probe_line(runtime_state.gemini_probe)}",
        f"node={_probe_line(runtime_state.node_probe)}",
        f"home={runtime_state.home_dir or 'unknown'}",
        f"state_dir={runtime_state.state_dir or 'unknown'}",
        f"webhook_url={'set' if runtime_state.webhook_url else 'empty'}",
        f"webhook_pending_updates={runtime_state.webhook_pending_updates or 0}",
        f"active_prompt_users={session_manager.active_prompt_users()}",
        f"last_prompt_latency={_latency_line(runtime_state.last_prompt_latency)}",
        f"last_error={_last_error_line(runtime_state)}",
    ]
    return "\n".join(lines)


def _format_duration(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _sanitize_error(text: str) -> str:
    return _TELEGRAM_TOKEN_RE.sub("<telegram-token>", text)


def _latency_line(snapshot: PromptLatencySnapshot | None) -> str:
    if snapshot is None:
        return "нет данных"

    parts = [
        f"user={snapshot.user_id}",
        f"spawn={_format_ms(snapshot.process_spawn_ms)}",
        f"init={_format_ms(snapshot.init_ms)}",
        f"first_text={_format_ms(snapshot.first_text_ms)}",
        f"total={_format_ms(snapshot.total_ms)}",
        f"rc={snapshot.returncode}",
    ]
    age = int(time.time() - snapshot.started_at)
    parts.append(f"{age}s ago")
    return ", ".join(parts)


def _format_ms(value: int | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value}ms"
