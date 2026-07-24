from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Callable

from PyQt6.QtGui import QImageReader

from universal_asset_library.domain import ATLAS_CATEGORIES, TEXTURE_CATEGORIES
from universal_asset_library.categories import CategoryCatalog, default_category_catalog

from .adapters import ADAPTERS, MetadataFacts, MapDeclaration, resolution_label
from .models import (
    ArchiveSource,
    Diagnostic,
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


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".tga", ".exr", ".hdr", ".webp"}
IGNORED_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}
JSON_LIMIT = 10 * 1024 * 1024
MAP_CONTAINER_NAMES = {"textures", "texture", "maps", "images"}
GENERIC_CATEGORIES = {"surface", "outdoor", "indoor", "floor", "wall", "manmade", "man made"}
FORMAT_PRIORITY = {"exr": 6, "tif": 5, "tiff": 5, "png": 4, "webp": 3, "tga": 2, "jpg": 1, "jpeg": 1}
ARCHIVE_EXTENSIONS = {".rar", ".zip"}
ARCHIVE_PREVIEW_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ARCHIVE_MAP_EXTENSIONS = IMAGE_EXTENSIONS - {".hdr"}
ARCHIVE_MEMBER_LIMIT = 4096
ARCHIVE_EXPANDED_LIMIT = 64 * 1024 * 1024 * 1024
ARCHIVE_RATIO_LIMIT = 500


TOKEN_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Packed ARM", ("arm",)),
    ("Base Color", ("basecolor", "base_color", "base-color", "albedo", "diffuse", "diff", "col")),
    ("Ambient Occlusion", ("ambientocclusion", "ambient_occlusion", "occlusion", "ao")),
    ("Roughness", ("roughness", "rough")),
    ("Glossiness", ("glossiness", "gloss")),
    ("Normal", ("normal_ogl", "normal_gl", "normal_dx", "norm_ogl", "norm_gl", "norm_dx", "nor_gl", "nor_dx", "normal", "norm", "nrm", "nor")),
    ("Displacement", ("displacement", "displace", "disp")),
    ("Height", ("height", "hgt")),
    ("Bump", ("bump", "bmp")),
    ("Cavity", ("cavity",)),
    ("Metalness", ("metalness", "metallic", "metal")),
    ("Specular", ("specular", "spec", "reflection", "reflect", "refl")),
    ("Opacity", ("opacity", "alpha")),
    ("Emission", ("emission", "emissive", "emit")),
    ("Translucency", ("translucency", "translucent", "subsurface", "sss")),
)


@dataclass(slots=True)
class _InventoryEntry:
    path: Path
    snapshot: SourceFileSnapshot
    diagnostics: list[Diagnostic] = field(default_factory=list)


def scan_texture_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
    category_catalog: CategoryCatalog | None = None,
) -> ScanResult:
    root = Path(source).expanduser().absolute()
    token = cancel_token or ScanCancellationToken()
    catalog = category_catalog or default_category_catalog("texture_set")
    plain = _scan_texture_folder_plain(
        root, default_category, progress, token, catalog
    )
    if plain.canceled:
        return plain
    archives = _scan_archive_material_pairs(
        root, default_category, progress, token, catalog
    )
    if archives.canceled:
        _remove_temporary_roots(archives.temporary_roots)
        plain.canceled = True
        plain.materials.clear()
        return plain
    plain.materials.extend(archives.materials)
    plain.materials.sort(key=lambda item: item.name.casefold())
    if archives.materials:
        plain.warnings = [
            warning for warning in plain.warnings
            if warning != "No folders containing recognizable PBR texture sets were found."
        ]
    plain.inventory = (*plain.inventory, *archives.inventory)
    plain.diagnostics.extend(archives.diagnostics)
    plain.warnings.extend(archives.warnings)
    plain.ignored_files.extend(archives.ignored_files)
    plain.temporary_roots.extend(archives.temporary_roots)
    return plain


def _scan_texture_folder_plain(
    source: str | Path,
    default_category: str,
    progress: Callable[[ScanProgress], None] | None,
    cancel_token: ScanCancellationToken,
    category_catalog: CategoryCatalog,
) -> ScanResult:
    root = Path(source).expanduser().absolute()
    result = ScanResult()
    token = cancel_token
    started = monotonic()
    if not root.exists():
        result.warnings.append("The selected source folder does not exist.")
        return result
    if not root.is_dir():
        result.warnings.append("The selected source path is not a folder.")
        return result

    try:
        inventory, inventory_diagnostics = _build_inventory(root, token, progress, started)
    except ScanCancelled:
        result.canceled = True
        return result
    result.inventory = tuple(entry.snapshot for entry in inventory)
    result.diagnostics.extend(inventory_diagnostics)
    material_roots = _discover_material_roots(root, inventory)
    if not material_roots:
        result.warnings.append("No folders containing recognizable PBR texture sets were found.")
        return result
    entries_by_material = _group_inventory_by_material(root, material_roots, inventory)
    try:
        for index, material_root in enumerate(material_roots):
            token.check()
            material_entries = entries_by_material[material_root]
            candidate, ignored = _scan_material(
                material_root,
                default_category,
                material_entries,
                category_catalog,
            )
            result.ignored_files.extend(ignored)
            result.diagnostics.extend(candidate.diagnostics)
            if candidate.resolutions:
                result.materials.append(candidate)
            else:
                result.warnings.append(f"No recognized texture maps found in {material_root}.")
            _emit_scan_progress(progress, "Parsing materials", len(inventory), len(inventory), index + 1, result.diagnostics, started)
    except ScanCancelled:
        result.canceled = True
        result.materials.clear()
        return result
    result.materials.sort(key=lambda item: item.name.casefold())
    result.ignored_files = sorted(set(result.ignored_files), key=str.casefold)
    return result


