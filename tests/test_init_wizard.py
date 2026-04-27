from pathlib import Path
import shutil
import uuid

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
        assert result.profile["answers"]["name"] == "answer 1"

        profile_path = store.save_preview(42, "# Personal\n\n- Test")
        gemini_md_path = store.confirm_preview(42)

        assert profile_path.exists()
        assert gemini_md_path == (
            tmp_path / "users" / "tg-user-42" / "workspace" / "GEMINI.md"
        )
        assert gemini_md_path.read_text(encoding="utf-8") == "# Personal\n\n- Test\n"
        assert not store.has_pending(42)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_init_wizard_prompt_and_sanitize_helpers() -> None:
    profile = {"answers": {"name": "Alex", "language": "русский"}}

    prompt = build_gemini_md_prompt(profile)
    markdown = sanitize_gemini_md("```markdown\n# Title\n```")

    assert "GEMINI.md" in prompt
    assert "Alex" in prompt
    assert markdown == "# Title"
