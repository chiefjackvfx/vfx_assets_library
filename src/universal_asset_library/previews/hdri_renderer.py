from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic
from typing import Callable, Mapping

from PyQt6.QtGui import QColor, QImage, QPainter


EXPECTED_SCENE_SIZE = (2048, 512)
PANORAMA_SIZE = (2048, 1024)
COMPOSITE_SIZE = (2048, 1536)


@dataclass(frozen=True, slots=True)
class HdriPreviewRequest:
    hdri_path: Path
    output_dir: Path
    asset_name: str
    resolutions: tuple[str, ...]
    source_relative: str
    blender_path: str = ""
    template_path: Path | None = None
    timeout_seconds: int = 600


@dataclass(slots=True)
class HdriPreviewResult:
    status: str
    thumbnail_path: Path | None = None
    hero_path: Path | None = None
    source_relative: str = ""
    blender_version: str = ""
    template_sha256: str = ""
    diagnostic: str = ""
    log: str = ""
    metadata: dict = field(default_factory=dict)


def default_template_path() -> Path:
    packaged = Path(__file__).resolve().parent / "templates" / "hdri_preview.blend"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "templates" / "hdri_preview.blend"


def driver_path() -> Path:
    return Path(__file__).resolve().with_name("blender_hdri_driver.py")


def resolve_blender_executable(configured: str = "") -> str:
    if configured.strip():
        return os.path.abspath(os.path.expanduser(os.path.expandvars(configured.strip())))
    return shutil.which("blender") or ""


def validate_blender_executable(configured: str = "") -> tuple[bool, str, str]:
    executable = resolve_blender_executable(configured)
    if not executable:
        return False, "Blender was not found. Choose its executable or install it on PATH.", ""
    path = Path(executable)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False, "The selected Blender path is not an executable file.", ""
    try:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Blender could not be launched: {error}", ""
    output = (completed.stdout or completed.stderr).strip()
    version = output.splitlines()[0] if output else "Unknown Blender version"
    if completed.returncode:
        return False, f"Blender version check failed: {version}", version
    return True, f"Detected {version}.", version


def select_hdri_variant(resolutions: Mapping[str, object]) -> tuple[str, object] | None:
    candidates: list[tuple[int, str, object]] = []
    for label, variant in resolutions.items():
        width = variant.get("width") if isinstance(variant, dict) else getattr(variant, "width", None)
        digits = "".join(character for character in str(label) if character.isdigit())
        value = max(1, round(int(width) / 1024)) if width else int(digits) if digits else 1_000_000
        candidates.append((value, str(label), variant))
    if not candidates:
        return None
    at_most_4k = [item for item in candidates if item[0] <= 4]
    selected = max(at_most_4k, default=min(candidates), key=lambda item: item[0])
    return selected[1], selected[2]


def select_hdri_file(variant: object) -> object | None:
    files = getattr(variant, "files", None)
    if files is None and isinstance(variant, dict):
        files = variant.get("files", [])
    values = list(files or [])

    def value(item, key, default=""):
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    if not values:
        return None
    def rank(item) -> tuple[int, str]:
        file_format = str(value(item, "format", value(item, "file_format", ""))).upper()
        preferred = bool(value(item, "preferred", False))
        priority = 4 if file_format == "EXR" and preferred else 3 if file_format == "EXR" else 2 if file_format == "HDR" and preferred else 1 if file_format == "HDR" else 0
        return priority, str(value(item, "path", "")).casefold()

    return max(values, key=rank)


