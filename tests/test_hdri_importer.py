import json
import os
from pathlib import Path
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage

from universal_asset_library.importer import scan_hdri_folder, scan_mixed_folder, scan_texture_folder
from universal_asset_library.library import LibraryRepository
from universal_asset_library.previews import HdriPreviewResult


def _hdr(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n"
        + f"-Y {height} +X {width}\n".encode("ascii")
        + b"fixture"
    )


def _exr_header(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x76\x2f\x31\x01" + struct.pack("<I", 2)
        + b"dataWindow\0box2i\0" + struct.pack("<I", 16)
        + struct.pack("<4i", 0, 0, width - 1, height - 1)
        + b"\0"
    )


def test_loose_exr_dimensions_and_small_suffix_are_grouped(tmp_path) -> None:
    _exr_header(tmp_path / "Beach_Noon.exr", 7000, 3500)
    _exr_header(tmp_path / "Beach_Noon_sm.exr", 1750, 875)
    mixed = scan_mixed_folder(tmp_path)
    assert len(mixed.materials) == 1
    candidate = mixed.materials[0]
    assert candidate.name == "Beach Noon"
    assert candidate.resolution_labels == ["2K", "8K"]
    assert candidate.resolutions["2K"].width == 1750
    assert candidate.resolutions["8K"].width == 7000
    assert {item.relative_path for variant in candidate.resolutions.values() for item in variant.files} == {
        "Beach_Noon.exr", "Beach_Noon_sm.exr",
    }
    forced_hdri = scan_hdri_folder(tmp_path)
    assert [(item.name, item.resolution_labels) for item in forced_hdri.materials] == [("Beach Noon", ["2K", "8K"])]


def _hdri_source(tmp_path: Path) -> Path:
    source = tmp_path / "sunny_courtyard"
    source.mkdir()
    _hdr(source / "sunny_courtyard_1k.hdr", 1024, 512)
    _hdr(source / "sunny_courtyard_4k.hdr", 4096, 2048)
    (source / "sunny_courtyard.blend").write_bytes(b"BLENDER-v300")
    (source / "notes.txt").write_text("lighting notes", encoding="utf-8")
    nested = source / "delivery"
    nested.mkdir()
    (nested / "license.dat").write_bytes(b"license")
    preview = QImage(256, 192, QImage.Format.Format_RGB32)
    preview.fill(QColor("#6d8ca8"))
    assert preview.save(str(source / "thumbnail.webp"), "PNG")
    document = {
        "name": "Sunny Courtyard",
        "authors": {"Lighting Artist": "All"},
        "categories": ["outdoor", "sunny"],
        "tags": ["courtyard", "day"],
        "max_resolution": [8192, 4096],
        "files": {
            "hdri": {
                "1k": {"hdr": {"url": "https://example.invalid/sunny_courtyard_1k.hdr"}},
                "2k": {"hdr": {"url": "https://example.invalid/sunny_courtyard_2k.hdr"}},
                "4k": {"hdr": {"url": "https://example.invalid/sunny_courtyard_4k.hdr"}},
            }
        },
    }
    (source / "info.json").write_text(json.dumps(document), encoding="utf-8")
    return source


def test_hdri_scan_uses_only_local_variants_and_classifies_companions(tmp_path) -> None:
    source = _hdri_source(tmp_path)
    result = scan_hdri_folder(source)
    assert len(result.materials) == 1
    candidate = result.materials[0]
    assert candidate.name == "Sunny Courtyard"
    assert candidate.provider == "Poly Haven"
    assert candidate.resolution_labels == ["1K", "4K"]
    assert candidate.category == "Outdoor"
    assert candidate.tags == ["courtyard", "day", "sunny"]
    assert candidate.metadata_paths == ["info.json"]
    assert candidate.extra_paths == [
        "delivery/license.dat",
        "notes.txt",
        "sunny_courtyard.blend",
        "thumbnail.webp",
    ]
    assert any("2K" in warning for warning in candidate.warnings)


