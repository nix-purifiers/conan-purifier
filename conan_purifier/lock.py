# SPDX-License-Identifier: MIT
import base64, hashlib, io, json, re, tarfile
from pathlib import Path
from urllib.parse import urlsplit

FIX = "; run conan-purifier lock"
CATEGORIES = ("requires", "build_requires", "python_requires", "config_requires")
REF = r"[A-Za-z0-9_+.-]+/[A-Za-z0-9_+.-]+(?:@[A-Za-z0-9_+.-]+/[A-Za-z0-9_+.-]+)?#[0-9a-f]{32}"
SYSTEMS = {"x86_64-linux": "x86_64", "aarch64-linux": "armv8"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(data):
    result = {k: v for k, v in data.items() if k != "contentHash"}
    return {
        **result,
        "contentHash": hashlib.sha256(canonical(result).encode()).hexdigest(),
    }


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sri(data):
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


def inputs(root):
    return [
        {"path": name, "hash": digest(root / name) if (root / name).is_file() else None}
        for name in ("conanfile.py", "conanfile.txt", "conandata.yml")
    ]


def native(text):
    data = json.loads(text)
    if (
        not isinstance(data, dict)
        or data.get("version") != "0.5"
        or set(data) - {"version", *CATEGORIES, "alias", "overrides"}
    ):
        raise ValueError("unsupported native Conan lock format" + FIX)
    result = {"version": "0.5"}
    for category in CATEGORIES:
        values = data.get(category, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("native package-ID lock matrices are not supported" + FIX)
        cleaned = [v.split("%")[0] for v in values]
        if any(not re.fullmatch(REF, v) for v in cleaned):
            raise ValueError("native hash-mode recipe revisions are required" + FIX)
        result[category] = sorted(set(cleaned))
    for key in ("alias", "overrides"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError("invalid native Conan lock " + key + FIX)
            result[key] = data[key]
    return result


def native_text(data):
    return (
        json.dumps(
            native(json.dumps(data)), sort_keys=True, indent=2, ensure_ascii=False
        )
        + "\n"
    )


def refs(data):
    return sorted({ref for category in CATEGORIES for ref in data[category]})


def union(profiles, active):
    selected = native(profiles[active]["nativeLock"])
    result = {k: v for k, v in selected.items() if k not in CATEGORIES}
    for category in CATEGORIES:
        result[category] = sorted(
            {v for p in profiles.values() for v in native(p["nativeLock"])[category]}
        )
    return result


def profile_text(settings):
    return (
        "[settings]\n"
        + "".join(k + "=" + v + "\n" for k, v in sorted(settings.items()))
        + "\n[conf]\ntools.system.package_manager:mode=report\n"
    )


def relative(value):
    return (
        isinstance(value, str)
        and value
        and not value.startswith("/")
        and "\\" not in value
        and all(v not in ("", ".", "..") for v in value.split("/"))
    )


def archive_refs(blob):
    """Validate the native recipe-only cache snapshot before native extraction."""
    data = base64.b64decode(blob, validate=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        metadata = archive.extractfile("pkglist.json")
        if metadata is None:
            raise ValueError("native recipe snapshot has no package list")
        listing = json.load(metadata)
        expected = []
        folders = set()
        if not isinstance(listing, dict):
            raise ValueError("invalid native recipe snapshot list")
        for name, record in listing.items():
            if not isinstance(record, dict) or not isinstance(
                record.get("revisions"), dict
            ):
                raise ValueError("invalid native recipe snapshot record")
            for revision, revision_data in record["revisions"].items():
                reference = name + "#" + revision
                if (
                    not re.fullmatch(REF, reference)
                    or not isinstance(revision_data, dict)
                    or revision_data.get("packages")
                ):
                    raise ValueError(
                        "snapshot must contain recipes without host binaries"
                    )
                folder = revision_data.get("recipe_folder")
                expected_folder = (
                    name.split("/")[0][:5]
                    + hashlib.sha256(reference.encode()).hexdigest()[:13]
                )
                if folder != expected_folder:
                    raise ValueError("invalid native recipe cache path")
                folders.add(folder)
                expected.append(reference)
        for member in members:
            name = member.name.removeprefix("./").rstrip("/")
            if not relative(name):
                raise ValueError("unsafe recipe snapshot path")
            if name != "pkglist.json":
                parts = name.split("/")
                if parts[0] not in folders or (
                    len(parts) > 1 and parts[1] not in ("e", "es", "d")
                ):
                    raise ValueError("undeclared recipe snapshot payload")
            if member.issym() or member.islnk():
                if not relative(member.linkname):
                    raise ValueError("unsafe recipe snapshot link")
            elif not member.isfile() and not member.isdir():
                raise ValueError("unsupported recipe snapshot member")
    return sorted(expected), data


def problems(root, path, data, system=None, build_type="Release"):
    try:
        if not isinstance(data, dict) or data.get("version") != 1:
            return ["unsupported companion format" + FIX]
        if data.get("contentHash") != seal(data)["contentHash"]:
            return ["companion contentHash mismatch" + FIX]
        if data.get("inputs") != inputs(root):
            return ["project manifests changed" + FIX]
        manifests = data.get("manifests")
        if not isinstance(manifests, dict) or set(manifests) != {
            item["path"] for item in data["inputs"] if item["hash"] is not None
        }:
            raise ValueError("missing native recipe snapshots")
        if any(
            not isinstance(text, str)
            or hashlib.sha256(text.encode()).hexdigest()
            != next(item["hash"] for item in data["inputs"] if item["path"] == name)
            for name, text in manifests.items()
        ):
            raise ValueError("native recipe snapshot changed")
        if (
            not (root / "conanfile.py").is_file()
            and not (root / "conanfile.txt").is_file()
        ):
            return ["missing native Conan recipe" + FIX]
        global_native = native(path.read_text())
        if (
            data.get("nativeHash") != digest(path)
            or data.get("nativeLock") != path.read_text()
        ):
            return ["native Conan lock changed" + FIX]
        profiles = data.get("profiles")
        active = data.get("activeProfile")
        if not isinstance(profiles, dict) or not profiles or active not in profiles:
            raise ValueError("missing native SDK profiles")
        if global_native != union(profiles, active):
            raise ValueError("native Conan pins do not match SDK profiles")
        for key, profile in profiles.items():
            if (
                not isinstance(profile, dict)
                or key != profile.get("system", "") + ":" + profile.get("buildType", "")
                or profile.get("system") not in SYSTEMS
                or profile.get("buildType") not in ("Release", "Debug")
            ):
                raise ValueError("invalid native SDK profile key")
            settings = profile["settings"]
            if (
                not isinstance(settings, dict)
                or set(settings)
                != {
                    "arch",
                    "build_type",
                    "compiler",
                    "compiler.cppstd",
                    "compiler.libcxx",
                    "compiler.version",
                    "os",
                }
                or any(not isinstance(v, str) for v in settings.values())
            ):
                raise ValueError("invalid detected compiler settings")
            if (
                settings["arch"] != SYSTEMS[profile["system"]]
                or settings["os"] != "Linux"
                or settings["build_type"] != profile["buildType"]
                or settings["compiler"] != "gcc"
                or settings["compiler.libcxx"] != "libstdc++11"
                or not re.fullmatch("[0-9]+", settings["compiler.version"])
            ):
                raise ValueError("unsupported compiler SDK ABI")
            if profile["profile"] != profile_text(settings) or profile.get(
                "buildProfile"
            ) != profile_text({**settings, "build_type": "Release"}):
                raise ValueError("native SDK profile changed")
            selected = native(profile["nativeLock"])
            if profile.get("recipeRefs") != refs(selected):
                raise ValueError("native recipe references changed")
            actual, archive = archive_refs(profile["recipeArchive"])
            if actual != profile["recipeRefs"] or profile["recipeArchiveHash"] != sri(
                archive
            ):
                raise ValueError("native recipe snapshot changed")
            entries = profile["entries"]
            if not isinstance(entries, list):
                raise ValueError("missing native source downloads")
            paths = set()
            for entry in entries:
                prefix = (
                    "profiles/"
                    + profile["system"]
                    + "/"
                    + profile["buildType"]
                    + "/assets/"
                )
                if (
                    not isinstance(entry, dict)
                    or not relative(entry.get("path"))
                    or not entry["path"].startswith(prefix)
                    or entry["path"] in paths
                ):
                    raise ValueError("invalid native source download path")
                paths.add(entry["path"])
                if not re.fullmatch(
                    "sha256-[A-Za-z0-9+/]{43}=", entry.get("hash", "")
                ) or not isinstance(entry.get("mirrors"), list):
                    raise ValueError("invalid source archive checksum")
                for url in [entry.get("url"), *entry["mirrors"]]:
                    if not isinstance(url, str) or re.search(r"\s", url):
                        raise ValueError("invalid source archive URL")
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in ("http", "https")
                        or not parsed.netloc
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError(
                            "only public HTTP(S) source archives are supported"
                        )
            if (
                not isinstance(profile.get("toolVersions"), dict)
                or not isinstance(profile["toolVersions"].get("conan"), str)
                or not isinstance(profile["toolVersions"].get("compiler"), str)
            ):
                raise ValueError("missing SDK versions")
        if system and system + ":" + build_type not in profiles:
            raise ValueError(
                "missing "
                + system
                + " "
                + build_type
                + " profile; run conan-purifier --build-type "
                + build_type
                + " lock on this platform"
            )
        return []
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        tarfile.TarError,
    ) as error:
        return [str(error) + FIX]
