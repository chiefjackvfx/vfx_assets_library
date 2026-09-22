from dataclasses import replace
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings, QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from universal_asset_library.library.polyhaven_sync import (
    PolyHavenSyncReview,
    PolyHavenSyncResult,
    plan_entry,
)
from universal_asset_library.settings import AppSettings, SettingsStore
from universal_asset_library.ui.polyhaven_panel import PolyHavenPanel
from universal_asset_library.ui.settings_tab import SettingsTab
from universal_asset_library.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def entry():
    return plan_entry(
        "test_sky",
        {"name": "Test Sky"},
        {
            "hdri": {
                "4k": {
                    "exr": {
                        "url": "https://dl.polyhaven.org/test_sky_4k.exr",
                        "size": 1024,
                        "md5": "",
                    }
                }
            }
        },
        "hdri",
        "4K",
    )


def wait_until(app, predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert predicate()


def test_selection_totals_ambiguity_and_invalidation(app, tmp_path, entry):
    panel = PolyHavenPanel()
    panel.set_context(AppSettings(library_path=str(tmp_path)), str(tmp_path))
    panel._show_review(
        PolyHavenSyncReview(
            entries=[
                entry,
                replace(entry, slug="ambiguous", possible_matches=("Older sky",)),
            ]
        )
    )
    assert panel.selected_entries() == [entry]
    assert "1 selected" in panel.totals.text()
    assert "1.0 KiB" in panel.totals.text()
    assert panel.download_button.isEnabled()
    panel._select(True)
    assert len(panel.selected_entries()) == 2
    panel._select(False)
    assert not panel.download_button.isEnabled()
    panel.resolution_combos["hdri"].setCurrentText("8K")
    assert not panel._review.entries
    panel._show_review(PolyHavenSyncReview(entries=[entry]))
    panel.set_context(
        AppSettings(library_path=str(tmp_path)), str(tmp_path / "unsaved")
    )
    assert not panel._review.entries
    assert not panel.check_button.isEnabled()


def test_settings_save_preferences_and_busy_controls(app, tmp_path):
    store = SettingsStore(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    settings = store.save(AppSettings(library_path=str(tmp_path)))
    widget = SettingsTab(store, settings)
    panel = widget.polyhaven_panel
    panel.resolution_combos["model"].setCurrentText("8K")
    assert widget.save_button.isEnabled()
    widget._save()
    assert store.load().polyhaven.model_resolution == "8K"
    # Complete the read-only maintenance audit started by Save.
    wait_until(app, lambda: widget._inspection_worker is None)
    panel._worker = object()
    widget._polyhaven_busy_changed(True)
    assert not widget.library_path.isEnabled()
    assert not widget.update_library_button.isEnabled()
    assert not widget.save_button.isEnabled()
    panel._worker = None
    widget._polyhaven_busy_changed(False)
    wait_until(app, lambda: widget._inspection_worker is None)
    assert widget.library_path.isEnabled()
    widget.shutdown_maintenance()


def test_worker_background_cancel_and_retry(app, tmp_path, entry, monkeypatch):
    entered = Event()

    class Service:
        def __init__(self, repository, *, token, **kwargs):
            self.token = token

        def discover(self, preferences, progress):
            entered.set()
            while not self.token.cancelled:
                self.token._event.wait(0.01)
            return PolyHavenSyncReview(canceled=True)

        def install(self, entries, progress):
            return PolyHavenSyncResult(failed={entries[0].name: "offline"})

    monkeypatch.setattr(
        "universal_asset_library.ui.polyhaven_panel.PolyHavenSyncService", Service
    )
    panel = PolyHavenPanel()
    panel.set_context(AppSettings(library_path=str(tmp_path)), str(tmp_path))
    changes = []
    panel.busy_changed.connect(changes.append)
    panel.check()
    wait_until(app, entered.is_set)
    assert panel.busy and panel.cancel_button.isEnabled()
    assert not panel.check_button.isEnabled()
    panel.cancel()
    wait_until(app, lambda: not panel.busy)
    assert changes == [True, False]
    assert "canceled" in panel.status.text().lower()
    panel._show_review(PolyHavenSyncReview(entries=[entry]))
    results = []
    panel.imported.connect(results.append)
    panel.download()
    wait_until(app, lambda: not panel.busy)
    assert results[0].failed == {"Test Sky": "offline"}
    assert panel.download_button.isEnabled()
    assert "offline" in panel.report.toPlainText()
    QThreadPool.globalInstance().waitForDone(1000)


def test_external_busy_blocks_check(app, tmp_path):
    panel = PolyHavenPanel()
    panel.set_context(AppSettings(library_path=str(tmp_path)), str(tmp_path))
    assert panel.check_button.isEnabled()
    panel.set_external_blocked(True)
    panel.set_blocked(False)
    assert not panel.check_button.isEnabled()
    panel.set_external_blocked(False)
    assert panel.check_button.isEnabled()


def test_main_window_cancels_sync_before_close_and_refreshes_imports(
    app, tmp_path, monkeypatch
):
    store = SettingsStore(
        QSettings(str(tmp_path / "main.ini"), QSettings.Format.IniFormat)
    )
    monkeypatch.setattr(
        "universal_asset_library.ui.main_window.SettingsStore", lambda: store
    )
    window = MainWindow()
    panel = window.settings_tab.polyhaven_panel
    panel.set_context(AppSettings(library_path=str(tmp_path)), str(tmp_path))
    queued = []
    monkeypatch.setattr(QThreadPool.globalInstance(), "start", queued.append)
    panel.check()
    assert panel.busy and not window.importer_tab.isEnabled()
    assert window.assets_tab.metadata_update_active
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and panel._token.cancelled
    panel._finished(PolyHavenSyncReview(canceled=True))
    assert window.importer_tab.isEnabled()
    assert not window.assets_tab.metadata_update_active
    app.processEvents()

    changes = []
    monkeypatch.setattr(
        window.assets_tab, "apply_asset_updates", lambda values: changes.append(values)
    )
    monkeypatch.setattr(window.assets_tab, "queue_import_previews", lambda values: None)
    monkeypatch.setattr(
        window.assets_tab, "refresh_catalog", lambda: changes.append("refresh")
    )
    window._polyhaven_imported(SimpleNamespace(imported=["new asset"]))
    assert changes == [["new asset"], "refresh"]
