from __future__ import annotations

from pathlib import Path
import re
from time import monotonic
from typing import Callable

from .detection import detect_asset_type
from .hdri_scanner import hdri_resolution_label, loose_hdri_key, scan_hdri_folder
from .model_scanner import scan_model_folder
from .stock_scanner import scan_stock_folder
from .vdb_scanner import scan_vdb_folder
from .stock_taxonomy import StockTaxonomy
from .models import (
    Diagnostic,
    HdriCandidate,
    HdriFile,
    HdriVariant,
    MaterialCandidate,
    PreviewCandidate,
    ResolutionVariant,
    ScanCancellationToken,
    ScanCancelled,
    ScanProgress,
    ScanResult,
    SourceFileSnapshot,
    TextureMap,
)
from .scanner import (
    FORMAT_PRIORITY,
    TOKEN_PATTERNS,
    _filename_channel,
    _image_dimensions,
    scan_atlas_folder,
    scan_texture_folder,
)
from universal_asset_library.categories import CategoryCatalog


def scan_mixed_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    default_model_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
    ffprobe_path: str = "",
    stock_taxonomy: StockTaxonomy | None = None,
    texture_category_catalog: CategoryCatalog | None = None,
) -> ScanResult:
    """Classify each immediate package and loose environment file independently."""
    root = Path(source).expanduser().absolute()
    token = cancel_token or ScanCancellationToken()
    result = ScanResult(detected_asset_type="mixed")
    started = monotonic()
    if not root.is_dir():
        result.warnings.append("The selected mixed source is not an existing folder.")
        return result

    try:
        direct_files = [path for path in root.iterdir() if path.is_file() and not path.is_symlink() and not path.name.startswith(".")]
        has_child_directories = any(path.is_dir() and not path.name.startswith(".") for path in root.iterdir())
        is_single_package = any(
            path.suffix.casefold() == ".json"
            or path.suffix.casefold() == ".zip"
            or path.suffix.casefold() in {".usd", ".usda", ".usdc", ".usdz", ".fbx", ".obj", ".abc", ".gltf", ".glb", ".blend", ".ma", ".mb"}
            or path.suffix.casefold() in {".mov", ".mp4"}
            or path.suffix.casefold() == ".vdb"
            or (_filename_channel(path.name)[0] and not has_child_directories)
            for path in direct_files
        )
        if is_single_package:
            mode, reason = detect_asset_type(root)
            scanner = {
                "model": scan_model_folder, "hdri": scan_hdri_folder, "atlas": scan_atlas_folder,
                "stock": scan_stock_folder,
                "vdb": scan_vdb_folder,
            }.get(mode, scan_texture_folder)
            category = default_model_category if mode == "model" else default_category
            scanner_kwargs = {"progress": progress, "cancel_token": token}
            if mode == "stock":
                scanner_kwargs["ffprobe_path"] = ffprobe_path
                scanner_kwargs["taxonomy"] = stock_taxonomy
            elif mode == "texture_set":
                scanner_kwargs["category_catalog"] = texture_category_catalog
            single = scanner(root, category, **scanner_kwargs)
            single.detected_asset_type = mode
            single.detection_reason = reason
            for candidate in single.materials:
                candidate.diagnostics.insert(0, Diagnostic(
                    "info", "asset_type_detected",
                    f"Detected as {_type_label(mode)} because {reason}.", material=str(candidate.source_root),
                ))
            return single

        children = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".") and not path.is_symlink()),
            key=lambda path: path.name.casefold(),
        )
        loose_environments = sorted(
            (
                path for path in root.iterdir()
                if path.is_file() and not path.is_symlink() and path.suffix.casefold() in {".hdr", ".exr"}
                and not _filename_channel(path.name)[0]
            ),
            key=lambda path: path.name.casefold(),
        )
        loose_texture_groups: dict[str, list[Path]] = {}
        for path in direct_files:
            if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".tga", ".webp", ".exr", ".hdr"}:
                continue
            if not _filename_channel(path.name)[0]:
                continue
            loose_texture_groups.setdefault(_loose_texture_key(path), []).append(path)
        loose_hdri_groups: dict[str, list[Path]] = {}
        for path in loose_environments:
            loose_hdri_groups.setdefault(loose_hdri_key(path).casefold(), []).append(path)
        total_units = len(children) + len(loose_hdri_groups) + len(loose_texture_groups)
        completed_units = 0

        for child in children:
            token.check()
            mode, reason = detect_asset_type(child)
            scanner = {
                "model": scan_model_folder, "hdri": scan_hdri_folder, "atlas": scan_atlas_folder,
                "stock": scan_stock_folder,
                "vdb": scan_vdb_folder,
            }.get(mode, scan_texture_folder)
            category = default_model_category if mode == "model" else default_category

            def child_progress(value: ScanProgress, folder=child) -> None:
                if progress:
                    progress(ScanProgress(
                        f"{folder.name}: {value.phase}", value.examined_files, value.total_files,
                        len(result.materials) + value.materials_found,
                        sum(item.severity == "warning" for item in result.diagnostics) + value.warning_count,
                        monotonic() - started,
                    ))

            scanner_kwargs = {"progress": child_progress, "cancel_token": token}
            if mode == "stock":
                scanner_kwargs["ffprobe_path"] = ffprobe_path
                scanner_kwargs["taxonomy"] = stock_taxonomy
            elif mode == "texture_set":
                scanner_kwargs["category_catalog"] = texture_category_catalog
            scanned = scanner(child, category, **scanner_kwargs)
            if scanned.canceled:
                raise ScanCancelled("Mixed scan canceled.")
            for candidate in scanned.materials:
                candidate.diagnostics.insert(0, Diagnostic(
                    "info", "asset_type_detected",
                    f"Detected as {_type_label(mode)} because {reason}.", material=str(candidate.source_root),
                ))
            result.materials.extend(scanned.materials)
            result.diagnostics.extend(scanned.diagnostics)
            result.ignored_files.extend(scanned.ignored_files)
            result.warnings.extend(f"{child.name}: {warning}" for warning in scanned.warnings)
            completed_units += 1
            _emit(progress, "Classifying asset folders", completed_units, total_units, result, started)

        for _key, paths in sorted(loose_hdri_groups.items()):
            token.check()
            candidate = _loose_hdri_group(root, paths, default_category)
            result.materials.append(candidate)
            result.diagnostics.extend(candidate.diagnostics)
            completed_units += 1
            _emit(progress, "Classifying loose HDRIs", completed_units, total_units, result, started)
        for key, paths in sorted(loose_texture_groups.items()):
            token.check()
            candidate = _loose_texture(root, key, paths, default_category)
            result.materials.append(candidate)
            result.diagnostics.extend(candidate.diagnostics)
            completed_units += 1
            _emit(progress, "Grouping loose texture maps", completed_units, total_units, result, started)
    except (OSError, ScanCancelled) as error:
        if isinstance(error, ScanCancelled):
            result.canceled = True
            result.materials.clear()
            return result
        result.diagnostics.append(Diagnostic("error", "mixed_scan_failed", str(error), str(root)))

    result.materials.sort(key=lambda item: (item.asset_type, item.name.casefold()))
    counts: dict[str, int] = {}
    for candidate in result.materials:
        counts[candidate.asset_type] = counts.get(candidate.asset_type, 0) + 1
    result.detection_reason = " · ".join(
        f"{count} {_type_label(asset_type, plural=count != 1)}"
        for asset_type, count in (
            ("texture_set", counts.get("texture_set", 0)),
            ("atlas", counts.get("atlas", 0)),
            ("hdri", counts.get("hdri", 0)),
            ("model", counts.get("model", 0)),
            ("vdb", counts.get("vdb", 0)),
            ("stock", counts.get("stock", 0)),
        )
        if count
    ) or "no supported assets found"
    return result


