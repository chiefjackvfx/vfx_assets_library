from __future__ import annotations

import html
import re
import traceback
from pathlib import Path

from PyQt6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QPersistentModelIndex, QRect, QRectF, QRunnable, QSettings, QSize, Qt, QSortFilterProxyModel, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QLayoutItem,
    QListView,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from universal_asset_library.domain import (
    ATLAS_CATEGORIES,
    HDRI_CATEGORIES,
    LibraryHdriAsset,
    LibraryModelAsset,
    LibraryStockAsset,
    LibraryTextureAsset,
    MODEL_CATEGORIES,
    PBR_CHANNELS,
    STOCK_CATEGORIES,
    TEXTURE_CATEGORIES,
)
from universal_asset_library.library import (
    AssetMetadataUpdate,
    CancelToken,
    CategoryConfigStore,
    LibraryRepository,
    ModelAssetRescan,
    ModelRescanSelection,
    PolyHavenDownloadPlan,
    PolyHavenOptions,
)
from universal_asset_library.categories import CategoryCatalog, default_category_catalog
from universal_asset_library.integrations.houdini import (
    BridgeResponse,
    HoudiniBridgeClient,
    HoudiniBridgeError,
    HoudiniSession,
    choose_hdri_file,
)
from universal_asset_library.integrations.blender import (
    BlenderBridgeClient,
    BlenderBridgeError,
    BlenderBridgeResponse,
    BlenderSession,
)
from universal_asset_library.integrations import (
    default_texture_resolution,
    model_conversion_sources,
    model_export_label,
    model_export_options,
    prepare_model_conversion,
    prepare_model_export,
    prepare_texture_export,
    validate_model_conversion_blender,
)
from .asset_type_tabs import AssetTypeTabs
from .category_rail import CategoryRail


ASSET_ROLE = int(Qt.ItemDataRole.UserRole) + 1

CHANNEL_COLORS = {
    "Base Color": "#e8a45f",
    "Roughness": "#9ca6b4",
    "Normal": "#8c80e8",
    "Displacement": "#6fc2d0",
    "Height": "#6fc2d0",
    "Bump": "#6fc2d0",
    "Metalness": "#d9dde3",
    "Ambient Occlusion": "#778190",
    "Opacity": "#69c995",
    "Emission": "#efcb62",
    "Translucency": "#ef89a7",
    "Specular": "#b5c7db",
    "Glossiness": "#b8c2cf",
    "Cavity": "#697586",
    "Packed ARM": "#d295df",
    "Packed": "#d295df",
}
DEFAULT_CHANNEL_COLOR = "#8793a2"
HDRI_FORMAT_COLORS = {"HDR": "#e8a45f", "EXR": "#8ac4f4"}


