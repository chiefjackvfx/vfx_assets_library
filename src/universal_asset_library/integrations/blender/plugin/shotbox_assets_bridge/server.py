from __future__ import annotations

import json
import os
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import actions
from .protocol import MAX_MESSAGE_BYTES, decode_payload, encode_message


PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.4.4"
REQUEST_TIMEOUT = 300.0
TIMER_INTERVAL = 0.05
MAX_ACCEPTS_PER_TICK = 8
MAX_READS_PER_TICK = 8


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


@dataclass(eq=False)
class ClientConnection:
    connection: socket.socket
    deadline: float
    buffer: bytearray = field(default_factory=bytearray)
    expected_size: int | None = None
    response: bytes = b""
    sent: int = 0
    request_id: str = ""


class BridgeServer:
    """A non-blocking localhost bridge polled only from Blender's main thread."""

    def __init__(self, bpy):
        self.bpy = bpy
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.setblocking(False)
        self.port = self.listener.getsockname()[1]
        self.clients: set[ClientConnection] = set()
        self._stopping = False
        self._stopped = False
        self._timer_callback = self.process_pending
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
            self.bpy.app.timers.register(
                self._timer_callback,
                first_interval=TIMER_INTERVAL,
                persistent=True,
            )
        except Exception:
            self.stop()
            raise

    def stop(self, unregister_timer=True):
        if self._stopped:
            return
        self._stopping = True
        self._stopped = True
        for client in tuple(self.clients):
            self._drop_client(client)
        try:
            self.listener.close()
        except OSError:
            pass
        if unregister_timer:
            try:
                if self.bpy.app.timers.is_registered(self._timer_callback):
                    self.bpy.app.timers.unregister(self._timer_callback)
            except Exception:
                pass
        try:
            self.descriptor_path.unlink()
        except OSError:
            pass

    def process_pending(self):
        if self._stopping:
            return None
        self._accept_connections()
        for client in tuple(self.clients):
            try:
                self._poll_client(client)
            except Exception as error:
                self._queue_response(client, self._failure(client.request_id, str(error)))
                self._flush_response(client)
        return None if self._stopping else TIMER_INTERVAL

    def _accept_connections(self):
        for _index in range(MAX_ACCEPTS_PER_TICK):
            try:
                connection, _address = self.listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                if self._stopping:
                    return
                raise
            connection.setblocking(False)
            self.clients.add(ClientConnection(connection, time.monotonic() + REQUEST_TIMEOUT))

    def _poll_client(self, client):
        if client.response:
            self._flush_response(client)
            return
        if time.monotonic() >= client.deadline:
            self._queue_response(client, self._failure(client.request_id, "The request expired before Blender could process it."))
            self._flush_response(client)
            return

        for _index in range(MAX_READS_PER_TICK):
            try:
                chunk = client.connection.recv(MAX_MESSAGE_BYTES + 4)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                self._drop_client(client)
                return
            client.buffer.extend(chunk)
            self._read_header(client)
            if client.expected_size is not None and len(client.buffer) >= client.expected_size:
                self._handle_request(client)
                self._flush_response(client)
                return

    def _read_header(self, client):
        if client.expected_size is not None or len(client.buffer) < 4:
            return
        client.expected_size = struct.unpack("!I", bytes(client.buffer[:4]))[0]
        del client.buffer[:4]
        if client.expected_size < 2 or client.expected_size > MAX_MESSAGE_BYTES:
            raise ValueError("Bridge message has an invalid length.")

    def _handle_request(self, client):
        if client.expected_size is None:
            return
        if len(client.buffer) != client.expected_size:
            raise ValueError("Bridge connection sent unexpected trailing data.")
        request = decode_payload(bytes(client.buffer))
        client.request_id = str(request.get("request_id", ""))
        self._validate(request)
        try:
            response = actions.execute(
                self.bpy,
                request["action"],
                request["payload"],
                self.session_id,
            )
            response["request_id"] = client.request_id
            self.cached_session_data = dict(response.get("data", self.cached_session_data))
            if request["action"] in {"set_hdri_world", "create_texture_material", "import_usd_model"} and response.get("ok"):
                payload = request["payload"]
                self.last_asset = str(payload.get("asset_name", ""))
                self.last_resolution = str(payload.get("resolution", ""))
                self.last_world = str(response.get("world_name", ""))
                self.last_result = str(response.get("diagnostic", ""))
        except Exception as error:
            self.last_result = str(error)
            response = self._failure(client.request_id, str(error))
        self._queue_response(client, response)

    def _queue_response(self, client, response):
        if not client.response:
            client.response = encode_message(response)

    def _flush_response(self, client):
        if not client.response or client not in self.clients:
            return
        try:
            sent = client.connection.send(client.response[client.sent:])
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._drop_client(client)
            return
        if sent <= 0:
            self._drop_client(client)
            return
        client.sent += sent
        if client.sent >= len(client.response):
            self._drop_client(client)

    def _drop_client(self, client):
        self.clients.discard(client)
        try:
            client.connection.close()
        except OSError:
            pass

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
