from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QListView

from universal_asset_library.ui.assets_tab import ASSET_ROLE, TextureListModel
from universal_asset_library.ui.quick_look import (
    AssetQuickLookController,
    QuickLookPopup,
    quick_look_media_for_asset,
)


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _asset(tmp_path: Path, asset_id: str, *, still=True, video=False):
    still_path = tmp_path / f"{asset_id}.jpg"
    if still:
        image = QImage(64, 32, QImage.Format.Format_RGB32)
        image.fill(QColor("#6f777a"))
        assert image.save(str(still_path))
    video_path = tmp_path / f"{asset_id}.mp4"
    if video:
        video_path.write_bytes(b"preview")
    return SimpleNamespace(
        id=asset_id,
        name=asset_id.title(),
        hero_path=still_path,
        thumbnail_path=still_path,
        preview_path=video_path,
    )


def test_asset_media_prefers_one_still_and_adds_motion_preview(app, tmp_path):
    media = quick_look_media_for_asset(_asset(tmp_path, "cloud", video=True))

    assert media.title == "Cloud"
    assert [entry.filename for entry in media.entries] == ["cloud.jpg", "cloud.mp4"]
    assert [entry.is_video for entry in media.entries] == [False, True]


def test_asset_media_ignores_missing_previews(app, tmp_path):
    media = quick_look_media_for_asset(_asset(tmp_path, "missing", still=False))

    assert not media.is_previewable


class FakePopup(QObject):
    navigation_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.visible = False
        self.shown = []

    def isVisible(self):
        return self.visible

    def show_media(self, media, screen=None):
        self.visible = True
        self.shown.append(media.title)

    def hide(self):
        self.visible = False


def test_controller_opens_selection_and_navigates_visible_assets(app, tmp_path):
    assets = [_asset(tmp_path, "one"), _asset(tmp_path, "skip", still=False), _asset(tmp_path, "three")]
    view = QListView()
    view.setModel(TextureListModel(assets, view))
    view.setCurrentIndex(view.model().index(0, 0))
    popup = FakePopup()
    selected = []
    controller = AssetQuickLookController(
        view,
        ASSET_ROLE,
        selection_callback=lambda index: selected.append(index.row()),
        popup_factory=lambda: popup,
    )

    assert controller.open_current()
    assert popup.shown == ["One"]
    assert controller.navigate(1)
    assert popup.shown == ["One", "Three"]
    assert selected == [0, 2]
    assert view.currentIndex().row() == 2
    assert [index.row() for index in view.selectedIndexes()] == [2]


def test_space_toggles_quick_look_for_focused_asset_grid(app, tmp_path):
    view = QListView()
    view.setModel(TextureListModel([_asset(tmp_path, "space")], view))
    view.setCurrentIndex(view.model().index(0, 0))
    popup = FakePopup()
    controller = AssetQuickLookController(
        view, ASSET_ROLE, popup_factory=lambda: popup
    )
    view.show()
    view.setFocus()

    QTest.keyClick(view, Qt.Key.Key_Space)
    app.processEvents()
    assert controller.is_open

    QTest.keyClick(view, Qt.Key.Key_Space)
    app.processEvents()
    assert not controller.is_open


def test_popup_formats_short_and_long_durations():
    assert QuickLookPopup._format_time(65_000) == "01:05"
    assert QuickLookPopup._format_time(3_665_000) == "1:01:05"
