from __future__ import annotations

import json
import os
import re
import socket
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_asset_library.integrations.model_export import ModelExportPayload
from universal_asset_library.integrations.texture_export import TextureExportPayload

from .paths import bridge_config_path, legacy_bridge_config_path, legacy_runtime_dir, runtime_dir

if TYPE_CHECKING:
    from universal_asset_library.domain import LibraryHdriAsset, LibraryHdriFile, LibraryVdbAsset


PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.6.0"
MAX_MESSAGE_BYTES = 64 * 1024


class HoudiniBridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HoudiniSession:
    id: str
    port: int
    pid: int
    houdini_version: str
    hip_file: str
    started_at: str
    bridge_version: str = ""
    capabilities: tuple[str, ...] = ()
    descriptor_path: Path | None = field(default=None, compare=False)
    config_file: Path | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        scene = Path(self.hip_file).name if self.hip_file else "Untitled"
        return f"Houdini {self.houdini_version} · {scene} · PID {self.pid}"


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    protocol_version: int = PROTOCOL_VERSION

    def document(self, token: str) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "token": token,
            "action": self.action,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class BridgeResponse:
    ok: bool
    request_id: str
    diagnostic: str = ""
    session_id: str = ""
    node_path: str = ""
    prim_path: str = ""
    material_name: str = ""
    material_path: str = ""
    model_path: str = ""
    network_path: str = ""
    assigned_targets: tuple[str, ...] = ()
    imported_targets: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "BridgeResponse":
        return cls(
            ok=bool(document.get("ok")),
            request_id=str(document.get("request_id", "")),
            diagnostic=str(document.get("diagnostic", "")),
            session_id=str(document.get("session_id", "")),
            node_path=str(document.get("node_path", "")),
            prim_path=str(document.get("prim_path", "")),
            material_name=str(document.get("material_name", "")),
            material_path=str(document.get("material_path", "")),
            model_path=str(document.get("model_path", "")),
            network_path=str(document.get("network_path", "")),
            assigned_targets=tuple(str(value) for value in document.get("assigned_targets", []) if value),
            imported_targets=tuple(str(value) for value in document.get("imported_targets", []) if value),
            data=document.get("data", {}) if isinstance(document.get("data", {}), dict) else {},
        )


def encode_message(document: dict[str, Any]) -> bytes:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise HoudiniBridgeError(f"Bridge message exceeds {MAX_MESSAGE_BYTES} bytes.")
    return struct.pack("!I", len(payload)) + payload


