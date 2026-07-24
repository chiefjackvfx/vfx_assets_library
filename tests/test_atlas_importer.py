import json
from pathlib import Path
import shutil

import pytest
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.importer import detect_asset_type, scan_atlas_folder, scan_mixed_folder
from universal_asset_library.library import LibraryRepository


def _image(path: Path, width: int = 256, height: int = 256) -> None:
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(QColor("#68805b"))
    assert value.save(str(path))


def _component_source(tmp_path: Path) -> Path:
    source = tmp_path / "dry_grass"
    (source / "Thumbs").mkdir(parents=True)
    (source / "previews").mkdir()
    document = {
        "id": "atlas-id",
        "name": "Wrong Name",
        "categories": ["atlas", "grass", "dried"],
        "assetCategories": {"atlas": {"grass": {"dried": {}}}},
        "semanticTags": {"name": "Dry Grass", "asset_type": "atlas", "contains": ["grass"]},
        "physicalSize": "0.4x0.4",
        "components": [
            {
                "type": channel,
                "name": channel.title(),
                "uris": [{"resolutions": [
                    {"resolution": "4096x4096", "formats": [
                        {"uri": f"grass_4K_{suffix}.exr", "bitDepth": bits}
                    ]},
                    {"resolution": "2048x2048", "formats": [
                        {"uri": f"grass_2K_{suffix}.jpg", "bitDepth": 8}
                    ]},
                ]}],
            }
            for channel, suffix, bits in (
                ("albedo", "Albedo", 16),
                ("normal", "Normal", 16),
                ("opacity", "Opacity", 32),
                ("translucency", "Translucency", 16),
            )
        ],
    }
    (source / "atlas.json").write_text(json.dumps(document), encoding="utf-8")
    for suffix in ("Albedo", "Normal", "Opacity", "Translucency"):
        (source / f"grass_4K_{suffix}.exr").write_bytes(b"local-exr-" + suffix.encode())
        _image(source / "Thumbs" / f"grass_2K_{suffix}.jpg")
    _image(source / "grass_Preview.png")
    _image(source / "previews" / "grass_Popup_1760_sp.jpg", 512, 256)
    return source


def test_megascans_components_detect_and_normalize_atlas(tmp_path: Path) -> None:
    source = _component_source(tmp_path)
    detected, reason = detect_asset_type(source)
    assert detected == "atlas"
    assert "Megascans atlas" in reason
    candidate = scan_atlas_folder(source).materials[0]
    assert candidate.asset_type == "atlas"
    assert candidate.name == "Dry Grass"
    assert candidate.category == "Grass"
    assert candidate.resolution_labels == ["4K"]
    assert set(candidate.resolutions["4K"].maps) == {
        "Base Color", "Normal", "Opacity", "Translucency",
    }
    assert candidate.selected_thumbnail == "grass_Preview.png"
    assert candidate.selected_hero == "previews/grass_Popup_1760_sp.jpg"
    assert all(f"Thumbs/grass_2K_{suffix}.jpg" in candidate.extra_paths for suffix in (
        "Albedo", "Normal", "Opacity", "Translucency",
    ))
    assert any(item.code == "atlas_thumbnail_resolution_excluded" for item in candidate.diagnostics)


def test_atlas_import_uses_distinct_manifest_container_and_duplicate_scope(tmp_path: Path) -> None:
    source = _component_source(tmp_path)
    atlas = scan_atlas_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = repository.import_atlases([atlas]).imported[0]
    assert imported.asset_type == "atlas"
    assert imported.asset_dir == library / "atlases" / "grass" / "dry-grass"
    document = json.loads((imported.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert document["type"] == "atlas"
    assert document["category"] == "Grass"
    assert isinstance(document["tags"], list)
    assert "surface" not in {tag.casefold() for tag in document["tags"]}
    assert "categories" not in document
    assert repository.list_atlas_assets()[0].id == imported.id
    assert repository.list_texture_assets() == []
    assert (imported.asset_dir / "extras" / "Thumbs" / "grass_2K_Albedo.jpg").is_file()

    texture = scan_atlas_folder(source).materials[0]
    texture.asset_type = "texture_set"
    texture_import = repository.import_materials([texture])
    assert len(texture_import.imported) == 1
    assert texture_import.imported[0].asset_dir.parent.parent == library / "textures"


def test_mixed_scan_keeps_unrelated_megascans_decal_as_texture(tmp_path: Path) -> None:
    source = tmp_path / "ordinary_decal"
    source.mkdir()
    document = {
        "id": "decal-id",
        "categories": ["decal", "paint"],
        "semanticTags": {"name": "Paint Mark", "asset_type": "decal"},
        "maps": [
            {"uri": "paint_1K_Albedo.jpg", "type": "albedo", "resolution": "1024x1024"},
            {"uri": "paint_1K_Opacity.jpg", "type": "opacity", "resolution": "1024x1024"},
        ],
    }
    (source / "asset.json").write_text(json.dumps(document), encoding="utf-8")
    _image(source / "paint_1K_Albedo.jpg", 1024, 1024)
    _image(source / "paint_1K_Opacity.jpg", 1024, 1024)
    assert detect_asset_type(source)[0] == "texture_set"
    assert scan_mixed_folder(source).materials[0].asset_type == "texture_set"


@pytest.mark.skipif(not Path("/home/gambit/000test").is_dir(), reason="Atlas integration fixture unavailable")
def test_supplied_megascans_atlas_fixture(request) -> None:
    result = scan_atlas_folder("/home/gambit/000test")
    request.addfinalizer(
        lambda: [
            shutil.rmtree(path, ignore_errors=True)
            for path in result.temporary_roots
        ]
    )
    candidates = result.materials
    expected = {"Spanish Moss", "Dry Ryegrass", "Bay Leaf", "Chestnut", "Pine Family"}
    if not expected.issubset({candidate.name for candidate in candidates}):
        pytest.skip("/home/gambit/000test currently contains a different integration fixture")
    assert len(candidates) == 5
    assert all(candidate.asset_type == "atlas" for candidate in candidates)
    assert all(candidate.resolution_labels == ["4K"] for candidate in candidates)
    assert {candidate.name for candidate in candidates} == expected
    assert all(candidate.selected_thumbnail.endswith("_Preview.png") for candidate in candidates)
    assert all(candidate.selected_hero.startswith("previews/") for candidate in candidates)
