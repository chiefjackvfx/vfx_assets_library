from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import signal
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep
from typing import Callable

from PyQt6.QtGui import QImage

from .stock_video import resolve_ffmpeg


@dataclass(frozen=True, slots=True)
class VdbPreviewRequest:
    source_path: Path
    vdb_path: str
    source_relative: str
    source_sha256: str
    output_dir: Path
    asset_name: str
    variant: str
    density_scale: int = 100
    mode: str = "still"
    ffmpeg_path: str = ""
    houdini_path: str = ""
    template_path: Path | None = None
    frame: int = 1
    timeout_seconds: int = 1800
    parallel_processes: int = 2


@dataclass(slots=True)
class VdbPreviewResult:
    status: str
    thumbnail_path: Path | None = None
    hero_path: Path | None = None
    variant: str = ""
    source_relative: str = ""
    frame: int = 1
    houdini_version: str = ""
    template_sha256: str = ""
    diagnostic: str = ""
    log: str = ""
    metadata: dict = field(default_factory=dict)
    video_path: Path | None = None
    mode: str = "still"


class VdbPreviewError(RuntimeError):
    def __init__(self, status: str, diagnostic: str, log: str = "") -> None:
        super().__init__(diagnostic)
        self.status = status
        self.diagnostic = diagnostic
        self.log = log


def default_template_path() -> Path:
    packaged = Path(__file__).resolve().parent / "templates" / "VDB_preview_v001.hip"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "templates" / "VDB_preview_v001.hip"


def driver_path() -> Path:
    return Path(__file__).resolve().with_name("houdini_vdb_driver.py")


def resolve_houdini_executable(configured: str = "") -> str:
    candidates: list[Path] = []
    if configured.strip():
        selected = Path(os.path.expandvars(os.path.expanduser(configured.strip())))
        if selected.is_dir():
            candidates.extend((selected / "bin" / _executable_name("hython"), selected / _executable_name("hython")))
        else:
            candidates.append(
                selected.with_name(_executable_name("hython"))
                if selected.stem.casefold() in {"houdini", "houdinifx", "hbatch", "hython"}
                else selected
            )
    else:
        discovered = shutil.which(_executable_name("hython")) or shutil.which("hython")
        if discovered:
            candidates.append(Path(discovered))
        if sys.platform.startswith("linux"):
            candidates.extend(Path(value) for value in glob.glob("/opt/hfs*/bin/hython"))
        elif sys.platform == "darwin":
            candidates.extend(Path(value) for value in glob.glob(
                "/Applications/Houdini/Houdini*.app/Contents/Frameworks/Houdini.framework/Versions/*/Resources/bin/hython"
            ))
        elif sys.platform == "win32":
            for root in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"))):
                candidates.extend(Path(value) for value in glob.glob(
                    str(Path(root) / "Side Effects Software" / "Houdini 22*" / "bin" / "hython.exe")
                ))
    usable = [path.absolute() for path in candidates if path.is_file() and os.access(path, os.X_OK)]
    usable.sort(key=_houdini_path_version, reverse=True)
    return str(usable[0]) if usable else ""


def resolve_iconvert(hython_path: str) -> str:
    if not hython_path:
        return ""
    sibling = Path(hython_path).with_name(_executable_name("iconvert"))
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which(_executable_name("iconvert")) or shutil.which("iconvert") or ""


