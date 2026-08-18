from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
from threading import Event
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QImageReader, QPainter, QPen

from universal_asset_library.domain import (
    LibraryExtraFile,
    LibraryHdriAsset,
    LibraryHdriFile,
    LibraryHdriVariant,
    LibraryMap,
    LibraryModelAsset,
    LibraryModelFile,
    LibraryModelTextureSet,
    LibraryUsdDerivative,
    LibraryProviderPackage,
    LibraryProviderPackageFile,
    LibraryResolution,
    LibraryStockAsset,
    LibraryStockMediaInfo,
    LibraryTextureAsset,
    LibraryVdbAsset,
    LibraryVdbFile,
    LibraryVdbVariant,
)
from universal_asset_library.importer.models import (
    Diagnostic,
    DuplicateConflict,
    HdriCandidate,
    MaterialCandidate,
    ModelCandidate,
    MaterialPreflight,
    PreflightResult,
    ScanCancelled,
    SourceFileSnapshot,
    StockCandidate,
    TextureMap,
    VdbCandidate,
)
from universal_asset_library.importer.adapters import normalize_channel
from universal_asset_library.importer.stock_taxonomy import StockTaxonomyStore, classify_stock_path
from universal_asset_library.importer.stock_scanner import (
    StockProbeError,
    probe_video,
    resolve_ffprobe,
)
from universal_asset_library.categories import CategoryCatalog, CategoryConfigStore
from universal_asset_library.previews import (
    BlenderPreviewSession,
    HdriPreviewRequest,
    HdriPreviewResult,
    StockPreviewError,
    TexturePreviewMap,
    TexturePreviewRequest,
    TexturePreviewResult,
    VdbPreviewRequest,
    VdbPreviewResult,
    render_hdri_preview,
    render_texture_preview,
    render_vdb_preview,
    select_hdri_variant,
    select_texture_maps,
    select_texture_variant,
    generate_midpoint_thumbnail,
    generate_stock_preview,
    resolve_ffmpeg,
)
from universal_asset_library.previews.hdri_renderer import select_hdri_file
from universal_asset_library.integrations.model_conversion import (
    ModelConversionError,
    ModelConversionResult,
    prepare_model_conversion,
    run_model_conversion,
)
from universal_asset_library.integrations.model_rescan import (
    ModelAssetRescan,
    ModelAssetRescanUpdate,
    ModelRescanSelection,
    USD_FORMATS as RESCAN_USD_FORMATS,
    inventory_model_asset,
)
from .polyhaven import (
    PolyHavenClient,
    PolyHavenDownloadPlan,
    PolyHavenDownloadResult,
    PolyHavenError,
    PolyHavenOptions,
    build_download_plan,
    cached_catalog,
    load_metadata_documents,
    options_from_catalog,
    resolve_polyhaven_slug,
)


SCHEMA_VERSION = 1
LIBRARY_SCHEMA_VERSION = 1
NAMING_VERSION = 2
LAYOUT_VERSION = 2
MODEL_LAYOUT_VERSION = 6
STOCK_LAYOUT_VERSION = 2
VDB_PREVIEW_CACHE_ROOT = Path(__file__).resolve().parents[3] / "temp_cache"
SPACE_CUSHION = 64 * 1024 * 1024
AssetRecord = (
    LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset | LibraryVdbAsset
)


class LibraryError(RuntimeError):
    pass


class LibraryLockedError(LibraryError):
    pass


class ImportCancelled(LibraryError):
    pass


class StaleSourceError(LibraryError):
    pass


@dataclass(frozen=True, slots=True)
class ImportProgress:
    material: str
    file: str
    completed_bytes: int
    total_bytes: int
    phase: str = ""
    completed_items: int = 0
    total_items: int = 0


