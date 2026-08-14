from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.ai import (
    DEFAULT_MODEL,
    CategoryGuess,
    Classification,
    OllamaClient,
    TagGuess,
)
from universal_asset_library.library import AssetMetadataPatch


FALLBACK_CATEGORIES = frozenset({"uncategorized", "miscellaneous", "other"})


def is_fallback_category(category: str) -> bool:
    return category.strip().casefold() in FALLBACK_CATEGORIES


def actionable_categories(categories: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value for value in categories if not is_fallback_category(value))


class AiGuessSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str)


class AiGuessWorker(QRunnable):
    def __init__(
        self,
        operation: str,
        preview_path: Path,
        asset_type: str,
        asset_name: str,
        current_category: str,
        current_tags: tuple[str, ...],
        *,
        categories: tuple[str, ...] = (),
        allowed_tags: tuple[str, ...] = (),
        model: str = DEFAULT_MODEL,
    ) -> None:
        super().__init__()
        self.operation = operation
        self.preview_path = preview_path
        self.asset_type = asset_type
        self.asset_name = asset_name
        self.current_category = current_category
        self.current_tags = current_tags
        self.categories = categories
        self.allowed_tags = allowed_tags
        self.model = model
        self.cancel_event = Event()
        self.signals = AiGuessSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        client = OllamaClient()
        try:
            if self.operation == "category":
                result = client.guess_category(
                    self.preview_path,
                    model=self.model,
                    categories=self.categories,
                    asset_type=self.asset_type,
                    asset_name=self.asset_name,
                    current_category=self.current_category,
                    current_tags=self.current_tags,
                    cancel_event=self.cancel_event,
                )
            elif self.operation == "tags":
                result = client.guess_tags(
                    self.preview_path,
                    model=self.model,
                    allowed_tags=self.allowed_tags,
                    asset_type=self.asset_type,
                    asset_name=self.asset_name,
                    current_category=self.current_category,
                    current_tags=self.current_tags,
                    cancel_event=self.cancel_event,
                )
            else:
                raise ValueError(f"Unsupported AI guess operation: {self.operation}")
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(self.operation, result)


@dataclass(slots=True)
class AiOrganiseItem:
    asset: object
    preview_path: Path | None
    categories: tuple[str, ...]
    allowed_tags: tuple[str, ...]
    result: Classification | None = None
    error: str = ""


class AiBatchSignals(QObject):
    item_finished = pyqtSignal(int, object, str)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool)


class AiBatchWorker(QRunnable):
    """Run combined category/tag requests sequentially for predictable GPU use."""

    def __init__(
        self,
        requests: tuple[tuple[int, AiOrganiseItem], ...],
        model: str = DEFAULT_MODEL,
    ) -> None:
        super().__init__()
        self.requests = requests
        self.model = model
        self.cancel_event = Event()
        self.signals = AiBatchSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        client = OllamaClient()
        total = len(self.requests)
        for completed, (row, item) in enumerate(self.requests, 1):
            if self.cancel_event.is_set():
                break
            try:
                if item.preview_path is None:
                    raise ValueError("No readable managed still preview.")
                result = client.classify(
                    item.preview_path,
                    model=self.model,
                    categories=item.categories,
                    allowed_tags=item.allowed_tags,
                    asset_name=item.asset.name,
                    asset_type=item.asset.asset_type,
                    current_category=item.asset.category,
                    current_tags=item.asset.tags,
                    cancel_event=self.cancel_event,
                )
            except Exception as error:
                self.signals.item_finished.emit(row, None, str(error))
            else:
                self.signals.item_finished.emit(row, result, "")
            self.signals.progress.emit(completed, total)
        self.signals.finished.emit(self.cancel_event.is_set())


