from PyQt6.QtCore import QSettings, QSize, QTimer
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from universal_asset_library.settings import AppSettings, SettingsStore

from .assets_tab import AssetsTab
from .importer_tab import ImporterTab
from .settings_tab import SettingsTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ShotBox Assets")
        self.setMinimumSize(QSize(960, 680))
        self.settings_store = SettingsStore()
        self._maintenance_inspection_scheduled = False
        current_settings = self.settings_store.load()
        self.assets_tab = AssetsTab()
        self.importer_tab = ImporterTab()
        self.settings_tab = SettingsTab(self.settings_store, current_settings)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.assets_tab, "Assets")
        self.tabs.addTab(self.importer_tab, "Importer")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.setCentralWidget(self.tabs)
        self.settings_tab.settings_saved.connect(self._apply_settings)
        self.settings_tab.library_repaired.connect(self._library_repaired)
        self.settings_tab.library_updated.connect(self._library_updated)
        self.settings_tab.houdini_bridge_changed.connect(self.assets_tab.refresh_houdini_sessions)
        self.settings_tab.blender_bridge_changed.connect(self.assets_tab.refresh_blender_sessions)
        self.assets_tab.open_settings_requested.connect(lambda: self.tabs.setCurrentWidget(self.settings_tab))
        self.assets_tab.material_updated.connect(self._material_updated)
        self.assets_tab.library_mutation_busy_changed.connect(
            self._library_mutation_busy_changed
        )
        self.importer_tab.import_completed.connect(self._imports_completed)
        self._apply_settings(current_settings)
        settings = QSettings()
        geometry = settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 820)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._maintenance_inspection_scheduled:
            self._maintenance_inspection_scheduled = True
            QTimer.singleShot(
                1000, self.settings_tab.start_initial_maintenance_inspection
            )

    def closeEvent(self, event) -> None:
        if self.assets_tab.metadata_update_active:
            QMessageBox.information(
                self,
                "Asset update in progress",
                "Please wait for the current asset move or metadata update to finish.",
            )
            event.ignore()
            return
        QSettings().setValue("window/geometry", self.saveGeometry())
        self.settings_tab.shutdown_maintenance()
        self.assets_tab.shutdown_preview_queue()
        self.assets_tab.shutdown_catalog()
        self.assets_tab.shutdown_ai()
        super().closeEvent(event)

    def _apply_settings(self, settings: AppSettings) -> None:
        self.assets_tab.set_thumbnail_size(settings.thumbnail_size)
        self.assets_tab.set_stock_hover_previews(settings.stock_hover_previews)
        self.assets_tab.load_library(settings.library_path)
        self.importer_tab.set_library_path(settings.library_path)
        self.importer_tab.set_default_category(settings.default_import_category)
        self.importer_tab.set_default_model_category(settings.default_model_category)
        self.importer_tab.set_preview_settings(
            settings.blender_path,
            settings.render_hdri_on_import,
            settings.render_texture_on_import,
            settings.save_texture_preview_blend,
        )
        self.importer_tab.set_stock_preview_settings(settings.ffmpeg_path)
        self.assets_tab.set_hdri_preview_settings(
            settings.blender_path,
            settings.save_texture_preview_blend,
            settings.render_hdri_on_import,
            settings.render_texture_on_import,
        )
        library = settings.library_path or "not configured"
        self.statusBar().showMessage(f"Texture, Atlas, HDRI, model, VDB, and Stock library · Library: {library}")

    def _imports_completed(self, summary) -> None:
        if summary.imported:
            self.assets_tab.apply_asset_updates(summary.imported)
            self.assets_tab.show_section(summary.imported[0].asset_type)
            queued = self.assets_tab.queue_import_previews(
                summary.imported
            )
            if queued:
                self.statusBar().showMessage(
                    f"Imported {len(summary.imported)} asset(s); "
                    f"queued {queued} preview render(s).",
                    8000,
                )
        else:
            self.assets_tab.refresh_catalog()
        self.tabs.setCurrentWidget(self.assets_tab)

    def _library_repaired(self, summary) -> None:
        self.assets_tab.apply_asset_updates(summary.renamed)
        self.assets_tab.refresh_catalog()
        self.statusBar().showMessage(f"Renamed {len(summary.renamed)} library asset(s) with human-readable filenames.", 8000)

    def _library_updated(self, summary) -> None:
        self.assets_tab.apply_asset_updates(summary.updated)
        self.assets_tab.refresh_catalog()
        self.statusBar().showMessage(f"Updated {len(summary.updated)} library asset(s) and refreshed the catalog.", 8000)

    def _material_updated(self, asset) -> None:
        self.statusBar().showMessage(f"Updated material metadata: {asset.name}", 6000)

    def _library_mutation_busy_changed(self, active: bool) -> None:
        for widget in (self.importer_tab, self.settings_tab):
            index = self.tabs.indexOf(widget)
            self.tabs.setTabEnabled(index, not active)
            self.tabs.setTabToolTip(
                index,
                (
                    "Library-writing actions are paused while asset updates run."
                    if active else ""
                ),
            )
        if active:
            self.statusBar().showMessage(
                "Asset updates are running in the background; browsing and exports remain available."
            )
