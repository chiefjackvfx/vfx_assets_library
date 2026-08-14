from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LibraryMap:
    channel: str
    path: str
    file_format: str
    size: int
    sha256: str
    bit_depth: int | None = None
    color_space: str = ""
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)
    preferred: bool = False
    material: str = ""
    lod: str = ""


@dataclass(frozen=True, slots=True)
class LibraryResolution:
    label: str
    width: int | None
    height: int | None
    maps: dict[str, tuple[LibraryMap, ...]]


@dataclass(frozen=True, slots=True)
class LibraryExtraFile:
    path: str
    original_path: str
    size: int
    sha256: str
    file_format: str = ""


LibraryCompanionFile = LibraryExtraFile


@dataclass(frozen=True, slots=True)
class LibraryProviderPackageFile:
    path: str
    role: str
    size: int
    sha256: str
    md5: str = ""
    reference_path: str = ""


@dataclass(frozen=True, slots=True)
class LibraryProviderPackage:
    kind: str
    resolution: str
    entry_path: str
    files: tuple[LibraryProviderPackageFile, ...]
    downloaded_at: str = ""


@dataclass(frozen=True, slots=True)
class LibraryTextureAsset:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    description: str
    author: str
    physical_size: str
    provider: str
    provider_id: str
    asset_dir: Path
    resolutions: dict[str, LibraryResolution]
    thumbnail_path: Path | None
    hero_path: Path | None
    fingerprint: str
    created_at: str
    total_size: int
    palette: tuple[str, str] = ("#69727a", "#343a40")
    extra_files: tuple[LibraryExtraFile, ...] = ()
    asset_type: str = "texture_set"
    source_metadata: tuple[str, ...] = ()
    provider_packages: tuple[LibraryProviderPackage, ...] = ()
    preview_render: dict = field(default_factory=dict)
    rating: int = 0

    @property
    def extras(self) -> tuple[LibraryExtraFile, ...]:
        return self.extra_files

    @property
    def resolution(self) -> str:
        return ", ".join(self.resolutions)

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted({channel for variant in self.resolutions.values() for channel in variant.maps}, key=str.casefold))

    @property
    def file_format(self) -> str:
        formats = {item.file_format for variant in self.resolutions.values() for maps in variant.maps.values() for item in maps}
        return "/".join(sorted(formats))

    @property
    def size_label(self) -> str:
        value = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def matches(self, query: str = "", category: str = "All", channel: str = "All") -> bool:
        haystack = " ".join((self.name, self.category, self.provider, *self.tags, *self.channels)).casefold()
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or category == self.category)
            and (channel == "All" or channel in self.channels)
        )


@dataclass(frozen=True, slots=True)
class LibraryHdriFile:
    path: str
    file_format: str
    size: int
    sha256: str
    preferred: bool = False


@dataclass(frozen=True, slots=True)
class LibraryHdriVariant:
    label: str
    width: int | None
    height: int | None
    files: tuple[LibraryHdriFile, ...]


@dataclass(frozen=True, slots=True)
class LibraryHdriAsset:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    description: str
    author: str
    provider: str
    provider_id: str
    asset_dir: Path
    resolutions: dict[str, LibraryHdriVariant]
    thumbnail_path: Path | None
    hero_path: Path | None
    fingerprint: str
    created_at: str
    total_size: int
    extra_files: tuple[LibraryExtraFile, ...] = ()
    palette: tuple[str, str] = ("#50708a", "#182631")
    asset_type: str = "hdri"
    physical_size: str = ""
    source_metadata: tuple[str, ...] = ()
    preview_render: dict = field(default_factory=dict)
    provider_packages: tuple[LibraryProviderPackage, ...] = ()
    rating: int = 0

    @property
    def extras(self) -> tuple[LibraryExtraFile, ...]:
        return self.extra_files

    @property
    def resolution(self) -> str:
        return ", ".join(self.resolutions)

    @property
    def channels(self) -> tuple[str, ...]:
        return ()

    @property
    def file_format(self) -> str:
        return "/".join(sorted({item.file_format for variant in self.resolutions.values() for item in variant.files}))

    @property
    def size_label(self) -> str:
        value = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def matches(self, query: str = "", category: str = "All", facet: str = "All") -> bool:
        haystack = " ".join((self.name, self.category, self.provider, *self.tags, self.file_format)).casefold()
        formats = {item.file_format for variant in self.resolutions.values() for item in variant.files}
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or category == self.category)
            and (facet == "All" or facet in self.resolutions or facet in formats)
        )


