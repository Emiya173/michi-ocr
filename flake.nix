{
  description = "michi-ocr dev shell (NixOS): Wayland OCR -> translate -> speak -> overlay";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      py = pkgs.python312;

      # GTK + gtk-layer-shell stack for the transparent wlr-layer-shell translation overlay.
      gtkPkgs = with pkgs; [
        gtk3
        gtk-layer-shell
        gobject-introspection
        gdk-pixbuf
        librsvg
        pango
        cairo
        harfbuzz
        atk
      ];

      # System libs the PyPI wheels (pillow, curl_cffi, protobuf, and optionally torch/surya)
      # dlopen at runtime, plus the GTK/Wayland libs the overlay needs.
      runtimeLibs = pkgs.lib.makeLibraryPath ([
        pkgs.stdenv.cc.cc.lib
        pkgs.zlib
        pkgs.glib
        pkgs.libGL
        pkgs.fontconfig
        pkgs.freetype
        pkgs.libxkbcommon
        pkgs.wayland
        # torch/ROCm wheel dlopen deps (the [local] Surya extra)
        pkgs.zstd          # libzstd.so.1
        pkgs.numactl       # libnuma.so.1
        pkgs.elfutils      # libelf.so.1
      ] ++ gtkPkgs);

      # GI typelibs (Gtk-3.0, GtkLayerShell-0.1, ...) for the overlay. Gtk-3.0 pulls
      # Pango/GObject/GLib/Gio typelibs transitively from the `.out` outputs.
      giTypelibPath = pkgs.lib.makeSearchPath "lib/girepository-1.0"
        (gtkPkgs ++ [ pkgs.glib.out pkgs.pango.out ]);

      # nixpkgs PyGObject/pycairo for the venv: its cp312 `_gi` .so is ABI-compatible with the
      # uv (python-build-standalone) cpython 3.12, and ships only gi/cairo, so it's safe on PYTHONPATH.
      pyGiPath = pkgs.lib.makeSearchPath "lib/python3.12/site-packages" [
        pkgs.python312Packages.pygobject3
        pkgs.python312Packages.pycairo
      ];
    in
    {
      # Reproducible nix build of the core (Lens) path. `nix run` / home-manager use this.
      packages.${system}.default = pkgs.callPackage ./nix/package.nix { };

      # Home Manager module: `services.michi-ocr.enable = true;`. See nix/hm-module.nix.
      homeManagerModules.default = import ./nix/hm-module.nix self;

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          (py.withPackages (ps: [ ps.pip ]))
          uv
          ruff
          git
          # runtime tools
          grim
          slurp
          mpv
          ffmpeg
          curl
        ];

        shellHook = ''
          export LD_LIBRARY_PATH=${runtimeLibs}:$LD_LIBRARY_PATH
          # nixpkgs python packages can leak their site-packages onto PYTHONPATH and shadow the
          # venv; start clean, then add only the nixpkgs PyGObject path (for the overlay's `import gi`).
          unset PYTHONPATH
          export GI_TYPELIB_PATH=${giTypelibPath}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
          export PYTHONPATH=${pyGiPath}

          # Reproducible installs come straight from uv.lock (uv sync --frozen; never resolves
          # the network). The heavy [local]/Surya extra (torch 2.9.1+rocm6.4, gfx1201/RDNA4) is
          # opt-in via MICHI_OCR_SURYA=1 and lands in its OWN venv, so the default Lens-only shell
          # never carries torch and the two never prune each other.
          if [ -n "''${MICHI_OCR_SURYA:-}" ]; then
            export UV_PROJECT_ENVIRONMENT=.venv
            uv sync --frozen --extra dev --extra local --python ${py}/bin/python3
          else
            export UV_PROJECT_ENVIRONMENT=.venv-lens
            uv sync --frozen --extra dev --python ${py}/bin/python3
          fi
          . "$UV_PROJECT_ENVIRONMENT/bin/activate"

          echo "michi-ocr dev shell ready (venv: $UV_PROJECT_ENVIRONMENT''${MICHI_OCR_SURYA:+, Surya}). Run 'python -m michi_ocr'."
        '';
      };
    };
}
