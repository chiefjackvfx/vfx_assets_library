from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
from time import sleep
from typing import Callable, Protocol

from universal_asset_library.importer.models import StockMediaInfo, StockPreviewProfile


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def check(self) -> None: ...


class StockPreviewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StockPreviewResult:
    video_path: Path
    thumbnail_path: Path
    origin: str
    profile: str
    thumbnail_time: float


def resolve_ffmpeg(value: str = "") -> str:
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_file() and candidate.name.casefold().startswith("ffmpeg"):
            return str(candidate)
    return shutil.which("ffmpeg") or ""


def generate_stock_preview(
    source: Path,
    destination: Path,
    media_info: StockMediaInfo,
    profile: StockPreviewProfile,
    ffmpeg_path: str,
    cancel_token: CancellationToken,
    progress: Callable[[str], None] | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    scale = (
        f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if media_info.alpha == "yes":
        graph = (
            f"[0:v]{scale},split[foreground][background];"
            "[background]format=rgba,"
            "geq=r='if(mod(floor(X/32)+floor(Y/32),2),112,80)':"
            "g='if(mod(floor(X/32)+floor(Y/32),2),112,80)':"
            "b='if(mod(floor(X/32)+floor(Y/32),2),112,80)':a=255[grid];"
            "[grid][foreground]overlay=0:0:shortest=1,"
            f"format={profile.pixel_format}[video]"
        )
        command.extend(["-filter_complex", graph, "-map", "[video]"])
    else:
        command.extend(["-vf", scale, "-map", "0:v:0"])
    command.extend([
        "-map", "0:a?", "-c:v", profile.video_codec, "-crf", str(profile.crf),
        "-preset", "medium", "-pix_fmt", profile.pixel_format,
        "-c:a", profile.audio_codec, "-b:a", "128k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(destination),
    ])
    if progress:
        progress("Generating 480p preview")
    _run_process(command, cancel_token, progress=progress, duration=media_info.duration)
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise StockPreviewError("FFmpeg did not create a usable Stock preview.")


def generate_midpoint_thumbnail(
    preview: Path,
    destination: Path,
    duration: float,
    ffmpeg_path: str,
    cancel_token: CancellationToken,
    progress: Callable[[str], None] | None = None,
) -> float:
    midpoint = max(0.0, duration / 2.0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(preview), "-ss", f"{midpoint:.6f}", "-frames:v", "1",
        "-vf", "scale=w='min(960,iw)':h=-2", "-q:v", "2", str(destination),
    ]
    if progress:
        progress("Extracting midpoint thumbnail")
    _run_process(command, cancel_token)
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise StockPreviewError("FFmpeg did not create the midpoint thumbnail.")
    return midpoint


def _run_process(
    command: list[str],
    token: CancellationToken,
    *,
    progress: Callable[[str], None] | None = None,
    duration: float = 0.0,
) -> None:
    token.check()
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        raise StockPreviewError(f"Could not start FFmpeg: {error}") from error
    output: Queue[str] = Queue()
    errors: list[str] = []

    def read_output() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                output.put(line.rstrip())

    def read_errors() -> None:
        if process.stderr is not None:
            errors.extend(process.stderr.readlines())

    stdout_reader = Thread(target=read_output, daemon=True)
    stderr_reader = Thread(target=read_errors, daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    while process.poll() is None:
        if token.cancelled:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            token.check()
        _report_ffmpeg_progress(output, progress, duration)
        sleep(0.05)
    stdout_reader.join(timeout=1)
    stderr_reader.join(timeout=1)
    _report_ffmpeg_progress(output, progress, duration)
    if process.returncode:
        raise StockPreviewError("".join(errors).strip() or "FFmpeg failed.")


def _report_ffmpeg_progress(
    output: Queue[str],
    callback: Callable[[str], None] | None,
    duration: float,
) -> None:
    latest: float | None = None
    while True:
        try:
            line = output.get_nowait()
        except Empty:
            break
        key, separator, value = line.partition("=")
        if not separator or key not in {"out_time_us", "out_time_ms"}:
            continue
        try:
            latest = float(value) / 1_000_000.0
        except ValueError:
            continue
    if latest is not None and callback is not None:
        callback(
            f"Generating 480p preview · {latest:.1f}s"
            + (f" / {duration:.1f}s" if duration > 0 else "")
        )
