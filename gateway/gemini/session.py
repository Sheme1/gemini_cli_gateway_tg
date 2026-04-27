import asyncio
import logging
import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from gateway.config import Config
from gateway.gemini.error_classifier import classify_gemini_error
from gateway.gemini.parser import GeminiStreamParser, StreamEvent
from gateway.runtime import GatewayRuntimeState, PromptLatencySnapshot
from gateway.user_environment import UserEnvironmentResolver

logger = logging.getLogger(__name__)
_SESSION_LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\.\s*(?P<title>.*?)\s*"
    r"\((?P<meta>[^()]*)\)\s*\[(?P<session_id>[A-Za-z0-9-]+)\]\s*$"
)


@dataclass(frozen=True)
class GeminiSessionInfo:
    session_id: str
    title: str
    relative_time: str
    is_current: bool
    source_index: int
    sort_index: int

    @property
    def short_id(self) -> str:
        if len(self.session_id) <= 12:
            return self.session_id
        return f"{self.session_id[:8]}..."


def strip_ansi_codes(text: str) -> str:
    """Удаляет ANSI escape коды из текста."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def parse_gemini_sessions_output(output: str) -> list[GeminiSessionInfo]:
    """Разобрать человекочитаемый вывод `gemini --list-sessions`."""
    sessions: list[GeminiSessionInfo] = []
    for raw_line in strip_ansi_codes(output).splitlines():
        line = raw_line.strip()
        if not line or _is_session_list_noise(line):
            continue

        match = _SESSION_LINE_RE.match(line)
        if not match:
            logger.debug("Skipping unrecognized Gemini session line: %s", line)
            continue

        source_index = int(match.group("index"))
        meta_parts = [
            part.strip() for part in match.group("meta").split(",") if part.strip()
        ]
        is_current = any(part.lower() == "current" for part in meta_parts)
        relative_time = ", ".join(
            part for part in meta_parts if part.lower() != "current"
        )
        title = match.group("title").strip().strip(":") or "Без описания"

        sessions.append(
            GeminiSessionInfo(
                session_id=match.group("session_id").strip(),
                title=title,
                relative_time=relative_time or "unknown",
                is_current=is_current,
                source_index=source_index,
                sort_index=source_index,
            )
        )

    return sorted(sessions, key=lambda item: item.sort_index, reverse=True)


def _is_session_list_noise(line: str) -> bool:
    lower = line.lower()
    return (
        "no previous sessions" in lower
        or "available sessions" in lower
        or "keychain" in lower
        or "loaded" in lower
        or "using" in lower
        or lower.startswith("[warn]")
        or lower.startswith("warn")
        or lower.startswith("error:")
    )


class BoundedTextBuffer:
    """Хранит последние строки stderr без безлимитного роста памяти."""

    def __init__(self, limit: int = 4000):
        self.limit = limit
        self._text = ""

    def append(self, text: str) -> None:
        if not text:
            return
        self._text = (self._text + text)[-self.limit :]

    def text(self) -> str:
        return self._text.strip()


def _build_process_error(returncode: int, stderr_text: str) -> str:
    details = (
        f"Gemini CLI завершился с кодом {returncode}.\n\nstderr:\n{stderr_text[-1500:]}"
        if stderr_text
        else f"Gemini CLI завершился с кодом {returncode}."
    )
    return classify_gemini_error(details, returncode=returncode).format_for_user()


def _build_empty_stream_warning(stderr_text: str) -> str:
    if stderr_text:
        return (
            "Gemini CLI завершился без ответа в stream-json.\n\n"
            f"stderr:\n{stderr_text[-1500:]}"
        )
    return (
        "Gemini CLI завершился без ответа. "
        "Проверьте авторизацию Gemini CLI и системные логи."
    )


def _build_headless_approval_warning(request: dict) -> str:
    tool_name = (
        request.get("tool")
        or request.get("name")
        or request.get("action")
        or request.get("tool_name")
        or "неизвестное действие"
    )
    return (
        "Gemini CLI запросил интерактивное подтверждение действия, "
        "но gateway запускает CLI в headless stream-json режиме.\n\n"
        f"Действие: {tool_name}\n\n"
        "В Gemini CLI 0.39.1 такие подтверждения в non-interactive режиме "
        "не могут быть безопасно продолжены из Telegram. Используйте "
        "GEMINI_APPROVAL_MODE=auto_edit/yolo или настройте policy rules через "
        "GEMINI_POLICY_PATHS / GEMINI_ADMIN_POLICY_PATHS."
    )


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int((end - start) * 1000))


class SessionManager:
    """Управляет сессиями Gemini CLI."""

    def __init__(
        self,
        config: Config,
        runtime_state: GatewayRuntimeState | None = None,
    ):
        self.config = config
        self.runtime_state = runtime_state
        self.user_environments = UserEnvironmentResolver(config)
        # user_id -> gemini_session_id
        self.active_sessions: dict[int, str] = {}
        self.active_prompt_processes: dict[int, asyncio.subprocess.Process] = {}
        self._prompt_locks: dict[int, asyncio.Lock] = {}
        self._cancelled_processes: set[int] = set()

    def has_active_prompt(self, user_id: int) -> bool:
        process = self.active_prompt_processes.get(user_id)
        return bool(process and process.returncode is None)

    def active_prompt_count(self) -> int:
        return sum(
            1
            for process in self.active_prompt_processes.values()
            if process.returncode is None
        )

    def active_prompt_users(self) -> list[int]:
        return [
            user_id
            for user_id, process in self.active_prompt_processes.items()
            if process.returncode is None
        ]

    def _gemini_executable(self) -> str:
        return shutil.which(self.config.gemini_bin) or self.config.gemini_bin

    def working_dir_for_user(self, user_id: int | None = None) -> str:
        return self.user_environments.working_dir_for(user_id)

    def artifact_roots_for_user(self, user_id: int | None = None) -> tuple[str, ...]:
        return self.user_environments.artifact_roots_for(user_id)

    async def get_sessions_list(
        self, user_id: int | None = None
    ) -> list[GeminiSessionInfo]:
        """Вернуть список сессий Gemini CLI, отсортированный от новых к старым."""
        process = await asyncio.create_subprocess_exec(
            self._gemini_executable(),
            "--list-sessions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir_for_user(user_id),
        )
        stdout, stderr = await process.communicate()
        output = (
            stdout.decode("utf-8", errors="replace")
            + "\n"
            + stderr.decode("utf-8", errors="replace")
        )
        if process.returncode != 0:
            raise RuntimeError(
                "gemini --list-sessions failed with code "
                f"{process.returncode}: {strip_ansi_codes(output).strip()[:1000]}"
            )
        return parse_gemini_sessions_output(output)

    async def get_mcp_list(self) -> list[tuple[str, bool]]:
        """Возвращает актуальный список MCP серверов: (имя, включен_ли)."""
        process = await asyncio.create_subprocess_exec(
            self._gemini_executable(),
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
            self._gemini_executable(),
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
            self._gemini_executable(),
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
            self._gemini_executable(),
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
        for user_id in list(self.active_prompt_processes):
            await self.cancel_active_prompt(user_id, reason="gateway shutdown")

    async def reset(self, user_id: int) -> None:
        """Сброс контекста (/new): очистка привязанного session_id."""
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            logger.info(f"Cleared session context for user {user_id}")

    async def set_active_session(self, user_id: int, session_id: str) -> None:
        self.active_sessions[user_id] = session_id
        logger.info(f"Set active session {session_id} for user {user_id}")

    def get_active_session(self, user_id: int) -> str | None:
        return self.active_sessions.get(user_id)

    async def delete_session_by_id(
        self, session_id: str, user_id: int | None = None
    ) -> bool:
        deleted, output = await self._delete_session(session_id, user_id=user_id)
        if deleted:
            self._clear_active_session_refs(session_id)
            return True

        sessions = await self.get_sessions_list(user_id=user_id)
        target = next(
            (session for session in sessions if session.session_id == session_id),
            None,
        )
        if target is None:
            return False

        deleted, fallback_output = await self._delete_session(
            str(target.source_index),
            user_id=user_id,
        )
        if not deleted:
            combined_output = strip_ansi_codes(
                "\n".join(part for part in (output, fallback_output) if part).strip()
            )
            raise RuntimeError(
                "gemini --delete-session failed with code "
                f"nonzero: {combined_output[:1000]}"
            )
        self._clear_active_session_refs(session_id)
        return True

    async def _delete_session(
        self, session_ref: str, user_id: int | None = None
    ) -> tuple[bool, str]:
        process = await asyncio.create_subprocess_exec(
            self._gemini_executable(),
            "--delete-session",
            session_ref,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir_for_user(user_id),
        )
        stdout, stderr = await process.communicate()
        output = (
            stdout.decode("utf-8", errors="replace")
            + "\n"
            + stderr.decode("utf-8", errors="replace")
        )
        if process.returncode != 0:
            return False, output
        return True, output

    def _clear_active_session_refs(self, session_id: str) -> None:
        for user_id, active_session_id in list(self.active_sessions.items()):
            if active_session_id == session_id:
                self.active_sessions.pop(user_id, None)

    async def cancel_active_prompt(self, user_id: int, reason: str = "") -> bool:
        process = self.active_prompt_processes.get(user_id)
        if not process or process.returncode is not None:
            return False
        logger.info(
            "Cancelling active Gemini prompt for user %s%s",
            user_id,
            f" ({reason})" if reason else "",
        )
        self._cancelled_processes.add(id(process))
        await self._terminate_process(process)
        return True

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        grace = self.config.gemini_shutdown_grace_seconds
        self._send_signal(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=grace)
            return
        except asyncio.TimeoutError:
            logger.warning("Gemini process did not stop after %.1fs; killing.", grace)
        except ProcessLookupError:
            return

        self._send_signal(process, getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            await process.wait()
        except ProcessLookupError:
            pass

    def _send_signal(
        self,
        process: asyncio.subprocess.Process,
        sig: signal.Signals,
    ) -> None:
        if process.returncode is not None:
            return
        try:
            pid = getattr(process, "pid", None)
            if os.name != "nt" and pid:
                os.killpg(pid, sig)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except (LookupError, ProcessLookupError):
            return
        except AttributeError:
            # Test doubles may only implement kill().
            try:
                process.kill()
            except ProcessLookupError:
                return

    def _build_prompt_args(
        self,
        prompt: str,
        effective_model: str,
        *,
        approval_mode: str | None = None,
    ) -> list[str]:
        args = [
            self._gemini_executable(),
            "-m",
            effective_model,
            "-o",
            "stream-json",
            "-p",
            prompt,
        ]
        args.extend(self.config.skip_trust_flag)
        if approval_mode:
            args.append(f"--approval-mode={approval_mode}")
        else:
            args.extend(self.config.approval_mode_flag)
        args.extend(self.config.sandbox_flag)
        args.extend(self.config.include_directories_flag)
        args.extend(self.config.policy_flags)
        args.extend(self.config.admin_policy_flags)
        args.extend(self.config.allowed_mcp_server_names_flag)
        args.extend(self.config.extensions_flag)
        args.extend(self.config.screen_reader_flag)
        return args

    async def send_prompt(
        self,
        prompt: str,
        user_id: int,
        on_event: Callable[[StreamEvent], asyncio.Future],
        on_approval: Callable[[dict], asyncio.Future],
        model: str | None = None,
    ) -> None:
        """Отправить промпт в одноразовый процесс и стримить ответ."""
        lock = self._prompt_locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            await on_event(
                StreamEvent(
                    event_type="warning",
                    warning_message=(
                        "У вас уже выполняется запрос. "
                        "Дождитесь ответа или используйте /cancel."
                    ),
                )
            )
            return

        async with lock:
            await self._send_prompt_locked(
                prompt,
                user_id,
                on_event,
                on_approval,
                model=model,
            )

    async def _send_prompt_locked(
        self,
        prompt: str,
        user_id: int,
        on_event: Callable[[StreamEvent], asyncio.Future],
        on_approval: Callable[[dict], asyncio.Future],
        model: str | None = None,
    ) -> None:
        effective_model = model or self.config.gemini_model
        args = self._build_prompt_args(prompt, effective_model)

        session_id = self.active_sessions.get(user_id)
        if session_id:
            args.extend(["--resume", session_id])

        working_dir = self.working_dir_for_user(user_id)
        logger.info(
            "Spawning Gemini for user %s with model %s in %s: %s",
            user_id,
            effective_model,
            working_dir,
            " ".join(arg if arg != prompt else "<prompt>" for arg in args),
        )
        logger.debug("Prompt length for user %s: %s chars", user_id, len(prompt))

        process_kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": working_dir,
        }
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        loop = asyncio.get_running_loop()
        prompt_started_at = time.time()
        prompt_start = loop.time()
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                **process_kwargs,
            )
        except FileNotFoundError as exc:
            await on_event(
                StreamEvent(
                    event_type="error",
                    error_message=classify_gemini_error(str(exc)).format_for_user(),
                )
            )
            return
        process_spawn_ms = _elapsed_ms(prompt_start, loop.time())
        self.active_prompt_processes[user_id] = process

        timeout = self.config.gemini_cli_timeout
        heartbeat_interval = 30  # Heartbeat каждые 30 секунд
        last_activity = loop.time()
        init_ms: int | None = None
        first_text_ms: int | None = None
        heartbeat_task = None
        stderr_task = None
        stderr_buffer = BoundedTextBuffer()
        tool_names_by_id: dict[str, str] = {}
        seen_assistant_text = False
        seen_result = False
        emitted_terminal_warning = False
        approval_requested = False
        logged_tool_before_text = False
        assistant_snapshot = ""
        result_status = ""
        result_total_tokens = 0
        result_thoughts_tokens = 0
        log_stream = logger.info if self.config.gemini_stream_debug else logger.debug

        async def read_stderr() -> None:
            if not process.stderr:
                return
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = strip_ansi_codes(line.decode("utf-8", errors="replace"))
                stderr_buffer.append(text)
                log_stream("[GEMINI STDERR] %s", text.rstrip())

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
            stderr_task = asyncio.create_task(read_stderr())

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
                    emitted_terminal_warning = True
                    await self._terminate_process(process)

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

                if event.event_type == "init" and init_ms is None:
                    init_ms = _elapsed_ms(prompt_start, loop.time())

                if event.event_type == "assistant_text":
                    original_text = event.assistant_text
                    if not event.message_delta:
                        if original_text == assistant_snapshot:
                            event.assistant_text = ""
                        elif original_text.startswith(assistant_snapshot):
                            event.assistant_text = original_text[
                                len(assistant_snapshot) :
                            ]
                        assistant_snapshot = original_text
                    else:
                        assistant_snapshot += original_text

                if event.event_type == "tool_use" and event.tool_id and event.tool_name:
                    tool_names_by_id[event.tool_id] = event.tool_name

                if event.event_type == "tool_result" and event.tool_id:
                    if not event.tool_name:
                        event.tool_name = tool_names_by_id.get(event.tool_id, "")
                    tool_names_by_id.pop(event.tool_id, None)

                if event.event_type == "assistant_text" and event.assistant_text:
                    if first_text_ms is None:
                        first_text_ms = _elapsed_ms(prompt_start, loop.time())
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

                if event.event_type == "result_stats":
                    result_status = event.result_status
                    result_total_tokens = event.total_tokens
                    result_thoughts_tokens = event.thoughts_tokens

                # Захват session_id из init-события
                if event.session_id and not session_id:
                    self.active_sessions[user_id] = event.session_id
                    logger.info(
                        f"Captured session_id: {event.session_id} for user {user_id}"
                    )

                if (
                    event.event_type
                    and event.event_type
                    not in {
                        "init",
                        "approval_request",
                    }
                    and (event.event_type != "assistant_text" or event.assistant_text)
                ):
                    await on_event(event)

                if event.event_type == "invalid_stream":
                    logger.warning("Received InvalidStream event, continuing...")
                    continue

                if event.is_empty_response:
                    logger.warning("Received empty response, notifying user...")
                    emitted_terminal_warning = True
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
                    approval_requested = True
                    emitted_terminal_warning = True
                    await on_event(
                        StreamEvent(
                            event_type="warning",
                            warning_message=_build_headless_approval_warning(
                                event.approval_request
                            ),
                        )
                    )
                    break

                if event.is_done:
                    seen_result = True
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
                    await asyncio.wait_for(process.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    await self._terminate_process(process)
            else:
                await process.wait()

            if stderr_task and not stderr_task.done():
                try:
                    await asyncio.wait_for(stderr_task, timeout=1)
                except asyncio.TimeoutError:
                    stderr_task.cancel()
                except asyncio.CancelledError:
                    pass

            cancelled_by_gateway = id(process) in self._cancelled_processes
            self._cancelled_processes.discard(id(process))

            if (
                process.returncode
                and process.returncode != 0
                and not emitted_terminal_warning
                and not approval_requested
                and not cancelled_by_gateway
            ):
                await on_event(
                    StreamEvent(
                        event_type="error",
                        error_message=_build_process_error(
                            process.returncode,
                            stderr_buffer.text(),
                        ),
                    )
                )
            elif (
                not seen_assistant_text
                and not seen_result
                and not emitted_terminal_warning
                and not approval_requested
                and not cancelled_by_gateway
            ):
                await on_event(
                    StreamEvent(
                        event_type="warning",
                        warning_message=_build_empty_stream_warning(
                            stderr_buffer.text()
                        ),
                    )
                )

            if self.active_prompt_processes.get(user_id) is process:
                self.active_prompt_processes.pop(user_id, None)

            total_ms = _elapsed_ms(prompt_start, loop.time())
            if self.runtime_state is not None:
                self.runtime_state.record_prompt_latency(
                    PromptLatencySnapshot(
                        user_id=user_id,
                        started_at=prompt_started_at,
                        process_spawn_ms=process_spawn_ms,
                        init_ms=init_ms,
                        first_text_ms=first_text_ms,
                        total_ms=total_ms,
                        returncode=process.returncode,
                        result_status=result_status,
                        total_tokens=result_total_tokens,
                        thoughts_tokens=result_thoughts_tokens,
                    )
                )

            logger.info(
                "Prompt processing completed for user %s, returncode=%s, "
                "spawn=%sms init=%s first_text=%s total=%sms",
                user_id,
                process.returncode,
                process_spawn_ms,
                init_ms,
                first_text_ms,
                total_ms,
            )

    async def generate_text(
        self,
        prompt: str,
        user_id: int,
        *,
        model: str | None = None,
        approval_mode: str = "plan",
    ) -> str:
        """Run a non-resumed headless prompt and return assistant text."""
        lock = self._prompt_locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError(
                "У вас уже выполняется запрос. Дождитесь ответа или используйте /cancel."
            )

        async with lock:
            return await self._generate_text_locked(
                prompt,
                user_id,
                model=model,
                approval_mode=approval_mode,
            )

    async def _generate_text_locked(
        self,
        prompt: str,
        user_id: int,
        *,
        model: str | None = None,
        approval_mode: str = "plan",
    ) -> str:
        effective_model = model or self.config.gemini_model
        args = self._build_prompt_args(
            prompt,
            effective_model,
            approval_mode=approval_mode,
        )
        working_dir = self.working_dir_for_user(user_id)
        process_kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": working_dir,
        }
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        logger.info(
            "Spawning internal Gemini prompt for user %s with model %s in %s.",
            user_id,
            effective_model,
            working_dir,
        )
        process = await asyncio.create_subprocess_exec(*args, **process_kwargs)
        self.active_prompt_processes[user_id] = process
        stderr_buffer = BoundedTextBuffer()
        assistant_snapshot = ""
        chunks: list[str] = []
        seen_result = False
        loop = asyncio.get_running_loop()
        last_activity = loop.time()

        async def read_stderr() -> None:
            if not process.stderr:
                return
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = strip_ansi_codes(line.decode("utf-8", errors="replace"))
                stderr_buffer.append(text)
                logger.debug("[GEMINI STDERR] %s", text.rstrip())

        stderr_task = asyncio.create_task(read_stderr())
        try:
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=self.config.gemini_cli_timeout,
                    )
                    last_activity = loop.time()
                except asyncio.TimeoutError as exc:
                    await self._terminate_process(process)
                    elapsed = int(loop.time() - last_activity)
                    raise RuntimeError(
                        f"Gemini CLI не отвечал {elapsed} секунд во время /init."
                    ) from exc

                if not line_bytes:
                    break

                line_text = line_bytes.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue

                event = GeminiStreamParser.parse_line(line_text)
                if event.event_type == "assistant_text":
                    text = event.assistant_text
                    if not event.message_delta:
                        if text == assistant_snapshot:
                            text = ""
                        elif text.startswith(assistant_snapshot):
                            text = text[len(assistant_snapshot) :]
                        assistant_snapshot = event.assistant_text
                    else:
                        assistant_snapshot += text
                    if text:
                        chunks.append(text)
                if event.is_empty_response:
                    break
                if event.approval_request:
                    raise RuntimeError(
                        _build_headless_approval_warning(event.approval_request)
                    )
                if event.is_done:
                    seen_result = True
                    break
        finally:
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    await self._terminate_process(process)
            else:
                await process.wait()

            if stderr_task and not stderr_task.done():
                try:
                    await asyncio.wait_for(stderr_task, timeout=1)
                except asyncio.TimeoutError:
                    stderr_task.cancel()
                except asyncio.CancelledError:
                    pass

            if self.active_prompt_processes.get(user_id) is process:
                self.active_prompt_processes.pop(user_id, None)

        cancelled_by_gateway = id(process) in self._cancelled_processes
        self._cancelled_processes.discard(id(process))
        if cancelled_by_gateway:
            raise RuntimeError("Генерация GEMINI.md остановлена.")

        if process.returncode and process.returncode != 0:
            raise RuntimeError(
                _build_process_error(process.returncode, stderr_buffer.text())
            )

        text = "".join(chunks).strip()
        if not text and not seen_result:
            raise RuntimeError(_build_empty_stream_warning(stderr_buffer.text()))
        return text

    async def answer_approval(self, answer: str) -> None:
        # Заглушка. Headless режим не поддерживает интерактивный ввод.
        pass
