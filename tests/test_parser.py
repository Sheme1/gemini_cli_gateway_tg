import json
import logging

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


def test_parser_supports_official_tool_use_schema() -> None:
    payload = {
        "type": "tool_use",
        "tool_name": "write_file",
        "tool_id": "tool-1",
        "parameters": {"path": "./out/report.docx"},
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "tool_use"
    assert event.tool_name == "write_file"
    assert event.tool_id == "tool-1"
    assert "./out/report.docx" in event.file_candidates


def test_parser_supports_official_tool_result_error_schema() -> None:
    payload = {
        "type": "tool_result",
        "tool_id": "tool-1",
        "status": "error",
        "error": {"message": "failed to open ./out/report.docx"},
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "tool_result"
    assert event.tool_id == "tool-1"
    assert event.tool_status == "error"
    assert "failed to open" in event.tool_output_preview
    assert "./out/report.docx" in event.file_candidates


def test_parser_tracks_message_delta_flag() -> None:
    payload = {
        "type": "message",
        "role": "assistant",
        "content": "Часть ответа",
        "delta": True,
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "assistant_text"
    assert event.message_delta is True


def test_parser_preserves_message_delta_whitespace() -> None:
    chunks = ["Я обновляю", " структуру", " ваших", " "]

    rendered = "".join(
        GeminiStreamParser.parse_line(
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": chunk,
                    "delta": True,
                },
                ensure_ascii=False,
            )
        ).assistant_text
        for chunk in chunks
    )

    assert rendered == "Я обновляю структуру ваших "


def test_parser_skips_structured_thought_messages() -> None:
    payload = {
        "type": "message",
        "role": "assistant",
        "content": "internal reasoning",
        "thought": True,
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload))

    assert event.event_type == ""


def test_parser_marks_empty_result() -> None:
    payload = {
        "status": "success",
        "type": "result",
        "stats": {"total_tokens": 10, "thoughts_tokens": 10, "duration_ms": 1200},
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "result_stats"
    assert event.is_done is True
    assert event.is_empty_response is True
    assert event.result_status == "success"
    assert event.stats["total_tokens"] == 10


def test_parser_sums_nested_model_stats() -> None:
    payload = {
        "status": "success",
        "type": "result",
        "stats": {
            "models": {
                "gemini-3-pro": {
                    "total_tokens": 12,
                    "thoughts_tokens": 3,
                },
                "gemini-3-flash": {
                    "totalTokens": 8,
                    "thoughtsTokens": 2,
                },
            }
        },
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.total_tokens == 20
    assert event.thoughts_tokens == 5


def test_parser_preserves_error_code_and_exit_code() -> None:
    payload = {
        "type": "error",
        "error": {"code": "TURN_LIMIT", "message": "Turn limit exceeded"},
        "exitCode": 53,
    }

    event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == "error"
    assert event.error_code == "TURN_LIMIT"
    assert event.exit_code == 53
    assert "Turn limit" in event.error_message


def test_parser_logs_unknown_event_payload_in_debug(caplog) -> None:
    payload = {"type": "mystery_event", "foo": "bar"}

    with caplog.at_level(logging.DEBUG, logger="gateway.gemini.parser"):
        event = GeminiStreamParser.parse_line(json.dumps(payload, ensure_ascii=False))

    assert event.event_type == ""
    assert "UNKNOWN EVENT" in caplog.text
    assert "mystery_event" in caplog.text
