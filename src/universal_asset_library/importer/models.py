from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Literal


DiagnosticSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    path: str = ""
    material: str = ""


@dataclass(frozen=True, slots=True)
class SourceFileSnapshot:
    relative_path: str
    size: int
    mtime_ns: int
    kind: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class SourceCompanion:
    relative_path: str
    file_format: str
    size: int


@dataclass(frozen=True, slots=True)
class ArchiveSource:
    archive_path: Path
    preview_path: Path | None
    archive_size: int
    archive_mtime_ns: int
    preview_size: int
    preview_mtime_ns: int
    file_format: str = "RAR"


@dataclass(frozen=True, slots=True)
class ScanProgress:
    phase: str
    examined_files: int
    total_files: int
    materials_found: int
    warning_count: int
    elapsed_seconds: float


class ScanCancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise ScanCancelled("Texture scan canceled.")


class ScanCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class TextureMap:
    channel: str
    relative_path: str
    file_format: str
    bit_depth: int | None = None
    color_space: str = ""
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)
    preferred: bool = False
    metadata_source: str = "filename"
    material: str = ""
    lod: str = ""


@dataclass(slots=True)
class ResolutionVariant:
    label: str
    width: int | None = None
    height: int | None = None
    maps: dict[str, list[TextureMap]] = field(default_factory=dict)

    @property
    def map_count(self) -> int:
        return sum(len(alternatives) for alternatives in self.maps.values())


@dataclass(slots=True)
class PreviewCandidate:
    relative_path: str
    width: int | None
    height: int | None
    inferred_role: str
    metadata_role: str = ""
    fallback: bool = False
    selected_roles: tuple[str, ...] = ()


@dataclass(slots=True)
class MaterialCandidate:
    source_root: Path
    provider: str = "Unknown"
    provider_id: str = ""
    name: str = ""
    category: str = "Uncategorized"
    tags: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""
    physical_size: str = ""
    resolutions: dict[str, ResolutionVariant] = field(default_factory=dict)
    previews: list[PreviewCandidate] = field(default_factory=list)
    selected_thumbnail: str = ""
    selected_hero: str = ""
    metadata_paths: list[str] = field(default_factory=list)
    extra_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    source_snapshots: dict[str, SourceFileSnapshot] = field(default_factory=dict)
    archive_source: ArchiveSource | None = None
    asset_type: str = "texture_set"

    @property
    def resolution_labels(self) -> list[str]:
        def key(label: str) -> tuple[int, str]:
            digits = "".join(char for char in label if char.isdigit())
            return (int(digits) if digits else 999, label)

        return sorted(self.resolutions, key=key)

    @property
    def map_count(self) -> int:
        return sum(variant.map_count for variant in self.resolutions.values())

    @property
    def material_key(self) -> str:
        return f"{self.source_root}|{self.asset_type}|{self.name}"

    @property
    def companions(self) -> tuple[SourceCompanion, ...]:
        return tuple(
            SourceCompanion(
                path,
                Path(path).suffix.lstrip(".").upper(),
                self.source_snapshots[path].size,
            )
            for path in self.extra_paths
            if path in self.source_snapshots
        )


@dataclass(slots=True)
class HdriFile:
    relative_path: str
    file_format: str
    preferred: bool = False


@dataclass(slots=True)
class HdriVariant:
    label: str
    width: int | None = None
    height: int | None = None
    files: list[HdriFile] = field(default_factory=list)

    @property
    def maps(self) -> dict[str, list[HdriFile]]:
        """Compatibility view used by shared validation and preflight code."""
        return {"Environment": self.files} if self.files else {}

    @property
    def map_count(self) -> int:
        return len(self.files)


@dataclass(slots=True)
class HdriCandidate(MaterialCandidate):
    resolutions: dict[str, HdriVariant] = field(default_factory=dict)
    asset_type: str = "hdri"


@dataclass(slots=True)
class ModelFile:
    relative_path: str
    file_format: str
    role: str = "mesh"
    lod: str = ""
    component: str = ""
    triangle_count: int | None = None
    preferred: bool = False
    metadata_declared: bool = False


@dataclass(slots=True)
class ModelLod:
    label: str
    triangle_count: int | None = None
    files: list[ModelFile] = field(default_factory=list)


@dataclass(slots=True)
class ModelTextureSet:
    name: str
    resolutions: dict[str, ResolutionVariant] = field(default_factory=dict)


@dataclass(slots=True)
class ModelCandidate(MaterialCandidate):
    model_files: list[ModelFile] = field(default_factory=list)
    lods: dict[str, ModelLod] = field(default_factory=dict)
    texture_sets: dict[str, ModelTextureSet] = field(default_factory=dict)
    dimensions: tuple[float, ...] = ()
    polycount: int | None = None
    excluded_paths: dict[str, str] = field(default_factory=dict)
    asset_type: str = "model"

    @property
    def usd_ready(self) -> bool:
        return any(item.file_format in {"USD", "USDA", "USDC", "USDZ"} for item in self.model_files)

    @property
    def preferred_model(self) -> ModelFile | None:
        return next((item for item in self.model_files if item.preferred), None)

    @property
    def model_file_count(self) -> int:
        return len(self.model_files)


@dataclass(frozen=True, slots=True)
class StockMediaInfo:
    container: str
    codec: str
    profile: str
    pixel_format: str
    width: int
    height: int
    frame_rate: float
    duration: float
    frame_count: int | None
    has_audio: bool
    alpha: str


@dataclass(slots=True)
class StockPreviewCandidate:
    relative_path: str
    media_info: StockMediaInfo
    compatible: bool
    match_reason: str = ""


@dataclass(frozen=True, slots=True)
class StockPreviewProfile:
    name: str = "SD 480p H.264"
    max_width: int = 854
    max_height: int = 480
    video_codec: str = "libx264"
    pixel_format: str = "yuv420p"
    crf: int = 26
    audio_codec: str = "aac"


@dataclass(slots=True)
class StockCandidate(MaterialCandidate):
    source_video: str = ""
    media_info: StockMediaInfo | None = None
    preview_candidates: list[StockPreviewCandidate] = field(default_factory=list)
    selected_preview: str = ""
    preview_policy: str = "generate"
    preview_profile: StockPreviewProfile = field(default_factory=StockPreviewProfile)
    classification_evidence: list[str] = field(default_factory=list)
    asset_type: str = "stock"

    @property
    def map_count(self) -> int:
        return 1 if self.source_video else 0

    @property
    def resolution_labels(self) -> list[str]:
        if not self.media_info:
            return []
        return [f"{self.media_info.width}×{self.media_info.height}"]


@dataclass(slots=True)
class ScanResult:
    materials: list[MaterialCandidate] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    inventory: tuple[SourceFileSnapshot, ...] = ()
    canceled: bool = False
    detected_asset_type: str = ""
    detection_reason: str = ""
    temporary_roots: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DuplicateConflict:
    material_key: str
    existing_asset_id: str
    existing_asset_name: str
    provider: str
    provider_id: str


@dataclass(slots=True)
class MaterialPreflight:
    material: MaterialCandidate
    status: str = "Ready"
    diagnostics: list[Diagnostic] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""
    archive_sha256: str = ""
    duplicate_reason: str = ""
    conflict: DuplicateConflict | None = None

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)


@dataclass(slots=True)
class PreflightResult:
    materials: list[MaterialPreflight] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    total_bytes: int = 0
    canceled: bool = False

    def for_material(self, material: MaterialCandidate) -> MaterialPreflight | None:
        return next((item for item in self.materials if item.material is material), None) or next(
            (item for item in self.materials if item.material.material_key == material.material_key), None
        )
