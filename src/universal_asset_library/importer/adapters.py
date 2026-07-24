from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import urlparse


@dataclass(slots=True)
class MapDeclaration:
    channel: str
    resolution: str = ""
    bit_depth: int | None = None
    color_space: str = ""
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MetadataFacts:
    provider: str
    provider_id: str = ""
    name: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""
    physical_size: str = ""
    maps_by_basename: dict[str, MapDeclaration] = field(default_factory=dict)
    maps_by_path: dict[str, MapDeclaration] = field(default_factory=dict)
    preview_roles_by_basename: dict[str, str] = field(default_factory=dict)
    preview_roles_by_path: dict[str, str] = field(default_factory=dict)
    declared_resolutions: set[str] = field(default_factory=set)
    asset_type: str = ""


class JsonMetadataAdapter(Protocol):
    def confidence(self, document: dict[str, Any], path: Path) -> int: ...

    def parse(self, document: dict[str, Any], path: Path) -> MetadataFacts: ...


CHANNEL_NAMES = {
    "albedo": "Base Color",
    "basecolor": "Base Color",
    "base_color": "Base Color",
    "diffuse": "Base Color",
    "diff": "Base Color",
    "ao": "Ambient Occlusion",
    "ambientocclusion": "Ambient Occlusion",
    "occlusion": "Ambient Occlusion",
    "rough": "Roughness",
    "roughness": "Roughness",
    "gloss": "Glossiness",
    "glossiness": "Glossiness",
    "normal": "Normal",
    "nor_gl": "Normal",
    "nor_dx": "Normal",
    "displacement": "Displacement",
    "disp": "Displacement",
    "height": "Height",
    "bump": "Bump",
    "cavity": "Cavity",
    "metal": "Metalness",
    "metallic": "Metalness",
    "metalness": "Metalness",
    "specular": "Specular",
    "spec": "Specular",
    "opacity": "Opacity",
    "alpha": "Opacity",
    "emission": "Emission",
    "emit": "Emission",
    "translucency": "Translucency",
    "subsurface": "Translucency",
    "sss": "Translucency",
    "arm": "Packed ARM",
}


