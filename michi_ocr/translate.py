"""DeepL translation (JA → ZH by default). Ported from the GSM fork's deepl_client.

Returns ``(translation, error)``: error is non-empty only when DeepL was configured but the
call failed (quota / network / proxy stall), so the overlay can show *why* it's blank.
"""

from __future__ import annotations

import logging

import requests

from michi_ocr.config import Config

logger = logging.getLogger("michi_ocr.translate")

FREE_URL = "https://api-free.deepl.com/v2/translate"
PRO_URL = "https://api.deepl.com/v2/translate"

_LANG_MAP = {
    "en": "EN",
    "ja": "JA",
    "zh": "ZH",
    "es": "ES",
    "fr": "FR",
    "de": "DE",
    "it": "IT",
    "nl": "NL",
    "pl": "PL",
    "pt": "PT-PT",
    "ru": "RU",
    "ko": "KO",
}


def _resolve_url(api_key: str, api_type: str) -> str:
    api_type = (api_type or "auto").strip().lower()
    if api_type == "free":
        return FREE_URL
    if api_type == "pro":
        return PRO_URL
    return FREE_URL if api_key.strip().endswith(":fx") else PRO_URL


def translate(text: str, cfg: Config) -> tuple[str, str]:
    """Translate `text` to the configured target language. Best-effort."""
    if not (text and cfg.deepl_configured()):
        return "", ""
    target = _LANG_MAP.get(cfg.deepl_target_lang.lower(), cfg.deepl_target_lang)
    try:
        # (connect, read) timeouts — a stalled (router) proxy must not freeze the overlay for
        # 30s; fail fast and report a timeout instead.
        resp = requests.post(
            _resolve_url(cfg.deepl_api_key, cfg.deepl_api_type),
            headers={
                "Authorization": f"DeepL-Auth-Key {cfg.deepl_api_key.strip()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"text": text.strip().strip('"').strip("'"), "target_lang": target},
            timeout=(5, 15),
        )
        resp.raise_for_status()
        translated = resp.json()["translations"][0]["text"]
        return translated, ("" if translated else "翻译为空（额度/网络？）")
    except Exception as e:  # noqa: BLE001
        logger.error(f"DeepL translation failed: {e}")
        return "", f"翻译失败: {str(e)[:80]}"
