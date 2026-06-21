"""Tiny config: one dataclass, loaded from ~/.config/michi-ocr/config.toml + env overrides.

Deliberately not GSM's config system — no profiles, no MasterConfig, no ``save_full_config``
rewriting the file behind your back. What you put in the TOML stays put.
"""

# NOTE: intentionally NOT using `from __future__ import annotations` — load() reflects over
# `dataclasses.fields(Config).type`, which must be real types (int/bool/...), not strings.

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(os.environ.get("MICHI_OCR_CONFIG_DIR") or (Path.home() / ".config" / "michi-ocr"))
CONFIG_PATH = CONFIG_DIR / "config.toml"
LOG_DIR = CONFIG_DIR / "logs"


@dataclass
class Config:
    # HTTP daemon
    port: int = 55000

    # Translation (DeepL)
    deepl_api_key: str = ""
    deepl_target_lang: str = "ZH"
    deepl_api_type: str = "auto"  # auto (detect ':fx') | free | pro

    # TTS (VoiceVox)
    tts_enabled: bool = True
    play_on_ocr: bool = True
    voicevox_url: str = "http://127.0.0.1:50021"
    speaker_id: int = 2
    speed_scale: float = 1.0
    voicevox_timeout: float = 30.0

    def deepl_configured(self) -> bool:
        return bool(self.deepl_api_key.strip())


_BOOL_TRUE = {"1", "true", "yes", "on"}


def _coerce(field_type, value):
    if field_type is bool:
        return str(value).strip().lower() in _BOOL_TRUE if isinstance(value, str) else bool(value)
    if field_type is int:
        return int(value)
    if field_type is float:
        return float(value)
    return str(value)


def load() -> Config:
    """Load config from TOML, then apply MICHI_OCR_<FIELD> env overrides."""
    data: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
    cfg = Config()
    for fld in fields(Config):
        if fld.name in data:
            setattr(cfg, fld.name, _coerce(fld.type, data[fld.name]))
        env = os.environ.get(f"MICHI_OCR_{fld.name.upper()}")
        if env is not None:
            setattr(cfg, fld.name, _coerce(fld.type, env))
    return cfg


_cached: Optional[Config] = None


def get() -> Config:
    global _cached
    if _cached is None:
        _cached = load()
    return _cached
