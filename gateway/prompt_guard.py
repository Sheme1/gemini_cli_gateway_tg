from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingPrompt:
    token: str
    user_id: int
    chat_id: int
    prompt: str
    expires_at: float


class PendingPromptStore:
    """In-memory confirmation store for oversized prompts."""

    def __init__(self) -> None:
        self._items: dict[str, PendingPrompt] = {}

    def put(
        self,
        *,
        user_id: int,
        chat_id: int,
        prompt: str,
        ttl_seconds: int,
    ) -> PendingPrompt:
        self.cleanup()
        token = uuid.uuid4().hex[:16]
        item = PendingPrompt(
            token=token,
            user_id=user_id,
            chat_id=chat_id,
            prompt=prompt,
            expires_at=time.time() + max(1, ttl_seconds),
        )
        self._items[token] = item
        return item

    def pop(self, token: str) -> PendingPrompt | None:
        self.cleanup()
        return self._items.pop(token, None)

    def get(self, token: str) -> PendingPrompt | None:
        self.cleanup()
        return self._items.get(token)

    def discard(self, token: str) -> bool:
        self.cleanup()
        return self._items.pop(token, None) is not None

    def cleanup(self) -> None:
        now = time.time()
        expired = [
            token for token, item in self._items.items() if item.expires_at <= now
        ]
        for token in expired:
            self._items.pop(token, None)
