from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from universal_asset_library.domain import LibraryStockAsset
from universal_asset_library.importer import (
    StockCategoryRule,
    StockTaxonomy,
    default_stock_taxonomy,
    infer_stock_display_name,
    scan_stock_folder,
)
from universal_asset_library.library import (
    AssetMetadataPatch,
    AssetMetadataUpdate,
    LibraryRepository,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg is required")


@pytest.mark.parametrize(
    ("relative", "expected"),
    (
        ("Light Leaks/Blue wipe/5.mov", "Light Leaks Blue wipe 05"),
        ("Light Leaks/Warm Wipe/11.mov", "Light Leaks Warm Wipe 11"),
        ("Explosions/Dirt Charges/Side/5.mov", "Dirt Charges Side 05"),
        ("02. Blood/5.mov", "05"),
        ("EXPLOSIONS/Fire/1.mov", "01"),
        ("motion_graphics/Lines/7.mov", "Lines 07"),
        ("Léger/001.mov", "Léger 001"),
        ("Blast_01.mov", "Blast 01"),
        ("05 - Meteors.mov", "05 - Meteors"),
    ),
)
def test_contextual_stock_name_for_numeric_clips(
    tmp_path: Path, relative: str, expected: str,
) -> None:
    root = tmp_path / "selected"
    path = root / relative

    assert infer_stock_display_name(path, root) == expected


def test_contextual_stock_name_excludes_selected_root(tmp_path: Path) -> None:
    root = tmp_path / "001test"

    assert infer_stock_display_name(root / "5.mov", root) == "05"
    assert (
        infer_stock_display_name(root / "Light Leaks" / "Blue wipe" / "5.mov", root)
        == "Light Leaks Blue wipe 05"
    )


def test_contextual_stock_name_uses_custom_canonical_categories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "selected"
    defaults = default_stock_taxonomy()
    taxonomy = StockTaxonomy(
        categories=(
            *defaults.categories,
            StockCategoryRule("Custom FX", ("custom effect",)),
        ),
        tags=defaults.tags,
        stop_words=defaults.stop_words,
    )

    assert (
        infer_stock_display_name(
            root / "Custom-FX" / "Variation" / "3.mov", root, taxonomy,
        )
        == "Variation 03"
    )
    # Aliases still classify content, but are not removed from generated names.
    assert (
        infer_stock_display_name(
            root / "custom effect" / "Variation" / "3.mov", root, taxonomy,
        )
        == "custom effect Variation 03"
    )


def test_numeric_clip_scan_drives_flat_managed_filenames(tmp_path: Path) -> None:
    incoming = tmp_path / "001test"
    source = incoming / "Explosions" / "Dirt Charges" / "Side" / "5.mov"
    _video(source)

    candidate = scan_stock_folder(incoming, ffprobe_path=FFPROBE).materials[0]
    assert candidate.name == "Dirt Charges Side 05"
    assert candidate.category == "Dust"
    assert candidate.classification_evidence

    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library, ffmpeg_path=FFMPEG).import_stock([candidate]).imported[0]

    assert {path.name for path in asset.asset_dir.iterdir()} == {
        "Dirt_Charges_Side_05.mov",
        "Dirt_Charges_Side_05_Preview.mp4",
        "Dirt_Charges_Side_05_Thumbnail.jpg",
        "Dirt_Charges_Side_05.json",
    }
    manifest = json.loads(
        (asset.asset_dir / "Dirt_Charges_Side_05.json").read_text(encoding="utf-8")
    )
    assert manifest["category"] == "Dust"
    assert isinstance(manifest["tags"], list)
    assert "surface" not in {tag.casefold() for tag in manifest["tags"]}
    assert "categories" not in manifest
    assert manifest["source"]["original_path"] == str(source)


def _video(
    path: Path, *, codec: str = "libx264", pixel_format: str = "yuv420p",
    audio: bool = False, duration: float = 1.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:duration={duration}",
    ]
    if audio:
        command.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-shortest"])
    command.extend(["-c:v", codec, "-pix_fmt", pixel_format])
    if audio:
        command.extend(["-c:a", "aac"])
    command.append(str(path))
    subprocess.run(command, check=True)


