# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  built = build {
    src = ./fixtures/ffi;
    nativeLibraries.proof = "lib/libproof${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
    nativeIncludeDirectories = [ "include" ];
    resourceRoots."proof.h" = "include/proof.h";
  };
in
pkgs.runCommand "foreign-library-and-header-consumers"
  {
    nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.cffi ])) ];
  }
  ''
    test -e ${
      import ./native-consumer.nix {
        inherit pkgs;
        projection = built.projections;
        library = "proof";
        symbol = "answer";
      }
    }
    test -e ${
      import ./resource-consumer.nix {
        inherit pkgs;
        projection = built.projections.resources;
      }
    }
    python - <<'PY'
    import cffi, json, pathlib
    catalog = json.load(open('${built.projections.native.catalog}'))
    ffi = cffi.FFI()
    ffi.cdef(pathlib.Path('${built.projections.resources.directory}/proof.h').read_text())
    assert ffi.dlopen(catalog['libraries']['proof']).answer() == 42
    PY
    touch $out
  ''