def validate_houdini_executable(configured: str = "") -> tuple[bool, str, str]:
    executable = resolve_houdini_executable(configured)
    if not executable:
        return False, "Houdini 22 hython was not found. Choose a Houdini executable or installation.", ""
    if not resolve_iconvert(executable):
        return False, "The Houdini installation does not contain iconvert.", ""
    try:
        completed = subprocess.run(
            [executable, "-c", "import hou; print(hou.applicationVersionString())"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Houdini could not be launched: {error}", ""
    output = "\n".join(value for value in (completed.stdout, completed.stderr) if value).strip()
    version = _version_from_text(output)
    if completed.returncode:
        detail = output.splitlines()[-1] if output else "Unknown Houdini or license error"
        return False, f"Houdini check failed: {detail}", version
    if not version or int(version.split(".", 1)[0]) < 22:
        return False, f"Houdini 22 or newer is required; detected {version or 'an unknown version'}.", version
    return True, f"Detected Houdini {version} with hython and iconvert.", version


def render_vdb_preview(
    request: VdbPreviewRequest,
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
) -> VdbPreviewResult:
    if request.mode not in {"still", "turntable"}:
        return _failure("failed", request, f"Unknown VDB preview mode: {request.mode}")
    if not 10 <= request.density_scale <= 500:
        return _failure(
            "failed",
            request,
            "VDB preview density must be between 10 and 500.",
        )
    executable = resolve_houdini_executable(request.houdini_path)
    if not executable:
        return _failure("unsupported", request, "Houdini 22 hython was not found. Configure it in Settings.")
    valid, validation_message, _detected_version = validate_houdini_executable(executable)
    if not valid:
        return _failure("unsupported", request, validation_message)
    iconvert = resolve_iconvert(executable)
    if not iconvert:
        return _failure("unsupported", request, "Houdini iconvert was not found beside hython.")
    ffmpeg = resolve_ffmpeg(request.ffmpeg_path) if request.mode == "turntable" else ""
    if request.mode == "turntable" and not ffmpeg:
        return _failure(
            "unsupported",
            request,
            "FFmpeg is required to generate a VDB turntable MP4. Configure it in Settings.",
        )
    template = request.template_path or default_template_path()
    if not template.is_file():
        return _failure("unsupported", request, f"VDB preview template is missing: {template}")
    if not request.source_path.is_file() or request.source_path.suffix.casefold() != ".vdb":
        return _failure("failed", request, f"The frame {request.frame} managed VDB is missing: {request.source_path}")
    request.output_dir.mkdir(parents=True, exist_ok=True)
    template_hash = _sha256(template)
    output_exr = (
        request.output_dir / "frames" / "vdb-turntable.$F4.exr"
        if request.mode == "turntable"
        else request.output_dir / "vdb-preview.exr"
    )
    output_jpg = request.output_dir / f"{_filename_token(request.asset_name)}_VDB_Preview.jpg"
    output_mp4 = request.output_dir / f"{_filename_token(request.asset_name)}_VDB_Turntable.mp4"
    log = ""
    try:
        if request.mode == "turntable":
            log, document = _render_turntable_processes(
                executable,
                template,
                request,
                output_exr,
                cancel_token,
                progress,
            )
        else:
            staged_template = request.output_dir / template.name
            shutil.copyfile(template, staged_template)
            request_json = request.output_dir / "houdini-vdb-request.json"
            result_json = request.output_dir / "houdini-vdb-result.json"
            request_json.write_text(
                json.dumps(
                    _driver_payload(
                        staged_template, request, output_exr, 1, 1
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
            if progress:
                progress("Starting Houdini 22 preview process")
            log = _run_process(
                [
                    executable,
                    str(driver_path()),
                    str(request_json),
                    str(result_json),
                ],
                request.timeout_seconds,
                cancel_token,
                progress,
            )
            document = _read_driver_result(result_json, log)
        if not document.get("ok"):
            raise VdbPreviewError("failed", str(document.get("diagnostic", "Houdini VDB preview failed.")), log)
        first_exr = (
            Path(str(output_exr).replace("$F4", "0001"))
            if request.mode == "turntable"
            else output_exr
        )
        if not first_exr.is_file() or first_exr.stat().st_size <= 0:
            raise VdbPreviewError("failed", "Houdini did not create the staged EXR.", log)
        if request.mode == "turntable":
            missing = [
                frame
                for frame in range(1, 51)
                if not Path(
                    str(output_exr).replace("$F4", f"{frame:04d}")
                ).is_file()
            ]
            if missing:
                raise VdbPreviewError(
                    "failed",
                    "Parallel Houdini rendering did not create every frame: "
                    + ", ".join(str(frame) for frame in missing[:12]),
                    log,
                )
        if _sha256(template) != template_hash:
            raise VdbPreviewError(
                "failed",
                "The authoritative VDB preview template changed during rendering; "
                "the result was not accepted.",
                log,
            )
        if progress:
            progress("Converting Houdini EXR to JPEG")
        conversion_log = _run_process(
            [iconvert, "-d", "8", "-g", "auto", str(first_exr), str(output_jpg)],
            120,
            cancel_token,
            None,
        )
        log = (log + "\n" + conversion_log).strip()
        image = QImage(str(output_jpg))
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            raise VdbPreviewError("failed", "Houdini iconvert did not create a readable JPEG.", log)
        fps = float(document.get("fps", 24.0) or 24.0)
        if request.mode == "turntable":
            video_frames = request.output_dir / "video_frames"
            video_frames.mkdir(parents=True, exist_ok=True)
            video_frame_pattern = video_frames / "vdb-turntable.%04d.png"
            for frame in range(1, 51):
                if progress and (frame == 1 or frame % 10 == 0):
                    progress(
                        f"Applying Houdini preview colour transform "
                        f"({frame}/50)"
                    )
                frame_exr = Path(
                    str(output_exr).replace("$F4", f"{frame:04d}")
                )
                frame_png = Path(str(video_frame_pattern) % frame)
                conversion_log = _run_process(
                    [
                        iconvert,
                        "-d", "8",
                        "-g", "auto",
                        str(frame_exr),
                        str(frame_png),
                    ],
                    120,
                    cancel_token,
                    None,
                )
                log = (log + "\n" + conversion_log).strip()
            if progress:
                progress("Encoding 50-frame VDB turntable MP4")
            encoding_log = _run_process(
                [
                    ffmpeg,
                    "-hide_banner", "-loglevel", "error", "-y",
                    "-framerate", f"{fps:g}",
                    "-start_number", "1",
                    "-i", str(video_frame_pattern),
                    "-frames:v", "50",
                    "-vf", (
                        "pad=ceil(iw/2)*2:ceil(ih/2)*2,"
                        "scale=iw:ih:in_range=full:out_range=tv:"
                        "out_color_matrix=bt709"
                    ),
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "18",
                    "-g", "1",
                    "-keyint_min", "1",
                    "-sc_threshold", "0",
                    "-pix_fmt", "yuv420p",
                    "-color_range", "tv",
                    "-colorspace", "bt709",
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709",
                    "-movflags", "+faststart",
                    str(output_mp4),
                ],
                600,
                cancel_token,
                None,
            )
            log = (log + "\n" + encoding_log).strip()
            if not output_mp4.is_file() or output_mp4.stat().st_size <= 0:
                raise VdbPreviewError("failed", "FFmpeg did not create the VDB turntable MP4.", log)
        version = str(document.get("houdini_version", ""))
        metadata = {
            "type": (
                "vdb_turntable" if request.mode == "turntable" else "vdb_still"
            ),
            "status": "ready",
            "variant": request.variant,
            "source": request.source_relative,
            "source_sha256": request.source_sha256,
            "frame": request.frame,
            "frame_start": 1,
            "frame_end": 50 if request.mode == "turntable" else 1,
            "fps": fps,
            "mode": request.mode,
            "scrub_optimized": request.mode == "turntable",
            "color_transform": (
                "houdini_iconvert_auto"
                if request.mode == "turntable"
                else ""
            ),
            "video_color_space": (
                "bt709" if request.mode == "turntable" else ""
            ),
            "parallel_processes": (
                min(max(1, request.parallel_processes), 4)
                if request.mode == "turntable"
                else 1
            ),
            "density_scale": request.density_scale,
            "width": image.width(),
            "height": image.height(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "houdini_version": version,
            "template_sha256": template_hash,
            "diagnostic": "",
        }
        return VdbPreviewResult(
            "ready", output_jpg, output_jpg, request.variant,
            request.source_relative, request.frame, version, template_hash,
            metadata=metadata, log=log[-20000:],
            video_path=output_mp4 if request.mode == "turntable" else None,
            mode=request.mode,
        )
    except VdbPreviewError as error:
        return _failure(error.status, request, error.diagnostic, log=error.log or log)
    except Exception as error:
        return _failure("failed", request, str(error), log=log)


class _ParallelCancelToken:
    def __init__(self, external, stopped: Event) -> None:
        self.external = external
        self.stopped = stopped

    @property
    def cancelled(self) -> bool:
        return self.stopped.is_set() or bool(
            self.external is not None and self.external.cancelled
        )


def _render_turntable_processes(
    executable: str,
    template: Path,
    request: VdbPreviewRequest,
    output_exr: Path,
    cancel_token,
    progress,
) -> tuple[str, dict]:
    worker_count = min(4, max(1, int(request.parallel_processes)))
    ranges = _frame_chunks(1, 50, worker_count)
    stopped = Event()
    combined_token = _ParallelCancelToken(cancel_token, stopped)
    workers_root = request.output_dir / "workers"
    workers_root.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(
            f"Launching {worker_count} parallel Houdini turntable "
            f"instance{'s' if worker_count != 1 else ''}"
        )
    futures = {}
    logs: dict[int, str] = {}
    documents: dict[int, dict] = {}
    first_error: Exception | None = None
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for index, (frame_start, frame_end) in enumerate(ranges, start=1):
            worker_dir = workers_root / f"worker-{index:02d}"
            worker_dir.mkdir(parents=True, exist_ok=False)
            staged_template = worker_dir / template.name
            shutil.copyfile(template, staged_template)
            request_json = worker_dir / "request.json"
            result_json = worker_dir / "result.json"
            request_json.write_text(
                json.dumps(
                    _driver_payload(
                        staged_template,
                        request,
                        output_exr,
                        frame_start,
                        frame_end,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )

            def worker_progress(message: str, worker=index) -> None:
                if progress:
                    progress(f"Houdini {worker}/{worker_count} · {message}")

            future = executor.submit(
                _run_process,
                [
                    executable,
                    str(driver_path()),
                    str(request_json),
                    str(result_json),
                ],
                request.timeout_seconds,
                combined_token,
                worker_progress,
            )
            futures[future] = (index, result_json)

        for future in as_completed(futures):
            index, result_json = futures[future]
            try:
                worker_log = future.result()
                document = _read_driver_result(result_json, worker_log)
                if not document.get("ok"):
                    raise VdbPreviewError(
                        "failed",
                        str(document.get(
                            "diagnostic", "Houdini turntable worker failed."
                        )),
                        worker_log,
                    )
                logs[index] = worker_log
                documents[index] = document
            except Exception as error:
                stopped.set()
                if first_error is None:
                    first_error = error
    if first_error is not None:
        if isinstance(first_error, VdbPreviewError):
            raise first_error
        raise VdbPreviewError("failed", str(first_error)) from first_error
    combined_log = "\n".join(logs[index] for index in sorted(logs))[-20000:]
    return combined_log, documents[min(documents)]


def _driver_payload(
    staged_template: Path,
    request: VdbPreviewRequest,
    output_exr: Path,
    frame_start: int,
    frame_end: int,
) -> dict:
    return {
        "template_path": str(staged_template.resolve()),
        "vdb_path": request.vdb_path,
        "output_exr": str(output_exr.resolve()),
        "frame": request.frame,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "mode": request.mode,
        "density_scale": request.density_scale,
    }


def _read_driver_result(result_json: Path, log: str) -> dict:
    try:
        return json.loads(result_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VdbPreviewError(
            "failed",
            f"Houdini did not return a valid preview result: {error}",
            log,
        ) from error


def _frame_chunks(
    frame_start: int, frame_end: int, count: int
) -> tuple[tuple[int, int], ...]:
    total = frame_end - frame_start + 1
    count = min(max(1, count), total)
    base, remainder = divmod(total, count)
    chunks = []
    current = frame_start
    for index in range(count):
        size = base + (1 if index < remainder else 0)
        chunks.append((current, current + size - 1))
        current += size
    return tuple(chunks)


def _run_process(command, timeout_seconds, token, progress) -> str:
    if token is not None and token.cancelled:
        raise VdbPreviewError("canceled", "VDB preview rendering was canceled.")
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as error:
        raise VdbPreviewError("unsupported", f"Could not start Houdini preview tool: {error}") from error
    lines: Queue[str] = Queue()

    def read_output() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                lines.put(line)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    output: list[str] = []
    started = monotonic()
    try:
        while process.poll() is None:
            _drain_output(lines, output, progress)
            if token is not None and token.cancelled:
                _terminate_process(process)
                raise VdbPreviewError("canceled", "VDB preview rendering was canceled.", "".join(output))
            if monotonic() - started > timeout_seconds:
                _terminate_process(process)
                raise VdbPreviewError("timeout", f"VDB preview rendering exceeded {timeout_seconds // 60} minutes.", "".join(output))
            sleep(0.1)
        reader.join(timeout=2)
        _drain_output(lines, output, progress)
        if process.returncode:
            diagnostic = next((line.strip() for line in reversed(output) if line.strip()), "Houdini preview process failed.")
            raise VdbPreviewError("failed", diagnostic, "".join(output))
        return "".join(output)[-20000:]
    finally:
        if process.poll() is None:
            _terminate_process(process)


def _drain_output(lines: Queue[str], output: list[str], progress) -> None:
    while True:
        try:
            line = lines.get_nowait()
        except Empty:
            return
        output.append(line)
        if progress and line.startswith("SHOTBOX_PROGRESS:"):
            progress(line.partition(":")[2].strip())


def _terminate_process(process) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            pass


def _failure(status: str, request: VdbPreviewRequest, diagnostic: str, *, log: str = "") -> VdbPreviewResult:
    metadata = {
        "type": (
            "vdb_turntable" if request.mode == "turntable" else "vdb_still"
        ),
        "status": status, "variant": request.variant,
        "source": request.source_relative, "source_sha256": request.source_sha256,
        "frame": request.frame, "generated_at": datetime.now(timezone.utc).isoformat(),
        "frame_start": 1,
        "frame_end": 50 if request.mode == "turntable" else 1,
        "mode": request.mode,
        "scrub_optimized": False,
        "color_transform": "",
        "video_color_space": "",
        "parallel_processes": (
            min(max(1, request.parallel_processes), 4)
            if request.mode == "turntable"
            else 1
        ),
        "density_scale": request.density_scale,
        "houdini_version": "", "template_sha256": "", "diagnostic": diagnostic,
    }
    return VdbPreviewResult(
        status, variant=request.variant, source_relative=request.source_relative,
        frame=request.frame, diagnostic=diagnostic, log=log[-20000:], metadata=metadata,
        mode=request.mode,
    )


def _executable_name(value: str) -> str:
    return value + ".exe" if sys.platform == "win32" else value


def _houdini_path_version(path: Path) -> tuple[int, ...]:
    values = re.findall(r"\d+", str(path.parent.parent))
    return tuple(int(value) for value in values[-3:])


def _version_from_text(value: str) -> str:
    match = re.search(r"\b(\d{2,3}\.\d+(?:\.\d+)?)\b", value)
    return match.group(1) if match else ""


def _filename_token(value: str) -> str:
    token = "_".join(part for part in "".join(character if character.isalnum() else " " for character in value).split())
    return token or "Asset"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