def _scan_archive_material_pairs(
    root: Path,
    default_category: str,
    progress: Callable[[ScanProgress], None] | None,
    token: ScanCancellationToken,
    category_catalog: CategoryCatalog,
) -> ScanResult:
    result = ScanResult()
    if not root.is_dir():
        return result
    pairs, pair_diagnostics = _archive_preview_pairs(root)
    result.diagnostics.extend(pair_diagnostics)
    result.warnings.extend(
        item.message for item in pair_diagnostics if item.severity == "warning"
    )
    if not pairs:
        return result
    extractor = shutil.which("bsdtar")
    workspace = Path(tempfile.mkdtemp(prefix="shotbox-archive-import-"))
    result.temporary_roots.append(str(workspace))
    try:
        for index, (archive, preview) in enumerate(pairs, start=1):
            token.check()
            package_root = workspace / f"{index:04d}_{archive.stem}"
            package_root.mkdir(parents=True)
            try:
                if archive.suffix.casefold() == ".zip":
                    extracted = _extract_zip_images(
                        archive, package_root / "maps", token
                    )
                else:
                    if not extractor:
                        raise RuntimeError(
                            "bsdtar is unavailable; install libarchive/bsdtar "
                            "to import RAR packages."
                        )
                    extracted = _extract_rar_images(
                        archive, package_root / "maps", extractor, token
                    )
                if not extracted:
                    raise RuntimeError("The archive contains no supported texture images.")
                archive_stat = archive.stat()
                preview_stat = preview.stat() if preview is not None else None
                if preview is not None:
                    preview_target = package_root / preview.name
                    shutil.copy2(preview, preview_target)
                category = (
                    category_catalog.canonical_name(archive.parent.name)
                    or default_category
                )
                scanned = _scan_texture_folder_plain(
                    package_root, category, None, token, category_catalog
                )
                if not scanned.materials:
                    raise RuntimeError(
                        "No recognizable PBR maps were found after extraction."
                    )
                candidate = scanned.materials[0]
                candidate.name = _display_name(archive.stem)
                candidate.category = category
                candidate.archive_source = ArchiveSource(
                    archive.resolve(),
                    preview.resolve() if preview is not None else None,
                    archive_stat.st_size,
                    archive_stat.st_mtime_ns,
                    preview_stat.st_size if preview_stat else 0,
                    preview_stat.st_mtime_ns if preview_stat else 0,
                    archive.suffix.lstrip(".").upper(),
                )
                diagnostic = (
                    Diagnostic(
                        "info",
                        "archive_preview_pair",
                        f"Paired {preview.name} as the preview for {archive.name}.",
                        archive.relative_to(root).as_posix(),
                        candidate.name,
                    )
                    if preview is not None else
                    Diagnostic(
                        "info",
                        "archive_scanned_without_external_preview",
                        f"Extracted {archive.name} during scanning; an internal "
                        "preview or Base Color fallback was selected.",
                        archive.relative_to(root).as_posix(),
                        candidate.name,
                    )
                )
                candidate.diagnostics.insert(0, diagnostic)
                result.materials.append(candidate)
                result.diagnostics.extend(candidate.diagnostics)
                snapshots = [
                    SourceFileSnapshot(
                        archive.relative_to(root).as_posix(),
                        archive_stat.st_size,
                        archive_stat.st_mtime_ns,
                        "archive",
                    ),
                ]
                if preview is not None and preview_stat is not None:
                    snapshots.append(SourceFileSnapshot(
                        preview.relative_to(root).as_posix(),
                        preview_stat.st_size,
                        preview_stat.st_mtime_ns,
                        "preview",
                        *_image_dimensions(preview),
                    ))
                result.inventory = (*result.inventory, *snapshots)
            except ScanCancelled:
                raise
            except Exception as error:
                shutil.rmtree(package_root, ignore_errors=True)
                message = f"Could not read {archive.name}: {error}"
                result.warnings.append(message)
                result.diagnostics.append(Diagnostic(
                    "error", "archive_package_failed", message,
                    archive.relative_to(root).as_posix(), archive.stem,
                ))
            _emit_scan_progress(
                progress,
                "Extracting archive materials",
                index,
                len(pairs),
                len(result.materials),
                result.diagnostics,
                monotonic(),
            )
    except ScanCancelled:
        result.canceled = True
        result.materials.clear()
        _remove_temporary_roots(result.temporary_roots)
        result.temporary_roots.clear()
    if not result.materials:
        _remove_temporary_roots(result.temporary_roots)
        result.temporary_roots.clear()
    return result