@dataclass(slots=True)
class ImportSummary:
    imported: list[AssetRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


@dataclass(frozen=True, slots=True)
class HdriPreviewUpdate:
    asset: LibraryHdriAsset
    render: HdriPreviewResult


@dataclass(frozen=True, slots=True)
class TexturePreviewUpdate:
    asset: LibraryTextureAsset
    render: TexturePreviewResult


@dataclass(frozen=True, slots=True)
class VdbPreviewUpdate:
    asset: LibraryVdbAsset
    render: VdbPreviewResult


@dataclass(frozen=True, slots=True)
class ModelConversionUpdate:
    asset: LibraryModelAsset
    conversion: ModelConversionResult


@dataclass(frozen=True, slots=True)
class RepairProgress:
    asset: str
    completed_assets: int
    total_assets: int


@dataclass(slots=True)
class RepairSummary:
    renamed: list[LibraryTextureAsset] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


@dataclass(slots=True)
class LibraryUpdateSummary:
    updated: list[LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset | LibraryVdbAsset] = field(default_factory=list)
    valid: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


@dataclass(slots=True)
class StockClassificationSummary:
    updated: list[LibraryStockAsset] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


@dataclass(frozen=True, slots=True)
class LibraryRecoveryState:
    staging_directories: tuple[str, ...]
    lock_owner: str = ""
    lock_host: str = ""
    lock_created_at: str = ""
    lock_is_local_stale: bool = False


@dataclass(frozen=True, slots=True)
class AssetMetadataUpdate:
    name: str
    category: str
    tags: tuple[str, ...] = ()
    author: str = ""
    description: str = ""
    physical_size: str = ""


@dataclass(frozen=True, slots=True)
class AssetMetadataPatch:
    """Narrow metadata changes that preserve fields not named by the patch."""

    category: str | None = None
    add_tags: tuple[str, ...] = ()
    rating: int | None = None


@dataclass(frozen=True, slots=True)
class MetadataPatchOutcome:
    """An updated asset together with its exact published manifest path."""

    asset: AssetRecord
    manifest_path: Path


class CancelToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise ImportCancelled("Import canceled by user.")


class LibraryRepository:
    def __init__(
        self,
        root: str | Path,
        *,
        blender_path: str = "",
        render_hdri_previews: bool = True,
        hdri_template_path: str | Path | None = None,
        render_texture_previews: bool = True,
        texture_template_path: str | Path | None = None,
        save_texture_preview_blend: bool = False,
        ffmpeg_path: str = "",
        houdini_path: str = "",
        vdb_template_path: str | Path | None = None,
        vdb_parallel_renders: int = 2,
    ) -> None:
        self.root = Path(root).expanduser().absolute()
        self.last_warnings: list[str] = []
        self.blender_path = blender_path
        self.render_hdri_previews = render_hdri_previews
        self.hdri_template_path = Path(hdri_template_path) if hdri_template_path else None
        self.render_texture_previews = render_texture_previews
        self.texture_template_path = (
            Path(texture_template_path) if texture_template_path else None
        )
        self.save_texture_preview_blend = bool(save_texture_preview_blend)
        self.ffmpeg_path = ffmpeg_path
        self.houdini_path = houdini_path
        self.vdb_template_path = Path(vdb_template_path) if vdb_template_path else None
        self.vdb_parallel_renders = max(1, min(4, int(vdb_parallel_renders)))

    def initialize(self) -> None:
        if not self.root.exists() or not self.root.is_dir():
            raise LibraryError("The configured library path is not an existing folder.")
        if not os.access(self.root, os.W_OK):
            raise LibraryError("The configured library folder is not writable.")
        control = self.root / ".ual"
        (control / "staging").mkdir(parents=True, exist_ok=True)
        (self.root / "textures").mkdir(parents=True, exist_ok=True)
        (self.root / "atlases").mkdir(parents=True, exist_ok=True)
        (self.root / "hdris").mkdir(parents=True, exist_ok=True)
        (self.root / "models").mkdir(parents=True, exist_ok=True)
        (self.root / "vdbs").mkdir(parents=True, exist_ok=True)
        (self.root / "stock").mkdir(parents=True, exist_ok=True)
        config = control / "library.json"
        if not config.exists():
            _atomic_json(config, {
                "schema_version": LIBRARY_SCHEMA_VERSION,
                "id": str(uuid4()),
                "created_at": _utc_now(),
            })
        CategoryConfigStore(self.root).ensure_defaults()

    def list_assets(
        self, asset_type: str | None = None
    ) -> list[LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset]:
        self.last_warnings = []
        assets: list[LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset] = []
        manifests = (
            _asset_manifest_paths_for_type(self.root, asset_type)
            if asset_type is not None
            else _asset_manifest_paths(self.root)
        )
        for manifest_path in sorted(manifests, key=lambda path: str(path).casefold()):
            try:
                document = json.loads(manifest_path.read_text(encoding="utf-8"))
                asset = _asset_from_manifest(document, manifest_path.parent)
                if asset_type is None or asset.asset_type == asset_type:
                    assets.append(asset)
                else:
                    self.last_warnings.append(
                        f"Could not load {manifest_path}: manifest type "
                        f"{asset.asset_type!r} does not match {asset_type!r}"
                    )
            except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as error:
                self.last_warnings.append(f"Could not load {manifest_path}: {error}")
        assets.sort(key=lambda asset: asset.name.casefold())
        return assets

    def list_assets_for_type(
        self, asset_type: str
    ) -> list[LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset]:
        """Scan only the canonical container for one asset type."""
        return self.list_assets(asset_type)

    def manifest_paths_for_type(self, asset_type: str) -> list[Path]:
        """Discover manifests only inside one canonical asset container."""
        return _asset_manifest_paths_for_type(self.root, asset_type)

    def load_asset_manifest(
        self, manifest_path: str | Path, *, expected_type: str | None = None
    ) -> AssetRecord:
        path = Path(manifest_path).absolute()
        document = json.loads(path.read_text(encoding="utf-8"))
        asset = _asset_from_manifest(document, path.parent)
        if expected_type is not None and asset.asset_type != expected_type:
            raise ValueError(
                f"Manifest type {asset.asset_type!r} does not match {expected_type!r}"
            )
        return asset

    def list_texture_assets(self) -> list[LibraryTextureAsset]:
        return [
            asset for asset in self.list_assets_for_type("texture_set")
            if isinstance(asset, LibraryTextureAsset) and asset.asset_type == "texture_set"
        ]

    def list_atlas_assets(self) -> list[LibraryTextureAsset]:
        return [
            asset for asset in self.list_assets_for_type("atlas")
            if isinstance(asset, LibraryTextureAsset) and asset.asset_type == "atlas"
        ]

    def list_hdri_assets(self) -> list[LibraryHdriAsset]:
        return [asset for asset in self.list_assets_for_type("hdri") if isinstance(asset, LibraryHdriAsset)]

    def list_model_assets(self) -> list[LibraryModelAsset]:
        return [asset for asset in self.list_assets_for_type("model") if isinstance(asset, LibraryModelAsset)]

    def list_stock_assets(self) -> list[LibraryStockAsset]:
        return [asset for asset in self.list_assets_for_type("stock") if isinstance(asset, LibraryStockAsset)]

    def list_vdb_assets(self) -> list[LibraryVdbAsset]:
        return [asset for asset in self.list_assets_for_type("vdb") if isinstance(asset, LibraryVdbAsset)]

    def classify_uncategorized_stock(
        self,
        progress: Callable[[RepairProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> StockClassificationSummary:
        token = cancel_token or CancelToken()
        self.initialize()
        taxonomy = StockTaxonomyStore(self.root).ensure_defaults()
        manifests = _stock_manifest_paths(self.root)
        summary = StockClassificationSummary()
        with _ImportLock(self.root / ".ual" / "import.lock"):
            for index, manifest_path in enumerate(manifests):
                if token.cancelled:
                    summary.canceled = True
                    break
                display_name = manifest_path.stem
                try:
                    document = json.loads(manifest_path.read_text(encoding="utf-8"))
                    display_name = str(document.get("name", display_name))
                    original_document = json.loads(json.dumps(document))
                    asset = _asset_from_manifest(document, manifest_path.parent)
                    if not isinstance(asset, LibraryStockAsset):
                        raise ValueError("Expected a Stock manifest")
                    if asset.category.casefold() != "uncategorized":
                        summary.skipped.append(asset.name)
                        continue
                    source = document.get("source", {})
                    original_path = str(source.get("original_path", "")).strip()
                    evidence_path = original_path or asset.source_path.name
                    classification = classify_stock_path(evidence_path, taxonomy)
                    document["category"] = classification.category
                    document.pop("categories", None)
                    document["tags"] = list(_clean_tags(
                        document.get("tags", ()),
                        classification.tags,
                    ))
                    document["updated_at"] = _utc_now()
                    if (
                        int(document.get("layout_version", 1)) >= STOCK_LAYOUT_VERSION
                        and manifest_path.name != "asset.json"
                    ):
                        updated = self._update_flat_stock_metadata(
                            manifest_path, document, original_document
                        ).asset
                    else:
                        _asset_from_manifest(document, manifest_path.parent)
                        _atomic_json(manifest_path, document)
                        updated = _asset_from_manifest(document, manifest_path.parent)
                    if not isinstance(updated, LibraryStockAsset):
                        raise ValueError("Updated manifest did not produce a Stock asset")
                    summary.updated.append(updated)
                except Exception as error:
                    summary.failed[str(manifest_path)] = str(error)
                if progress:
                    progress(RepairProgress(
                        display_name, index + 1, len(manifests)
                    ))
        return summary

    def update_asset_metadata(
        self, asset_id: str, update: AssetMetadataUpdate
    ) -> AssetRecord:
        self.initialize()
        with _ImportLock(self.root / ".ual" / "import.lock"):
            return self._update_asset_metadata_locked(asset_id, update).asset

    def patch_asset_metadata(
        self, asset_id: str, patch: AssetMetadataPatch
    ) -> AssetRecord:
        """Apply narrow metadata changes against the latest manifest."""
        with self.metadata_patch_batch((asset_id,)) as batch:
            return batch.patch(asset_id, patch).asset

    def metadata_patch_batch(
        self,
        asset_ids: Iterable[str],
        manifest_hints: Mapping[str, str | Path] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> MetadataPatchBatch:
        """Create one locked, indexed session for a sequence of metadata patches."""
        return MetadataPatchBatch(
            self,
            tuple(dict.fromkeys(str(asset_id) for asset_id in asset_ids)),
            manifest_hints or {},
            cancel_token or CancelToken(),
        )

    def _update_asset_metadata_locked(
        self,
        asset_id: str,
        update: AssetMetadataUpdate,
        *,
        manifest: tuple[Path, dict] | None = None,
        category_catalog: CategoryCatalog | None = None,
    ) -> MetadataPatchOutcome:
        name = update.name.strip()
        category = re.sub(r"\s+", " ", update.category.strip())
        if not name:
            raise LibraryError("Material name is required.")
        if not category:
            raise LibraryError("Material category is required.")
        manifest_path, document = manifest or self._manifest_by_id(asset_id)
        asset_type = str(document.get("type", "texture_set"))
        catalog = category_catalog or CategoryConfigStore(self.root).load(asset_type)
        canonical_category = catalog.canonical_name(category)
        if canonical_category is None:
            raise LibraryError(
                f"Category {category!r} is not defined in {catalog.asset_type} categories."
            )
        category = canonical_category
        original_document = json.loads(json.dumps(document))
        source_dir = manifest_path.parent
        document["name"] = name
        document["slug"] = _slugify(name)
        document["category"] = category
        document.pop("categories", None)
        document["tags"] = list(_clean_tags(update.tags))
        document["author"] = update.author.strip()
        document["description"] = update.description.strip()
        document["physical_size"] = update.physical_size.strip()
        document["updated_at"] = _utc_now()
        if (
            document.get("type") == "stock"
            and int(document.get("layout_version", 1)) >= STOCK_LAYOUT_VERSION
            and manifest_path.name != "asset.json"
        ):
            return self._update_flat_stock_metadata(
                manifest_path, document, original_document
            )
        container = _asset_container(str(document.get("type", "")))
        target_parent = self.root / container / _slugify(category)
        destination = source_dir
        if source_dir.parent.resolve() != target_parent.resolve():
            target_parent.mkdir(parents=True, exist_ok=True)
            destination = _unique_asset_destination(target_parent, source_dir.name)

        # Validate while the payload is still at its original location.
        # Every payload path is asset-relative and remains valid after the
        # complete directory is moved.
        updated = _asset_from_manifest(document, source_dir)
        if destination == source_dir:
            _atomic_json(manifest_path, document)
            return MetadataPatchOutcome(updated, manifest_path)

        moved = False
        try:
            os.replace(source_dir, destination)
            moved = True
            _sync_directory(source_dir.parent)
            _sync_directory(destination.parent)
            _atomic_json(destination / "asset.json", document)
            updated = _asset_from_manifest(document, destination)
        except Exception as error:
            if moved and destination.exists():
                try:
                    # Restore the old sidecar before returning the directory
                    # to its original category, even if publication failed
                    # after the new sidecar had already been installed.
                    _atomic_json(destination / "asset.json", original_document)
                    os.replace(destination, source_dir)
                    _sync_directory(source_dir.parent)
                    _sync_directory(destination.parent)
                except Exception as rollback_error:
                    raise LibraryError(
                        "The category move failed and could not be rolled back: "
                        f"{rollback_error}"
                    ) from error
            raise
        container_root = self.root / container
        if source_dir.parent != container_root:
            try:
                source_dir.parent.rmdir()
            except OSError:
                pass
        return MetadataPatchOutcome(updated, destination / "asset.json")

    def _update_flat_stock_metadata(
        self, manifest_path: Path, document: dict, original_document: dict
    ) -> MetadataPatchOutcome:
        source_parent = manifest_path.parent
        category = str(document["category"])
        target_parent = self.root / "stock" / _slugify(category)
        old_token = manifest_path.stem
        old_name = str(original_document.get("name", ""))
        new_name = str(document.get("name", ""))
        requested_token = (
            _filename_token(new_name)
            if _filename_token(old_name) != _filename_token(new_name)
            else old_token
        )
        old_relatives = _stock_manifest_file_paths(original_document)
        ignored = (
            {manifest_path.name, *old_relatives}
            if source_parent.resolve() == target_parent.resolve() else set()
        )
        token = _unique_stock_token(target_parent, requested_token, ignored)
        if source_parent.resolve() == target_parent.resolve() and token == old_token:
            _asset_from_manifest(document, source_parent)
            _atomic_json(manifest_path, document)
            updated = _asset_from_manifest(document, source_parent)
            if not isinstance(updated, LibraryStockAsset):
                raise LibraryError("Updated manifest did not produce a Stock asset.")
            return MetadataPatchOutcome(updated, manifest_path)

        required = sum(
            (source_parent / relative).stat().st_size for relative in old_relatives
        )
        if shutil.disk_usage(self.root).free < required + SPACE_CUSHION:
            raise LibraryError(
                "The library does not have enough free space for a safe Stock rename."
            )
        operation = uuid4().hex
        stage = self.root / ".ual" / "staging" / f"stock-edit-{operation}"
        backup_manifest = stage / ".previous-manifest.json"
        stage.mkdir(parents=True, exist_ok=False)
        updated = json.loads(json.dumps(document))
        used: set[str] = set()

        def copy_managed(relative: str, filename: str) -> str:
            old_path = _safe_asset_file(source_parent, relative)
            destination_name = _unique_stock_filename(filename, used)
            destination = stage / destination_name
            shutil.copy2(old_path, destination)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            return destination_name

        source_record = updated["source"]
        old_source = str(source_record["path"])
        source_record["path"] = copy_managed(
            old_source, f"{token}{Path(old_source).suffix}"
        )
        preview_record = updated["preview"]
        old_preview = str(preview_record["video"])
        old_thumbnail = str(preview_record["thumbnail"])
        preview_record["video"] = copy_managed(
            old_preview, f"{token}_Preview{Path(old_preview).suffix}"
        )
        preview_record["thumbnail"] = copy_managed(
            old_thumbnail, f"{token}_Thumbnail{Path(old_thumbnail).suffix}"
        )
        metadata: list[str] = []
        for value in updated.get("source_metadata", []):
            old = str(value)
            original = _stock_companion_name(Path(old).name, old_token, "Metadata")
            metadata.append(copy_managed(old, f"{token}_Metadata_{original}"))
        updated["source_metadata"] = metadata
        for record in updated.get("extra_files", []):
            old = str(record["path"])
            original = _stock_companion_name(Path(old).name, old_token, "Extra")
            record["path"] = copy_managed(old, f"{token}_Extra_{original}")
        updated["slug"] = _slugify(new_name)
        updated["layout_version"] = STOCK_LAYOUT_VERSION
        new_manifest_name = f"{token}.json"
        _atomic_json(stage / new_manifest_name, updated)
        staged_asset = _asset_from_manifest(updated, stage)
        if not isinstance(staged_asset, LibraryStockAsset):
            raise LibraryError("Edited manifest did not produce a Stock asset.")

        target_parent.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        hidden = False
        try:
            os.replace(manifest_path, backup_manifest)
            hidden = True
            staged_manifest = stage / new_manifest_name
            staged_files = sorted(
                (
                    path for path in stage.iterdir()
                    if path not in {backup_manifest, staged_manifest}
                ),
                key=lambda path: path.name.casefold(),
            )
            for staged in (*staged_files, staged_manifest):
                destination = target_parent / staged.name
                if destination.exists():
                    raise LibraryError(
                        f"Stock rename destination already exists: {destination.name}"
                    )
                os.replace(staged, destination)
                published.append(destination)
            _sync_directory(target_parent)
            result = _asset_from_manifest(updated, target_parent)
            if not isinstance(result, LibraryStockAsset):
                raise LibraryError("Published manifest did not produce a Stock asset.")
            for relative in old_relatives:
                old_path = source_parent / relative
                if old_path not in published:
                    old_path.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)
            stage.rmdir()
            _sync_directory(source_parent)
            if source_parent != target_parent:
                try:
                    source_parent.rmdir()
                except OSError:
                    pass
            return MetadataPatchOutcome(result, target_parent / new_manifest_name)
        except Exception:
            for path in reversed(published):
                path.unlink(missing_ok=True)
            if hidden and backup_manifest.exists():
                os.replace(backup_manifest, manifest_path)
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            _sync_directory(source_parent)
            _sync_directory(target_parent)
            raise

    def polyhaven_options(
        self, asset_id: str, *, client: PolyHavenClient | None = None
    ) -> PolyHavenOptions:
        manifest_path, document = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(document, manifest_path.parent)
        if not isinstance(asset, (LibraryTextureAsset, LibraryHdriAsset, LibraryModelAsset)) or "poly haven" not in asset.provider.casefold():
            raise LibraryError("Online Poly Haven downloads are only available for Poly Haven textures, HDRIs, and models.")
        documents = load_metadata_documents(asset.asset_dir, asset.source_metadata)
        try:
            slug = resolve_polyhaven_slug(asset.provider_id, documents)
        except PolyHavenError as error:
            raise LibraryError(str(error)) from error
        expected_type = 1 if isinstance(asset, LibraryTextureAsset) else 0 if isinstance(asset, LibraryHdriAsset) else 2
        try:
            catalog = (client or PolyHavenClient()).fetch_catalog(slug, expected_type)
            from_cache = False
        except PolyHavenError as live_error:
            catalog = cached_catalog(documents)
            if catalog is None:
                raise LibraryError(str(live_error)) from live_error
            from_cache = True
        return options_from_catalog(slug, catalog, asset.asset_type, from_cache=from_cache)

    def prepare_polyhaven_download(
        self,
        asset_id: str,
        kind: str,
        resolution: str,
        *,
        options: PolyHavenOptions | None = None,
        client: PolyHavenClient | None = None,
    ) -> PolyHavenDownloadPlan:
        manifest_path, document = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(document, manifest_path.parent)
        selected = options or self.polyhaven_options(asset_id, client=client)
        preferred_formats: dict[str, str] = {}
        if isinstance(asset, LibraryTextureAsset):
            for variant in asset.resolutions.values():
                for channel, records in variant.maps.items():
                    preferred = next((item for item in records if item.preferred), records[0] if records else None)
                    if preferred:
                        preferred_formats.setdefault(channel, preferred.file_format)
        try:
            return build_download_plan(
                asset_id=asset_id,
                asset_type=asset.asset_type,
                slug=selected.slug,
                kind=kind,
                resolution=resolution,
                catalog=selected.catalog,
                manifest_updated_at=str(document.get("updated_at", "")),
                manifest_fingerprint=str(document.get("fingerprint", "")),
                preferred_formats=preferred_formats,
                from_cache=selected.from_cache,
                asset_name=asset.name,
            )
        except PolyHavenError as error:
            raise LibraryError(str(error)) from error

    def install_polyhaven_download(
        self,
        plan: PolyHavenDownloadPlan,
        *,
        client: PolyHavenClient | None = None,
        progress: Callable[[ImportProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> PolyHavenDownloadResult:
        self.initialize()
        token = cancel_token or CancelToken()
        downloader = client or PolyHavenClient()
        initial_path, initial_document = self._manifest_by_id(plan.asset_id)
        if (str(initial_document.get("updated_at", "")) != plan.manifest_updated_at
                or str(initial_document.get("fingerprint", "")) != plan.manifest_fingerprint):
            raise StaleSourceError("The asset changed after the Poly Haven download was planned.")
        if _polyhaven_already_installed(initial_document, initial_path.parent, plan):
            asset = _asset_from_manifest(initial_document, initial_path.parent)
            return PolyHavenDownloadResult(asset, plan.kind, plan.resolution, 0, True)
        required = plan.total_size + SPACE_CUSHION
        if plan.kind == "usd":
            required += _directory_size(initial_path.parent)
        if shutil.disk_usage(self.root).free < required:
            raise LibraryError("The library does not have enough free space for this Poly Haven download.")
        operation = uuid4().hex
        stage = self.root / ".ual" / "staging" / f"polyhaven-{operation}"
        payload = stage / "payload"
        records: list[dict] = []
        completed = 0
        try:
            if plan.kind == "maps":
                existing_target = initial_path.parent / _polyhaven_target_root(plan)
                if existing_target.is_dir():
                    shutil.copytree(existing_target, payload)
            for remote in plan.files:
                token.check()
                relative = _polyhaven_staged_path(plan, remote)
                destination = payload / relative
                def advanced(count: int, item=remote) -> None:
                    nonlocal completed
                    completed += count
                    if progress:
                        progress(ImportProgress(plan.slug, item.source_path, completed, plan.total_size))
                size, digest = downloader.download(remote, destination, progress=advanced, cancel=token.check)
                records.append({
                    "path": relative.as_posix(),
                    "source_path": remote.source_path,
                    "role": remote.role,
                    "channel": remote.channel,
                    "format": remote.file_format.upper(),
                    "normal_convention": remote.normal_convention,
                    "packed_channels": remote.packed_channels,
                    "preferred": remote.preferred,
                    "size": size,
                    "md5": remote.md5,
                    "sha256": digest,
                })
            token.check()
            with _ImportLock(self.root / ".ual" / "import.lock"):
                manifest_path, current = self._manifest_by_id(plan.asset_id)
                if (str(current.get("updated_at", "")) != plan.manifest_updated_at
                        or str(current.get("fingerprint", "")) != plan.manifest_fingerprint):
                    raise StaleSourceError("The asset changed while downloading; check online resolutions again.")
                asset_dir = manifest_path.parent
                target_relative = _polyhaven_target_root(plan)
                target = asset_dir / target_relative
                previous = stage / "previous"
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    os.replace(target, previous)
                published = False
                try:
                    os.replace(payload, target)
                    published = True
                    _apply_polyhaven_manifest(current, plan, records, target_relative)
                    current["updated_at"] = _utc_now()
                    _asset_from_manifest(current, asset_dir)
                    _atomic_json(manifest_path, current)
                    metadata_dir = asset_dir / "metadata"
                    try:
                        _atomic_json(metadata_dir / "polyhaven-files.json", {"files": plan.catalog})
                    except OSError:
                        pass
                    _sync_directory(target.parent)
                except Exception:
                    if published and target.exists():
                        shutil.rmtree(target)
                    if previous.exists():
                        os.replace(previous, target)
                    _sync_directory(target.parent)
                    raise
                if previous.exists():
                    shutil.rmtree(previous)
                updated = _asset_from_manifest(current, asset_dir)
                if plan.kind == "usd" and isinstance(updated, LibraryModelAsset):
                    updated = self._flatten_model_layout(asset_dir, current, token)
                return PolyHavenDownloadResult(updated, plan.kind, plan.resolution, completed, False)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def render_texture_preview(
        self,
        asset_id: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
        preview_session: BlenderPreviewSession | None = None,
    ) -> TexturePreviewUpdate:
        """Render outside the library lock and atomically publish a texture preview."""
        self.initialize()
        token = cancel_token or CancelToken()
        manifest_path, original = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(original, manifest_path.parent)
        if not isinstance(asset, LibraryTextureAsset) or asset.asset_type != "texture_set":
            raise LibraryError(
                "Shader preview rendering is only available for texture-set assets."
            )
        selected = select_texture_variant(asset.resolutions)
        if not selected:
            raise LibraryError("The texture asset has no renderable resolution.")
        resolution, variant = selected
        selected_maps = select_texture_maps(variant)
        if "Base Color" not in selected_maps:
            raise LibraryError(
                "The selected texture resolution has no Base Color map."
            )
        preview_maps = tuple(
            TexturePreviewMap(
                channel=channel,
                path=asset.asset_dir / record.path,
                source_relative=record.path,
                sha256=record.sha256,
                color_space=record.color_space,
                normal_convention=record.normal_convention,
                packed_channels=dict(record.packed_channels),
            )
            for channel, record in selected_maps.items()
        )
        manifest_stamp = str(original.get("updated_at", ""))
        manifest_fingerprint = str(original.get("fingerprint", ""))
        render_stage = (
            self.root / ".ual" / "staging" / f"texture-preview-{uuid4()}"
        )
        request = TexturePreviewRequest(
            output_dir=render_stage,
            asset_name=asset.name,
            resolution=resolution,
            maps=preview_maps,
            blender_path=self.blender_path,
            template_path=self.texture_template_path,
            save_blend_file=self.save_texture_preview_blend,
        )
        try:
            result = render_texture_preview(
                request,
                progress=progress,
                cancel_token=token,
                session=preview_session,
            )
            if (
                result.status != "ready"
                or not result.thumbnail_path
                or not result.hero_path
            ):
                return TexturePreviewUpdate(
                    self._record_texture_render_status(asset_id, result.metadata),
                    result,
                )
            token.check()
            with _ImportLock(self.root / ".ual" / "import.lock"):
                current_path, current = self._manifest_by_id(asset_id)
                if (
                    current_path != manifest_path
                    or str(current.get("updated_at", "")) != manifest_stamp
                    or str(current.get("fingerprint", "")) != manifest_fingerprint
                ):
                    raise StaleSourceError(
                        "The texture manifest changed while its preview was rendering; "
                        "the result was not published."
                    )
                current_asset = _asset_from_manifest(current, current_path.parent)
                if (
                    not isinstance(current_asset, LibraryTextureAsset)
                    or current_asset.asset_type != "texture_set"
                ):
                    raise LibraryError("The asset is no longer a texture set.")
                current_variant = current_asset.resolutions.get(resolution)
                if current_variant is None:
                    raise StaleSourceError(
                        "The selected texture resolution is no longer available."
                    )
                for preview_map in preview_maps:
                    current_record = next(
                        (
                            item
                            for item in current_variant.maps.get(
                                preview_map.channel, ()
                            )
                            if item.path == preview_map.source_relative
                        ),
                        None,
                    )
                    if (
                        current_record is None
                        or current_record.sha256 != preview_map.sha256
                        or preview_map.sha256
                        and _sha256_path(preview_map.path) != preview_map.sha256
                    ):
                        raise StaleSourceError(
                            f"The selected {preview_map.channel} map changed while "
                            "its preview was rendering; regenerate from the current file."
                        )
                preview_dir = current_asset.asset_dir / "previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                hero_target = preview_dir / result.hero_path.name
                thumb_target = preview_dir / result.thumbnail_path.name
                blend_target = (
                    preview_dir / result.blend_path.name
                    if result.blend_path is not None
                    else None
                )
                rollback: dict[Path, Path | None] = {}
                for index, target in enumerate(
                    dict.fromkeys(
                        target
                        for target in (
                            hero_target,
                            thumb_target,
                            blend_target,
                        )
                        if target is not None
                    )
                ):
                    if target.exists():
                        backup = (
                            render_stage
                            / f"published-backup-{index}{target.suffix}"
                        )
                        shutil.copyfile(target, backup)
                        rollback[target] = backup
                    else:
                        rollback[target] = None
                try:
                    _publish_preview_files(
                        result.hero_path,
                        hero_target,
                        result.thumbnail_path,
                        thumb_target,
                    )
                    if result.blend_path is not None and blend_target is not None:
                        os.replace(result.blend_path, blend_target)
                        result.blend_path = blend_target
                        result.metadata["debug_blend"] = (
                            blend_target.relative_to(
                                current_asset.asset_dir
                            ).as_posix()
                        )
                    previews = current.setdefault("previews", {})
                    previews["hero"] = hero_target.relative_to(
                        current_asset.asset_dir
                    ).as_posix()
                    previews["thumbnail"] = thumb_target.relative_to(
                        current_asset.asset_dir
                    ).as_posix()
                    previews["render"] = result.metadata
                    current["updated_at"] = _utc_now()
                    _asset_from_manifest(current, current_asset.asset_dir)
                    _atomic_json(current_path, current)
                except Exception:
                    for target, backup in rollback.items():
                        if backup is None:
                            target.unlink(missing_ok=True)
                        else:
                            os.replace(backup, target)
                    _sync_directory(preview_dir)
                    raise
                result.hero_path = hero_target
                result.thumbnail_path = thumb_target
                updated = _asset_from_manifest(current, current_asset.asset_dir)
                if not isinstance(updated, LibraryTextureAsset):
                    raise LibraryError(
                        "The rendered manifest did not produce a texture asset."
                    )
                return TexturePreviewUpdate(updated, result)
        finally:
            if render_stage.exists():
                shutil.rmtree(render_stage, ignore_errors=True)

    def render_vdb_preview(
        self,
        asset_id: str,
        variant_label: str,
        *,
        density_scale: int = 100,
        mode: str = "still",
        progress: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> VdbPreviewUpdate:
        """Render frame one outside the lock and atomically publish a VDB still."""
        self.initialize()
        token = cancel_token or CancelToken()
        manifest_path, original = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(original, manifest_path.parent)
        if not isinstance(asset, LibraryVdbAsset):
            raise LibraryError("VDB preview rendering is only available for VDB assets.")
        if mode not in {"still", "turntable"}:
            raise LibraryError(f"Unknown VDB preview mode: {mode}")
        label = _selected_vdb_variant_label(asset, variant_label)
        if not 10 <= density_scale <= 500:
            raise LibraryError("VDB preview density must be between 10 and 500.")
        variant = asset.variants[label]
        source_record, path_expression = _vdb_frame_one_source(asset, variant)
        source_path = asset.asset_dir / source_record.path
        if not source_record.sha256:
            raise LibraryError("The selected VDB has no managed source hash; reimport it before rendering.")
        manifest_stamp = str(original.get("updated_at", ""))
        manifest_fingerprint = str(original.get("fingerprint", ""))
        VDB_PREVIEW_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        render_stage = VDB_PREVIEW_CACHE_ROOT / f"vdb-preview-{uuid4()}"
        request = VdbPreviewRequest(
            source_path=source_path,
            vdb_path=path_expression,
            source_relative=source_record.path,
            source_sha256=source_record.sha256,
            output_dir=render_stage,
            asset_name=asset.name,
            variant=label,
            density_scale=density_scale,
            mode=mode,
            ffmpeg_path=self.ffmpeg_path,
            houdini_path=self.houdini_path,
            template_path=self.vdb_template_path,
            frame=1,
            timeout_seconds=7200 if mode == "turntable" else 1800,
            parallel_processes=self.vdb_parallel_renders,
        )
        try:
            result = render_vdb_preview(
                request,
                progress=progress,
                cancel_token=token,
            )
            if (
                result.status != "ready"
                or result.thumbnail_path is None
                or result.hero_path is None
            ):
                return VdbPreviewUpdate(
                    self._record_vdb_render_status(asset_id, result.metadata),
                    result,
                )
            token.check()
            with _ImportLock(self.root / ".ual" / "import.lock"):
                current_path, current = self._manifest_by_id(asset_id)
                if (
                    current_path != manifest_path
                    or str(current.get("updated_at", "")) != manifest_stamp
                    or str(current.get("fingerprint", "")) != manifest_fingerprint
                ):
                    raise StaleSourceError(
                        "The VDB manifest changed while its preview was rendering; "
                        "the result was not published."
                    )
                current_asset = _asset_from_manifest(current, current_path.parent)
                if not isinstance(current_asset, LibraryVdbAsset):
                    raise LibraryError("The asset is no longer a VDB.")
                current_variant = current_asset.variants.get(label)
                current_record = next(
                    (
                        item for item in current_variant.files
                        if item.path == source_record.path and item.frame == source_record.frame
                    ),
                    None,
                ) if current_variant else None
                if (
                    current_record is None
                    or current_record.sha256 != source_record.sha256
                    or _sha256_path(source_path) != source_record.sha256
                ):
                    raise StaleSourceError(
                        "The selected VDB changed while its preview was rendering; "
                        "regenerate from the current managed file."
                    )
                preview_dir = current_asset.asset_dir / "previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                target = preview_dir / result.hero_path.name
                video_target = (
                    preview_dir / result.video_path.name
                    if result.video_path is not None
                    else None
                )
                rollback: dict[Path, Path | None] = {}
                for index, publish_target in enumerate(
                    item for item in (target, video_target) if item is not None
                ):
                    backup = None
                    if publish_target.exists():
                        backup = render_stage / (
                            f"published-backup-{index}{publish_target.suffix}"
                        )
                        shutil.copyfile(publish_target, backup)
                    rollback[publish_target] = backup
                try:
                    if result.video_path is not None and video_target is not None:
                        _publish_preview_files(
                            result.hero_path,
                            target,
                            result.video_path,
                            video_target,
                        )
                    else:
                        _publish_preview_files(
                            result.hero_path, target,
                            result.thumbnail_path, target,
                        )
                    previews = current.setdefault("previews", {})
                    relative = target.relative_to(current_asset.asset_dir).as_posix()
                    previews["hero"] = relative
                    previews["thumbnail"] = relative
                    if video_target is not None:
                        previews["video"] = video_target.relative_to(
                            current_asset.asset_dir
                        ).as_posix()
                    previews["render"] = result.metadata
                    originals = current.setdefault("preview_original_paths", {})
                    originals["hero"] = None
                    originals["thumbnail"] = None
                    if video_target is not None:
                        originals["video"] = None
                    current["updated_at"] = _utc_now()
                    _asset_from_manifest(current, current_asset.asset_dir)
                    _atomic_json(current_path, current)
                except Exception:
                    for publish_target, backup in rollback.items():
                        if backup is None:
                            publish_target.unlink(missing_ok=True)
                        else:
                            os.replace(backup, publish_target)
                    _sync_directory(preview_dir)
                    raise
                result.hero_path = target
                result.thumbnail_path = target
                if video_target is not None:
                    result.video_path = video_target
                updated = _asset_from_manifest(current, current_asset.asset_dir)
                if not isinstance(updated, LibraryVdbAsset):
                    raise LibraryError("The rendered manifest did not produce a VDB asset.")
                return VdbPreviewUpdate(updated, result)
        finally:
            if render_stage.exists():
                shutil.rmtree(render_stage, ignore_errors=True)

    def render_hdri_preview(
        self,
        asset_id: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
        preview_session: BlenderPreviewSession | None = None,
    ) -> HdriPreviewUpdate:
        """Render outside the library lock, then publish only if the manifest and source stayed unchanged."""
        self.initialize()
        token = cancel_token or CancelToken()
        manifest_path, original = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(original, manifest_path.parent)
        if not isinstance(asset, LibraryHdriAsset):
            raise LibraryError("Composite preview rendering is only available for HDRI assets.")
        selected = select_hdri_variant(asset.resolutions)
        if not selected:
            raise LibraryError("The HDRI asset has no renderable local map.")
        _label, variant = selected
        source_record = select_hdri_file(variant)
        if source_record is None:
            raise LibraryError("The selected HDRI resolution has no local map.")
        source_relative = source_record.path
        source_path = asset.asset_dir / source_relative
        expected_hash = source_record.sha256
        manifest_stamp = str(original.get("updated_at", ""))
        manifest_fingerprint = str(original.get("fingerprint", ""))
        render_stage = self.root / ".ual" / "staging" / f"preview-{uuid4()}"
        request = HdriPreviewRequest(
            hdri_path=source_path,
            output_dir=render_stage,
            asset_name=asset.name,
            resolutions=tuple(asset.resolutions),
            source_relative=source_relative,
            blender_path=self.blender_path,
            template_path=self.hdri_template_path,
        )
        try:
            result = render_hdri_preview(
                request,
                progress=progress,
                cancel_token=token,
                session=preview_session,
            )
            if result.status != "ready" or not result.thumbnail_path or not result.hero_path:
                return HdriPreviewUpdate(self._record_hdri_render_status(asset_id, result.metadata), result)
            token.check()
            with _ImportLock(self.root / ".ual" / "import.lock"):
                current_path, current = self._manifest_by_id(asset_id)
                if current_path != manifest_path or str(current.get("updated_at", "")) != manifest_stamp or str(current.get("fingerprint", "")) != manifest_fingerprint:
                    raise StaleSourceError("The HDRI manifest changed while its preview was rendering; the result was not published.")
                current_asset = _asset_from_manifest(current, current_path.parent)
                if not isinstance(current_asset, LibraryHdriAsset):
                    raise LibraryError("The asset is no longer an HDRI.")
                current_variant = next((value for value in current_asset.resolutions.values() if any(item.path == source_relative for item in value.files)), None)
                current_file = next((item for item in current_variant.files if item.path == source_relative), None) if current_variant else None
                if current_file is None or current_file.sha256 != expected_hash or _sha256_path(source_path) != expected_hash:
                    raise StaleSourceError("The selected HDRI changed while its preview was rendering; regenerate from the current file.")
                preview_dir = current_asset.asset_dir / "previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                hero_name = result.hero_path.name
                thumb_name = result.thumbnail_path.name
                hero_target = preview_dir / hero_name
                thumb_target = preview_dir / thumb_name
                rollback: dict[Path, Path | None] = {}
                for index, target in enumerate(dict.fromkeys((hero_target, thumb_target))):
                    if target.exists():
                        backup = render_stage / f"published-backup-{index}{target.suffix}"
                        shutil.copyfile(target, backup)
                        rollback[target] = backup
                    else:
                        rollback[target] = None
                try:
                    _publish_preview_files(result.hero_path, hero_target, result.thumbnail_path, thumb_target)
                    previews = current.setdefault("previews", {})
                    previews["hero"] = hero_target.relative_to(current_asset.asset_dir).as_posix()
                    previews["thumbnail"] = thumb_target.relative_to(current_asset.asset_dir).as_posix()
                    previews["render"] = result.metadata
                    current["updated_at"] = _utc_now()
                    _asset_from_manifest(current, current_asset.asset_dir)
                    _atomic_json(current_path, current)
                except Exception:
                    for target, backup in rollback.items():
                        if backup is None:
                            target.unlink(missing_ok=True)
                        else:
                            os.replace(backup, target)
                    _sync_directory(preview_dir)
                    raise
                result.hero_path = hero_target
                result.thumbnail_path = thumb_target
                return HdriPreviewUpdate(_asset_from_manifest(current, current_asset.asset_dir), result)
        finally:
            if render_stage.exists():
                shutil.rmtree(render_stage, ignore_errors=True)

    def convert_model_to_usd(
        self,
        asset_id: str,
        selected_source_path: str = "",
        orientation: str = "usd_interchange",
        *,
        progress: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ModelConversionUpdate:
        """Build outside the lock, then atomically publish a generated USDC derivative."""
        self.initialize()
        token = cancel_token or CancelToken()
        manifest_path, original = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(original, manifest_path.parent)
        if not isinstance(asset, LibraryModelAsset):
            raise LibraryError("USD conversion is only available for model assets.")
        try:
            request = prepare_model_conversion(
                asset,
                selected_source_path,
                orientation,
                self.root,
                str(original.get("updated_at", "")),
            )
        except ModelConversionError as error:
            raise LibraryError(str(error)) from error
        stage = self.root / ".ual" / "staging" / f"model-usd-{uuid4()}"
        output = stage / "generated"
        try:
            result = run_model_conversion(
                request,
                output,
                blender_path=self.blender_path,
                progress=progress,
                cancel_token=token,
            )
            if result.status == "canceled":
                raise ImportCancelled(result.diagnostic or "Model conversion was canceled.")
            if result.status != "ready" or result.entry_path is None:
                detail = result.diagnostic or "Blender did not create a valid USDC derivative."
                if result.log:
                    detail += "\n\n" + result.log[-4000:]
                raise LibraryError(detail)
            token.check()
            required = _directory_size(output) + SPACE_CUSHION
            if shutil.disk_usage(self.root).free < required:
                raise LibraryError(
                    "The library does not have enough free space to publish the USD derivative."
                )
            with _ImportLock(self.root / ".ual" / "import.lock"):
                current_path, current = self._manifest_by_id(asset_id)
                if (
                    current_path != manifest_path
                    or str(current.get("updated_at", "")) != request.manifest_updated_at
                ):
                    raise StaleSourceError(
                        "The model manifest changed during conversion; the generated USD was not published."
                    )
                current_asset = _asset_from_manifest(current, current_path.parent)
                if not isinstance(current_asset, LibraryModelAsset):
                    raise LibraryError("The asset is no longer a model.")
                source_record = next(
                    (item for item in current_asset.model_files if item.path == request.source_relative),
                    None,
                )
                if source_record is None:
                    raise StaleSourceError("The selected source model is no longer in this asset.")
                current_source = current_asset.asset_dir / source_record.path
                if (
                    source_record.sha256 != request.source_sha256
                    or request.source_sha256
                    and _sha256_path(current_source) != request.source_sha256
                ):
                    raise StaleSourceError(
                        "The selected source model changed during conversion; the result was not published."
                    )
                target = current_asset.asset_dir / "usd"
                target.parent.mkdir(parents=True, exist_ok=True)
                previous = stage / "previous-usd"
                merged = stage / "publish-usd"
                if target.is_dir():
                    shutil.copytree(target, merged, symlinks=False)
                else:
                    merged.mkdir(parents=True)
                derivative_record = current.get("usd_derivative", {})
                owned_files = {
                    str(derivative_record.get("entry_path", "")),
                    *(
                        str(item.get("path", ""))
                        for item in derivative_record.get("dependencies", [])
                        if isinstance(item, dict)
                    ),
                } if isinstance(derivative_record, dict) else set()
                owned_files.discard("")
                for relative in owned_files:
                    path = PurePosixPath(relative)
                    try:
                        inside = path.relative_to("usd")
                    except ValueError:
                        continue
                    owned = merged / inside
                    if owned.is_file():
                        owned.unlink()
                for directory in sorted(
                    (path for path in merged.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts),
                    reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                entry_relative_inside = result.entry_path.relative_to(output)
                generated_paths: list[tuple[Path, Path]] = []
                for source in sorted(output.rglob("*")):
                    if not source.is_file():
                        continue
                    relative = source.relative_to(output)
                    destination = merged / relative
                    if destination.exists():
                        raise LibraryError(
                            "The generated USD conflicts with an existing provider or "
                            f"manual file: usd/{relative.as_posix()}"
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    generated_paths.append((relative, destination))
                published = False
                try:
                    if target.exists():
                        os.replace(target, previous)
                    os.replace(merged, target)
                    published = True
                    entry = target / entry_relative_inside
                    entry_relative = entry.relative_to(current_asset.asset_dir).as_posix()
                    dependency_records = [
                        {
                            "path": (PurePosixPath("usd") / relative).as_posix(),
                            "size": (target / relative).stat().st_size,
                            "sha256": _sha256_file(target / relative),
                        }
                        for relative, _staged in generated_paths
                        if relative != entry_relative_inside
                    ]
                    generated = current.get("usd_derivative", {})
                    old_entry = str(generated.get("entry_path", ""))
                    model_files = [
                        dict(item)
                        for item in current.get("model_files", [])
                        if isinstance(item, dict) and str(item.get("path", "")) != old_entry
                    ]
                    manual_preferred = next((
                        item for item in current_asset.model_files
                        if item.preferred and item.available and item.origin == "manual"
                    ), None)
                    for item in model_files:
                        item["preferred"] = bool(
                            manual_preferred
                            and str(item.get("path", "")) == manual_preferred.path
                        )
                    model_files.append({
                        "path": entry_relative,
                        "original_path": request.source_relative,
                        "format": "USDC",
                        "role": "generated_derivative",
                        "lod": request.source_lod,
                        "component": request.source_component,
                        "triangle_count": source_record.triangle_count,
                        "preferred": manual_preferred is None,
                        "size": entry.stat().st_size,
                        "sha256": _sha256_file(entry),
                        "resolution": source_record.resolution,
                        "origin": "generated",
                    })
                    current["model_files"] = model_files
                    current["usd_derivative"] = {
                        "entry_path": entry_relative,
                        "source_path": request.source_relative,
                        "source_sha256": request.source_sha256,
                        "forward_axis": request.forward_axis,
                        "up_axis": request.up_axis,
                        "blender_version": result.blender_version,
                        "generated_at": _utc_now(),
                        "dependencies": dependency_records,
                        "diagnostics": list(result.diagnostics),
                    }
                    current["updated_at"] = _utc_now()
                    updated = _asset_from_manifest(current, current_asset.asset_dir)
                    if not isinstance(updated, LibraryModelAsset):
                        raise LibraryError("Generated manifest did not produce a model asset.")
                    _sync_directory(target.parent)
                    _atomic_json(current_path, current)
                except Exception:
                    if published and target.exists():
                        shutil.rmtree(target)
                    if previous.exists():
                        os.replace(previous, target)
                    _sync_directory(target.parent)
                    raise
                if previous.exists():
                    shutil.rmtree(previous)
                final_result = ModelConversionResult(
                    result.status,
                    target / entry_relative_inside,
                    result.blender_version,
                    result.mesh_count,
                    result.material_count,
                    tuple(
                        current_asset.asset_dir / str(item["path"])
                        for item in dependency_records
                    ),
                    result.diagnostics,
                    result.diagnostic,
                    result.log,
                )
                return ModelConversionUpdate(updated, final_result)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def rescan_model_asset(
        self,
        asset_id: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ModelAssetRescan:
        """Inventory manually added model payloads without changing the asset."""
        self.initialize()
        token = cancel_token or CancelToken()
        manifest_path, document = self._manifest_by_id(asset_id)
        asset = _asset_from_manifest(document, manifest_path.parent)
        if not isinstance(asset, LibraryModelAsset):
            raise LibraryError("Rescan asset is only available for model assets.")
        try:
            return inventory_model_asset(
                asset,
                str(document.get("updated_at", "")),
                blender_path=self.blender_path,
                progress=progress,
                cancel_token=token,
            )
        except ImportCancelled:
            raise
        except Exception as error:
            raise LibraryError(str(error)) from error

    def apply_model_asset_rescan(
        self,
        asset_id: str,
        scan: ModelAssetRescan,
        selection: ModelRescanSelection,
        *,
        cancel_token: CancelToken | None = None,
    ) -> ModelAssetRescanUpdate:
        """Atomically register reviewed manual model-file changes."""
        self.initialize()
        token = cancel_token or CancelToken()
        if scan.asset_id != asset_id:
            raise LibraryError("The rescan result belongs to a different asset.")
        add_paths = set(selection.add_paths)
        refresh_paths = set(selection.refresh_paths)
        remove_paths = set(selection.remove_paths)
        if (add_paths & refresh_paths) or (add_paths & remove_paths) or (
            refresh_paths & remove_paths
        ):
            raise LibraryError("A rescan path cannot have more than one action.")
        selected_items = {
            path: scan.item(path)
            for path in add_paths | refresh_paths | remove_paths
        }
        if any(item is None for item in selected_items.values()):
            raise LibraryError("The rescan selection contains an unknown file.")
        for path in add_paths:
            item = selected_items[path]
            if item.status != "new" or not item.valid_for_apply:
                raise LibraryError(f"The new model file is not valid for adoption: {path}")
        for path in refresh_paths:
            item = selected_items[path]
            if (
                item.status != "changed" or item.origin != "manual"
                or not item.valid_for_apply
            ):
                raise LibraryError(f"Only changed manual files can be refreshed: {path}")
        for path in remove_paths:
            item = selected_items[path]
            if item.origin != "manual" or item.status not in {"missing", "changed"}:
                raise LibraryError(f"Only missing or changed manual files can be removed: {path}")
        preferred_path = _safe_manifest_path(selection.preferred_path)
        token.check()
        with _ImportLock(self.root / ".ual" / "import.lock"):
            manifest_path, current = self._manifest_by_id(asset_id)
            if str(current.get("updated_at", "")) != scan.manifest_updated_at:
                raise StaleSourceError(
                    "The model manifest changed after the rescan; scan the asset again."
                )
            current_asset = _asset_from_manifest(current, manifest_path.parent)
            if not isinstance(current_asset, LibraryModelAsset):
                raise LibraryError("The asset is no longer a model.")
            root = current_asset.asset_dir.resolve(strict=True)
            records = [
                dict(value) for value in current.get("model_files", [])
                if isinstance(value, dict)
            ]
            by_path = {str(value.get("path", "")): value for value in records}
            now = _utc_now()
            for path in sorted(add_paths | refresh_paths):
                token.check()
                item = selected_items[path]
                managed = _safe_asset_file(root, path)
                stat_result = managed.stat()
                if (
                    stat_result.st_size != item.size
                    or _sha256_file(managed) != item.sha256
                ):
                    raise StaleSourceError(
                        f"{path} changed after the rescan; scan the asset again."
                    )
                dependencies = []
                validation = item.validation
                expected_dependencies = {
                    relative: (size, digest)
                    for relative, size, digest in (
                        validation.dependency_records if validation else ()
                    )
                }
                for relative in validation.dependencies if validation else ():
                    dependency = _safe_asset_file(root, relative)
                    expected_size, expected_digest = expected_dependencies.get(
                        relative, (-1, "")
                    )
                    dependency_size = dependency.stat().st_size
                    dependency_digest = _sha256_file(dependency)
                    if (
                        dependency_size != expected_size
                        or dependency_digest != expected_digest
                    ):
                        raise StaleSourceError(
                            f"{relative} changed after USD validation; scan the asset again."
                        )
                    dependencies.append({
                        "path": relative,
                        "size": dependency_size,
                        "sha256": dependency_digest,
                    })
                existing = by_path.get(path, {})
                record = {
                    "path": path,
                    "original_path": str(existing.get("original_path", path)),
                    "format": item.file_format,
                    "role": item.role,
                    "lod": item.lod,
                    "component": item.component,
                    "triangle_count": existing.get("triangle_count"),
                    "preferred": False,
                    "size": item.size,
                    "sha256": item.sha256,
                    "resolution": str(existing.get("resolution", "")),
                    "origin": "manual",
                    "registered_at": str(existing.get("registered_at", "")) or now,
                    "validation": validation.document() if validation else {},
                    "dependencies": dependencies,
                }
                if path in by_path:
                    records[records.index(by_path[path])] = record
                else:
                    records.append(record)
                by_path[path] = record
            for path in sorted(remove_paths):
                token.check()
                item = selected_items[path]
                managed = root / _safe_manifest_path(path)
                if item.status == "missing" and managed.exists():
                    raise StaleSourceError(
                        f"{path} reappeared after the rescan; scan the asset again."
                    )
                record = by_path.get(path)
                if not record or _model_record_origin(record) != "manual":
                    raise StaleSourceError(f"{path} is no longer a removable manual file.")
                records.remove(record)
                by_path.pop(path, None)
            if preferred_path not in by_path:
                raise LibraryError("Choose a registered, available preferred model file.")
            preferred_file = root / preferred_path
            if not preferred_file.is_file():
                raise LibraryError("The selected preferred model file is unavailable.")
            available_usd = [
                record for record in records
                if str(record.get("format", "")).upper() in RESCAN_USD_FORMATS
                and (root / _safe_manifest_path(str(record.get("path", "")))).is_file()
            ]
            if (
                available_usd
                and str(by_path[preferred_path].get("format", "")).upper()
                not in RESCAN_USD_FORMATS
            ):
                raise LibraryError(
                    "A USD-family model must remain preferred while USD files are available."
                )
            for record in records:
                record["preferred"] = str(record.get("path", "")) == preferred_path
            updated = json.loads(json.dumps(current))
            updated["model_files"] = records
            updated["updated_at"] = now
            token.check()
            asset = _asset_from_manifest(updated, root)
            if not isinstance(asset, LibraryModelAsset):
                raise LibraryError("The updated manifest did not produce a model asset.")
            _atomic_json(manifest_path, updated)
            return ModelAssetRescanUpdate(
                asset,
                tuple(sorted(add_paths)),
                tuple(sorted(refresh_paths)),
                tuple(sorted(remove_paths)),
            )

    def _record_hdri_render_status(self, asset_id: str, metadata: dict) -> LibraryHdriAsset:
        with _ImportLock(self.root / ".ual" / "import.lock"):
            manifest_path, document = self._manifest_by_id(asset_id)
            asset = _asset_from_manifest(document, manifest_path.parent)
            if not isinstance(asset, LibraryHdriAsset):
                raise LibraryError("The asset is no longer an HDRI.")
            document.setdefault("previews", {})["render"] = dict(metadata)
            document["updated_at"] = _utc_now()
            _atomic_json(manifest_path, document)
            return _asset_from_manifest(document, manifest_path.parent)

    def _record_texture_render_status(
        self, asset_id: str, metadata: dict
    ) -> LibraryTextureAsset:
        with _ImportLock(self.root / ".ual" / "import.lock"):
            manifest_path, document = self._manifest_by_id(asset_id)
            asset = _asset_from_manifest(document, manifest_path.parent)
            if (
                not isinstance(asset, LibraryTextureAsset)
                or asset.asset_type != "texture_set"
            ):
                raise LibraryError("The asset is no longer a texture set.")
            document.setdefault("previews", {})["render"] = dict(metadata)
            document["updated_at"] = _utc_now()
            _atomic_json(manifest_path, document)
            updated = _asset_from_manifest(document, manifest_path.parent)
            if not isinstance(updated, LibraryTextureAsset):
                raise LibraryError(
                    "The updated manifest did not produce a texture asset."
                )
            return updated

    def _record_vdb_render_status(
        self, asset_id: str, metadata: dict
    ) -> LibraryVdbAsset:
        with _ImportLock(self.root / ".ual" / "import.lock"):
            manifest_path, document = self._manifest_by_id(asset_id)
            asset = _asset_from_manifest(document, manifest_path.parent)
            if not isinstance(asset, LibraryVdbAsset):
                raise LibraryError("The asset is no longer a VDB.")
            document.setdefault("previews", {})["render"] = dict(metadata)
            document["updated_at"] = _utc_now()
            _atomic_json(manifest_path, document)
            updated = _asset_from_manifest(document, manifest_path.parent)
            if not isinstance(updated, LibraryVdbAsset):
                raise LibraryError("The updated manifest did not produce a VDB asset.")
            return updated

    def _manifest_by_id(self, asset_id: str) -> tuple[Path, dict]:
        paths = _asset_manifest_paths(self.root)
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if str(document.get("id", "")) == asset_id:
                return path, document
        raise LibraryError(f"Asset {asset_id} was not found in the configured library.")

    def preflight_materials(
        self,
        materials: Iterable[MaterialCandidate],
        progress: Callable[[ImportProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
        hash_cache: dict[tuple[str, int, int], str] | None = None,
    ) -> PreflightResult:
        selected = list(materials)
        token = cancel_token or CancelToken()
        cache = hash_cache if hash_cache is not None else {}
        result = PreflightResult()
        self.initialize()
        existing = self.list_assets()
        existing_fingerprints = {
            (asset.asset_type, asset.fingerprint): asset for asset in existing if asset.fingerprint
        }
        existing_providers = {
            (asset.asset_type, asset.provider.casefold(), asset.provider_id): asset
            for asset in existing
            if asset.provider_id
        }
        source_files: list[tuple[MaterialCandidate, str]] = []
        for material in selected:
            source_files.extend((material, relative) for relative in _candidate_source_paths(material))
        total_bytes = 0
        for material, relative in source_files:
            try:
                total_bytes += _safe_source(material.source_root, relative).stat().st_size
            except (OSError, LibraryError):
                pass
        result.total_bytes = total_bytes
        completed_bytes = 0
        completed_files = 0
        total_files = len(source_files)

        for material in selected:
            item = MaterialPreflight(material=material)
            result.materials.append(item)
            try:
                token.check()
                if progress:
                    progress(ImportProgress(
                        material.name,
                        "Paths, metadata, and source snapshots",
                        completed_bytes,
                        total_bytes,
                        "Validating",
                        completed_files,
                        total_files,
                    ))
                if material.archive_source:
                    _validate_archive_source(material)
                    archive = material.archive_source.archive_path
                    archive_stat = archive.stat()
                    cache_key = (
                        str(archive),
                        archive_stat.st_size,
                        archive_stat.st_mtime_ns,
                    )
                    item.archive_sha256 = cache.get(cache_key, "")
                    if not item.archive_sha256:
                        item.archive_sha256 = _sha256_path(archive)
                        cache[cache_key] = item.archive_sha256
                    _validate_archive_source(material, item.archive_sha256)
                if isinstance(material, StockCandidate) and not resolve_ffmpeg(self.ffmpeg_path):
                    raise LibraryError("FFmpeg was not found. Configure it in Settings before importing Stock footage.")
                _validate_candidate(material, self.root)
                item.diagnostics.extend(_portable_candidate_diagnostics(material, self.root))
                _validate_all_snapshots(material)
                if any(diagnostic.severity == "error" for diagnostic in item.diagnostics):
                    item.status = "Invalid"
                    result.diagnostics.extend(item.diagnostics)
                    continue
                fingerprint_records: list[str] = []
                primary = {
                    texture_file.relative_path: (label, channel)
                    for label, channel, texture_file in _primary_file_records(material)
                }
                for relative in _candidate_source_paths(material):
                    source = _safe_source(material.source_root, relative)
                    snapshot = material.source_snapshots.get(relative)
                    cache_key = (str(source), source.stat().st_size, source.stat().st_mtime_ns)
                    digest = cache.get(cache_key)
                    current_file = completed_files + 1
                    if not digest:
                        digest, used = _hash_source(
                            source,
                            snapshot,
                            material.name,
                            (
                                lambda value, position=current_file: progress(ImportProgress(
                                    value.material,
                                    value.file,
                                    value.completed_bytes,
                                    value.total_bytes,
                                    "Hashing",
                                    position,
                                    total_files,
                                ))
                            ) if progress else None,
                            token,
                            completed_bytes, total_bytes,
                        )
                        completed_bytes += used
                        cache[cache_key] = digest
                    else:
                        completed_bytes += source.stat().st_size
                        if progress:
                            progress(ImportProgress(
                                material.name,
                                source.name,
                                completed_bytes,
                                total_bytes,
                                "Using cached hash",
                                current_file,
                                total_files,
                            ))
                    completed_files += 1
                    item.hashes[relative] = digest
                    if relative in primary:
                        label, channel = primary[relative]
                        fingerprint_records.append(f"{label}|{channel}|{digest}")
                if isinstance(material, StockCandidate):
                    item.fingerprint = item.hashes.get(material.source_video, "")
                else:
                    item.fingerprint = hashlib.sha256(
                        "\n".join(sorted(fingerprint_records)).encode("utf-8")
                    ).hexdigest()
                if progress:
                    progress(ImportProgress(
                        material.name,
                        "Content fingerprints and provider IDs",
                        completed_bytes,
                        total_bytes,
                        "Checking duplicates",
                        completed_files,
                        total_files,
                    ))
                duplicate = existing_fingerprints.get((material.asset_type, item.fingerprint))
                if duplicate:
                    item.status = "Duplicate"
                    same_provider = bool(
                        material.provider_id
                        and duplicate.provider.casefold() == material.provider.casefold()
                        and duplicate.provider_id == material.provider_id
                    )
                    item.duplicate_reason = "matching provider ID" if same_provider else "matching content fingerprint"
                else:
                    provider_key = (material.asset_type, material.provider.casefold(), material.provider_id)
                    provider_asset = existing_providers.get(provider_key) if material.provider_id else None
                    if provider_asset:
                        item.status = "Conflict"
                        item.conflict = DuplicateConflict(
                            material.material_key,
                            provider_asset.id,
                            provider_asset.name,
                            material.provider,
                            material.provider_id,
                        )
                    elif material.diagnostics:
                        item.status = "Warning"
            except ImportCancelled:
                result.canceled = True
                break
            except StaleSourceError as error:
                item.status = "Stale"
                item.diagnostics.append(Diagnostic("error", "source_changed", str(error), material=str(material.source_root)))
            except Exception as error:
                item.status = "Invalid"
                item.diagnostics.append(Diagnostic("error", "preflight_failed", str(error), material=str(material.source_root)))
            result.diagnostics.extend(item.diagnostics)

        if progress:
            progress(ImportProgress(
                "",
                "Required capacity plus the 64 MB safety cushion",
                completed_bytes,
                total_bytes,
                "Checking disk space",
                completed_files,
                total_files,
            ))
        required = sum(
            _material_source_bytes(item.material)
            for item in result.materials
            if item.status in {"Ready", "Warning", "Conflict"}
        )
        if required and shutil.disk_usage(self.root).free < required + SPACE_CUSHION:
            diagnostic = Diagnostic("error", "insufficient_space", "The library does not have enough free space for this import.")
            result.diagnostics.append(diagnostic)
            for item in result.materials:
                if item.status in {"Ready", "Warning", "Conflict"}:
                    item.status = "Invalid"
                    item.diagnostics.append(diagnostic)
        return result

    def preflight_assets(self, assets: Iterable[MaterialCandidate], **kwargs) -> PreflightResult:
        """Type-neutral entry point; preflight_materials remains for compatibility."""
        return self.preflight_materials(assets, **kwargs)

    def legacy_asset_count(self) -> int:
        return len(self._legacy_manifests())

    def library_update_count(self) -> int:
        hdri_candidates, failures = self._hdri_layout_candidates()
        model_candidates, model_failures = self._model_layout_candidates()
        stock_candidates, stock_failures = self._stock_layout_candidates()
        metadata_candidates, metadata_failures = self._metadata_migration_candidates()
        failures.update(model_failures)
        failures.update(stock_failures)
        failures.update(metadata_failures)
        candidate_paths = {
            path
            for path, _document in (
                *hdri_candidates,
                *model_candidates,
                *stock_candidates,
                *metadata_candidates,
            )
            if str(path) not in failures
        }
        return len(candidate_paths) + len(failures)

    def update_library(
        self,
        progress: Callable[[RepairProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> LibraryUpdateSummary:
        """Validate manifests and safely update legacy HDRI, model, and Stock layouts."""
        token = cancel_token or CancelToken()
        self.initialize()
        summary = LibraryUpdateSummary()
        with _ImportLock(self.root / ".ual" / "import.lock"):
            hdri_candidates, failures = self._hdri_layout_candidates()
            model_candidates, model_failures = self._model_layout_candidates()
            stock_candidates, stock_failures = self._stock_layout_candidates()
            metadata_candidates, metadata_failures = self._metadata_migration_candidates()
            failures.update(model_failures)
            failures.update(stock_failures)
            failures.update(metadata_failures)
            summary.failed.update(failures)
            metadata_by_path = {path: document for path, document in metadata_candidates}
            blocked_paths = set(metadata_failures)
            layout_candidates = [
                item
                for item in (
                [("hdri", *item) for item in hdri_candidates]
                + [("model", *item) for item in model_candidates]
                + [("stock", *item) for item in stock_candidates]
                )
                if str(item[1]) not in blocked_paths
            ]
            normalized_layout_candidates = [
                (
                    kind,
                    path,
                    self._single_category_document(document)
                    if path in metadata_by_path else document,
                )
                for kind, path, document in layout_candidates
            ]
            layout_paths = {path for _kind, path, _document in layout_candidates}
            candidates = (
                normalized_layout_candidates
                + [
                    ("metadata", path, document)
                    for path, document in metadata_candidates
                    if path not in layout_paths
                ]
            )
            if candidates:
                # Updates are published and cleaned one asset at a time, so
                # only the largest candidate needs simultaneous staging space.
                required = max(
                    (
                        path.stat().st_size
                        if kind == "metadata"
                        else _directory_size(path.parent)
                    )
                    for kind, path, _document in candidates
                )
                if shutil.disk_usage(self.root).free < required + SPACE_CUSHION:
                    raise LibraryError("The library does not have enough free space for a safe layout update.")
            for index, (kind, manifest_path, document) in enumerate(candidates):
                if token.cancelled:
                    summary.canceled = True
                    break
                name = str(document.get("name", manifest_path.parent.name))
                try:
                    asset = (
                        self._flatten_model_layout(manifest_path.parent, document, token)
                        if kind == "model" else
                        self._flatten_stock_layout(manifest_path, document, token)
                        if kind == "stock" else
                        self._migrate_single_category_manifest(manifest_path, document)
                        if kind == "metadata" else
                        self._flatten_hdri_layout(manifest_path.parent, document, token)
                    )
                except Exception as error:
                    summary.failed[name] = str(error)
                else:
                    summary.updated.append(asset)
                if progress:
                    progress(RepairProgress(name, index + 1, len(candidates)))
            for asset in self.list_assets():
                if asset.id not in {item.id for item in summary.updated}:
                    summary.valid += 1
        return summary

    def _metadata_migration_candidates(
        self,
    ) -> tuple[list[tuple[Path, dict]], dict[str, str]]:
        candidates: list[tuple[Path, dict]] = []
        failures: dict[str, str] = {}
        store = CategoryConfigStore(self.root)
        for path in sorted(_asset_manifest_paths(self.root), key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                asset_type = str(document.get("type", "texture_set"))
                catalog = store.load(asset_type)
                primary = _manifest_primary_category(document)
                if catalog.canonical_name(primary) is None:
                    raise ValueError(
                        f"Category {primary!r} is not defined in {catalog.asset_type} categories."
                    )
                has_surface_tag = any(
                    str(value).strip().casefold() == "surface"
                    for value in document.get("tags", ())
                )
                if "categories" in document or has_surface_tag or str(
                    document.get("category", "")
                ).strip().casefold() == "surface":
                    candidates.append((path, document))
            except Exception as error:
                failures[str(path)] = str(error)
        return candidates, failures

    def _migrate_single_category_manifest(
        self,
        manifest_path: Path,
        document: dict,
    ) -> LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset:
        updated = self._single_category_document(document)
        asset = _asset_from_manifest(updated, manifest_path.parent)
        _atomic_json(manifest_path, updated)
        return asset

    def _single_category_document(self, document: dict) -> dict:
        updated = json.loads(json.dumps(document))
        asset_type = str(updated.get("type", "texture_set"))
        catalog = CategoryConfigStore(self.root).load(asset_type)
        raw_primary = str(updated.get("category", "")).strip()
        if not raw_primary:
            legacy = _normalized_values(updated.get("categories", ()))
            raw_primary = legacy[0] if legacy else "Uncategorized"
        if raw_primary.casefold() == "surface":
            raw_primary = "Uncategorized"
        primary = catalog.canonical_name(raw_primary)
        if primary is None:
            raise LibraryError(
                f"Category {raw_primary!r} is not defined in {catalog.asset_type} categories."
            )
        legacy_categories = _normalized_values(updated.get("categories", ()))
        secondary = (
            value
            for value in legacy_categories
            if value.casefold() not in {primary.casefold(), raw_primary.casefold(), "surface"}
        )
        updated["category"] = primary
        updated["tags"] = list(_clean_tags(updated.get("tags", ()), secondary))
        updated.pop("categories", None)
        updated["updated_at"] = _utc_now()
        return updated

    def _stock_layout_candidates(self) -> tuple[list[tuple[Path, dict]], dict[str, str]]:
        candidates: list[tuple[Path, dict]] = []
        failures: dict[str, str] = {}
        root = self.root / "stock"
        if not root.is_dir():
            return candidates, failures
        for path in sorted(root.glob("*/*/asset.json"), key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                if document.get("type") != "stock":
                    raise ValueError("Expected a Stock manifest")
                asset = _asset_from_manifest(document, path.parent)
                if not isinstance(asset, LibraryStockAsset):
                    raise ValueError("Expected a Stock asset")
            except Exception as error:
                failures[str(path)] = str(error)
                continue
            candidates.append((path, document))
        return candidates, failures

    def _hdri_layout_candidates(self) -> tuple[list[tuple[Path, dict]], dict[str, str]]:
        candidates: list[tuple[Path, dict]] = []
        failures: dict[str, str] = {}
        root = self.root / "hdris"
        if not root.is_dir():
            return candidates, failures
        for path in sorted(root.glob("**/asset.json"), key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                if document.get("type") != "hdri":
                    raise ValueError("Expected an HDRI manifest")
                _asset_from_manifest(document, path.parent)
            except Exception as error:
                failures[str(path)] = str(error)
                continue
            legacy = any(
                len(Path(str(record.get("path", ""))).parts) > 2
                for value in document.get("resolutions", {}).values()
                for record in value.get("files", [])
                if isinstance(record, dict)
            )
            if legacy or int(document.get("layout_version", 1)) < LAYOUT_VERSION:
                candidates.append((path, document))
        return candidates, failures

    def _model_layout_candidates(self) -> tuple[list[tuple[Path, dict]], dict[str, str]]:
        candidates: list[tuple[Path, dict]] = []
        failures: dict[str, str] = {}
        root = self.root / "models"
        if not root.is_dir():
            return candidates, failures
        for path in sorted(root.glob("**/asset.json"), key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                if document.get("type") != "model":
                    raise ValueError("Expected a model manifest")
                _asset_from_manifest(document, path.parent)
            except Exception as error:
                failures[str(path)] = str(error)
                continue
            records = list(document.get("model_files", []))
            for texture_set in document.get("texture_sets", {}).values():
                for variant in texture_set.get("resolutions", {}).values():
                    for alternatives in variant.get("maps", {}).values():
                        records.extend(alternatives)
            legacy = any(str(record.get("path", "")).startswith("source/") for record in records if isinstance(record, dict))
            legacy = legacy or any(
                str(value).startswith("source/")
                for value in (*document.get("source_metadata", []), *document.get("previews", {}).values())
                if value
            )
            legacy = legacy or any(
                str(package.get("kind", "")) == "usd"
                and str(package.get("entry_path", "")).startswith("packages/usd/")
                for package in document.get("provider_packages", [])
                if isinstance(package, dict)
            )
            if legacy or int(document.get("layout_version", 1)) < MODEL_LAYOUT_VERSION:
                candidates.append((path, document))
        return candidates, failures

    def _flatten_stock_layout(
        self, manifest_path: Path, document: dict, token: CancelToken
    ) -> LibraryStockAsset:
        source_dir = manifest_path.parent
        target_parent = self.root / "stock" / _slugify(
            str(document.get("category", "Uncategorized"))
        )
        target_parent.mkdir(parents=True, exist_ok=True)
        asset_token = _unique_stock_token(
            target_parent,
            _filename_token(str(document.get("name", source_dir.name))),
            {source_dir.name},
        )
        operation = uuid4().hex
        stage = self.root / ".ual" / "staging" / f"stock-update-{operation}"
        backup = self.root / ".ual" / "staging" / f"stock-backup-{operation}"
        stage.mkdir(parents=True, exist_ok=False)
        updated = json.loads(json.dumps(document))
        used: set[str] = set()

        def copy_managed(relative: str, filename: str) -> str:
            token.check()
            source = _safe_asset_file(source_dir, relative)
            destination_name = _unique_stock_filename(filename, used)
            destination = stage / destination_name
            shutil.copy2(source, destination)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            return destination_name

        try:
            source_record = updated["source"]
            old_source = _safe_manifest_path(str(source_record["path"]))
            source_record["path"] = copy_managed(
                old_source, f"{asset_token}{Path(old_source).suffix}"
            )
            preview_record = updated["preview"]
            old_preview = _safe_manifest_path(str(preview_record["video"]))
            old_thumbnail = _safe_manifest_path(str(preview_record["thumbnail"]))
            preview_record["video"] = copy_managed(
                old_preview, f"{asset_token}_Preview{Path(old_preview).suffix}"
            )
            preview_record["thumbnail"] = copy_managed(
                old_thumbnail, f"{asset_token}_Thumbnail{Path(old_thumbnail).suffix}"
            )
            metadata: list[str] = []
            for value in updated.get("source_metadata", []):
                old = _safe_manifest_path(str(value))
                metadata.append(copy_managed(
                    old, f"{asset_token}_Metadata_{Path(old).name}"
                ))
            updated["source_metadata"] = metadata
            for record in updated.get("extra_files", []):
                old = _safe_manifest_path(str(record["path"]))
                record["path"] = copy_managed(
                    old, f"{asset_token}_Extra_{Path(old).name}"
                )
            updated["layout_version"] = STOCK_LAYOUT_VERSION
            manifest_name = f"{asset_token}.json"
            _atomic_json(stage / manifest_name, updated)
            staged = _asset_from_manifest(updated, stage)
            if not isinstance(staged, LibraryStockAsset):
                raise LibraryError("Migrated manifest did not produce a Stock asset.")

            os.replace(source_dir, backup)
            published: list[Path] = []
            try:
                staged_manifest = stage / manifest_name
                payloads = sorted(
                    (path for path in stage.iterdir() if path != staged_manifest),
                    key=lambda path: path.name.casefold(),
                )
                for staged_path in (*payloads, staged_manifest):
                    token.check()
                    destination = target_parent / staged_path.name
                    if destination.exists():
                        raise LibraryError(
                            f"Stock migration destination already exists: {destination.name}"
                        )
                    os.replace(staged_path, destination)
                    published.append(destination)
                _sync_directory(target_parent)
                result = _asset_from_manifest(updated, target_parent)
                if not isinstance(result, LibraryStockAsset):
                    raise LibraryError("Published migration is not a Stock asset.")
            except Exception:
                for path in reversed(published):
                    path.unlink(missing_ok=True)
                if backup.exists() and not source_dir.exists():
                    os.replace(backup, source_dir)
                _sync_directory(target_parent)
                raise
            shutil.rmtree(backup)
            stage.rmdir()
            _sync_directory(target_parent)
            return result
        except Exception:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if backup.exists() and not source_dir.exists():
                os.replace(backup, source_dir)
            raise

    def _flatten_hdri_layout(self, source_dir: Path, document: dict, token: CancelToken) -> LibraryHdriAsset:
        update_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / f"update-{update_id}"
        backup = self.root / ".ual" / "staging" / f"backup-{update_id}"
        try:
            shutil.copytree(source_dir, stage, symlinks=False)
            updated = json.loads(json.dumps(document))
            used: set[str] = set()
            for label in _sorted_resolution_labels(updated.get("resolutions", {})):
                for record in updated["resolutions"][label].get("files", []):
                    token.check()
                    relative = _safe_manifest_path(str(record["path"]))
                    source = _safe_asset_file(stage, relative)
                    filename = _unique_flat_filename(source.name, used)
                    destination = stage / "maps" / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source != destination:
                        os.replace(source, destination)
                    record["path"] = destination.relative_to(stage).as_posix()
            maps = stage / "maps"
            if maps.is_dir():
                for directory in sorted((path for path in maps.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            updated["layout_version"] = LAYOUT_VERSION
            _atomic_json(stage / "asset.json", updated)
            asset = _asset_from_manifest(updated, stage)
            if not isinstance(asset, LibraryHdriAsset):
                raise LibraryError("Updated manifest is not an HDRI asset.")
            os.replace(source_dir, backup)
            try:
                os.replace(stage, source_dir)
            except Exception:
                os.replace(backup, source_dir)
                raise
            shutil.rmtree(backup)
            _sync_directory(source_dir.parent)
            return _asset_from_manifest(updated, source_dir)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            if backup.exists() and not source_dir.exists():
                os.replace(backup, source_dir)
            raise

    def _flatten_model_layout(self, source_dir: Path, document: dict, token: CancelToken) -> LibraryModelAsset:
        update_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / f"update-{update_id}"
        backup = self.root / ".ual" / "staging" / f"backup-{update_id}"
        try:
            shutil.copytree(source_dir, stage, symlinks=False)
            updated = json.loads(json.dumps(document))
            moved: dict[str, str] = {}
            used: dict[str, set[str]] = {}
            map_used: dict[Path, set[str]] = {}
            asset_token = _filename_token(str(updated.get("name", "Model")))

            def relocate(record: dict, container: str, filename: str = "") -> None:
                token.check()
                old = _safe_manifest_path(str(record["path"]))
                expected_parent = PurePosixPath(container)
                old_path = PurePosixPath(old)
                if (
                    old_path.parent == expected_parent
                    and not old.startswith("source/")
                    and (not filename or old_path.name == filename)
                ):
                    used.setdefault(container, set()).add(old_path.name.casefold())
                    moved[old] = old
                    record["path"] = old
                    return
                original = str(record.get("original_path", "")) or old.removeprefix("source/")
                if old not in moved:
                    new = (
                        _managed_named_relative(container, filename, used)
                        if filename else _managed_flat_relative(container, original, used)
                    )
                    source = _safe_asset_file(stage, old)
                    destination = stage / new
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source != destination:
                        if destination.exists():
                            raise LibraryError(f"Model layout destination already exists: {new}")
                        os.replace(source, destination)
                    moved[old] = new
                record["path"] = moved[old]

            source_model_records = list(updated.get("model_files", []))
            model_count = len(source_model_records)
            for record in updated.get("model_files", []):
                old = str(record.get("path", ""))
                relocate(
                    record,
                    (
                        "usd"
                        if str(record.get("format", "")).upper()
                        in {"USD", "USDA", "USDC", "USDZ"}
                        else "models"
                    ),
                    _model_managed_filename(
                        asset_token,
                        str(record.get("format", Path(old).suffix.lstrip("."))),
                        str(record.get("lod", "")),
                        str(record.get("role", "")),
                        Path(old).suffix,
                        model_count,
                    ),
                )
            derivative = updated.get("usd_derivative")
            if isinstance(derivative, dict):
                derivative_source = str(derivative.get("source_path", ""))
                if derivative_source in moved:
                    derivative["source_path"] = moved[derivative_source]
                derivative_entry = str(derivative.get("entry_path", ""))
                if derivative_entry in moved:
                    derivative["entry_path"] = moved[derivative_entry]
                for dependency in derivative.get("dependencies", []):
                    if not isinstance(dependency, dict):
                        continue
                    old = str(dependency.get("path", ""))
                    if old.startswith("derivatives/usd/"):
                        inner = PurePosixPath(old).relative_to("derivatives/usd")
                        relocate(
                            dependency,
                            (PurePosixPath("usd") / inner.parent).as_posix(),
                            inner.name,
                        )
                    elif old in moved:
                        dependency["path"] = moved[old]
            for record in updated.get("model_files", []):
                for dependency in record.get("dependencies", []):
                    if not isinstance(dependency, dict):
                        continue
                    old = str(dependency.get("path", ""))
                    if old.startswith("derivatives/usd/"):
                        inner = PurePosixPath(old).relative_to("derivatives/usd")
                        relocate(
                            dependency,
                            (PurePosixPath("usd") / inner.parent).as_posix(),
                            inner.name,
                        )
                    elif old in moved:
                        dependency["path"] = moved[old]
            texture_sets = updated.get("texture_sets", {})
            multiple_sets = len(texture_sets) > 1
            for set_name, texture_set in texture_sets.items():
                texture_token = (
                    f"{asset_token}_{_filename_token(str(set_name))}"
                    if multiple_sets else asset_token
                )
                for label, variant in texture_set.get("resolutions", {}).items():
                    for channel, alternatives in variant.get("maps", {}).items():
                        for record in alternatives:
                            old = str(record.get("path", ""))
                            texture_map = TextureMap(
                                channel=str(channel),
                                relative_path=old,
                                file_format=str(record.get("format", Path(old).suffix.lstrip("."))),
                                bit_depth=record.get("bit_depth"),
                                color_space=str(record.get("color_space", "")),
                                normal_convention=str(record.get("normal_convention", "")),
                                packed_channels=dict(record.get("packed_channels", {})),
                                preferred=bool(record.get("preferred", False)),
                                material=str(record.get("material", set_name)),
                                lod=str(record.get("lod", "")),
                            )
                            container = f"maps/{_safe_component(str(label))}"
                            desired = _map_destination(
                                Path(container),
                                texture_token,
                                str(label),
                                str(channel),
                                texture_map,
                                Path(old).suffix,
                                map_used,
                            )
                            relocate(record, container, desired.name)
            for record in updated.get("extra_files", []):
                relocate(record, "extras")

            for package in updated.get("provider_packages", []):
                if not isinstance(package, dict):
                    continue
                entry = str(package.get("entry_path", ""))
                if entry.startswith("packages/usd/"):
                    continue
                if entry in moved:
                    package["entry_path"] = moved[entry]
                for file_record in package.get("files", []):
                    if not isinstance(file_record, dict):
                        continue
                    file_path = str(file_record.get("path", ""))
                    if file_path in moved:
                        file_record["path"] = moved[file_path]
                    reference = str(file_record.get("reference_path", ""))
                    if not reference.startswith("models/textures/"):
                        continue
                    source = _safe_asset_file(stage, reference)
                    relative = PurePosixPath(reference).relative_to("models")
                    destination = stage / "usd" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        if _sha256_path(destination) != _sha256_path(source):
                            raise LibraryError(
                                "USD compatibility texture conflicts with "
                                f"{destination.relative_to(stage)}"
                            )
                        source.unlink()
                    else:
                        os.replace(source, destination)
                    file_record["reference_path"] = (
                        PurePosixPath("usd") / relative
                    ).as_posix()

            metadata: list[str] = []
            for value in updated.get("source_metadata", []):
                old = _safe_manifest_path(str(value))
                original = old.removeprefix("source/")
                if old not in moved:
                    new = _managed_flat_relative("metadata", original, used)
                    source = _safe_asset_file(stage, old)
                    destination = stage / new
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source != destination:
                        if destination.exists():
                            raise LibraryError(f"Model metadata destination already exists: {new}")
                        os.replace(source, destination)
                    moved[old] = new
                metadata.append(moved[old])
            updated["source_metadata"] = metadata

            for role, value in list(updated.get("previews", {}).items()):
                if not value:
                    continue
                old = _safe_manifest_path(str(value))
                if old not in moved:
                    new = _managed_named_relative(
                        "previews",
                        f"{asset_token}_{role.title()}{Path(old).suffix}",
                        used,
                    )
                    source = _safe_asset_file(stage, old)
                    destination = stage / new
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source != destination:
                        if destination.exists():
                            raise LibraryError(f"Model preview destination already exists: {new}")
                        os.replace(source, destination)
                    moved[old] = new
                updated["previews"][role] = moved[old]

            _organize_polyhaven_usd_packages(stage, updated, token)

            for directory in sorted(
                (path for path in stage.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts), reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            legacy_source = stage / "source"
            if legacy_source.is_dir():
                for directory in sorted(
                    (path for path in legacy_source.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts), reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                try:
                    legacy_source.rmdir()
                except OSError as error:
                    raise LibraryError("Could not remove the non-empty legacy source folder.") from error
            (stage / "models").mkdir(parents=True, exist_ok=True)
            (stage / "usd").mkdir(parents=True, exist_ok=True)
            updated["layout_version"] = MODEL_LAYOUT_VERSION
            _atomic_json(stage / "asset.json", updated)
            asset = _asset_from_manifest(updated, stage)
            if not isinstance(asset, LibraryModelAsset):
                raise LibraryError("Updated manifest is not a model asset.")
            os.replace(source_dir, backup)
            try:
                os.replace(stage, source_dir)
            except Exception:
                os.replace(backup, source_dir)
                raise
            shutil.rmtree(backup)
            _sync_directory(source_dir.parent)
            return _asset_from_manifest(updated, source_dir)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            if backup.exists() and not source_dir.exists():
                os.replace(backup, source_dir)
            raise

    def recovery_state(self) -> LibraryRecoveryState:
        control = self.root / ".ual"
        staging = control / "staging"
        directories = tuple(
            path.name for path in sorted(staging.iterdir(), key=lambda item: item.name.casefold()) if path.is_dir()
        ) if staging.is_dir() else ()
        lock_path = control / "import.lock"
        payload = _read_lock_payload(lock_path)
        return LibraryRecoveryState(
            directories,
            str(payload.get("pid", "unknown")) if lock_path.exists() else "",
            str(payload.get("host", "unknown")) if lock_path.exists() else "",
            str(payload.get("created_at", "")),
            _lock_is_local_stale(payload),
        )

    def cleanup_abandoned_staging(self) -> int:
        self.initialize()
        removed = 0
        with _ImportLock(self.root / ".ual" / "import.lock"):
            staging = self.root / ".ual" / "staging"
            for path in list(staging.iterdir()):
                if path.is_dir():
                    shutil.rmtree(path)
                    removed += 1
        return removed

    def recover_stale_lock(self, force: bool = False) -> bool:
        path = self.root / ".ual" / "import.lock"
        payload = _read_lock_payload(path)
        if not path.exists():
            return False
        if not force and not _lock_is_local_stale(payload):
            raise LibraryLockedError("Only a verified stale lock from this workstation can be recovered automatically.")
        path.unlink()
        _sync_directory(path.parent)
        return True

    def repair_legacy_names(
        self,
        progress: Callable[[RepairProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> RepairSummary:
        token = cancel_token or CancelToken()
        self.initialize()
        summary = RepairSummary()
        with _ImportLock(self.root / ".ual" / "import.lock"):
            candidates = self._legacy_manifests(summary)
            if not candidates:
                return summary
            required = sum(_directory_size(path.parent) for path, _document in candidates)
            if shutil.disk_usage(self.root).free < required + SPACE_CUSHION:
                raise LibraryError("The library does not have enough free space to repair asset names safely.")

            candidate_dirs = {path.parent.resolve() for path, _document in candidates}
            claimed = {
                path.resolve().as_posix().casefold()
                for path in (self.root / "textures").glob("*/*")
                if path.is_dir() and path.resolve() not in candidate_dirs
            }
            total = len(candidates)
            for index, (manifest_path, document) in enumerate(candidates):
                name = str(document.get("name", manifest_path.parent.name))
                if token.cancelled:
                    summary.canceled = True
                    break
                final_parent = self.root / "textures" / _slugify(str(document.get("category", "Uncategorized")))
                final = _claim_asset_destination(final_parent, _slugify(name), claimed)
                try:
                    repaired = self._repair_one(manifest_path.parent, final, document, token)
                except ImportCancelled:
                    summary.canceled = True
                    break
                except Exception as error:
                    summary.failed[name] = str(error)
                    claimed.discard(final.resolve().as_posix().casefold())
                    continue
                summary.renamed.append(repaired)
                if progress:
                    progress(RepairProgress(name, index + 1, total))
        return summary

    def _legacy_manifests(self, summary: RepairSummary | None = None) -> list[tuple[Path, dict]]:
        candidates: list[tuple[Path, dict]] = []
        textures = self.root / "textures"
        if not textures.is_dir():
            return candidates
        for path in sorted(textures.glob("**/asset.json"), key=lambda item: str(item).casefold()):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                _asset_from_manifest(document, path.parent)
            except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as error:
                if summary is not None:
                    summary.failed[path.parent.name] = str(error)
                continue
            if int(document.get("naming_version", 1)) < NAMING_VERSION:
                candidates.append((path, document))
            elif summary is not None:
                summary.skipped.append(str(document.get("name", path.parent.name)))
        return candidates

    def _repair_one(
        self,
        source_dir: Path,
        final: Path,
        document: dict,
        token: CancelToken,
    ) -> LibraryTextureAsset:
        repair_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / f"repair-{repair_id}"
        backup = self.root / ".ual" / "staging" / f"backup-{repair_id}"
        try:
            _build_repaired_asset(source_dir, stage, document, token)
            repaired_document = json.loads((stage / "asset.json").read_text(encoding="utf-8"))
            _asset_from_manifest(repaired_document, stage)
            token.check()
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source_dir, backup)
            try:
                os.replace(stage, final)
            except Exception:
                os.replace(backup, source_dir)
                raise
            shutil.rmtree(backup)
            return _asset_from_manifest(repaired_document, final)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            if backup.exists() and not source_dir.exists():
                os.replace(backup, source_dir)
            raise

    def import_materials(
        self,
        materials: Iterable[MaterialCandidate],
        progress: Callable[[ImportProgress], None] | None = None,
        cancel_token: CancelToken | None = None,
        preflight_result: PreflightResult | None = None,
        conflict_decisions: dict[str, str] | None = None,
    ) -> ImportSummary:
        selected = list(materials)
        token = cancel_token or CancelToken()
        preflight = preflight_result or self.preflight_materials(selected, cancel_token=token)
        decisions = conflict_decisions or {}
        summary = ImportSummary()
        valid_materials: list[tuple[MaterialCandidate, MaterialPreflight]] = []
        for material in selected:
            item = preflight.for_material(material)
            if item is None:
                summary.failed[material.name] = "Material has not completed preflight."
            elif item.status == "Duplicate":
                summary.skipped.append(f"{material.name}: {item.duplicate_reason or 'matching content fingerprint'}")
            elif item.status == "Conflict" and decisions.get(material.material_key, decisions.get(str(material.source_root), "skip")) != "separate":
                summary.skipped.append(f"{material.name}: provider ID conflict")
            elif item.status in {"Invalid", "Stale"} or item.has_errors:
                messages = "; ".join(diagnostic.message for diagnostic in item.diagnostics if diagnostic.severity == "error")
                summary.failed[material.name] = messages or f"Preflight status is {item.status}."
            else:
                valid_materials.append((material, item))
        if preflight.canceled:
            summary.canceled = True
        if not valid_materials:
            return summary
        total_bytes = sum(_material_source_bytes(material) for material, _item in valid_materials)
        if shutil.disk_usage(self.root).free < total_bytes + SPACE_CUSHION:
            raise LibraryError("The library does not have enough free space for this import.")
        completed_bytes = 0

        with (
            BlenderPreviewSession(self.blender_path) as preview_session,
            _ImportLock(self.root / ".ual" / "import.lock"),
        ):
            existing = self.list_assets()
            provider_keys = {
                (asset.asset_type, asset.provider.casefold(), asset.provider_id)
                for asset in existing if asset.provider_id
            }
            fingerprints_by_type: dict[str, set[str]] = {}
            for asset in existing:
                if asset.fingerprint:
                    fingerprints_by_type.setdefault(asset.asset_type, set()).add(asset.fingerprint)
            for material, preflight_item in valid_materials:
                if token.cancelled:
                    summary.canceled = True
                    break
                provider_key = (material.asset_type, material.provider.casefold(), material.provider_id)
                fingerprints = fingerprints_by_type.setdefault(material.asset_type, set())
                if preflight_item.fingerprint in fingerprints:
                    summary.skipped.append(f"{material.name}: matching content fingerprint")
                    continue
                if material.provider_id and provider_key in provider_keys and decisions.get(material.material_key, decisions.get(str(material.source_root), "skip")) != "separate":
                    summary.skipped.append(f"{material.name}: matching provider ID")
                    continue
                try:
                    asset, used_bytes = self._import_one(
                        material,
                        completed_bytes,
                        total_bytes,
                        progress,
                        token,
                        fingerprints,
                        preflight_item,
                        preview_session,
                    )
                except ImportCancelled:
                    summary.canceled = True
                    break
                except Exception as error:
                    summary.failed[material.name] = str(error)
                    continue
                completed_bytes += used_bytes
                if asset is None:
                    summary.skipped.append(f"{material.name}: matching content fingerprint")
                    continue
                summary.imported.append(asset)
                fingerprints.add(asset.fingerprint)
                if asset.provider_id:
                    provider_keys.add((asset.asset_type, asset.provider.casefold(), asset.provider_id))
        self._cache_assets_best_effort(summary.imported)
        return summary

    def _cache_assets_best_effort(
        self,
        assets: Iterable[
            LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset
        ],
    ) -> None:
        """Publish managed mutations to the disposable index without risking library writes."""
        try:
            from .catalog import CatalogIndex, CatalogRecord

            index = CatalogIndex.for_library(self.root)
            for asset in assets:
                manifest = asset.asset_dir / "asset.json"
                if not manifest.is_file():
                    # Legacy flat Stock assets keep their manifest beside the
                    # payload. They are uncommon and safe to discover here
                    # because this runs only after a ShotBox mutation.
                    manifest = next(
                        (
                            path
                            for path in _stock_manifest_paths(self.root)
                            if json.loads(path.read_text(encoding="utf-8-sig")).get("id")
                            == asset.id
                        ),
                        manifest,
                    )
                if manifest.is_file():
                    index.upsert(CatalogRecord.from_manifest(asset, manifest))
        except Exception:
            # Cache acceleration must never make an authoritative library
            # mutation fail.
            return

    def import_assets(self, assets: Iterable[MaterialCandidate], **kwargs) -> ImportSummary:
        """Type-neutral entry point; import_materials remains for compatibility."""
        return self.import_materials(assets, **kwargs)

    def import_hdris(self, assets: Iterable[HdriCandidate], **kwargs) -> ImportSummary:
        return self.import_materials(assets, **kwargs)

    def import_models(self, assets: Iterable[ModelCandidate], **kwargs) -> ImportSummary:
        return self.import_materials(assets, **kwargs)

    def import_atlases(self, assets: Iterable[MaterialCandidate], **kwargs) -> ImportSummary:
        selected = list(assets)
        for asset in selected:
            asset.asset_type = "atlas"
        return self.import_materials(selected, **kwargs)

    def import_stock(self, assets: Iterable[StockCandidate], **kwargs) -> ImportSummary:
        return self.import_materials(assets, **kwargs)

    def import_vdbs(self, assets: Iterable[VdbCandidate], **kwargs) -> ImportSummary:
        return self.import_materials(assets, **kwargs)

    def _import_one(
        self,
        material: MaterialCandidate,
        completed_before: int,
        total_bytes: int,
        progress: Callable[[ImportProgress], None] | None,
        token: CancelToken,
        existing_fingerprints: set[str],
        preflight: MaterialPreflight,
        preview_session: BlenderPreviewSession | None = None,
    ) -> tuple[AssetRecord | None, int]:
        if isinstance(material, StockCandidate):
            return self._import_one_stock(
                material, completed_before, total_bytes, progress, token,
                existing_fingerprints, preflight,
            )
        if isinstance(material, VdbCandidate):
            return self._import_one_vdb(
                material, completed_before, total_bytes, progress, token,
                existing_fingerprints, preflight,
            )
        if isinstance(material, ModelCandidate):
            return self._import_one_model(
                material, completed_before, total_bytes, progress, token,
                existing_fingerprints, preflight,
            )
        if isinstance(material, HdriCandidate):
            return self._import_one_hdri(
                material, completed_before, total_bytes, progress, token,
                existing_fingerprints, preflight, preview_session,
            )
        asset_id = str(uuid4())
        _validate_archive_source(material, preflight.archive_sha256)
        stage = self.root / ".ual" / "staging" / asset_id
        stage.mkdir(parents=True, exist_ok=False)
        copied_bytes = 0
        used_names: dict[Path, set[str]] = {}
        asset_token = _filename_token(material.name)
        manifest_resolutions: dict[str, dict] = {}
        fingerprint_records: list[str] = []
        try:
            for label in material.resolution_labels:
                variant = material.resolutions[label]
                map_groups: dict[str, list[dict]] = {}
                for channel in sorted(variant.maps, key=str.casefold):
                    records: list[dict] = []
                    for source_map in _ordered_map_alternatives(variant.maps[channel]):
                        source = _safe_source(material.source_root, source_map.relative_path)
                        destination_dir = stage / "maps" / _safe_component(label)
                        convert_webp = source.suffix.casefold() == ".webp"
                        destination = _map_destination(
                            destination_dir,
                            asset_token,
                            label,
                            channel,
                            source_map,
                            ".jpg" if convert_webp else source.suffix,
                            used_names,
                        )
                        if convert_webp:
                            processed, source_digest, size, digest = _convert_webp_to_jpeg(
                                source, destination, material.name, progress, token,
                                completed_before + copied_bytes, total_bytes,
                                material.source_snapshots.get(source_map.relative_path),
                                preflight.hashes.get(source_map.relative_path, ""),
                            )
                        else:
                            processed, digest = _copy_hash(
                                source, destination, material.name, progress, token,
                                completed_before + copied_bytes, total_bytes,
                                material.source_snapshots.get(source_map.relative_path),
                                preflight.hashes.get(source_map.relative_path, ""),
                            )
                            source_digest = digest
                            size = processed
                        copied_bytes += processed
                        relative = destination.relative_to(stage).as_posix()
                        records.append(_map_manifest(
                            source_map,
                            relative,
                            size,
                            digest,
                            format_override="JPEG" if convert_webp else None,
                        ))
                        fingerprint_records.append(f"{label}|{channel}|{source_digest}")
                    map_groups[channel] = records
                manifest_resolutions[label] = {
                    "width": variant.width,
                    "height": variant.height,
                    "maps": map_groups,
                }

            fingerprint = hashlib.sha256("\n".join(sorted(fingerprint_records)).encode("utf-8")).hexdigest()
            if preflight.fingerprint and fingerprint != preflight.fingerprint:
                raise StaleSourceError(f"{material.name} changed after preflight; rescan before importing.")
            if fingerprint in existing_fingerprints:
                shutil.rmtree(stage)
                return None, copied_bytes

            preview_manifest: dict[str, str] = {"thumbnail": "", "hero": ""}
            preview_sources: dict[str, str] = {}
            preview_original_paths: dict[str, str | None] = {
                "thumbnail": None, "hero": None,
            }
            for role, relative_source in (("thumbnail", material.selected_thumbnail), ("hero", material.selected_hero)):
                if not relative_source:
                    continue
                if relative_source in preview_sources:
                    preview_manifest[role] = preview_sources[relative_source]
                    preview_original_paths[role] = relative_source
                    continue
                source = _safe_source(material.source_root, relative_source)
                convert_webp = source.suffix.casefold() == ".webp"
                suffix = ".jpg" if convert_webp else _safe_suffix(source.suffix)
                destination = stage / "previews" / f"{asset_token}_{role.title()}{suffix}"
                if convert_webp:
                    processed, _source_digest, _size, _digest = _convert_webp_to_jpeg(
                        source, destination, material.name, progress, token,
                        completed_before + copied_bytes, total_bytes,
                        material.source_snapshots.get(relative_source),
                        preflight.hashes.get(relative_source, ""),
                    )
                else:
                    processed, _digest = _copy_hash(
                        source, destination, material.name, progress, token,
                        completed_before + copied_bytes, total_bytes,
                        material.source_snapshots.get(relative_source),
                        preflight.hashes.get(relative_source, ""),
                    )
                copied_bytes += processed
                stored = destination.relative_to(stage).as_posix()
                preview_manifest[role] = stored
                preview_original_paths[role] = relative_source
                preview_sources[relative_source] = stored

            texture_render: TexturePreviewResult | None = None
            if material.asset_type == "texture_set":
                selected_variant = select_texture_variant(manifest_resolutions)
                selected_label = selected_variant[0] if selected_variant else ""
                selected_maps = (
                    select_texture_maps(selected_variant[1])
                    if selected_variant
                    else {}
                )
                request_maps = tuple(
                    TexturePreviewMap(
                        channel=channel,
                        path=stage / str(record["path"]),
                        source_relative=str(record["path"]),
                        sha256=str(record.get("sha256", "")),
                        color_space=str(record.get("color_space", "")),
                        normal_convention=str(
                            record.get("normal_convention", "")
                        ),
                        packed_channels=dict(
                            record.get("packed_channels", {})
                        ),
                    )
                    for channel, record in selected_maps.items()
                )
                has_source_preview = any(
                    not preview.fallback
                    for preview in material.previews
                )
                if has_source_preview:
                    texture_render = TexturePreviewResult(
                        "source",
                        resolution=selected_label,
                        sources={
                            item.channel: item.source_relative
                            for item in request_maps
                        },
                        diagnostic=(
                            "A source preview was found; automatic Blender "
                            "rendering was skipped."
                        ),
                        metadata={
                            "type": "texture_shader",
                            "status": "source",
                            "resolution": selected_label,
                            "sources": {
                                item.channel: item.source_relative
                                for item in request_maps
                            },
                            "generated_at": "",
                            "blender_version": "",
                            "template_sha256": "",
                            "diagnostic": (
                                "A source preview was found; automatic Blender "
                                "rendering was skipped."
                            ),
                        },
                    )
                elif "Base Color" not in selected_maps:
                    texture_render = TexturePreviewResult(
                        "unsupported",
                        resolution=selected_label,
                        sources={
                            item.channel: item.source_relative
                            for item in request_maps
                        },
                        diagnostic=(
                            "The selected texture resolution has no Base Color map."
                        ),
                        metadata={
                            "type": "texture_shader",
                            "status": "unsupported",
                            "resolution": selected_label,
                            "sources": {
                                item.channel: item.source_relative
                                for item in request_maps
                            },
                            "generated_at": "",
                            "blender_version": "",
                            "template_sha256": "",
                            "diagnostic": (
                                "The selected texture resolution has no Base Color map."
                            ),
                        },
                    )
                elif not self.render_texture_previews:
                    texture_render = TexturePreviewResult(
                        "pending",
                        resolution=selected_label,
                        sources={
                            item.channel: item.source_relative
                            for item in request_maps
                        },
                        diagnostic=(
                            "Automatic texture preview rendering is disabled."
                        ),
                        metadata={
                            "type": "texture_shader",
                            "status": "pending",
                            "resolution": selected_label,
                            "sources": {
                                item.channel: item.source_relative
                                for item in request_maps
                            },
                            "generated_at": "",
                            "blender_version": "",
                            "template_sha256": "",
                            "diagnostic": (
                                "Automatic texture preview rendering is disabled."
                            ),
                        },
                    )
                else:
                    render_stage = stage / ".texture-preview-render"
                    request = TexturePreviewRequest(
                        output_dir=render_stage,
                        asset_name=material.name,
                        resolution=selected_label,
                        maps=request_maps,
                        blender_path=self.blender_path,
                        template_path=self.texture_template_path,
                        save_blend_file=self.save_texture_preview_blend,
                    )
                    try:
                        texture_render = render_texture_preview(
                            request,
                            progress=(
                                lambda phase: progress(
                                    ImportProgress(
                                        material.name,
                                        phase,
                                        completed_before + copied_bytes,
                                        total_bytes,
                                    )
                                )
                            )
                            if progress
                            else None,
                            cancel_token=token,
                            session=preview_session,
                        )
                        if (
                            texture_render.status == "ready"
                            and texture_render.hero_path
                            and texture_render.thumbnail_path
                        ):
                            preview_dir = stage / "previews"
                            preview_dir.mkdir(parents=True, exist_ok=True)
                            hero = preview_dir / texture_render.hero_path.name
                            same_image = (
                                texture_render.thumbnail_path
                                == texture_render.hero_path
                            )
                            os.replace(texture_render.hero_path, hero)
                            thumbnail = (
                                hero
                                if same_image
                                else preview_dir
                                / texture_render.thumbnail_path.name
                            )
                            if not same_image:
                                os.replace(
                                    texture_render.thumbnail_path, thumbnail
                                )
                            if texture_render.blend_path is not None:
                                debug_blend = (
                                    preview_dir
                                    / texture_render.blend_path.name
                                )
                                os.replace(
                                    texture_render.blend_path, debug_blend
                                )
                                texture_render.blend_path = debug_blend
                                texture_render.metadata["debug_blend"] = (
                                    debug_blend.relative_to(stage).as_posix()
                                )
                            texture_render.hero_path = hero
                            texture_render.thumbnail_path = thumbnail
                            preview_manifest["hero"] = hero.relative_to(
                                stage
                            ).as_posix()
                            preview_manifest["thumbnail"] = thumbnail.relative_to(
                                stage
                            ).as_posix()
                    except Exception as error:
                        texture_render = TexturePreviewResult(
                            "failed",
                            resolution=selected_label,
                            sources={
                                item.channel: item.source_relative
                                for item in request_maps
                            },
                            diagnostic=(
                                f"Texture preview rendering failed: {error}"
                            ),
                            metadata={
                                "type": "texture_shader",
                                "status": "failed",
                                "resolution": selected_label,
                                "sources": {
                                    item.channel: item.source_relative
                                    for item in request_maps
                                },
                                "generated_at": _utc_now(),
                                "blender_version": "",
                                "template_sha256": "",
                                "diagnostic": (
                                    f"Texture preview rendering failed: {error}"
                                ),
                            },
                        )
                    finally:
                        if render_stage.exists():
                            shutil.rmtree(render_stage, ignore_errors=True)
                    if texture_render.status == "canceled":
                        token.cancel()
                        token.check()

            metadata_manifest: list[str] = []
            metadata_original_paths: dict[str, str] = {}
            for relative_source in material.metadata_paths:
                source = _safe_source(material.source_root, relative_source)
                destination = stage / "metadata" / _safe_manifest_path(relative_source)
                size, _digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(relative_source),
                    preflight.hashes.get(relative_source, ""),
                )
                copied_bytes += size
                managed = destination.relative_to(stage).as_posix()
                metadata_manifest.append(managed)
                metadata_original_paths[managed] = relative_source

            extras_manifest: list[dict] = []
            for relative_source in _material_extra_paths(material):
                source = _safe_source(material.source_root, relative_source)
                destination = stage / "extras" / _safe_manifest_path(relative_source)
                size, digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(relative_source),
                    preflight.hashes.get(relative_source, ""),
                )
                copied_bytes += size
                extras_manifest.append({
                    "path": destination.relative_to(stage).as_posix(),
                    "original_path": relative_source,
                    "format": source.suffix.lstrip(".").upper(),
                    "size": size,
                    "sha256": digest,
                })

            now = _utc_now()
            previews_manifest: dict = dict(preview_manifest)
            if material.asset_type == "texture_set":
                previews_manifest["render"] = (
                    texture_render.metadata
                    if texture_render
                    else {
                        "type": "texture_shader",
                        "status": "unsupported",
                        "resolution": "",
                        "sources": {},
                        "generated_at": "",
                        "blender_version": "",
                        "template_sha256": "",
                        "diagnostic": (
                            "No local texture resolution was available for preview rendering."
                        ),
                    }
                )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "rating": 0,
                "naming_version": NAMING_VERSION,
                "id": asset_id,
                "type": material.asset_type if material.asset_type == "atlas" else "texture_set",
                "name": material.name,
                "slug": _slugify(material.name),
                "category": material.category,
                "tags": list(_clean_tags(material.tags)),
                "description": material.description,
                "author": material.author,
                "physical_size": material.physical_size,
                "provider": {"name": material.provider, "id": material.provider_id},
                "created_at": now,
                "updated_at": now,
                "source": _source_manifest(material, preflight.archive_sha256),
                "resolutions": manifest_resolutions,
                "previews": previews_manifest,
                "preview_original_paths": preview_original_paths,
                "source_metadata": metadata_manifest,
                "source_metadata_original_paths": metadata_original_paths,
                "extra_files": extras_manifest,
                "fingerprint": fingerprint,
            }
            _atomic_json(stage / "asset.json", manifest)
            _asset_from_manifest(manifest, stage)
            container = "atlases" if material.asset_type == "atlas" else "textures"
            final_parent = self.root / container / _slugify(material.category)
            final_parent.mkdir(parents=True, exist_ok=True)
            final = _unique_asset_destination(final_parent, _slugify(material.name))
            os.replace(stage, final)
            _sync_directory(final_parent)
            return _asset_from_manifest(manifest, final), copied_bytes
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    def _import_one_stock(
        self,
        material: StockCandidate,
        completed_before: int,
        total_bytes: int,
        progress: Callable[[ImportProgress], None] | None,
        token: CancelToken,
        existing_fingerprints: set[str],
        preflight: MaterialPreflight,
    ) -> tuple[LibraryStockAsset | None, int]:
        if not material.media_info:
            raise LibraryError(f"{material.name} has no valid video metadata.")
        ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        if not ffmpeg:
            raise LibraryError("FFmpeg was not found. Configure it in Settings before importing Stock footage.")
        asset_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / asset_id
        stage.mkdir(parents=True, exist_ok=False)
        copied_bytes = 0
        final_parent = self.root / "stock" / _slugify(material.category)
        final_parent.mkdir(parents=True, exist_ok=True)
        asset_token = _unique_stock_token(final_parent, _filename_token(material.name))
        published: list[Path] = []
        try:
            source = _safe_source(material.source_root, material.source_video)
            source_destination = stage / f"{asset_token}{_safe_suffix(source.suffix)}"
            size, source_digest = _copy_hash(
                source, source_destination, material.name, progress, token,
                completed_before + copied_bytes, total_bytes,
                material.source_snapshots.get(material.source_video),
                preflight.hashes.get(material.source_video, ""),
            )
            copied_bytes += size
            fingerprint = source_digest
            if preflight.fingerprint and fingerprint != preflight.fingerprint:
                raise StaleSourceError(f"{material.name} changed after preflight; rescan before importing.")
            if fingerprint in existing_fingerprints:
                shutil.rmtree(stage)
                return None, copied_bytes

            preview_origin = "generated"
            selected_info = None
            preview_destination: Path
            if material.preview_policy == "use_existing" and material.selected_preview:
                preview_source = _safe_source(material.source_root, material.selected_preview)
                preview_destination = (
                    stage / f"{asset_token}_Preview{_safe_suffix(preview_source.suffix)}"
                )
                preview_size, _preview_digest = _copy_hash(
                    preview_source, preview_destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(material.selected_preview),
                    preflight.hashes.get(material.selected_preview, ""),
                )
                copied_bytes += preview_size
                selected_info = next(
                    (
                        item.media_info for item in material.preview_candidates
                        if item.relative_path == material.selected_preview
                    ),
                    None,
                )
                preview_origin = "existing"
            else:
                preview_destination = stage / f"{asset_token}_Preview.mp4"
                generate_stock_preview(
                    source_destination,
                    preview_destination,
                    material.media_info,
                    material.preview_profile,
                    ffmpeg,
                    token,
                    (
                        lambda phase: progress(ImportProgress(
                            material.name, phase, completed_before + copied_bytes, total_bytes,
                        ))
                    ) if progress else None,
                )

            preview_duration = selected_info.duration if selected_info else material.media_info.duration
            thumbnail_destination = stage / f"{asset_token}_Thumbnail.jpg"
            thumbnail_time = generate_midpoint_thumbnail(
                preview_destination,
                thumbnail_destination,
                preview_duration,
                ffmpeg,
                token,
                (
                    lambda phase: progress(ImportProgress(
                        material.name, phase, completed_before + copied_bytes, total_bytes,
                    ))
                ) if progress else None,
            )
            preview_digest = _sha256_file(preview_destination)
            thumbnail_digest = _sha256_file(thumbnail_destination)

            now = _utc_now()
            info = material.media_info
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "rating": 0,
                "naming_version": NAMING_VERSION,
                "layout_version": STOCK_LAYOUT_VERSION,
                "id": asset_id,
                "type": "stock",
                "name": material.name,
                "slug": _slugify(material.name),
                "category": material.category,
                "tags": list(_clean_tags(material.tags)),
                "description": material.description,
                "author": material.author,
                "physical_size": "",
                "provider": {"name": material.provider, "id": material.provider_id},
                "created_at": now,
                "updated_at": now,
                "source": {
                    "path": source_destination.relative_to(stage).as_posix(),
                    "original_path": str(source),
                    "format": source.suffix.lstrip(".").upper(),
                    "size": size,
                    "sha256": source_digest,
                },
                "media": _stock_media_document(info),
                "preview": {
                    "video": preview_destination.relative_to(stage).as_posix(),
                    "thumbnail": thumbnail_destination.relative_to(stage).as_posix(),
                    "original_path": (
                        str(_safe_source(material.source_root, material.selected_preview))
                        if preview_origin == "existing" and material.selected_preview
                        else None
                    ),
                    "thumbnail_original_path": None,
                    "origin": preview_origin,
                    "profile": material.preview_profile.name,
                    "thumbnail_time": thumbnail_time,
                    "video_size": preview_destination.stat().st_size,
                    "video_sha256": preview_digest,
                    "thumbnail_size": thumbnail_destination.stat().st_size,
                    "thumbnail_sha256": thumbnail_digest,
                },
                "source_metadata": [],
                "extra_files": [],
                "fingerprint": fingerprint,
            }
            manifest_name = f"{asset_token}.json"
            _atomic_json(stage / manifest_name, manifest)
            asset = _asset_from_manifest(manifest, stage)
            if not isinstance(asset, LibraryStockAsset):
                raise LibraryError("Imported manifest did not produce a Stock asset.")
            for source_file in (
                source_destination, preview_destination, thumbnail_destination,
            ):
                destination = final_parent / source_file.name
                os.replace(source_file, destination)
                published.append(destination)
            manifest_destination = final_parent / manifest_name
            os.replace(stage / manifest_name, manifest_destination)
            published.append(manifest_destination)
            _sync_directory(final_parent)
            stage.rmdir()
            return _asset_from_manifest(manifest, final_parent), copied_bytes
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            for path in reversed(published):
                path.unlink(missing_ok=True)
            if published:
                _sync_directory(final_parent)
            raise

    def _import_one_hdri(
        self,
        material: HdriCandidate,
        completed_before: int,
        total_bytes: int,
        progress: Callable[[ImportProgress], None] | None,
        token: CancelToken,
        existing_fingerprints: set[str],
        preflight: MaterialPreflight,
        preview_session: BlenderPreviewSession | None = None,
    ) -> tuple[LibraryHdriAsset | None, int]:
        asset_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / asset_id
        stage.mkdir(parents=True, exist_ok=False)
        copied_bytes = 0
        fingerprint_records: list[str] = []
        manifest_resolutions: dict[str, dict] = {}
        used: set[str] = set()
        try:
            for label in material.resolution_labels:
                variant = material.resolutions[label]
                records: list[dict] = []
                for source_file in sorted(variant.files, key=lambda item: (not item.preferred, item.relative_path.casefold())):
                    source = _safe_source(material.source_root, source_file.relative_path)
                    filename = _safe_filename(source.name)
                    stem, suffix = Path(filename).stem, Path(filename).suffix
                    counter = 2
                    while filename.casefold() in used:
                        filename = f"{stem}_{counter}{suffix}"
                        counter += 1
                    used.add(filename.casefold())
                    destination = stage / "maps" / filename
                    size, digest = _copy_hash(
                        source, destination, material.name, progress, token,
                        completed_before + copied_bytes, total_bytes,
                        material.source_snapshots.get(source_file.relative_path),
                        preflight.hashes.get(source_file.relative_path, ""),
                    )
                    copied_bytes += size
                    records.append({
                        "path": destination.relative_to(stage).as_posix(),
                        "original_path": source_file.relative_path,
                        "format": source_file.file_format,
                        "size": size,
                        "sha256": digest,
                        "preferred": source_file.preferred,
                    })
                    fingerprint_records.append(f"{label}|Environment|{digest}")
                manifest_resolutions[label] = {
                    "width": variant.width,
                    "height": variant.height,
                    "files": records,
                }

            fingerprint = hashlib.sha256("\n".join(sorted(fingerprint_records)).encode("utf-8")).hexdigest()
            if preflight.fingerprint and fingerprint != preflight.fingerprint:
                raise StaleSourceError(f"{material.name} changed after preflight; rescan before importing.")
            if fingerprint in existing_fingerprints:
                shutil.rmtree(stage)
                return None, copied_bytes

            preview_path = stage / "previews" / f"{_filename_token(material.name)}_Thumbnail.jpg"
            _write_hdri_thumbnail(material, preview_path, token)
            preview_relative = preview_path.relative_to(stage).as_posix()

            render_result: HdriPreviewResult | None = None
            existing_webp = (
                material.selected_thumbnail
                if material.selected_thumbnail
                and Path(material.selected_thumbnail).suffix.casefold() == ".webp"
                else ""
            )
            if existing_webp:
                preview_image = QImage(str(preview_path))
                render_result = HdriPreviewResult(
                    "ready",
                    preview_path,
                    preview_path,
                    existing_webp,
                    metadata={
                        "type": "existing_preview",
                        "status": "ready",
                        "source": existing_webp,
                        "width": preview_image.width(),
                        "height": preview_image.height(),
                        "generated_at": _utc_now(),
                        "blender_version": "",
                        "template_sha256": "",
                        "diagnostic": "Converted the existing WebP preview to JPEG.",
                    },
                )

            selected = select_hdri_variant(manifest_resolutions)
            if not existing_webp and selected:
                _selected_label, selected_variant = selected
                selected_file = select_hdri_file(selected_variant)
                if selected_file:
                    selected_relative = str(selected_file["path"])
                    render_stage = stage / ".preview-render"
                    request = HdriPreviewRequest(
                        hdri_path=stage / selected_relative,
                        output_dir=render_stage,
                        asset_name=material.name,
                        resolutions=tuple(material.resolution_labels),
                        source_relative=selected_relative,
                        blender_path=self.blender_path,
                        template_path=self.hdri_template_path,
                    )
                    if self.render_hdri_previews:
                        try:
                            render_result = render_hdri_preview(
                                request,
                                progress=(lambda phase: progress(ImportProgress(
                                    material.name, phase, completed_before + copied_bytes, total_bytes,
                                ))) if progress else None,
                                cancel_token=token,
                                session=preview_session,
                            )
                            if render_result.status == "ready" and render_result.hero_path and render_result.thumbnail_path:
                                preview_dir = stage / "previews"
                                preview_dir.mkdir(parents=True, exist_ok=True)
                                hero = preview_dir / render_result.hero_path.name
                                same_image = render_result.thumbnail_path == render_result.hero_path
                                os.replace(render_result.hero_path, hero)
                                thumbnail = hero if same_image else preview_dir / render_result.thumbnail_path.name
                                if not same_image:
                                    os.replace(render_result.thumbnail_path, thumbnail)
                                render_result.hero_path = hero
                                render_result.thumbnail_path = thumbnail
                                if preview_path not in {hero, thumbnail}:
                                    preview_path.unlink(missing_ok=True)
                        except Exception as error:
                            render_result = HdriPreviewResult(
                                "failed",
                                source_relative=selected_relative,
                                diagnostic=f"HDRI preview rendering failed: {error}",
                                metadata={
                                    "type": "hdri_composite", "status": "failed", "source": selected_relative,
                                    "generated_at": _utc_now(), "blender_version": "", "template_sha256": "",
                                    "diagnostic": f"HDRI preview rendering failed: {error}",
                                },
                            )
                        finally:
                            if render_stage.exists():
                                shutil.rmtree(render_stage, ignore_errors=True)
                        if render_result.status == "canceled":
                            token.cancel()
                            token.check()
                    else:
                        render_result = HdriPreviewResult(
                            "pending",
                            source_relative=selected_relative,
                            diagnostic="Automatic HDRI preview rendering is disabled.",
                            metadata={
                                "type": "hdri_composite", "status": "pending",
                                "source": selected_relative,
                                "generated_at": "", "blender_version": "",
                                "template_sha256": "",
                                "diagnostic": "Automatic HDRI preview rendering is disabled.",
                            },
                        )

            metadata_manifest: list[str] = []
            metadata_original_paths: dict[str, str] = {}
            for relative_source in material.metadata_paths:
                source = _safe_source(material.source_root, relative_source)
                destination = stage / "metadata" / _safe_manifest_path(relative_source)
                size, _digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(relative_source),
                    preflight.hashes.get(relative_source, ""),
                )
                copied_bytes += size
                managed = destination.relative_to(stage).as_posix()
                metadata_manifest.append(managed)
                metadata_original_paths[managed] = relative_source

            extras_manifest: list[dict] = []
            for relative_source in _material_extra_paths(material):
                source = _safe_source(material.source_root, relative_source)
                destination = stage / "extras" / _safe_manifest_path(relative_source)
                size, digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(relative_source),
                    preflight.hashes.get(relative_source, ""),
                )
                copied_bytes += size
                extras_manifest.append({
                    "path": destination.relative_to(stage).as_posix(),
                    "original_path": relative_source,
                    "format": source.suffix.lstrip(".").upper(),
                    "size": size,
                    "sha256": digest,
                })

            now = _utc_now()
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "rating": 0,
                "naming_version": NAMING_VERSION,
                "layout_version": LAYOUT_VERSION,
                "id": asset_id,
                "type": "hdri",
                "name": material.name,
                "slug": _slugify(material.name),
                "category": material.category,
                "tags": list(_clean_tags(material.tags)),
                "description": material.description,
                "author": material.author,
                "provider": {"name": material.provider, "id": material.provider_id},
                "created_at": now,
                "updated_at": now,
                "source": {"original_path": str(material.source_root)},
                "resolutions": manifest_resolutions,
                "previews": {
                    "thumbnail": (
                        render_result.thumbnail_path.relative_to(stage).as_posix()
                        if render_result and render_result.status == "ready" and render_result.thumbnail_path else preview_relative
                    ),
                    "hero": (
                        render_result.hero_path.relative_to(stage).as_posix()
                        if render_result and render_result.status == "ready" and render_result.hero_path else preview_relative
                    ),
                    "render": render_result.metadata if render_result else {
                        "type": "hdri_composite", "status": "unsupported", "source": "",
                        "generated_at": "", "blender_version": "", "template_sha256": "",
                        "diagnostic": "No local HDRI map was available for preview rendering.",
                    },
                },
                "preview_original_paths": {
                    "thumbnail": existing_webp or None,
                    "hero": existing_webp or None,
                },
                "source_metadata": metadata_manifest,
                "source_metadata_original_paths": metadata_original_paths,
                "extra_files": extras_manifest,
                "fingerprint": fingerprint,
            }
            _atomic_json(stage / "asset.json", manifest)
            _asset_from_manifest(manifest, stage)
            final_parent = self.root / "hdris" / _slugify(material.category)
            final_parent.mkdir(parents=True, exist_ok=True)
            final = _unique_asset_destination(final_parent, _slugify(material.name))
            os.replace(stage, final)
            _sync_directory(final_parent)
            return _asset_from_manifest(manifest, final), copied_bytes
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    def _import_one_vdb(
        self,
        material: VdbCandidate,
        completed_before: int,
        total_bytes: int,
        progress: Callable[[ImportProgress], None] | None,
        token: CancelToken,
        existing_fingerprints: set[str],
        preflight: MaterialPreflight,
    ) -> tuple[LibraryVdbAsset | None, int]:
        asset_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / asset_id
        stage.mkdir(parents=True, exist_ok=False)
        copied_bytes = 0
        fingerprint_records: list[str] = []
        manifest_variants: dict[str, dict] = {}
        try:
            for label in material.resolution_labels:
                variant = material.variants[label]
                records: list[dict] = []
                used: set[str] = set()
                for source_file in variant.files:
                    source = _safe_source(material.source_root, source_file.relative_path)
                    filename = _safe_filename(source.name)
                    stem, suffix = Path(filename).stem, Path(filename).suffix
                    counter = 2
                    while filename.casefold() in used:
                        filename = f"{stem}_{counter}{suffix}"
                        counter += 1
                    used.add(filename.casefold())
                    destination = stage / "volumes" / _safe_component(label) / filename
                    size, digest = _copy_hash(
                        source, destination, material.name, progress, token,
                        completed_before + copied_bytes, total_bytes,
                        material.source_snapshots.get(source_file.relative_path),
                        preflight.hashes.get(source_file.relative_path, ""),
                    )
                    copied_bytes += size
                    records.append({
                        "path": destination.relative_to(stage).as_posix(),
                        "original_path": source_file.relative_path,
                        "format": "VDB",
                        "size": size,
                        "sha256": digest,
                        "frame": source_file.frame,
                        "padding": source_file.padding,
                    })
                    identity = source_file.frame if source_file.frame is not None else "static"
                    fingerprint_records.append(f"{label}|{identity}|{digest}")
                manifest_variants[label] = {
                    "label": label,
                    "mode": "sequence" if variant.is_sequence else "static",
                    "is_sequence": variant.is_sequence,
                    "frame_start": variant.frame_start,
                    "frame_end": variant.frame_end,
                    "padding": variant.padding,
                    "missing_frames": list(variant.missing_frames),
                    "files": records,
                }

            fingerprint = hashlib.sha256(
                "\n".join(sorted(fingerprint_records)).encode("utf-8")
            ).hexdigest()
            if preflight.fingerprint and fingerprint != preflight.fingerprint:
                raise StaleSourceError(
                    f"{material.name} changed after preflight; rescan before importing."
                )
            if fingerprint in existing_fingerprints:
                shutil.rmtree(stage)
                return None, copied_bytes

            preview_manifest = {
                "thumbnail": "", "hero": "", "video": "",
                "render": {
                    "type": "vdb_still",
                    "status": "pending",
                    "variant": "",
                    "frame": 1,
                    "density_scale": 100,
                    "mode": "still",
                    "generated_at": "",
                    "houdini_version": "",
                    "template_sha256": "",
                    "diagnostic": "Render a still manually from the VDB inspector.",
                },
            }
            preview_original_paths: dict[str, str | None] = {
                "thumbnail": None, "hero": None, "video": None,
            }
            if material.selected_thumbnail:
                source = _safe_source(material.source_root, material.selected_thumbnail)
                destination = stage / "previews" / (
                    f"{_filename_token(material.name)}_Thumbnail{_safe_suffix(source.suffix)}"
                )
                size, _digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(material.selected_thumbnail),
                    preflight.hashes.get(material.selected_thumbnail, ""),
                )
                copied_bytes += size
                stored = destination.relative_to(stage).as_posix()
                preview_manifest["thumbnail"] = stored
                preview_manifest["hero"] = stored
                preview_original_paths["thumbnail"] = material.selected_thumbnail
                preview_original_paths["hero"] = material.selected_thumbnail
            if material.selected_preview_video:
                source = _safe_source(material.source_root, material.selected_preview_video)
                destination = stage / "previews" / (
                    f"{_filename_token(material.name)}_Preview{_safe_suffix(source.suffix)}"
                )
                size, _digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(material.selected_preview_video),
                    preflight.hashes.get(material.selected_preview_video, ""),
                )
                copied_bytes += size
                preview_manifest["video"] = destination.relative_to(stage).as_posix()
                preview_original_paths["video"] = material.selected_preview_video
            if (
                not preview_manifest["thumbnail"]
                and preview_manifest["video"]
            ):
                ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
                ffprobe = resolve_ffprobe(self.ffmpeg_path)
                if ffmpeg and ffprobe:
                    video = stage / preview_manifest["video"]
                    thumbnail = stage / "previews" / (
                        f"{_filename_token(material.name)}_Thumbnail.jpg"
                    )
                    try:
                        media_info = probe_video(video, ffprobe, token)
                        generate_midpoint_thumbnail(
                            video,
                            thumbnail,
                            media_info.duration,
                            ffmpeg,
                            token,
                        )
                    except ScanCancelled as error:
                        raise ImportCancelled("Import canceled by user.") from error
                    except (StockProbeError, StockPreviewError):
                        pass
                    else:
                        relative = thumbnail.relative_to(stage).as_posix()
                        preview_manifest["thumbnail"] = relative
                        preview_manifest["hero"] = relative
            if not preview_manifest["thumbnail"]:
                placeholder = stage / "previews" / (
                    f"{_filename_token(material.name)}_Placeholder.jpg"
                )
                _write_vdb_placeholder(material.name, placeholder, token)
                relative = placeholder.relative_to(stage).as_posix()
                preview_manifest["thumbnail"] = relative
                preview_manifest["hero"] = relative

            now = _utc_now()
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "rating": 0,
                "naming_version": NAMING_VERSION,
                "layout_version": LAYOUT_VERSION,
                "id": asset_id,
                "type": "vdb",
                "name": material.name,
                "slug": _slugify(material.name),
                "category": material.category,
                "tags": list(_clean_tags(material.tags)),
                "description": material.description,
                "author": material.author,
                "physical_size": material.physical_size,
                "provider": {"name": material.provider, "id": material.provider_id},
                "created_at": now,
                "updated_at": now,
                "source": {"original_path": str(material.source_root)},
                "variants": manifest_variants,
                "previews": preview_manifest,
                "preview_original_paths": preview_original_paths,
                "source_metadata": [],
                "extra_files": [],
                "fingerprint": fingerprint,
            }
            _atomic_json(stage / "asset.json", manifest)
            _asset_from_manifest(manifest, stage)
            final_parent = self.root / "vdbs" / _slugify(material.category)
            final_parent.mkdir(parents=True, exist_ok=True)
            final = _unique_asset_destination(final_parent, _slugify(material.name))
            os.replace(stage, final)
            _sync_directory(final_parent)
            return _asset_from_manifest(manifest, final), copied_bytes
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    def _import_one_model(
        self,
        material: ModelCandidate,
        completed_before: int,
        total_bytes: int,
        progress: Callable[[ImportProgress], None] | None,
        token: CancelToken,
        existing_fingerprints: set[str],
        preflight: MaterialPreflight,
    ) -> tuple[LibraryModelAsset | None, int]:
        asset_id = str(uuid4())
        stage = self.root / ".ual" / "staging" / asset_id
        stage.mkdir(parents=True, exist_ok=False)
        copied_bytes = 0
        try:
            copied: dict[str, tuple[str, int, str]] = {}
            destinations = _model_source_destinations(material)
            for relative in _candidate_source_paths(material):
                source = _safe_source(material.source_root, relative)
                destination = stage / destinations[relative]
                size, digest = _copy_hash(
                    source, destination, material.name, progress, token,
                    completed_before + copied_bytes, total_bytes,
                    material.source_snapshots.get(relative),
                    preflight.hashes.get(relative, ""),
                )
                copied_bytes += size
                copied[relative] = (destination.relative_to(stage).as_posix(), size, digest)

            fingerprint_records = [
                f"{label}|{identity}|{copied[item.relative_path][2]}"
                for label, identity, item in _primary_file_records(material)
            ]
            fingerprint = hashlib.sha256("\n".join(sorted(fingerprint_records)).encode("utf-8")).hexdigest()
            if preflight.fingerprint and fingerprint != preflight.fingerprint:
                raise StaleSourceError(f"{material.name} changed after preflight; rescan before importing.")
            if fingerprint in existing_fingerprints:
                shutil.rmtree(stage)
                return None, copied_bytes

            model_manifest: list[dict] = []
            for item in material.model_files:
                stored, size, digest = copied[item.relative_path]
                model_manifest.append({
                    "path": stored,
                    "original_path": item.relative_path,
                    "format": item.file_format,
                    "role": item.role,
                    "lod": item.lod,
                    "component": item.component,
                    "triangle_count": item.triangle_count,
                    "preferred": item.preferred,
                    "size": size,
                    "sha256": digest,
                })

            texture_manifest: dict[str, dict] = {}
            for set_name, texture_set in sorted(material.texture_sets.items(), key=lambda value: value[0].casefold()):
                resolutions: dict[str, dict] = {}
                for label in _sorted_resolution_labels(texture_set.resolutions):
                    variant = texture_set.resolutions[label]
                    groups: dict[str, list[dict]] = {}
                    for channel, alternatives in sorted(variant.maps.items(), key=lambda value: value[0].casefold()):
                        records: list[dict] = []
                        for item in alternatives:
                            stored, size, digest = copied[item.relative_path]
                            record = _map_manifest(item, stored, size, digest)
                            record["original_path"] = item.relative_path
                            record["material"] = item.material
                            record["lod"] = item.lod
                            records.append(record)
                        groups[channel] = records
                    resolutions[label] = {"width": variant.width, "height": variant.height, "maps": groups}
                texture_manifest[set_name] = {"resolutions": resolutions}

            preview_manifest: dict[str, str] = {}
            preview_original_paths: dict[str, str | None] = {}
            for role, relative in (("thumbnail", material.selected_thumbnail), ("hero", material.selected_hero)):
                if relative and relative in copied:
                    preview_manifest[role] = copied[relative][0]
                    preview_original_paths[role] = relative
            if not preview_manifest.get("thumbnail"):
                placeholder = stage / "previews" / f"{_filename_token(material.name)}_Placeholder.jpg"
                _write_model_placeholder(material.name, placeholder, token)
                relative = placeholder.relative_to(stage).as_posix()
                preview_manifest["thumbnail"] = relative
                preview_original_paths["thumbnail"] = None
                preview_manifest.setdefault("hero", relative)
                preview_original_paths.setdefault("hero", None)
            elif not preview_manifest.get("hero"):
                preview_manifest["hero"] = preview_manifest["thumbnail"]
                preview_original_paths["hero"] = preview_original_paths.get("thumbnail")

            metadata_manifest = [copied[path][0] for path in material.metadata_paths if path in copied]
            metadata_original_paths = {
                copied[path][0]: path for path in material.metadata_paths if path in copied
            }
            extras_manifest = [
                {
                    "path": copied[path][0],
                    "original_path": path,
                    "format": Path(path).suffix.lstrip(".").upper(),
                    "size": copied[path][1],
                    "sha256": copied[path][2],
                }
                for path in _material_extra_paths(material)
                if path in copied
            ]
            now = _utc_now()
            (stage / "models").mkdir(parents=True, exist_ok=True)
            (stage / "usd").mkdir(parents=True, exist_ok=True)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "rating": 0,
                "naming_version": NAMING_VERSION,
                "layout_version": MODEL_LAYOUT_VERSION,
                "id": asset_id,
                "type": "model",
                "name": material.name,
                "slug": _slugify(material.name),
                "category": material.category,
                "tags": list(_clean_tags(material.tags)),
                "description": material.description,
                "author": material.author,
                "physical_size": material.physical_size,
                "dimensions": list(material.dimensions),
                "polycount": material.polycount,
                "provider": {"name": material.provider, "id": material.provider_id},
                "created_at": now,
                "updated_at": now,
                "source": {"original_path": str(material.source_root)},
                "model_files": model_manifest,
                "texture_sets": texture_manifest,
                "previews": preview_manifest,
                "preview_original_paths": preview_original_paths,
                "source_metadata": metadata_manifest,
                "source_metadata_original_paths": metadata_original_paths,
                "extra_files": extras_manifest,
                "excluded_files": [
                    {"path": path, "reason": reason}
                    for path, reason in sorted(material.excluded_paths.items(), key=lambda value: value[0].casefold())
                ],
                "fingerprint": fingerprint,
            }
            _atomic_json(stage / "asset.json", manifest)
            asset = _asset_from_manifest(manifest, stage)
            if not isinstance(asset, LibraryModelAsset):
                raise LibraryError("Imported manifest did not produce a model asset.")
            final_parent = self.root / "models" / _slugify(material.category)
            final_parent.mkdir(parents=True, exist_ok=True)
            final = _unique_asset_destination(final_parent, _slugify(material.name))
            os.replace(stage, final)
            _sync_directory(final_parent)
            return _asset_from_manifest(manifest, final), copied_bytes
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise


class MetadataPatchBatch:
    """One initialized and locked metadata-patch session.

    Manifest hints are accelerators only. They are constrained to the library,
    read, and matched to their expected IDs before use.
    """

    def __init__(
        self,
        repository: LibraryRepository,
        asset_ids: tuple[str, ...],
        manifest_hints: Mapping[str, str | Path],
        cancel_token: CancelToken,
    ) -> None:
        self.repository = repository
        self.asset_ids = asset_ids
        self.manifest_hints = dict(manifest_hints)
        self.cancel_token = cancel_token
        self._manifest_paths: dict[str, Path] = {}
        self._manifest_documents: dict[str, dict] = {}
        self._category_catalogs: dict[str, CategoryCatalog] = {}
        self._lock: _ImportLock | None = None
        self._entered = False

    def __enter__(self) -> MetadataPatchBatch:
        self.cancel_token.check()
        self.repository.initialize()
        lock = _ImportLock(self.repository.root / ".ual" / "import.lock")
        lock.__enter__()
        self._lock = lock
        try:
            self._prepare_manifest_paths()
        except Exception:
            self._release_lock()
            raise
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._entered = False
        self._release_lock(exc_type, exc, tb)

    def patch(
        self, asset_id: str, patch: AssetMetadataPatch
    ) -> MetadataPatchOutcome:
        if not self._entered:
            raise RuntimeError("Metadata patch batch is not active.")
        manifest_path = self._manifest_paths.get(asset_id)
        if manifest_path is None:
            raise LibraryError(
                f"Asset {asset_id} was not found in the configured library."
            )
        document = self._manifest_documents.pop(asset_id, None)
        if document is None:
            try:
                document = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError) as error:
                raise LibraryError(
                    f"Asset {asset_id} metadata could not be read: {error}"
                ) from error
        if not isinstance(document, dict) or str(document.get("id", "")) != asset_id:
            raise LibraryError(
                f"Asset {asset_id} no longer matches its catalog manifest."
            )
        if patch.rating is not None:
            if isinstance(patch.rating, bool) or not isinstance(patch.rating, int):
                raise LibraryError("Asset rating must be a whole number from 0 to 5.")
            if not 0 <= patch.rating <= 5:
                raise LibraryError("Asset rating must be between 0 and 5.")
            document["rating"] = patch.rating
        existing_tags = _clean_tags(document.get("tags", ()))
        update = AssetMetadataUpdate(
            name=str(document.get("name", "")).strip(),
            category=(
                str(document.get("category", ""))
                if patch.category is None
                else patch.category
            ),
            tags=_clean_tags((*existing_tags, *patch.add_tags)),
            author=str(document.get("author", "")),
            description=str(document.get("description", "")),
            physical_size=str(document.get("physical_size", "")),
        )
        asset_type = str(document.get("type", "texture_set"))
        catalog = self._category_catalogs.get(asset_type)
        if catalog is None:
            catalog = CategoryConfigStore(self.repository.root).load(asset_type)
            self._category_catalogs[asset_type] = catalog
        outcome = self.repository._update_asset_metadata_locked(
            asset_id,
            update,
            manifest=(manifest_path, document),
            category_catalog=catalog,
        )
        self._manifest_paths[asset_id] = outcome.manifest_path
        return outcome

    def _prepare_manifest_paths(self) -> None:
        root = self.repository.root.resolve()
        requested = set(self.asset_ids)
        for asset_id in self.asset_ids:
            self.cancel_token.check()
            hint = self.manifest_hints.get(asset_id)
            if hint is None:
                continue
            validated = self._validated_hint(root, asset_id, hint)
            if validated is not None:
                manifest_path, document = validated
                self._manifest_paths[asset_id] = manifest_path
                self._manifest_documents[asset_id] = document

        unresolved = requested - self._manifest_paths.keys()
        if not unresolved:
            return
        for manifest_path in _asset_manifest_paths(self.repository.root):
            self.cancel_token.check()
            try:
                document = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            asset_id = str(document.get("id", ""))
            if asset_id not in unresolved:
                continue
            self._manifest_paths[asset_id] = manifest_path
            self._manifest_documents[asset_id] = document
            unresolved.remove(asset_id)
            if not unresolved:
                break

    @staticmethod
    def _validated_hint(
        root: Path, asset_id: str, hint: str | Path
    ) -> tuple[Path, dict] | None:
        try:
            path = Path(hint).expanduser().absolute().resolve()
            path.relative_to(root)
            document = json.loads(path.read_text(encoding="utf-8"))
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            UnicodeError,
        ):
            return None
        if not isinstance(document, dict) or str(document.get("id", "")) != asset_id:
            return None
        return path, document

    def _release_lock(self, exc_type=None, exc=None, tb=None) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.__exit__(exc_type, exc, tb)


class _ImportLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            payload = _read_lock_payload(self.path)
            if _lock_is_local_stale(payload):
                try:
                    self.path.unlink()
                    self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except OSError as retry_error:
                    raise LibraryLockedError("A stale local import lock could not be recovered safely.") from retry_error
            else:
                owner = f"PID {payload.get('pid', '?')} on {payload.get('host', 'an unknown host')}"
                created = f" since {payload.get('created_at')}" if payload.get("created_at") else ""
                raise LibraryLockedError(f"Another import is using this library ({owner}{created}).") from error
        payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "created_at": _utc_now()}).encode("utf-8")
        os.write(self.fd, payload)
        os.fsync(self.fd)
        _sync_directory(self.path.parent)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)
        _sync_directory(self.path.parent)


def _read_lock_payload(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _lock_is_local_stale(payload: dict) -> bool:
    if not payload or str(payload.get("host", "")) != socket.gethostname():
        return False
    try:
        pid = int(payload["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    return not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _asset_from_manifest(
    document: dict, asset_dir: Path
) -> AssetRecord:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported asset manifest")
    if document.get("type") == "hdri":
        return _hdri_asset_from_manifest(document, asset_dir)
    if document.get("type") == "model":
        return _model_asset_from_manifest(document, asset_dir)
    if document.get("type") == "stock":
        return _stock_asset_from_manifest(document, asset_dir)
    if document.get("type") == "vdb":
        return _vdb_asset_from_manifest(document, asset_dir)
    if document.get("type") not in {"texture_set", "atlas"}:
        raise ValueError("Unsupported asset manifest")
    resolutions: dict[str, LibraryResolution] = {}
    total_size = 0
    for label, value in document.get("resolutions", {}).items():
        groups: dict[str, tuple[LibraryMap, ...]] = {}
        for channel, records in value.get("maps", {}).items():
            maps: list[LibraryMap] = []
            for record in records:
                relative = _safe_manifest_path(str(record["path"]))
                path = asset_dir / relative
                if not path.is_file():
                    raise ValueError(f"Missing asset file: {relative}")
                size = int(record.get("size", path.stat().st_size))
                total_size += size
                maps.append(LibraryMap(
                    channel=channel,
                    path=relative,
                    file_format=str(record.get("format", path.suffix.lstrip("."))).upper(),
                    size=size,
                    sha256=str(record.get("sha256", "")),
                    bit_depth=record.get("bit_depth"),
                    color_space=str(record.get("color_space", "")),
                    normal_convention=str(record.get("normal_convention", "")),
                    packed_channels=dict(record.get("packed_channels", {})),
                    preferred=bool(record.get("preferred", False)),
                    material=str(record.get("material", "")),
                    lod=str(record.get("lod", "")),
                ))
            groups[channel] = tuple(maps)
        resolutions[label] = LibraryResolution(label, value.get("width"), value.get("height"), groups)
    if not resolutions:
        raise ValueError("Manifest contains no resolutions")
    previews = document.get("previews", {})
    thumbnail = _optional_asset_path(asset_dir, previews.get("thumbnail"))
    hero = _optional_asset_path(asset_dir, previews.get("hero"))
    provider = document.get("provider", {})
    extra_files: list[LibraryExtraFile] = []
    for record in document.get("extra_files", []):
        if not isinstance(record, dict):
            raise ValueError("Invalid extra file record")
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        if not path.is_file():
            raise ValueError(f"Missing extra file: {relative}")
        size = int(record.get("size", path.stat().st_size))
        total_size += size
        extra_files.append(LibraryExtraFile(
            relative,
            str(record.get("original_path", "")),
            size,
            str(record.get("sha256", "")),
            str(record.get("format", path.suffix.lstrip("."))).upper(),
        ))
    source_metadata = _validated_support_paths(asset_dir, document.get("source_metadata", []))
    support_paths = {path for path in (thumbnail, hero) if path}
    support_paths.update(asset_dir / value for value in source_metadata)
    total_size += sum(path.stat().st_size for path in support_paths)
    package_sizes: dict[str, int] = {}
    provider_packages = _provider_packages_from_manifest(document, asset_dir, package_sizes)
    total_size += sum(package_sizes.values())
    category = _manifest_primary_category(document)
    return LibraryTextureAsset(
        id=str(document["id"]),
        name=str(document["name"]),
        category=category,
        tags=tuple(str(tag) for tag in document.get("tags", [])),
        description=str(document.get("description", "")),
        author=str(document.get("author", "")),
        physical_size=str(document.get("physical_size", "")),
        provider=_provider_name(provider.get("name")),
        provider_id=str(provider.get("id", "")),
        asset_dir=asset_dir,
        resolutions=resolutions,
        thumbnail_path=thumbnail,
        hero_path=hero,
        fingerprint=str(document.get("fingerprint", "")),
        created_at=str(document.get("created_at", "")),
        total_size=total_size,
        extra_files=tuple(extra_files),
        source_metadata=source_metadata,
        provider_packages=provider_packages,
        asset_type=str(document.get("type", "texture_set")),
        preview_render=(
            dict(previews.get("render", {}))
            if isinstance(previews.get("render", {}), dict)
            else {}
        ),
        rating=_manifest_rating(document),
    )


def _vdb_asset_from_manifest(document: dict, asset_dir: Path) -> LibraryVdbAsset:
    variants: dict[str, LibraryVdbVariant] = {}
    total_size = 0
    for label, value in document.get("variants", {}).items():
        if not isinstance(value, dict):
            raise ValueError("Invalid VDB variant record")
        canonical_label = str(value.get("label", label)).strip()
        if not canonical_label or canonical_label != str(label):
            raise ValueError("VDB variant label does not match its manifest key")
        is_sequence = bool(value.get("is_sequence", False))
        mode = str(value.get("mode", "sequence" if is_sequence else "static")).casefold()
        if mode not in {"static", "sequence"} or (mode == "sequence") != is_sequence:
            raise ValueError(f"VDB variant {label} has inconsistent sequence metadata")
        files: list[LibraryVdbFile] = []
        for record in value.get("files", []):
            if not isinstance(record, dict):
                raise ValueError("Invalid VDB file record")
            relative = _safe_manifest_path(str(record.get("path", "")))
            path = asset_dir / relative
            if path.suffix.casefold() != ".vdb" or not path.is_file():
                raise ValueError(f"Missing VDB file: {relative}")
            size = int(record.get("size", path.stat().st_size))
            total_size += size
            frame = record.get("frame")
            files.append(LibraryVdbFile(
                relative,
                str(record.get("original_path", "")),
                size,
                str(record.get("sha256", "")),
                int(frame) if frame is not None else None,
                int(record.get("padding", 0)),
            ))
        if not files:
            raise ValueError(f"VDB variant {label} contains no local files")
        frames = [item.frame for item in files if item.frame is not None]
        frame_start = int(value["frame_start"]) if value.get("frame_start") is not None else None
        frame_end = int(value["frame_end"]) if value.get("frame_end") is not None else None
        padding = int(value.get("padding", 0))
        missing_frames = tuple(int(frame) for frame in value.get("missing_frames", []))
        if is_sequence:
            if len(frames) != len(files) or len(set(frames)) != len(frames):
                raise ValueError(f"VDB variant {label} has invalid sequence frame records")
            if not frames or frame_start != min(frames) or frame_end != max(frames) or padding <= 0:
                raise ValueError(f"VDB variant {label} has an invalid frame range or padding")
            expected_missing = tuple(frame for frame in range(frame_start, frame_end + 1) if frame not in frames)
            if missing_frames != expected_missing:
                raise ValueError(f"VDB variant {label} has inconsistent missing-frame metadata")
        elif frames or frame_start is not None or frame_end is not None or padding or missing_frames:
            raise ValueError(f"Static VDB variant {label} contains sequence metadata")
        variants[str(label)] = LibraryVdbVariant(
            str(label),
            tuple(files),
            is_sequence,
            frame_start,
            frame_end,
            padding,
            missing_frames,
        )
    if not variants:
        raise ValueError("VDB manifest contains no variants")
    previews = document.get("previews", {})
    thumbnail = _optional_asset_path(asset_dir, previews.get("thumbnail"))
    hero = _optional_asset_path(asset_dir, previews.get("hero"))
    video = _optional_asset_path(asset_dir, previews.get("video"))
    for path in {item for item in (thumbnail, hero, video) if item}:
        total_size += path.stat().st_size
    extras: list[LibraryExtraFile] = []
    for record in document.get("extra_files", []):
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        if not path.is_file():
            raise ValueError(f"Missing VDB companion: {relative}")
        size = int(record.get("size", path.stat().st_size))
        total_size += size
        extras.append(LibraryExtraFile(
            relative, str(record.get("original_path", "")), size,
            str(record.get("sha256", "")),
            str(record.get("format", path.suffix.lstrip("."))).upper(),
        ))
    source_metadata = _validated_support_paths(asset_dir, document.get("source_metadata", []))
    total_size += sum((asset_dir / path).stat().st_size for path in source_metadata)
    provider = document.get("provider", {})
    return LibraryVdbAsset(
        id=str(document["id"]),
        name=str(document["name"]),
        category=_manifest_primary_category(document),
        tags=tuple(str(tag) for tag in document.get("tags", [])),
        description=str(document.get("description", "")),
        author=str(document.get("author", "")),
        provider=_provider_name(provider.get("name")),
        provider_id=str(provider.get("id", "")),
        asset_dir=asset_dir,
        variants=variants,
        thumbnail_path=thumbnail,
        hero_path=hero,
        preview_path=video,
        fingerprint=str(document.get("fingerprint", "")),
        created_at=str(document.get("created_at", "")),
        total_size=total_size,
        extra_files=tuple(extras),
        source_metadata=source_metadata,
        physical_size=str(document.get("physical_size", "")),
        rating=_manifest_rating(document),
        preview_render=(
            dict(previews.get("render", {}))
            if isinstance(previews.get("render", {}), dict)
            else {}
        ),
    )


def _stock_asset_from_manifest(document: dict, asset_dir: Path) -> LibraryStockAsset:
    source_record = document.get("source")
    preview_record = document.get("preview")
    media_record = document.get("media")
    if not isinstance(source_record, dict) or not isinstance(preview_record, dict) or not isinstance(media_record, dict):
        raise ValueError("Stock manifest is missing source, preview, or media metadata")
    source_relative = _safe_manifest_path(str(source_record["path"]))
    preview_relative = _safe_manifest_path(str(preview_record["video"]))
    thumbnail_relative = _safe_manifest_path(str(preview_record["thumbnail"]))
    source_path = asset_dir / source_relative
    preview_path = asset_dir / preview_relative
    thumbnail_path = asset_dir / thumbnail_relative
    for path, label in (
        (source_path, "source clip"), (preview_path, "preview video"), (thumbnail_path, "thumbnail"),
    ):
        if not path.is_file():
            raise ValueError(f"Missing Stock {label}: {path.relative_to(asset_dir).as_posix()}")
    try:
        media = LibraryStockMediaInfo(
            container=str(media_record.get("container", "")),
            codec=str(media_record.get("codec", "")),
            profile=str(media_record.get("profile", "")),
            pixel_format=str(media_record.get("pixel_format", "")),
            width=int(media_record["width"]),
            height=int(media_record["height"]),
            frame_rate=float(media_record.get("frame_rate", 0)),
            duration=float(media_record["duration"]),
            frame_count=int(media_record["frame_count"]) if media_record.get("frame_count") is not None else None,
            has_audio=bool(media_record.get("has_audio", False)),
            alpha=str(media_record.get("alpha", "unknown")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Stock manifest has invalid media metadata") from error
    if media.width <= 0 or media.height <= 0 or media.duration <= 0:
        raise ValueError("Stock manifest has invalid dimensions or duration")
    extras: list[LibraryExtraFile] = []
    total_size = source_path.stat().st_size + preview_path.stat().st_size + thumbnail_path.stat().st_size
    for record in document.get("extra_files", []):
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        if not path.is_file():
            raise ValueError(f"Missing Stock extra file: {relative}")
        size = int(record.get("size", path.stat().st_size))
        total_size += size
        extras.append(LibraryExtraFile(
            relative,
            str(record.get("original_path", "")),
            size,
            str(record.get("sha256", "")),
            str(record.get("format", path.suffix.lstrip("."))).upper(),
        ))
    source_metadata = _validated_support_paths(asset_dir, document.get("source_metadata", []))
    total_size += sum((asset_dir / path).stat().st_size for path in source_metadata)
    category = _manifest_primary_category(document)
    provider = document.get("provider", {})
    return LibraryStockAsset(
        id=str(document["id"]),
        name=str(document["name"]),
        category=category,
        tags=tuple(str(value) for value in document.get("tags", [])),
        description=str(document.get("description", "")),
        author=str(document.get("author", "")),
        provider=_provider_name(provider.get("name")),
        provider_id=str(provider.get("id", "")),
        asset_dir=asset_dir,
        source_path=source_path,
        source_original_path=str(source_record.get("original_path", "")),
        source_format=str(source_record.get("format", source_path.suffix.lstrip("."))).upper(),
        source_size=int(source_record.get("size", source_path.stat().st_size)),
        source_sha256=str(source_record.get("sha256", "")),
        media_info=media,
        preview_path=preview_path,
        thumbnail_path=thumbnail_path,
        hero_path=thumbnail_path,
        preview_origin=str(preview_record.get("origin", "")),
        preview_profile=str(preview_record.get("profile", "")),
        thumbnail_time=float(preview_record.get("thumbnail_time", media.duration / 2)),
        fingerprint=str(document.get("fingerprint", "")),
        created_at=str(document.get("created_at", "")),
        total_size=total_size,
        extra_files=tuple(extras),
        source_metadata=source_metadata,
        physical_size=str(document.get("physical_size", "")),
        rating=_manifest_rating(document),
    )


def _hdri_asset_from_manifest(document: dict, asset_dir: Path) -> LibraryHdriAsset:
    resolutions: dict[str, LibraryHdriVariant] = {}
    total_size = 0
    for label, value in document.get("resolutions", {}).items():
        files: list[LibraryHdriFile] = []
        for record in value.get("files", []):
            relative = _safe_manifest_path(str(record["path"]))
            path = asset_dir / relative
            if not path.is_file():
                raise ValueError(f"Missing HDRI file: {relative}")
            size = int(record.get("size", path.stat().st_size))
            total_size += size
            files.append(LibraryHdriFile(
                relative,
                str(record.get("format", path.suffix.lstrip("."))).upper(),
                size,
                str(record.get("sha256", "")),
                bool(record.get("preferred", False)),
            ))
        if files:
            resolutions[str(label)] = LibraryHdriVariant(
                str(label), value.get("width"), value.get("height"), tuple(files)
            )
    if not resolutions:
        raise ValueError("HDRI manifest contains no local environment maps")
    extras: list[LibraryExtraFile] = []
    for record in document.get("extra_files", []):
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        if not path.is_file():
            raise ValueError(f"Missing extra file: {relative}")
        size = int(record.get("size", path.stat().st_size))
        total_size += size
        extras.append(LibraryExtraFile(
            relative,
            str(record.get("original_path", "")),
            size,
            str(record.get("sha256", "")),
            str(record.get("format", path.suffix.lstrip("."))).upper(),
        ))
    category = _manifest_primary_category(document)
    provider = document.get("provider", {})
    previews = document.get("previews", {})
    thumbnail = _optional_asset_path(asset_dir, previews.get("thumbnail"))
    hero = _optional_asset_path(asset_dir, previews.get("hero"))
    source_metadata = _validated_support_paths(asset_dir, document.get("source_metadata", []))
    support_paths = {path for path in (thumbnail, hero) if path}
    support_paths.update(asset_dir / value for value in source_metadata)
    total_size += sum(path.stat().st_size for path in support_paths)
    return LibraryHdriAsset(
        id=str(document["id"]),
        name=str(document["name"]),
        category=category,
        tags=tuple(str(tag) for tag in document.get("tags", [])),
        description=str(document.get("description", "")),
        author=str(document.get("author", "")),
        provider=_provider_name(provider.get("name")),
        provider_id=str(provider.get("id", "")),
        asset_dir=asset_dir,
        resolutions=resolutions,
        thumbnail_path=thumbnail,
        hero_path=hero,
        fingerprint=str(document.get("fingerprint", "")),
        created_at=str(document.get("created_at", "")),
        total_size=total_size,
        extra_files=tuple(extras),
        source_metadata=source_metadata,
        preview_render=dict(previews.get("render", {})) if isinstance(previews.get("render", {}), dict) else {},
        rating=_manifest_rating(document),
    )


def _model_asset_from_manifest(document: dict, asset_dir: Path) -> LibraryModelAsset:
    payload_sizes: dict[str, int] = {}
    model_files: list[LibraryModelFile] = []
    for record in document.get("model_files", []):
        if not isinstance(record, dict):
            raise ValueError("Invalid model-file record")
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        origin = _model_record_origin(record)
        available = path.is_file()
        if not available and origin != "manual":
            raise ValueError(f"Missing model file: {relative}")
        size = int(record.get("size", path.stat().st_size if available else 0))
        if available:
            payload_sizes[relative] = size
        dependencies: list[LibraryExtraFile] = []
        for dependency_record in record.get("dependencies", []):
            if not isinstance(dependency_record, dict):
                raise ValueError("Invalid manual model dependency")
            dependency_relative = _safe_manifest_path(
                str(dependency_record.get("path", ""))
            )
            dependency_path = asset_dir / dependency_relative
            if not dependency_path.is_file():
                if origin == "manual":
                    available = False
                    continue
                raise ValueError(
                    f"Missing model dependency: {dependency_relative}"
                )
            dependency_size = int(
                dependency_record.get("size", dependency_path.stat().st_size)
            )
            payload_sizes[dependency_relative] = dependency_size
            dependencies.append(LibraryExtraFile(
                dependency_relative,
                dependency_relative,
                dependency_size,
                str(dependency_record.get("sha256", "")),
                dependency_path.suffix.lstrip(".").upper(),
            ))
        model_files.append(LibraryModelFile(
            path=relative,
            original_path=str(record.get("original_path", "")),
            file_format=str(record.get("format", path.suffix.lstrip("."))).upper(),
            role=str(record.get("role", "mesh")),
            lod=str(record.get("lod", "")),
            component=str(record.get("component", "")),
            triangle_count=record.get("triangle_count"),
            preferred=bool(record.get("preferred", False)),
            size=size,
            sha256=str(record.get("sha256", "")),
            resolution=str(record.get("resolution", "")),
            origin=origin,
            registered_at=str(record.get("registered_at", "")),
            available=available,
            validation=(
                dict(record.get("validation", {}))
                if isinstance(record.get("validation", {}), dict) else {}
            ),
            dependencies=tuple(dependencies),
        ))
    if not model_files:
        raise ValueError("Model manifest contains no local model files")
    preferred = [item for item in model_files if item.preferred]
    if len(preferred) != 1:
        raise ValueError("Model manifest must have exactly one preferred model file")
    if (
        any(
            item.available and item.file_format in {"USD", "USDA", "USDC", "USDZ"}
            for item in model_files
        )
        and preferred[0].file_format not in {"USD", "USDA", "USDC", "USDZ"}
    ):
        raise ValueError("A USD-ready model must prefer a USD-family file")

    texture_sets: dict[str, LibraryModelTextureSet] = {}
    for set_name, set_record in document.get("texture_sets", {}).items():
        resolutions: dict[str, LibraryResolution] = {}
        for label, value in set_record.get("resolutions", {}).items():
            groups: dict[str, tuple[LibraryMap, ...]] = {}
            for channel, records in value.get("maps", {}).items():
                maps: list[LibraryMap] = []
                for record in records:
                    relative = _safe_manifest_path(str(record["path"]))
                    path = asset_dir / relative
                    if not path.is_file():
                        raise ValueError(f"Missing model texture: {relative}")
                    size = int(record.get("size", path.stat().st_size))
                    payload_sizes[relative] = size
                    maps.append(LibraryMap(
                        channel=channel,
                        path=relative,
                        file_format=str(record.get("format", path.suffix.lstrip("."))).upper(),
                        size=size,
                        sha256=str(record.get("sha256", "")),
                        bit_depth=record.get("bit_depth"),
                        color_space=str(record.get("color_space", "")),
                        normal_convention=str(record.get("normal_convention", "")),
                        packed_channels=dict(record.get("packed_channels", {})),
                        preferred=bool(record.get("preferred", False)),
                        material=str(record.get("material", set_name)),
                        lod=str(record.get("lod", "")),
                    ))
                groups[str(channel)] = tuple(maps)
            resolutions[str(label)] = LibraryResolution(str(label), value.get("width"), value.get("height"), groups)
        texture_sets[str(set_name)] = LibraryModelTextureSet(str(set_name), resolutions)

    extras: list[LibraryExtraFile] = []
    for record in document.get("extra_files", []):
        relative = _safe_manifest_path(str(record["path"]))
        path = asset_dir / relative
        if not path.is_file():
            raise ValueError(f"Missing model companion: {relative}")
        size = int(record.get("size", path.stat().st_size))
        payload_sizes[relative] = size
        extras.append(LibraryExtraFile(
            relative,
            str(record.get("original_path", "")),
            size,
            str(record.get("sha256", "")),
            str(record.get("format", path.suffix.lstrip("."))).upper(),
        ))
    source_metadata = _validated_support_paths(asset_dir, document.get("source_metadata", []))
    for relative in source_metadata:
        payload_sizes[relative] = (asset_dir / relative).stat().st_size
    previews = document.get("previews", {})
    thumbnail = _optional_asset_path(asset_dir, previews.get("thumbnail"))
    hero = _optional_asset_path(asset_dir, previews.get("hero"))
    for path in {item for item in (thumbnail, hero) if item}:
        payload_sizes[path.relative_to(asset_dir).as_posix()] = path.stat().st_size
    provider_packages = _provider_packages_from_manifest(document, asset_dir, payload_sizes)
    excluded: list[tuple[str, str]] = []
    for record in document.get("excluded_files", []):
        if isinstance(record, dict):
            original = _safe_manifest_path(str(record.get("path", "")))
            excluded.append((original, str(record.get("reason", "Excluded"))))
    usd_derivative = None
    derivative_record = document.get("usd_derivative")
    if isinstance(derivative_record, dict):
        entry_relative = _safe_manifest_path(str(derivative_record.get("entry_path", "")))
        if not (asset_dir / entry_relative).is_file():
            raise ValueError(f"Missing generated USD entry: {entry_relative}")
        dependencies: list[LibraryExtraFile] = []
        for record in derivative_record.get("dependencies", []):
            if not isinstance(record, dict):
                raise ValueError("Invalid generated USD dependency")
            relative = _safe_manifest_path(str(record.get("path", "")))
            path = asset_dir / relative
            if not path.is_file():
                raise ValueError(f"Missing generated USD dependency: {relative}")
            size = int(record.get("size", path.stat().st_size))
            payload_sizes[relative] = size
            dependencies.append(LibraryExtraFile(
                relative,
                relative,
                size,
                str(record.get("sha256", "")),
                path.suffix.lstrip(".").upper(),
            ))
        usd_derivative = LibraryUsdDerivative(
            entry_relative,
            str(derivative_record.get("source_path", "")),
            str(derivative_record.get("source_sha256", "")),
            str(derivative_record.get("forward_axis", "")),
            str(derivative_record.get("up_axis", "")),
            str(derivative_record.get("blender_version", "")),
            str(derivative_record.get("generated_at", "")),
            tuple(dependencies),
            tuple(str(value) for value in derivative_record.get("diagnostics", []) if value),
        )
    category = _manifest_primary_category(document)
    provider = document.get("provider", {})
    dimensions = document.get("dimensions", [])
    return LibraryModelAsset(
        id=str(document["id"]),
        name=str(document["name"]),
        category=category,
        tags=tuple(str(tag) for tag in document.get("tags", [])),
        description=str(document.get("description", "")),
        author=str(document.get("author", "")),
        physical_size=str(document.get("physical_size", "")),
        provider=_provider_name(provider.get("name")),
        provider_id=str(provider.get("id", "")),
        asset_dir=asset_dir,
        model_files=tuple(model_files),
        texture_sets=texture_sets,
        thumbnail_path=thumbnail,
        hero_path=hero,
        fingerprint=str(document.get("fingerprint", "")),
        created_at=str(document.get("created_at", "")),
        total_size=sum(payload_sizes.values()),
        dimensions=tuple(float(value) for value in dimensions) if isinstance(dimensions, list) else (),
        polycount=document.get("polycount"),
        extra_files=tuple(extras),
        source_metadata=source_metadata,
        excluded_files=tuple(excluded),
        provider_packages=provider_packages,
        usd_derivative=usd_derivative,
        rating=_manifest_rating(document),
    )


def _manifest_rating(document: dict) -> int:
    rating = document.get("rating", 0)
    if isinstance(rating, bool) or not isinstance(rating, int) or not 0 <= rating <= 5:
        raise ValueError("Manifest rating must be a whole number from 0 to 5")
    return rating


def _validated_support_paths(asset_dir: Path, values) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values if isinstance(values, list) else ():
        relative = _safe_manifest_path(str(value))
        if not (asset_dir / relative).is_file():
            raise ValueError(f"Missing source metadata: {relative}")
        paths.append(relative)
    return tuple(paths)


def _provider_packages_from_manifest(
    document: dict, asset_dir: Path, payload_sizes: dict[str, int]
) -> tuple[LibraryProviderPackage, ...]:
    packages: list[LibraryProviderPackage] = []
    for package in document.get("provider_packages", []):
        if not isinstance(package, dict):
            raise ValueError("Invalid provider-package record")
        files: list[LibraryProviderPackageFile] = []
        for record in package.get("files", []):
            if not isinstance(record, dict):
                raise ValueError("Invalid provider-package file record")
            relative = _safe_manifest_path(str(record["path"]))
            path = asset_dir / relative
            if not path.is_file():
                raise ValueError(f"Missing provider-package file: {relative}")
            size = int(record.get("size", path.stat().st_size))
            payload_sizes[relative] = size
            files.append(LibraryProviderPackageFile(
                relative,
                str(record.get("role", "dependency")),
                size,
                str(record.get("sha256", "")),
                str(record.get("md5", "")),
                str(record.get("reference_path", "")),
            ))
            reference = str(record.get("reference_path", ""))
            if reference:
                reference_relative = _safe_manifest_path(reference)
                if not (asset_dir / reference_relative).is_file():
                    raise ValueError(f"Missing provider-package compatibility file: {reference_relative}")
        entry = _safe_manifest_path(str(package.get("entry_path", "")))
        if entry not in {item.path for item in files}:
            raise ValueError("Provider-package entry is not present in its file list")
        packages.append(LibraryProviderPackage(
            str(package.get("kind", "")),
            str(package.get("resolution", "")),
            entry,
            tuple(files),
            str(package.get("downloaded_at", "")),
        ))
    return tuple(packages)


def _polyhaven_target_root(plan: PolyHavenDownloadPlan) -> Path:
    if plan.kind == "maps":
        return Path("maps") / _safe_component(plan.resolution)
    if plan.kind == "hdri":
        return Path("maps") / "polyhaven" / _safe_component(plan.resolution)
    return Path("packages") / plan.kind / _safe_component(plan.resolution)


def _organize_polyhaven_usd_packages(stage: Path, document: dict, token: CancelToken) -> None:
    packages = document.get("provider_packages", [])
    if not isinstance(packages, list):
        return
    asset_token = _filename_token(str(document.get("name", "Asset")))
    model_files = [item for item in document.get("model_files", []) if isinstance(item, dict)]
    texture_sets = document.setdefault("texture_sets", {})
    default_set = texture_sets.setdefault("Default", {"resolutions": {}})
    resolutions = default_set.setdefault("resolutions", {})
    used: dict[Path, set[str]] = {}
    for path in (stage / "maps").glob("*/*") if (stage / "maps").is_dir() else ():
        if path.is_file():
            used.setdefault(path.parent, set()).add(path.name.casefold())

    for package in packages:
        if not isinstance(package, dict) or str(package.get("kind", "")) != "usd":
            continue
        old_entry = str(package.get("entry_path", ""))
        if not old_entry.startswith("packages/usd/"):
            continue
        token.check()
        resolution = str(package.get("resolution", "")) or "Unknown"
        model_record = next((
            item for item in model_files
            if str(item.get("resolution", "")) == resolution
            and str(item.get("format", "")).upper() in {"USD", "USDA", "USDC", "USDZ"}
        ), None)
        if model_record is None:
            raise LibraryError(f"Could not find the {resolution} USD model entry while organizing its files.")
        package["entry_path"] = str(model_record["path"])
        package_root = PurePosixPath(old_entry).parent
        variant = resolutions.setdefault(resolution, {
            "width": _resolution_pixels(resolution),
            "height": _resolution_pixels(resolution),
            "maps": {},
        })
        groups = variant.setdefault("maps", {})
        for file_record in package.get("files", []):
            if not isinstance(file_record, dict):
                continue
            old = _safe_manifest_path(str(file_record.get("path", "")))
            if str(file_record.get("role", "")) == "entry":
                file_record["path"] = str(model_record["path"])
                file_record.pop("reference_path", None)
                continue
            source = _safe_asset_file(stage, old)
            try:
                provider_relative = PurePosixPath(old).relative_to(package_root)
            except ValueError as error:
                raise LibraryError(f"USD dependency is outside its package: {old}") from error
            channel, convention, packed = _polyhaven_map_semantics(provider_relative.name)
            suffix = source.suffix
            parent = stage / "maps" / _safe_component(resolution)
            if channel:
                texture_map = TextureMap(
                    channel=channel,
                    relative_path=provider_relative.as_posix(),
                    file_format=suffix.lstrip(".").upper(),
                    normal_convention=convention,
                    packed_channels=packed,
                    preferred=True,
                    material="Default",
                )
                destination = _map_destination(
                    parent, asset_token, resolution, channel, texture_map, suffix, used,
                )
            else:
                destination = _unique_destination(parent, provider_relative.name, used)
            digest = str(file_record.get("sha256", ""))
            existing_record = next((
                item
                for value in groups.values()
                for item in value
                if str(item.get("sha256", "")) == digest and (stage / str(item.get("path", ""))).is_file()
            ), None)
            if existing_record is not None:
                destination = stage / str(existing_record["path"])
                source.unlink(missing_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise LibraryError(f"Organized map destination already exists: {destination.relative_to(stage)}")
                os.replace(source, destination)
                if channel:
                    for item in groups.setdefault(channel, []):
                        item["preferred"] = False
                    groups[channel].append({
                        "path": destination.relative_to(stage).as_posix(),
                        "original_path": provider_relative.as_posix(),
                        "format": suffix.lstrip(".").upper(),
                        "size": int(file_record.get("size", destination.stat().st_size)),
                        "sha256": digest,
                        "source_md5": str(file_record.get("md5", "")),
                        "bit_depth": None,
                        "color_space": "sRGB" if channel in {"Base Color", "Emission"} else "Raw",
                        "normal_convention": convention,
                        "packed_channels": packed,
                        "preferred": True,
                        "material": "Default",
                        "lod": "",
                    })
            canonical = destination.relative_to(stage).as_posix()
            reference = Path("usd") / Path(provider_relative.as_posix())
            reference_path = stage / reference
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            if reference_path.exists():
                if _sha256_path(reference_path) != digest:
                    raise LibraryError(f"USD compatibility texture conflicts with an existing file: {reference}")
                reference_path.unlink()
            try:
                os.link(destination, reference_path)
            except OSError:
                shutil.copy2(destination, reference_path)
            file_record["path"] = canonical
            file_record["reference_path"] = reference.as_posix()


def _polyhaven_map_semantics(filename: str) -> tuple[str, str, dict[str, str]]:
    stem = Path(filename).stem.casefold().replace("-", "_")
    tokens = [token for token in stem.split("_") if token]
    compounds = ["nor_gl", "nor_dx", "base_color", "ambient_occlusion"]
    for compound in compounds:
        if compound in stem:
            channel, convention, packed = normalize_channel(compound)
            if channel:
                return channel, convention, packed
    for token in reversed(tokens):
        channel, convention, packed = normalize_channel(token)
        if channel:
            return channel, convention, packed
    return "", "", {}


def _polyhaven_staged_path(plan: PolyHavenDownloadPlan, remote) -> Path:
    if plan.kind == "hdri":
        return Path(Path(remote.source_path).name)
    if plan.kind != "maps":
        return Path(remote.source_path)
    texture_map = TextureMap(
        channel=remote.channel,
        relative_path=remote.source_path,
        file_format=remote.file_format,
        normal_convention=remote.normal_convention,
        packed_channels=remote.packed_channels,
    )
    stem = (
        f"{_filename_token(plan.asset_name or plan.slug.replace('_', ' '))}_"
        f"{_filename_token(plan.resolution)}_{_map_channel_token(remote.channel, texture_map)}"
    )
    suffix = _safe_suffix(f".{remote.file_format}")
    peers = [
        item for item in plan.files
        if item.channel == remote.channel and item.file_format.casefold() == remote.file_format.casefold()
    ]
    index = peers.index(remote) + 1
    return Path(f"{stem}{'' if index == 1 else f'_alt{index}'}{suffix}")


def _apply_polyhaven_manifest(
    document: dict,
    plan: PolyHavenDownloadPlan,
    records: list[dict],
    target_relative: Path,
) -> None:
    prefix = target_relative.as_posix()
    full_records: list[dict] = []
    for record in records:
        value = dict(record)
        value["path"] = f"{prefix}/{record['path']}"
        full_records.append(value)
    provider = document.setdefault("provider", {})
    provider.setdefault("name", "Poly Haven")
    if not str(provider.get("id", "")).strip():
        provider["id"] = plan.slug
    if plan.kind == "maps":
        resolutions = document.setdefault("resolutions", {})
        variant = resolutions.setdefault(plan.resolution, {"width": None, "height": None, "maps": {}})
        pixels = _resolution_pixels(plan.resolution)
        variant["width"] = variant.get("width") or pixels
        variant["height"] = variant.get("height") or pixels
        groups = variant.setdefault("maps", {})
        by_channel: dict[str, list[dict]] = {}
        for record in full_records:
            by_channel.setdefault(str(record["channel"]), []).append(record)
        for channel, downloaded in by_channel.items():
            paths = {str(item["path"]).casefold() for item in downloaded}
            existing = [dict(item) for item in groups.get(channel, []) if str(item.get("path", "")).casefold() not in paths]
            if downloaded:
                for item in existing:
                    item["preferred"] = False
            normalized = []
            for item in downloaded:
                normalized.append({
                    "path": item["path"],
                    "format": item["format"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "source_md5": item["md5"],
                    "source_path": item["source_path"],
                    "bit_depth": None,
                    "color_space": "sRGB" if channel in {"Base Color", "Emission"} else "Raw",
                    "normal_convention": item["normal_convention"],
                    "packed_channels": item["packed_channels"],
                    "preferred": item["preferred"],
                })
            groups[channel] = existing + normalized
        document["fingerprint"] = _texture_manifest_fingerprint(document)
        return

    if plan.kind == "hdri":
        resolutions = document.setdefault("resolutions", {})
        variant = resolutions.setdefault(plan.resolution, {"width": None, "height": None, "files": []})
        pixels = _resolution_pixels(plan.resolution)
        variant["width"] = variant.get("width") or pixels
        variant["height"] = variant.get("height") or (pixels // 2 if pixels else None)
        downloaded_paths = {str(item["path"]).casefold() for item in full_records}
        existing = [
            dict(item) for item in variant.get("files", [])
            if str(item.get("path", "")).casefold() not in downloaded_paths
        ]
        for item in existing:
            item["preferred"] = False
        variant["files"] = existing + [
            {
                "path": item["path"],
                "format": item["format"],
                "size": item["size"],
                "sha256": item["sha256"],
                "source_md5": item["md5"],
                "source_path": item["source_path"],
                "preferred": item["preferred"],
            }
            for item in full_records
        ]
        document["fingerprint"] = _hdri_manifest_fingerprint(document)
        return

    entry_record = next((item for item in full_records if item["role"] == "entry"), None)
    if entry_record is None:
        raise LibraryError("The downloaded provider package has no entry file.")
    packages = [
        item for item in document.get("provider_packages", [])
        if not (str(item.get("kind", "")) == plan.kind and str(item.get("resolution", "")) == plan.resolution)
    ]
    packages.append({
        "kind": plan.kind,
        "resolution": plan.resolution,
        "entry_path": entry_record["path"],
        "downloaded_at": _utc_now(),
        "files": [
            {
                "path": item["path"], "role": item["role"], "size": item["size"],
                "sha256": item["sha256"], "md5": item["md5"],
            }
            for item in full_records
        ],
    })
    document["provider_packages"] = sorted(
        packages, key=lambda item: (str(item.get("kind", "")), _resolution_pixels(str(item.get("resolution", ""))) or 999999)
    )
    if plan.kind == "usd":
        model_files = [
            dict(item) for item in document.get("model_files", [])
            if not (str(item.get("resolution", "")) == plan.resolution and str(item.get("format", "")).upper() in {"USD", "USDA", "USDC", "USDZ"})
        ]
        for item in model_files:
            item["preferred"] = False
        suffix = Path(entry_record["path"]).suffix.lstrip(".").upper() or "USD"
        model_files.append({
            "path": entry_record["path"],
            "original_path": plan.entry_source_path,
            "format": suffix,
            "role": "scene",
            "lod": "",
            "resolution": plan.resolution,
            "component": str(document.get("name", "")),
            "triangle_count": document.get("polycount"),
            "preferred": True,
            "size": entry_record["size"],
            "sha256": entry_record["sha256"],
            "source_md5": entry_record["md5"],
        })
        document["model_files"] = model_files


def _polyhaven_already_installed(document: dict, asset_dir: Path, plan: PolyHavenDownloadPlan) -> bool:
    expected = {item.md5.casefold() for item in plan.files if item.md5}
    if not expected:
        return False
    if plan.kind in {"maps", "hdri"}:
        variant = document.get("resolutions", {}).get(plan.resolution, {})
        records = (
            [item for values in variant.get("maps", {}).values() for item in values]
            if plan.kind == "maps" else list(variant.get("files", []))
        )
        matching = [item for item in records if str(item.get("source_md5", "")).casefold() in expected]
    else:
        package = next((
            item for item in document.get("provider_packages", [])
            if str(item.get("kind", "")) == plan.kind and str(item.get("resolution", "")) == plan.resolution
        ), None)
        matching = list(package.get("files", [])) if isinstance(package, dict) else []
    if {str(item.get("md5", item.get("source_md5", ""))).casefold() for item in matching} != expected:
        return False
    for record in matching:
        try:
            path = asset_dir / _safe_manifest_path(str(record["path"]))
        except (KeyError, ValueError):
            return False
        if not path.is_file() or (record.get("sha256") and _sha256_path(path) != record["sha256"]):
            return False
    return True


def _texture_manifest_fingerprint(document: dict) -> str:
    records: list[str] = []
    for label, variant in document.get("resolutions", {}).items():
        for channel, maps in variant.get("maps", {}).items():
            for item in maps:
                records.append(f"{label}|{channel}|{item.get('sha256', '')}")
    return hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()


def _hdri_manifest_fingerprint(document: dict) -> str:
    records: list[str] = []
    for label, variant in document.get("resolutions", {}).items():
        for item in variant.get("files", []):
            records.append(f"{label}|Environment|{item.get('sha256', '')}")
    return hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()


def _stock_media_document(info) -> dict:
    return {
        "container": info.container,
        "codec": info.codec,
        "profile": info.profile,
        "pixel_format": info.pixel_format,
        "width": info.width,
        "height": info.height,
        "frame_rate": info.frame_rate,
        "duration": info.duration,
        "frame_count": info.frame_count,
        "has_audio": info.has_audio,
        "alpha": info.alpha,
    }


def _resolution_pixels(label: str) -> int | None:
    match = re.fullmatch(r"(\d+)K", label.strip(), re.IGNORECASE)
    return int(match.group(1)) * 1024 if match else None


def _validate_candidate(material: MaterialCandidate, library_root: Path) -> None:
    if isinstance(material, StockCandidate):
        if not material.name.strip() or not material.category.strip() or not material.source_video or not material.media_info:
            raise LibraryError(f"{material.name or 'Stock clip'} is missing required review metadata or a valid source video.")
        if material.preview_policy == "use_existing":
            preview = next(
                (
                    item for item in material.preview_candidates
                    if item.relative_path == material.selected_preview
                ),
                None,
            )
            if preview is None:
                raise LibraryError(f"{material.name} has no selected local preview.")
            if not preview.compatible:
                raise LibraryError(
                    f"{material.name}'s selected preview is not H.264 4:2:0; choose Generate 480p preview."
                )
    elif isinstance(material, VdbCandidate):
        if not material.name.strip() or not material.category.strip() or not material.variants:
            raise LibraryError(f"{material.name or 'VDB'} is missing required review metadata or VDB variants.")
        if not any(variant.files for variant in material.variants.values()):
            raise LibraryError(f"{material.name} has no local VDB files.")
    elif isinstance(material, ModelCandidate):
        if not material.name.strip() or not material.category.strip() or not material.model_files:
            raise LibraryError(f"{material.name or 'Model'} is missing required review metadata or model files.")
    elif not material.name.strip() or not material.category.strip() or not material.resolutions:
        raise LibraryError(f"{material.name or 'Material'} is missing required review metadata.")
    source_root = material.source_root.resolve(strict=True)
    library = library_root.resolve(strict=True)
    if _contains(source_root, library) or _contains(library, source_root):
        raise LibraryError(f"Source and library folders overlap for {material.name}.")
    for relative in _candidate_source_paths(material):
        _safe_source(material.source_root, relative)
    if (
        not isinstance(material, (ModelCandidate, StockCandidate, VdbCandidate))
        and not any(variant.maps for variant in material.resolutions.values())
    ):
        raise LibraryError(f"{material.name} has no local texture maps.")


def _validate_archive_source(
    material: MaterialCandidate, expected_hash: str = ""
) -> None:
    source = material.archive_source
    if source is None:
        return
    records = [(
        source.archive_path,
        source.archive_size,
        source.archive_mtime_ns,
        f"{source.file_format} archive",
    )]
    if source.preview_path is not None:
        records.append((
            source.preview_path,
            source.preview_size,
            source.preview_mtime_ns,
            "preview",
        ))
    for path, size, mtime_ns, label in records:
        try:
            stat = path.stat()
        except OSError as error:
            raise StaleSourceError(
                f"{material.name}'s source {label} is unavailable; rescan before importing."
            ) from error
        if stat.st_size != size or stat.st_mtime_ns != mtime_ns:
            raise StaleSourceError(
                f"{material.name}'s source {label} changed after scanning; rescan before importing."
            )
    if expected_hash and _sha256_path(source.archive_path) != expected_hash:
        raise StaleSourceError(
            f"{material.name}'s {source.file_format} archive no longer matches "
            "preflight; rescan before importing."
        )


def _source_manifest(material: MaterialCandidate, archive_sha256: str = "") -> dict:
    source = material.archive_source
    if source is None:
        return {"original_path": str(material.source_root)}
    return {
        "original_path": str(source.archive_path),
        "package": {
            "format": source.file_format,
            "path": str(source.archive_path),
            "size": source.archive_size,
            "sha256": archive_sha256,
            "preview_path": str(source.preview_path) if source.preview_path else "",
        },
    }


def _validate_all_snapshots(material: MaterialCandidate) -> None:
    for relative in _candidate_source_paths(material):
        source = _safe_source(material.source_root, relative)
        snapshot = material.source_snapshots.get(relative)
        if snapshot and not _snapshot_matches(source, snapshot):
            raise StaleSourceError(f"{relative} changed after it was scanned.")


def _portable_candidate_diagnostics(material: MaterialCandidate, library_root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    reserved = {"con", "prn", "aux", "nul", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
    components = (_slugify(material.category), _slugify(material.name))
    for component in components:
        stem = component.split(".", 1)[0].casefold()
        if stem in reserved or component.endswith((" ", ".")):
            diagnostics.append(Diagnostic("error", "windows_reserved_name", f"{component!r} is not a portable Windows folder name."))
        if len(component.encode("utf-8")) > 180:
            diagnostics.append(Diagnostic("error", "filename_too_long", f"{component!r} is too long for a portable library path."))
    container = (
        "models" if isinstance(material, ModelCandidate)
        else "vdbs" if isinstance(material, VdbCandidate)
        else "hdris" if isinstance(material, HdriCandidate)
        else "stock" if isinstance(material, StockCandidate)
        else "atlases" if material.asset_type == "atlas"
        else "textures"
    )
    tail = (
        ("source", "textures", "16K") if isinstance(material, ModelCandidate)
        else ("volumes", "high", "volume_1001.vdb") if isinstance(material, VdbCandidate)
        else (f"{_filename_token(material.name)}{Path(material.source_video).suffix}",) if isinstance(material, StockCandidate)
        else ("maps", "16K")
    )
    estimated = library_root / container / components[0]
    if not isinstance(material, StockCandidate):
        estimated /= components[1]
    for value in tail:
        estimated /= value
    if len(str(estimated)) > 220:
        diagnostics.append(Diagnostic("error", "path_too_long", "The destination path is too long for reliable Windows use."))
    return diagnostics


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _selected_vdb_variant_label(asset: LibraryVdbAsset, requested: str = "") -> str:
    if requested in asset.variants:
        return requested
    if requested:
        match = next(
            (label for label in asset.variants if label.casefold() == requested.casefold()),
            None,
        )
        if match:
            return match
        raise LibraryError(f"The requested VDB variant is unavailable: {requested}")
    for preferred in ("mid", "low", "high"):
        match = next((label for label in asset.variants if label.casefold() == preferred), None)
        if match:
            return match
    if not asset.variants:
        raise LibraryError("The VDB asset has no managed variants.")
    return next(iter(asset.variants))


def _vdb_frame_one_source(
    asset: LibraryVdbAsset,
    variant: LibraryVdbVariant,
) -> tuple[LibraryVdbFile, str]:
    if not variant.files:
        raise LibraryError(f"The {variant.label} VDB variant has no managed files.")
    if not variant.is_sequence:
        record = variant.files[0]
        return record, (asset.asset_dir / record.path).resolve().as_posix()
    record = next((item for item in variant.files if item.frame == 1), None)
    if record is None:
        raise LibraryError(
            f"The {variant.label} VDB sequence has no source frame 1. "
            "Still-preview V1 evaluates the sequence at timeline frame 1."
        )
    padding = max(1, variant.padding, record.padding)
    expression = (asset.asset_dir / record.path).resolve().as_posix()
    token = f"{1:0{padding}d}"
    match = re.search(rf"{re.escape(token)}(?=\.vdb$)", expression, re.IGNORECASE)
    if not match:
        raise LibraryError(
            f"The {variant.label} frame-1 filename does not contain its "
            f"{padding}-digit frame token."
        )
    return record, expression[:match.start()] + f"$F{padding}" + expression[match.end():]


def _safe_source(root: Path, relative: str) -> Path:
    base = root.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    if not _contains(base, candidate) or not candidate.is_file():
        raise LibraryError(f"Unsafe or missing source path: {relative}")
    return candidate


def _safe_manifest_path(relative: str) -> str:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe manifest path: {relative}")
    return path.as_posix()


def _managed_flat_relative(container: str, original: str, used: dict[str, set[str]]) -> str:
    safe = _safe_manifest_path(original)
    name = _portable_flat_filename(Path(safe).name)
    names = used.setdefault(container.casefold(), set())
    stem, suffix = Path(name).stem, Path(name).suffix
    unique = name
    counter = 2
    while unique.casefold() in names:
        unique = f"{stem}_{counter}{suffix}"
        counter += 1
    names.add(unique.casefold())
    return (Path(container) / unique).as_posix()


def _managed_named_relative(
    container: str, filename: str, used: dict[str, set[str]]
) -> str:
    name = _portable_flat_filename(filename)
    names = used.setdefault(container.casefold(), set())
    stem, suffix = Path(name).stem, Path(name).suffix
    unique = name
    counter = 2
    while unique.casefold() in names:
        unique = f"{stem}_{counter}{suffix}"
        counter += 1
    names.add(unique.casefold())
    return (Path(container) / unique).as_posix()


def _model_managed_filename(
    asset_token: str,
    file_format: str,
    lod: str,
    role: str,
    suffix: str,
    model_count: int,
) -> str:
    extension = _safe_suffix(suffix or f".{file_format.casefold()}")
    if lod:
        qualifier = _filename_token(lod)
    elif model_count > 1:
        qualifier = _filename_token(file_format or role or "Model")
    else:
        qualifier = ""
    return f"{asset_token}{f'_{qualifier}' if qualifier else ''}{extension}"


def _portable_flat_filename(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    if not name:
        return "file"
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if Path(name).stem.casefold() in reserved:
        name = "_" + name
    return name


def _optional_asset_path(asset_dir: Path, relative: object) -> Path | None:
    if not relative:
        return None
    safe = _safe_manifest_path(str(relative))
    path = asset_dir / safe
    return path if path.is_file() else None


def _material_source_bytes(material: MaterialCandidate) -> int:
    return sum(_safe_source(material.source_root, path).stat().st_size for path in _candidate_source_paths(material))


def _primary_file_records(material: MaterialCandidate):
    if isinstance(material, StockCandidate):
        class _StockSource:
            relative_path = material.source_video
        yield "STOCK", "source", _StockSource()
        return
    if isinstance(material, ModelCandidate):
        for model_file in material.model_files:
            identity = f"{model_file.role}:{model_file.lod}:{model_file.file_format}"
            yield "MODEL", identity, model_file
        for set_name, texture_set in sorted(material.texture_sets.items(), key=lambda item: item[0].casefold()):
            for label in _sorted_resolution_labels(texture_set.resolutions):
                variant = texture_set.resolutions[label]
                for channel in sorted(variant.maps, key=str.casefold):
                    for texture_file in variant.maps[channel]:
                        yield f"TEXTURE:{set_name}:{label}", f"{channel}:{texture_file.lod}", texture_file
        return
    if isinstance(material, VdbCandidate):
        for label in material.resolution_labels:
            for vdb_file in material.variants[label].files:
                identity = str(vdb_file.frame) if vdb_file.frame is not None else "static"
                yield label, identity, vdb_file
        return
    for label in material.resolution_labels:
        variant = material.resolutions[label]
        for channel in sorted(variant.maps, key=str.casefold):
            for texture_file in variant.maps[channel]:
                yield label, channel, texture_file


def _model_source_destinations(material: ModelCandidate) -> dict[str, str]:
    destinations: dict[str, str] = {}
    used: dict[str, set[str]] = {}
    map_used: dict[Path, set[str]] = {}
    asset_token = _filename_token(material.name)

    def assign(path: str, container: str, filename: str = "") -> None:
        if path and path not in destinations:
            destinations[path] = (
                _managed_named_relative(container, filename, used)
                if filename else _managed_flat_relative(container, path, used)
            )

    model_count = len(material.model_files)
    for model_file in material.model_files:
        assign(
            model_file.relative_path,
            (
                "usd"
                if model_file.file_format.upper() in {"USD", "USDA", "USDC", "USDZ"}
                else "models"
            ),
            _model_managed_filename(
                asset_token,
                model_file.file_format,
                model_file.lod,
                model_file.role,
                Path(model_file.relative_path).suffix,
                model_count,
            ),
        )
    multiple_sets = len(material.texture_sets) > 1
    for set_name, texture_set in material.texture_sets.items():
        texture_token = (
            f"{asset_token}_{_filename_token(set_name)}"
            if multiple_sets else asset_token
        )
        for label, variant in texture_set.resolutions.items():
            for channel, alternatives in variant.maps.items():
                for texture_file in alternatives:
                    if texture_file.relative_path in destinations:
                        continue
                    parent = Path(f"maps/{_safe_component(label)}")
                    destination = _map_destination(
                        parent,
                        texture_token,
                        label,
                        channel,
                        texture_file,
                        Path(texture_file.relative_path).suffix,
                        map_used,
                    )
                    destinations[texture_file.relative_path] = destination.as_posix()
    assign(
        material.selected_thumbnail,
        "previews",
        f"{asset_token}_Thumbnail{Path(material.selected_thumbnail).suffix}"
        if material.selected_thumbnail else "",
    )
    assign(
        material.selected_hero,
        "previews",
        f"{asset_token}_Hero{Path(material.selected_hero).suffix}"
        if material.selected_hero else "",
    )
    for path in material.metadata_paths:
        assign(path, "metadata")
    for path in material.extra_paths:
        assign(path, "extras")
    return destinations


def _candidate_source_paths(material: MaterialCandidate) -> list[str]:
    paths = {item.relative_path for _label, _channel, item in _primary_file_records(material)}
    if isinstance(material, StockCandidate):
        if material.preview_policy == "use_existing" and material.selected_preview:
            paths.add(material.selected_preview)
        return sorted(paths, key=str.casefold)
    if isinstance(material, VdbCandidate) and material.selected_preview_video:
        paths.add(material.selected_preview_video)
    paths.update(path for path in (material.selected_thumbnail, material.selected_hero) if path)
    paths.update(material.metadata_paths)
    paths.update(_material_extra_paths(material))
    return sorted(paths, key=str.casefold)


def _material_extra_paths(material: MaterialCandidate) -> list[str]:
    if isinstance(material, StockCandidate):
        assigned = {material.source_video, material.selected_preview, *material.metadata_paths}
        return sorted(
            (path for path in material.extra_paths if path and path not in assigned),
            key=str.casefold,
        )
    if isinstance(material, ModelCandidate):
        return sorted(set(material.extra_paths), key=str.casefold)
    if isinstance(material, VdbCandidate):
        assigned = {
            item.relative_path
            for variant in material.variants.values()
            for item in variant.files
        }
        assigned.update(path for path in (
            material.selected_thumbnail, material.selected_hero,
            material.selected_preview_video,
        ) if path)
        assigned.update(material.metadata_paths)
        return sorted((path for path in material.extra_paths if path not in assigned), key=str.casefold)
    assigned = {
        texture_map.relative_path
        for variant in material.resolutions.values()
        for alternatives in variant.maps.values()
        for texture_map in alternatives
    }
    assigned.update(material.metadata_paths)
    assigned.update(path for path in (material.selected_thumbnail, material.selected_hero) if path)
    candidates = set(material.extra_paths)
    candidates.update(path for path in material.source_snapshots if path not in assigned)
    return sorted((path for path in candidates if path not in assigned), key=str.casefold)


def _copy_hash(
    source: Path,
    destination: Path,
    material: str,
    callback: Callable[[ImportProgress], None] | None,
    token: CancelToken,
    completed_before: int,
    total_bytes: int,
    expected_snapshot: SourceFileSnapshot | None = None,
    expected_hash: str = "",
) -> tuple[int, str]:
    if expected_snapshot and not _snapshot_matches(source, expected_snapshot):
        raise StaleSourceError(f"{source.name} changed after preflight.")
    before = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    with source.open("rb") as read, destination.open("xb") as write:
        while chunk := read.read(1024 * 1024):
            token.check()
            write.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
            if callback:
                callback(ImportProgress(material, source.name, completed_before + copied, total_bytes))
        write.flush()
        os.fsync(write.fileno())
    shutil.copystat(source, destination, follow_symlinks=False)
    after = source.stat()
    actual_hash = digest.hexdigest()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise StaleSourceError(f"{source.name} changed while it was being copied.")
    if expected_snapshot and not _snapshot_matches(source, expected_snapshot):
        raise StaleSourceError(f"{source.name} changed after preflight.")
    if expected_hash and actual_hash != expected_hash:
        raise StaleSourceError(f"{source.name} no longer matches its reviewed content.")
    return copied, actual_hash


def _write_hdri_thumbnail(material: HdriCandidate, destination: Path, token: CancelToken) -> None:
    token.check()
    image = QImage()
    relative = material.selected_thumbnail
    if relative:
        source = _safe_source(material.source_root, relative)
        snapshot = material.source_snapshots.get(relative)
        if snapshot and not _snapshot_matches(source, snapshot):
            raise StaleSourceError(f"{source.name} changed after it was scanned.")
        reader = QImageReader(str(source))
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        image = reader.read()
        if snapshot and not _snapshot_matches(source, snapshot):
            raise StaleSourceError(f"{source.name} changed while its preview was generated.")
    if image.isNull():
        image = QImage(512, 256, QImage.Format.Format_RGB32)
        image.fill(QColor("#263746"))
    else:
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        if image.width() > 1024 or image.height() > 512:
            image = image.scaled(1024, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "JPG", 90):
        raise LibraryError(f"Could not generate the JPEG thumbnail for {material.name}.")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


def _convert_webp_to_jpeg(
    source: Path,
    destination: Path,
    material: str,
    callback: Callable[[ImportProgress], None] | None,
    token: CancelToken,
    completed_before: int,
    total_bytes: int,
    expected_snapshot: SourceFileSnapshot | None,
    expected_hash: str,
) -> tuple[int, str, int, str]:
    source_digest, processed = _hash_source(
        source,
        expected_snapshot,
        material,
        callback,
        token,
        completed_before,
        total_bytes,
    )
    if expected_hash and source_digest != expected_hash:
        raise StaleSourceError(f"{source.name} no longer matches its reviewed content.")

    token.check()
    reader = QImageReader(str(source))
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise LibraryError(f"Could not decode the WebP texture {source.name}.")
    if expected_snapshot and not _snapshot_matches(source, expected_snapshot):
        raise StaleSourceError(f"{source.name} changed while it was being converted.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    if not image.save(str(destination), "JPG", 95):
        raise LibraryError(f"Could not convert the WebP texture {source.name} to JPEG.")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    token.check()
    return processed, source_digest, destination.stat().st_size, _sha256_file(destination)


def _write_model_placeholder(name: str, destination: Path, token: CancelToken) -> None:
    token.check()
    image = QImage(640, 360, QImage.Format.Format_RGB32)
    image.fill(QColor("#29243a"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "JPG", 90):
        raise LibraryError(f"Could not create a placeholder preview for {name}.")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


def _write_vdb_placeholder(name: str, destination: Path, token: CancelToken) -> None:
    token.check()
    image = QImage(640, 360, QImage.Format.Format_RGB32)
    image.fill(QColor("#263340"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9eb1c1"), 3))
    painter.setBrush(QColor("#70869b"))
    for x, y, width, height in (
        (120, 170, 210, 105), (230, 105, 230, 160),
        (355, 155, 165, 120), (185, 205, 300, 80),
    ):
        painter.drawEllipse(x, y, width, height)
    painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "JPG", 90):
        raise LibraryError(f"Could not create a VDB placeholder preview for {name}.")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    token.check()


def _hash_source(
    source: Path,
    expected_snapshot: SourceFileSnapshot | None,
    material: str,
    callback: Callable[[ImportProgress], None] | None,
    token: CancelToken,
    completed_before: int,
    total_bytes: int,
) -> tuple[str, int]:
    if expected_snapshot and not _snapshot_matches(source, expected_snapshot):
        raise StaleSourceError(f"{source.name} changed after it was scanned.")
    before = source.stat()
    digest = hashlib.sha256()
    processed = 0
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            token.check()
            digest.update(chunk)
            processed += len(chunk)
            if callback:
                callback(ImportProgress(material, source.name, completed_before + processed, total_bytes))
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise StaleSourceError(f"{source.name} changed while it was being checked.")
    if expected_snapshot and not _snapshot_matches(source, expected_snapshot):
        raise StaleSourceError(f"{source.name} changed after it was scanned.")
    return digest.hexdigest(), processed


def _snapshot_matches(path: Path, snapshot: SourceFileSnapshot) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size == snapshot.size and stat.st_mtime_ns == snapshot.mtime_ns


def _map_manifest(
    source: TextureMap,
    path: str,
    size: int,
    digest: str,
    *,
    format_override: str | None = None,
) -> dict:
    return {
        "path": path,
        "original_path": source.relative_path,
        "format": format_override or source.file_format,
        "size": size,
        "sha256": digest,
        "bit_depth": source.bit_depth,
        "color_space": source.color_space,
        "normal_convention": source.normal_convention,
        "packed_channels": source.packed_channels,
        "preferred": source.preferred,
        "material": source.material,
        "lod": source.lod,
    }


def _build_repaired_asset(source_dir: Path, stage: Path, document: dict, token: CancelToken) -> None:
    stage.mkdir(parents=True, exist_ok=False)
    repaired = json.loads(json.dumps(document))
    asset_token = _filename_token(str(repaired["name"]))
    used_names: dict[Path, set[str]] = {}
    copied_sources: set[str] = set()

    for label in _sorted_resolution_labels(repaired.get("resolutions", {})):
        variant = repaired["resolutions"][label]
        for channel in sorted(variant.get("maps", {}), key=str.casefold):
            records = variant["maps"][channel]
            ordered_records = sorted(
                records,
                key=lambda record: (
                    not bool(record.get("preferred", False)),
                    str(record.get("path", "")).casefold(),
                ),
            )
            for record in ordered_records:
                token.check()
                relative = _safe_manifest_path(str(record["path"]))
                source = _safe_asset_file(source_dir, relative)
                texture_map = TextureMap(
                    channel=channel,
                    relative_path=relative,
                    file_format=str(record.get("format", source.suffix.lstrip("."))),
                    bit_depth=record.get("bit_depth"),
                    color_space=str(record.get("color_space", "")),
                    normal_convention=str(record.get("normal_convention", "")),
                    packed_channels=dict(record.get("packed_channels", {})),
                    preferred=bool(record.get("preferred", False)),
                )
                destination_dir = stage / "maps" / _safe_component(label)
                destination = _map_destination(
                    destination_dir, asset_token, label, channel, texture_map, source.suffix, used_names
                )
                _copy_verified(source, destination, str(record.get("sha256", "")))
                record["path"] = destination.relative_to(stage).as_posix()
                copied_sources.add(relative)

    preview_sources: dict[str, str] = {}
    previews = repaired.setdefault("previews", {})
    for role in ("thumbnail", "hero"):
        relative_value = previews.get(role)
        if not relative_value:
            continue
        relative = _safe_manifest_path(str(relative_value))
        if relative in preview_sources:
            previews[role] = preview_sources[relative]
            copied_sources.add(relative)
            continue
        token.check()
        source = _safe_asset_file(source_dir, relative)
        destination = stage / "previews" / f"{asset_token}_{role.title()}{_safe_suffix(source.suffix)}"
        _copy_verified(source, destination)
        stored = destination.relative_to(stage).as_posix()
        previews[role] = stored
        preview_sources[relative] = stored
        copied_sources.add(relative)

    for relative_value in repaired.get("source_metadata", []):
        token.check()
        relative = _safe_manifest_path(str(relative_value))
        source = _safe_asset_file(source_dir, relative)
        destination = stage / relative
        _copy_verified(source, destination)
        copied_sources.add(relative)

    for source in sorted(source_dir.rglob("*"), key=lambda item: str(item).casefold()):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir).as_posix()
        if relative == "asset.json" or relative in copied_sources:
            continue
        token.check()
        destination = stage / relative
        if destination.exists():
            destination = _unique_destination(destination.parent, destination.name, used_names)
        _copy_verified(source, destination)

    repaired["naming_version"] = NAMING_VERSION
    _atomic_json(stage / "asset.json", repaired)


def _copy_verified(source: Path, destination: Path, expected_hash: str = "") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if expected_hash and _sha256_file(destination) != expected_hash:
        raise LibraryError(f"Hash validation failed while repairing {source.name}.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_asset_file(asset_dir: Path, relative: str) -> Path:
    base = asset_dir.resolve(strict=True)
    candidate = (asset_dir / relative).resolve(strict=True)
    if not _contains(base, candidate) or not candidate.is_file():
        raise LibraryError(f"Unsafe or missing asset path: {relative}")
    return candidate


def _model_record_origin(record: dict) -> str:
    value = str(record.get("origin", "")).strip().casefold()
    if value in {"imported", "generated", "manual"}:
        return value
    return (
        "generated"
        if str(record.get("role", "")).casefold() == "generated_derivative"
        else "imported"
    )


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _sorted_resolution_labels(resolutions: dict) -> list[str]:
    def key(label: str) -> tuple[int, str]:
        digits = "".join(char for char in label if char.isdigit())
        return (int(digits) if digits else 999, label.casefold())

    return sorted(resolutions, key=key)


def _ordered_map_alternatives(alternatives: list[TextureMap]) -> list[TextureMap]:
    return sorted(alternatives, key=lambda item: (not item.preferred, item.relative_path.casefold()))


def _map_destination(
    parent: Path,
    asset_token: str,
    resolution: str,
    channel: str,
    texture_map: TextureMap,
    suffix: str,
    used: dict[Path, set[str]],
) -> Path:
    names = used.setdefault(parent, set())
    channel_token = _map_channel_token(channel, texture_map)
    stem = f"{asset_token}_{_filename_token(resolution)}_{channel_token}"
    if texture_map.lod:
        stem += f"_{_filename_token(texture_map.lod)}"
    extension = _safe_suffix(suffix or f".{texture_map.file_format}")
    candidate = f"{stem}{extension}"
    if candidate.casefold() in names and texture_map.bit_depth:
        candidate = f"{stem}_{texture_map.bit_depth}bit{extension}"
    counter = 2
    alternative_stem = Path(candidate).stem
    while candidate.casefold() in names:
        candidate = f"{alternative_stem}_alt{counter}{extension}"
        counter += 1
    names.add(candidate.casefold())
    return parent / candidate


def _map_channel_token(channel: str, texture_map: TextureMap) -> str:
    packed = _packed_map_token(texture_map.packed_channels)
    if packed:
        return packed
    aliases = {
        "base color": "BaseColor",
        "ambient occlusion": "AmbientOcclusion",
        "ao": "AO",
        "roughness": "Roughness",
        "glossiness": "Glossiness",
        "normal": "Normal",
        "displacement": "Displacement",
        "height": "Height",
        "bump": "Bump",
        "cavity": "Cavity",
        "metalness": "Metalness",
        "metallic": "Metalness",
        "specular": "Specular",
        "opacity": "Opacity",
        "emission": "Emission",
    }
    token = aliases.get(channel.strip().casefold(), _filename_token(channel).replace("_", ""))
    convention = texture_map.normal_convention.strip()
    if token == "Normal" and convention:
        token += f"_{_filename_token(convention).replace('_', '')}"
    return token


def _packed_map_token(layout: dict[str, str]) -> str:
    if not layout:
        return ""
    ordered = [str(layout[key]).casefold().replace(" ", "") for key in sorted(layout, key=str.casefold)]
    normalized = {value.replace("ambientocclusion", "ao").replace("metalness", "metallic") for value in ordered}
    if {"ao", "roughness", "metallic"}.issubset(normalized):
        return "ARM"
    return "Packed_" + "_".join(_filename_token(value).replace("_", "") for value in ordered)


def _filename_token(value: str) -> str:
    parts = [part for part in re.split(r"[^\w]+", value.strip(), flags=re.UNICODE) if part]
    # The reviewed display name is authoritative, including intentional case
    # such as "Blue wipe" or "iPhone". This helper only makes it portable.
    return "_".join(parts) or "Asset"


def _normalized_values(values) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value).strip())
        if not text or text.casefold() in seen:
            continue
        normalized.append(text)
        seen.add(text.casefold())
    return tuple(normalized)


def _manifest_primary_category(document: dict) -> str:
    primary = re.sub(
        r"\s+",
        " ",
        str(document.get("category", "Uncategorized")).strip(),
    ) or "Uncategorized"
    return "Uncategorized" if primary.casefold() == "surface" else primary


def _clean_tags(values, migrated=()) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value, normalize_case in (
        *((item, False) for item in values),
        *((item, True) for item in migrated),
    ):
        text = re.sub(r"\s+", " ", str(value).strip())
        if normalize_case:
            text = text.casefold()
        folded = text.casefold()
        if not text or folded == "surface" or folded in seen:
            continue
        cleaned.append(text)
        seen.add(folded)
    return tuple(cleaned)


def _provider_name(value: object) -> str:
    name = str(value or "").strip()
    return "Unknown" if not name or name.casefold() == "folder" else name


def _stock_manifest_paths(library_root: Path) -> list[Path]:
    stock_root = library_root / "stock"
    if not stock_root.is_dir():
        return []
    paths = set(stock_root.glob("*/*/asset.json"))
    for path in stock_root.glob("*/*.json"):
        lowered = path.stem.casefold()
        if "_metadata_" in lowered or "_extra_" in lowered:
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("type") == "stock":
            paths.add(path)
    return sorted(paths, key=lambda item: str(item).casefold())


def _asset_manifest_paths(library_root: Path) -> list[Path]:
    paths: set[Path] = set(_stock_manifest_paths(library_root))
    for name in ("textures", "atlases", "hdris", "models", "vdbs"):
        container = library_root / name
        if container.is_dir():
            paths.update(container.glob("**/asset.json"))
    return sorted(paths, key=lambda item: str(item).casefold())


def _asset_manifest_paths_for_type(library_root: Path, asset_type: str) -> list[Path]:
    container_name = _asset_container(asset_type)
    if asset_type == "stock":
        return _stock_manifest_paths(library_root)
    container = library_root / container_name
    if not container.is_dir():
        return []
    return sorted(container.glob("**/asset.json"), key=lambda item: str(item).casefold())


def _asset_container(asset_type: str) -> str:
    containers = {
        "texture_set": "textures",
        "atlas": "atlases",
        "hdri": "hdris",
        "model": "models",
        "stock": "stock",
        "vdb": "vdbs",
    }
    try:
        return containers[asset_type.casefold()]
    except KeyError as error:
        raise LibraryError(f"Unsupported asset type: {asset_type or 'unknown'}") from error


def _safe_suffix(value: str) -> str:
    suffix = "".join(char for char in value.casefold() if char.isalnum() or char == ".")
    if not suffix:
        return ".bin"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _unique_asset_destination(parent: Path, slug: str) -> Path:
    candidate = parent / slug
    counter = 2
    while candidate.exists():
        candidate = parent / f"{slug}-{counter}"
        counter += 1
    return candidate


def _unique_stock_token(
    parent: Path, token: str, ignored_names: set[str] | None = None
) -> str:
    try:
        ignored = {name.casefold() for name in (ignored_names or set())}
        names = {
            path.name.casefold() for path in parent.iterdir()
            if path.name.casefold() not in ignored
        }
    except OSError:
        names = set()
    candidate = token
    counter = 2
    while (
        f"{candidate}.json".casefold() in names
        or any(
            Path(name).stem in {
                candidate.casefold(),
                f"{candidate}_preview".casefold(),
                f"{candidate}_thumbnail".casefold(),
            }
            for name in names
        )
    ):
        candidate = f"{token}_{counter}"
        counter += 1
    return candidate


def _unique_stock_filename(filename: str, used: set[str]) -> str:
    safe_name = _portable_flat_filename(filename)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    candidate = safe_name
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _stock_manifest_file_paths(document: dict) -> list[str]:
    values = [
        str(document.get("source", {}).get("path", "")),
        str(document.get("preview", {}).get("video", "")),
        str(document.get("preview", {}).get("thumbnail", "")),
        *(str(value) for value in document.get("source_metadata", [])),
        *(
            str(record.get("path", ""))
            for record in document.get("extra_files", [])
            if isinstance(record, dict)
        ),
    ]
    return list(dict.fromkeys(_safe_manifest_path(value) for value in values if value))


def _stock_companion_name(filename: str, token: str, role: str) -> str:
    prefix = f"{token}_{role}_"
    if filename.casefold().startswith(prefix.casefold()):
        return filename[len(prefix):]
    return filename


def _claim_asset_destination(parent: Path, slug: str, claimed: set[str]) -> Path:
    candidate = parent / slug
    counter = 2
    while candidate.resolve().as_posix().casefold() in claimed:
        candidate = parent / f"{slug}-{counter}"
        counter += 1
    claimed.add(candidate.resolve().as_posix().casefold())
    return candidate


def _unique_destination(parent: Path, filename: str, used: dict[Path, set[str]]) -> Path:
    names = used.setdefault(parent, set())
    safe_name = _safe_filename(filename)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    candidate = safe_name
    counter = 2
    while candidate.casefold() in names:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    names.add(candidate.casefold())
    return parent / candidate


def _unique_flat_filename(filename: str, used: set[str]) -> str:
    safe_name = _safe_filename(filename)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    candidate = safe_name
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _safe_filename(value: str) -> str:
    stem = _slugify(Path(value).stem, separator="_") or "file"
    suffix = "".join(char for char in Path(value).suffix.casefold() if char.isalnum() or char == ".")
    return stem + suffix


def _preserved_source_filename(value: str) -> str:
    """Keep a Stock source name intact except for characters unsafe on Windows."""
    name = "".join(
        "_" if ord(character) < 32 or character in '<>:"/\\|?*' else character
        for character in Path(value).name
    ).rstrip(" .")
    if not name:
        return "source.mov"
    reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if Path(name).stem.casefold() in reserved:
        name = f"_{name}"
    return name


def _safe_component(value: str) -> str:
    return _slugify(value, separator="_").upper() or "UNKNOWN"


def _slugify(value: str, separator: str = "-") -> str:
    cleaned = "".join(char.casefold() if char.isalnum() else separator for char in value.strip())
    while separator * 2 in cleaned:
        cleaned = cleaned.replace(separator * 2, separator)
    return cleaned.strip(separator) or "asset"


def _atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_preview_files(hero_source: Path, hero_target: Path, thumb_source: Path, thumb_target: Path) -> None:
    """Publish one shared preview or two distinct files as a recoverable transaction."""
    nonce = uuid4().hex
    pairs = tuple({target: source for source, target in (
        (hero_source, hero_target), (thumb_source, thumb_target),
    )}.items())
    prepared: list[tuple[Path, Path, Path]] = []
    try:
        for target, source in pairs:
            temporary = target.with_name(f".{target.name}.{nonce}.new")
            backup = target.with_name(f".{target.name}.{nonce}.old")
            shutil.copyfile(source, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            prepared.append((temporary, target, backup))
        for temporary, target, backup in prepared:
            if target.exists():
                os.replace(target, backup)
            os.replace(temporary, target)
        _sync_directory(hero_target.parent)
    except Exception:
        for temporary, target, backup in reversed(prepared):
            if backup.exists():
                if target.exists():
                    target.unlink()
                os.replace(backup, target)
            temporary.unlink(missing_ok=True)
        raise
    else:
        for _temporary, _target, backup in prepared:
            backup.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
