from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from time import monotonic
from typing import Callable

from universal_asset_library.domain import LibraryMap, LibraryModelAsset, LibraryModelFile
from universal_asset_library.previews.hdri_renderer import (
    resolve_blender_executable,
    validate_blender_executable,
)


COMPATIBLE_SOURCE_FORMATS = {"BLEND", "FBX", "OBJ", "GLTF", "GLB", "ABC"}
ORIENTATION_PRESETS = {
    "usd_interchange": ("-Z", "Y"),
    "z_up": ("Y", "Z"),
}


class ModelConversionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelConversionMap:
    channel: str
    path: Path
    file_format: str
    color_space: str
    normal_convention: str
    packed_channels: dict[str, str]

    def document(self) -> dict:
        return {
            "channel": self.channel,
            "path": self.path.as_posix(),
            "format": self.file_format,
            "color_space": self.color_space,
            "normal_convention": self.normal_convention,
            "packed_channels": self.packed_channels,
        }


@dataclass(frozen=True, slots=True)
class ModelConversionTextureSet:
    name: str
    resolution: str
    maps: tuple[ModelConversionMap, ...]

    def document(self) -> dict:
        return {
            "name": self.name,
            "resolution": self.resolution,
            "maps": [item.document() for item in self.maps],
        }


@dataclass(frozen=True, slots=True)
class ModelConversionRequest:
    asset_id: str
    asset_name: str
    asset_dir: Path
    library_root: Path
    source: ModelConversionMap
    source_relative: str
    source_sha256: str
    source_lod: str
    source_component: str
    orientation: str
    forward_axis: str
    up_axis: str
    texture_sets: tuple[ModelConversionTextureSet, ...]
    manifest_updated_at: str = ""
    timeout_seconds: int = 600

    @property
    def estimated_size(self) -> int:
        paths = {self.source.path, *(item.path for group in self.texture_sets for item in group.maps)}
        return sum(path.stat().st_size for path in paths if path.is_file())

    def document(self, output_dir: Path) -> dict:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "source": self.source.document(),
            "source_relative": self.source_relative,
            "source_sha256": self.source_sha256,
            "source_lod": self.source_lod,
            "source_component": self.source_component,
            "orientation": self.orientation,
            "forward_axis": self.forward_axis,
            "up_axis": self.up_axis,
            "texture_sets": [item.document() for item in self.texture_sets],
            "output_dir": output_dir.as_posix(),
        }


@dataclass(frozen=True, slots=True)
class ModelConversionResult:
    status: str
    entry_path: Path | None = None
    blender_version: str = ""
    mesh_count: int = 0
    material_count: int = 0
    dependencies: tuple[Path, ...] = ()
    diagnostics: tuple[str, ...] = ()
    diagnostic: str = ""
    log: str = ""


def model_conversion_sources(asset: LibraryModelAsset) -> tuple[LibraryModelFile, ...]:
    sources = [
        item for item in asset.model_files
        if item.available and item.file_format.upper() in COMPATIBLE_SOURCE_FORMATS
    ]
    return tuple(sorted(
        sources,
        key=lambda item: (
            _lod_rank(item.lod),
            not item.preferred,
            -int(item.triangle_count or 0),
            item.path.casefold(),
        ),
    ))


