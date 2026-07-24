from __future__ import annotations

import sys

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from .app import HdriTaggerWindow


def main() -> int:
    QCoreApplication.setOrganizationName("ShotBox")
    QCoreApplication.setApplicationName("Asset AI Tagger Prototype")
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    window = HdriTaggerWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
