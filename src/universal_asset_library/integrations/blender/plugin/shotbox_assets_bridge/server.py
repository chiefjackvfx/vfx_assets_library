from __future__ import annotations

import atexit
import json
import os
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import actions
from .protocol import encode_message, receive_message


PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.4.3"
REQUEST_TIMEOUT = 300.0
THREAD_JOIN_TIMEOUT = 2.0


def _config_path():
    override = os.environ.get("SHOTBOX_ASSETS_BLENDER_BRIDGE_CONFIG")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "ShotBoxAssets" / "blender_bridge.json"


def _runtime_dir():
    override = os.environ.get("SHOTBOX_ASSETS_BLENDER_BRIDGE_RUNTIME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "ShotBoxAssets" / "runtime" / "blender"
    root = os.environ.get("XDG_RUNTIME_DIR")
    return Path(root) / "shotbox-assets-blender" if root else Path("/tmp") / f"shotbox-assets-blender-{os.getuid()}"


@dataclass
class PendingRequest:
    request: dict
    deadline: float
    ready: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None


class BridgeServer:
    def __init__(self, bpy):
        self.bpy = bpy
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self.pending = queue.Queue()
        self.stopping = threading.Event()
        self._stop_lock = threading.Lock()
        self._connections_lock = threading.Lock()
        self._connections = set()
        self._workers = set()
        self._stopped = False
        self.thread = threading.Thread(target=self._listen, name="ShotBoxBlenderBridge", daemon=True)
        self.descriptor_path = _runtime_dir() / f"session-{self.session_id}.json"
        self.last_result = "Waiting for an asset from ShotBox Assets."
        self.last_asset = ""
        self.last_resolution = ""
        self.last_world = ""
        self.cached_session_data = actions.session_data(self.bpy)

    def start(self):
        try:
            self._token()
            self.descriptor_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_descriptor()
            self.thread.start()
            self.bpy.app.timers.register(self.process_pending, first_interval=0.1, persistent=True)
        except Exception:
            self.stop()
            raise

    def stop(self, unregister_timer=True):
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            self.stopping.set()
            self._fail_pending("Blender is shutting down.")
            try:
                self.listener.close()
            except OSError:
                pass
            with self._connections_lock:
                connections = tuple(self._connections)
                workers = tuple(self._workers)
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
            if unregister_timer:
                try:
                    if self.bpy.app.timers.is_registered(self.process_pending):
                        self.bpy.app.timers.unregister(self.process_pending)
                except Exception:
                    pass
            current = threading.current_thread()
            if self.thread is not current and self.thread.is_alive():
                self.thread.join(THREAD_JOIN_TIMEOUT)
            for worker in workers:
                if worker is not current and worker.is_alive():
                    worker.join(THREAD_JOIN_TIMEOUT)
            self._fail_pending("Blender is shutting down.")
            try:
                self.descriptor_path.unlink()
            except OSError:
                pass

    def process_pending(self):
        if self.stopping.is_set():
            self._fail_pending("Blender is shutting down.")
            return None
        for _index in range(8):
            try:
                pending = self.pending.get_nowait()
            except queue.Empty:
                break
            request_id = str(pending.request.get("request_id", ""))
            if time.monotonic() >= pending.deadline:
                pending.response = self._failure(request_id, "The request expired before Blender could process it.")
            else:
                try:
                    pending.response = actions.execute(
                        self.bpy,
                        pending.request["action"],
                        pending.request["payload"],
                        self.session_id,
                    )
                    pending.response["request_id"] = request_id
                    self.cached_session_data = dict(pending.response.get("data", self.cached_session_data))
                    if pending.request["action"] in {"set_hdri_world", "create_texture_material", "import_usd_model"} and pending.response.get("ok"):
                        payload = pending.request["payload"]
                        self.last_asset = str(payload.get("asset_name", ""))
                        self.last_resolution = str(payload.get("resolution", ""))
                        self.last_world = str(pending.response.get("world_name", ""))
                        self.last_result = str(pending.response.get("diagnostic", ""))
                except Exception as error:
                    self.last_result = str(error)
                    pending.response = self._failure(request_id, str(error))
            pending.ready.set()
        return None if self.stopping.is_set() else 0.1

    def _listen(self):
        while not self.stopping.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="ShotBoxBlenderBridgeClient",
                daemon=True,
            )
            with self._connections_lock:
                if self.stopping.is_set():
                    connection.close()
                    break
                self._connections.add(connection)
                self._workers.add(worker)
                worker.start()

    def _handle_connection(self, connection):
        try:
            self._handle(connection)
        finally:
            with self._connections_lock:
                self._connections.discard(connection)
                self._workers.discard(threading.current_thread())

    def _handle(self, connection):
        with connection:
            connection.settimeout(REQUEST_TIMEOUT + 1.0)
            request_id = ""
            try:
                request = receive_message(connection)
                request_id = str(request.get("request_id", ""))
                self._validate(request)
                if self.stopping.is_set():
                    raise RuntimeError("Blender is shutting down.")
                pending = PendingRequest(request, time.monotonic() + REQUEST_TIMEOUT)
                self.pending.put(pending)
                while (
                    not pending.ready.wait(0.1)
                    and not self.stopping.is_set()
                    and time.monotonic() < pending.deadline
                ):
                    pass
                if self.stopping.is_set() and not pending.ready.is_set():
                    response = self._failure(request_id, "Blender is shutting down.")
                elif not pending.ready.is_set():
                    response = self._failure(request_id, "Blender did not process the request before it timed out.")
                else:
                    response = pending.response
            except Exception as error:
                response = self._failure(request_id, str(error))
            try:
                connection.sendall(encode_message(response))
            except OSError:
                pass

    def _fail_pending(self, diagnostic):
        while True:
            try:
                pending = self.pending.get_nowait()
            except queue.Empty:
                return
            request_id = str(pending.request.get("request_id", ""))
            pending.response = self._failure(request_id, diagnostic)
            pending.ready.set()

    def _validate(self, request):
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("Unsupported ShotBox Assets Blender Bridge protocol version.")
        if request.get("token") != self._token():
            raise PermissionError("ShotBox Assets Blender Bridge authentication failed.")
        if request.get("action") not in {"ping", "set_hdri_world", "create_texture_material", "import_usd_model"}:
            raise ValueError("Unsupported Blender Bridge action.")
        if not str(request.get("request_id", "")):
            raise ValueError("Bridge request has no request ID.")
        if not isinstance(request.get("payload"), dict):
            raise ValueError("Bridge payload must be an object.")

    def _token(self):
        try:
            token = str(json.loads(_config_path().read_text(encoding="utf-8"))["token"])
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError("ShotBox Assets Blender Bridge authentication is not installed.") from error
        if len(token) < 32:
            raise RuntimeError("ShotBox Assets Blender Bridge authentication token is invalid.")
        return token

    def _write_descriptor(self):
        data = actions.session_data(self.bpy)
        document = {
            "session_id": self.session_id,
            "port": self.port,
            "pid": os.getpid(),
            "blender_version": data["blender_version"],
            "blend_file": data["blend_file"],
            "started_at": self.started_at,
            "bridge_version": BRIDGE_VERSION,
            "capabilities": ["hdri", "texture_material", "usd_model"],
        }
        temporary = self.descriptor_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.descriptor_path)

    def _failure(self, request_id, diagnostic):
        return {
            "ok": False,
            "request_id": request_id,
            "session_id": self.session_id,
            "diagnostic": diagnostic,
            "data": self.cached_session_data,
        }


_INSTANCE = None


def start(bpy):
    global _INSTANCE
    if _INSTANCE is None:
        bridge = BridgeServer(bpy)
        try:
            bridge.start()
        except Exception:
            bridge.stop()
            raise
        _INSTANCE = bridge
        print(f"ShotBox Assets Blender Bridge {BRIDGE_VERSION} listening on 127.0.0.1:{bridge.port}")
    return _INSTANCE


def stop():
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.stop()
        _INSTANCE = None


def instance():
    return _INSTANCE


def _stop_at_exit():
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.stop(unregister_timer=False)
        _INSTANCE = None


atexit.register(_stop_at_exit)
