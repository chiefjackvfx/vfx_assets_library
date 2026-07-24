from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlparse

from universal_asset_library.domain import MODEL_CATEGORIES

from .adapters import normalize_channel, resolution_label
from .models import (
    Diagnostic,
    ModelCandidate,
    ModelFile,
    ModelLod,
    ModelTextureSet,
    PreviewCandidate,
    ResolutionVariant,
    ScanCancellationToken,
    ScanCancelled,
    ScanProgress,
    ScanResult,
    SourceFileSnapshot,
    TextureMap,
)
from .scanner import _single_category_tags
from .scanner import (
    FORMAT_PRIORITY,
    JSON_LIMIT,
    _build_inventory,
    _emit_scan_progress,
    _filename_channel,
    _image_dimensions,
    _inferred_bit_depth,
    _is_within,
    _map_preference,
    _map_resolution,
    _resolution_sort_key,
)


MODEL_FORMATS = {
    ".usd": "USD", ".usda": "USDA", ".usdc": "USDC", ".usdz": "USDZ",
    ".fbx": "FBX", ".obj": "OBJ", ".abc": "ABC",
    ".gltf": "GLTF", ".glb": "GLB", ".blend": "BLEND",
    ".ma": "MA", ".mb": "MB",
}
USD_FORMATS = {"USD", "USDA", "USDC", "USDZ"}
EXCLUDED_SUFFIXES = {
    ".zip": "Packaged archive",
    ".rar": "Packaged archive",
    ".7z": "Packaged archive",
    ".tar": "Packaged archive",
    ".gz": "Packaged archive",
    ".bz2": "Packaged archive",
    ".xz": "Packaged archive",
    ".tgz": "Packaged archive",
    ".zst": "Packaged archive",
    ".rat": "Renderer texture cache",
    ".rs": "Renderer proxy",
    ".ass": "Renderer proxy",
    ".vrmesh": "Renderer proxy",
    ".vrscene": "Renderer scene",
    ".rib": "Renderer scene",
    ".ifd": "Renderer scene",
    ".tx": "Renderer texture cache",
    ".bgeo": "Renderer geometry cache",
}
TEMP_SUFFIXES = {".tmp", ".temp", ".part", ".bak", ".autosave"}
GENERIC_MODEL_CATEGORIES = {"3d", "3d asset", "asset", "model"}


@dataclass(slots=True)
class _ModelFacts:
    provider: str = "Unknown"
    provider_id: str = ""
    name: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""
    physical_size: str = ""
    dimensions: tuple[float, ...] = ()
    polycount: int | None = None
    model_declarations: dict[str, tuple[str, int | None, str]] = field(default_factory=dict)
    preview_roles: dict[str, str] = field(default_factory=dict)
    texture_declarations: dict[str, tuple[str, str, str, int | None, str]] = field(default_factory=dict)


def scan_model_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
) -> ScanResult:
    root = Path(source).expanduser().absolute()
    result = ScanResult()
    token = cancel_token or ScanCancellationToken()
    started = monotonic()
    if not root.exists() or not root.is_dir():
        result.warnings.append("The selected model source is not an existing folder.")
        return result
    try:
        inventory, diagnostics = _build_inventory(root, token, progress, started, report_exclusions=True)
        result.inventory = tuple(entry.snapshot for entry in inventory)
        result.diagnostics.extend(diagnostics)
        roots = _discover_model_roots(root, inventory)
        for index, model_root in enumerate(roots):
            token.check()
            entries = [entry for entry in inventory if _is_within(entry.path, model_root)]
            root_diagnostics = _diagnostics_for_root(root, model_root, diagnostics)
            candidate = _scan_model(model_root, default_category, entries, root_diagnostics)
            if candidate.model_files:
                result.materials.append(candidate)
                result.diagnostics.extend(candidate.diagnostics)
            _emit_scan_progress(
                progress, "Parsing models", len(inventory), len(inventory), index + 1,
                result.diagnostics, started,
            )
    except ScanCancelled:
        result.canceled = True
        result.materials.clear()
        return result
    result.materials.sort(key=lambda item: item.name.casefold())
    if not result.materials:
        result.warnings.append("No folders containing supported local model files were found.")
    return result


