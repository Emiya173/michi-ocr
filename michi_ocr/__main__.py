"""`python -m michi_ocr` — start the daemon."""

from __future__ import annotations

import logging

from michi_ocr import config, daemon


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOG_DIR / "michi-ocr.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    _setup_logging()
    cfg = config.get()
    if not cfg.translate_configured():
        provider = (cfg.translate_provider or "xfyun").strip().lower()
        keys = "deepl_api_key" if provider == "deepl" else "xfyun_app_id/xfyun_api_key/xfyun_api_secret"
        logging.getLogger("michi_ocr").warning(
            "No %s credentials set (%s in %s) — OCR + TTS will work, translation stays blank.",
            provider,
            keys,
            config.CONFIG_PATH,
        )
    daemon.serve(cfg)


if __name__ == "__main__":
    main()
