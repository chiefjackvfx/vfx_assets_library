from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.domain import MODEL_CATEGORIES, TEXTURE_CATEGORIES
from universal_asset_library.settings import AppSettings, SettingsStore, validate_library_path
from universal_asset_library.library import (
    CancelToken,
    LibraryRecoveryState,
    LibraryRepository,
    LibraryUpdateSummary,
    RepairProgress,
    RepairSummary,
)
from universal_asset_library.previews import (
    resolve_blender_executable,
    resolve_houdini_executable,
    validate_blender_executable,
    validate_houdini_executable,
)
from universal_asset_library.integrations.houdini import HoudiniBridgeClient, HoudiniInstallation, HoudiniPluginInstaller
from universal_asset_library.integrations.blender import (
    BlenderBridgeClient,
    BlenderInstallation,
    BlenderPluginInstaller,
)
from universal_asset_library.importer import StockTaxonomyStore
from universal_asset_library.categories import CategoryConfigStore


class RepairSignals(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class RepairWorker(QRunnable):
    def __init__(self, library_path: str, cancel_token: CancelToken) -> None:
        super().__init__()
        self.library_path = library_path
        self.cancel_token = cancel_token
        self.signals = RepairSignals()

    def run(self) -> None:
        try:
            summary = LibraryRepository(self.library_path).repair_legacy_names(
                progress=self.signals.progress.emit,
                cancel_token=self.cancel_token,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(summary)


class LibraryUpdateWorker(QRunnable):
    def __init__(self, library_path: str, cancel_token: CancelToken) -> None:
        super().__init__()
        self.library_path = library_path
        self.cancel_token = cancel_token
        self.signals = RepairSignals()

    def run(self) -> None:
        try:
            summary = LibraryRepository(self.library_path).update_library(
                progress=self.signals.progress.emit,
                cancel_token=self.cancel_token,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(summary)


class MaintenanceSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class MaintenanceWorker(QRunnable):
    def __init__(self, library_path: str, operation: str, force: bool = False) -> None:
        super().__init__()
        self.library_path = library_path
        self.operation = operation
        self.force = force
        self.signals = MaintenanceSignals()

    def run(self) -> None:
        try:
            repository = LibraryRepository(self.library_path)
            if self.operation == "cleanup":
                result = repository.cleanup_abandoned_staging()
            else:
                result = repository.recover_stale_lock(force=self.force)
        except Exception:
            self.signals.failed.emit(self.operation, traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(self.operation, result)


@dataclass(frozen=True, slots=True)
class LibraryInspectionResult:
    legacy_count: int
    library_update_count: int
    recovery: LibraryRecoveryState


class LibraryInspectionSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class LibraryInspectionWorker(QRunnable):
    """Inspect a potentially remote library without blocking the GUI thread."""

    def __init__(self, library_path: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.library_path = library_path
        self.signals = LibraryInspectionSignals()

    def run(self) -> None:
        try:
            repository = LibraryRepository(self.library_path)
            result = LibraryInspectionResult(
                legacy_count=repository.legacy_asset_count(),
                library_update_count=repository.library_update_count(),
                recovery=repository.recovery_state(),
            )
        except Exception:
            self._emit("failed", traceback.format_exc(limit=5))
        else:
            self._emit("finished", result)

    def _emit(self, name: str, value: object) -> None:
        """Ignore completion when Qt has already destroyed the receiver."""
        try:
            getattr(self.signals, name).emit(value)
        except RuntimeError as error:
            if "has been deleted" not in str(error):
                raise


_LIVE_INSPECTION_WORKERS: set[LibraryInspectionWorker] = set()


class HoudiniBridgeSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class HoudiniBridgeWorker(QRunnable):
    def __init__(self, operation: str, installations: list[HoudiniInstallation]) -> None:
        super().__init__()
        self.operation = operation
        self.installations = installations
        self.signals = HoudiniBridgeSignals()

    def run(self) -> None:
        try:
            if self.operation == "install":
                result = HoudiniPluginInstaller().install(self.installations)
            elif self.operation == "uninstall":
                result = HoudiniPluginInstaller().uninstall(self.installations)
            else:
                result = HoudiniBridgeClient(timeout=1.0).discover_sessions()
        except Exception:
            self.signals.failed.emit(self.operation, traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(self.operation, result)


class BlenderBridgeSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class BlenderBridgeWorker(QRunnable):
    def __init__(self, operation: str, installations: list[BlenderInstallation], configured_executable: str = "") -> None:
        super().__init__()
        self.operation = operation
        self.installations = installations
        self.configured_executable = configured_executable
        self.signals = BlenderBridgeSignals()

    def run(self) -> None:
        try:
            installer = BlenderPluginInstaller(configured_executable=self.configured_executable)
            if self.operation == "install":
                result = installer.install(self.installations)
            elif self.operation == "uninstall":
                result = installer.uninstall(self.installations)
            else:
                result = BlenderBridgeClient(timeout=1.0).discover_sessions()
        except Exception:
            self.signals.failed.emit(self.operation, traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(self.operation, result)


class SettingsTab(QWidget):
    settings_saved = pyqtSignal(object)
    library_repaired = pyqtSignal(object)
    library_updated = pyqtSignal(object)
    houdini_bridge_changed = pyqtSignal()
    blender_bridge_changed = pyqtSignal()

    def __init__(self, store: SettingsStore, initial: AppSettings) -> None:
        super().__init__()
        self.store = store
        self._saved = initial
        self._loading = False
        self._repairing = False
        self._repair_worker: RepairWorker | None = None
        self._repair_token: CancelToken | None = None
        self._legacy_count = 0
        self._library_update_count = 0
        self._update_worker: LibraryUpdateWorker | None = None
        self._maintenance_worker: MaintenanceWorker | None = None
        self._inspection_worker: LibraryInspectionWorker | None = None
        self._inspection_generation = 0
        self._inspection_pending = False
        self._inspection_shutdown = False
        self._inspection_requested = False
        self._houdini_worker: HoudiniBridgeWorker | None = None
        self._blender_bridge_worker: BlenderBridgeWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(18)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Choose where your library lives and set lightweight browsing defaults.")
        subtitle.setObjectName("mutedLabel")
        root.addWidget(title)
        root.addWidget(subtitle)

        library_panel = QFrame()
        library_panel.setObjectName("panel")
        library_layout = QVBoxLayout(library_panel)
        library_layout.setContentsMargins(18, 16, 18, 16)
        library_layout.setSpacing(11)
        library_title = QLabel("Library")
        library_title.setObjectName("sectionTitle")
        library_help = QLabel("Select the existing folder that will eventually contain your managed PBR texture library.")
        library_help.setObjectName("mutedLabel")
        library_help.setWordWrap(True)
        path_row = QHBoxLayout()
        self.library_path = QLineEdit()
        self.library_path.setPlaceholderText("No library folder configured")
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.library_path.clear)
        path_row.addWidget(self.library_path, 1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.clear_button)
        self.path_status = QLabel()
        self.path_status.setWordWrap(True)
        library_layout.addWidget(library_title)
        library_layout.addWidget(library_help)
        library_layout.addLayout(path_row)
        library_layout.addWidget(self.path_status)
        root.addWidget(library_panel)

        taxonomy_panel = QFrame()
        taxonomy_panel.setObjectName("panel")
        taxonomy_layout = QVBoxLayout(taxonomy_panel)
        taxonomy_layout.setContentsMargins(18, 16, 18, 16)
        taxonomy_layout.setSpacing(9)
        taxonomy_title = QLabel("Stock taxonomy")
        taxonomy_title.setObjectName("sectionTitle")
        taxonomy_help = QLabel(
            "Portable category and tag vocabularies are stored in the library’s .ual folder. "
            "Edit the JSON files externally, then reload them before the next Stock scan."
        )
        taxonomy_help.setObjectName("mutedLabel")
        taxonomy_help.setWordWrap(True)
        self.stock_categories_path = QLabel()
        self.stock_categories_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stock_categories_path.setWordWrap(True)
        self.stock_tags_path = QLabel()
        self.stock_tags_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stock_tags_path.setWordWrap(True)
        taxonomy_actions = QHBoxLayout()
        self.open_stock_categories = QPushButton("Open Categories")
        self.open_stock_categories.clicked.connect(lambda: self._open_stock_taxonomy("categories"))
        self.open_stock_tags = QPushButton("Open Tags")
        self.open_stock_tags.clicked.connect(lambda: self._open_stock_taxonomy("tags"))
        self.reveal_stock_taxonomy = QPushButton("Reveal Folder")
        self.reveal_stock_taxonomy.clicked.connect(self._reveal_stock_taxonomy)
        self.reload_stock_taxonomy = QPushButton("Reload")
        self.reload_stock_taxonomy.clicked.connect(self._reload_stock_taxonomy)
        taxonomy_actions.addWidget(self.open_stock_categories)
        taxonomy_actions.addWidget(self.open_stock_tags)
        taxonomy_actions.addWidget(self.reveal_stock_taxonomy)
        taxonomy_actions.addStretch()
        taxonomy_actions.addWidget(self.reload_stock_taxonomy)
        self.stock_taxonomy_status = QLabel()
        self.stock_taxonomy_status.setObjectName("mutedLabel")
        self.stock_taxonomy_status.setWordWrap(True)
        taxonomy_layout.addWidget(taxonomy_title)
        taxonomy_layout.addWidget(taxonomy_help)
        taxonomy_layout.addWidget(self.stock_categories_path)
        taxonomy_layout.addWidget(self.stock_tags_path)
        taxonomy_layout.addLayout(taxonomy_actions)
        taxonomy_layout.addWidget(self.stock_taxonomy_status)
        root.addWidget(taxonomy_panel)

        maintenance_panel = QFrame()
        maintenance_panel.setObjectName("panel")
        maintenance_layout = QVBoxLayout(maintenance_panel)
        maintenance_layout.setContentsMargins(18, 16, 18, 16)
        maintenance_layout.setSpacing(10)
        maintenance_heading = QHBoxLayout()
        maintenance_title = QLabel("Library maintenance")
        maintenance_title.setObjectName("sectionTitle")
        self.refresh_maintenance_button = QPushButton("Refresh status")
        self.refresh_maintenance_button.clicked.connect(
            self.refresh_maintenance_state
        )
        maintenance_heading.addWidget(maintenance_title)
        maintenance_heading.addStretch()
        maintenance_heading.addWidget(self.refresh_maintenance_button)
        maintenance_help = QLabel(
            "Validate the catalog, update older library layouts, or rename legacy assets without changing stable IDs or provider metadata."
        )
        maintenance_help.setObjectName("mutedLabel")
        maintenance_help.setWordWrap(True)
        maintenance_row = QHBoxLayout()
        self.repair_status = QLabel()
        self.repair_status.setObjectName("mutedLabel")
        self.repair_progress = QProgressBar()
        self.repair_progress.setTextVisible(True)
        self.repair_progress.setFixedWidth(180)
        self.repair_progress.hide()
        self.repair_cancel = QPushButton("Cancel")
        self.repair_cancel.clicked.connect(self._cancel_repair)
        self.repair_cancel.hide()
        self.repair_button = QPushButton("Check asset names")
        self.repair_button.clicked.connect(self._confirm_repair)
        maintenance_row.addWidget(self.repair_status, 1)
        maintenance_row.addWidget(self.repair_progress)
        maintenance_row.addWidget(self.repair_cancel)
        maintenance_row.addWidget(self.repair_button)
        maintenance_layout.addLayout(maintenance_heading)
        maintenance_layout.addWidget(maintenance_help)
        maintenance_layout.addLayout(maintenance_row)
        update_row = QHBoxLayout()
        self.update_status = QLabel("Check the library for layout updates and invalid manifests.")
        self.update_status.setObjectName("mutedLabel")
        self.update_library_button = QPushButton("Update / Fix Library")
        self.update_library_button.setObjectName("primaryButton")
        self.update_library_button.clicked.connect(self._confirm_library_update)
        update_row.addWidget(self.update_status, 1)
        update_row.addWidget(self.update_library_button)
        maintenance_layout.addLayout(update_row)
        recovery_row = QHBoxLayout()
        self.recovery_status = QLabel()
        self.recovery_status.setObjectName("mutedLabel")
        self.cleanup_staging_button = QPushButton("Clean staging")
        self.cleanup_staging_button.clicked.connect(self._confirm_cleanup_staging)
        self.recover_lock_button = QPushButton("Recover library lock")
        self.recover_lock_button.clicked.connect(self._confirm_recover_lock)
        recovery_row.addWidget(self.recovery_status, 1)
        recovery_row.addWidget(self.cleanup_staging_button)
        recovery_row.addWidget(self.recover_lock_button)
        maintenance_layout.addLayout(recovery_row)
        root.addWidget(maintenance_panel)

        preferences_panel = QFrame()
        preferences_panel.setObjectName("panel")
        preferences_layout = QVBoxLayout(preferences_panel)
        preferences_layout.setContentsMargins(18, 16, 18, 16)
        preferences_layout.setSpacing(12)
        preferences_title = QLabel("Display and importer")
        preferences_title.setObjectName("sectionTitle")
        form = QFormLayout()
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(14)
        self.thumbnail_size = QComboBox()
        self.thumbnail_size.addItem("Small — more textures", "small")
        self.thumbnail_size.addItem("Medium — balanced", "medium")
        self.thumbnail_size.addItem("Large — bigger previews", "large")
        self.stock_hover_previews = QCheckBox(
            "Play Stock previews when hovering over cards"
        )
        self.stock_hover_previews.setToolTip(
            "Uses the managed low-resolution preview and remains muted."
        )
        self.default_category = QComboBox()
        self.default_category.addItems(TEXTURE_CATEGORIES)
        self.default_model_category = QComboBox()
        self.default_model_category.addItems(MODEL_CATEGORIES)
        form.addRow("Thumbnail size", self.thumbnail_size)
        form.addRow("Grid previews", self.stock_hover_previews)
        form.addRow("Default texture category", self.default_category)
        form.addRow("Default model category", self.default_model_category)
        preferences_layout.addWidget(preferences_title)
        preferences_layout.addLayout(form)
        root.addWidget(preferences_panel)

        tools_panel = QFrame()
        tools_panel.setObjectName("panel")
        tools_layout = QVBoxLayout(tools_panel)
        tools_layout.setContentsMargins(18, 16, 18, 16)
        tools_layout.setSpacing(10)
        tools_title = QLabel("Media and preview tools")
        tools_title.setObjectName("sectionTitle")
        tools_help = QLabel(
            "Blender renders texture and HDRI previews. Houdini 22+ renders manual VDB stills. "
            "FFmpeg probes Stock clips, generates playable 480p previews, and extracts midpoint thumbnails. "
            "Leave an executable empty to auto-detect it."
        )
        tools_help.setObjectName("mutedLabel")
        tools_help.setWordWrap(True)
        blender_row = QHBoxLayout()
        self.blender_path = QLineEdit()
        self.blender_path.setPlaceholderText("Auto-detect Blender on PATH")
        self.blender_browse = QPushButton("Browse…")
        self.blender_browse.clicked.connect(self._browse_blender)
        self.blender_clear = QPushButton("Clear")
        self.blender_clear.clicked.connect(self.blender_path.clear)
        self.blender_check = QPushButton("Check Blender")
        self.blender_check.clicked.connect(self._check_blender)
        blender_row.addWidget(self.blender_path, 1)
        blender_row.addWidget(self.blender_browse)
        blender_row.addWidget(self.blender_clear)
        blender_row.addWidget(self.blender_check)
        self.blender_status = QLabel()
        self.blender_status.setObjectName("mutedLabel")
        self.blender_status.setWordWrap(True)
        houdini_preview_row = QHBoxLayout()
        self.houdini_path = QLineEdit()
        self.houdini_path.setPlaceholderText("Auto-detect Houdini 22 for VDB previews")
        self.houdini_browse = QPushButton("Browse Houdini…")
        self.houdini_browse.clicked.connect(self._browse_houdini)
        self.houdini_clear = QPushButton("Clear")
        self.houdini_clear.clicked.connect(self.houdini_path.clear)
        self.houdini_check = QPushButton("Check Houdini")
        self.houdini_check.clicked.connect(self._check_houdini)
        houdini_preview_row.addWidget(self.houdini_path, 1)
        houdini_preview_row.addWidget(self.houdini_browse)
        houdini_preview_row.addWidget(self.houdini_clear)
        houdini_preview_row.addWidget(self.houdini_check)
        self.houdini_preview_status = QLabel()
        self.houdini_preview_status.setObjectName("mutedLabel")
        self.houdini_preview_status.setWordWrap(True)
        vdb_parallel_row = QHBoxLayout()
        vdb_parallel_label = QLabel("Parallel VDB turntable renders")
        self.vdb_parallel_renders = QSpinBox()
        self.vdb_parallel_renders.setRange(1, 4)
        self.vdb_parallel_renders.setValue(2)
        self.vdb_parallel_renders.setSuffix(" instances")
        self.vdb_parallel_renders.setToolTip(
            "Each instance loads its own HIP and VDB. More instances require "
            "additional Houdini licenses, memory, and GPU capacity."
        )
        vdb_parallel_row.addWidget(vdb_parallel_label)
        vdb_parallel_row.addWidget(self.vdb_parallel_renders)
        vdb_parallel_row.addStretch()
        self.render_hdri_on_import = QCheckBox("Render composite previews automatically during HDRI import")
        self.render_texture_on_import = QCheckBox(
            "Render missing shader previews automatically during texture import"
        )
        self.save_texture_preview_blend = QCheckBox(
            "Save a debug Blender file beside generated texture previews"
        )
        self.save_texture_preview_blend.setToolTip(
            "Packs the selected maps into a .blend file in the asset's "
            "previews folder. This is intended for troubleshooting."
        )
        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_path = QLineEdit()
        self.ffmpeg_path.setPlaceholderText("Auto-detect FFmpeg on PATH")
        self.ffmpeg_browse = QPushButton("Browse FFmpeg…")
        self.ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_clear = QPushButton("Clear")
        self.ffmpeg_clear.clicked.connect(self.ffmpeg_path.clear)
        ffmpeg_row.addWidget(self.ffmpeg_path, 1)
        ffmpeg_row.addWidget(self.ffmpeg_browse)
        ffmpeg_row.addWidget(self.ffmpeg_clear)
        self.ffmpeg_status = QLabel()
        self.ffmpeg_status.setObjectName("mutedLabel")
        tools_layout.addWidget(tools_title)
        tools_layout.addWidget(tools_help)
        tools_layout.addLayout(blender_row)
        tools_layout.addWidget(self.blender_status)
        tools_layout.addLayout(houdini_preview_row)
        tools_layout.addWidget(self.houdini_preview_status)
        tools_layout.addLayout(vdb_parallel_row)
        tools_layout.addWidget(self.render_texture_on_import)
        tools_layout.addWidget(self.save_texture_preview_blend)
        tools_layout.addWidget(self.render_hdri_on_import)
        tools_layout.addLayout(ffmpeg_row)
        tools_layout.addWidget(self.ffmpeg_status)
        root.addWidget(tools_panel)

        houdini_panel = QFrame()
        houdini_panel.setObjectName("panel")
        houdini_layout = QVBoxLayout(houdini_panel)
        houdini_layout.setContentsMargins(18, 16, 18, 16)
        houdini_layout.setSpacing(10)
        houdini_title = QLabel("Houdini Bridge")
        houdini_title.setObjectName("sectionTitle")
        houdini_help = QLabel(
            "Install the lightweight ShotBox Assets package into Houdini 21 or 22, then restart Houdini. "
            "The bridge listens only on this computer and lets HDRIs create Solaris Dome Lights."
        )
        houdini_help.setObjectName("mutedLabel")
        houdini_help.setWordWrap(True)
        self.houdini_installations = QListWidget()
        self.houdini_installations.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.houdini_installations.setMaximumHeight(88)
        self.houdini_status = QLabel()
        self.houdini_status.setObjectName("mutedLabel")
        self.houdini_status.setWordWrap(True)
        houdini_actions = QHBoxLayout()
        self.houdini_install = QPushButton("Install / Update Plugin")
        self.houdini_install.setObjectName("primaryButton")
        self.houdini_install.clicked.connect(lambda: self._start_houdini_operation("install"))
        self.houdini_uninstall = QPushButton("Uninstall Plugin")
        self.houdini_uninstall.clicked.connect(lambda: self._start_houdini_operation("uninstall"))
        self.houdini_refresh = QPushButton("Refresh Connection")
        self.houdini_refresh.clicked.connect(lambda: self._start_houdini_operation("refresh"))
        houdini_actions.addWidget(self.houdini_install)
        houdini_actions.addWidget(self.houdini_uninstall)
        houdini_actions.addStretch()
        houdini_actions.addWidget(self.houdini_refresh)
        houdini_layout.addWidget(houdini_title)
        houdini_layout.addWidget(houdini_help)
        houdini_layout.addWidget(self.houdini_installations)
        houdini_layout.addLayout(houdini_actions)
        houdini_layout.addWidget(self.houdini_status)
        root.addWidget(houdini_panel)
        self._refresh_houdini_installations()

        blender_bridge_panel = QFrame()
        blender_bridge_panel.setObjectName("panel")
        blender_bridge_layout = QVBoxLayout(blender_bridge_panel)
        blender_bridge_layout.setContentsMargins(18, 16, 18, 16)
        blender_bridge_layout.setSpacing(10)
        blender_bridge_title = QLabel("Blender Bridge")
        blender_bridge_title.setObjectName("sectionTitle")
        blender_bridge_help = QLabel(
            "Install the lightweight ShotBox Assets extension into Blender 5.1 or 5.2, then restart Blender. "
            "This is separate from the Blender executable used for HDRI preview rendering."
        )
        blender_bridge_help.setObjectName("mutedLabel")
        blender_bridge_help.setWordWrap(True)
        self.blender_installations = QListWidget()
        self.blender_installations.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.blender_installations.setMaximumHeight(88)
        self.blender_bridge_status = QLabel()
        self.blender_bridge_status.setObjectName("mutedLabel")
        self.blender_bridge_status.setWordWrap(True)
        blender_bridge_actions = QHBoxLayout()
        self.blender_bridge_install = QPushButton("Install / Update Plugin")
        self.blender_bridge_install.setObjectName("primaryButton")
        self.blender_bridge_install.clicked.connect(lambda: self._start_blender_bridge_operation("install"))
        self.blender_bridge_uninstall = QPushButton("Uninstall Plugin")
        self.blender_bridge_uninstall.clicked.connect(lambda: self._start_blender_bridge_operation("uninstall"))
        self.blender_bridge_refresh = QPushButton("Refresh Connection")
        self.blender_bridge_refresh.clicked.connect(lambda: self._start_blender_bridge_operation("refresh"))
        blender_bridge_actions.addWidget(self.blender_bridge_install)
        blender_bridge_actions.addWidget(self.blender_bridge_uninstall)
        blender_bridge_actions.addStretch()
        blender_bridge_actions.addWidget(self.blender_bridge_refresh)
        blender_bridge_layout.addWidget(blender_bridge_title)
        blender_bridge_layout.addWidget(blender_bridge_help)
        blender_bridge_layout.addWidget(self.blender_installations)
        blender_bridge_layout.addLayout(blender_bridge_actions)
        blender_bridge_layout.addWidget(self.blender_bridge_status)
        root.addWidget(blender_bridge_panel)
        self._refresh_blender_installations()

        future = QLabel(
            "Later settings: filename-token mappings, scan on startup, thumbnail cache, "
            "color management, external tools, themes, and remembered filters."
        )
        future.setObjectName("mutedLabel")
        future.setWordWrap(True)
        root.addWidget(future)
        root.addStretch()

        footer = QHBoxLayout()
        self.save_button = QPushButton("Save settings")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save)
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self._reset)
        self.save_message = QLabel()
        self.save_message.setObjectName("mutedLabel")
        footer.addWidget(self.save_message)
        footer.addStretch()
        footer.addWidget(self.reset_button)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

        self.library_path.textChanged.connect(self._library_path_changed)
        self.thumbnail_size.currentIndexChanged.connect(self._changed)
        self.stock_hover_previews.toggled.connect(self._changed)
        self.default_category.currentIndexChanged.connect(self._changed)
        self.default_model_category.currentIndexChanged.connect(self._changed)
        self.blender_path.textChanged.connect(self._changed)
        self.houdini_path.textChanged.connect(self._changed)
        self.vdb_parallel_renders.valueChanged.connect(self._changed)
        self.ffmpeg_path.textChanged.connect(self._changed)
        self.render_hdri_on_import.toggled.connect(self._changed)
        self.render_texture_on_import.toggled.connect(self._changed)
        self.save_texture_preview_blend.toggled.connect(self._changed)
        self._show(self._saved)
        self._show_maintenance_unchecked()

    def _browse(self) -> None:
        current = self.library_path.text().strip()
        start = current if current and os.path.isdir(current) else ""
        folder = QFileDialog.getExistingDirectory(self, "Choose asset library folder", start)
        if folder:
            self.library_path.setText(folder)

    def _browse_blender(self) -> None:
        current = self.blender_path.text().strip()
        start = current if current and os.path.isfile(current) else ""
        filename, _filter = QFileDialog.getOpenFileName(self, "Choose Blender executable", start)
        if filename:
            self.blender_path.setText(filename)
            self._check_blender()

    def _browse_houdini(self) -> None:
        current = self.houdini_path.text().strip()
        start = current if current and os.path.isfile(current) else ""
        filename, _filter = QFileDialog.getOpenFileName(
            self, "Choose Houdini, hbatch, or hython executable", start
        )
        if filename:
            self.houdini_path.setText(filename)
            self._check_houdini()

    def _taxonomy_store(self) -> StockTaxonomyStore | None:
        path = self._saved.library_path
        if not path or not os.path.isdir(path):
            return None
        return StockTaxonomyStore(path)

    def _refresh_stock_taxonomy_status(self) -> None:
        store = self._taxonomy_store()
        enabled = store is not None
        for button in (
            self.open_stock_categories, self.open_stock_tags,
            self.reveal_stock_taxonomy, self.reload_stock_taxonomy,
        ):
            button.setEnabled(enabled)
        if not store:
            self.stock_categories_path.setText("Categories: library not configured")
            self.stock_tags_path.setText("Tags: library not configured")
            self.stock_taxonomy_status.setText("Save a valid library path to enable Stock taxonomy files.")
            return
        self.stock_categories_path.setText(f"Categories: {store.categories_path}")
        self.stock_tags_path.setText(f"Tags: {store.tags_path}")
        missing = [
            path.name for path in (store.categories_path, store.tags_path) if not path.is_file()
        ]
        self.stock_taxonomy_status.setText(
            f"{', '.join(missing)} will be created from defaults on the next Stock scan."
            if missing else "Stock taxonomy files are available."
        )

    def _open_stock_taxonomy(self, kind: str) -> None:
        store = self._taxonomy_store()
        if not store:
            return
        store.ensure_defaults()
        path = store.categories_path if kind == "categories" else store.tags_path
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._refresh_stock_taxonomy_status()

    def _reveal_stock_taxonomy(self) -> None:
        store = self._taxonomy_store()
        if not store:
            return
        store.ensure_defaults()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(store.control_root)))
        self._refresh_stock_taxonomy_status()

    def _reload_stock_taxonomy(self) -> None:
        store = self._taxonomy_store()
        if not store:
            return
        taxonomy = store.ensure_defaults()
        if store.last_warnings:
            self.stock_taxonomy_status.setText("\n".join(store.last_warnings))
            self.stock_taxonomy_status.setStyleSheet("color:#e6b566;")
        else:
            self.stock_taxonomy_status.setText(
                f"Loaded {len(taxonomy.categories)} categories and {len(taxonomy.tags)} canonical tags."
            )
            self.stock_taxonomy_status.setStyleSheet("color:#78c995;")

    def _browse_ffmpeg(self) -> None:
        current = self.ffmpeg_path.text().strip()
        start = current if current and os.path.isfile(current) else ""
        filename, _filter = QFileDialog.getOpenFileName(self, "Choose FFmpeg executable", start)
        if filename:
            self.ffmpeg_path.setText(filename)

    def _check_blender(self) -> None:
        self.blender_check.setEnabled(False)
        valid, message, _version = validate_blender_executable(self.blender_path.text())
        self.blender_status.setText(message)
        self.blender_status.setStyleSheet(f"color: {'#78c995' if valid else '#e6b566'};")
        self.blender_check.setEnabled(True)
        self._refresh_blender_installations()

    def _check_houdini(self) -> None:
        self.houdini_check.setEnabled(False)
        valid, message, _version = validate_houdini_executable(
            self.houdini_path.text()
        )
        self.houdini_preview_status.setText(message)
        self.houdini_preview_status.setStyleSheet(
            f"color: {'#78c995' if valid else '#e6b566'};"
        )
        self.houdini_check.setEnabled(True)

    def _refresh_houdini_installations(self) -> None:
        selected = {
            item.data(Qt.ItemDataRole.UserRole).preference_dir
            for item in self.houdini_installations.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }
        self.houdini_installations.clear()
        installer = HoudiniPluginInstaller()
        installations = installer.detect()
        for installation in installations:
            status = installer.status(installation)
            state = f"installed {status.version}" if status.installed else "not installed"
            if status.current:
                state += " · current"
            item = QListWidgetItem(f"{installation.label} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, installation)
            self.houdini_installations.addItem(item)
            if not selected or installation.preference_dir in selected:
                item.setSelected(True)
        available = bool(installations)
        self.houdini_install.setEnabled(available)
        self.houdini_uninstall.setEnabled(available)
        self.houdini_refresh.setEnabled(True)
        if not available:
            self.houdini_status.setText("No Houdini 21.0 or 22.0 user preference folders were detected. Launch Houdini once, then refresh Settings.")
            self.houdini_status.setStyleSheet("color: #e6b566;")
        elif not self.houdini_status.text():
            self.houdini_status.setText("Select one or more detected versions. A Houdini restart is required after installation or update.")

    def _selected_houdini_installations(self) -> list[HoudiniInstallation]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.houdini_installations.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), HoudiniInstallation)
        ]

    def _start_houdini_operation(self, operation: str) -> None:
        if self._houdini_worker is not None:
            return
        installations = self._selected_houdini_installations()
        if operation != "refresh" and not installations:
            self.houdini_status.setText("Select at least one detected Houdini version.")
            self.houdini_status.setStyleSheet("color: #e6b566;")
            return
        worker = HoudiniBridgeWorker(operation, installations)
        self._houdini_worker = worker
        for button in (self.houdini_install, self.houdini_uninstall, self.houdini_refresh):
            button.setEnabled(False)
        self.houdini_status.setText(
            "Checking running Houdini sessions…" if operation == "refresh" else
            "Installing the Houdini Bridge…" if operation == "install" else "Removing the Houdini package…"
        )
        self.houdini_status.setStyleSheet("color: #8792a1;")
        worker.signals.finished.connect(self._houdini_operation_finished)
        worker.signals.failed.connect(self._houdini_operation_failed)
        QThreadPool.globalInstance().start(worker)

    def _houdini_operation_finished(self, operation: str, result: object) -> None:
        self._houdini_worker = None
        self._refresh_houdini_installations()
        if operation == "refresh":
            sessions = list(result)
            self.houdini_status.setText(
                f"Connected to {len(sessions)} running Houdini session(s)." if sessions else
                "No running ShotBox Assets-enabled Houdini session found. Install the plugin and restart Houdini."
            )
            self.houdini_status.setStyleSheet("color: #78c995;" if sessions else "color: #e6b566;")
        elif operation == "install":
            self.houdini_status.setText(f"Installed ShotBox Assets Bridge for {len(list(result))} Houdini version(s). Restart those Houdini sessions.")
            self.houdini_status.setStyleSheet("color: #78c995;")
            self.houdini_bridge_changed.emit()
        else:
            self.houdini_status.setText(f"Removed ShotBox Assets Bridge from {len(list(result))} Houdini version(s). Restart Houdini to unload a running bridge.")
            self.houdini_status.setStyleSheet("color: #78c995;")
            self.houdini_bridge_changed.emit()

    def _houdini_operation_failed(self, _operation: str, details: str) -> None:
        self._houdini_worker = None
        self._refresh_houdini_installations()
        message = details.strip().splitlines()[-1] if details.strip() else "Unknown Houdini Bridge error"
        self.houdini_status.setText(message)
        self.houdini_status.setStyleSheet("color: #ef7d7d;")

    def _refresh_blender_installations(self) -> None:
        selected = {
            item.data(Qt.ItemDataRole.UserRole).executable
            for item in self.blender_installations.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), BlenderInstallation)
        }
        self.blender_installations.clear()
        installer = BlenderPluginInstaller(configured_executable=self.blender_path.text())
        installations = installer.detect()
        for installation in installations:
            status = installer.status(installation)
            state = f"installed {status.version}" if status.installed else "not installed"
            if status.current:
                state += " · current"
            item = QListWidgetItem(f"{installation.label} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, installation)
            self.blender_installations.addItem(item)
            if not selected or installation.executable in selected:
                item.setSelected(True)
        available = bool(installations)
        self.blender_bridge_install.setEnabled(available)
        self.blender_bridge_uninstall.setEnabled(available)
        self.blender_bridge_refresh.setEnabled(True)
        if not available:
            self.blender_bridge_status.setText(
                "No Blender 5.1 or 5.2 executable was detected. Configure the Blender executable above and refresh Settings."
            )
            self.blender_bridge_status.setStyleSheet("color: #e6b566;")
        elif not self.blender_bridge_status.text():
            self.blender_bridge_status.setText(
                "Select one or more detected versions. Restart Blender after installation or update."
            )

    def _selected_blender_installations(self) -> list[BlenderInstallation]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.blender_installations.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole), BlenderInstallation)
        ]

    def _start_blender_bridge_operation(self, operation: str) -> None:
        if self._blender_bridge_worker is not None:
            return
        installations = self._selected_blender_installations()
        if operation != "refresh" and not installations:
            self.blender_bridge_status.setText("Select at least one detected Blender version.")
            self.blender_bridge_status.setStyleSheet("color: #e6b566;")
            return
        worker = BlenderBridgeWorker(operation, installations, self.blender_path.text())
        self._blender_bridge_worker = worker
        for button in (self.blender_bridge_install, self.blender_bridge_uninstall, self.blender_bridge_refresh):
            button.setEnabled(False)
        self.blender_bridge_status.setText(
            "Checking running Blender sessions…" if operation == "refresh" else
            "Installing the Blender Bridge…" if operation == "install" else "Removing the Blender extension…"
        )
        self.blender_bridge_status.setStyleSheet("color: #8792a1;")
        worker.signals.finished.connect(self._blender_bridge_operation_finished)
        worker.signals.failed.connect(self._blender_bridge_operation_failed)
        QThreadPool.globalInstance().start(worker)

    def _blender_bridge_operation_finished(self, operation: str, result: object) -> None:
        self._blender_bridge_worker = None
        self._refresh_blender_installations()
        if operation == "refresh":
            sessions = list(result)
            self.blender_bridge_status.setText(
                f"Connected to {len(sessions)} running Blender session(s)." if sessions else
                "No running ShotBox Assets-enabled Blender session found. Install the extension and restart Blender."
            )
            self.blender_bridge_status.setStyleSheet("color: #78c995;" if sessions else "color: #e6b566;")
        elif operation == "install":
            self.blender_bridge_status.setText(
                f"Installed ShotBox Assets Bridge for {len(list(result))} Blender version(s). Restart those Blender sessions."
            )
            self.blender_bridge_status.setStyleSheet("color: #78c995;")
            self.blender_bridge_changed.emit()
        else:
            self.blender_bridge_status.setText(
                f"Removed ShotBox Assets Bridge from {len(list(result))} Blender version(s). Restart Blender to unload it."
            )
            self.blender_bridge_status.setStyleSheet("color: #78c995;")
            self.blender_bridge_changed.emit()

    def _blender_bridge_operation_failed(self, _operation: str, details: str) -> None:
        self._blender_bridge_worker = None
        self._refresh_blender_installations()
        message = details.strip().splitlines()[-1] if details.strip() else "Unknown Blender Bridge error"
        self.blender_bridge_status.setText(message)
        self.blender_bridge_status.setStyleSheet("color: #ef7d7d;")

    def _draft(self) -> AppSettings:
        return AppSettings(
            library_path=self.library_path.text(),
            thumbnail_size=str(self.thumbnail_size.currentData()),
            default_import_category=self.default_category.currentText(),
            default_model_category=self.default_model_category.currentText(),
            blender_path=self.blender_path.text(),
            houdini_path=self.houdini_path.text(),
            render_hdri_on_import=self.render_hdri_on_import.isChecked(),
            render_texture_on_import=self.render_texture_on_import.isChecked(),
            save_texture_preview_blend=(
                self.save_texture_preview_blend.isChecked()
            ),
            ffmpeg_path=self.ffmpeg_path.text(),
            stock_hover_previews=self.stock_hover_previews.isChecked(),
            vdb_parallel_renders=self.vdb_parallel_renders.value(),
        ).normalized()

    def _show(self, settings: AppSettings) -> None:
        self._loading = True
        self.library_path.setText(settings.library_path)
        self._refresh_texture_categories(settings.library_path, settings.default_import_category)
        size_index = self.thumbnail_size.findData(settings.thumbnail_size)
        self.thumbnail_size.setCurrentIndex(max(0, size_index))
        self.stock_hover_previews.setChecked(settings.stock_hover_previews)
        self.default_category.setCurrentText(settings.default_import_category)
        self.default_model_category.setCurrentText(settings.default_model_category)
        self.blender_path.setText(settings.blender_path)
        self.houdini_path.setText(settings.houdini_path)
        self.vdb_parallel_renders.setValue(settings.vdb_parallel_renders)
        self.ffmpeg_path.setText(settings.ffmpeg_path)
        self.render_hdri_on_import.setChecked(settings.render_hdri_on_import)
        self.render_texture_on_import.setChecked(
            settings.render_texture_on_import
        )
        self.save_texture_preview_blend.setChecked(
            settings.save_texture_preview_blend
        )
        self._loading = False
        detected = resolve_blender_executable(settings.blender_path)
        if detected:
            self.blender_status.setText(f"Blender executable: {detected}. Use Check Blender to verify its version.")
            self.blender_status.setStyleSheet("color: #8792a1;")
        else:
            self.blender_status.setText("Blender is unavailable; imports will keep their fallback preview.")
            self.blender_status.setStyleSheet("color: #e6b566;")
        detected_houdini = resolve_houdini_executable(settings.houdini_path)
        self.houdini_preview_status.setText(
            f"Houdini preview executable: {detected_houdini}. Use Check Houdini to verify its license and version."
            if detected_houdini else
            "Houdini 22 is unavailable; VDB still rendering is disabled."
        )
        self.houdini_preview_status.setStyleSheet(
            "color: #8792a1;" if detected_houdini else "color: #e6b566;"
        )
        from universal_asset_library.previews import resolve_ffmpeg
        detected_ffmpeg = resolve_ffmpeg(settings.ffmpeg_path)
        self.ffmpeg_status.setText(
            f"FFmpeg executable: {detected_ffmpeg}"
            if detected_ffmpeg else
            "FFmpeg is unavailable; Stock imports are disabled."
        )
        self.ffmpeg_status.setStyleSheet("color: #8792a1;" if detected_ffmpeg else "color: #e6b566;")
        self._refresh_stock_taxonomy_status()
        self.save_message.clear()
        self._changed()

    def _library_path_changed(self, path: str) -> None:
        self._refresh_texture_categories(path)
        self._changed()

    def _refresh_texture_categories(self, path: str, selected: str = "") -> None:
        current = selected or self.default_category.currentText()
        names = TEXTURE_CATEGORIES
        if path and os.path.isdir(path):
            names = CategoryConfigStore(path).load("texture_set").names
        self.default_category.blockSignals(True)
        self.default_category.clear()
        self.default_category.addItems(names)
        self.default_category.setCurrentText(
            current if current in names else "Uncategorized"
        )
        self.default_category.blockSignals(False)

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        draft = self._draft()
        valid, message = validate_library_path(draft.library_path)
        if not valid:
            color = "#ef7d7d"
        elif draft.library_path and not os.access(draft.library_path, os.W_OK):
            color = "#e6b566"
        elif draft.library_path:
            color = "#78c995"
        else:
            color = "#8792a1"
        self.path_status.setText(message)
        self.path_status.setStyleSheet(f"color: {color};")
        dirty = draft != self._saved
        self.save_button.setEnabled(dirty and valid)
        self.reset_button.setEnabled(dirty)
        if dirty:
            self.save_message.clear()
        self._update_repair_button(draft, valid)

    def _save(self) -> None:
        try:
            self._saved = self.store.save(self._draft())
        except (OSError, ValueError) as error:
            self._changed()
            self.save_message.setText(str(error))
            self.save_message.setStyleSheet("color: #ef7d7d;")
            return
        self._show(self._saved)
        self.save_message.setText("Settings saved.")
        self.save_message.setStyleSheet("color: #78c995;")
        self.settings_saved.emit(self._saved)
        self.refresh_maintenance_state()

    def _reset(self) -> None:
        self._show(self._saved)

    def _show_maintenance_unchecked(self) -> None:
        self._legacy_count = 0
        self._library_update_count = 0
        path = self._saved.library_path
        if path:
            self.repair_status.setText("Maintenance status has not been checked yet.")
            self.update_status.setText("Library layout status has not been checked yet.")
            self.recovery_status.setText("Recovery status has not been checked yet.")
        else:
            self.repair_status.setText("Configure and save a library path first.")
            self.update_status.setText("Configure and save a library path first.")
            self.recovery_status.setText("Recovery checks require a saved library path.")
        self.repair_status.setStyleSheet("color: #8792a1;")
        self.update_status.setStyleSheet("color: #8792a1;")
        self.recovery_status.setStyleSheet("color: #8792a1;")
        self.repair_button.setText("Check asset names")
        self.update_library_button.setText("Update / Fix Library")
        self.repair_button.setEnabled(False)
        self.update_library_button.setEnabled(False)
        self.cleanup_staging_button.setEnabled(False)
        self.recover_lock_button.setEnabled(False)
        self.refresh_maintenance_button.setEnabled(bool(path))

    def refresh_maintenance_state(self) -> None:
        """Queue a coalesced maintenance inspection for the saved library."""
        if self._inspection_shutdown:
            return
        self._inspection_requested = True
        self._inspection_generation += 1
        if self._inspection_worker is not None:
            self._inspection_pending = True
            self._set_inspection_busy(True)
            return
        self._start_maintenance_inspection(self._inspection_generation)

    def start_initial_maintenance_inspection(self) -> None:
        """Start the post-paint audit unless the user already requested one."""
        if not self._inspection_requested:
            self.refresh_maintenance_state()

    def _start_maintenance_inspection(self, generation: int) -> None:
        path = self._saved.library_path
        self._inspection_pending = False
        if not path:
            self._show_maintenance_unchecked()
            return
        self._set_inspection_busy(True)
        worker = LibraryInspectionWorker(path)
        self._inspection_worker = worker
        _LIVE_INSPECTION_WORKERS.add(worker)
        worker.signals.finished.connect(
            lambda result, active=worker: self._inspection_finished(
                generation, path, result, active
            )
        )
        worker.signals.failed.connect(
            lambda details, active=worker: self._inspection_failed(
                generation, path, details, active
            )
        )
        worker.signals.finished.connect(
            lambda _result, active=worker: _LIVE_INSPECTION_WORKERS.discard(active)
        )
        worker.signals.failed.connect(
            lambda _details, active=worker: _LIVE_INSPECTION_WORKERS.discard(active)
        )
        QThreadPool.globalInstance().start(worker, -1)

    def _set_inspection_busy(self, active: bool) -> None:
        self.refresh_maintenance_button.setEnabled(
            not active and bool(self._saved.library_path)
        )
        if active:
            self.repair_status.setText("Checking library in background…")
            self.update_status.setText("Checking library in background…")
            self.recovery_status.setText("Checking library in background…")
            for widget in (
                self.repair_button,
                self.update_library_button,
                self.cleanup_staging_button,
                self.recover_lock_button,
            ):
                widget.setEnabled(False)

    def _inspection_finished(
        self,
        generation: int,
        path: str,
        result: LibraryInspectionResult,
        worker: LibraryInspectionWorker,
    ) -> None:
        if self._inspection_worker is worker:
            self._inspection_worker = None
        if self._inspection_shutdown:
            return
        current = (
            generation == self._inspection_generation
            and path == self._saved.library_path
        )
        if current:
            self._apply_inspection_result(result)
        self._continue_pending_inspection()

    def _inspection_failed(
        self,
        generation: int,
        path: str,
        details: str,
        worker: LibraryInspectionWorker,
    ) -> None:
        if self._inspection_worker is worker:
            self._inspection_worker = None
        if self._inspection_shutdown:
            return
        current = (
            generation == self._inspection_generation
            and path == self._saved.library_path
        )
        if current:
            message = (
                details.strip().splitlines()[-1]
                if details.strip()
                else "Unknown library inspection error"
            )
            self.repair_status.setText(f"Could not inspect library: {message}")
            self.repair_status.setStyleSheet("color: #ef7d7d;")
            self.update_status.setText("Library maintenance status is unavailable.")
            self.recovery_status.setText("Recovery status is unavailable.")
            self._set_inspection_busy(False)
            self.repair_button.setEnabled(False)
            self.update_library_button.setEnabled(False)
            self.cleanup_staging_button.setEnabled(False)
            self.recover_lock_button.setEnabled(False)
        self._continue_pending_inspection()

    def _continue_pending_inspection(self) -> None:
        if self._inspection_pending and not self._inspection_shutdown:
            self._start_maintenance_inspection(self._inspection_generation)

    def _apply_inspection_result(self, result: LibraryInspectionResult) -> None:
        self._legacy_count = result.legacy_count
        self._library_update_count = result.library_update_count
        recovery = result.recovery
        staging_count = len(recovery.staging_directories)
        if recovery.lock_owner:
            stale = "stale local" if recovery.lock_is_local_stale else "active or remote"
            age = _lock_age(recovery.lock_created_at)
            self.recovery_status.setText(
                f"Library lock: PID {recovery.lock_owner} on {recovery.lock_host} ({stale}); "
                f"age {age}; {staging_count} staging folder(s)."
            )
        elif staging_count:
            self.recovery_status.setText(
                f"Found {staging_count} abandoned staging folder(s)."
            )
        else:
            self.recovery_status.setText(
                "No abandoned staging data or library lock detected."
            )
        self.cleanup_staging_button.setEnabled(
            staging_count > 0 and not recovery.lock_owner
        )
        self.recover_lock_button.setEnabled(bool(recovery.lock_owner))
        self.recover_lock_button.setProperty(
            "localStale", recovery.lock_is_local_stale
        )
        self.repair_status.setText(
            f"{self._legacy_count} legacy asset(s) can be renamed."
            if self._legacy_count
            else "Asset names already use the current convention."
        )
        self.repair_status.setStyleSheet("color: #8792a1;")
        self.update_status.setText(
            f"{self._library_update_count} asset or manifest issue(s) need attention."
            if self._library_update_count
            else "Library layout is current; the button can run a validation check."
        )
        self.update_status.setStyleSheet("color: #8792a1;")
        self.recovery_status.setStyleSheet("color: #8792a1;")
        self._set_inspection_busy(False)
        self._update_repair_button(self._draft(), validate_library_path(self._draft().library_path)[0])

    def _refresh_repair_state(self) -> None:
        """Compatibility wrapper for callers that request a maintenance refresh."""
        self.refresh_maintenance_state()

    def shutdown_maintenance(self) -> None:
        self._inspection_shutdown = True
        self._inspection_pending = False
        self._inspection_generation += 1

    def _update_repair_button(self, draft: AppSettings, valid: bool) -> None:
        saved_path = self._saved.library_path
        ready = (
            not self._repairing
            and self._inspection_worker is None
            and valid
            and bool(saved_path)
            and draft.library_path == saved_path
            and os.path.isdir(saved_path)
            and os.access(saved_path, os.W_OK)
            and self._legacy_count > 0
        )
        self.repair_button.setText(f"Rename existing assets ({self._legacy_count})" if self._legacy_count else "Asset names up to date")
        self.repair_button.setEnabled(ready)
        update_ready = (
            not self._repairing
            and self._inspection_worker is None
            and valid
            and bool(saved_path)
            and draft.library_path == saved_path
            and os.path.isdir(saved_path)
            and os.access(saved_path, os.W_OK)
        )
        label = f"Update / Fix Library ({self._library_update_count})" if self._library_update_count else "Check / Fix Library"
        self.update_library_button.setText(label)
        self.update_library_button.setEnabled(update_ready)

    def _confirm_library_update(self) -> None:
        answer = QMessageBox.question(
            self,
            "Update and validate library?",
            "Validate all catalog manifests, convert legacy secondary categories into tags, remove the reserved “surface” term, "
            "upgrade older HDRI/model layouts, and flatten legacy Stock assets into their category folders.\n\n"
            "Updates use the library lock, staging, validation, and atomic replacement.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_library_update()

    def _start_library_update(self) -> None:
        if self._repairing or not self._saved.library_path:
            return
        self._repairing = True
        self._repair_token = CancelToken()
        self._set_controls_for_repair(True)
        self.repair_progress.setRange(0, max(1, self._library_update_count))
        self.repair_progress.setValue(0)
        self.repair_progress.show()
        self.repair_cancel.show()
        self.update_status.setText("Validating and updating the library…")
        worker = LibraryUpdateWorker(self._saved.library_path, self._repair_token)
        self._update_worker = worker
        worker.signals.progress.connect(self._library_update_progressed)
        worker.signals.finished.connect(self._library_update_finished)
        worker.signals.failed.connect(self._library_update_failed)
        QThreadPool.globalInstance().start(worker)

    def _library_update_progressed(self, progress: RepairProgress) -> None:
        self.repair_progress.setMaximum(max(1, progress.total_assets))
        self.repair_progress.setValue(progress.completed_assets)
        self.update_status.setText(f"Updated {progress.asset}")

    def _library_update_finished(self, summary: LibraryUpdateSummary) -> None:
        self._repairing = False
        self._repair_token = None
        self._update_worker = None
        self._set_controls_for_repair(False)
        self.repair_progress.hide()
        self.repair_cancel.hide()
        if summary.updated:
            self.library_updated.emit(summary)
        self._refresh_repair_state()
        parts = [f"Updated {len(summary.updated)}", f"valid {summary.valid}"]
        if summary.failed:
            parts.append(f"needs attention {len(summary.failed)}")
        if summary.canceled:
            parts.append("canceled")
        self.update_status.setText(" · ".join(parts))
        self.update_status.setStyleSheet("color: #78c995;" if not summary.failed else "color: #e6b566;")

    def _library_update_failed(self, details: str) -> None:
        self._repairing = False
        self._repair_token = None
        self._update_worker = None
        self._set_controls_for_repair(False)
        self.repair_progress.hide()
        self.repair_cancel.hide()
        self._refresh_repair_state()
        message = details.strip().splitlines()[-1] if details.strip() else "Unknown library update error"
        self.update_status.setText(message)
        self.update_status.setStyleSheet("color: #ef7d7d;")

    def _confirm_repair(self) -> None:
        answer = QMessageBox.question(
            self,
            "Rename existing assets?",
            f"Rename {self._legacy_count} legacy asset(s) in:\n{self._saved.library_path}\n\n"
            "The operation uses staging and preserves stable asset IDs and provider metadata.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_repair()

    def _start_repair(self) -> None:
        if self._repairing or not self._saved.library_path or self._legacy_count < 1:
            return
        self._repairing = True
        self._repair_token = CancelToken()
        self._set_controls_for_repair(True)
        self.repair_progress.setRange(0, self._legacy_count)
        self.repair_progress.setValue(0)
        self.repair_progress.show()
        self.repair_cancel.show()
        self.repair_status.setText("Preparing safe staged rename…")
        worker = RepairWorker(self._saved.library_path, self._repair_token)
        self._repair_worker = worker
        worker.signals.progress.connect(self._repair_progressed)
        worker.signals.finished.connect(self._repair_finished)
        worker.signals.failed.connect(self._repair_failed)
        QThreadPool.globalInstance().start(worker)

    def _repair_progressed(self, progress: RepairProgress) -> None:
        self.repair_progress.setMaximum(progress.total_assets)
        self.repair_progress.setValue(progress.completed_assets)
        self.repair_status.setText(f"Renamed {progress.asset}")

    def _cancel_repair(self) -> None:
        if self._repair_token:
            self._repair_token.cancel()
            if self._update_worker:
                self.update_status.setText("Canceling safely after the current asset…")
            else:
                self.repair_status.setText("Canceling safely…")
            self.repair_cancel.setEnabled(False)

    def _repair_finished(self, summary: RepairSummary) -> None:
        self._repairing = False
        self._repair_worker = None
        self._repair_token = None
        self._set_controls_for_repair(False)
        self.repair_progress.hide()
        self.repair_cancel.hide()
        parts = [f"Renamed {len(summary.renamed)}"]
        if summary.skipped:
            parts.append(f"already current {len(summary.skipped)}")
        if summary.failed:
            parts.append(f"failed {len(summary.failed)}")
        if summary.canceled:
            parts.append("canceled")
        if summary.renamed:
            self.library_repaired.emit(summary)
        self._refresh_repair_state()
        self.repair_status.setText(" · ".join(parts))
        self.repair_status.setStyleSheet("color: #78c995;" if summary.renamed and not summary.failed else "color: #e6b566;")

    def _repair_failed(self, details: str) -> None:
        self._repairing = False
        self._repair_worker = None
        self._repair_token = None
        self._set_controls_for_repair(False)
        self.repair_progress.hide()
        self.repair_cancel.hide()
        self._refresh_repair_state()
        message = details.strip().splitlines()[-1] if details.strip() else "Unknown repair error"
        self.repair_status.setText(message)
        self.repair_status.setStyleSheet("color: #ef7d7d;")

    def _set_controls_for_repair(self, active: bool) -> None:
        for widget in (
            self.library_path,
            self.browse_button,
            self.clear_button,
            self.thumbnail_size,
            self.stock_hover_previews,
            self.default_category,
            self.ffmpeg_path,
            self.ffmpeg_browse,
            self.ffmpeg_clear,
            self.save_button,
            self.reset_button,
            self.repair_button,
            self.update_library_button,
            self.cleanup_staging_button,
            self.recover_lock_button,
            self.refresh_maintenance_button,
        ):
            widget.setEnabled(not active)
        self.repair_cancel.setEnabled(active)

    def _confirm_cleanup_staging(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clean abandoned staging data?",
            "Remove incomplete staged copies from the configured library? Source folders and visible assets are not changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_maintenance("cleanup")

    def _confirm_recover_lock(self) -> None:
        local_stale = bool(self.recover_lock_button.property("localStale"))
        message = (
            "Recover the verified stale lock from this workstation?"
            if local_stale
            else "This lock may belong to an active process or another workstation. Break it only after confirming no library write is running."
        )
        answer = QMessageBox.warning(
            self,
            "Recover library lock?",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_maintenance("unlock", force=not local_stale)

    def _start_maintenance(self, operation: str, force: bool = False) -> None:
        self._set_controls_for_repair(True)
        self.recovery_status.setText("Cleaning staging safely…" if operation == "cleanup" else "Recovering library lock…")
        worker = MaintenanceWorker(self._saved.library_path, operation, force)
        self._maintenance_worker = worker
        worker.signals.finished.connect(self._maintenance_finished)
        worker.signals.failed.connect(self._maintenance_failed)
        QThreadPool.globalInstance().start(worker)

    def _maintenance_finished(self, operation: str, result: object) -> None:
        self._maintenance_worker = None
        self._set_controls_for_repair(False)
        self._refresh_repair_state()
        if operation == "cleanup":
            self.recovery_status.setText(f"Removed {int(result)} abandoned staging folder(s).")
        else:
            self.recovery_status.setText("Library lock recovered." if result else "No library lock remained.")

    def _maintenance_failed(self, _operation: str, details: str) -> None:
        self._maintenance_worker = None
        self._set_controls_for_repair(False)
        self._refresh_repair_state()
        message = details.strip().splitlines()[-1] if details.strip() else "Unknown recovery error"
        self.recovery_status.setText(message)
        self.recovery_status.setStyleSheet("color: #ef7d7d;")


def _lock_age(value: str) -> str:
    if not value:
        return "unknown"
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()))
    except (ValueError, TypeError):
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
