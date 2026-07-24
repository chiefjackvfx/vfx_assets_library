from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from universal_asset_library.domain import LibraryHdriAsset, LibraryHdriFile, LibraryHdriVariant
from universal_asset_library.integrations.houdini import (
    BridgeRequest,
    HoudiniBridgeClient,
    HoudiniBridgeError,
    HoudiniInstallation,
    HoudiniPluginInstaller,
    choose_hdri_file,
)
from universal_asset_library.integrations.houdini.bridge import MAX_MESSAGE_BYTES, encode_message, receive_message
from universal_asset_library.integrations import ModelExportFile, ModelExportPayload
from universal_asset_library.integrations.houdini import BridgeResponse, HoudiniSession


def _asset(tmp_path: Path) -> LibraryHdriAsset:
    variants = {
        "1K": LibraryHdriVariant("1K", 1024, 512, (
            LibraryHdriFile("maps/sky_1k.hdr", "HDR", 1, "a", True),
        )),
        "4K": LibraryHdriVariant("4K", 4096, 2048, (
            LibraryHdriFile("maps/sky_4k.hdr", "HDR", 1, "b", True),
            LibraryHdriFile("maps/sky_4k.exr", "EXR", 1, "c", False),
        )),
        "8K": LibraryHdriVariant("8K", 8192, 4096, (
            LibraryHdriFile("maps/sky_8k.exr", "EXR", 1, "d", True),
        )),
    }
    return LibraryHdriAsset(
        "asset-id", "Test Sky", "Outdoor", (), "", "", "Unknown", "", tmp_path,
        variants, None, None, "fingerprint", "2026-01-01T00:00:00+00:00", 4,
    )


def test_hdri_default_and_format_precedence(tmp_path) -> None:
    asset = _asset(tmp_path)
    label, selected = choose_hdri_file(asset)
    assert label == "4K"
    assert selected.path == "maps/sky_4k.exr"
    label, selected = choose_hdri_file(asset, "8K")
    assert (label, selected.path) == ("8K", "maps/sky_8k.exr")


def test_protocol_framing_and_limits() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(encode_message({"hello": "世界"}))
        assert receive_message(right) == {"hello": "世界"}
    finally:
        left.close()
        right.close()
    with pytest.raises(HoudiniBridgeError, match="exceeds"):
        encode_message({"value": "x" * MAX_MESSAGE_BYTES})


def test_installer_detects_installs_updates_and_uninstalls(tmp_path) -> None:
    home = tmp_path / "home"
    h21 = home / "houdini21.0"
    h22 = home / "houdini22.0"
    h21.mkdir(parents=True)
    h22.mkdir()
    config = tmp_path / "config" / "bridge.json"
    installer = HoudiniPluginInstaller(
        home=home,
        data_dir=tmp_path / "data",
        config_file=config,
        registry_dir=tmp_path / "runtime",
        platform_name="linux",
    )
    detected = installer.detect()
    assert [item.version for item in detected] == ["21.0", "22.0"]
    statuses = installer.install(detected)
    assert all(status.current for status in statuses)
    token = json.loads(config.read_text(encoding="utf-8"))["token"]
    assert len(token) == 64
    assert config.stat().st_mode & 0o077 == 0
    package = json.loads((h21 / "packages" / "shotbox_assets_bridge.json").read_text(encoding="utf-8"))
    assert package["enable"] is True
    assert package["version"] == "0.5.0"
    assert package["path"].endswith("shotbox-assets-plugin-0.5.0")
    plugin_root = Path(package["path"])
    assert (plugin_root / "python3.11libs" / "uiready.py").is_file()
    assert (plugin_root / "python3.13libs" / "uiready.py").is_file()
    assert (plugin_root / "scripts" / "python" / "ual_houdini_bridge" / "server.py").is_file()
    assert installer.install(detected)[0].current
    assert all(not status.installed for status in installer.uninstall(detected))


def test_installer_replaces_legacy_package_name(tmp_path) -> None:
    home = tmp_path / "home"
    preference = home / "houdini22.0"
    packages = preference / "packages"
    packages.mkdir(parents=True)
    legacy = packages / "ual_bridge.json"
    legacy.write_text(json.dumps({"enable": True, "version": "0.1.0"}), encoding="utf-8")
    installer = HoudiniPluginInstaller(
        home=home,
        data_dir=tmp_path / "data",
        config_file=tmp_path / "config.json",
        registry_dir=tmp_path / "runtime",
        platform_name="linux",
    )
    installation = HoudiniInstallation("22.0", preference)
    assert installer.status(installation).installed
    assert not installer.status(installation).current
    assert installer.install([installation])[0].current
    assert not legacy.exists()
    assert (packages / "shotbox_assets_bridge.json").is_file()


def test_client_discovers_authenticated_session(tmp_path) -> None:
    token = "a" * 64
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"token": token}), encoding="utf-8")
    registry = tmp_path / "runtime"
    registry.mkdir()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    descriptor = {
        "session_id": "session-one",
        "port": port,
        "pid": os.getpid(),
        "houdini_version": "22.0.368",
        "hip_file": "/project/shot.hip",
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    (registry / "session-session-one.json").write_text(json.dumps(descriptor), encoding="utf-8")

    def serve() -> None:
        connection, _address = listener.accept()
        with connection:
            request = receive_message(connection)
            assert request["token"] == token
            assert request["action"] == "ping"
            connection.sendall(encode_message({
                "ok": True,
                "request_id": request["request_id"],
                "session_id": "session-one",
                "data": {"houdini_version": "22.0.368", "hip_file": "/project/shot.hip"},
            }))
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    sessions = HoudiniBridgeClient(registry_dir=registry, config_file=config, timeout=1.0).discover_sessions()
    thread.join(timeout=2)
    assert len(sessions) == 1
    assert sessions[0].label == "Houdini 22.0.368 · shot.hip · PID " + str(os.getpid())


def test_missing_token_is_reported(tmp_path) -> None:
    client = HoudiniBridgeClient(registry_dir=tmp_path, config_file=tmp_path / "missing.json")
    with pytest.raises(HoudiniBridgeError, match="not installed"):
        client._load_token()


def test_model_client_dispatches_target(monkeypatch, tmp_path) -> None:
    payload = ModelExportPayload(
        "id", "Tree", "tree", tmp_path,
        ModelExportFile(tmp_path / "tree.usdc", "USDC", "4K", "LOD0"),
    )
    session = HoudiniSession("s", 1, 1, "22.0", "", "now", "0.4.0", ("usd_model",))
    client = HoudiniBridgeClient(registry_dir=tmp_path, config_file=tmp_path / "config")
    captured = {}

    def request(_session, bridge_request):
        captured["request"] = bridge_request
        return BridgeResponse(True, bridge_request.request_id, diagnostic="Imported")

    monkeypatch.setattr(client, "_request", request)
    assert client.import_usd_model(session, payload, target="sop").ok
    assert captured["request"].action == "import_usd_model"
    assert captured["request"].payload["target"] == "sop"
    with pytest.raises(HoudiniBridgeError, match="Unsupported"):
        client.import_usd_model(session, payload, target="obj")
