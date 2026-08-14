from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from time import monotonic
from typing import Callable

from .models import (
    Diagnostic,
    PreviewCandidate,
    ScanCancellationToken,
    ScanCancelled,
    ScanProgress,
    ScanResult,
    SourceFileSnapshot,
    VdbCandidate,
    VdbFile,
    VdbVariant,
)
from .scanner import _build_inventory, _emit_scan_progress, _image_dimensions


_QUALITY = re.compile(
    r"(?i)(?:^|[_ .-])(low|mid|high)(?:[_ .-]?res(?:olution)?)?(?=$|[_ .-])"
)
_TRAILING_FRAME = re.compile(r"^(.*?)(?:[_ .-])(\d+)$")
_PREVIEW_SUFFIX = re.compile(r"(?i)(?:[_ .-](?:preview|thumbnail|thumb|render|hero))+$")
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_SUFFIXES = {".mov", ".mp4"}


@dataclass(frozen=True, slots=True)
class _ParsedVdb:
    path: Path
    asset_key: str
    display_stem: str
    variant: str
    frame: int | None
    padding: int


def scan_vdb_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
) -> ScanResult:
    root = Path(source).expanduser().absolute()
    result = ScanResult(detected_asset_type="vdb")
    token = cancel_token or ScanCancellationToken()
    started = monotonic()
    if not root.is_dir():
        result.warnings.append("The selected VDB source is not an existing folder.")
        return result
    try:
        inventory, diagnostics = _build_inventory(
            root, token, progress, started, report_exclusions=True
        )
        result.inventory = tuple(entry.snapshot for entry in inventory)
        result.diagnostics.extend(diagnostics)
        parsed = [
            _parse_vdb(entry.path, root)
            for entry in inventory
            if entry.path.suffix.casefold() == ".vdb" and entry.snapshot.size > 0
        ]
        groups: dict[tuple[str, str], list[_ParsedVdb]] = {}
        for item in parsed:
            parent = item.path.parent.relative_to(root).as_posix().casefold()
            groups.setdefault((parent, item.asset_key.casefold()), []).append(item)
        preview_entries = [
            entry for entry in inventory
            if entry.path.suffix.casefold() in _IMAGE_SUFFIXES | _VIDEO_SUFFIXES
        ]
        for index, items in enumerate(groups.values(), 1):
            token.check()
            candidate = _candidate(root, items, preview_entries, default_category)
            result.materials.append(candidate)
            result.diagnostics.extend(candidate.diagnostics)
            _emit_scan_progress(
                progress, "Grouping VDB assets", len(inventory), len(inventory),
                index, result.diagnostics, started,
            )
    except ScanCancelled:
        result.canceled = True
        result.materials.clear()
        return result
    result.materials.sort(key=lambda item: item.name.casefold())
    if not result.materials:
        result.warnings.append("No local .vdb files were found.")
    result.detection_reason = f"found {len(parsed)} OpenVDB file(s)"
    return result


def _parse_vdb(path: Path, root: Path) -> _ParsedVdb:
    stem = path.stem
    match = _QUALITY.search(stem)
    variant = match.group(1).title() if match else "Default"
    frame = None
    padding = 0
    if match:
        before = stem[:match.start()].strip("_ .-")
        after = stem[match.end():].strip("_ .-")
        if after.isdigit():
            asset_key = before or stem
            frame = int(after)
            padding = len(after)
        else:
            # A quality suffix after a numeric token identifies a static
            # variation, as in cloud_formation_001_High_Res.vdb.
            asset_key = "_".join(value for value in (before, after) if value) or stem
    else:
        trailing = _TRAILING_FRAME.match(stem)
        if trailing:
            asset_key = trailing.group(1).strip("_ .-")
            digits = trailing.group(2)
            frame = int(digits)
            padding = len(digits)
        else:
            asset_key = stem
    parent = path.parent.relative_to(root).as_posix()
    identity = f"{parent}/{asset_key}" if parent != "." else asset_key
    return _ParsedVdb(path, identity, asset_key, variant, frame, padding)


