"""Surya OCR wrapper — local torch multilingual line OCR, used as the offline fallback.

Surya reads multi-line game/VN text accurately (unlike single-bubble models). It needs the
``[local]`` extra (``surya-ocr==0.15.4`` + torch). On AMD/ROCm the predictors pick the GPU
automatically. Pinned to 0.15.x: 0.16+ ("Surya2") only serves via vLLM/llama.cpp.
"""

from __future__ import annotations

import logging
import os
import re
import threading

from michi_ocr.ocr.lens import input_to_pil_image

logger = logging.getLogger("michi_ocr.surya")

# Only ever one Surya inference at a time — two concurrent GPU inferences deadlock the AMD
# GPU. Non-blocking: if busy, the call bails so the caller uses Lens instead of piling up.
_surya_lock = threading.Lock()
_engine = None
_LEADING_BAR_RE = re.compile(r"^[|｜]\s*")


class SuryaEngine:
    name = "surya"
    available = True

    def __init__(self, rec, det):
        self._rec = rec
        self._det = det

    def __call__(self, img, timeout: float = 0.0):
        pil, is_path = input_to_pil_image(img)
        if not pil:
            return (False, "Invalid image provided")
        if not _surya_lock.acquire(blocking=False):
            return (False, "surya busy")
        try:
            result = self._rec([pil], det_predictor=self._det, sort_lines=True)
        finally:
            _surya_lock.release()
            if is_path:
                pil.close()
        lines = []
        for tl in result[0].text_lines:
            t = _LEADING_BAR_RE.sub("", (tl.text or "")).strip()
            if t:
                lines.append(t)
        return (True, "\n".join(lines))


def get_surya_engine():
    """Lazily build the Surya predictors once. None if surya isn't installed."""
    global _engine
    if _engine is not None:
        return _engine
    try:
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Surya not installed ({e}); offline OCR fallback unavailable.")
        return None
    try:
        kwargs = {"device": "cpu"} if os.environ.get("MICHI_OCR_FORCE_CPU") == "1" else {}
        foundation = FoundationPredictor(**kwargs)
        _engine = SuryaEngine(RecognitionPredictor(foundation), DetectionPredictor(**kwargs))
        logger.info("Surya OCR ready.")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Surya failed to load ({e}); offline OCR fallback unavailable.")
        return None
    return _engine
