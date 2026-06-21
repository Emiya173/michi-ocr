"""Glue: image bytes -> OCR -> translate -> (TTS, overlay) -> result dict.

This replaces GSM's whole async text pipeline (clipboard / DB / stats / Anki / message bus).
For a one-region lookup tool none of that applies: we OCR, translate, optionally speak, and
optionally pop the overlay — all inline on the HTTP worker thread.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

from michi_ocr import translate, tts
from michi_ocr.config import LOG_DIR, Config
from michi_ocr.ocr import engine

logger = logging.getLogger("michi_ocr.pipeline")


def run_ocr_region(img_bytes: bytes, cfg: Config, play_tts: bool | None = None) -> dict:
    """OCR the image, translate, fire TTS. Returns a JSON-able result dict."""
    text, err = engine.ocr(img_bytes)
    if not text:
        return {"status": "no_text", "text": "", "translation": "", "error": err or ""}

    translation, translation_error = translate.translate(text, cfg)

    should_play = cfg.play_on_ocr if play_tts is None else play_tts
    if cfg.tts_enabled and should_play:
        tts.play(text, cfg)

    return {
        "status": "ok",
        "text": text,
        "translation": translation,
        "translation_error": translation_error,
    }


def spawn_overlay(geometry: str, text: str, translation: str, port: int) -> None:
    """Launch the layer-shell overlay as a subprocess (inherits the nix GTK/GI env)."""
    cmd = [
        sys.executable,
        "-m",
        "michi_ocr.overlay",
        "--geometry",
        geometry,
        "--port",
        str(port),
        "--text",
        text,
        "--translation",
        translation,
    ]
    # The child needs the same sys.path (nix PyGObject + this package) to `import gi`.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "overlay.log"
    logger.info(f"Spawning overlay (geometry={geometry!r}); stderr -> {log_path}")
    try:
        logf = open(log_path, "ab")  # append, not truncate — keep prior spawns' crash output
        logf.write(f"--- spawn @ {time.strftime('%H:%M:%S')}: {' '.join(cmd)}\n".encode())
        logf.flush()
        subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to spawn overlay: {e}")
