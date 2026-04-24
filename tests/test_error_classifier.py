from gateway.gemini.error_classifier import classify_gemini_error


def test_error_classifier_detects_auth_failure() -> None:
    hint = classify_gemini_error("auth failed: not authenticated")

    assert hint.code == "auth"
    assert "авторизован" in hint.title
    assert "gemini" in hint.fix


def test_error_classifier_detects_invalid_model() -> None:
    hint = classify_gemini_error("Model gemini-x is invalid or not supported")

    assert hint.code == "invalid_model"
    assert "Модель" in hint.title
