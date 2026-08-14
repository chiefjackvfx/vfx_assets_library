from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from PyQt6 import sip

from universal_asset_library.domain import (
    LibraryExtraFile,
    LibraryHdriAsset,
    LibraryHdriFile,
    LibraryHdriVariant,
    LibraryMap,
    LibraryModelAsset,
    LibraryModelFile,
    LibraryModelTextureSet,
    LibraryProviderPackage,
    LibraryProviderPackageFile,
    LibraryResolution,
    LibraryStockAsset,
    LibraryStockMediaInfo,
    LibraryTextureAsset,
    LibraryUsdDerivative,
)
from universal_asset_library.library import (
    CATALOG_SCHEMA_VERSION,
    CatalogError,
    CatalogIndex,
    CatalogRecord,
    decode_asset,
    encode_asset,
    CatalogRefreshWorker,
    CancelToken,
    refresh_catalog_section,
)


def assets(tmp_path: Path):
    root = tmp_path / "library"
    texture_dir = root / "textures" / "stone"
    common_map = LibraryMap(
        "Base Color", "maps/base.jpg", "JPEG", 12, "map-hash",
        bit_depth=8, color_space="sRGB", packed_channels={"R": "color"},
        preferred=True, material="Stone", lod="LOD0",
    )
    resolution = LibraryResolution("1K", 1024, 1024, {"Base Color": (common_map,)})
    extra = LibraryExtraFile("extras/license.txt", "license.txt", 3, "extra-hash", "TXT")
    package_file = LibraryProviderPackageFile(
        "packages/source.zip", "archive", 30, "package-hash", "md5", "source.zip"
    )
    package = LibraryProviderPackage(
        "source", "1K", "packages/source.zip", (package_file,), "2026-01-01"
    )
    texture = LibraryTextureAsset(
        "texture", "Stone", "Rock", ("rough",), "Description", "Artist", "2 m",
        "Provider", "provider-texture", texture_dir, {"1K": resolution},
        texture_dir / "thumb.jpg", texture_dir / "hero.jpg", "fingerprint",
        "2026-01-01", 100, ("#111111", "#222222"), (extra,), "texture_set",
        ("metadata/source.json",), (package,),
    )
    atlas = replace(
        texture,
        id="atlas",
        name="Leaves",
        asset_dir=root / "atlases" / "leaves",
        thumbnail_path=None,
        hero_path=None,
        asset_type="atlas",
    )
    hdri_dir = root / "hdris" / "studio"
    hdri = LibraryHdriAsset(
        "hdri", "Studio", "Studio", ("soft",), "Description", "Artist",
        "Provider", "provider-hdri", hdri_dir,
        {"4K": LibraryHdriVariant(
            "4K", 4096, 2048,
            (LibraryHdriFile("files/studio.exr", "EXR", 200, "hdri-hash", True),),
        )},
        hdri_dir / "thumb.jpg", hdri_dir / "hero.jpg", "fingerprint",
        "2026-01-01", 200, (extra,), ("#333333", "#444444"), "hdri", "",
        ("metadata/source.json",), {"engine": "Cycles", "samples": [1, 2]}, (package,),
    )
    stock_dir = root / "stock" / "smoke"
    stock = LibraryStockAsset(
        "stock", "Smoke", "Smoke", ("wispy",), "Description", "Artist",
        "Provider", "provider-stock", stock_dir, stock_dir / "source.mov",
        "source.mov", "MOV", 300, "stock-hash",
        LibraryStockMediaInfo("mov", "prores", "4444", "yuva", 1920, 1080, 24.0, 2.5, 60, False, "yes"),
        stock_dir / "preview.mp4", stock_dir / "thumb.jpg", "generated", "proxy",
        1.25, "fingerprint", "2026-01-01", 350, (extra,),
        ("#555555", "#666666"), "stock", "", stock_dir / "thumb.jpg",
        ("metadata/source.json",), (package,),
    )
    model_dir = root / "models" / "chair"
    dependency = LibraryExtraFile("textures/base.jpg", "base.jpg", 10, "dep-hash", "JPEG")
    model_file = LibraryModelFile(
        "models/chair.fbx", "chair.fbx", "FBX", "render", "LOD0", "body",
        1000, True, 500, "model-hash", "2K", "manual", "2026-01-01", True,
        {"valid": True, "messages": ["ok"]}, (dependency,),
    )
    usd = LibraryUsdDerivative(
        "models/chair.usdc", "models/chair.fbx", "model-hash", "-Z", "Y",
        "4.3", "2026-01-01", (dependency,), ("clean",),
    )
    model = LibraryModelAsset(
        "model", "Chair", "Furniture", ("wood",), "Description", "Artist", "1 m",
        "Provider", "provider-model", model_dir, (model_file,),
        {"Chair": LibraryModelTextureSet("Chair", {"1K": resolution})},
        model_dir / "thumb.jpg", model_dir / "hero.jpg", "fingerprint",
        "2026-01-01", 600, (1.0, 2.0, 3.0), 1000, (extra,),
        ("metadata/source.json",), (("ignored.tmp", "unsupported"),), (package,),
        usd, ("#777777", "#888888"), "model",
    )
    return texture, atlas, hdri, model, stock