def resolution_label(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.casefold()
        match = re.fullmatch(r"([1-9]\d*)k", lowered.strip())
        if match:
            return f"{int(match.group(1))}K"
        try:
            width = int(lowered.split("x", 1)[0])
        except (ValueError, IndexError):
            return ""
    elif isinstance(value, int):
        width = value
    else:
        return ""
    for pixels, label in ((1024, "1K"), (2048, "2K"), (4096, "4K"), (8192, "8K"), (16384, "16K")):
        if abs(width - pixels) <= max(8, pixels // 100):
            return label
    nearest_k = round(width / 1024)
    if nearest_k > 0 and abs(width - nearest_k * 1024) <= max(8, width // 100):
        return f"{nearest_k}K"
    return f"{width}px" if width > 0 else ""


def safe_basename(value: str) -> str:
    if not value or value.startswith("data:"):
        return ""
    parsed = urlparse(value)
    return Path(parsed.path or value).name.casefold()


def safe_relative_path(value: str) -> str:
    if not value or value.startswith("data:"):
        return ""
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return ""
    path = Path(parsed.path or value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix().lstrip("./").casefold()


def normalize_channel(value: str) -> tuple[str, str, dict[str, str]]:
    token = value.casefold().replace("-", "_").replace(" ", "_")
    convention = ""
    packed: dict[str, str] = {}
    if token in {"nor_gl", "normal_gl", "normal_ogl"}:
        convention = "OpenGL"
    elif token in {"nor_dx", "normal_dx"}:
        convention = "DirectX"
    if token == "arm":
        packed = {"R": "Ambient Occlusion", "G": "Roughness", "B": "Metalness"}
    return CHANNEL_NAMES.get(token, ""), convention, packed


class MegascansAdapter:
    def confidence(self, document: dict[str, Any], path: Path) -> int:
        has_maps = isinstance(document.get("maps"), list) or isinstance(document.get("components"), list)
        return 100 if has_maps and "semanticTags" in document and "id" in document else 0

    def parse(self, document: dict[str, Any], path: Path) -> MetadataFacts:
        semantic = document.get("semanticTags") if isinstance(document.get("semanticTags"), dict) else {}
        facts = MetadataFacts(
            provider="Megascans",
            provider_id=str(document.get("id", "")),
            name=str(semantic.get("name") or document.get("name") or ""),
            categories=[str(value) for value in document.get("categories", []) if value],
            tags=_flatten_tags(document.get("tags", []), semantic),
            physical_size=str(document.get("physicalSize") or ""),
            asset_type=_megascans_asset_type(document, semantic),
        )
        maps = document.get("maps") if isinstance(document.get("maps"), list) else []
        for item in maps:
            if not isinstance(item, dict):
                continue
            basename = safe_basename(str(item.get("uri", "")))
            relative = safe_relative_path(str(item.get("uri", "")))
            channel, convention, packed = normalize_channel(str(item.get("type") or item.get("name") or ""))
            label = resolution_label(item.get("resolution"))
            if label:
                facts.declared_resolutions.add(label)
            if basename and channel:
                declaration = MapDeclaration(
                    channel=channel,
                    resolution=label,
                    bit_depth=_as_int(item.get("bitDepth")),
                    color_space=str(item.get("colorSpace") or ""),
                    normal_convention=convention,
                    packed_channels=packed,
                )
                facts.maps_by_basename[basename] = declaration
                if relative:
                    facts.maps_by_path[relative] = declaration
        components = document.get("components") if isinstance(document.get("components"), list) else []
        for component in components:
            if not isinstance(component, dict):
                continue
            channel, convention, packed = normalize_channel(str(component.get("type") or component.get("name") or ""))
            if not channel:
                continue
            for uri_group in component.get("uris", []):
                if not isinstance(uri_group, dict):
                    continue
                for resolution in uri_group.get("resolutions", []):
                    if not isinstance(resolution, dict):
                        continue
                    label = resolution_label(resolution.get("resolution"))
                    if label:
                        facts.declared_resolutions.add(label)
                    for item in resolution.get("formats", []):
                        if not isinstance(item, dict):
                            continue
                        basename = safe_basename(str(item.get("uri", "")))
                        relative = safe_relative_path(str(item.get("uri", "")))
                        if not basename:
                            continue
                        declaration = MapDeclaration(
                            channel=channel,
                            resolution=label,
                            bit_depth=_as_int(item.get("bitDepth")),
                            color_space=str(item.get("colorSpace") or ""),
                            normal_convention=convention,
                            packed_channels=packed,
                        )
                        facts.maps_by_basename[basename] = declaration
                        if relative:
                            facts.maps_by_path[relative] = declaration
        previews = document.get("previews") if isinstance(document.get("previews"), dict) else {}
        for item in previews.get("images", []):
            if not isinstance(item, dict):
                continue
            basename = safe_basename(str(item.get("uri", "")))
            relative = safe_relative_path(str(item.get("uri", "")))
            tags = {str(tag).casefold() for tag in item.get("tags", [])}
            if basename:
                role = "hero" if tags & {"preview", "sidepanel"} else "thumbnail"
                facts.preview_roles_by_basename[basename] = role
                if relative:
                    facts.preview_roles_by_path[relative] = role
        return facts


def _megascans_asset_type(document: dict[str, Any], semantic: dict[str, Any]) -> str:
    declared = str(semantic.get("asset_type") or "").strip().casefold()
    categories = {str(value).strip().casefold() for value in document.get("categories", []) if value}
    asset_categories = document.get("assetCategories")
    atlas_tree = isinstance(asset_categories, dict) and "atlas" in {
        str(value).casefold() for value in asset_categories
    }
    cutout = False
    maps = document.get("maps") if isinstance(document.get("maps"), list) else []
    for item in maps:
        if isinstance(item, dict) and str(item.get("type") or item.get("name") or "").casefold() in {"opacity", "translucency"}:
            cutout = True
            break
    if not cutout:
        cutout = any(
            isinstance(item, dict)
            and str(item.get("type") or item.get("name") or "").casefold() in {"opacity", "translucency"}
            for item in (document.get("components") if isinstance(document.get("components"), list) else [])
        )
    if declared == "atlas" or (cutout and ("atlas" in categories or atlas_tree)):
        return "atlas"
    return "texture_set"


class PolyHavenAdapter:
    def confidence(self, document: dict[str, Any], path: Path) -> int:
        return 90 if isinstance(document.get("files"), dict) and "authors" in document and "max_resolution" in document else 0

    def parse(self, document: dict[str, Any], path: Path) -> MetadataFacts:
        authors = document.get("authors") if isinstance(document.get("authors"), dict) else {}
        dimensions = document.get("dimensions")
        facts = MetadataFacts(
            provider="Poly Haven",
            name=str(document.get("name") or ""),
            categories=[str(value) for value in document.get("categories", []) if value],
            tags=[str(value) for value in document.get("tags", []) if value],
            author=", ".join(str(name) for name in authors),
            description=str(document.get("description") or ""),
            physical_size=" × ".join(str(value) for value in dimensions) if isinstance(dimensions, list) else "",
        )
        files = document.get("files", {})
        for map_name, resolutions in files.items():
            channel, convention, packed = normalize_channel(str(map_name))
            if not channel or not isinstance(resolutions, dict):
                continue
            for resolution, formats in resolutions.items():
                label = resolution_label(resolution)
                if label:
                    facts.declared_resolutions.add(label)
                if not isinstance(formats, dict):
                    continue
                for entry in formats.values():
                    if not isinstance(entry, dict):
                        continue
                    basename = safe_basename(str(entry.get("url", "")))
                    relative = safe_relative_path(str(entry.get("url", "")))
                    if basename:
                        declaration = MapDeclaration(
                            channel=channel,
                            resolution=label,
                            normal_convention=convention,
                            packed_channels=packed,
                        )
                        facts.maps_by_basename[basename] = declaration
                        if relative:
                            facts.maps_by_path[relative] = declaration
        thumbnail = safe_basename(str(document.get("thumbnail_url", "")))
        if thumbnail:
            facts.preview_roles_by_basename[thumbnail] = "thumbnail"
        return facts


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _flatten_tags(base: Any, semantic: dict[str, Any]) -> list[str]:
    values: list[str] = [str(value).strip() for value in base if value] if isinstance(base, list) else []
    for key, item in semantic.items():
        if key in {"name", "resolution", "minSize", "maxSize", "locations"}:
            continue
        if isinstance(item, list):
            values.extend(str(value).strip() for value in item if str(value).strip())
        elif isinstance(item, str) and item.strip():
            values.append(item.strip())
    return sorted(set(values), key=str.casefold)


ADAPTERS: tuple[JsonMetadataAdapter, ...] = (MegascansAdapter(), PolyHavenAdapter())