def _archive_preview_pairs(
    root: Path,
) -> tuple[list[tuple[Path, Path | None]], list[Diagnostic]]:
    pairs: list[tuple[Path, Path | None]] = []
    diagnostics: list[Diagnostic] = []
    archives = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
            and not any(part.startswith(".") for part in path.relative_to(root).parts)
            and path.suffix.casefold() in ARCHIVE_EXTENSIONS
        ),
        key=lambda path: str(path).casefold(),
    )
    for archive in archives:
        matches = [
            path for path in archive.parent.iterdir()
            if path.is_file() and not path.is_symlink()
            and path.suffix.casefold() in ARCHIVE_PREVIEW_EXTENSIONS
            and path.stem.casefold() == archive.stem.casefold()
        ]
        if len(matches) == 1:
            pairs.append((archive, matches[0]))
        elif not matches:
            if archive.suffix.casefold() == ".zip":
                pairs.append((archive, None))
                diagnostics.append(Diagnostic(
                    "info", "zip_preview_external_missing",
                    f"{archive.name} has no same-name external preview; "
                    "the ZIP will still be extracted and scanned.",
                    archive.relative_to(root).as_posix(), archive.stem,
                ))
            else:
                diagnostics.append(Diagnostic(
                    "warning", "archive_preview_missing",
                    f"{archive.name} has no same-name JPEG or image preview.",
                    archive.relative_to(root).as_posix(), archive.stem,
                ))
        else:
            if archive.suffix.casefold() == ".zip":
                pairs.append((archive, None))
            diagnostics.append(Diagnostic(
                "warning" if archive.suffix.casefold() == ".zip" else "error",
                "archive_preview_ambiguous",
                f"{archive.name} has multiple same-name previews; no preview was guessed."
                + (" The ZIP will still be extracted and scanned."
                   if archive.suffix.casefold() == ".zip" else ""),
                archive.relative_to(root).as_posix(), archive.stem,
            ))
    return pairs, diagnostics