class FlowLayout(QLayout):
    """Small wrapping layout used by the tag-chip editor."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, test_only: bool) -> int:
        x, y = rect.x(), rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class TagEditor(QFrame):
    tags_changed = pyqtSignal()

    def __init__(
        self,
        tags: tuple[str, ...] = (),
        parent: QWidget | None = None,
        *,
        chip_prefix: str = "#",
        placeholder: str = "Type or paste tags…",
        helper_text: str = "Press Enter or comma to add · click a chip to remove",
        suggestions: tuple[str, ...] = (),
        add_button_text: str = "Add tag",
        empty_text: str = "No tags yet",
        item_name: str = "tag",
    ) -> None:
        super().__init__(parent)
        self._chip_prefix = chip_prefix
        self._empty_text = empty_text
        self._item_name = item_name
        self.setObjectName("tagEditor")
        self.setStyleSheet(
            "QFrame#tagEditor { background:#1E2228; border:1px solid #2F3542; border-radius:2px; }"
            "QPushButton#tagChip { background:#2F3542; border:1px solid #404754; border-radius:2px; "
            "padding:3px 8px; color:#D4D4D4; font-weight:bold; }"
            "QPushButton#tagChip:hover { background:#404754; border-color:#FF6B35; color:#FFFFFF; }"
        )
        self._tags: list[str] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 7)
        root.setSpacing(7)
        self.chips = QWidget()
        self.flow = FlowLayout(self.chips)
        self.chip_scroll = QScrollArea()
        self.chip_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chip_scroll.setWidgetResizable(True)
        self.chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chip_scroll.setWidget(self.chips)
        self.chip_scroll.setFixedHeight(30)
        self.empty = QLabel(empty_text)
        self.empty.setObjectName("mutedLabel")
        self.flow.addWidget(self.empty)
        root.addWidget(self.chip_scroll)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self._completer: QCompleter | None = None
        self.set_suggestions(suggestions)
        self.input.returnPressed.connect(self.commit_input)
        self.input.textChanged.connect(self._commit_delimited_input)
        self.input.installEventFilter(self)
        self.add_button = QPushButton(add_button_text)
        self.add_button.clicked.connect(self.commit_input)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.add_button)
        root.addLayout(input_row)
        helper = QLabel(helper_text)
        helper.setObjectName("mutedLabel")
        root.addWidget(helper)
        self.set_tags(tags)

    def tags(self) -> tuple[str, ...]:
        return tuple(self._tags)

    def set_tags(self, tags: tuple[str, ...] | list[str]) -> None:
        self._tags = []
        self._rebuild_chips()
        self.add_tags(tags, emit=False)

    def clear(self) -> None:
        self.input.clear()
        self.set_tags(())

    def set_suggestions(self, suggestions: tuple[str, ...] | list[str]) -> None:
        if self._completer is not None:
            self._completer.deleteLater()
        completer = QCompleter(list(suggestions), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.activated.connect(self._suggestion_chosen)
        self._completer = completer
        self.input.setCompleter(completer)

    def add_tags(self, values, emit: bool = True) -> None:
        existing = {tag.casefold() for tag in self._tags}
        changed = False
        for value in values:
            tag = re.sub(r"\s+", " ", str(value).strip().lstrip("#"))
            if not tag or tag.casefold() in existing:
                continue
            self._tags.append(tag)
            existing.add(tag.casefold())
            changed = True
        if changed:
            self._rebuild_chips()
            if emit:
                self.tags_changed.emit()

    def remove_tag(self, tag: str) -> None:
        lowered = tag.casefold()
        updated = [value for value in self._tags if value.casefold() != lowered]
        if updated == self._tags:
            return
        self._tags = updated
        self._rebuild_chips()
        self.tags_changed.emit()

    def commit_input(self) -> None:
        values = re.split(r"[,;\n]+", self.input.text())
        self.input.clear()
        self.add_tags(values)

    def _commit_delimited_input(self, value: str) -> None:
        if any(delimiter in value for delimiter in (",", ";", "\n")):
            self.commit_input()

    def _suggestion_chosen(self, value: str) -> None:
        self.input.clear()
        self.add_tags((value,))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Backspace and not self.input.text() and self._tags:
                self.remove_tag(self._tags[-1])
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_chip_height()

    def _rebuild_chips(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        if not self._tags:
            self.empty = QLabel(self._empty_text)
            self.empty.setObjectName("mutedLabel")
            self.flow.addWidget(self.empty)
        else:
            for tag in self._tags:
                chip = QPushButton(f"{self._chip_prefix}{tag}  ×")
                chip.setObjectName("tagChip")
                chip.setToolTip(f"Remove {self._item_name}: {tag}")
                chip.clicked.connect(lambda _checked=False, value=tag: self.remove_tag(value))
                self.flow.addWidget(chip)
        self.chips.updateGeometry()
        self._update_chip_height()
        QTimer.singleShot(0, self._update_chip_height)
        self.updateGeometry()

    def _update_chip_height(self) -> None:
        width = max(1, self.chip_scroll.viewport().width())
        desired = max(26, self.flow.heightForWidth(width))
        self.chips.setMinimumHeight(desired)
        self.chip_scroll.setFixedHeight(min(118, desired + 2))


AssetRecord = LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset


class MaterialEditDialog(QDialog):
    def __init__(
        self,
        asset: AssetRecord,
        parent: QWidget | None = None,
        category_suggestions: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit {asset.name}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        heading = QLabel("Edit Asset")
        heading.setObjectName("pageTitle")
        note = QLabel("Updates catalog metadata only. Managed payloads, previews, and provider JSON keep their current paths.")
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(note)
        form = QFormLayout()
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(12)
        self.name = QLineEdit(asset.name)
        self.category = QComboBox()
        self.category.setEditable(False)
        self.category.addItems(category_suggestions or (
                MODEL_CATEGORIES if isinstance(asset, LibraryModelAsset)
                else STOCK_CATEGORIES if isinstance(asset, LibraryStockAsset)
                else HDRI_CATEGORIES if isinstance(asset, LibraryHdriAsset)
                else ATLAS_CATEGORIES if asset.asset_type == "atlas"
                else TEXTURE_CATEGORIES
        ))
        self.category.setCurrentText(asset.category)
        self.tags = TagEditor(asset.tags)
        self.author = QLineEdit(asset.author)
        self.physical_size = QLineEdit(asset.physical_size)
        self.description = QTextEdit(asset.description)
        self.description.setMinimumHeight(110)
        form.addRow("Name", self.name)
        form.addRow("Category", self.category)
        form.addRow("Tags", self.tags)
        form.addRow("Author", self.author)
        form.addRow("Physical size", self.physical_size)
        form.addRow("Description", self.description)
        layout.addLayout(form)
        self.validation = QLabel()
        self.validation.setStyleSheet("color:#ef7d7d;")
        layout.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def metadata_update(self) -> AssetMetadataUpdate:
        return AssetMetadataUpdate(
            name=self.name.text().strip(),
            category=self.category.currentText(),
            tags=self.tags.tags(),
            author=self.author.text().strip(),
            description=self.description.toPlainText().strip(),
            physical_size=self.physical_size.text().strip(),
        )

    def _accept_if_valid(self) -> None:
        update = self.metadata_update()
        if not update.name:
            self.validation.setText("Asset name is required.")
            self.name.setFocus()
            return
        if not update.category:
            self.validation.setText("Choose an asset category.")
            self.category.setFocus()
            return
        self.accept()


class ModelAssetRescanDialog(QDialog):
    def __init__(
        self,
        asset: LibraryModelAsset,
        scan: ModelAssetRescan,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.asset = asset
        self.scan = scan
        self._actions: dict[str, QCheckBox] = {}
        self._preferred: dict[str, QRadioButton] = {}
        self.setWindowTitle(f"Rescan {asset.name}")
        self.setMinimumSize(860, 440)
        layout = QVBoxLayout(self)
        heading = QLabel("Review managed model-file changes")
        heading.setObjectName("pageTitle")
        note = QLabel(
            "Only checked changes will be registered. Imported and generated files "
            "are protected; manually added files keep their current names and locations."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(note)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Apply", "Preferred", "Status", "File", "Format", "Origin / LOD",
            "Validation / dependencies",
        ])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self._preferred_group = QButtonGroup(self)
        for item in scan.items:
            self._add_item(item)
        layout.addWidget(self.table, 1)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color:#e6b566;")
        if scan.warnings:
            self.warning.setText("\n".join(scan.warnings[:4]))
            self.warning.show()
        else:
            self.warning.hide()
        layout.addWidget(self.warning)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.validation.setStyleSheet("color:#ef7d7d;")
        layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Update asset"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._select_current_preferred()
        self._update_state()

    def _add_item(self, item) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        action = QCheckBox()
        if item.selectable and item.valid_for_apply:
            action.setChecked(item.status in {"new", "changed", "missing"})
            action.toggled.connect(self._update_state)
            self._actions[item.path] = action
        else:
            action.setEnabled(False)
        self.table.setCellWidget(row, 0, action)
        preferred = QRadioButton()
        can_prefer = item.status != "missing" and (
            item.status != "new" or item.valid_for_apply
        )
        preferred.setEnabled(can_prefer)
        preferred.toggled.connect(self._update_state)
        self._preferred_group.addButton(preferred)
        self._preferred[item.path] = preferred
        self.table.setCellWidget(row, 1, preferred)
        values = [
            item.status.replace("_", " ").title(),
            item.path,
            item.file_format,
            " · ".join(value for value in (item.origin.title(), item.lod, item.component) if value),
        ]
        for column, value in enumerate(values, start=2):
            self.table.setItem(row, column, QTableWidgetItem(value))
        validation = "Basic file checks"
        if item.validation:
            validation = (
                f"{'Valid' if item.validation.valid else 'Invalid'} · "
                f"{item.validation.mesh_count} meshes · "
                f"{item.validation.material_count} materials"
            )
            if item.validation.up_axis:
                validation += f" · {item.validation.up_axis}-up"
            if item.validation.dependencies:
                validation += f" · {len(item.validation.dependencies)} dependencies"
        if item.diagnostic:
            validation = item.diagnostic
        elif item.validation and item.validation.diagnostics:
            validation += " · " + "; ".join(item.validation.diagnostics)
        self.table.setItem(row, 6, QTableWidgetItem(validation))

    def _select_current_preferred(self) -> None:
        current = next(
            (
                item for item in self.scan.items
                if item.preferred and item.status != "missing"
            ),
            None,
        )
        if current and current.path in self._preferred:
            self._preferred[current.path].setChecked(True)

    def _selected_preferred(self) -> str:
        return next(
            (path for path, button in self._preferred.items() if button.isChecked()),
            "",
        )

    def _selected_actions(self) -> tuple[set[str], set[str], set[str]]:
        additions, refreshes, removals = set(), set(), set()
        for path, checkbox in self._actions.items():
            if not checkbox.isChecked():
                continue
            item = self.scan.item(path)
            if item.status == "new":
                additions.add(path)
            elif item.status == "changed":
                refreshes.add(path)
            elif item.status == "missing":
                removals.add(path)
        return additions, refreshes, removals

    def _update_state(self, *_args) -> None:
        additions, refreshes, removals = self._selected_actions()
        preferred = self._selected_preferred()
        for path, button in self._preferred.items():
            item = self.scan.item(path)
            enabled = item.status != "missing" and (
                item.status != "new" or path in additions
            )
            if path in removals:
                enabled = False
            button.setEnabled(enabled and item.valid_for_apply)
            if not button.isEnabled() and button.isChecked():
                button.setAutoExclusive(False)
                button.setChecked(False)
                button.setAutoExclusive(True)
                preferred = ""
        available = [
            item for item in self.scan.items
            if item.status != "missing"
            and item.path not in removals
            and (item.status != "new" or item.path in additions)
        ]
        has_usd = any(
            item.file_format in {"USD", "USDA", "USDC", "USDZ"}
            for item in available
        )
        chosen = self.scan.item(preferred) if preferred else None
        error = ""
        if not (additions or refreshes or removals):
            error = "Select at least one change to apply."
        elif not chosen or chosen.path in removals:
            error = "Choose the model file that should be preferred."
        elif has_usd and chosen.file_format not in {"USD", "USDA", "USDC", "USDZ"}:
            error = "Choose a USD-family preferred file while USD is available."
        self.validation.setText(error)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not error)

    @property
    def selection(self) -> ModelRescanSelection:
        additions, refreshes, removals = self._selected_actions()
        return ModelRescanSelection(
            tuple(sorted(additions)),
            tuple(sorted(refreshes)),
            tuple(sorted(removals)),
            self._selected_preferred(),
        )


class ModelConversionDialog(QDialog):
    def __init__(
        self,
        asset: LibraryModelAsset,
        library_path: str,
        blender_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.asset = asset
        self.library_path = library_path
        self.setWindowTitle(f"Convert {asset.name} to USD")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        heading = QLabel("Headless Blender USD conversion")
        heading.setObjectName("pageTitle")
        note = QLabel(
            "Blender imports the selected static model, constructs managed materials, "
            "and publishes a portable preferred USDC derivative."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(note)
        form = QFormLayout()
        self.source = QComboBox()
        for item in model_conversion_sources(asset):
            details = " · ".join(value for value in (item.file_format, item.lod, item.component) if value)
            self.source.addItem(f"{Path(item.path).name} · {details}", item.path)
        self.orientation = QComboBox()
        self.orientation.addItem("USD Interchange · −Z forward, Y up", "usd_interchange")
        self.orientation.addItem("Z-up · Y forward, Z up", "z_up")
        form.addRow("Source variant", self.source)
        form.addRow("Orientation", self.orientation)
        layout.addLayout(form)
        valid, diagnostic, _version = validate_model_conversion_blender(blender_path)
        self.availability = QLabel(diagnostic)
        self.availability.setWordWrap(True)
        self.availability.setStyleSheet(f"color:{'#78c995' if valid else '#ef7d7d'};")
        layout.addWidget(self.availability)
        self.estimate = QLabel()
        self.estimate.setObjectName("mutedLabel")
        layout.addWidget(self.estimate)
        self.validation = QLabel()
        self.validation.setStyleSheet("color:#ef7d7d;")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Rebuild USD" if asset.usd_derivative else "Convert to USD"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid and self.source.count() > 0)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        self.source.currentIndexChanged.connect(self._refresh_estimate)
        self.orientation.currentIndexChanged.connect(self._refresh_estimate)
        layout.addWidget(self.buttons)
        self._refresh_estimate()

    @property
    def source_path(self) -> str:
        return str(self.source.currentData() or "")

    @property
    def orientation_preset(self) -> str:
        return str(self.orientation.currentData() or "usd_interchange")

    def _refresh_estimate(self, *_args) -> None:
        try:
            request = prepare_model_conversion(
                self.asset, self.source_path, self.orientation_preset, self.library_path,
            )
        except Exception as error:
            self.estimate.setText("")
            self.validation.setText(str(error))
        else:
            self.validation.clear()
            self.estimate.setText(
                f"Estimated staging requirement: {_human_size(request.estimated_size)} plus safety space."
            )

    def _accept_if_valid(self) -> None:
        self._refresh_estimate()
        if not self.validation.text() and self.source_path:
            self.accept()


class TextureListModel(QAbstractListModel):
    def __init__(self, assets: list[AssetRecord] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.assets = assets or []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.assets)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self.assets):
            return None
        asset = self.assets[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return asset.name
        if role == ASSET_ROLE:
            return asset
        return None

    def replace(self, assets: list[AssetRecord]) -> None:
        self.beginResetModel()
        self.assets = assets
        self.endResetModel()


class TextureFilterModel(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.query = ""
        self.category = "All"
        self.channel = "All"
        self.sort_mode = "Name"
        self.category_catalog = default_category_catalog("texture_set")

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        index = self.sourceModel().index(source_row, 0, source_parent)
        asset = self.sourceModel().data(index, ASSET_ROLE)
        if not asset or not asset.matches(self.query, "All", self.channel):
            return False
        return (
            self.category == "All"
            or self.category.casefold() in {
                value.casefold()
                for value in _asset_filter_categories(asset, self.category_catalog)
            }
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a: AssetRecord = self.sourceModel().data(left, ASSET_ROLE)
        b: AssetRecord = self.sourceModel().data(right, ASSET_ROLE)
        if self.sort_mode == "Resolution":
            return _largest_resolution(a) < _largest_resolution(b)
        if self.sort_mode == "Duration":
            return getattr(getattr(a, "media_info", None), "duration", 0) < getattr(getattr(b, "media_info", None), "duration", 0)
        if self.sort_mode == "Import Date":
            return a.created_at < b.created_at
        if self.sort_mode == "Category":
            return (a.category.casefold(), a.name.casefold()) < (b.category.casefold(), b.name.casefold())
        return a.name.casefold() < b.name.casefold()

    def set_filters(self, query: str, category: str, channel: str) -> None:
        self.query, self.category, self.channel = query, category, channel
        self.invalidateFilter()

    def set_category_catalog(self, catalog: CategoryCatalog) -> None:
        self.category_catalog = catalog
        self.invalidateFilter()

    def set_sort_mode(self, mode: str) -> None:
        self.sort_mode = mode
        self.invalidate()
        self.sort(0)


class TextureCardDelegate(QStyledItemDelegate):
    PRESETS = {
        "small": (QSize(198, 210), 146),
        "medium": (QSize(238, 245), 180),
        "large": (QSize(286, 285), 218),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.card_size, self.preview_height = self.PRESETS["medium"]
        self._pixmaps: dict[str, QPixmap] = {}
        self._hover_asset_id = ""
        self._hover_pixmap = QPixmap()

    def set_thumbnail_size(self, size: str) -> None:
        self.card_size, self.preview_height = self.PRESETS.get(size, self.PRESETS["medium"])

    def clear_cache(self) -> None:
        self._pixmaps.clear()

    def set_hover_frame(self, asset_id: str, pixmap: QPixmap) -> None:
        self._hover_asset_id = asset_id
        self._hover_pixmap = pixmap

    def clear_hover_frame(self) -> None:
        self._hover_asset_id = ""
        self._hover_pixmap = QPixmap()

    def sizeHint(self, option, index) -> QSize:
        return self.card_size

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        asset: AssetRecord = index.data(ASSET_ROLE)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(option.rect.adjusted(4, 4, -4, -4))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor("#FF6B35" if selected else "#2F3542"), 2 if selected else 1))
        painter.setBrush(QColor("#23272D" if selected else "#1E2228"))
        painter.drawRoundedRect(outer, 5, 5)
        preview = QRectF(outer.left() + 1, outer.top() + 1, outer.width() - 2, self.preview_height)
        clip = QPainterPath()
        clip.addRoundedRect(preview, 4, 4)
        painter.save()
        painter.setClipPath(clip)
        pixmap = (
            self._hover_pixmap
            if asset.id == self._hover_asset_id and not self._hover_pixmap.isNull()
            else self._thumbnail(asset)
        )
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(preview.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = preview.left() + (preview.width() - scaled.width()) / 2
            y = preview.top() + (preview.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
        else:
            gradient = QLinearGradient(preview.topLeft(), preview.bottomRight())
            gradient.setColorAt(0, QColor(asset.palette[0]))
            gradient.setColorAt(1, QColor(asset.palette[1]))
            painter.fillRect(preview, gradient)
        painter.restore()

        badge_text = ("USD" if asset.usd_ready else (asset.preferred_model.file_format if asset.preferred_model else "MODEL")) if isinstance(asset, LibraryModelAsset) else asset.resolution or "—"
        badge_width = max(34, min(74, 12 + len(badge_text) * 6))
        badge = QRectF(preview.right() - badge_width - 8, preview.top() + 8, badge_width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 16, 21, 205))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QColor("#f2f5f8"))
        small = QFont(option.font)
        small.setPointSize(8)
        small.setBold(True)
        painter.setFont(small)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)

        name_font = QFont(option.font)
        name_font.setPointSize(10)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor("#f1f4f7"))
        painter.drawText(
            QRectF(outer.left() + 10, preview.bottom() + 9, outer.width() - 20, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            asset.name,
        )
        painter.setFont(small)
        painter.setPen(QColor("#8793a2"))
        meta = (
            f"{asset.category}  ·  {'USD Ready' if asset.usd_ready else asset.file_format}"
            if isinstance(asset, LibraryModelAsset) else
            f"{asset.category}  ·  {asset.duration_label} · {'Alpha' if asset.media_info.alpha == 'yes' else asset.resolution}"
            if isinstance(asset, LibraryStockAsset) else
            f"{asset.category}  ·  {asset.resolution or asset.file_format}"
        )
        painter.drawText(
            QRectF(outer.left() + 10, preview.bottom() + 30, outer.width() - 20, 17),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            meta,
        )
        if selected:
            marker = QRectF(preview.left() + 8, preview.top() + 8, 18, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FF6B35"))
            painter.drawEllipse(marker)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.7))
            painter.drawLine(
                int(marker.left() + 5), int(marker.center().y()),
                int(marker.left() + 8), int(marker.bottom() - 5),
            )
            painter.drawLine(
                int(marker.left() + 8), int(marker.bottom() - 5),
                int(marker.right() - 4), int(marker.top() + 5),
            )
        painter.restore()

    def _thumbnail(self, asset: AssetRecord) -> QPixmap | None:
        if not asset.thumbnail_path:
            return None
        key = str(asset.thumbnail_path)
        if key not in self._pixmaps:
            self._pixmaps[key] = QPixmap(key)
        return self._pixmaps[key]


class StockHoverPreviewController(QObject):
    """Own one muted decoder and paints its frames through the card delegate."""

    def __init__(
        self,
        view: QListView,
        delegate: TextureCardDelegate,
        *,
        delay_ms: int = 0,
    ) -> None:
        super().__init__(view)
        self.view = view
        self._viewport = view.viewport()
        self.delegate = delegate
        self.delay_ms = delay_ms
        self.enabled = True
        self.suspended = False
        self._pending_index = QPersistentModelIndex()
        self._active_index = QPersistentModelIndex()
        self._active_asset_id = ""
        self._failed_asset_ids: set[str] = set()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._start_pending)
        self.player = QMediaPlayer(self)
        self.video_sink = QVideoSink(self)
        self.player.setVideoOutput(self.video_sink)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.video_sink.videoFrameChanged.connect(self._frame_changed)
        self.player.errorOccurred.connect(self._playback_failed)

        self.view.setMouseTracking(True)
        self._viewport.setMouseTracking(True)
        self._viewport.installEventFilter(self)
        self.view.entered.connect(self.schedule)
        self.view.viewportEntered.connect(self.stop)
        self.view.verticalScrollBar().valueChanged.connect(self.stop)
        self.view.horizontalScrollBar().valueChanged.connect(self.stop)
        if self.view.selectionModel() is not None:
            self.view.selectionModel().currentChanged.connect(self.stop)
        model = self.view.model()
        if model is not None:
            model.modelAboutToBeReset.connect(self.stop)
            model.layoutAboutToBeChanged.connect(self.stop)
            model.rowsAboutToBeRemoved.connect(self.stop)
        application = QApplication.instance()
        if application is not None:
            application.applicationStateChanged.connect(self._application_state_changed)

    @property
    def active_asset_id(self) -> str:
        return self._active_asset_id

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()

    def set_suspended(self, suspended: bool) -> None:
        self.suspended = bool(suspended)
        if self.suspended:
            self.stop()

    def schedule(self, index: QModelIndex) -> None:
        asset = index.data(ASSET_ROLE) if index.isValid() else None
        if (
            not self.enabled
            or self.suspended
            or not isinstance(asset, LibraryStockAsset)
            or asset.id in self._failed_asset_ids
            or not asset.preview_path.is_file()
        ):
            self.stop()
            return
        if asset.id == self._active_asset_id:
            return
        self.stop()
        self._pending_index = QPersistentModelIndex(index)
        if self.delay_ms <= 0:
            self._start_pending()
        else:
            self.timer.start(self.delay_ms)

    def stop(self, *_args) -> None:
        self.timer.stop()
        previous = self._active_index
        self._pending_index = QPersistentModelIndex()
        self._active_index = QPersistentModelIndex()
        self._active_asset_id = ""
        self.player.stop()
        self.player.setSource(QUrl())
        self.delegate.clear_hover_frame()
        self._update_index(previous)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport and event.type() in {
            QEvent.Type.Leave,
            QEvent.Type.Hide,
            QEvent.Type.Wheel,
        }:
            self.stop()
        return super().eventFilter(watched, event)

    def _start_pending(self) -> None:
        if not self.enabled or self.suspended or not self._pending_index.isValid():
            self.stop()
            return
        asset = self._pending_index.data(ASSET_ROLE)
        if (
            not isinstance(asset, LibraryStockAsset)
            or asset.id in self._failed_asset_ids
            or not asset.preview_path.is_file()
        ):
            self.stop()
            return
        self._active_index = self._pending_index
        self._pending_index = QPersistentModelIndex()
        self._active_asset_id = asset.id
        self.player.setSource(QUrl.fromLocalFile(str(asset.preview_path)))
        self.player.setPosition(0)
        self.player.play()

    def _frame_changed(self, frame) -> None:
        if not self._active_asset_id or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.delegate.set_hover_frame(
            self._active_asset_id, QPixmap.fromImage(image)
        )
        self._update_index(self._active_index)

    def _playback_failed(self, error, _message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        failed_id = self._active_asset_id
        self.stop()
        if failed_id:
            self._failed_asset_ids.add(failed_id)

    def _application_state_changed(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self.stop()

    def _update_index(self, index: QPersistentModelIndex) -> None:
        if index.isValid():
            self.view.viewport().update(
                self.view.visualRect(QModelIndex(index))
            )


class CollapsibleSection(QFrame):
    """Compact inspector section with a clickable disclosure header."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorSection")
        section_layout = QVBoxLayout(self)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setObjectName("inspectorSectionToggle")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.body = QWidget()
        self.body.setObjectName("inspectorSectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 10, 12, 12)
        self.body_layout.setSpacing(8)
        self.body.hide()
        self.toggle.toggled.connect(self.set_expanded)
        section_layout.addWidget(self.toggle)
        section_layout.addWidget(self.body)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)

    def set_title(self, title: str) -> None:
        self.toggle.setText(title)


