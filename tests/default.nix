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
  cli = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  environment = release.packages.environment;
  project = release.tests;
  debug = debug.tests;
  release = release.release;
}
