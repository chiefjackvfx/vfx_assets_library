from pathlib import Path

import pytest

from universal_asset_library.domain import (
    LibraryMap,
    LibraryModelAsset,
    LibraryModelFile,
    LibraryModelTextureSet,
    LibraryResolution,
)
from universal_asset_library.integrations import (
    ModelExportError,
    model_export_options,
    prepare_model_export,
)


def _asset(asset_dir: Path) -> LibraryModelAsset:
    return LibraryModelAsset(
        id="model-id",
        name="Oak Tree",
        category="Nature",
        tags=(),
        description="",
        author="",
        physical_size="",
        provider="Poly Haven",
        provider_id="oak-tree",
        asset_dir=asset_dir,
        model_files=(
            LibraryModelFile("usd/oak_2k.usdc", "", "USDC", "primary", "LOD1", "", None, False, 3, "a", "2K"),
            LibraryModelFile("usd/oak_4k.usdc", "", "USDC", "primary", "LOD0", "", None, True, 3, "b", "4K"),
            LibraryModelFile("models/oak.fbx", "", "FBX", "alternative", "", "", None, False, 3, "c"),
        ),
        texture_sets={},
        thumbnail_path=None,
        hero_path=None,
        fingerprint="fingerprint",
        created_at="now",
        total_size=9,
    )


def test_model_export_uses_preferred_usd_and_selected_variant(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "oak"
    (asset_dir / "usd").mkdir(parents=True)
    (asset_dir / "usd" / "oak_2k.usdc").write_bytes(b"usd")
    (asset_dir / "usd" / "oak_4k.usdc").write_bytes(b"usd")
    asset = _asset(asset_dir)
    assert [item.path for item in model_export_options(asset)] == [
        "usd/oak_4k.usdc", "usd/oak_2k.usdc",
    ]
    preferred = prepare_model_export(asset, library_root=tmp_path)
    assert preferred.asset_slug == "oak-tree"
    assert preferred.model.resolution == "4K"
    selected = prepare_model_export(asset, "usd/oak_2k.usdc", tmp_path)
    assert selected.model.lod == "LOD1"
    assert selected.document()["model_path"].endswith("oak_2k.usdc")


def test_model_export_rejects_unknown_missing_and_outside_paths(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "oak"
    (asset_dir / "usd").mkdir(parents=True)
    (asset_dir / "usd" / "oak_4k.usdc").write_bytes(b"usd")
    asset = _asset(asset_dir)
    with pytest.raises(ModelExportError, match="not part"):
        prepare_model_export(asset, "usd/unknown.usdc", tmp_path)
    with pytest.raises(ModelExportError, match="unavailable"):
        prepare_model_export(asset, "usd/oak_2k.usdc", tmp_path)
    outside = tmp_path.parent / "outside.usdc"
    outside.write_bytes(b"usd")
    unsafe = LibraryModelAsset(
        **{
            **{field: getattr(asset, field) for field in asset.__dataclass_fields__ if field != "model_files"},
            "model_files": (
                LibraryModelFile(str(outside), "", "USDC", "primary", "", "", None, True, 3, "x"),
            ),
        }
    )
    with pytest.raises(ModelExportError, match="unsafe"):
        prepare_model_export(unsafe, library_root=tmp_path)


def test_model_export_includes_managed_material_maps_for_houdini_sop(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "oak"
    (asset_dir / "models").mkdir(parents=True)
    (asset_dir / "usd").mkdir()
    (asset_dir / "maps").mkdir()
    (asset_dir / "usd" / "oak_4k.usdc").write_bytes(b"usd")
    (asset_dir / "maps" / "oak_base.jpg").write_bytes(b"base")
    (asset_dir / "maps" / "oak_normal.jpg").write_bytes(b"normal")
    asset = _asset(asset_dir)
    maps = {
        "Base Color": (
            LibraryMap("Base Color", "maps/oak_base.jpg", "JPG", 4, "base", color_space="sRGB"),
        ),
        "Normal": (
            LibraryMap("Normal", "maps/oak_normal.jpg", "JPG", 6, "normal", color_space="Raw"),
        ),
    }
    asset = LibraryModelAsset(**{
        **{
            field: getattr(asset, field)
            for field in asset.__dataclass_fields__
            if field != "texture_sets"
        },
        "texture_sets": {
            "Default": LibraryModelTextureSet(
                "Default", {"4K": LibraryResolution("4K", 4096, 4096, maps)}
            )
        },
    })

    document = prepare_model_export(asset, library_root=tmp_path).document()

    assert document["texture_sets"][0]["name"] == "Default"
    assert {
        item["channel"] for item in document["texture_sets"][0]["maps"]
    } == {"Base Color", "Normal"}
