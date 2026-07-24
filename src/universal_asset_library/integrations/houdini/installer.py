from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .bridge import BRIDGE_VERSION, PROTOCOL_VERSION
from .paths import bridge_config_path, runtime_dir, user_data_dir


@dataclass(frozen=True, slots=True)
class HoudiniInstallation:
    version: str
    preference_dir: Path

    @property
    def label(self) -> str:
        return f"Houdini {self.version} — {self.preference_dir}"


@dataclass(frozen=True, slots=True)
class PluginStatus:
    installation: HoudiniInstallation
    installed: bool
    version: str = ""
    current: bool = False


class HoudiniPluginInstaller:
    def __init__(
        self,
        *,
        home: Path | None = None,
        data_dir: Path | None = None,
        config_file: Path | None = None,
        registry_dir: Path | None = None,
        payload_dir: Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.home = home or Path.home()
        self.data_dir = data_dir or user_data_dir()
        self.config_file = config_file or bridge_config_path()
        self.registry_dir = registry_dir or runtime_dir()
        self.payload_dir = payload_dir
        self.platform_name = platform_name or sys.platform

    def detect(self) -> list[HoudiniInstallation]:
        if self.platform_name == "win32":
            preference_root = self.home / "Documents"
        else:
            preference_root = self.home
        found: list[HoudiniInstallation] = []
        for version in ("21.0", "22.0"):
            preference = preference_root / f"houdini{version}"
            if preference.is_dir() or self._version_is_installed(version):
                found.append(HoudiniInstallation(version, preference))
        return found

    def _version_is_installed(self, version: str) -> bool:
        if self.platform_name == "win32":
            program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            root = program_files / "Side Effects Software"
            return any(root.glob(f"Houdini {version}*")) if root.is_dir() else False
        root = Path("/opt")
        return any(root.glob(f"hfs{version}.*")) if root.is_dir() else False

    def status(self, installation: HoudiniInstallation) -> PluginStatus:
        package = installation.preference_dir / "packages" / "shotbox_assets_bridge.json"
        legacy_package = installation.preference_dir / "packages" / "ual_bridge.json"
        if not package.is_file() and legacy_package.is_file():
            package = legacy_package
        try:
            document = json.loads(package.read_text(encoding="utf-8"))
            version = str(document.get("version", ""))
            installed = bool(document.get("enable", True))
        except (OSError, json.JSONDecodeError):
            return PluginStatus(installation, False)
        return PluginStatus(installation, installed, version, installed and version == BRIDGE_VERSION)

    def install(self, installations: list[HoudiniInstallation]) -> list[PluginStatus]:
        if not installations:
            return []
        self.ensure_token()
        destination = self.data_dir / f"shotbox-assets-plugin-{BRIDGE_VERSION}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.payload_dir or Path(str(files("universal_asset_library.integrations.houdini").joinpath("plugin")))
        if not source.is_dir():
            raise FileNotFoundError(f"Bundled Houdini plugin payload is missing: {source}")
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        result: list[PluginStatus] = []
        for installation in installations:
            package = installation.preference_dir / "packages" / "shotbox_assets_bridge.json"
            package.parent.mkdir(parents=True, exist_ok=True)
            document = {
                "enable": True,
                "version": BRIDGE_VERSION,
                "path": destination.resolve().as_posix(),
                "env": [
                    {"SHOTBOX_ASSETS_BRIDGE_CONFIG": self.config_file.resolve().as_posix()},
                    {"SHOTBOX_ASSETS_BRIDGE_RUNTIME": self.registry_dir.resolve().as_posix()},
                ],
            }
            _atomic_json(package, document)
            try:
                (installation.preference_dir / "packages" / "ual_bridge.json").unlink()
            except FileNotFoundError:
                pass
            result.append(self.status(installation))
        return result

    def uninstall(self, installations: list[HoudiniInstallation]) -> list[PluginStatus]:
        for installation in installations:
            for filename in ("shotbox_assets_bridge.json", "ual_bridge.json"):
                package = installation.preference_dir / "packages" / filename
                try:
                    package.unlink()
                except FileNotFoundError:
                    pass
        return [self.status(item) for item in installations]

    def ensure_token(self) -> str:
        try:
            document = json.loads(self.config_file.read_text(encoding="utf-8"))
            token = str(document["token"])
            if len(token) >= 32:
                return token
        except (OSError, KeyError, json.JSONDecodeError):
            pass
        token = secrets.token_hex(32)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.config_file, {"protocol_version": PROTOCOL_VERSION, "token": token})
        if os.name != "nt":
            self.config_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return token


def _atomic_json(path: Path, document: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