def render_hdri_preview(
    request: HdriPreviewRequest,
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
) -> HdriPreviewResult:
    executable = resolve_blender_executable(request.blender_path)
    if not executable:
        return _failure("unsupported", request, "Blender was not found. Configure its executable in Settings.")
    valid, diagnostic, version = validate_blender_executable(executable)
    if not valid:
        return _failure("unsupported", request, diagnostic, blender_version=version)
    template = request.template_path or default_template_path()
    if not template.is_file():
        return _failure("unsupported", request, f"HDRI preview template is missing: {template}", blender_version=version)
    if not request.hdri_path.is_file():
        return _failure("failed", request, f"HDRI source is missing: {request.hdri_path}", blender_version=version)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    scene_path = request.output_dir / "scene.png"
    panorama_path = request.output_dir / "panorama.png"
    result_path = request.output_dir / "blender-result.json"
    command = [
        executable, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", str(template),
        "--python", str(driver_path()), "--",
        "--hdri", str(request.hdri_path),
        "--scene-output", str(scene_path),
        "--panorama-output", str(panorama_path),
        "--result", str(result_path),
    ]
    if progress:
        progress("Launching Blender")
    started = monotonic()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as error:
        return _failure("failed", request, f"Blender could not be launched: {error}", blender_version=version)
    output = ""
    while True:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            process.terminate()
            try:
                tail, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                tail, _ = process.communicate()
            return _failure("canceled", request, "HDRI preview rendering was canceled.", blender_version=version, log=output + tail)
        if monotonic() - started > request.timeout_seconds:
            process.terminate()
            try:
                tail, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                tail, _ = process.communicate()
            return _failure("failed", request, "Blender preview rendering timed out.", blender_version=version, log=output + tail)
        try:
            tail, _ = process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired as pending:
            if pending.output:
                output = pending.output if isinstance(pending.output, str) else pending.output.decode(errors="replace")
            continue
        output = (tail or output or "")[-20000:]
        break
    if process.returncode:
        message = _last_log_line(output) or f"Blender exited with code {process.returncode}."
        return _failure("failed", request, message, blender_version=version, log=output)
    if not scene_path.is_file() or not panorama_path.is_file():
        return _failure("failed", request, "Blender did not create both required render passes.", blender_version=version, log=output)
    metadata = {}
    try:
        metadata = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        metadata = {}
    if progress:
        progress("Compositing HDRI preview")
    hero = request.output_dir / f"{_filename_token(request.asset_name)}_HDRI_Preview.jpg"
    try:
        compose_hdri_preview(scene_path, panorama_path, hero, request.resolutions)
    except ValueError as error:
        return _failure("failed", request, str(error), blender_version=version, log=output)
    template_hash = _sha256(template)
    render_metadata = {
        "type": "hdri_composite", "status": "ready", "source": request.source_relative,
        "width": COMPOSITE_SIZE[0], "height": COMPOSITE_SIZE[1],
        "scene_width": EXPECTED_SCENE_SIZE[0], "scene_height": EXPECTED_SCENE_SIZE[1],
        "panorama_width": PANORAMA_SIZE[0], "panorama_height": PANORAMA_SIZE[1],
        "generated_at": datetime.now(timezone.utc).isoformat(), "blender_version": metadata.get("blender_version", version),
        "template_sha256": template_hash, "diagnostic": "",
    }
    return HdriPreviewResult("ready", hero, hero, request.source_relative, str(render_metadata["blender_version"]), template_hash, metadata=render_metadata, log=output)


def compose_hdri_preview(
    scene_path: Path, panorama_path: Path, hero_path: Path, resolutions: tuple[str, ...],
) -> None:
    scene = QImage(str(scene_path))
    panorama = QImage(str(panorama_path))
    if (scene.width(), scene.height()) != EXPECTED_SCENE_SIZE:
        raise ValueError(f"Template render must be 2048×512; received {scene.width()}×{scene.height()}.")
    if (panorama.width(), panorama.height()) != PANORAMA_SIZE:
        raise ValueError(f"Panorama render must be 2048×1024; received {panorama.width()}×{panorama.height()}.")
    canvas = QImage(*COMPOSITE_SIZE, QImage.Format.Format_RGB32)
    canvas.fill(QColor("#11151a"))
    painter = QPainter(canvas)
    painter.drawImage(0, 0, panorama)
    painter.drawImage(0, PANORAMA_SIZE[1], scene)
    painter.end()
    hero_path.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(hero_path), "JPG", 90):
        raise ValueError("Could not save the composite HDRI preview JPEG.")


def _failure(status: str, request: HdriPreviewRequest, diagnostic: str, *, blender_version: str = "", log: str = "") -> HdriPreviewResult:
    metadata = {
        "type": "hdri_composite", "status": status, "source": request.source_relative,
        "generated_at": datetime.now(timezone.utc).isoformat(), "blender_version": blender_version,
        "template_sha256": "", "diagnostic": diagnostic,
    }
    return HdriPreviewResult(status, source_relative=request.source_relative, blender_version=blender_version, diagnostic=diagnostic, log=log[-20000:], metadata=metadata)


def _filename_token(value: str) -> str:
    token = "_".join(part for part in "".join(character if character.isalnum() else " " for character in value).split())
    return token or "Asset"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _last_log_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.casefold() or "traceback" in line.casefold()]
    return (errors[-1] if errors else lines[-1] if lines else "")[:500]
