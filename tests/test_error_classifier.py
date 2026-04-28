import pytest

from gateway.gemini.error_classifier import classify_gemini_error


@pytest.mark.parametrize(
    ("text", "returncode", "expected_code", "title_part", "fix_part"),
    [
        (
            "stderr details",
            42,
            "input_error",
            "входные параметры",
            "session_id",
        ),
        (
            "stderr details",
            53,
            "turn_limit",
            "лимит ходов",
            "/sessions",
        ),
        (
            "spawn ENOENT",
            None,
            "missing_binary",
            "не найден",
            "GEMINI_BIN",
        ),
        (
            "FatalUntrustedWorkspaceError: untrusted workspace",
            None,
            "untrusted_workspace",
            "рабочей папке",
            "GEMINI_SKIP_TRUST",
        ),
        (
            "auth failed: not authenticated",
            None,
            "auth",
            "авторизован",
            "авторизацию",
        ),
        (
            "policy denied by admin policy",
            None,
            "policy_denied",
            "policy rules",
            "approval mode",
        ),
        (
            "Resource exhausted 429 rate limit",
            None,
            "quota",
            "временно отклонил",
            "лимиты аккаунта",
        ),
        (
            "model overloaded capacity fallback model",
            None,
            "model_capacity",
            "временно недоступна",
            "auto/flash",
        ),
        (
            "Model gemini-x is invalid or not supported",
            None,
            "invalid_model",
            "Модель",
            "/model",
        ),
        (
            "Gemini timed out and did not answer",
            None,
            "timeout",
            "слишком долго",
            "GEMINI_CLI_TIMEOUT",
        ),
        (
            "mcp tool failed with error",
            None,
            "tool_failure",
            "инструмента",
            "/diagnostics",
        ),
    ],
)
def test_error_classifier_detects_known_errors(
    text: str,
    returncode: int | None,
    expected_code: str,
    title_part: str,
    fix_part: str,
) -> None:
    hint = classify_gemini_error(text, returncode=returncode)

    assert hint.code == expected_code
    assert title_part in hint.title
    assert fix_part in hint.fix


def test_error_classifier_returns_unknown_without_returncode() -> None:
    hint = classify_gemini_error("")

    assert hint.code == "unknown"
    assert hint.technical == "нет stderr/stdout деталей"


def test_error_classifier_returns_exit_code_for_unknown_returncode() -> None:
    hint = classify_gemini_error("unexpected failure", returncode=7)

    assert hint.code == "exit_7"
    assert hint.technical == "unexpected failure"


def test_error_classifier_preserves_returncode_priority() -> None:
    hint = classify_gemini_error("quota and policy denied", returncode=42)

    assert hint.code == "input_error"
