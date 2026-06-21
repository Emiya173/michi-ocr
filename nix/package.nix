{
  lib,
  python3Packages,
  gtk3,
  gtk-layer-shell,
  gobject-introspection,
  wrapGAppsHook3,
  grim,
  slurp,
  mpv,
  ffmpeg,
  curl,
}:
# Reproducible build of michi-ocr's *core* (Lens) path: all deps come from nixpkgs. The
# optional offline Surya OCR (torch/ROCm) is NOT packaged here — ROCm torch isn't reproducibly
# in nixpkgs; use the devshell backend for that. The overlay's GTK/gtk-layer-shell typelibs are
# wired via wrapGAppsHook3 (the daemon's env is inherited by the overlay subprocess).
python3Packages.buildPythonApplication rec {
  pname = "michi-ocr";
  version = "0.1.0";
  pyproject = true;

  src = ../.;

  build-system = [ python3Packages.hatchling ];

  nativeBuildInputs = [
    gobject-introspection
    wrapGAppsHook3
  ];

  buildInputs = [
    gtk3
    gtk-layer-shell
  ];

  dependencies = with python3Packages; [
    requests
    curl-cffi
    protobuf
    pillow
    jaconv
    pygobject3
    pycairo
  ];

  # Don't double-wrap: let the Python entry-point wrapper carry the GApps env (GI_TYPELIB_PATH
  # for Gtk-3.0 / GtkLayerShell-0.1) plus the runtime CLIs the app shells out to.
  dontWrapGApps = true;
  makeWrapperArgs = [
    "\${gappsWrapperArgs[@]}"
    "--prefix PATH : ${
      lib.makeBinPath [
        grim
        slurp
        mpv
        ffmpeg
        curl
      ]
    }"
  ];

  # Install the niri trigger as `michi-ocr-trigger` so a keybind can reference a stable path.
  postInstall = ''
    install -Dm755 scripts/michi-ocr.sh $out/bin/michi-ocr-trigger
  '';

  # Light import check only — the overlay needs a display + GTK typelib env, so skip it here.
  pythonImportsCheck = [ "michi_ocr" "michi_ocr.ocr.lens" "michi_ocr.daemon" ];

  meta = {
    description = "Wayland-native OCR -> translate -> speak -> overlay tool for learning Japanese";
    mainProgram = "michi-ocr";
    platforms = lib.platforms.linux;
  };
}
