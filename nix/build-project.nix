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
  gnutar,
  gzip,
}:
{
  src,
  lockFile ? src + "/conan-purifier.lock.json",
  conanLock ? src + "/conan.lock",
  buildType ? "Release",
  fetchers ? { },
  nativeLibraries ? { },
  nativeIncludeDirectories ? [ ],
  resourceRoots ? { },
  resourceDependencies ? [ ],
  resourceSchema ? null,
  nativeBuildInputs ? [ ],
  cmakeFlags ? [ ],
  buildCommand ? ''
    cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE="$PWD/generated/conan_toolchain.cmake" -DCMAKE_BUILD_TYPE=${lib.escapeShellArg buildType} ${lib.escapeShellArgs cmakeFlags}
    cmake --build build --parallel "$NIX_BUILD_CORES"
    ctest --test-dir build --output-on-failure
    cmake --install build --prefix "$out"
  '',
  srcFilter ?
    path: type:
    !(builtins.elem (baseNameOf path) [
      ".git"
      ".conan-purifier"
      "build"
      "build-debug"
      "build-release"
      "generated"
      "result"
      "__pycache__"
      "CMakeUserPresets.json"
    ]),
}:
let
  lock = purifier.checkLock (
    import ./read-lock.nix { inherit lib; } { inherit src lockFile conanLock; }
  );
  environment =
    import ./environment.nix
      {
        inherit
          lib
          runCommand
          fetchurl
          python3
          writeText
          nativeConan
          toolchain
          stdenv
          purifier
          ;
      }
      {
        inherit
          lock
          buildType
          fetchers
          nativeBuildInputs
          ;
      };
  source = lib.cleanSourceWith {
    inherit src;
    filter = srcFilter;
  };
  builtProject =
    runCommand "purified-conan-project" { nativeBuildInputs = [ toolchain ] ++ nativeBuildInputs; }
      ''
        export HOME="$TMPDIR/user-home" XDG_CACHE_HOME="$TMPDIR/xdg"
        export CONAN_HOME="$TMPDIR/conan" CONAN_PURIFIER_HOME="$TMPDIR/conan"
        export CONAN_PURIFIER_HOOKS=${../conan_purifier}
        export PYTHONDONTWRITEBYTECODE=1 SOURCE_DATE_EPOCH=1
        export CC=${stdenv.cc}/bin/gcc CXX=${stdenv.cc}/bin/g++
        mkdir -p "$HOME" "$CONAN_HOME/extensions/commands" "$CONAN_HOME/profiles" project
        cp ${../conan_purifier/cmd_purifier.py} "$CONAN_HOME/extensions/commands/cmd_purifier.py"
        cp ${environment}/profile "$CONAN_HOME/profiles/purifier"
        cp ${environment}/build-profile "$CONAN_HOME/profiles/purifier-build"
        ${nativeConan}/bin/conan purifier hydrate --environment ${environment}
        export CONAN_PURIFIER_PROVIDER=${environment}
        cp -r ${source}/. project/
        chmod -R u+w project
        cd project
        ${nativeConan}/bin/conan install . --no-remote --build=never --profile:host=purifier --profile:build=purifier-build --lockfile=${environment}/conan.lock --output-folder=generated
        ${buildCommand}
      '';
  projections =
    lib.optionalAttrs (nativeLibraries != { }) (
      purifier.nativeProjections {
        producer = builtProject;
        libraries = nativeLibraries;
        includeDirectories = nativeIncludeDirectories;
        system = stdenv.hostPlatform.system;
        targetMetadata.tool = "conan";
        inherit runCommand python3;
      }
    )
    // lib.optionalAttrs (resourceRoots != { }) (
      purifier.resourceProjections {
        producer = builtProject;
        roots = resourceRoots;
        dependencies = resourceDependencies;
        schema = resourceSchema;
        inherit runCommand python3;
      }
    );
  project = builtProject // {
    inherit projections;
  };
  release =
    assert lock.problems == [ ];
    runCommand "conan-project-release"
      {
        nativeBuildInputs = [
          gnutar
          gzip
        ];
      }
      ''
        mkdir source "$out"
        cp -r ${source}/. source/
        chmod -R u+w source
        cp ${lockFile} source/conan-purifier.lock.json
        cp ${conanLock} source/conan.lock
        tar --sort=name --mtime=@1 --owner=0 --group=0 --numeric-owner -C source -cf - . | gzip -n > "$out/project.tar.gz"
      '';
in
{
  packages = {
    inherit project environment;
    default = project;
  };
  tests = project;
  inherit release projections;
  fetches = lock.entries;
}
