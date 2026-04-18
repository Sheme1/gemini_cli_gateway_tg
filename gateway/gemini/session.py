import asyncio
import logging
import re
from typing import Callable

from gateway.config import Config
from gateway.gemini.parser import GeminiStreamParser, StreamEvent

logger = logging.getLogger(__name__)


def strip_ansi_codes(text: str) -> str:
    """Удаляет ANSI escape коды из текста."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


class SessionManager:
    """Управляет сессиями Gemini CLI."""

    def __init__(self, config: Config):
        self.config = config
        # user_id -> gemini_session_id
        self.active_sessions: dict[int, str] = {}

    async def get_sessions_list(self) -> list[tuple[str, str]]:
        """Возвращает актуальный список сессий: список кортежей (id, описание)."""
        process = await asyncio.create_subprocess_exec(
            "gemini",
            "--list-sessions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.gemini_working_dir,
        )
        stdout, _ = await process.communicate()
        lines = stdout.decode("utf-8").splitlines()

        sessions = []
        for line in lines:
            line = line.strip()
            # Пропускаем заголовки, логи и пустые строки
            if (
                not line
                or "No previous" in line
                or "Available sessions" in line
                or "Keychain" in line
                or "Loaded" in line
                or "Using" in line
                or "error:" in line.lower()
            ):
                continue

            # Формат: "8. Узнать погоду. (Just now) [ed4342c5-efa4-48ff-8846-6ee37b8efb64]"
            match = re.search(
                r"^\s*\d+\.\s*(.*?)\s*\((.*?)\)\s*\[([a-fA-F0-9\-]+)\]", line
            )
            if match:
                desc_raw = match.group(1).strip().strip(":")
                time_ago = match.group(2).strip()
                session_id = match.group(3).strip()

                desc = desc_raw if desc_raw else "Без описания"
                desc = f"{desc} ({time_ago})"
                desc = desc[:60] + "..." if len(desc) > 60 else desc

                sessions.append((session_id, desc))
        return sessions

    async def get_mcp_list(self) -> list[tuple[str, bool]]:
        """Возвращает актуальный список MCP серверов: (имя, включен_ли)."""
        process = await asyncio.create_subprocess_exec(
            "gemini",
            "mcp",
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # Читаем stderr тоже
            cwd=self.config.gemini_working_dir,
        )
        stdout, stderr = await process.communicate()
        # Объединяем stdout и stderr, так как вывод может быть в любом из них
        output = stdout.decode("utf-8") + stderr.decode("utf-8")
        # Удаляем ANSI escape коды
        output = strip_ansi_codes(output)
        lines = output.splitlines()

        logger.debug(f"Raw output from 'gemini mcp list':\n{output}")

        mcp_servers = []
        for line in lines:
            line = line.strip()
            # Пропускаем служебные строки
            if not line or "Configured MCP servers:" in line or "Loaded cached" in line:
                continue

            # Формат: "✓ exa: ..." или "✗ context7: ..." или "✓ chrome-devtools (from ...): ..."
            # Улавливаем статус (✓/✗), имя (до двоеточия, без "(from ...)")
            match = re.search(
                r"^([✓✗xX])\s+([a-zA-Z0-9_\-]+)(?:\s+\(from [^)]+\))?:", line
            )
            if match:
                status_icon = match.group(1).strip()
                name = match.group(2).strip()
                is_enabled = status_icon == "✓"
                mcp_servers.append((name, is_enabled))
                logger.debug(f"Parsed MCP server: {name} (enabled={is_enabled})")

        logger.info(f"Found {len(mcp_servers)} MCP servers")
        return mcp_servers

    async def get_skills_list(self) -> list[tuple[str, bool]]:
        """Возвращает актуальный список Skills: (имя, включен_ли)."""
        process = await asyncio.create_subprocess_exec(
            "gemini",
            "skills",
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # Читаем stderr тоже
            cwd=self.config.gemini_working_dir,
        )
        stdout, stderr = await process.communicate()
        # Объединяем stdout и stderr, так как вывод может быть в любом из них
        output = stdout.decode("utf-8") + stderr.decode("utf-8")
        # Удаляем ANSI escape коды
        output = strip_ansi_codes(output)
        lines = output.splitlines()

        logger.debug(f"Raw output from 'gemini skills list':\n{output}")

        skills_list = []
        for line in lines:
            line = line.strip()
            # Пропускаем служебные строки (логи, заголовки, пустые строки)
            if (
                not line
                or "Loaded cached" in line
                or "Loading extension" in line
                or "Scheduling MCP" in line
                or "Executing MCP" in line
                or "MCP context refresh" in line
                or "Registering notification" in line
                or ("Server" in line and "supports" in line)
                or "Discovered Agent Skills:" in line
                or "Description:" in line
                or "Location:" in line
                or line.startswith("Capabilities:")
            ):
                continue

            # Формат: "a11y-debugging [Enabled]"
            match = re.search(
                r"^([a-zA-Z0-9_\-]+)\s+\[(Enabled|Disabled)\]", line, re.IGNORECASE
            )
            if match:
                name = match.group(1).strip()
                status = match.group(2).strip().lower()
                is_enabled = status == "enabled"
                skills_list.append((name, is_enabled))
                logger.debug(f"Parsed skill: {name} (enabled={is_enabled})")

        logger.info(f"Found {len(skills_list)} skills")
        return skills_list

    async def toggle_mcp(self, name: str, enable: bool) -> bool:
        cmd = "enable" if enable else "disable"
        process = await asyncio.create_subprocess_exec(
            "gemini",
            "mcp",
            cmd,
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.gemini_working_dir,
        )
        await process.wait()
        return process.returncode == 0

    async def toggle_skill(self, name: str, enable: bool) -> bool:
        cmd = "enable" if enable else "disable"
        process = await asyncio.create_subprocess_exec(
            "gemini",
            "skills",
            cmd,
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.gemini_working_dir,
        )
        await process.wait()
        return process.returncode == 0

    async def is_alive(self) -> bool:
        return True

    async def kill(self) -> None:
        pass

    async def reset(self, user_id: int) -> None:
        """Сброс контекста (/new): очистка привязанного session_id."""
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            logger.info(f"Cleared session context for user {user_id}")

    async def set_active_session(self, user_id: int, session_id: str) -> None:
        self.active_sessions[user_id] = session_id
        logger.info(f"Set active session {session_id} for user {user_id}")

    async def send_prompt(
        self,
        prompt: str,
        user_id: int,
        on_event: Callable[[StreamEvent], asyncio.Future],
        on_approval: Callable[[dict], asyncio.Future],
    ) -> None:
        """Отправить промпт в одноразовый процесс и стримить ответ."""
        args = [
            "gemini",
            "-m",
            self.config.gemini_model,
            "-o",
            "stream-json",
            "-p",
            prompt,
        ]
        args.extend(self.config.approval_mode_flag)
        args.extend(self.config.sandbox_flag)

        session_id = self.active_sessions.get(user_id)
        if session_id:
            args.extend(["--resume", session_id])

        logger.info(f"Spawning Gemini for user {user_id}: {' '.join(args)}")
        logger.debug(f"Prompt preview: {prompt[:100]}...")

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # Отбрасываем stderr — избегаем deadlock
            cwd=self.config.gemini_working_dir,
        )

        timeout = self.config.gemini_cli_timeout
        heartbeat_interval = 30  # Heartbeat каждые 30 секунд
        last_activity = asyncio.get_event_loop().time()
        heartbeat_task = None
        tool_names_by_id: dict[str, str] = {}
        seen_assistant_text = False
        logged_tool_before_text = False
        log_stream = logger.info if self.config.gemini_stream_debug else logger.debug

        async def send_heartbeat():
            """Периодически отправляет heartbeat если нет активности."""
            nonlocal last_activity
            heartbeat_count = 0
            while True:
                await asyncio.sleep(heartbeat_interval)
                current_time = asyncio.get_event_loop().time()
                idle_time = current_time - last_activity

                if idle_time >= heartbeat_interval:
                    heartbeat_count += 1
                    elapsed_total = int(idle_time)
                    await on_event(
                        StreamEvent(
                            event_type="heartbeat",
                            status_message=f"Обработка... ({elapsed_total}с)",
                        )
                    )
                    logger.debug(f"Heartbeat #{heartbeat_count}: {elapsed_total}s idle")

        try:
            # Запускаем heartbeat task
            heartbeat_task = asyncio.create_task(send_heartbeat())

            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=timeout,
                    )
                    last_activity = (
                        asyncio.get_event_loop().time()
                    )  # Обновляем время активности
                except asyncio.TimeoutError:
                    elapsed = int(asyncio.get_event_loop().time() - last_activity)
                    logger.warning(f"Gemini CLI timeout after {elapsed}s of inactivity")
                    process.kill()

                    # Проверяем был ли хоть какой-то output
                    if elapsed > 0:
                        await on_event(
                            StreamEvent(
                                event_type="warning",
                                warning_message=(
                                    f"Таймаут: Gemini не отвечал {elapsed} секунд.\n"
                                    "Возможные причины:\n"
                                    "• Слишком сложный запрос для модели\n"
                                    "• Проблемы с MCP-серверами\n"
                                    "• Зависание CLI в non-interactive режиме\n\n"
                                    "Попробуйте:\n"
                                    "• Упростить запрос\n"
                                    "• Использовать /new для сброса контекста\n"
                                    "• Повторить попытку позже"
                                ),
                            )
                        )
                    else:
                        await on_event(
                            StreamEvent(
                                event_type="warning",
                                warning_message=(
                                    "Таймаут: Gemini не запустился.\n"
                                    "Проверьте логи сервера."
                                ),
                            )
                        )
                    break

                if not line_bytes:
                    # EOF — процесс завершился
                    break

                line_text = line_bytes.decode("utf-8").strip()
                if not line_text:
                    continue

                log_stream("[GEMINI RAW] %s", line_text)

                event = GeminiStreamParser.parse_line(line_text)

                # Логируем события для диагностики
                if event.event_type:
                    log_stream(
                        "Event: type=%s tool=%s tool_id=%s delta=%s has_text=%s "
                        "is_done=%s is_empty=%s is_invalid=%s",
                        event.event_type,
                        event.tool_name,
                        event.tool_id,
                        event.message_delta,
                        bool(event.assistant_text),
                        event.is_done,
                        event.is_empty_response,
                        bool(event.invalid_stream_reason),
                    )

                if event.event_type == "tool_use" and event.tool_id and event.tool_name:
                    tool_names_by_id[event.tool_id] = event.tool_name

                if event.event_type == "tool_result" and event.tool_id:
                    if not event.tool_name:
                        event.tool_name = tool_names_by_id.get(event.tool_id, "")
                    tool_names_by_id.pop(event.tool_id, None)

                if event.event_type == "assistant_text" and event.assistant_text:
                    seen_assistant_text = True

                if (
                    not seen_assistant_text
                    and not logged_tool_before_text
                    and event.event_type in {"tool_use", "tool_result"}
                ):
                    logger.info(
                        "Gemini stream progress detected before assistant text for user %s.",
                        user_id,
                    )
                    logged_tool_before_text = True

                # Захват session_id из init-события
                if event.session_id and not session_id:
                    self.active_sessions[user_id] = event.session_id
                    logger.info(
                        f"Captured session_id: {event.session_id} for user {user_id}"
                    )

                if event.event_type and event.event_type not in {"init", "approval_request"}:
                    await on_event(event)

                if event.event_type == "invalid_stream":
                    logger.warning("Received InvalidStream event, continuing...")
                    continue

                if event.is_empty_response:
                    logger.warning("Received empty response, notifying user...")
                    await on_event(
                        StreamEvent(
                            event_type="warning",
                            warning_message=(
                                "Модель вернула пустой ответ. "
                                "Попробуйте переформулировать запрос."
                            ),
                        )
                    )
                    break

                if event.approval_request:
                    await on_approval(event.approval_request)
                    break

                if event.is_done:
                    break

        finally:
            # Останавливаем heartbeat task
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Гарантируем что процесс завершён
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

            logger.info(
                f"Prompt processing completed for user {user_id}, "
                f"returncode={process.returncode}"
            )

    async def answer_approval(self, answer: str) -> None:
        # Заглушка. Headless режим не поддерживает интерактивный ввод.
        pass
