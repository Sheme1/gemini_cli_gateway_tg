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
    "auto": ModelPreset(
        key="auto",
        label="Auto",
        model="auto",
        description="Рекомендуемый роутинг Gemini CLI: CLI сам выбирает подходящую модель.",
    ),
    "pro": ModelPreset(
        key="pro",
        label="Pro",
        model="pro",
        description="Алиас Gemini CLI для сложных задач и сильного reasoning.",
    ),
    "flash": ModelPreset(
        key="flash",
        label="Flash",
        model="flash",
        description="Алиас Gemini CLI для быстрых повседневных задач.",
    ),
    "flash_lite": ModelPreset(
        key="flash_lite",
        label="Flash Lite",
        model="flash-lite",
        description="Алиас Gemini CLI для самых быстрых простых запросов.",
    ),
    "cheap": ModelPreset(
        key="cheap",
        label="Legacy дешёвый",
        model="gemini-3.1-flash-lite-preview",
        description="Ручная конкретная модель: preview Flash Lite.",
    ),
    "fast": ModelPreset(
        key="fast",
        label="Legacy быстрый",
        model="gemini-2.5-flash",
        description="Ручная конкретная модель: Gemini 2.5 Flash.",
    ),
    "balanced": ModelPreset(
        key="balanced",
        label="Legacy баланс",
        model="gemini-3-flash-preview",
        description="Ручная конкретная модель: Gemini 3 Flash Preview.",
    ),
    "quality": ModelPreset(
        key="quality",
        label="Legacy качество",
        model="gemini-3.1-pro-preview",
        description="Ручная конкретная модель: Gemini 3.1 Pro Preview.",
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