@pytest.mark.parametrize("index", range(5))
def test_asset_safe_json_round_trip(tmp_path, index: int) -> None:
    asset = replace(assets(tmp_path)[index], rating=index + 1)
    payload = encode_asset(asset)
    assert decode_asset(payload) == asset
    assert decode_asset(payload).rating == index + 1
    assert "pickle" not in payload.casefold()


def test_decoder_rejects_unknown_or_malformed_types() -> None:
    with pytest.raises(CatalogError):
        decode_asset('{"$type":"dataclass","name":"Exploit","fields":{}}')
    with pytest.raises(CatalogError):
        decode_asset('{"$type":"path","value":3}')


def test_catalog_section_replace_upsert_remove_and_reopen(tmp_path) -> None:
    texture, atlas, *_ = assets(tmp_path)
    database = tmp_path / "cache" / "catalog.sqlite3"
    index = CatalogIndex(database, "library-key")
    texture_manifest = texture.asset_dir / "asset.json"
    atlas_manifest = atlas.asset_dir / "asset.json"
    texture_record = CatalogRecord(texture, texture_manifest, 20, 100)
    atlas_record = CatalogRecord(atlas, atlas_manifest, 21, 101)

    index.replace_section("texture_set", [texture_record])
    index.replace_section("atlas", [atlas_record])
    assert index.query_section("texture_set") == [texture]
    assert index.query_section("atlas") == [atlas]

    renamed = replace(texture, name="A Better Stone")
    index.upsert(CatalogRecord(renamed, texture_manifest, 22, 102))
    reopened = CatalogIndex(database, "library-key")
    assert reopened.query_section("texture_set") == [renamed]
    assert reopened.records_for_section("texture_set")[texture_manifest].manifest_size == 22

    reopened.remove_manifest(texture_manifest)
    reopened.remove_asset(atlas.id)
    assert all(not section for section in reopened.sections().values())


def test_catalog_records_for_ids_and_reusable_writer_commit_each_item(tmp_path) -> None:
    texture, atlas, *_ = assets(tmp_path)
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")
    texture_record = CatalogRecord(
        texture, texture.asset_dir / "asset.json", 20, 100
    )
    atlas_record = CatalogRecord(
        atlas, atlas.asset_dir / "asset.json", 21, 101
    )

    with index.writer() as writer:
        writer.upsert(texture_record)
        assert CatalogIndex(
            index.database_path, "library-key"
        ).query_section("texture_set") == [texture]
        writer.upsert(atlas_record)

    records = index.records_for_ids(
        ("missing", atlas.id, texture.id, atlas.id)
    )
    assert set(records) == {texture.id, atlas.id}
    assert records[texture.id].manifest_path == texture_record.manifest_path
    assert records[atlas.id].manifest_path == atlas_record.manifest_path


def test_replace_section_is_transactional_and_does_not_touch_other_sections(tmp_path) -> None:
    texture, atlas, *_ = assets(tmp_path)
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")
    index.upsert(CatalogRecord(texture, texture.asset_dir / "asset.json", 1, 1))
    index.upsert(CatalogRecord(atlas, atlas.asset_dir / "asset.json", 1, 1))

    with pytest.raises(ValueError):
        index.replace_section(
            "texture_set",
            [CatalogRecord(atlas, atlas.asset_dir / "asset.json", 1, 1)],
        )

    assert index.query_section("texture_set") == [texture]
    assert index.query_section("atlas") == [atlas]


