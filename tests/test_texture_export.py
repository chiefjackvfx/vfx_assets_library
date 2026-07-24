from __future__ import annotations

from pathlib import Path

import pytest

from universal_asset_library.domain import LibraryMap, LibraryResolution, LibraryTextureAsset
from universal_asset_library.integrations import TextureExportError, default_texture_resolution, prepare_texture_export


def _asset(root: Path) -> LibraryTextureAsset:
    asset_dir = root / "textures" / "stone"
    (asset_dir / "maps" / "1K").mkdir(parents=True, exist_ok=True)
    (asset_dir / "maps" / "4K").mkdir(parents=True, exist_ok=True)
    for relative in ("maps/1K/base.jpg", "maps/4K/base.jpg", "maps/4K/rough.exr", "maps/4K/arm.png"):
        (asset_dir / relative).write_bytes(relative.encode())
    one = LibraryResolution("1K", 1024, 1024, {
        "Base Color": (LibraryMap("Base Color", "maps/1K/base.jpg", "JPG", 1, "a", preferred=True),),
    })
    four = LibraryResolution("4K", 4096, 4096, {
        "Base Color": (LibraryMap("Base Color", "maps/4K/base.jpg", "JPG", 1, "b", preferred=True),),
        "Roughness": (LibraryMap("Roughness", "maps/4K/rough.exr", "EXR", 1, "c", color_space="Linear", preferred=True),),
        "Packed ARM": (LibraryMap(
            "Packed ARM", "maps/4K/arm.png", "PNG", 1, "d", preferred=True,
            packed_channels={"R": "Ambient Occlusion", "G": "Roughness", "B": "Metalness"},
        ),),
    })
    return LibraryTextureAsset(
        "asset-id", "Stone", "Stone", (), "", "", "", "Folder", "", asset_dir,
        {"1K": one, "4K": four}, None, None, "fp", "now", 4,
    )


def test_prepare_texture_export_defaults_to_4k_and_prefers_explicit_channels(tmp_path) -> None:
    asset = _asset(tmp_path)
    payload = prepare_texture_export(asset, library_root=tmp_path)
    assert payload.resolution == "4K"
    assert [item.channel for item in payload.maps] == ["Base Color", "Packed ARM", "Roughness"]
    packed = next(item for item in payload.maps if item.channel == "Packed ARM")
    assert packed.packed_channels == {"R": "Ambient Occlusion", "B": "Metalness"}
    assert next(item for item in payload.maps if item.channel == "Base Color").color_space == "sRGB"
    assert next(item for item in payload.maps if item.channel == "Roughness").color_space == "Linear"


def test_resolution_default_uses_smallest_variant_above_4k() -> None:
    assert default_texture_resolution({"8K": object(), "16K": object()}) == "8K"


def test_prepare_texture_export_rejects_missing_and_outside_files(tmp_path) -> None:
    asset = _asset(tmp_path)
    (asset.asset_dir / "maps/4K/base.jpg").unlink()
    with pytest.raises(TextureExportError, match="unavailable"):
        prepare_texture_export(asset, "4K", tmp_path)
    outside = tmp_path.parent / "outside-library"
    outside.mkdir(exist_ok=True)
    with pytest.raises(TextureExportError, match="outside"):
        prepare_texture_export(_asset(tmp_path), "4K", outside)
