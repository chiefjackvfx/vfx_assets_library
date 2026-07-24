from __future__ import annotations

from pathlib import Path
import os
import shutil
import traceback
from dataclasses import replace

from PyQt6.QtCore import QObject, QRunnable, QSize, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.domain import (
    ATLAS_CATEGORIES,
    HDRI_CATEGORIES,
    MODEL_CATEGORIES,
    STOCK_CATEGORIES,
    TEXTURE_CATEGORIES,
)
from universal_asset_library.categories import (
    CategoryConfigStore,
    default_category_catalog,
)
from universal_asset_library.importer import (
    Diagnostic,
    HdriCandidate,
    MaterialCandidate,
    ModelCandidate,
    ModelFile,
    StockCandidate,
    StockTaxonomy,
    StockTaxonomyStore,
    PreflightResult,
    ScanCancellationToken,
    ScanProgress,
    ScanResult,
    TextureMap,
    ZipExtractionProgress,
    ZipExtractionSummary,
    scan_atlas_folder,
    scan_hdri_folder,
    scan_mixed_folder,
    scan_model_folder,
    scan_stock_folder,
    scan_texture_folder,
    default_stock_taxonomy,
    unzip_all_zip_files,
)
from universal_asset_library.library import CancelToken, ImportProgress, ImportSummary, LibraryRepository
from .assets_tab import TagEditor
from .asset_type_tabs import AssetTypeTabs


class ScanSignals(QObject):
    progress = pyqtSignal(int, object)
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class ScanWorker(QRunnable):
    def __init__(
        self, token: int, source: str, default_category: str, default_model_category: str,
        cancel_token: ScanCancellationToken, mode: str = "texture_set", ffmpeg_path: str = "",
        library_path: str = "",
    ) -> None:
        super().__init__()
        self.token = token
        self.source = source
        self.default_category = default_category
        self.default_model_category = default_model_category
        self.cancel_token = cancel_token
        self.mode = mode
        self.ffmpeg_path = ffmpeg_path
        self.library_path = library_path
        self.signals = ScanSignals()

    def run(self) -> None:
        try:
            reason = ""
            taxonomy = default_stock_taxonomy()
            texture_categories = default_category_catalog("texture_set")
            taxonomy_warnings: list[str] = []
            if self.library_path and Path(self.library_path).is_dir():
                store = StockTaxonomyStore(self.library_path)
                taxonomy = store.ensure_defaults()
                taxonomy_warnings = store.last_warnings
                texture_categories = CategoryConfigStore(self.library_path).load("texture_set")
            if self.mode == "auto":
                result = scan_mixed_folder(
                    self.source, self.default_category, self.default_model_category,
                    progress=lambda value: self.signals.progress.emit(self.token, value),
                    cancel_token=self.cancel_token,
                    ffprobe_path=self.ffmpeg_path,
                    stock_taxonomy=taxonomy,
                    texture_category_catalog=texture_categories,
                )
            else:
                scanners = {
                    "atlas": scan_atlas_folder, "hdri": scan_hdri_folder,
                    "model": scan_model_folder, "stock": scan_stock_folder,
                }
                scanner = scanners.get(self.mode, scan_texture_folder)
                category = self.default_model_category if self.mode == "model" else self.default_category
                scanner_kwargs = {
                    "progress": lambda value: self.signals.progress.emit(self.token, value),
                    "cancel_token": self.cancel_token,
                }
                if self.mode == "stock":
                    scanner_kwargs["ffprobe_path"] = self.ffmpeg_path
                    scanner_kwargs["taxonomy"] = taxonomy
                elif self.mode == "texture_set":
                    scanner_kwargs["category_catalog"] = texture_categories
                result = scanner(self.source, category, **scanner_kwargs)
                result.detected_asset_type = self.mode
                result.detection_reason = reason
            for message in taxonomy_warnings:
                result.diagnostics.append(Diagnostic(
                    "warning", "stock_taxonomy_fallback", message,
                    str(Path(self.library_path) / ".ual"),
                ))
                result.warnings.append(message)
        except Exception:
            self.signals.failed.emit(self.token, traceback.format_exc(limit=4))
        else:
            self.signals.finished.emit(self.token, result)


