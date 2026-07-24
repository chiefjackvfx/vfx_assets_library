import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.domain import (
    LibraryMap,
    LibraryModelAsset,
    LibraryModelFile,
    LibraryModelTextureSet,
    LibraryResolution,
)
from universal_asset_library.importer import scan_model_folder
from universal_asset_library.integrations import (
    ModelRescanSelection,
    ModelUsdValidation,
    ModelConversionError,
    ModelConversionResult,
    model_conversion_sources,
    prepare_model_conversion,
    run_model_conversion,
)
from universal_asset_library.library import LibraryError, LibraryRepository, StaleSourceError
import universal_asset_library.integrations.blender_model_conversion_driver as driver_module
import universal_asset_library.integrations.model_conversion as conversion_module
import universal_asset_library.integrations.model_rescan as rescan_module
import universal_asset_library.library.repository as repository_module


def _asset(tmp_path: Path) -> LibraryModelAsset:
    asset_dir = tmp_path / "models" / "chair"
    (asset_dir / "models").mkdir(parents=True)
    (asset_dir / "maps" / "2K").mkdir(parents=True)
    (asset_dir / "maps" / "4K").mkdir(parents=True)
    (asset_dir / "models" / "chair.fbx").write_bytes(b"fbx")
    (asset_dir / "models" / "chair.obj").write_bytes(b"obj")
    (asset_dir / "maps" / "2K" / "base.jpg").write_bytes(b"2k")
    (asset_dir / "maps" / "4K" / "base.jpg").write_bytes(b"4k")
    variants = {}
    for label in ("2K", "4K"):
        relative = f"maps/{label}/base.jpg"
        variants[label] = LibraryResolution(
            label, None, None,
            {"Base Color": (
                LibraryMap("Base Color", relative, "JPG", 2, label, preferred=True),
            )},
        )
    return LibraryModelAsset(
        "id", "Chair", "Furniture", (), "", "", "", "Unknown", "",
        asset_dir,
        (
            LibraryModelFile("models/chair.obj", "", "OBJ", "alternative", "LOD1", "", 50, False, 3, "obj"),
            LibraryModelFile("models/chair.fbx", "", "FBX", "primary", "LOD0", "", 100, False, 3, "fbx"),
            LibraryModelFile("models/chair.glb", "", "GLB", "primary", "LOD2", "", 200, True, 3, "glb"),
        ),
        {"Chair Material": LibraryModelTextureSet("Chair Material", variants)},
        None, None, "fingerprint", "now", 12,
    )


def test_conversion_request_selects_source_resolution_and_orientation(tmp_path: Path) -> None:
    asset = _asset(tmp_path)

    assert [item.file_format for item in model_conversion_sources(asset)] == ["FBX", "OBJ", "GLB"]
    request = prepare_model_conversion(asset, orientation="usd_interchange", library_root=tmp_path)

    assert request.source_relative == "models/chair.fbx"
    assert (request.forward_axis, request.up_axis) == ("-Z", "Y")
    assert request.texture_sets[0].resolution == "4K"
    assert request.texture_sets[0].maps[0].path.name == "base.jpg"

    selected = prepare_model_conversion(asset, "models/chair.obj", "z_up", tmp_path)
    assert selected.source_lod == "LOD1"
    assert (selected.forward_axis, selected.up_axis) == ("Y", "Z")
    generic = LibraryMap("Normal", "generic.png", "PNG", 1, "", lod="")
    lod0 = LibraryMap("Normal", "lod0.png", "PNG", 1, "", lod="LOD0")
    lod5 = LibraryMap("Normal", "lod5.png", "PNG", 1, "", preferred=True, lod="LOD5")
    assert conversion_module._preferred_map((generic, lod5, lod0), "LOD0") is lod0
    assert conversion_module._preferred_map((generic, lod5, lod0), "LOD2") is generic