def _extract_rar_images(
    archive: Path,
    destination: Path,
    extractor: str,
    token: ScanCancellationToken,
) -> list[Path]:
    listed = subprocess.run(
        [extractor, "-tf", str(archive)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if listed.returncode:
        raise RuntimeError(
            listed.stderr.strip() or "The archive directory could not be read."
        )
    members = [line.rstrip("\r") for line in listed.stdout.splitlines() if line.strip()]
    if len(members) > ARCHIVE_MEMBER_LIMIT:
        raise RuntimeError(
            f"The archive contains {len(members)} entries; the safety limit is {ARCHIVE_MEMBER_LIMIT}."
        )
    verbose = subprocess.run(
        [extractor, "-tvf", str(archive)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if verbose.returncode:
        raise RuntimeError(
            verbose.stderr.strip() or "The archive members could not be validated."
        )
    regular_members = {
        member for member in members
        if any(
            line.startswith("-") and line.rstrip().endswith(member)
            for line in verbose.stdout.splitlines()
        )
    }
    selected = []
    for member in members:
        token.check()
        normalized = member.replace("\\", "/")
        parts = Path(normalized).parts
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in parts)
            or "\x00" in normalized
            or "\n" in normalized
            or "\r" in normalized
        ):
            raise RuntimeError(f"Unsafe archive member path: {member!r}")
        if Path(normalized).suffix.casefold() not in ARCHIVE_MAP_EXTENSIONS:
            continue
        if member not in regular_members:
            raise RuntimeError(f"Archive member is not a regular file: {member}")
        selected.append(member)
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    used: set[str] = set()
    expanded = 0
    for member in selected:
        token.check()
        filename = _unique_archive_filename(Path(member.replace("\\", "/")).name, used)
        target = destination / filename
        process = subprocess.Popen(
            [extractor, "-xOf", str(archive), "--", member],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            with target.open("xb") as handle:
                while True:
                    token.check()
                    chunk = process.stdout.read(1024 * 1024) if process.stdout else b""
                    if not chunk:
                        break
                    handle.write(chunk)
                    expanded += len(chunk)
                    if (
                        expanded > ARCHIVE_EXPANDED_LIMIT
                        or expanded > max(archive.stat().st_size * ARCHIVE_RATIO_LIMIT, 1024 * 1024)
                    ):
                        raise RuntimeError("The archive exceeds the safe expanded-size limit.")
            stderr = process.communicate(timeout=60)[1]
            if process.returncode:
                raise RuntimeError(
                    stderr.decode("utf-8", errors="replace").strip()
                    or f"Could not extract {member}."
                )
            if not target.stat().st_size:
                raise RuntimeError(f"Archive member is empty: {member}")
            extracted.append(target)
        except Exception:
            process.kill()
            process.communicate()
            target.unlink(missing_ok=True)
            raise
    return extracted


def _extract_zip_images(
    archive: Path,
    destination: Path,
    token: ScanCancellationToken,
) -> list[Path]:
    try:
        package = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"The ZIP directory could not be read: {error}") from error
    with package:
        members = package.infolist()
        if len(members) > ARCHIVE_MEMBER_LIMIT:
            raise RuntimeError(
                f"The archive contains {len(members)} entries; "
                f"the safety limit is {ARCHIVE_MEMBER_LIMIT}."
            )
        selected: list[zipfile.ZipInfo] = []
        declared_size = 0
        compressed_size = 0
        for member in members:
            token.check()
            normalized = member.filename.replace("\\", "/")
            parts = Path(normalized).parts
            if (
                normalized.startswith("/")
                or re.match(r"^[A-Za-z]:", normalized)
                or any(part in {"", ".", ".."} for part in parts)
                or "\x00" in normalized
                or "\n" in normalized
                or "\r" in normalized
            ):
                raise RuntimeError(f"Unsafe archive member path: {member.filename!r}")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError(
                    f"Archive member is a symbolic link: {member.filename}"
                )
            if member.is_dir():
                continue
            if member.flag_bits & 0x1:
                raise RuntimeError(
                    f"Encrypted ZIP members are unsupported: {member.filename}"
                )
            if Path(normalized).suffix.casefold() not in ARCHIVE_MAP_EXTENSIONS:
                continue
            declared_size += member.file_size
            compressed_size += member.compress_size
            selected.append(member)
        if (
            declared_size > ARCHIVE_EXPANDED_LIMIT
            or declared_size
            > max(compressed_size * ARCHIVE_RATIO_LIMIT, 1024 * 1024)
        ):
            raise RuntimeError("The archive exceeds the safe expanded-size limit.")
        destination.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        used: set[str] = set()
        expanded = 0
        for member in selected:
            token.check()
            filename = _unique_archive_filename(
                Path(member.filename.replace("\\", "/")).name, used
            )
            target = destination / filename
            try:
                with package.open(member) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        token.check()
                        output.write(chunk)
                        expanded += len(chunk)
                        if expanded > ARCHIVE_EXPANDED_LIMIT:
                            raise RuntimeError(
                                "The archive exceeds the safe expanded-size limit."
                            )
                if not target.stat().st_size:
                    raise RuntimeError(
                        f"Archive member is empty: {member.filename}"
                    )
                extracted.append(target)
            except Exception:
                target.unlink(missing_ok=True)
                raise
        return extracted


def _unique_archive_filename(name: str, used: set[str]) -> str:
    candidate = name
    number = 2
    while candidate.casefold() in used:
        path = Path(name)
        candidate = f"{path.stem}_{number}{path.suffix}"
        number += 1
    used.add(candidate.casefold())
    return candidate


def _remove_temporary_roots(roots: list[str]) -> None:
    for value in roots:
        path = Path(value)
        if path.name.startswith("shotbox-archive-import-"):
            shutil.rmtree(path, ignore_errors=True)


def scan_atlas_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
) -> ScanResult:
    """Scan PBR cutout packages while explicitly classifying them as atlases."""
    result = scan_texture_folder(source, default_category, progress, cancel_token)
    result.detected_asset_type = "atlas"
    for candidate in result.materials:
        candidate.asset_type = "atlas"
        removed_resolutions: set[str] = set()
        removed_paths: set[str] = set()
        for label, variant in list(candidate.resolutions.items()):
            for channel, alternatives in list(variant.maps.items()):
                retained = [
                    item for item in alternatives
                    if "thumbs" not in {part.casefold() for part in Path(item.relative_path).parts[:-1]}
                ]
                removed_paths.update(item.relative_path for item in alternatives if item not in retained)
                if retained:
                    variant.maps[channel] = retained
                else:
                    del variant.maps[channel]
            if not variant.maps:
                del candidate.resolutions[label]
                removed_resolutions.add(label)
        if removed_paths:
            candidate.extra_paths = sorted(set(candidate.extra_paths) | removed_paths, key=str.casefold)
        if removed_resolutions:
            warning = (
                "Metadata declares unavailable source resolutions: "
                + ", ".join(sorted(removed_resolutions, key=_resolution_sort_key))
                + "; reduced maps under Thumbs are retained as extras."
            )
            candidate.warnings.append(warning)
            candidate.diagnostics.append(Diagnostic(
                "warning", "atlas_thumbnail_resolution_excluded", warning, material=candidate.source_root.name,
            ))
        candidate.category = _select_atlas_category(
            candidate.tags,
            candidate.source_root.name,
            default_category,
        )
        candidate.tags = _single_category_tags(candidate.tags, (), candidate.category)
    return result


def _group_inventory_by_material(
    scan_root: Path,
    material_roots: list[Path],
    inventory: list[_InventoryEntry],
) -> dict[Path, list[_InventoryEntry]]:
    grouped = {root: [] for root in material_roots}
    for entry in inventory:
        parent = entry.path.parent
        while _is_within(parent, scan_root):
            if parent in grouped:
                grouped[parent].append(entry)
                break
            if parent == scan_root:
                break
            parent = parent.parent
    return grouped


def _build_inventory(
    root: Path,
    token: ScanCancellationToken,
    progress: Callable[[ScanProgress], None] | None,
    started: float,
    report_exclusions: bool = False,
) -> tuple[list[_InventoryEntry], list[Diagnostic]]:
    entries: list[_InventoryEntry] = []
    diagnostics: list[Diagnostic] = []

    def walk_error(error: OSError) -> None:
        diagnostics.append(Diagnostic("error", "filesystem_walk_error", str(error), str(error.filename or "")))

    examined = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False, onerror=walk_error):
        token.check()
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in directory_names:
            path = current_path / name
            if name.startswith("."):
                if report_exclusions:
                    diagnostics.append(Diagnostic("info", "hidden_directory_excluded", "Excluded hidden directory.", path.relative_to(root).as_posix()))
                continue
            if path.is_symlink():
                diagnostics.append(Diagnostic("warning", "symlink_ignored", "Ignored symbolic-link directory.", path.relative_to(root).as_posix()))
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in file_names:
            token.check()
            if name.startswith("."):
                if report_exclusions:
                    diagnostics.append(Diagnostic("info", "hidden_file_excluded", "Excluded hidden file.", (current_path / name).relative_to(root).as_posix()))
                continue
            if name.casefold() in IGNORED_NAMES:
                if report_exclusions:
                    diagnostics.append(Diagnostic("info", "os_junk_excluded", "Excluded operating-system metadata.", (current_path / name).relative_to(root).as_posix()))
                continue
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                diagnostics.append(Diagnostic("warning", "symlink_ignored", "Ignored symbolic-link file.", relative))
                continue
            try:
                stat = path.stat()
            except OSError as error:
                diagnostics.append(Diagnostic("error", "file_stat_failed", str(error), relative))
                continue
            suffix = path.suffix.casefold()
            kind = "image" if suffix in IMAGE_EXTENSIONS else "json" if suffix == ".json" else "other"
            width = height = None
            entry_diagnostics: list[Diagnostic] = []
            if kind == "image":
                if stat.st_size <= 0:
                    kind = "invalid_image"
                    entry_diagnostics.append(Diagnostic("error", "empty_image", "Ignored an empty image file.", relative))
                else:
                    width, height = _image_dimensions(path)
                    if not width or not height:
                        if suffix in {".exr", ".hdr"}:
                            entry_diagnostics.append(Diagnostic(
                                "warning", "image_header_unavailable", f"{suffix[1:].upper()} dimensions could not be read; metadata or filename resolution will be used.", relative
                            ))
                        else:
                            kind = "invalid_image"
                            entry_diagnostics.append(Diagnostic("error", "unreadable_image", "Ignored an unreadable or corrupt image.", relative))
            snapshot = SourceFileSnapshot(relative, stat.st_size, stat.st_mtime_ns, kind, width, height)
            entries.append(_InventoryEntry(path, snapshot, entry_diagnostics))
            diagnostics.extend(entry_diagnostics)
            examined += 1
            if examined == 1 or examined % 64 == 0:
                _emit_scan_progress(progress, "Inventory", examined, 0, 0, diagnostics, started)
    _emit_scan_progress(progress, "Inventory complete", examined, examined, 0, diagnostics, started)
    return entries, diagnostics


