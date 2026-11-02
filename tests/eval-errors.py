#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject invalid declarations and typed source/profile metadata before fetching."""

import hashlib, json, shutil, subprocess, tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]


def seal(data):
    data.pop("contentHash", None)
    data["contentHash"] = hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return data


with tempfile.TemporaryDirectory(prefix="conan-eval-") as temporary:
    root = Path(temporary) / "source"
    shutil.copytree(
        repo / "examples/minimal",
        root,
        ignore=shutil.ignore_patterns(
            ".conan-purifier",
            "build",
            "build-debug",
            "CMakeUserPresets.json",
            "*.log",
            "install.json",
        ),
    )
    path = root / "conan-purifier.lock.json"
    original = path.read_text()
    native = root / "conan.lock"
    native_original = native.read_text()
    expression = (
        "let f=builtins.getFlake "
        + json.dumps(str(repo))
        + "; r=f.lib.conan-purifier.readLock {src=/. + "
        + json.dumps(str(root))
        + ";}; in {inherit (r) problems outputs entries;}"
    )

    def evaluate():
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            text=True,
            capture_output=True,
        )
        result.check_returncode()
        return json.loads(result.stdout)

    assert evaluate()["problems"] == []
    for mutation in (
        "seal",
        "hash",
        "path",
        "abi",
        "snapshot",
        "pins",
        "native",
        "missing",
        "manifest",
    ):
        path.write_text(original)
        native.write_text(native_original)
        data = json.loads(original)
        profile = next(iter(data["profiles"].values()))
        if mutation == "seal":
            data["contentHash"] = "bad"
        elif mutation == "hash":
            profile["entries"][0]["hash"] = "bad"
            seal(data)
        elif mutation == "path":
            profile["entries"][0]["path"] = "../outside"
            seal(data)
        elif mutation == "abi":
            profile["settings"]["arch"] = "wrong"
            seal(data)
        elif mutation == "snapshot":
            profile["recipeArchive"] = "bad"
            seal(data)
        elif mutation == "pins":
            profile["recipeRefs"] = []
            seal(data)
        if mutation in ("seal", "hash", "path", "abi", "snapshot", "pins"):
            path.write_text(json.dumps(data))
        elif mutation == "native":
            native.write_text(native_original + "# changed\n")
        elif mutation == "missing":
            native.unlink()
        else:
            (root / "conanfile.py").write_text(
                (root / "conanfile.py").read_text() + "# changed\n"
            )
        result = evaluate()
        assert (
            result["problems"] and result["outputs"] == {} and result["entries"] == []
        ), (mutation, result)
    print("Pure Conan lock/profile rejection passed")
