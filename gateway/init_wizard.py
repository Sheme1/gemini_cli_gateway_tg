from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gateway.config import Config
from gateway.user_environment import UserEnvironmentResolver


INIT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("name", "Как к тебе обращаться? Пример: Тимофей."),
    (
        "language_tone",
        "На каком языке и в каком тоне отвечать? Пример: русский, спокойно и по делу.",
    ),
    (
        "typical_tasks",
        "Какие задачи чаще всего будут у бота? Пример: код, тексты, документы, учёба.",
    ),
    (
        "response_format",
        "Какой формат ответов удобен? Пример: сначала краткий ответ, потом шаги.",
    ),
    (
        "boundaries",
        "Что нельзя делать или что важно уточнять? Пример: не выдумывать факты, "
        "спрашивать при нехватке данных.",
    ),
)

_FORBIDDEN_GEMINI_MD_TERMS = (
    "telegram",
    "gateway",
    "семья",
    "семей",
    "family",
    "skill",
    "skills",
    "tool",
    "tools",
    "навык",
    "навыки",
    "инструмент",
    "инструменты",
    "systemd",
    "ubuntu",
    "workspace",
    "активирую",
    "воспользуюсь",
)


@dataclass
class InitDraft:
    step: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    preview_markdown: str = ""


@dataclass(frozen=True)
class InitAnswerResult:
    complete: bool
    next_question: str = ""
    profile: dict[str, Any] | None = None


