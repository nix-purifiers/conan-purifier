# conan-purifier

Locked, Nix-resolved dependencies for Conan 2.

## Quickstart

From a project with `conanfile.py` or `conanfile.txt`:

```sh
nix shell 'github:nix-purifiers/conan-purifier'
conan-purifier lock
conan-purifier prefetch
conan install . --output-folder build --build=missing
conan build . --output-folder build
```

`conan build` uses your recipe's build method. For a `conanfile.txt`
consumer, use your usual build-system commands after installation.
Conan generates its normal build/run environment scripts and CMake
integration. The shell supplies Conan, the compiler, CMake and clangd.
You can install the wrapper through your usual Nix configuration.

Commit `conan.lock` and `conan-purifier.lock.json`; ignore
`.conan-purifier/`, `build/` and generated `CMakeUserPresets.json`.
Without a companion lock, the wrapper warns and runs ordinary, impure
Conan using its normal cache and configuration. Invalid or stale locks
fail with a repair instruction.

## Lock and update

```sh
conan-purifier lock
conan-purifier update fmt
conan-purifier update
```

Conan resolves recipe revisions and downloads their sources. Locking
uses its source-only API; dependency compilation happens in Nix during
prefetch. The companion contains a native recipe-only cache snapshot and
the source archive URLs and checksums. Host-built packages are excluded.

`lock` retains valid selections. `update NAME` updates that recipe, and
`update` updates the graph within the recipe's requirements.
Conan's native revision timestamps are omitted while exact recipe
revisions remain pinned.

The wrapper prepares a private native cache and directs Conan's recipe
and package reads to immutable Nix-built payloads. An existing ordinary
cache or `.conanrc` cannot replace those dependencies. Build, test and
editor commands continue in your working tree.

## Debug and platforms

```sh
conan-purifier --build-type Debug lock
conan-purifier --build-type Debug prefetch
conan install . -of build-debug -s build_type=Debug
conan build . -of build-debug -s build_type=Debug
```

Profiles are recorded per platform and build type. Generate locks on
each platform you use; compatible profiles are retained in the same
companion. Dependency options belong in the native recipe. The wrapper
supplies the compiler profiles; build tools use the Release profile.

## External locks

```sh
conan-purifier \
  --project ./app \
  --lockfile ./locks/companion.json \
  --tool-lockfile ./locks/conan.lock \
  lock

export CONAN_PURIFIER_LOCKFILE="$PWD/locks/companion.json"
export CONAN_PURIFIER_TOOL_LOCKFILE="$PWD/locks/conan.lock"
cd app
conan install . --output-folder build
```

The working tree's `conan.lock` remains untouched. `--native-lock` and
`CONAN_PURIFIER_NATIVE_LOCKFILE` also select the native lock.

## Nix builds

The overlay exposes `pkgs.conan-purifier.buildConanProject`:

```nix
pkgs.conan-purifier.buildConanProject {
  src = ./.;
}
```

It returns `packages`, `tests`, `release` and `fetches`.
`packages.environment` builds the selected dependencies;
`packages.project` builds, tests and installs a CMake consumer by default.
Use `buildCommand` for another build system. `buildType`, `cmakeFlags`,
`nativeBuildInputs`, `lockFile` and `conanLock` customize the build.
Output names and fetch declarations are available during evaluation
without building dependencies.

## Releases

```sh
conan-purifier publish \
  --repo OWNER/REPOSITORY \
  --tag v0.1.0
```

This creates or updates a GitHub release with a deterministic Nix-built
source archive containing both locks. Authentication uses ordinary `gh`
configuration.

## Scope

Supports public archive-based, hash-revision Conan recipes and the
supplied Linux GCC SDK, with Release and Debug profiles. Dependency
options must be declared in the recipe. Custom compiler profiles,
workspaces/editable bindings, package-ID lock matrices, direct Git
source clones and project-local Python recipe imports are not yet
supported. The dependency snapshot includes the native recipe and
`conandata.yml`; application source edits do not rebuild dependencies.

Guarded dependency/cache mutation commands, including `conan create`,
`export`, `download`, `upload` and cache export, are not yet supported.
Use the helper for locking and source releases. Native `install`,
`build`, `graph info` and cache inspection use the prepared dependencies.

The SDK targets x86_64-linux and aarch64-linux. Build hooks must work
offline; provide additional build tools/libraries through the Nix builder
when needed. OS package managers only report requirements.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
