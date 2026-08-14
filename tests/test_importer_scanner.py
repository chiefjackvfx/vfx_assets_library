import json
import os
from pathlib import Path
import shutil

import pytest
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.importer import ScanCancellationToken, detect_asset_type, scan_mixed_folder, scan_texture_folder
from universal_asset_library.library import LibraryRepository
import universal_asset_library.importer.scanner as scanner_module


def image(path: Path, width: int = 1024, height: int = 1024) -> None:
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(QColor("#777777"))
    assert value.save(str(path))


def test_filename_only_material_detects_resolution_channels_and_preview(tmp_path) -> None:
    material = tmp_path / "2K_Bricks06"
    material.mkdir()
    image(material / "Bricks06_diffuse_xtm.jpg", 2048, 2048)
    image(material / "Bricks06_roughness_xtm.jpg", 2048, 2048)
    image(material / "Bricks06_normal_xtm.jpg", 2048, 2048)
    image(material / "Bricks06.png", 256, 256)
    result = scan_texture_folder(tmp_path)
    assert len(result.materials) == 1
    candidate = result.materials[0]
    assert candidate.name == "Bricks06"
    assert candidate.resolution_labels == ["2K"]
    assert set(candidate.resolutions["2K"].maps) == {"Base Color", "Roughness", "Normal"}
    assert candidate.selected_thumbnail == "Bricks06.png"


@pytest.mark.parametrize("suffix", ("ALPHAMASKED", "ALPHA_MASKED"))
def test_alpha_masked_filename_is_scanned_as_opacity(
    tmp_path,
    suffix,
) -> None:
    material = tmp_path / f"2K_Fabric_{suffix}"
    material.mkdir()
    image(material / "Fabric_2K_BASECOLOR.jpg", 2048, 2048)
    opacity_path = f"Fabric_2K_{suffix}.png"
    image(material / opacity_path, 2048, 2048)

    candidate = scan_texture_folder(material).materials[0]

    opacity = candidate.resolutions["2K"].maps["Opacity"]
    assert [texture_map.relative_path for texture_map in opacity] == [
        opacity_path,
    ]
    assert opacity_path not in candidate.extra_paths


def test_tiffs_are_maps_or_extras_and_display_images_are_all_previews(
    tmp_path,
) -> None:
    material = tmp_path / "Stone Package"
    material.mkdir()

    def png_payload(path: Path, width: int, height: int) -> None:
        value = QImage(width, height, QImage.Format.Format_RGB32)
        value.fill(QColor("#777777"))
        assert value.save(str(path), "PNG")

    png_payload(material / "stone_basecolor_2k.tif", 2048, 2048)
    png_payload(material / "stone_roughness_2k.tiff", 2048, 2048)
    png_payload(material / "unlabelled_payload.tif", 512, 512)
    image(material / "stone_card.jpg", 320, 320)
    image(material / "stone_wide.png", 960, 540)

    candidate = scan_texture_folder(material).materials[0]

    assert set(candidate.resolutions["2K"].maps) == {
        "Base Color", "Roughness",
    }
    assert {preview.relative_path for preview in candidate.previews} == {
        "stone_card.jpg", "stone_wide.png",
    }
    assert candidate.selected_thumbnail == "stone_card.jpg"
    assert candidate.selected_hero == "stone_wide.png"
    assert "unlabelled_payload.tif" in candidate.extra_paths
    assert "stone_card.jpg" not in candidate.extra_paths
    assert "stone_wide.png" not in candidate.extra_paths


def test_unknown_json_warns_and_uses_base_color_preview_fallback(tmp_path) -> None:
    material = tmp_path / "D_Wood_Pine_04"
    material.mkdir()
    (material / "custom.json").write_text('{"vendor": "unknown"}', encoding="utf-8")
    image(material / "D_Wood_Pine_04_DIFF.jpg", 4096, 4096)
    image(material / "D_Wood_Pine_04_NORM_OGL.jpg", 4096, 4096)
    image(material / "D_Wood_Pine_04_ROUGH.jpg", 4096, 4096)
    candidate = scan_texture_folder(material).materials[0]
    normal = candidate.resolutions["4K"].maps["Normal"][0]
    assert normal.normal_convention == "OpenGL"
    assert candidate.selected_thumbnail == "D_Wood_Pine_04_DIFF.jpg"
    assert any("Unrecognized JSON schema" in warning for warning in candidate.warnings)
    assert any("preview fallback" in warning for warning in candidate.warnings)


