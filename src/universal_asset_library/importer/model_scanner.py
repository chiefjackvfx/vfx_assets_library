from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable
from urllib.parse import unquote, urlparse

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
    _filename_preview_role,
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
DISPLAY_PREVIEW_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PREVIEW_DIRECTORY_TOKENS = {
    "preview", "previews", "thumbnail", "thumbnails", "thumb", "thumbs",
    "render", "renders", "rendering", "renderings", "screenshot", "screenshots",
    "gallery", "beauty", "stills",
}
TEXTURE_DIRECTORY_TOKENS = {
    "texture", "textures", "tex", "map", "maps", "material", "materials",
    "sourceimage", "sourceimages",
}
THUMBNAIL_NAME_TOKENS = {"thumbnail", "thumb", "icon", "swatch"}
THUMBNAIL_DIRECTORY_TOKENS = {"thumbnail", "thumbnails", "thumb", "thumbs"}
HERO_NAME_TOKENS = {
    "preview", "render", "beauty", "hero", "cover", "popup", "perspective",
    "angle", "front", "side", "threequarter", "turntable", "shot", "still",
}
NON_PREVIEW_NAME_TOKENS = {
    "logo", "license", "licence", "barcode", "qrcode", "wireframe", "wire",
    "diagram", "contactsheet",
}
MODEL_PAYLOAD_DIRECTORIES = {
    "model", "models", "mesh", "meshes", "geometry", "geo", "source", "sources",
    "scene", "scenes", "fbx", "obj", "usd",
}


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
    preview_roles_by_path: dict[str, str] = field(default_factory=dict)
    preview_roles_by_basename: dict[str, str] = field(default_factory=dict)
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
            selected = (
                scan_root
                if len(relative.parts) == 1
                or relative.parts[0].casefold() in MODEL_PAYLOAD_DIRECTORIES
                else scan_root / relative.parts[0]
            )
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
    preview_images = []
    image_basename_counts: dict[str, int] = {}
    for entry in accepted:
        if entry.snapshot.kind == "image":
            basename = entry.path.name.casefold()
            image_basename_counts[basename] = image_basename_counts.get(basename, 0) + 1
    ambiguous_preview_metadata: set[str] = set()
    assigned: set[str] = {item.relative_path for item in model_files}
    for entry in sorted(accepted, key=lambda item: str(item.path).casefold()):
        if entry.snapshot.kind != "image":
            continue
        relative = entry.path.relative_to(root).as_posix()
        basename = entry.path.name.casefold()
        role = facts.preview_roles_by_path.get(relative.casefold(), "")
        if not role and image_basename_counts.get(basename, 0) == 1:
            role = facts.preview_roles_by_basename.get(basename, "")
        elif (
            not role
            and basename in facts.preview_roles_by_basename
            and basename not in ambiguous_preview_metadata
        ):
            diagnostics.append(Diagnostic(
                "warning", "ambiguous_model_preview_basename",
                f"Provider preview metadata names {entry.path.name}, but multiple local images share that basename; using path and filename evidence instead.",
                relative, root.name,
            ))
            ambiguous_preview_metadata.add(basename)
        declaration = facts.texture_declarations.get(entry.path.name.casefold())
        if not declaration and _has_explicit_model_preview_signal(root, entry.path, role):
            preview_images.append((entry, relative, role))
            continue
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
        preview_images.append((entry, relative, role))

    for texture_set in texture_sets.values():
        for variant in texture_set.resolutions.values():
            _select_texture_preferences(variant)
    preview_candidates, preview_scores = _model_preview_candidates(
        root, facts.name or root.name, model_files, preview_images
    )
    _select_model_previews(preview_candidates, preview_scores)
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
        diagnostics.append(Diagnostic("warning", "model_preview_missing", "No model preview found; the catalog will use its built-in placeholder.", material=root.name))

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
        uri = str(record.get("uri", ""))
        basename = _basename(uri)
        tags = {str(value).casefold() for value in record.get("tags", [])}
        if basename and not uri.startswith("data:"):
            _register_preview_role(
                facts, uri,
                "hero" if tags & {"preview", "retina", "sidepanel"} else "thumbnail",
            )
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
        _register_preview_role(facts, str(document.get("thumbnail_url", "")), "thumbnail")
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