def _loose_hdri_group(root: Path, paths: list[Path], default_category: str) -> HdriCandidate:
    variants: dict[str, HdriVariant] = {}
    snapshots: dict[str, SourceFileSnapshot] = {}
    for path in sorted(paths, key=lambda item: item.name.casefold()):
        stat = path.stat()
        width, height = _image_dimensions(path)
        label = hdri_resolution_label(width)
        if label == "Unknown":
            label = _resolution_label(width, path.stem)
        relative = path.relative_to(root).as_posix()
        variant = variants.setdefault(label, HdriVariant(label, width, height))
        if width and height:
            variant.width = max(variant.width or 0, width)
            variant.height = max(variant.height or 0, height)
        variant.files.append(HdriFile(relative, path.suffix.lstrip(".").upper()))
        snapshots[relative] = SourceFileSnapshot(relative, stat.st_size, stat.st_mtime_ns, "image", width, height)
    for variant in variants.values():
        preferred = max(variant.files, key=lambda item: (item.file_format == "EXR", item.relative_path.casefold()))
        preferred.preferred = True
    display_key = loose_hdri_key(paths[0])
    relative = paths[0].relative_to(root).as_posix()
    warning = "No readable preview found; a JPEG placeholder will be generated during import."
    diagnostics = [
        Diagnostic("info", "asset_type_detected", "Detected and grouped as a loose HDRI from its HDR/EXR files and dimensions.", relative, display_key),
        Diagnostic("warning", "hdri_preview_missing", warning, relative, display_key),
    ]
    if any(not snapshot.width or not snapshot.height for snapshot in snapshots.values()):
        diagnostics.append(Diagnostic(
            "warning", "image_header_unavailable",
            "One or more HDRI dimensions could not be read; filename resolution will be used.", relative, display_key,
        ))
    return HdriCandidate(
        source_root=root,
        provider="Unknown",
        provider_id="",
        name=re.sub(r"[_-]+", " ", display_key).strip().title(),
        category=default_category,
        resolutions=variants,
        warnings=[warning],
        diagnostics=diagnostics,
        source_snapshots=snapshots,
    )


