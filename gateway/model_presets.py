from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MODEL_PRESET = "env"


@dataclass(frozen=True)
class ModelPreset:
    key: str
    label: str
    model: str
    description: str


MODEL_PRESETS: dict[str, ModelPreset] = {
    "cheap": ModelPreset(
        key="cheap",
        label="Cheap",
        model="gemini-3.1-flash-lite-preview",
        description="Минимальная стоимость и быстрые короткие ответы.",
    ),
    "fast": ModelPreset(
        key="fast",
        label="Fast",
        model="gemini-2.5-flash",
        description="Быстрые повседневные запросы.",
    ),
    "balanced": ModelPreset(
        key="balanced",
        label="Balanced",
        model="gemini-3-flash-preview",
        description="Баланс скорости и качества для основной работы.",
    ),
    "quality": ModelPreset(
        key="quality",
        label="Quality",
        model="gemini-3.1-pro-preview",
        description="Более сильная модель для сложных задач.",
    ),
}


def resolve_model(preset: str, fallback_model: str) -> str:
    model_preset = MODEL_PRESETS.get(preset)
    if model_preset is None:
        return fallback_model
    return model_preset.model


def get_model_preset_label(preset: str) -> str:
    if preset == DEFAULT_MODEL_PRESET:
        return "Из .env"
    model_preset = MODEL_PRESETS.get(preset)
    return model_preset.label if model_preset else preset
