# SPDX-License-Identifier: MIT
{ lib }:
{
  src,
  lockFile ? src + "/conan-purifier.lock.json",
  conanLock ? src + "/conan.lock",
}:
let
  fix = message: "conan-purifier: ${message}; run conan-purifier lock";
  exists = builtins.pathExists lockFile && builtins.pathExists conanLock;
  raw =
    if exists then
      builtins.addErrorContext (fix "invalid companion JSON") (
        builtins.fromJSON (builtins.readFile lockFile)
      )
    else
      { };
  data = if builtins.isAttrs raw then raw else { };
  nativeText = if exists then builtins.readFile conanLock else "";
  native =
    if exists then
      builtins.addErrorContext (fix "invalid native lock JSON") (builtins.fromJSON nativeText)
    else
      { };
  categories = [
    "requires"
    "build_requires"
    "python_requires"
    "config_requires"
  ];
  systems = {
    x86_64-linux = "x86_64";
    aarch64-linux = "armv8";
  };
  refOK =
    r:
    builtins.isString r
    &&
      builtins.match "[A-Za-z0-9_+.-]+/[A-Za-z0-9_+.-]+(@[A-Za-z0-9_+.-]+/[A-Za-z0-9_+.-]+)?#[0-9a-f]{32}" r
      != null;
  nativeOK =
    n:
    builtins.isAttrs n
    && (n.version or null) == "0.5"
    && builtins.all (
      key:
      builtins.elem key (
        [
          "version"
          "alias"
          "overrides"
        ]
        ++ categories
      )
    ) (builtins.attrNames n)
    && builtins.all (
      category:
      let
        values = n.${category} or null;
      in
      builtins.isList values
      && builtins.all refOK values
      && values == lib.sort builtins.lessThan (lib.unique values)
    ) categories
    && builtins.isAttrs (n.alias or { })
    && builtins.isAttrs (n.overrides or { });
  pathOK =
    p:
    builtins.isString p
    && p != ""
    && !lib.hasPrefix "/" p
    && !lib.hasInfix "\\" p
    && builtins.all (
      v:
      !(builtins.elem v [
        ""
        "."
        ".."
      ])
    ) (lib.splitString "/" p);
  hashOK = h: builtins.isString h && builtins.match "sha256-[A-Za-z0-9+/]{43}=" h != null;
  hex64 = h: builtins.isString h && builtins.match "[0-9a-f]{64}" h != null;
  b64 =
    v:
    builtins.isString v
    && v != ""
    && builtins.match "([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?" v != null;
  urlOK =
    u:
    let
      parts =
        if builtins.isString u then builtins.match "https?://([^/[:space:]]+)(/[^[:space:]]*)?" u else null;
    in
    parts != null && !lib.hasInfix "@" (builtins.elemAt parts 0);
  entryOK =
    p: e:
    builtins.isAttrs e
    && urlOK (e.url or null)
    && builtins.isList (e.mirrors or null)
    && builtins.all urlOK e.mirrors
    && hashOK (e.hash or null)
    &&
      (e.path or null) == "profiles/${p.system}/${p.buildType}/assets/${
        builtins.hashString "sha256" (builtins.toJSON ([ e.url ] ++ e.mirrors))
      }.archive";
  profiles = data.profiles or null;
  profileNative =
    p:
    if builtins.isAttrs p && builtins.isString (p.nativeLock or null) then
      builtins.addErrorContext (fix "invalid profile lock JSON") (builtins.fromJSON p.nativeLock)
    else
      { };
  profileOK =
    key: p:
    let
      s = p.settings or { };
      pins = profileNative p;
      expectedProfile =
        "[settings]\n"
        + lib.concatMapStrings (k: "${k}=${s.${k}}\n") (builtins.attrNames s)
        + "\n[conf]\ntools.system.package_manager:mode=report\n";
      expectedBuildProfile =
        "[settings]\n"
        + lib.concatMapStrings (k: "${k}=${if k == "build_type" then "Release" else s.${k}}\n") (
          builtins.attrNames s
        )
        + "\n[conf]\ntools.system.package_manager:mode=report\n";
      refs = lib.sort builtins.lessThan (
        lib.unique (lib.concatMap (category: pins.${category}) categories)
      );
    in
    builtins.isAttrs p
    && builtins.isString (p.system or null)
    && builtins.hasAttr p.system systems
    && builtins.elem (p.buildType or null) [
      "Release"
      "Debug"
    ]
    && key == "${p.system}:${p.buildType}"
    && builtins.isAttrs s
    &&
      builtins.attrNames s == [
        "arch"
        "build_type"
        "compiler"
        "compiler.cppstd"
        "compiler.libcxx"
        "compiler.version"
        "os"
      ]
    && builtins.all builtins.isString (builtins.attrValues s)
    && s.arch == systems.${p.system}
    && s.build_type == p.buildType
    && s.os == "Linux"
    && s.compiler == "gcc"
    && s."compiler.libcxx" == "libstdc++11"
    && builtins.match "[0-9]+" s."compiler.version" != null
    && builtins.match "(gnu)?[0-9]+" s."compiler.cppstd" != null
    && (p.profile or null) == expectedProfile
    && (p.buildProfile or null) == expectedBuildProfile
    && nativeOK pins
    && (p.recipeRefs or null) == refs
    && b64 (p.recipeArchive or null)
    && hashOK (p.recipeArchiveHash or null)
    && builtins.isList (p.entries or null)
    && builtins.all (entryOK p) p.entries
    && map (e: e.path) p.entries == lib.sort builtins.lessThan (lib.unique (map (e: e.path) p.entries))
    && builtins.isAttrs (p.toolVersions or null)
    && builtins.isString (p.toolVersions.conan or null)
    && builtins.isString (p.toolVersions.compiler or null)
    && hex64 (p.toolVersions.resolver or null);
  inputs =
    map
      (path: {
        inherit path;
        hash =
          if builtins.pathExists (src + "/${path}") then
            builtins.hashFile "sha256" (src + "/${path}")
          else
            null;
      })
      [
        "conanfile.py"
        "conanfile.txt"
        "conandata.yml"
      ];
  manifests = data.manifests or null;
  snapshotsOK =
    builtins.isAttrs manifests
    &&
      builtins.attrNames manifests
      == lib.sort builtins.lessThan (map (i: i.path) (builtins.filter (i: i.hash != null) inputs))
    && builtins.all (
      i:
      builtins.isString manifests.${i.path} && builtins.hashString "sha256" manifests.${i.path} == i.hash
    ) (builtins.filter (i: i.hash != null) inputs);
  active = data.activeProfile or null;
  activePins = profileNative profiles.${active};
  expectedNative =
    (removeAttrs activePins categories)
    // builtins.listToAttrs (
      map (category: {
        name = category;
        value = lib.sort builtins.lessThan (
          lib.unique (lib.concatMap (p: (profileNative p).${category}) (builtins.attrValues profiles))
        );
      }) categories
    );
  problems = map fix (
    if !exists then
      [ "missing native or companion lock" ]
    else if
      !builtins.pathExists (src + "/conanfile.py") && !builtins.pathExists (src + "/conanfile.txt")
    then
      [ "missing native recipe" ]
    else if !builtins.isAttrs raw || (data.version or null) != 1 then
      [ "unsupported companion format" ]
    else if
      (data.contentHash or null)
      != builtins.hashString "sha256" (builtins.toJSON (removeAttrs data [ "contentHash" ]))
    then
      [ "companion contentHash mismatch" ]
    else if (data.inputs or null) != inputs then
      [ "project manifests changed" ]
    else if !snapshotsOK then
      [ "native recipe snapshots changed" ]
    else if
      (data.nativeHash or null) != builtins.hashString "sha256" nativeText
      || (data.nativeLock or null) != nativeText
    then
      [ "native Conan lock changed" ]
    else if !nativeOK native then
      [ "invalid native hash-mode recipe pins" ]
    else if
      !builtins.isAttrs profiles
      || profiles == { }
      || !builtins.isString active
      || !builtins.hasAttr active profiles
    then
      [ "missing native SDK profiles" ]
    else if !builtins.all (key: profileOK key profiles.${key}) (builtins.attrNames profiles) then
      [ "invalid native SDK profile or source snapshot" ]
    else if native != expectedNative then
      [ "native recipe pins do not match SDK profiles" ]
    else
      [ ]
  );
in
{
  purifier = "conan-purifier";
  inherit problems data;
  entries =
    if problems == [ ] then lib.concatMap (p: p.entries) (builtins.attrValues profiles) else [ ];
  outputs =
    if problems == [ ] then
      {
        project = { };
        environment = { };
      }
    else
      { };
}
