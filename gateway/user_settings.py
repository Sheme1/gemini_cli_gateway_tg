from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from gateway.model_presets import DEFAULT_MODEL_PRESET, MODEL_PRESETS, resolve_model

DEFAULT_RENDER_MODE = "compact"
VALID_RENDER_MODES = {"compact", "summary", "detailed"}


class UserSettingsStore:
    """Простое persistent-хранилище пользовательских UI-настроек."""

    def __init__(self, path: Path | None = None, state_dir: Path | None = None):
        base_path = state_dir or (
            Path(__file__).resolve().parents[1] / ".gateway_state"
        )
        self.path = path or (base_path / "user_settings.json")
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    def get_render_mode(self, user_id: int) -> str:
        with self._lock:
            value = self._data.get(str(user_id), {}).get(
                "render_mode", DEFAULT_RENDER_MODE
            )
        return value if value in VALID_RENDER_MODES else DEFAULT_RENDER_MODE

    def set_render_mode(self, user_id: int, render_mode: str) -> str:
        normalized = (
            render_mode if render_mode in VALID_RENDER_MODES else DEFAULT_RENDER_MODE
        )
        with self._lock:
            payload = self._data.setdefault(str(user_id), {})
            payload["render_mode"] = normalized
            self._write_locked()
        return normalized

    def get_model_preset(self, user_id: int) -> str:
        with self._lock:
            value = self._data.get(str(user_id), {}).get(
                "model_preset", DEFAULT_MODEL_PRESET
            )
        if value == DEFAULT_MODEL_PRESET or value in MODEL_PRESETS:
            return value
        return DEFAULT_MODEL_PRESET

    def get_effective_model(self, user_id: int, fallback_model: str) -> str:
        return resolve_model(self.get_model_preset(user_id), fallback_model)

    def set_model_preset(self, user_id: int, model_preset: str) -> str:
        normalized = (
            model_preset
            if model_preset == DEFAULT_MODEL_PRESET or model_preset in MODEL_PRESETS
            else DEFAULT_MODEL_PRESET
        )
        with self._lock:
            payload = self._data.setdefault(str(user_id), {})
            payload["model_preset"] = normalized
            if normalized == DEFAULT_MODEL_PRESET:
                payload.pop("model", None)
            else:
                payload["model"] = MODEL_PRESETS[normalized].model
            self._write_locked()
        return normalized

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(raw, dict):
            return {}

        return {
            str(user_id): value
            for user_id, value in raw.items()
            if isinstance(value, dict)
        }

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
