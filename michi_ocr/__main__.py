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
    if not cfg.deepl_configured():
        logging.getLogger("michi_ocr").warning(
            "No DeepL key set (deepl_api_key in %s) — OCR + TTS will work, translation stays blank.",
            config.CONFIG_PATH,
        )
    daemon.serve(cfg)


if __name__ == "__main__":
    main()
