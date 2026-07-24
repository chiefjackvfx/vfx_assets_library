from __future__ import annotations

import atexit
import json
import os
import queue
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .actions import ActionError, execute
from .protocol import PROTOCOL_VERSION, encode_message, receive_message


BRIDGE_VERSION = "0.5.0"
REQUEST_TIMEOUT = 300.0
_INSTANCE = None


class _Job:
    def __init__(self, request):
        self.request = request
        self.completed = threading.Event()
        self.response = None


class BridgeServer:
    def __init__(self, hou):
        self.hou = hou
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.jobs = queue.Queue()
        self.stopping = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.socket.settimeout(0.5)
        self.port = self.socket.getsockname()[1]
        self.config_file = Path(
            os.environ.get("SHOTBOX_ASSETS_BRIDGE_CONFIG") or os.environ["UAL_HOUDINI_BRIDGE_CONFIG"]
        )
        self.runtime_dir = Path(
            os.environ.get("SHOTBOX_ASSETS_BRIDGE_RUNTIME") or os.environ["UAL_HOUDINI_BRIDGE_RUNTIME"]
        )
        self.token = self._load_token()
        self.descriptor = self.runtime_dir / f"session-{self.session_id}.json"
        self.thread = threading.Thread(target=self._listen, name="UALHoudiniBridge", daemon=True)

    def start(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._write_descriptor()
        self.hou.ui.addEventLoopCallback(self.process_jobs)
        self.thread.start()

    def stop(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        try:
            self.socket.close()
        except OSError:
            pass
        try:
            self.hou.ui.removeEventLoopCallback(self.process_jobs)
        except Exception:
            pass
        try:
            self.descriptor.unlink()
        except FileNotFoundError:
            pass

    def process_jobs(self):
        for _index in range(8):
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            request_id = str(job.request.get("request_id", ""))
            try:
                response = execute(
                    self.hou,
                    str(job.request.get("action", "")),
                    job.request.get("payload", {}),
                    self.session_id,
                )
            except Exception as error:
                response = {
                    "ok": False,
                    "session_id": self.session_id,
                    "diagnostic": str(error),
                }
            response["request_id"] = request_id
            job.response = response
            job.completed.set()

    def _listen(self):
        while not self.stopping.is_set():
            try:
                connection, _address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(REQUEST_TIMEOUT + 1.0)
                request_id = ""
                try:
                    request = receive_message(connection)
                    request_id = str(request.get("request_id", ""))
                    self._validate_request(request)
                    job = _Job(request)
                    self.jobs.put(job)
                    if not job.completed.wait(REQUEST_TIMEOUT):
                        raise TimeoutError("Houdini did not process the bridge request in time.")
                    response = job.response
                except Exception as error:
                    response = {
                        "ok": False,
                        "request_id": request_id,
                        "session_id": self.session_id,
                        "diagnostic": str(error),
                    }
                try:
                    connection.sendall(encode_message(response))
                except OSError:
                    pass

    def _validate_request(self, request):
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("Unsupported ShotBox Assets Houdini Bridge protocol version.")
        if request.get("token") != self.token:
            raise PermissionError("ShotBox Assets Houdini Bridge authentication failed.")
        if request.get("action") not in {"ping", "create_hdri_dome", "create_texture_material", "import_usd_model"}:
            raise ActionError("Unsupported bridge action.")
        if not str(request.get("request_id", "")):
            raise ValueError("Bridge request has no request ID.")
        if not isinstance(request.get("payload", {}), dict):
            raise ValueError("Bridge payload must be an object.")

    def _load_token(self):
        document = json.loads(self.config_file.read_text(encoding="utf-8"))
        token = str(document["token"])
        if len(token) < 32:
            raise ValueError("ShotBox Assets Houdini Bridge authentication token is invalid.")
        return token

    def _write_descriptor(self):
        try:
            version = self.hou.applicationVersionString()
        except Exception:
            version = "Unknown"
        try:
            hip_file = self.hou.hipFile.path()
        except Exception:
            hip_file = ""
        document = {
            "bridge_version": BRIDGE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "port": self.port,
            "pid": os.getpid(),
            "houdini_version": version,
            "hip_file": hip_file,
            "started_at": self.started_at,
            "capabilities": ["hdri", "texture_material", "usd_model"],
        }
        temporary = self.descriptor.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.descriptor)


def start():
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    import hou

    if not hou.isUIAvailable():
        return None
    _INSTANCE = BridgeServer(hou)
    _INSTANCE.start()
    atexit.register(stop)
    print(f"ShotBox Assets Houdini Bridge {BRIDGE_VERSION} listening on 127.0.0.1:{_INSTANCE.port}")
    return _INSTANCE


def stop():
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.stop()
        _INSTANCE = None
