import json

from gateway.gemini.parser import GeminiStreamParser


def test_parser_extracts_direct_and_inferred_file_candidates() -> None:
    payload = {
        "type": "message",
        "role": "assistant",
        "content": (
            "Файл готов. [SEND_FILE: /tmp/report.docx] "
            "Черновик сохранен как drafts/notes.md"
        ),
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "assistant_text"
    assert "[SEND_FILE:" not in event.assistant_text
    assert event.direct_file_candidates == ["/tmp/report.docx"]
    assert "/tmp/report.docx" in event.file_candidates
    assert "drafts/notes.md" in event.file_candidates


def test_parser_truncates_tool_result_and_detects_paths() -> None:
    payload = {
        "type": "tool_result",
        "output": "Saved to ./build/output/result.pdf\n" + ("x" * 800),
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "tool_result"
    assert event.tool_output_preview.endswith("...")
    assert "./build/output/result.pdf" in event.file_candidates


def test_parser_marks_empty_result() -> None:
    payload = {
        "type": "result",
        "stats": {"total_tokens": 10, "thoughts_tokens": 10, "duration_ms": 1200},
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "result_stats"
    assert event.is_done is True
    assert event.is_empty_response is True