def receive_message(connection: socket.socket) -> dict[str, Any]:
    header = _receive_exact(connection, 4)
    length = struct.unpack("!I", header)[0]
    if length < 2 or length > MAX_MESSAGE_BYTES:
        raise HoudiniBridgeError("Bridge message has an invalid length.")
    try:
        document = json.loads(_receive_exact(connection, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HoudiniBridgeError("Bridge returned malformed JSON.") from error
    if not isinstance(document, dict):
        raise HoudiniBridgeError("Bridge response must be a JSON object.")
    return document


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise HoudiniBridgeError("Bridge connection closed before the message completed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class HoudiniBridgeClient:
    def __init__(
        self,
        *,
        registry_dir: Path | None = None,
        config_file: Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.registry_dir = registry_dir or runtime_dir()
        self.config_file = config_file or bridge_config_path()
        self._locations = [(self.registry_dir, self.config_file)]
        if registry_dir is None and config_file is None:
            legacy = (legacy_runtime_dir(), legacy_bridge_config_path())
            if legacy != self._locations[0]:
                self._locations.append(legacy)
        self.timeout = timeout

    def discover_sessions(self) -> list[HoudiniSession]:
        sessions: list[HoudiniSession] = []
        seen: set[str] = set()
        for registry, config in self._locations:
            if not registry.is_dir() or not config.is_file():
                continue
            for descriptor in sorted(registry.glob("session-*.json")):
                try:
                    document = json.loads(descriptor.read_text(encoding="utf-8"))
                    session = HoudiniSession(
                        id=str(document["session_id"]),
                        port=int(document["port"]),
                        pid=int(document["pid"]),
                        houdini_version=str(document.get("houdini_version", "Unknown")),
                        hip_file=str(document.get("hip_file", "")),
                        started_at=str(document.get("started_at", "")),
                        bridge_version=str(document.get("bridge_version", "")),
                        capabilities=tuple(str(value) for value in document.get("capabilities", []) if value),
                        descriptor_path=descriptor,
                        config_file=config,
                    )
                    if session.id in seen or session.port < 1 or session.port > 65535 or not _process_exists(session.pid):
                        continue
                    response = self._request(session, BridgeRequest("ping"))
                    if response.ok and response.session_id == session.id:
                        data = response.data
                        sessions.append(HoudiniSession(
                            id=session.id, port=session.port, pid=session.pid,
                            houdini_version=str(data.get("houdini_version", session.houdini_version)),
                            hip_file=str(data.get("hip_file", session.hip_file)),
                            started_at=session.started_at,
                            bridge_version=str(data.get("bridge_version", session.bridge_version)),
                            capabilities=tuple(str(value) for value in data.get("capabilities", session.capabilities) if value),
                            descriptor_path=descriptor, config_file=config,
                        ))
                        seen.add(session.id)
                except (OSError, ValueError, KeyError, json.JSONDecodeError, HoudiniBridgeError):
                    continue
        return sorted(sessions, key=lambda item: (item.houdini_version, item.pid), reverse=True)

    def create_hdri_dome(
        self,
        session: HoudiniSession,
        *,
        asset_id: str,
        asset_name: str,
        resolution: str,
        hdri_path: Path,
        library_root: Path,
    ) -> BridgeResponse:
        response = self._request(session, BridgeRequest("create_hdri_dome", {
            "asset_id": asset_id,
            "asset_name": asset_name,
            "resolution": resolution,
            "hdri_path": hdri_path.resolve().as_posix(),
            "library_root": library_root.resolve().as_posix(),
        }))
        if not response.ok:
            raise HoudiniBridgeError(response.diagnostic or "Houdini could not create the Dome Light.")
        return response

    def create_texture_material(
        self,
        session: HoudiniSession,
        payload: TextureExportPayload,
    ) -> BridgeResponse:
        if session.capabilities and "texture_material" not in session.capabilities:
            raise HoudiniBridgeError("Update the Houdini plug-in in Settings and restart Houdini.")
        response = self._request(session, BridgeRequest("create_texture_material", payload.document()))
        if not response.ok:
            raise HoudiniBridgeError(response.diagnostic or "Houdini could not create the texture material.")
        return response

    def import_usd_model(
        self,
        session: HoudiniSession,
        payload: ModelExportPayload,
        *,
        target: str = "lop",
    ) -> BridgeResponse:
        if target not in {"lop", "sop"}:
            raise HoudiniBridgeError(f"Unsupported Houdini model target: {target}")
        if "usd_model" not in session.capabilities:
            raise HoudiniBridgeError("Update the Houdini plug-in in Settings and restart Houdini.")
        document = payload.document()
        document["target"] = target
        response = self._request(session, BridgeRequest("import_usd_model", document))
        if not response.ok:
            raise HoudiniBridgeError(response.diagnostic or "Houdini could not import the USD model.")
        return response

    def import_vdb(
        self,
        session: HoudiniSession,
        asset: "LibraryVdbAsset",
        variant: str = "",
        *,
        library_root: Path,
    ) -> BridgeResponse:
        if "vdb_file" not in session.capabilities:
            raise HoudiniBridgeError("Update the Houdini plug-in in Settings and restart Houdini.")
        label, record, path_expression = choose_vdb_variant(asset, variant)
        try:
            root = library_root.expanduser().resolve(strict=True)
            asset.asset_dir.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise HoudiniBridgeError("The managed VDB asset is outside the configured library.") from error
        for item in record.files:
            try:
                path = (asset.asset_dir / item.path).resolve(strict=True)
                path.relative_to(root)
            except (OSError, ValueError) as error:
                raise HoudiniBridgeError("A managed VDB file is unavailable or outside the configured library.") from error
            if path.suffix.casefold() != ".vdb" or not path.is_file():
                raise HoudiniBridgeError(f"Managed VDB file is missing: {path}")
        response = self._request(session, BridgeRequest("import_vdb", {
            "asset_id": asset.id,
            "asset_name": asset.name,
            "variant": label,
            "vdb_path": path_expression,
            "library_root": root.as_posix(),
            "is_sequence": record.is_sequence,
            "frame_start": record.frame_start,
            "frame_end": record.frame_end,
            "padding": record.padding,
            "missing_frames": list(record.missing_frames),
        }))
        if not response.ok:
            raise HoudiniBridgeError(response.diagnostic or "Houdini could not create the VDB File SOP.")
        return response

    def _request(self, session: HoudiniSession, request: BridgeRequest) -> BridgeResponse:
        token = self._load_token(session.config_file)
        try:
            with socket.create_connection(("127.0.0.1", session.port), timeout=self.timeout) as connection:
                connection.settimeout(self.timeout)
                connection.sendall(encode_message(request.document(token)))
                response = BridgeResponse.from_document(receive_message(connection))
        except (OSError, TimeoutError) as error:
            raise HoudiniBridgeError(f"Could not reach Houdini session PID {session.pid}: {error}") from error
        if response.request_id != request.request_id:
            raise HoudiniBridgeError("Houdini returned a mismatched request ID.")
        return response

    def _load_token(self, config_file: Path | None = None) -> str:
        target = config_file or self.config_file
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
            token = str(document["token"])
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise HoudiniBridgeError("The Houdini Bridge is not installed or its authentication file is invalid.") from error
        if len(token) < 32:
            raise HoudiniBridgeError("The Houdini Bridge authentication token is invalid.")
        return token


def choose_hdri_file(asset: "LibraryHdriAsset", resolution: str = "") -> tuple[str, "LibraryHdriFile"]:
    if not asset.resolutions:
        raise HoudiniBridgeError("This HDRI has no managed environment maps.")
    label = resolution if resolution in asset.resolutions else _default_resolution(asset.resolutions)
    files = list(asset.resolutions[label].files)
    valid = [item for item in files if item.file_format.upper() in {"EXR", "HDR"}]
    if not valid:
        raise HoudiniBridgeError(f"The {label} variant has no EXR or HDR file.")
    valid.sort(key=lambda item: (
        0 if item.file_format.upper() == "EXR" and item.preferred else
        1 if item.file_format.upper() == "EXR" else
        2 if item.file_format.upper() == "HDR" and item.preferred else 3,
        item.path.casefold(),
    ))
    return label, valid[0]


def choose_vdb_variant(asset: "LibraryVdbAsset", label: str = ""):
    if not asset.variants:
        raise HoudiniBridgeError("This VDB asset has no managed variants.")
    if label not in asset.variants:
        label = next(
            (value for preferred in ("mid", "low", "high") for value in asset.variants if value.casefold() == preferred),
            next(iter(asset.variants)),
        )
    variant = asset.variants[label]
    if not variant.files:
        raise HoudiniBridgeError(f"The {label} VDB variant contains no files.")
    first = asset.asset_dir / variant.files[0].path
    expression = first.resolve().as_posix()
    if variant.is_sequence:
        padding = max(1, variant.padding)
        frame = variant.files[0].frame
        if frame is None:
            raise HoudiniBridgeError(f"The {label} VDB sequence has no frame metadata.")
        match = re.search(rf"{frame:0{padding}d}(?=\.vdb$)", expression, re.IGNORECASE)
        if not match:
            raise HoudiniBridgeError("The managed VDB sequence filename does not contain its padded frame number.")
        expression = expression[:match.start()] + f"$F{padding}" + expression[match.end():]
    return label, variant, expression


def _default_resolution(resolutions: dict[str, Any]) -> str:
    ranked = sorted(((_resolution_value(label), label) for label in resolutions), key=lambda item: (item[0], item[1]))
    within = [item for item in ranked if 0 < item[0] <= 4]
    if within:
        return within[-1][1]
    larger = [item for item in ranked if item[0] > 4]
    return larger[0][1] if larger else ranked[-1][1]


def _resolution_value(label: str) -> int:
    digits = "".join(character for character in str(label) if character.isdigit())
    return int(digits) if digits else 0


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
