from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabBar


class AssetTypeTabs(QTabBar):
    """Compact shared section switch used by the catalog and importer."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("assetTypeTabs")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.addTab("Textures")
        self.setTabData(0, "texture_set")
        self.addTab("Atlases")
        self.setTabData(1, "atlas")
        self.addTab("HDRIs")
        self.setTabData(2, "hdri")
        self.addTab("Models")
        self.setTabData(3, "model")
        self.addTab("Stock")
        self.setTabData(4, "stock")
        self.currentChanged.connect(self.currentIndexChanged.emit)

    def currentData(self):
        return self.tabData(self.currentIndex())

    def findData(self, value) -> int:
        for index in range(self.count()):
            if self.tabData(index) == value:
                return index
        return -1
