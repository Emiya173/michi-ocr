#!/usr/bin/env bash
# Wayland-native one-shot OCR for michi-ocr. Bind to a niri keybind:
#
#   binds {
#       Mod+Shift+T { spawn "/path/to/michi-ocr/scripts/michi-ocr.sh"; }
#   }
#
# Drag-select a screen region (slurp), capture it with grim (proper Wayland capture), and POST
# the PNG to the running daemon's /ocr-region. The daemon OCRs (Google Lens, Surya fallback),
# translates with DeepL, speaks the Japanese with VoiceVox, and pops a layer-shell overlay
# above the region (left-click/r/Space re-scans, t re-reads, m toggles TTS, Esc/right-click closes).
#
# Requirements: grim, slurp, curl. Daemon running (`python -m michi_ocr`).
# Override the port with MICHI_OCR_PORT.
set -euo pipefail

PORT="${MICHI_OCR_PORT:-55000}"

region="$(slurp 2>/dev/null)" || exit 0   # user cancelled the selection
[ -n "$region" ] || exit 0

# slurp prints "X,Y WxH"; url-encode the space for the query string.
geom_q="${region// /%20}"
URL="http://localhost:${PORT}/ocr-region?geometry=${geom_q}&port=${PORT}"

grim -g "$region" - | curl -fsS --data-binary @- \
    -H 'Content-Type: image/png' \
    -X POST "$URL"
echo