def test_hdri_import_generates_real_jpeg_and_preserves_all_safe_files(tmp_path) -> None:
    source = _hdri_source(tmp_path)
    candidate = scan_hdri_folder(source).materials[0]
    originals = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    summary = repository.import_materials([candidate])
    assert not summary.failed
    asset = summary.imported[0]
    assert asset.asset_type == "hdri"
    assert asset.resolution == "1K, 4K"
    assert asset.thumbnail_path.read_bytes().startswith(b"\xff\xd8\xff")
    assert {item.original_path for item in asset.extra_files} == {
        "delivery/license.dat", "notes.txt", "sunny_courtyard.blend", "thumbnail.webp"
    }
    manifest = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert manifest["type"] == "hdri"
    assert manifest["category"] == "Outdoor"
    assert manifest["tags"] == ["courtyard", "day", "sunny"]
    assert "categories" not in manifest
    assert manifest["source_metadata"] == ["metadata/info.json"]
    assert manifest["source"]["original_path"] == str(source)
    assert manifest["source_metadata_original_paths"] == {
        "metadata/info.json": "info.json",
    }
    assert {
        record["original_path"]
        for variant in manifest["resolutions"].values()
        for record in variant["files"]
    } == {"sunny_courtyard_1k.hdr", "sunny_courtyard_4k.hdr"}
    assert (asset.asset_dir / "maps" / "sunny_courtyard_1k.hdr").is_file()
    assert not (asset.asset_dir / "maps" / "1K").exists()
    assert manifest["layout_version"] == 2
    assert (asset.asset_dir / "extras" / "delivery" / "license.dat").is_file()
    assert originals == {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    assert repository.list_hdri_assets()[0].id == asset.id


def test_hdri_import_publishes_composite_render_metadata_when_blender_succeeds(tmp_path, monkeypatch) -> None:
    source = _hdri_source(tmp_path)
    library = tmp_path / "library"
    library.mkdir()

    def fake_render(request, progress=None, cancel_token=None):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hero = request.output_dir / "Sunny_Courtyard_HDRI_Preview.jpg"
        thumbnail = request.output_dir / "Sunny_Courtyard_HDRI_Thumbnail.jpg"
        image = QImage(2048, 1536, QImage.Format.Format_RGB32)
        image.fill(QColor("#446688"))
        assert image.save(str(hero), "JPG", 90)
        thumb = image.scaled(640, 480)
        assert thumb.save(str(thumbnail), "JPG", 90)
        metadata = {
            "type": "hdri_composite", "status": "ready", "source": request.source_relative,
            "width": 2048, "height": 1536, "scene_width": 2048, "scene_height": 512,
            "panorama_width": 2048, "panorama_height": 1024, "generated_at": "now",
            "blender_version": "5.0.1", "template_sha256": "abc", "diagnostic": "",
        }
        return HdriPreviewResult("ready", thumbnail, hero, request.source_relative, metadata=metadata)

    monkeypatch.setattr("universal_asset_library.library.repository.render_hdri_preview", fake_render)
    asset = LibraryRepository(library).import_hdris([scan_hdri_folder(source).materials[0]]).imported[0]
    assert asset.preview_render["status"] == "ready"
    assert asset.hero_path.name == "Sunny_Courtyard_HDRI_Preview.jpg"
    assert asset.thumbnail_path.name == "Sunny_Courtyard_HDRI_Thumbnail.jpg"
    assert sorted(path.name for path in (asset.asset_dir / "previews").iterdir()) == [
        "Sunny_Courtyard_HDRI_Preview.jpg", "Sunny_Courtyard_HDRI_Thumbnail.jpg",
    ]


def test_hdri_render_exception_never_fails_import_or_replaces_fallback(tmp_path, monkeypatch) -> None:
    source = _hdri_source(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_hdri_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("renderer exploded")),
    )
    summary = LibraryRepository(library).import_hdris([scan_hdri_folder(source).materials[0]])
    assert not summary.failed
    asset = summary.imported[0]
    assert asset.preview_render["status"] == "failed"
    assert asset.hero_path == asset.thumbnail_path
    assert asset.hero_path.is_file()
    assert not (asset.asset_dir / ".preview-render").exists()


def test_manual_hdri_regeneration_keeps_previous_preview_when_source_changes(tmp_path, monkeypatch) -> None:
    source = _hdri_source(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library, render_hdri_previews=False)
    asset = repository.import_hdris([scan_hdri_folder(source).materials[0]]).imported[0]
    previous = asset.hero_path.read_bytes()

    def fake_render(request, progress=None, cancel_token=None):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hero = request.output_dir / "Sunny_Courtyard_HDRI_Preview.jpg"
        thumbnail = request.output_dir / "Sunny_Courtyard_HDRI_Thumbnail.jpg"
        with request.hdri_path.open("ab") as handle:
            handle.write(b"changed-during-render")
        image = QImage(2048, 1536, QImage.Format.Format_RGB32)
        image.fill(QColor("#112233"))
        assert image.save(str(hero), "JPG", 90)
        assert image.scaled(640, 480).save(str(thumbnail), "JPG", 90)
        return HdriPreviewResult("ready", thumbnail, hero, request.source_relative, metadata={"status": "ready"})

    monkeypatch.setattr("universal_asset_library.library.repository.render_hdri_preview", fake_render)
    import pytest
    with pytest.raises(Exception, match="changed while its preview was rendering"):
        repository.render_hdri_preview(asset.id)
    assert asset.hero_path.read_bytes() == previous


def test_library_update_flattens_existing_hdri_layout_atomically(tmp_path) -> None:
    source = _hdri_source(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_hdris([scan_hdri_folder(source).materials[0]]).imported[0]
    manifest_path = asset.asset_dir / "asset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_id = manifest["id"]
    for label, variant in manifest["resolutions"].items():
        for record in variant["files"]:
            old = asset.asset_dir / record["path"]
            nested = asset.asset_dir / "maps" / label / old.name
            nested.parent.mkdir(parents=True, exist_ok=True)
            old.rename(nested)
            record["path"] = nested.relative_to(asset.asset_dir).as_posix()
    manifest.pop("layout_version")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert repository.library_update_count() == 1
    summary = repository.update_library()
    assert len(summary.updated) == 1
    assert not summary.failed
    updated = summary.updated[0]
    assert updated.id == original_id
    assert all(len(Path(item.path).parts) == 2 for variant in updated.resolutions.values() for item in variant.files)
    assert not any(path.is_dir() for path in (updated.asset_dir / "maps").iterdir())
    assert repository.library_update_count() == 0
    repeated = repository.update_library()
    assert not repeated.updated


def test_texture_extra_changes_do_not_change_primary_fingerprint(tmp_path) -> None:
    source = tmp_path / "stone_source"
    source.mkdir()
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor("#777777"))
    assert image.save(str(source / "stone_diff_1k.png"))
    (source / "scene.blend").write_bytes(b"first")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    first = repository.preflight_materials([candidate]).materials[0].fingerprint
    (source / "scene.blend").write_bytes(b"second companion revision")
    rescanned = scan_texture_folder(source).materials[0]
    second = repository.preflight_materials([rescanned]).materials[0].fingerprint
    assert first == second
