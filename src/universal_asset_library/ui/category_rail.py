from __future__ import annotations

from importlib.resources import files

from PyQt6.QtCore import QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.categories import GENERIC_ICON_ID, ICON_SHAPES


class CategoryRail(QFrame):
    category_changed = pyqtSignal(str)
    COLLAPSED_WIDTH = 52
    EXPANDED_WIDTH = 190

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("categoryRail")
        self._expanded = _setting_bool(QSettings().value("assets/category_rail_expanded", False))
        self._category = "All"
        self._buttons: dict[str, QToolButton] = {}
        self._counts: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(4)
        self.toggle = QToolButton()
        self.toggle.setObjectName("categoryRailToggle")
        self.toggle.setToolTip("Expand category sidebar")
        self.toggle.clicked.connect(self.toggle_expanded)
        root.addWidget(self.toggle)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("categoryRailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("categoryRailContent")
        self.items = QVBoxLayout(self.content)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.items.setSpacing(4)
        self.items.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.buttonClicked.connect(self._button_clicked)
        self._apply_expanded()

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def current_category(self) -> str:
        return self._category

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        QSettings().setValue("assets/category_rail_expanded", self._expanded)
        self._apply_expanded()
        self._refresh_button_text()

    def set_categories(
        self,
        categories: tuple[str, ...] | list[str],
        icon_ids: dict[str, str],
        counts: dict[str, int],
        *,
        selected: str = "All",
    ) -> None:
        while self.items.count() > 1:
            item = self.items.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self.group.removeButton(widget)
                widget.deleteLater()
        self._buttons.clear()
        self._counts = dict(counts)
        entries = ("All", *tuple(categories))
        for name in entries:
            button = QToolButton()
            button.setObjectName("categoryRailButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("category", name)
            icon_id = "all" if name == "All" else icon_ids.get(name, GENERIC_ICON_ID)
            button.setIcon(_category_icon(icon_id))
            button.setIconSize(QSize(22, 22))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if self._expanded else Qt.ToolButtonStyle.ToolButtonIconOnly
            )
            button.setMinimumHeight(40)
            self.group.addButton(button)
            self.items.insertWidget(self.items.count() - 1, button)
            self._buttons[name] = button
        self._refresh_button_text()
        self.set_current(selected if selected in self._buttons else "All", emit=False)

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)
        self._refresh_button_text()

    def set_current(self, category: str, *, emit: bool = False) -> None:
        value = category if category in self._buttons else "All"
        changed = value != self._category
        self._category = value
        button = self._buttons.get(value)
        if button is not None:
            button.setChecked(True)
        if emit and changed:
            self.category_changed.emit(value)

    def _button_clicked(self, button: QToolButton) -> None:
        self.set_current(str(button.property("category")), emit=True)

    def _apply_expanded(self) -> None:
        width = self.EXPANDED_WIDTH if self._expanded else self.COLLAPSED_WIDTH
        self.setFixedWidth(width)
        self.toggle.setText("‹" if self._expanded else "›")
        self.toggle.setToolTip(
            "Collapse category sidebar" if self._expanded else "Expand category sidebar"
        )

    def _refresh_button_text(self) -> None:
        for name, button in self._buttons.items():
            count = self._counts.get(name, 0)
            button.setText(f"{name}  {count}" if self._expanded else "")
            button.setToolTip(f"{name} · {count} matching asset{'s' if count != 1 else ''}")
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if self._expanded else Qt.ToolButtonStyle.ToolButtonIconOnly
            )


def _category_icon(icon_id: str) -> QIcon:
    shape = ICON_SHAPES.get(icon_id, ICON_SHAPES[GENERIC_ICON_ID])
    resource = files("universal_asset_library.ui").joinpath("icons", f"{shape}.svg")
    return QIcon(str(resource))


def _setting_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)
