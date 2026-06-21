"""Google Lens OCR client (vendored + slimmed from owocr / GameSentenceMiner).

This is a faithful port of the proven owocr ``GoogleLens`` engine, trimmed to the only
thing michi-ocr needs: **text**. The original returned a 6-tuple carrying per-word bounding
boxes, crop coordinates and a furigana-size filter to support an interactive overlay with
per-word hover; none of that is used here, so it's dropped. The protobuf request/response
machinery (the genuinely complex, working part) is kept verbatim — see ``lens_protos/`` and
``lens_betterproto.py``, vendored unmodified.

``GoogleLens()(img)`` returns ``(ok: bool, text_or_error: str)``.
"""

from __future__ import annotations

import importlib
import io
import logging
import os
import random
import re
from math import sqrt
from pathlib import Path

import curl_cffi
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("michi_ocr.lens")

_UNINITIALIZED = object()
_LENS_PROTO_DEPS = _UNINITIALIZED
_MESSAGE_TO_DICT = _UNINITIALIZED

_PROTO_PKG = "michi_ocr.ocr.lens_protos"

# The public Google Lens API key that Chromium ships and that owocr / chrome-lens-ocr reuse
# (the whole request impersonates Chrome). It is NOT a personal/secret credential — it's
# already public in Chromium's source and many repos. Override with MICHI_OCR_LENS_API_KEY.
_DEFAULT_LENS_API_KEY = "AIzaSyDr2UxVnv_U85AbhhY8XSHSIavUW0DC-sY"


def _lens_api_key() -> str:
    return os.environ.get("MICHI_OCR_LENS_API_KEY") or _DEFAULT_LENS_API_KEY


def _load_lens_proto_dependencies():
    """Import the generated Lens protobuf messages (cached). None if protobuf is missing."""
    global _LENS_PROTO_DEPS
    if _LENS_PROTO_DEPS is not _UNINITIALIZED:
        return _LENS_PROTO_DEPS

    def _import():
        server = importlib.import_module(f"{_PROTO_PKG}.lens_overlay_server_pb2")
        return {
            "LensOverlayServerRequestPb2": server.LensOverlayServerRequest,
            "LensOverlayServerResponsePb2": server.LensOverlayServerResponse,
            "PLATFORM_WEB": importlib.import_module(f"{_PROTO_PKG}.lens_overlay_platform_pb2").PLATFORM_WEB,
            "SURFACE_CHROMIUM": importlib.import_module(f"{_PROTO_PKG}.lens_overlay_surface_pb2").SURFACE_CHROMIUM,
            "AUTO_FILTER": importlib.import_module(f"{_PROTO_PKG}.lens_overlay_filters_pb2").AUTO_FILTER,
        }

    try:
        _LENS_PROTO_DEPS = _import()
    except Exception:
        # Some protobuf runtime combinations need this compatibility switch to import gencode.
        previous = os.environ.get("TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK")
        try:
            os.environ["TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK"] = "true"
            _LENS_PROTO_DEPS = _import()
        except Exception:
            _LENS_PROTO_DEPS = None
        finally:
            if previous is None:
                os.environ.pop("TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK", None)
            else:
                os.environ["TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK"] = previous
    return _LENS_PROTO_DEPS


def _load_message_to_dict():
    global _MESSAGE_TO_DICT
    if _MESSAGE_TO_DICT is _UNINITIALIZED:
        try:
            _MESSAGE_TO_DICT = importlib.import_module("google.protobuf.json_format").MessageToDict
        except ImportError:
            _MESSAGE_TO_DICT = None
    return _MESSAGE_TO_DICT