def _model_preview_candidates(root, asset_name, model_files, images):
    previews = []
    scores = {}
    model_names = {
        _normalized_preview_name(asset_name),
        _normalized_preview_name(root.name),
        *(_normalized_preview_name(Path(item.relative_path).stem) for item in model_files),
    }
    model_names = {value for value in model_names if len(value) >= 5}
    for entry, relative, metadata_role in images:
        path = entry.path
        display_format = path.suffix.casefold() in DISPLAY_PREVIEW_SUFFIXES
        name_tokens = _preview_tokens(path.stem)
        directory_tokens = {
            token
            for part in path.relative_to(root).parts[:-1]
            for token in _preview_tokens(part)
        }
        has_preview_directory = bool(directory_tokens & PREVIEW_DIRECTORY_TOKENS)
        has_thumbnail_directory = bool(directory_tokens & THUMBNAIL_DIRECTORY_TOKENS)
        in_texture_directory = bool(directory_tokens & TEXTURE_DIRECTORY_TOKENS)
        thumbnail_name = bool(name_tokens & THUMBNAIL_NAME_TOKENS)
        hero_name = bool(name_tokens & HERO_NAME_TOKENS)
        rejected_name = bool(name_tokens & NON_PREVIEW_NAME_TOKENS)
        normalized_stem = _normalized_preview_name(path.stem)
        asset_match = any(
            value in normalized_stem or normalized_stem in value
            for value in model_names
            if len(normalized_stem) >= 5
        )
        width = entry.snapshot.width or 0
        height = entry.snapshot.height or 0
        large_enough = width >= 96 and height >= 96
        explicit_signal = _has_explicit_model_preview_signal(root, path, metadata_role)
        if not display_format and not explicit_signal:
            continue
        if rejected_name and not metadata_role:
            continue
        if in_texture_directory and not explicit_signal:
            continue
        if not explicit_signal:
            continue

        aspect = width / height if width and height else 1.0
        if metadata_role:
            inferred = metadata_role
        elif has_thumbnail_directory:
            inferred = "thumbnail"
        elif thumbnail_name and not hero_name:
            inferred = "thumbnail"
        elif hero_name and not thumbnail_name:
            inferred = "hero"
        else:
            inferred = "hero" if aspect >= 1.35 else "thumbnail"
        score = (
            (1000 if metadata_role else 0)
            + (450 if thumbnail_name or hero_name else 0)
            + (350 if has_preview_directory else 0)
            + (220 if asset_match else 0)
            + (80 if path.parent == root else 0)
            + (40 if large_enough else 0)
        )
        candidate = PreviewCandidate(
            relative, entry.snapshot.width, entry.snapshot.height,
            inferred, metadata_role,
        )
        previews.append(candidate)
        scores[relative] = score
    return previews, scores


def _has_explicit_model_preview_signal(root, path, metadata_role):
    if metadata_role:
        return True
    name_tokens = _preview_token_list(path.stem)
    if set(name_tokens) & NON_PREVIEW_NAME_TOKENS:
        return False
    directory_tokens = {
        token
        for part in path.relative_to(root).parts[:-1]
        for token in _preview_tokens(part)
    }
    if directory_tokens & PREVIEW_DIRECTORY_TOKENS:
        return True
    return bool(_filename_preview_role(path.name))


def _select_model_previews(
    previews: list[PreviewCandidate], scores: dict[str, int] | None = None,
) -> None:
    if not previews:
        return
    evidence = scores or {}
    thumbnail = max(previews, key=lambda item: _model_preview_rank(item, "thumbnail", evidence))
    hero = max(previews, key=lambda item: _model_preview_rank(item, "hero", evidence))
    for item in previews:
        roles = []
        if item is thumbnail:
            roles.append("thumbnail")
        if item is hero:
            roles.append("hero")
        item.selected_roles = tuple(roles)


def _model_preview_rank(item, desired_role, evidence):
    aspect = (item.width / item.height) if item.width and item.height else 1.0
    shape_bonus = (
        max(0, 100 - int(abs(1.0 - aspect) * 100))
        if desired_role == "thumbnail"
        else (100 if aspect >= 1.35 else int(max(0.0, aspect - 1.0) * 100))
    )
    metadata_score = (
        5000 if item.metadata_role == desired_role
        else 0
    )
    role_score = (
        2000 if item.inferred_role == desired_role
        else 1200 if item.inferred_role == "candidate"
        else 600
    )
    pixels = (item.width or 0) * (item.height or 0)
    return (
        metadata_score + role_score + evidence.get(item.relative_path, 0) + shape_bonus,
        pixels,
        item.relative_path.casefold(),
    )


def _preview_tokens(value):
    return set(_preview_token_list(value))


def _preview_token_list(value):
    return [
        token for token in re.split(r"[^a-z0-9]+", str(value).casefold()) if token
    ]


def _normalized_preview_name(value):
    cleaned = re.sub(
        r"(?i)(?:^|[_\-\s])(?:lod[_\-]?\d+|thumbnail|thumb|preview|render|hero|cover)(?:$|[_\-\s])",
        " ", str(value),
    )
    return re.sub(r"[^a-z0-9]+", "", cleaned.casefold())


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


def _register_preview_role(facts: _ModelFacts, uri: str, role: str) -> None:
    parsed = urlparse(str(uri))
    path = unquote(parsed.path or str(uri)).replace("\\", "/")
    basename = Path(path).name
    if not basename:
        return
    facts.preview_roles_by_basename[basename.casefold()] = role
    relative = path.lstrip("./")
    if (
        relative
        and not parsed.scheme
        and not parsed.netloc
        and not path.startswith("/")
        and not re.match(r"^[A-Za-z]:/", path)
    ):
        facts.preview_roles_by_path[relative.casefold()] = role


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
