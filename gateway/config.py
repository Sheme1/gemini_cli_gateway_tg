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
    target_chat_ids: tuple[int, ...] = field(default_factory=tuple)

    # === Gemini CLI ===
    gemini_model: str = "auto"
    gemini_target_version: str = "0.39.1"
    gemini_bin: str = "gemini"
    gemini_skip_trust: bool = True
    gemini_approval_mode: str = "yolo"  # default / auto_edit / yolo / plan
    gemini_working_dir: str = field(default_factory=lambda: str(Path.home()))
    gemini_include_directories: tuple[str, ...] = field(default_factory=tuple)
    gemini_policy_paths: tuple[str, ...] = field(default_factory=tuple)
    gemini_admin_policy_paths: tuple[str, ...] = field(default_factory=tuple)
    gemini_allowed_mcp_server_names: tuple[str, ...] = field(default_factory=tuple)
    gemini_extensions: tuple[str, ...] = field(default_factory=tuple)
    gemini_screen_reader: bool = False
    gemini_artifact_roots: tuple[str, ...] = field(default_factory=tuple)
    gemini_cli_timeout: int = 600  # секунды
    gemini_stream_reader_limit_bytes: int = 8 * 1024 * 1024
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
    gateway_experimental_multi_user_workspaces: bool = False
    gateway_user_workspaces_dir: str = field(
        default_factory=lambda: str(Path(".gateway_state") / "users")
    )

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
        _load_env_file(env_path)

        token = _read_telegram_token(require_telegram_token)

        target_chat_ids = _parse_target_chat_ids(os.getenv("TARGET_CHAT_ID", ""))
        target_chat_id = target_chat_ids[0] if target_chat_ids else None

        sandbox = _parse_bool(os.getenv("GEMINI_SANDBOX"), default=False)
        stream_debug = _parse_bool(os.getenv("GEMINI_STREAM_DEBUG"), default=False)
        skip_trust = _parse_bool(os.getenv("GEMINI_SKIP_TRUST"), default=True)
        screen_reader = _parse_bool(os.getenv("GEMINI_SCREEN_READER"), default=False)

        working_dir = _parse_working_dir()
        include_directories = _parse_existing_directories(
            os.getenv("GEMINI_INCLUDE_DIRECTORIES", ""),
            "GEMINI_INCLUDE_DIRECTORIES",
        )
        artifact_roots = _parse_artifact_roots(working_dir)
        state_dir, multi_user_workspaces, user_workspaces_dir = _parse_state_paths()

        return cls(
            telegram_bot_token=token,
            target_chat_id=target_chat_id,
            target_chat_ids=target_chat_ids,
            gemini_model=os.getenv("GEMINI_MODEL", cls.gemini_model),
            gemini_target_version=os.getenv(
                "GEMINI_TARGET_VERSION", cls.gemini_target_version
            ),
            gemini_bin=os.getenv("GEMINI_BIN", cls.gemini_bin),
            gemini_skip_trust=skip_trust,
            gemini_approval_mode=_normalize_approval_mode(
                os.getenv("GEMINI_APPROVAL_MODE", cls.gemini_approval_mode)
            ),
            gemini_working_dir=str(working_dir),
            gemini_include_directories=tuple(dict.fromkeys(include_directories)),
            gemini_policy_paths=tuple(
                dict.fromkeys(_parse_csv_values(os.getenv("GEMINI_POLICY_PATHS", "")))
            ),
            gemini_admin_policy_paths=tuple(
                dict.fromkeys(
                    _parse_csv_values(os.getenv("GEMINI_ADMIN_POLICY_PATHS", ""))
                )
            ),
            gemini_allowed_mcp_server_names=tuple(
                dict.fromkeys(
                    _parse_csv_values(os.getenv("GEMINI_ALLOWED_MCP_SERVER_NAMES", ""))
                )
            ),
            gemini_extensions=tuple(
                dict.fromkeys(_parse_csv_values(os.getenv("GEMINI_EXTENSIONS", "")))
            ),
            gemini_screen_reader=screen_reader,
            gemini_artifact_roots=tuple(dict.fromkeys(artifact_roots)),
            gemini_cli_timeout=int(
                os.getenv("GEMINI_CLI_TIMEOUT", str(cls.gemini_cli_timeout))
            ),
            gemini_stream_reader_limit_bytes=int(
                os.getenv(
                    "GEMINI_STREAM_READER_LIMIT_BYTES",
                    str(cls.gemini_stream_reader_limit_bytes),
                )
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
            gateway_experimental_multi_user_workspaces=multi_user_workspaces,
            gateway_user_workspaces_dir=str(user_workspaces_dir),
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
            "target_chat_ids": self.allowed_target_chat_ids,
            "gemini_model": self.gemini_model,
            "gemini_target_version": self.gemini_target_version,
            "gemini_bin": self.gemini_bin,
            "gemini_skip_trust": self.gemini_skip_trust,
            "gemini_approval_mode": self.gemini_approval_mode,
            "gemini_working_dir": self.gemini_working_dir,
            "gemini_include_directories": self.gemini_include_directories,
            "gemini_policy_paths": self.gemini_policy_paths,
            "gemini_admin_policy_paths": self.gemini_admin_policy_paths,
            "gemini_allowed_mcp_server_names": self.gemini_allowed_mcp_server_names,
            "gemini_extensions": self.gemini_extensions,
            "gemini_screen_reader": self.gemini_screen_reader,
            "gemini_artifact_roots": self.gemini_artifact_roots,
            "gemini_cli_timeout": self.gemini_cli_timeout,
            "gemini_stream_reader_limit_bytes": self.gemini_stream_reader_limit_bytes,
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
            "gateway_experimental_multi_user_workspaces": (
                self.gateway_experimental_multi_user_workspaces
            ),
            "gateway_user_workspaces_dir": self.gateway_user_workspaces_dir,
            "approval_timeout": self.approval_timeout,
            "log_mode": self.log_mode,
            "log_level": self.log_level,
            "gemini_api_key": _mask_secret(self.gemini_api_key),
        }

    @property
    def approval_mode_flag(self) -> list[str]:
        """Аргументы командной строки для approval-mode."""
        mode = self.gemini_approval_mode
        if mode in ("default", "auto_edit", "yolo", "plan"):
            return [f"--approval-mode={mode}"]
        return []

    @property
    def allowed_target_chat_ids(self) -> tuple[int, ...]:
        """Нормализованный allowlist Telegram chat/user id."""
        if self.target_chat_ids:
            return self.target_chat_ids
        if self.target_chat_id is not None:
            return (self.target_chat_id,)
        return ()

    @property
    def sandbox_flag(self) -> list[str]:
        """Аргумент --sandbox, если включён."""
        return ["--sandbox"] if self.gemini_sandbox else []

    @property
    def skip_trust_flag(self) -> list[str]:
        """Аргумент --skip-trust для headless Gemini CLI 0.39+."""
        return ["--skip-trust"] if self.gemini_skip_trust else []

    @property
    def include_directories_flag(self) -> list[str]:
        """Аргументы для дополнительных директорий Gemini workspace."""
        if not self.gemini_include_directories:
            return []
        return ["--include-directories", ",".join(self.gemini_include_directories)]

    @property
    def policy_flags(self) -> list[str]:
        """Аргументы --policy для пользовательских policy rules."""
        return _repeat_flag("--policy", self.gemini_policy_paths)

    @property
    def admin_policy_flags(self) -> list[str]:
        """Аргументы --admin-policy для дополнительных admin policy rules."""
        return _repeat_flag("--admin-policy", self.gemini_admin_policy_paths)

    @property
    def allowed_mcp_server_names_flag(self) -> list[str]:
        """Аргументы allowlist для MCP-серверов."""
        return _repeat_flag(
            "--allowed-mcp-server-names",
            self.gemini_allowed_mcp_server_names,
        )

    @property
    def extensions_flag(self) -> list[str]:
        """Аргументы выбора Gemini CLI extensions."""
        return _repeat_flag("--extensions", self.gemini_extensions)

    @property
    def screen_reader_flag(self) -> list[str]:
        """Аргумент --screen-reader, если включён."""
        return ["--screen-reader"] if self.gemini_screen_reader else []


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


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _load_env_file(env_path: Optional[str]) -> None:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()


def _read_telegram_token(require_telegram_token: bool) -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    if require_telegram_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN не задан. "
            "Укажите его в .env файле или переменной окружения."
        )
    return "__missing_telegram_bot_token__"


