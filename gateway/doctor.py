from __future__ import annotations

import importlib.util
import json
import os
import asyncio
import shutil
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import Any

from gateway.config import Config
from gateway.runtime import probe_command


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    details: str
    hint: str = ""


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck]
    config: dict[str, Any] | None = None

    @property
    def has_errors(self) -> bool:
        return any(check.status == "error" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.has_errors,
            "checks": [asdict(check) for check in self.checks],
            "config": self.config,
        }


async def run_doctor() -> DoctorReport:
    checks: list[DoctorCheck] = []
    env_path = Path(".env")
    checks.append(
        DoctorCheck(
            name=".env",
            status="ok" if env_path.exists() else "warn",
            details=str(env_path.resolve()) if env_path.exists() else ".env не найден",
            hint="Скопируйте .env.example в .env и заполните TELEGRAM_BOT_TOKEN."
            if not env_path.exists()
            else "",
        )
    )

    config: Config | None = None
    try:
        config = Config.from_env(require_telegram_token=False)
        checks.append(
            DoctorCheck(
                name="config",
                status="ok",
                details="Конфигурация загружена.",
            )
        )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                name="config",
                status="error",
                details=f"{type(exc).__name__}: {exc}",
                hint="Проверьте обязательные переменные и пути в .env.",
            )
        )
        return DoctorReport(checks=checks)

    if config.telegram_bot_token == "__missing_telegram_bot_token__":
        checks.append(
            DoctorCheck(
                name="TELEGRAM_BOT_TOKEN",
                status="warn",
                details="не задан",
                hint="Gateway не сможет стартовать без токена, но local doctor продолжает проверку.",
            )
        )

    checks.append(_headless_trust_check(config))
    checks.extend(_python_import_checks())
    checks.extend(await _runtime_checks(config))
    checks.extend(await _gemini_capability_checks(config))
    checks.extend(_path_checks(config))
    return DoctorReport(checks=checks, config=config.redacted_dict())


def format_doctor_text(report: DoctorReport, *, html: bool = False) -> str:
    status = "OK" if not report.has_errors else "ERROR"
    title = f"Doctor: {status}"
    lines = [f"🩺 <b>{escape(title)}</b>" if html else f"Doctor: {status}", ""]
    for check in report.checks:
        icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(check.status, "•")
        name = escape(check.name) if html else check.name
        details = escape(check.details) if html else check.details
        if html:
            lines.append(f"{icon} <b>{name}</b>: {details}")
        else:
            lines.append(f"{icon} {name}: {details}")
        if check.hint:
            hint = escape(check.hint) if html else check.hint
            lines.append(f"   {hint}")

    if report.config:
        lines.extend(["", "<b>Config:</b>" if html else "Config:"])
        for key, value in sorted(report.config.items()):
            line = f"{key}={value}"
            lines.append(f"<code>{escape(line)}</code>" if html else line)

    return "\n".join(lines).strip()


def format_doctor_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _python_import_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    modules = {
        "aiogram": "aiogram",
        "aiohttp": "aiohttp",
        "aiofiles": "aiofiles",
        "python-dotenv": "dotenv",
    }
    for package_name, module_name in modules.items():
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            DoctorCheck(
                name=f"python import {package_name}",
                status="ok" if found else "error",
                details="найден" if found else "не найден",
                hint="Установите зависимости: pip install -r requirements.txt"
                if not found
                else "",
            )
        )
    return checks


def _headless_trust_check(config: Config) -> DoctorCheck:
    env_trust = os.getenv("GEMINI_CLI_TRUST_WORKSPACE", "").strip().lower()
    env_trust_enabled = env_trust in {"true", "1", "yes"}
    if config.gemini_skip_trust:
        return DoctorCheck(
            name="headless trust",
            status="ok",
            details="GEMINI_SKIP_TRUST=true; prompt-запуски передают --skip-trust.",
        )
    if env_trust_enabled:
        return DoctorCheck(
            name="headless trust",
            status="ok",
            details="GEMINI_CLI_TRUST_WORKSPACE=true задан во внешнем окружении.",
        )
    return DoctorCheck(
        name="headless trust",
        status="warn",
        details="GEMINI_SKIP_TRUST=false и GEMINI_CLI_TRUST_WORKSPACE не включён.",
        hint=(
            "Gemini CLI 0.39.1 может остановиться на trust-check в headless-режиме. "
            "Включите GEMINI_SKIP_TRUST=true или GEMINI_CLI_TRUST_WORKSPACE=true."
        ),
    )