class DetailPanel(QFrame):
    edit_requested = pyqtSignal(object)
    model_convert_requested = pyqtSignal(object)
    model_convert_canceled = pyqtSignal()
    model_rescan_requested = pyqtSignal(object)
    model_rescan_canceled = pyqtSignal()
    hdri_render_requested = pyqtSignal(object)
    hdri_render_canceled = pyqtSignal()
    houdini_send_requested = pyqtSignal(object, str, str, object)
    houdini_refresh_requested = pyqtSignal()
    blender_send_requested = pyqtSignal(object, str, str, object)
    blender_refresh_requested = pyqtSignal()
    polyhaven_check_requested = pyqtSignal(object)
    polyhaven_download_requested = pyqtSignal(object, str, str)
    polyhaven_cancel_requested = pyqtSignal()
    stock_playback_active_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._asset: AssetRecord | None = None
        self._houdini_sessions: list[HoudiniSession] = []
        self._blender_sessions: list[BlenderSession] = []
        self.setObjectName("panel")
        self.setMinimumWidth(320)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.details_scroll = QScrollArea()
        self.details_scroll.setObjectName("inspectorScroll")
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_content = QWidget()
        self.details_content.setObjectName("inspectorContent")
        layout = QVBoxLayout(self.details_content)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        self.details_scroll.setWidget(self.details_content)
        self.eyebrow = QLabel("TEXTURE SET")
        self.eyebrow.setObjectName("mutedLabel")
        self.hero = QLabel("No preview")
        self.hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero.setFixedHeight(150)
        self.hero.setObjectName("inspectorHero")
        self.stock_player_frame = QFrame()
        self.stock_player_frame.setObjectName("inspectorHero")
        stock_player_layout = QVBoxLayout(self.stock_player_frame)
        stock_player_layout.setContentsMargins(0, 0, 0, 6)
        stock_player_layout.setSpacing(5)
        self.stock_video = QVideoWidget()
        self.stock_video.setMinimumHeight(220)
        self.stock_player = QMediaPlayer(self)
        self.stock_audio: QAudioOutput | None = None
        if QApplication.platformName().casefold() != "offscreen":
            self.stock_audio = QAudioOutput(self)
            self.stock_audio.setVolume(0.65)
            self.stock_player.setAudioOutput(self.stock_audio)
        self.stock_player.setVideoOutput(self.stock_video)
        self.stock_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.stock_play = QPushButton("Play")
        self.stock_play.clicked.connect(self._toggle_stock_playback)
        self.stock_seek = QSlider(Qt.Orientation.Horizontal)
        self.stock_seek.setRange(0, 0)
        self.stock_seek.sliderMoved.connect(self.stock_player.setPosition)
        self.stock_time = QLabel("0:00 / 0:00")
        self.stock_time.setObjectName("mutedLabel")
        self.stock_mute = QPushButton("Mute")
        self.stock_mute.setCheckable(True)
        self.stock_mute.toggled.connect(self._set_stock_muted)
        self.stock_volume = QSlider(Qt.Orientation.Horizontal)
        self.stock_volume.setRange(0, 100)
        self.stock_volume.setValue(65)
        self.stock_volume.setMaximumWidth(72)
        self.stock_volume.setToolTip("Preview volume")
        self.stock_volume.valueChanged.connect(self._set_stock_volume)
        self.stock_open = QPushButton("Open preview")
        self.stock_open.clicked.connect(self._open_stock_preview)
        stock_controls = QHBoxLayout()
        stock_controls.setContentsMargins(6, 0, 6, 0)
        stock_controls.addWidget(self.stock_play)
        stock_controls.addWidget(self.stock_seek, 1)
        stock_controls.addWidget(self.stock_time)
        stock_controls.addWidget(self.stock_mute)
        stock_controls.addWidget(self.stock_volume)
        stock_controls.addWidget(self.stock_open)
        self.stock_player_error = QLabel()
        self.stock_player_error.setObjectName("mutedLabel")
        self.stock_player_error.setWordWrap(True)
        stock_player_layout.addWidget(self.stock_video)
        stock_player_layout.addLayout(stock_controls)
        stock_player_layout.addWidget(self.stock_player_error)
        self.stock_player.positionChanged.connect(self._stock_position_changed)
        self.stock_player.durationChanged.connect(self._stock_duration_changed)
        self.stock_player.playbackStateChanged.connect(self._stock_playback_changed)
        self.stock_player.errorOccurred.connect(
            lambda _error, message: self.stock_player_error.setText(
                message or "The Qt multimedia backend could not play this preview. Use Open preview."
            )
        )
        self.stock_player_frame.hide()
        self.name = QLabel("No asset selected")
        self.name.setObjectName("pageTitle")
        self.name.setWordWrap(True)
        self.description = QLabel("Select an imported asset to inspect it here.")
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        self.metadata = QLabel("")
        self.metadata.setTextFormat(Qt.TextFormat.RichText)
        self.metadata.setWordWrap(True)
        self.technical_details = QLabel("")
        self.technical_details.setTextFormat(Qt.TextFormat.RichText)
        self.technical_details.setWordWrap(True)
        self.technical_details.setObjectName("mutedLabel")
        self.maps_title = QLabel("Available maps")
        self.maps_title.setObjectName("sectionTitle")
        self.channels = QLabel("—")
        self.channels.setTextFormat(Qt.TextFormat.RichText)
        self.channels.setWordWrap(True)
        self.tags = QLabel("")
        self.tags.setObjectName("mutedLabel")
        self.tags.setWordWrap(True)
        self.extras = QLabel("—")
        self.extras.setObjectName("mutedLabel")
        self.extras.setWordWrap(True)
        self.extra_selector = QComboBox()
        self.extra_copy_button = QPushButton("Copy extra path")
        self.extra_copy_button.clicked.connect(self._copy_extra_path)
        self.extra_reveal_button = QPushButton("Reveal extra")
        self.extra_reveal_button.clicked.connect(self._reveal_extra)
        extra_actions = QHBoxLayout()
        extra_actions.addWidget(self.extra_copy_button)
        extra_actions.addWidget(self.extra_reveal_button)
        self.path_button = QPushButton("Copy asset path")
        self.path_button.clicked.connect(self._copy_path)
        self.reveal_button = QPushButton("Reveal in folder")
        self.reveal_button.clicked.connect(self._reveal)
        self.edit_button = QPushButton("Edit material…")
        self.edit_button.setObjectName("primaryButton")
        self.edit_button.clicked.connect(self._edit)
        self.view_3d_button = QPushButton("View 3D externally")
        self.view_3d_button.clicked.connect(self._view_3d)
        self.model_convert_button = QPushButton("Convert to USD")
        self.model_convert_button.setObjectName("primaryButton")
        self.model_convert_button.clicked.connect(
            lambda: self._asset and self.model_convert_requested.emit(self._asset)
        )
        self.model_convert_cancel = QPushButton("Cancel conversion")
        self.model_convert_cancel.clicked.connect(self.model_convert_canceled)
        self.model_convert_status = QLabel()
        self.model_convert_status.setObjectName("mutedLabel")
        self.model_convert_status.setWordWrap(True)
        self.model_rescan_button = QPushButton("Rescan asset")
        self.model_rescan_button.clicked.connect(
            lambda: self._asset and self.model_rescan_requested.emit(self._asset)
        )
        self.model_rescan_cancel = QPushButton("Cancel rescan")
        self.model_rescan_cancel.clicked.connect(self.model_rescan_canceled)
        self.model_rescan_status = QLabel()
        self.model_rescan_status.setObjectName("mutedLabel")
        self.model_rescan_status.setWordWrap(True)
        self.hdri_render_button = QPushButton("Render preview")
        self.hdri_render_button.setObjectName("primaryButton")
        self.hdri_render_button.clicked.connect(lambda: self._asset and self.hdri_render_requested.emit(self._asset))
        self.hdri_cancel_button = QPushButton("Cancel render")
        self.hdri_cancel_button.clicked.connect(self.hdri_render_canceled)
        self.hdri_render_status = QLabel()
        self.hdri_render_status.setObjectName("mutedLabel")
        self.hdri_render_status.setWordWrap(True)
        self.polyhaven_check_button = QPushButton("Check online resolutions")
        self.polyhaven_check_button.clicked.connect(
            lambda: self._asset and self.polyhaven_check_requested.emit(self._asset)
        )
        self.polyhaven_resolution = QComboBox()
        self.polyhaven_resolution.currentIndexChanged.connect(self._update_polyhaven_buttons)
        self.polyhaven_maps_button = QPushButton("Download texture maps")
        self.polyhaven_maps_button.clicked.connect(lambda: self._request_polyhaven_download("maps"))
        self.polyhaven_mtlx_button = QPushButton("Download MaterialX package")
        self.polyhaven_mtlx_button.clicked.connect(lambda: self._request_polyhaven_download("materialx"))
        self.polyhaven_usd_button = QPushButton("Download USD package")
        self.polyhaven_usd_button.clicked.connect(lambda: self._request_polyhaven_download("usd"))
        self.polyhaven_hdri_button = QPushButton("Download HDRI files")
        self.polyhaven_hdri_button.clicked.connect(lambda: self._request_polyhaven_download("hdri"))
        self.polyhaven_progress = QProgressBar()
        self.polyhaven_progress.setRange(0, 1000)
        self.polyhaven_progress.setTextVisible(False)
        self.polyhaven_cancel_button = QPushButton("Cancel download")
        self.polyhaven_cancel_button.clicked.connect(self.polyhaven_cancel_requested)
        self.polyhaven_status = QLabel("Check Poly Haven for available downloads.")
        self.polyhaven_status.setObjectName("mutedLabel")
        self.polyhaven_status.setWordWrap(True)
        self.polyhaven_attribution = QLabel("<a href='https://polyhaven.com'>Powered by Poly Haven</a>")
        self.polyhaven_attribution.setOpenExternalLinks(True)
        self.polyhaven_package_selector = QComboBox()
        self.polyhaven_package_open = QPushButton("Open package")
        self.polyhaven_package_open.clicked.connect(self._open_polyhaven_package)
        self.polyhaven_package_copy = QPushButton("Copy package path")
        self.polyhaven_package_copy.clicked.connect(self._copy_polyhaven_package)
        self.polyhaven_package_reveal = QPushButton("Reveal package")
        self.polyhaven_package_reveal.clicked.connect(self._reveal_polyhaven_package)
        self.dcc_title = QLabel("Export")
        self.dcc_title.setObjectName("sectionTitle")
        self.dcc_app = QComboBox()
        self.dcc_app.addItem("Blender", "blender")
        self.dcc_app.addItem("Houdini", "houdini")
        self.dcc_stack = QStackedWidget()
        self.houdini_session_label = QLabel("Houdini session")
        self.houdini_session_label.setObjectName("mutedLabel")
        self.houdini_session = QComboBox()
        self.houdini_session.currentIndexChanged.connect(self._update_houdini_controls)
        self.houdini_resolution = QComboBox()
        self.houdini_target = QComboBox()
        self.houdini_target.addItem("Solaris LOP", "lop")
        self.houdini_target.addItem("SOPs · Packed USD", "sop")
        self.houdini_target.setToolTip("Reference in Solaris, or load lightweight packed USD primitives in SOPs.")
        self.houdini_send_button = QPushButton("Send to Houdini")
        self.houdini_send_button.setObjectName("primaryButton")
        self.houdini_send_button.clicked.connect(self._send_to_houdini)
        self.houdini_refresh_button = QPushButton("Refresh sessions")
        self.houdini_refresh_button.clicked.connect(self.houdini_refresh_requested)
        self.houdini_status = QLabel()
        self.houdini_status.setObjectName("mutedLabel")
        self.houdini_status.setWordWrap(True)
        self.blender_session_label = QLabel("Blender session")
        self.blender_session_label.setObjectName("mutedLabel")
        self.blender_session = QComboBox()
        self.blender_session.currentIndexChanged.connect(self._update_blender_controls)
        self.blender_resolution = QComboBox()
        self.blender_world_mode = QComboBox()
        self.blender_world_mode.addItem("Edit current World", "edit_current")
        self.blender_world_mode.addItem("Create new World", "new")
        self.blender_world_mode.setToolTip(
            "Create new World preserves the current World datablock. Edit current World changes its environment setup."
        )
        self.blender_send_button = QPushButton("Send to Blender")
        self.blender_send_button.setObjectName("primaryButton")
        self.blender_send_button.clicked.connect(self._send_to_blender)
        self.blender_refresh_button = QPushButton("Refresh sessions")
        self.blender_refresh_button.clicked.connect(self.blender_refresh_requested)
        self.blender_status = QLabel()
        self.blender_status.setObjectName("mutedLabel")
        self.blender_status.setWordWrap(True)
        self.blender_dcc_page = QWidget()
        blender_dcc_layout = QVBoxLayout(self.blender_dcc_page)
        blender_dcc_layout.setContentsMargins(0, 0, 0, 0)
        blender_dcc_layout.setSpacing(6)
        blender_dcc_layout.addWidget(self.blender_session_label)
        blender_dcc_layout.addWidget(self.blender_session)
        blender_options = QHBoxLayout()
        blender_options.setSpacing(6)
        blender_options.addWidget(self.blender_resolution, 1)
        blender_options.addWidget(self.blender_world_mode, 1)
        blender_dcc_layout.addLayout(blender_options)
        blender_dcc_layout.addWidget(self.blender_send_button)
        blender_status_row = QHBoxLayout()
        blender_status_row.addWidget(self.blender_status, 1)
        self.blender_refresh_button.setText("Refresh")
        blender_status_row.addWidget(self.blender_refresh_button)
        blender_dcc_layout.addLayout(blender_status_row)
        self.houdini_dcc_page = QWidget()
        houdini_dcc_layout = QVBoxLayout(self.houdini_dcc_page)
        houdini_dcc_layout.setContentsMargins(0, 0, 0, 0)
        houdini_dcc_layout.setSpacing(6)
        houdini_dcc_layout.addWidget(self.houdini_session_label)
        houdini_dcc_layout.addWidget(self.houdini_session)
        houdini_options = QHBoxLayout()
        houdini_options.setSpacing(6)
        houdini_options.addWidget(self.houdini_resolution, 1)
        houdini_options.addWidget(self.houdini_target, 1)
        houdini_dcc_layout.addLayout(houdini_options)
        houdini_dcc_layout.addWidget(self.houdini_send_button)
        houdini_status_row = QHBoxLayout()
        houdini_status_row.addWidget(self.houdini_status, 1)
        self.houdini_refresh_button.setText("Refresh")
        houdini_status_row.addWidget(self.houdini_refresh_button)
        houdini_dcc_layout.addLayout(houdini_status_row)
        self.dcc_stack.addWidget(self.blender_dcc_page)
        self.dcc_stack.addWidget(self.houdini_dcc_page)
        self.dcc_app.currentIndexChanged.connect(self._dcc_app_changed)

        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(6)
        primary_actions.addWidget(self.edit_button)
        primary_actions.addWidget(self.view_3d_button)
        primary_actions.addWidget(self.model_convert_button)
        primary_actions.addWidget(self.model_rescan_button)
        primary_actions.addWidget(self.hdri_render_button)
        primary_actions.addStretch()
        path_actions = QHBoxLayout()
        path_actions.setSpacing(6)
        path_actions.addWidget(self.path_button)
        path_actions.addWidget(self.reveal_button)

        self.files_section = CollapsibleSection("Files & Maps")
        self.files_section.body_layout.addWidget(self.maps_title)
        self.files_section.body_layout.addWidget(self.channels)

        self.downloads_section = CollapsibleSection("Poly Haven Downloads")
        self.polyhaven_title = self.downloads_section.toggle
        for widget in (
            self.polyhaven_check_button, self.polyhaven_resolution,
            self.polyhaven_maps_button, self.polyhaven_mtlx_button,
            self.polyhaven_usd_button, self.polyhaven_hdri_button,
            self.polyhaven_progress, self.polyhaven_cancel_button,
            self.polyhaven_status, self.polyhaven_attribution,
            self.polyhaven_package_selector, self.polyhaven_package_open,
            self.polyhaven_package_copy, self.polyhaven_package_reveal,
        ):
            self.downloads_section.body_layout.addWidget(widget)

        self.technical_section = CollapsibleSection("Technical & Source")
        self.technical_toggle = self.technical_section.toggle
        self.technical_section.body_layout.addWidget(self.technical_details)

        self.extras_section = CollapsibleSection("Included Extras")
        self.extras_title = self.extras_section.toggle
        self.extras_section.body_layout.addWidget(self.extras)
        self.extras_section.body_layout.addWidget(self.extra_selector)
        self.extras_section.body_layout.addLayout(extra_actions)

        self.export_footer = QFrame()
        self.export_footer.setObjectName("exportFooter")
        footer_layout = QVBoxLayout(self.export_footer)
        footer_layout.setContentsMargins(14, 12, 14, 12)
        footer_layout.setSpacing(7)
        footer_header = QHBoxLayout()
        footer_header.addWidget(self.dcc_title)
        footer_header.addStretch()
        footer_header.addWidget(self.dcc_app)
        footer_layout.addLayout(footer_header)
        footer_layout.addWidget(self.dcc_stack)

        layout.addWidget(self.hero)
        layout.addWidget(self.stock_player_frame)
        layout.addWidget(self.eyebrow)
        layout.addWidget(self.name)
        layout.addWidget(self.description)
        layout.addLayout(primary_actions)
        layout.addLayout(path_actions)
        layout.addWidget(self.hdri_cancel_button)
        layout.addWidget(self.hdri_render_status)
        layout.addWidget(self.model_convert_cancel)
        layout.addWidget(self.model_convert_status)
        layout.addWidget(self.model_rescan_cancel)
        layout.addWidget(self.model_rescan_status)
        layout.addWidget(self.metadata)
        layout.addWidget(self.tags)
        layout.addWidget(self.files_section)
        layout.addWidget(self.downloads_section)
        layout.addWidget(self.technical_section)
        layout.addWidget(self.extras_section)
        layout.addStretch()
        outer_layout.addWidget(self.details_scroll, 1)
        outer_layout.addWidget(self.export_footer, 0)

        # Retained as an empty compatibility surface for callers from the
        # earlier prototype; disabled future actions no longer clutter the UI.
        self.future_buttons = []
        self.clear()

    def clear(self) -> None:
        self._asset = None
        self.stock_player.stop()
        self.stock_player.setSource(QUrl())
        self.stock_player_frame.hide()
        self.hero.show()
        self.hero.setPixmap(QPixmap())
        self.hero.setText("No preview")
        self.name.setText("No asset selected")
        self.description.setText("Choose an imported asset to inspect it.")
        self.metadata.clear()
        self.technical_details.clear()
        self.channels.setText("—")
        self.tags.clear()
        self.extra_selector.clear()
        for section in (
            self.files_section, self.downloads_section,
            self.technical_section, self.extras_section,
        ):
            section.set_expanded(False)
            section.hide()
        self.path_button.setEnabled(False)
        self.reveal_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.view_3d_button.hide()
        self.model_convert_button.hide()
        self.model_convert_cancel.hide()
        self.model_convert_status.hide()
        self.model_rescan_button.hide()
        self.model_rescan_cancel.hide()
        self.model_rescan_status.hide()
        self.hdri_render_button.hide()
        self.hdri_cancel_button.hide()
        self.hdri_render_status.hide()
        self.model_convert_cancel.hide()
        self.model_convert_status.hide()
        for widget in (
            self.polyhaven_check_button, self.polyhaven_resolution,
            self.polyhaven_maps_button, self.polyhaven_mtlx_button, self.polyhaven_usd_button,
            self.polyhaven_hdri_button,
            self.polyhaven_progress, self.polyhaven_cancel_button, self.polyhaven_status,
            self.polyhaven_attribution, self.polyhaven_package_selector,
            self.polyhaven_package_open, self.polyhaven_package_copy,
            self.polyhaven_package_reveal,
        ):
            widget.hide()
        self.export_footer.hide()
        self.houdini_target.hide()

    def show_asset(self, asset: AssetRecord) -> None:
        self.stock_player.stop()
        self.stock_player.setSource(QUrl())
        self.stock_player_error.clear()
        self._asset = asset
        self.hdri_render_button.hide()
        self.hdri_cancel_button.hide()
        self.hdri_render_status.hide()
        self.model_rescan_cancel.hide()
        self.model_rescan_status.hide()
        self.export_footer.hide()
        self._configure_polyhaven(asset)
        for section in (self.files_section, self.technical_section, self.extras_section):
            section.set_expanded(False)
        self.files_section.show()
        self.technical_section.show()
        self.maps_title.show()
        self.channels.show()
        is_stock = isinstance(asset, LibraryStockAsset)
        self.stock_player_frame.setVisible(is_stock)
        self.hero.setVisible(not is_stock)
        if is_stock:
            self.stock_player.setSource(QUrl.fromLocalFile(str(asset.preview_path)))
            self.stock_seek.setRange(0, max(0, int(asset.media_info.duration * 1000)))
            self.stock_time.setText(f"0:00 / {_media_time(int(asset.media_info.duration * 1000))}")
        preview = asset.hero_path or asset.thumbnail_path
        pixmap = QPixmap(str(preview)) if preview else QPixmap()
        if pixmap.isNull():
            self.hero.setPixmap(QPixmap())
            self.hero.setText("No preview")
        else:
            self.hero.setText("")
            self.hero.setPixmap(pixmap.scaled(self.hero.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.name.setText(asset.name)
        self.description.setText(asset.description or "No description provided.")
        provider = asset.provider + (f" · {asset.provider_id}" if asset.provider_id else "")
        self.metadata.setText(
            f"<span style='color:#8792a1'>Category</span>  {asset.category}<br>"
            f"<span style='color:#8792a1'>Variants</span>  {asset.resolution or '—'}<br>"
            f"<span style='color:#8792a1'>Format</span>  {asset.file_format} · {asset.size_label}<br>"
            f"<span style='color:#8792a1'>Provider</span>  {provider}"
        )
        self.technical_details.setText(
            f"<span style='color:#8792a1'>Asset ID</span><br>{html.escape(asset.id)}<br><br>"
            f"<span style='color:#8792a1'>Managed path</span><br>{html.escape(str(asset.asset_dir))}<br><br>"
            f"<span style='color:#8792a1'>Provider metadata</span><br>"
            f"{html.escape(', '.join(asset.source_metadata) if asset.source_metadata else 'None')}"
        )
        if isinstance(asset, LibraryModelAsset):
            self.eyebrow.setText(
                "MODEL · NEEDS RESCAN" if asset.needs_rescan else
                "MODEL · USD READY" if asset.usd_ready else "MODEL · NO USD"
            )
            self.maps_title.setText("Model files and textures")
            preferred = asset.preferred_model
            dimensions = " × ".join(f"{value:g}" for value in asset.dimensions) if asset.dimensions else "Unknown"
            model_lines = []
            for item in asset.model_files:
                flags = []
                if item.preferred:
                    flags.append("preferred")
                if item.origin == "manual":
                    flags.append("manual")
                if not item.available:
                    flags.append("missing")
                if item.lod:
                    flags.append(item.lod)
                if item.resolution:
                    flags.append(item.resolution)
                if item.role:
                    flags.append(item.role)
                if item.triangle_count is not None:
                    flags.append(f"{item.triangle_count:,} tris")
                model_lines.append(
                    f"<span style='color:{'#78c995' if item.preferred else '#8793a2'}'>●</span>&nbsp;&nbsp;"
                    f"{html.escape(item.file_format)} · {html.escape(item.original_path)}"
                    + (f"<br><span style='color:#8793a2'>&nbsp;&nbsp;&nbsp;&nbsp;{html.escape(' · '.join(flags))}</span>" if flags else "")
                )
            texture_count = sum(
                len(maps)
                for texture_set in asset.texture_sets.values()
                for variant in texture_set.resolutions.values()
                for maps in variant.maps.values()
            )
            self.metadata.setText(
                f"<span style='color:#8792a1'>Category</span>  {asset.category}<br>"
                f"<span style='color:#8792a1'>Preferred</span>  "
                f"{html.escape(preferred.file_format) if preferred else 'None'}"
                f"{f' · {html.escape(preferred.resolution)}' if preferred and preferred.resolution else ''}<br>"
                f"<span style='color:#8792a1'>Available</span>  {asset.file_format}"
                f"{f' · {html.escape(asset.resolution)}' if asset.resolution else ''}<br>"
                f"<span style='color:#8792a1'>Provider</span>  {provider} · {asset.size_label}"
            )
            self.technical_details.setText(
                f"<span style='color:#8792a1'>Preferred file</span><br>"
                f"{html.escape(preferred.path) if preferred else 'None'}<br><br>"
                f"<span style='color:#8792a1'>Geometry</span><br>"
                f"{f'{asset.polycount:,} triangles' if asset.polycount is not None else 'Triangle count unknown'}"
                f" · {dimensions}<br><br>"
                f"<span style='color:#8792a1'>Materials and maps</span><br>"
                f"{texture_count} texture file(s) · {len(asset.texture_sets)} material group(s)<br><br>"
                f"<span style='color:#8792a1'>Provider metadata</span><br>"
                f"{html.escape(', '.join(asset.source_metadata) if asset.source_metadata else 'None')}"
            )
            self.files_section.set_title(f"Files & Materials · {len(asset.model_files)}")
            self.maps_title.hide()
            self.channels.setText("<br>".join(model_lines))
            self.channels.show()
            self.edit_button.setText("Edit model…")
            self.view_3d_button.setVisible(True)
            self.view_3d_button.setEnabled(asset.usd_ready)
            self.view_3d_button.setToolTip("Open the managed USD file with the operating system viewer." if asset.usd_ready else "This asset has no managed USD file.")
            sources = model_conversion_sources(asset)
            self.model_convert_button.setText(
                "Rebuild USD" if asset.usd_derivative else "Convert to USD"
            )
            self.model_convert_button.setVisible(True)
            self.model_convert_button.setEnabled(bool(sources))
            self.model_convert_button.setToolTip(
                "Run Blender headlessly and publish a preferred USDC derivative."
                if sources else
                "No compatible BLEND, FBX, OBJ, GLTF, GLB, or Alembic source is available."
            )
            self.model_rescan_button.setVisible(True)
            self.model_rescan_button.setEnabled(True)
            self.model_rescan_button.setToolTip(
                "Find source models under models/ and USD-family files under usd/."
            )
            if asset.needs_rescan:
                self.model_rescan_status.setText(
                    "A manually registered model file is missing. Rescan to repair the asset."
                )
                self.model_rescan_status.setStyleSheet("color:#e6b566;")
                self.model_rescan_status.show()
            if asset.usd_derivative:
                self.model_convert_status.setText(
                    f"Generated with {asset.usd_derivative.blender_version} · "
                    f"{asset.usd_derivative.forward_axis} forward, {asset.usd_derivative.up_axis} up"
                )
                self.model_convert_status.setStyleSheet("color:#78c995;")
                self.model_convert_status.show()
            self._configure_model_dcc(asset)
        elif isinstance(asset, LibraryHdriAsset):
            self.eyebrow.setText("HDRI")
            self.maps_title.setText("Environment variants")
            self.files_section.set_title("Environment Files")
            lines = []
            for label, variant in asset.resolutions.items():
                dimensions = f"{variant.width}×{variant.height}" if variant.width and variant.height else "dimensions unknown"
                formats = "/".join(sorted({item.file_format for item in variant.files}))
                lines.append(
                    f"<span style='color:#8ac4f4'>●</span>&nbsp;&nbsp;"
                    f"{html.escape(label)} · {html.escape(dimensions)} · {html.escape(formats)}"
                )
            self.channels.setText("<br>".join(lines))
            self.edit_button.setText("Edit HDRI…")
            render = asset.preview_render
            status = str(render.get("status", "pending"))
            diagnostic = str(render.get("diagnostic", ""))
            self.hdri_render_button.setText("Regenerate preview" if status == "ready" else "Render preview")
            self.hdri_render_button.show()
            self.hdri_render_button.setEnabled(True)
            self.hdri_cancel_button.hide()
            self.hdri_render_status.setText(
                "Composite preview ready" if status == "ready" else diagnostic or f"Preview render: {status}"
            )
            self.hdri_render_status.setStyleSheet("color:#78c995;" if status == "ready" else "color:#e6b566;")
            self.hdri_render_status.show()
            self.houdini_send_button.setText("Send to Houdini")
            self.blender_send_button.setText("Send to Blender")
            self.blender_world_mode.show()
            self.houdini_target.hide()
            self.houdini_resolution.clear()
            for label in sorted(asset.resolutions, key=lambda value: (_resolution_number(value), value)):
                self.houdini_resolution.addItem(label)
            try:
                default_label, _file = choose_hdri_file(asset)
                self.houdini_resolution.setCurrentText(default_label)
            except HoudiniBridgeError:
                pass
            self._update_houdini_controls()
            self.blender_resolution.clear()
            for label in sorted(asset.resolutions, key=lambda value: (_resolution_number(value), value)):
                self.blender_resolution.addItem(label)
            try:
                default_label, _file = choose_hdri_file(asset)
                self.blender_resolution.setCurrentText(default_label)
            except HoudiniBridgeError:
                pass
            self.blender_world_mode.setCurrentIndex(0)
            self._update_blender_controls()
            self.dcc_title.show()
            self.dcc_app.show()
            self.dcc_stack.show()
            self._dcc_app_changed(self.dcc_app.currentIndex())
            self.export_footer.show()
        elif isinstance(asset, LibraryStockAsset):
            info = asset.media_info
            self.eyebrow.setText("STOCK · ALPHA" if info.alpha == "yes" else "STOCK")
            self.metadata.setText(
                f"<span style='color:#8792a1'>Category</span>  {asset.category}<br>"
                f"<span style='color:#8792a1'>Duration</span>  {asset.duration_label} · {info.frame_rate:.3g} fps<br>"
                f"<span style='color:#8792a1'>Frame</span>  {info.width}×{info.height} · "
                f"{'Alpha' if info.alpha == 'yes' else 'Opaque' if info.alpha == 'no' else 'Alpha unknown'}<br>"
                f"<span style='color:#8792a1'>Codec</span>  {info.codec.upper()} · {asset.source_format} · {asset.size_label}"
            )
            self.technical_details.setText(
                f"<span style='color:#8792a1'>Source</span><br>{html.escape(asset.source_original_path)}<br><br>"
                f"<span style='color:#8792a1'>Codec details</span><br>"
                f"{html.escape(info.codec.upper())} · {html.escape(info.profile or 'No profile')} · "
                f"{html.escape(info.pixel_format)}<br><br>"
                f"<span style='color:#8792a1'>Frames and audio</span><br>"
                f"{info.frame_count if info.frame_count is not None else 'Unknown'} frames · "
                f"{'Audio' if info.has_audio else 'Silent'}<br><br>"
                f"<span style='color:#8792a1'>Preview</span><br>"
                f"{html.escape(asset.preview_origin.title())} · midpoint {asset.thumbnail_time:.3f}s"
            )
            self.files_section.set_title("Source & Preview")
            self.maps_title.setText("Managed media")
            self.channels.setText(
                f"• Source: {html.escape(asset.source_path.name)}<br>"
                f"• Preview: {html.escape(asset.preview_path.name)}<br>"
                f"• Thumbnail: {html.escape(asset.thumbnail_path.name)}"
            )
            self.edit_button.setText("Edit Stock clip…")
            self.view_3d_button.hide()
            self.export_footer.hide()
        else:
            self.eyebrow.setText("ATLAS" if asset.asset_type == "atlas" else "TEXTURE SET")
            self.maps_title.setText("Available maps")
            self.files_section.set_title("Files & Maps")
            self.channels.setText("<br>".join(
                f"<span style='color:{CHANNEL_COLORS.get(channel, DEFAULT_CHANNEL_COLOR)}'>●</span>"
                f"&nbsp;&nbsp;{html.escape(channel)}"
                for channel in asset.channels
            ))
            self.edit_button.setText("Edit atlas…" if asset.asset_type == "atlas" else "Edit material…")
            self._configure_texture_dcc(asset)
        if not isinstance(asset, LibraryModelAsset):
            self.view_3d_button.hide()
            self.model_convert_button.hide()
            self.model_rescan_button.hide()
            self.model_rescan_cancel.hide()
            self.model_rescan_status.hide()
        self.tags.setText("  ".join(f"#{tag}" for tag in asset.tags) or "No tags")
        if asset.extra_files:
            names = [item.original_path or item.path.removeprefix("extras/") for item in asset.extra_files]
            visible = names[:6]
            if len(names) > len(visible):
                visible.append(f"…and {len(names) - len(visible)} more")
            self.extras_section.set_title(f"Included Extras · {len(names)}")
            self.extras.setText("\n".join(f"• {name}" for name in visible))
            self.extras_section.show()
            self.extras.show()
            self.extra_selector.clear()
            for item in asset.extra_files:
                self.extra_selector.addItem(item.original_path or item.path, str(asset.asset_dir / item.path))
            self.extra_selector.show()
            self.extra_copy_button.show()
            self.extra_reveal_button.show()
        else:
            self.extras_section.hide()
        self.path_button.setEnabled(True)
        self.reveal_button.setEnabled(True)
        self.edit_button.setEnabled(True)

    def _toggle_technical_details(self, expanded: bool) -> None:
        self.technical_section.set_expanded(expanded)

    def _configure_polyhaven(self, asset: AssetRecord) -> None:
        self._polyhaven_options = None
        supported = isinstance(asset, (LibraryTextureAsset, LibraryHdriAsset, LibraryModelAsset)) and "poly haven" in asset.provider.casefold()
        self.downloads_section.set_expanded(False)
        self.downloads_section.setVisible(supported)
        base_widgets = (self.polyhaven_check_button, self.polyhaven_status, self.polyhaven_attribution)
        for widget in base_widgets:
            widget.setVisible(supported)
        for widget in (
            self.polyhaven_resolution, self.polyhaven_maps_button, self.polyhaven_mtlx_button,
            self.polyhaven_usd_button, self.polyhaven_hdri_button,
            self.polyhaven_progress, self.polyhaven_cancel_button,
        ):
            widget.hide()
        self.polyhaven_package_selector.clear()
        for package in getattr(asset, "provider_packages", ()):
            entry = asset.asset_dir / package.entry_path
            self.polyhaven_package_selector.addItem(
                f"{package.kind.upper()} · {package.resolution}", str(entry)
            )
        has_packages = self.polyhaven_package_selector.count() > 0
        for widget in (
            self.polyhaven_package_selector, self.polyhaven_package_open,
            self.polyhaven_package_copy, self.polyhaven_package_reveal,
        ):
            widget.setVisible(supported and has_packages)
        if supported:
            self.polyhaven_status.setText("Check Poly Haven for available downloads.")
            self.polyhaven_status.setStyleSheet("color:#8792a1;")
            self.polyhaven_check_button.setEnabled(True)

    def set_polyhaven_options(self, options: PolyHavenOptions) -> None:
        if not self._asset:
            return
        labels = sorted(
            set((*options.map_resolutions, *options.materialx_resolutions, *options.usd_resolutions, *options.hdri_resolutions)),
            key=lambda value: (_resolution_number(value), value),
        )
        self.polyhaven_resolution.clear()
        self.polyhaven_resolution.addItems(labels)
        self.polyhaven_resolution.setVisible(bool(labels))
        self.polyhaven_maps_button.setVisible(isinstance(self._asset, LibraryTextureAsset) and bool(options.map_resolutions))
        self.polyhaven_mtlx_button.setVisible(isinstance(self._asset, LibraryTextureAsset) and bool(options.materialx_resolutions))
        self.polyhaven_usd_button.setVisible(isinstance(self._asset, LibraryModelAsset) and bool(options.usd_resolutions))
        self.polyhaven_hdri_button.setVisible(isinstance(self._asset, LibraryHdriAsset) and bool(options.hdri_resolutions))
        self.polyhaven_status.setText(
            ("Using the preserved Poly Haven catalog because the live API was unavailable. " if options.from_cache else "")
            + (f"{len(labels)} online resolution(s) available." if labels else "No compatible downloads were advertised.")
        )
        self.polyhaven_status.setStyleSheet("color:#e6b566;" if options.from_cache else "color:#78c995;")
        self._update_polyhaven_buttons()

    def set_polyhaven_busy(self, active: bool, message: str = "", completed: int = 0, total: int = 0) -> None:
        self.polyhaven_check_button.setEnabled(not active)
        self.polyhaven_resolution.setEnabled(not active)
        for button in (
            self.polyhaven_maps_button, self.polyhaven_mtlx_button,
            self.polyhaven_usd_button, self.polyhaven_hdri_button,
        ):
            button.setEnabled(not active)
        self.polyhaven_progress.setVisible(active)
        self.polyhaven_cancel_button.setVisible(active)
        self.polyhaven_progress.setValue(min(1000, int(completed * 1000 / total)) if total else 0)
        if message:
            self.polyhaven_status.setText(message)
            self.polyhaven_status.setStyleSheet("color:#8792a1;")
        if not active:
            self._update_polyhaven_buttons()

    def set_polyhaven_result(self, message: str, success: bool) -> None:
        self.set_polyhaven_busy(False)
        self.polyhaven_status.setText(message)
        self.polyhaven_status.setStyleSheet(f"color:{'#78c995' if success else '#ef7d7d'};")
        self._update_polyhaven_buttons()

    def _update_polyhaven_buttons(self, *_args) -> None:
        label = self.polyhaven_resolution.currentText()
        options = getattr(self, "_polyhaven_options", None)
        if not isinstance(options, PolyHavenOptions):
            return
        self.polyhaven_maps_button.setEnabled(label in options.map_resolutions)
        self.polyhaven_mtlx_button.setEnabled(label in options.materialx_resolutions)
        self.polyhaven_usd_button.setEnabled(label in options.usd_resolutions)
        self.polyhaven_hdri_button.setEnabled(label in options.hdri_resolutions)

    def apply_polyhaven_options(self, options: PolyHavenOptions) -> None:
        self._polyhaven_options = options
        self.set_polyhaven_options(options)

    def _request_polyhaven_download(self, kind: str) -> None:
        if self._asset and self.polyhaven_resolution.currentText():
            self.polyhaven_download_requested.emit(self._asset, kind, self.polyhaven_resolution.currentText())

    def _open_polyhaven_package(self) -> None:
        value = str(self.polyhaven_package_selector.currentData() or "")
        if value:
            QDesktopServices.openUrl(QUrl.fromLocalFile(value))

    def _copy_polyhaven_package(self) -> None:
        value = str(self.polyhaven_package_selector.currentData() or "")
        if value:
            QApplication.clipboard().setText(value)

    def _reveal_polyhaven_package(self) -> None:
        value = str(self.polyhaven_package_selector.currentData() or "")
        if value:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(value).parent)))

    def set_houdini_sessions(self, sessions: list[HoudiniSession], preferred_id: str = "") -> None:
        current = self.houdini_session.currentData()
        current_id = current.id if isinstance(current, HoudiniSession) else preferred_id
        self._houdini_sessions = list(sessions)
        self.houdini_session.clear()
        for session in sessions:
            self.houdini_session.addItem(session.label, session)
        self.houdini_refresh_button.setEnabled(True)
        self.houdini_resolution.setEnabled(True)
        self.houdini_target.setEnabled(True)
        self.houdini_session.setEnabled(True)
        target = next((index for index, session in enumerate(sessions) if session.id == current_id), 0)
        if sessions:
            self.houdini_session.setCurrentIndex(target)
            self.houdini_status.setText(f"Connected to {len(sessions)} Houdini session(s).")
            self.houdini_status.setStyleSheet("color: #78c995;")
        else:
            self.houdini_status.setText("No ShotBox Assets-enabled Houdini session found. Install the plugin in Settings and restart Houdini.")
            self.houdini_status.setStyleSheet("color: #e6b566;")
        self._update_houdini_controls()

    def set_houdini_busy(self, active: bool, message: str = "") -> None:
        self.houdini_send_button.setEnabled(not active and self._houdini_can_send())
        self.houdini_refresh_button.setEnabled(not active)
        self.houdini_resolution.setEnabled(not active)
        self.houdini_target.setEnabled(not active)
        self.houdini_session.setEnabled(not active)
        if message:
            self.houdini_status.setText(message)
            self.houdini_status.setStyleSheet("color: #8792a1;")

    def set_houdini_result(self, message: str, success: bool) -> None:
        self.set_houdini_busy(False)
        self.houdini_status.setText(message)
        self.houdini_status.setStyleSheet(f"color: {'#78c995' if success else '#ef7d7d'};")

    def _update_houdini_controls(self) -> None:
        supported = isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset))
        multiple = supported and len(self._houdini_sessions) > 1
        self.houdini_session_label.setVisible(multiple)
        self.houdini_session.setVisible(multiple)
        self.houdini_send_button.setVisible(supported)
        self.houdini_send_button.setEnabled(self._houdini_can_send())
        session = self.houdini_session.currentData()
        if isinstance(self._asset, LibraryTextureAsset) and isinstance(session, HoudiniSession) and "texture_material" not in session.capabilities:
            self.houdini_status.setText("Update the Houdini plug-in in Settings and restart Houdini.")
            self.houdini_status.setStyleSheet("color: #e6b566;")
        if isinstance(self._asset, LibraryModelAsset) and isinstance(session, HoudiniSession) and "usd_model" not in session.capabilities:
            self.houdini_status.setText("Update the Houdini plug-in in Settings and restart Houdini.")
            self.houdini_status.setStyleSheet("color: #e6b566;")
        elif isinstance(self._asset, LibraryModelAsset) and not self._asset.usd_ready:
            self.houdini_status.setText("This model has no managed USD file. Download or import USD first.")
            self.houdini_status.setStyleSheet("color: #e6b566;")

    def _dcc_app_changed(self, index: int) -> None:
        app = self.dcc_app.itemData(index)
        self.dcc_stack.setCurrentWidget(self.houdini_dcc_page if app == "houdini" else self.blender_dcc_page)

    def _send_to_houdini(self) -> None:
        session = self.houdini_session.currentData()
        if isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)) and isinstance(session, HoudiniSession):
            self.houdini_send_requested.emit(
                self._asset,
                str(self.houdini_resolution.currentData() or self.houdini_resolution.currentText()),
                str(self.houdini_target.currentData() or "lop"),
                session,
            )

    def _houdini_can_send(self) -> bool:
        session = self.houdini_session.currentData()
        if not isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)) or not isinstance(session, HoudiniSession):
            return False
        return self.houdini_resolution.count() > 0 and (
            isinstance(self._asset, LibraryHdriAsset)
            or isinstance(self._asset, LibraryTextureAsset) and "texture_material" in session.capabilities
            or isinstance(self._asset, LibraryModelAsset) and "usd_model" in session.capabilities
        )

    def set_blender_sessions(self, sessions: list[BlenderSession], preferred_id: str = "") -> None:
        current = self.blender_session.currentData()
        current_id = current.id if isinstance(current, BlenderSession) else preferred_id
        self._blender_sessions = list(sessions)
        self.blender_session.clear()
        for session in sessions:
            self.blender_session.addItem(session.label, session)
        self.blender_refresh_button.setEnabled(True)
        self.blender_resolution.setEnabled(True)
        self.blender_world_mode.setEnabled(True)
        self.blender_session.setEnabled(True)
        target = next((index for index, session in enumerate(sessions) if session.id == current_id), 0)
        if sessions:
            self.blender_session.setCurrentIndex(target)
            self.blender_status.setText(f"Connected to {len(sessions)} Blender session(s).")
            self.blender_status.setStyleSheet("color: #78c995;")
        else:
            self.blender_status.setText(
                "No ShotBox Assets-enabled Blender session found. Install the extension in Settings and restart Blender."
            )
            self.blender_status.setStyleSheet("color: #e6b566;")
        self._update_blender_controls()

    def set_blender_busy(self, active: bool, message: str = "") -> None:
        self.blender_send_button.setEnabled(not active and self._blender_can_send())
        self.blender_refresh_button.setEnabled(not active)
        self.blender_resolution.setEnabled(not active)
        self.blender_world_mode.setEnabled(not active)
        self.blender_session.setEnabled(not active)
        if message:
            self.blender_status.setText(message)
            self.blender_status.setStyleSheet("color: #8792a1;")

    def set_blender_result(self, message: str, success: bool) -> None:
        self.set_blender_busy(False)
        self.blender_status.setText(message)
        self.blender_status.setStyleSheet(f"color: {'#78c995' if success else '#ef7d7d'};")

    def _update_blender_controls(self) -> None:
        supported = isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset))
        multiple = supported and len(self._blender_sessions) > 1
        self.blender_session_label.setVisible(multiple)
        self.blender_session.setVisible(multiple)
        self.blender_send_button.setVisible(supported)
        self.blender_send_button.setEnabled(self._blender_can_send())
        session = self.blender_session.currentData()
        if isinstance(self._asset, LibraryTextureAsset) and isinstance(session, BlenderSession) and "texture_material" not in session.capabilities:
            self.blender_status.setText("Update the Blender plug-in in Settings and restart Blender.")
            self.blender_status.setStyleSheet("color: #e6b566;")
        if isinstance(self._asset, LibraryModelAsset) and isinstance(session, BlenderSession) and "usd_model" not in session.capabilities:
            self.blender_status.setText("Update the Blender plug-in in Settings and restart Blender.")
            self.blender_status.setStyleSheet("color: #e6b566;")
        elif isinstance(self._asset, LibraryModelAsset) and not self._asset.usd_ready:
            self.blender_status.setText("This model has no managed USD file. Download or import USD first.")
            self.blender_status.setStyleSheet("color: #e6b566;")

    def _send_to_blender(self) -> None:
        session = self.blender_session.currentData()
        if isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)) and isinstance(session, BlenderSession):
            self.blender_send_requested.emit(
                self._asset,
                str(self.blender_resolution.currentData() or self.blender_resolution.currentText()),
                str(self.blender_world_mode.currentData()),
                session,
            )

    def _blender_can_send(self) -> bool:
        session = self.blender_session.currentData()
        if not isinstance(self._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)) or not isinstance(session, BlenderSession):
            return False
        return self.blender_resolution.count() > 0 and (
            isinstance(self._asset, LibraryHdriAsset)
            or isinstance(self._asset, LibraryTextureAsset) and "texture_material" in session.capabilities
            or isinstance(self._asset, LibraryModelAsset) and "usd_model" in session.capabilities
        )

    def _configure_texture_dcc(self, asset: LibraryTextureAsset) -> None:
        labels = sorted(asset.resolutions, key=lambda value: (_resolution_number(value), value))
        default = default_texture_resolution(asset.resolutions)
        for combo in (self.houdini_resolution, self.blender_resolution):
            combo.clear()
            combo.addItems(labels)
            combo.setCurrentText(default)
        self.blender_world_mode.hide()
        self.houdini_target.hide()
        self.blender_send_button.setText("Send material to Blender")
        self.houdini_send_button.setText("Send material to Houdini")
        self._update_houdini_controls()
        self._update_blender_controls()
        self.dcc_title.show()
        self.dcc_app.show()
        self.dcc_stack.show()
        self._dcc_app_changed(self.dcc_app.currentIndex())
        self.export_footer.show()

    def _configure_model_dcc(self, asset: LibraryModelAsset) -> None:
        options = model_export_options(asset)
        for combo in (self.houdini_resolution, self.blender_resolution):
            combo.clear()
            for record in options:
                combo.addItem(model_export_label(record), record.path)
        self.blender_world_mode.hide()
        self.houdini_target.show()
        self.blender_send_button.setText("Import model into Blender")
        self.houdini_send_button.setText("Import model into Houdini")
        self._update_houdini_controls()
        self._update_blender_controls()
        self.dcc_title.show()
        self.dcc_app.show()
        self.dcc_stack.show()
        self._dcc_app_changed(self.dcc_app.currentIndex())
        self.export_footer.show()

    def set_hdri_rendering(self, active: bool, message: str = "") -> None:
        if not isinstance(self._asset, LibraryHdriAsset):
            return
        self.hdri_render_button.setEnabled(not active)
        self.hdri_cancel_button.setVisible(active)
        if message:
            self.hdri_render_status.setText(message)
            self.hdri_render_status.show()

    def set_model_conversion_busy(self, active: bool, message: str = "") -> None:
        self.model_convert_button.setEnabled(
            not active
            and isinstance(self._asset, LibraryModelAsset)
            and bool(model_conversion_sources(self._asset))
        )
        self.model_convert_cancel.setVisible(active)
        self.model_rescan_button.setEnabled(not active)
        self.model_convert_status.setVisible(bool(message))
        if message:
            self.model_convert_status.setText(message)
            self.model_convert_status.setStyleSheet("color:#8792a1;")

    def set_model_conversion_result(self, message: str, success: bool) -> None:
        self.set_model_conversion_busy(False, message)
        self.model_convert_status.setStyleSheet(
            f"color:{'#78c995' if success else '#ef7d7d'};"
        )

    def set_model_rescan_busy(self, active: bool, message: str = "") -> None:
        self.model_rescan_button.setEnabled(
            not active and isinstance(self._asset, LibraryModelAsset)
        )
        self.model_convert_button.setEnabled(
            not active
            and isinstance(self._asset, LibraryModelAsset)
            and bool(model_conversion_sources(self._asset))
        )
        self.model_rescan_cancel.setVisible(active)
        self.model_rescan_status.setVisible(bool(message))
        if message:
            self.model_rescan_status.setText(message)
            self.model_rescan_status.setStyleSheet("color:#8792a1;")

    def set_model_rescan_result(self, message: str, success: bool) -> None:
        self.set_model_rescan_busy(False, message)
        self.model_rescan_status.setStyleSheet(
            f"color:{'#78c995' if success else '#ef7d7d'};"
        )

    def _edit(self) -> None:
        if self._asset:
            self.edit_requested.emit(self._asset)

    def _toggle_stock_playback(self) -> None:
        if not isinstance(self._asset, LibraryStockAsset):
            return
        if self.stock_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.stock_player.pause()
        else:
            self.stock_player.play()

    def _stock_position_changed(self, position: int) -> None:
        if not self.stock_seek.isSliderDown():
            self.stock_seek.setValue(position)
        self.stock_time.setText(f"{_media_time(position)} / {_media_time(self.stock_player.duration())}")

    def _stock_duration_changed(self, duration: int) -> None:
        self.stock_seek.setRange(0, max(0, duration))
        self._stock_position_changed(self.stock_player.position())

    def _stock_playback_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.stock_play.setText(
            "Pause" if playing else "Play"
        )
        self.stock_playback_active_changed.emit(playing)

    def _set_stock_muted(self, muted: bool) -> None:
        if self.stock_audio is not None:
            self.stock_audio.setMuted(muted)

    def _set_stock_volume(self, value: int) -> None:
        if self.stock_audio is not None:
            self.stock_audio.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _open_stock_preview(self) -> None:
        if isinstance(self._asset, LibraryStockAsset):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._asset.preview_path)))

    def _copy_path(self) -> None:
        if self._asset:
            path = (
                self._asset.source_path
                if isinstance(self._asset, LibraryStockAsset)
                else self._asset.asset_dir
            )
            QApplication.clipboard().setText(str(path))

    def _reveal(self) -> None:
        if self._asset:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._asset.asset_dir)))

    def _copy_extra_path(self) -> None:
        value = str(self.extra_selector.currentData() or "")
        if value:
            QApplication.clipboard().setText(value)

    def _reveal_extra(self) -> None:
        value = str(self.extra_selector.currentData() or "")
        if value:
            QDesktopServices.openUrl(QUrl.fromLocalFile(value))

    def _view_3d(self) -> None:
        if not isinstance(self._asset, LibraryModelAsset) or not self._asset.usd_path:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._asset.usd_path))):
            QMessageBox.warning(self, "No USD viewer", "No application is associated with this USD file. Install or configure an external USD viewer and try again.")


