from __future__ import annotations

import sys

from PyQt6.QtCore import QCoreApplication, QSettings
from PyQt6.QtWidgets import QApplication

from .ui import MainWindow
from .ui.theme import APP_STYLESHEET


def create_application(argv: list[str] | None = None) -> QApplication:
    QCoreApplication.setOrganizationName("ShotBox")
    QCoreApplication.setApplicationName("ShotBox Assets")
    app = QApplication(argv if argv is not None else sys.argv)
    _migrate_legacy_settings()
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    return app


def _migrate_legacy_settings() -> None:
    """Preserve user preferences from the application's former product name."""
    current = QSettings()
    legacy = QSettings("UniversalAssetLibrary", "Universal Asset Library")
    changed = False
    for key in legacy.allKeys():
        if not current.contains(key):
            current.setValue(key, legacy.value(key))
            changed = True
    if changed:
        current.sync()


def main() -> int:
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