def _loose_texture(root: Path, key: str, paths: list[Path], default_category: str) -> MaterialCandidate:
    variants: dict[str, ResolutionVariant] = {}
    snapshots: dict[str, SourceFileSnapshot] = {}
    for path in sorted(paths, key=lambda item: item.name.casefold()):
        stat = path.stat()
        width, height = _image_dimensions(path)
        label = _resolution_label(width, path.stem)
        channel, convention, packed = _filename_channel(path.name)
        relative = path.relative_to(root).as_posix()
        texture = TextureMap(
            channel, relative, path.suffix.lstrip(".").upper(),
            normal_convention=convention, packed_channels=packed,
        )
        variant = variants.setdefault(label, ResolutionVariant(label, width, height))
        variant.maps.setdefault(channel, []).append(texture)
        snapshots[relative] = SourceFileSnapshot(relative, stat.st_size, stat.st_mtime_ns, "image", width, height)
    for variant in variants.values():
        for alternatives in variant.maps.values():
            preferred = max(alternatives, key=lambda item: (FORMAT_PRIORITY.get(item.file_format.casefold(), 0), item.relative_path.casefold()))
            preferred.preferred = True
    base_color = next(
        (item for variant in variants.values() for item in variant.maps.get("Base Color", [])),
        None,
    )
    previews = []
    selected = ""
    if base_color:
        snapshot = snapshots[base_color.relative_path]
        previews = [PreviewCandidate(base_color.relative_path, snapshot.width, snapshot.height, "thumbnail", fallback=True, selected_roles=("thumbnail", "hero"))]
        selected = base_color.relative_path
    diagnostic = Diagnostic(
        "info", "asset_type_detected", "Detected as a loose texture material from PBR channel filenames.", material=key,
    )
    return MaterialCandidate(
        source_root=root,
        provider="Unknown",
        name=re.sub(r"[_-]+", " ", key).strip().title(),
        category=default_category,
        resolutions=variants,
        previews=previews,
        selected_thumbnail=selected,
        selected_hero=selected,
        diagnostics=[diagnostic],
        source_snapshots=snapshots,
    )


def _loose_texture_key(path: Path) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", path.stem.casefold()).strip("_")
    aliases = sorted({alias for _channel, values in TOKEN_PATTERNS for alias in values}, key=len, reverse=True)
    for alias in aliases:
        normalized = re.sub(r"[^a-z0-9]+", "_", alias.casefold()).strip("_")
        value = re.sub(rf"(?:^|_){re.escape(normalized)}(?:_|$)", "_", value)
    value = re.sub(r"(?:^|_)(?:1|2|4|8|16)k(?:_|$)", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or path.stem


def _resolution_label(width: int | None, stem: str) -> str:
    if width:
        return f"{max(1, round(width / 1024))}K"
    match = re.search(r"(?i)(?:^|[^a-z0-9])(1|2|4|8|16)k(?:[^a-z0-9]|$)", stem)
    return f"{match.group(1)}K" if match else "Unknown"


def _type_label(asset_type: str, plural: bool = False) -> str:
    labels = {
        "texture_set": "texture material", "atlas": "atlas", "hdri": "HDRI",
        "model": "model", "stock": "Stock clip",
        "vdb": "VDB volume",
    }
    label = labels.get(asset_type, asset_type)
    if not plural:
        return label
    return "atlases" if asset_type == "atlas" else label + "s"


def _emit(callback, phase: str, completed: int, total: int, result: ScanResult, started: float) -> None:
    if callback:
        callback(ScanProgress(
            phase, completed, total, len(result.materials),
            sum(item.severity == "warning" for item in result.diagnostics), monotonic() - started,
        ))
