# SPDX-License-Identifier: MIT
{
  lib,
  stdenvNoCC,
  stdenv,
  makeWrapper,
  python3,
  nix,
  gh,
  nativeConan,
  toolchain,
  cacert,
  nixpkgsSrc,
  purifierSrc,
}:
stdenvNoCC.mkDerivation {
  pname = "conan-purifier";
  version = "0.1.0";
  src = ../.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    mkdir -p "$out/bin" "$out/lib/conan-purifier"
    cp -r conan_purifier nix "$out/lib/conan-purifier/"
    install -m755 bin/conan-purifier "$out/bin/conan-purifier"
    substituteInPlace "$out/lib/conan-purifier/nix/realize.nix" \
      --replace-fail '@nixpkgs@' '${nixpkgsSrc}' \
      --replace-fail '@purifier@' '${purifierSrc}' \
      --replace-fail '@runtime@' "$out"
    wrapProgram "$out/bin/conan-purifier" \
      --prefix PATH : ${
        lib.makeBinPath [
          python3
          nix
          gh
          toolchain
        ]
      } \
      --set-default SSL_CERT_FILE ${cacert}/etc/ssl/certs/ca-bundle.crt \
      --set CONAN_PURIFIER_CONAN ${nativeConan}/bin/conan \
      --set CONAN_PURIFIER_SYSTEM ${stdenv.hostPlatform.system} \
      --set CONAN_PURIFIER_CC ${stdenv.cc}/bin/gcc \
      --set CONAN_PURIFIER_CXX ${stdenv.cc}/bin/g++ \
      --set CONAN_PURIFIER_CONAN_VERSION ${nativeConan.version} \
      --set CONAN_PURIFIER_COMPILER_VERSION ${stdenv.cc.version}
    makeWrapper "$out/bin/conan-purifier" "$out/bin/conan" --add-flags 'native --'
    for program in ${toolchain}/bin/*; do
      name=$(basename "$program")
      if [ ! -e "$out/bin/$name" ]; then ln -s "$program" "$out/bin/$name"; fi
    done
  '';
  meta = {
    description = "Native Conan workflow with locked Nix-resolved dependencies";
    homepage = "https://nix-purifiers.github.io/";
    license = lib.licenses.mit;
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
    mainProgram = "conan-purifier";
  };
}
