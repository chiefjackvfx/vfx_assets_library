from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_asset_library.domain import LibraryVdbAsset
from universal_asset_library.importer import ScanCancellationToken, scan_vdb_folder
from universal_asset_library.library import AssetMetadataPatch, LibraryRepository
from universal_asset_library.library.catalog import decode_asset, encode_asset


def _vdb(path: Path, payload: bytes = b"VDB fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_cloud_collection_groups_numbered_assets_with_quality_variants(tmp_path: Path) -> None:
    for number in (1, 2):
        for label in ("Low", "Mid", "High"):
            _vdb(tmp_path / f"cloud_formation_{number:03d}_{label}_Res.vdb", f"{number}-{label}".encode())

    result = scan_vdb_folder(tmp_path)

    assert [item.name for item in result.materials] == ["Cloud Formation 001", "Cloud Formation 002"]
    for candidate in result.materials:
        assert candidate.category == "Clouds"
        assert candidate.resolution_labels == ["Low", "Mid", "High"]
        assert all(len(variant.files) == 1 for variant in candidate.variants.values())
        assert not any(variant.is_sequence for variant in candidate.variants.values())


def test_quality_sequence_preserves_frames_padding_and_gaps(tmp_path: Path) -> None:
    for frame in (1001, 1003):
        _vdb(tmp_path / f"smoke_high_{frame}.vdb", str(frame).encode())

    candidate = scan_vdb_folder(tmp_path).materials[0]
    variant = candidate.variants["High"]

    assert candidate.name == "Smoke"
    assert candidate.category == "Smoke"
    assert variant.is_sequence
    assert (variant.frame_start, variant.frame_end, variant.padding) == (1001, 1003, 4)
    assert variant.missing_frames == (1002,)
    assert any(item.code == "vdb_sequence_gaps" for item in candidate.diagnostics)


def test_standalone_numbered_vdb_is_static_and_extension_is_case_insensitive(tmp_path: Path) -> None:
    _vdb(tmp_path / "magic_orb_007.VDB")

    candidate = scan_vdb_folder(tmp_path).materials[0]

    assert candidate.name == "Magic Orb 007"
    assert candidate.resolution_labels == ["Default"]
    assert not candidate.variants["Default"].is_sequence
    assert candidate.variants["Default"].files[0].frame is None


def test_mixed_folders_pair_normalized_still_and_video_previews(tmp_path: Path) -> None:
    _vdb(tmp_path / "cloud" / "cloud_bank_Low_Res.vdb")
    _vdb(tmp_path / "cloud" / "cloud_bank_High_Res.vdb")
    (tmp_path / "cloud" / "cloud_bank_preview.PNG").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
        )
    )
    _vdb(tmp_path / "smoke" / "smoke_high_0101.VDB")
    _vdb(tmp_path / "smoke" / "smoke_high_0102.vdb")
    (tmp_path / "smoke" / "smoke_preview.mp4").write_bytes(b"preview")

    result = scan_vdb_folder(tmp_path)
    cloud, smoke = result.materials

    assert cloud.selected_thumbnail.endswith("cloud_bank_preview.PNG")
    assert smoke.selected_preview_video.endswith("smoke_preview.mp4")
    assert smoke.variants["High"].is_sequence


def test_vdb_scan_excludes_symlinks_and_honors_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "cloud_low.vdb"
    _vdb(source)
    (tmp_path / "cloud_high.vdb").symlink_to(source)

    result = scan_vdb_folder(tmp_path)
    assert len(result.materials[0].variants["Low"].files) == 1
    assert any(item.code == "symlink_ignored" for item in result.diagnostics)

    token = ScanCancellationToken()
    token.cancel()
    canceled = scan_vdb_folder(tmp_path, cancel_token=token)
    assert canceled.canceled
    assert not canceled.materials


def test_vdb_import_manifest_catalog_and_managed_layout(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    for label in ("Low", "Mid", "High"):
        _vdb(source / f"cloud_formation_001_{label}_Res.vdb", label.encode())

    candidate = scan_vdb_folder(source).materials[0]
    summary = LibraryRepository(library).import_vdbs([candidate])

    assert not summary.failed
    asset = summary.imported[0]
    assert isinstance(asset, LibraryVdbAsset)
    assert asset.asset_dir == library / "vdbs" / "clouds" / "cloud-formation-001"
    assert list(asset.variants) == ["Low", "Mid", "High"]
    assert all((asset.asset_dir / variant.files[0].path).is_file() for variant in asset.variants.values())
    document = json.loads((asset.asset_dir / "asset.json").read_text(encoding="utf-8"))
    assert document["type"] == "vdb"
    assert document["variants"]["Mid"]["label"] == "Mid"
    assert document["variants"]["Mid"]["mode"] == "static"
    assert document["variants"]["Mid"]["is_sequence"] is False
    assert isinstance(decode_asset(encode_asset(asset)), LibraryVdbAsset)
    assert LibraryRepository(library).list_vdb_assets()[0].id == asset.id

    duplicate = LibraryRepository(library).import_vdbs([candidate])
    assert not duplicate.imported
    assert len(duplicate.skipped) == 1
    assert duplicate.skipped[0].startswith(candidate.name)

    moved = LibraryRepository(library).patch_asset_metadata(
        asset.id, AssetMetadataPatch(category="Fog")
    )
    assert isinstance(moved, LibraryVdbAsset)
    assert moved.category == "Fog"
    assert moved.asset_dir.parent == library / "vdbs" / "fog"


def test_real_cloud_pack_scans_as_75_static_assets() -> None:
    root = Path("/home/gambit/Downloads/vdb_series_clouds_volume_014_cloud_formation")
    if not root.is_dir():
        pytest.skip("Read-only cloud VDB pack is unavailable")

    result = scan_vdb_folder(root)

    assert len(result.materials) == 75
    assert {item.name for item in result.materials} == {
        f"Cloud Formation {number:03d}" for number in range(1, 76)
    }
    assert all(item.category == "Clouds" for item in result.materials)
    assert all(item.resolution_labels == ["Low", "Mid", "High"] for item in result.materials)
    assert all(
        len(variant.files) == 1 and not variant.is_sequence
        for item in result.materials for variant in item.variants.values()
    )