def _emit_scan_progress(
    callback: Callable[[ScanProgress], None] | None,
    phase: str,
    examined: int,
    total: int,
    materials: int,
    diagnostics: list[Diagnostic],
    started: float,
) -> None:
    if callback:
        callback(ScanProgress(
            phase,
            examined,
            total,
            materials,
            sum(item.severity == "warning" for item in diagnostics),
            monotonic() - started,
        ))


def _discover_material_roots(root: Path, inventory: list[_InventoryEntry]) -> list[Path]:
    files_by_directory: dict[Path, list[_InventoryEntry]] = {}
    subtree_channel_counts: dict[Path, int] = {}
    for entry in inventory:
        files_by_directory.setdefault(entry.path.parent, []).append(entry)
        if entry.snapshot.kind != "image" or not _filename_channel(entry.path.name)[0]:
            continue
        parent = entry.path.parent
        while _is_within(parent, root):
            subtree_channel_counts[parent] = subtree_channel_counts.get(parent, 0) + 1
            if parent == root:
                break
            parent = parent.parent

    anchored: list[Path] = []
    for directory, entries in files_by_directory.items():
        if any(entry.snapshot.kind == "json" for entry in entries):
            if subtree_channel_counts.get(directory, 0) >= 1:
                anchored.append(directory)

    generic: list[Path] = []
    for directory, entries in files_by_directory.items():
        if any(_is_within(directory, anchor) for anchor in anchored):
            continue
        channel_count = sum(1 for entry in entries if entry.snapshot.kind == "image" and _filename_channel(entry.path.name)[0])
        if channel_count < 1:
            continue
        candidate = directory.parent if directory.name.casefold() in MAP_CONTAINER_NAMES or _resolution_directory(directory.name) else directory
        if not any(_is_within(candidate, anchor) for anchor in anchored):
            generic.append(candidate)

    roots: list[Path] = []
    for candidate in sorted(set((*anchored, *generic)), key=lambda path: (len(path.parts), str(path).casefold())):
        if any(_is_within(candidate, existing) for existing in roots):
            continue
        roots.append(candidate)
    return roots


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _scan_material(
    root: Path,
    default_category: str,
    entries: list[_InventoryEntry],
    category_catalog: CategoryCatalog,
) -> tuple[MaterialCandidate, list[str]]:
    warnings: list[str] = []
    ignored: list[str] = []
    diagnostics: list[Diagnostic] = []
    for entry in entries:
        for diagnostic in entry.diagnostics:
            relative = entry.path.relative_to(root).as_posix()
            localized = Diagnostic(diagnostic.severity, diagnostic.code, diagnostic.message, relative, root.name)
            diagnostics.append(localized)
            warnings.append(diagnostic.message)
    facts, metadata_paths = _metadata_for_root(root, warnings, diagnostics, entries)
    image_entries = [entry for entry in entries if entry.snapshot.kind == "image"]
    for entry in entries:
        relative = entry.path.relative_to(root).as_posix()
        if entry.snapshot.kind not in {"image", "json", "invalid_image"}:
            ignored.append(f"{root.name}/{relative}")

    basename_counts: dict[str, int] = {}
    for entry in image_entries:
        basename_counts[entry.path.name.casefold()] = basename_counts.get(entry.path.name.casefold(), 0) + 1
    ambiguous_reported: set[str] = set()

    resolutions: dict[str, ResolutionVariant] = {}
    preview_files: list[tuple[Path, tuple[int | None, int | None], str]] = []
    for entry in sorted(image_entries, key=lambda item: str(item.path).casefold()):
        path = entry.path
        relative = path.relative_to(root).as_posix()
        declaration = facts.maps_by_path.get(relative.casefold()) if facts else None
        if not declaration and facts and basename_counts[path.name.casefold()] == 1:
            declaration = facts.maps_by_basename.get(path.name.casefold())
        elif not declaration and facts and path.name.casefold() in facts.maps_by_basename and path.name.casefold() not in ambiguous_reported:
            message = f"Ambiguous metadata basename {path.name}; using filename inference for duplicate local files."
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "ambiguous_metadata_basename", message, relative, root.name))
            ambiguous_reported.add(path.name.casefold())
        channel, convention, packed = _map_identity(path.name, declaration)
        dimensions = (entry.snapshot.width, entry.snapshot.height)
        if not channel:
            metadata_role = facts.preview_roles_by_path.get(relative.casefold(), "") if facts else ""
            if not metadata_role and facts and basename_counts[path.name.casefold()] == 1:
                metadata_role = facts.preview_roles_by_basename.get(path.name.casefold(), "")
            preview_files.append((path, dimensions, metadata_role))
            continue
        label = _map_resolution(path, root, dimensions, declaration)
        variant = resolutions.setdefault(label, ResolutionVariant(label=label))
        if dimensions[0] and dimensions[1]:
            variant.width = max(variant.width or 0, dimensions[0])
            variant.height = max(variant.height or 0, dimensions[1])
        extension = path.suffix.casefold().lstrip(".")
        texture_map = TextureMap(
            channel=channel,
            relative_path=path.relative_to(root).as_posix(),
            file_format=extension.upper(),
            bit_depth=declaration.bit_depth if declaration and declaration.bit_depth else _inferred_bit_depth(extension),
            color_space=declaration.color_space if declaration and declaration.color_space else ("sRGB" if channel == "Base Color" else "Linear"),
            normal_convention=declaration.normal_convention if declaration and declaration.normal_convention else convention,
            packed_channels=declaration.packed_channels if declaration else packed,
            metadata_source="json" if declaration else "filename",
        )
        variant.maps.setdefault(channel, []).append(texture_map)

    for variant in resolutions.values():
        for alternatives in variant.maps.values():
            preferred = max(alternatives, key=_map_preference)
            preferred.preferred = True

    name = facts.name if facts and facts.name else _display_name(root.name)
    category = _select_category(
        facts.categories if facts else [],
        root.name,
        default_category,
        category_catalog,
    )
    candidate = MaterialCandidate(
        source_root=root,
        provider=facts.provider if facts else "Unknown",
        provider_id=facts.provider_id if facts else "",
        name=name,
        category=category,
        tags=_single_category_tags(
            facts.tags if facts else [],
            facts.categories if facts else [],
            category,
        ),
        author=facts.author if facts else "",
        description=facts.description if facts else "",
        physical_size=facts.physical_size if facts else "",
        resolutions=resolutions,
        metadata_paths=metadata_paths,
        warnings=warnings,
        diagnostics=diagnostics,
        asset_type="texture_set",
        source_snapshots={
            entry.path.relative_to(root).as_posix(): SourceFileSnapshot(
                entry.path.relative_to(root).as_posix(),
                entry.snapshot.size,
                entry.snapshot.mtime_ns,
                entry.snapshot.kind,
                entry.snapshot.width,
                entry.snapshot.height,
            )
            for entry in entries
        },
    )
    if facts:
        missing = sorted(facts.declared_resolutions - set(resolutions), key=_resolution_sort_key)
        if missing:
            candidate.warnings.append(f"Metadata declares unavailable resolutions: {', '.join(missing)}.")
    _assign_previews(candidate, preview_files)
    assigned_paths = {
        texture_map.relative_path
        for variant in candidate.resolutions.values()
        for alternatives in variant.maps.values()
        for texture_map in alternatives
    }
    assigned_paths.update(candidate.metadata_paths)
    assigned_paths.update(path for path in (candidate.selected_thumbnail, candidate.selected_hero) if path)
    candidate.extra_paths = sorted(
        (
            relative for relative, snapshot in candidate.source_snapshots.items()
            if relative not in assigned_paths and snapshot.kind != "invalid_image"
        ),
        key=str.casefold,
    )
    # Unrecognized regular files are retained as companions. Invalid images remain ignored.
    ignored = [
        f"{root.name}/{relative}" for relative, snapshot in candidate.source_snapshots.items()
        if snapshot.kind == "invalid_image"
    ]
    diagnosed_messages = {item.message for item in candidate.diagnostics}
    for warning in candidate.warnings:
        if warning not in diagnosed_messages:
            candidate.diagnostics.append(Diagnostic("warning", "review_warning", warning, material=root.name))
    return candidate, ignored


