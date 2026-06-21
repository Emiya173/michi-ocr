# Home Manager module for michi-ocr. Wired from the flake as `homeManagerModules.default`,
# which passes `self` so the package default resolves to this flake's build.
self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.michi-ocr;
  tomlFormat = pkgs.formats.toml { };
  # Merge the port into the rendered settings so config.toml is the single source of truth.
  settings = cfg.settings // { inherit (cfg) port; };
  configFile = tomlFormat.generate "michi-ocr-config.toml" settings;
in
{
  options.services.michi-ocr = {
    enable = lib.mkEnableOption "michi-ocr (Wayland OCR -> translate -> speak -> overlay)";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "michi-ocr.packages.\${system}.default";
      description = "The michi-ocr package to use (the nix-built core / Lens path).";
    };

    backend = lib.mkOption {
      type = lib.types.enum [ "package" "devshell" ];
      default = "package";
      description = ''
        How the daemon is run:
        - "package": the reproducible nix build (Lens + translate + TTS + overlay; no Surya).
        - "devshell": `nix develop <devshellPath> --command python -m michi_ocr`, so the local
          checkout's `[local]` Surya/ROCm install is used. Set `devshellPath` to the repo path.
      '';
    };

    devshellPath = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "%h/dev/michi-ocr";
      description = "Path to the repo checkout, required when backend = \"devshell\".";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 55000;
      description = "Localhost port the daemon listens on.";
    };

    settings = lib.mkOption {
      type = tomlFormat.type;
      default = { };
      example = lib.literalExpression ''
        {
          deepl_target_lang = "ZH";
          voicevox_url = "http://127.0.0.1:50021";
          speaker_id = 2;
          play_on_ocr = true;
        }
      '';
      description = ''
        Rendered to ~/.config/michi-ocr/config.toml. Put non-secret fields here. For the DeepL
        key prefer `deeplApiKeyFile` (keeps it out of the world-readable nix store).
      '';
    };

    deeplApiKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/secrets/michi-ocr-deepl";
      description = ''
        Path to a file containing `MICHI_OCR_DEEPL_API_KEY=<key>` (systemd EnvironmentFile
        format). Read at service start; never copied into the nix store. The env override takes
        precedence over config.toml.
      '';
    };

    secretsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/secrets/michi-ocr";
      description = ''
        Path to a systemd EnvironmentFile with any `MICHI_OCR_<FIELD>=<value>` overrides — the
        place for the iFlytek / 讯飞 credentials so they stay out of the world-readable nix store:

            MICHI_OCR_XFYUN_APP_ID=...
            MICHI_OCR_XFYUN_API_KEY=...
            MICHI_OCR_XFYUN_API_SECRET=...

        Read at service start; env overrides take precedence over config.toml.
      '';
    };

    service = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run the daemon as a systemd user service bound to graphical-session.target.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.backend != "devshell" || cfg.devshellPath != null;
        message = "services.michi-ocr: backend = \"devshell\" requires devshellPath.";
      }
    ];

    # `michi-ocr` + `michi-ocr-trigger` (the niri keybind target) on PATH.
    home.packages = [ cfg.package ];

    xdg.configFile."michi-ocr/config.toml".source = configFile;

    systemd.user.services.michi-ocr = lib.mkIf cfg.service {
      Unit = {
        Description = "michi-ocr daemon";
        After = [ "graphical-session.target" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        ExecStart =
          if cfg.backend == "devshell" then
            "${pkgs.nix}/bin/nix develop ${cfg.devshellPath} --command python -m michi_ocr"
          else
            lib.getExe cfg.package;
        WorkingDirectory = lib.mkIf (cfg.backend == "devshell") cfg.devshellPath;
        EnvironmentFile =
          let files = lib.filter (f: f != null) [ cfg.deeplApiKeyFile cfg.secretsFile ];
          in lib.mkIf (files != [ ]) (map toString files);
        Restart = "on-failure";
        RestartSec = 3;
      };
      Install.WantedBy = [ "graphical-session.target" ];
    };
  };
}
