from gateway.telegram_formatting import normalize_telegram_text, render_telegram_html


def test_render_telegram_html_escapes_text_and_renders_basic_markdown() -> None:
    rendered = render_telegram_html(
        "# Заголовок\n\n**Важно** <tag> & value\n\n- пункт\n1. шаг\n`code`"
    )

    assert "<b>Заголовок</b>" in rendered.html_text
    assert "<b>Важно</b> &lt;tag&gt; &amp; value" in rendered.html_text
    assert "• пункт" in rendered.html_text
    assert "1. шаг" in rendered.html_text
    assert "<code>code</code>" in rendered.html_text


def test_render_telegram_html_preserves_code_block_without_inline_formatting() -> None:
    rendered = render_telegram_html("```python\nprint('**x** <y>')\n```")

    assert "<pre><code>" in rendered.html_text
    assert "**x** &lt;y&gt;" in rendered.html_text
    assert "<b>x</b>" not in rendered.html_text


def test_normalize_telegram_text_removes_thought_marker_and_splits_lists() -> None:
    normalized = normalize_telegram_text(
        "Текст.[Thought: true]Разделы:1. **Введение**2. **Итоги**"
    )

    assert "[Thought:" not in normalized
    assert "Текст. Разделы:" in normalized
    assert "\n1. **Введение**" in normalized
    assert "\n2. **Итоги**" in normalized


def test_normalize_telegram_text_does_not_break_urls() -> None:
    normalized = normalize_telegram_text(
        "Смотри https://example.com/path?a=1, затем продолжай.Еще текст."
    )

    assert "https://example.com/path?a=1" in normalized
    assert "продолжай. Еще текст." in normalized
