{
  description = "weft-cm5-carrier: development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.just
            pkgs.typos
            pkgs.reuse
            pkgs.kicad-small
            pkgs.freerouting
            pkgs.python3
          ];
          # KiCad's library paths and its Python module (pcbnew), taken from the kicad-cli
          # wrapper so the generator and the PCB scripts see the same KiCad as kicad-cli.
          shellHook = ''
            eval "$(sed -n "s/^export \\(KICAD10_[A-Z0-9]*_DIR\\)=\''${[A-Z0-9_]*-'\\(.*\\)'}$/export \\1='\\2'/p" ${pkgs.kicad-small}/bin/kicad-cli)"
            export PYTHONPATH="$(sed -n "s/^export PYTHONPATH='\\(.*\\)'$/\\1/p" ${pkgs.kicad-small}/bin/kicad-cli)"
          '';
        };
      });
    };
}
