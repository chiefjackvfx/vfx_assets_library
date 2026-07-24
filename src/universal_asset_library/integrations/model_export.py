from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_asset_library.domain import LibraryMap, LibraryModelAsset, LibraryModelFile
from universal_asset_library.integrations.texture_export import (
    SUPPORTED_CHANNELS,
    TextureExportMap,
    default_texture_resolution,
)


USD_FORMATS = {"USD", "USDA", "USDC", "USDZ"}
USD_SUFFIXES = {".usd", ".usda", ".usdc", ".usdz"}


class ModelExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelExportFile:
    path: Path
    file_format: str
    resolution: str = ""
    lod: str = ""

    @property
    def label(self) -> str:
        details = [value for value in (self.resolution, self.lod, self.file_format) if value]
        return " · ".join(details) or self.path.name

    def document(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
            "format": self.file_format,
            "resolution": self.resolution,
            "lod": self.lod,
        }


@dataclass(frozen=True, slots=True)
class ModelExportPayload:
    asset_id: str
    asset_name: str
    asset_slug: str
    library_root: Path
    model: ModelExportFile
    texture_sets: tuple["ModelExportTextureSet", ...] = ()

    @property
    def variant(self) -> str:
        return self.model.label

    def document(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_slug": self.asset_slug,
            "library_root": self.library_root.as_posix(),
            "model_path": self.model.path.as_posix(),
            "format": self.model.file_format,
            "resolution": self.model.resolution,
            "lod": self.model.lod,
            "variant": self.variant,
            "texture_sets": [item.document() for item in self.texture_sets],
        }


@dataclass(frozen=True, slots=True)
class ModelExportTextureSet:
    name: str
    resolution: str
    maps: tuple[TextureExportMap, ...]

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "resolution": self.resolution,
            "maps": [item.document() for item in self.maps],
        }


def model_export_options(asset: LibraryModelAsset) -> tuple[LibraryModelFile, ...]:
    records = [
        item for item in asset.model_files
        if item.available and item.file_format.upper() in USD_FORMATS
    ]
    return tuple(sorted(
        records,
        key=lambda item: (
            not item.preferred,
            _resolution_rank(item.resolution),
            _lod_rank(item.lod),
            item.path.casefold(),
        ),
    ))


def model_export_label(record: LibraryModelFile) -> str:
    details = [value for value in (record.resolution, record.lod, record.file_format.upper()) if value]
    return " · ".join(details) + f" · {Path(record.path).name}"


def prepare_model_export(
    asset: LibraryModelAsset,
    selected_path: str = "",
    library_root: str | Path = "",
) -> ModelExportPayload:
    options = model_export_options(asset)
    if not options:
        raise ModelExportError("This model has no managed USD file.")
    record = next((item for item in options if item.path == selected_path), None) if selected_path else options[0]
    if record is None:
        raise ModelExportError("The selected USD variant is not part of this asset.")
    relative = Path(record.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ModelExportError("The selected USD record has an unsafe managed path.")
    try:
        root = Path(library_root).expanduser().resolve(strict=True)
        managed = (asset.asset_dir / relative).resolve(strict=True)
    except OSError as error:
        raise ModelExportError(f"The managed USD file is unavailable: {error}") from error
    if not root.is_dir():
        raise ModelExportError("The configured library root is not a directory.")
    try:
        managed.relative_to(root)
    except ValueError as error:
        raise ModelExportError("The managed USD file is outside the configured library.") from error
    if not managed.is_file():
        raise ModelExportError("The managed USD path is not a regular file.")
    file_format = record.file_format.upper()
    if file_format not in USD_FORMATS or managed.suffix.casefold() not in USD_SUFFIXES:
        raise ModelExportError("Only managed USD, USDA, USDC, and USDZ files can be sent to a DCC.")
    model = ModelExportFile(managed, file_format, record.resolution, record.lod)
    texture_sets = tuple(
        prepared
        for name, texture_set in sorted(asset.texture_sets.items(), key=lambda item: item[0].casefold())
        if (prepared := _prepare_texture_set(asset, name, texture_set.resolutions, record, root))
    )
    return ModelExportPayload(
        asset.id, asset.name, _slug(asset.name), root, model, texture_sets
    )


def _prepare_texture_set(asset, name, resolutions, model_record, root):
    if not resolutions:
        return None
    label = (
        model_record.resolution
        if model_record.resolution in resolutions
        else default_texture_resolution(resolutions)
    )
    variant = resolutions[label]
    explicit = {channel for channel in variant.maps if channel in SUPPORTED_CHANNELS}
    maps = []
    used_paths = set()
    for channel, alternatives in sorted(variant.maps.items(), key=lambda item: item[0].casefold()):
        selected = _preferred_map(alternatives, model_record.lod)
        if selected is None:
            continue
        packed = {
            component.upper(): semantic
            for component, semantic in selected.packed_channels.items()
            if semantic in SUPPORTED_CHANNELS and semantic not in explicit
        }
        if channel not in SUPPORTED_CHANNELS and not packed:
            continue
        try:
            path = (asset.asset_dir / selected.path).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError) as error:
            raise ModelExportError(
                f"Managed model texture is unavailable or outside the library: {selected.path}"
            ) from error
        if not path.is_file() or path in used_paths:
            continue
        used_paths.add(path)
        maps.append(TextureExportMap(
            channel,
            path,
            selected.color_space or ("sRGB" if channel in {"Base Color", "Emission"} else "Raw"),
            selected.normal_convention,
            packed,
        ))
    return ModelExportTextureSet(name, label, tuple(maps)) if maps else None


def _preferred_map(alternatives: tuple[LibraryMap, ...], lod: str):
    if not alternatives:
        return None
    if lod:
        exact = next((item for item in alternatives if item.lod.casefold() == lod.casefold()), None)
        if exact is not None:
            return exact
    generic = next((item for item in alternatives if not item.lod), None)
    if generic is not None:
        return generic
    return next((item for item in alternatives if item.preferred), alternatives[0])


def _resolution_rank(label: str) -> int:
    digits = "".join(character for character in label if character.isdigit())
    return -(int(digits) if digits else 0)


def _lod_rank(label: str) -> int:
    digits = "".join(character for character in label if character.isdigit())
    return int(digits) if digits else 10_000


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "model"