class HdriRenderSignals(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class HdriRenderWorker(QRunnable):
    def __init__(self, library_path: str, asset_id: str, blender_path: str, token: CancelToken) -> None:
        super().__init__()
        self.library_path = library_path
        self.asset_id = asset_id
        self.blender_path = blender_path
        self.token = token
        self.signals = HdriRenderSignals()

    def run(self) -> None:
        try:
            result = LibraryRepository(self.library_path, blender_path=self.blender_path).render_hdri_preview(
                self.asset_id,
                progress=self.signals.progress.emit,
                cancel_token=self.token,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=5))
        else:
            self.signals.finished.emit(result)


class ModelConversionSignals(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class ModelConversionWorker(QRunnable):
    def __init__(
        self,
        library_path: str,
        asset_id: str,
        source_path: str,
        orientation: str,
        blender_path: str,
        token: CancelToken,
    ) -> None:
        super().__init__()
        self.library_path = library_path
        self.asset_id = asset_id
        self.source_path = source_path
        self.orientation = orientation
        self.blender_path = blender_path
        self.token = token
        self.signals = ModelConversionSignals()

    def run(self) -> None:
        try:
            result = LibraryRepository(
                self.library_path, blender_path=self.blender_path,
            ).convert_model_to_usd(
                self.asset_id,
                self.source_path,
                self.orientation,
                progress=self.signals.progress.emit,
                cancel_token=self.token,
            )
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)


