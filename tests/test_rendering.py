from gateway.gemini.parser import StreamEvent
from gateway.gemini.renderer import render_event


def test_render_event_hides_tool_details_in_compact_mode() -> None:
    event = StreamEvent(
        event_type="tool_use",
        tool_name="write_file",
        tool_args_preview='{"path":"draft.md"}',
    )

    assert render_event(event, "compact") == ""


def test_render_event_shows_summary_for_tool_use() -> None:
    event = StreamEvent(
        event_type="tool_use",
        tool_name="write_file",
        tool_args_preview='{"path":"draft.md"}',
    )

    rendered = render_event(event, "summary")

    assert "Инструмент" in rendered
    assert "write_file" in rendered
    assert "Параметры" not in rendered


def test_render_event_drops_unknown_tool_name() -> None:
    event = StreamEvent(
        event_type="tool_use",
        tool_name="unknown",
        tool_args_preview="{}",
    )

    rendered = render_event(event, "summary")

    assert "unknown" not in rendered
    assert "внутренний шаг" in rendered