class InitWizardStore:
    """Stores /init questionnaire progress and per-user profile files."""

    def __init__(self, config: Config):
        self.config = config
        self.environments = UserEnvironmentResolver(config)
        self._drafts: dict[int, InitDraft] = {}

    def has_pending(self, user_id: int) -> bool:
        return user_id in self._drafts

    def is_waiting_for_preview_or_confirmation(self, user_id: int) -> bool:
        draft = self._drafts.get(int(user_id))
        return bool(draft and draft.step >= len(INIT_QUESTIONS))

    def start(self, user_id: int) -> str:
        self._drafts[int(user_id)] = InitDraft()
        return self.current_question(user_id)

    def reset(self, user_id: int) -> None:
        normalized_user_id = int(user_id)
        self._drafts.pop(normalized_user_id, None)
        environment = self.environments.for_user(normalized_user_id)
        environment.profile_path.unlink(missing_ok=True)

    def current_question(self, user_id: int) -> str:
        draft = self._drafts.get(int(user_id))
        if draft is None:
            return INIT_QUESTIONS[0][1]
        safe_step = min(max(draft.step, 0), len(INIT_QUESTIONS) - 1)
        return INIT_QUESTIONS[safe_step][1]

    def current_question_number(self, user_id: int) -> int:
        draft = self._drafts.get(int(user_id))
        if draft is None:
            return 1
        return min(draft.step + 1, len(INIT_QUESTIONS))

    def answer(self, user_id: int, answer: str) -> InitAnswerResult:
        normalized_user_id = int(user_id)
        draft = self._drafts.setdefault(normalized_user_id, InitDraft())
        key, _question = INIT_QUESTIONS[draft.step]
        draft.answers[key] = answer.strip()
        draft.step += 1

        if draft.step < len(INIT_QUESTIONS):
            return InitAnswerResult(
                complete=False,
                next_question=INIT_QUESTIONS[draft.step][1],
            )

        profile = self._build_profile(normalized_user_id, draft.answers)
        self.save_profile(normalized_user_id, profile)
        return InitAnswerResult(complete=True, profile=profile)

    def save_profile(self, user_id: int, profile: dict[str, Any]) -> Path:
        environment = self.environments.for_user(user_id)
        _write_json(environment.profile_path, profile)
        return environment.profile_path

    def save_preview(self, user_id: int, markdown: str) -> Path:
        normalized_user_id = int(user_id)
        draft = self._drafts.setdefault(normalized_user_id, InitDraft())
        draft.preview_markdown = markdown
        environment = self.environments.for_user(normalized_user_id)
        profile = self.load_profile(normalized_user_id) or self._build_profile(
            normalized_user_id,
            draft.answers,
        )
        profile["status"] = "pending_confirmation"
        profile["gemini_md_preview"] = markdown
        profile["updated_at"] = _now()
        _write_json(environment.profile_path, profile)
        return environment.profile_path

    def confirm_preview(self, user_id: int) -> Path:
        normalized_user_id = int(user_id)
        draft = self._drafts.get(normalized_user_id)
        markdown = draft.preview_markdown if draft else ""
        profile = self.load_profile(normalized_user_id) or {}
        if not markdown:
            markdown = str(profile.get("gemini_md_preview") or "").strip()
        if not markdown:
            raise RuntimeError("Нет готового preview для записи GEMINI.md.")
        markdown = sanitize_gemini_md(markdown)

        environment = self.environments.for_user(normalized_user_id)
        environment.gemini_md_path.write_text(
            markdown.rstrip() + "\n", encoding="utf-8"
        )
        profile["status"] = "active"
        profile["gemini_md_path"] = str(environment.gemini_md_path)
        profile["activated_at"] = _now()
        profile.pop("gemini_md_preview", None)
        _write_json(environment.profile_path, profile)
        self._drafts.pop(normalized_user_id, None)
        return environment.gemini_md_path

    def cancel_preview(self, user_id: int) -> None:
        normalized_user_id = int(user_id)
        draft = self._drafts.get(normalized_user_id)
        if draft:
            draft.preview_markdown = ""
        profile = self.load_profile(normalized_user_id)
        if profile:
            profile["status"] = "cancelled"
            profile.pop("gemini_md_preview", None)
            profile["updated_at"] = _now()
            self.save_profile(normalized_user_id, profile)
        self._drafts.pop(normalized_user_id, None)

    def load_profile(self, user_id: int) -> dict[str, Any] | None:
        environment = self.environments.for_user(user_id)
        if not environment.profile_path.exists():
            return None
        try:
            raw = json.loads(environment.profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _build_profile(self, user_id: int, answers: dict[str, str]) -> dict[str, Any]:
        return {
            "version": 2,
            "telegram_user_id": int(user_id),
            "status": "answers_collected",
            "answers": dict(answers),
            "created_at": _now(),
            "updated_at": _now(),
        }


def build_gemini_md_prompt(profile: dict[str, Any]) -> str:
    answers = profile.get("answers") if isinstance(profile.get("answers"), dict) else {}
    payload = json.dumps(answers, ensure_ascii=False, indent=2)
    return (
        "Ты создаёшь содержимое файла GEMINI.md для Gemini CLI 0.39.1.\n"
        "Используй только ответы анкеты ниже. Не выдумывай факты, биографию, "
        "интересы, профессии, доступы, окружение или будущие действия.\n"
        "Верни только готовый Markdown без code fence, вступлений и пояснений.\n"
        "Строгий формат:\n"
        "# Личные инструкции\n"
        "- 5-8 коротких инструкций в виде Markdown bullet list.\n"
        "- Каждая инструкция должна быть практичной и проверяемой.\n"
        "- Не упоминай Telegram, gateway, сервер, Ubuntu, workspace, семью, "
        "skills, tools, MCP, расширения, systemd или внутреннюю реализацию.\n"
        "- Не заявляй, что ты активируешь навыки, инструменты или поиск.\n"
        "- Если ответ анкеты пустой или неопределённый, запиши нейтральное правило: "
        "уточнять детали перед действием.\n\n"
        f"Ответы анкеты:\n{payload}\n"
    )


def sanitize_gemini_md(markdown: str) -> str:
    text = markdown.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    _validate_gemini_md(text)
    return text


def _validate_gemini_md(markdown: str) -> None:
    text = markdown.strip()
    _validate_non_empty_gemini_md(text)
    _validate_gemini_md_header(text)
    _validate_gemini_md_bullets(text)
    _validate_gemini_md_terms(text)


def _validate_non_empty_gemini_md(text: str) -> None:
    if not text:
        raise ValueError("Gemini вернул пустой preview GEMINI.md.")
    if "```" in text:
        raise ValueError("Gemini вернул preview с code fence.")


def _validate_gemini_md_header(text: str) -> None:
    if not text.startswith("# Личные инструкции"):
        raise ValueError("Gemini вернул preview не в формате '# Личные инструкции'.")


def _validate_gemini_md_bullets(text: str) -> None:
    bullet_count = _count_markdown_bullets(text)
    if bullet_count < 5 or bullet_count > 8:
        raise ValueError(
            "Gemini вернул preview не в формате 5-8 коротких Markdown bullets."
        )


def _validate_gemini_md_terms(text: str) -> None:
    lowered = text.lower()
    forbidden = [term for term in _FORBIDDEN_GEMINI_MD_TERMS if term in lowered]
    if forbidden:
        raise ValueError(
            "Gemini вернул preview с лишним системным или выдуманным контекстом: "
            + ", ".join(sorted(set(forbidden))[:5])
        )


def _count_markdown_bullets(text: str) -> int:
    return sum(
        1 for line in text.splitlines() if line.lstrip().startswith(("- ", "* "))
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