def test_stock_scan_pairs_preview_and_reports_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _video(source / "08. Explosions" / "Blast_01.mov")
    _video(source / "Video_Thumbnails" / "08. Explosions" / "Blast_01.mp4")
    _video(source / "Smoke" / "Smoke_02.mp4")

    result = scan_stock_folder(source, ffprobe_path=FFPROBE)

    assert [item.name for item in result.materials] == ["Blast 01", "Smoke 02"]
    blast = result.materials[0]
    assert blast.category == "Explosions"
    assert blast.preview_policy == "use_existing"
    assert blast.selected_preview.endswith("Blast_01.mp4")
    smoke = result.materials[1]
    assert smoke.preview_policy == "generate"
    assert any(item.code == "preview_generation_required" for item in smoke.diagnostics)


def test_stock_scan_finds_sibling_preview_tree_and_matches_provider_numbering(
    tmp_path: Path,
) -> None:
    effects = tmp_path / "EFFECTS"
    _video(effects / "5 - METEORS" / "05 - Meteors.mov")
    _video(
        effects / "5 - METEORS" / "05 - Meteors - LAYERS"
        / "05 - Meteors - BODY.mov"
    )
    provider_preview = (
        tmp_path / "Previews" / "5 - METEORS preview" / "05 - Meteors 2.mov"
    )
    _video(provider_preview)

    result = scan_stock_folder(effects, ffprobe_path=FFPROBE)

    assert len(result.materials) == 2
    assert all(candidate.source_root == tmp_path for candidate in result.materials)
    assert all(candidate.preview_policy == "use_existing" for candidate in result.materials)
    assert all(
        candidate.selected_preview
        == "Previews/5 - METEORS preview/05 - Meteors 2.mov"
        for candidate in result.materials
    )
    assert all(
        any(item.code == "preview_match_structured" for item in candidate.diagnostics)
        for candidate in result.materials
    )


def test_structured_preview_match_requires_media_agreement(tmp_path: Path) -> None:
    effects = tmp_path / "EFFECTS"
    _video(effects / "5 - METEORS" / "05 - Meteors.mov", duration=1.0)
    _video(
        tmp_path / "Previews" / "5 - METEORS preview" / "05 - Meteors 2.mov",
        duration=2.0,
    )

    candidate = scan_stock_folder(effects, ffprobe_path=FFPROBE).materials[0]

    assert not candidate.selected_preview
    assert candidate.preview_policy == "generate"
    assert any(item.code == "preview_media_mismatch" for item in candidate.diagnostics)
    assert any(item.code == "preview_generation_required" for item in candidate.diagnostics)


@pytest.mark.parametrize(
    "folder",
    (
        "Previews",
        "deep/VIDEO thumbnails/category",
        "deep/Electricity preview/more",
        "deep/PROXIES/category",
    ),
)
def test_videos_anywhere_beneath_preview_folders_never_become_assets(
    tmp_path: Path, folder: str
) -> None:
    preview_root = tmp_path / folder
    _video(preview_root / "Preview_Only.mov")

    result = scan_stock_folder(preview_root, ffprobe_path=FFPROBE)

    assert result.materials == []


