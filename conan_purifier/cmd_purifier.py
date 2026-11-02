# SPDX-License-Identifier: MIT
"""Private native Conan commands used by the wrapper and Nix builder."""

import hashlib, json, os, shutil, sys
from pathlib import Path
import importlib.util

spec = importlib.util.spec_from_file_location(
    "_conan_purifier_support", Path(os.environ["CONAN_PURIFIER_HOOKS"]) / "support.py"
)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
support.configure()
from conan.cli.command import conan_command
from conan.api.model import PackagesList, RecipeReference, PkgReference
from conan.errors import ConanException


def ref_key(text):
    return hashlib.sha256(text.encode()).hexdigest()


@conan_command(group="Purifier")
def purifier(conan_api, parser, *args):
    """Prepare native dependency sources, cache snapshots and immutable payloads."""
    parser.add_argument(
        "kind",
        choices=["resolve", "build", "recipes", "packages", "hydrate", "validate"],
    )
    parser.add_argument("--lockfile")
    parser.add_argument("--lockfile-out")
    parser.add_argument("--graph")
    parser.add_argument("--file")
    parser.add_argument("--project")
    parser.add_argument("--profile")
    parser.add_argument("--environment")
    parser.add_argument("--update", nargs="*", default=None)
    args = parser.parse_args(*args)
    if args.kind in ("resolve", "build"):
        root = str(Path(args.project).resolve())
        recipe = conan_api.local.get_conanfile_path(root, root, py=None)
        native = conan_api.lockfile.get_lockfile(
            lockfile=args.lockfile or "",
            conanfile_path=recipe,
            cwd=root,
            partial=args.kind == "resolve",
        )
        update = None
        if args.update is not None:
            update = args.update or ["*"]
            if args.update and native:
                patterns = [name + "/*" for name in args.update]
                conan_api.lockfile.remove_lockfile(
                    native,
                    requires=patterns,
                    build_requires=patterns,
                    python_requires=patterns,
                    config_requires=patterns,
                )
            elif not args.update:
                native = None
        host = conan_api.profiles.get_profile(
            [args.profile], conf=["tools.build:download_source=True"], context="host"
        )
        build = conan_api.profiles.get_profile(
            [args.profile],
            settings=["build_type=Release"],
            conf=["tools.build:download_source=True"],
            context="build",
        )
        remotes = conan_api.remotes.list() if args.kind == "resolve" else []
        graph = conan_api.graph.load_graph_consumer(
            recipe, None, None, None, None, host, build, native, remotes, update
        )
        graph.report_graph_error()
        conan_api.graph.analyze_binaries(
            graph, ["*"], remotes, update=update, lockfile=native
        )
        if args.kind == "resolve":
            conan_api.install.install_sources(graph, remotes)
        else:
            conan_api.install.install_binaries(graph, remotes)
        native = conan_api.lockfile.update_lockfile(native, graph, clean=True)
        if args.lockfile_out:
            conan_api.lockfile.save_lockfile(native, args.lockfile_out)
        Path(args.graph).write_text(json.dumps({"graph": graph.serialize()}, indent=2))
        return
    if args.kind == "hydrate":
        environment = Path(args.environment)
        conan_api.cache.restore(str(environment / "runtime.tgz"))

        return
    selection = PackagesList()
    native = json.loads(Path(args.lockfile).read_text())
    for category in (
        "requires",
        "build_requires",
        "python_requires",
        "config_requires",
    ):
        for text in native.get(category, []):
            selection.add_ref(RecipeReference.loads(text.split("%")[0]))
    packages = {}
    if args.kind == "validate":
        from conan.internal.model.manifest import FileTreeManifest

        conan_api.cache.check_integrity(selection)
        for ref, _ in selection.items():
            manifest = FileTreeManifest.load(conan_api.cache.export_path(ref))
            if manifest.summary_hash != ref.revision:
                raise ConanException(
                    "Recipe snapshot does not match native revision: "
                    + ref.repr_notime()
                )
        return
    if args.kind == "packages":
        graph = json.loads(Path(args.graph).read_text())["graph"]["nodes"]
        for node in graph.values():
            if not node.get("prev") or not node.get("package_id"):
                continue
            ref = RecipeReference.loads(node["ref"].split("%")[0])
            selection.add_ref(ref)
            pref = PkgReference(ref, node["package_id"], node["prev"])
            selection.add_pref(pref, node.get("info"))
            packages[pref.repr_notime()] = pref
    conan_api.cache.save(selection, str(Path(args.file).resolve()), no_source=True)
    if args.kind == "packages":
        out = Path(args.file).resolve().parent
        links = {"recipes": [], "packages": []}
        for ref, _ in selection.items():
            text = ref.repr_notime()
            key = ref_key(text)
            record = {"ref": text, "export": "recipes/" + key + "/e"}
            shutil.copytree(
                conan_api.cache.export_path(ref), out / record["export"], symlinks=True
            )
            try:
                exported = Path(conan_api.cache.export_source_path(ref))
            except ConanException:
                exported = None
            if exported is not None and exported.is_dir():
                record["exportSource"] = "recipes/" + key + "/es"
                shutil.copytree(exported, out / record["exportSource"], symlinks=True)
            links["recipes"].append(record)
        for text, pref in sorted(packages.items()):
            relative = "packages/" + ref_key(text)
            shutil.copytree(
                conan_api.cache.package_path(pref), out / relative, symlinks=True
            )
            links["packages"].append({"ref": text, "package": relative})
        (out / "links.json").write_text(
            json.dumps(links, sort_keys=True, indent=2) + "\n"
        )
