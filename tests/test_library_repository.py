import json
import socket
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.importer import scan_texture_folder
from universal_asset_library.importer import TextureMap
from universal_asset_library.library import AssetMetadataPatch, AssetMetadataUpdate, CancelToken, LibraryError, LibraryLockedError, LibraryRepository
import universal_asset_library.library.repository as repository_module


def image(path: Path, color: str, width: int = 1024) -> None:
    value = QImage(width, width, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    assert value.save(str(path))


def source_material(tmp_path: Path, name: str = "stone"):
    source = tmp_path / name
    source.mkdir()
    image(source / f"{name}_diff_1k.jpg", "#736c62")
    image(source / f"{name}_rough_1k.jpg", "#999999")
    image(source / f"{name}_preview.png", "#736c62", 256)
    metadata = source / "custom.json"
    metadata.write_text('{"source": "test"}', encoding="utf-8")
    return source, scan_texture_folder(source).materials[0]


def test_atomic_import_manifest_catalog_and_source_integrity(tmp_path) -> None:
    source, candidate = source_material(tmp_path)
    originals = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    summary = repository.import_materials([candidate])
    assert len(summary.imported) == 1
    assert not summary.failed
    asset = summary.imported[0]
    assert asset.name == "Stone"
    assert asset.resolution == "1K"
    assert set(asset.channels) == {"Base Color", "Roughness"}
    assert asset.thumbnail_path and asset.thumbnail_path.is_file()
    manifest = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["naming_version"] == 2
    assert manifest["category"] == "Stone"
    assert isinstance(manifest["tags"], list)
    assert "categories" not in manifest
    assert manifest["fingerprint"]
    assert manifest["source_metadata"] == ["metadata/custom.json"]
    assert manifest["source"]["original_path"] == str(source)
    assert manifest["source_metadata_original_paths"] == {
        "metadata/custom.json": "custom.json",
    }
    assert {
        record["original_path"]
        for maps in manifest["resolutions"]["1K"]["maps"].values()
        for record in maps
    } == {"stone_diff_1k.jpg", "stone_rough_1k.jpg"}
    assert manifest["preview_original_paths"]["thumbnail"] == "stone_preview.png"
    assert all(record["sha256"] for maps in manifest["resolutions"]["1K"]["maps"].values() for record in maps)
    assert {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} == originals
    assert repository.list_assets()[0].id == asset.id
    assert not list((library / ".ual" / "staging").iterdir())
    assert asset.asset_dir.name == "stone"
    assert {path.name for path in (asset.asset_dir / "maps" / "1K").iterdir()} == {
        "Stone_1K_BaseColor.jpg",
        "Stone_1K_Roughness.jpg",
    }
    assert asset.thumbnail_path.name == "Stone_Thumbnail.png"


def test_texture_import_converts_webp_maps_and_previews_to_jpeg(tmp_path) -> None:
    source = tmp_path / "webp-stone"
    source.mkdir()
    image(source / "webp_stone_diff_1k.webp", "#736c62")
    image(source / "webp_stone_preview.webp", "#736c62", 256)
    candidate = scan_texture_folder(source).materials[0]
    original_map = (source / "webp_stone_diff_1k.webp").read_bytes()

    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    manifest = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    map_record = manifest["resolutions"]["1K"]["maps"]["Base Color"][0]
    managed_map = asset.asset_dir / map_record["path"]

    assert managed_map.suffix == ".jpg"
    assert managed_map.read_bytes().startswith(b"\xff\xd8\xff")
    assert map_record["format"] == "JPEG"
    assert map_record["original_path"] == "webp_stone_diff_1k.webp"
    assert map_record["size"] == managed_map.stat().st_size
    assert map_record["sha256"] == repository_module._sha256_file(managed_map)
    assert asset.thumbnail_path.suffix == ".jpg"
    assert asset.thumbnail_path.read_bytes().startswith(b"\xff\xd8\xff")
    assert manifest["preview_original_paths"]["thumbnail"] == "webp_stone_preview.webp"
    assert (source / "webp_stone_diff_1k.webp").read_bytes() == original_map


def test_content_duplicate_is_skipped_without_visible_copy(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    assert len(repository.import_materials([candidate]).imported) == 1
    duplicate = repository.import_materials([candidate])
    assert not duplicate.imported
    assert len(duplicate.skipped) == 1
    assert len(repository.list_assets()) == 1
    assert not list((library / ".ual" / "staging").iterdir())


def test_targeted_texture_listing_does_not_discover_other_containers(
    tmp_path, monkeypatch,
) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = repository.import_materials([candidate]).imported
    monkeypatch.setattr(
        repository_module,
        "_asset_manifest_paths",
        lambda _root: pytest.fail("full-library discovery should not run"),
    )
    monkeypatch.setattr(
        repository_module,
        "_stock_manifest_paths",
        lambda _root: pytest.fail("Stock discovery should not run"),
    )

    assert repository.list_assets_for_type("texture_set") == imported


def test_targeted_listing_rejects_unknown_asset_type(tmp_path) -> None:
    with pytest.raises(LibraryError, match="Unsupported asset type"):
        LibraryRepository(tmp_path).list_assets_for_type("unknown")


def test_provider_id_duplicate_skips_before_copy(tmp_path) -> None:
    source = tmp_path / "provider"
    source.mkdir()
    document = {
        "id": "same-id",
        "semanticTags": {"name": "Provider Stone"},
        "maps": [
            {"uri": "stone_diff_1k.jpg", "type": "albedo", "resolution": "1024x1024"},
            {"uri": "stone_rough_1k.jpg", "type": "roughness", "resolution": "1024x1024"},
        ],
    }
    (source / "asset.json").write_text(json.dumps(document), encoding="utf-8")
    image(source / "stone_diff_1k.jpg", "#777777")
    image(source / "stone_rough_1k.jpg", "#999999")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    repository.import_materials([candidate])
    summary = repository.import_materials([candidate])
    assert summary.skipped == ["Provider Stone: matching provider ID"]


def test_failed_material_does_not_stop_batch(tmp_path) -> None:
    _bad_source, bad = source_material(tmp_path, "bad")
    _good_source, good = source_material(tmp_path, "good")
    (bad.source_root / bad.resolutions["1K"].maps["Base Color"][0].relative_path).unlink()
    library = tmp_path / "library"
    library.mkdir()
    summary = LibraryRepository(library).import_materials([bad, good])
    assert "Bad" in summary.failed
    assert [asset.name for asset in summary.imported] == ["Good"]


def test_cancel_and_active_lock_leave_no_partial_asset(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    token = CancelToken()
    token.cancel()
    summary = repository.import_materials([candidate], cancel_token=token)
    assert summary.canceled
    assert repository.list_assets() == []
    lock = library / ".ual" / "import.lock"
    lock.write_text("active", encoding="utf-8")
    with pytest.raises(LibraryLockedError):
        repository.import_materials([candidate])


def test_corrupt_manifest_is_reported_without_hiding_valid_assets(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    repository.import_materials([candidate])
    corrupt = library / "textures" / "broken" / "asset"
    corrupt.mkdir(parents=True)
    (corrupt / "asset.json").write_text("{broken", encoding="utf-8")
    assert len(repository.list_assets()) == 1
    assert len(repository.last_warnings) == 1


def test_readable_folder_collisions_and_map_alternatives(tmp_path) -> None:
    first_source, first = source_material(tmp_path, "first")
    second_source, second = source_material(tmp_path, "second")
    first.name = second.name = "Aerial Asphalt 01"
    first.category = second.category = "Asphalt"
    alternative = first_source / "alternate_diff_1k.jpg"
    image(alternative, "#334455")
    first.resolutions["1K"].maps["Base Color"].append(TextureMap(
        channel="Base Color",
        relative_path=alternative.name,
        file_format="jpg",
        bit_depth=16,
    ))
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = repository.import_materials([first, second]).imported
    assert [asset.asset_dir.name for asset in imported] == ["aerial-asphalt-01", "aerial-asphalt-01-2"]
    names = {path.name for path in (imported[0].asset_dir / "maps" / "1K").iterdir()}
    assert "Aerial_Asphalt_01_1K_BaseColor.jpg" in names
    assert "Aerial_Asphalt_01_1K_BaseColor_16bit.jpg" in names


def test_normal_and_packed_map_names_are_semantic(tmp_path) -> None:
    source, candidate = source_material(tmp_path)
    normal = source / "normal.jpg"
    arm = source / "packed.jpg"
    image(normal, "#7788ff")
    image(arm, "#998877")
    candidate.resolutions["1K"].maps["Normal"] = [TextureMap(
        channel="Normal",
        relative_path=normal.name,
        file_format="jpg",
        normal_convention="OpenGL",
        preferred=True,
    )]
    candidate.resolutions["1K"].maps["Packed"] = [TextureMap(
        channel="Packed",
        relative_path=arm.name,
        file_format="jpg",
        packed_channels={"r": "Ambient Occlusion", "g": "Roughness", "b": "Metalness"},
        preferred=True,
    )]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    names = {path.name for path in (asset.asset_dir / "maps" / "1K").iterdir()}
    assert "Stone_1K_Normal_OpenGL.jpg" in names
    assert "Stone_1K_ARM.jpg" in names


def test_legacy_repair_preserves_identity_hashes_metadata_and_is_idempotent(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    original_manifest = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    original_metadata = (asset.asset_dir / "metadata" / "custom.json").read_bytes()
    legacy = asset.asset_dir.with_name("stone_deadbeef")
    asset.asset_dir.rename(legacy)
    manifest_path = legacy / "asset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("naming_version")
    for channel, records in manifest["resolutions"]["1K"]["maps"].items():
        for index, record in enumerate(records):
            source_path = legacy / record["path"]
            target = source_path.with_name(f"old_{channel.replace(' ', '_')}_{index}{source_path.suffix}")
            source_path.rename(target)
            record["path"] = target.relative_to(legacy).as_posix()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert repository.legacy_asset_count() == 1
    summary = repository.repair_legacy_names()
    assert len(summary.renamed) == 1
    repaired = summary.renamed[0]
    assert repaired.asset_dir.name == "stone"
    repaired_manifest = json.loads((repaired.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert repaired_manifest["id"] == original_manifest["id"]
    assert repaired_manifest["fingerprint"] == original_manifest["fingerprint"]
    assert repaired_manifest["naming_version"] == 2
    assert (repaired.asset_dir / "metadata" / "custom.json").read_bytes() == original_metadata
    assert repository.legacy_asset_count() == 0
    repeated = repository.repair_legacy_names()
    assert not repeated.renamed
    assert len(repeated.skipped) == 1


def test_failed_legacy_repair_restores_original(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    legacy = asset.asset_dir.with_name("stone_deadbeef")
    asset.asset_dir.rename(legacy)
    manifest_path = legacy / "asset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("naming_version")
    next(iter(manifest["resolutions"]["1K"]["maps"].values()))[0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary = repository.repair_legacy_names()
    assert "Stone" in summary.failed
    assert legacy.is_dir()
    assert not (library / "textures" / "uncategorized" / "stone").exists()
    assert not list((library / ".ual" / "staging").iterdir())


def test_canceled_and_locked_repairs_leave_legacy_asset_untouched(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    legacy = asset.asset_dir.with_name("stone_deadbeef")
    asset.asset_dir.rename(legacy)
    manifest_path = legacy / "asset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("naming_version")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    token = CancelToken()
    token.cancel()
    summary = repository.repair_legacy_names(cancel_token=token)
    assert summary.canceled
    assert legacy.is_dir()
    lock = library / ".ual" / "import.lock"
    lock.write_text("active", encoding="utf-8")
    with pytest.raises(LibraryLockedError):
        repository.repair_legacy_names()


def test_preflight_blocks_source_changed_after_scan(tmp_path) -> None:
    source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    image(source / "stone_diff_1k.jpg", "#112233")
    repository = LibraryRepository(library)
    preflight = repository.preflight_materials([candidate])
    assert preflight.materials[0].status == "Stale"
    summary = repository.import_materials([candidate], preflight_result=preflight)
    assert "Stone" in summary.failed
    assert repository.list_assets() == []


def test_provider_id_changed_content_requires_explicit_separate_choice(tmp_path) -> None:
    source = tmp_path / "provider-conflict"
    source.mkdir()
    document = {
        "id": "provider-id",
        "semanticTags": {"name": "Provider Stone"},
        "maps": [
            {"uri": "stone_diff_1k.jpg", "type": "albedo", "resolution": "1024x1024"},
            {"uri": "stone_rough_1k.jpg", "type": "roughness", "resolution": "1024x1024"},
        ],
    }
    (source / "asset.json").write_text(json.dumps(document), encoding="utf-8")
    image(source / "stone_diff_1k.jpg", "#777777")
    image(source / "stone_rough_1k.jpg", "#999999")
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    first = scan_texture_folder(source).materials[0]
    assert len(repository.import_materials([first]).imported) == 1
    image(source / "stone_diff_1k.jpg", "#123456")
    changed = scan_texture_folder(source).materials[0]
    preflight = repository.preflight_materials([changed])
    assert preflight.materials[0].status == "Conflict"
    assert repository.import_materials([changed], preflight_result=preflight).skipped
    separate = repository.import_materials(
        [changed],
        preflight_result=preflight,
        conflict_decisions={str(changed.source_root): "separate"},
    )
    assert len(separate.imported) == 1
    assert separate.imported[0].asset_dir.name == "provider-stone-2"


def test_source_mutation_during_copy_removes_staging(tmp_path, monkeypatch) -> None:
    source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    preflight = repository.preflight_materials([candidate])
    original = repository_module._copy_hash
    changed = False

    def mutate_then_copy(source_path, *args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            source_path.write_bytes(source_path.read_bytes() + b"changed")
        return original(source_path, *args, **kwargs)

    monkeypatch.setattr(repository_module, "_copy_hash", mutate_then_copy)
    summary = repository.import_materials([candidate], preflight_result=preflight)
    assert "Stone" in summary.failed
    assert repository.list_assets() == []
    assert not list((library / ".ual" / "staging").iterdir())


def test_portable_name_validation_and_insufficient_space(tmp_path, monkeypatch) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    candidate.name = "CON"
    preflight = repository.preflight_materials([candidate])
    assert preflight.materials[0].status == "Invalid"
    assert any(item.code == "windows_reserved_name" for item in preflight.materials[0].diagnostics)
    candidate.name = "Stone"
    usage = type("Usage", (), {"total": 1, "used": 1, "free": 0})()
    monkeypatch.setattr(repository_module.shutil, "disk_usage", lambda _path: usage)
    preflight = repository.preflight_materials([candidate])
    assert preflight.materials[0].status == "Invalid"
    assert any(item.code == "insufficient_space" for item in preflight.diagnostics)


def test_local_stale_lock_recovers_remote_lock_blocks_and_staging_cleans(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    repository.initialize()
    lock = library / ".ual" / "import.lock"
    lock.write_text(json.dumps({"pid": 99999999, "host": socket.gethostname(), "created_at": "old"}), encoding="utf-8")
    assert len(repository.import_materials([candidate]).imported) == 1
    abandoned = library / ".ual" / "staging" / "abandoned"
    abandoned.mkdir()
    (abandoned / "partial.bin").write_bytes(b"partial")
    lock.write_text(json.dumps({"pid": 42, "host": "another-workstation", "created_at": "now"}), encoding="utf-8")
    with pytest.raises(LibraryLockedError, match="another-workstation"):
        repository.cleanup_abandoned_staging()
    lock.unlink()
    assert repository.recovery_state().staging_directories == ("abandoned",)
    assert repository.cleanup_abandoned_staging() == 1
    assert repository.recovery_state().staging_directories == ()


def test_update_asset_metadata_is_atomic_and_preserves_payloads(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    before = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    payloads = {
        path.relative_to(asset.asset_dir).as_posix(): path.read_bytes()
        for path in asset.asset_dir.rglob("*")
        if path.is_file() and path.name != "asset.json"
    }
    updated = repository.update_asset_metadata(asset.id, AssetMetadataUpdate(
        name="Edited Stone",
        category="Concrete",
        tags=("edited", " studio ", "edited"),
        author="Material Team",
        description="Reviewed material metadata.",
        physical_size="2 × 2 m",
    ))
    after = json.loads((updated.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert updated.name == "Edited Stone"
    assert updated.category == "Concrete"
    assert updated.tags == ("edited", "studio")
    assert updated.asset_dir == library / "textures" / "concrete" / asset.asset_dir.name
    assert not asset.asset_dir.exists()
    assert after["id"] == before["id"]
    assert after["fingerprint"] == before["fingerprint"]
    assert after["provider"] == before["provider"]
    assert "categories" not in after
    assert after["updated_at"] != before["updated_at"]
    assert payloads == {
        path.relative_to(updated.asset_dir).as_posix(): path.read_bytes()
        for path in updated.asset_dir.rglob("*")
        if path.is_file() and path.name != "asset.json"
    }
    assert repository.list_assets()[0].description == "Reviewed material metadata."


def test_metadata_tag_edit_does_not_move_asset(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]

    updated = repository.update_asset_metadata(asset.id, AssetMetadataUpdate(
        name=asset.name,
        category=asset.category,
        tags=(*asset.tags, "secondary"),
    ))

    assert updated.asset_dir == asset.asset_dir
    assert updated.tags[-1] == "secondary"


def test_asset_rating_patch_round_trips_and_preserves_other_metadata(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    manifest_path = asset.asset_dir / "asset.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert original["rating"] == 0

    updated = repository.patch_asset_metadata(
        asset.id, AssetMetadataPatch(rating=5)
    )
    document = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert updated.rating == 5
    assert document["rating"] == 5
    assert document["name"] == original["name"]
    assert document["tags"] == original["tags"]
    assert document["category"] == original["category"]
    assert document["updated_at"] != original["updated_at"]

    document.pop("rating")
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    assert repository.list_assets()[0].rating == 0

    document["rating"] = 6
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    assert repository.list_assets() == []
    assert "rating" in repository.last_warnings[0].casefold()


@pytest.mark.parametrize("rating", [-1, 6, 1.5, True])
def test_asset_rating_patch_rejects_invalid_values(tmp_path, rating) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]

    with pytest.raises(LibraryError, match="rating"):
        repository.patch_asset_metadata(
            asset.id, AssetMetadataPatch(rating=rating)
        )

    assert repository.list_assets()[0].rating == 0


def test_metadata_patch_preserves_unmentioned_fields_and_merges_tags(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    candidate.tags = ["Existing", "Sunny"]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    asset = repository.update_asset_metadata(asset.id, AssetMetadataUpdate(
        name=asset.name,
        category=asset.category,
        tags=asset.tags,
        author="Material Team",
        description="Keep this description.",
        physical_size="2 × 2 m",
    ))

    updated = repository.patch_asset_metadata(
        asset.id,
        AssetMetadataPatch(
            category="Concrete",
            add_tags=("sunny", "urban", "Existing"),
        ),
    )

    assert updated.category == "Concrete"
    assert updated.tags == ("Existing", "Sunny", "urban")
    assert updated.author == "Material Team"
    assert updated.description == "Keep this description."
    assert updated.physical_size == "2 × 2 m"
    assert updated.asset_dir.parent.name == "concrete"


def test_metadata_patch_batch_uses_one_lock_and_valid_manifest_hints(
    tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = []
    for index, name in enumerate(("batch-stone-a", "batch-stone-b")):
        source, _candidate = source_material(tmp_path, name)
        image(source / f"{name}_diff_1k.jpg", f"#{65 + index * 40:02x}6c62")
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = "Stone"
        imported.extend(repository.import_materials([candidate]).imported)

    calls = {"initialize": 0, "enter": 0, "exit": 0, "categories": 0}
    original_initialize = repository.initialize
    original_enter = repository_module._ImportLock.__enter__
    original_exit = repository_module._ImportLock.__exit__
    original_category_load = repository_module.CategoryConfigStore.load

    def initialize():
        calls["initialize"] += 1
        original_initialize()

    def enter(lock):
        calls["enter"] += 1
        return original_enter(lock)

    def exit_lock(lock, exc_type, exc, tb):
        calls["exit"] += 1
        return original_exit(lock, exc_type, exc, tb)

    def load_categories(store, asset_type):
        calls["categories"] += 1
        return original_category_load(store, asset_type)

    monkeypatch.setattr(repository, "initialize", initialize)
    monkeypatch.setattr(repository_module._ImportLock, "__enter__", enter)
    monkeypatch.setattr(repository_module._ImportLock, "__exit__", exit_lock)
    monkeypatch.setattr(
        repository_module.CategoryConfigStore, "load", load_categories
    )
    monkeypatch.setattr(
        repository_module,
        "_asset_manifest_paths",
        lambda _root: pytest.fail("valid catalog hints must avoid a library scan"),
    )
    hints = {
        asset.id: asset.asset_dir / "asset.json" for asset in imported
    }

    with repository.metadata_patch_batch(
        (asset.id for asset in imported), hints
    ) as batch:
        outcomes = [
            batch.patch(
                asset.id, AssetMetadataPatch(category="Concrete")
            )
            for asset in imported
        ]

    assert calls == {
        "initialize": 1,
        "enter": 1,
        "exit": 1,
        "categories": 1,
    }
    assert {outcome.asset.category for outcome in outcomes} == {"Concrete"}
    assert all(outcome.manifest_path.is_file() for outcome in outcomes)
    assert all(
        outcome.manifest_path == outcome.asset.asset_dir / "asset.json"
        for outcome in outcomes
    )


def test_metadata_patch_batch_validates_hints_and_scans_at_most_once(
    tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = []
    for index, name in enumerate(("hint-stone-a", "hint-stone-b")):
        source, _candidate = source_material(tmp_path, name)
        image(source / f"{name}_diff_1k.jpg", f"#{75 + index * 40:02x}6c62")
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = "Stone"
        imported.extend(repository.import_materials([candidate]).imported)
    first, second = imported
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"id": first.id, "type": "texture_set"}),
        encoding="utf-8",
    )
    scans = 0
    original_scan = repository_module._asset_manifest_paths

    def counted_scan(root):
        nonlocal scans
        scans += 1
        return original_scan(root)

    monkeypatch.setattr(
        repository_module, "_asset_manifest_paths", counted_scan
    )
    hints = {
        first.id: outside,
        second.id: first.asset_dir / "asset.json",
    }

    with repository.metadata_patch_batch(
        (first.id, second.id), hints
    ) as batch:
        first_outcome = batch.patch(
            first.id, AssetMetadataPatch(category="Concrete")
        )
        second_outcome = batch.patch(
            second.id, AssetMetadataPatch(category="Concrete")
        )

    assert scans == 1
    assert first_outcome.asset.id == first.id
    assert second_outcome.asset.id == second.id
    assert json.loads(outside.read_text(encoding="utf-8"))["id"] == first.id


def test_update_library_migrates_categories_to_tags_and_removes_surface(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    candidate.category = "Stone"
    candidate.tags = ["Hero", "surface", "rough"]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    manifest = asset.asset_dir / "asset.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["categories"] = ["surface", "Stone", "Indoor", "ROUGH"]
    document["tags"] = ["Hero", "surface", "rough"]
    manifest.write_text(json.dumps(document), encoding="utf-8")

    assert repository.library_update_count() == 1
    summary = repository.update_library()
    updated_document = json.loads(manifest.read_text(encoding="utf-8"))

    assert len(summary.updated) == 1
    assert summary.failed == {}
    assert updated_document["category"] == "Stone"
    assert updated_document["tags"] == ["Hero", "rough", "indoor"]
    assert "categories" not in updated_document
    assert repository.library_update_count() == 0
    assert repository.update_library().updated == []


def test_update_library_rejects_unknown_primary_without_rewriting(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    manifest = asset.asset_dir / "asset.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["category"] = "Future Category"
    document["categories"] = ["Future Category", "surface", "useful"]
    manifest.write_text(json.dumps(document, indent=2), encoding="utf-8")
    before = manifest.read_bytes()

    assert repository.library_update_count() == 1
    summary = repository.update_library()

    assert summary.updated == []
    assert str(manifest) in summary.failed
    assert "not defined" in summary.failed[str(manifest)]
    assert manifest.read_bytes() == before


def test_update_library_maps_surface_primary_to_uncategorized(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    manifest = asset.asset_dir / "asset.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["category"] = "surface"
    document["categories"] = ["surface", "Rough"]
    document["tags"] = ["surface"]
    manifest.write_text(json.dumps(document), encoding="utf-8")

    summary = repository.update_library()
    updated = json.loads(manifest.read_text(encoding="utf-8"))

    assert len(summary.updated) == 1
    assert updated["category"] == "Uncategorized"
    assert updated["tags"] == ["rough"]
    assert "categories" not in updated


def test_metadata_category_move_uses_readable_collision_suffix(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    occupied = library / "textures" / "concrete" / asset.asset_dir.name
    occupied.mkdir(parents=True)

    updated = repository.update_asset_metadata(asset.id, AssetMetadataUpdate(
        name=asset.name,
        category="Concrete",
    ))

    assert updated.asset_dir == occupied.with_name(f"{occupied.name}-2")
    assert occupied.is_dir()
    assert not asset.asset_dir.exists()


def test_metadata_category_move_rolls_back_when_manifest_write_fails(
    tmp_path, monkeypatch
) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    original_manifest = (asset.asset_dir / "asset.json").read_bytes()
    atomic_json = repository_module._atomic_json
    calls = 0

    def fail_first_write(path, document):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected manifest failure")
        return atomic_json(path, document)

    monkeypatch.setattr(repository_module, "_atomic_json", fail_first_write)
    with pytest.raises(OSError, match="injected manifest failure"):
        repository.update_asset_metadata(asset.id, AssetMetadataUpdate(
            name=asset.name,
            category="Concrete",
        ))

    assert asset.asset_dir.is_dir()
    assert (asset.asset_dir / "asset.json").read_bytes() == original_manifest
    assert not (library / "textures" / "concrete" / asset.asset_dir.name).exists()


def test_update_asset_metadata_validates_required_fields_and_lock(tmp_path) -> None:
    _source, candidate = source_material(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    with pytest.raises(LibraryError, match="name is required"):
        repository.update_asset_metadata(asset.id, AssetMetadataUpdate("", "Stone"))
    lock = library / ".ual" / "import.lock"
    lock.write_text("active", encoding="utf-8")
    with pytest.raises(LibraryLockedError):
        repository.update_asset_metadata(asset.id, AssetMetadataUpdate("Stone", "Stone"))


def test_import_preserves_all_safe_extra_files_with_paths_hashes_and_bytes(tmp_path) -> None:
    source, _candidate = source_material(tmp_path)
    (source / "scene.blend").write_bytes(b"blend-project-data")
    (source / "notes.txt").write_text("material notes", encoding="utf-8")
    nested = source / "documentation"
    nested.mkdir()
    (nested / "readme.md").write_text("# Source notes", encoding="utf-8")
    extra_preview = QImage(320, 180, QImage.Format.Format_RGB32)
    extra_preview.fill(QColor("#123456"))
    assert extra_preview.save(str(source / "hero_render.jpg"))
    reference = QImage(160, 240, QImage.Format.Format_RGB32)
    reference.fill(QColor("#654321"))
    assert reference.save(str(source / "reference.png"))
    (source / ".hidden-cache").write_bytes(b"ignored")
    (source / "linked-notes.txt").symlink_to(source / "notes.txt")
    candidate = scan_texture_folder(source).materials[0]
    assert set(candidate.extra_paths) == {
        "documentation/readme.md",
        "notes.txt",
        "reference.png",
        "scene.blend",
    }
    originals = {relative: (source / relative).read_bytes() for relative in candidate.extra_paths}
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    manifest = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert {record["original_path"] for record in manifest["extra_files"]} == set(originals)
    assert len(asset.extra_files) == 4
    for record in manifest["extra_files"]:
        copied = asset.asset_dir / record["path"]
        assert record["path"] == f"extras/{record['original_path']}"
        assert copied.read_bytes() == originals[record["original_path"]]
        assert record["sha256"] == repository_module._sha256_file(copied)
    assert not (asset.asset_dir / "extras" / ".hidden-cache").exists()
    assert not (asset.asset_dir / "extras" / "linked-notes.txt").exists()


def test_changed_or_missing_extra_file_is_detected(tmp_path) -> None:
    source, _candidate = source_material(tmp_path)
    extra = source / "workflow.txt"
    extra.write_text("v1", encoding="utf-8")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    extra.write_text("v2 changed", encoding="utf-8")
    preflight = repository.preflight_materials([candidate])
    assert preflight.materials[0].status == "Stale"
    candidate = scan_texture_folder(source).materials[0]
    asset = repository.import_materials([candidate]).imported[0]
    (asset.asset_dir / asset.extra_files[0].path).unlink()
    assert repository.list_assets() == []
    assert any("Missing extra file" in warning for warning in repository.last_warnings)
