from __future__ import annotations

import os

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from universal_asset_library.library import CancelToken, LibraryRepository
from universal_asset_library.library.polyhaven_sync import (
    PolyHavenSyncService,
    PolyHavenSyncReview,
)
from universal_asset_library.polyhaven_settings import (
    PolyHavenSyncPreferences,
    RESOLUTIONS,
    polyhaven_download_directory,
)
from universal_asset_library.settings import AppSettings


def size_text(value: int) -> str:
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return ""


class SyncSignals(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class PolyHavenSyncWorker(QRunnable):
    def __init__(
        self,
        settings: AppSettings,
        preferences: PolyHavenSyncPreferences,
        token: CancelToken,
        entries=None,
    ):
        super().__init__()
        self.settings = settings
        self.preferences = preferences
        self.token = token
        self.entries = entries
        self.signals = SyncSignals()

    def run(self):
        try:
            settings = self.settings
            repository = LibraryRepository(
                settings.library_path,
                blender_path=settings.blender_path,
                render_hdri_previews=settings.render_hdri_on_import,
                render_texture_previews=settings.render_texture_on_import,
                save_texture_preview_blend=settings.save_texture_preview_blend,
            )
            service = PolyHavenSyncService(
                repository,
                token=self.token,
                default_category=settings.default_import_category,
                default_model_category=settings.default_model_category,
            )
            if self.entries is None:
                result = service.discover(self.preferences, self.signals.progress.emit)
            else:
                result = service.install(self.entries, self.signals.progress.emit)
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)


