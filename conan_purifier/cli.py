# SPDX-License-Identifier: MIT
import argparse, base64, configparser, fcntl, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from . import lock

CONAN = os.environ.get("CONAN_PURIFIER_CONAN", "conan")
SYSTEM = os.environ.get("CONAN_PURIFIER_SYSTEM", "")


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    temporary.replace(path)


def clean_environment():
    env = dict(os.environ)
    for key in (
        "CONAN_PURIFIER_HOME",
        "CONAN_PURIFIER_HOOKS",
        "CONAN_PURIFIER_CAPTURE",
        "CONAN_PURIFIER_ASSETS",
        "CONAN_PURIFIER_PROVIDER",
        "CONAN_PURIFIER_REPRODUCIBLE",
    ):
        env.pop(key, None)
    return env


def bootstrap(home):
    commands = home / "extensions/commands"
    commands.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        Path(__file__).parent / "cmd_purifier.py", commands / "cmd_purifier.py"
    )


def sdk_environment(home, reproducible=False):
    env = clean_environment()
    env.update(
        CONAN_HOME=str(home),
        CONAN_PURIFIER_HOME=str(home),
        CONAN_PURIFIER_HOOKS=str(Path(__file__).parent),
        PYTHONDONTWRITEBYTECODE="1",
        CC=os.environ["CONAN_PURIFIER_CC"],
        CXX=os.environ["CONAN_PURIFIER_CXX"],
    )
    if reproducible:
        env.update(CONAN_PURIFIER_REPRODUCIBLE="1", SOURCE_DATE_EPOCH="1")
    return env


def versions():
    return {
        "conan": os.environ["CONAN_PURIFIER_CONAN_VERSION"],
        "compiler": os.environ["CONAN_PURIFIER_COMPILER_VERSION"],
        "resolver": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))
        ).hexdigest(),
    }


def detected_profile(home, env, build_type):
    run(
        [CONAN, "profile", "detect", "--name", "purifier", "--force"],
        env=env,
        stdout=sys.stderr,
    )
    path = home / "profiles/purifier"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path)
    settings = dict(parser.items("settings"))
    settings["build_type"] = build_type
    text = lock.profile_text(settings)
    write(path, text)
    return settings, text


