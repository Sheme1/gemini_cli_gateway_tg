from pathlib import Path
import shutil
import uuid

import pytest

from gateway.config import Config
from gateway.init_wizard import (
    INIT_QUESTIONS,
    InitWizardStore,
    build_gemini_md_prompt,
    sanitize_gemini_md,
)


def _config(tmp_path: Path) -> Config:
    return Config(
        telegram_bot_token="token",
        gemini_working_dir=str(tmp_path / "legacy"),
        gemini_artifact_roots=(str(tmp_path / "legacy"),),
        gateway_state_dir=str(tmp_path / "state"),
        gateway_experimental_multi_user_workspaces=True,
        gateway_user_workspaces_dir=str(tmp_path / "users"),
    )


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"init-wizard-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_init_wizard_collects_answers_and_confirms_gemini_md() -> None:
    tmp_path = make_test_dir()
    try:
        store = InitWizardStore(_config(tmp_path))

        first_question = store.start(42)
        assert first_question == INIT_QUESTIONS[0][1]

        result = None
        for index, (_key, _question) in enumerate(INIT_QUESTIONS, start=1):
            result = store.answer(42, f"answer {index}")

        assert result is not None
        assert result.complete is True
        assert result.profile is not None
        assert result.profile["version"] == 2
        assert result.profile["answers"]["name"] == "answer 1"

        markdown = (
            "# Личные инструкции\n\n"
            "- Обращайся по имени из анкеты.\n"
            "- Отвечай на выбранном языке и в указанном тоне.\n"
            "- Помогай с типичными задачами из анкеты.\n"
            "- Соблюдай желаемый формат ответа.\n"
            "- Уточняй детали, если данных недостаточно."
        )
        profile_path = store.save_preview(42, markdown)
        gemini_md_path = store.confirm_preview(42)

        assert profile_path.exists()
        assert gemini_md_path == (
            tmp_path / "users" / "tg-user-42" / "workspace" / "GEMINI.md"
        )
        assert gemini_md_path.read_text(encoding="utf-8") == markdown + "\n"
        assert not store.has_pending(42)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_init_wizard_questions_are_short_and_neutral() -> None:
    assert len(INIT_QUESTIONS) == 5
    questions = "\n".join(question for _key, question in INIT_QUESTIONS).lower()
    assert "пример:" in questions
    assert "семь" not in questions
    assert "family" not in questions


def test_init_wizard_prompt_and_sanitize_helpers() -> None:
    profile = {"answers": {"name": "Alex", "language_tone": "русский"}}

    prompt = build_gemini_md_prompt(profile)
    markdown = sanitize_gemini_md(
        "```markdown\n"
        "# Личные инструкции\n\n"
        "- Обращайся ко мне как Alex.\n"
        "- Отвечай на русском языке.\n"
        "- Сначала давай короткий ответ.\n"
        "- Потом перечисляй шаги, если они нужны.\n"
        "- Уточняй детали при нехватке данных.\n"
        "```"
    )

    assert "GEMINI.md" in prompt
    assert "Не выдумывай факты" in prompt
    assert "Не заявляй, что ты активируешь" in prompt
    assert "Alex" in prompt
    assert markdown.startswith("# Личные инструкции")


def test_init_wizard_rejects_bad_gemini_md_preview() -> None:
    with pytest.raises(ValueError, match="пустой"):
        sanitize_gemini_md("")
    with pytest.raises(ValueError, match="5-8"):
        sanitize_gemini_md("# Личные инструкции\n\n- Too short")
    with pytest.raises(ValueError, match="лишним"):
        sanitize_gemini_md(
            "# Личные инструкции\n\n"
            "- Не упоминай Telegram.\n"
            "- Отвечай коротко.\n"
            "- Уточняй детали.\n"
            "- Не выдумывай факты.\n"
            "- Следуй формату."
        )
