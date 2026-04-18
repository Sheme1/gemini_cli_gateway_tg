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

        if raw_type == "init":
            session_id = data.get("session_id")
            logger.info(
                "Gemini session init: id=%s, model=%s",
                session_id,
                data.get("model"),
            )
            return StreamEvent(event_type="init", session_id=session_id)

        if raw_type == "message":
            role = data.get("role", "")
            content = _stringify_payload(data.get("content")).strip()
            if role != "assistant" or not content:
                return StreamEvent()

            direct_candidates = [
                _normalize_path_candidate(match)
                for match in _SEND_FILE_RE.findall(content)
            ]
            content = _SEND_FILE_RE.sub("", content).strip()
            inferred_candidates = _extract_file_candidates(content)

            return StreamEvent(
                event_type="assistant_text",
                assistant_text=content,
                message_delta=bool(data.get("delta")),
                file_candidates=_dedupe([*direct_candidates, *inferred_candidates]),
                direct_file_candidates=_dedupe(direct_candidates),
            )

        if raw_type == "tool_use":
            tool_name = _stringify_payload(
                data.get("tool_name") or data.get("name")
            ).strip()
            tool_id = _stringify_payload(data.get("tool_id")).strip()
            args_preview = _stringify_payload(
                data.get("parameters", data.get("args"))
            )
            if len(args_preview) > 400:
                args_preview = args_preview[:400].rstrip() + "..."
            return StreamEvent(
                event_type="tool_use",
                tool_id=tool_id,
                tool_name=tool_name,
                tool_args_preview=args_preview,
                file_candidates=_extract_file_candidates(args_preview),
            )

        if raw_type == "tool_result":
            tool_id = _stringify_payload(data.get("tool_id")).strip()
            tool_name = _stringify_payload(
                data.get("tool_name") or data.get("name")
            ).strip()
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

        if raw_type in {"approval_request", "confirmation_request"}:
            request_payload = data if isinstance(data, dict) else None
            return StreamEvent(
                event_type="approval_request",
                approval_request=request_payload,
            )

        if raw_type == "result":
            stats = data.get("stats", {}) if isinstance(data.get("stats"), dict) else {}
            total_tokens = int(stats.get("total_tokens", 0) or 0)
            duration_ms = int(stats.get("duration_ms", 0) or 0)
            thoughts_tokens = int(stats.get("thoughts_tokens", 0) or 0)
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
                is_done=True,
                is_empty_response=is_empty,
            )

        if raw_type == "error":
            return StreamEvent(
                event_type="error",
                error_message=_stringify_payload(
                    data.get("message", "Неизвестная ошибка")
                ),
            )

        if raw_type == "invalid_stream":
            reason = _stringify_payload(data.get("reason", "UNKNOWN"))
            logger.warning("InvalidStream event: reason=%s", reason)
            return StreamEvent(
                event_type="invalid_stream",
                invalid_stream_reason=reason,
            )

        logger.debug(
            "[UNKNOWN EVENT] type=%s payload=%s",
            raw_type or "<empty>",
            _preview(_stringify_payload(data), limit=400),
        )
        return StreamEvent()