def test_stock_import_preserves_source_generates_midpoint_preview_and_skips_duplicate(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "incoming"
    source = source_root / "Smoke" / "Smoke_Wisp_01.mov"
    _video(source, audio=True)
    (source.parent / "notes.txt").write_text("source notes", encoding="utf-8")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    candidate = scan_stock_folder(source_root, ffprobe_path=FFPROBE).materials[0]
    assert candidate.category == "Smoke"
    assert candidate.tags == ["wisp"]
    assert candidate.extra_paths == ["Smoke/notes.txt"]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library, ffmpeg_path=FFMPEG)

    preflight = repository.preflight_materials([candidate])
    assert preflight.materials[0].status in {"Ready", "Warning"}
    summary = repository.import_stock([candidate], preflight_result=preflight)

    assert not summary.failed
    assert len(summary.imported) == 1
    asset = summary.imported[0]
    assert isinstance(asset, LibraryStockAsset)
    assert asset.asset_type == "stock"
    assert asset.asset_dir.parent.parent == library
    assert asset.asset_dir == library / "stock" / "smoke"
    assert asset.source_path.name == source.name
    assert hashlib.sha256(asset.source_path.read_bytes()).hexdigest() == original_hash
    assert asset.preview_path.suffix == ".mp4"
    assert asset.thumbnail_path.suffix == ".jpg"
    assert asset.preview_origin == "generated"
    assert asset.tags == ("wisp",)
    assert asset.media_info.has_audio
    preview_probe = subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(asset.preview_path)],
        text=True,
    )
    assert '"codec_type": "audio"' in preview_probe
    assert asset.thumbnail_time == pytest.approx(asset.media_info.duration / 2.0)
    assert {path.name for path in asset.asset_dir.iterdir()} == {
        "Smoke_Wisp_01.mov",
        "Smoke_Wisp_01_Preview.mp4",
        "Smoke_Wisp_01_Thumbnail.jpg",
        "Smoke_Wisp_01.json",
    }
    manifest = json.loads((asset.asset_dir / "Smoke_Wisp_01.json").read_text(encoding="utf-8"))
    assert manifest["layout_version"] == 2
    assert manifest["source"]["path"] == "Smoke_Wisp_01.mov"
    assert manifest["source"]["original_path"] == str(source)
    assert manifest["preview"]["video"] == "Smoke_Wisp_01_Preview.mp4"
    assert manifest["preview"]["thumbnail"] == "Smoke_Wisp_01_Thumbnail.jpg"
    assert manifest["preview"]["original_path"] is None
    assert manifest["preview"]["thumbnail_original_path"] is None
    assert manifest["source_metadata"] == []
    assert manifest["extra_files"] == []
    assert not source.parent.joinpath("previews").exists()

    loaded = repository.list_stock_assets()
    assert len(loaded) == 1
    duplicate = repository.import_stock([candidate])
    assert not duplicate.imported
    assert duplicate.skipped


def test_existing_h264_preview_is_copied_unchanged(tmp_path: Path) -> None:
    source_root = tmp_path / "incoming"
    _video(source_root / "Particles" / "Spark_01.mov")
    provider_preview = source_root / "Previews" / "Particles" / "Spark_01.mov"
    _video(provider_preview)
    preview_hash = hashlib.sha256(provider_preview.read_bytes()).hexdigest()
    candidate = scan_stock_folder(source_root, ffprobe_path=FFPROBE).materials[0]
    library = tmp_path / "library"
    library.mkdir()

    asset = LibraryRepository(library, ffmpeg_path=FFMPEG).import_stock([candidate]).imported[0]

    assert asset.preview_origin == "existing"
    assert asset.preview_path.suffix == ".mov"
    assert hashlib.sha256(asset.preview_path.read_bytes()).hexdigest() == preview_hash
    manifest = json.loads(
        (asset.asset_dir / f"{asset.source_path.stem}.json").read_text(encoding="utf-8")
    )
    assert manifest["preview"]["original_path"] == str(provider_preview)


def test_flat_stock_metadata_edit_renames_and_moves_only_its_files(
    tmp_path: Path,
) -> None:
    incoming = tmp_path / "incoming"
    _video(incoming / "Smoke" / "Smoke_One.mov")
    _video(incoming / "Smoke" / "Smoke_Two.mov", duration=2.0)
    candidates = scan_stock_folder(incoming, ffprobe_path=FFPROBE).materials
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library, ffmpeg_path=FFMPEG)
    imported = repository.import_stock(candidates).imported
    first = next(asset for asset in imported if asset.name == "Smoke One")
    second = next(asset for asset in imported if asset.name == "Smoke Two")
    second_files = {
        path.name: path.read_bytes()
        for path in second.asset_dir.iterdir()
        if path.name.startswith("Smoke_Two")
    }

    updated = repository.update_asset_metadata(
        first.id,
        AssetMetadataUpdate(
            name="Hero Smoke",
            category="Atmospheres",
            tags=("hero", "smoke"),
        ),
    )

    assert updated.asset_dir == library / "stock" / "atmospheres"
    assert updated.source_path.name == "Hero_Smoke.mov"
    assert updated.preview_path.name == "Hero_Smoke_Preview.mp4"
    assert updated.thumbnail_path.name == "Hero_Smoke_Thumbnail.jpg"
    assert (updated.asset_dir / "Hero_Smoke.json").is_file()
    assert not any(path.name.startswith("Smoke_One") for path in second.asset_dir.iterdir())
    assert second_files == {
        path.name: path.read_bytes()
        for path in second.asset_dir.iterdir()
        if path.name.startswith("Smoke_Two")
    }
    assert {asset.id for asset in repository.list_stock_assets()} == {
        first.id, second.id
    }
    manifest = updated.asset_dir / "Hero_Smoke.json"
    with repository.metadata_patch_batch(
        (updated.id,), {updated.id: manifest}
    ) as batch:
        outcome = batch.patch(
            updated.id, AssetMetadataPatch(add_tags=("approved",))
        )
    assert outcome.manifest_path == manifest
    assert outcome.asset.tags == ("hero", "smoke", "approved")


