{
  description = "NixOS package for iCloud Keychain for Linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/44a91898084f46797b5fac650c7e8c9ac38c43d4";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      python = pkgs.python3;
      icp = python.pkgs.buildPythonApplication {
        pname = "icp-linux";
        version = "0.0.1";
        src = self;
        pyproject = true;
        build-system = [ python.pkgs.setuptools ];
        dependencies = with python.pkgs; [
          requests srp cryptography pynacl secretstorage
        ];
        nativeCheckInputs = [ python.pkgs.pytestCheckHook ];
      };
      pythonEnv = python.withPackages (_: [ icp ]);
      nativeHost = pkgs.writeShellScriptBin "icp-native-host" ''
        exec ${pythonEnv}/bin/python -m icp.vault.host "$@"
      '';
      registerChrome = pkgs.writeShellScriptBin "icp-register-chrome" ''
        set -euo pipefail
        id="''${1:-}"
        if [[ ! "$id" =~ ^[a-p]{32}$ ]]; then
          echo "Usage: icp-register-chrome <32-character Chrome extension ID>" >&2
          exit 2
        fi
        dir="''${XDG_CONFIG_HOME:-$HOME/.config}/google-chrome/NativeMessagingHosts"
        mkdir -p "$dir"
        ${pkgs.jq}/bin/jq -n \
          --arg path "${nativeHost}/bin/icp-native-host" \
          --arg origin "chrome-extension://$id/" \
          '{name:"org.icp.native",description:"Apple Passwords native messaging host",path:$path,type:"stdio",allowed_origins:[$origin]}' \
          > "$dir/org.icp.native.json"
        echo "Registered Chrome native messaging host: $dir/org.icp.native.json"
      '';
      extension = pkgs.runCommand "icp-chrome-extension" { } ''
        mkdir -p "$out/share/icp/extension"
        cp -r ${self}/extension/. "$out/share/icp/extension/"
      '';
    in {
      packages.${system} = {
        inherit icp extension;
        default = pkgs.symlinkJoin {
          name = "icp-linux-nixos";
          paths = [ icp nativeHost registerChrome extension ];
        };
      };
    };
}
