# michi-ocr

A small, Wayland-native tool for reading Japanese while playing games: select a screen
region, and it OCRs the text, translates it (iFlytek / 讯飞 or DeepL → Chinese), speaks the Japanese line
(VoiceVox), and shows a transparent overlay above the region that you can re-scan / re-read
without alt-tabbing.

It's a focused extraction of a fork that lived inside
[GameSentenceMiner](https://github.com/bpwhelan/GameSentenceMiner) — the OCR→translate→speak→
overlay pipeline only, with none of GSM's Anki / OBS / stats / Electron machinery.

## How it works

```
niri keybind ──> scripts/michi-ocr.sh ──(grim PNG)──> daemon  /ocr-region
                  (slurp region)                         │
                                                          ├─ OCR   (Google Lens, Surya fallback)
                                                          ├─ 讯飞 / DeepL translation (JA → ZH)
                                                          ├─ VoiceVox TTS  (mpv subprocess)
                                                          └─ spawn layer-shell overlay
```

- **OCR**: Google Lens (network, fast, accurate) first; if it fails/stalls (offline), a local
  **Surya** torch model (the `[local]` extra) is used. Set `MICHI_OCR_RACE=1` to race them.
- **Overlay** controls: left-click / `r` / `Space` re-scan · arrow keys move (Shift = larger
  step) · `t` re-read · `m` toggle TTS · right-click / `Esc` close.

## Setup (NixOS)

```sh
cd ~/dev/michi-ocr
nix develop                      # builds .venv, installs deps, wires GTK/PyGObject
python -m michi_ocr              # start the daemon (http://127.0.0.1:55000)
```

Start VoiceVox and configure the translator:

```sh
docker compose -f scripts/voicevox-compose.yml up -d
$EDITOR ~/.config/michi-ocr/config.toml
```

Bind the trigger in niri:

```kdl
binds {
    Mod+Shift+T { spawn "/home/you/dev/michi-ocr/scripts/michi-ocr.sh"; }
}
```

## Reproducible install (flake + home-manager)

The flake exposes a **`packages.default`** (a reproducible nix build of the core / Lens path —
all deps from nixpkgs) and a **`homeManagerModules.default`**.

Run it directly without cloning:

```sh
nix run github:Emiya173/michi-ocr            # start the daemon
nix run github:Emiya173/michi-ocr#default -- # same
```

Declarative home-manager setup — add the input and enable the module:

```nix
# flake.nix
{
  inputs.michi-ocr.url = "github:Emiya173/michi-ocr";
  # ... pass `inputs` through to home-manager ...
}

# home.nix
{ inputs, ... }:
{
  imports = [ inputs.michi-ocr.homeManagerModules.default ];

  services.michi-ocr = {
    enable = true;                       # nix package + systemd user service + config.toml
    port = 55000;
    settings = {
      translate_provider = "xfyun";      # iFlytek / 讯飞 (default), or "deepl"
      xfyun_from = "ja";
      xfyun_to = "cn";                   # NiuTrans uses "cn" for Chinese, not "zh"
      voicevox_url = "http://127.0.0.1:50021";
      speaker_id = 2;
      play_on_ocr = true;
    };
    # Keep API secrets OUT of the world-readable nix store. `secretsFile` is a systemd
    # EnvironmentFile with any MICHI_OCR_<FIELD>=… overrides — here the 讯飞 credentials:
    #   MICHI_OCR_XFYUN_APP_ID=...
    #   MICHI_OCR_XFYUN_API_KEY=...
    #   MICHI_OCR_XFYUN_API_SECRET=...
    secretsFile = "/run/secrets/michi-ocr";             # e.g. sops-nix / agenix
    # For DeepL instead: set translate_provider = "deepl" and point deeplApiKeyFile at a file
    # containing MICHI_OCR_DEEPL_API_KEY=xxxxxxxx:fx
    # deeplApiKeyFile = "/run/secrets/michi-ocr-deepl";
  };
}
```

`michi-ocr-trigger` (the niri keybind target) lands on your `$PATH`:

```kdl
binds { Mod+Shift+T { spawn "michi-ocr-trigger"; } }
```

### Offline OCR (Surya) with the module

The nix package is the **Lens core only** (ROCm torch isn't reproducibly in nixpkgs). To use
the local Surya fallback, point the module at a local checkout and switch the backend — the
service then runs `nix develop <path> --command python -m michi_ocr`, picking up the `[local]`
extra you installed into that checkout's `.venv` (see *Offline OCR* below):

```nix
services.michi-ocr = {
  enable = true;
  backend = "devshell";
  devshellPath = "%h/dev/michi-ocr";
};
```

## Config (`~/.config/michi-ocr/config.toml`)

```toml
# Translation provider: "xfyun" (iFlytek / 讯飞 NiuTrans, default) or "deepl"
translate_provider = "xfyun"

# --- iFlytek / 讯飞 (default) — credentials from https://console.xfyun.cn (机器翻译 niutrans)
xfyun_app_id     = "xxxxxxxx"
xfyun_api_key    = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
xfyun_api_secret = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
xfyun_from       = "ja"         # source lang ("auto" to auto-detect)
xfyun_to         = "cn"         # NiuTrans uses "cn" for Chinese, not "zh"

# --- DeepL (still supported; used when translate_provider = "deepl")
deepl_api_key = "xxxxxxxx:fx"
deepl_target_lang = "ZH"

voicevox_url = "http://127.0.0.1:50021"
speaker_id = 2                  # browse :50021/speakers
play_on_ocr = true
port = 55000
```

Every field can also be overridden by an env var: `MICHI_OCR_<FIELD>` (e.g.
`MICHI_OCR_PORT=7000`). Unlike GSM, nothing rewrites this file behind your back.

The Google Lens request uses the public Chromium-shipped Lens API key by default (same one
owocr / chrome-lens-ocr use — not a personal credential). Override it with
`MICHI_OCR_LENS_API_KEY` if you'd rather supply your own.

## Offline OCR (`[local]` extra, AMD/ROCm)

Surya needs torch. On an AMD GPU (e.g. RX 9070 XT / RDNA4 / gfx1201) install the ROCm wheel —
the default index pulls the CUDA build:

```sh
.venv/bin/uv pip install --index-strategy unsafe-best-match \
  --index-url https://download.pytorch.org/whl/rocm6.4 \
  --extra-index-url https://pypi.org/simple "torch==2.9.1+rocm6.4"
.venv/bin/uv pip install -e ".[local]"
```

Notes: RDNA4 needs **ROCm 6.4+**; the **first** Surya inference JIT-compiles MIOpen kernels
for the new arch (one-time, can take a long while, cached in `~/.cache/miopen`). Force CPU
with `MICHI_OCR_FORCE_CPU=1`. Use `.venv/bin/...` directly — `uv run` re-syncs from the lock
and reverts these imperative installs.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/ocr-region?geometry=…&port=…&tts=0\|1&spawn_overlay=0\|1` | OCR a posted PNG |
| POST | `/tts/play` `{text}` | re-speak a line |
| GET | `/healthz` | liveness |