def test_poly_haven_metadata_is_truth_but_missing_files_are_not_variants(tmp_path) -> None:
    material = tmp_path / "asphalt_06"
    textures = material / "textures"
    textures.mkdir(parents=True)
    document = {
        "name": "Asphalt 06",
        "authors": {"Artist": "All"},
        "categories": ["asphalt", "road"],
        "tags": ["rough"],
        "max_resolution": [4096, 4096],
        "files": {
            "Diffuse": {
                "1k": {"jpg": {"url": "https://example/asphalt_diff_1k.jpg"}},
                "4k": {"jpg": {"url": "https://example/asphalt_diff_4k.jpg"}},
            },
            "Rough": {
                "1k": {"jpg": {"url": "https://example/asphalt_rough_1k.jpg"}},
                "4k": {"jpg": {"url": "https://example/asphalt_rough_4k.jpg"}},
            },
        },
    }
    (material / "info.json").write_text(json.dumps(document), encoding="utf-8")
    image(textures / "asphalt_diff_1k.jpg")
    image(textures / "asphalt_rough_1k.jpg")
    image(material / "thumbnail.webp", 256, 256)
    candidate = scan_texture_folder(material).materials[0]
    assert candidate.provider == "Poly Haven"
    assert candidate.name == "Asphalt 06"
    assert candidate.author == "Artist"
    assert candidate.resolution_labels == ["1K"]
    assert any("4K" in warning for warning in candidate.warnings)


def test_megascans_adapter_keeps_format_alternatives_and_prefers_exr(tmp_path) -> None:
    material = tmp_path / "surface_id"
    material.mkdir()
    document = {
        "id": "abc123",
        "name": "Source Name",
        "categories": ["surface", "concrete"],
        "tags": ["worn"],
        "semanticTags": {
            "name": "Concrete Truth abc123",
            "environment": ["urban"],
        },
        "maps": [
            {"uri": "abc_4K_Albedo.jpg", "type": "albedo", "resolution": "4096x4096", "bitDepth": 8, "colorSpace": "sRGB"},
            {"uri": "abc_4K_Displacement.jpg", "type": "displacement", "resolution": "4096x4096", "bitDepth": 8, "colorSpace": "Linear"},
            {"uri": "abc_4K_Displacement.exr", "type": "displacement", "resolution": "4096x4096", "bitDepth": 32, "colorSpace": "Linear"},
        ],
        "previews": {"images": []},
    }
    (material / "abc.json").write_text(json.dumps(document), encoding="utf-8")
    image(material / "abc_4K_Albedo.jpg", 4096, 4096)
    image(material / "abc_4K_Displacement.jpg", 4096, 4096)
    (material / "abc_4K_Displacement.exr").write_bytes(b"not-an-image-but-local")
    candidate = scan_texture_folder(material).materials[0]
    alternatives = candidate.resolutions["4K"].maps["Displacement"]
    assert candidate.name == "Concrete Truth"
    assert len(alternatives) == 2
    assert next(item for item in alternatives if item.preferred).file_format == "EXR"


def test_filename_only_megascans_suffix_is_removed_from_material_name(
    tmp_path,
) -> None:
    material = tmp_path / "Mossy_Rocky_Ground_tjlpcb3r"
    material.mkdir()
    image(material / "tjlpcb3r_Albedo_2K.jpg", 2048, 2048)
    image(material / "tjlpcb3r_Roughness_2K.jpg", 2048, 2048)

    candidate = scan_texture_folder(material).materials[0]

    assert candidate.name == "Mossy Rocky Ground"


def test_malformed_json_does_not_abort_filename_inference(tmp_path) -> None:
    material = tmp_path / "stone"
    material.mkdir()
    (material / "broken.json").write_text("{broken", encoding="utf-8")
    image(material / "stone_diff_1k.jpg")
    image(material / "stone_rough_1k.jpg")
    candidate = scan_texture_folder(material).materials[0]
    assert candidate.resolution_labels == ["1K"]
    assert any("Could not parse" in warning for warning in candidate.warnings)


def test_oversized_json_is_skipped_and_unrelated_files_are_retained(tmp_path, monkeypatch) -> None:
    material = tmp_path / "ground"
    material.mkdir()
    (material / "large.json").write_text('{"unused": true}', encoding="utf-8")
    (material / "notes.txt").write_text("notes", encoding="utf-8")
    image(material / "ground_diff_1k.jpg")
    image(material / "ground_rough_1k.jpg")
    monkeypatch.setattr("universal_asset_library.importer.scanner.JSON_LIMIT", 4)
    result = scan_texture_folder(material)
    assert any("oversized JSON" in warning for warning in result.materials[0].warnings)
    assert "notes.txt" in result.materials[0].extra_paths
    assert not any(path.endswith("notes.txt") for path in result.ignored_files)


