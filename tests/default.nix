# SPDX-License-Identifier: MIT
{ pkgs, self }:
let
  release = pkgs.conan-purifier.buildConanProject { src = ../examples/minimal; };
  debug = pkgs.conan-purifier.buildConanProject {
    src = ../examples/minimal;
    buildType = "Debug";
  };
in
{
  foreign-projections = import ./foreign.nix {
    inherit pkgs;
    build = pkgs.conan-purifier.buildConanProject;
  };
  cli = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  environment = release.packages.environment;
  project = release.tests;
  debug = debug.tests;
  release = release.release;
}
