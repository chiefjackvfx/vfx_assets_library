from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_asset_library.domain import LibraryTextureAsset


SUPPORTED_CHANNELS = (
    "Base Color", "Ambient Occlusion", "Cavity", "Roughness", "Glossiness",
    "Metalness", "Specular", "Normal", "Bump", "Height", "Displacement",
    "Opacity", "Emission", "Translucency",
)
COLOR_CHANNELS = {"Base Color", "Emission"}


class TextureExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TextureExportMap:
    channel: str
    path: Path
    color_space: str
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "path": self.path.as_posix(),
            "color_space": self.color_space,
            "normal_convention": self.normal_convention,
            "packed_channels": dict(self.packed_channels),
        }


@dataclass(frozen=True, slots=True)
class TextureExportPayload:
    asset_id: str
    asset_name: str
    resolution: str
    library_root: Path
    maps: tuple[TextureExportMap, ...]
    missing_channels: tuple[str, ...] = ()

    def document(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "resolution": self.resolution,
            "library_root": self.library_root.as_posix(),
            "maps": [item.document() for item in self.maps],
            "missing_channels": list(self.missing_channels),
        }


def prepare_texture_export(
    asset: LibraryTextureAsset,
    resolution: str = "",
    library_root: str | Path = "",
) -> TextureExportPayload:
    if not asset.resolutions:
        raise TextureExportError("This texture asset has no managed resolution variants.")
    label = resolution if resolution in asset.resolutions else default_texture_resolution(asset.resolutions)
    try:
        root = Path(library_root).expanduser().resolve(strict=True)
    except OSError as error:
        raise TextureExportError(f"The configured library root is unavailable: {error}") from error
    if not root.is_dir():
        raise TextureExportError("The configured library root is not a directory.")

    variant = asset.resolutions[label]
    explicit_channels = {channel for channel in variant.maps if channel in SUPPORTED_CHANNELS}
    selected: list[TextureExportMap] = []
    used_paths: set[Path] = set()
    provided = set(explicit_channels)
    for channel, alternatives in sorted(variant.maps.items(), key=lambda item: item[0].casefold()):
        if not alternatives:
            continue
        preferred = next((item for item in alternatives if item.preferred), alternatives[0])
        packed = {
            component.upper(): semantic
            for component, semantic in preferred.packed_channels.items()
            if semantic in SUPPORTED_CHANNELS and semantic not in explicit_channels
        }
        if channel not in SUPPORTED_CHANNELS and not packed:
            continue
        try:
            managed = (asset.asset_dir / preferred.path).resolve(strict=True)
        except OSError as error:
            raise TextureExportError(f"Managed texture is unavailable: {preferred.path}") from error
        try:
            managed.relative_to(root)
        except ValueError as error:
            raise TextureExportError(f"Managed texture is outside the configured library: {managed}") from error
        if not managed.is_file():
            raise TextureExportError(f"Managed texture is not a regular file: {managed}")
        if managed in used_paths:
            continue
        used_paths.add(managed)
        provided.update(packed.values())
        selected.append(TextureExportMap(
            channel=channel,
            path=managed,
            color_space=_export_color_space(channel, preferred.color_space),
            normal_convention=preferred.normal_convention,
            packed_channels=packed,
        ))
    if not selected:
        raise TextureExportError(f"The {label} variant has no supported managed texture maps.")
    missing = tuple(channel for channel in SUPPORTED_CHANNELS if channel not in provided)
    return TextureExportPayload(asset.id, asset.name, label, root, tuple(selected), missing)


def default_texture_resolution(resolutions: dict[str, Any]) -> str:
    ranked = sorted(((_resolution_value(label), label) for label in resolutions), key=lambda item: (item[0], item[1]))
    within = [item for item in ranked if 0 < item[0] <= 4]
    if within:
        return within[-1][1]
    larger = [item for item in ranked if item[0] > 4]
    return larger[0][1] if larger else ranked[-1][1]


def _resolution_value(label: str) -> int:
    digits = "".join(character for character in str(label) if character.isdigit())
    return int(digits) if digits else 0


def _export_color_space(channel: str, declared: str) -> str:
    value = declared.strip()
    if value:
        return value
    return "sRGB" if channel in COLOR_CHANNELS else "Raw"
