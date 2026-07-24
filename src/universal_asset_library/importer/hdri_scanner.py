from __future__ import annotations

import json
from pathlib import Path
import re
from time import monotonic
from typing import Callable
from urllib.parse import urlparse

from .adapters import resolution_label
from .models import (
    Diagnostic,
    HdriCandidate,
    HdriFile,
    HdriVariant,
    PreviewCandidate,
    ScanCancellationToken,
    ScanCancelled,
    ScanProgress,
    ScanResult,
    SourceFileSnapshot,
)
from .scanner import (
    JSON_LIMIT,
    _build_inventory,
    _emit_scan_progress,
    _image_dimensions,
    _is_within,
    _single_category_tags,
)


ENVIRONMENT_EXTENSIONS = {".hdr", ".exr"}


def scan_hdri_folder(
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
        result.warnings.append("The selected HDRI source is not an existing folder.")
        return result
    try:
        inventory, diagnostics = _build_inventory(root, token, progress, started)
        result.inventory = tuple(entry.snapshot for entry in inventory)
        result.diagnostics.extend(diagnostics)
        roots = _discover_hdri_roots(root, inventory)
        if roots == [root] and not any(entry.snapshot.kind == "json" for entry in inventory):
            environment_entries = [
                entry for entry in inventory
                if entry.path.parent == root and entry.path.suffix.casefold() in ENVIRONMENT_EXTENSIONS
            ]
            groups: dict[str, list] = {}
            for entry in environment_entries:
                groups.setdefault(loose_hdri_key(entry.path), []).append(entry)
            if groups:
                for index, (key, group_entries) in enumerate(sorted(groups.items(), key=lambda item: item[0].casefold())):
                    token.check()
                    entries = inventory if len(groups) == 1 else group_entries
                    candidate = _scan_hdri(root, default_category, entries)
                    candidate.name = _display_name(key)
                    candidate.provider_id = ""
                    if candidate.resolutions:
                        result.materials.append(candidate)
                        result.diagnostics.extend(candidate.diagnostics)
                    _emit_scan_progress(
                        progress, "Grouping loose HDRIs", len(inventory), len(inventory), index + 1,
                        result.diagnostics, started,
                    )
                roots = []
        for index, material_root in enumerate(roots):
            token.check()
            entries = [entry for entry in inventory if _is_within(entry.path, material_root)]
            candidate = _scan_hdri(material_root, default_category, entries)
            if candidate.resolutions:
                result.materials.append(candidate)
                result.diagnostics.extend(candidate.diagnostics)
            _emit_scan_progress(
                progress, "Parsing HDRIs", len(inventory), len(inventory), index + 1,
                result.diagnostics, started,
            )
    except ScanCancelled:
        result.canceled = True
        result.materials.clear()
        return result
    result.materials.sort(key=lambda item: item.name.casefold())
    if not result.materials:
        result.warnings.append("No local HDR or EXR environment maps were found.")
    return result


def _discover_hdri_roots(scan_root: Path, inventory) -> list[Path]:
    json_directories = {entry.path.parent for entry in inventory if entry.snapshot.kind == "json"}
    candidates: set[Path] = set()
    for entry in inventory:
        if entry.path.suffix.casefold() not in ENVIRONMENT_EXTENSIONS:
            continue
        candidate = entry.path.parent
        parent = candidate
        while _is_within(parent, scan_root):
            if parent in json_directories:
                candidate = parent
                break
            if parent == scan_root:
                break
            parent = parent.parent
        candidates.add(candidate)
    ordered = sorted(candidates, key=lambda path: (len(path.parts), str(path).casefold()))
    return [path for path in ordered if not any(_is_within(path, prior) for prior in ordered if prior != path and len(prior.parts) < len(path.parts))]


def _scan_hdri(root: Path, default_category: str, entries) -> HdriCandidate:
    warnings: list[str] = []
    diagnostics: list[Diagnostic] = []
    document, metadata_paths = _hdri_metadata(root, entries, warnings, diagnostics)
    declared, declarations = _declared_hdri_files(document)
    variants: dict[str, HdriVariant] = {}
    primary_paths: set[str] = set()
    for entry in sorted(entries, key=lambda item: str(item.path).casefold()):
        if entry.path.suffix.casefold() not in ENVIRONMENT_EXTENSIONS:
            continue
        if entry.path.suffix.casefold() == ".hdr" and not entry.snapshot.width:
            relative = entry.path.relative_to(root).as_posix()
            diagnostics.append(Diagnostic(
                "error", "invalid_hdr_header", "Ignored a malformed Radiance HDR file.", relative, root.name
            ))
            continue
        relative = entry.path.relative_to(root).as_posix()
        label = _environment_resolution(entry, root, declarations.get(entry.path.name.casefold(), ""))
        variant = variants.setdefault(label, HdriVariant(label, entry.snapshot.width, entry.snapshot.height))
        if entry.snapshot.width and entry.snapshot.height:
            variant.width = max(variant.width or 0, entry.snapshot.width)
            variant.height = max(variant.height or 0, entry.snapshot.height)
        variant.files.append(HdriFile(relative, entry.path.suffix.lstrip(".").upper()))
        primary_paths.add(relative)
    for variant in variants.values():
        if variant.files:
            preferred = max(variant.files, key=lambda item: (item.file_format == "EXR", item.relative_path.casefold()))
            preferred.preferred = True

    categories = _strings(document.get("categories")) if document else []
    category = categories[0].strip().title() if categories else default_category
    name = str(document.get("name") or "").strip() if document else ""
    authors = document.get("authors") if document else {}
    author = ", ".join(str(value) for value in authors) if isinstance(authors, dict) else ""
    candidate = HdriCandidate(
        source_root=root,
        provider="Poly Haven" if document and isinstance(document.get("files", {}).get("hdri"), dict) else "Unknown",
        provider_id=root.name,
        name=name or _display_name(root.name),
        category=category or "Uncategorized",
        tags=_single_category_tags(
            _strings(document.get("tags")) if document else [],
            categories,
            category or "Uncategorized",
        ),
        author=author,
        description=str(document.get("description") or "") if document else "",
        resolutions=variants,
        metadata_paths=metadata_paths,
        warnings=warnings,
        diagnostics=diagnostics,
        source_snapshots={
            entry.path.relative_to(root).as_posix(): SourceFileSnapshot(
                entry.path.relative_to(root).as_posix(), entry.snapshot.size, entry.snapshot.mtime_ns,
                entry.snapshot.kind, entry.snapshot.width, entry.snapshot.height,
            )
            for entry in entries
        },
    )
    local_labels = set(variants)
    missing = sorted(declared - local_labels, key=_resolution_key)
    if missing:
        candidate.warnings.append(f"Provider metadata lists non-local resolutions: {', '.join(missing)}.")
    _assign_hdri_preview(candidate, entries)
    assigned = primary_paths | set(metadata_paths)
    candidate.extra_paths = sorted(
        (
            path for path, snapshot in candidate.source_snapshots.items()
            if path not in assigned and snapshot.kind != "invalid_image"
        ), key=str.casefold
    )
    for entry in entries:
        for item in entry.diagnostics:
            relative = entry.path.relative_to(root).as_posix()
            candidate.diagnostics.append(Diagnostic(item.severity, item.code, item.message, relative, root.name))
    known = {item.message for item in candidate.diagnostics}
    for warning in candidate.warnings:
        if warning not in known:
            candidate.diagnostics.append(Diagnostic("warning", "hdri_review_warning", warning, material=root.name))
    return candidate


def _hdri_metadata(root: Path, entries, warnings: list[str], diagnostics: list[Diagnostic]):
    metadata_paths: list[str] = []
    recognized: list[tuple[dict, str]] = []
    for entry in sorted((item for item in entries if item.snapshot.kind == "json"), key=lambda item: str(item.path).casefold()):
        relative = entry.path.relative_to(root).as_posix()
        metadata_paths.append(relative)
        if entry.snapshot.size > JSON_LIMIT:
            warnings.append(f"Skipped oversized JSON: {relative}.")
            continue
        try:
            value = json.loads(entry.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            diagnostics.append(Diagnostic("warning", "json_parse_failed", str(error), relative, root.name))
            continue
        if isinstance(value, dict) and isinstance(value.get("files"), dict) and isinstance(value["files"].get("hdri"), dict):
            recognized.append((value, relative))
        else:
            diagnostics.append(Diagnostic("warning", "json_schema_unknown", "JSON is retained but is not recognized as HDRI metadata.", relative, root.name))
    if len(recognized) > 1:
        warnings.append(f"Multiple HDRI metadata files found; using {recognized[0][1]}.")
    return (recognized[0][0] if recognized else None), metadata_paths


def _declared_hdri_files(document: dict | None) -> tuple[set[str], dict[str, str]]:
    labels: set[str] = set()
    files: dict[str, str] = {}
    if not document:
        return labels, files
    for resolution, formats in document.get("files", {}).get("hdri", {}).items():
        label = resolution_label(resolution)
        if label:
            labels.add(label)
        if not isinstance(formats, dict):
            continue
        for record in formats.values():
            if not isinstance(record, dict):
                continue
            parsed = urlparse(str(record.get("url", "")))
            basename = Path(parsed.path).name.casefold()
            if basename:
                files[basename] = label
    return labels, files


def _environment_resolution(entry, root: Path, declared: str) -> str:
    if entry.snapshot.width:
        label = hdri_resolution_label(entry.snapshot.width)
        if label:
            return label
    if declared:
        return declared
    for part in (entry.path.stem, *reversed(entry.path.relative_to(root).parts[:-1])):
        match = re.search(r"(?i)(?:^|[^a-z0-9])(1|2|4|8|16)k(?:[^a-z0-9]|$)", part)
        if match:
            return f"{match.group(1)}K"
    return "Unknown"


def hdri_resolution_label(width: int | None) -> str:
    """Use practical HDRI width bands (vendors commonly use 8000 rather than 8192)."""
    if not width:
        return "Unknown"
    if width < 1536:
        return "1K"
    if width < 3072:
        return "2K"
    if width < 6144:
        return "4K"
    if width < 12288:
        return "8K"
    return "16K"


def loose_hdri_key(path: Path) -> str:
    """Collapse size suffixes so `name.exr` and `name_sm.exr` are one HDRI asset."""
    value = path.stem
    value = re.sub(r"(?i)(?:[\s_.-]+)(?:sm|small)$", "", value)
    value = re.sub(r"(?i)(?:[\s_.-]+)(?:1|2|4|8|16)k$", "", value)
    return value.strip(" _.-") or path.stem


def _assign_hdri_preview(candidate: HdriCandidate, entries) -> None:
    options: list[PreviewCandidate] = []
    for entry in entries:
        suffix = entry.path.suffix.casefold()
        if entry.snapshot.kind != "image" or suffix in ENVIRONMENT_EXTENSIONS:
            continue
        relative = entry.path.relative_to(candidate.source_root).as_posix()
        options.append(PreviewCandidate(relative, entry.snapshot.width, entry.snapshot.height, "thumbnail"))
    if options:
        selected = max(options, key=lambda item: (
            any(token in item.relative_path.casefold() for token in ("thumbnail", "thumb", "preview", "render")),
            (item.width or 0) * (item.height or 0),
        ))
        selected.selected_roles = ("thumbnail", "hero")
        candidate.previews = options
        candidate.selected_thumbnail = selected.relative_path
        candidate.selected_hero = selected.relative_path
    else:
        candidate.warnings.append("No readable preview found; a JPEG placeholder will be generated during import.")


def _strings(value) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _display_name(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title()


def _resolution_key(label: str) -> tuple[int, str]:
    digits = "".join(character for character in label if character.isdigit())
    return int(digits) if digits else 999, label
