# SPDX-License-Identifier: MIT
{ conan }:
conan.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./conan-cache-save-temp.patch ];
  postPatch = (old.postPatch or "") + ''
    substituteInPlace conan/internal/paths.py \
      --replace-fail 'user_home = _user_home_from_conanrc_file() or os.getenv("CONAN_HOME")' \
        'user_home = os.getenv("CONAN_PURIFIER_HOME") or _user_home_from_conanrc_file() or os.getenv("CONAN_HOME")'
  '';
  # Check the adapted home selector and the native cache protocol used here.
  enabledTestPaths = [
    "test/unittests/paths/user_home_test.py"
    "test/integration/command/cache/test_cache_save_restore.py"
    "test/integration/command/cache/test_cache_integrity.py"
  ];
})
