from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QProcess, QSettings, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .metadata import (
    AssetRecord,
    MetadataError,
    apply_classification,
    choose_preview,
    discover_assets,
    load_allowed_tags,
    load_category_names,
)
from .ollama_client import Classification, OllamaClient, OllamaError
from universal_asset_library.ai import bundled_tag_path


DEFAULT_MODEL = "ministral-3:8b"


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    row: int
    record: AssetRecord


class AnalysisThread(QThread):
    item_done = pyqtSignal(int, object, str)
    progress_changed = pyqtSignal(int, int)

    def __init__(
        self,
        requests: list[AnalysisRequest],
        model: str,
        categories: tuple[str, ...],
        allowed_tags: tuple[str, ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.model = model
        self.categories = categories
        self.allowed_tags = allowed_tags
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        client = OllamaClient()
        total = len(self.requests)
        for index, request in enumerate(self.requests, 1):
            if self.cancel_event.is_set():
                break
            record = request.record
            try:
                if not record.preview_path:
                    raise OllamaError("Choose a rendered preview first.")
                result = client.classify(
                    record.preview_path,
                    model=self.model,
                    categories=self.categories,
                    allowed_tags=self.allowed_tags,
                    asset_name=record.name,
                    asset_type=record.asset_type,
                    current_category=record.category,
                    current_tags=record.tags,
                    cancel_event=self.cancel_event,
                )
                self.item_done.emit(request.row, result, "")
            except Exception as error:
                self.item_done.emit(request.row, None, str(error))
            self.progress_changed.emit(index, total)


class PullThread(QThread):
    progress_changed = pyqtSignal(object)
    failed = pyqtSignal(str)
    succeeded = pyqtSignal()

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            OllamaClient(timeout=3600).pull(
                self.model, self.progress_changed.emit, self.cancel_event
            )
            self.succeeded.emit()
        except Exception as error:
            self.failed.emit(str(error))


class HdriTaggerWindow(QMainWindow):
    COLUMNS = (
        "Apply", "Asset", "Rendered preview", "Current", "Proposed category",
        "Proposed tags", "Confidence", "Rationale", "Status",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Asset AI Tagger Prototype")
        self.resize(1500, 850)
        self.records: list[AssetRecord] = []
        self.categories: tuple[str, ...] = ()
        self.allowed_tags: tuple[str, ...] = ()
        self.analysis_thread: AnalysisThread | None = None
        self.pull_thread: PullThread | None = None
        self.ollama_process: QProcess | None = None
        self._close_requested = False
        self._build_ui()
        self._load_defaults()
        QTimer.singleShot(0, self.refresh_ollama)

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        paths = QFormLayout()
        self.asset_type_combo = QComboBox()
        self.asset_type_combo.addItem("HDRIs", "hdri")
        self.asset_type_combo.addItem("Textures", "texture_set")
        self.asset_type_combo.addItem("Atlases / Decals", "atlas")
        self.asset_type_combo.addItem("3D Models", "model")
        self.asset_type_combo.addItem("Stock Footage", "stock")
        self.root_edit, root_row = self._path_row("Choose preview root", self._choose_root)
        self.categories_edit, categories_row = self._path_row(
            "Choose the matching category JSON", self._choose_categories
        )
        self.tags_edit, tags_row = self._path_row(
            "Choose allowed_tags.json", self._choose_tags
        )
        paths.addRow("Asset type", self.asset_type_combo)
        paths.addRow("Asset / preview root", root_row)
        paths.addRow("Category JSON", categories_row)
        paths.addRow("Allowed tags JSON", tags_row)
        outer.addLayout(paths)
        self.asset_type_combo.currentIndexChanged.connect(self._profile_changed)

        ollama_row = QHBoxLayout()
        self.model_edit = QLineEdit(DEFAULT_MODEL)
        self.model_edit.setMinimumWidth(220)
        self.ollama_status = QLabel("Checking Ollama…")
        self.start_button = QPushButton("Start Server")
        self.start_button.clicked.connect(self.start_ollama)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_ollama)
        self.download_button = QPushButton("Download Model")
        self.download_button.clicked.connect(self.download_model)
        ollama_row.addWidget(QLabel("Ollama model"))
        ollama_row.addWidget(self.model_edit)
        ollama_row.addWidget(self.ollama_status, 1)
        ollama_row.addWidget(self.start_button)
        ollama_row.addWidget(self.refresh_button)
        ollama_row.addWidget(self.download_button)
        outer.addLayout(ollama_row)

        actions = QHBoxLayout()
        self.scan_button = QPushButton("Scan Preview Folder")
        self.scan_button.clicked.connect(self.scan)
        self.analyze_selected_button = QPushButton("Analyze Selected")
        self.analyze_selected_button.clicked.connect(lambda: self.analyze(False))
        self.analyze_all_button = QPushButton("Analyze All")
        self.analyze_all_button.clicked.connect(lambda: self.analyze(True))
        self.apply_button = QPushButton("Apply Approved")
        self.apply_button.clicked.connect(self.apply_approved)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_work)
        for button in (
            self.scan_button, self.analyze_selected_button, self.analyze_all_button,
            self.apply_button, self.cancel_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        outer.addLayout(actions)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_selected_preview)
        splitter.addWidget(self.table)

        preview_panel = QWidget()
        preview_layout = QHBoxLayout(preview_panel)
        self.preview_label = QLabel("Select an asset to inspect its rendered preview.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(210)
        self.preview_label.setStyleSheet("QLabel { background: #1b1d20; color: #aeb4bb; }")
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumWidth(470)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addWidget(self.details)
        splitter.addWidget(preview_panel)
        splitter.setSizes([560, 260])
        outer.addWidget(splitter, 1)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Choose a preview root and scan.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setMaximumWidth(340)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.progress)
        outer.addLayout(status_row)

        self.setCentralWidget(central)

    def _path_row(self, placeholder: str, callback):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        button = QPushButton("Browse…")
        button.clicked.connect(callback)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return edit, row

    def _load_defaults(self) -> None:
        settings = QSettings("ShotBox", "ShotBox Assets")
        self.library_path = Path(str(settings.value("library/path", "") or "")).expanduser()
        if self.library_path.is_dir():
            self.root_edit.setText(str(self.library_path))
        self._profile_changed()

    def _profile_changed(self) -> None:
        asset_type = str(self.asset_type_combo.currentData() or "hdri")
        category_names = {
            "texture_set": "texture_categories.json",
            "atlas": "atlas_categories.json",
            "hdri": "hdri_categories.json",
            "model": "model_categories.json",
            "stock": "stock_categories.json",
        }
        category_path = self.library_path / ".ual" / category_names[asset_type]
        self.categories_edit.setText(str(category_path) if category_path.is_file() else "")
        stock_tags = self.library_path / ".ual" / "stock_tags.json"
        if asset_type == "stock" and stock_tags.is_file():
            self.tags_edit.setText(str(stock_tags))
        else:
            self.tags_edit.setText(str(bundled_tag_path(asset_type)))

    def _choose_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose rendered preview root", self.root_edit.text())
        if path:
            self.root_edit.setText(path)

    def _choose_categories(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose asset category JSON", self.categories_edit.text(), "JSON (*.json)"
        )
        if path:
            self.categories_edit.setText(path)

    def _choose_tags(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose allowed-tags JSON", self.tags_edit.text(), "JSON (*.json)"
        )
        if path:
            self.tags_edit.setText(path)

    def scan(self) -> None:
        try:
            self.categories = load_category_names(self.categories_edit.text())
            self.allowed_tags = load_allowed_tags(self.tags_edit.text())
            asset_type = str(self.asset_type_combo.currentData())
            self.records = discover_assets(self.root_edit.text(), asset_type)
        except MetadataError as error:
            QMessageBox.critical(self, "Scan failed", str(error))
            return
        self._populate_table()
        ready = sum(bool(record.preview_path and not record.diagnostic) for record in self.records)
        self.status_label.setText(
            f"Found {len(self.records)} matching assets; {ready} ready for analysis."
        )

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for row, record in enumerate(self.records):
            self.table.insertRow(row)
            approval = QCheckBox()
            approval.setEnabled(False)
            approval_container = QWidget()
            approval_layout = QHBoxLayout(approval_container)
            approval_layout.setContentsMargins(0, 0, 0, 0)
            approval_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            approval_layout.addWidget(approval)
            self.table.setCellWidget(row, 0, approval_container)

            name_item = QTableWidgetItem(record.name)
            name_item.setToolTip(str(record.folder))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, name_item)

            preview_combo = QComboBox()
            if len(record.preview_candidates) > 1 and not record.preview_path:
                preview_combo.addItem("Choose preview…", "")
            for candidate in record.preview_candidates:
                preview_combo.addItem(candidate.name, str(candidate))
            if record.preview_path:
                preview_combo.setCurrentIndex(
                    max(0, preview_combo.findData(str(record.preview_path)))
                )
            preview_combo.currentIndexChanged.connect(
                lambda _index, current_row=row: self._preview_changed(current_row)
            )
            self.table.setCellWidget(row, 2, preview_combo)

            current = f"{record.category or '(none)'} | {', '.join(record.tags) or '(no tags)'}"
            current_item = QTableWidgetItem(current)
            current_item.setFlags(current_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, current_item)

            category_combo = QComboBox()
            category_combo.addItems(self.categories)
            if record.category in self.categories:
                category_combo.setCurrentText(record.category)
            category_combo.setEnabled(False)
            self.table.setCellWidget(row, 4, category_combo)

            tags_edit = QLineEdit()
            tags_edit.setPlaceholderText("Five comma-separated allowed tags")
            tags_edit.setEnabled(False)
            self.table.setCellWidget(row, 5, tags_edit)
            for column in (6, 7):
                item = QTableWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            status = record.diagnostic or ("Ready" if record.preview_path else "Choose a preview")
            status_item = QTableWidgetItem(status)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 8, status_item)

    def _preview_changed(self, row: int) -> None:
        combo = self.table.cellWidget(row, 2)
        if not isinstance(combo, QComboBox):
            return
        value = combo.currentData()
        if not value:
            return
        try:
            self.records[row] = choose_preview(self.records[row], value)
            self.table.item(row, 8).setText(
                self.records[row].diagnostic or "Ready"
            )
            self._show_selected_preview()
        except MetadataError as error:
            self.table.item(row, 8).setText(str(error))

    def _show_selected_preview(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.records):
            return
        record = self.records[row]
        if record.preview_path:
            pixmap = QPixmap(str(record.preview_path))
            if not pixmap.isNull():
                self.preview_label.setPixmap(
                    pixmap.scaled(
                        self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                self.preview_label.setText("The selected preview could not be decoded.")
        else:
            self.preview_label.setText("Choose a rendered preview for this asset.")
        self.details.setPlainText(
            f"Asset: {record.name}\n"
            f"Folder: {record.folder}\n"
            f"Metadata: {record.metadata_path}\n"
            f"Asset type: {record.asset_type}\n"
            f"Schema: {record.metadata_kind}\n"
            f"Current category: {record.category or '(none)'}\n"
            f"Current tags: {', '.join(record.tags) or '(none)'}\n"
            f"Diagnostic: {record.diagnostic or '(none)'}"
        )

    def analyze(self, all_rows: bool) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            return
        if not self.records:
            QMessageBox.information(self, "Nothing to analyze", "Scan a preview folder first.")
            return
        rows = (
            list(range(len(self.records)))
            if all_rows
            else sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        )
        if not rows:
            QMessageBox.information(self, "Nothing selected", "Select one or more asset rows.")
            return
        requests = [
            AnalysisRequest(row, self.records[row])
            for row in rows
            if self.records[row].preview_path and not self.records[row].diagnostic
        ]
        if not requests:
            QMessageBox.warning(
                self, "No ready assets",
                "The selected rows need an unambiguous rendered preview and valid metadata.",
            )
            return
        model = self.model_edit.text().strip()
        if not model:
            QMessageBox.warning(self, "Model required", "Enter an Ollama vision model name.")
            return
        for request in requests:
            self.table.item(request.row, 8).setText("Analyzing…")
        self.analysis_thread = AnalysisThread(
            requests,
            model,
            tuple(
                category for category in self.categories
                if category.casefold() != "uncategorized"
            ) or self.categories,
            self.allowed_tags,
            self,
        )
        self.analysis_thread.item_done.connect(self._analysis_item_done)
        self.analysis_thread.progress_changed.connect(self._progress_changed)
        self.analysis_thread.finished.connect(self._work_finished)
        self.progress.setRange(0, len(requests))
        self.progress.setValue(0)
        self._set_busy(True)
        self.status_label.setText(f"Analyzing {len(requests)} asset previews…")
        self.analysis_thread.start()

    def _analysis_item_done(
        self, row: int, result: Classification | None, error: str
    ) -> None:
        if error or not result:
            self.table.item(row, 8).setText(error or "Analysis failed.")
            return
        category = self.table.cellWidget(row, 4)
        tags = self.table.cellWidget(row, 5)
        approval = self._approval(row)
        if isinstance(category, QComboBox):
            category.setEnabled(True)
            category.setCurrentText(result.category)
        if isinstance(tags, QLineEdit):
            tags.setEnabled(True)
            tags.setText(", ".join(result.tags))
        if approval:
            approval.setEnabled(True)
            approval.setChecked(True)
        self.table.item(row, 6).setText(f"{result.confidence:.0%}")
        self.table.item(row, 7).setText(result.rationale)
        self.table.item(row, 8).setText("Review, then apply")

    def _progress_changed(self, current: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)

    def _work_finished(self) -> None:
        self._set_busy(False)
        self.status_label.setText("Analysis finished. Review approved rows before applying.")

    def apply_approved(self) -> None:
        if not self.records:
            return
        applied = 0
        failures = 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for row, record in enumerate(tuple(self.records)):
            approval = self._approval(row)
            if not approval or not approval.isChecked():
                continue
            category_widget = self.table.cellWidget(row, 4)
            tags_widget = self.table.cellWidget(row, 5)
            category = category_widget.currentText() if isinstance(category_widget, QComboBox) else ""
            tag_values = (
                tuple(value.strip() for value in tags_widget.text().split(",") if value.strip())
                if isinstance(tags_widget, QLineEdit) else ()
            )
            error = self._validate_edit(category, tag_values)
            if error:
                self.table.item(row, 8).setText(error)
                failures += 1
                continue
            canonical_tags = {tag.casefold(): tag for tag in self.allowed_tags}
            tag_values = tuple(canonical_tags[tag.casefold()] for tag in tag_values)
            try:
                updated = apply_classification(
                    record, category, tag_values, preview_root=self.root_edit.text(),
                    backup_stamp=stamp,
                )
                self.records[row] = updated
                self.table.item(row, 1).setToolTip(str(updated.folder))
                self.table.item(row, 3).setText(
                    f"{updated.category} | {', '.join(updated.tags) or '(no tags)'}"
                )
                self.table.item(row, 8).setText("Applied")
                approval.setChecked(False)
                applied += 1
            except Exception as error:
                self.table.item(row, 8).setText(str(error))
                failures += 1
            QApplication.processEvents()
        self.status_label.setText(f"Applied {applied} classifications; {failures} failed.")

    def _validate_edit(self, category: str, tags: tuple[str, ...]) -> str:
        if category not in self.categories:
            return "Choose a category from the configured list."
        canonical = {tag.casefold(): tag for tag in self.allowed_tags}
        normalized = [canonical.get(tag.casefold()) for tag in tags]
        if len(tags) != 5 or any(tag is None for tag in normalized):
            return "Enter exactly five tags from the allowed-tags JSON."
        if len({str(tag).casefold() for tag in normalized}) != 5:
            return "The five proposed tags must be distinct."
        return ""

    def refresh_ollama(self) -> None:
        status = OllamaClient(timeout=3).status()
        model = self.model_edit.text().strip()
        if not status.available:
            self.ollama_status.setText(f"Server unavailable: {status.diagnostic}")
            self.start_button.setEnabled(True)
            self.download_button.setEnabled(False)
            return
        installed = model in status.models or any(
            item.split(":")[0] == model and ":" not in model for item in status.models
        )
        suffix = "installed" if installed else "not downloaded"
        self.ollama_status.setText(
            f"Ollama online — {len(status.models)} model(s); {model} {suffix}."
        )
        self.start_button.setEnabled(False)
        self.download_button.setEnabled(not installed)

    def start_ollama(self) -> None:
        if self.ollama_process and self.ollama_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.ollama_process = QProcess(self)
        self.ollama_process.setProgram("ollama")
        self.ollama_process.setArguments(["serve"])
        self.ollama_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.ollama_process.errorOccurred.connect(
            lambda _error: self.ollama_status.setText(
                f"Could not start Ollama: {self.ollama_process.errorString()}"
            )
        )
        self.ollama_process.start()
        self.ollama_status.setText("Starting Ollama server…")
        QTimer.singleShot(1800, self.refresh_ollama)

    def download_model(self) -> None:
        if self.pull_thread and self.pull_thread.isRunning():
            return
        model = self.model_edit.text().strip()
        if not model:
            return
        answer = QMessageBox.question(
            self, "Download model",
            f"Download {model} with Ollama? This may use several gigabytes of disk space.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.pull_thread = PullThread(model, self)
        self.pull_thread.progress_changed.connect(self._pull_progress)
        self.pull_thread.failed.connect(self._pull_failed)
        self.pull_thread.succeeded.connect(self._pull_succeeded)
        self.pull_thread.finished.connect(lambda: self._set_busy(False))
        self.progress.setRange(0, 0)
        self._set_busy(True)
        self.status_label.setText(f"Downloading {model}…")
        self.pull_thread.start()

    def _pull_progress(self, payload: dict) -> None:
        total = int(payload.get("total", 0) or 0)
        completed = int(payload.get("completed", 0) or 0)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(completed)
        self.status_label.setText(str(payload.get("status", "Downloading model…")))

    def _pull_failed(self, error: str) -> None:
        self.status_label.setText(error)
        QMessageBox.critical(self, "Model download failed", error)

    def _pull_succeeded(self) -> None:
        self.status_label.setText("Model download finished.")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.refresh_ollama()

    def cancel_work(self) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.cancel()
        if self.pull_thread and self.pull_thread.isRunning():
            self.pull_thread.cancel()
        self.status_label.setText("Cancel requested; waiting for the current request to finish…")

    def _approval(self, row: int) -> QCheckBox | None:
        container = self.table.cellWidget(row, 0)
        if not container:
            return None
        return container.findChild(QCheckBox)

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.scan_button, self.analyze_selected_button, self.analyze_all_button,
            self.apply_button, self.download_button,
        ):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.cancel_work()
        running = [
            thread for thread in (self.analysis_thread, self.pull_thread)
            if thread and thread.isRunning()
        ]
        if running:
            event.ignore()
            self._close_requested = True
            self.status_label.setText(
                "Waiting for the current Ollama request to finish before closing…"
            )
            for thread in running:
                try:
                    thread.finished.disconnect(self._retry_close)
                except TypeError:
                    pass
                thread.finished.connect(self._retry_close)
            return
        if self.ollama_process and self.ollama_process.state() != QProcess.ProcessState.NotRunning:
            self.ollama_process.terminate()
            if not self.ollama_process.waitForFinished(2000):
                self.ollama_process.kill()
        super().closeEvent(event)

    def _retry_close(self) -> None:
        if self._close_requested and not any(
            thread and thread.isRunning()
            for thread in (self.analysis_thread, self.pull_thread)
        ):
            self._close_requested = False
            QTimer.singleShot(0, self.close)