class AiOrganiserDialog(QDialog):
    COLUMNS = (
        "Apply",
        "Preview",
        "Asset",
        "Type",
        "Current category",
        "Current tags",
        "Proposed category",
        "Proposed tags",
        "Confidence",
        "Rationale",
        "Status",
    )

    def __init__(
        self,
        assets: tuple[object, ...],
        category_catalogs: dict[str, object],
        tag_vocabularies: dict[str, tuple[str, ...]],
        preview_resolver: Callable[[object], Path | None],
        *,
        current_asset_type: str,
        selected_ids: set[str] | None = None,
        initial_scope: str = "all_fallback",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.assets = assets
        self.category_catalogs = category_catalogs
        self.tag_vocabularies = tag_vocabularies
        self.preview_resolver = preview_resolver
        self.current_asset_type = current_asset_type
        self.selected_ids = selected_ids or set()
        self.items: list[AiOrganiseItem] = []
        self.worker: AiBatchWorker | None = None
        self.setWindowTitle("AI Organise Assets")
        self.resize(1480, 780)

        root = QVBoxLayout(self)
        heading = QLabel("AI Organise Assets")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)
        note = QLabel(
            "Analyse rendered previews locally, review every suggestion, then apply "
            "only the checked rows."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Scope"))
        self.scope = QComboBox()
        self.scope.addItem("All fallback-category assets", "all_fallback")
        self.scope.addItem("Current type fallback-category assets", "type_fallback")
        self.scope.addItem("Currently selected assets", "selected")
        initial_index = self.scope.findData(initial_scope)
        if initial_index >= 0:
            self.scope.setCurrentIndex(initial_index)
        self.scope.currentIndexChanged.connect(self._populate)
        controls.addWidget(self.scope)
        self.analyse_button = QPushButton("Analyse Candidates")
        self.analyse_button.setObjectName("primaryButton")
        self.analyse_button.clicked.connect(self._analyse)
        controls.addWidget(self.analyse_button)
        self.cancel_button = QPushButton("Cancel Analysis")
        self.cancel_button.clicked.connect(self._cancel_analysis)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        root.addLayout(controls)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 5, 7, 9, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("mutedLabel")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(360)
        self.progress.setRange(0, 1)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.progress)
        root.addLayout(status_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.addButton(
            "Apply Checked", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept_checked)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._populate()

    def _candidate_assets(self) -> tuple[object, ...]:
        scope = str(self.scope.currentData() or "all_fallback")
        if scope == "selected":
            return tuple(
                asset for asset in self.assets if asset.id in self.selected_ids
            )
        return tuple(
            asset
            for asset in self.assets
            if is_fallback_category(asset.category)
            and (
                scope != "type_fallback"
                or asset.asset_type == self.current_asset_type
            )
        )

    def _populate(self, *_args) -> None:
        if self.worker is not None:
            return
        self.items = []
        self.table.setRowCount(0)
        for row, asset in enumerate(self._candidate_assets()):
            catalog = self.category_catalogs[asset.asset_type]
            categories = actionable_categories(tuple(catalog.names))
            tags = self.tag_vocabularies.get(asset.asset_type, ())
            preview = self.preview_resolver(asset)
            item = AiOrganiseItem(asset, preview, categories, tags)
            if preview is None:
                item.error = "Skipped: no readable managed still preview."
            elif not categories:
                item.error = "Skipped: no non-fallback category is configured."
            elif len(tags) < 5:
                item.error = "Skipped: fewer than five allowed tags are configured."
            self.items.append(item)
            self.table.insertRow(row)
            self.table.setRowHeight(row, 68)
            self._populate_row(row, item)
        ready = sum(not item.error for item in self.items)
        self.status.setText(
            f"{len(self.items)} candidate asset(s); {ready} ready for analysis."
        )
        self.analyse_button.setEnabled(bool(ready))
        self.apply_button.setEnabled(False)
        self.progress.setRange(0, max(1, ready))
        self.progress.setValue(0)

    def _populate_row(self, row: int, item: AiOrganiseItem) -> None:
        approval = QCheckBox()
        approval.setEnabled(False)
        approval_holder = QWidget()
        approval_layout = QHBoxLayout(approval_holder)
        approval_layout.setContentsMargins(0, 0, 0, 0)
        approval_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        approval_layout.addWidget(approval)
        self.table.setCellWidget(row, 0, approval_holder)

        preview_label = QLabel("—")
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if item.preview_path is not None:
            pixmap = QPixmap(str(item.preview_path))
            if not pixmap.isNull():
                preview_label.setPixmap(pixmap.scaled(
                    96,
                    60,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        self.table.setCellWidget(row, 1, preview_label)
        self._readonly_item(row, 2, item.asset.name)
        self._readonly_item(row, 3, _asset_type_label(item.asset.asset_type))
        self._readonly_item(row, 4, item.asset.category or "(none)")
        self._readonly_item(row, 5, ", ".join(item.asset.tags) or "(none)")

        category = QComboBox()
        category.addItems(item.categories)
        category.setEnabled(False)
        self.table.setCellWidget(row, 6, category)
        tags = QLineEdit()
        tags.setPlaceholderText("Five comma-separated allowed tags")
        tags.setEnabled(False)
        self.table.setCellWidget(row, 7, tags)
        self._readonly_item(row, 8, "")
        self._readonly_item(row, 9, "")
        self._readonly_item(row, 10, item.error or "Ready")

    def _readonly_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, column, item)

    def _analyse(self) -> None:
        if self.worker is not None:
            return
        requests = tuple(
            (row, item)
            for row, item in enumerate(self.items)
            if item.preview_path is not None
            and item.categories
            and len(item.allowed_tags) >= 5
            and item.result is None
        )
        if not requests:
            self.status.setText("There are no unanalysed candidates to process.")
            return
        for row, _item in requests:
            self.table.item(row, 10).setText("Analysing…")
        worker = AiBatchWorker(requests)
        self.worker = worker
        worker.signals.item_finished.connect(self._item_finished)
        worker.signals.progress.connect(self._progress)
        worker.signals.finished.connect(self._analysis_finished)
        self.scope.setEnabled(False)
        self.analyse_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.apply_button.setEnabled(False)
        self.progress.setRange(0, len(requests))
        self.progress.setValue(0)
        self.status.setText(f"Analysing {len(requests)} asset preview(s)…")
        QThreadPool.globalInstance().start(worker)

    def _item_finished(
        self, row: int, result: Classification | None, error: str
    ) -> None:
        item = self.items[row]
        item.result = result
        item.error = error
        if result is None:
            self.table.item(row, 10).setText(error or "Analysis failed.")
            return
        category = self.table.cellWidget(row, 6)
        tags = self.table.cellWidget(row, 7)
        approval = self._approval(row)
        if isinstance(category, QComboBox):
            category.setCurrentText(result.category)
            category.setEnabled(True)
        if isinstance(tags, QLineEdit):
            tags.setText(", ".join(result.tags))
            tags.setEnabled(True)
        if approval is not None:
            approval.setEnabled(True)
            approval.setChecked(True)
        self.table.item(row, 8).setText(f"{result.confidence:.0%}")
        self.table.item(row, 9).setText(result.rationale)
        self.table.item(row, 10).setText("Review, then apply")

    def _progress(self, completed: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(completed)

    def _analysis_finished(self, canceled: bool) -> None:
        self.worker = None
        self.scope.setEnabled(True)
        self.cancel_button.setEnabled(False)
        for row, item in enumerate(self.items):
            if (
                item.result is None
                and self.table.item(row, 10).text() == "Analysing…"
            ):
                item.error = "Canceled before analysis."
                self.table.item(row, 10).setText(item.error)
        failed = sum(
            bool(item.error)
            and item.preview_path is not None
            and bool(item.categories)
            and len(item.allowed_tags) >= 5
            for item in self.items
        )
        ready = sum(item.result is not None for item in self.items)
        self.analyse_button.setText("Retry Failed" if failed else "Analysis Complete")
        self.analyse_button.setEnabled(bool(failed))
        self.apply_button.setEnabled(bool(ready))
        self.status.setText(
            "Analysis canceled; completed results can still be reviewed."
            if canceled
            else f"Analysis finished: {ready} result(s), {failed} failure(s)."
        )

    def _cancel_analysis(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Canceling after the current request…")

    def _approval(self, row: int) -> QCheckBox | None:
        holder = self.table.cellWidget(row, 0)
        if holder is None:
            return None
        return holder.findChild(QCheckBox)

    def approved_patches(self) -> tuple[tuple[object, AssetMetadataPatch], ...]:
        approved: list[tuple[object, AssetMetadataPatch]] = []
        for row, item in enumerate(self.items):
            approval = self._approval(row)
            if approval is None or not approval.isChecked() or item.result is None:
                continue
            category_widget = self.table.cellWidget(row, 6)
            tags_widget = self.table.cellWidget(row, 7)
            category = (
                category_widget.currentText()
                if isinstance(category_widget, QComboBox)
                else ""
            )
            raw_tags = (
                tuple(
                    value.strip()
                    for value in tags_widget.text().split(",")
                    if value.strip()
                )
                if isinstance(tags_widget, QLineEdit)
                else ()
            )
            canonical = {value.casefold(): value for value in item.allowed_tags}
            tags = tuple(canonical[value.casefold()] for value in raw_tags)
            approved.append((
                item.asset,
                AssetMetadataPatch(category=category, add_tags=tags),
            ))
        return tuple(approved)

    def _accept_checked(self) -> None:
        checked = 0
        for row, item in enumerate(self.items):
            approval = self._approval(row)
            if approval is None or not approval.isChecked():
                continue
            checked += 1
            category_widget = self.table.cellWidget(row, 6)
            tags_widget = self.table.cellWidget(row, 7)
            category = (
                category_widget.currentText()
                if isinstance(category_widget, QComboBox)
                else ""
            )
            tags = tuple(
                value.strip()
                for value in tags_widget.text().split(",")
                if value.strip()
            ) if isinstance(tags_widget, QLineEdit) else ()
            error = self._validate_edit(item, category, tags)
            if error:
                self.table.item(row, 10).setText(error)
                self.table.scrollToItem(self.table.item(row, 10))
                return
        if not checked:
            self.status.setText("Check at least one analysed row to apply.")
            return
        self.accept()

    @staticmethod
    def _validate_edit(
        item: AiOrganiseItem, category: str, tags: tuple[str, ...]
    ) -> str:
        if category not in item.categories:
            return "Choose a configured non-fallback category."
        allowed = {value.casefold() for value in item.allowed_tags}
        if len(tags) != 5 or any(value.casefold() not in allowed for value in tags):
            return "Enter exactly five tags from this asset type's allowed list."
        if len({value.casefold() for value in tags}) != 5:
            return "The five proposed tags must be distinct."
        return ""

    def reject(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
        super().reject()


class PullSignals(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class OllamaPullWorker(QRunnable):
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        super().__init__()
        self.model = model
        self.cancel_event = Event()
        self.signals = PullSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            OllamaClient(timeout=3600).pull(
                self.model, self.signals.progress.emit, self.cancel_event
            )
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit()


class GuessConfirmationDialog(QDialog):
    def __init__(
        self,
        asset,
        operation: str,
        result: CategoryGuess | TagGuess,
        preview_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.operation = operation
        self.setWindowTitle(
            "Confirm guessed category" if operation == "category"
            else "Confirm guessed tags"
        )
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        heading = QLabel(asset.name)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedHeight(180)
        preview.setStyleSheet("background:#1b1d20;")
        pixmap = QPixmap(str(preview_path))
        if pixmap.isNull():
            preview.setText("Preview unavailable")
        else:
            preview.setPixmap(pixmap.scaled(
                520, 170, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        layout.addWidget(preview)

        if operation == "category" and isinstance(result, CategoryGuess):
            current = asset.category or "(none)"
            proposed = result.category
            action_text = "Apply Category"
        elif operation == "tags" and isinstance(result, TagGuess):
            current = ", ".join(asset.tags) or "(none)"
            proposed = ", ".join(result.tags)
            action_text = "Add Tags"
        else:
            raise ValueError("Guess result does not match the requested operation.")
        comparison = QLabel(
            f"<b>Current</b><br>{_escape(current)}<br><br>"
            f"<b>Suggested</b><br>{_escape(proposed)}<br><br>"
            f"<b>Confidence</b> {result.confidence:.0%}<br>"
            f"<b>Reason</b> {_escape(result.rationale)}"
        )
        comparison.setWordWrap(True)
        layout.addWidget(comparison)

        note = QLabel(
            "This suggestion is read-only. Cancel and use Edit Asset if it needs correction."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        apply_button = buttons.addButton(
            action_text, QDialogButtonBox.ButtonRole.AcceptRole
        )
        apply_button.setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class OllamaSetupDialog(QDialog):
    """Modal setup surface; expensive pull work remains in the global thread pool."""

    def __init__(
        self,
        start_server: Callable[[], None],
        parent: QWidget | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        super().__init__(parent)
        self.start_server_callback = start_server
        self.model = model
        self.pull_worker: OllamaPullWorker | None = None
        self._waiting_for_server = False
        self.setWindowTitle("Set up local AI classification")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        heading = QLabel("Ollama setup")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        note = QLabel(
            f"ShotBox uses the local {model} vision model. Images stay on this computer."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        action_row = QHBoxLayout()
        self.start_button = QPushButton("Start Ollama")
        self.start_button.clicked.connect(self._start_server)
        self.download_button = QPushButton("Download Model")
        self.download_button.clicked.connect(self._download)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.download_button)
        action_row.addStretch()
        layout.addLayout(action_row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.continue_button = self.buttons.addButton(
            "Continue", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.continue_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(650)
        self.poll_timer.timeout.connect(self.refresh_status)
        QTimer.singleShot(0, self.refresh_status)

    def refresh_status(self) -> None:
        status = OllamaClient(timeout=1.5).status()
        if not status.available:
            self.status_label.setText(
                "Ollama is not running. Start it here, or run `ollama serve` in a terminal."
            )
            self.start_button.setEnabled(not self._waiting_for_server)
            self.download_button.setEnabled(False)
            self.continue_button.setEnabled(False)
            return
        self._waiting_for_server = False
        self.poll_timer.stop()
        self.start_button.setEnabled(False)
        if status.has_model(self.model):
            self.status_label.setText(f"Ollama and {self.model} are ready.")
            self.download_button.setEnabled(False)
            self.continue_button.setEnabled(True)
            if self.isVisible():
                self.accept()
        else:
            self.status_label.setText(
                f"Ollama is running, but {self.model} has not been downloaded."
            )
            self.download_button.setEnabled(self.pull_worker is None)
            self.continue_button.setEnabled(False)

    def _start_server(self) -> None:
        self.start_server_callback()
        self._waiting_for_server = True
        self.start_button.setEnabled(False)
        self.status_label.setText("Starting Ollama…")
        self.poll_timer.start()

    def _download(self) -> None:
        answer = QMessageBox.question(
            self,
            "Download vision model",
            f"Download {self.model}? This requires several gigabytes of disk space.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = OllamaPullWorker(self.model)
        self.pull_worker = worker
        worker.signals.progress.connect(self._pull_progress)
        worker.signals.finished.connect(self._pull_finished)
        worker.signals.failed.connect(self._pull_failed)
        self.download_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        QThreadPool.globalInstance().start(worker)

    def _pull_progress(self, payload: dict) -> None:
        total = int(payload.get("total", 0) or 0)
        completed = int(payload.get("completed", 0) or 0)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(completed)
        self.status_label.setText(str(payload.get("status", "Downloading model…")))

    def _pull_finished(self) -> None:
        self.pull_worker = None
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.refresh_status()

    def _pull_failed(self, message: str) -> None:
        self.pull_worker = None
        self.progress.hide()
        self.status_label.setText(message)
        self.download_button.setEnabled(True)

    def reject(self) -> None:
        if self.pull_worker is not None:
            self.pull_worker.cancel()
        self.poll_timer.stop()
        super().reject()


def _asset_type_label(asset_type: str) -> str:
    return {
        "texture_set": "Texture",
        "atlas": "Atlas",
        "hdri": "HDRI",
        "model": "Model",
        "stock": "Stock",
    }.get(asset_type, asset_type)


def _escape(value: str) -> str:
    import html

    return html.escape(value)
