from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from universal_asset_library.integrations.blender import (
    BlenderBridgeClient,
    BlenderBridgeError,
    BlenderInstallation,
    BlenderPluginInstaller,
)
from universal_asset_library.integrations.blender.bridge import MAX_MESSAGE_BYTES, encode_message, receive_message
from universal_asset_library.integrations import ModelExportFile, ModelExportPayload
from universal_asset_library.integrations.blender import BlenderBridgeResponse, BlenderSession


def test_protocol_framing_and_limits() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(encode_message({"hello": "世界"}))
        assert receive_message(right) == {"hello": "世界"}
    finally:
        left.close()
        right.close()
    with pytest.raises(BlenderBridgeError, match="exceeds"):
        encode_message({"value": "x" * MAX_MESSAGE_BYTES})


def test_client_discovers_authenticated_blender_session_without_signalling_process(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        os,
        "kill",
        lambda *_args: pytest.fail("Session discovery must never signal the Blender process"),
    )
    token = "b" * 64
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"token": token}), encoding="utf-8")
    registry = tmp_path / "runtime"
    registry.mkdir()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    descriptor = {
        "session_id": "blender-one",
        "port": listener.getsockname()[1],
        "pid": os.getpid(),
        "blender_version": "5.2.0",
        "blend_file": "/project/shot.blend",
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    (registry / "session-blender-one.json").write_text(json.dumps(descriptor), encoding="utf-8")

    def serve() -> None:
        connection, _address = listener.accept()
        with connection:
            request = receive_message(connection)
            assert request["token"] == token
            assert request["action"] == "ping"
            connection.sendall(encode_message({
                "ok": True,
                "request_id": request["request_id"],
                "session_id": "blender-one",
                "data": {"blender_version": "5.2.0", "blend_file": "/project/shot.blend"},
            }))
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    sessions = BlenderBridgeClient(registry_dir=registry, config_file=config, timeout=1.0).discover_sessions()
    thread.join(timeout=2)
    assert len(sessions) == 1
    assert sessions[0].label == f"Blender 5.2.0 · shot.blend · PID {os.getpid()}"


def test_extension_installer_builds_installs_and_removes_package(tmp_path) -> None:
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"fixture")
    extension_dir = (
        tmp_path / "home" / "AppData" / "Roaming" / "Blender Foundation" / "Blender" /
        "5.2" / "extensions" / "user_default" / "shotbox_assets_bridge"
    )
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(list(command))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Blender 5.2.0\n", "")
        if "install-file" in command:
            extension_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(command[-1]) as archive:
                archive.extractall(extension_dir)
        elif "remove" in command:
            shutil.rmtree(extension_dir)
        return subprocess.CompletedProcess(command, 0, "", "")

    installer = BlenderPluginInstaller(
        home=tmp_path / "home",
        configured_executable=str(executable),
        config_file=tmp_path / "config" / "bridge.json",
        package_dir=tmp_path / "packages",
        platform_name="win32",
        runner=runner,
    )
    detected = installer.detect()
    assert len(detected) == 1
    assert detected[0].version == "5.2.0"
    assert installer.install(detected)[0].current
    assert (extension_dir / "blender_manifest.toml").is_file()
    assert (extension_dir / "shotbox_assets_bridge" / "server.py").is_file()
    assert json.loads((tmp_path / "config" / "bridge.json").read_text(encoding="utf-8"))["token"]
    install_calls = len([command for command in commands if "install-file" in command])
    assert installer.install(detected)[0].current
    assert len([command for command in commands if "install-file" in command]) == install_calls
    assert all(not status.installed for status in installer.uninstall(detected))


def test_set_hdri_world_request_rejects_invalid_mode(tmp_path) -> None:
    client = BlenderBridgeClient(registry_dir=tmp_path, config_file=tmp_path / "missing")
    installation = BlenderInstallation("5.2.0", tmp_path / "blender", tmp_path / "extension")
    with pytest.raises(BlenderBridgeError, match="Unsupported"):
        client.set_hdri_world(
            None,  # type: ignore[arg-type]
            asset_id="id",
            asset_name="Sky",
            resolution="4K",
            hdri_path=tmp_path / "sky.exr",
            library_root=tmp_path,
            world_mode="replace_everything",
        )


def test_model_client_dispatches_additive_action(monkeypatch, tmp_path) -> None:
    payload = ModelExportPayload(
        "id", "Tree", "tree", tmp_path,
        ModelExportFile(tmp_path / "tree.usdc", "USDC", "4K", "LOD0"),
    )
    session = BlenderSession("s", 1, 1, "5.2", "", "now", "0.3.0", ("usd_model",))
    client = BlenderBridgeClient(registry_dir=tmp_path, config_file=tmp_path / "config")
    captured = {}

    def request(_session, action, document):
        captured.update(action=action, document=document)
        return BlenderBridgeResponse(True, "request", diagnostic="Imported")

    monkeypatch.setattr(client, "_request", request)
    assert client.import_usd_model(session, payload).ok
    assert captured["action"] == "import_usd_model"
    assert captured["document"]["model_path"].endswith("tree.usdc")
