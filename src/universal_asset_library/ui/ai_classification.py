from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.ai import (
    DEFAULT_MODEL,
    CategoryGuess,
    OllamaClient,
    TagGuess,
)


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


def _escape(value: str) -> str:
    import html

    return html.escape(value)
