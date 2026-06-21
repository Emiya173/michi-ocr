"""Translation backends (JA → ZH by default).

Two providers, selected by ``cfg.translate_provider``:
  * ``xfyun`` — iFlytek / 讯飞 NiuTrans (default), https://www.xfyun.cn/doc/nlp/niutrans/API.html
  * ``deepl`` — DeepL Free/Pro (ported from the GSM fork's deepl_client)

``translate()`` returns ``(translation, error)``: error is non-empty only when a provider was
configured but the call failed (quota / network / bad signature), so the overlay can show
*why* it's blank.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from email.utils import formatdate

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

# iFlytek NiuTrans
XFYUN_HOST = "ntrans.xfyun.cn"
XFYUN_PATH = "/v2/ots"
XFYUN_URL = f"https://{XFYUN_HOST}{XFYUN_PATH}"


def translate(text: str, cfg: Config) -> tuple[str, str]:
    """Translate `text` to the configured target language via the active provider."""
    if not text:
        return "", ""
    provider = (cfg.translate_provider or "").strip().lower()
    if provider == "deepl":
        return _translate_deepl(text, cfg)
    return _translate_xfyun(text, cfg)


# --- iFlytek / 讯飞 NiuTrans ------------------------------------------------------------------


def _xfyun_auth_headers(body: bytes, cfg: Config) -> dict[str, str]:
    """Build the HMAC-SHA256 signed headers NiuTrans expects (signature over the request body)."""
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    digest = "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
    signature_origin = (
        f"host: {XFYUN_HOST}\n"
        f"date: {date}\n"
        f"POST {XFYUN_PATH} HTTP/1.1\n"
        f"digest: {digest}"
    )
    signature = base64.b64encode(
        hmac.new(
            cfg.xfyun_api_secret.strip().encode(),
            signature_origin.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    authorization = (
        f'api_key="{cfg.xfyun_api_key.strip()}", algorithm="hmac-sha256", '
        f'headers="host date request-line digest", signature="{signature}"'
    )
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Method": "POST",
        "Host": XFYUN_HOST,
        "Date": date,
        "Digest": digest,
        "Authorization": authorization,
    }


def _translate_xfyun(text: str, cfg: Config) -> tuple[str, str]:
    if not cfg.xfyun_configured():
        return "", ""
    src = text.strip().strip('"').strip("'")
    payload = {
        "common": {"app_id": cfg.xfyun_app_id.strip()},
        "business": {"from": cfg.xfyun_from or "auto", "to": cfg.xfyun_to or "cn"},
        "data": {"text": base64.b64encode(src.encode()).decode()},
    }
    # Serialize once: the digest must be over the exact bytes we send. The signature is over the
    # JSON *string*, so json.dumps() and the bytes we POST must be identical.
    body = json.dumps(payload).encode()
    try:
        # (connect, read) timeouts — a stalled network must not freeze the overlay; fail fast.
        resp = requests.post(
            XFYUN_URL, data=body, headers=_xfyun_auth_headers(body, cfg), timeout=(5, 15)
        )
        # NiuTrans returns its real error (auth / quota / unsupported pair) as a JSON code+message
        # even on a 4xx — don't let raise_for_status() swallow it.
        try:
            data = resp.json()
        except ValueError:
            logger.error(f"iFlytek HTTP {resp.status_code}: {resp.text[:200]}")
            return "", f"翻译失败: HTTP {resp.status_code}"
        if data.get("code") != 0:
            msg = data.get("message", "unknown")
            logger.error(f"iFlytek translation failed: code={data.get('code')} {msg} | {resp.text[:200]}")
            return "", f"翻译失败: [{data.get('code')}] {str(msg)[:60]}"
        translated = data["data"]["result"]["trans_result"]["dst"]
        return translated, ("" if translated else "翻译为空（额度/网络？）")
    except Exception as e:  # noqa: BLE001
        logger.error(f"iFlytek translation failed: {e}")
        return "", f"翻译失败: {str(e)[:80]}"


# --- DeepL -----------------------------------------------------------------------------------


def _resolve_url(api_key: str, api_type: str) -> str:
    api_type = (api_type or "auto").strip().lower()
    if api_type == "free":
        return FREE_URL
    if api_type == "pro":
        return PRO_URL
    return FREE_URL if api_key.strip().endswith(":fx") else PRO_URL


def _translate_deepl(text: str, cfg: Config) -> tuple[str, str]:
    if not cfg.deepl_configured():
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
