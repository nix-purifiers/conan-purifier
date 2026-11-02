# SPDX-License-Identifier: MIT
"""Build dependencies with Conan's native engine using declared source archives."""

import base64, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
import lock

metadata = json.loads(Path(sys.argv[1]).read_text())
sources = Path(sys.argv[2])
out = Path(sys.argv[3])
conan = sys.argv[4]
profile = metadata["profile"]
home = Path(os.environ["CONAN_PURIFIER_HOME"])
home.mkdir(parents=True, exist_ok=True)
commands = home / "extensions/commands"
commands.mkdir(parents=True, exist_ok=True)
shutil.copyfile(Path(__file__).parent / "cmd_purifier.py", commands / "cmd_purifier.py")
selected = lock.native(profile["nativeLock"])
refs, payload = lock.archive_refs(profile["recipeArchive"])
if refs != profile["recipeRefs"] or lock.sri(payload) != profile["recipeArchiveHash"]:
    raise ValueError("native recipe snapshot does not match sealed pins")
project = Path.cwd() / "consumer"
project.mkdir()
for name, text in metadata["manifests"].items():
    expected = next(item["hash"] for item in metadata["inputs"] if item["path"] == name)
    if hashlib.sha256(text.encode()).hexdigest() != expected:
        raise ValueError("native consumer snapshot changed")
    (project / name).write_bytes(text.encode())
profile_path = home / "profiles/purifier"
profile_path.parent.mkdir(parents=True, exist_ok=True)
profile_path.write_text(profile["profile"])
(profile_path.parent / "purifier-build").write_text(profile["buildProfile"])
native = Path.cwd() / "conan.lock"
native.write_text(profile["nativeLock"])
archive = Path.cwd() / "recipes.tgz"
archive.write_bytes(payload)
assets = {
    url: str(sources / entry["path"])
    for entry in profile["entries"]
    for url in [entry["url"], *entry["mirrors"]]
}
asset_file = Path.cwd() / "assets.json"
asset_file.write_text(json.dumps(assets))
os.environ["CONAN_PURIFIER_ASSETS"] = str(asset_file)


def run(args):
    subprocess.run(list(map(str, args)), check=True, cwd=project)


run([conan, "cache", "restore", archive])
run([conan, "purifier", "validate", "--lockfile", native])
graph = Path.cwd() / "graph.json"
run(
    [
        conan,
        "purifier",
        "build",
        "--project",
        project,
        "--profile",
        "purifier",
        "--lockfile",
        native,
        "--graph",
        graph,
    ]
)
out.mkdir(exist_ok=True)
run(
    [
        conan,
        "purifier",
        "packages",
        "--lockfile",
        native,
        "--graph",
        graph,
        "--file",
        out / "runtime.tgz",
    ]
)
(out / "profile").write_text(profile["profile"])
(out / "build-profile").write_text(profile["buildProfile"])
(out / "conan.lock").write_text(profile["nativeLock"])
