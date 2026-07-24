from __future__ import annotations

import os
import sys
from pathlib import Path


def user_config_dir() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BRIDGE_CONFIG") or os.environ.get("UAL_HOUDINI_BRIDGE_CONFIG")
    if override:
        value = Path(override).expanduser()
        return value.parent if value.suffix else value
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "ShotBoxAssets"


def bridge_config_path() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BRIDGE_CONFIG") or os.environ.get("UAL_HOUDINI_BRIDGE_CONFIG")
    if override:
        return Path(override).expanduser()
    return user_config_dir() / "houdini_bridge.json"


def user_data_dir() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BRIDGE_DATA") or os.environ.get("UAL_HOUDINI_BRIDGE_DATA")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "ShotBoxAssets" / "houdini"


def runtime_dir() -> Path:
    override = os.environ.get("SHOTBOX_ASSETS_BRIDGE_RUNTIME") or os.environ.get("UAL_HOUDINI_BRIDGE_RUNTIME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "ShotBoxAssets" / "runtime" / "houdini"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / "shotbox-assets-houdini"
    return Path("/tmp") / f"shotbox-assets-houdini-{os.getuid()}"


def legacy_bridge_config_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "UniversalAssetLibrary" / "houdini_bridge.json"


def legacy_runtime_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "UniversalAssetLibrary" / "runtime" / "houdini"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / "ual-houdini"
    return Path("/tmp") / f"ual-houdini-{os.getuid()}"
