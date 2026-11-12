# SPDX-License-Identifier: MIT
{
  project,
  lockFile,
  conanLock,
  buildType,
  target,
}:
let
  pkgs = import (/. + "@nixpkgs@") { };
  purifier = import (/. + "@purifier@/nix/lib") { inherit (pkgs) lib; };
  nativeConan = pkgs.callPackage ./conan.nix { };
  toolchain = pkgs.buildEnv {
    name = "conan-purifier-toolchain";
    paths = [
      nativeConan
      pkgs.stdenv.cc
      pkgs.cmake
      pkgs.ninja
      pkgs.gnumake
      pkgs.pkg-config
      pkgs.git
      pkgs.coreutils
      pkgs.clang-tools
      pkgs.autoconf
      pkgs.automake
      pkgs.libtool
      pkgs.which
      pkgs.bash
    ];
  };
  args = {
    src = /. + project;
    lockFile = /. + lockFile;
    conanLock = /. + conanLock;
  };
  lock = import ../../lib-overlays/default/read-lock.nix { inherit (pkgs) lib; } args;
in
if target == "environment" then
  pkgs.callPackage ./environment.nix { inherit purifier nativeConan toolchain; } {
    inherit lock buildType;
  }
else if target == "runtime" then
  pkgs.runCommand "conan-purifier-runtime" { } ''ln -s @runtime@ "$out"''
else if target == "release" then
  (pkgs.callPackage ./default.nix { inherit purifier nativeConan toolchain; } (
    args // { inherit buildType; }
  )).release
else
  throw "conan-purifier: unknown realization target ${target}"