class _ResolutionCombo(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class PolyHavenPanel(QFrame):
    preferences_changed = pyqtSignal()
    busy_changed = pyqtSignal(bool)
    imported = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._settings = AppSettings()
        self._draft_path = ""
        self._blocked = False
        self._external_blocked = False
        self._worker = None
        self._token = None
        self._review = PolyHavenSyncReview()
        self._loading = False
        layout = QVBoxLayout(self)
        title = QLabel(
            'Poly Haven · <a style="color: #ff9857" href="https://polyhaven.com/">Free assets</a>'
        )
        title.setOpenExternalLinks(True)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        help_text = QLabel(
            "Find assets missing from your library. Review sizes before downloading. Existing assets are skipped at any resolution."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        download_location = QHBoxLayout()
        download_location.addWidget(QLabel("Local downloads"))
        self.download_path = QLineEdit(str(polyhaven_download_directory()))
        self.download_path.setReadOnly(True)
        download_location.addWidget(self.download_path)
        layout.addLayout(download_location)
        download_help = QLabel(
            "Assets download here first, then import into your main library (including network drives). "
            "Staging files are cleaned up after import or cancellation."
        )
        download_help.setWordWrap(True)
        download_help.setObjectName("mutedLabel")
        layout.addWidget(download_help)
        options = QGridLayout()
        self.type_checks = {}
        self.resolution_combos = {}
        for row, (key, label) in enumerate(
            (("hdri", "HDRIs"), ("texture", "Textures"), ("model", "Models"))
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            combo = _ResolutionCombo()
            combo.addItems(RESOLUTIONS)
            combo.setCurrentText("4K")
            checkbox.toggled.connect(self._preferences_changed)
            combo.currentTextChanged.connect(self._preferences_changed)
            self.type_checks[key] = checkbox
            self.resolution_combos[key] = combo
            options.addWidget(checkbox, row, 0)
            options.addWidget(combo, row, 1)
        layout.addLayout(options)
        formats = QLabel(
            "EXR HDRIs · Best available texture maps · USD + Blender models with required textures"
        )
        formats.setWordWrap(True)
        formats.setObjectName("mutedLabel")
        layout.addWidget(formats)
        actions = QHBoxLayout()
        self.check_button = QPushButton("Check for new assets")
        self.check_button.clicked.connect(self.check)
        self.download_button = QPushButton("Download selected")
        self.download_button.setObjectName("primaryButton")
        self.download_button.clicked.connect(self.download)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        for button in (self.check_button, self.download_button, self.cancel_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Asset", "Type", "Resolution", "Formats", "Download", "Notes"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.table.setMinimumHeight(220)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.hide()
        layout.addWidget(self.table)
        selection = QHBoxLayout()
        self.select_all = QPushButton("Select all")
        self.select_none = QPushButton("Select none")
        self.select_all.clicked.connect(lambda: self._select(True))
        self.select_none.clicked.connect(lambda: self._select(False))
        selection.addWidget(self.select_all)
        selection.addWidget(self.select_none)
        self.totals = QLabel()
        selection.addWidget(self.totals)
        selection.addStretch()
        layout.addLayout(selection)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(170)
        self.report.hide()
        layout.addWidget(self.report)
        self._update_controls()

    @property
    def busy(self):
        return self._worker is not None

    def preferences(self):
        return PolyHavenSyncPreferences(
            self.type_checks["hdri"].isChecked(),
            self.type_checks["texture"].isChecked(),
            self.type_checks["model"].isChecked(),
            *(
                self.resolution_combos[key].currentText()
                for key in ("hdri", "texture", "model")
            ),
        )

    def set_preferences(self, preferences):
        changed = preferences != self.preferences()
        self._loading = True
        for key, enabled, resolution in (
            ("hdri", preferences.hdris, preferences.hdri_resolution),
            ("texture", preferences.textures, preferences.texture_resolution),
            ("model", preferences.models, preferences.model_resolution),
        ):
            self.type_checks[key].setChecked(enabled)
            self.resolution_combos[key].setCurrentText(resolution)
        self._loading = False
        if changed:
            self.invalidate()

    def set_context(self, settings, draft_path):
        changed = (settings.library_path, draft_path) != (
            self._settings.library_path,
            self._draft_path,
        )
        self._settings = settings
        self._draft_path = draft_path
        if changed:
            if self.busy:
                self.cancel()
            self.invalidate()
        self._update_controls()

    def set_blocked(self, blocked):
        self._blocked = blocked
        self._update_controls()

    def set_external_blocked(self, blocked):
        self._external_blocked = blocked
        self._update_controls()

    def invalidate(self):
        self._review = PolyHavenSyncReview()
        self.table.setRowCount(0)
        self.table.hide()
        self._selection_changed()

    def _preferences_changed(self, *_args):
        if self._loading:
            return
        self.invalidate()
        self.preferences_changed.emit()

    def selected_entries(self):
        return [
            entry
            for row, entry in enumerate(self._review.entries)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _select(self, checked):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
        self.table.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args):
        selected = self.selected_entries()
        self.totals.setText(
            f"{len(selected)} selected · {size_text(sum(entry.total_size for entry in selected))}"
        )
        self._update_controls()

    def _ready(self):
        path = self._settings.library_path
        return bool(
            path
            and path == self._draft_path
            and os.path.isdir(path)
            and os.access(path, os.W_OK)
        )

    def _update_controls(self):
        idle = not self.busy and not self._blocked and not self._external_blocked
        ready = idle and self._ready()
        self.check_button.setEnabled(
            ready and bool(self.preferences().selected_types())
        )
        self.download_button.setEnabled(ready and bool(self.selected_entries()))
        self.cancel_button.setEnabled(
            self.busy and self._token is not None and not self._token.cancelled
        )
        self.table.setEnabled(idle)
        for widget in (
            *self.type_checks.values(),
            *self.resolution_combos.values(),
            self.select_all,
            self.select_none,
        ):
            widget.setEnabled(idle)
        if not self.busy and not self._ready():
            self.status.setText(
                "Choose your main library in Settings → Library and click Save settings. "
                "Downloads will be staged locally."
            )
        elif not self.busy and self.status.text().startswith("Choose your main library"):
            self.status.setText("Ready to check Poly Haven.")

    def check(self):
        if not self.check_button.isEnabled():
            return
        self.invalidate()
        self._start(None)

    def download(self):
        if not self.download_button.isEnabled():
            return
        self._start(self.selected_entries())

    def _start(self, entries):
        self._token = CancelToken()
        worker = PolyHavenSyncWorker(
            self._settings, self.preferences(), self._token, entries
        )
        self._worker = worker
        self._run_path = self._settings.library_path
        self.report.clear()
        self.report.hide()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(
            "Checking Poly Haven…" if entries is None else "Starting downloads…"
        )
        worker.signals.progress.connect(self._progress)
        worker.signals.finished.connect(self._finished)
        worker.signals.failed.connect(self._failed)
        self._update_controls()
        self.busy_changed.emit(True)
        QThreadPool.globalInstance().start(worker)

    def cancel(self):
        if self._token:
            self._token.cancel()
            self.status.setText("Canceling safely… completed imports will be kept.")
            self._update_controls()

    def _progress(self, value):
        if self._token and self._token.cancelled:
            return
        self.status.setText(f"{value.phase} · {value.name}".rstrip(" ·"))
        if value.bytes_total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(
                min(1000, value.bytes_completed * 1000 // value.bytes_total)
            )
            self.progress.setFormat(
                f"{size_text(value.bytes_completed)} / {size_text(value.bytes_total)}"
            )
        elif value.total:
            self.progress.setRange(0, value.total)
            self.progress.setValue(value.completed)
            self.progress.setFormat("%v / %m")

    def _finish_busy(self):
        self._worker = None
        self._token = None
        self.progress.hide()
        self._update_controls()
        self.busy_changed.emit(False)

    def _finished(self, result):
        stale = (
            self._run_path != self._settings.library_path
            or self._run_path != self._draft_path
        )
        if isinstance(result, PolyHavenSyncReview):
            if not stale and not result.canceled:
                self._show_review(result)
            self.status.setText(
                "Check canceled. Check again to refresh the list."
                if result.canceled or stale
                else f"{len(result.entries)} missing or possible matches · {result.owned} already owned · {len(result.failed)} unavailable"
            )
            lines = [f"{name}: {error}" for name, error in result.failed.items()]
        else:
            # Successful entries disappear; failed/unattempted ones remain available for retry.
            imported_slugs = {asset.provider_id for asset in result.imported}
            self._show_review(
                PolyHavenSyncReview(
                    entries=[
                        entry
                        for entry in self._review.entries
                        if entry.slug not in imported_slugs
                    ]
                )
            )
            self.status.setText(
                f"{'Canceled' if result.canceled else 'Finished'} · {len(result.imported)} imported · {len(result.skipped)} skipped · {len(result.failed)} failed"
            )
            lines = [f"Imported: {asset.name}" for asset in result.imported]
            lines += [f"Skipped: {item}" for item in result.skipped]
            lines += [
                f"Failed: {name}: {error}" for name, error in result.failed.items()
            ]
        self.report.setPlainText("\n".join(lines))
        self.report.setVisible(bool(lines))
        self._finish_busy()
        if not isinstance(result, PolyHavenSyncReview) and not stale:
            self.imported.emit(result)

    def _show_review(self, review):
        self._review = review
        self.table.blockSignals(True)
        self.table.setRowCount(len(review.entries))
        names = {"hdri": "HDRI", "texture_set": "Texture", "model": "Model"}
        for row, entry in enumerate(review.entries):
            notes = entry.note
            if entry.possible_matches:
                notes += " Possible existing asset: " + ", ".join(
                    entry.possible_matches
                )
            for column, value in enumerate(
                (
                    entry.name,
                    names[entry.asset_type],
                    entry.resolution,
                    entry.formats,
                    size_text(entry.total_size),
                    notes.strip(),
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(
                        Qt.CheckState.Unchecked
                        if entry.possible_matches
                        else Qt.CheckState.Checked
                    )
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self.table.setVisible(bool(review.entries))
        self._selection_changed()

    def _failed(self, message):
        self.status.setText(f"Poly Haven: {message}")
        self._finish_busy()
