from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .catalog import AssetRecord, CatalogIndex, CatalogRecord
from .repository import CancelToken, ImportCancelled, LibraryRepository


ASSET_TYPES = ("texture_set", "atlas", "hdri", "model", "stock")


@dataclass(frozen=True, slots=True)
class CatalogSectionResult:
    asset_type: str
    assets: tuple[AssetRecord, ...]
    discovered: int
    parsed: int
    reused: int
    removed: int
    warnings: tuple[str, ...] = ()


def refresh_catalog_section(
    repository: LibraryRepository,
    index: CatalogIndex,
    asset_type: str,
    *,
    cancel_token: CancelToken | None = None,
    phase: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> CatalogSectionResult:
    """Refresh one complete section while retaining last-good records on parse errors."""
    token = cancel_token or CancelToken()
    token.check()
    if phase:
        phase("Discovering manifests")
    manifests = repository.manifest_paths_for_type(asset_type)
    token.check()
    if progress:
        progress(len(manifests), 0)

    cached = index.records_for_section(asset_type)
    records: list[CatalogRecord] = []
    warnings: list[str] = []
    parsed = 0
    reused = 0
    if phase:
        phase("Verifying catalog")
    for processed, manifest_path in enumerate(manifests, 1):
        token.check()
        path = manifest_path.absolute()
        old = cached.get(path)
        try:
            stat = path.stat()
            if (
                old is not None
                and old.manifest_size == stat.st_size
                and old.manifest_mtime_ns == stat.st_mtime_ns
            ):
                record = old
                reused += 1
            else:
                asset = repository.load_asset_manifest(path, expected_type=asset_type)
                record = CatalogRecord(asset, path, stat.st_size, stat.st_mtime_ns)
                parsed += 1
            records.append(record)
        except (OSError, ValueError, TypeError, KeyError) as error:
            warnings.append(f"Could not load {path}: {error}")
            if old is not None:
                records.append(old)
        if progress:
            progress(len(manifests), processed)

    token.check()
    if phase:
        phase("Saving catalog")
    current_paths = {record.manifest_path for record in records}
    removed = len(set(cached) - current_paths)
    index.replace_section(asset_type, records)
    assets = tuple(sorted((record.asset for record in records), key=lambda item: item.name.casefold()))
    return CatalogSectionResult(
        asset_type, assets, len(manifests), parsed, reused, removed, tuple(warnings)
    )


class CatalogRefreshSignals(QObject):
    phase = pyqtSignal(str, str)
    progress = pyqtSignal(str, int, int)
    section_ready = pyqtSignal(str, object, object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    canceled = pyqtSignal()


class CatalogRefreshWorker(QRunnable):
    """Prioritized, cancellable QRunnable wrapper around catalog refreshes."""

    def __init__(
        self,
        repository: LibraryRepository,
        index: CatalogIndex,
        selected_asset_type: str,
        cancel_token: CancelToken | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        if selected_asset_type not in ASSET_TYPES:
            raise ValueError(f"Unsupported asset type: {selected_asset_type}")
        self.repository = repository
        self.index = index
        self.selected_asset_type = selected_asset_type
        self.cancel_token = cancel_token or CancelToken()
        self.signals = CatalogRefreshSignals()

    def _emit(self, name: str, *args) -> bool:
        """Emit unless Qt has already torn down the worker's signal object."""
        try:
            getattr(self.signals, name).emit(*args)
        except RuntimeError as error:
            if "has been deleted" not in str(error):
                raise
            # QApplication shutdown can delete QObject signal owners before a
            # canceled QThreadPool task reaches its final callback.
            self.cancel_token.cancel()
            return False
        return True

    def run(self) -> None:
        results: dict[str, CatalogSectionResult] = {}
        order = (self.selected_asset_type,) + tuple(
            value for value in ASSET_TYPES if value != self.selected_asset_type
        )
        try:
            for asset_type in order:
                result = refresh_catalog_section(
                    self.repository,
                    self.index,
                    asset_type,
                    cancel_token=self.cancel_token,
                    phase=lambda value, current=asset_type: self._emit(
                        "phase", current, value
                    ),
                    progress=lambda discovered, processed, current=asset_type: (
                        self._emit(
                            "progress", current, discovered, processed
                        )
                    ),
                )
                results[asset_type] = result
                if not self._emit(
                    "section_ready",
                    asset_type,
                    list(result.assets),
                    list(result.warnings),
                ):
                    return
            self._emit("finished", results)
        except ImportCancelled:
            # Cancellation is an expected stale-worker exit, not a user-facing failure.
            self._emit("canceled")
        except Exception as error:
            self._emit("failed", str(error))
