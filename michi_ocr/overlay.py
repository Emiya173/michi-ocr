"""Transparent OCR-translation overlay (Wayland/niri, GTK3 + gtk-layer-shell).

A wlr-layer-shell window that floats above the OCR region showing the recognized Japanese
line + its translation (notifications hide under fullscreen games; a layer-shell surface does
not). The window is sized to the text box only, so everything outside it passes through to the
game — you can keep playing while it's up.

Run as a subprocess of the daemon (inherits the nix GTK/GI env):

    python -m michi_ocr.overlay --geometry "X,Y WxH" --port 55000 --text "<ja>" --translation "<zh>"

Controls (mouse works without stealing the game's keyboard):
  * Left-click / r / Space  -> re-OCR the same region (re-fires TTS + translation)
  * Arrow keys              -> move the window (hold Shift for larger steps)
  * t                       -> re-speak the current line (no re-OCR / translate)
  * m                       -> toggle TTS for this window (re-scans then pass ?tts=0)
  * Right-click / Esc       -> close

Keyboard controls need the box focused (layer-shell ON_DEMAND) — click it once first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import urllib.parse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

CSS = b"""
.michi-box {
    background-color: rgba(20, 22, 28, 0.92);
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    padding: 12px 16px;
}
.michi-ja { color: #b9c0cc; font-size: 13pt; }
.michi-zh { color: #ffffff; font-size: 17pt; font-weight: 600; }
.michi-hint { color: #6b7280; font-size: 9pt; }
"""


def _parse_geometry(geom: str) -> tuple[int, int, int, int]:
    pos, size = geom.strip().split(" ")
    x, y = (int(v) for v in pos.split(","))
    w, h = (int(v) for v in size.split("x"))
    return x, y, w, h


class Overlay:
    def __init__(self, geometry: str, port: int, text: str, translation: str):
        self.geometry = geometry
        self.port = port
        self.x, self.y, self.w, self.h = _parse_geometry(geometry)
        self.tts_enabled = True

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.win = Gtk.Window()
        GtkLayerShell.init_for_window(self.win)
        GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.OVERLAY)
        # ON_DEMAND so we don't grab the game's keyboard; box-sized so only the box intercepts
        # the pointer — clicks elsewhere reach the game.
        GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.ON_DEMAND)
        # Multi-monitor: pin the surface to the output that contains the region, and use margins
        # RELATIVE to that output's origin. Without set_monitor, layer-shell picks a default
        # output and our global (slurp) coords become wrong there — on a secondary monitor the
        # margin lands off-screen and the window is invisible (while TTS still fired). slurp/grim
        # and Gdk monitor geometry are both in logical compositor coords, so no scaling math.
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(self.x, self.y) if display else None
        if monitor is not None:
            GtkLayerShell.set_monitor(self.win, monitor)
            mgeo = monitor.get_geometry()
            self.mon_x, self.mon_y = mgeo.x, mgeo.y
        else:
            self.mon_x, self.mon_y = 0, 0
        self.off_x = 0  # arrow-key nudge from the auto-anchored position (logical px)
        self.off_y = 0
        GtkLayerShell.set_anchor(self.win, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self.win, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.LEFT, max(0, self.x - self.mon_x))
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.TOP, max(0, self.y - self.mon_y))

        self.win.set_app_paintable(True)
        visual = Gdk.Screen.get_default().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)

        self.win.connect("button-press-event", self._on_click)
        self.win.connect("key-press-event", self._on_key)
        self.win.connect("destroy", Gtk.main_quit)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.get_style_context().add_class("michi-box")
        # A wrapping label collapses to its minimum width unless the box width is pinned.
        self.box_width = min(max(self.w, 360), 720)
        self.box.set_size_request(self.box_width, -1)
        self.ja_label = self._make_label("michi-ja")
        self.zh_label = self._make_label("michi-zh")
        self.hint = self._make_label("michi-hint")
        self._update_hint()
        self.box.pack_start(self.ja_label, False, False, 0)
        self.box.pack_start(self.zh_label, False, False, 0)
        self.box.pack_start(self.hint, False, False, 0)
        self.win.add(self.box)

        self.box.connect("size-allocate", lambda *_: self._reposition())
        self.set_content(text, translation)

    def _make_label(self, css_class: str) -> Gtk.Label:
        lbl = Gtk.Label()
        lbl.get_style_context().add_class(css_class)
        lbl.set_xalign(0.0)
        lbl.set_line_wrap(True)
        return lbl

    def _update_hint(self) -> None:
        tts = "开" if self.tts_enabled else "关"
        self.hint.set_text(
            f"方向键: 移动 (Shift 大步)    左键 / r: 重扫    t: 重读    m: TTS[{tts}]    右键 / Esc: 关闭"
        )

    def set_content(self, text: str, translation: str, error: str = "") -> None:
        self.text = text or ""
        self.ja_label.set_text(self.text)
        self.zh_label.set_text(translation or error or "（无翻译）")

    def _reposition(self) -> None:
        # Anchor the box just above the region (drop below if there's no room at the top of the
        # output), then apply the user's arrow-key nudge. All coords are global; margins are
        # relative to the output origin (mon_x/mon_y).
        bh = self.box.get_allocated_height()
        top = self.y - bh - 8
        if top < self.mon_y:
            top = self.y + self.h + 8
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.LEFT, max(0, self.x - self.mon_x + self.off_x))
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.TOP, max(0, top - self.mon_y + self.off_y))

    def _on_click(self, _w, event) -> bool:
        if event.button == 1:
            self._rescan()
        elif event.button == 3:
            self.win.destroy()
        return True

    def _on_key(self, _w, event) -> bool:
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.win.destroy()
        elif key in (Gdk.KEY_r, Gdk.KEY_R, Gdk.KEY_space):
            self._rescan()
        elif key in (Gdk.KEY_t, Gdk.KEY_T):
            self._replay_tts()
        elif key in (Gdk.KEY_m, Gdk.KEY_M):
            self.tts_enabled = not self.tts_enabled
            self._update_hint()
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            step = 100 if (event.state & Gdk.ModifierType.SHIFT_MASK) else 20
            if key == Gdk.KEY_Left:
                self.off_x -= step
            elif key == Gdk.KEY_Right:
                self.off_x += step
            elif key == Gdk.KEY_Up:
                self.off_y -= step
            else:
                self.off_y += step
            self._reposition()
        return True

    def _replay_tts(self) -> None:
        text = self.text
        if not text:
            return

        def worker():
            try:
                subprocess.run(
                    [
                        "curl",
                        "-fsS",
                        "-X",
                        "POST",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        json.dumps({"text": text}),
                        f"http://localhost:{self.port}/tts/play",
                    ],
                    capture_output=True,
                    check=True,
                )
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _rescan(self) -> None:
        # Hide so grim can't catch our own box, then re-OCR the region off-thread.
        self.win.hide()
        GLib.timeout_add(80, self._do_rescan)

    def _rescan_once(self) -> tuple[str, str, str]:
        """One grim->POST round-trip. Returns (text, translation, error)."""
        png = subprocess.run(["grim", "-g", self.geometry, "-"], capture_output=True)
        if png.returncode != 0:
            return "", "", f"grim 失败: {png.stderr.decode('utf-8', 'replace')[:80]}"

        params = {"geometry": self.geometry, "spawn_overlay": "0"}
        if not self.tts_enabled:
            params["tts"] = "0"
        q = urllib.parse.urlencode(params)
        proc = subprocess.run(
            [
                "curl",
                "-sS",  # not -f: keep the body on HTTP errors so we can show it
                "--max-time",
                "40",  # cap the round-trip; a stalled translate must not hang forever
                "-w",
                "\n%{http_code}",  # append the HTTP status on its own trailing line
                "--data-binary",
                "@-",
                "-X",
                "POST",
                f"http://localhost:{self.port}/ocr-region?{q}",
            ],
            input=png.stdout,
            capture_output=True,
        )
        if proc.returncode != 0:
            return "", "", f"curl 失败: {proc.stderr.decode('utf-8', 'replace')[:80]}"

        raw = proc.stdout.decode("utf-8", "replace")
        body, _, code = raw.rpartition("\n")  # split off the -w status line
        body = body.strip()
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            # Log the full response so the root cause is visible (HTML 500, wrong port, ...).
            print(f"[overlay] rescan non-JSON (HTTP {code}): {body!r}", file=sys.stderr, flush=True)
            return "", "", f"HTTP {code} 非JSON: {body[:60]}"
        text = data.get("text", "")
        translation = data.get("translation", "")
        error = data.get("translation_error", "") or ("" if text else data.get("error", ""))
        return text, translation, error

    def _do_rescan(self) -> bool:
        def worker():
            text = translation = error = ""
            for _attempt in range(3):  # retry: rescans fail transiently (proxy/translate blips)
                try:
                    text, translation, error = self._rescan_once()
                except Exception as e:  # noqa: BLE001
                    text, translation, error = "", "", str(e)[:80]
                if text:
                    break
            if not text and not error:
                error = "重扫无文本"
            GLib.idle_add(self._apply_rescan, text or "重扫失败", translation, error)

        threading.Thread(target=worker, daemon=True).start()
        return False  # don't repeat the timeout

    def _apply_rescan(self, text: str, translation: str, error: str) -> bool:
        self.set_content(text, translation, error)
        self.win.show_all()
        self._reposition()
        return False

    def run(self) -> None:
        self.win.show_all()
        Gtk.main()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--geometry", required=True, help='region in slurp format: "X,Y WxH"')
    p.add_argument("--port", type=int, default=55000)
    p.add_argument("--text", default="")
    p.add_argument("--translation", default="")
    args = p.parse_args()
    Overlay(args.geometry, args.port, args.text, args.translation).run()


if __name__ == "__main__":
    main()
