import asyncio
import logging
import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Awaitable, Callable

from gateway.config import Config
from gateway.gemini.error_classifier import classify_gemini_error
from gateway.gemini.parser import GeminiStreamParser, StreamEvent
from gateway.runtime import GatewayRuntimeState, PromptLatencySnapshot
from gateway.session_state import SessionStateStore
from gateway.user_environment import UserEnvironmentResolver

logger = logging.getLogger(__name__)
_SESSION_LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\.\s*(?P<title>.*?)\s*"
    r"\((?P<meta>[^()]*)\)\s*\[(?P<session_id>[A-Za-z0-9-]+)\]\s*$"
)
_MCP_LIST_LINE_RE = re.compile(
    r"^(?P<status>[✓✗xX])\s+(?P<name>[a-zA-Z0-9_\-]+)"
    r"(?:\s+\(from [^)]+\))?:"
)
_SKILL_LIST_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z0-9_\-]+)\s+\[(?P<status>Enabled|Disabled)\]",
    re.IGNORECASE,
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


def _parse_mcp_list_lines(lines: list[str]) -> list[tuple[str, bool]]:
    return _parse_named_status_lines(
        lines,
        pattern=_MCP_LIST_LINE_RE,
        is_noise=_is_mcp_list_noise,
        is_enabled=lambda status: status == "✓",
        log_label="MCP server",
    )


def _parse_skills_list_lines(lines: list[str]) -> list[tuple[str, bool]]:
    return _parse_named_status_lines(
        lines,
        pattern=_SKILL_LIST_LINE_RE,
        is_noise=_is_skills_list_noise,
        is_enabled=lambda status: status.lower() == "enabled",
        log_label="skill",
    )


