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


@dataclass(frozen=True)
class _GeminiErrorRule:
    code: str
    title: str
    cause: str
    fix: str
    needle_groups: tuple[tuple[str, ...], ...] = ()
    returncodes: tuple[int, ...] = ()


_GEMINI_ERROR_RULES: tuple[_GeminiErrorRule, ...] = (
    _GeminiErrorRule(
        code="input_error",
        title="Gemini CLI отклонил входные параметры.",
        cause="Запрос или аргументы запуска оказались некорректными для headless-режима.",
        fix="Проверьте длину запроса, модель, session_id и дополнительные CLI-флаги.",
        needle_groups=(("input error", "invalid prompt"),),
        returncodes=(42,),
    ),
    _GeminiErrorRule(
        code="turn_limit",
        title="Достигнут лимит ходов Gemini CLI.",
        cause="Текущая сохранённая сессия стала слишком длинной для продолжения.",
        fix="Запустите /new или выберите более ранний/другой диалог через /sessions.",
        needle_groups=(("turn limit exceeded",),),
        returncodes=(53,),
    ),
    _GeminiErrorRule(
        code="missing_binary",
        title="Gemini CLI не найден.",
        cause="Шлюз не смог запустить команду Gemini CLI.",
        fix="Проверьте GEMINI_BIN и PATH. Для systemd лучше указать полный путь к gemini.",
        needle_groups=(("enoent", "not found", "is not recognized", "no such file"),),
    ),
    _GeminiErrorRule(
        code="untrusted_workspace",
        title="Gemini CLI не доверяет рабочей папке.",
        cause="В headless-режиме Gemini CLI не может показать интерактивный trust-dialog.",
        fix="Включите GEMINI_SKIP_TRUST=true или задайте GEMINI_CLI_TRUST_WORKSPACE=true для пользователя сервиса.",
        needle_groups=(
            (
                "untrusted workspace",
                "untrusted folder",
                "fataluntrustedworkspaceerror",
                "trust workspace",
                "folder trust",
            ),
        ),
    ),
    _GeminiErrorRule(
        code="auth",
        title="Gemini CLI не авторизован.",
        cause="Gemini CLI не нашёл рабочую авторизацию для пользователя сервиса.",
        fix="Запустите gemini под тем же пользователем, что и gateway, и выполните авторизацию.",
        needle_groups=(
            (
                "not authenticated",
                "login",
                "oauth",
                "auth failed",
                "credential",
                "keychain",
                "keytar",
            ),
        ),
    ),
    _GeminiErrorRule(
        code="policy_denied",
        title="Действие заблокировано policy rules.",
        cause="Gemini CLI получил запрет от Policy Engine.",
        fix="Проверьте пользовательские или admin policy TOML-файлы и текущий approval mode.",
        needle_groups=(
            ("policy",),
            ("deny", "denied", "blocked", "disallowed"),
        ),
    ),
    _GeminiErrorRule(
        code="quota",
        title="Gemini временно отклонил запрос.",
        cause="Похоже на лимит квоты или rate limit.",
        fix="Повторите позже, смените модель на более дешёвую или проверьте лимиты аккаунта.",
        needle_groups=(("quota", "rate limit", "resource exhausted", "429"),),
    ),
    _GeminiErrorRule(
        code="model_capacity",
        title="Модель Gemini временно недоступна.",
        cause="Выбранная модель перегружена или требует fallback-маршрутизации.",
        fix="Попробуйте модельный пресет auto/flash или повторите запрос позже.",
        needle_groups=(("overloaded", "capacity", "fallback model"),),
    ),
    _GeminiErrorRule(
        code="invalid_model",
        title="Модель Gemini недоступна.",
        cause="Выбранная модель не поддерживается текущей авторизацией или версией CLI.",
        fix="Откройте /model и выберите другой пресет или измените GEMINI_MODEL в .env.",
        needle_groups=(
            ("model", "not supported"),
            ("invalid", "unknown", "not found", "not supported"),
        ),
    ),
    _GeminiErrorRule(
        code="timeout",
        title="Gemini CLI завис или слишком долго молчал.",
        cause="Процесс не прислал новых stream-json событий до таймаута.",
        fix="Упростите запрос, отключите тяжёлые MCP/skills или увеличьте GEMINI_CLI_TIMEOUT.",
        needle_groups=(("timeout", "timed out", "did not answer"),),
    ),
    _GeminiErrorRule(
        code="tool_failure",
        title="Ошибка инструмента Gemini CLI.",
        cause="Один из MCP-серверов, skills или встроенных инструментов завершился с ошибкой.",
        fix="Проверьте /mcp, /skills и подробности в /diagnostics.",
        needle_groups=(
            ("mcp", "tool"),
            ("failed", "error", "timeout"),
        ),
    ),
)


def classify_gemini_error(text: str, returncode: int | None = None) -> GeminiErrorHint:
    normalized = " ".join(text.split())
    lowered = normalized.lower()

    for rule in _GEMINI_ERROR_RULES:
        if _matches_rule(rule, lowered, returncode):
            return _hint(rule.code, rule.title, rule.cause, rule.fix, normalized)

    code = f"exit_{returncode}" if returncode is not None else "unknown"
    return _hint(
        code,
        "Gemini CLI завершился с ошибкой.",
        "Процесс Gemini вернул ненулевой код или неожиданное сообщение.",
        "Проверьте /diagnostics и серверные логи. Часто помогает /new или смена модели.",
        normalized,
    )


def _matches_rule(rule: _GeminiErrorRule, text: str, returncode: int | None) -> bool:
    if returncode in rule.returncodes:
        return True
    return bool(rule.needle_groups) and all(
        _contains_any(text, *needles) for needles in rule.needle_groups
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