def input_to_pil_image(img):
    """Accept a PIL image, raw bytes, or a path; return (pil_image, is_path)."""
    if isinstance(img, Image.Image):
        return img, False
    if isinstance(img, (bytes, bytearray)):
        try:
            return Image.open(io.BytesIO(img)), False
        except (UnidentifiedImageError, OSError):
            return None, False
    if isinstance(img, Path):
        try:
            pil = Image.open(img)
            pil.load()
            return pil, True
        except (UnidentifiedImageError, OSError):
            return None, False
    raise ValueError(f"img must be a path, PIL.Image or bytes object, got: {type(img)}")


def _pil_to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="png", compress_level=6)
    return buf.getvalue()


# Inter-line spacing heuristic (from owocr): when two detected line fragments sit on the same
# axis, decide whether to join them with a space. For CJK this is almost always "".
_INTER_LINE_OPENING_PUNCTUATION = "([{\"'「『（【〈《［｛＜"
_INTER_LINE_CLOSING_PUNCTUATION_RE = re.compile(
    r"^[\)\]\}\.,!?:;%…，。、？！：；"
    r"」』）】〉》］｝＞]"
)


def _should_insert_inter_line_space(previous_text: str, current_text: str) -> bool:
    if not previous_text or not current_text:
        return False
    prev_last, curr_first = previous_text[-1], current_text[0]
    if prev_last.isspace() or curr_first.isspace():
        return False
    if prev_last in _INTER_LINE_OPENING_PUNCTUATION:
        return False
    if _INTER_LINE_CLOSING_PUNCTUATION_RE.match(current_text):
        return False
    return True


def build_spatial_text(line_entries, same_axis_height_ratio=0.6, blank_line_height_ratio=2.0, blank_line_token=None):
    """Assemble detected line fragments into reading-order text (handles vertical JP)."""
    parts = []
    previous = None
    for entry in line_entries:
        line_text = str(entry.get("text", "") or "")
        if not line_text:
            continue
        if previous is not None:
            separator = "\n"
            use_vertical = bool(previous.get("is_vertical")) and bool(entry.get("is_vertical"))
            if use_vertical:
                prev_c, curr_c = previous.get("center_x"), entry.get("center_x")
                prev_d = max(float(previous.get("width") or 0.0), 1.0)
                curr_d = max(float(entry.get("width") or 0.0), 1.0)
            else:
                prev_c, curr_c = previous.get("center_y"), entry.get("center_y")
                prev_d = max(float(previous.get("height") or 0.0), 1.0)
                curr_d = max(float(entry.get("height") or 0.0), 1.0)
            if prev_c is not None and curr_c is not None:
                dist = abs(float(curr_c) - float(prev_c))
                if dist <= max(prev_d, curr_d) * float(same_axis_height_ratio):
                    separator = " " if _should_insert_inter_line_space(previous.get("text", ""), line_text) else ""
                elif blank_line_token and dist > ((prev_d + curr_d) / 2.0) * float(blank_line_height_ratio):
                    separator = f"\n{blank_line_token}\n"
            parts.append(separator)
        parts.append(line_text)
        previous = entry
    return "".join(parts)


