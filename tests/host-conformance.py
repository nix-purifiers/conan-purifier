#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real native revisions, immutable payloads, ordinary builds and compiler profiles."""

import json, os, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    result = subprocess.run(list(map(str, args)), text=True, **kwargs)
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        result.check_returncode()
    return result


case = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="conan-conformance-") as temporary:
    temp = Path(temporary)
    cli = temp / "cli"
    sdk = temp / "sdk"
    run(["nix", "build", str(REPO) + "#cli", "--out-link", cli])
    run(["nix", "build", str(REPO) + "#native", "--out-link", sdk])
    helper = cli / "bin/conan-purifier"
    conan = cli / "bin/conan"
    ordinary = sdk / "bin/conan"
    root = temp / "project"
    root.mkdir()
    (root / "src").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONAN_PURIFIER_")}
    env["CONAN_HOME"] = str(temp / "ordinary-cache")

    def recipe(wide=False):
        fmt = "fmt/[>=11.1 <12]" if wide else "fmt/11.1.1"
        json_ref = "nlohmann_json/[>=3.11.2 <4]" if wide else "nlohmann_json/3.11.2"
        return (
            'from conan import ConanFile\nfrom conan.tools.cmake import CMake\nclass Consumer(ConanFile):\n    settings = "os", "arch", "compiler", "build_type"\n    requires = '
            + repr((fmt, json_ref))
            + '\n    generators = "CMakeDeps", "CMakeToolchain"\n    def build(self):\n        cmake = CMake(self)\n        cmake.configure()\n        cmake.build()\n'
        )

    (root / "conanfile.py").write_text(recipe())
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nproject(consumer CXX)\nset(CMAKE_EXPORT_COMPILE_COMMANDS ON)\nfind_package(fmt CONFIG REQUIRED)\nfind_package(nlohmann_json CONFIG REQUIRED)\nadd_executable(consumer src/main.cpp)\ntarget_link_libraries(consumer PRIVATE fmt::fmt nlohmann_json::nlohmann_json)\ninstall(TARGETS consumer RUNTIME DESTINATION bin)\nenable_testing()\nadd_test(NAME consumer COMMAND consumer)\n"
    )
    (root / "src/main.cpp").write_text(
        '#include <fmt/core.h>\n#include <nlohmann/json.hpp>\nint main(){nlohmann::json j={{"value",1}};fmt::print("locked Conan {}\\n",j["value"].get<int>());}\n'
    )
    decoy = temp / "decoy-cache"
    (root / ".conanrc").write_text("conan_home=" + str(decoy) + "\n")
    args = [helper, "--project", root]
    native = root / "conan.lock"
    companion = root / "conan-purifier.lock.json"
    run([*args, "lock"], env=env)
    (root / "conanfile.py").write_text(recipe(True))
    run([*args, "lock"], env=env)
    initial = companion.read_bytes()
    native_initial = native.read_bytes()

    def chosen(path=native):
        return {
            ref.split("/")[0]: ref.split("#")[0]
            for ref in json.loads(path.read_text())["requires"]
        }

    assert chosen() == {"fmt": "fmt/11.1.1", "nlohmann_json": "nlohmann_json/3.11.2"}, (
        chosen()
    )
    run([*args, "lock"], env=env)
    assert companion.read_bytes() == initial and native.read_bytes() == native_initial
    selected = companion
    selected_native = native
    build_type = "Release"
    if case == "update":
        run([*args, "update", "fmt"], env=env)
        named = chosen()
        assert (
            named["fmt"] != "fmt/11.1.1"
            and named["nlohmann_json"] == "nlohmann_json/3.11.2"
        ), named
        run([*args, "update"], env=env)
        assert chosen()["nlohmann_json"] != "nlohmann_json/3.11.2", chosen()
    elif case == "relock":
        damaged = json.loads(initial)
        damaged["contentHash"] = "bad"
        companion.write_text(json.dumps(damaged))
        run([*args, "lock"], env=env)
        assert native.read_bytes() == native_initial
        native.write_text("invalid native JSON")
        run([*args, "lock"], env=env)
        assert native.read_bytes() == native_initial
    elif case == "external":
        external = temp / "external"
        external.mkdir()
        selected = external / "companion.json"
        selected_native = external / "native.lock"
        args = [
            helper,
            "--project",
            root,
            "--lockfile",
            selected,
            "--tool-lockfile",
            selected_native,
        ]
        run([*args, "lock"], env=env)
        assert (
            companion.read_bytes() == initial and native.read_bytes() == native_initial
        )
        companion.unlink()
        native.write_text("intentionally invalid working native lock\n")
        env.update(
            CONAN_PURIFIER_LOCKFILE=str(selected),
            CONAN_PURIFIER_TOOL_LOCKFILE=str(selected_native),
        )
    elif case == "debug":
        args = [*args, "--build-type", "Debug"]
        run([*args, "lock"], env=env)
        build_type = "Debug"
        profiles = json.loads(companion.read_text())["profiles"]
        assert len(profiles) == 2, profiles.keys()
    elif case != "native":
        raise ValueError(case)
    environment = Path(
        run([*args, "prefetch"], env=env, capture_output=True).stdout.strip()
    )
    offline = {
        **env,
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
    }
    output = root / "build"
    options = ["-sbuild_type=Debug"] if build_type == "Debug" else []
    installed = json.loads(
        run(
            [
                conan,
                "install",
                ".",
                *options,
                "--output-folder",
                output,
                "-bmissing",
                "--format=json",
            ],
            cwd=root,
            env=offline,
            capture_output=True,
        ).stdout
    )
    nodes = [n for n in installed["graph"]["nodes"].values() if n.get("prev")]
    assert nodes and all(
        n["package_folder"].startswith(str(environment) + "/packages/") for n in nodes
    ), nodes
    run(
        [conan, "build", ".", *options, "--output-folder", output],
        cwd=root,
        env=offline,
    )
    assert (
        run([output / "consumer"], capture_output=True).stdout.strip()
        == "locked Conan 1"
    )
    editor = run(
        [
            cli / "bin/clangd",
            "--check=" + str(root / "src/main.cpp"),
            "--compile-commands-dir=" + str(output),
            "--header-insertion=never",
        ],
        cwd=root,
        env=offline,
        capture_output=True,
    )
    assert "0 errors" in editor.stderr, editor.stderr
    assert not decoy.exists() and not Path(env["CONAN_HOME"]).exists()
    if case == "external":
        assert native.read_text() == "intentionally invalid working native lock\n"
    if case == "native":
        home = Path(
            run(
                [conan, "config", "home"], cwd=root, env=offline, capture_output=True
            ).stdout.strip()
        )
        raw_env = {**env, "CONAN_HOME": str(home), "CONAN_PURIFIER_HOME": str(home)}
        json_node = next(n for n in nodes if n["ref"].startswith("nlohmann_json/"))
        reference = (
            json_node["ref"] + ":" + json_node["package_id"] + "#" + json_node["prev"]
        )
        backing = Path(
            run(
                [ordinary, "cache", "path", reference],
                cwd=root,
                env=raw_env,
                capture_output=True,
            ).stdout.strip()
        )
        assert str(backing).startswith(str(home)) and not backing.is_symlink(), backing
        (backing / "include/nlohmann/json.hpp").write_text(
            "invalid ordinary cached header\n"
        )
        run([conan, "cache", "check-integrity", "*"], cwd=root, env=offline)
        # Source edits must not change or rebuild the dependency environment.
        source = root / "src/main.cpp"
        source.write_text(source.read_text() + "// application edit\n")
        unchanged = Path(
            run([*args, "prefetch"], env=offline, capture_output=True).stdout.strip()
        )
        assert unchanged == environment, (environment, unchanged)
        run([conan, "build", ".", "--output-folder", output], cwd=root, env=offline)
        assert (
            run([output / "consumer"], capture_output=True).stdout.strip()
            == "locked Conan 1"
        )
        bypass = subprocess.run(
            [str(conan), "install", ".", "-pr:hcustom"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
        )
        assert bypass.returncode and "custom profiles" in bypass.stderr, bypass.stderr
        (root / "conanfile.py").write_text(recipe(True) + "# changed\n")
        stale = subprocess.run(
            [str(conan), "graph", "info", "."],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
        )
        assert stale.returncode and "manifests changed" in stale.stderr, stale.stderr
        (root / "conanfile.py").write_text(recipe(True))
        fake = temp / "gh"
        record = temp / "gh-calls.jsonl"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\na=sys.argv[1:]\np=Path("
            + repr(str(record))
            + ')\nwith p.open("a") as f:f.write(json.dumps(a)+"\\n")\nif a[:2]==["release","view"]:sys.exit(0 if len(p.read_text().splitlines())>2 else 1)\n'
        )
        fake.chmod(0o755)
        for _ in range(2):
            run(
                [*args, "publish", "--repo", "fixture/project", "--tag", "v0.1.0"],
                env={**env, "CONAN_PURIFIER_GH": str(fake)},
            )
        calls = [json.loads(line) for line in record.read_text().splitlines()]
        assert calls[1][:2] == ["release", "create"] and calls[3][:2] == [
            "release",
            "upload",
        ], calls
        archive = Path(next(a for a in calls[1] if a.endswith("/project.tar.gz")))
        with tarfile.open(archive) as release:
            assert {
                "./conanfile.py",
                "./conan.lock",
                "./conan-purifier.lock.json",
            } <= set(release.getnames())
        empty = temp / "unlocked"
        empty.mkdir()
        fallback = run([conan, "--version"], cwd=empty, env=env, capture_output=True)
        baseline = run([ordinary, "--version"], cwd=empty, env=env, capture_output=True)
        assert (
            fallback.stdout == baseline.stdout
            and "ordinary impure conan" in fallback.stderr
            and not (empty / "conan-purifier.lock.json").exists()
        )
    print("Conan native conformance passed:", case)
