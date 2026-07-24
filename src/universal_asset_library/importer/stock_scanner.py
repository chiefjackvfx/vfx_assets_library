from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from time import monotonic
from typing import Callable
import unicodedata

from .models import (
    Diagnostic,
    ScanCancellationToken,
    ScanCancelled,
    ScanProgress,
    ScanResult,
    SourceFileSnapshot,
    StockCandidate,
    StockMediaInfo,
    StockPreviewCandidate,
)
from .stock_taxonomy import (
    StockTaxonomy,
    classify_stock_path,
    default_stock_taxonomy,
    normalize_taxonomy_text,
)


VIDEO_EXTENSIONS = {".mov", ".mp4"}
PREVIEW_DIRECTORIES = {"previews", "video_thumbnails", "thumbnails", "proxies"}
DOCUMENT_EXTENSIONS = {".json", ".txt", ".md", ".pdf", ".csv", ".xml"}
COMPATIBLE_PREVIEW_PIXEL_FORMATS = {"yuv420p", "yuvj420p"}


class StockProbeError(RuntimeError):
    pass


def scan_stock_folder(
    source: str | Path,
    default_category: str = "Uncategorized",
    progress: Callable[[ScanProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
    ffprobe_path: str = "",
    taxonomy: StockTaxonomy | None = None,
) -> ScanResult:
    selected_root = Path(source).expanduser().absolute()
    token = cancel_token or ScanCancellationToken()
    result = ScanResult(detected_asset_type="stock")
    started = monotonic()
    taxonomy = taxonomy or default_stock_taxonomy()
    if not selected_root.is_dir():
        result.warnings.append("The selected Stock source is not an existing folder.")
        return result
    root, inventory_roots = _stock_inventory_scope(selected_root)
    probe = resolve_ffprobe(ffprobe_path)
    if not probe:
        result.warnings.append("FFprobe was not found. Configure FFmpeg in Settings before scanning Stock footage.")
        result.diagnostics.append(Diagnostic(
            "error", "ffprobe_missing",
            "FFprobe was not found. Configure FFmpeg in Settings before scanning Stock footage.",
            str(root),
        ))
        return result

    inventory: list[tuple[Path, SourceFileSnapshot]] = []
    try:
        for inventory_root in inventory_roots:
            for current, directories, files in os.walk(inventory_root, followlinks=False):
                token.check()
                current_path = Path(current)
                retained: list[str] = []
                for name in directories:
                    path = current_path / name
                    if name.startswith("."):
                        continue
                    if path.is_symlink():
                        result.diagnostics.append(Diagnostic(
                            "warning", "symlink_ignored", "Ignored symbolic-link directory.",
                            path.relative_to(root).as_posix(),
                        ))
                        continue
                    retained.append(name)
                directories[:] = retained
                for name in files:
                    token.check()
                    path = current_path / name
                    if name.startswith(".") or path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                    except OSError as error:
                        result.diagnostics.append(Diagnostic(
                            "warning", "file_stat_failed", str(error), path.relative_to(root).as_posix(),
                        ))
                        continue
                    relative = path.relative_to(root).as_posix()
                    suffix = path.suffix.casefold()
                    kind = "video" if suffix in VIDEO_EXTENSIONS else "document" if suffix in DOCUMENT_EXTENSIONS else "other"
                    inventory.append((path, SourceFileSnapshot(relative, stat.st_size, stat.st_mtime_ns, kind)))
                    if progress and len(inventory) % 256 == 0:
                        progress(ScanProgress(
                            "Inventorying Stock files", len(inventory), 0, 0,
                            sum(item.severity == "warning" for item in result.diagnostics),
                            monotonic() - started,
                        ))
        result.inventory = tuple(snapshot for _path, snapshot in inventory)
    except ScanCancelled:
        result.canceled = True
        return result

    media_paths = [(path, snapshot) for path, snapshot in inventory if snapshot.kind == "video"]
    preview_paths = [(path, snapshot) for path, snapshot in media_paths if _is_preview_path(path, root)]
    source_paths = [(path, snapshot) for path, snapshot in media_paths if not _is_preview_path(path, root)]
    preview_relatives = {snapshot.relative_path for _path, snapshot in preview_paths}
    total = len(media_paths)
    probed: dict[str, StockMediaInfo] = {}
    valid_previews: list[tuple[Path, SourceFileSnapshot, StockMediaInfo]] = []

    try:
        examined = 0
        for path, snapshot in (*source_paths, *preview_paths):
            token.check()
            relative = snapshot.relative_path
            if snapshot.size <= 0:
                result.diagnostics.append(Diagnostic(
                    "error", "empty_video", "Ignored an empty video file.", relative,
                ))
            else:
                try:
                    info = probe_video(path, probe, token)
                except StockProbeError as error:
                    result.diagnostics.append(Diagnostic(
                        "warning", "video_probe_failed", str(error), relative,
                    ))
                else:
                    probed[relative] = info
                    if relative in preview_relatives:
                        valid_previews.append((path, snapshot, info))
            examined += 1
            if progress and (examined == 1 or examined % 8 == 0 or examined == total):
                progress(ScanProgress(
                    "Probing Stock media", examined, total, 0,
                    sum(item.severity == "warning" for item in result.diagnostics),
                    monotonic() - started,
                ))
    except ScanCancelled:
        result.canceled = True
        return result

    previews_by_key: dict[str, list[tuple[Path, SourceFileSnapshot, StockMediaInfo]]] = {}
    previews_by_identity: dict[tuple[str, int], list[tuple[Path, SourceFileSnapshot, StockMediaInfo]]] = {}
    for entry in valid_previews:
        previews_by_key.setdefault(_media_key(entry[0]), []).append(entry)
        identity = _media_identity(entry[0], root)
        if identity:
            previews_by_identity.setdefault(identity, []).append(entry)

    common_documents: dict[Path, list[str]] = {}
    for path, snapshot in inventory:
        if snapshot.kind == "document" and not _inside_preview_tree(path, root):
            common_documents.setdefault(path.parent, []).append(snapshot.relative_path)

    for path, snapshot in source_paths:
        token.check()
        media_info = probed.get(snapshot.relative_path)
        if media_info is None:
            result.ignored_files.append(snapshot.relative_path)
            continue
        key = _media_key(path)
        raw_matches = list(previews_by_key.get(key, ()))
        matches = [
            entry for entry in raw_matches if _media_agrees(media_info, entry[2])
        ]
        rejected_matches = len(raw_matches) - len(matches)
        match_reason = "normalized filename"
        if not matches:
            identity = _media_identity(path, root)
            raw_structured = list(previews_by_identity.get(identity, ())) if identity else []
            matches = [
                entry for entry in raw_structured if _media_agrees(media_info, entry[2])
            ]
            rejected_matches += len(raw_structured) - len(matches)
            if matches:
                match_reason = "effect folder and clip number"
        selected: tuple[Path, SourceFileSnapshot, StockMediaInfo] | None = None
        diagnostics: list[Diagnostic] = []
        if rejected_matches:
            diagnostics.append(Diagnostic(
                "warning", "preview_media_mismatch",
                "A filename or numbered preview match was rejected because its duration "
                "or frame count does not agree with the source.",
                snapshot.relative_path, path.stem,
            ))
        if len(matches) == 1:
            selected = matches[0]
        elif len(matches) > 1:
            ranked = sorted(matches, key=lambda item: _preview_match_score(path, media_info, item[0], item[2]), reverse=True)
            if len(ranked) == 1 or _preview_match_score(path, media_info, ranked[0][0], ranked[0][2]) > _preview_match_score(path, media_info, ranked[1][0], ranked[1][2]):
                selected = ranked[0]
            else:
                diagnostics.append(Diagnostic(
                    "warning", "preview_match_ambiguous",
                    f"Multiple previews match {path.name}; choose one during review.",
                    snapshot.relative_path, path.stem,
                ))
        preview_candidates = [
            StockPreviewCandidate(
                item_snapshot.relative_path,
                item_info,
                preview_is_compatible(item_info),
                match_reason,
            )
            for item_path, item_snapshot, item_info in matches
        ]
        selected_relative = selected[1].relative_path if selected else ""
        if selected and match_reason != "normalized filename":
            diagnostics.append(Diagnostic(
                "info", "preview_match_structured",
                f"Matched preview by effect folder and clip number ({selected[0].name}).",
                selected_relative, path.stem,
            ))
        policy = "use_existing" if selected and preview_is_compatible(selected[2]) else "generate"
        if not selected:
            diagnostics.append(Diagnostic(
                "warning", "preview_generation_required",
                "No matching preview was found; a full-duration 480p H.264 preview will be generated.",
                snapshot.relative_path, path.stem,
            ))
        elif policy == "generate":
            diagnostics.append(Diagnostic(
                "warning", "preview_incompatible",
                "The matched preview is not broadly playable H.264 4:2:0; a normalized preview will be generated.",
                selected_relative, path.stem,
            ))
        if media_info.alpha == "unknown":
            diagnostics.append(Diagnostic(
                "warning", "alpha_unknown",
                "Alpha could not be determined and will default to opaque preview processing.",
                snapshot.relative_path, path.stem,
            ))

        metadata_paths = _matching_sidecars(path, inventory)
        extras = sorted(set(common_documents.get(path.parent, ())) - set(metadata_paths), key=str.casefold)
        classification = classify_stock_path(snapshot.relative_path, taxonomy)
        category = classification.category
        candidate = StockCandidate(
            source_root=root,
            provider="Unknown",
            name=infer_stock_display_name(path, selected_root, taxonomy),
            category=category,
            tags=list(classification.tags),
            source_video=snapshot.relative_path,
            media_info=media_info,
            preview_candidates=preview_candidates,
            selected_preview=selected_relative,
            preview_policy=policy,
            metadata_paths=metadata_paths,
            extra_paths=extras,
            classification_evidence=list(classification.evidence),
            diagnostics=diagnostics,
            warnings=[item.message for item in diagnostics if item.severity == "warning"],
            source_snapshots={
                value.relative_path: value
                for _inventory_path, value in inventory
                if value.relative_path in {
                    snapshot.relative_path, selected_relative, *metadata_paths, *extras,
                    *(item.relative_path for item in preview_candidates),
                }
            },
        )
        result.materials.append(candidate)

    result.materials.sort(key=lambda item: (item.category.casefold(), item.name.casefold()))
    result.ignored_files = sorted(set(result.ignored_files), key=str.casefold)
    result.diagnostics.extend(
        diagnostic for candidate in result.materials for diagnostic in candidate.diagnostics
    )
    result.detection_reason = f"{len(result.materials)} Stock clip{'s' if len(result.materials) != 1 else ''}"
    return result


def resolve_ffprobe(value: str = "") -> str:
    if value:
        candidate = Path(value).expanduser()
        if candidate.name.casefold().startswith("ffmpeg"):
            sibling = candidate.with_name("ffprobe.exe" if candidate.suffix.casefold() == ".exe" else "ffprobe")
            if sibling.is_file():
                return str(sibling)
        if candidate.name.casefold().startswith("ffprobe") and candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe") or ""


def probe_video(path: Path, ffprobe_path: str, token: ScanCancellationToken) -> StockMediaInfo:
    command = [
        ffprobe_path, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        raise StockProbeError(f"Could not start FFprobe: {error}") from error
    while True:
        if token.cancelled:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            raise ScanCancelled("Stock scan canceled.")
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode:
        raise StockProbeError(stderr.strip() or "FFprobe rejected this video.")
    try:
        document = json.loads(stdout)
        streams = document.get("streams", [])
        video = next(item for item in streams if item.get("codec_type") == "video")
        audio = any(item.get("codec_type") == "audio" for item in streams)
        format_info = document.get("format", {})
        duration = _number(video.get("duration") or format_info.get("duration"))
        frame_rate = _rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        frames = _integer(video.get("nb_frames"))
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
        raise StockProbeError("FFprobe returned incomplete video metadata.") from error
    if width <= 0 or height <= 0 or duration <= 0:
        raise StockProbeError("Video dimensions or duration are invalid.")
    pixel_format = str(video.get("pix_fmt") or "")
    codec = str(video.get("codec_name") or "")
    return StockMediaInfo(
        container=str(format_info.get("format_name") or path.suffix.lstrip(".")),
        codec=codec,
        profile=str(video.get("profile") or ""),
        pixel_format=pixel_format,
        width=width,
        height=height,
        frame_rate=frame_rate,
        duration=duration,
        frame_count=frames,
        has_audio=audio,
        alpha=_alpha_state(codec, pixel_format, video),
    )


def preview_is_compatible(info: StockMediaInfo) -> bool:
    return info.codec.casefold() == "h264" and info.pixel_format.casefold() in COMPATIBLE_PREVIEW_PIXEL_FORMATS


def _stock_inventory_scope(selected_root: Path) -> tuple[Path, tuple[Path, ...]]:
    """Include a sibling provider-preview tree without scanning unrelated siblings."""
    if _is_preview_directory_name(selected_root.name):
        return selected_root, (selected_root,)
    try:
        siblings = tuple(
            path for path in selected_root.parent.iterdir()
            if path != selected_root
            and path.is_dir()
            and not path.is_symlink()
            and _is_preview_directory_name(path.name)
        )
    except OSError:
        siblings = ()
    if not siblings:
        return selected_root, (selected_root,)
    return selected_root.parent, (selected_root, *sorted(siblings, key=lambda path: path.name.casefold()))


def _is_preview_path(path: Path, root: Path) -> bool:
    if _inside_preview_tree(path, root):
        return True
    return bool(re.search(r"(?i)(?:^|[\s_-])(?:preview|proxy|thumbnail)$", path.stem))


def _inside_preview_tree(path: Path, root: Path) -> bool:
    # Inspect the real ancestry rather than only the path below the scan root.
    # This keeps a directly selected Previews folder (or a category beneath
    # one) from turning its proxy videos into source candidates.
    return any(_is_preview_directory_name(parent.name) for parent in path.parents)


def _is_preview_directory_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if normalized in PREVIEW_DIRECTORIES:
        return True
    return bool(re.search(r"(?:^|_)(?:preview|previews|proxy|proxies|thumbnail|thumbnails)$", normalized))


def _media_key(path: Path) -> str:
    stem = re.sub(r"(?i)[\s_-]+(?:preview|proxy|thumbnail)$", "", path.stem)
    return re.sub(r"[^a-z0-9]+", "", stem.casefold())


def _media_identity(path: Path, root: Path) -> tuple[str, int] | None:
    """Return a provider-style (effect group, clip number) identity."""
    number_match = re.match(r"^\s*(\d+)(?:\s*[-_.]|\s+)", path.stem)
    if not number_match:
        return None
    clip_number = int(number_match.group(1))
    for part in reversed(path.relative_to(root).parts[:-1]):
        normalized = re.sub(r"^\s*\d+\s*[-_.]?\s*", "", part.casefold())
        normalized = re.sub(
            r"(?:\s*[-_.]?\s*)(?:preview|previews|layer|layers)$", "", normalized
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
        if normalized and normalized not in {"effect", "effects", "preview", "previews"}:
            return normalized, clip_number
    return None


def _media_agrees(source: StockMediaInfo, preview: StockMediaInfo) -> bool:
    frame_duration = 2.0 / max(source.frame_rate, preview.frame_rate, 1.0)
    if abs(source.duration - preview.duration) > max(0.15, frame_duration):
        return False
    if source.frame_count and preview.frame_count:
        return abs(source.frame_count - preview.frame_count) <= 2
    return True


def _preview_match_score(source: Path, source_info: StockMediaInfo, preview: Path, preview_info: StockMediaInfo) -> tuple[int, float, int]:
    common = len(os.path.commonprefix((source.parent.parts, preview.parent.parts)))
    duration_delta = abs(source_info.duration - preview_info.duration)
    frame_delta = abs((source_info.frame_count or 0) - (preview_info.frame_count or 0))
    return common, -duration_delta, -frame_delta


def _matching_sidecars(
    source: Path,
    inventory: list[tuple[Path, SourceFileSnapshot]],
) -> list[str]:
    key = _media_key(source)
    return sorted(
        (
            snapshot.relative_path for path, snapshot in inventory
            if snapshot.kind == "document" and path.parent == source.parent and _media_key(path) == key
        ),
        key=str.casefold,
    )


def _display_name(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_]+", " ", stem)).strip()


def infer_stock_display_name(
    path: str | Path,
    selected_root: str | Path,
    taxonomy: StockTaxonomy | None = None,
) -> str:
    """Return a contextual review name for a numeric-only Stock clip.

    The selected scan root is a collection boundary and is intentionally not
    included. Folder names below it are ordered from broadest to most specific.
    """
    source = Path(path)
    stem = source.stem.strip()
    if not re.fullmatch(r"[0-9]+", stem):
        return _display_name(source.stem)

    root = Path(selected_root).expanduser().absolute()
    try:
        relative = source.absolute().relative_to(root)
    except ValueError:
        return _display_name(source.stem)

    active_taxonomy = taxonomy or default_stock_taxonomy()
    category_names = {
        normalize_taxonomy_text(name) for name in active_taxonomy.category_names
    }
    folders: list[str] = []
    for part in relative.parts[:-1]:
        component = _stock_folder_name(part)
        if (
            component
            and normalize_taxonomy_text(component) not in category_names
        ):
            folders.append(component)
    number = stem if len(stem) >= 2 else stem.zfill(2)
    return " ".join((*folders, number)) if folders else number


def _stock_folder_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    # Remove provider ordering prefixes such as "08. " and "1 - " without
    # stripping meaningful numeric words such as "45 Caliber".
    normalized = re.sub(r"^\d+\s*(?:[._:-]\s*)+", "", normalized)
    normalized = re.sub(r"[_]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _alpha_state(codec: str, pixel_format: str, video: dict) -> str:
    lowered = pixel_format.casefold()
    if any(token in lowered for token in ("rgba", "bgra", "argb", "abgr", "yuva", "gbrap")):
        return "yes"
    tags = video.get("tags") if isinstance(video.get("tags"), dict) else {}
    alpha_mode = str(tags.get("alpha_mode") or video.get("alpha_mode") or "").casefold()
    if alpha_mode in {"1", "straight", "premultiplied"}:
        return "yes"
    if pixel_format:
        return "no"
    return "unknown"


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate(value) -> float:
    if not value:
        return 0.0
    try:
        numerator, denominator = str(value).split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        return _number(value)