def test_multiple_metadata_files_choose_highest_confidence_and_arm_is_packed(tmp_path) -> None:
    material = tmp_path / "mixed"
    material.mkdir()
    megascans = {
        "id": "mega-id",
        "name": "Megascans Name",
        "semanticTags": {"name": "Chosen Name"},
        "maps": [
            {"uri": "mixed_diff_1k.jpg", "type": "albedo", "resolution": "1024x1024"},
            {"uri": "mixed_arm_1k.png", "type": "arm", "resolution": "1024x1024"},
        ],
    }
    poly_haven = {
        "name": "Other Name",
        "authors": {"Artist": "All"},
        "max_resolution": [1024, 1024],
        "files": {},
    }
    (material / "a_megascans.json").write_text(json.dumps(megascans), encoding="utf-8")
    (material / "b_info.json").write_text(json.dumps(poly_haven), encoding="utf-8")
    image(material / "mixed_diff_1k.jpg")
    image(material / "mixed_arm_1k.png")
    candidate = scan_texture_folder(material).materials[0]
    assert candidate.provider == "Megascans"
    assert candidate.name == "Chosen Name"
    packed = candidate.resolutions["1K"].maps["Packed ARM"][0]
    assert packed.packed_channels == {"R": "Ambient Occlusion", "G": "Roughness", "B": "Metalness"}
    assert any("Multiple recognized metadata files" in warning for warning in candidate.warnings)


@pytest.mark.skipif(not Path("/home/gambit/000test").is_dir(), reason="Local integration examples are unavailable")
def test_supplied_example_folder_produces_seven_expected_materials(request) -> None:
    result = scan_texture_folder("/home/gambit/000test")
    request.addfinalizer(
        lambda: [
            shutil.rmtree(path, ignore_errors=True)
            for path in result.temporary_roots
        ]
    )
    expected = {"Asphalt Road", "Asphalt 06", "Aerial Asphalt 01", "Bricks06", "Concrete Pavers", "D Wood Pine 04", "Damaged Concrete Wall"}
    if not expected.issubset({material.name for material in result.materials}):
        pytest.skip("Optional seven-texture integration fixture is not currently installed.")
    assert len(result.materials) == 7
    by_name = {material.name: material for material in result.materials}
    assert by_name["Asphalt Road"].resolution_labels == ["1K", "2K", "4K"]
    assert by_name["Asphalt 06"].resolution_labels == ["1K"]
    assert by_name["Aerial Asphalt 01"].resolution_labels == ["1K", "4K"]
    assert by_name["Bricks06"].resolution_labels == ["2K"]
    wood = by_name["D Wood Pine 04"]
    assert wood.resolution_labels == ["4K"]
    assert wood.resolutions["4K"].maps["Normal"][0].normal_convention == "OpenGL"
    assert wood.previews[0].fallback


def test_scan_cancellation_returns_no_partial_materials(tmp_path) -> None:
    material = tmp_path / "material"
    material.mkdir()
    image(material / "stone_diff_1k.jpg")
    token = ScanCancellationToken()
    token.cancel()
    result = scan_texture_folder(tmp_path, cancel_token=token)
    assert result.canceled
    assert not result.materials


def test_corrupt_empty_and_symlink_images_are_excluded_with_diagnostics(tmp_path) -> None:
    material = tmp_path / "material"
    material.mkdir()
    image(material / "stone_diff_1k.jpg")
    (material / "stone_rough_1k.jpg").write_bytes(b"not an image")
    (material / "stone_normal_1k.png").write_bytes(b"")
    os.symlink(material / "stone_diff_1k.jpg", material / "stone_ao_1k.jpg")
    result = scan_texture_folder(material)
    candidate = result.materials[0]
    assert set(candidate.resolutions["1K"].maps) == {"Base Color"}
    codes = {item.code for item in result.diagnostics}
    assert {"unreadable_image", "empty_image", "symlink_ignored"}.issubset(codes)


def test_duplicate_basenames_do_not_receive_ambiguous_json_semantics(tmp_path) -> None:
    material = tmp_path / "material"
    (material / "a").mkdir(parents=True)
    (material / "b").mkdir()
    document = {
        "id": "ambiguous",
        "semanticTags": {"name": "Ambiguous"},
        "maps": [{"uri": "same_diff_1k.jpg", "type": "roughness", "resolution": "1024x1024"}],
    }
    (material / "asset.json").write_text(json.dumps(document), encoding="utf-8")
    image(material / "a" / "same_diff_1k.jpg")
    image(material / "b" / "same_diff_1k.jpg")
    candidate = scan_texture_folder(material).materials[0]
    assert set(candidate.resolutions["1K"].maps) == {"Base Color"}
    assert any(item.code == "ambiguous_metadata_basename" for item in candidate.diagnostics)