class ModelRescanWorker(QRunnable):
    def __init__(
        self,
        library_path: str,
        asset_id: str,
        blender_path: str,
        token: CancelToken,
        *,
        scan: ModelAssetRescan | None = None,
        selection: ModelRescanSelection | None = None,
    ) -> None:
        super().__init__()
        self.library_path = library_path
        self.asset_id = asset_id
        self.blender_path = blender_path
        self.token = token
        self.scan = scan
        self.selection = selection
        self.signals = ModelConversionSignals()

    def run(self) -> None:
        try:
            repository = LibraryRepository(
                self.library_path, blender_path=self.blender_path,
            )
            if self.scan is None:
                result = repository.rescan_model_asset(
                    self.asset_id,
                    progress=self.signals.progress.emit,
                    cancel_token=self.token,
                )
            else:
                result = repository.apply_model_asset_rescan(
                    self.asset_id,
                    self.scan,
                    self.selection or ModelRescanSelection(),
                    cancel_token=self.token,
                )
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)


class PolyHavenWorkerSignals(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class PolyHavenWorker(QRunnable):
    def __init__(
        self,
        operation: str,
        library_path: str,
        *,
        asset_id: str = "",
        plan: PolyHavenDownloadPlan | None = None,
        token: CancelToken | None = None,
    ) -> None:
        super().__init__()
        self.operation = operation
        self.library_path = library_path
        self.asset_id = asset_id
        self.plan = plan
        self.token = token
        self.signals = PolyHavenWorkerSignals()

    def run(self) -> None:
        try:
            repository = LibraryRepository(self.library_path)
            if self.operation == "lookup":
                result = repository.polyhaven_options(self.asset_id)
            elif self.operation == "download" and self.plan is not None:
                result = repository.install_polyhaven_download(
                    self.plan,
                    progress=self.signals.progress.emit,
                    cancel_token=self.token,
                )
            else:
                raise RuntimeError("Invalid Poly Haven worker operation.")
        except Exception as error:
            self.signals.failed.emit(self.operation, str(error))
        else:
            self.signals.finished.emit(self.operation, result)


class HoudiniWorkerSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class HoudiniWorker(QRunnable):
    def __init__(
        self,
        operation: str,
        *,
        session: HoudiniSession | None = None,
        asset: LibraryHdriAsset | LibraryTextureAsset | LibraryModelAsset | None = None,
        resolution: str = "",
        target: str = "lop",
        library_path: str = "",
    ) -> None:
        super().__init__()
        self.operation = operation
        self.session = session
        self.asset = asset
        self.resolution = resolution
        self.target = target
        self.library_path = library_path
        self.signals = HoudiniWorkerSignals()

    def run(self) -> None:
        try:
            client = HoudiniBridgeClient(timeout=1.0 if self.operation == "discover" else 300.0)
            if self.operation == "discover":
                result = client.discover_sessions()
            else:
                if self.session is None or self.asset is None:
                    raise HoudiniBridgeError("The Houdini send request is incomplete.")
                if isinstance(self.asset, LibraryTextureAsset):
                    payload = prepare_texture_export(self.asset, self.resolution, self.library_path)
                    result = client.create_texture_material(self.session, payload)
                elif isinstance(self.asset, LibraryModelAsset):
                    payload = prepare_model_export(self.asset, self.resolution, self.library_path)
                    result = client.import_usd_model(self.session, payload, target=self.target)
                else:
                    label, hdri_file = choose_hdri_file(self.asset, self.resolution)
                    managed_path = self.asset.asset_dir / hdri_file.path
                    if not managed_path.is_file():
                        raise HoudiniBridgeError(f"Managed HDRI file is missing: {managed_path}")
                    result = client.create_hdri_dome(
                        self.session,
                        asset_id=self.asset.id,
                        asset_name=self.asset.name,
                        resolution=label,
                        hdri_path=managed_path,
                        library_root=Path(self.library_path),
                    )
        except Exception as error:
            self.signals.failed.emit(self.operation, str(error))
        else:
            self.signals.finished.emit(self.operation, result)


class BlenderWorkerSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)