def test_corrupt_and_old_schema_catalogs_rebuild(tmp_path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    rebuilt = CatalogIndex(corrupt, "library-key")
    assert rebuilt.sections()["model"] == []

    old = tmp_path / "old.sqlite3"
    with sqlite3.connect(old) as connection:
        connection.execute("CREATE TABLE obsolete(value TEXT)")
        connection.execute(f"PRAGMA user_version = {CATALOG_SCHEMA_VERSION - 1}")
    migrated = CatalogIndex(old, "library-key")
    with sqlite3.connect(old) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "assets" in tables
    assert "obsolete" in tables
    assert version == CATALOG_SCHEMA_VERSION
    assert migrated.query_section("stock") == []


def test_library_index_path_is_stable_and_outside_library(tmp_path) -> None:
    library = tmp_path / "library"
    (library / ".ual").mkdir(parents=True)
    (library / ".ual" / "library.json").write_text('{"id":"portable-id"}', encoding="utf-8")
    cache = tmp_path / "local-cache"

    first = CatalogIndex.for_library(library, cache_root=cache)
    second = CatalogIndex.for_library(library, cache_root=cache)

    assert first.database_path == second.database_path
    assert first.database_path.is_relative_to(cache)
    assert not first.database_path.is_relative_to(library)


class FakeRepository:
    def __init__(self, manifests: dict[str, list[Path]], loaded: dict[Path, object]):
        self.manifests = manifests
        self.loaded = loaded
        self.discovered: list[str] = []
        self.load_calls: list[Path] = []
        self.fail_load = False

    def manifest_paths_for_type(self, asset_type: str) -> list[Path]:
        self.discovered.append(asset_type)
        return list(self.manifests.get(asset_type, ()))

    def load_asset_manifest(self, path: Path, *, expected_type: str | None = None):
        self.load_calls.append(path)
        if self.fail_load:
            raise ValueError("temporarily invalid")
        asset = self.loaded[path]
        assert expected_type == asset.asset_type
        return asset


def test_refresh_reuses_unchanged_and_preserves_last_good_on_parse_error(tmp_path) -> None:
    texture = assets(tmp_path)[0]
    manifest = texture.asset_dir / "asset.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("first", encoding="utf-8")
    repository = FakeRepository({"texture_set": [manifest]}, {manifest: texture})
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")

    cold = refresh_catalog_section(repository, index, "texture_set")
    assert cold.parsed == 1
    assert cold.reused == 0
    assert cold.assets == (texture,)

    warm = refresh_catalog_section(repository, index, "texture_set")
    assert warm.parsed == 0
    assert warm.reused == 1
    assert repository.load_calls == [manifest]

    manifest.write_text("changed and temporarily broken", encoding="utf-8")
    repository.fail_load = True
    partial = refresh_catalog_section(repository, index, "texture_set")
    assert partial.assets == (texture,)
    assert len(partial.warnings) == 1
    assert index.query_section("texture_set") == [texture]

    repository.manifests["texture_set"] = []
    removed = refresh_catalog_section(repository, index, "texture_set")
    assert removed.removed == 1
    assert index.query_section("texture_set") == []


def test_refresh_cancellation_does_not_replace_section(tmp_path) -> None:
    texture = assets(tmp_path)[0]
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")
    index.upsert(CatalogRecord(texture, texture.asset_dir / "asset.json", 1, 1))
    repository = FakeRepository({"texture_set": []}, {})
    token = CancelToken()
    token.cancel()

    with pytest.raises(Exception, match="canceled"):
        refresh_catalog_section(
            repository, index, "texture_set", cancel_token=token
        )

    assert index.query_section("texture_set") == [texture]


def test_worker_refreshes_selected_section_first(tmp_path) -> None:
    repository = FakeRepository({}, {})
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")
    worker = CatalogRefreshWorker(repository, index, "model")
    ready: list[str] = []
    completed = []
    worker.signals.section_ready.connect(
        lambda asset_type, _assets, _warnings: ready.append(asset_type)
    )
    worker.signals.finished.connect(completed.append)

    worker.run()

    assert repository.discovered == ["model", "texture_set", "atlas", "hdri", "vdb", "stock"]
    assert ready == repository.discovered
    assert list(completed[0]) == repository.discovered


def test_canceled_worker_ignores_signals_deleted_during_qt_shutdown(
    tmp_path,
) -> None:
    repository = FakeRepository({}, {})
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "library-key")
    token = CancelToken()
    worker = CatalogRefreshWorker(
        repository, index, "texture_set", cancel_token=token
    )
    token.cancel()
    sip.delete(worker.signals)

    worker.run()