def _resolution_directory(value: str) -> bool:
    return bool(re.fullmatch(r"(?i)[1-9]\d*k", value.strip()))


def _metadata_for_root(
    root: Path,
    warnings: list[str],
    diagnostics: list[Diagnostic],
    entries: list[_InventoryEntry],
) -> tuple[MetadataFacts | None, list[str]]:
    parsed: list[tuple[int, MetadataFacts, str]] = []
    metadata_paths: list[str] = []
    json_paths = [entry.path for entry in entries if entry.path.parent == root and entry.snapshot.kind == "json"]
    for path in sorted(json_paths, key=lambda item: item.name.casefold()):
        metadata_paths.append(path.name)
        snapshot = next(entry.snapshot for entry in entries if entry.path == path)
        if snapshot.size > JSON_LIMIT:
            message = f"Skipped oversized JSON: {path.name}."
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "json_oversized", message, path.name, root.name))
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            message = f"Could not parse {path.name}: {error}."
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "json_parse_failed", message, path.name, root.name))
            continue
        if not isinstance(document, dict):
            message = f"Unsupported JSON root in {path.name}; using filename inference."
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "json_root_unsupported", message, path.name, root.name))
            continue
        matches = [(adapter.confidence(document, path), adapter) for adapter in ADAPTERS]
        confidence, adapter = max(matches, key=lambda item: item[0])
        if confidence:
            try:
                facts = adapter.parse(document, path)
            except Exception as error:
                message = f"Metadata adapter failed for {path.name}: {error}; using filename inference."
                warnings.append(message)
                diagnostics.append(Diagnostic("warning", "metadata_adapter_failed", message, path.name, root.name))
            else:
                parsed.append((confidence, facts, path.name))
        else:
            message = f"Unrecognized JSON schema in {path.name}; using filename inference."
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "json_schema_unknown", message, path.name, root.name))
    if not parsed:
        return None, metadata_paths
    parsed.sort(key=lambda item: item[0], reverse=True)
    if len(parsed) > 1:
        message = f"Multiple recognized metadata files found; using {parsed[0][2]}."
        warnings.append(message)
        diagnostics.append(Diagnostic("warning", "multiple_metadata_files", message, parsed[0][2], root.name))
    return parsed[0][1], metadata_paths


