from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SEND_FILE_RE = re.compile(r"\[SEND_FILE:\s*(.+?)\]")
_FILE_TOKEN_RE = re.compile(
    r"(?P<path>"
    r"(?:~|/|[A-Za-z]:[\\/]|\.{1,2}[\\/])?[^\s<>'\"`]+"
    r"(?:[\\/][^\s<>'\"`]+)*"
    r"\.[A-Za-z0-9]{1,10}"
    r")"
)
_LIKELY_ARTIFACT_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".svg",
    ".tar",
    ".tgz",
    ".tsv",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
_THOUGHT_FLAG_KEYS = {
    "thought",
    "is_thought",
    "isThought",
    "thinking",
    "is_thinking",
    "isThinking",
}


@dataclass
class StreamEvent:
    event_type: str = ""
    assistant_text: str = ""
    message_delta: bool = False
    tool_id: str = ""
    tool_name: str = ""
    tool_status: str = ""
    tool_args_preview: str = ""
    tool_output_preview: str = ""
    approval_request: Optional[dict[str, Any]] = None
    file_candidates: list[str] = field(default_factory=list)
    direct_file_candidates: list[str] = field(default_factory=list)
    session_id: Optional[str] = None
    total_tokens: int = 0
    duration_ms: int = 0
    thoughts_tokens: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    result_status: str = ""
    error_code: str = ""
    exit_code: int | None = None
    error_message: str = ""
    invalid_stream_reason: str = ""
    status_message: str = ""
    warning_message: str = ""
    is_done: bool = False
    is_empty_response: bool = False


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _stringify_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _preview(text: str, limit: int = 280) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _int_from_payload(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _extract_total_tokens(stats: dict[str, Any]) -> int:
    total = _int_from_payload(
        stats.get("total_tokens"),
        stats.get("totalTokens"),
        stats.get("total_token_count"),
        stats.get("totalTokenCount"),
    )
    if total:
        return total

    for key in ("models", "per_model", "perModel", "model_usage", "modelUsage"):
        value = stats.get(key)
        if isinstance(value, list):
            summed = sum(
                _extract_total_tokens(item) for item in value if isinstance(item, dict)
            )
            if summed:
                return summed
        if isinstance(value, dict):
            summed = sum(
                _extract_total_tokens(item)
                for item in value.values()
                if isinstance(item, dict)
            )
            if summed:
                return summed

    return 0


def _extract_thoughts_tokens(stats: dict[str, Any]) -> int:
    total = _int_from_payload(
        stats.get("thoughts_tokens"),
        stats.get("thoughtsTokens"),
        stats.get("thoughts_token_count"),
        stats.get("thoughtsTokenCount"),
    )
    if total:
        return total

    for key in ("models", "per_model", "perModel", "model_usage", "modelUsage"):
        value = stats.get(key)
        if isinstance(value, list):
            summed = sum(
                _extract_thoughts_tokens(item)
                for item in value
                if isinstance(item, dict)
            )
            if summed:
                return summed
        if isinstance(value, dict):
            summed = sum(
                _extract_thoughts_tokens(item)
                for item in value.values()
                if isinstance(item, dict)
            )
            if summed:
                return summed

    return 0


def _extract_duration_ms(stats: dict[str, Any]) -> int:
    return _int_from_payload(
        stats.get("duration_ms"),
        stats.get("durationMs"),
        stats.get("api_latency_ms"),
        stats.get("apiLatencyMs"),
    )


def _extract_error_message(data: dict[str, Any]) -> str:
    error_value = data.get("error")
    if isinstance(error_value, dict):
        return _stringify_payload(
            error_value.get("message")
            or error_value.get("details")
            or error_value.get("code")
            or error_value
        )
    return _stringify_payload(
        data.get("message") or error_value or "Неизвестная ошибка"
    )


def _extract_error_code(data: dict[str, Any]) -> str:
    error_value = data.get("error")
    if isinstance(error_value, dict):
        return _stringify_payload(error_value.get("code") or error_value.get("status"))
    return _stringify_payload(data.get("code") or data.get("status"))


def _normalize_path_candidate(raw_value: str) -> str:
    candidate = raw_value.strip().strip("\"'`")
    candidate = candidate.rstrip(".,;:)]}>")
    candidate = candidate.lstrip("([{<")
    return candidate.strip()


def _looks_like_artifact(candidate: str) -> bool:
    lowered = candidate.lower()
    if "/" in candidate or "\\" in candidate or candidate.startswith("~"):
        return True
    for ext in _LIKELY_ARTIFACT_EXTENSIONS:
        if lowered.endswith(ext):
            return True
    return False


def _extract_file_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in _FILE_TOKEN_RE.finditer(text):
        candidate = _normalize_path_candidate(match.group("path"))
        if candidate and _looks_like_artifact(candidate):
            candidates.append(candidate)
    return _dedupe(candidates)


def _is_thought_payload(data: dict[str, Any]) -> bool:
    if _stringify_payload(data.get("type")).lower() in {"thought", "thinking"}:
        return True

    for key in _THOUGHT_FLAG_KEYS:
        if data.get(key) is True:
            return True

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        return any(metadata.get(key) is True for key in _THOUGHT_FLAG_KEYS)

    return False


def _parse_init_event(data: dict[str, Any]) -> StreamEvent:
    session_id = data.get("session_id")
    logger.info(
        "Gemini session init: id=%s, model=%s",
        session_id,
        data.get("model"),
    )
    return StreamEvent(event_type="init", session_id=session_id)


def _parse_message_event(data: dict[str, Any]) -> StreamEvent:
    role = data.get("role", "")
    if role != "assistant" or _is_thought_payload(data):
        return StreamEvent()

    content = _stringify_payload(data.get("content"))
    if content == "":
        return StreamEvent()

    direct_candidates = [
        _normalize_path_candidate(match) for match in _SEND_FILE_RE.findall(content)
    ]
    content = _SEND_FILE_RE.sub("", content)
    inferred_candidates = _extract_file_candidates(content)

    return StreamEvent(
        event_type="assistant_text",
        assistant_text=content,
        message_delta=bool(data.get("delta")),
        file_candidates=_dedupe([*direct_candidates, *inferred_candidates]),
        direct_file_candidates=_dedupe(direct_candidates),
    )


def _parse_tool_use_event(data: dict[str, Any]) -> StreamEvent:
    tool_name = _stringify_payload(data.get("tool_name") or data.get("name")).strip()
    tool_id = _stringify_payload(data.get("tool_id")).strip()
    args_preview = _stringify_payload(data.get("parameters", data.get("args")))
    if len(args_preview) > 400:
        args_preview = args_preview[:400].rstrip() + "..."
    return StreamEvent(
        event_type="tool_use",
        tool_id=tool_id,
        tool_name=tool_name,
        tool_args_preview=args_preview,
        file_candidates=_extract_file_candidates(args_preview),
    )


def _parse_tool_result_event(data: dict[str, Any]) -> StreamEvent:
    tool_id = _stringify_payload(data.get("tool_id")).strip()
    tool_name = _stringify_payload(data.get("tool_name") or data.get("name")).strip()
    tool_status = _stringify_payload(data.get("status")).strip().lower()

    output_value = data.get("output")
    error_value = data.get("error")
    output_preview = _stringify_payload(output_value)
    error_preview = _stringify_payload(error_value)
    if not output_preview and error_preview:
        output_preview = error_preview
    if len(output_preview) > 700:
        output_preview = output_preview[:700].rstrip() + "..."
    return StreamEvent(
        event_type="tool_result",
        tool_id=tool_id,
        tool_name=tool_name,
        tool_status=tool_status,
        tool_output_preview=output_preview,
        file_candidates=_dedupe(
            [
                *_extract_file_candidates(_stringify_payload(output_value)),
                *_extract_file_candidates(_stringify_payload(error_value)),
            ]
        ),
    )


def _parse_approval_event(data: dict[str, Any]) -> StreamEvent:
    return StreamEvent(event_type="approval_request", approval_request=data)


def _parse_result_event(data: dict[str, Any]) -> StreamEvent:
    stats = data.get("stats", {}) if isinstance(data.get("stats"), dict) else {}
    total_tokens = _extract_total_tokens(stats)
    duration_ms = _extract_duration_ms(stats)
    thoughts_tokens = _extract_thoughts_tokens(stats)
    result_status = _stringify_payload(data.get("status")).strip().lower()
    is_empty = total_tokens == 0 or (
        thoughts_tokens > 0 and total_tokens == thoughts_tokens
    )

    if is_empty:
        logger.warning(
            "Detected empty response: total=%s, thoughts=%s",
            total_tokens,
            thoughts_tokens,
        )

    return StreamEvent(
        event_type="result_stats",
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        thoughts_tokens=thoughts_tokens,
        stats=stats,
        result_status=result_status,
        is_done=True,
        is_empty_response=is_empty,
    )


def _parse_error_event(data: dict[str, Any]) -> StreamEvent:
    exit_code = data.get("exit_code", data.get("exitCode"))
    return StreamEvent(
        event_type="error",
        error_message=_extract_error_message(data),
        error_code=_extract_error_code(data),
        exit_code=_int_from_payload(exit_code) if exit_code is not None else None,
    )


def _parse_invalid_stream_event(data: dict[str, Any]) -> StreamEvent:
    reason = _stringify_payload(data.get("reason", "UNKNOWN"))
    logger.warning("InvalidStream event: reason=%s", reason)
    return StreamEvent(event_type="invalid_stream", invalid_stream_reason=reason)


_EVENT_PARSERS = {
    "init": _parse_init_event,
    "message": _parse_message_event,
    "tool_use": _parse_tool_use_event,
    "tool_result": _parse_tool_result_event,
    "approval_request": _parse_approval_event,
    "confirmation_request": _parse_approval_event,
    "result": _parse_result_event,
    "error": _parse_error_event,
    "invalid_stream": _parse_invalid_stream_event,
}


class GeminiStreamParser:
    """Парсер для --output-format stream-json Gemini CLI."""

    @staticmethod
    def parse_line(line: str) -> StreamEvent:
        if not line or not line.strip():
            return StreamEvent()

        clean_line = _ANSI_ESCAPE_RE.sub("", line).strip()
        if not clean_line:
            return StreamEvent()

        try:
            data = json.loads(clean_line)
        except json.JSONDecodeError:
            logger.debug("[RAW NON-JSON] %r", clean_line)
            return StreamEvent()

        if not isinstance(data, dict):
            logger.debug("[RAW JSON NON-OBJECT] %r", _preview(_stringify_payload(data)))
            return StreamEvent()

        raw_type = data.get("type", "")
        parser = _EVENT_PARSERS.get(raw_type)
        if parser:
            return parser(data)

        logger.debug(
            "[UNKNOWN EVENT] type=%s payload=%s",
            raw_type or "<empty>",
            _preview(_stringify_payload(data), limit=400),
        )
        return StreamEvent()
