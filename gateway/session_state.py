from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import threading
from typing import Any


@dataclass(frozen=True)
class ActiveSessionRecord:
    user_id: int
    active_session_id: str
    workspace: str
    source: str
    updated_at: str


class SessionStateStore:
    """Persistent active Gemini session ids per Telegram user."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def get(self, user_id: int, *, workspace: str) -> ActiveSessionRecord | None:
        with self._lock:
            raw = self._data.get(str(user_id))
            if not isinstance(raw, dict):
                return None
            if raw.get("cleared") is True:
                return None
            if raw.get("workspace") != workspace:
                return None
            session_id = raw.get("active_session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                return None
            return ActiveSessionRecord(
                user_id=int(user_id),
                active_session_id=session_id.strip(),
                workspace=workspace,
                source=_safe_str(raw.get("source"), default="persisted"),
                updated_at=_safe_str(raw.get("updated_at"), default=""),
            )

    def set(
        self,
        user_id: int,
        *,
        active_session_id: str,
        workspace: str,
        source: str,
    ) -> None:
        session_id = active_session_id.strip()
        if not session_id:
            self.clear(user_id)
            return

        with self._lock:
            self._data[str(user_id)] = {
                "active_session_id": session_id,
                "workspace": workspace,
                "source": source,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._write_locked()

    def mark_cleared(self, user_id: int, *, workspace: str, source: str) -> None:
        with self._lock:
            self._data[str(user_id)] = {
                "active_session_id": "",
                "workspace": workspace,
                "source": source,
                "cleared": True,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._write_locked()

    def is_cleared(self, user_id: int, *, workspace: str) -> bool:
        with self._lock:
            raw = self._data.get(str(user_id))
            return (
                isinstance(raw, dict)
                and raw.get("workspace") == workspace
                and raw.get("cleared") is True
            )

    def clear(self, user_id: int) -> bool:
        with self._lock:
            removed = self._data.pop(str(user_id), None) is not None
            if removed:
                self._write_locked()
            return removed

    def clear_matching_session(
        self,
        session_id: str,
        *,
        user_id: int | None = None,
    ) -> list[int]:
        target = session_id.strip()
        if not target:
            return []

        with self._lock:
            removed: list[int] = []
            for raw_user_id, raw in list(self._data.items()):
                if user_id is not None and raw_user_id != str(user_id):
                    continue
                if not isinstance(raw, dict):
                    continue
                if raw.get("active_session_id") != target:
                    continue
                self._data.pop(raw_user_id, None)
                removed.append(int(raw_user_id))
            if removed:
                self._write_locked()
            return removed

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(users, dict):
            return {}

        return {
            str(user_id): value
            for user_id, value in users.items()
            if isinstance(value, dict)
        }

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        payload = {
            "version": 1,
            "users": self._data,
        }
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def _safe_str(value: object, *, default: str) -> str:
    if isinstance(value, str):
        return value
    return default