class GoogleLens:
    """Network OCR via Google Lens (lensfrontend-pa.googleapis.com). No local model."""

    name = "lens"

    def __init__(self):
        self.available = False
        self._deps = _load_lens_proto_dependencies()
        self._message_to_dict = _load_message_to_dict()
        if self._deps is None or self._message_to_dict is None:
            logger.warning("Google Lens unavailable: protobuf dependencies missing.")
        else:
            self.available = True
            logger.info("Google Lens ready.")

    @staticmethod
    def _is_timeout_error(exc) -> bool:
        cls = getattr(getattr(curl_cffi, "exceptions", None), "Timeout", None)
        return cls is not None and isinstance(exc, cls)

    @staticmethod
    def _is_connection_error(exc) -> bool:
        cls = getattr(getattr(curl_cffi, "exceptions", None), "ConnectionError", None)
        return cls is not None and isinstance(exc, cls)

    def _preprocess(self, img):
        # Lens rejects very large images; cap at ~3MP keeping aspect ratio.
        if img.width * img.height > 3_000_000:
            ar = img.width / img.height
            new_w = int(sqrt(3_000_000 * ar))
            img = img.resize((new_w, int(new_w / ar)), Image.Resampling.LANCZOS)
        return _pil_to_png_bytes(img), img.width, img.height

    def __call__(self, img, timeout: float = 20.0):
        img, is_path = input_to_pil_image(img)
        if not img:
            return (False, "Invalid image provided")
        if not self.available:
            if is_path:
                img.close()
            return (False, "Google Lens is not available.")
        try:
            request = self._deps["LensOverlayServerRequestPb2"]()
            ctx = request.objects_request.request_context
            ctx.request_id.uuid = random.randint(0, 2**64 - 1)
            ctx.request_id.sequence_id = 0
            ctx.request_id.image_sequence_id = 0
            ctx.request_id.analytics_id = random.randbytes(16)
            ctx.request_id.routing_info.Clear()
            ctx.client_context.platform = self._deps["PLATFORM_WEB"]
            ctx.client_context.surface = self._deps["SURFACE_CHROMIUM"]
            ctx.client_context.locale_context.language = "ja"
            ctx.client_context.locale_context.region = "Asia/Tokyo"
            ctx.client_context.locale_context.time_zone = ""
            ctx.client_context.app_id = ""
            request_filter = ctx.client_context.client_filters.filter.add()
            request_filter.filter_type = self._deps["AUTO_FILTER"]

            payload_bytes, width, height = self._preprocess(img)
            request.objects_request.image_data.payload.image_bytes = payload_bytes
            request.objects_request.image_data.image_metadata.width = width
            request.objects_request.image_data.image_metadata.height = height

            headers = {
                "Host": "lensfrontend-pa.googleapis.com",
                "Connection": "keep-alive",
                "Content-Type": "application/x-protobuf",
                "X-Goog-Api-Key": _lens_api_key(),
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Dest": "empty",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
                ),
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "ja-JP;q=0.6,ja;q=0.5",
            }
            try:
                res = curl_cffi.post(
                    "https://lensfrontend-pa.googleapis.com/v1/crupload",
                    data=request.SerializeToString(),
                    headers=headers,
                    impersonate="chrome",
                    timeout=timeout,
                )
            except Exception as e:  # noqa: BLE001
                if self._is_timeout_error(e):
                    return (False, "Request timeout!")
                if self._is_connection_error(e):
                    return (False, "Connection error!")
                return (False, f"Lens request error: {e}")

            if res.status_code != 200:
                return (False, f"Lens HTTP {res.status_code}")

            response_proto = self._deps["LensOverlayServerResponsePb2"]()
            response_proto.ParseFromString(res.content)
            response_dict = self._message_to_dict(response_proto, preserving_proto_field_name=True)

            text = response_dict.get("objects_response", {}).get("text", {})
            line_entries = []
            for paragraph in text.get("text_layout", {}).get("paragraphs", []):
                is_vertical = "TOP_TO_BOTTOM" in str(paragraph.get("writing_direction", ""))
                for line in paragraph.get("lines", []):
                    box = line.get("geometry", {}).get("bounding_box", {}) or {}
                    words = line.get("words", [])
                    line_text = "".join(
                        (w.get("plain_text", "") + (w.get("text_separator", "") or "")) for w in words
                    ).strip()
                    if not line_text:
                        continue
                    line_entries.append(
                        {
                            "text": line_text,
                            "center_x": float(box.get("center_x", 0.0)) * img.width,
                            "center_y": float(box.get("center_y", 0.0)) * img.height,
                            "width": float(box.get("width", 0.0)) * img.width,
                            "height": float(box.get("height", 0.0)) * img.height,
                            "is_vertical": is_vertical,
                        }
                    )
            return (True, build_spatial_text(line_entries))
        finally:
            if is_path:
                img.close()
