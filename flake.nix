# SPDX-License-Identifier: MIT
{
  description = "Locked, Nix-resolved dependencies for Conan";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.purifier = {
    url = "github:nix-purifiers/purifier";
    flake = false;
  };
  outputs =
    {
      self,
      nixpkgs,
      purifier,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = nixpkgs.lib.genAttrs systems;
      overlay = final: prev: {
        conan-purifier = final.lib.makeScope final.newScope (scope: {
          nativeConan = scope.callPackage ./pkgs/build-conan-project/conan.nix { };
          toolchain = final.buildEnv {
            name = "conan-purifier-toolchain";
            paths = [
              scope.nativeConan
              final.stdenv.cc
              final.cmake
              final.ninja
              final.gnumake
              final.pkg-config
              final.git
              final.coreutils
              final.clang-tools
              final.autoconf
              final.automake
              final.libtool
              final.which
              final.bash
            ];
          };
          cli = scope.callPackage ./pkgs/cli/default.nix {
            nixpkgsSrc = nixpkgs;
            purifierSrc = purifier;
          };
          buildConanProject = scope.callPackage ./pkgs/build-conan-project/default.nix {
            purifier = import (purifier + "/nix/lib") { inherit (final) lib; };
          };
        });
      };
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
    in
    {
      overlays.default = overlay;
      packages = forAll (system: {
        native = (pkgsFor system).conan-purifier.nativeConan;
        default = (pkgsFor system).conan-purifier.cli;
        cli = self.packages.${system}.default;
      });
      lib.conan-purifier = (nixpkgs.lib.extend (import ./lib-overlays/default)).conan-purifier;
      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/conan-purifier";
          meta.description = "Lock and prefetch Conan dependencies";
        };
      });
      checks = forAll (
        system:
        import ./tests {
          pkgs = pkgsFor system;
          inherit self;
        }
      );
      formatter = forAll (system: (pkgsFor system).nixfmt-tree);
    };
}
