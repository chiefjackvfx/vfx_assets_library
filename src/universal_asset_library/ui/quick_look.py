"""Quick Look-style previews for assets in the library browser."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QObject,
    QPoint,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QKeySequence, QMouseEvent, QPixmap, QShortcut, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    HAS_MULTIMEDIA = True
except ImportError:  # pragma: no cover - depends on the Qt installation
    QAudioOutput = None
    QMediaMetaData = None
    QMediaPlayer = None
    QVideoWidget = None
    HAS_MULTIMEDIA = False


IMAGE_SUFFIXES = {
    ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
}
VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


@dataclass(frozen=True)
class QuickLookEntry:
    filename: str
    path: Path
    is_video: bool = False


@dataclass(frozen=True)
class QuickLookMedia:
    title: str
    entries: tuple[QuickLookEntry, ...]

    @property
    def is_previewable(self) -> bool:
        return bool(self.entries)


def quick_look_media_for_asset(asset) -> QuickLookMedia:
    """Resolve the managed still and video previews belonging to an asset."""
    entries: list[QuickLookEntry] = []
    seen: set[Path] = set()

    # The hero is normally the highest-quality still. Fall back to the card
    # thumbnail, while avoiding duplicate entries when they share a path.
    for attribute in ("hero_path", "thumbnail_path"):
        candidate = getattr(asset, attribute, None)
        path = Path(candidate) if candidate else None
        if (
            path is not None
            and path not in seen
            and path.is_file()
            and path.suffix.casefold() in IMAGE_SUFFIXES
        ):
            entries.append(QuickLookEntry(path.name, path))
            seen.add(path)
            break

    # Stock clips and animated VDBs expose their lightweight playback file as
    # preview_path. Keep the still first so Up/Down mirrors Shotbox's stack.
    candidate = getattr(asset, "preview_path", None)
    video_path = Path(candidate) if candidate else None
    if (
        video_path is not None
        and video_path not in seen
        and video_path.is_file()
        and video_path.suffix.casefold() in VIDEO_SUFFIXES
    ):
        entries.append(QuickLookEntry(video_path.name, video_path, True))

    return QuickLookMedia(str(getattr(asset, "name", "") or "Untitled Asset"), tuple(entries))


class SeekSlider(QSlider):
    """Seek immediately when the user clicks or drags on the timeline."""

    def _value_at(self, event: QMouseEvent) -> int:
        pixel = round(event.position().x())
        return self.style().sliderValueFromPosition(
            self.minimum(), self.maximum(), min(max(0, pixel), max(1, self.width())),
            max(1, self.width()), self.invertedAppearance(),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setSliderDown(True)
        self.setSliderPosition(self._value_at(event))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.isSliderDown():
            super().mouseMoveEvent(event)
            return
        self.setSliderPosition(self._value_at(event))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isSliderDown():
            super().mouseReleaseEvent(event)
            return
        self.setSliderPosition(self._value_at(event))
        self.setSliderDown(False)
        event.accept()


class PanZoomViewport(QScrollArea):
    """Frameless viewport with pointer-centred zoom and drag panning."""

    zoom_changed = pyqtSignal(float)
    MIN_ZOOM = 0.25
    FIT_ZOOM = 1.0
    MAX_ZOOM = 8.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._zoom = self.FIT_ZOOM
        self._drag_origin: QPoint | None = None
        self._drag_scroll_origin = QPoint()
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Scroll to zoom · Drag to pan · Double-click to reset")
        self.viewport().installEventFilter(self)

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self.add_interaction_target(widget)
        self._apply_zoom()

    def add_interaction_target(self, widget: QWidget | None) -> None:
        if widget is not None:
            widget.installEventFilter(self)

    def reset_zoom(self) -> None:
        self.set_zoom(self.FIT_ZOOM)

    def set_zoom(self, zoom: float, anchor: QPoint | None = None) -> None:
        widget = self.widget()
        if widget is None:
            return
        new_zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, float(zoom)))
        if abs(new_zoom - self._zoom) < 0.001:
            if new_zoom == self.FIT_ZOOM:
                self.horizontalScrollBar().setValue(0)
                self.verticalScrollBar().setValue(0)
            return
        viewport_size = self.viewport().size()
        anchor = anchor or QPoint(viewport_size.width() // 2, viewport_size.height() // 2)
        old_size = widget.size()
        x_ratio = (self.horizontalScrollBar().value() + anchor.x()) / max(1, old_size.width())
        y_ratio = (self.verticalScrollBar().value() + anchor.y()) / max(1, old_size.height())
        self._zoom = new_zoom
        self._apply_zoom()
        new_size = widget.size()
        self.horizontalScrollBar().setValue(round(x_ratio * new_size.width() - anchor.x()))
        self.verticalScrollBar().setValue(round(y_ratio * new_size.height() - anchor.y()))
        self._update_cursor()
        self.zoom_changed.emit(self._zoom)

    def _apply_zoom(self) -> None:
        widget = self.widget()
        if widget is not None:
            size = self.viewport().size()
            widget.resize(
                max(1, round(size.width() * self._zoom)),
                max(1, round(size.height() * self._zoom)),
            )

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                anchor = self.viewport().mapFromGlobal(event.globalPosition().toPoint())
                self.set_zoom(self._zoom * (1.0015 ** delta), anchor)
                event.accept()
                return True
        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and self._zoom > self.FIT_ZOOM:
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_scroll_origin = QPoint(
                    self.horizontalScrollBar().value(), self.verticalScrollBar().value()
                )
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
        elif event.type() == QEvent.Type.MouseMove and self._drag_origin is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.horizontalScrollBar().setValue(self._drag_scroll_origin.x() - delta.x())
            self.verticalScrollBar().setValue(self._drag_scroll_origin.y() - delta.y())
            event.accept()
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_origin is not None:
            self._drag_origin = None
            self._update_cursor()
            event.accept()
            return True
        elif event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.reset_zoom()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _update_cursor(self) -> None:
        self.viewport().setCursor(
            Qt.CursorShape.OpenHandCursor
            if self._zoom > self.FIT_ZOOM else Qt.CursorShape.ArrowCursor
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_zoom()


class CenterResizeGrip(QSizeGrip):
    """Resize a frameless popup around its centre point."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._press_position: QPoint | None = None
        self._start_geometry = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_position = event.globalPosition().toPoint()
        self._start_geometry = self.window().geometry()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_position is None or self._start_geometry is None:
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._press_position
        minimum = self.window().minimumSize()
        width = max(minimum.width(), self._start_geometry.width() + 2 * delta.x())
        height = max(minimum.height(), self._start_geometry.height() + 2 * delta.y())
        centre = self._start_geometry.center()
        self.window().setGeometry(centre.x() - width // 2, centre.y() - height // 2, width, height)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_position is None:
            super().mouseReleaseEvent(event)
            return
        self.mouseMoveEvent(event)
        self._press_position = None
        self._start_geometry = None
        event.accept()


class QuickLookPopup(QWidget):
    """Frameless, modeless still/video preview window."""

    dismissed = pyqtSignal()
    navigation_requested = pyqtSignal(int)

    def __init__(self, parent=None, screen_percentage: int = 70) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("quickLookPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 320)
        self._screen_percentage = min(100, max(25, int(screen_percentage)))
        self._entries: tuple[QuickLookEntry, ...] = ()
        self._entry_index = -1
        self._original_pixmap: QPixmap | None = None
        self._is_video = False
        self._session_visible = False
        self._slider_dragging = False
        self._resume_after_scrub = False
        self._reverse_playing = False
        self._reverse_timer = QTimer(self)
        self._reverse_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._reverse_timer.timeout.connect(self._reverse_tick)
        self._build_ui()
        self._build_player()
        self._build_shortcuts()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        self.panel = QFrame(self)
        self.panel.setObjectName("quickLookPanel")
        root.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(14, 10, 14, 22)
        layout.setSpacing(10)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title_label = QLabel("Quick Look")
        self.title_label.setObjectName("quickLookTitle")
        self.filename_label = QLabel()
        self.filename_label.setObjectName("quickLookMeta")
        titles.addWidget(self.title_label)
        titles.addWidget(self.filename_label)
        header.addLayout(titles, 1)
        hint = QLabel("←/→ Asset  ·  ↑/↓ Media  ·  J/K/L  ·  ,/. Frame  ·  Scroll Zoom  ·  Space/Esc")
        hint.setObjectName("quickLookHint")
        header.addWidget(hint)
        close_button = QPushButton("×")
        close_button.setObjectName("quickLookButton")
        close_button.setFixedSize(30, 30)
        close_button.setToolTip("Close Quick Look (Space or Esc)")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.media_stack = QStackedWidget()
        self.media_stack.setObjectName("quickLookMedia")
        self.media_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_view = PanZoomViewport()
        self.media_view.setObjectName("quickLookViewport")
        self.media_view.setWidget(self.media_stack)
        self.media_view.zoom_changed.connect(lambda _zoom: self._scale_still())
        self.image_label = QLabel("No preview available")
        self.image_label.setObjectName("quickLookImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_stack.addWidget(self.image_label)
        self.media_view.add_interaction_target(self.image_label)
        layout.addWidget(self.media_view, 1)

        self.controls = QFrame()
        self.controls.setObjectName("quickLookControls")
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(8, 5, 8, 5)
        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("quickLookButton")
        self.play_button.setFixedSize(32, 28)
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)
        self.position_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.position_slider.sliderPressed.connect(self._slider_pressed)
        self.position_slider.sliderReleased.connect(self._slider_released)
        self.position_slider.sliderMoved.connect(self._slider_moved)
        controls.addWidget(self.position_slider, 1)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("quickLookMeta")
        controls.addWidget(self.time_label)
        self.mute_button = QPushButton("🔊")
        self.mute_button.setObjectName("quickLookButton")
        self.mute_button.setFixedSize(36, 28)
        self.mute_button.clicked.connect(self._toggle_mute)
        controls.addWidget(self.mute_button)
        layout.addWidget(self.controls)
        self.controls.hide()
        self.resize_grip = CenterResizeGrip(self.panel)
        self.resize_grip.setFixedSize(20, 20)
        self._position_resize_grip()

    def _build_player(self) -> None:
        self.video_widget = None
        self.player = None
        self.audio_output = None
        if not HAS_MULTIMEDIA:
            return
        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_stack.addWidget(self.video_widget)
        self.media_view.add_interaction_target(self.video_widget)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.5)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.errorOccurred.connect(self._player_error)

    def _shortcut(self, key, callback: Callable[[], None]) -> QShortcut:
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.setAutoRepeat(False)
        shortcut.activated.connect(callback)
        return shortcut

    def _build_shortcuts(self) -> None:
        self._shortcuts = [
            self._shortcut(Qt.Key.Key_Space, self.hide),
            self._shortcut(Qt.Key.Key_Escape, self.hide),
            self._shortcut(Qt.Key.Key_Left, lambda: self.navigation_requested.emit(-1)),
            self._shortcut(Qt.Key.Key_Right, lambda: self.navigation_requested.emit(1)),
            self._shortcut(Qt.Key.Key_Up, lambda: self.navigate_media(-1)),
            self._shortcut(Qt.Key.Key_Down, lambda: self.navigate_media(1)),
            self._shortcut(Qt.Key.Key_J, self._play_reverse),
            self._shortcut(Qt.Key.Key_K, self._stop_playback),
            self._shortcut(Qt.Key.Key_L, self._play_forward),
            self._shortcut(Qt.Key.Key_Comma, lambda: self._step_frame(-1)),
            self._shortcut(Qt.Key.Key_Period, lambda: self._step_frame(1)),
        ]

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget#quickLookPopup { background: transparent; }
            QFrame#quickLookPanel { background:#17191c; border:1px solid #4c5057; border-radius:10px; }
            QLabel#quickLookTitle { color:#f2f3f5; font-size:16px; font-weight:600; }
            QLabel#quickLookMeta { color:#aeb3bb; font-size:11px; }
            QLabel#quickLookHint { color:#777d86; font-size:10px; }
            QStackedWidget#quickLookMedia, QLabel#quickLookImage, QScrollArea#quickLookViewport {
                background:#08090a; color:#8f949b; border:0; border-radius:5px;
            }
            QFrame#quickLookControls { background:#24272b; border-radius:5px; }
            QPushButton#quickLookButton { background:#30343a; color:#f2f3f5; border:1px solid #4a4f57; border-radius:5px; padding:0; }
            QPushButton#quickLookButton:hover { background:#ff6b35; border-color:#ff6b35; }
            QSlider::groove:horizontal { height:5px; background:#4a4f57; border-radius:2px; }
            QSlider::handle:horizontal { background:#ff8357; width:13px; margin:-4px 0; border-radius:6px; }
        """)

    def show_media(self, media: QuickLookMedia, screen=None) -> None:
        self._entries = media.entries
        self.title_label.setText(media.title)
        # Prefer motion when it exists, matching the Shotbox popup.
        initial = next((i for i, entry in enumerate(self._entries) if entry.is_video), 0)
        self._show_entry(initial)
        if not self._session_visible:
            self._position_on_screen(screen)
        self._session_visible = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def navigate_media(self, direction: int) -> bool:
        target = self._entry_index + direction
        if direction not in (-1, 1) or not 0 <= target < len(self._entries):
            return False
        self._show_entry(target)
        return True

    def _show_entry(self, index: int) -> None:
        if not self._entries:
            self._entry_index = -1
            self._show_still(None, "No preview available")
            return
        self._entry_index = min(max(0, index), len(self._entries) - 1)
        entry = self._entries[self._entry_index]
        suffix = f"   {self._entry_index + 1}/{len(self._entries)}" if len(self._entries) > 1 else ""
        self.filename_label.setText(f"{entry.filename}{suffix}")
        self.media_view.reset_zoom()
        if entry.is_video and self.player is not None:
            self._show_video(entry.path)
        elif entry.is_video:
            still = next((item.path for item in self._entries if not item.is_video), None)
            self._show_still(still, "Video playback is unavailable")
        else:
            self._show_still(entry.path)

    def _show_still(self, path: Path | None, message: str = "") -> None:
        self._stop_reverse()
        self._is_video = False
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self.controls.hide()
        self.media_stack.setCurrentWidget(self.image_label)
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        self._original_pixmap = pixmap if not pixmap.isNull() else None
        self.image_label.setToolTip(message)
        if self._original_pixmap is None:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(message or "No preview available")
        else:
            self.image_label.clear()
            self._scale_still()

    def _show_video(self, path: Path) -> None:
        self._stop_reverse()
        self._is_video = True
        self.controls.show()
        self.position_slider.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")
        self.media_stack.setCurrentWidget(self.video_widget)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _position_on_screen(self, screen) -> None:
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        scale = self._screen_percentage / 100.0
        width = min(available.width(), max(self.minimumWidth(), round(available.width() * scale)))
        height = min(available.height(), max(self.minimumHeight(), round(available.height() * scale)))
        self.resize(width, height)
        self.move(available.x() + (available.width() - width) // 2, available.y() + (available.height() - height) // 2)

    def _frame_ms(self) -> int:
        frame_rate = 25.0
        if self.player is not None and QMediaMetaData is not None:
            try:
                value = float(self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate))
                if 1 <= value <= 240:
                    frame_rate = value
            except (AttributeError, TypeError, ValueError):
                pass
        return max(1, round(1000 / frame_rate))

    def _play_forward(self) -> None:
        if self._is_video and self.player is not None:
            self._stop_reverse()
            self.player.play()

    def _play_reverse(self) -> None:
        if not self._is_video or self.player is None:
            return
        self.player.pause()
        self._reverse_playing = True
        self._reverse_tick()
        if self._reverse_playing:
            self._reverse_timer.start(self._frame_ms())

    def _stop_playback(self) -> None:
        self._stop_reverse()
        if self._is_video and self.player is not None:
            self.player.pause()

    def _stop_reverse(self) -> None:
        self._reverse_timer.stop()
        self._reverse_playing = False

    def _reverse_tick(self) -> None:
        if not self._reverse_playing or not self._set_frame_position(-1):
            self._stop_playback()

    def _step_frame(self, direction: int) -> None:
        if self._is_video and self.player is not None:
            self._stop_playback()
            self._set_frame_position(direction)

    def _set_frame_position(self, direction: int) -> bool:
        if self.player is None:
            return False
        current = max(0, self.player.position())
        duration = max(0, self.player.duration())
        target = max(0, current + direction * self._frame_ms())
        if duration:
            target = min(duration, target)
        self.player.setPosition(target)
        return target != current

    def _toggle_playback(self) -> None:
        if not self._is_video or self.player is None:
            return
        if self._reverse_playing or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_playback()
        else:
            self._play_forward()

    def _toggle_mute(self) -> None:
        if self.audio_output is not None:
            muted = not self.audio_output.isMuted()
            self.audio_output.setMuted(muted)
            self.mute_button.setText("🔇" if muted else "🔊")

    def _slider_pressed(self) -> None:
        self._slider_dragging = True
        self._resume_after_scrub = bool(
            self.player is not None
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if self.player is not None:
            self.player.pause()

    def _slider_released(self) -> None:
        if self.player is not None:
            self.player.setPosition(self.position_slider.value())
            if self._resume_after_scrub:
                self.player.play()
        self._slider_dragging = False
        self._resume_after_scrub = False

    def _slider_moved(self, position: int) -> None:
        if self.player is not None:
            self.player.setPosition(position)
            self.time_label.setText(f"{self._format_time(position)} / {self._format_time(self.player.duration())}")

    def _position_changed(self, position: int) -> None:
        if not self._slider_dragging:
            self.position_slider.setValue(position)
            duration = self.player.duration() if self.player is not None else 0
            self.time_label.setText(f"{self._format_time(position)} / {self._format_time(duration)}")

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))

    def _playback_state_changed(self, state) -> None:
        if QMediaPlayer is not None:
            playing = state == QMediaPlayer.PlaybackState.PlayingState
            self.play_button.setText("❚❚" if playing else "▶")

    def _player_error(self, *_args) -> None:
        if self._is_video:
            still = next((item.path for item in self._entries if not item.is_video), None)
            self._show_still(still, "Video unavailable; showing still preview")

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        seconds = max(0, int(milliseconds)) // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _scale_still(self) -> None:
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        self.image_label.setPixmap(self._original_pixmap.scaled(
            self.image_label.contentsRect().size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_resize_grip()
        if not self._is_video:
            self._scale_still()

    def _position_resize_grip(self) -> None:
        self.resize_grip.move(
            max(0, self.panel.width() - self.resize_grip.width() - 3),
            max(0, self.panel.height() - self.resize_grip.height() - 3),
        )
        self.resize_grip.raise_()

    def hideEvent(self, event) -> None:
        emit_dismissed = self._session_visible
        self._session_visible = False
        self._stop_reverse()
        if self.player is not None:
            self.player.pause()
        super().hideEvent(event)
        if emit_dismissed:
            self.dismissed.emit()


class AssetQuickLookController(QObject):
    """Connect the asset list selection to a reusable Quick Look popup."""

    def __init__(
        self,
        view: QListView,
        asset_role: int,
        selection_callback: Callable[[object], None] | None = None,
        popup_factory: Callable[[], QuickLookPopup] | None = None,
    ) -> None:
        super().__init__(view)
        self.view = view
        self.asset_role = asset_role
        self.selection_callback = selection_callback
        self._popup_factory = popup_factory or (lambda: QuickLookPopup(view.window()))
        self._popup: QuickLookPopup | None = None
        self._active_row = -1
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)

    @property
    def is_open(self) -> bool:
        return bool(self._popup is not None and self._popup.isVisible())

    def toggle(self) -> bool:
        if self.is_open:
            self._popup.hide()
            return True
        return self.open_current()

    def open_current(self) -> bool:
        index = self.view.currentIndex()
        if not index.isValid():
            selected = self.view.selectionModel().selectedIndexes()
            index = selected[0] if selected else self.view.model().index(0, 0)
        return self.open_index(index)

    def open_index(self, index) -> bool:
        if not index.isValid():
            return False
        media = quick_look_media_for_asset(index.data(self.asset_role))
        if not media.is_previewable:
            return False
        self._active_row = index.row()
        self._select(index)
        popup = self._ensure_popup()
        centre = self.view.viewport().mapToGlobal(self.view.visualRect(index).center())
        popup.show_media(media, QApplication.screenAt(centre) or self.view.screen())
        return True

    def navigate(self, direction: int) -> bool:
        if not self.is_open or direction not in (-1, 1):
            return False
        model = self.view.model()
        row = self._active_row + direction
        while 0 <= row < model.rowCount():
            index = model.index(row, 0)
            if quick_look_media_for_asset(index.data(self.asset_role)).is_previewable:
                return self.open_index(index)
            row += direction
        return False

    def dismiss(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Space
            and not event.isAutoRepeat()
        ):
            if self.toggle():
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _select(self, index) -> None:
        self.view.selectionModel().setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        self.view.scrollTo(index, QListView.ScrollHint.EnsureVisible)
        if self.selection_callback is not None:
            self.selection_callback(index)

    def _ensure_popup(self) -> QuickLookPopup:
        if self._popup is None:
            self._popup = self._popup_factory()
            self._popup.navigation_requested.connect(self.navigate)
        return self._popup
