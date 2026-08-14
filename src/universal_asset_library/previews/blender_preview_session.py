from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import RLock, Thread
from time import monotonic
from typing import Callable
from uuid import uuid4

from .hdri_renderer import resolve_blender_executable


PROTOCOL_PREFIX = "UAL_PREVIEW_EVENT "


class BlenderPreviewSessionError(RuntimeError):
    def __init__(self, status: str, diagnostic: str, log: str = "") -> None:
        super().__init__(diagnostic)
        self.status = status
        self.diagnostic = diagnostic
        self.log = log[-20000:]


def server_path() -> Path:
    return Path(__file__).resolve().with_name("blender_preview_server.py")


class BlenderPreviewSession:
    """One background Blender process serving serial texture and HDRI jobs."""

    def __init__(self, blender_path: str = "") -> None:
        self.blender_path = blender_path
        self._process: subprocess.Popen | None = None
        self._events: Queue[dict] = Queue()
        self._reader: Thread | None = None
        self._lock = RLock()
        self._log: deque[str] = deque(maxlen=400)
        self._template_hashes: dict[Path, str] = {}
        self.blender_version = ""
        self.start_count = 0

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def log(self) -> str:
        return "\n".join(self._log)[-20000:]

    def template_hash(self, template: Path) -> str:
        path = template.resolve()
        cached = self._template_hashes.get(path)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        value = digest.hexdigest()
        self._template_hashes[path] = value
        return value

    def render(
        self,
        kind: str,
        template: Path,
        payload: dict,
        *,
        timeout_seconds: int,
        progress: Callable[[str], None] | None = None,
        cancel_token=None,
    ) -> dict:
        with self._lock:
            self._start(timeout_seconds=min(timeout_seconds, 30))
            process = self._process
            if process is None or process.stdin is None:
                raise BlenderPreviewSessionError(
                    "failed", "Blender preview session is unavailable.", self.log
                )
            job_id = uuid4().hex
            command = {
                "command": "render",
                "job_id": job_id,
                "kind": kind,
                "template": str(template.resolve()),
                "payload": payload,
            }
            try:
                process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self._terminate_locked()
                raise BlenderPreviewSessionError(
                    "failed", f"Blender preview session stopped: {error}", self.log
                ) from error

            started = monotonic()
            while True:
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    self._terminate_locked()
                    raise BlenderPreviewSessionError(
                        "canceled", "Preview rendering was canceled.", self.log
                    )
                if monotonic() - started > timeout_seconds:
                    self._terminate_locked()
                    raise BlenderPreviewSessionError(
                        "failed", "Blender preview rendering timed out.", self.log
                    )
                event = self._next_event(0.2)
                if event is None:
                    if not self.running:
                        self._terminate_locked()
                        raise BlenderPreviewSessionError(
                            "failed", "Blender preview session exited unexpectedly.", self.log
                        )
                    continue
                if event.get("event") == "eof":
                    self._terminate_locked()
                    raise BlenderPreviewSessionError(
                        "failed", "Blender preview session exited unexpectedly.", self.log
                    )
                if event.get("job_id") != job_id:
                    continue
                if event.get("event") == "progress":
                    if progress:
                        progress(str(event.get("message", "Rendering preview")))
                    continue
                if event.get("event") != "result":
                    continue
                status = str(event.get("status", "failed"))
                if status != "ready":
                    raise BlenderPreviewSessionError(
                        status,
                        str(event.get("diagnostic", "Blender preview rendering failed.")),
                        self.log,
                    )
                metadata = event.get("metadata", {})
                return dict(metadata) if isinstance(metadata, dict) else {}

    def abort(self) -> None:
        with self._lock:
            self._terminate_locked()

    def close(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write('{"command":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=2)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    pass
            self._terminate_locked()

    def _start(self, timeout_seconds: int) -> None:
        if self.running:
            return
        self._terminate_locked()
        executable = resolve_blender_executable(self.blender_path)
        if not executable:
            raise BlenderPreviewSessionError(
                "unsupported",
                "Blender was not found. Configure its executable in Settings.",
            )
        executable_path = Path(executable)
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            raise BlenderPreviewSessionError(
                "unsupported", "The selected Blender path is not an executable file."
            )
        command = [
            executable,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(server_path()),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise BlenderPreviewSessionError(
                "failed", f"Blender could not be launched: {error}"
            ) from error
        self._process = process
        self.start_count += 1
        self._reader = Thread(
            target=self._read_output,
            args=(process,),
            name="shotbox-blender-preview-output",
            daemon=True,
        )
        self._reader.start()
        started = monotonic()
        while monotonic() - started <= timeout_seconds:
            event = self._next_event(0.2)
            if event is not None and event.get("event") == "ready":
                self.blender_version = str(event.get("blender_version", ""))
                return
            if not self.running:
                break
        self._terminate_locked()
        raise BlenderPreviewSessionError(
            "failed", "Blender preview session did not become ready.", self.log
        )

    def _read_output(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is None:
            self._events.put({"event": "eof"})
            return
        try:
            for raw in stream:
                line = raw.rstrip("\r\n")
                if line.startswith(PROTOCOL_PREFIX):
                    try:
                        value = json.loads(line[len(PROTOCOL_PREFIX) :])
                    except json.JSONDecodeError:
                        self._log.append(line)
                    else:
                        if isinstance(value, dict):
                            self._events.put(value)
                elif line:
                    self._log.append(line)
        finally:
            self._events.put({"event": "eof"})

    def _next_event(self, timeout: float) -> dict | None:
        try:
            return self._events.get(timeout=timeout)
        except Empty:
            return None

    def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def __enter__(self) -> "BlenderPreviewSession":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