def _parse_named_status_lines(
    lines: list[str],
    *,
    pattern: re.Pattern[str],
    is_noise: Callable[[str], bool],
    is_enabled: Callable[[str], bool],
    log_label: str,
) -> list[tuple[str, bool]]:
    items: list[tuple[str, bool]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if is_noise(line):
            continue

        match = pattern.search(line)
        if not match:
            continue

        name = match.group("name").strip()
        enabled = is_enabled(match.group("status").strip())
        items.append((name, enabled))
        logger.debug("Parsed %s: %s (enabled=%s)", log_label, name, enabled)

    return items


def _is_mcp_list_noise(line: str) -> bool:
    return not line or "Configured MCP servers:" in line or "Loaded cached" in line


def _is_skills_list_noise(line: str) -> bool:
    return (
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


def _is_stream_reader_limit_error(exc: BaseException) -> bool:
    return isinstance(exc, asyncio.LimitOverrunError) or (
        isinstance(exc, ValueError)
        and "Separator is not found, and chunk exceed the limit" in str(exc)
    )


def _build_stream_reader_limit_error(limit_bytes: int) -> str:
    limit_mib = limit_bytes / (1024 * 1024)
    return (
        "Gemini CLI прислал слишком крупное stream-json событие, и gateway "
        "не смог прочитать его одной JSONL-строкой.\n\n"
        f"Текущий лимит чтения: {limit_bytes} байт (~{limit_mib:.1f} MiB).\n\n"
        "Увеличьте в .env параметр GEMINI_STREAM_READER_LIMIT_BYTES и "
        "перезапустите сервис: sudo systemctl restart telegram-gateway."
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


class GeminiStreamReaderLimitExceeded(RuntimeError):
    """Raised when asyncio readline cannot fit one JSONL stream event."""


@dataclass
class AssistantTextTracker:
    snapshot: str = ""

    def apply(self, event: StreamEvent) -> str:
        """Normalize full snapshots into deltas while preserving whitespace."""
        if event.event_type != "assistant_text":
            return ""

        original_text = event.assistant_text
        text = original_text
        if not event.message_delta:
            if original_text == self.snapshot:
                text = ""
            elif original_text.startswith(self.snapshot):
                text = original_text[len(self.snapshot) :]
            self.snapshot = original_text
        else:
            self.snapshot += original_text

        event.assistant_text = text
        return text


@dataclass
class PromptStreamState:
    started_with_session: bool
    resume_ref: str | None = None
    resume_source: str = "new"
    text_tracker: AssistantTextTracker = field(default_factory=AssistantTextTracker)
    tool_names_by_id: dict[str, str] = field(default_factory=dict)
    seen_assistant_text: bool = False
    seen_result: bool = False
    emitted_terminal_warning: bool = False
    approval_requested: bool = False
    logged_tool_before_text: bool = False
    captured_session: bool = False
    init_ms: int | None = None
    first_text_ms: int | None = None
    result_status: str = ""
    result_total_tokens: int = 0
    result_thoughts_tokens: int = 0


StreamCallback = Callable[[StreamEvent], Awaitable[None]]
ApprovalCallback = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class ResumeDecision:
    session_ref: str | None
    source: str
    warning: str = ""


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
        self.session_state = SessionStateStore(
            Path(config.gateway_state_dir) / "session_state.json"
        )
        # user_id -> gemini_session_id
        self.active_sessions: dict[int, str] = {}
        self.active_session_sources: dict[int, str] = {}
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

    def internal_working_dir_for_user(self, user_id: int, purpose: str = "init") -> str:
        path = (
            Path(self.config.gateway_state_dir)
            / "internal"
            / f"tg-user-{int(user_id)}"
            / purpose
        )
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def artifact_roots_for_user(self, user_id: int | None = None) -> tuple[str, ...]:
        return self.user_environments.artifact_roots_for(user_id)

    def _subprocess_kwargs(
        self,
        cwd: str,
        *,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        process_group: bool = True,
    ) -> dict:
        kwargs = {
            "stdout": stdout,
            "stderr": stderr,
            "cwd": cwd,
        }
        if stdout == asyncio.subprocess.PIPE or stderr == asyncio.subprocess.PIPE:
            kwargs["limit"] = self.config.gemini_stream_reader_limit_bytes
        if process_group:
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
        return kwargs

    async def _start_process(
        self,
        args: list[str],
        *,
        cwd: str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            **self._subprocess_kwargs(cwd, stdout=stdout, stderr=stderr),
        )

    async def _run_gemini_command(
        self,
        *args: str,
        cwd: str,
    ) -> tuple[int | None, str]:
        process = await self._start_process([self._gemini_executable(), *args], cwd=cwd)
        stdout, stderr = await process.communicate()
        output = (
            stdout.decode("utf-8", errors="replace")
            + "\n"
            + stderr.decode("utf-8", errors="replace")
        )
        return process.returncode, output

    async def _read_stdout_line(
        self,
        process: asyncio.subprocess.Process,
        *,
        timeout: int,
    ) -> str | None:
        if not process.stdout:
            return None
        try:
            line_bytes = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=timeout,
            )
        except (ValueError, asyncio.LimitOverrunError) as exc:
            if not _is_stream_reader_limit_error(exc):
                raise
            raise GeminiStreamReaderLimitExceeded(
                _build_stream_reader_limit_error(
                    self.config.gemini_stream_reader_limit_bytes
                )
            ) from exc

        if not line_bytes:
            return None
        return line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

    async def _read_stderr(
        self,
        process: asyncio.subprocess.Process,
        stderr_buffer: BoundedTextBuffer,
        log_stream: Callable[[str, object], None] = logger.debug,
    ) -> None:
        if not process.stderr:
            return
        while True:
            try:
                line = await process.stderr.readline()
            except (ValueError, asyncio.LimitOverrunError) as exc:
                if not _is_stream_reader_limit_error(exc):
                    raise
                text = _build_stream_reader_limit_error(
                    self.config.gemini_stream_reader_limit_bytes
                )
                stderr_buffer.append(text)
                logger.warning("Gemini stderr stream reader limit exceeded: %s", exc)
                break
            if not line:
                break
            text = strip_ansi_codes(line.decode("utf-8", errors="replace"))
            stderr_buffer.append(text)
            log_stream("[GEMINI STDERR] %s", text.rstrip())

    async def _finish_stderr_task(self, stderr_task: asyncio.Task | None) -> None:
        if not stderr_task or stderr_task.done():
            return
        try:
            await asyncio.wait_for(stderr_task, timeout=1)
        except asyncio.TimeoutError:
            stderr_task.cancel()
        except asyncio.CancelledError:
            pass

    async def _ensure_process_finished(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                await self._terminate_process(process)
            return
        await process.wait()

    async def get_sessions_list(
        self, user_id: int | None = None
    ) -> list[GeminiSessionInfo]:
        """Вернуть список сессий Gemini CLI, отсортированный от новых к старым."""
        returncode, output = await self._run_gemini_command(
            "--list-sessions",
            cwd=self.working_dir_for_user(user_id),
        )
        if returncode != 0:
            raise RuntimeError(
                "gemini --list-sessions failed with code "
                f"{returncode}: {strip_ansi_codes(output).strip()[:1000]}"
            )
        return self._mark_active_session(
            parse_gemini_sessions_output(output),
            user_id=user_id,
        )

    def _mark_active_session(
        self,
        sessions: list[GeminiSessionInfo],
        *,
        user_id: int | None,
    ) -> list[GeminiSessionInfo]:
        if user_id is None or not sessions:
            return sessions

        active_session = self.get_active_session(user_id)
        if not active_session:
            return sessions

        current_session_id = (
            sessions[0].session_id if active_session == "latest" else active_session
        )
        return [
            replace(
                session,
                is_current=session.is_current
                or session.session_id == current_session_id,
            )
            for session in sessions
        ]

    async def _run_global_list_command(self, group: str) -> list[str]:
        returncode, output = await self._run_gemini_command(
            group,
            "list",
            cwd=self.config.gemini_working_dir,
        )
        if returncode != 0:
            logger.debug("gemini %s list returned code %s", group, returncode)
        output = strip_ansi_codes(output)
        logger.debug("Raw output from 'gemini %s list':\n%s", group, output)
        return output.splitlines()

    async def get_mcp_list(self) -> list[tuple[str, bool]]:
        """Возвращает актуальный список MCP серверов: (имя, включен_ли)."""
        mcp_servers = _parse_mcp_list_lines(await self._run_global_list_command("mcp"))
        logger.info(f"Found {len(mcp_servers)} MCP servers")
        return mcp_servers

    async def get_skills_list(self) -> list[tuple[str, bool]]:
        """Возвращает актуальный список Skills: (имя, включен_ли)."""
        skills_list = _parse_skills_list_lines(
            await self._run_global_list_command("skills")
        )
        logger.info(f"Found {len(skills_list)} skills")
        return skills_list

    async def toggle_mcp(self, name: str, enable: bool) -> bool:
        cmd = "enable" if enable else "disable"
        process = await self._start_process(
            [self._gemini_executable(), "mcp", cmd, name],
            cwd=self.config.gemini_working_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
        return process.returncode == 0

    async def toggle_skill(self, name: str, enable: bool) -> bool:
        cmd = "enable" if enable else "disable"
        process = await self._start_process(
            [self._gemini_executable(), "skills", cmd, name],
            cwd=self.config.gemini_working_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
        return process.returncode == 0

    async def is_alive(self) -> bool:
        return True

    async def kill(self) -> None:
        for user_id in list(self.active_prompt_processes):
            await self.cancel_active_prompt(user_id, reason="gateway shutdown")

    async def reset(self, user_id: int, *, reason: str = "manual") -> None:
        """Сброс контекста (/new): очистка привязанного session_id."""
        had_memory_session = self.active_sessions.pop(user_id, None) is not None
        self.active_session_sources.pop(user_id, None)
        self.session_state.mark_cleared(
            user_id,
            workspace=self.working_dir_for_user(user_id),
            source=reason,
        )
        logger.info(
            "Cleared session context for user %s reason=%s had_memory_session=%s",
            user_id,
            reason,
            had_memory_session,
        )

    async def set_active_session(
        self,
        user_id: int,
        session_id: str,
        *,
        source: str = "manual",
    ) -> None:
        if session_id == "latest" and source == "manual":
            source = "manual-latest"
        self._set_active_session(user_id, session_id, source=source)
        logger.info(
            "Set active session %s for user %s source=%s",
            session_id,
            user_id,
            source,
        )

    def _set_active_session(
        self, user_id: int, session_id: str, *, source: str
    ) -> None:
        working_dir = self.working_dir_for_user(user_id)
        self.active_sessions[user_id] = session_id
        self.active_session_sources[user_id] = source
        self.session_state.set(
            user_id,
            active_session_id=session_id,
            workspace=working_dir,
            source=source,
        )

    def get_active_session(self, user_id: int) -> str | None:
        if user_id in self.active_sessions:
            return self.active_sessions[user_id]

        working_dir = self.working_dir_for_user(user_id)
        record = self.session_state.get(user_id, workspace=working_dir)
        if record is None:
            return None

        self.active_sessions[user_id] = record.active_session_id
        self.active_session_sources[user_id] = "persisted"
        return record.active_session_id

    def get_active_session_source(self, user_id: int) -> str:
        if self.get_active_session(user_id):
            return self.active_session_sources.get(user_id, "persisted")
        return "none"

    async def _resolve_resume_decision(self, user_id: int) -> ResumeDecision:
        memory_session = self.active_sessions.get(user_id)
        if memory_session:
            source = self.active_session_sources.get(user_id, "memory")
            return ResumeDecision(memory_session, source)

        working_dir = self.working_dir_for_user(user_id)
        if self.session_state.is_cleared(user_id, workspace=working_dir):
            return ResumeDecision(None, "new")

        record = self.session_state.get(user_id, workspace=working_dir)
        if record is not None:
            if record.active_session_id == "latest" or await self._session_ref_exists(
                user_id,
                record.active_session_id,
            ):
                self.active_sessions[user_id] = record.active_session_id
                self.active_session_sources[user_id] = "persisted"
                return ResumeDecision(record.active_session_id, "persisted")

            logger.warning(
                "Persisted session %s for user %s is missing; trying latest fallback.",
                record.active_session_id,
                user_id,
            )
            self.session_state.clear(user_id)
            if (
                self.config.gateway_session_auto_resume_latest
                and await self._has_sessions(user_id)
            ):
                self.active_sessions[user_id] = "latest"
                self.active_session_sources[user_id] = "latest-fallback"
                return ResumeDecision("latest", "latest-fallback")

            return ResumeDecision(
                None,
                "new",
                warning=(
                    "⚠️ Сохранённый диалог не найден в Gemini CLI. Начинаю новый диалог."
                ),
            )

        if self.config.gateway_session_auto_resume_latest and await self._has_sessions(
            user_id
        ):
            self.active_sessions[user_id] = "latest"
            self.active_session_sources[user_id] = "latest-fallback"
            return ResumeDecision("latest", "latest-fallback")

        return ResumeDecision(None, "new")

    async def _session_ref_exists(self, user_id: int, session_id: str) -> bool:
        try:
            returncode, output = await self._run_gemini_command(
                "--list-sessions",
                cwd=self.working_dir_for_user(user_id),
            )
        except Exception as exc:
            logger.warning(
                "Could not validate persisted session for user %s: %s",
                user_id,
                exc,
            )
            return True
        if returncode != 0:
            return True
        sessions = parse_gemini_sessions_output(output)
        return any(session.session_id == session_id for session in sessions)

    async def _has_sessions(self, user_id: int) -> bool:
        try:
            returncode, output = await self._run_gemini_command(
                "--list-sessions",
                cwd=self.working_dir_for_user(user_id),
            )
        except Exception as exc:
            logger.warning(
                "Could not list sessions for latest fallback for user %s: %s",
                user_id,
                exc,
            )
            return False
        if returncode != 0:
            return False
        return bool(parse_gemini_sessions_output(output))

    async def delete_session_by_id(
        self, session_id: str, user_id: int | None = None
    ) -> bool:
        deleted, output = await self._delete_session(session_id, user_id=user_id)
        if deleted:
            self._clear_active_session_refs(session_id, user_id=user_id)
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
        self._clear_active_session_refs(session_id, user_id=user_id)
        return True

    async def _delete_session(
        self, session_ref: str, user_id: int | None = None
    ) -> tuple[bool, str]:
        returncode, output = await self._run_gemini_command(
            "--delete-session",
            session_ref,
            cwd=self.working_dir_for_user(user_id),
        )
        if returncode != 0:
            return False, output
        return True, output

    def _clear_active_session_refs(
        self,
        session_id: str,
        *,
        user_id: int | None = None,
    ) -> None:
        cleared_user_ids: set[int] = set()
        for active_user_id, active_session_id in list(self.active_sessions.items()):
            if user_id is not None and active_user_id != user_id:
                continue
            if active_session_id == session_id:
                self.active_sessions.pop(active_user_id, None)
                self.active_session_sources.pop(active_user_id, None)
                self.session_state.mark_cleared(
                    active_user_id,
                    workspace=self.working_dir_for_user(active_user_id),
                    source="delete-current-session",
                )
                cleared_user_ids.add(active_user_id)
                logger.info(
                    "Cleared session context for user %s reason=delete-current-session",
                    active_user_id,
                )
        for removed_user_id in self.session_state.clear_matching_session(
            session_id,
            user_id=user_id,
        ):
            self.active_session_sources.pop(removed_user_id, None)
            if removed_user_id in cleared_user_ids:
                continue
            self.session_state.mark_cleared(
                removed_user_id,
                workspace=self.working_dir_for_user(removed_user_id),
                source="delete-current-session",
            )

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
        include_directories: tuple[str, ...] = (),
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
        args.extend(self._include_directories_flag(include_directories))
        args.extend(self.config.policy_flags)
        args.extend(self.config.admin_policy_flags)
        args.extend(self.config.allowed_mcp_server_names_flag)
        args.extend(self.config.extensions_flag)
        args.extend(self.config.screen_reader_flag)
        return args

    def _include_directories_flag(
        self,
        include_directories: tuple[str, ...] = (),
    ) -> list[str]:
        directories = tuple(
            dict.fromkeys(
                directory
                for directory in (
                    *self.config.gemini_include_directories,
                    *include_directories,
                )
                if directory
            )
        )
        if not directories:
            return []
        return ["--include-directories", ",".join(directories)]

    async def _heartbeat_loop(
        self,
        on_event: StreamCallback,
        last_activity: Callable[[], float],
        *,
        interval: int = 30,
    ) -> None:
        heartbeat_count = 0
        while True:
            await asyncio.sleep(interval)
            idle_time = asyncio.get_event_loop().time() - last_activity()
            if idle_time < interval:
                continue
            heartbeat_count += 1
            elapsed_total = int(idle_time)
            await on_event(
                StreamEvent(
                    event_type="heartbeat",
                    status_message=f"Обработка... ({elapsed_total}с)",
                )
            )
            logger.debug("Heartbeat #%s: %ss idle", heartbeat_count, elapsed_total)

    def _log_stream_event(
        self,
        event: StreamEvent,
        log_stream: Callable[..., None],
    ) -> None:
        if not event.event_type:
            return
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

    async def _emit_timeout_warning(
        self,
        process: asyncio.subprocess.Process,
        elapsed: int,
        on_event: StreamCallback,
    ) -> None:
        logger.warning("Gemini CLI timeout after %ss of inactivity", elapsed)
        await self._terminate_process(process)
        if elapsed > 0:
            message = (
                f"Таймаут: Gemini не отвечал {elapsed} секунд.\n"
                "Возможные причины:\n"
                "• Слишком сложный запрос для модели\n"
                "• Проблемы с MCP-серверами\n"
                "• Зависание CLI в non-interactive режиме\n\n"
                "Попробуйте:\n"
                "• Упростить запрос\n"
                "• Использовать /new для сброса контекста\n"
                "• Повторить попытку позже"
            )
        else:
            message = "Таймаут: Gemini не запустился.\nПроверьте логи сервера."
        await on_event(StreamEvent(event_type="warning", warning_message=message))

    async def _emit_stream_limit_error(
        self,
        process: asyncio.subprocess.Process,
        on_event: StreamCallback,
    ) -> None:
        await self._terminate_process(process)
        await on_event(
            StreamEvent(
                event_type="error",
                error_message=_build_stream_reader_limit_error(
                    self.config.gemini_stream_reader_limit_bytes
                ),
            )
        )

    async def _handle_prompt_event(
        self,
        event: StreamEvent,
        state: PromptStreamState,
        *,
        user_id: int,
        prompt_start: float,
        loop: asyncio.AbstractEventLoop,
        on_event: StreamCallback,
    ) -> bool:
        if event.event_type == "init" and state.init_ms is None:
            state.init_ms = _elapsed_ms(prompt_start, loop.time())

        state.text_tracker.apply(event)
        self._track_tool_name(event, state)
        self._track_first_assistant_text(event, state, prompt_start, loop, user_id)
        self._track_result_stats(event, state)
        self._capture_session_id(event, state, user_id)

        if self._should_emit_stream_event(event):
            await on_event(event)

        if event.event_type == "invalid_stream":
            logger.warning("Received InvalidStream event, continuing...")
            return False
        if event.is_empty_response:
            await self._emit_empty_response_warning(state, on_event)
            return True
        if event.approval_request:
            await self._emit_headless_approval_warning(event, state, on_event)
            return True
        if event.is_done:
            state.seen_result = True
            return True
        return False

    def _track_tool_name(
        self,
        event: StreamEvent,
        state: PromptStreamState,
    ) -> None:
        if event.event_type == "tool_use" and event.tool_id and event.tool_name:
            state.tool_names_by_id[event.tool_id] = event.tool_name
            return
        if event.event_type != "tool_result" or not event.tool_id:
            return
        if not event.tool_name:
            event.tool_name = state.tool_names_by_id.get(event.tool_id, "")
        state.tool_names_by_id.pop(event.tool_id, None)

    def _track_first_assistant_text(
        self,
        event: StreamEvent,
        state: PromptStreamState,
        prompt_start: float,
        loop: asyncio.AbstractEventLoop,
        user_id: int,
    ) -> None:
        if event.event_type == "assistant_text" and event.assistant_text:
            if state.first_text_ms is None:
                state.first_text_ms = _elapsed_ms(prompt_start, loop.time())
            state.seen_assistant_text = True
            return
        if (
            not state.seen_assistant_text
            and not state.logged_tool_before_text
            and event.event_type in {"tool_use", "tool_result"}
        ):
            logger.info(
                "Gemini stream progress detected before assistant text for user %s.",
                user_id,
            )
            state.logged_tool_before_text = True

    def _track_result_stats(
        self,
        event: StreamEvent,
        state: PromptStreamState,
    ) -> None:
        if event.event_type != "result_stats":
            return
        state.result_status = event.result_status
        state.result_total_tokens = event.total_tokens
        state.result_thoughts_tokens = event.thoughts_tokens

    def _capture_session_id(
        self,
        event: StreamEvent,
        state: PromptStreamState,
        user_id: int,
    ) -> None:
        if not event.session_id or state.captured_session:
            return
        source = state.resume_source or "new"
        self._set_active_session(user_id, event.session_id, source=source)
        state.captured_session = True
        logger.info(
            "Captured session_id: %s for user %s resume_source=%s",
            event.session_id,
            user_id,
            state.resume_source,
        )

    @staticmethod
    def _should_emit_stream_event(event: StreamEvent) -> bool:
        return bool(
            event.event_type
            and event.event_type not in {"init", "approval_request"}
            and (event.event_type != "assistant_text" or event.assistant_text)
        )

    async def _emit_empty_response_warning(
        self,
        state: PromptStreamState,
        on_event: StreamCallback,
    ) -> None:
        logger.warning("Received empty response, notifying user...")
        state.emitted_terminal_warning = True
        await on_event(
            StreamEvent(
                event_type="warning",
                warning_message=(
                    "Модель вернула пустой ответ. Попробуйте переформулировать запрос."
                ),
            )
        )

    async def _emit_headless_approval_warning(
        self,
        event: StreamEvent,
        state: PromptStreamState,
        on_event: StreamCallback,
    ) -> None:
        state.approval_requested = True
        state.emitted_terminal_warning = True
        await on_event(
            StreamEvent(
                event_type="warning",
                warning_message=_build_headless_approval_warning(
                    event.approval_request or {}
                ),
            )
        )

    async def _cancel_task(self, task: asyncio.Task | None) -> None:
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _consume_cancelled_process(self, process: asyncio.subprocess.Process) -> bool:
        cancelled_by_gateway = id(process) in self._cancelled_processes
        self._cancelled_processes.discard(id(process))
        return cancelled_by_gateway

    async def _emit_prompt_terminal_event(
        self,
        process: asyncio.subprocess.Process,
        state: PromptStreamState,
        stderr_buffer: BoundedTextBuffer,
        cancelled_by_gateway: bool,
        on_event: StreamCallback,
    ) -> None:
        if (
            process.returncode
            and process.returncode != 0
            and not state.emitted_terminal_warning
            and not state.approval_requested
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
            return

        if (
            not state.seen_assistant_text
            and not state.seen_result
            and not state.emitted_terminal_warning
            and not state.approval_requested
            and not cancelled_by_gateway
        ):
            await on_event(
                StreamEvent(
                    event_type="warning",
                    warning_message=_build_empty_stream_warning(stderr_buffer.text()),
                )
            )

    def _record_prompt_latency(
        self,
        *,
        user_id: int,
        prompt_started_at: float,
        process_spawn_ms: int,
        total_ms: int,
        process: asyncio.subprocess.Process,
        state: PromptStreamState,
    ) -> None:
        if self.runtime_state is None:
            return
        self.runtime_state.record_prompt_latency(
            PromptLatencySnapshot(
                user_id=user_id,
                started_at=prompt_started_at,
                process_spawn_ms=process_spawn_ms,
                init_ms=state.init_ms,
                first_text_ms=state.first_text_ms,
                total_ms=total_ms,
                returncode=process.returncode,
                result_status=state.result_status,
                total_tokens=state.result_total_tokens,
                thoughts_tokens=state.result_thoughts_tokens,
            )
        )

    async def send_prompt(
        self,
        prompt: str,
        user_id: int,
        on_event: StreamCallback,
        on_approval: ApprovalCallback,
        model: str | None = None,
        include_directories: tuple[str, ...] = (),
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
                include_directories=include_directories,
            )

    async def _send_prompt_locked(
        self,
        prompt: str,
        user_id: int,
        on_event: StreamCallback,
        on_approval: ApprovalCallback,
        model: str | None = None,
        include_directories: tuple[str, ...] = (),
    ) -> None:
        _ = on_approval
        effective_model = model or self.config.gemini_model
        args = self._build_prompt_args(
            prompt,
            effective_model,
            include_directories=include_directories,
        )

        resume_decision = await self._resolve_resume_decision(user_id)
        if resume_decision.session_ref:
            args.extend(["--resume", resume_decision.session_ref])
        if resume_decision.warning:
            await on_event(
                StreamEvent(
                    event_type="warning",
                    warning_message=resume_decision.warning,
                )
            )

        working_dir = self.working_dir_for_user(user_id)
        logger.info(
            "Spawning Gemini for user %s with model %s in %s resume_source=%s: %s",
            user_id,
            effective_model,
            working_dir,
            resume_decision.source,
            " ".join(arg if arg != prompt else "<prompt>" for arg in args),
        )
        logger.debug("Prompt length for user %s: %s chars", user_id, len(prompt))

        loop = asyncio.get_running_loop()
        prompt_started_at = time.time()
        prompt_start = loop.time()
        try:
            process = await self._start_process(args, cwd=working_dir)
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
        last_activity = loop.time()
        stderr_buffer = BoundedTextBuffer()
        state = PromptStreamState(
            started_with_session=bool(resume_decision.session_ref),
            resume_ref=resume_decision.session_ref,
            resume_source=resume_decision.source,
        )
        log_stream = logger.info if self.config.gemini_stream_debug else logger.debug
        heartbeat_task: asyncio.Task | None = None
        stderr_task: asyncio.Task | None = None

        try:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(on_event, lambda: last_activity)
            )
            stderr_task = asyncio.create_task(
                self._read_stderr(process, stderr_buffer, log_stream)
            )

            while True:
                try:
                    line_text = await self._read_stdout_line(process, timeout=timeout)
                    last_activity = loop.time()
                except asyncio.TimeoutError:
                    elapsed = int(loop.time() - last_activity)
                    state.emitted_terminal_warning = True
                    await self._emit_timeout_warning(process, elapsed, on_event)
                    break
                except GeminiStreamReaderLimitExceeded:
                    state.emitted_terminal_warning = True
                    await self._emit_stream_limit_error(process, on_event)
                    break

                if line_text is None:
                    break
                if not line_text:
                    continue

                log_stream("[GEMINI RAW] %s", line_text)

                event = GeminiStreamParser.parse_line(line_text)
                self._log_stream_event(event, log_stream)
                should_stop = await self._handle_prompt_event(
                    event,
                    state,
                    user_id=user_id,
                    prompt_start=prompt_start,
                    loop=loop,
                    on_event=on_event,
                )
                if should_stop:
                    break

        finally:
            await self._cancel_task(heartbeat_task)
            await self._ensure_process_finished(process)
            await self._finish_stderr_task(stderr_task)

            cancelled_by_gateway = self._consume_cancelled_process(process)
            await self._emit_prompt_terminal_event(
                process,
                state,
                stderr_buffer,
                cancelled_by_gateway,
                on_event,
            )

            if self.active_prompt_processes.get(user_id) is process:
                self.active_prompt_processes.pop(user_id, None)

            total_ms = _elapsed_ms(prompt_start, loop.time())
            self._record_prompt_latency(
                user_id=user_id,
                prompt_started_at=prompt_started_at,
                process_spawn_ms=process_spawn_ms,
                total_ms=total_ms,
                process=process,
                state=state,
            )

            logger.info(
                "Prompt processing completed for user %s, returncode=%s, "
                "spawn=%sms init=%s first_text=%s total=%sms",
                user_id,
                process.returncode,
                process_spawn_ms,
                state.init_ms,
                state.first_text_ms,
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
        working_dir = self.internal_working_dir_for_user(user_id, purpose="init")

        logger.info(
            "Spawning internal Gemini prompt for user %s with model %s in %s.",
            user_id,
            effective_model,
            working_dir,
        )
        process = await self._start_process(args, cwd=working_dir)
        self.active_prompt_processes[user_id] = process
        stderr_buffer = BoundedTextBuffer()
        text_tracker = AssistantTextTracker()
        chunks: list[str] = []
        seen_result = False
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        stderr_task = asyncio.create_task(self._read_stderr(process, stderr_buffer))
        try:
            while True:
                try:
                    line_text = await self._read_stdout_line(
                        process,
                        timeout=self.config.gemini_cli_timeout,
                    )
                    last_activity = loop.time()
                except asyncio.TimeoutError as exc:
                    await self._terminate_process(process)
                    elapsed = int(loop.time() - last_activity)
                    raise RuntimeError(
                        f"Gemini CLI не отвечал {elapsed} секунд во время /init."
                    ) from exc
                except GeminiStreamReaderLimitExceeded as exc:
                    await self._terminate_process(process)
                    raise RuntimeError(str(exc)) from exc

                if line_text is None:
                    break
                if not line_text:
                    continue

                event = GeminiStreamParser.parse_line(line_text)
                text = text_tracker.apply(event)
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
            await self._ensure_process_finished(process)
            await self._finish_stderr_task(stderr_task)

            if self.active_prompt_processes.get(user_id) is process:
                self.active_prompt_processes.pop(user_id, None)

        if self._consume_cancelled_process(process):
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