def _candidate(root: Path, items, preview_entries, default_category: str) -> VdbCandidate:
    variants: dict[str, VdbVariant] = {}
    snapshots: dict[str, SourceFileSnapshot] = {}
    for item in sorted(items, key=lambda value: value.path.name.casefold()):
        relative = item.path.relative_to(root).as_posix()
        stat = item.path.stat()
        variants.setdefault(item.variant, VdbVariant(item.variant)).files.append(
            VdbFile(relative, item.frame, item.padding)
        )
        snapshots[relative] = SourceFileSnapshot(
            relative, stat.st_size, stat.st_mtime_ns, "vdb"
        )
    # A lone number-suffixed file is a static asset, not a one-frame sequence.
    if len(items) == 1 and items[0].frame is not None:
        only = next(iter(variants.values())).files[0]
        only.frame = None
        only.padding = 0
        display_stem = items[0].path.stem
    else:
        display_stem = items[0].display_stem
    for variant in variants.values():
        variant.files.sort(key=lambda item: (item.frame is None, item.frame or 0, item.relative_path.casefold()))

    name = _display_name(display_stem)
    normalized = _normalize_key(display_stem)
    previews: list[PreviewCandidate] = []
    selected_video = ""
    for entry in preview_entries:
        preview_key = _normalize_key(_PREVIEW_SUFFIX.sub("", entry.path.stem))
        if preview_key != normalized:
            continue
        relative = entry.path.relative_to(root).as_posix()
        snapshots[relative] = entry.snapshot
        if entry.path.suffix.casefold() in _VIDEO_SUFFIXES:
            selected_video = selected_video or relative
            continue
        width, height = entry.snapshot.width, entry.snapshot.height
        if not width or not height:
            width, height = _image_dimensions(entry.path)
        previews.append(PreviewCandidate(relative, width, height, "thumbnail"))
    selected_still = previews[0].relative_path if previews else ""
    if selected_still:
        previews[0].selected_roles = ("thumbnail", "hero")

    diagnostics: list[Diagnostic] = []
    warnings: list[str] = []
    for variant in variants.values():
        if variant.missing_frames:
            message = (
                f"{variant.label} is missing {len(variant.missing_frames)} frame(s): "
                + ", ".join(str(value) for value in variant.missing_frames[:12])
                + ("…" if len(variant.missing_frames) > 12 else "")
            )
            warnings.append(message)
            diagnostics.append(Diagnostic("warning", "vdb_sequence_gaps", message, material=name))
    if not previews and not selected_video:
        diagnostics.append(Diagnostic(
            "info", "vdb_preview_missing",
            "No supplied VDB preview found; the catalog will use a volume placeholder.",
            material=name,
        ))
    category = _category_for(name, default_category)
    return VdbCandidate(
        source_root=root,
        name=name,
        category=category,
        variants=variants,
        previews=previews,
        selected_thumbnail=selected_still,
        selected_hero=selected_still,
        selected_preview_video=selected_video,
        warnings=warnings,
        diagnostics=diagnostics,
        source_snapshots=snapshots,
    )


def _display_name(value: str) -> str:
    words = re.sub(r"[_ .-]+", " ", value).strip().split()
    return " ".join(word if word.isdigit() else word.title() for word in words) or "VDB Volume"


def _normalize_key(value: str) -> str:
    value = _QUALITY.sub("_", value)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _category_for(name: str, default: str) -> str:
    folded = name.casefold()
    rules = (
        ("Clouds", ("cloud",)), ("Smoke", ("smoke",)),
        ("Fire", ("fire", "flame")), ("Explosions", ("explosion", "blast")),
        ("Fog", ("fog", "mist")), ("Dust", ("dust",)),
        ("Water", ("water", "splash", "liquid")), ("Magic", ("magic",)),
    )
    return next((category for category, tokens in rules if any(token in folded for token in tokens)), default)
