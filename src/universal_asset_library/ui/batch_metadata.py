from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from universal_asset_library.library import (
    AssetMetadataPatch,
    CancelToken,
    CatalogIndex,
    CatalogRecord,
    LibraryRepository,
)


@dataclass(frozen=True, slots=True)
class BatchMetadataRequest:
    asset_id: str
    asset_name: str
    patch: AssetMetadataPatch


@dataclass(frozen=True, slots=True)
class BatchMetadataProgress:
    asset_id: str
    asset_name: str
    completed: int
    total: int


@dataclass(slots=True)
class BatchMetadataResult:
    updated: list[object] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    completed: int = 0
    total: int = 0
    canceled: bool = False


@dataclass(frozen=True, slots=True)
class BatchMetadataItemResult:
    request: BatchMetadataRequest
    updated: object | None
    error: str
    completed: int
    total: int


class BatchMetadataSignals(QObject):
    progress = pyqtSignal(object)
    item_finished = pyqtSignal(object)
    finished = pyqtSignal(object)


class BatchMetadataWorker(QRunnable):
    """Apply independent metadata patches, continuing after per-asset failures."""

    def __init__(
        self,
        library_path: str,
        requests: tuple[BatchMetadataRequest, ...],
        catalog_index: CatalogIndex | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        super().__init__()
        self.library_path = library_path
        self.requests = requests
        self.catalog_index = catalog_index
        self.cancel_token = cancel_token or CancelToken()
        self.signals = BatchMetadataSignals()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        repository = LibraryRepository(self.library_path)
        total = len(self.requests)
        result = BatchMetadataResult(total=total)
        asset_ids = tuple(request.asset_id for request in self.requests)
        manifest_hints = {}
        if self.catalog_index is not None:
            try:
                records = self.catalog_index.records_for_ids(asset_ids)
                manifest_hints = {
                    asset_id: record.manifest_path
                    for asset_id, record in records.items()
                }
            except Exception:
                # The catalog is disposable; the repository performs one safe
                # fallback discovery pass for any unresolved IDs.
                manifest_hints = {}
        try:
            with repository.metadata_patch_batch(
                asset_ids,
                manifest_hints,
                self.cancel_token,
            ) as batch:
                writer = None
                if self.catalog_index is not None:
                    try:
                        writer = self.catalog_index.writer()
                        writer.__enter__()
                    except Exception:
                        writer = None
                try:
                    for completed, request in enumerate(self.requests, 1):
                        if self.cancel_token.cancelled:
                            result.canceled = True
                            break
                        result.names[request.asset_id] = request.asset_name
                        try:
                            outcome = batch.patch(
                                request.asset_id, request.patch
                            )
                            updated = outcome.asset
                            if writer is not None:
                                try:
                                    writer.upsert(CatalogRecord.from_manifest(
                                        updated, outcome.manifest_path
                                    ))
                                except Exception:
                                    # A normal refresh repairs the disposable
                                    # local catalog.
                                    pass
                            result.updated.append(updated)
                            error_message = ""
                        except Exception as error:
                            if self.cancel_token.cancelled:
                                result.canceled = True
                                break
                            error_message = str(error)
                            updated = None
                            result.failures[request.asset_id] = error_message
                        result.completed = completed
                        self.signals.item_finished.emit(BatchMetadataItemResult(
                            request,
                            updated,
                            error_message,
                            completed,
                            total,
                        ))
                        self.signals.progress.emit(BatchMetadataProgress(
                            request.asset_id,
                            request.asset_name,
                            completed,
                            total,
                        ))
                finally:
                    if writer is not None:
                        try:
                            writer.__exit__(None, None, None)
                        except Exception:
                            pass
        except Exception as error:
            if self.cancel_token.cancelled:
                result.canceled = True
            else:
                self._report_session_failure(result, str(error))
        result.canceled = result.canceled or self.cancel_token.cancelled
        self.signals.finished.emit(result)

    def _report_session_failure(
        self, result: BatchMetadataResult, message: str
    ) -> None:
        total = len(self.requests)
        updated_ids = {asset.id for asset in result.updated}
        for completed, request in enumerate(self.requests, 1):
            if (
                request.asset_id in result.failures
                or request.asset_id in updated_ids
            ):
                continue
            result.names[request.asset_id] = request.asset_name
            result.failures[request.asset_id] = message
            result.completed = completed
            self.signals.item_finished.emit(BatchMetadataItemResult(
                request,
                None,
                message,
                completed,
                total,
            ))
            self.signals.progress.emit(BatchMetadataProgress(
                request.asset_id,
                request.asset_name,
                completed,
                total,
            ))
