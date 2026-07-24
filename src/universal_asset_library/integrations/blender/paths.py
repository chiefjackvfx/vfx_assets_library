from __future__ import annotations

import os
import sys
from pathlib import Path


def config_path() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BLENDER_BRIDGE_CONFIG")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "ShotBoxAssets" / "blender_bridge.json"


def data_dir() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BLENDER_BRIDGE_DATA")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "ShotBoxAssets" / "blender"


def runtime_dir() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BLENDER_BRIDGE_RUNTIME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "ShotBoxAssets" / "runtime" / "blender"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / "shotbox-assets-blender"
    return Path("/tmp") / f"shotbox-assets-blender-{os.getuid()}"
