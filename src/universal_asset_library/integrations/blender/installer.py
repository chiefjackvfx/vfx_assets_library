from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable

from .bridge import BRIDGE_VERSION, PROTOCOL_VERSION
from .paths import config_path, data_dir


@dataclass(frozen=True, slots=True)
class BlenderInstallation:
    version: str
    executable: Path
    extension_dir: Path

    @property
    def label(self) -> str:
        return f"Blender {self.version} — {self.executable}"


@dataclass(frozen=True, slots=True)
class BlenderPluginStatus:
    installation: BlenderInstallation
    installed: bool
    version: str = ""
    current: bool = False


class BlenderPluginInstaller:
    def __init__(
        self,
        *,
        home: Path | None = None,
        configured_executable: str = "",
        payload_dir: Path | None = None,
        config_file: Path | None = None,
        package_dir: Path | None = None,
        platform_name: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.home = home or Path.home()
        self.configured_executable = configured_executable
        self.payload_dir = payload_dir
        self.config_file = config_file or config_path()
        self.package_dir = package_dir or data_dir()
        self.platform_name = platform_name or sys.platform
        self.runner = runner or subprocess.run

    def detect(self) -> list[BlenderInstallation]:
        candidates: list[Path] = []
        if self.configured_executable:
            candidates.append(Path(self.configured_executable).expanduser())
        discovered = shutil.which("blender")
        if discovered:
            candidates.append(Path(discovered))
        if self.platform_name == "win32":
            program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            candidates.extend((program_files / "Blender Foundation").glob("Blender 5.*/blender.exe"))
        else:
            candidates.extend(Path("/opt").glob("blender-5.*/blender"))
            candidates.extend((Path("/usr/bin/blender"), Path("/usr/local/bin/blender")))
        found: dict[tuple[str, str], BlenderInstallation] = {}
        for executable in candidates:
            try:
                resolved = executable.resolve(strict=True)
                version = self._version(resolved)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            short = ".".join(version.split(".")[:2])
            if short not in {"5.1", "5.2"}:
                continue
            extension = self._extension_root(short) / "shotbox_assets_bridge"
            found[(short, resolved.as_posix())] = BlenderInstallation(version, resolved, extension)
        return sorted(found.values(), key=lambda item: (item.version, item.executable.as_posix()))

    def status(self, installation: BlenderInstallation) -> BlenderPluginStatus:
        manifest = installation.extension_dir / "blender_manifest.toml"
        try:
            document = tomllib.loads(manifest.read_text(encoding="utf-8"))
            version = str(document.get("version", ""))
            installed = document.get("id") == "shotbox_assets_bridge"
        except (OSError, tomllib.TOMLDecodeError):
            return BlenderPluginStatus(installation, False)
        return BlenderPluginStatus(installation, installed, version, installed and version == BRIDGE_VERSION)

    def install(self, installations: list[BlenderInstallation]) -> list[BlenderPluginStatus]:
        if not installations:
            return []
        self.ensure_token()
        package = self._build_package()
        result: list[BlenderPluginStatus] = []
        for installation in installations:
            if not self.status(installation).current:
                self._run(installation, [
                    str(installation.executable), "--command", "extension", "install-file",
                    "-r", "user_default", "-e", str(package),
                ])
            status = self.status(installation)
            if not status.current:
                raise RuntimeError(f"Blender {installation.version} did not report the installed extension after installation.")
            result.append(status)
        return result

    def uninstall(self, installations: list[BlenderInstallation]) -> list[BlenderPluginStatus]:
        for installation in installations:
            if self.status(installation).installed:
                self._run(installation, [
                    str(installation.executable), "--command", "extension", "remove", "shotbox_assets_bridge",
                ])
        return [self.status(item) for item in installations]

    def ensure_token(self) -> str:
        try:
            token = str(json.loads(self.config_file.read_text(encoding="utf-8"))["token"])
            if len(token) >= 32:
                return token
        except (OSError, KeyError, json.JSONDecodeError):
            pass
        token = secrets.token_hex(32)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_file.with_name(f".{self.config_file.name}.tmp-{os.getpid()}")
        temporary.write_text(json.dumps({"protocol_version": PROTOCOL_VERSION, "token": token}, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.config_file)
        if os.name != "nt":
            self.config_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return token

    def _build_package(self) -> Path:
        source = self.payload_dir or Path(str(files("universal_asset_library.integrations.blender").joinpath("plugin")))
        if not source.is_dir():
            raise FileNotFoundError(f"Bundled Blender extension payload is missing: {source}")
        self.package_dir.mkdir(parents=True, exist_ok=True)
        destination = self.package_dir / f"shotbox_assets_bridge-{BRIDGE_VERSION}.zip"
        temporary = destination.with_suffix(".tmp.zip")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                    archive.write(path, path.relative_to(source).as_posix())
        os.replace(temporary, destination)
        return destination

    def _version(self, executable: Path) -> str:
        result = self.runner([str(executable), "--version"], capture_output=True, text=True, timeout=10, check=False)
        match = re.search(r"Blender\s+(\d+\.\d+(?:\.\d+)?)", (result.stdout or "") + (result.stderr or ""))
        if not match:
            raise ValueError("Could not determine Blender version")
        return match.group(1)

    def _run(self, installation: BlenderInstallation, command: list[str]) -> None:
        result = self.runner(command, capture_output=True, text=True, timeout=120, check=False)
        if result.returncode:
            details = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            raise RuntimeError(f"Blender {installation.version} extension command failed: {details or 'unknown error'}")

    def _extension_root(self, version: str) -> Path:
        if self.platform_name == "win32":
            appdata = Path(os.environ.get("APPDATA", self.home / "AppData" / "Roaming"))
            return appdata / "Blender Foundation" / "Blender" / version / "extensions" / "user_default"
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", self.home / ".config"))
        return xdg / "blender" / version / "extensions" / "user_default"
