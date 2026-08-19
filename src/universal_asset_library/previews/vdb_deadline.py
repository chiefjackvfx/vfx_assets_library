from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
from threading import Thread
from time import monotonic, sleep
from typing import Callable

from PyQt6.QtGui import QImage

from .stock_video import resolve_ffmpeg
from .vdb_config import (
    VDB_TURNTABLE_FRAME_COUNT,
    VDB_TURNTABLE_FRAME_END,
    VDB_TURNTABLE_FRAME_START,
)
from .vdb_renderer import (
    resolve_houdini_executable,
    resolve_iconvert,
    vdb_iconvert_command,
)


DEADLINE_BACKEND = "deadline_husk"


def deadline_debug(message: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] [ShotBox Deadline] {message}", flush=True)


def _report_progress(
    message: str,
    progress: Callable[[str], None] | None = None,
) -> None:
    deadline_debug(message)
    if progress:
        progress(message)


@dataclass(frozen=True, slots=True)
class VdbDeadlineExportItem:
    asset_id: str
    asset_name: str
    vdb_path: str
    usd_path: Path
    exr_pattern: Path
    variant: str
    density_scale: int


@dataclass(slots=True)
class VdbDeadlineExportResult:
    successful: tuple[VdbDeadlineExportItem, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)
    houdini_version: str = ""
    fps: float = 24.0
    log: str = ""


@dataclass(frozen=True, slots=True)
class VdbDeadlineFinalizationResult:
    jpeg_path: Path
    video_path: Path
    width: int
    height: int
    generated_at: str
    log: str = ""


def deadline_driver_path() -> Path:
    return Path(__file__).resolve().with_name("houdini_vdb_deadline_driver.py")


