from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Callable, Mapping

from PyQt6.QtGui import QImage

from universal_asset_library.integrations.texture_export import (
    SUPPORTED_CHANNELS as MATERIAL_CHANNELS,
)

from .hdri_renderer import resolve_blender_executable

if TYPE_CHECKING:
    from .blender_preview_session import BlenderPreviewSession


THUMBNAIL_SIZE = (512, 512)
HERO_SIZE = (1024, 512)
# Compatibility alias for callers that treated the original square render as
# the only texture-preview size.
PREVIEW_SIZE = THUMBNAIL_SIZE
SUPPORTED_CHANNELS = MATERIAL_CHANNELS


@dataclass(frozen=True, slots=True)
class TexturePreviewMap:
    channel: str
    path: Path
    source_relative: str
    sha256: str = ""
    color_space: str = ""
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TexturePreviewRequest:
    output_dir: Path
    asset_name: str
    resolution: str
    maps: tuple[TexturePreviewMap, ...]
    blender_path: str = ""
    template_path: Path | None = None
    timeout_seconds: int = 600
    save_blend_file: bool = False


@dataclass(slots=True)
class TexturePreviewResult:
    status: str
    thumbnail_path: Path | None = None
    hero_path: Path | None = None
    resolution: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    blender_version: str = ""
    template_sha256: str = ""
    diagnostic: str = ""
    log: str = ""
    metadata: dict = field(default_factory=dict)
    blend_path: Path | None = None


def default_template_path() -> Path:
    packaged = Path(__file__).resolve().parent / "templates" / "shader_preview.blend"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "templates" / "shader_preview.blend"


def driver_path() -> Path:
    return Path(__file__).resolve().with_name("blender_texture_driver.py")


def select_texture_variant(
    resolutions: Mapping[str, object],
) -> tuple[str, object] | None:
    ranked = sorted(
        (
            (_resolution_value(label), str(label), variant)
            for label, variant in resolutions.items()
        ),
        key=lambda item: (item[0], item[1].casefold()),
    )
    if not ranked:
        return None
    within = [item for item in ranked if 0 < item[0] <= 4]
    selected = within[-1] if within else next(
        (item for item in ranked if item[0] > 4),
        ranked[-1],
    )
    return selected[1], selected[2]


def select_texture_maps(variant: object) -> dict[str, object]:
    groups = (
        variant.get("maps", {})
        if isinstance(variant, dict)
        else getattr(variant, "maps", {})
    )
    explicit = {
        str(channel)
        for channel, alternatives in groups.items()
        if channel in SUPPORTED_CHANNELS and alternatives
    }
    selected: dict[str, object] = {}
    for channel in sorted(groups, key=lambda value: str(value).casefold()):
        alternatives = list(groups.get(channel, ()) or ())
        if not alternatives:
            continue
        alternatives.sort(
            key=lambda item: (
                not bool(_value(item, "preferred", False)),
                str(_value(item, "path", _value(item, "relative_path", ""))).casefold(),
            )
        )
        preferred = alternatives[0]
        packed = _value(preferred, "packed_channels", {}) or {}
        supplies_supported_channel = any(
            str(semantic) in SUPPORTED_CHANNELS
            and str(semantic) not in explicit
            for semantic in packed.values()
        )
        if channel in SUPPORTED_CHANNELS or supplies_supported_channel:
            selected[str(channel)] = preferred
    return selected


