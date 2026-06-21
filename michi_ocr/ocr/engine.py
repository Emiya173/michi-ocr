"""OCR orchestration: lens-first, Surya fallback.

Strategy (the one validated in the GSM fork):
  * Try **Google Lens** (network, ~0.6s, accurate) with a short timeout. On success, return —
    Surya is never loaded or run, so a normal online shot pays nothing for the local model.
  * Only if Lens fails/stalls (offline, proxy down) do we lazily load **Surya** (~15s first
    time) and OCR locally.

Set ``MICHI_OCR_RACE=1`` to run both concurrently and take the faster valid result instead.
Each engine call is bounded by a wall-clock timeout so a stalled engine can't block the next.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading

import jaconv

from michi_ocr.ocr.lens import GoogleLens

logger = logging.getLogger("michi_ocr.ocr")

_LENS_TIMEOUT = float(os.environ.get("MICHI_OCR_LENS_TIMEOUT") or 4.0)
_LOCAL_TIMEOUT = float(os.environ.get("MICHI_OCR_LOCAL_TIMEOUT") or 30.0)

_lens = None
_lens_lock = threading.Lock()


def _get_lens() -> GoogleLens:
    global _lens
    with _lens_lock:
        if _lens is None:
            _lens = GoogleLens()
        return _lens


def post_process(text: str, keep_newlines: bool = True) -> str:
    """Collapse intra-line whitespace and normalize to full-width JP. Keeps line breaks."""
    if not text:
        return text
    text = text.replace('"', "")
    joiner = "\n" if keep_newlines else ""
    text = joiner.join("".join(line.split()) for line in text.splitlines())
    return jaconv.h2z(text, ascii=True, digit=True)


def _run(engine, img) -> tuple[str | None, str | None]:
    """Call one engine; return (text, error). text is None on failure, '' on empty."""
    try:
        result = engine(img)
    except Exception as e:  # noqa: BLE001
        return None, f"{getattr(engine, 'name', '?')} raised: {e}"
    ok, payload = (list(result) + [None, None])[:2]
    if not ok:
        return None, str(payload) if payload else "no result"
    return (payload or ""), None


def _run_timed(engine, img, timeout: float) -> tuple[str | None, str | None]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = executor.submit(_run, engine, img)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return None, f"{getattr(engine, 'name', '?')} timed out after {timeout}s"
    finally:
        executor.shutdown(wait=False)


def _get_surya():
    from michi_ocr.ocr.surya_engine import get_surya_engine

    return get_surya_engine()


def _race(img) -> tuple[str | None, str | None]:
    engines = [_get_lens()]
    surya = _get_surya()
    if surya is not None:
        engines.append(surya)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(engines))
    futures = {executor.submit(_run, e, img): e for e in engines}
    text, err = None, None
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=_LOCAL_TIMEOUT):
            t, e = fut.result()
            if t:
                text = t
                logger.info(f"OCR race: '{getattr(futures[fut], 'name', '?')}' won.")
                break
            err = err or e
    except concurrent.futures.TimeoutError:
        err = err or f"OCR race timed out after {_LOCAL_TIMEOUT}s"
    finally:
        executor.shutdown(wait=False)
    return text, err


def ocr(img) -> tuple[str | None, str | None]:
    """OCR an image (PIL/bytes/path). Returns (text, error); text None on failure/empty."""
    if os.environ.get("MICHI_OCR_RACE") == "1":
        text, err = _race(img)
        return (post_process(text), None) if text else (None, err)

    text, err = _run_timed(_get_lens(), img, _LENS_TIMEOUT)
    if text:
        logger.info("OCR: 'lens' succeeded.")
        return post_process(text), None

    logger.info(f"OCR: lens failed ({err}); trying local Surya fallback.")
    surya = _get_surya()
    if surya is None:
        return None, err or "lens failed and no local fallback installed"
    text, err2 = _run_timed(surya, img, _LOCAL_TIMEOUT)
    if text:
        logger.info("OCR: 'surya' succeeded.")
        return post_process(text), None
    return None, err2 or err