def _discover_model_roots(scan_root: Path, inventory) -> list[Path]:
    json_directories = {entry.path.parent for entry in inventory if entry.snapshot.kind == "json"}
    candidates: set[Path] = set()
    for entry in inventory:
        if entry.path.suffix.casefold() not in MODEL_FORMATS:
            continue
        parent = entry.path.parent
        selected: Path | None = None
        current = parent
        while _is_within(current, scan_root):
            if current in json_directories:
                selected = current
                break
            if current == scan_root:
                break
            current = current.parent
        if selected is None:
            relative = entry.path.relative_to(scan_root)
            selected = scan_root if len(relative.parts) == 1 else scan_root / relative.parts[0]
        candidates.add(selected)
    ordered = sorted(candidates, key=lambda path: (len(path.parts), str(path).casefold()))
    return [path for path in ordered if not any(_is_within(path, prior) for prior in ordered if prior != path and len(prior.parts) < len(path.parts))]


def _diagnostics_for_root(scan_root: Path, model_root: Path, diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    prefix = model_root.relative_to(scan_root).as_posix()
    values: list[Diagnostic] = []
    for item in diagnostics:
        if not item.path:
            continue
        if model_root == scan_root:
            relative = item.path
        elif item.path == prefix or item.path.startswith(prefix + "/"):
            relative = item.path[len(prefix):].lstrip("/")
        else:
            continue
        values.append(Diagnostic(item.severity, item.code, item.message, relative, model_root.name))
    return values


def _scan_model(root: Path, default_category: str, entries, inherited_diagnostics: list[Diagnostic]) -> ModelCandidate:
    diagnostics = list(inherited_diagnostics)
    excluded: dict[str, str] = {item.path: item.message for item in diagnostics if "excluded" in item.code}
    accepted = []
    for entry in entries:
        relative = entry.path.relative_to(root).as_posix()
        suffix = entry.path.suffix.casefold()
        reason = EXCLUDED_SUFFIXES.get(suffix)
        if entry.path.name.casefold().endswith((".bgeo.sc", ".tar.gz", ".tar.bz2", ".tar.xz")):
            reason = "Packaged archive" if ".tar." in entry.path.name.casefold() else "Renderer geometry cache"
        if suffix in TEMP_SUFFIXES or entry.path.name.endswith("~"):
            reason = "Temporary or backup file"
        if re.search(r"\.blend\d+$", entry.path.name, re.IGNORECASE):
            reason = "Temporary or backup file"
        if reason:
            excluded[relative] = reason
            diagnostics.append(Diagnostic("info", "model_file_excluded", f"Excluded {reason.lower()}.", relative, root.name))
            continue
        if entry.snapshot.kind == "invalid_image":
            excluded[relative] = "Unreadable image"
            continue
        accepted.append(entry)

    facts, metadata_paths = _read_model_metadata(root, accepted, diagnostics)
    model_files: list[ModelFile] = []
    declared_present: set[str] = set()
    for entry in sorted(accepted, key=lambda item: str(item.path).casefold()):
        file_format = MODEL_FORMATS.get(entry.path.suffix.casefold())
        if not file_format:
            continue
        relative = entry.path.relative_to(root).as_posix()
        if entry.snapshot.size <= 0:
            diagnostics.append(Diagnostic("error", "empty_model_file", "Ignored an empty model file.", relative, root.name))
            continue
        declaration = facts.model_declarations.get(entry.path.name.casefold())
        lod = declaration[0] if declaration else _lod_label(entry.path.stem)
        tris = declaration[1] if declaration else None
        declared_role = declaration[2] if declaration else ""
        role = declared_role or _model_role(entry.path.stem, file_format, lod)
        component = _component_name(entry.path.stem, lod)
        model_files.append(ModelFile(relative, file_format, role, lod, component, tris, metadata_declared=bool(declaration)))
        if declaration:
            declared_present.add(entry.path.name.casefold())
    _select_preferred_model(model_files, root.name)

    texture_sets: dict[str, ModelTextureSet] = {}
    aggregate: dict[str, ResolutionVariant] = {}
    preview_candidates: list[PreviewCandidate] = []
    assigned: set[str] = {item.relative_path for item in model_files}
    for entry in sorted(accepted, key=lambda item: str(item.path).casefold()):
        if entry.snapshot.kind != "image":
            continue
        relative = entry.path.relative_to(root).as_posix()
        declaration = facts.texture_declarations.get(entry.path.name.casefold())
        channel, convention, packed = _filename_channel(entry.path.name)
        if declaration:
            channel = declaration[0] or channel
            convention = declaration[2] or convention
        if channel:
            label = _map_resolution(entry.path, root, (entry.snapshot.width, entry.snapshot.height), None)
            if declaration and declaration[1]:
                label = declaration[1]
            material_name = _material_name(entry.path, root)
            lod = _lod_label(entry.path.stem)
            extension = entry.path.suffix.lstrip(".").upper()
            texture = TextureMap(
                channel, relative, extension,
                bit_depth=declaration[3] if declaration and declaration[3] else _inferred_bit_depth(extension.casefold()),
                color_space=declaration[4] if declaration else "",
                normal_convention=convention,
                packed_channels=packed,
                metadata_source="json" if declaration else "filename",
                material=material_name,
                lod=lod,
            )
            texture_set = texture_sets.setdefault(material_name, ModelTextureSet(material_name))
            variant = texture_set.resolutions.setdefault(label, ResolutionVariant(label, entry.snapshot.width, entry.snapshot.height))
            variant.maps.setdefault(channel, []).append(texture)
            combined = aggregate.setdefault(label, ResolutionVariant(label, entry.snapshot.width, entry.snapshot.height))
            combined.maps.setdefault(channel, []).append(texture)
            assigned.add(relative)
            continue
        role = facts.preview_roles.get(entry.path.name.casefold(), "")
        lowered = entry.path.name.casefold()
        if role or any(token in lowered for token in ("thumbnail", "preview", "render", "_thumb")):
            aspect = (entry.snapshot.width / entry.snapshot.height) if entry.snapshot.width and entry.snapshot.height else 1.0
            inferred = role or ("hero" if aspect >= 1.35 else "thumbnail")
            preview_candidates.append(PreviewCandidate(relative, entry.snapshot.width, entry.snapshot.height, inferred, role))

    for texture_set in texture_sets.values():
        for variant in texture_set.resolutions.values():
            _select_texture_preferences(variant)
    _select_model_previews(preview_candidates)
    selected_thumbnail = next((item.relative_path for item in preview_candidates if "thumbnail" in item.selected_roles), "")
    selected_hero = next((item.relative_path for item in preview_candidates if "hero" in item.selected_roles), "")
    assigned.update(path for path in (selected_thumbnail, selected_hero) if path)
    assigned.update(metadata_paths)

    snapshots = {
        entry.path.relative_to(root).as_posix(): SourceFileSnapshot(
            entry.path.relative_to(root).as_posix(), entry.snapshot.size, entry.snapshot.mtime_ns,
            entry.snapshot.kind, entry.snapshot.width, entry.snapshot.height,
        )
        for entry in accepted
    }
    extra_paths = sorted((path for path in snapshots if path not in assigned), key=str.casefold)
    lods: dict[str, ModelLod] = {}
    for item in model_files:
        if item.lod:
            lod = lods.setdefault(item.lod, ModelLod(item.lod, item.triangle_count))
            lod.files.append(item)
            if lod.triangle_count is None:
                lod.triangle_count = item.triangle_count
    missing = sorted(set(facts.model_declarations) - declared_present)
    if missing:
        diagnostics.append(Diagnostic(
            "warning", "declared_models_missing",
            f"Provider metadata declares {len(missing)} model file(s) that are not local.", material=root.name,
        ))
    if not preview_candidates:
        diagnostics.append(Diagnostic("warning", "model_preview_missing", "No model preview found; a placeholder will be generated.", material=root.name))

    category = _model_category(facts.categories, root.name, default_category)
    candidate = ModelCandidate(
        source_root=root,
        provider=facts.provider,
        provider_id=facts.provider_id,
        name=facts.name or _display_name(root.name),
        category=category,
        tags=_single_category_tags(
            facts.tags,
            (
                value
                for value in facts.categories
                if value.casefold() not in GENERIC_MODEL_CATEGORIES
            ),
            category,
        ),
        author=facts.author,
        description=facts.description,
        physical_size=facts.physical_size,
        resolutions=aggregate,
        previews=preview_candidates,
        selected_thumbnail=selected_thumbnail,
        selected_hero=selected_hero,
        metadata_paths=metadata_paths,
        extra_paths=extra_paths,
        diagnostics=diagnostics,
        warnings=[item.message for item in diagnostics if item.severity == "warning"],
        source_snapshots=snapshots,
        model_files=model_files,
        lods=lods,
        texture_sets=texture_sets,
        dimensions=facts.dimensions,
        polycount=facts.polycount,
        excluded_paths=excluded,
    )
    return candidate


def _read_model_metadata(root: Path, entries, diagnostics: list[Diagnostic]) -> tuple[_ModelFacts, list[str]]:
    recognized: list[tuple[int, _ModelFacts, str]] = []
    metadata_paths: list[str] = []
    for entry in sorted((item for item in entries if item.path.parent == root and item.snapshot.kind == "json"), key=lambda item: item.path.name.casefold()):
        relative = entry.path.relative_to(root).as_posix()
        metadata_paths.append(relative)
        if entry.snapshot.size > JSON_LIMIT:
            diagnostics.append(Diagnostic("warning", "json_oversized", "Skipped oversized model JSON.", relative, root.name))
            continue
        try:
            document = json.loads(entry.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            diagnostics.append(Diagnostic("warning", "json_parse_failed", str(error), relative, root.name))
            continue
        if not isinstance(document, dict):
            continue
        if "id" in document and isinstance(document.get("meshes"), list) and isinstance(document.get("semanticTags"), dict):
            recognized.append((100, _parse_megascans(document), relative))
        elif document.get("type") == 2 and isinstance(document.get("files"), dict) and isinstance(document.get("authors"), dict):
            recognized.append((90, _parse_poly_haven(document, root.name), relative))
        else:
            diagnostics.append(Diagnostic("warning", "json_schema_unknown", "JSON is retained but is not recognized as model metadata.", relative, root.name))
    if not recognized:
        return _ModelFacts(), metadata_paths
    recognized.sort(key=lambda item: item[0], reverse=True)
    if len(recognized) > 1:
        diagnostics.append(Diagnostic("warning", "multiple_metadata_files", f"Using {recognized[0][2]} as the primary model metadata.", recognized[0][2], root.name))
    return recognized[0][1], metadata_paths


def _parse_megascans(document: dict[str, Any]) -> _ModelFacts:
    semantic = document.get("semanticTags", {})
    facts = _ModelFacts(
        provider="Megascans",
        provider_id=str(document.get("id", "")),
        name=str(semantic.get("name") or document.get("name") or ""),
        categories=[str(value) for value in document.get("categories", []) if value],
        tags=_flatten_values(document.get("tags", []), semantic),
        physical_size=str(document.get("physicalSize") or ""),
    )
    for mesh in document.get("meshes", []):
        if not isinstance(mesh, dict):
            continue
        mesh_type = str(mesh.get("type") or "")
        for record in mesh.get("uris", []):
            if not isinstance(record, dict):
                continue
            basename = _basename(record.get("uri"))
            if basename:
                lod = _lod_label(Path(basename).stem)
                facts.model_declarations[basename.casefold()] = (lod, _integer(mesh.get("tris")), "high" if mesh_type == "original" else "lod")
    for component in document.get("components", []):
        if not isinstance(component, dict):
            continue
        channel, convention, _packed = normalize_channel(str(component.get("type") or component.get("name") or ""))
        if not channel:
            continue
        for uri_group in component.get("uris", []):
            if not isinstance(uri_group, dict):
                continue
            for resolution in uri_group.get("resolutions", []):
                if not isinstance(resolution, dict):
                    continue
                label = resolution_label(resolution.get("resolution"))
                for record in resolution.get("formats", []):
                    if not isinstance(record, dict):
                        continue
                    basename = _basename(record.get("uri"))
                    if basename:
                        facts.texture_declarations[basename.casefold()] = (
                            channel, label, convention, _integer(record.get("bitDepth")), str(component.get("colorSpace") or "")
                        )
    previews = document.get("previews", {})
    for record in previews.get("images", []) if isinstance(previews, dict) else []:
        if not isinstance(record, dict):
            continue
        basename = _basename(record.get("uri"))
        tags = {str(value).casefold() for value in record.get("tags", [])}
        if basename and not str(record.get("uri", "")).startswith("data:"):
            facts.preview_roles[basename.casefold()] = "hero" if tags & {"preview", "retina", "sidepanel"} else "thumbnail"
    return facts


def _parse_poly_haven(document: dict[str, Any], provider_id: str) -> _ModelFacts:
    authors = document.get("authors", {})
    dimensions = document.get("dimensions")
    facts = _ModelFacts(
        provider="Poly Haven",
        provider_id=provider_id,
        name=str(document.get("name") or ""),
        categories=[str(value) for value in document.get("categories", []) if value],
        tags=[str(value) for value in document.get("tags", []) if value],
        author=", ".join(str(value) for value in authors),
        description=str(document.get("description") or ""),
        dimensions=tuple(float(value) for value in dimensions) if isinstance(dimensions, list) else (),
        polycount=_integer(document.get("polycount")),
    )
    for group_name, resolutions in document.get("files", {}).items():
        channel, convention, _packed = normalize_channel(str(group_name))
        if not isinstance(resolutions, dict):
            continue
        for resolution, formats in resolutions.items():
            if not isinstance(formats, dict):
                continue
            label = resolution_label(resolution)
            for format_name, record in formats.items():
                if not isinstance(record, dict):
                    continue
                basename = _basename(record.get("url"))
                if channel and basename:
                    facts.texture_declarations[basename.casefold()] = (channel, label, convention, None, "")
                elif str(format_name).casefold() in {"usd", "usdc", "usda", "usdz", "fbx", "blend", "gltf", "glb", "obj"} and basename:
                    facts.model_declarations[basename.casefold()] = ("", None, "scene" if str(format_name).casefold() == "blend" else "mesh")
    thumbnail = _basename(document.get("thumbnail_url"))
    if thumbnail:
        facts.preview_roles[thumbnail.casefold()] = "thumbnail"
    return facts


def _select_preferred_model(files: list[ModelFile], asset_name: str) -> None:
    if not files:
        return
    for item in files:
        item.preferred = False
    selected = max(files, key=lambda item: _model_preference(item, asset_name))
    selected.preferred = True


def _model_preference(item: ModelFile, asset_name: str) -> tuple[int, int, int, int, str]:
    lowered = Path(item.relative_path).stem.casefold()
    asset_token = re.sub(r"[^a-z0-9]+", "", asset_name.casefold())
    file_token = re.sub(r"[^a-z0-9]+", "", lowered)
    usd_score = {"USD": 1000, "USDC": 990, "USDA": 980, "USDZ": 970}.get(item.file_format, 0)
    fallback = {"BLEND": 800, "MA": 790, "MB": 790, "FBX": 700, "OBJ": 600, "ABC": 590, "GLTF": 580, "GLB": 580}.get(item.file_format, 0)
    role = {"high": 80, "scene": 70, "mesh": 60, "lod": 50, "component": 30}.get(item.role, 0)
    name_bonus = 30 if any(token in lowered for token in ("master", "main", "root")) or asset_token in file_token else 0
    lod_bonus = 20 if not item.lod else 15 if item.lod == "LOD0" else max(0, 10 - _lod_number(item.lod))
    return usd_score or fallback, role, name_bonus, lod_bonus, item.relative_path.casefold()


def _select_texture_preferences(variant: ResolutionVariant) -> None:
    for alternatives in variant.maps.values():
        for item in alternatives:
            item.preferred = False
        if alternatives:
            max(alternatives, key=_map_preference).preferred = True


def _select_model_previews(previews: list[PreviewCandidate]) -> None:
    if not previews:
        return
    thumbnail = max(previews, key=lambda item: (
        item.inferred_role == "thumbnail", item.width or 0, item.relative_path.casefold()
    ))
    hero = max(previews, key=lambda item: (
        item.inferred_role == "hero", (item.width or 0) * (item.height or 0), item.relative_path.casefold()
    ))
    for item in previews:
        roles = []
        if item is thumbnail:
            roles.append("thumbnail")
        if item is hero:
            roles.append("hero")
        item.selected_roles = tuple(roles)


def _model_role(stem: str, file_format: str, lod: str) -> str:
    lowered = stem.casefold()
    if file_format in {"BLEND", "MA", "MB"}:
        return "scene"
    if re.search(r"(?:^|[_-])hair(?:[_-]|$)", lowered):
        return "component"
    if re.search(r"(?:^|[_-])high(?:[_-]|$)", lowered):
        return "high"
    if lod:
        return "lod"
    return "mesh"


def _lod_label(stem: str) -> str:
    match = re.search(r"(?i)(?:^|[_-])lod[_-]?(\d+)(?:[_-]|$)", stem)
    return f"LOD{int(match.group(1))}" if match else ""


def _lod_number(label: str) -> int:
    digits = "".join(value for value in label if value.isdigit())
    return int(digits) if digits else 999


def _component_name(stem: str, lod: str) -> str:
    value = re.sub(r"(?i)(?:[_-])lod[_-]?\d+", "", stem)
    return re.sub(r"[_-]+", " ", value).strip().title()


def _material_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts[:-1]
    ignored = {"textures", "texture", "tex", "maps", "thumbs", "1k", "2k", "4k", "8k", "16k"}
    for value in reversed(parts):
        if value.casefold() not in ignored:
            return _display_name(value)
    return "Default"


def _model_category(categories: list[str], folder_name: str, default: str) -> str:
    lookup = {
        "building": "Architecture", "architecture": "Architecture", "furniture": "Furniture",
        "prop": "Props", "props": "Props", "nature": "Nature", "plant": "Plants", "plants": "Plants",
        "stone": "Rocks", "rock": "Rocks", "rocks": "Rocks", "vehicle": "Vehicles",
        "character": "Characters", "creature": "Creatures", "food": "Food", "industrial": "Industrial",
        "misc": "Miscellaneous", "miscellaneous": "Miscellaneous",
    }
    for value in categories:
        lowered = value.strip().casefold()
        if lowered in GENERIC_MODEL_CATEGORIES:
            continue
        if lowered in lookup:
            return lookup[lowered]
        if value.strip():
            return value.strip().title()
    folder = folder_name.casefold()
    if any(token in folder for token in ("raspberry", "fruit", "food")):
        return "Food"
    for token, category in lookup.items():
        if token in folder:
            return category
    return default if default in MODEL_CATEGORIES else "Uncategorized"


def _flatten_values(values: Any, semantic: dict[str, Any]) -> list[str]:
    result = [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []
    for key, value in semantic.items():
        if key in {"name", "locations", "minSize", "maxSize"}:
            continue
        if isinstance(value, list):
            result.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            result.append(value.strip())
    return sorted(set(result), key=str.casefold)


def _basename(value: Any) -> str:
    text = str(value or "")
    if not text or text.startswith("data:"):
        return ""
    return Path(urlparse(text).path).name


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _display_name(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title()
