"""VoiceVox TTS: synthesize a Japanese line and play it via an external player subprocess.

Playback is **never** done in-process. sounddevice/PortAudio aborts the whole interpreter
(SIGABRT in PaUnixThread_Terminate / SIGSEGV in PipeWire) when overlapping play() calls race
on its single global stream — and speaking every OCR line makes that race constant. A child
player crashing can't take us down; the lock serializes lines so they don't talk over each
other. (This is the crash that plagued the GSM fork; the standalone tool ships without it.)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading

import requests

from michi_ocr.config import Config

logger = logging.getLogger("michi_ocr.tts")

_play_lock = threading.Lock()
_player_cmd: list[str] | None = None
_player_resolved = False


def _resolve_player() -> list[str] | None:
    """Pick an external audio player once. Returns argv prefix (file path appended)."""
    global _player_cmd, _player_resolved
    if _player_resolved:
        return _player_cmd
    for exe, argv in (
        ("mpv", ["mpv", "--no-video", "--no-terminal", "--really-quiet", "--audio-display=no"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ("paplay", ["paplay"]),
        ("aplay", ["aplay", "-q"]),
    ):
        if shutil.which(exe):
            _player_cmd = argv
            break
    else:
        _player_cmd = None
        logger.warning("No audio player found (mpv/ffplay/paplay/aplay); TTS will be silent.")
    _player_resolved = True
    return _player_cmd


def synthesize(text: str, cfg: Config, speaker_id: int | None = None) -> bytes | None:
    """Render `text` to WAV bytes via the VoiceVox engine. None on failure/disabled."""
    if not (cfg.tts_enabled and text and text.strip()):
        return None
    speaker = cfg.speaker_id if speaker_id is None else speaker_id
    base = cfg.voicevox_url.rstrip("/")
    try:
        q = requests.post(
            f"{base}/audio_query", params={"text": text, "speaker": speaker}, timeout=cfg.voicevox_timeout
        )
        q.raise_for_status()
        audio_query = q.json()
        if cfg.speed_scale != 1.0:
            audio_query["speedScale"] = cfg.speed_scale
        synth = requests.post(
            f"{base}/synthesis", params={"speaker": speaker}, json=audio_query, timeout=cfg.voicevox_timeout
        )
        synth.raise_for_status()
        return synth.content
    except requests.RequestException as e:
        logger.error(f"VoiceVox synthesis failed: {e}")
        return None


def play(text: str, cfg: Config, speaker_id: int | None = None) -> None:
    """Synthesize and play `text` without blocking the caller (used by play_on_ocr)."""

    def _worker():
        audio = synthesize(text, cfg, speaker_id=speaker_id)
        if not audio:
            return
        player = _resolve_player()
        if not player:
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio)
            path = f.name
        with _play_lock:  # one line finishes before the next starts (no overlap, no 2 streams)
            try:
                subprocess.run([*player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            except Exception as e:  # noqa: BLE001
                logger.error(f"TTS playback failed: {e}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    threading.Thread(target=_worker, daemon=True).start()