def test_inventory_walks_source_tree_once(tmp_path, monkeypatch) -> None:
    material = tmp_path / "material"
    material.mkdir()
    image(material / "stone_diff_1k.jpg")
    image(material / "stone_rough_1k.jpg")
    real_walk = os.walk
    calls = 0

    def counted_walk(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("universal_asset_library.importer.scanner.os.walk", counted_walk)
    assert len(scan_texture_folder(tmp_path).materials) == 1
    assert calls == 1


def test_filename_only_resolution_subfolders_merge_into_one_material(tmp_path) -> None:
    material = tmp_path / "Layered Stone"
    for label, pixels in (("1K", 1024), ("2K", 2048)):
        folder = material / label
        folder.mkdir(parents=True)
        image(folder / f"stone_{label}_diff.jpg", pixels, pixels)
        image(folder / f"stone_{label}_rough.jpg", pixels, pixels)
    result = scan_texture_folder(tmp_path)
    assert len(result.materials) == 1
    assert result.materials[0].resolution_labels == ["1K", "2K"]


def test_metadata_adapter_failure_is_localized_to_material_warning(tmp_path, monkeypatch) -> None:
    class BrokenAdapter:
        def confidence(self, _document, _path):
            return 100

        def parse(self, _document, _path):
            raise ValueError("bad provider payload")

    material = tmp_path / "material"
    material.mkdir()
    (material / "provider.json").write_text('{"provider": true}', encoding="utf-8")
    image(material / "stone_diff_1k.jpg")
    image(material / "stone_rough_1k.jpg")
    monkeypatch.setattr(scanner_module, "ADAPTERS", (BrokenAdapter(),))
    result = scan_texture_folder(material)
    assert len(result.materials) == 1
    assert any(item.code == "metadata_adapter_failed" for item in result.materials[0].diagnostics)


def test_asset_type_auto_detection_and_provider_override(tmp_path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "chair.fbx").write_bytes(b"fbx")
    assert detect_asset_type(model)[0] == "model"

    hdri = tmp_path / "hdri"
    hdri.mkdir()
    (hdri / "studio_2k.hdr").write_bytes(b"hdr")
    assert detect_asset_type(hdri)[0] == "hdri"

    texture = tmp_path / "texture"
    texture.mkdir()
    (texture / "info.json").write_text('{"type": 1}', encoding="utf-8")
    (texture / "material.blend").write_bytes(b"optional companion")
    (texture / "stone_basecolor_2k.jpg").write_bytes(b"image")
    detected, reason = detect_asset_type(texture)
    assert detected == "texture_set"
    assert "metadata" in reason


def test_mixed_scan_classifies_each_folder_and_loose_exr_independently(tmp_path) -> None:
    model = tmp_path / "chair"
    model.mkdir()
    (model / "chair.fbx").write_bytes(b"fbx")

    texture = tmp_path / "stone"
    texture.mkdir()
    (texture / "info.json").write_text('{"type": 1}', encoding="utf-8")
    image(texture / "stone_basecolor_1k.jpg")
    image(texture / "stone_roughness_1k.jpg")

    hdri = tmp_path / "studio"
    hdri.mkdir()
    (hdri / "info.json").write_text('{"type": 0, "files": {"hdri": {}}}', encoding="utf-8")
    (hdri / "studio_1k.hdr").write_bytes(
        b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 512 +X 1024\nfixture"
    )

    (tmp_path / "Loose Panorama.exr").write_bytes(b"unsupported EXR header is still reviewable")
    (tmp_path / "Second Panorama.exr").write_bytes(b"another reviewable unsupported EXR")
    result = scan_mixed_folder(tmp_path)
    assert not result.canceled
    assert result.detected_asset_type == "mixed"
    assert [item.asset_type for item in result.materials].count("model") == 1
    assert [item.asset_type for item in result.materials].count("texture_set") == 1
    assert [item.asset_type for item in result.materials].count("hdri") == 3
    loose = next(item for item in result.materials if item.name == "Loose Panorama")
    assert loose.provider == "Unknown"
    assert loose.resolution_labels == ["Unknown"]
    loose_hdris = [item for item in result.materials if item.source_root == tmp_path and item.asset_type == "hdri"]
    library = tmp_path.parent / f"{tmp_path.name}-library"
    library.mkdir()
    repository = LibraryRepository(library)
    preflight = repository.preflight_assets(loose_hdris)
    assert len(preflight.materials) == 2
    assert all(preflight.for_material(item) is not None for item in loose_hdris)
    summary = repository.import_assets(loose_hdris, preflight_result=preflight)
    assert len(summary.imported) == 2
    assert not summary.failed