def _filename_channel(filename: str) -> tuple[str, str, dict[str, str]]:
    stem = Path(filename).stem.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    for channel, aliases in TOKEN_PATTERNS:
        for alias in aliases:
            pattern = rf"(?:^|_){re.escape(alias)}(?:_|$)"
            if re.search(pattern, normalized):
                convention = ""
                if channel == "Normal":
                    if re.search(r"(?:^|_)(?:ogl|gl)(?:_|$)", normalized):
                        convention = "OpenGL"
                    elif re.search(r"(?:^|_)dx(?:_|$)", normalized):
                        convention = "DirectX"
                packed = {"R": "Ambient Occlusion", "G": "Roughness", "B": "Metalness"} if channel == "Packed ARM" else {}
                return channel, convention, packed
    return "", "", {}


def _map_identity(filename: str, declaration: MapDeclaration | None) -> tuple[str, str, dict[str, str]]:
    if declaration:
        return declaration.channel, declaration.normal_convention, declaration.packed_channels
    return _filename_channel(filename)


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    if size.isValid():
        return size.width(), size.height()
    if path.suffix.casefold() == ".hdr":
        try:
            with path.open("rb") as handle:
                header = handle.read(64 * 1024).decode("latin-1", errors="ignore")
        except OSError:
            return None, None
        match = re.search(r"(?m)^[+-]Y\s+(\d+)\s+[+-]X\s+(\d+)\s*$", header)
        if match:
            return int(match.group(2)), int(match.group(1))
    if path.suffix.casefold() == ".exr":
        return _exr_dimensions(path)
    return None, None