class BlenderWorker(QRunnable):
    def __init__(
        self,
        operation: str,
        *,
        session: BlenderSession | None = None,
        asset: LibraryHdriAsset | LibraryTextureAsset | LibraryModelAsset | None = None,
        resolution: str = "",
        world_mode: str = "new",
        library_path: str = "",
    ) -> None:
        super().__init__()
        self.operation = operation
        self.session = session
        self.asset = asset
        self.resolution = resolution
        self.world_mode = world_mode
        self.library_path = library_path
        self.signals = BlenderWorkerSignals()

    def run(self) -> None:
        try:
            client = BlenderBridgeClient(timeout=1.0 if self.operation == "discover" else 300.0)
            if self.operation == "discover":
                result = client.discover_sessions()
            else:
                if self.session is None or self.asset is None:
                    raise BlenderBridgeError("The Blender send request is incomplete.")
                if isinstance(self.asset, LibraryTextureAsset):
                    payload = prepare_texture_export(self.asset, self.resolution, self.library_path)
                    result = client.create_texture_material(self.session, payload)
                elif isinstance(self.asset, LibraryModelAsset):
                    payload = prepare_model_export(self.asset, self.resolution, self.library_path)
                    result = client.import_usd_model(self.session, payload)
                else:
                    label, hdri_file = choose_hdri_file(self.asset, self.resolution)
                    managed_path = self.asset.asset_dir / hdri_file.path
                    if not managed_path.is_file():
                        raise BlenderBridgeError(f"Managed HDRI file is missing: {managed_path}")
                    result = client.set_hdri_world(
                        self.session,
                        asset_id=self.asset.id,
                        asset_name=self.asset.name,
                        resolution=label,
                        hdri_path=managed_path,
                        library_root=Path(self.library_path),
                        world_mode=self.world_mode,
                    )
        except Exception as error:
            self.signals.failed.emit(self.operation, str(error))
        else:
            self.signals.finished.emit(self.operation, result)