def render_texture_preview(
    request: TexturePreviewRequest,
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
    session: "BlenderPreviewSession | None" = None,
) -> TexturePreviewResult:
    sources = {item.channel: item.source_relative for item in request.maps}
    by_channel = {item.channel: item for item in request.maps}
    if "Base Color" not in by_channel:
        return _failure(
            "unsupported", request, "A Base Color map is required for a texture preview."
        )
    executable = resolve_blender_executable(request.blender_path)
    if not executable:
        return _failure(
            "unsupported",
            request,
            "Blender was not found. Configure its executable in Settings.",
        )
    template = request.template_path or default_template_path()
    if not template.is_file():
        return _failure(
            "unsupported",
            request,
            f"Texture preview template is missing: {template}",
            blender_version="",
        )
    for item in request.maps:
        if item.channel not in SUPPORTED_CHANNELS and not any(
            semantic in SUPPORTED_CHANNELS
            for semantic in item.packed_channels.values()
        ):
            return _failure(
                "failed",
                request,
                f"Unsupported texture preview channel: {item.channel}",
                blender_version="",
            )
        if not item.path.is_file():
            return _failure(
                "failed",
                request,
                f"Texture preview source is missing: {item.path}",
                blender_version="",
            )

    request.output_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_render_path = request.output_dir / "shader-thumbnail.png"
    hero_render_path = request.output_dir / "shader-hero.png"
    maps_path = request.output_dir / "texture-maps.json"
    maps_path.write_text(
        json.dumps(
            [
                {
                    "channel": item.channel,
                    "path": str(item.path),
                    "color_space": item.color_space,
                    "normal_convention": item.normal_convention,
                    "packed_channels": dict(item.packed_channels),
                }
                for item in request.maps
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    blend_path = (
        request.output_dir
        / f"{_filename_token(request.asset_name)}_Texture_Preview.blend"
        if request.save_blend_file
        else None
    )
    from .blender_preview_session import (
        BlenderPreviewSession,
        BlenderPreviewSessionError,
    )

    owned_session = session is None
    active_session = session or BlenderPreviewSession(executable)
    metadata: dict = {}
    try:
        if progress:
            progress("Starting Blender preview session")
        metadata = active_session.render(
            "texture",
            template,
            {
                "maps_json": str(maps_path.resolve()),
                "thumbnail_output": str(thumbnail_render_path.resolve()),
                "hero_output": str(hero_render_path.resolve()),
                "blend_output": str(blend_path.resolve()) if blend_path else "",
            },
            timeout_seconds=request.timeout_seconds,
            progress=progress,
            cancel_token=cancel_token,
        )
    except BlenderPreviewSessionError as error:
        return _failure(
            error.status,
            request,
            error.diagnostic,
            blender_version=active_session.blender_version,
            log=error.log,
        )
    finally:
        if owned_session:
            active_session.close()
    version = active_session.blender_version
    output = active_session.log
    if blend_path is not None and (
        not blend_path.is_file() or blend_path.stat().st_size <= 0
    ):
        return _failure(
            "failed",
            request,
            "Blender did not save the requested debug .blend file.",
            blender_version=version,
            log=output,
        )
    thumbnail_image = QImage(str(thumbnail_render_path))
    hero_image = QImage(str(hero_render_path))
    for camera_name, image, expected in (
        ("render_ball", thumbnail_image, THUMBNAIL_SIZE),
        ("render_plane", hero_image, HERO_SIZE),
    ):
        if (image.width(), image.height()) != expected:
            return _failure(
                "failed",
                request,
                f"{camera_name} must render at "
                f"{expected[0]}×{expected[1]}; received "
                f"{image.width()}×{image.height()}.",
                blender_version=version,
                log=output,
            )
    hero = (
        request.output_dir
        / f"{_filename_token(request.asset_name)}_Texture_Preview.jpg"
    )
    thumbnail = (
        request.output_dir
        / f"{_filename_token(request.asset_name)}_Texture_Thumbnail.jpg"
    )
    if not thumbnail_image.save(str(thumbnail), "JPG", 90):
        return _failure(
            "failed",
            request,
            "Could not save the texture thumbnail JPEG.",
            blender_version=version,
            log=output,
        )
    if not hero_image.save(str(hero), "JPG", 90):
        return _failure(
            "failed",
            request,
            "Could not save the texture preview JPEG.",
            blender_version=version,
            log=output,
        )
    template_hash = active_session.template_hash(template)
    render_metadata = {
        "type": "texture_shader",
        "status": "ready",
        "resolution": request.resolution,
        "sources": sources,
        "width": HERO_SIZE[0],
        "height": HERO_SIZE[1],
        "thumbnail_width": THUMBNAIL_SIZE[0],
        "thumbnail_height": THUMBNAIL_SIZE[1],
        "hero_width": HERO_SIZE[0],
        "hero_height": HERO_SIZE[1],
        "thumbnail_camera": metadata.get("thumbnail_camera", "render_ball"),
        "hero_camera": metadata.get("hero_camera", "render_plane"),
        "map_channels": metadata.get(
            "map_channels", list(sources)
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "blender_version": metadata.get("blender_version", version),
        "render_device": metadata.get("render_device", "GPU"),
        "compute_device_type": metadata.get("compute_device_type", ""),
        "gpu_devices": metadata.get("gpu_devices", []),
        "template_sha256": template_hash,
        "diagnostic": "",
    }
    if blend_path is not None:
        render_metadata["debug_blend"] = blend_path.name
    return TexturePreviewResult(
        status="ready",
        thumbnail_path=thumbnail,
        hero_path=hero,
        resolution=request.resolution,
        sources=sources,
        blender_version=str(render_metadata["blender_version"]),
        template_sha256=template_hash,
        metadata=render_metadata,
        log=output,
        blend_path=blend_path,
    )


def _failure(
    status: str,
    request: TexturePreviewRequest,
    diagnostic: str,
    *,
    blender_version: str = "",
    log: str = "",
) -> TexturePreviewResult:
    sources = {item.channel: item.source_relative for item in request.maps}
    metadata = {
        "type": "texture_shader",
        "status": status,
        "resolution": request.resolution,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "blender_version": blender_version,
        "template_sha256": "",
        "diagnostic": diagnostic,
    }
    return TexturePreviewResult(
        status,
        resolution=request.resolution,
        sources=sources,
        blender_version=blender_version,
        diagnostic=diagnostic,
        log=log[-20000:],
        metadata=metadata,
    )


def _value(item: object, key: str, default=""):
    return (
        item.get(key, default)
        if isinstance(item, dict)
        else getattr(item, key, default)
    )


def _resolution_value(label: str) -> int:
    digits = "".join(character for character in str(label) if character.isdigit())
    return int(digits) if digits else 0


def _filename_token(value: str) -> str:
    token = "_".join(
        part
        for part in "".join(
            character if character.isalnum() else " " for character in value
        ).split()
    )
    return token or "Asset"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _last_log_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    exception = re.compile(
        r"^(?:[\w.]+(?:Error|Exception)|Exception):\s+.+$",
        re.IGNORECASE,
    )
    exceptions = [
        line
        for line in lines
        if exception.match(line)
        and not line.casefold().startswith("error: script failed")
    ]
    if exceptions:
        return exceptions[-1][:500]
    errors = [
        line
        for line in lines
        if (
            "error" in line.casefold()
            or "traceback" in line.casefold()
        )
        and not line.casefold().startswith("error: script failed")
    ]
    return (errors[-1] if errors else lines[-1] if lines else "")[:500]