def _exr_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read the OpenEXR dataWindow without requiring OpenImageIO or OpenEXR bindings."""
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"\x76\x2f\x31\x01":
                return None, None
            handle.read(4)  # version and flags
            while handle.tell() < 1024 * 1024:
                name = _read_exr_string(handle)
                if not name:
                    break
                attribute_type = _read_exr_string(handle)
                raw_size = handle.read(4)
                if len(raw_size) != 4:
                    return None, None
                size = struct.unpack("<I", raw_size)[0]
                if size > 64 * 1024 * 1024:
                    return None, None
                value = handle.read(size)
                if len(value) != size:
                    return None, None
                if name == b"dataWindow" and attribute_type == b"box2i" and size >= 16:
                    minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<4i", value[:16])
                    width = maximum_x - minimum_x + 1
                    height = maximum_y - minimum_y + 1
                    return (width, height) if width > 0 and height > 0 else (None, None)
    except (OSError, struct.error):
        return None, None
    return None, None


def _read_exr_string(handle) -> bytes:
    value = bytearray()
    while len(value) <= 255:
        character = handle.read(1)
        if not character or character == b"\0":
            return bytes(value)
        value.extend(character)
    return b""


def _map_resolution(
    path: Path,
    root: Path,
    dimensions: tuple[int | None, int | None],
    declaration: MapDeclaration | None,
) -> str:
    if dimensions[0]:
        label = resolution_label(dimensions[0])
        if label:
            return label
    if declaration and declaration.resolution:
        return declaration.resolution
    search_parts = [path.stem, *reversed(path.relative_to(root).parts[:-1])]
    for value in search_parts:
        match = re.search(r"(?i)(?:^|[^a-z0-9])([1-9]\d*)k(?:[^a-z0-9]|$)", value)
        if match:
            return f"{match.group(1)}K"
    return "Unknown"


def _inferred_bit_depth(extension: str) -> int:
    if extension == "exr":
        return 32
    if extension in {"tif", "tiff"}:
        return 16
    return 8


def _map_preference(texture_map: TextureMap) -> tuple[int, int, str]:
    return (
        texture_map.bit_depth or 0,
        FORMAT_PRIORITY.get(texture_map.file_format.casefold(), 0),
        texture_map.relative_path.casefold(),
    )


def _assign_previews(candidate: MaterialCandidate, files: list[tuple[Path, tuple[int | None, int | None], str]]) -> None:
    previews: list[PreviewCandidate] = []
    for path, (width, height), metadata_role in files:
        lowered = path.name.casefold()
        aspect = (width / height) if width and height else 1.0
        if metadata_role:
            role = metadata_role
        elif any(token in lowered for token in ("popup", "render", "hero")) or aspect >= 1.35:
            role = "hero"
        elif any(token in lowered for token in ("thumbnail", "thumb", "preview")) or 0.8 <= aspect <= 1.2:
            role = "thumbnail"
        else:
            role = "candidate"
        previews.append(
            PreviewCandidate(
                relative_path=path.relative_to(candidate.source_root).as_posix(),
                width=width,
                height=height,
                inferred_role=role,
                metadata_role=metadata_role,
            )
        )
    if not previews:
        fallback = _base_color_fallback(candidate)
        if fallback:
            path = candidate.source_root / fallback
            width, height = _image_dimensions(path)
            previews.append(PreviewCandidate(fallback, width, height, "fallback", fallback=True))
            candidate.warnings.append("No preview render found; using Base Color as the preview fallback.")
    if not previews:
        candidate.warnings.append("No usable preview or Base Color map was found.")
        return

    thumbnail = max(previews, key=lambda item: _thumbnail_score(item))
    hero = max(previews, key=lambda item: _hero_score(item))
    candidate.selected_thumbnail = thumbnail.relative_path
    candidate.selected_hero = hero.relative_path
    for preview in previews:
        roles: list[str] = []
        if preview.relative_path == candidate.selected_thumbnail:
            roles.append("thumbnail")
        if preview.relative_path == candidate.selected_hero:
            roles.append("hero")
        preview.selected_roles = tuple(roles)
    candidate.previews = previews


def _thumbnail_score(item: PreviewCandidate) -> tuple[int, int, str]:
    aspect = (item.width / item.height) if item.width and item.height else 1.0
    square_bonus = 20 if 0.8 <= aspect <= 1.2 else 0
    role_score = {"thumbnail": 100, "candidate": 50, "hero": 20, "fallback": 10}.get(item.inferred_role, 0)
    return role_score + square_bonus, item.width or 0, item.relative_path.casefold()


def _hero_score(item: PreviewCandidate) -> tuple[int, int, str]:
    aspect = (item.width / item.height) if item.width and item.height else 1.0
    wide_bonus = 20 if aspect >= 1.35 else 0
    role_score = {"hero": 100, "candidate": 50, "thumbnail": 30, "fallback": 10}.get(item.inferred_role, 0)
    return role_score + wide_bonus, item.width or 0, item.relative_path.casefold()


def _base_color_fallback(candidate: MaterialCandidate) -> str:
    for label in reversed(candidate.resolution_labels):
        maps = candidate.resolutions[label].maps.get("Base Color", [])
        preferred = next((item for item in maps if item.preferred), maps[0] if maps else None)
        if preferred:
            return preferred.relative_path
    return ""


def _select_category(
    categories: list[str],
    folder_name: str,
    default: str,
    category_catalog: CategoryCatalog | None = None,
) -> str:
    catalog = category_catalog or default_category_catalog("texture_set")
    for value in categories:
        lowered = value.strip().casefold()
        if lowered in GENERIC_CATEGORIES:
            continue
        canonical = catalog.canonical_name(value)
        if canonical:
            return canonical
        return value.strip().title()
    folder_match = catalog.match_text(folder_name)
    if folder_match:
        return folder_match
    return catalog.canonical_name(default) or "Uncategorized"


def _select_atlas_category(categories: list[str], folder_name: str, default: str) -> str:
    aliases = {
        "plant": "Plants", "plants": "Plants", "perennial": "Plants", "perennials": "Plants",
        "grass": "Grass", "tree": "Trees", "trees": "Trees", "needle": "Leaves",
        "leaf": "Leaves", "leaves": "Leaves", "flower": "Flowers", "flowers": "Flowers",
        "moss": "Moss", "debris": "Debris", "branch": "Branches", "branches": "Branches",
        "ground cover": "Ground Cover", "decal": "Decals", "decals": "Decals",
        "nature": "Miscellaneous",
    }
    for value in categories:
        normalized = value.strip().casefold()
        if normalized == "atlas":
            continue
        if normalized in aliases:
            return aliases[normalized]
    lowered = folder_name.casefold()
    for token, category in aliases.items():
        if token in lowered:
            return category
    return default if default in ATLAS_CATEGORIES else "Uncategorized"


def _single_category_tags(existing, provider_categories, category: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = {category.casefold(), "surface"}
    for value in (*existing, *provider_categories):
        text = re.sub(r"\s+", " ", str(value).strip())
        folded = text.casefold()
        if not text or folded in seen:
            continue
        values.append(text)
        seen.add(folded)
    return values


def _display_name(folder_name: str) -> str:
    cleaned = re.sub(r"(?i)^[1-9]\d*k[_\- ]+", "", folder_name)
    return re.sub(r"[_\-]+", " ", cleaned).strip().title()


def _resolution_sort_key(label: str) -> tuple[int, str]:
    digits = "".join(char for char in label if char.isdigit())
    return int(digits) if digits else 999, label