def _parse_working_dir() -> Path:
    working_dir_raw = os.getenv("GEMINI_WORKING_DIR", "").strip()
    working_dir = (
        Path(working_dir_raw).expanduser().resolve() if working_dir_raw else Path.home()
    )
    _ensure_existing_directory(working_dir, "GEMINI_WORKING_DIR")
    return working_dir


def _parse_artifact_roots(working_dir: Path) -> list[str]:
    artifact_roots_raw = os.getenv("GEMINI_ARTIFACT_ROOTS", "").strip()
    if not artifact_roots_raw:
        return [str(working_dir)]

    artifact_roots: list[str] = []
    for raw_root in artifact_roots_raw.split(","):
        root = raw_root.strip()
        if not root:
            continue
        resolved = Path(root).expanduser().resolve()
        _ensure_existing_directory(resolved, "GEMINI_ARTIFACT_ROOTS")
        artifact_roots.append(str(resolved))
    return artifact_roots


def _parse_state_paths(
    default_state_dir: str = ".gateway_state",
) -> tuple[Path, bool, Path]:
    state_dir_raw = os.getenv("GATEWAY_STATE_DIR", default_state_dir).strip()
    state_dir = Path(state_dir_raw or default_state_dir).expanduser().resolve()
    multi_user_workspaces = _parse_bool(
        os.getenv("GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES"),
        default=False,
    )
    user_workspaces_raw = os.getenv("GATEWAY_USER_WORKSPACES_DIR", "").strip()
    user_workspaces_dir = (
        Path(user_workspaces_raw).expanduser().resolve()
        if user_workspaces_raw
        else state_dir / "users"
    )
    return state_dir, multi_user_workspaces, user_workspaces_dir


