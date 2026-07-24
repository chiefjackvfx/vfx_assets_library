import json
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.domain import LibraryModelAsset
from universal_asset_library.importer import ModelCandidate, scan_model_folder
from universal_asset_library.library import LibraryRepository


def image(path: Path, color: str = "#777777", width: int = 128, height: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = QImage(width, height or width, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    assert value.save(str(path))


def model_source(tmp_path: Path, *, usd: bool = True) -> Path:
    source = tmp_path / "painted_chair"
    source.mkdir()
    (source / "painted_chair.blend").write_bytes(b"BLENDER-scene")
    if usd:
        (source / "painted_chair.usdc").write_bytes(b"PXR-USDC")
    (source / "mesh_LOD0.fbx").write_bytes(b"FBX-LOD0")
    image(source / "textures" / "chair_basecolor_1k.png", "#76543a", 64)
    image(source / "chair_preview.jpg", "#76543a", 192, 128)
    (source / "notes.txt").write_text("Keep this companion.", encoding="utf-8")
    (source / "asset.zip").write_bytes(b"archive")
    (source / "render.rs").write_bytes(b"renderer proxy")
    (source / "cached.rat").write_bytes(b"renderer cache")
    hidden = source / ".mayaSwatches"
    hidden.mkdir()
    (hidden / "swatch.png").write_bytes(b"hidden")
    return source


def test_model_scan_prefers_usd_and_excludes_renderer_archives_and_hidden_data(tmp_path) -> None:
    source = model_source(tmp_path)
    result = scan_model_folder(source)
    assert len(result.materials) == 1
    candidate = result.materials[0]
    assert isinstance(candidate, ModelCandidate)
    assert candidate.provider == "Unknown"
    assert candidate.usd_ready
    assert candidate.preferred_model is not None
    assert candidate.preferred_model.file_format == "USDC"
    assert {item.file_format for item in candidate.model_files} == {"BLEND", "USDC", "FBX"}
    texture_variant = next(iter(candidate.resolutions.values()))
    assert "Base Color" in texture_variant.maps
    assert "notes.txt" in candidate.extra_paths
    assert candidate.excluded_paths["asset.zip"] == "Packaged archive"
    assert candidate.excluded_paths["render.rs"] == "Renderer proxy"
    assert candidate.excluded_paths["cached.rat"] == "Renderer texture cache"
    assert any(path.startswith(".mayaSwatches") for path in candidate.excluded_paths)
    library = tmp_path / "library"
    library.mkdir()
    imported = LibraryRepository(library).import_models([candidate]).imported[0]
    assert imported.preferred_model.path.startswith("usd/")
    assert (imported.asset_dir / imported.preferred_model.path).is_file()


def test_non_usd_model_imports_with_best_available_fallback(tmp_path) -> None:
    source = model_source(tmp_path, usd=False)
    candidate = scan_model_folder(source).materials[0]
    assert isinstance(candidate, ModelCandidate)
    assert not candidate.usd_ready
    assert candidate.preferred_model is not None
    assert candidate.preferred_model.file_format == "BLEND"

    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    summary = repository.import_models([candidate])
    assert not summary.failed
    assert len(summary.imported) == 1
    asset = summary.imported[0]
    assert isinstance(asset, LibraryModelAsset)
    assert not asset.usd_ready
    assert asset.preferred_model and asset.preferred_model.file_format == "BLEND"
    assert asset.asset_dir == library / "models" / "uncategorized" / "painted-chair"
    assert not (asset.asset_dir / "source").exists()
    assert (asset.asset_dir / "models" / "Painted_Chair_BLEND.blend").read_bytes() == b"BLENDER-scene"
    assert (asset.asset_dir / "models" / "Painted_Chair_LOD0.fbx").is_file()
    assert (asset.asset_dir / "maps" / "64PX" / "Painted_Chair_64px_BaseColor.png").is_file()
    assert (asset.asset_dir / "extras" / "notes.txt").is_file()
    assert not (asset.asset_dir / "asset.zip").exists()
    assert not (asset.asset_dir / "render.rs").exists()
    assert not (asset.asset_dir / "cached.rat").exists()
    assert not (asset.asset_dir / ".mayaSwatches").exists()
    document = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert document["type"] == "model"
    assert document["category"] == "Uncategorized"
    assert isinstance(document["tags"], list)
    assert "surface" not in {tag.casefold() for tag in document["tags"]}
    assert "categories" not in document
    assert document["fingerprint"]
    assert {entry["reason"] for entry in document["excluded_files"]} >= {
        "Packaged archive", "Renderer proxy", "Renderer texture cache"
    }
    assert repository.list_model_assets()[0].id == asset.id
    assert repository.library_update_count() == 0


def test_update_library_removes_legacy_model_source_folder(tmp_path) -> None:
    candidate = scan_model_folder(model_source(tmp_path, usd=False)).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_models([candidate]).imported[0]
    manifest_path = asset.asset_dir / "asset.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = list(document["model_files"])
    for texture_set in document["texture_sets"].values():
        for variant in texture_set["resolutions"].values():
            for alternatives in variant["maps"].values():
                records.extend(alternatives)
    records.extend(document["extra_files"])
    for record in records:
        old = asset.asset_dir / record["path"]
        new = asset.asset_dir / "source" / record["original_path"]
        new.parent.mkdir(parents=True, exist_ok=True)
        old.replace(new)
        record["path"] = new.relative_to(asset.asset_dir).as_posix()
    for role, relative in list(document["previews"].items()):
        if relative.startswith("previews/"):
            continue
        old = asset.asset_dir / relative
        new = asset.asset_dir / "source" / relative
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.replace(new)
        document["previews"][role] = new.relative_to(asset.asset_dir).as_posix()
    document["layout_version"] = 2
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    assert repository.library_update_count() == 1
    summary = repository.update_library()
    assert len(summary.updated) == 1
    updated = repository.list_model_assets()[0]
    assert not (updated.asset_dir / "source").exists()
    assert (updated.asset_dir / "models" / "Painted_Chair_BLEND.blend").is_file()
    assert (updated.asset_dir / "models" / "Painted_Chair_LOD0.fbx").is_file()
    assert (updated.asset_dir / "maps" / "64PX" / "Painted_Chair_64px_BaseColor.png").is_file()
    assert repository.library_update_count() == 0


def test_real_fixture_detects_six_expected_models() -> None:
    root = Path("/home/gambit/000test")
    if not root.is_dir():
        pytest.skip("Read-only integration fixture is unavailable")
    if not any(path.suffix.casefold() in {".usd", ".usda", ".usdc", ".usdz", ".fbx", ".obj", ".abc", ".gltf", ".glb", ".blend", ".ma", ".mb"} for path in root.rglob("*")):
        pytest.skip("The live integration folder currently contains no model fixtures")
    result = scan_model_folder(root)
    names = {candidate.name for candidate in result.materials}
    expected = {
        "Painted Wooden Chair 02",
        "Painted Wooden Shelves",
        "Modular Shrine Roof Apex",
        "Stakes and Wedges Pack",
        "Small Limestone Rocks Pack",
        "Raspberry",
    }
    assert expected <= names
    chair = next(item for item in result.materials if item.name == "Painted Wooden Chair 02")
    shelves = next(item for item in result.materials if item.name == "Painted Wooden Shelves")
    raspberry = next(item for item in result.materials if item.name == "Raspberry")
    assert chair.usd_ready and chair.preferred_model and chair.preferred_model.file_format == "USDC"
    assert not shelves.usd_ready
    assert {item.file_format for item in raspberry.model_files} == {"MA", "MB", "FBX", "OBJ"} or {
        item.file_format for item in raspberry.model_files
    } == {"MB", "FBX", "OBJ"}
    assert any(path.lower().endswith(".zip") for path in raspberry.excluded_paths)
    assert any(path.lower().endswith(".rs") for path in raspberry.excluded_paths)