async def _runtime_checks(config: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        gemini_probe = await probe_command(
            config.gemini_bin,
            "--version",
            cwd=config.gemini_working_dir,
            limit=config.gemini_stream_reader_limit_bytes,
        )
        gemini_status = (
            "ok"
            if gemini_probe.version.strip() == config.gemini_target_version
            else "warn"
        )
        checks.append(
            DoctorCheck(
                name="gemini",
                status=gemini_status,
                details=f"{gemini_probe.version} ({gemini_probe.path})",
                hint=(
                    f"Ожидалась версия {config.gemini_target_version}; "
                    "несовпадение не блокирует запуск, но может менять формат вывода."
                )
                if gemini_status == "warn"
                else "",
            )
        )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                name="gemini",
                status="error",
                details=f"{type(exc).__name__}: {exc}",
                hint="Проверьте GEMINI_BIN, PATH и установку @google/gemini-cli.",
            )
        )

    try:
        node_probe = await probe_command("node", "--version")
        checks.append(
            DoctorCheck(
                name="node",
                status="ok",
                details=f"{node_probe.version} ({node_probe.path})",
            )
        )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                name="node",
                status="error",
                details=f"{type(exc).__name__}: {exc}",
                hint="Установите Node.js и проверьте PATH.",
            )
        )
    return checks


async def _gemini_capability_checks(config: Config) -> list[DoctorCheck]:
    required_flags = {
        "--skip-trust",
        "--approval-mode",
        "--policy",
        "--admin-policy",
        "--acp",
        "--raw-output",
    }
    try:
        output = await _command_output(
            config.gemini_bin,
            "--help",
            cwd=config.gemini_working_dir,
            limit=config.gemini_stream_reader_limit_bytes,
        )
    except Exception as exc:
        return [
            DoctorCheck(
                name="gemini capabilities",
                status="warn",
                details=f"{type(exc).__name__}: {exc}",
                hint="Не удалось проверить `gemini --help`; version check уже выполнен отдельно.",
            )
        ]

    missing = sorted(flag for flag in required_flags if flag not in output)
    if missing:
        return [
            DoctorCheck(
                name="gemini capabilities",
                status="warn",
                details="missing flags: " + ", ".join(missing),
                hint=(
                    "Установленная версия Gemini CLI может отличаться от ожидаемой "
                    f"{config.gemini_target_version} или иметь другой набор флагов."
                ),
            )
        ]
    return [
        DoctorCheck(
            name="gemini capabilities",
            status="ok",
            details=f"{config.gemini_target_version} headless/security flags are available.",
        )
    ]


async def _command_output(
    command: str,
    *args: str,
    cwd: str | None = None,
    limit: int = 8 * 1024 * 1024,
) -> str:
    executable = shutil.which(command) or command
    process = await asyncio.create_subprocess_exec(
        executable,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        limit=limit,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{command} did not answer within 10 seconds") from exc

    output = (stdout + stderr).decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise RuntimeError(
            f"{command} exited with code {process.returncode}: {output[:500]}"
        )
    return output


def _path_checks(config: Config) -> list[DoctorCheck]:
    checks = [
        _directory_check("GEMINI_WORKING_DIR", Path(config.gemini_working_dir), True),
        _directory_check("GATEWAY_STATE_DIR", Path(config.gateway_state_dir), True),
    ]
    if config.gateway_experimental_multi_user_workspaces:
        checks.append(
            _directory_check(
                "GATEWAY_USER_WORKSPACES_DIR",
                Path(config.gateway_user_workspaces_dir),
                True,
            )
        )
    for path in config.gemini_include_directories:
        checks.append(_directory_check("GEMINI_INCLUDE_DIRECTORIES", Path(path), False))
    for path in config.gemini_artifact_roots:
        checks.append(_directory_check("GEMINI_ARTIFACT_ROOTS", Path(path), False))
    for path in config.gemini_policy_paths:
        checks.append(_path_exists_check("GEMINI_POLICY_PATHS", Path(path)))
    for path in config.gemini_admin_policy_paths:
        checks.append(_path_exists_check("GEMINI_ADMIN_POLICY_PATHS", Path(path)))
    return checks


def _directory_check(name: str, path: Path, require_writable: bool) -> DoctorCheck:
    if require_writable and not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return DoctorCheck(
                name=name,
                status="error",
                details=f"{path}: {type(exc).__name__}: {exc}",
                hint="Дайте пользователю сервиса права на создание директории.",
            )

    if not path.exists() or not path.is_dir():
        return DoctorCheck(
            name=name,
            status="error",
            details=f"{path} не является директорией",
            hint="Проверьте путь в .env.",
        )

    if not require_writable:
        return DoctorCheck(name=name, status="ok", details=str(path))

    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        return DoctorCheck(
            name=name,
            status="error",
            details=f"{path}: {type(exc).__name__}: {exc}",
            hint="Дайте пользователю сервиса права на запись.",
        )
    return DoctorCheck(name=name, status="ok", details=f"{path} writable")


def _path_exists_check(name: str, path: Path) -> DoctorCheck:
    resolved = path.expanduser()
    if resolved.exists():
        return DoctorCheck(name=name, status="ok", details=str(resolved))
    return DoctorCheck(
        name=name,
        status="warn",
        details=f"{resolved} не найден",
        hint="Gemini CLI вернёт ошибку, если переданный policy путь недоступен.",
    )
