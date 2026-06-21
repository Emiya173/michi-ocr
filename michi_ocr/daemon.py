"""HTTP daemon — stdlib ThreadingHTTPServer, no Flask/waitress.

Endpoints (all always return JSON; the overlay parses the body as JSON, so an HTML error
page would surface as "响应非JSON"):

  POST /ocr-region?geometry=X,Y%20WxH&port=N&tts=0|1&spawn_overlay=0|1
       body = raw image bytes (PNG from grim). OCR -> translate -> TTS -> (spawn overlay).
  POST /tts/play     {text}  -> re-speak a line (overlay's "t" key)
  GET  /healthz              -> {"status":"ok"}

One server thread per request (ThreadingHTTPServer), so a slow OCR/translate doesn't block
other requests.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from michi_ocr import pipeline, tts
from michi_ocr.config import Config

logger = logging.getLogger("michi_ocr.daemon")


class Handler(BaseHTTPRequestHandler):
    cfg: Config  # injected on the server instance, mirrored here in do_*

    def log_message(self, *_args):  # quiet the default per-request stderr spam
        pass

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def _cfg(self) -> Config:
        return self.server.cfg  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/healthz":
            return self._json({"status": "ok"})
        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/ocr-region":
                return self._handle_ocr_region(parse_qs(parsed.query))
            if parsed.path == "/tts/play":
                return self._handle_tts_play()
            self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 — always answer JSON, never an HTML 500
            logger.exception(f"{parsed.path} failed: {e}")
            self._json({"status": "error", "error": str(e)}, 500)

    def _handle_ocr_region(self, q: dict) -> None:
        img = self._read_body()
        if not img:
            return self._json({"error": "empty body; POST raw image bytes"}, 400)

        def _q1(name, default=None):
            v = q.get(name)
            return v[0] if v else default

        play_tts = None
        if _q1("tts") == "0":
            play_tts = False
        elif _q1("tts") == "1":
            play_tts = True

        result = pipeline.run_ocr_region(img, self._cfg, play_tts=play_tts)

        geometry = _q1("geometry", "")
        if result.get("status") == "ok" and geometry and _q1("spawn_overlay", "1") != "0":
            try:
                port = int(_q1("port") or self._cfg.port)
            except (TypeError, ValueError):
                port = self._cfg.port
            pipeline.spawn_overlay(geometry, result["text"], result.get("translation", ""), port)
        self._json(result, 200)

    def _handle_tts_play(self) -> None:
        try:
            body = json.loads(self._read_body() or b"{}")
        except json.JSONDecodeError:
            body = {}
        text = (body.get("text") or "").strip()
        if not text:
            return self._json({"error": "Missing 'text'"}, 400)
        tts.play(text, self._cfg)
        self._json({"status": "ok"})


def serve(cfg: Config) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", cfg.port), Handler)
    server.cfg = cfg  # type: ignore[attr-defined]
    server.daemon_threads = True
    logger.info(f"michi-ocr daemon listening on http://127.0.0.1:{cfg.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.shutdown()
