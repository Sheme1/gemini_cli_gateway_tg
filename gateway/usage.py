from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UsageSnapshot:
    date: str
    user_tokens: int
    global_tokens: int
    user_limit: int
    global_limit: int
    last_request: dict[str, Any] | None

    @property
    def user_limit_reached(self) -> bool:
        return self.user_limit > 0 and self.user_tokens >= self.user_limit

    @property
    def global_limit_reached(self) -> bool:
        return self.global_limit > 0 and self.global_tokens >= self.global_limit


class UsageLedger:
    """Persistent daily token counters without storing prompt text."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "usage.json"
        self._lock = threading.Lock()
        self._data = self._load()

    def snapshot(
        self,
        user_id: int,
        *,
        user_limit: int = 0,
        global_limit: int = 0,
    ) -> UsageSnapshot:
        today = _today()
        with self._lock:
            day = self._day_locked(today)
            users = day.setdefault("users", {})
            user = users.setdefault(str(user_id), {"tokens": 0, "requests": 0})
            return UsageSnapshot(
                date=today,
                user_tokens=int(user.get("tokens", 0) or 0),
                global_tokens=int(day.get("global_tokens", 0) or 0),
                user_limit=max(0, user_limit),
                global_limit=max(0, global_limit),
                last_request=self._data.get("last_request")
                if isinstance(self._data.get("last_request"), dict)
                else None,
            )

    def can_start_request(
        self,
        user_id: int,
        *,
        user_limit: int = 0,
        global_limit: int = 0,
    ) -> tuple[bool, str]:
        snapshot = self.snapshot(
            user_id,
            user_limit=user_limit,
            global_limit=global_limit,
        )
        if snapshot.global_limit_reached:
            return (
                False,
                "Дневной общий лимит токенов уже исчерпан. "
                "Увеличьте GLOBAL_DAILY_TOKEN_LIMIT или дождитесь нового дня.",
            )
        if snapshot.user_limit_reached:
            return (
                False,
                "Ваш дневной лимит токенов уже исчерпан. "
                "Увеличьте USER_DAILY_TOKEN_LIMIT или дождитесь нового дня.",
            )
        return True, ""

    def record_request(
        self,
        user_id: int,
        *,
        model: str,
        total_tokens: int,
        duration_ms: int,
        thoughts_tokens: int = 0,
        result_status: str = "",
        stats: dict[str, Any] | None = None,
    ) -> None:
        tokens = max(0, int(total_tokens or 0))
        today = _today()
        with self._lock:
            day = self._day_locked(today)
            users = day.setdefault("users", {})
            user = users.setdefault(str(user_id), {"tokens": 0, "requests": 0})
            user["tokens"] = int(user.get("tokens", 0) or 0) + tokens
            user["requests"] = int(user.get("requests", 0) or 0) + 1
            day["global_tokens"] = int(day.get("global_tokens", 0) or 0) + tokens
            day["requests"] = int(day.get("requests", 0) or 0) + 1
            self._data["last_request"] = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "user_id": user_id,
                "model": model,
                "total_tokens": tokens,
                "duration_ms": max(0, int(duration_ms or 0)),
                "thoughts_tokens": max(0, int(thoughts_tokens or 0)),
                "result_status": result_status,
            }
            if stats:
                self._data["last_request"]["stats"] = _compact_stats(stats)
            self._write_locked()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"days": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"days": {}}
        if not isinstance(raw, dict):
            return {"days": {}}
        days = raw.get("days")
        if not isinstance(days, dict):
            raw["days"] = {}
        return raw

    def _day_locked(self, date_key: str) -> dict[str, Any]:
        days = self._data.setdefault("days", {})
        day = days.setdefault(
            date_key,
            {
                "global_tokens": 0,
                "requests": 0,
                "users": {},
            },
        )
        if not isinstance(day, dict):
            day = {"global_tokens": 0, "requests": 0, "users": {}}
            days[date_key] = day
        if not isinstance(day.get("users"), dict):
            day["users"] = {}
        return day

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _compact_stats(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:200]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if len(compact) >= 30:
                compact["..."] = "truncated"
                break
            compact[str(key)] = _compact_stats(item, depth=depth + 1)
        return compact
    if isinstance(value, list):
        return [_compact_stats(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
