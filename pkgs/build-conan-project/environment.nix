# SPDX-License-Identifier: MIT
{
  lib,
  runCommand,
  fetchurl,
  python3,
  writeText,
  nativeConan,
  toolchain,
  stdenv,
  purifier,
}:
{
  lock,
  buildType ? "Release",
  fetchers ? { },
  nativeBuildInputs ? [ ],
}:
let
  checked = purifier.checkLock lock;
  key = "${stdenv.hostPlatform.system}:${buildType}";
  profile =
    checked.data.profiles.${key}
      or (throw "conan-purifier: missing ${key} profile; run conan-purifier --build-type ${buildType} lock on this platform");
  sdkOK =
    profile.toolVersions.conan == nativeConan.version
    && profile.toolVersions.compiler == stdenv.cc.version;
  sources = purifier.tree (
    {
      name = "locked-conan-source-archives";
      entries = profile.entries;
      inherit runCommand fetchurl;
    }
    // fetchers
  );
  metadata = writeText "conan-build-inputs.json" (
    builtins.toJSON {
      inherit profile;
      inherit (checked.data) manifests inputs;
    }
  );
in
assert lib.assertMsg sdkOK
  "conan-purifier: compiler or Conan SDK changed; run conan-purifier --build-type ${buildType} lock";
runCommand "purified-conan-${lib.toLower buildType}-environment"
  {
    nativeBuildInputs = [
      toolchain
      python3
    ]
    ++ nativeBuildInputs;
  }
  ''
    export HOME="$TMPDIR/user-home" XDG_CACHE_HOME="$TMPDIR/xdg"
    export CONAN_HOME="$TMPDIR/conan" CONAN_PURIFIER_HOME="$TMPDIR/conan"
    export CONAN_PURIFIER_HOOKS=${../../conan_purifier}
    export CONAN_PURIFIER_REPRODUCIBLE=1 SOURCE_DATE_EPOCH=1
    export PYTHONDONTWRITEBYTECODE=1
    export CC=${stdenv.cc}/bin/gcc CXX=${stdenv.cc}/bin/g++
    mkdir -p "$HOME"
    python3 ${../../conan_purifier}/build.py ${metadata} ${sources} "$out" ${nativeConan}/bin/conan
    ln -s ${toolchain} "$out/sdk"
    ln -s ${sources} "$out/sources"
  ''