class AssetsTab(QWidget):
    open_settings_requested = pyqtSignal()
    material_updated = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._library_path = ""
        self._blender_path = ""
        self._hdri_render_worker: HdriRenderWorker | None = None
        self._hdri_render_token: CancelToken | None = None
        self._model_conversion_worker: ModelConversionWorker | None = None
        self._model_conversion_token: CancelToken | None = None
        self._model_rescan_worker: ModelRescanWorker | None = None
        self._model_rescan_token: CancelToken | None = None
        self._houdini_worker: HoudiniWorker | None = None
        self._houdini_sessions: list[HoudiniSession] = []
        self._blender_worker: BlenderWorker | None = None
        self._blender_sessions: list[BlenderSession] = []
        self._polyhaven_worker: PolyHavenWorker | None = None
        self._polyhaven_token: CancelToken | None = None
        self._polyhaven_options: PolyHavenOptions | None = None
        self._polyhaven_asset_id = ""
        self._all_assets: list[AssetRecord] = []
        self._category_catalogs = {
            asset_type: default_category_catalog(asset_type)
            for asset_type in ("texture_set", "atlas", "hdri", "model", "stock")
        }
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        self.title = QLabel("PBR Textures")
        self.title.hide()
        self.section = AssetTypeTabs()
        self.section.setMinimumWidth(290)
        self.section.setStyleSheet(
            "QTabBar::tab { min-width:70px; padding:6px 10px; }"
        )
        self.section.currentIndexChanged.connect(self._section_changed)
        self.count = QLabel("Library not configured")
        self.count.setObjectName("mutedLabel")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search textures, tags, providers, or maps…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self.category = QComboBox()
        self.category.addItem("All")
        self.category.hide()
        self.channel = QComboBox()
        self.channel.addItems(["All", *PBR_CHANNELS])
        self.sort = QComboBox()
        self.sort.addItems(["Name", "Category", "Resolution", "Duration", "Import Date"])
        self.toolbar = QFrame()
        self.toolbar.setObjectName("assetsToolbar")
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(8, 7, 8, 7)
        toolbar_layout.setSpacing(8)
        toolbar_layout.addWidget(self.section)
        toolbar_layout.addWidget(self.search, 1)
        toolbar_layout.addWidget(self.channel)
        toolbar_layout.addWidget(self.sort)
        toolbar_layout.addWidget(self.count)
        root.addWidget(self.toolbar)

        self.source_model = TextureListModel(parent=self)
        self.proxy = TextureFilterModel(self)
        self.proxy.setSourceModel(self.source_model)
        self.proxy.setDynamicSortFilter(True)
        self.proxy.sort(0)
        self.view = QListView()
        self.view.setModel(self.proxy)
        self.card_delegate = TextureCardDelegate(self.view)
        self.view.setItemDelegate(self.card_delegate)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setSpacing(2)
        self.view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.stock_hover_previews = StockHoverPreviewController(
            self.view, self.card_delegate
        )
        self.detail = DetailPanel()
        self.detail.stock_playback_active_changed.connect(
            self.stock_hover_previews.set_suspended
        )
        self.detail.edit_requested.connect(self._edit_material)
        self.detail.model_convert_requested.connect(self._convert_model_to_usd)
        self.detail.model_convert_canceled.connect(self._cancel_model_conversion)
        self.detail.model_rescan_requested.connect(self._rescan_model_asset)
        self.detail.model_rescan_canceled.connect(self._cancel_model_rescan)
        self.detail.hdri_render_requested.connect(self._render_hdri_preview)
        self.detail.hdri_render_canceled.connect(self._cancel_hdri_render)
        self.detail.houdini_refresh_requested.connect(self.refresh_houdini_sessions)
        self.detail.houdini_send_requested.connect(self._send_hdri_to_houdini)
        self.detail.blender_refresh_requested.connect(self.refresh_blender_sessions)
        self.detail.blender_send_requested.connect(self._send_hdri_to_blender)
        self.detail.polyhaven_check_requested.connect(self._check_polyhaven)
        self.detail.polyhaven_download_requested.connect(self._download_polyhaven)
        self.detail.polyhaven_cancel_requested.connect(self._cancel_polyhaven)
        self.detail_scroll = self.detail.details_scroll
        self.splitter = QSplitter()
        self.splitter.setObjectName("assetsSplitter")
        self.splitter.addWidget(self.view)
        self.splitter.addWidget(self.detail)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([900, 380])
        saved_splitter = QSettings().value("assets/splitter_state")
        if saved_splitter:
            self.splitter.restoreState(saved_splitter)
        self.splitter.splitterMoved.connect(self._save_splitter_state)
        self.category_rail = CategoryRail()
        self.catalog = QWidget()
        catalog_layout = QHBoxLayout(self.catalog)
        catalog_layout.setContentsMargins(0, 0, 0, 0)
        catalog_layout.setSpacing(8)
        catalog_layout.addWidget(self.category_rail)
        catalog_layout.addWidget(self.splitter, 1)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        self.empty_title = QLabel("No library configured")
        self.empty_title.setObjectName("pageTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message = QLabel("Choose a writable library folder in Settings.")
        self.empty_message.setObjectName("mutedLabel")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_button = QPushButton("Open Settings")
        self.settings_button.clicked.connect(self.open_settings_requested)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.settings_button)
        button_row.addStretch()
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_message)
        empty_layout.addLayout(button_row)
        empty_layout.addStretch()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.catalog)
        self.stack.addWidget(empty)
        root.addWidget(self.stack, 1)

        self.search.textChanged.connect(self._filter)
        self.category.currentTextChanged.connect(self._filter)
        self.category_rail.category_changed.connect(self._rail_category_changed)
        self.channel.currentTextChanged.connect(self._filter)
        self.sort.currentTextChanged.connect(self.proxy.set_sort_mode)
        self.view.clicked.connect(self._selected)
        self.proxy.rowsInserted.connect(self._update_count)
        self.proxy.rowsRemoved.connect(self._update_count)
        self.proxy.modelReset.connect(self._update_count)
        self.stack.setCurrentIndex(1)
        # Discovery is started when a supported asset is selected or the user
        # presses Refresh. Avoid keeping network workers alive for empty,
        # Stock-only, or short-lived catalog widgets.

    def load_library(self, path: str, selected_id: str = "") -> None:
        self.stock_hover_previews.stop()
        self._library_path = path
        assets: list[AssetRecord] = []
        warnings: list[str] = []
        if path and LibraryRepository(path).root.is_dir():
            repository = LibraryRepository(path)
            category_store = CategoryConfigStore(path)
            category_store.ensure_defaults()
            self._category_catalogs = category_store.load_all()
            assets = repository.list_assets()
            warnings = [*repository.last_warnings, *category_store.last_warnings]
        self._all_assets = assets
        visible_assets = [asset for asset in assets if asset.asset_type == self._section_type()]
        self.card_delegate.clear_cache()
        self.source_model.replace(visible_assets)
        self._rebuild_categories()
        self._rebuild_facets()
        if not path:
            self._show_empty("No library configured", "Choose a writable library folder in Settings.", True)
        elif not visible_assets:
            kind = (
                "model" if self._section_type() == "model"
                else "HDRI" if self._section_type() == "hdri"
                else "atlas" if self._section_type() == "atlas"
                else "Stock clip" if self._section_type() == "stock"
                else "texture"
            )
            message = f"Use the Importer tab to add your first {kind} asset."
            if warnings:
                message = f"No valid assets found. {len(warnings)} manifest warning(s)."
            self._show_empty(f"Your {kind} library is empty", message, False)
        else:
            self.stack.setCurrentIndex(0)
            noun = (
                "models" if self._section_type() == "model"
                else "HDRIs" if self._section_type() == "hdri"
                else "atlases" if self._section_type() == "atlas"
                else "Stock clips" if self._section_type() == "stock"
                else "texture sets"
            )
            self.count.setText(f"{len(visible_assets)} asset{'s' if len(visible_assets) != 1 else ''}")
            self.count.setToolTip(
                f"{len(visible_assets)} imported {noun}" + (f" · {len(warnings)} manifest warnings" if warnings else "")
            )
            selected = self._proxy_index_for_id(selected_id) if selected_id else self.proxy.index(0, 0)
            if selected.isValid():
                self.view.setCurrentIndex(selected)
                self.detail.show_asset(selected.data(ASSET_ROLE))

    def reload_library(self) -> None:
        self.load_library(self._library_path)

    def _proxy_index_for_id(self, asset_id: str) -> QModelIndex:
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 0)
            asset = index.data(ASSET_ROLE)
            if asset and asset.id == asset_id:
                return index
        return QModelIndex()

    def _edit_material(self, asset: AssetRecord) -> None:
        dialog = MaterialEditDialog(
            asset,
            self,
            self._category_catalogs[self._section_type()].names,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_material_edit(asset, dialog.metadata_update())

    def _save_material_edit(self, asset: AssetRecord, update: AssetMetadataUpdate) -> bool:
        try:
            updated = LibraryRepository(self._library_path).update_asset_metadata(asset.id, update)
        except Exception as error:
            QMessageBox.critical(self, "Could not update material", str(error))
            return False
        self.load_library(self._library_path, selected_id=updated.id)
        self.material_updated.emit(updated)
        return True

    def _show_empty(self, title: str, message: str, show_settings: bool) -> None:
        self.empty_title.setText(title)
        self.empty_message.setText(message)
        self.settings_button.setVisible(show_settings)
        self.stack.setCurrentIndex(1)
        self.count.setText("0 assets")
        self.count.setToolTip(message)
        self.detail.clear()

    def _rebuild_categories(self) -> None:
        current = self.category.currentText()
        catalog = self._category_catalogs[self._section_type()]
        self.proxy.set_category_catalog(catalog)
        used = {
            category
            for asset in self.source_model.assets
            for category in _asset_filter_categories(asset, catalog)
        }
        ordered = catalog.ordered_used(used)
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(["All", *ordered])
        self.category.setCurrentText(current if self.category.findText(current) >= 0 else "All")
        self.category.blockSignals(False)
        icon_ids = {name: catalog.icon_for(name) for name in ordered}
        self.category_rail.set_categories(
            ordered,
            icon_ids,
            self._category_counts(),
            selected=self.category.currentText(),
        )
        self._filter()

    def _rebuild_facets(self) -> None:
        current = self.channel.currentText()
        self.channel.blockSignals(True)
        self.channel.clear()
        if self._section_type() == "model":
            values = {
                value
                for asset in self.source_model.assets
                for value in (*asset.file_format.split("/"), *asset.lods, "USD Ready" if asset.usd_ready else "No USD")
                if value
            }
            self.channel.addItems(["All", *sorted(values, key=str.casefold)])
            self.channel.setToolTip("Filter by model format, LOD, or USD readiness")
        elif self._section_type() == "hdri":
            values = {
                value
                for asset in self.source_model.assets
                for value in (*asset.resolutions.keys(), *asset.file_format.split("/"))
                if value
            }
            self.channel.addItems(["All", *sorted(values, key=str.casefold)])
            self.channel.setToolTip("Filter by HDRI resolution or format")
        elif self._section_type() == "stock":
            values = {
                value
                for asset in self.source_model.assets
                if isinstance(asset, LibraryStockAsset)
                for value in (
                    asset.media_info.codec.upper(),
                    asset.source_format.upper(),
                    asset.resolution,
                    "Alpha" if asset.media_info.alpha == "yes" else "Opaque",
                    "Audio" if asset.media_info.has_audio else "Silent",
                )
                if value
            }
            self.channel.addItems(["All", *sorted(values, key=str.casefold)])
            self.channel.setToolTip("Filter by dimensions, codec, alpha, or audio")
        else:
            self.channel.addItems(["All", *PBR_CHANNELS])
            self.channel.setToolTip("Filter by required texture channel")
        self.channel.setCurrentText(current if self.channel.findText(current) >= 0 else "All")
        self.channel.blockSignals(False)
        self._filter()

    def _selected(self, index: QModelIndex) -> None:
        asset = index.data(ASSET_ROLE)
        if asset:
            self.detail.show_asset(asset)

    def _filter(self, *_args) -> None:
        self.stock_hover_previews.stop()
        self.proxy.set_filters(self.search.text(), self.category.currentText(), self.channel.currentText())
        self.category_rail.set_counts(self._category_counts())
        current = self.view.currentIndex()
        if not current.isValid() and self.proxy.rowCount():
            current = self.proxy.index(0, 0)
            self.view.setCurrentIndex(current)
            self.detail.show_asset(current.data(ASSET_ROLE))
        elif not self.proxy.rowCount():
            self.detail.clear()
        self._update_count()

    def _category_counts(self) -> dict[str, int]:
        counts = {"All": 0}
        query = self.search.text()
        facet = self.channel.currentText()
        for asset in self.source_model.assets:
            if not asset.matches(query, "All", facet):
                continue
            counts["All"] += 1
            for category in _asset_filter_categories(
                asset,
                self._category_catalogs[self._section_type()],
            ):
                counts[category] = counts.get(category, 0) + 1
        return counts

    def _rail_category_changed(self, category: str) -> None:
        self.category.setCurrentText(category)

    def _update_count(self, *_args) -> None:
        total = len(self.source_model.assets)
        shown = self.proxy.rowCount()
        if total:
            noun = (
                "models" if self._section_type() == "model"
                else "HDRIs" if self._section_type() == "hdri"
                else "atlases" if self._section_type() == "atlas"
                else "Stock clips" if self._section_type() == "stock"
                else "texture sets"
            )
            self.count.setText(f"{shown} / {total}")
            self.count.setToolTip(f"{shown} of {total} imported {noun}")

    def set_thumbnail_size(self, size: str) -> None:
        self.stock_hover_previews.stop()
        self.card_delegate.set_thumbnail_size(size)
        self.view.doItemsLayout()
        self.view.viewport().update()

    def set_stock_hover_previews(self, enabled: bool) -> None:
        self.stock_hover_previews.set_enabled(enabled)

    def _save_splitter_state(self, *_args) -> None:
        QSettings().setValue("assets/splitter_state", self.splitter.saveState())

    def set_hdri_preview_settings(self, blender_path: str) -> None:
        self._blender_path = blender_path

    def refresh_houdini_sessions(self) -> None:
        if self._houdini_worker is not None:
            return
        worker = HoudiniWorker("discover")
        self._houdini_worker = worker
        if isinstance(self.detail._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)):
            self.detail.set_houdini_busy(True, "Looking for running Houdini sessions…")
        worker.signals.finished.connect(self._houdini_finished)
        worker.signals.failed.connect(self._houdini_failed)
        QThreadPool.globalInstance().start(worker)

    def _send_hdri_to_houdini(
        self,
        asset: LibraryHdriAsset | LibraryTextureAsset | LibraryModelAsset,
        resolution: str,
        target: str,
        session: HoudiniSession,
    ) -> None:
        if self._houdini_worker is not None or not self._library_path:
            return
        QSettings().setValue("houdini/last_session_id", session.id)
        worker = HoudiniWorker(
            "send",
            session=session,
            asset=asset,
            resolution=resolution,
            target=target,
            library_path=self._library_path,
        )
        self._houdini_worker = worker
        noun = "material" if isinstance(asset, LibraryTextureAsset) else "model" if isinstance(asset, LibraryModelAsset) else "HDRI"
        verb = "Importing" if isinstance(asset, LibraryModelAsset) else "Sending"
        self.detail.set_houdini_busy(True, f"{verb} {asset.name} {noun} in Houdini…")
        worker.signals.finished.connect(self._houdini_finished)
        worker.signals.failed.connect(self._houdini_failed)
        QThreadPool.globalInstance().start(worker)

    def _houdini_finished(self, operation: str, result: object) -> None:
        self._houdini_worker = None
        if operation == "discover":
            self._houdini_sessions = list(result)
            preferred = str(QSettings().value("houdini/last_session_id", "") or "")
            self.detail.set_houdini_sessions(self._houdini_sessions, preferred)
            return
        response = result
        if isinstance(response, BridgeResponse):
            self.detail.set_houdini_result(response.diagnostic or f"Created {response.node_path}.", True)
        else:
            self.detail.set_houdini_result("Houdini completed the request.", True)

    def _houdini_failed(self, operation: str, message: str) -> None:
        self._houdini_worker = None
        if operation == "discover":
            self._houdini_sessions = []
            self.detail.set_houdini_sessions([])
        else:
            self.detail.set_houdini_result(message, False)

    def refresh_blender_sessions(self) -> None:
        if self._blender_worker is not None:
            return
        worker = BlenderWorker("discover")
        self._blender_worker = worker
        if isinstance(self.detail._asset, (LibraryHdriAsset, LibraryTextureAsset, LibraryModelAsset)):
            self.detail.set_blender_busy(True, "Looking for running Blender sessions…")
        worker.signals.finished.connect(self._blender_finished)
        worker.signals.failed.connect(self._blender_failed)
        QThreadPool.globalInstance().start(worker)

    def _send_hdri_to_blender(
        self,
        asset: LibraryHdriAsset | LibraryTextureAsset | LibraryModelAsset,
        resolution: str,
        world_mode: str,
        session: BlenderSession,
    ) -> None:
        if self._blender_worker is not None or not self._library_path:
            return
        QSettings().setValue("blender_bridge/last_session_id", session.id)
        worker = BlenderWorker(
            "send",
            session=session,
            asset=asset,
            resolution=resolution,
            world_mode=world_mode,
            library_path=self._library_path,
        )
        self._blender_worker = worker
        noun = "material" if isinstance(asset, LibraryTextureAsset) else "model" if isinstance(asset, LibraryModelAsset) else "HDRI"
        verb = "Importing" if isinstance(asset, LibraryModelAsset) else "Sending"
        self.detail.set_blender_busy(True, f"{verb} {asset.name} {noun} in Blender…")
        worker.signals.finished.connect(self._blender_finished)
        worker.signals.failed.connect(self._blender_failed)
        QThreadPool.globalInstance().start(worker)

    def _blender_finished(self, operation: str, result: object) -> None:
        self._blender_worker = None
        if operation == "discover":
            self._blender_sessions = list(result)
            preferred = str(QSettings().value("blender_bridge/last_session_id", "") or "")
            self.detail.set_blender_sessions(self._blender_sessions, preferred)
            return
        if isinstance(result, BlenderBridgeResponse):
            self.detail.set_blender_result(result.diagnostic or f"Updated {result.world_name}.", True)
        else:
            self.detail.set_blender_result("Blender completed the request.", True)

    def _blender_failed(self, operation: str, message: str) -> None:
        self._blender_worker = None
        if operation == "discover":
            self._blender_sessions = []
            self.detail.set_blender_sessions([])
        else:
            self.detail.set_blender_result(message, False)

    def _check_polyhaven(self, asset: AssetRecord) -> None:
        if self._polyhaven_worker is not None or not self._library_path:
            return
        self._polyhaven_asset_id = asset.id
        self._polyhaven_options = None
        worker = PolyHavenWorker("lookup", self._library_path, asset_id=asset.id)
        self._polyhaven_worker = worker
        self.detail.set_polyhaven_busy(True, "Contacting Poly Haven…")
        self.detail.polyhaven_cancel_button.hide()
        worker.signals.finished.connect(self._polyhaven_finished)
        worker.signals.failed.connect(self._polyhaven_failed)
        QThreadPool.globalInstance().start(worker)

    def _download_polyhaven(self, asset: AssetRecord, kind: str, resolution: str) -> None:
        if self._polyhaven_worker is not None or not self._library_path:
            return
        if self._polyhaven_options is None or asset.id != self._polyhaven_asset_id:
            self.detail.set_polyhaven_result("Check online resolutions again before downloading.", False)
            return
        try:
            plan = LibraryRepository(self._library_path).prepare_polyhaven_download(
                asset.id, kind, resolution, options=self._polyhaven_options
            )
        except Exception as error:
            self.detail.set_polyhaven_result(str(error), False)
            return
        channels = sorted({item.channel for item in plan.files if item.channel}, key=str.casefold)
        details = (
            f"Download {kind.upper()} at {resolution}?\n\n"
            f"{len(plan.files)} file(s) · {_human_size(plan.total_size)}"
            + (f"\nChannels: {', '.join(channels)}" if channels else f"\nIncludes {max(0, len(plan.files) - 1)} dependency file(s).")
        )
        if plan.from_cache:
            details += "\n\nThis plan uses the preserved catalog because the live API was unavailable."
        answer = QMessageBox.question(
            self,
            "Confirm Poly Haven download",
            details,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        token = CancelToken()
        worker = PolyHavenWorker("download", self._library_path, plan=plan, token=token)
        self._polyhaven_token = token
        self._polyhaven_worker = worker
        self.detail.set_polyhaven_busy(True, f"Downloading {kind.upper()} {resolution}…", 0, plan.total_size)
        worker.signals.progress.connect(self._polyhaven_progressed)
        worker.signals.finished.connect(self._polyhaven_finished)
        worker.signals.failed.connect(self._polyhaven_failed)
        QThreadPool.globalInstance().start(worker)

    def _polyhaven_progressed(self, progress) -> None:
        self.detail.set_polyhaven_busy(
            True,
            f"Downloading {progress.file}…",
            progress.completed_bytes,
            progress.total_bytes,
        )

    def _polyhaven_finished(self, operation: str, result: object) -> None:
        self._polyhaven_worker = None
        self._polyhaven_token = None
        if operation == "lookup" and isinstance(result, PolyHavenOptions):
            if self.detail._asset and self.detail._asset.id == self._polyhaven_asset_id:
                self._polyhaven_options = result
                self.detail.apply_polyhaven_options(result)
                self.detail.set_polyhaven_busy(False)
            return
        asset = getattr(result, "asset", None)
        if asset is not None:
            skipped = bool(getattr(result, "skipped", False))
            self.load_library(self._library_path, selected_id=asset.id)
            self.detail.set_polyhaven_result(
                "The selected package is already complete." if skipped else
                f"Downloaded {getattr(result, 'kind', 'files').upper()} {getattr(result, 'resolution', '')}.",
                True,
            )

    def _polyhaven_failed(self, operation: str, message: str) -> None:
        self._polyhaven_worker = None
        self._polyhaven_token = None
        if self.detail._asset and self.detail._asset.id == self._polyhaven_asset_id:
            self.detail.set_polyhaven_result(message, False)

    def _cancel_polyhaven(self) -> None:
        if self._polyhaven_token:
            self._polyhaven_token.cancel()
            self.detail.set_polyhaven_busy(True, "Canceling download safely…")

    def _render_hdri_preview(self, asset: AssetRecord) -> None:
        if not isinstance(asset, LibraryHdriAsset) or self._hdri_render_worker is not None:
            return
        token = CancelToken()
        worker = HdriRenderWorker(self._library_path, asset.id, self._blender_path, token)
        self._hdri_render_token = token
        self._hdri_render_worker = worker
        self.detail.set_hdri_rendering(True, "Starting Blender…")
        worker.signals.progress.connect(lambda message: self.detail.set_hdri_rendering(True, message))
        worker.signals.finished.connect(self._hdri_render_finished)
        worker.signals.failed.connect(self._hdri_render_failed)
        QThreadPool.globalInstance().start(worker)

    def _cancel_hdri_render(self) -> None:
        if self._hdri_render_token:
            self._hdri_render_token.cancel()
            self.detail.set_hdri_rendering(True, "Canceling Blender safely…")

    def _hdri_render_finished(self, update) -> None:
        self._hdri_render_worker = None
        self._hdri_render_token = None
        self.load_library(self._library_path, selected_id=update.asset.id)
        if update.render.status != "ready":
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("HDRI preview not rendered")
            dialog.setText(update.render.diagnostic or update.render.status)
            if update.render.log:
                dialog.setDetailedText(update.render.log)
            dialog.exec()

    def _hdri_render_failed(self, details: str) -> None:
        self._hdri_render_worker = None
        self._hdri_render_token = None
        self.detail.set_hdri_rendering(False, "Preview rendering failed; the previous preview was retained.")
        QMessageBox.critical(self, "Could not render HDRI preview", details)

    def _convert_model_to_usd(self, asset: AssetRecord) -> None:
        if (
            not isinstance(asset, LibraryModelAsset)
            or self._model_conversion_worker is not None
            or self._model_rescan_worker is not None
        ):
            return
        dialog = ModelConversionDialog(
            asset, self._library_path, self._blender_path, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        token = CancelToken()
        worker = ModelConversionWorker(
            self._library_path,
            asset.id,
            dialog.source_path,
            dialog.orientation_preset,
            self._blender_path,
            token,
        )
        self._model_conversion_token = token
        self._model_conversion_worker = worker
        self.detail.set_model_conversion_busy(True, "Starting headless Blender conversion…")
        worker.signals.progress.connect(
            lambda message: self.detail.set_model_conversion_busy(True, message)
        )
        worker.signals.finished.connect(self._model_conversion_finished)
        worker.signals.failed.connect(self._model_conversion_failed)
        QThreadPool.globalInstance().start(worker)

    def _cancel_model_conversion(self) -> None:
        if self._model_conversion_token:
            self._model_conversion_token.cancel()
            self.detail.set_model_conversion_busy(True, "Canceling Blender safely…")

    def _model_conversion_finished(self, update) -> None:
        self._model_conversion_worker = None
        self._model_conversion_token = None
        self.load_library(self._library_path, selected_id=update.asset.id)
        self.detail.set_model_conversion_result(
            f"Published preferred USDC with {update.conversion.mesh_count} mesh(es) "
            f"and {update.conversion.material_count} material binding(s).",
            True,
        )

    def _model_conversion_failed(self, message: str) -> None:
        self._model_conversion_worker = None
        self._model_conversion_token = None
        self.detail.set_model_conversion_result(
            message or "USD conversion failed; the previous model was retained.",
            False,
        )

    def _rescan_model_asset(self, asset: AssetRecord) -> None:
        if (
            not isinstance(asset, LibraryModelAsset)
            or self._model_rescan_worker is not None
            or self._model_conversion_worker is not None
        ):
            return
        token = CancelToken()
        worker = ModelRescanWorker(
            self._library_path, asset.id, self._blender_path, token,
        )
        self._model_rescan_token = token
        self._model_rescan_worker = worker
        self.detail.set_model_rescan_busy(True, "Scanning managed model files…")
        worker.signals.progress.connect(
            lambda message: self.detail.set_model_rescan_busy(True, message)
        )
        worker.signals.finished.connect(self._model_rescan_inventory_finished)
        worker.signals.failed.connect(self._model_rescan_failed)
        QThreadPool.globalInstance().start(worker)

    def _model_rescan_inventory_finished(self, scan: ModelAssetRescan) -> None:
        self._model_rescan_worker = None
        self._model_rescan_token = None
        self.detail.set_model_rescan_busy(False)
        asset = next(
            (
                value for value in self._all_assets
                if isinstance(value, LibraryModelAsset) and value.id == scan.asset_id
            ),
            None,
        )
        if asset is None:
            self.detail.set_model_rescan_result(
                "The asset is no longer loaded; reload the library and rescan.", False,
            )
            return
        dialog = ModelAssetRescanDialog(asset, scan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.detail.set_model_rescan_result("Rescan review canceled; no changes applied.", False)
            return
        token = CancelToken()
        worker = ModelRescanWorker(
            self._library_path,
            asset.id,
            self._blender_path,
            token,
            scan=scan,
            selection=dialog.selection,
        )
        self._model_rescan_token = token
        self._model_rescan_worker = worker
        self.detail.set_model_rescan_busy(True, "Applying reviewed model changes…")
        worker.signals.finished.connect(self._model_rescan_apply_finished)
        worker.signals.failed.connect(self._model_rescan_failed)
        QThreadPool.globalInstance().start(worker)

    def _model_rescan_apply_finished(self, update) -> None:
        self._model_rescan_worker = None
        self._model_rescan_token = None
        changes = len(update.added) + len(update.refreshed) + len(update.removed)
        self.load_library(self._library_path, selected_id=update.asset.id)
        self.detail.set_model_rescan_result(
            f"Updated {changes} model file{'s' if changes != 1 else ''}.", True,
        )

    def _cancel_model_rescan(self) -> None:
        if self._model_rescan_token:
            self._model_rescan_token.cancel()
            self.detail.set_model_rescan_busy(True, "Canceling model rescan safely…")

    def _model_rescan_failed(self, message: str) -> None:
        self._model_rescan_worker = None
        self._model_rescan_token = None
        self.detail.set_model_rescan_result(
            message or "Model rescan failed; the asset was not changed.", False,
        )

    def _section_type(self) -> str:
        return str(self.section.currentData() or "texture_set")

    def _section_changed(self, _index: int) -> None:
        mode = self._section_type()
        is_hdri = mode == "hdri"
        is_model = mode == "model"
        is_atlas = mode == "atlas"
        is_stock = mode == "stock"
        self.title.setText(
            "3D Models" if is_model else "HDRIs" if is_hdri else
            "Atlases" if is_atlas else "Stock Footage" if is_stock else "PBR Textures"
        )
        self.search.setPlaceholderText(
            "Search models, formats, LODs, providers, or USD status…" if is_model else
            "Search HDRIs, tags, providers, or formats…" if is_hdri else
            "Search atlases, tags, providers, or maps…" if is_atlas else
            "Search Stock clips, categories, codecs, or tags…" if is_stock else
            "Search textures, tags, providers, or maps…"
        )
        self.category.setCurrentText("All")
        self.category_rail.set_current("All", emit=False)
        self.load_library(self._library_path)
        if is_hdri:
            self.refresh_houdini_sessions()

    def show_section(self, asset_type: str) -> None:
        index = self.section.findData(asset_type)
        if index >= 0:
            self.section.setCurrentIndex(index)


def _largest_resolution(asset: AssetRecord) -> int:
    if isinstance(asset, LibraryStockAsset):
        return asset.media_info.width * asset.media_info.height
    values = []
    for label in asset.resolutions:
        digits = "".join(char for char in label if char.isdigit())
        values.append(int(digits) if digits else 0)
    return max(values, default=0)


def _asset_filter_categories(
    asset: AssetRecord,
    catalog: CategoryCatalog,
) -> tuple[str, ...]:
    primary = catalog.canonical_name(asset.category) or asset.category.strip() or "Uncategorized"
    return (primary,)


def _resolution_number(label: str) -> int:
    digits = "".join(character for character in str(label) if character.isdigit())
    return int(digits) if digits else 0


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _media_time(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