def test_flat_stock_same_name_collision_uses_shared_numeric_token(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _video(first_root / "Smoke" / "Clip.mov", duration=1.0)
    _video(second_root / "Smoke" / "Clip.mov", duration=2.0)
    candidates = [
        scan_stock_folder(first_root, ffprobe_path=FFPROBE).materials[0],
        scan_stock_folder(second_root, ffprobe_path=FFPROBE).materials[0],
    ]
    library = tmp_path / "library"
    library.mkdir()

    imported = LibraryRepository(
        library, ffmpeg_path=FFMPEG
    ).import_stock(candidates).imported

    assert {asset.source_path.name for asset in imported} == {
        "Clip.mov", "Clip_2.mov",
    }
    assert {
        path.name for path in (library / "stock" / "smoke").glob("*.json")
    } == {"Clip.json", "Clip_2.json"}


def test_update_library_flattens_legacy_stock_and_preserves_companions(
    tmp_path: Path,
) -> None:
    incoming = tmp_path / "incoming"
    _video(incoming / "Smoke" / "Legacy_Clip.mov")
    candidate = scan_stock_folder(incoming, ffprobe_path=FFPROBE).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library, ffmpeg_path=FFMPEG)
    asset = repository.import_stock([candidate]).imported[0]
    manifest_path = asset.asset_dir / "Legacy_Clip.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy = asset.asset_dir / "legacy-clip"
    (legacy / "source").mkdir(parents=True)
    (legacy / "previews").mkdir()
    (legacy / "metadata").mkdir()
    (legacy / "extras").mkdir()
    source_target = legacy / "source" / asset.source_path.name
    preview_target = legacy / "previews" / asset.preview_path.name
    thumbnail_target = legacy / "previews" / asset.thumbnail_path.name
    asset.source_path.rename(source_target)
    asset.preview_path.rename(preview_target)
    asset.thumbnail_path.rename(thumbnail_target)
    metadata = legacy / "metadata" / "provider.json"
    metadata.write_text('{"provider": true}', encoding="utf-8")
    extra = legacy / "extras" / "notes.txt"
    extra.write_text("notes", encoding="utf-8")
    document.pop("layout_version", None)
    document["source"]["path"] = "source/" + source_target.name
    document["preview"]["video"] = "previews/" + preview_target.name
    document["preview"]["thumbnail"] = "previews/" + thumbnail_target.name
    document["source_metadata"] = ["metadata/provider.json"]
    document["extra_files"] = [{
        "path": "extras/notes.txt",
        "original_path": "notes.txt",
        "format": "TXT",
        "size": extra.stat().st_size,
        "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
    }]
    manifest_path.unlink()
    (legacy / "asset.json").write_text(
        json.dumps(document, indent=2), encoding="utf-8"
    )

    assert repository.library_update_count() == 1
    summary = repository.update_library()

    assert len(summary.updated) == 1
    migrated = summary.updated[0]
    assert isinstance(migrated, LibraryStockAsset)
    assert migrated.id == asset.id
    assert migrated.source_sha256 == asset.source_sha256
    assert migrated.source_path == asset.asset_dir / "Legacy_Clip.mov"
    assert (asset.asset_dir / "Legacy_Clip.json").is_file()
    assert (asset.asset_dir / "Legacy_Clip_Metadata_provider.json").is_file()
    assert (asset.asset_dir / "Legacy_Clip_Extra_notes.txt").is_file()
    assert not legacy.exists()
    assert repository.library_update_count() == 0
    assert repository.update_library().updated == []


def test_unrelated_stock_json_is_not_reported_as_a_manifest(tmp_path: Path) -> None:
    library = tmp_path / "library"
    category = library / "stock" / "miscellaneous"
    category.mkdir(parents=True)
    (category / "notes.json").write_text('{"notes": true}', encoding="utf-8")
    repository = LibraryRepository(library)

    assert repository.list_stock_assets() == []
    assert repository.last_warnings == []