class ImportSignals(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class ImportWorker(QRunnable):
    def __init__(
        self,
        library_path: str,
        materials: list[MaterialCandidate],
        cancel_token: CancelToken,
        preflight: PreflightResult,
        conflict_decisions: dict[str, str],
        blender_path: str = "",
        render_hdri_previews: bool = True,
        ffmpeg_path: str = "",
    ) -> None:
        super().__init__()
        self.library_path = library_path
        self.materials = materials
        self.cancel_token = cancel_token
        self.preflight = preflight
        self.conflict_decisions = conflict_decisions
        self.blender_path = blender_path
        self.render_hdri_previews = render_hdri_previews
        self.ffmpeg_path = ffmpeg_path
        self.signals = ImportSignals()

    def run(self) -> None:
        try:
            summary = LibraryRepository(
                self.library_path,
                blender_path=self.blender_path,
                render_hdri_previews=self.render_hdri_previews,
                ffmpeg_path=self.ffmpeg_path,
            ).import_materials(
                self.materials,
                progress=self.signals.progress.emit,
                cancel_token=self.cancel_token,
                preflight_result=self.preflight,
                conflict_decisions=self.conflict_decisions,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(summary)


class PreflightWorker(QRunnable):
    def __init__(
        self,
        library_path: str,
        materials: list[MaterialCandidate],
        cancel_token: CancelToken,
        hash_cache: dict[tuple[str, int, int], str],
        ffmpeg_path: str = "",
    ) -> None:
        super().__init__()
        self.library_path = library_path
        self.materials = materials
        self.cancel_token = cancel_token
        self.hash_cache = hash_cache
        self.ffmpeg_path = ffmpeg_path
        self.signals = ImportSignals()

    def run(self) -> None:
        try:
            result = LibraryRepository(self.library_path, ffmpeg_path=self.ffmpeg_path).preflight_materials(
                self.materials,
                progress=self.signals.progress.emit,
                cancel_token=self.cancel_token,
                hash_cache=self.hash_cache,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(result)


class UnzipWorker(QRunnable):
    def __init__(self, source: str, cancel_token: ScanCancellationToken) -> None:
        super().__init__()
        self.source = source
        self.cancel_token = cancel_token
        self.signals = ImportSignals()

    def run(self) -> None:
        try:
            result = unzip_all_zip_files(
                self.source,
                progress=self.signals.progress.emit,
                cancel_token=self.cancel_token,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(result)


class ImporterTab(QWidget):
    """Shared folder scanner and review workflow for supported asset types."""

    import_completed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._default_category = "Uncategorized"
        self._default_model_category = "Uncategorized"
        self._blender_path = ""
        self._render_hdri_previews = True
        self._ffmpeg_path = ""
        self._stock_taxonomy: StockTaxonomy = default_stock_taxonomy()
        self._category_catalogs = {
            asset_type: default_category_catalog(asset_type)
            for asset_type in ("texture_set", "atlas", "hdri", "model", "stock")
        }
        self._scan_token = 0
        self._result = ScanResult()
        self._current: MaterialCandidate | None = None
        self._loading = False
        self._workers: list[ScanWorker] = []
        self._scan_cancel_token: ScanCancellationToken | None = None
        self._library_path = ""
        self._importing = False
        self._import_worker: ImportWorker | None = None
        self._preflight_worker: PreflightWorker | None = None
        self._cancel_token: CancelToken | None = None
        self._preflight_result: PreflightResult | None = None
        self._conflict_decisions: dict[str, str] = {}
        self._hash_cache: dict[tuple[str, int, int], str] = {}
        self._preflighting = False
        self._reclassifying = False
        self._unzip_worker: UnzipWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(15)
        heading = QHBoxLayout()
        self.title = QLabel("Import PBR Textures")
        self.title.setObjectName("pageTitle")
        self.import_mode = AssetTypeTabs()
        self.import_mode.currentIndexChanged.connect(self._change_mode)
        self.auto_detect = QCheckBox("Auto detect")
        self.auto_detect.setChecked(True)
        self.auto_detect.setToolTip("Inspect the selected folder and choose Textures, HDRIs, or Models automatically.")
        self.import_mode.tabBarClicked.connect(lambda _index: self.auto_detect.setChecked(False))
        heading.addWidget(self.title)
        heading.addStretch()
        heading.addWidget(self.auto_detect)
        heading.addWidget(self.import_mode)
        self.subtitle = QLabel("Scan provider metadata and local texture maps, then review the normalized material sets.")
        self.subtitle.setObjectName("mutedLabel")
        root.addLayout(heading)
        root.addWidget(self.subtitle)

        notice = QFrame()
        notice.setObjectName("notice")
        notice.setStyleSheet("QFrame#notice { background:#242020; border:1px solid #FF6B35; border-radius:2px; } QLabel { background:transparent; }")
        notice_layout = QHBoxLayout(notice)
        notice_layout.setContentsMargins(14, 9, 14, 9)
        notice_label = QLabel(
            "Scanning is read-only. Unzip all ZIPs is a separate source-folder action "
            "that creates one new folder beside each ZIP."
        )
        notice_label.setStyleSheet("color:#FF8357;")
        notice_layout.addWidget(notice_label)
        root.addWidget(notice)

        source_panel = QFrame()
        source_panel.setObjectName("panel")
        source_layout = QHBoxLayout(source_panel)
        source_layout.setContentsMargins(16, 14, 16, 14)
        self.source_path = QLineEdit()
        self.source_path.setPlaceholderText("Choose one material folder or a parent library folder")
        self.browse_button = QPushButton("Choose folder…")
        self.browse_button.clicked.connect(self._choose_folder)
        self.scan_button = QPushButton("Scan folder")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self._start_scan)
        self.unzip_button = QPushButton("Unzip all ZIPs")
        self.unzip_button.setToolTip(
            "Extract every ZIP under this import folder into a same-name sibling folder."
        )
        self.unzip_button.clicked.connect(self._start_unzip)
        self.scan_cancel_button = QPushButton("Cancel scan")
        self.scan_cancel_button.clicked.connect(self._cancel_scan)
        self.scan_cancel_button.hide()
        self.rescan_stale_button = QPushButton("Rescan stale")
        self.rescan_stale_button.clicked.connect(self._start_scan)
        self.rescan_stale_button.hide()
        source_layout.addWidget(self.source_path, 1)
        source_layout.addWidget(self.browse_button)
        source_layout.addWidget(self.scan_button)
        source_layout.addWidget(self.unzip_button)
        source_layout.addWidget(self.scan_cancel_button)
        source_layout.addWidget(self.rescan_stale_button)
        root.addWidget(source_panel)

        self.status = QLabel("Choose a folder to begin.")
        self.status.setObjectName("mutedLabel")
        root.addWidget(self.status)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_material_list())
        splitter.addWidget(self._build_review_panel())
        splitter.setSizes([290, 900])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.scan_summary = QLabel("No scan results.")
        self.scan_summary.setObjectName("mutedLabel")
        self.import_progress = QProgressBar()
        self.import_progress.setRange(0, 1000)
        self.import_progress.setValue(0)
        self.import_progress.setTextVisible(False)
        self.import_progress.setFixedWidth(180)
        self.import_progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_import)
        self.cancel_button.hide()
        self.import_button = QPushButton("Import checked assets (0)")
        self.import_button.setObjectName("primaryButton")
        self.import_button.clicked.connect(self._start_import)
        self.import_button.setEnabled(False)
        self.preflight_button = QPushButton("Preflight checked assets (0)")
        self.preflight_button.clicked.connect(self._start_preflight)
        self.preflight_button.setEnabled(False)
        footer.addWidget(self.scan_summary)
        footer.addStretch()
        footer.addWidget(self.import_progress)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.preflight_button)
        footer.addWidget(self.import_button)
        root.addLayout(footer)
        self._set_review_enabled(False)

    def _build_material_list(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(245)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        self.detected_heading = QLabel("Detected assets")
        self.detected_heading.setObjectName("sectionTitle")
        self.material_list = QListWidget()
        self.material_list.setSpacing(4)
        self.material_list.setUniformItemSizes(True)
        self.material_list.setStyleSheet(
            "QListWidget { padding: 4px; }"
            "QListWidget::item { background:#1E2228; border:1px solid #2F3542; "
            "border-radius:2px; padding:7px 9px; color:#D4D4D4; }"
            "QListWidget::item:hover { background:#252931; border-color:#404754; }"
            "QListWidget::item:selected { background:#242020; border:2px solid #FF6B35; color:#FFFFFF; }"
        )
        self.material_list.currentRowChanged.connect(self._select_material)
        self.material_list.itemChanged.connect(self._update_import_state)
        self.ignored_summary = QLabel("")
        self.ignored_summary.setObjectName("mutedLabel")
        self.ignored_summary.setWordWrap(True)
        layout.addWidget(self.detected_heading)
        layout.addWidget(self.material_list, 1)
        layout.addWidget(self.ignored_summary)
        return panel

    def _build_review_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QFrame()
        content.setObjectName("panel")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(13)
        heading_row = QHBoxLayout()
        self.review_heading = QLabel("Asset details")
        self.review_heading.setObjectName("sectionTitle")
        self.provider_label = QLabel("No asset selected")
        self.provider_label.setObjectName("mutedLabel")
        heading_row.addWidget(self.review_heading)
        heading_row.addStretch()
        heading_row.addWidget(self.provider_label)
        layout.addLayout(heading_row)

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        self.asset_name = QLineEdit()
        self.category = QComboBox()
        self.category.setEditable(False)
        self.category.addItems(TEXTURE_CATEGORIES)
        self.tags = TagEditor((), suggestions=())
        self.author = QLineEdit()
        self.description = QTextEdit()
        self.description.setMaximumHeight(58)
        self.stock_alpha = QComboBox()
        self.stock_alpha.addItem("Alpha", "yes")
        self.stock_alpha.addItem("Opaque", "no")
        self.stock_alpha.addItem("Unknown", "unknown")
        self.stock_preview = QComboBox()
        self.stock_preview_policy = QComboBox()
        self.stock_preview_policy.addItem("Use existing preview", "use_existing")
        self.stock_preview_policy.addItem("Generate 480p H.264 preview", "generate")
        self.stock_media_summary = QLabel()
        self.stock_media_summary.setObjectName("mutedLabel")
        self.stock_media_summary.setWordWrap(True)
        self.stock_inference = QLabel()
        self.stock_inference.setObjectName("mutedLabel")
        self.stock_inference.setWordWrap(True)
        type_editor = QWidget()
        type_layout = QHBoxLayout(type_editor)
        type_layout.setContentsMargins(0, 0, 0, 0)
        self.detected_type = QComboBox()
        self.detected_type.addItem("Texture material", "texture_set")
        self.detected_type.addItem("Atlas", "atlas")
        self.detected_type.addItem("HDRI", "hdri")
        self.detected_type.addItem("3D model", "model")
        self.detected_type.addItem("Stock footage", "stock")
        self.reclassify_button = QPushButton("Reclassify folder")
        self.reclassify_button.clicked.connect(self._reclassify_selected)
        type_layout.addWidget(self.detected_type, 1)
        type_layout.addWidget(self.reclassify_button)
        form.addRow("Name", self.asset_name)
        form.addRow("Detected type", type_editor)
        form.addRow("Category", self.category)
        form.addRow("Tags", self.tags)
        form.addRow("Author", self.author)
        form.addRow("Description", self.description)
        form.addRow("Alpha", self.stock_alpha)
        form.addRow("Stock preview", self.stock_preview)
        form.addRow("Preview policy", self.stock_preview_policy)
        form.addRow("Media", self.stock_media_summary)
        form.addRow("Smart suggestions", self.stock_inference)
        self.stock_fields = (
            self.stock_alpha, self.stock_preview, self.stock_preview_policy,
            self.stock_media_summary, self.stock_inference,
        )
        self.stock_field_labels = []
        for widget in self.stock_fields:
            widget.hide()
            label = form.labelForField(widget)
            if label:
                self.stock_field_labels.append(label)
                label.hide()
        layout.addLayout(form)

        self.model_status = QLabel("No USD")
        self.model_status.setObjectName("sectionTitle")
        self.model_status.hide()
        layout.addWidget(self.model_status)
        self.model_table = QTableWidget(0, 7)
        self.model_table.setHorizontalHeaderLabels(["Preferred", "File", "Format", "Role", "Component", "LOD", "Triangles"])
        model_header = self.model_table.horizontalHeader()
        model_header.setSectionResizeMode(0, model_header.ResizeMode.ResizeToContents)
        model_header.setSectionResizeMode(1, model_header.ResizeMode.Stretch)
        for column in range(2, 7):
            model_header.setSectionResizeMode(column, model_header.ResizeMode.ResizeToContents)
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.setMinimumHeight(150)
        self.model_table.hide()
        layout.addWidget(self.model_table)

        preview_heading = QLabel("Preview assignments")
        preview_heading.setObjectName("sectionTitle")
        layout.addWidget(preview_heading)
        preview_row = QHBoxLayout()
        thumb_group = QVBoxLayout()
        thumb_group.addWidget(QLabel("Thumbnail"))
        self.thumbnail_combo = QComboBox()
        self.thumbnail_combo.currentIndexChanged.connect(lambda index: self._preview_changed("thumbnail", index))
        self.thumbnail_image = self._preview_label(150, 95)
        thumb_group.addWidget(self.thumbnail_combo)
        thumb_group.addWidget(self.thumbnail_image)
        hero_group = QVBoxLayout()
        hero_group.addWidget(QLabel("Hero preview"))
        self.hero_combo = QComboBox()
        self.hero_combo.currentIndexChanged.connect(lambda index: self._preview_changed("hero", index))
        self.hero_image = self._preview_label(230, 95)
        hero_group.addWidget(self.hero_combo)
        hero_group.addWidget(self.hero_image)
        preview_row.addLayout(thumb_group, 1)
        preview_row.addLayout(hero_group, 1)
        layout.addLayout(preview_row)

        maps_row = QHBoxLayout()
        self.files_heading = QLabel("Files by resolution")
        self.files_heading.setObjectName("sectionTitle")
        self.resolution = QComboBox()
        self.resolution.currentTextChanged.connect(self._show_resolution)
        maps_row.addWidget(self.files_heading)
        maps_row.addStretch()
        maps_row.addWidget(QLabel("Resolution"))
        maps_row.addWidget(self.resolution)
        layout.addLayout(maps_row)
        self.channel_table = QTableWidget(0, 7)
        self.channel_table.setHorizontalHeaderLabels(["Channel", "Preferred", "File", "Format", "Bits", "Color", "Normal / packing"])
        header = self.channel_table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        for column in range(3, 7):
            header.setSectionResizeMode(column, header.ResizeMode.ResizeToContents)
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setMinimumHeight(190)
        layout.addWidget(self.channel_table)

        extras_heading = QLabel("Retained extra files")
        extras_heading.setObjectName("sectionTitle")
        self.extra_files = QTextEdit()
        self.extra_files.setReadOnly(True)
        self.extra_files.setMaximumHeight(82)
        layout.addWidget(extras_heading)
        layout.addWidget(self.extra_files)

        self.excluded_heading = QLabel("Excluded source files")
        self.excluded_heading.setObjectName("sectionTitle")
        self.excluded_files = QTextEdit()
        self.excluded_files.setReadOnly(True)
        self.excluded_files.setMaximumHeight(92)
        self.excluded_heading.hide()
        self.excluded_files.hide()
        layout.addWidget(self.excluded_heading)
        layout.addWidget(self.excluded_files)

        warnings_row = QHBoxLayout()
        warnings_heading = QLabel("Review notes")
        warnings_heading.setObjectName("sectionTitle")
        self.diagnostic_filter = QComboBox()
        self.diagnostic_filter.addItem("All notes", "all")
        self.diagnostic_filter.addItem("Errors only", "error")
        self.diagnostic_filter.addItem("Warnings only", "warning")
        self.diagnostic_filter.currentIndexChanged.connect(self._show_diagnostics)
        warnings_row.addWidget(warnings_heading)
        warnings_row.addStretch()
        warnings_row.addWidget(self.diagnostic_filter)
        self.warnings = QTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setMaximumHeight(80)
        layout.addLayout(warnings_row)
        layout.addWidget(self.warnings)
        layout.addStretch()
        scroll.setWidget(content)

        for editor in (self.asset_name, self.author):
            editor.textChanged.connect(self._save_edits)
        self.tags.tags_changed.connect(self._save_edits)
        self.category.currentTextChanged.connect(self._save_edits)
        self.description.textChanged.connect(self._save_edits)
        self.stock_alpha.currentIndexChanged.connect(self._save_stock_options)
        self.stock_preview.currentIndexChanged.connect(self._save_stock_options)
        self.stock_preview_policy.currentIndexChanged.connect(self._save_stock_options)
        return scroll

    @staticmethod
    def _preview_label(width: int, height: int) -> QLabel:
        label = QLabel("No preview")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedHeight(height)
        label.setMinimumWidth(width)
        label.setStyleSheet("background:#141619; border:1px solid #2F3542; border-radius:2px; color:#6B7280;")
        return label

    def _choose_folder(self) -> None:
        start = self.source_path.text() if Path(self.source_path.text()).is_dir() else ""
        kind = "HDRI" if self._mode() == "hdri" else "texture"
        folder = QFileDialog.getExistingDirectory(self, f"Choose {kind} source folder", start)
        if folder:
            self.source_path.setText(folder)

    def _start_scan(self) -> None:
        source = self.source_path.text().strip()
        if not source or not Path(source).is_dir():
            self.status.setText("Choose an existing readable source folder.")
            self.status.setStyleSheet("color:#ef7d7d;")
            return
        if self._scan_cancel_token:
            self._scan_cancel_token.cancel()
        self._invalidate_preflight()
        self._scan_token += 1
        token = self._scan_token
        self._scan_cancel_token = ScanCancellationToken()
        self.status.setText(f"Scanning {source}…")
        self.status.setStyleSheet("color:#FF8357;")
        self.scan_button.setEnabled(False)
        self.unzip_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.source_path.setEnabled(False)
        self.scan_cancel_button.show()
        self.rescan_stale_button.hide()
        scan_mode = "auto" if self.auto_detect.isChecked() else self._mode()
        worker = ScanWorker(
            token, source, self._default_category, self._default_model_category,
            self._scan_cancel_token, scan_mode, self._ffmpeg_path, self._library_path,
        )
        worker.signals.progress.connect(self._scan_progressed)
        worker.signals.finished.connect(self._scan_finished)
        worker.signals.failed.connect(self._scan_failed)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _start_unzip(self) -> None:
        source = self.source_path.text().strip()
        if not source or not Path(source).is_dir():
            self.status.setText("Choose an existing readable source folder.")
            self.status.setStyleSheet("color:#ef7d7d;")
            return
        if self._scan_cancel_token or self._unzip_worker:
            return
        self._scan_cancel_token = ScanCancellationToken()
        self.scan_button.setEnabled(False)
        self.unzip_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.source_path.setEnabled(False)
        self.scan_cancel_button.setText("Cancel unzip")
        self.scan_cancel_button.setEnabled(True)
        self.scan_cancel_button.show()
        self.status.setText(f"Finding ZIP files under {source}…")
        self.status.setStyleSheet("color:#FF8357;")
        worker = UnzipWorker(source, self._scan_cancel_token)
        worker.signals.progress.connect(self._unzip_progressed)
        worker.signals.finished.connect(self._unzip_finished)
        worker.signals.failed.connect(self._unzip_failed)
        self._unzip_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _unzip_progressed(self, progress: ZipExtractionProgress) -> None:
        self.status.setText(
            f"Unzipping {progress.archive} · "
            f"{progress.completed_archives}/{progress.total_archives}"
        )

    def _unzip_finished(self, summary: ZipExtractionSummary) -> None:
        self._finish_unzip_ui()
        _cleanup_scan_workspaces(self._result)
        self._result = ScanResult()
        self._current = None
        self.material_list.clear()
        self._clear_review()
        self._invalidate_preflight()
        message = (
            f"Unzipped {len(summary.extracted)} · skipped {len(summary.skipped)} "
            f"· failed {len(summary.failed)}"
        )
        if summary.canceled:
            message += " · canceled"
        elif summary.extracted:
            message += ". Click Scan folder to inspect the extracted assets."
        self.status.setText(message)
        self.status.setStyleSheet(
            "color:#78c995;" if summary.extracted and not summary.failed
            else "color:#e6b566;"
        )
        details = [*summary.skipped.values(), *summary.failed.values()]
        self.scan_summary.setText(" · ".join(details[:3]) if details else message)
        self._update_import_state()

    def _unzip_failed(self, details: str) -> None:
        self._finish_unzip_ui()
        self.status.setText("ZIP extraction failed. No completed folder was overwritten.")
        self.status.setStyleSheet("color:#ef7d7d;")
        self.scan_summary.setText(
            details.splitlines()[-1] if details else "Unknown ZIP extraction error"
        )
        self._update_import_state()

    def _finish_unzip_ui(self) -> None:
        self._unzip_worker = None
        self._scan_cancel_token = None
        self.scan_button.setEnabled(True)
        self.unzip_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.source_path.setEnabled(True)
        self.scan_cancel_button.setText("Cancel scan")
        self.scan_cancel_button.hide()

    def _scan_progressed(self, token: int, progress: ScanProgress) -> None:
        if token != self._scan_token:
            return
        total = f"/{progress.total_files}" if progress.total_files else ""
        self.status.setText(
            f"{progress.phase} · {progress.examined_files}{total} files · "
            f"{progress.materials_found} assets · {progress.warning_count} warnings · {progress.elapsed_seconds:.1f}s"
        )

    def _cancel_scan(self) -> None:
        if self._scan_cancel_token:
            self._scan_cancel_token.cancel()
            self.scan_cancel_button.setEnabled(False)
            self.status.setText(
                "Canceling unzip…" if self._unzip_worker else "Canceling scan…"
            )

    def _scan_finished(self, token: int, result: ScanResult) -> None:
        self._workers = [worker for worker in self._workers if worker.token != token]
        if token != self._scan_token:
            _cleanup_scan_workspaces(result)
            return
        self._load_stock_taxonomy()
        self._finish_scan_ui()
        if result.canceled:
            self.status.setText("Scan canceled. Previous source files were not modified.")
            self.status.setStyleSheet("color:#e6b566;")
            return
        if self.auto_detect.isChecked() and result.detected_asset_type and result.detected_asset_type != "mixed":
            detected_index = self.import_mode.findData(result.detected_asset_type)
            if detected_index >= 0 and detected_index != self.import_mode.currentIndex():
                self.import_mode.blockSignals(True)
                self.import_mode.setCurrentIndex(detected_index)
                self.import_mode.blockSignals(False)
                self._change_mode(detected_index)
        elif self.auto_detect.isChecked() and result.detected_asset_type == "mixed":
            self.title.setText("Import Mixed Assets")
            self.subtitle.setText("Each folder and loose environment file was classified independently. Select any row to review or correct it.")
            self.source_path.setPlaceholderText("Choose a mixed asset library folder")
        _cleanup_scan_workspaces(self._result)
        self._result = result
        self._current = None
        self.material_list.clear()
        for material in result.materials:
            self.material_list.addItem(self._material_item(material))
        extra_count = sum(len(material.extra_paths) for material in result.materials)
        excluded_count = sum(len(getattr(material, "excluded_paths", ())) for material in result.materials)
        summary_parts = []
        if extra_count:
            summary_parts.append(f"{extra_count} companion files retained")
        if excluded_count:
            summary_parts.append(f"{excluded_count} source files excluded")
        self.ignored_summary.setText(" · ".join(summary_parts) if summary_parts else "No extra or excluded files")
        if result.materials:
            detected = ""
            if result.detection_reason:
                detected = (
                    f" Auto-detected mixed library: {result.detection_reason}."
                    if result.detected_asset_type == "mixed" else
                    f" Auto-detected {self.import_mode.tabText(self.import_mode.currentIndex())}: {result.detection_reason}."
                )
            self.status.setText(f"Found {len(result.materials)} assets. Select one to review.{detected}")
            self.status.setStyleSheet("color:#78c995;")
            self.material_list.setCurrentRow(0)
        else:
            self.status.setText(" ".join(result.warnings) or "No assets found.")
            self.status.setStyleSheet("color:#e6b566;")
            self._clear_review()
        self.scan_summary.setText(f"{len(result.materials)} assets · {extra_count} retained · {excluded_count} excluded")
        self._update_import_state()

    def _scan_failed(self, token: int, details: str) -> None:
        self._workers = [worker for worker in self._workers if worker.token != token]
        if token != self._scan_token:
            return
        self._finish_scan_ui()
        self.status.setText("The folder scan failed. No source files were changed.")
        self.status.setStyleSheet("color:#ef7d7d;")
        self.scan_summary.setText(details.splitlines()[-1] if details else "Unknown scan error")
        self._update_import_state()

    def _finish_scan_ui(self) -> None:
        self._scan_cancel_token = None
        self.scan_button.setEnabled(True)
        self.unzip_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.source_path.setEnabled(True)
        self.scan_cancel_button.setEnabled(True)
        self.scan_cancel_button.hide()

    def _select_material(self, row: int) -> None:
        self._save_edits()
        if row < 0:
            self._clear_review()
            return
        item = self.material_list.item(row)
        material = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(material, MaterialCandidate):
            return
        self._current = material
        self._loading = True
        self.detected_type.setCurrentIndex(max(0, self.detected_type.findData(material.asset_type)))
        is_loose_mixed_file = (
            self._result.detected_asset_type == "mixed"
            and material.source_root == Path(self.source_path.text()).expanduser().absolute()
        )
        is_archive_package = material.archive_source is not None
        self.reclassify_button.setEnabled(
            not is_loose_mixed_file and not is_archive_package
        )
        self.reclassify_button.setToolTip(
            "Loose HDR/EXR files are classified individually as HDRIs."
            if is_loose_mixed_file else
            "Archive material packages are already classified from their extracted PBR maps."
            if is_archive_package else
            "Rescan only this asset folder using the selected type."
        )
        self.asset_name.setText(material.name)
        choices = self._category_names(material.asset_type)
        self.category.clear()
        self.category.addItems(choices)
        self.category.setCurrentText(material.category)
        self.tags.set_suggestions(
            self._stock_taxonomy.tag_names if isinstance(material, StockCandidate) else ()
        )
        self.tags.set_tags(material.tags)
        self.author.setText(material.author)
        self.description.setPlainText(material.description)
        provider = material.provider + (f" · {material.provider_id}" if material.provider_id else "")
        if material.physical_size:
            provider += f" · size {material.physical_size}"
        self.provider_label.setText(provider)
        is_stock = isinstance(material, StockCandidate)
        for widget in (*self.stock_fields, *self.stock_field_labels):
            widget.setVisible(is_stock)
        if is_stock and material.media_info:
            self.stock_alpha.setCurrentIndex(max(0, self.stock_alpha.findData(material.media_info.alpha)))
            self.stock_preview.clear()
            self.stock_preview.addItem("No existing preview", "")
            for preview in material.preview_candidates:
                suffix = "compatible" if preview.compatible else "will be transcoded"
                self.stock_preview.addItem(f"{preview.relative_path} · {suffix}", preview.relative_path)
            self.stock_preview.setCurrentIndex(max(0, self.stock_preview.findData(material.selected_preview)))
            self.stock_preview_policy.setCurrentIndex(
                max(0, self.stock_preview_policy.findData(material.preview_policy))
            )
            info = material.media_info
            self.stock_media_summary.setText(
                f"{info.width}×{info.height} · {info.duration:.2f}s · {info.frame_rate:.3g} fps · "
                f"{info.codec.upper()} / {info.pixel_format} · "
                f"{'audio' if info.has_audio else 'silent'}"
            )
            self.stock_inference.setText(
                "\n".join(f"• {value}" for value in material.classification_evidence)
                or "No smart category or tag evidence."
            )
        self.extra_files.setPlainText(
            "\n".join(f"• {path}" for path in material.extra_paths)
            if material.extra_paths else "No additional companion files."
        )
        is_model = isinstance(material, ModelCandidate)
        is_hdri = isinstance(material, HdriCandidate)
        self.files_heading.setText(
            "Source video" if is_stock else
            "Model textures by resolution" if is_model else
            "Environment variants" if is_hdri else "Files by resolution"
        )
        self.channel_table.setHorizontalHeaderLabels(
            ["Type", "Preferred", "File", "Format", "Bits", "Color", "Details"]
            if is_hdri else
            ["Channel", "Preferred", "File", "Format", "Bits", "Color", "Normal / packing"]
        )
        self.model_status.setVisible(is_model)
        self.model_table.setVisible(is_model)
        self.excluded_heading.setVisible(is_model)
        self.excluded_files.setVisible(is_model)
        if is_model:
            self.model_status.setText("USD Ready" if material.usd_ready else "No USD — best available model will be imported")
            self.model_status.setStyleSheet("color:#78c995;" if material.usd_ready else "color:#e6b566;")
            self._populate_model_files(material)
            self.excluded_files.setPlainText(
                "\n".join(f"• {path}: {reason}" for path, reason in material.excluded_paths.items())
                if material.excluded_paths else "No excluded source files."
            )
        else:
            self.model_table.setRowCount(0)
            self.excluded_files.clear()
        self.resolution.clear()
        self.resolution.addItems(material.resolution_labels)
        self._populate_previews()
        self._loading = False
        self._set_review_enabled(True)
        self._show_diagnostics()
        if material.resolution_labels:
            self._show_resolution(material.resolution_labels[0])

    def _populate_model_files(self, material: ModelCandidate) -> None:
        files = sorted(material.model_files, key=lambda item: (not item.preferred, item.relative_path.casefold()))
        self.model_table.setRowCount(len(files))
        for row, model_file in enumerate(files):
            preferred = QRadioButton()
            preferred.setChecked(model_file.preferred)
            preferred.clicked.connect(lambda _checked, selected=model_file: self._set_preferred_model(selected))
            preferred.setStyleSheet("margin-left:18px;")
            self.model_table.setCellWidget(row, 0, preferred)
            values = (
                model_file.relative_path,
                model_file.file_format,
                model_file.role,
                model_file.component or "—",
                model_file.lod or "—",
                str(model_file.triangle_count) if model_file.triangle_count is not None else "—",
            )
            for column, value in enumerate(values, start=1):
                self.model_table.setItem(row, column, self._readonly_item(value))

    def _set_preferred_model(self, selected: ModelFile) -> None:
        if not isinstance(self._current, ModelCandidate):
            return
        for model_file in self._current.model_files:
            model_file.preferred = model_file is selected
        self._invalidate_preflight()
        self._populate_model_files(self._current)

    def _save_stock_options(self, *_args) -> None:
        if self._loading or not isinstance(self._current, StockCandidate) or not self._current.media_info:
            return
        alpha = str(self.stock_alpha.currentData() or "unknown")
        selected = str(self.stock_preview.currentData() or "")
        policy = str(self.stock_preview_policy.currentData() or "generate")
        if not selected:
            policy = "generate"
        self._current.media_info = replace(self._current.media_info, alpha=alpha)
        self._current.selected_preview = selected
        self._current.preview_policy = policy
        self._invalidate_preflight()
        item = self.material_list.currentItem()
        if item:
            self._refresh_item_text(item, self._current)

    def _material_item(self, material: MaterialCandidate) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 58))
        item.setToolTip(f"{material.source_root}\n{material.map_count} local primary files")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, material)
        self._set_item_state(item, "Warning" if material.diagnostics or material.warnings else "Ready")
        return item

    def _populate_previews(self) -> None:
        material = self._current
        if not material:
            return
        self.thumbnail_combo.clear()
        self.hero_combo.clear()
        for preview in material.previews:
            label = preview.relative_path + ("  [map fallback]" if preview.fallback else "")
            self.thumbnail_combo.addItem(label, preview.relative_path)
            self.hero_combo.addItem(label, preview.relative_path)
        self.thumbnail_combo.setCurrentIndex(max(0, self.thumbnail_combo.findData(material.selected_thumbnail)))
        self.hero_combo.setCurrentIndex(max(0, self.hero_combo.findData(material.selected_hero)))
        self._update_preview_image("thumbnail")
        self._update_preview_image("hero")

    def _preview_changed(self, role: str, _index: int) -> None:
        if self._loading or not self._current:
            return
        combo = self.thumbnail_combo if role == "thumbnail" else self.hero_combo
        value = str(combo.currentData() or "")
        if role == "thumbnail":
            self._current.selected_thumbnail = value
        else:
            self._current.selected_hero = value
        self._invalidate_preflight()
        self._update_preview_image(role)

    def _update_preview_image(self, role: str) -> None:
        material = self._current
        if not material:
            return
        combo = self.thumbnail_combo if role == "thumbnail" else self.hero_combo
        target = self.thumbnail_image if role == "thumbnail" else self.hero_image
        relative = str(combo.currentData() or "")
        pixmap = QPixmap(str(material.source_root / relative))
        if pixmap.isNull():
            target.setPixmap(QPixmap())
            target.setText("Preview unavailable")
            return
        target.setText("")
        target.setPixmap(pixmap.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _show_resolution(self, label: str) -> None:
        if self._loading or not self._current or label not in self._current.resolutions:
            return
        variant = self._current.resolutions[label]
        rows = [(channel, texture_map) for channel, maps in variant.maps.items() for texture_map in maps]
        rows.sort(key=lambda item: (item[0].casefold(), not item[1].preferred, item[1].relative_path.casefold()))
        self.channel_table.setRowCount(len(rows))
        for row, (channel, texture_map) in enumerate(rows):
            self.channel_table.setItem(row, 0, self._readonly_item(channel))
            preferred = QRadioButton()
            preferred.setChecked(texture_map.preferred)
            preferred.clicked.connect(lambda _checked, ch=channel, item=texture_map: self._set_preferred(ch, item))
            preferred.setStyleSheet("margin-left:18px;")
            self.channel_table.setCellWidget(row, 1, preferred)
            self.channel_table.setItem(row, 2, self._readonly_item(texture_map.relative_path))
            self.channel_table.setItem(row, 3, self._readonly_item(texture_map.file_format))
            self.channel_table.setItem(row, 4, self._readonly_item(str(getattr(texture_map, "bit_depth", None) or "—")))
            self.channel_table.setItem(row, 5, self._readonly_item(getattr(texture_map, "color_space", "") or "—"))
            extra = getattr(texture_map, "normal_convention", "")
            if getattr(texture_map, "packed_channels", {}):
                extra = ", ".join(f"{component}={mapped}" for component, mapped in texture_map.packed_channels.items())
            self.channel_table.setItem(row, 6, self._readonly_item(extra or "—"))

    @staticmethod
    def _readonly_item(value: str) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _show_diagnostics(self, *_args) -> None:
        if not self._current:
            self.warnings.clear()
            return
        diagnostics = list(self._current.diagnostics)
        if self._preflight_result:
            item = self._preflight_result.for_material(self._current)
            if item:
                diagnostics.extend(item.diagnostics)
        selected = str(self.diagnostic_filter.currentData() or "all")
        if selected != "all":
            diagnostics = [item for item in diagnostics if item.severity == selected]
        if diagnostics:
            icons = {"error": "✕", "warning": "⚠", "info": "•"}
            self.warnings.setPlainText("\n".join(
                f"{icons[item.severity]} [{item.code}] {item.message}" + (f" — {item.path}" if item.path else "")
                for item in diagnostics
            ))
        elif self._current.warnings and selected == "all":
            self.warnings.setPlainText("\n".join(f"• {warning}" for warning in self._current.warnings))
        else:
            self.warnings.setPlainText("No matching review notes.")

    def _set_item_state(self, item: QListWidgetItem, state: str) -> None:
        item.setData(Qt.ItemDataRole.UserRole + 1, state)
        material = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(material, MaterialCandidate):
            self._refresh_item_text(item, material)

    def _refresh_item_text(self, item: QListWidgetItem, material: MaterialCandidate) -> None:
        state = str(item.data(Qt.ItemDataRole.UserRole + 1) or "Ready")
        icons = {
            "Ready": "●",
            "Warning": "⚠",
            "Invalid": "✕",
            "Stale": "↻",
            "Duplicate": "↷",
            "Conflict": "!",
            "Imported": "✓",
            "Failed": "✕",
        }
        if isinstance(material, ModelCandidate):
            preferred = material.preferred_model.file_format if material.preferred_model else "No model"
            detail = f"{preferred} · {'USD Ready' if material.usd_ready else 'No USD'} · {material.model_file_count} models"
        elif isinstance(material, StockCandidate) and material.media_info:
            info = material.media_info
            preview = "existing preview" if material.preview_policy == "use_existing" else "generate 480p"
            detail = (
                f"{info.duration:.1f}s · {info.width}×{info.height} · {info.codec.upper()} · "
                f"{'Alpha' if info.alpha == 'yes' else 'Opaque' if info.alpha == 'no' else 'Alpha unknown'} · {preview}"
            )
        else:
            detail = f"{', '.join(material.resolution_labels)} · {material.map_count} files"
            if material.archive_source:
                detail = f"{material.archive_source.file_format} package · {detail}"
        type_label = {
            "model": "MODEL", "hdri": "HDRI", "atlas": "ATLAS",
            "texture_set": "TEXTURE", "stock": "STOCK",
        }.get(material.asset_type, "ASSET")
        item.setText(
            f"{icons.get(state, '•')} {material.name}\n"
            f"{state} · {type_label} · {material.provider} · {detail}"
        )

    def _invalidate_preflight(self) -> None:
        if self._preflighting or self._importing:
            return
        self._preflight_result = None
        self._conflict_decisions.clear()
        self.rescan_stale_button.hide()
        self._update_import_state()

    def _set_preferred(self, channel: str, selected: TextureMap) -> None:
        if not self._current:
            return
        variant = self._current.resolutions.get(self.resolution.currentText())
        if not variant:
            return
        for texture_map in variant.maps.get(channel, []):
            texture_map.preferred = texture_map is selected
        self._invalidate_preflight()
        self._show_resolution(variant.label)

    def _save_edits(self, *_args) -> None:
        if self._loading or not self._current:
            return
        fallback = (
            self._default_model_category if isinstance(self._current, ModelCandidate)
            else "Uncategorized" if isinstance(self._current, StockCandidate)
            else self._default_category
        )
        primary = self.category.currentText().strip() or fallback
        values = (
            self.asset_name.text().strip() or self._current.name,
            primary,
            sorted(set(self.tags.tags()), key=str.casefold),
            self.author.text().strip(),
            self.description.toPlainText().strip(),
        )
        changed = values != (
            self._current.name,
            self._current.category,
            self._current.tags,
            self._current.author,
            self._current.description,
        )
        self._current.name, self._current.category, self._current.tags, self._current.author, self._current.description = values
        if changed:
            self._invalidate_preflight()
        current_item = self.material_list.currentItem()
        if current_item:
            self._refresh_item_text(current_item, self._current)

    def _checked_materials(self) -> list[MaterialCandidate]:
        materials: list[MaterialCandidate] = []
        for row in range(self.material_list.count()):
            item = self.material_list.item(row)
            material = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and isinstance(material, MaterialCandidate):
                materials.append(material)
        return materials

    def _reclassify_selected(self) -> None:
        if not self._current or self._reclassifying or not self.reclassify_button.isEnabled():
            return
        self._save_edits()
        mode = str(self.detected_type.currentData() or self._current.asset_type)
        self._scan_token += 1
        token = self._scan_token
        self._scan_cancel_token = ScanCancellationToken()
        self._reclassifying = True
        self._set_busy(True)
        self.status.setText(f"Reclassifying {self._current.name} as {self.detected_type.currentText()}…")
        worker = ScanWorker(
            token, str(self._current.source_root), self._default_category, self._default_model_category,
            self._scan_cancel_token, mode, self._ffmpeg_path, self._library_path,
        )
        worker.signals.progress.connect(self._scan_progressed)
        worker.signals.finished.connect(self._reclassify_finished)
        worker.signals.failed.connect(self._reclassify_failed)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _reclassify_finished(self, token: int, result: ScanResult) -> None:
        self._workers = [worker for worker in self._workers if worker.token != token]
        if token != self._scan_token:
            return
        old = self._current
        self._scan_cancel_token = None
        self._reclassifying = False
        self._set_busy(False)
        if result.canceled or not result.materials or old is None:
            self.status.setText("That type found no valid assets in the selected folder. The original detection was kept.")
            self.status.setStyleSheet("color:#e6b566;")
            self._update_import_state()
            return
        row = self.material_list.currentRow()
        old_item = self.material_list.takeItem(row)
        was_checked = old_item.checkState() == Qt.CheckState.Checked if old_item else True
        try:
            result_index = self._result.materials.index(old)
        except ValueError:
            result_index = len(self._result.materials)
        if result_index < len(self._result.materials):
            self._result.materials[result_index:result_index + 1] = result.materials
        else:
            self._result.materials.extend(result.materials)
        for offset, material in enumerate(result.materials):
            item = self._material_item(material)
            item.setCheckState(Qt.CheckState.Checked if was_checked else Qt.CheckState.Unchecked)
            self.material_list.insertItem(row + offset, item)
        self.material_list.setCurrentRow(row)
        self._invalidate_preflight()
        self.status.setText(
            f"Reclassified {old.name} as {self.detected_type.currentText()}"
            + (f" and found {len(result.materials)} assets." if len(result.materials) != 1 else ".")
        )
        self.status.setStyleSheet("color:#78c995;")
        self._update_import_state()

    def _reclassify_failed(self, token: int, details: str) -> None:
        self._workers = [worker for worker in self._workers if worker.token != token]
        if token != self._scan_token:
            return
        self._scan_cancel_token = None
        self._reclassifying = False
        self._set_busy(False)
        self.status.setText("Reclassification failed. The original detection was kept.")
        self.status.setStyleSheet("color:#ef7d7d;")
        self.scan_summary.setText(details.splitlines()[-1] if details else "Unknown reclassification error")
        self._update_import_state()

    def _update_import_state(self, *_args) -> None:
        checked = self._checked_materials()
        self.preflight_button.setText(f"Preflight checked assets ({len(checked)})")
        self.import_button.setText(f"Import checked assets ({len(checked)})")
        valid_library = bool(self._library_path and Path(self._library_path).is_dir() and os.access(self._library_path, os.W_OK))
        overlap = valid_library and any(_paths_overlap(Path(self._library_path), material.source_root) for material in checked)
        can_preflight = bool(checked and valid_library and not overlap and not self._importing and not self._preflighting)
        self.preflight_button.setEnabled(can_preflight)
        preflight_ready = bool(self._preflight_result and not self._preflight_result.canceled)
        if preflight_ready:
            for material in checked:
                item = self._preflight_result.for_material(material)
                if not item or item.status in {"Invalid", "Stale"} or item.has_errors:
                    preflight_ready = False
                    break
                if item.status == "Conflict" and material.material_key not in self._conflict_decisions:
                    preflight_ready = False
                    break
        self.import_button.setEnabled(bool(checked and preflight_ready and not self._importing and not self._preflighting))
        if not self._library_path:
            tooltip = "Configure a library path in Settings first."
        elif not valid_library:
            tooltip = "The configured library folder must be writable."
        elif overlap:
            tooltip = "The source and library folders cannot contain one another."
        elif not preflight_ready:
            tooltip = "Run preflight and resolve blocking material states before importing."
        else:
            tooltip = ""
        self.import_button.setToolTip(tooltip)
        self.preflight_button.setToolTip(tooltip if not can_preflight else "Hash and validate the checked materials before import.")

    def _start_preflight(self) -> None:
        self._save_edits()
        materials = self._checked_materials()
        if not materials or not self.preflight_button.isEnabled():
            return
        self._preflight_result = None
        self._conflict_decisions.clear()
        self._preflighting = True
        self._cancel_token = CancelToken()
        self._set_busy(True)
        self.import_progress.setValue(0)
        self.import_progress.show()
        self.cancel_button.setText("Cancel preflight")
        self.cancel_button.show()
        self.status.setText(f"Preflighting {len(materials)} checked assets…")
        self.status.setStyleSheet("color:#FF8357;")
        worker = PreflightWorker(
            self._library_path, materials, self._cancel_token, self._hash_cache, self._ffmpeg_path
        )
        worker.signals.progress.connect(self._preflight_progressed)
        worker.signals.finished.connect(self._preflight_finished)
        worker.signals.failed.connect(self._preflight_failed)
        self._preflight_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _preflight_progressed(self, progress: ImportProgress) -> None:
        fraction = progress.completed_bytes / progress.total_bytes if progress.total_bytes else 0
        self.import_progress.setValue(min(1000, int(fraction * 1000)))
        self.status.setText(f"Checking {progress.material} · {progress.file}")

    def _preflight_finished(self, result: PreflightResult) -> None:
        self._preflight_result = result
        self._preflighting = False
        self._preflight_worker = None
        self._cancel_token = None
        self._set_busy(False)
        self.import_progress.hide()
        self.cancel_button.hide()
        for row in range(self.material_list.count()):
            list_item = self.material_list.item(row)
            material = list_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(material, MaterialCandidate):
                continue
            preflight = result.for_material(material)
            if preflight:
                self._set_item_state(list_item, preflight.status)
                if preflight.diagnostics:
                    list_item.setToolTip(list_item.toolTip() + "\n" + "\n".join(item.message for item in preflight.diagnostics))
        if result.canceled:
            self.status.setText("Preflight canceled. No files were copied.")
            self.status.setStyleSheet("color:#e6b566;")
        else:
            self._resolve_conflicts(result)
            counts: dict[str, int] = {}
            for item in result.materials:
                counts[item.status] = counts.get(item.status, 0) + 1
            summary = " · ".join(f"{state} {count}" for state, count in sorted(counts.items()))
            self.scan_summary.setText(summary or "No assets checked")
            self.status.setText(f"Preflight complete · {summary}")
            self.status.setStyleSheet("color:#78c995;" if not any(item.has_errors for item in result.materials) else "color:#e6b566;")
        self.rescan_stale_button.setVisible(any(item.status == "Stale" for item in result.materials))
        self._show_diagnostics()
        self._update_import_state()

    def _resolve_conflicts(self, result: PreflightResult) -> None:
        for item in result.materials:
            if not item.conflict:
                continue
            box = QMessageBox(self)
            box.setWindowTitle("Provider content conflict")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                f"{item.material.name} uses the same {item.conflict.provider} ID as "
                f"{item.conflict.existing_asset_name}, but its primary content differs."
            )
            box.setInformativeText("Skip preserves the existing asset. Import Separate creates a new independent asset.")
            skip = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
            separate = box.addButton("Import Separate", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(skip)
            box.exec()
            decision = "separate" if box.clickedButton() is separate else "skip"
            self._conflict_decisions[item.conflict.material_key] = decision
            if decision == "separate":
                item.status = "Ready"
                list_item = self._item_for_material(item.material)
                if list_item:
                    self._set_item_state(list_item, "Ready")

    def _preflight_failed(self, details: str) -> None:
        self._preflighting = False
        self._preflight_worker = None
        self._cancel_token = None
        self._set_busy(False)
        self.import_progress.hide()
        self.cancel_button.hide()
        self.status.setText("Preflight failed. No files were copied.")
        self.status.setStyleSheet("color:#ef7d7d;")
        self.scan_summary.setText(details.splitlines()[-1] if details else "Unknown preflight error")
        self._update_import_state()

    def _item_for_material(self, material: MaterialCandidate) -> QListWidgetItem | None:
        for row in range(self.material_list.count()):
            item = self.material_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) is material:
                return item
        return None

    def _start_import(self) -> None:
        self._save_edits()
        materials = self._checked_materials()
        if not materials or not self.import_button.isEnabled() or not self._preflight_result:
            return
        self._importing = True
        self._cancel_token = CancelToken()
        self.import_progress.setValue(0)
        self.import_progress.show()
        self.cancel_button.setText("Cancel import")
        self.cancel_button.show()
        self._set_busy(True)
        self.status.setText(f"Importing {len(materials)} checked assets…")
        self.status.setStyleSheet("color:#FF8357;")
        worker = ImportWorker(
            self._library_path,
            materials,
            self._cancel_token,
            self._preflight_result,
            self._conflict_decisions,
            self._blender_path,
            self._render_hdri_previews,
            self._ffmpeg_path,
        )
        worker.signals.progress.connect(self._import_progressed)
        worker.signals.finished.connect(self._import_finished)
        worker.signals.failed.connect(self._import_failed)
        self._import_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _import_progressed(self, progress: ImportProgress) -> None:
        fraction = progress.completed_bytes / progress.total_bytes if progress.total_bytes else 0
        self.import_progress.setValue(min(1000, int(fraction * 1000)))
        self.status.setText(f"Importing {progress.material} · {progress.file}")

    def _cancel_import(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Canceling safely after the current file chunk…")

    def _import_finished(self, summary: ImportSummary) -> None:
        imported_names = {asset.name for asset in summary.imported}
        skipped_names = {value.split(":", 1)[0] for value in summary.skipped}
        for row in range(self.material_list.count()):
            item = self.material_list.item(row)
            material = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(material, MaterialCandidate):
                continue
            if material.name in imported_names:
                item.setCheckState(Qt.CheckState.Unchecked)
                self._set_item_state(item, "Imported")
                item.setToolTip(item.toolTip() + "\nImported successfully")
            elif material.name in skipped_names:
                item.setCheckState(Qt.CheckState.Unchecked)
                self._set_item_state(item, "Duplicate")
                item.setToolTip(item.toolTip() + "\nSkipped as duplicate")
            elif material.name in summary.failed:
                state = "Stale" if "changed" in summary.failed[material.name].casefold() else "Failed"
                self._set_item_state(item, state)
                item.setToolTip(item.toolTip() + f"\nImport failed: {summary.failed[material.name]}")
        message = f"Imported {len(summary.imported)} · skipped {len(summary.skipped)} · failed {len(summary.failed)}"
        if summary.canceled:
            message += " · canceled"
        self.scan_summary.setText(message)
        self.status.setText(message)
        self.status.setStyleSheet("color:#78c995;" if summary.imported else "color:#e6b566;")
        self._finish_import_ui()
        if summary.imported:
            self.import_completed.emit(summary)

    def _import_failed(self, details: str) -> None:
        self.status.setText("Import could not start. No partial asset was added.")
        self.status.setStyleSheet("color:#ef7d7d;")
        self.scan_summary.setText(details.splitlines()[-1] if details else "Unknown import error")
        self._finish_import_ui()

    def _finish_import_ui(self) -> None:
        self._importing = False
        self._import_worker = None
        self._cancel_token = None
        self.import_progress.hide()
        self.cancel_button.setEnabled(True)
        self.cancel_button.hide()
        self._set_busy(False)
        self._update_import_state()

    def _set_busy(self, active: bool) -> None:
        self.import_mode.setEnabled(not active)
        self.scan_button.setEnabled(not active)
        self.browse_button.setEnabled(not active)
        self.source_path.setEnabled(not active)
        self.material_list.setEnabled(not active)
        self.preflight_button.setEnabled(not active)
        self.import_button.setEnabled(not active)
        self._set_review_enabled(not active and self._current is not None)

    def _clear_review(self) -> None:
        self._current = None
        self._loading = True
        for editor in (self.asset_name, self.tags, self.author):
            editor.clear()
        self.category.setCurrentText(self._default_category)
        self.description.clear()
        self.provider_label.setText("No asset selected")
        self.thumbnail_combo.clear()
        self.hero_combo.clear()
        self.thumbnail_image.setPixmap(QPixmap())
        self.thumbnail_image.setText("No preview")
        self.hero_image.setPixmap(QPixmap())
        self.hero_image.setText("No preview")
        self.resolution.clear()
        self.channel_table.setRowCount(0)
        self.model_table.setRowCount(0)
        self.model_table.hide()
        self.model_status.hide()
        self.excluded_heading.hide()
        self.excluded_files.clear()
        self.excluded_files.hide()
        self.extra_files.clear()
        self.warnings.clear()
        self.stock_preview.clear()
        self.stock_media_summary.clear()
        for widget in (*self.stock_fields, *self.stock_field_labels):
            widget.hide()
        self._loading = False
        self._set_review_enabled(False)

    def _set_review_enabled(self, enabled: bool) -> None:
        for widget in (
            self.asset_name,
            self.category,
            self.tags,
            self.author,
            self.description,
            self.thumbnail_combo,
            self.hero_combo,
            self.resolution,
            self.channel_table,
            self.model_table,
            self.detected_type,
            self.reclassify_button,
            self.stock_alpha,
            self.stock_preview,
            self.stock_preview_policy,
        ):
            widget.setEnabled(enabled)

    def set_default_category(self, category: str) -> None:
        choices = self._category_names("texture_set")
        self._default_category = category if category in choices else "Uncategorized"
        if not self._current:
            self.category.setCurrentText(self._default_category)

    def set_default_model_category(self, category: str) -> None:
        self._default_model_category = category if category in MODEL_CATEGORIES else "Uncategorized"
        if not self._current and self._mode() == "model":
            self.category.setCurrentText(self._default_model_category)

    def set_hdri_preview_settings(self, blender_path: str, render_on_import: bool) -> None:
        self._blender_path = blender_path
        self._render_hdri_previews = bool(render_on_import)

    def set_stock_preview_settings(self, ffmpeg_path: str) -> None:
        self._ffmpeg_path = ffmpeg_path

    def set_library_path(self, path: str) -> None:
        if path != self._library_path:
            self._preflight_result = None
            self._conflict_decisions.clear()
        self._library_path = path
        self._load_category_catalogs()
        self._load_stock_taxonomy()
        self._update_import_state()

    def _load_category_catalogs(self) -> None:
        if self._library_path and Path(self._library_path).is_dir():
            store = CategoryConfigStore(self._library_path)
            store.ensure_defaults()
            self._category_catalogs = store.load_all()
        else:
            self._category_catalogs = {
                asset_type: default_category_catalog(asset_type)
                for asset_type in ("texture_set", "atlas", "hdri", "model", "stock")
            }

    def _category_names(self, asset_type: str) -> tuple[str, ...]:
        catalog = self._category_catalogs.get(asset_type)
        return catalog.names if catalog else default_category_catalog(asset_type).names

    def _load_stock_taxonomy(self) -> None:
        if self._library_path and Path(self._library_path).is_dir():
            self._stock_taxonomy = StockTaxonomyStore(self._library_path).load()
        else:
            self._stock_taxonomy = default_stock_taxonomy()
        if self._mode() == "stock":
            self.tags.set_suggestions(self._stock_taxonomy.tag_names)

    def _mode(self) -> str:
        return str(self.import_mode.currentData() or "texture_set")

    def _change_mode(self, _index: int) -> None:
        if self._scan_cancel_token:
            self._scan_cancel_token.cancel()
        mode = self._mode()
        is_hdri = mode == "hdri"
        is_model = mode == "model"
        is_atlas = mode == "atlas"
        is_stock = mode == "stock"
        self.title.setText(
            "Import 3D Models" if is_model else
            "Import HDRIs" if is_hdri else
            "Import Atlases" if is_atlas else
            "Import Stock Footage" if is_stock else
            "Import PBR Textures"
        )
        self.subtitle.setText(
            "Scan model folders, preserve their local source tree, and prefer USD whenever it is available."
            if is_model else
            "Scan local HDR/EXR variants, provider metadata, previews, and companion files."
            if is_hdri else
            "Scan Megascans atlases and other PBR cutouts, preserving opacity, translucency, previews, and metadata."
            if is_atlas else
            "Scan MOV and MP4 clips, match provider previews, detect alpha, and generate missing 480p H.264 previews."
            if is_stock else
            "Scan provider metadata and local texture maps, then review the normalized material sets."
        )
        self.source_path.setPlaceholderText(
            "Choose one model folder or a parent containing multiple models"
            if is_model else
            "Choose one HDRI folder or a parent containing multiple HDRIs"
            if is_hdri else
            "Choose one atlas folder or a parent containing multiple atlases"
            if is_atlas else
            "Choose a Stock clip folder or a parent footage library"
            if is_stock else "Choose one material folder or a parent library folder"
        )
        self.channel_table.setHorizontalHeaderLabels(
            ["Type", "Preferred", "File", "Format", "Bits", "Color", "Details"]
            if is_hdri else
            ["Channel", "Preferred", "File", "Format", "Bits", "Color", "Normal / packing"]
        )
        categories = self._category_names(mode)
        current_default = self._default_model_category if is_model else self._default_category
        self.category.clear()
        self.category.addItems(categories)
        self.tags.set_suggestions(self._stock_taxonomy.tag_names if is_stock else ())
        self.category.setCurrentText(current_default)
        self.material_list.clear()
        _cleanup_scan_workspaces(self._result)
        self._result = ScanResult()
        self._invalidate_preflight()
        self._clear_review()
        self.status.setText("Choose a folder to begin.")
        self.scan_summary.setText("No scan results.")
        self.ignored_summary.clear()

    def closeEvent(self, event) -> None:
        _cleanup_scan_workspaces(self._result)
        super().closeEvent(event)


def _cleanup_scan_workspaces(result: ScanResult) -> None:
    for value in result.temporary_roots:
        path = Path(value)
        if path.name.startswith("shotbox-archive-import-"):
            shutil.rmtree(path, ignore_errors=True)
    result.temporary_roots.clear()


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first_resolved = first.resolve(strict=True)
        second_resolved = second.resolve(strict=True)
    except OSError:
        return False
    try:
        first_resolved.relative_to(second_resolved)
        return True
    except ValueError:
        pass
    try:
        second_resolved.relative_to(first_resolved)
        return True
    except ValueError:
        return False
