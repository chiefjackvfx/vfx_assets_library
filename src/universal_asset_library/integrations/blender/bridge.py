from __future__ import annotations

import json
import os
import socket
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_asset_library.integrations.model_export import ModelExportPayload
from universal_asset_library.integrations.texture_export import TextureExportPayload

from .paths import config_path, runtime_dir


PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.4.4"
MAX_MESSAGE_BYTES = 64 * 1024
WORLD_MODES = ("new", "edit_current")


class BlenderBridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BlenderSession:
    id: str
    port: int
    pid: int
    blender_version: str
    blend_file: str
    started_at: str
    bridge_version: str = ""
    capabilities: tuple[str, ...] = ()
    descriptor_path: Path | None = field(default=None, compare=False)
    config_file: Path | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        scene = Path(self.blend_file).name if self.blend_file else "Untitled"
        return f"Blender {self.blender_version} · {scene} · PID {self.pid}"


@dataclass(frozen=True, slots=True)
class BlenderBridgeResponse:
    ok: bool
    request_id: str
    diagnostic: str = ""
    session_id: str = ""
    world_name: str = ""
    image_name: str = ""
    material_name: str = ""
    material_path: str = ""
    model_path: str = ""
    collection_name: str = ""
    root_object: str = ""
    assigned_targets: tuple[str, ...] = ()
    imported_targets: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "BlenderBridgeResponse":
        return cls(
            ok=bool(document.get("ok")),
            request_id=str(document.get("request_id", "")),
            diagnostic=str(document.get("diagnostic", "")),
            session_id=str(document.get("session_id", "")),
            world_name=str(document.get("world_name", "")),
            image_name=str(document.get("image_name", "")),
            material_name=str(document.get("material_name", "")),
            material_path=str(document.get("material_path", "")),
            model_path=str(document.get("model_path", "")),
            collection_name=str(document.get("collection_name", "")),
            root_object=str(document.get("root_object", "")),
            assigned_targets=tuple(str(value) for value in document.get("assigned_targets", []) if value),
            imported_targets=tuple(str(value) for value in document.get("imported_targets", []) if value),
            data=document.get("data", {}) if isinstance(document.get("data"), dict) else {},
        )


def encode_message(document: dict[str, Any]) -> bytes:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise BlenderBridgeError(f"Bridge message exceeds {MAX_MESSAGE_BYTES} bytes.")
    return struct.pack("!I", len(payload)) + payload