def _normalize_approval_mode(value: str | None) -> str:
    normalized = (value or "yolo").strip().lower()
    if normalized in {"default", "auto_edit", "yolo", "plan"}:
        return normalized
    return "yolo"


def _parse_target_chat_ids(raw_value: str | None) -> tuple[int, ...]:
    raw = (raw_value or "").strip()
    if not raw:
        return ()

    values: list[int] = []
    seen: set[int] = set()
    for raw_item in raw.split(","):
        item = raw_item.strip()
        if not item:
            continue
        try:
            chat_id = int(item)
        except ValueError as exc:
            raise ValueError(
                "TARGET_CHAT_ID содержит некорректный Telegram chat/user id "
                f"'{item}'. Используйте число или список через запятую, "
                "например TARGET_CHAT_ID=111111111,222222222."
            ) from exc
        if chat_id not in seen:
            seen.add(chat_id)
            values.append(chat_id)
    return tuple(values)


def _parse_csv_values(raw_value: str) -> list[str]:
    values: list[str] = []
    for raw_item in raw_value.strip().split(","):
        item = raw_item.strip()
        if item:
            values.append(item)
    return values


def _repeat_flag(flag: str, values: tuple[str, ...]) -> list[str]:
    args: list[str] = []
    for value in values:
        args.extend([flag, value])
    return args


def _parse_existing_directories(raw_value: str, env_name: str) -> list[str]:
    directories: list[str] = []
    for raw_path in raw_value.strip().split(","):
        path = raw_path.strip()
        if not path:
            continue
        resolved = Path(path).expanduser().resolve()
        _ensure_existing_directory(resolved, env_name)
        directories.append(str(resolved))
    return directories


def _ensure_existing_directory(path: Path, env_name: str) -> None:
    if path.exists() and path.is_dir():
        return
    raise ValueError(
        f"Директория {env_name}='{path}' не существует "
        "или не является папкой. Проверьте настройки в .env файле."
    )
