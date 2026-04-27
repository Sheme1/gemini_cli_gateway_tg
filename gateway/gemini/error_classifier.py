from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GeminiErrorHint:
    code: str
    title: str
    cause: str
    fix: str
    technical: str

    def format_for_user(self) -> str:
        return (
            f"{self.title}\n\n"
            f"Что случилось: {self.cause}\n"
            f"Как исправить: {self.fix}\n\n"
            f"Технически: {self.technical}"
        )


def classify_gemini_error(text: str, returncode: int | None = None) -> GeminiErrorHint:
    normalized = " ".join(text.split())
    lowered = normalized.lower()

    if returncode == 42 or _contains_any(lowered, "input error", "invalid prompt"):
        return _hint(
            "input_error",
            "Gemini CLI отклонил входные параметры.",
            "Запрос или аргументы запуска оказались некорректными для headless-режима.",
            "Проверьте длину запроса, модель, session_id и дополнительные CLI-флаги.",
            normalized,
        )

    if returncode == 53 or _contains_any(lowered, "turn limit exceeded"):
        return _hint(
            "turn_limit",
            "Достигнут лимит ходов Gemini CLI.",
            "Текущая сохранённая сессия стала слишком длинной для продолжения.",
            "Запустите /new или выберите более ранний/другой диалог через /sessions.",
            normalized,
        )

    if _contains_any(
        lowered, "enoent", "not found", "is not recognized", "no such file"
    ):
        return _hint(
            "missing_binary",
            "Gemini CLI не найден.",
            "Шлюз не смог запустить команду Gemini CLI.",
            "Проверьте GEMINI_BIN и PATH. Для systemd лучше указать полный путь к gemini.",
            normalized,
        )

    if _contains_any(
        lowered,
        "untrusted workspace",
        "untrusted folder",
        "fataluntrustedworkspaceerror",
        "trust workspace",
        "folder trust",
    ):
        return _hint(
            "untrusted_workspace",
            "Gemini CLI не доверяет рабочей папке.",
            "В headless-режиме Gemini CLI не может показать интерактивный trust-dialog.",
            "Включите GEMINI_SKIP_TRUST=true или задайте GEMINI_CLI_TRUST_WORKSPACE=true для пользователя сервиса.",
            normalized,
        )

    if _contains_any(
        lowered,
        "not authenticated",
        "login",
        "oauth",
        "auth failed",
        "credential",
        "keychain",
        "keytar",
    ):
        return _hint(
            "auth",
            "Gemini CLI не авторизован.",
            "Gemini CLI не нашёл рабочую авторизацию для пользователя сервиса.",
            "Запустите gemini под тем же пользователем, что и gateway, и выполните авторизацию.",
            normalized,
        )

    if _contains_any(lowered, "policy") and _contains_any(
        lowered, "deny", "denied", "blocked", "disallowed"
    ):
        return _hint(
            "policy_denied",
            "Действие заблокировано policy rules.",
            "Gemini CLI получил запрет от Policy Engine.",
            "Проверьте пользовательские или admin policy TOML-файлы и текущий approval mode.",
            normalized,
        )

    if _contains_any(lowered, "quota", "rate limit", "resource exhausted", "429"):
        return _hint(
            "quota",
            "Gemini временно отклонил запрос.",
            "Похоже на лимит квоты или rate limit.",
            "Повторите позже, смените модель на более дешёвую или проверьте лимиты аккаунта.",
            normalized,
        )

    if _contains_any(lowered, "overloaded", "capacity", "fallback model"):
        return _hint(
            "model_capacity",
            "Модель Gemini временно недоступна.",
            "Выбранная модель перегружена или требует fallback-маршрутизации.",
            "Попробуйте модельный пресет auto/flash или повторите запрос позже.",
            normalized,
        )

    if _contains_any(lowered, "model", "not supported") and _contains_any(
        lowered, "invalid", "unknown", "not found", "not supported"
    ):
        return _hint(
            "invalid_model",
            "Модель Gemini недоступна.",
            "Выбранная модель не поддерживается текущей авторизацией или версией CLI.",
            "Откройте /model и выберите другой пресет или измените GEMINI_MODEL в .env.",
            normalized,
        )

    if _contains_any(lowered, "timeout", "timed out", "did not answer"):
        return _hint(
            "timeout",
            "Gemini CLI завис или слишком долго молчал.",
            "Процесс не прислал новых stream-json событий до таймаута.",
            "Упростите запрос, отключите тяжёлые MCP/skills или увеличьте GEMINI_CLI_TIMEOUT.",
            normalized,
        )

    if _contains_any(lowered, "mcp", "tool") and _contains_any(
        lowered, "failed", "error", "timeout"
    ):
        return _hint(
            "tool_failure",
            "Ошибка инструмента Gemini CLI.",
            "Один из MCP-серверов, skills или встроенных инструментов завершился с ошибкой.",
            "Проверьте /mcp, /skills и подробности в /diagnostics.",
            normalized,
        )

    code = f"exit_{returncode}" if returncode is not None else "unknown"
    return _hint(
        code,
        "Gemini CLI завершился с ошибкой.",
        "Процесс Gemini вернул ненулевой код или неожиданное сообщение.",
        "Проверьте /diagnostics и серверные логи. Часто помогает /new или смена модели.",
        normalized,
    )


def _hint(
    code: str,
    title: str,
    cause: str,
    fix: str,
    technical: str,
) -> GeminiErrorHint:
    return GeminiErrorHint(
        code=code,
        title=title,
        cause=cause,
        fix=fix,
        technical=_compact_technical(technical),
    )


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _compact_technical(text: str, limit: int = 900) -> str:
    stripped = re.sub(r"\s+", " ", text).strip()
    if not stripped:
        return "нет stderr/stdout деталей"
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "..."
