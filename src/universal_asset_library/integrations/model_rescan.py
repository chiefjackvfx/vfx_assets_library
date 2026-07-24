from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from time import monotonic
from typing import Callable

from universal_asset_library.domain import LibraryModelAsset
from universal_asset_library.previews.hdri_renderer import resolve_blender_executable
from .model_conversion import validate_model_conversion_blender


MODEL_FORMATS = {
    ".usd": "USD",
    ".usda": "USDA",
    ".usdc": "USDC",
    ".usdz": "USDZ",
    ".fbx": "FBX",
    ".obj": "OBJ",
    ".abc": "ABC",
    ".gltf": "GLTF",
    ".glb": "GLB",
    ".blend": "BLEND",
    ".ma": "MA",
    ".mb": "MB",
}
USD_FORMATS = {"USD", "USDA", "USDC", "USDZ"}
TEMP_SUFFIXES = {".tmp", ".temp", ".part", ".bak", ".autosave"}


@dataclass(frozen=True, slots=True)
class ModelUsdValidation:
    valid: bool
    blender_version: str = ""
    mesh_count: int = 0
    material_count: int = 0
    up_axis: str = ""
    dependencies: tuple[str, ...] = ()
    dependency_records: tuple[tuple[str, int, str], ...] = ()
    diagnostics: tuple[str, ...] = ()
    diagnostic: str = ""

    def document(self) -> dict:
        return {
            "status": "valid" if self.valid else "invalid",
            "blender_version": self.blender_version,
            "mesh_count": self.mesh_count,
            "material_count": self.material_count,
            "up_axis": self.up_axis,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class ModelRescanItem:
    path: str
    status: str
    file_format: str
    origin: str
    role: str
    lod: str
    component: str
    size: int
    sha256: str
    preferred: bool = False
    mutable: bool = False
    validation: ModelUsdValidation | None = None
    diagnostic: str = ""

    @property
    def selectable(self) -> bool:
        return self.status == "new" or (
            self.origin == "manual" and self.status in {"changed", "missing"}
        )

    @property
    def valid_for_apply(self) -> bool:
        return not self.validation or self.validation.valid


@dataclass(frozen=True, slots=True)
class ModelAssetRescan:
    asset_id: str
    manifest_updated_at: str
    items: tuple[ModelRescanItem, ...]
    warnings: tuple[str, ...] = ()

    def item(self, path: str) -> ModelRescanItem | None:
        return next((item for item in self.items if item.path == path), None)


@dataclass(frozen=True, slots=True)
class ModelRescanSelection:
    add_paths: tuple[str, ...] = ()
    refresh_paths: tuple[str, ...] = ()
    remove_paths: tuple[str, ...] = ()
    preferred_path: str = ""


@dataclass(frozen=True, slots=True)
class ModelAssetRescanUpdate:
    asset: LibraryModelAsset
    added: tuple[str, ...] = ()
    refreshed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def inventory_model_asset(
    asset: LibraryModelAsset,
    manifest_updated_at: str,
    *,
    blender_path: str = "",
    progress: Callable[[str], None] | None = None,
    cancel_token=None,
) -> ModelAssetRescan:
    root = asset.asset_dir.resolve(strict=True)
    models = (root / "models").resolve(strict=True)
    if models != root / "models" or not models.is_dir():
        raise ValueError("The managed model folder is unavailable or unsafe.")
    usd = root / "usd"
    if usd.exists():
        usd = usd.resolve(strict=True)
        if usd != root / "usd" or not usd.is_dir():
            raise ValueError("The managed USD folder is unavailable or unsafe.")
    discovered: dict[str, tuple[Path, int, str, str, str, str]] = {}
    roots = (models, usd) if usd.is_dir() else (models,)
    paths = sorted(
        (path for container in roots for path in container.rglob("*")),
        key=lambda value: str(value).casefold(),
    )
    candidates = [
        path for path in paths
        if _discoverable_model(path, models, usd)
    ]
    for index, path in enumerate(candidates, start=1):
        _check_cancel(cancel_token)
        relative = path.relative_to(root).as_posix()
        if progress:
            progress(f"Hashing {path.name} · {index}/{len(candidates)}")
        digest = _sha256(path)
        file_format = MODEL_FORMATS[path.suffix.casefold()]
        lod = _lod_label(path.stem)
        discovered[relative] = (
            path, path.stat().st_size, digest, file_format,
            _model_role(path.stem, file_format, lod), lod,
        )

    registered = {item.path: item for item in asset.model_files}
    items: list[ModelRescanItem] = []
    warnings: list[str] = []
    for relative, values in discovered.items():
        path, size, digest, file_format, role, lod = values
        record = registered.get(relative)
        origin = record.origin if record else "manual"
        status = "new" if record is None else (
            "unchanged" if record.sha256 == digest and record.size == size else "changed"
        )
        mutable = record is None or origin == "manual"
        diagnostic = ""
        if status == "changed" and not mutable:
            diagnostic = f"{origin.title()} files cannot be refreshed by Rescan asset."
            warnings.append(f"{relative}: {diagnostic}")
        validation = None
        if file_format in USD_FORMATS and status in {"new", "changed"} and mutable:
            if progress:
                progress(f"Validating {path.name} in Blender")
            validation = validate_usd_file(
                path, root, blender_path=blender_path,
                cancel_token=cancel_token,
            )
            if not validation.valid:
                diagnostic = validation.diagnostic
        items.append(ModelRescanItem(
            relative,
            status,
            file_format,
            origin,
            record.role if record else role,
            record.lod if record else lod,
            record.component if record else _component_name(path.stem, lod),
            size,
            digest,
            record.preferred if record else False,
            mutable,
            validation,
            diagnostic,
        ))

    for relative, record in registered.items():
        if relative in discovered:
            continue
        path = root / relative
        if path.is_file():
            size = path.stat().st_size
            digest = _sha256(path)
            status = "unchanged" if record.sha256 == digest and record.size == size else "changed"
        else:
            size = 0
            digest = ""
            status = "missing"
        mutable = record.origin == "manual"
        diagnostic = ""
        if not mutable and status != "unchanged":
            diagnostic = f"{record.origin.title()} files cannot be changed by Rescan asset."
            warnings.append(f"{relative}: {diagnostic}")
        items.append(ModelRescanItem(
            relative,
            status,
            record.file_format,
            record.origin,
            record.role,
            record.lod,
            record.component,
            size,
            digest,
            record.preferred,
            mutable,
            None,
            diagnostic,
        ))
    items.sort(key=lambda item: (item.status == "unchanged", item.path.casefold()))
    return ModelAssetRescan(
        asset.id, manifest_updated_at, tuple(items), tuple(warnings),
    )


def validate_usd_file(
    path: Path,
    asset_dir: Path,
    *,
    blender_path: str = "",
    cancel_token=None,
    timeout_seconds: int = 300,
) -> ModelUsdValidation:
    executable = resolve_blender_executable(blender_path)
    valid, diagnostic, version = validate_model_conversion_blender(executable)
    if not valid:
        return ModelUsdValidation(False, version, diagnostic=diagnostic)
    driver = Path(__file__).resolve().with_name("blender_model_rescan_driver.py")
    with tempfile.TemporaryDirectory(prefix="shotbox-model-rescan-") as temporary:
        result_path = Path(temporary) / "result.json"
        command = [
            executable,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(driver),
            "--",
            "--usd",
            str(path),
            "--asset",
            str(asset_dir),
            "--result",
            str(result_path),
        ]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as error:
            return ModelUsdValidation(False, version, diagnostic=str(error))
        started = monotonic()
        output = ""
        while True:
            if getattr(cancel_token, "cancelled", False):
                _stop_process(process)
                cancel_token.check()
                raise RuntimeError("Model asset rescan was canceled.")
            if monotonic() - started > timeout_seconds:
                _stop_process(process)
                return ModelUsdValidation(
                    False, version, diagnostic="Blender USD validation timed out."
                )
            try:
                tail, _ = process.communicate(timeout=0.2)
            except subprocess.TimeoutExpired as pending:
                if pending.output:
                    output = (
                        pending.output if isinstance(pending.output, str)
                        else pending.output.decode(errors="replace")
                    )
                continue
            output = tail or output
            break
        try:
            document = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = {}
        if process.returncode or not document.get("ok"):
            message = str(document.get("diagnostic", "")).strip()
            if not message:
                message = next(
                    (line.strip() for line in reversed(output.splitlines()) if line.strip()),
                    f"Blender exited with code {process.returncode}.",
                )
            return ModelUsdValidation(False, version, diagnostic=message)
        dependencies = tuple(str(value) for value in document.get("dependencies", []))
        dependency_records = []
        for relative in dependencies:
            dependency = (asset_dir / relative).resolve(strict=True)
            dependency.relative_to(asset_dir.resolve(strict=True))
            dependency_records.append((
                relative, dependency.stat().st_size, _sha256(dependency),
            ))
        return ModelUsdValidation(
            True,
            str(document.get("blender_version", version)),
            int(document.get("mesh_count", 0)),
            int(document.get("material_count", 0)),
            str(document.get("up_axis", "")),
            dependencies,
            tuple(dependency_records),
            tuple(str(value) for value in document.get("diagnostics", []) if value),
        )


def _discoverable_model(path: Path, models: Path, usd: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        container = usd if path.is_relative_to(usd) else models
        relative = path.relative_to(container)
        resolved = path.resolve(strict=True)
        resolved.relative_to(container)
    except (OSError, ValueError):
        return False
    if resolved != path.absolute():
        return False
    file_format = MODEL_FORMATS.get(path.suffix.casefold(), "")
    return (
        bool(file_format)
        and (
            file_format in USD_FORMATS
            if container == usd
            else file_format not in USD_FORMATS
        )
        and path.suffix.casefold() not in TEMP_SUFFIXES
        and not any(part.startswith(".") for part in relative.parts)
    )


def _check_cancel(token) -> None:
    if token is not None:
        token.check()


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _lod_label(stem: str) -> str:
    match = re.search(r"(?i)(?:^|[_-])lod[_-]?(\d+)(?:[_-]|$)", stem)
    return f"LOD{int(match.group(1))}" if match else ""


def _component_name(stem: str, lod: str) -> str:
    value = re.sub(r"(?i)(?:[_-])lod[_-]?\d+", "", stem)
    return re.sub(r"[_-]+", " ", value).strip().title()


def _model_role(stem: str, file_format: str, lod: str) -> str:
    lowered = stem.casefold()
    if file_format in {"BLEND", "MA", "MB"}:
        return "scene"
    if re.search(r"(?:^|[_-])hair(?:[_-]|$)", lowered):
        return "component"
    if re.search(r"(?:^|[_-])high(?:[_-]|$)", lowered):
        return "high"
    return "lod" if lod else "mesh"


def _stop_process(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