def do_lock(root, native, companion, build_type, update=False, names=()):
    current_inputs = lock.inputs(root)
    key = SYSTEM + ":" + build_type
    previous = None
    if companion.is_file():
        try:
            candidate = json.loads(companion.read_text())
            if (
                isinstance(candidate, dict)
                and candidate.get("contentHash") == lock.seal(candidate)["contentHash"]
            ):
                previous = candidate
            profile = candidate.get("profiles", {}).get(key, {})
            if (
                not update
                and profile.get("toolVersions") == versions()
                and not lock.problems(root, native, candidate, SYSTEM, build_type)
            ):
                print("Existing native Conan selections retained", file=sys.stderr)
                return
        except (ValueError, OSError, TypeError, AttributeError):
            pass
    profiles = (
        dict(previous.get("profiles", {}))
        if previous and previous.get("inputs") == current_inputs
        else {}
    )
    old = previous.get("profiles", {}).get(key) if previous else None
    seed = None
    if old:
        try:
            seed = lock.native(old["nativeLock"])
        except (ValueError, KeyError, TypeError):
            pass
    if seed is None:
        prior = native if native.is_file() else root / "conan.lock"
        if prior.is_file():
            try:
                seed = lock.native(prior.read_text())
            except (ValueError, OSError, TypeError):
                pass
    if seed is None and previous and previous.get("inputs") == current_inputs:
        try:
            seed = lock.native(previous["nativeLock"])
        except (ValueError, KeyError, TypeError):
            pass
    if names:
        known = (
            {reference.split("/")[0] for reference in lock.refs(seed)}
            if seed
            else set()
        )
        if not seed or any(name not in known for name in names):
            raise ValueError(
                "named updates require existing locked recipe names" + lock.FIX
            )
    with tempfile.TemporaryDirectory(prefix="conan-purifier-lock-") as temporary:
        temp = Path(temporary)
        stage = temp / "project"
        stage.mkdir()
        for item in current_inputs:
            if item["hash"] is not None:
                shutil.copyfile(root / item["path"], stage / item["path"])
        home = temp / "home"
        bootstrap(home)
        env = sdk_environment(home)
        env.update(HOME=str(temp / "user-home"), XDG_CACHE_HOME=str(temp / "xdg"))
        Path(env["HOME"]).mkdir()
        settings, profile_text = detected_profile(home, env, build_type)
        staged = temp / "conan.lock"
        if seed:
            write(staged, lock.native_text(seed))
        if old:
            try:
                _, payload = lock.archive_refs(old["recipeArchive"])
                archive = temp / "seed.tgz"
                archive.write_bytes(payload)
                run([CONAN, "cache", "restore", archive], env=env, stdout=sys.stderr)
            except (ValueError, KeyError, TypeError):
                pass
        assets = temp / "assets.jsonl"
        env["CONAN_PURIFIER_CAPTURE"] = str(assets)
        graph = temp / "graph.json"
        result = temp / "resolved.lock"
        args = [
            CONAN,
            "purifier",
            "resolve",
            "--project",
            stage,
            "--profile",
            "purifier",
            "--lockfile-out",
            result,
            "--graph",
            graph,
        ]
        if seed:
            args += ["--lockfile", staged]
        if update:
            args += ["--update", *names]
        run(args, cwd=stage, env=env, stdout=sys.stderr)
        selected = lock.native(result.read_text())
        text = lock.native_text(selected)
        write(result, text)
        entries = {}
        for line in assets.read_text().splitlines() if assets.exists() else []:
            asset = json.loads(line)
            urls = asset["urls"]
            identity = hashlib.sha256(lock.canonical(urls).encode()).hexdigest()
            entry = {
                "path": "profiles/"
                + SYSTEM
                + "/"
                + build_type
                + "/assets/"
                + identity
                + ".archive",
                "url": urls[0],
                "mirrors": urls[1:],
                "hash": asset["hash"],
            }
            if identity in entries and entries[identity] != entry:
                raise ValueError("one source URL resolved to different archive bytes")
            entries[identity] = entry
        snapshot = temp / "recipes.tgz"
        save_env = {**env, "CONAN_PURIFIER_REPRODUCIBLE": "1"}
        save_env.pop("CONAN_PURIFIER_CAPTURE", None)
        run(
            [CONAN, "purifier", "recipes", "--lockfile", result, "--file", snapshot],
            env=save_env,
            stdout=sys.stderr,
        )
        payload = snapshot.read_bytes()
        profile = {
            "system": SYSTEM,
            "buildType": build_type,
            "settings": settings,
            "profile": profile_text,
            "buildProfile": lock.profile_text({**settings, "build_type": "Release"}),
            "nativeLock": text,
            "recipeRefs": lock.refs(selected),
            "recipeArchive": base64.b64encode(payload).decode(),
            "recipeArchiveHash": lock.sri(payload),
            "entries": sorted(entries.values(), key=lambda e: e["path"]),
            "toolVersions": versions(),
        }
        profiles[key] = profile
        global_text = lock.native_text(lock.union(profiles, key))
        data = {
            "version": 1,
            "inputs": current_inputs,
            "manifests": {
                item["path"]: (root / item["path"]).read_bytes().decode("utf-8")
                for item in current_inputs
                if item["hash"] is not None
            },
            "activeProfile": key,
            "profiles": profiles,
            "nativeLock": global_text,
            "nativeHash": hashlib.sha256(global_text.encode()).hexdigest(),
        }
        candidate = lock.seal(data)
        # Validate snapshot paths and the graph before writing either public lock.
        temporary_native = temp / "final.lock"
        temporary_native.write_text(global_text)
        if problems := lock.problems(
            root, temporary_native, candidate, SYSTEM, build_type
        ):
            raise ValueError("\n".join(problems))
        write(native, global_text)
        write(
            companion,
            json.dumps(candidate, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        )
        print(
            "Locked native Conan " + build_type + " graph for " + SYSTEM,
            file=sys.stderr,
        )


def checked(root, native, companion, build_type):
    data = json.loads(companion.read_text())
    if problems := lock.problems(root, native, data, SYSTEM, build_type):
        raise ValueError("\n".join(problems))
    profile = data["profiles"][SYSTEM + ":" + build_type]
    current = versions()
    if any(
        profile["toolVersions"][key] != current[key] for key in ("conan", "compiler")
    ):
        raise ValueError("Conan/compiler SDK changed" + lock.FIX)
    return data


def realize(root, native, companion, build_type, target):
    definition = Path(__file__).resolve().parents[1] / "nix/realize.nix"
    args = [
        "nix",
        "build",
        "--impure",
        "--file",
        definition,
        "--argstr",
        "project",
        root,
        "--argstr",
        "lockFile",
        companion,
        "--argstr",
        "conanLock",
        native,
        "--argstr",
        "buildType",
        build_type,
        "--argstr",
        "target",
        target,
        "--no-link",
        "--print-out-paths",
    ]
    return Path(run(args, text=True, stdout=subprocess.PIPE).stdout.strip())


def prefetch(root, native, companion, build_type):
    data = checked(root, native, companion, build_type)
    outputs = {
        target: realize(root, native, companion, build_type, target)
        for target in ("environment", "runtime")
    }
    identity = hashlib.sha256(
        (str(companion) + "\0" + str(outputs["environment"])).encode()
    ).hexdigest()[:24]
    cache = companion.parent / ".conan-purifier/roots" / identity
    cache.mkdir(parents=True, exist_ok=True)
    for target, output in outputs.items():
        run(
            [
                "nix-store",
                "--add-root",
                cache / target,
                "--indirect",
                "--realise",
                output,
            ],
            stdout=subprocess.DEVNULL,
        )
    return outputs["environment"]


def native_arguments(arguments, native, default_type):
    graph = arguments[:1] in (["install"], ["build"]) or arguments[:2] == [
        "graph",
        "info",
    ]
    build_type = default_type
    clean = []
    i = 0
    if not graph:
        return arguments, build_type, False
    expanded = []
    for value in arguments:
        if value.startswith("--") or value.startswith("-of"):
            expanded.append(value)
            continue
        for prefix in (
            "-pr:h",
            "-pr:b",
            "-pr:a",
            "-pr",
            "-s:h",
            "-s:b",
            "-s:a",
            "-o:h",
            "-o:b",
            "-o:a",
            "-cc",
            "-s",
            "-o",
            "-b",
            "-l",
            "-u",
            "-r",
        ):
            if value.startswith(prefix) and len(value) > len(prefix):
                expanded.extend([prefix, value[len(prefix) :].removeprefix("=")])
                break
        else:
            expanded.append(value)
    arguments = expanded
    while i < len(arguments):
        value = arguments[i]
        head, separator, inline = value.partition("=")
        if (
            head in ("-cc", "--core-conf")
            or head.startswith("-cc")
            or head
            in (
                "--lockfile-partial",
                "--lockfile-out",
                "--lockfile-overrides",
                "-u",
                "--update",
            )
        ):
            raise ValueError(
                "resolution/core overrides belong in conan-purifier lock or update"
            )
        settings = head in (
            "-s",
            "-s:h",
            "-s:b",
            "-s:a",
            "--settings",
            "--settings:host",
            "--settings:build",
            "--settings:all",
        )
        profiles = head in (
            "-pr",
            "-pr:h",
            "-pr:b",
            "-pr:a",
            "--profile",
            "--profile:host",
            "--profile:build",
            "--profile:all",
        )
        options = head in (
            "-o",
            "-o:h",
            "-o:b",
            "-o:a",
            "--options",
            "--options:host",
            "--options:build",
            "--options:all",
        )
        builds = head in ("-b", "--build")
        locks = head in ("-l", "--lockfile")
        remotes = head in ("-r", "--remote")
        if settings or profiles or options or builds or locks or remotes:
            if not separator:
                if i + 1 >= len(arguments):
                    raise ValueError("missing value after " + head)
                selected = arguments[i + 1]
                i += 2
            else:
                selected = inline
                i += 1
            if settings:
                name, equal, setting = selected.partition("=")
                if (
                    not equal
                    or name != "build_type"
                    or setting not in ("Release", "Debug")
                ):
                    raise ValueError(
                        "only the prepared SDK settings are supported; select Release/Debug with build_type"
                    )
                if (
                    head in ("-s:b", "--settings:build", "-s:a", "--settings:all")
                    and setting != "Release"
                ):
                    raise ValueError("the prepared build-tool profile is Release")
                build_type = (
                    setting if head not in ("-s:b", "--settings:build") else build_type
                )
            elif profiles:
                if selected not in ("default", "purifier", "purifier-build"):
                    raise ValueError(
                        "custom profiles are not prepared; use the supplied compiler SDK profile"
                    )
            elif options:
                raise ValueError(
                    "declare dependency options in conanfile.py or conanfile.txt, then run conan-purifier lock"
                )
            elif builds:
                if selected not in ("missing", "never"):
                    raise ValueError(
                        "dependency builds are prepared by Nix; use conan-purifier prefetch"
                    )
            elif locks:
                if not selected or Path(selected).expanduser().resolve() != native:
                    raise ValueError(
                        "select external native locks with CONAN_PURIFIER_NATIVE_LOCKFILE or --native-lock"
                    )
            continue
        clean.append(value)
        i += 1
    return clean, build_type, True


def runtime(root, native, companion, arguments, build_type):
    if not companion.exists():
        print(
            "conan-purifier: no lockfile; using ordinary impure conan", file=sys.stderr
        )
        os.execve(CONAN, [CONAN, *arguments], clean_environment())
    command = arguments[0] if arguments else ""
    if command in (
        "create",
        "export",
        "export-pkg",
        "download",
        "remove",
        "upload",
        "lock",
        "purifier",
        "editable",
        "workspace",
    ):
        raise ValueError(
            "this command changes the managed dependency cache; use conan-purifier lock, update or publish"
        )
    if command == "cache" and arguments[1:2] not in (["path"], ["check-integrity"]):
        raise ValueError("cache mutation/export is managed by conan-purifier")
    if command == "profile" and arguments[1:2] not in (["show"], ["list"], ["path"]):
        raise ValueError("the compiler SDK profile is supplied by conan-purifier")
    if command == "config" and arguments[1:2] not in (["home"], ["list"]):
        raise ValueError(
            "custom dependency-cache configuration is not supported in locked projects"
        )
    if command == "graph" and arguments[1:2] != ["info"]:
        raise ValueError(
            "only native graph info is currently supported in locked projects"
        )
    arguments, build_type, graph = native_arguments(arguments, native, build_type)
    checked(root, native, companion, build_type)
    output = prefetch(root, native, companion, build_type)
    identity = hashlib.sha256(
        (str(companion) + "\0" + str(output)).encode()
    ).hexdigest()[:24]
    state = root / ".conan-purifier/contexts" / identity
    home = state / "home"
    state.mkdir(parents=True, exist_ok=True)
    env = sdk_environment(home)
    with (state / "setup.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        marker = state / "environment"
        if not marker.exists() or marker.read_text() != str(output):
            if home.exists():
                shutil.rmtree(home)
            bootstrap(home)
            (home / "profiles").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output / "profile", home / "profiles/purifier")
            shutil.copyfile(output / "profile", home / "profiles/default")
            shutil.copyfile(output / "build-profile", home / "profiles/purifier-build")
            run(
                [CONAN, "purifier", "hydrate", "--environment", output],
                env=env,
                stdout=sys.stderr,
            )
            write(marker, str(output))
    env["CONAN_PURIFIER_PROVIDER"] = str(output)
    if graph:
        arguments += [
            "--no-remote",
            "--build=never",
            "--profile:host=purifier",
            "--profile:build=purifier-build",
            "--lockfile=" + str(output / "conan.lock"),
        ]
    os.execve(CONAN, [CONAN, *arguments], env)


def publish(root, native, companion, build_type, repository, tag):
    checked(root, native, companion, build_type)
    release = realize(root, native, companion, build_type, "release")
    gh = os.environ.get("CONAN_PURIFIER_GH", "gh")
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", tag):
        raise ValueError("publish requires OWNER/REPOSITORY and a release tag")
    present = (
        subprocess.run(
            [gh, "release", "view", tag, "--repo", repository],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )
    if present:
        run(
            [
                gh,
                "release",
                "upload",
                tag,
                "--repo",
                repository,
                release / "project.tar.gz",
                "--clobber",
            ]
        )
    else:
        run(
            [
                gh,
                "release",
                "create",
                tag,
                "--repo",
                repository,
                "--title",
                tag,
                "--notes",
                "Source release built by Nix.",
                release / "project.tar.gz",
            ]
        )


def project(arguments):
    candidate = Path.cwd()
    for value in arguments[1:]:
        path = Path(value)
        if path.is_file() and path.name in ("conanfile.py", "conanfile.txt"):
            candidate = path.resolve().parent
            break
        if path.is_dir() and any(
            (path / name).is_file() for name in ("conanfile.py", "conanfile.txt")
        ):
            candidate = path.resolve()
            break
    return next(
        (
            path
            for path in [candidate, *candidate.parents]
            if any(
                (path / name).is_file() for name in ("conanfile.py", "conanfile.txt")
            )
        ),
        candidate,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--lockfile", default=os.environ.get("CONAN_PURIFIER_LOCKFILE"))
    parser.add_argument(
        "--native-lock", "--conan-lock", "--tool-lockfile", dest="native_lock"
    )
    parser.add_argument(
        "--build-type",
        choices=["Release", "Debug"],
        default=os.environ.get("CONAN_PURIFIER_BUILD_TYPE", "Release"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("lock", "prefetch"):
        sub.add_parser(name)
    p = sub.add_parser("update")
    p.add_argument("names", nargs="*")
    p = sub.add_parser("native")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("publish")
    p.add_argument("--repo", required=True)
    p.add_argument("--tag", required=True)
    args = parser.parse_args()
    arguments = args.args if args.command == "native" else []
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    root = (
        Path(args.project).expanduser().resolve()
        if args.project
        else project(arguments)
    )
    if (
        any(
            (path / name).exists()
            for path in [root, *root.parents]
            for name in ("conanws.py", "conanws.yml")
        )
        and args.command != "native"
    ):
        raise ValueError(
            "Conan workspaces/editable dependency bindings are not supported yet"
        )
    selection = (
        args.native_lock
        or os.environ.get("CONAN_PURIFIER_NATIVE_LOCKFILE")
        or os.environ.get("CONAN_PURIFIER_TOOL_LOCKFILE")
    )
    native = (
        Path(selection).expanduser().resolve() if selection else root / "conan.lock"
    )
    companion = (
        Path(args.lockfile).expanduser().resolve()
        if args.lockfile
        else root / "conan-purifier.lock.json"
    )
    if args.command in ("lock", "update"):
        do_lock(
            root,
            native,
            companion,
            args.build_type,
            args.command == "update",
            names=getattr(args, "names", []),
        )
    elif args.command == "prefetch":
        print(prefetch(root, native, companion, args.build_type))
    elif args.command == "publish":
        publish(root, native, companion, args.build_type, args.repo, args.tag)
    else:
        if companion.exists() and any(
            (path / name).exists()
            for path in [root, *root.parents]
            for name in ("conanws.py", "conanws.yml")
        ):
            raise ValueError("Conan workspace bindings are not prepared by this lock")
        runtime(root, native, companion, arguments, args.build_type)