@dataclass(frozen=True, slots=True)
class LibraryStockMediaInfo:
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


@dataclass(frozen=True, slots=True)
class LibraryStockAsset:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    description: str
    author: str
    provider: str
    provider_id: str
    asset_dir: Path
    source_path: Path
    source_original_path: str
    source_format: str
    source_size: int
    source_sha256: str
    media_info: LibraryStockMediaInfo
    preview_path: Path
    thumbnail_path: Path
    preview_origin: str
    preview_profile: str
    thumbnail_time: float
    fingerprint: str
    created_at: str
    total_size: int
    extra_files: tuple[LibraryExtraFile, ...] = ()
    palette: tuple[str, str] = ("#46505c", "#171b20")
    asset_type: str = "stock"
    physical_size: str = ""
    hero_path: Path | None = None
    source_metadata: tuple[str, ...] = ()
    provider_packages: tuple[LibraryProviderPackage, ...] = ()
    rating: int = 0

    @property
    def extras(self) -> tuple[LibraryExtraFile, ...]:
        return self.extra_files

    @property
    def resolution(self) -> str:
        return f"{self.media_info.width}×{self.media_info.height}"

    @property
    def channels(self) -> tuple[str, ...]:
        values = ["Alpha" if self.media_info.alpha == "yes" else "Opaque"]
        if self.media_info.has_audio:
            values.append("Audio")
        return tuple(values)

    @property
    def file_format(self) -> str:
        return self.source_format

    @property
    def size_label(self) -> str:
        value = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
            value /= 1024
        return ""

    @property
    def duration_label(self) -> str:
        minutes, seconds = divmod(max(0, int(round(self.media_info.duration))), 60)
        return f"{minutes}:{seconds:02d}"

    def matches(self, query: str = "", category: str = "All", facet: str = "All") -> bool:
        values = {
            self.media_info.codec.upper(),
            self.source_format.upper(),
            self.resolution,
            "Alpha" if self.media_info.alpha == "yes" else "Opaque",
            "Audio" if self.media_info.has_audio else "Silent",
        }
        haystack = " ".join((self.name, self.category, self.provider, *self.tags, *values)).casefold()
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or category == self.category)
            and (facet == "All" or facet in values)
        )


