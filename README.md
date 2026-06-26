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
nix develop                      # .venv-lens, deps frozen from uv.lock, GTK/PyGObject wired (Lens only)
python -m michi_ocr              # start the daemon (http://127.0.0.1:55000)

# offline Surya fallback (torch/ROCm) is opt-in and lands in its own .venv:
MICHI_OCR_SURYA=1 nix develop
```

`nix develop` installs **`--frozen` from `uv.lock`** (no network resolution) — pinning is the
reproducibility boundary. The light shell (`.venv-lens`) never carries torch; `MICHI_OCR_SURYA=1`
selects a separate `.venv` with the `[local]` Surya stack so the two don't prune each other.

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

The nix package is the **Lens core only** (ROCm torch isn't reproducibly in nixpkgs). For the
offline **Surya** fallback, run the daemon from a local checkout's devshell, which installs the
`[local]` extra **frozen from `uv.lock`** (torch 2.9.1+rocm6.4) — but only when `MICHI_OCR_SURYA=1`
is in the environment:

```nix
services.michi-ocr = {
  enable = true;
  backend = "devshell";
  devshellPath = "%h/dev/michi-ocr";
};
```

The module's `devshell` backend runs `nix develop <path> --command python -m michi_ocr` **without**
that flag, so it gets the light Lens venv. To autostart *with* Surya, drive the devshell from your
own user service that sets the env:

```nix
systemd.user.services.michi-ocr.Service = {
  Environment = "MICHI_OCR_SURYA=1";
  ExecStart = "${pkgs.nix}/bin/nix develop %h/dev/michi-ocr --command python -m michi_ocr";
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

Surya needs torch. The `[local]` extra pins it to the ROCm wheel (`torch==2.9.1` →
`2.9.1+rocm6.4`) via `[tool.uv.sources]`, so it's locked in `uv.lock` — no imperative install.
Just enter the Surya devshell (its own `.venv`, synced `--frozen` from the lock):

```sh
MICHI_OCR_SURYA=1 nix develop
python -m michi_ocr
```

(`pytorch-triton-rocm` is listed as a direct dep on purpose — it's not on PyPI and `uv`'s
`[tool.uv.sources]` only reroutes *direct* deps, so otherwise `uv lock` can't find it.)

Notes: RDNA4 (RX 9070 XT / gfx1201) needs **ROCm 6.4+**; the **first** Surya inference
JIT-compiles MIOpen kernels for the new arch (one-time, can take a long while, cached in
`~/.cache/miopen`). Force CPU with `MICHI_OCR_FORCE_CPU=1`.

Bumping torch/Surya: edit the pins in `pyproject.toml`, run `uv lock`, commit `uv.lock`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/ocr-region?geometry=…&port=…&tts=0\|1&spawn_overlay=0\|1` | OCR a posted PNG |
| POST | `/tts/play` `{text}` | re-speak a line |
| GET | `/healthz` | liveness |
