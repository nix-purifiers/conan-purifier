import base64, hashlib, json, os, shutil, sys
from pathlib import Path
from urllib.parse import urlsplit


def configure():
    from conan.internal.rest.caching_file_downloader import SourcesCachingDownloader
    from conan.internal.rest.file_downloader import FileDownloader
    from conan.errors import ConanException

    original = SourcesCachingDownloader.download

    def download(
        self,
        urls,
        path,
        retry,
        retry_wait,
        verify_ssl,
        auth,
        headers,
        md5,
        sha1,
        sha256,
    ):
        choices = list(urls) if isinstance(urls, (list, tuple)) else [urls]
        assets = os.environ.get("CONAN_PURIFIER_ASSETS")
        if assets:
            mapping = json.loads(Path(assets).read_text())
            selected = next((mapping[u] for u in choices if u in mapping), None)
            if selected is None:
                raise ConanException("Source URL is not locked: " + str(choices))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(selected, path)
            FileDownloader.check_checksum(path, md5, sha1, sha256)
        else:
            original(
                self,
                urls,
                path,
                retry,
                retry_wait,
                verify_ssl,
                auth,
                headers,
                md5,
                sha1,
                sha256,
            )
        capture = os.environ.get("CONAN_PURIFIER_CAPTURE")
        if capture:
            for url in choices:
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in ("http", "https")
                    or parsed.username
                    or parsed.password
                ):
                    raise ConanException(
                        "Only public HTTP(S) source archives are supported"
                    )
            data = Path(path).read_bytes()
            digest = (
                "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()
            )
            with Path(capture).open("a") as stream:
                stream.write(json.dumps({"urls": choices, "hash": digest}) + "\n")

    SourcesCachingDownloader.download = download
    provider = os.environ.get("CONAN_PURIFIER_PROVIDER")
    if provider:
        root = Path(provider)
        links = json.loads((root / "links.json").read_text())
        recipes = {r["ref"]: r for r in links["recipes"]}
        packages = {p["ref"]: p for p in links["packages"]}
        from conan.internal.cache.conan_reference_layout import (
            RecipeLayout,
            PackageLayout,
        )

        export = RecipeLayout.export
        export_sources = RecipeLayout.export_sources
        package = PackageLayout.package

        def locked_export(self):
            selected = recipes.get(self.reference.repr_notime())
            return str(root / selected["export"]) if selected else export(self)

        def locked_export_sources(self):
            selected = recipes.get(self.reference.repr_notime())
            return (
                str(
                    root
                    / (
                        selected.get("exportSource")
                        or str(Path(selected["export"]).parent / "es")
                    )
                )
                if selected
                else export_sources(self)
            )

        def locked_package(self):
            selected = packages.get(self.reference.repr_notime())
            return str(root / selected["package"]) if selected else package(self)

        RecipeLayout.export = locked_export
        RecipeLayout.export_sources = locked_export_sources
        PackageLayout.package = locked_package
    if os.environ.get("CONAN_PURIFIER_REPRODUCIBLE") != "1":
        return
    import conan.internal.util.dates as dates

    functions = {dates.timestamp_now, dates.revision_timestamp_now}

    def epoch():
        return 1

    for module in list(sys.modules.values()):
        if module is None or not str(getattr(module, "__name__", "")).startswith(
            "conan."
        ):
            continue
        for name in ("timestamp_now", "revision_timestamp_now"):
            value = getattr(module, name, None)
            if callable(value) and value in functions:
                setattr(module, name, epoch)
    from conan.internal.model.manifest import FileTreeManifest

    create = FileTreeManifest.create.__func__

    def fixed_manifest(cls, *args, **kwargs):
        result = create(cls, *args, **kwargs)
        result.time = 1
        return result

    FileTreeManifest.create = classmethod(fixed_manifest)
    from conan.internal.cache.cache import PkgCache
    from conan.internal.cache.conan_reference_layout import PackageLayout

    def build_layout(self, pref):
        name = pref.ref.name[:5] + self._short_hash_path(
            pref.repr_notime() + "purifier"
        )
        relative = os.path.join("b", name)
        self._create_path(relative)
        return PackageLayout(pref, os.path.join(self._base_folder, relative))

    PkgCache.create_build_pkg_layout = build_layout
    import conan.internal.api.uploader as uploader

    gzip_open = uploader.gzopen_without_timestamps

    def reproducible_tar(*args, **kwargs):
        tar = gzip_open(*args, **kwargs)
        add = tar.add

        def normalized(info):
            info.mtime = 1
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.pax_headers = {
                k: v
                for k, v in info.pax_headers.items()
                if k not in ("mtime", "ctime", "atime")
            }
            return info

        def add_file(name, arcname=None, recursive=True, *, filter=None):
            def both(info):
                if filter:
                    info = filter(info)
                return normalized(info) if info is not None else None

            return add(name, arcname, recursive=recursive, filter=both)

        tar.add = add_file
        return tar

    uploader.gzopen_without_timestamps = reproducible_tar
