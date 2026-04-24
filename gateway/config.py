"""
Конфигурация Gateway — загрузка параметров из .env с дефолтами.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Иммутабельная конфигурация приложения, загружаемая из .env."""

    # === Telegram ===
    telegram_bot_token: str
    target_chat_id: Optional[int] = None

    # === Gemini CLI ===
    gemini_model: str = "gemini-3-flash-preview"
    gemini_target_version: str = "0.38.2"
    gemini_bin: str = "gemini"
    gemini_approval_mode: str = "yolo"  # default / auto_edit / yolo / plan
    gemini_working_dir: str = field(default_factory=lambda: str(Path.home()))
    gemini_include_directories: tuple[str, ...] = field(default_factory=tuple)
    gemini_artifact_roots: tuple[str, ...] = field(default_factory=tuple)
    gemini_cli_timeout: int = 600  # секунды
    gemini_shutdown_grace_seconds: float = 5.0
    gemini_sandbox: bool = False
    gemini_stream_debug: bool = False
    gemini_soft_finalize_idle_seconds: int = 90
    artifact_watch_interval: float = 1.0
    artifact_stable_seconds: float = 5.0

    # === Gemini API (для голосовых) ===
    gemini_api_key: Optional[str] = None

    # === Стриминг ===
    stream_update_interval: float = 1.5  # секунды между editMessageText
    stream_max_message_length: int = 4096  # лимит Telegram
    stream_min_update_chars: int = 120
    stream_retry_max_delay: float = 30.0

    # === Prompt safety ===
    prompt_warn_chars: int = 12000
    prompt_max_chars: int = 60000
    prompt_confirm_timeout: int = 120

    # === Usage limits ===
    user_daily_token_limit: int = 0
    global_daily_token_limit: int = 0

    # === Polling ===
    polling_timeout: int = 10
    polling_concurrency_limit: int = 4

    # === State ===
    gateway_state_dir: str = field(default_factory=lambda: str(Path(".gateway_state")))

    # === Аппрув ===
    approval_timeout: int = 120  # секунды до авто-отклонения

    # === Логирование ===
    log_mode: str = "normal"
    log_level: str = "INFO"

    @classmethod
    def from_env(
        cls,
        env_path: Optional[str] = None,
        *,
        require_telegram_token: bool = True,
    ) -> Config:
        """Загрузить конфигурацию из .env файла и переменных окружения."""
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            if require_telegram_token:
                raise ValueError(
                    "TELEGRAM_BOT_TOKEN не задан. "
                    "Укажите его в .env файле или переменной окружения."
                )
            token = "__missing_telegram_bot_token__"

        # Парсинг TARGET_CHAT_ID
        chat_id_raw = os.getenv("TARGET_CHAT_ID", "").strip()
        target_chat_id = int(chat_id_raw) if chat_id_raw else None

        # Парсинг GEMINI_SANDBOX
        sandbox_raw = os.getenv("GEMINI_SANDBOX", "false").strip().lower()
        sandbox = sandbox_raw in ("true", "1", "yes")

        # Парсинг GEMINI_STREAM_DEBUG
        stream_debug_raw = os.getenv("GEMINI_STREAM_DEBUG", "false").strip().lower()
        stream_debug = stream_debug_raw in ("true", "1", "yes")

        # Парсинг GEMINI_WORKING_DIR
        working_dir_raw = os.getenv("GEMINI_WORKING_DIR", "").strip()
        working_dir = (
            Path(working_dir_raw).expanduser().resolve()
            if working_dir_raw
            else Path.home()
        )
        if not working_dir.exists() or not working_dir.is_dir():
            raise ValueError(
                f"Директория GEMINI_WORKING_DIR='{working_dir}' не существует "
                "или не является папкой. Проверьте настройки в .env файле."
            )

        include_directories = _parse_existing_directories(
            os.getenv("GEMINI_INCLUDE_DIRECTORIES", ""),
            "GEMINI_INCLUDE_DIRECTORIES",
        )

        artifact_roots_raw = os.getenv("GEMINI_ARTIFACT_ROOTS", "").strip()
        artifact_roots: list[str] = []
        if artifact_roots_raw:
            for raw_root in artifact_roots_raw.split(","):
                root = raw_root.strip()
                if not root:
                    continue
                resolved = Path(root).expanduser().resolve()
                if not resolved.exists() or not resolved.is_dir():
                    raise ValueError(
                        f"Директория GEMINI_ARTIFACT_ROOTS='{resolved}' не существует "
                        "или не является папкой. Проверьте настройки в .env файле."
                    )
                artifact_roots.append(str(resolved))
        else:
            artifact_roots.append(str(working_dir))

        state_dir_raw = os.getenv("GATEWAY_STATE_DIR", ".gateway_state").strip()
        state_dir = Path(state_dir_raw or ".gateway_state").expanduser().resolve()

        return cls(
            telegram_bot_token=token,
            target_chat_id=target_chat_id,
            gemini_model=os.getenv("GEMINI_MODEL", cls.gemini_model),
            gemini_target_version=os.getenv(
                "GEMINI_TARGET_VERSION", cls.gemini_target_version
            ),
            gemini_bin=os.getenv("GEMINI_BIN", cls.gemini_bin),
            gemini_approval_mode=os.getenv(
                "GEMINI_APPROVAL_MODE", cls.gemini_approval_mode
            ),
            gemini_working_dir=str(working_dir),
            gemini_include_directories=tuple(dict.fromkeys(include_directories)),
            gemini_artifact_roots=tuple(dict.fromkeys(artifact_roots)),
            gemini_cli_timeout=int(
                os.getenv("GEMINI_CLI_TIMEOUT", str(cls.gemini_cli_timeout))
            ),
            gemini_shutdown_grace_seconds=float(
                os.getenv(
                    "GEMINI_SHUTDOWN_GRACE_SECONDS",
                    str(cls.gemini_shutdown_grace_seconds),
                )
            ),
            gemini_sandbox=sandbox,
            gemini_stream_debug=stream_debug,
            gemini_soft_finalize_idle_seconds=int(
                os.getenv(
                    "GEMINI_SOFT_FINALIZE_IDLE_SECONDS",
                    str(cls.gemini_soft_finalize_idle_seconds),
                )
            ),
            artifact_watch_interval=float(
                os.getenv(
                    "ARTIFACT_WATCH_INTERVAL",
                    str(cls.artifact_watch_interval),
                )
            ),
            artifact_stable_seconds=float(
                os.getenv(
                    "ARTIFACT_STABLE_SECONDS",
                    str(cls.artifact_stable_seconds),
                )
            ),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            stream_update_interval=float(
                os.getenv(
                    "STREAM_UPDATE_INTERVAL",
                    str(cls.stream_update_interval),
                )
            ),
            stream_min_update_chars=int(
                os.getenv(
                    "STREAM_MIN_UPDATE_CHARS",
                    str(cls.stream_min_update_chars),
                )
            ),
            stream_retry_max_delay=float(
                os.getenv(
                    "STREAM_RETRY_MAX_DELAY",
                    str(cls.stream_retry_max_delay),
                )
            ),
            prompt_warn_chars=int(
                os.getenv("PROMPT_WARN_CHARS", str(cls.prompt_warn_chars))
            ),
            prompt_max_chars=int(
                os.getenv("PROMPT_MAX_CHARS", str(cls.prompt_max_chars))
            ),
            prompt_confirm_timeout=int(
                os.getenv(
                    "PROMPT_CONFIRM_TIMEOUT",
                    str(cls.prompt_confirm_timeout),
                )
            ),
            user_daily_token_limit=int(
                os.getenv(
                    "USER_DAILY_TOKEN_LIMIT",
                    str(cls.user_daily_token_limit),
                )
            ),
            global_daily_token_limit=int(
                os.getenv(
                    "GLOBAL_DAILY_TOKEN_LIMIT",
                    str(cls.global_daily_token_limit),
                )
            ),
            polling_timeout=int(os.getenv("POLLING_TIMEOUT", str(cls.polling_timeout))),
            polling_concurrency_limit=int(
                os.getenv(
                    "POLLING_CONCURRENCY_LIMIT",
                    str(cls.polling_concurrency_limit),
                )
            ),
            approval_timeout=int(
                os.getenv("APPROVAL_TIMEOUT", str(cls.approval_timeout))
            ),
            gateway_state_dir=str(state_dir),
            log_mode=_normalize_log_mode(os.getenv("LOG_MODE", cls.log_mode)),
            log_level=_resolve_log_level(
                os.getenv("LOG_MODE", cls.log_mode),
                os.getenv("LOG_LEVEL"),
            ),
        )

    def redacted_dict(self) -> dict[str, object]:
        """Вернуть безопасный для логов снимок конфигурации."""
        return {
            "telegram_bot_token": _mask_secret(self.telegram_bot_token),
            "target_chat_id": self.target_chat_id,
            "gemini_model": self.gemini_model,
            "gemini_target_version": self.gemini_target_version,
            "gemini_bin": self.gemini_bin,
            "gemini_approval_mode": self.gemini_approval_mode,
            "gemini_working_dir": self.gemini_working_dir,
            "gemini_include_directories": self.gemini_include_directories,
            "gemini_artifact_roots": self.gemini_artifact_roots,
            "gemini_cli_timeout": self.gemini_cli_timeout,
            "gemini_shutdown_grace_seconds": self.gemini_shutdown_grace_seconds,
            "gemini_sandbox": self.gemini_sandbox,
            "gemini_stream_debug": self.gemini_stream_debug,
            "gemini_soft_finalize_idle_seconds": (
                self.gemini_soft_finalize_idle_seconds
            ),
            "artifact_watch_interval": self.artifact_watch_interval,
            "artifact_stable_seconds": self.artifact_stable_seconds,
            "stream_update_interval": self.stream_update_interval,
            "stream_min_update_chars": self.stream_min_update_chars,
            "stream_retry_max_delay": self.stream_retry_max_delay,
            "prompt_warn_chars": self.prompt_warn_chars,
            "prompt_max_chars": self.prompt_max_chars,
            "prompt_confirm_timeout": self.prompt_confirm_timeout,
            "user_daily_token_limit": self.user_daily_token_limit,
            "global_daily_token_limit": self.global_daily_token_limit,
            "polling_timeout": self.polling_timeout,
            "polling_concurrency_limit": self.polling_concurrency_limit,
            "gateway_state_dir": self.gateway_state_dir,
            "approval_timeout": self.approval_timeout,
            "log_mode": self.log_mode,
            "log_level": self.log_level,
            "gemini_api_key": _mask_secret(self.gemini_api_key),
        }

    @property
    def approval_mode_flag(self) -> list[str]:
        """Аргументы командной строки для approval-mode."""
        mode = self.gemini_approval_mode
        if mode == "yolo":
            return ["--yolo"]
        elif mode in ("default", "auto_edit", "plan"):
            return [f"--approval-mode={mode}"]
        return []

    @property
    def sandbox_flag(self) -> list[str]:
        """Аргумент --sandbox, если включён."""
        return ["--sandbox"] if self.gemini_sandbox else []

    @property
    def include_directories_flag(self) -> list[str]:
        """Аргументы для дополнительных директорий Gemini workspace."""
        if not self.gemini_include_directories:
            return []
        return ["--include-directories", ",".join(self.gemini_include_directories)]


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _normalize_log_mode(value: str | None) -> str:
    normalized = (value or "normal").strip().lower()
    if normalized in {"quiet", "normal", "debug"}:
        return normalized
    return "normal"


def _resolve_log_level(log_mode: str | None, log_level: str | None) -> str:
    if log_level and log_level.strip():
        return log_level.strip().upper()
    return {
        "quiet": "WARNING",
        "debug": "DEBUG",
    }.get(_normalize_log_mode(log_mode), "INFO")


def _parse_existing_directories(raw_value: str, env_name: str) -> list[str]:
    directories: list[str] = []
    for raw_path in raw_value.strip().split(","):
        path = raw_path.strip()
        if not path:
            continue
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(
                f"Директория {env_name}='{resolved}' не существует "
                "или не является папкой. Проверьте настройки в .env файле."
            )
        directories.append(str(resolved))
    return directories