def test_conversion_request_rejects_unknown_source_orientation_and_missing_maps(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    with pytest.raises(ModelConversionError, match="orientation"):
        prepare_model_conversion(asset, orientation="sideways", library_root=tmp_path)
    with pytest.raises(ModelConversionError, match="not part"):
        prepare_model_conversion(asset, "models/missing.fbx", library_root=tmp_path)
    without_maps = LibraryModelAsset(
        **{
            **{name: getattr(asset, name) for name in asset.__dataclass_fields__ if name != "texture_sets"},
            "texture_sets": {},
        }
    )
    with pytest.raises(ModelConversionError, match="no managed texture"):
        prepare_model_conversion(without_maps, library_root=tmp_path)


def _source_model(tmp_path: Path) -> Path:
    source = tmp_path / "chair"
    source.mkdir()
    (source / "chair.blend").write_bytes(b"BLENDER")
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#795b42"))
    assert image.save(str(source / "chair_basecolor_1k.png"))
    return source


def _imported_repository(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    candidate = scan_model_folder(_source_model(tmp_path)).materials[0]
    repository = LibraryRepository(library, blender_path="/fake/blender")
    asset = repository.import_models([candidate]).imported[0]
    return repository, asset


def _successful_runner(request, output, **_kwargs):
    output.mkdir(parents=True)
    entry = output / "Chair.usdc"
    entry.write_bytes(b"usdc")
    texture = output / "textures" / "Default" / "base.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"texture")
    return ModelConversionResult(
        "ready", entry, "Blender 5.2.0", 1, 1, (texture,),
    )


def test_repository_publishes_and_rebuilds_generated_usd_atomically(tmp_path, monkeypatch) -> None:
    repository, original = _imported_repository(tmp_path)
    monkeypatch.setattr(repository_module, "run_model_conversion", _successful_runner)

    first = repository.convert_model_to_usd(original.id)
    assert first.asset.usd_ready
    assert first.asset.preferred_model.file_format == "USDC"
    assert first.asset.usd_derivative is not None
    assert first.asset.usd_derivative.forward_axis == "-Z"
    assert first.conversion.entry_path.is_file()
    manifest_path = first.asset.asset_dir / "asset.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_records = [
        record for record in document["model_files"]
        if record["format"] != "USDC"
    ]
    assert original_records and not any(record["preferred"] for record in original_records)
    assert len([record for record in document["model_files"] if record["format"] == "USDC"]) == 1

    second = repository.convert_model_to_usd(original.id, orientation="z_up")
    rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert second.asset.usd_derivative.up_axis == "Z"
    assert len([record for record in rebuilt["model_files"] if record["format"] == "USDC"]) == 1
    assert repository.list_model_assets()[0].total_size >= original.total_size + 4


def test_repository_failure_or_stale_manifest_publishes_nothing(tmp_path, monkeypatch) -> None:
    repository, asset = _imported_repository(tmp_path)
    manifest = asset.asset_dir / "asset.json"
    before = manifest.read_bytes()
    monkeypatch.setattr(
        repository_module,
        "run_model_conversion",
        lambda *_args, **_kwargs: ModelConversionResult("failed", diagnostic="ambiguous slots"),
    )
    with pytest.raises(LibraryError, match="ambiguous slots"):
        repository.convert_model_to_usd(asset.id)
    assert manifest.read_bytes() == before
    assert not any((asset.asset_dir / "usd").iterdir())

    def stale_runner(request, output, **_kwargs):
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["updated_at"] = "changed"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        return _successful_runner(request, output)

    monkeypatch.setattr(repository_module, "run_model_conversion", stale_runner)
    with pytest.raises(StaleSourceError):
        repository.convert_model_to_usd(asset.id)
    assert not any((asset.asset_dir / "usd").iterdir())


def test_repository_rolls_back_rebuild_and_preserves_unowned_usd_files(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    monkeypatch.setattr(repository_module, "run_model_conversion", _successful_runner)
    first = repository.convert_model_to_usd(asset.id)
    manifest = first.asset.asset_dir / "asset.json"
    entry = first.conversion.entry_path
    before_manifest = manifest.read_bytes()
    before_entry = entry.read_bytes()
    provider_usd = first.asset.asset_dir / "usd" / "Provider.usdc"
    provider_usd.write_bytes(b"provider")
    real_atomic = repository_module._atomic_json

    def fail_manifest(path, document):
        if path == manifest:
            raise OSError("publication failed")
        real_atomic(path, document)

    monkeypatch.setattr(repository_module, "_atomic_json", fail_manifest)
    with pytest.raises(OSError, match="publication failed"):
        repository.convert_model_to_usd(asset.id, orientation="z_up")
    assert manifest.read_bytes() == before_manifest
    assert entry.read_bytes() == before_entry
    assert provider_usd.read_bytes() == b"provider"

    monkeypatch.setattr(repository_module, "_atomic_json", real_atomic)
    generated = json.loads(manifest.read_text(encoding="utf-8"))["usd_derivative"]
    generated["dependencies"] = []
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["usd_derivative"] = generated
    real_atomic(manifest, document)
    with pytest.raises(LibraryError, match="conflicts"):
        repository.convert_model_to_usd(asset.id)
    assert entry.read_bytes() == before_entry


def test_repository_checks_publication_space_before_replacing_derivative(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    monkeypatch.setattr(repository_module, "run_model_conversion", _successful_runner)

    class Usage:
        free = 0

    monkeypatch.setattr(repository_module.shutil, "disk_usage", lambda _path: Usage())
    with pytest.raises(LibraryError, match="enough free space"):
        repository.convert_model_to_usd(asset.id)
    assert not any((asset.asset_dir / "usd").iterdir())


def test_repository_canceled_conversion_keeps_asset_unchanged(tmp_path, monkeypatch) -> None:
    repository, asset = _imported_repository(tmp_path)
    manifest = asset.asset_dir / "asset.json"
    before = manifest.read_bytes()
    monkeypatch.setattr(
        repository_module,
        "run_model_conversion",
        lambda *_args, **_kwargs: ModelConversionResult(
            "canceled", diagnostic="Model conversion was canceled.",
        ),
    )
    with pytest.raises(LibraryError, match="canceled"):
        repository.convert_model_to_usd(asset.id)
    assert manifest.read_bytes() == before
    assert not any((asset.asset_dir / "usd").iterdir())


def test_model_rescan_adopts_reviewed_manual_files_and_preserves_manual_preference(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    manual = asset.asset_dir / "usd" / "Chair_Manual.usdc"
    manual.write_bytes(b"manual usd")
    monkeypatch.setattr(
        rescan_module,
        "validate_usd_file",
        lambda *_args, **_kwargs: ModelUsdValidation(
            True, "Blender 5.2.0", 2, 1, "Y",
        ),
    )

    scan = repository.rescan_model_asset(asset.id)
    candidate = scan.item("usd/Chair_Manual.usdc")
    assert candidate and candidate.status == "new"
    update = repository.apply_model_asset_rescan(
        asset.id,
        scan,
        ModelRescanSelection(
            add_paths=(candidate.path,),
            preferred_path=candidate.path,
        ),
    )
    assert update.asset.preferred_model.path == candidate.path
    assert update.asset.preferred_model.origin == "manual"

    monkeypatch.setattr(repository_module, "run_model_conversion", _successful_runner)
    rebuilt = repository.convert_model_to_usd(asset.id)
    assert rebuilt.asset.usd_derivative is not None
    assert rebuilt.asset.preferred_model.path == candidate.path


def test_model_rescan_only_reconciles_manual_changes_and_stale_scans_abort(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    added = asset.asset_dir / "models" / "alternate.obj"
    added.write_bytes(b"first")
    scan = repository.rescan_model_asset(asset.id)
    repository.apply_model_asset_rescan(
        asset.id,
        scan,
        ModelRescanSelection(
            add_paths=("models/alternate.obj",),
            preferred_path="models/alternate.obj",
        ),
    )
    added.write_bytes(b"second")
    imported = next(
        item for item in repository.list_model_assets()[0].model_files
        if item.origin == "imported"
    )
    (asset.asset_dir / imported.path).write_bytes(b"changed imported")
    changed = repository.rescan_model_asset(asset.id)
    assert changed.item("models/alternate.obj").mutable
    assert not changed.item(imported.path).mutable

    manifest = asset.asset_dir / "asset.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["updated_at"] = "stale"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(StaleSourceError):
        repository.apply_model_asset_rescan(
            asset.id,
            changed,
            ModelRescanSelection(
                refresh_paths=("models/alternate.obj",),
                preferred_path="models/alternate.obj",
            ),
        )


def test_missing_manual_model_remains_loadable_for_rescan(tmp_path) -> None:
    repository, asset = _imported_repository(tmp_path)
    added = asset.asset_dir / "models" / "alternate.obj"
    added.write_bytes(b"manual")
    scan = repository.rescan_model_asset(asset.id)
    repository.apply_model_asset_rescan(
        asset.id,
        scan,
        ModelRescanSelection(
            add_paths=("models/alternate.obj",),
            preferred_path="models/alternate.obj",
        ),
    )
    added.unlink()

    degraded = repository.list_model_assets()[0]
    assert degraded.needs_rescan
    missing = repository.rescan_model_asset(asset.id).item("models/alternate.obj")
    assert missing and missing.status == "missing" and missing.mutable


def test_model_rescan_discovers_every_supported_model_extension(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    expected = {
        ".usd": "USD", ".usda": "USDA", ".usdc": "USDC", ".usdz": "USDZ",
        ".fbx": "FBX", ".obj": "OBJ", ".abc": "ABC", ".gltf": "GLTF",
        ".glb": "GLB", ".blend": "BLEND", ".ma": "MA", ".mb": "MB",
    }
    for suffix, file_format in expected.items():
        folder = "usd" if file_format in {"USD", "USDA", "USDC", "USDZ"} else "models"
        (asset.asset_dir / folder / f"manual{suffix}").write_bytes(suffix.encode())
    monkeypatch.setattr(
        rescan_module,
        "validate_usd_file",
        lambda *_args, **_kwargs: ModelUsdValidation(
            True, "Blender 5.2.0", 1, 0, "Y",
        ),
    )

    scan = repository.rescan_model_asset(asset.id)
    discovered = {
        Path(item.path).suffix: item.file_format
        for item in scan.items if item.status == "new"
    }
    assert discovered == expected


def test_update_library_moves_legacy_generated_derivative_into_usd(
    tmp_path, monkeypatch,
) -> None:
    repository, asset = _imported_repository(tmp_path)
    monkeypatch.setattr(repository_module, "run_model_conversion", _successful_runner)
    converted = repository.convert_model_to_usd(asset.id).asset
    manifest = converted.asset_dir / "asset.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    (converted.asset_dir / "derivatives").mkdir()
    (converted.asset_dir / "usd").replace(
        converted.asset_dir / "derivatives" / "usd"
    )
    for record in document["model_files"]:
        if str(record["path"]).startswith("usd/"):
            record["path"] = "derivatives/" + record["path"]
    derivative = document["usd_derivative"]
    derivative["entry_path"] = "derivatives/" + derivative["entry_path"]
    for dependency in derivative["dependencies"]:
        dependency["path"] = "derivatives/" + dependency["path"]
    document["layout_version"] = 5
    manifest.write_text(json.dumps(document), encoding="utf-8")

    assert repository.library_update_count() == 1
    assert len(repository.update_library().updated) == 1
    migrated = repository.list_model_assets()[0]
    assert migrated.usd_derivative.entry_path.startswith("usd/")
    assert migrated.usd_path and migrated.usd_path.is_file()
    assert not (migrated.asset_dir / "derivatives").exists()


def test_headless_runner_builds_secure_blender_command_and_reads_result(tmp_path, monkeypatch) -> None:
    request = prepare_model_conversion(_asset(tmp_path), library_root=tmp_path)
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **_kwargs):
            captured["command"] = command
            request_path = Path(command[command.index("--request") + 1])
            result_path = Path(command[command.index("--result") + 1])
            document = json.loads(request_path.read_text(encoding="utf-8"))
            output = Path(document["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "Chair.usdc").write_bytes(b"usdc")
            result_path.write_text(json.dumps({
                "ok": True,
                "entry_path": "Chair.usdc",
                "mesh_count": 2,
                "material_count": 1,
                "blender_version": "Blender 5.2.0",
            }), encoding="utf-8")

        def communicate(self, timeout=None):
            return "conversion complete", None

    monkeypatch.setattr(conversion_module, "validate_blender_executable", lambda _path: (True, "ok", "Blender 5.2.0"))
    monkeypatch.setattr(conversion_module, "resolve_blender_executable", lambda _path: "/blender")
    monkeypatch.setattr(conversion_module.subprocess, "Popen", Process)
    result = run_model_conversion(request, tmp_path / "stage" / "generated", blender_path="/blender")

    assert result.status == "ready"
    assert (result.mesh_count, result.material_count) == (2, 1)
    assert captured["command"][:5] == [
        "/blender", "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code",
    ]
    assert "--python" in captured["command"]
    assert str(request.source.path) not in captured["command"]


def test_driver_material_matching_is_strict_with_single_pair_fallback() -> None:
    wood = {"name": "Wood"}
    metal = {"name": "Metal"}
    assert driver_module._match_materials({"Wood": [object()]}, [wood]) == {"Wood": wood}
    assert driver_module._match_materials({"Material": [object()]}, [wood]) == {"Material": wood}
    with pytest.raises(RuntimeError, match="could not be matched"):
        driver_module._match_materials(
            {"Material": [object()], "Trim": [object()]}, [wood, metal],
        )
    assert driver_module._material_name("Fancy Chair", "Default", 1) == "Fancy_Chair"
    assert driver_module._material_name("Fancy Chair", "Wood", 2) == "Fancy_Chair_Wood"
    assert driver_module._normal_semantic(
        {"Normal": object(), "Bump": object()}
    ) == "Normal"
    assert driver_module._normal_semantic({"Bump": object()}) == "Bump"
    assert driver_module._normal_semantic({}) == ""
    assert driver_module._axis_selection("-Z") == "NEGATIVE_Z"
    assert driver_module._axis_selection("Y") == "Y"
    mesh = SimpleNamespace(type="MESH", material_slots=())
    meshes, slots, unassigned = driver_module._material_slots(
        SimpleNamespace(data=SimpleNamespace(objects=[mesh]))
    )
    assert meshes == [mesh]
    assert slots == {}
    assert unassigned == [mesh]