@dataclass(frozen=True, slots=True)
class LibraryModelFile:
    path: str
    original_path: str
    file_format: str
    role: str
    lod: str
    component: str
    triangle_count: int | None
    preferred: bool
    size: int
    sha256: str
    resolution: str = ""
    origin: str = "imported"
    registered_at: str = ""
    available: bool = True
    validation: dict = field(default_factory=dict)
    dependencies: tuple[LibraryExtraFile, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryModelTextureSet:
    name: str
    resolutions: dict[str, LibraryResolution]


@dataclass(frozen=True, slots=True)
class LibraryUsdDerivative:
    entry_path: str
    source_path: str
    source_sha256: str
    forward_axis: str
    up_axis: str
    blender_version: str
    generated_at: str
    dependencies: tuple[LibraryExtraFile, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryModelAsset:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    description: str
    author: str
    physical_size: str
    provider: str
    provider_id: str
    asset_dir: Path
    model_files: tuple[LibraryModelFile, ...]
    texture_sets: dict[str, LibraryModelTextureSet]
    thumbnail_path: Path | None
    hero_path: Path | None
    fingerprint: str
    created_at: str
    total_size: int
    dimensions: tuple[float, ...] = ()
    polycount: int | None = None
    extra_files: tuple[LibraryExtraFile, ...] = ()
    source_metadata: tuple[str, ...] = ()
    excluded_files: tuple[tuple[str, str], ...] = ()
    provider_packages: tuple[LibraryProviderPackage, ...] = ()
    usd_derivative: LibraryUsdDerivative | None = None
    palette: tuple[str, str] = ("#67588b", "#29243a")
    asset_type: str = "model"
    rating: int = 0

    @property
    def extras(self) -> tuple[LibraryExtraFile, ...]:
        return self.extra_files

    @property
    def preferred_model(self) -> LibraryModelFile | None:
        return next((item for item in self.model_files if item.preferred), None)

    @property
    def usd_ready(self) -> bool:
        return any(
            item.available and item.file_format in {"USD", "USDA", "USDC", "USDZ"}
            for item in self.model_files
        )

    @property
    def usd_path(self) -> Path | None:
        preferred = self.preferred_model
        if (
            preferred and preferred.available
            and preferred.file_format in {"USD", "USDA", "USDC", "USDZ"}
        ):
            return self.asset_dir / preferred.path
        item = next((
            value for value in self.model_files
            if value.available and value.file_format in {"USD", "USDA", "USDC", "USDZ"}
        ), None)
        return self.asset_dir / item.path if item else None

    @property
    def needs_rescan(self) -> bool:
        return any(item.origin == "manual" and not item.available for item in self.model_files)

    @property
    def lods(self) -> tuple[str, ...]:
        return tuple(sorted({item.lod for item in self.model_files if item.lod}, key=_lod_key))

    @property
    def resolution(self) -> str:
        if self.lods:
            return ", ".join(self.lods)
        resolutions = {label for texture_set in self.texture_sets.values() for label in texture_set.resolutions}
        return ", ".join(sorted(resolutions, key=_resolution_key)) or "No LOD"

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted({
            channel
            for texture_set in self.texture_sets.values()
            for variant in texture_set.resolutions.values()
            for channel in variant.maps
        }, key=str.casefold))

    @property
    def file_format(self) -> str:
        return "/".join(sorted({item.file_format for item in self.model_files}))

    @property
    def size_label(self) -> str:
        value = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def matches(self, query: str = "", category: str = "All", facet: str = "All") -> bool:
        status = "USD Ready" if self.usd_ready else "No USD"
        haystack = " ".join((
            self.name, self.category, self.provider, *self.tags,
            self.file_format, *self.lods, status, *self.channels,
        )).casefold()
        formats = {item.file_format for item in self.model_files}
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or category == self.category)
            and (facet == "All" or facet in formats or facet in self.lods or facet == status)
        )


def _lod_key(label: str) -> tuple[int, str]:
    digits = "".join(value for value in label if value.isdigit())
    return int(digits) if digits else 999, label


def _resolution_key(label: str) -> tuple[int, str]:
    digits = "".join(value for value in label if value.isdigit())
    return int(digits) if digits else 999, label


@dataclass(frozen=True, slots=True)
class LibraryVdbFile:
    path: str
    original_path: str
    size: int
    sha256: str
    frame: int | None = None
    padding: int = 0


@dataclass(frozen=True, slots=True)
class LibraryVdbVariant:
    label: str
    files: tuple[LibraryVdbFile, ...]
    is_sequence: bool = False
    frame_start: int | None = None
    frame_end: int | None = None
    padding: int = 0
    missing_frames: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryVdbAsset:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    description: str
    author: str
    provider: str
    provider_id: str
    asset_dir: Path
    variants: dict[str, LibraryVdbVariant]
    thumbnail_path: Path | None
    hero_path: Path | None
    preview_path: Path | None
    fingerprint: str
    created_at: str
    total_size: int
    extra_files: tuple[LibraryExtraFile, ...] = ()
    source_metadata: tuple[str, ...] = ()
    palette: tuple[str, str] = ("#70869b", "#263340")
    asset_type: str = "vdb"
    physical_size: str = ""
    rating: int = 0

    @property
    def extras(self) -> tuple[LibraryExtraFile, ...]:
        return self.extra_files

    @property
    def resolution(self) -> str:
        order = {"low": 0, "mid": 1, "high": 2}
        return ", ".join(sorted(self.variants, key=lambda label: (order.get(label.casefold(), 99), label.casefold())))

    @property
    def channels(self) -> tuple[str, ...]:
        return ()

    @property
    def file_format(self) -> str:
        return "VDB"

    @property
    def is_sequence(self) -> bool:
        return any(variant.is_sequence for variant in self.variants.values())

    @property
    def size_label(self) -> str:
        value = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def matches(self, query: str = "", category: str = "All", facet: str = "All") -> bool:
        modes = {"Sequence" if item.is_sequence else "Static" for item in self.variants.values()}
        values = {*self.variants, *modes, "VDB"}
        haystack = " ".join((self.name, self.category, self.provider, *self.tags, *values)).casefold()
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or category == self.category)
            and (facet == "All" or facet in values)
        )
