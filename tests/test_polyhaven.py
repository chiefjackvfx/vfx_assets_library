import hashlib
import json
from pathlib import Path

from PyQt6.QtGui import QColor, QImage

from universal_asset_library.importer import scan_hdri_folder, scan_model_folder, scan_texture_folder
from universal_asset_library.library import LibraryRepository
from universal_asset_library.library.polyhaven import (
    PolyHavenClient,
    PolyHavenError,
    PolyHavenRemoteFile,
    build_download_plan,
    options_from_catalog,
    resolve_polyhaven_slug,
)


def _image(path: Path, color: str = "#777777") -> None:
    value = QImage(32, 32, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    assert value.save(str(path))


def _record(name: str, payload: bytes) -> dict:
    return {
        "url": f"https://dl.polyhaven.org/files/{name}",
        "size": len(payload),
        "md5": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
    }


class FakeClient:
    def __init__(self, catalog: dict, payloads: dict[str, bytes]) -> None:
        self.catalog = catalog
        self.payloads = payloads

    def fetch_catalog(self, _slug: str, _expected_type: int) -> dict:
        return self.catalog

    def download(self, remote: PolyHavenRemoteFile, destination: Path, *, progress=None, cancel=None, retries=2):
        if cancel:
            cancel()
        payload = self.payloads[Path(remote.source_path).name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        if progress:
            progress(len(payload))
        return len(payload), hashlib.sha256(payload).hexdigest()


def _texture_asset(tmp_path: Path):
    source = tmp_path / "marble_tiles"
    source.mkdir()
    _image(source / "marble_tiles_diff_1k.jpg", "#76543a")
    info = {
        "name": "Marble Tiles",
        "authors": {"Tester": "https://example.invalid"},
        "max_resolution": [4096, 4096],
        "thumbnail_url": "https://cdn.polyhaven.com/asset_img/thumbs/marble_tiles.png",
        "files": {"Diffuse": {"1k": {"jpg": _record("marble_tiles_diff_1k.jpg", b"old")}}},
    }
    (source / "info.json").write_text(json.dumps(info), encoding="utf-8")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    return repository, repository.import_materials([candidate]).imported[0]


def test_texture_maps_and_materialx_are_published_and_cataloged(tmp_path) -> None:
    repository, asset = _texture_asset(tmp_path)
    base = b"base-2k"
    rough = b"rough-2k"
    mtlx = b"<materialx/>"
    catalog = {
        "Diffuse": {"2k": {"jpg": _record("marble_tiles_diff_2k.jpg", base)}},
        "Rough": {"2k": {"exr": _record("marble_tiles_rough_2k.exr", rough)}},
        "mtlx": {"2k": {"mtlx": {
            **_record("marble_tiles_2k.mtlx", mtlx),
            "include": {"textures/marble_tiles_diff_2k.jpg": _record("marble_tiles_diff_2k.jpg", base)},
        }}},
    }
    client = FakeClient(catalog, {
        "marble_tiles_diff_2k.jpg": base,
        "marble_tiles_rough_2k.exr": rough,
        "marble_tiles_2k.mtlx": mtlx,
    })
    options = repository.polyhaven_options(asset.id, client=client)
    assert options.map_resolutions == ("2K",)
    assert options.materialx_resolutions == ("2K",)

    map_plan = repository.prepare_polyhaven_download(asset.id, "maps", "2K", options=options)
    updated = repository.install_polyhaven_download(map_plan, client=client).asset
    assert set(updated.resolutions["2K"].maps) == {"Base Color", "Roughness"}
    assert (updated.asset_dir / "maps" / "2K" / "Marble_Tiles_2K_BaseColor.jpg").read_bytes() == base

    options = repository.polyhaven_options(asset.id, client=client)
    package_plan = repository.prepare_polyhaven_download(asset.id, "materialx", "2K", options=options)
    result = repository.install_polyhaven_download(package_plan, client=client)
    package = result.asset.provider_packages[0]
    assert package.kind == "materialx"
    assert (result.asset.asset_dir / package.entry_path).read_bytes() == mtlx
    assert (result.asset.asset_dir / "packages/materialx/2K/textures/marble_tiles_diff_2k.jpg").read_bytes() == base
    options = repository.polyhaven_options(asset.id, client=client)
    repeat_plan = repository.prepare_polyhaven_download(asset.id, "materialx", "2K", options=options)
    assert repository.install_polyhaven_download(repeat_plan, client=client).skipped


def test_model_usd_resolutions_coexist_and_latest_is_preferred(tmp_path) -> None:
    source = tmp_path / "clock"
    source.mkdir()
    (source / "clock.blend").write_bytes(b"blend")
    candidate = scan_model_folder(source).materials[0]
    candidate.provider = "Poly Haven"
    candidate.provider_id = "mantel_clock_01"
    candidate.name = "Mantel Clock 01"
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_models([candidate]).imported[0]
    usd1, usd2, tex1, tex2 = b"usd-1", b"usd-2", b"tex-1", b"tex-2"
    catalog = {"usd": {
        "1k": {"usd": {**_record("mantel_clock_01_1k.usdc", usd1), "include": {"textures/diff_1k.jpg": _record("diff_1k.jpg", tex1)}}},
        "2k": {"usd": {**_record("mantel_clock_01_2k.usdc", usd2), "include": {"textures/diff_2k.jpg": _record("diff_2k.jpg", tex2)}}},
    }}
    client = FakeClient(catalog, {
        "mantel_clock_01_1k.usdc": usd1, "mantel_clock_01_2k.usdc": usd2,
        "diff_1k.jpg": tex1, "diff_2k.jpg": tex2,
    })
    options = repository.polyhaven_options(asset.id, client=client)
    for label in ("1K", "2K"):
        plan = repository.prepare_polyhaven_download(asset.id, "usd", label, options=options)
        asset = repository.install_polyhaven_download(plan, client=client).asset
        options = repository.polyhaven_options(asset.id, client=client)
    usd_files = [item for item in asset.model_files if item.file_format == "USDC"]
    assert {item.resolution for item in usd_files} == {"1K", "2K"}
    assert asset.preferred_model and asset.preferred_model.resolution == "2K"
    assert (asset.asset_dir / "maps/1K/Mantel_Clock_01_1K_BaseColor.jpg").read_bytes() == tex1
    assert (asset.asset_dir / "maps/2K/Mantel_Clock_01_2K_BaseColor.jpg").read_bytes() == tex2
    assert (asset.asset_dir / "usd/textures/diff_1k.jpg").read_bytes() == tex1
    assert (asset.asset_dir / "usd/textures/diff_2k.jpg").read_bytes() == tex2
    assert not (asset.asset_dir / "packages/usd").exists()
    assert {path.parent.name for path in (asset.asset_dir / "usd").glob("*.usdc")} == {"usd"}
    assert set(asset.texture_sets["Default"].resolutions) == {"1K", "2K"}

    # Update/Fix also converts packages downloaded by the previous layout.
    manifest_path = asset.asset_dir / "asset.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = next(item for item in document["provider_packages"] if item["resolution"] == "1K")
    entry_record = next(item for item in package["files"] if item["role"] == "entry")
    dependency_record = next(item for item in package["files"] if item["role"] == "dependency")
    old_entry = "packages/usd/1K/mantel_clock_01_1k.usdc"
    old_dependency = "packages/usd/1K/textures/diff_1k.jpg"
    (asset.asset_dir / old_entry).parent.mkdir(parents=True)
    (asset.asset_dir / entry_record["path"]).replace(asset.asset_dir / old_entry)
    (asset.asset_dir / dependency_record["reference_path"]).unlink()
    (asset.asset_dir / old_dependency).parent.mkdir(parents=True)
    (asset.asset_dir / dependency_record["path"]).replace(asset.asset_dir / old_dependency)
    package["entry_path"] = old_entry
    entry_record["path"] = old_entry
    dependency_record["path"] = old_dependency
    dependency_record.pop("reference_path")
    next(item for item in document["model_files"] if item.get("resolution") == "1K")["path"] = old_entry
    del document["texture_sets"]["Default"]["resolutions"]["1K"]
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    assert repository.library_update_count() == 1
    repaired = repository.update_library()
    assert len(repaired.updated) == 1
    migrated = repository.list_model_assets()[0]
    assert (migrated.asset_dir / "maps/1K/Mantel_Clock_01_1K_BaseColor.jpg").is_file()
    assert (migrated.asset_dir / "usd/textures/diff_1k.jpg").is_file()
    assert not (migrated.asset_dir / "packages/usd").exists()


def test_hdri_download_adds_hdr_and_exr_without_replacing_local_variants(tmp_path) -> None:
    source = tmp_path / "studio_small_03"
    source.mkdir()
    (source / "studio_small_03_1k.hdr").write_bytes(
        b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 512 +X 1024\nfixture"
    )
    info = {
        "name": "Studio Small 03",
        "authors": {"Tester": "CC0"},
        "max_resolution": [4096, 2048],
        "files": {"hdri": {"1k": {"hdr": _record("studio_small_03_1k.hdr", b"local")}}},
    }
    (source / "info.json").write_text(json.dumps(info), encoding="utf-8")
    candidate = scan_hdri_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    hdr, exr = b"remote-hdr-4k", b"remote-exr-4k"
    catalog = {"hdri": {"4k": {
        "hdr": _record("studio_small_03_4k.hdr", hdr),
        "exr": _record("studio_small_03_4k.exr", exr),
    }}}
    client = FakeClient(catalog, {
        "studio_small_03_4k.hdr": hdr,
        "studio_small_03_4k.exr": exr,
    })
    options = repository.polyhaven_options(asset.id, client=client)
    assert options.hdri_resolutions == ("4K",)
    plan = repository.prepare_polyhaven_download(asset.id, "hdri", "4K", options=options)
    result = repository.install_polyhaven_download(plan, client=client)
    assert set(result.asset.resolutions) == {"1K", "4K"}
    files = result.asset.resolutions["4K"].files
    assert {item.file_format for item in files} == {"HDR", "EXR"}
    assert next(item for item in files if item.preferred).file_format == "EXR"
    assert (asset.asset_dir / "maps/polyhaven/4K/studio_small_03_4k.hdr").read_bytes() == hdr
    assert (asset.asset_dir / "maps/polyhaven/4K/studio_small_03_4k.exr").read_bytes() == exr
    assert (asset.asset_dir / "maps/studio_small_03_1k.hdr").is_file()


def test_slug_recovery_and_unsafe_package_paths() -> None:
    assert resolve_polyhaven_slug("", [{
        "thumbnail_url": "https://cdn.polyhaven.com/asset_img/thumbs/marble_tiles.png"
    }]) == "marble_tiles"
    catalog = {"usd": {"1k": {"usd": {
        **_record("asset.usdc", b"usd"),
        "include": {"../escape.jpg": _record("escape.jpg", b"bad")},
    }}}}
    options = options_from_catalog("asset", catalog, "model")
    try:
        build_download_plan(
            asset_id="id", asset_type="model", slug="asset", kind="usd", resolution="1K",
            catalog=options.catalog, manifest_updated_at="", manifest_fingerprint="",
        )
    except Exception as error:
        assert "Unsafe" in str(error)
    else:
        raise AssertionError("Unsafe dependency path was accepted")


def test_client_sends_identity_and_rejects_untrusted_download_host() -> None:
    class Response:
        def __init__(self, url: str, document: dict) -> None:
            self.url = url
            self.payload = json.dumps(document).encode()

        def geturl(self):
            return self.url

        def read(self, _limit=-1):
            return self.payload

        def close(self):
            pass

    client = PolyHavenClient(api_root="https://api.test.invalid")
    responses = {
        "https://api.test.invalid/info/asset": {"type": 1},
        "https://api.test.invalid/files/asset": {"Diffuse": {}},
    }
    client._open = lambda url: Response(url, responses[url])
    assert client.fetch_catalog("asset", 1) == {"Diffuse": {}}
    assert "ShotBoxAssets" in client.user_agent
    remote = PolyHavenRemoteFile("bad.jpg", "https://evil.invalid/bad.jpg", 1, "")
    try:
        client.download(remote, Path("/tmp/never-written"))
    except PolyHavenError as error:
        assert "untrusted" in str(error)
    else:
        raise AssertionError("Untrusted download host was accepted")