def prepare_model_conversion(
    asset: LibraryModelAsset,
    selected_path: str = "",
    orientation: str = "usd_interchange",
    library_root: str | Path = "",
    manifest_updated_at: str = "",
) -> ModelConversionRequest:
    if orientation not in ORIENTATION_PRESETS:
        raise ModelConversionError(f"Unsupported USD orientation preset: {orientation}")
    options = model_conversion_sources(asset)
    if not options:
        raise ModelConversionError(
            "This asset has no Blender-compatible static source model. "
            "Supported sources are BLEND, FBX, OBJ, GLTF, GLB, and ABC."
        )
    source_record = (
        next((item for item in options if item.path == selected_path), None)
        if selected_path else options[0]
    )
    if source_record is None:
        raise ModelConversionError("The selected source model is not part of this asset.")
    root = Path(library_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ModelConversionError("The configured library root is not a directory.")
    source_path = _managed_file(asset, source_record.path, root)
    texture_sets: list[ModelConversionTextureSet] = []
    for name, texture_set in sorted(asset.texture_sets.items(), key=lambda value: value[0].casefold()):
        label, variant = _conversion_resolution(texture_set.resolutions)
        maps: list[ModelConversionMap] = []
        for channel, alternatives in sorted(variant.maps.items(), key=lambda value: value[0].casefold()):
            selected = _preferred_map(alternatives, source_record.lod)
            if selected is None:
                continue
            path = _managed_file(asset, selected.path, root)
            maps.append(_conversion_map(selected, path))
        if maps:
            texture_sets.append(ModelConversionTextureSet(name, label, tuple(maps)))
    if not texture_sets:
        raise ModelConversionError("This model has no managed texture sets for material construction.")
    forward, up = ORIENTATION_PRESETS[orientation]
    return ModelConversionRequest(
        asset.id,
        asset.name,
        asset.asset_dir,
        root,
        ModelConversionMap(
            "Model", source_path, source_record.file_format.upper(), "", "", {},
        ),
        source_record.path,
        source_record.sha256,
        source_record.lod,
        source_record.component,
        orientation,
        forward,
        up,
        tuple(texture_sets),
        manifest_updated_at,
    )


def run_model_conversion(
    request: ModelConversionRequest,
    output_dir: Path,
    *,
    blender_path: str = "",
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
) -> ModelConversionResult:
    executable = resolve_blender_executable(blender_path)
    valid, diagnostic, version = validate_model_conversion_blender(executable)
    if not valid:
        return ModelConversionResult("unsupported", blender_version=version, diagnostic=diagnostic)
    output_dir.mkdir(parents=True, exist_ok=True)
    request_path = output_dir.parent / "conversion-request.json"
    result_path = output_dir.parent / "conversion-result.json"
    request_path.write_text(
        json.dumps(request.document(output_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    driver = Path(__file__).resolve().with_name("blender_model_conversion_driver.py")
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
    ]
    command.extend([
        "--python", str(driver), "--",
        "--request", str(request_path),
        "--result", str(result_path),
    ])
    if progress:
        progress("Launching Blender")
    started = monotonic()
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except OSError as error:
        return ModelConversionResult("failed", blender_version=version, diagnostic=str(error))
    output = ""
    while True:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            _stop_process(process)
            return ModelConversionResult(
                "canceled", blender_version=version,
                diagnostic="Model conversion was canceled.", log=output[-20000:],
            )
        if monotonic() - started > request.timeout_seconds:
            _stop_process(process)
            return ModelConversionResult(
                "failed", blender_version=version,
                diagnostic="Blender model conversion timed out.", log=output[-20000:],
            )
        try:
            tail, _ = process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired as pending:
            if pending.output:
                output = (
                    pending.output if isinstance(pending.output, str)
                    else pending.output.decode(errors="replace")
                )
            continue
        output = (tail or output or "")[-20000:]
        break
    document = {}
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if process.returncode or not document.get("ok"):
        message = str(document.get("diagnostic", "")).strip() or _last_log_line(output)
        return ModelConversionResult(
            "failed", blender_version=str(document.get("blender_version", version)),
            diagnostic=message or f"Blender exited with code {process.returncode}.", log=output,
        )
    entry = output_dir / str(document.get("entry_path", ""))
    if not entry.is_file() or entry.suffix.casefold() != ".usdc":
        return ModelConversionResult(
            "failed", blender_version=version,
            diagnostic="Blender did not create the expected USDC file.", log=output,
        )
    dependencies = tuple(
        path for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path != entry
    )
    return ModelConversionResult(
        "ready",
        entry,
        str(document.get("blender_version", version)),
        int(document.get("mesh_count", 0)),
        int(document.get("material_count", 0)),
        dependencies,
        tuple(str(value) for value in document.get("diagnostics", []) if value),
        log=output,
    )


def validate_model_conversion_blender(configured: str = "") -> tuple[bool, str, str]:
    executable = resolve_blender_executable(configured)
    valid, diagnostic, version = validate_blender_executable(executable)
    if valid and _blender_version(version) < (5, 1):
        return (
            False,
            f"Headless model conversion requires Blender 5.1 or newer; detected {version}.",
            version,
        )
    return valid, diagnostic, version


def _managed_file(asset: LibraryModelAsset, relative_value: str, root: Path) -> Path:
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ModelConversionError("The selected managed path is unsafe.")
    try:
        path = (asset.asset_dir / relative).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as error:
        raise ModelConversionError(f"The managed source is unavailable or outside the library: {relative}") from error
    if not path.is_file():
        raise ModelConversionError(f"The managed source is not a file: {relative}")
    return path


def _conversion_resolution(resolutions: dict):
    candidates = []
    for label, variant in resolutions.items():
        digits = "".join(character for character in label if character.isdigit())
        rank = int(digits) if digits else 1_000_000
        candidates.append((rank, str(label), variant))
    if not candidates:
        raise ModelConversionError("A model texture set has no usable resolution.")
    at_most_4k = [item for item in candidates if item[0] <= 4]
    selected = max(at_most_4k, default=max(candidates), key=lambda item: item[0])
    return selected[1], selected[2]


def _preferred_map(
    values: tuple[LibraryMap, ...], source_lod: str = "",
) -> LibraryMap | None:
    if source_lod:
        exact = next(
            (item for item in values if item.lod.casefold() == source_lod.casefold()),
            None,
        )
        if exact is not None:
            return exact
    generic = [item for item in values if not item.lod]
    if generic:
        return next((item for item in generic if item.preferred), generic[0])
    return next((item for item in values if item.preferred), values[0] if values else None)


def _conversion_map(item: LibraryMap, path: Path) -> ModelConversionMap:
    return ModelConversionMap(
        item.channel, path, item.file_format, item.color_space,
        item.normal_convention, dict(item.packed_channels),
    )


def _lod_rank(value: str) -> int:
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else 10_000


def _blender_version(value: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _last_log_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.casefold() or "traceback" in line.casefold()]
    return (errors[-1] if errors else lines[-1] if lines else "")[:500]