def validate_deadline_husk(
    deadline_command: str,
    submitter_path: str,
    houdini_path: str = "",
    library_path: str = "",
    *,
    check_connection: bool = True,
) -> tuple[bool, str]:
    command = Path(deadline_command).expanduser()
    submitter = Path(submitter_path).expanduser()
    deadline_debug(
        f"Validating Deadline setup: command={command}, submitter={submitter}"
    )
    if not command.is_file() or not os.access(command, os.X_OK):
        return False, f"Deadline command is not executable: {command}"
    if not submitter.is_file():
        return False, f"Husk submitter script is missing: {submitter}"
    if not resolve_houdini_executable(houdini_path):
        return False, "Houdini 22 hython was not found for USD export."
    if library_path:
        library = Path(library_path).expanduser()
        if not library.is_dir() or not os.access(library, os.W_OK):
            return False, "The configured library must be a writable shared folder."
    if check_connection:
        deadline_debug("Checking Deadline repository connectivity")
        try:
            completed = subprocess.run(
                [str(command), "-GetRepositoryPath"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Deadline connectivity check failed: {error}"
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            return False, detail[-1] if detail else "Deadline repository is unavailable."
    deadline_debug("Deadline setup validation completed successfully")
    return True, "Deadline, Husk submitter, Houdini USD export, and shared library are ready."


def export_deadline_usds(
    items: tuple[VdbDeadlineExportItem, ...],
    *,
    houdini_path: str,
    template_path: Path,
    work_dir: Path,
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
    timeout_seconds: int = 1800,
) -> VdbDeadlineExportResult:
    executable = resolve_houdini_executable(houdini_path)
    if not executable:
        return VdbDeadlineExportResult(
            failed={item.asset_id: "Houdini 22 hython was not found." for item in items}
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    request_path = work_dir / "deadline-vdb-export.json"
    result_path = work_dir / "deadline-vdb-export-result.json"
    request_path.write_text(json.dumps({
        "template_path": str(template_path),
        "frame_start": VDB_TURNTABLE_FRAME_START,
        "frame_end": VDB_TURNTABLE_FRAME_END,
        "items": [{
            "asset_id": item.asset_id,
            "asset_name": item.asset_name,
            "vdb_path": item.vdb_path,
            "usd_path": str(item.usd_path),
            "exr_pattern": str(item.exr_pattern),
            "density_scale": item.density_scale,
        } for item in items],
    }, indent=2), encoding="utf-8")
    _report_progress(
        f"Starting Houdini Export USD batch for {len(items)} asset(s)",
        progress,
    )
    log = _run_process(
        [executable, str(deadline_driver_path()), str(request_path), str(result_path)],
        timeout_seconds,
        cancel_token,
        progress,
        allow_failure=True,
    )
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return VdbDeadlineExportResult(
            failed={item.asset_id: f"Houdini did not return an export result: {error}" for item in items},
            log=log,
        )
    by_id = {item.asset_id: item for item in items}
    successful = []
    failed = {}
    for result in document.get("items", []):
        asset_id = str(result.get("asset_id", ""))
        item = by_id.get(asset_id)
        if item is None:
            continue
        if result.get("ok") and item.usd_path.is_file():
            successful.append(item)
        else:
            failed[asset_id] = str(result.get("diagnostic", "USD export failed."))
    for asset_id in by_id.keys() - {item.asset_id for item in successful} - failed.keys():
        failed[asset_id] = "Houdini did not report an export result for this asset."
    deadline_debug(
        f"USD export batch finished: {len(successful)} succeeded, "
        f"{len(failed)} failed"
    )
    return VdbDeadlineExportResult(
        tuple(successful), failed,
        str(document.get("houdini_version", "")),
        float(document.get("fps", 24.0) or 24.0), log,
    )


def launch_husk_submitter(
    deadline_command: str,
    submitter_path: str,
    usd_paths: tuple[Path, ...],
) -> subprocess.Popen:
    if not usd_paths:
        raise ValueError("At least one exported USD is required for Deadline submission.")
    deadline_debug(
        f"Opening Husk submitter for {len(usd_paths)} USD file(s)"
    )
    for path in usd_paths:
        deadline_debug(f"  USD: {path}")
    return subprocess.Popen(
        [
            deadline_command,
            "ExecuteScript",
            submitter_path,
            *(str(path) for path in usd_paths),
            "--modal",
        ],
        start_new_session=os.name != "nt",
    )


def deadline_frame_paths(pattern: Path) -> tuple[Path, ...]:
    return tuple(
        Path(str(pattern).replace("$F4", f"{frame:04d}"))
        for frame in range(VDB_TURNTABLE_FRAME_START, VDB_TURNTABLE_FRAME_END + 1)
    )


def deadline_frame_count(pattern: Path) -> int:
    count = 0
    for path in deadline_frame_paths(pattern):
        try:
            if path.stat().st_size > 0:
                count += 1
        except OSError:
            continue
    return count


def deadline_frame_signature(pattern: Path) -> tuple[tuple[int, int], ...] | None:
    values = []
    for path in deadline_frame_paths(pattern):
        try:
            stat_result = path.stat()
        except OSError:
            return None
        if stat_result.st_size <= 0:
            return None
        values.append((stat_result.st_size, stat_result.st_mtime_ns))
    return tuple(values)


def finalize_deadline_turntable(
    exr_pattern: Path,
    output_dir: Path,
    asset_name: str,
    *,
    houdini_path: str = "",
    ffmpeg_path: str = "",
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
    fps: float = 24.0,
) -> VdbDeadlineFinalizationResult:
    iconvert = resolve_iconvert(resolve_houdini_executable(houdini_path))
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    if not iconvert:
        raise RuntimeError("Houdini iconvert is required to finalize Deadline EXRs.")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to finalize Deadline turntables.")
    frames = deadline_frame_paths(exr_pattern)
    if any(not path.is_file() or path.stat().st_size <= 0 for path in frames):
        raise RuntimeError("All 36 Deadline EXRs must exist before finalization.")
    _report_progress(
        f"Finalizing {asset_name}: {len(frames)} stable EXRs found",
        progress,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    token = _filename_token(asset_name)
    jpeg = output_dir / f"{token}_VDB_Preview.jpg"
    video = output_dir / f"{token}_VDB_Turntable.mp4"
    png_dir = output_dir / "video_frames"
    png_dir.mkdir(parents=True, exist_ok=True)
    log = ""
    for index, exr in enumerate(frames, VDB_TURNTABLE_FRAME_START):
        if index == 1 or index % 10 == 0 or index == VDB_TURNTABLE_FRAME_END:
            _report_progress(
                f"Converting {asset_name} frame "
                f"{index}/{VDB_TURNTABLE_FRAME_END} to display colour",
                progress,
            )
        png = png_dir / f"vdb-turntable.{index:04d}.png"
        log += _run_process(
            vdb_iconvert_command(iconvert, exr, png),
            120, cancel_token, None,
        )
        if index == VDB_TURNTABLE_FRAME_START:
            log += _run_process(
                vdb_iconvert_command(iconvert, exr, jpeg),
                120, cancel_token, None,
            )
    image = QImage(str(jpeg))
    if image.isNull():
        raise RuntimeError("Houdini iconvert did not create a readable preview JPEG.")
    _report_progress(
        f"Encoding {asset_name} MP4 from {VDB_TURNTABLE_FRAME_COUNT} PNG frames",
        progress,
    )
    log += _run_process([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", f"{fps:g}", "-start_number", "1",
        "-i", str(png_dir / "vdb-turntable.%04d.png"),
        "-frames:v", str(VDB_TURNTABLE_FRAME_COUNT),
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,scale=iw:ih:in_range=full:out_range=tv:out_color_matrix=bt709",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-g", "1", "-keyint_min", "1", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-movflags", "+faststart", str(video),
    ], 600, cancel_token, progress)
    if not video.is_file() or video.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not create the Deadline turntable MP4.")
    deadline_debug(
        f"Finalization complete for {asset_name}: JPEG={jpeg.name}, "
        f"MP4={video.name}"
    )
    return VdbDeadlineFinalizationResult(
        jpeg, video, image.width(), image.height(),
        datetime.now(timezone.utc).isoformat(), log[-20000:],
    )


def _run_process(
    command: list[str],
    timeout_seconds: int,
    cancel_token,
    progress: Callable[[str], None] | None,
    *,
    allow_failure: bool = False,
) -> str:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=os.name != "nt",
        bufsize=1,
    )
    started = monotonic()
    output: list[str] = []
    lines: Queue[str] = Queue()

    def read_output() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                lines.put(line)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    while process.poll() is None:
        _drain_output(lines, output, progress)
        if cancel_token is not None and cancel_token.cancelled:
            _terminate(process)
            raise RuntimeError("Deadline VDB export was canceled.")
        if monotonic() - started > timeout_seconds:
            _terminate(process)
            raise RuntimeError("Deadline VDB operation timed out.")
        sleep(0.05)
    reader.join(timeout=2)
    _drain_output(lines, output, progress)
    log = "".join(output)
    if process.returncode and not allow_failure:
        detail = next((line for line in reversed(log.splitlines()) if line.strip()), "Process failed.")
        raise RuntimeError(detail)
    return log


def _drain_output(
    lines: Queue[str], output: list[str], progress: Callable[[str], None] | None
) -> None:
    while True:
        try:
            line = lines.get_nowait()
        except Empty:
            return
        output.append(line)
        if line.startswith(("SHOTBOX_PROGRESS:", "SHOTBOX_DEBUG:")):
            message = line.partition(":")[2].strip()
            deadline_debug(message)
            if line.startswith("SHOTBOX_PROGRESS:") and progress:
                progress(message)


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except Exception:
        process.kill()


def _filename_token(value: str) -> str:
    token = "_".join(value.strip().split())
    return "".join(character for character in token if character.isalnum() or character in "_-.") or "VDB"