def receive_message(connection: socket.socket) -> dict[str, Any]:
    header = _receive_exact(connection, 4)
    length = struct.unpack("!I", header)[0]
    if length < 2 or length > MAX_MESSAGE_BYTES:
        raise BlenderBridgeError("Bridge message has an invalid length.")
    try:
        document = json.loads(_receive_exact(connection, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BlenderBridgeError("Bridge returned malformed JSON.") from error
    if not isinstance(document, dict):
        raise BlenderBridgeError("Bridge response must be a JSON object.")
    return document


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise BlenderBridgeError("Bridge connection closed before the message completed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class BlenderBridgeClient:
    def __init__(
        self,
        *,
        registry_dir: Path | None = None,
        config_file: Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.registry_dir = registry_dir or runtime_dir()
        self.config_file = config_file or config_path()
        self.timeout = timeout

    def discover_sessions(self) -> list[BlenderSession]:
        if not self.registry_dir.is_dir() or not self.config_file.is_file():
            return []
        sessions: list[BlenderSession] = []
        for descriptor in sorted(self.registry_dir.glob("session-*.json")):
            try:
                document = json.loads(descriptor.read_text(encoding="utf-8"))
                session = BlenderSession(
                    id=str(document["session_id"]),
                    port=int(document["port"]),
                    pid=int(document["pid"]),
                    blender_version=str(document.get("blender_version", "Unknown")),
                    blend_file=str(document.get("blend_file", "")),
                    started_at=str(document.get("started_at", "")),
                    bridge_version=str(document.get("bridge_version", "")),
                    capabilities=tuple(str(value) for value in document.get("capabilities", []) if value),
                    descriptor_path=descriptor,
                    config_file=self.config_file,
                )
                if session.port not in range(1, 65536) or not _process_exists(session.pid):
                    continue
                response = self._request(session, "ping", {})
                if response.ok and response.session_id == session.id:
                    sessions.append(BlenderSession(
                        id=session.id, port=session.port, pid=session.pid,
                        blender_version=str(response.data.get("blender_version", session.blender_version)),
                        blend_file=str(response.data.get("blend_file", session.blend_file)),
                        started_at=session.started_at,
                        bridge_version=str(response.data.get("bridge_version", session.bridge_version)),
                        capabilities=tuple(str(value) for value in response.data.get("capabilities", session.capabilities) if value),
                        descriptor_path=descriptor, config_file=self.config_file,
                    ))
            except (OSError, ValueError, KeyError, json.JSONDecodeError, BlenderBridgeError):
                continue
        unique = {session.id: session for session in sessions}
        return sorted(unique.values(), key=lambda item: (item.blender_version, item.pid), reverse=True)

    def set_hdri_world(
        self,
        session: BlenderSession,
        *,
        asset_id: str,
        asset_name: str,
        resolution: str,
        hdri_path: Path,
        library_root: Path,
        world_mode: str = "edit_current",
    ) -> BlenderBridgeResponse:
        if world_mode not in WORLD_MODES:
            raise BlenderBridgeError(f"Unsupported Blender World mode: {world_mode}")
        response = self._request(session, "set_hdri_world", {
            "asset_id": asset_id,
            "asset_name": asset_name,
            "resolution": resolution,
            "hdri_path": hdri_path.resolve().as_posix(),
            "library_root": library_root.resolve().as_posix(),
            "world_mode": world_mode,
        })
        if not response.ok:
            raise BlenderBridgeError(response.diagnostic or "Blender could not assign the HDRI.")
        return response

    def create_texture_material(
        self,
        session: BlenderSession,
        payload: TextureExportPayload,
    ) -> BlenderBridgeResponse:
        if session.capabilities and "texture_material" not in session.capabilities:
            raise BlenderBridgeError("Update the Blender plug-in in Settings and restart Blender.")
        response = self._request(session, "create_texture_material", payload.document())
        if not response.ok:
            raise BlenderBridgeError(response.diagnostic or "Blender could not create the texture material.")
        return response

    def import_usd_model(
        self,
        session: BlenderSession,
        payload: ModelExportPayload,
    ) -> BlenderBridgeResponse:
        if "usd_model" not in session.capabilities:
            raise BlenderBridgeError("Update the Blender plug-in in Settings and restart Blender.")
        response = self._request(session, "import_usd_model", payload.document())
        if not response.ok:
            raise BlenderBridgeError(response.diagnostic or "Blender could not import the USD model.")
        return response

    def _request(self, session: BlenderSession, action: str, payload: dict[str, Any]) -> BlenderBridgeResponse:
        token = self._load_token(session.config_file)
        request_id = str(uuid.uuid4())
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "token": token,
            "action": action,
            "payload": payload,
        }
        try:
            with socket.create_connection(("127.0.0.1", session.port), timeout=self.timeout) as connection:
                connection.settimeout(self.timeout)
                connection.sendall(encode_message(request))
                response = BlenderBridgeResponse.from_document(receive_message(connection))
        except (OSError, TimeoutError) as error:
            raise BlenderBridgeError(f"Could not reach Blender session PID {session.pid}: {error}") from error
        if response.request_id != request_id:
            raise BlenderBridgeError("Blender returned a mismatched request ID.")
        return response

    def _load_token(self, target: Path | None = None) -> str:
        try:
            token = str(json.loads((target or self.config_file).read_text(encoding="utf-8"))["token"])
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise BlenderBridgeError("The Blender Bridge is not installed or its authentication file is invalid.") from error
        if len(token) < 32:
            raise BlenderBridgeError("The Blender Bridge authentication token is invalid.")
        return token


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
