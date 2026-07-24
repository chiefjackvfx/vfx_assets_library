import os
import json
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings, QThreadPool, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from universal_asset_library.app import create_application
from universal_asset_library.settings import AppSettings, SettingsStore
from universal_asset_library.ui.assets_tab import (
    ASSET_ROLE,
    AssetsTab,
    DetailPanel,
    MaterialEditDialog,
    ModelAssetRescanDialog,
    ModelConversionDialog,
    TagEditor,
    _classification_preview,
    _merge_ai_tags,
)
from universal_asset_library.ai import CategoryGuess, OllamaStatus, TagGuess
from universal_asset_library.ui.importer_tab import ImporterTab
from universal_asset_library.ui.main_window import MainWindow
from universal_asset_library.ui.settings_tab import SettingsTab
from universal_asset_library.ui.asset_type_tabs import AssetTypeTabs
from universal_asset_library.importer import (
    ScanResult,
    StockCandidate,
    StockMediaInfo,
    scan_atlas_folder,
    scan_hdri_folder,
    scan_model_folder,
    scan_texture_folder,
)
from universal_asset_library.library import (
    AssetMetadataUpdate,
    LibraryRepository,
    ModelAssetRescan,
    ModelRescanItem,
    ModelUsdValidation,
    PolyHavenOptions,
)
from universal_asset_library.domain import LibraryStockAsset, LibraryStockMediaInfo
import universal_asset_library.ui.assets_tab as assets_tab_module
import shotbox_assets


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or create_application([])
    yield application


def texture_source(tmp_path, name: str = "D_Wood_Pine_04"):
    source = tmp_path / name
    source.mkdir()
    for filename, color in ((f"{name}_diff_4k.jpg", "#777777"), (f"{name}_rough_4k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    return source


def stock_asset(tmp_path, asset_id: str, *, preview_exists: bool = True) -> LibraryStockAsset:
    asset_dir = tmp_path / "stock" / "smoke" / asset_id
    source = asset_dir / "source" / f"{asset_id}.mov"
    preview = asset_dir / "previews" / f"{asset_id}_Preview.mp4"
    thumbnail = asset_dir / "previews" / f"{asset_id}_Thumbnail.jpg"
    source.parent.mkdir(parents=True)
    preview.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    if preview_exists:
        preview.write_bytes(b"preview")
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(QColor("#555555"))
    assert image.save(str(thumbnail))
    return LibraryStockAsset(
        id=asset_id,
        name=asset_id.replace("-", " ").title(),
        category="Smoke",
        tags=("smoke",),
        description="",
        author="",
        provider="Unknown",
        provider_id="",
        asset_dir=asset_dir,
        source_path=source,
        source_original_path=f"Smoke/{source.name}",
        source_format="MOV",
        source_size=source.stat().st_size,
        source_sha256="a" * 64,
        media_info=LibraryStockMediaInfo(
            "mov", "h264", "", "yuv420p", 854, 480, 24.0, 2.0, 48,
            False, "no",
        ),
        preview_path=preview,
        thumbnail_path=thumbnail,
        preview_origin="generated",
        preview_profile="SD 480p H.264",
        thumbnail_time=1.0,
        fingerprint="a" * 64,
        created_at="2026-01-01T00:00:00+00:00",
        total_size=16,
    )


def test_main_window_has_three_tabs_in_order(app, tmp_path) -> None:
    assert shotbox_assets.create_application is create_application
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    window = MainWindow()
    assert window.windowTitle() == "ShotBox Assets"
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == ["Assets", "Importer", "Settings"]
    window.close()


def test_thumbnail_size_presets_update_delegate(app) -> None:
    tab = AssetsTab()
    expected = {"small": (198, 210), "medium": (238, 245), "large": (286, 285)}
    for name, dimensions in expected.items():
        tab.set_thumbnail_size(name)
        size = tab.card_delegate.sizeHint(None, None)
        assert (size.width(), size.height()) == dimensions


def test_importer_and_catalog_share_asset_type_tabs(app) -> None:
    importer = ImporterTab()
    assets = AssetsTab()
    assert isinstance(importer.import_mode, AssetTypeTabs)
    assert isinstance(assets.section, AssetTypeTabs)
    assert [importer.import_mode.tabText(index) for index in range(5)] == ["Textures", "Atlases", "HDRIs", "Models", "Stock"]
    assert [assets.section.tabText(index) for index in range(5)] == ["Textures", "Atlases", "HDRIs", "Models", "Stock"]
    importer.import_mode.setCurrentIndex(1)
    assets.section.setCurrentIndex(1)
    assert importer.title.text() == "Import Atlases"
    assert assets.title.text() == "Atlases"
    importer.import_mode.setCurrentIndex(2)
    assets.section.setCurrentIndex(2)
    assert importer.title.text() == "Import HDRIs"
    assert assets.title.text() == "HDRIs"
    assert importer.review_heading.text() == "Asset details"
    assert assets.channel.toolTip() == "Filter by HDRI resolution or format"
    importer.import_mode.setCurrentIndex(4)
    assets.section.setCurrentIndex(4)
    assert importer.title.text() == "Import Stock Footage"
    assert assets.title.text() == "Stock Footage"
    assert assets.channel.toolTip() == "Filter by dimensions, codec, alpha, or audio"


def test_assets_category_rail_filters_primary_categories_and_live_counts(app, tmp_path) -> None:
    first = replace(
        stock_asset(tmp_path, "smoke-magic"),
        category="Magic",
    )
    second = stock_asset(tmp_path, "smoke-only")
    tab = AssetsTab()
    tab.show_section("stock")
    tab.source_model.replace([first, second])
    tab._rebuild_categories()
    tab._rebuild_facets()

    assert set(tab.category_rail._buttons) == {"All", "Smoke", "Magic"}
    assert tab.category_rail._counts["All"] == 2
    assert tab.category_rail._counts["Magic"] == 1

    tab.category_rail._buttons["Magic"].click()

    assert tab.category.currentText() == "Magic"
    assert tab.proxy.rowCount() == 1
    assert tab.proxy.index(0, 0).data(ASSET_ROLE).id == first.id

    tab.search.setText("smoke only")

    assert tab.proxy.rowCount() == 0
    assert tab.category_rail._counts["All"] == 1
    assert tab.category_rail._counts.get("Magic", 0) == 0
    assert tab.detail._asset is None


def test_texture_category_rail_uses_configured_categories_not_provider_descriptors(app, tmp_path) -> None:
    source = texture_source(tmp_path, "Worn_Wood")
    candidate = scan_texture_folder(source).materials[0]
    candidate.category = "Wood"
    candidate.tags = ["rough", "floor"]
    library = tmp_path / "library"
    library.mkdir()
    LibraryRepository(library).import_materials([candidate])

    tab = AssetsTab()
    tab.load_library(str(library))

    assert set(tab.category_rail._buttons) == {"All", "Wood"}
    assert "surface" not in tab.category_rail._buttons
    assert "rough" not in tab.category_rail._buttons


def test_category_rail_expansion_persists_and_asset_type_switch_resets_filter(app) -> None:
    QSettings().remove("assets/category_rail_expanded")
    tab = AssetsTab()
    assert not tab.category_rail.expanded
    tab.category_rail.set_expanded(True)
    assert tab.category_rail.width() == tab.category_rail.EXPANDED_WIDTH

    restored = AssetsTab()
    assert restored.category_rail.expanded
    restored.category.addItems(["Smoke"])
    restored.category.setCurrentText("Smoke")
    restored.section.setCurrentIndex(1)
    assert restored.category.currentText() == "All"
    QSettings().remove("assets/category_rail_expanded")


def test_stock_review_shows_editable_smart_metadata(app, tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    candidate = StockCandidate(
        source_root=source,
        name="Lens Splash 01",
        category="Lens",
        tags=["splash", "water"],
        source_video="Lens_Splash_01.mov",
        media_info=StockMediaInfo(
            "mov", "png", "", "rgba", 2048, 1152, 24.0, 2.0, 48, False, "yes"
        ),
        classification_evidence=[
            "Filename “Lens_Splash_01” → Lens",
            "Folder “Water” → Water",
            "Path and filename tags → splash",
        ],
    )
    tab = ImporterTab()
    tab.set_library_path(str(library))
    tab.import_mode.setCurrentIndex(4)
    tab._scan_token = 1
    tab._scan_finished(1, ScanResult(materials=[candidate], detected_asset_type="stock"))

    assert tab.category.currentText() == "Lens"
    assert not tab.category.isEditable()
    assert tab.tags.tags() == ("splash", "water")
    assert "Filename “Lens_Splash_01” → Lens" in tab.stock_inference.text()
    tab.category.setCurrentText("Water")
    tab.tags.add_tags(("wet",))
    tab._save_edits()
    assert candidate.category == "Water"
    assert candidate.tags == ["splash", "water", "wet"]


def test_settings_initializes_and_validates_stock_taxonomy(app, tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    store = SettingsStore(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings = store.save(AppSettings(library_path=str(library)))
    tab = SettingsTab(store, settings)

    assert "stock_categories.json" in tab.stock_categories_path.text()
    assert "will be created" in tab.stock_taxonomy_status.text()
    tab._reload_stock_taxonomy()
    assert (library / ".ual" / "stock_categories.json").is_file()
    assert (library / ".ual" / "stock_tags.json").is_file()
    assert "22 categories" in tab.stock_taxonomy_status.text()
    assert "canonical tags" in tab.stock_taxonomy_status.text()


def test_atlas_review_catalog_and_texture_export_controls(app, tmp_path) -> None:
    candidate = scan_atlas_folder(texture_source(tmp_path, "Leaf_Cutout")).materials[0]
    importer = ImporterTab()
    importer.import_mode.setCurrentIndex(1)
    importer._scan_token = 1
    importer._scan_finished(1, type("Result", (), {
        "canceled": False,
        "detected_asset_type": "atlas",
        "detection_reason": "forced Atlas scan",
        "materials": [candidate],
        "warnings": [],
    })())
    assert importer.material_list.item(0).text().splitlines()[1].split(" · ")[1] == "ATLAS"
    assert importer.category.findText("Grass") >= 0

    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_atlases([candidate]).imported[0]
    assets = AssetsTab()
    assets.show_section("atlas")
    assets.load_library(str(library))
    assert assets.source_model.assets == [asset]
    assert assets.detail.eyebrow.text() == "ATLAS"
    assert assets.detail.edit_button.text() == "Edit atlas…"
    assert not assets.detail.export_footer.isHidden()
    assert assets.detail.blender_send_button.text() == "Send material to Blender"


def test_model_importer_review_and_catalog_usd_controls(app, tmp_path) -> None:
    source = tmp_path / "chair"
    source.mkdir()
    (source / "chair.blend").write_bytes(b"blend")
    (source / "chair.usdc").write_bytes(b"usdc")
    (source / "chair_LOD0.fbx").write_bytes(b"fbx")
    (source / "license.txt").write_text("CC0", encoding="utf-8")
    result = scan_model_folder(source)

    importer = ImporterTab()
    importer.import_mode.setCurrentIndex(3)
    importer._scan_token = 1
    importer._scan_finished(1, result)
    assert importer.title.text() == "Import 3D Models"
    assert importer.model_table.isVisibleTo(importer)
    assert importer.model_table.rowCount() == 3
    assert importer.model_status.text() == "USD Ready"
    assert "license.txt" in importer.extra_files.toPlainText()

    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_models(result.materials).imported[0]
    assets = AssetsTab()
    assets.show_section("model")
    assets.load_library(str(library))
    assert assets.title.text() == "3D Models"
    assert len(assets.source_model.assets) == 1
    assert assets.detail._asset.id == asset.id
    assert assets.detail.view_3d_button.isVisibleTo(assets.detail)
    assert assets.detail.view_3d_button.isEnabled()
    assert assets.detail.future_buttons == []
    assert assets.channel.findText("USD Ready") >= 0
    assert not assets.detail.technical_toggle.isHidden()
    assert assets.detail.technical_section.body.isHidden()
    assert assets.detail.maps_title.isHidden()
    assets.detail.technical_toggle.setChecked(True)
    assert not assets.detail.technical_section.body.isHidden()
    assert "Geometry" in assets.detail.technical_details.text()
    assets.detail.files_section.set_expanded(True)
    assert "chair.usdc" in assets.detail.channels.text()
    assert assets.detail.maximumWidth() > 10000
    assert assets.detail_scroll is assets.detail.details_scroll


def test_polyhaven_hdri_download_controls(app, tmp_path) -> None:
    source = tmp_path / "studio_small_03"
    source.mkdir()
    (source / "studio_small_03_1k.hdr").write_bytes(
        b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 512 +X 1024\nfixture"
    )
    (source / "info.json").write_text(json.dumps({
        "name": "Studio Small 03",
        "authors": {"Tester": "CC0"},
        "max_resolution": [4096, 2048],
        "files": {"hdri": {"1k": {"hdr": {"url": "https://dl.polyhaven.org/studio_small_03_1k.hdr"}}}},
    }), encoding="utf-8")
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library, render_hdri_previews=False).import_materials(
        scan_hdri_folder(source).materials
    ).imported[0]
    panel = DetailPanel()
    panel.show()
    panel.show_asset(asset)
    assert panel.polyhaven_title.isVisible()
    panel.apply_polyhaven_options(PolyHavenOptions(
        slug="studio_small_03",
        map_resolutions=(),
        materialx_resolutions=(),
        usd_resolutions=(),
        hdri_resolutions=("2K", "4K"),
        catalog={"hdri": {}},
    ))
    panel.downloads_section.set_expanded(True)
    assert panel.polyhaven_hdri_button.isVisible()
    assert panel.polyhaven_resolution.currentText() == "2K"
    assert panel.polyhaven_hdri_button.isEnabled()
    panel.close()


def test_model_conversion_dialog_and_inspector_expose_headless_workflow(
    app, tmp_path, monkeypatch,
) -> None:
    source = tmp_path / "chair"
    source.mkdir()
    (source / "chair.blend").write_bytes(b"BLENDER")
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor("#795b42"))
    assert image.save(str(source / "chair_basecolor_1k.png"))
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_models(
        scan_model_folder(source).materials
    ).imported[0]
    monkeypatch.setattr(
        assets_tab_module,
        "validate_model_conversion_blender",
        lambda _path: (True, "Detected Blender 5.2.0.", "Blender 5.2.0"),
    )

    dialog = ModelConversionDialog(asset, str(library), "/blender")
    assert dialog.source.count() == 1
    assert dialog.source_path.endswith(".blend")
    assert dialog.orientation_preset == "usd_interchange"
    assert "Estimated staging requirement" in dialog.estimate.text()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    panel = DetailPanel()
    panel.show()
    panel.show_asset(asset)
    app.processEvents()
    assert panel.model_convert_button.isVisibleTo(panel)
    assert panel.model_convert_button.text() == "Convert to USD"
    assert panel.model_convert_button.isEnabled()
    assert panel.model_rescan_button.isVisibleTo(panel)
    assert panel.model_rescan_button.text() == "Rescan asset"
    panel.close()
    dialog.close()


def test_model_rescan_review_requires_a_valid_resulting_preference(
    app, tmp_path,
) -> None:
    source = tmp_path / "chair"
    source.mkdir()
    (source / "chair.blend").write_bytes(b"BLENDER")
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#795b42"))
    assert image.save(str(source / "chair_basecolor_1k.png"))
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_models(
        scan_model_folder(source).materials
    ).imported[0]
    current = asset.preferred_model
    scan = ModelAssetRescan(asset.id, "now", (
        ModelRescanItem(
            current.path, "unchanged", current.file_format, "imported",
            current.role, current.lod, current.component, current.size,
            current.sha256, True,
        ),
        ModelRescanItem(
            "usd/Chair_Manual.usdc", "new", "USDC", "manual",
            "mesh", "LOD0", "Chair Manual", 10, "a" * 64,
            validation=ModelUsdValidation(True, "Blender 5.2.0", 1, 1, "Y"),
            mutable=True,
        ),
    ))
    dialog = ModelAssetRescanDialog(asset, scan)
    apply_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not apply_button.isEnabled()
    dialog._preferred["usd/Chair_Manual.usdc"].setChecked(True)
    assert apply_button.isEnabled()
    assert dialog.selection.add_paths == ("usd/Chair_Manual.usdc",)
    assert dialog.selection.preferred_path == "usd/Chair_Manual.usdc"
    dialog.close()


def test_importer_auto_detects_mixed_parent_by_default(app, tmp_path) -> None:
    model = tmp_path / "model_asset"
    model.mkdir()
    (model / "prop.fbx").write_bytes(b"fbx")
    texture = tmp_path / "texture_asset"
    texture.mkdir()
    value = QImage(64, 64, QImage.Format.Format_RGB32)
    value.fill(QColor("#777777"))
    assert value.save(str(texture / "stone_basecolor_1k.jpg"))
    assert value.save(str(texture / "stone_roughness_1k.jpg"))
    (tmp_path / "Loose Studio.exr").write_bytes(b"reviewable unsupported EXR")

    tab = ImporterTab()
    assert tab.auto_detect.isChecked()
    tab.source_path.setText(str(tmp_path))
    tab._start_scan()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert tab._result.detected_asset_type == "mixed"
    assert {item.asset_type for item in tab._result.materials} == {"texture_set", "hdri", "model"}
    assert tab.title.text() == "Import Mixed Assets"
    assert "HDRI" in "\n".join(tab.material_list.item(row).text() for row in range(tab.material_list.count()))


def test_importer_default_does_not_overwrite_active_review(app, tmp_path) -> None:
    tab = ImporterTab()
    tab.set_default_category("Wood")
    assert tab.category.currentText() == "Wood"
    result = scan_texture_folder(texture_source(tmp_path))
    tab._scan_token = 1
    tab._scan_finished(1, result)
    assert tab.category.currentText() == "Wood"
    tab.set_default_category("Brick")
    assert tab.category.currentText() == "Wood"
    tab._clear_review()
    assert tab.category.currentText() == "Brick"


def test_importer_review_populates_materials_resolutions_and_maps(app, tmp_path) -> None:
    tab = ImporterTab()
    result = scan_texture_folder(texture_source(tmp_path))
    tab._scan_token = 3
    tab._scan_finished(3, result)
    assert tab.material_list.count() == 1
    assert tab._current is not None
    assert tab.resolution.count() >= 1
    assert tab.channel_table.rowCount() >= 1


def test_settings_save_reset_and_invalid_state(app, tmp_path) -> None:
    store = SettingsStore(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat))
    tab = SettingsTab(store, AppSettings())
    captured = []
    tab.settings_saved.connect(captured.append)
    tab.library_path.setText(str(tmp_path / "missing"))
    assert not tab.save_button.isEnabled()
    tab.library_path.setText(str(tmp_path))
    tab.thumbnail_size.setCurrentIndex(tab.thumbnail_size.findData("large"))
    assert tab.save_button.isEnabled()
    tab._save()
    assert captured[-1].thumbnail_size == "large"
    tab.default_category.setCurrentText("Wood")
    assert tab.reset_button.isEnabled()
    tab._reset()
    assert tab.default_category.currentText() == "Uncategorized"


def test_assets_tab_uses_real_library_and_empty_states(app, tmp_path) -> None:
    tab = AssetsTab()
    tab.load_library("")
    assert tab.source_model.assets == []
    assert tab.stack.currentIndex() == 1
    source = tmp_path / "source"
    source.mkdir()
    for filename, color in (("stone_diff_1k.jpg", "#777777"), ("stone_rough_1k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    preview = QImage(256, 256, QImage.Format.Format_RGB32)
    preview.fill(QColor("#777777"))
    assert preview.save(str(source / "stone_preview.png"))
    candidate = scan_texture_folder(source).materials[0]
    candidate.category = "Stone"
    library = tmp_path / "library"
    library.mkdir()
    LibraryRepository(library).import_materials([candidate])
    tab.load_library(str(library))
    assert tab.stack.currentIndex() == 0
    assert len(tab.source_model.assets) == 1
    assert tab.source_model.assets[0].thumbnail_path.is_file()
    assert tab.category.findText("Stone") >= 0
    assert "color:#e8a45f" in tab.detail.channels.text()
    assert "color:#9ca6b4" in tab.detail.channels.text()


def test_checked_import_state_requires_writable_library(app, tmp_path) -> None:
    tab = ImporterTab()
    result = scan_texture_folder(texture_source(tmp_path))
    tab._scan_token = 1
    tab._scan_finished(1, result)
    assert tab.material_list.item(0).checkState() == Qt.CheckState.Checked
    assert not tab.import_button.isEnabled()
    library = tmp_path / "library"
    library.mkdir()
    tab.set_library_path(str(library))
    assert tab.preflight_button.isEnabled()
    assert not tab.import_button.isEnabled()
    tab.material_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not tab.preflight_button.isEnabled()


def test_background_preflight_enables_import_and_sets_ready_state(app, tmp_path) -> None:
    tab = ImporterTab()
    result = scan_texture_folder(texture_source(tmp_path))
    tab._scan_token = 1
    tab._scan_finished(1, result)
    library = tmp_path / "library"
    library.mkdir()
    tab.set_library_path(str(library))
    tab._start_preflight()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert tab._preflight_result is not None
    assert tab._preflight_result.materials[0].status in {"Ready", "Warning"}
    assert tab.import_button.isEnabled()
    assert tab.material_list.item(0).data(Qt.ItemDataRole.UserRole + 1) in {"Ready", "Warning"}


def test_settings_detects_abandoned_staging_for_confirmed_cleanup(app, tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    repository.initialize()
    (library / ".ual" / "staging" / "abandoned").mkdir()
    store = SettingsStore(QSettings(str(tmp_path / "recovery.ini"), QSettings.Format.IniFormat))
    settings = store.save(AppSettings(str(library)))
    tab = SettingsTab(store, settings)
    assert "1 abandoned" in tab.recovery_status.text()
    assert tab.cleanup_staging_button.isEnabled()
    assert tab.update_library_button.isEnabled()


def test_material_edit_dialog_and_assets_tab_save_refresh(app, tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for filename, color in (("stone_diff_1k.jpg", "#777777"), ("stone_rough_1k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    dialog = MaterialEditDialog(asset)
    dialog.name.setText("Edited Surface")
    dialog.category.setCurrentText("Concrete")
    dialog.tags.set_tags([])
    dialog.tags.input.setText("edited, studio, edited")
    update = dialog.metadata_update()
    assert update.tags == ("edited", "studio")

    tab = AssetsTab()
    tab.load_library(str(library))
    captured = []
    tab.material_updated.connect(captured.append)
    assert tab._save_material_edit(asset, AssetMetadataUpdate(
        name=update.name,
        category=update.category,
        tags=update.tags,
        author="Team",
        description="Updated from the Assets inspector.",
    ))
    assert captured[0].name == "Edited Surface"
    assert tab.detail._asset.name == "Edited Surface"
    assert tab.category.findText("Concrete") >= 0
    assert tab.category.findText("Stone") == -1
    tab.category.setCurrentText("Concrete")
    assert tab.proxy.rowCount() == 1
    assert LibraryRepository(library).list_assets()[0].tags == ("edited", "studio")


def test_category_selector_is_json_backed_and_not_editable(app, tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for filename, color in (("stone_diff_1k.jpg", "#777777"), ("stone_rough_1k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    dialog = MaterialEditDialog(asset)
    assert not dialog.category.isEditable()
    assert dialog.category.findText("Concrete") >= 0
    dialog.category.setCurrentText("Concrete")
    assert dialog.metadata_update().category == "Concrete"


def test_assets_detail_lists_retained_extra_files(app, tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for filename, color in (("stone_diff_1k.jpg", "#777777"), ("stone_rough_1k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    (source / "license.txt").write_text("Internal use", encoding="utf-8")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    LibraryRepository(library).import_materials([candidate])
    tab = AssetsTab()
    tab.load_library(str(library))
    assert tab.detail.extras_title.text() == "Included Extras · 1"
    assert "license.txt" in tab.detail.extras.text()


def test_asset_inspector_ai_buttons_use_managed_preview(app, tmp_path) -> None:
    source = texture_source(tmp_path, "AI_Stone")
    preview = QImage(320, 180, QImage.Format.Format_RGB32)
    preview.fill(QColor("#6f777a"))
    assert preview.save(str(source / "AI_Stone_preview.jpg"))
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials(
        scan_texture_folder(source).materials
    ).imported[0]
    panel = DetailPanel()
    panel.show()
    captured = []
    panel.ai_guess_requested.connect(lambda value, operation: captured.append((value.id, operation)))
    panel.show_asset(asset)
    app.processEvents()

    assert panel.guess_category_button.isVisibleTo(panel)
    assert panel.guess_tags_button.isVisibleTo(panel)
    assert panel.guess_category_button.isEnabled()
    assert _classification_preview(asset) in {asset.hero_path, asset.thumbnail_path}
    panel.guess_category_button.click()
    panel.guess_tags_button.click()
    assert captured == [(asset.id, "category"), (asset.id, "tags")]
    panel.set_ai_busy(True, "Working")
    assert not panel.guess_category_button.isEnabled()
    assert panel.ai_status.text() == "Working"
    panel.close()


def test_asset_inspector_ai_buttons_disable_without_still_preview(app, tmp_path) -> None:
    asset = stock_asset(tmp_path, "missing-thumb")
    asset.thumbnail_path.unlink()
    panel = DetailPanel()
    panel.show_asset(asset)

    assert not panel.guess_category_button.isEnabled()
    assert not panel.guess_tags_button.isEnabled()
    assert _classification_preview(asset) is None


def test_ai_tag_merge_is_case_insensitive_and_stable() -> None:
    assert _merge_ai_tags(
        ("existing", "Sunny"),
        ("sunny", "urban", "midday", "hard-light", "high-contrast"),
    ) == ("existing", "Sunny", "urban", "midday", "hard-light", "high-contrast")


def test_ai_confirmation_cancel_does_not_write(app, tmp_path, monkeypatch) -> None:
    source = texture_source(tmp_path, "Cancel_Stone")
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials(
        scan_texture_folder(source).materials
    ).imported[0]
    tab = AssetsTab()
    tab.load_library(str(library))
    before = (asset.category, asset.tags)
    tab._ai_asset_id = asset.id
    monkeypatch.setattr(
        assets_tab_module.GuessConfirmationDialog,
        "exec",
        lambda self: QDialog.DialogCode.Rejected,
    )

    tab._ai_guess_finished(
        "category", CategoryGuess("Concrete", 0.8, "Looks concrete.")
    )

    current = LibraryRepository(library).list_assets()[0]
    assert (current.category, current.tags) == before


def test_ai_category_and_tags_confirm_through_repository(
    app, tmp_path, monkeypatch,
) -> None:
    source = texture_source(tmp_path, "Apply_Stone")
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials(
        scan_texture_folder(source).materials
    ).imported[0]
    tab = AssetsTab()
    tab.load_library(str(library))
    monkeypatch.setattr(
        assets_tab_module.GuessConfirmationDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )

    tab._ai_asset_id = asset.id
    tab._ai_guess_finished(
        "category", CategoryGuess("Concrete", 0.9, "Concrete surface.")
    )
    categorized = LibraryRepository(library).list_assets()[0]
    assert categorized.category == "Concrete"
    assert categorized.tags == asset.tags
    assert categorized.asset_dir.parent.name == "concrete"

    tab._ai_asset_id = asset.id
    tab._ai_guess_finished(
        "tags",
        TagGuess(
            ("rough", "outdoor", "weathered", "stone", "matte"),
            0.85,
            "Visible surface properties.",
        ),
    )
    tagged = LibraryRepository(library).list_assets()[0]
    assert tagged.category == "Concrete"
    assert tagged.tags[-5:] == (
        "rough", "outdoor", "weathered", "stone", "matte",
    )


def test_ollama_setup_flow_rechecks_readiness(app, monkeypatch) -> None:
    statuses = iter((
        OllamaStatus(False, diagnostic="offline"),
        OllamaStatus(True, ("ministral-3:8b",)),
    ))
    monkeypatch.setattr(
        assets_tab_module.OllamaClient,
        "status",
        lambda self: next(statuses),
    )
    opened = []

    class FakeSetupDialog:
        def __init__(self, start_server, parent, model):
            opened.append(model)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(assets_tab_module, "OllamaSetupDialog", FakeSetupDialog)
    tab = AssetsTab()

    assert tab._ensure_ollama_ready()
    assert opened == ["ministral-3:8b"]


def test_assets_bridge_layout_has_compact_toolbar_and_pinned_footer(app, tmp_path) -> None:
    source = texture_source(tmp_path, "Bridge_Stone")
    library = tmp_path / "library"
    library.mkdir()
    LibraryRepository(library).import_materials(scan_texture_folder(source).materials)
    tab = AssetsTab()
    tab.resize(1280, 720)
    tab.load_library(str(library))
    tab.show()
    app.processEvents()

    toolbar = tab.toolbar.layout()
    assert toolbar.itemAt(0).widget() is tab.section
    assert toolbar.itemAt(1).widget() is tab.search
    assert toolbar.itemAt(toolbar.count() - 1).widget() is tab.count
    assert tab.view.selectionMode().name == "SingleSelection"
    assert tab.detail.details_scroll.parent() is tab.detail
    assert tab.detail.export_footer.parent() is tab.detail
    assert not tab.detail.details_scroll.isAncestorOf(tab.detail.export_footer)
    assert tab.detail.export_footer.isVisibleTo(tab.detail)
    assert tab.detail.files_section.body.isHidden()
    assert tab.detail.technical_section.body.isHidden()
    tab.close()


def test_stock_inspector_has_player_controls_and_no_dcc_footer(app, tmp_path) -> None:
    asset_dir = tmp_path / "stock" / "smoke" / "smoke-puff"
    source = asset_dir / "source" / "Smoke_Puff.mov"
    preview = asset_dir / "previews" / "Smoke_Puff_Preview.mp4"
    thumbnail = asset_dir / "previews" / "Smoke_Puff_Thumbnail.jpg"
    source.parent.mkdir(parents=True)
    preview.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    preview.write_bytes(b"preview")
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(QColor("#555555"))
    assert image.save(str(thumbnail))
    asset = LibraryStockAsset(
        id="stock-id",
        name="Smoke Puff",
        category="Smoke",
        tags=("smoke",),
        description="",
        author="",
        provider="Unknown",
        provider_id="",
        asset_dir=asset_dir,
        source_path=source,
        source_original_path="Smoke/Smoke_Puff.mov",
        source_format="MOV",
        source_size=source.stat().st_size,
        source_sha256="a" * 64,
        media_info=LibraryStockMediaInfo(
            "mov", "png", "", "rgba", 2048, 1152, 24.0, 2.0, 48, False, "yes"
        ),
        preview_path=preview,
        thumbnail_path=thumbnail,
        preview_origin="generated",
        preview_profile="SD 480p H.264",
        thumbnail_time=1.0,
        fingerprint="a" * 64,
        created_at="2026-01-01T00:00:00+00:00",
        total_size=16,
    )
    panel = DetailPanel()
    panel.show()
    panel.show_asset(asset)
    app.processEvents()

    assert panel.stock_player_frame.isVisibleTo(panel)
    assert not panel.hero.isVisible()
    assert panel.stock_play.text() == "Play"
    assert panel.stock_seek.maximum() >= 2000
    assert panel.stock_volume.value() == 65
    assert panel.export_footer.isHidden()
    assert "midpoint 1.000s" in panel.technical_details.text()
    panel.clear()
    assert panel.stock_player.source().isEmpty()
    panel.close()


def test_stock_hover_preview_scheduling_and_inspector_priority(app, tmp_path) -> None:
    first = stock_asset(tmp_path, "first")
    second = stock_asset(tmp_path, "second")
    missing = stock_asset(tmp_path, "missing", preview_exists=False)
    tab = AssetsTab()
    controller = tab.stock_hover_previews
    controller.delay_ms = 10_000
    tab.source_model.replace([first, second, missing])
    app.processEvents()

    indexes = {
        tab.proxy.index(row, 0).data(ASSET_ROLE).id: tab.proxy.index(row, 0)
        for row in range(tab.proxy.rowCount())
    }
    first_index = indexes["first"]
    second_index = indexes["second"]
    missing_index = indexes["missing"]

    controller.schedule(first_index)
    assert controller.timer.isActive()
    assert controller._pending_index.data(ASSET_ROLE).id == "first"
    controller.schedule(second_index)
    assert controller.timer.isActive()
    assert controller._pending_index.data(ASSET_ROLE).id == "second"

    tab.detail.stock_playback_active_changed.emit(True)
    assert controller.suspended
    assert not controller.timer.isActive()
    controller.schedule(first_index)
    assert not controller.timer.isActive()

    tab.detail.stock_playback_active_changed.emit(False)
    controller.schedule(missing_index)
    assert not controller.timer.isActive()
    controller.schedule(first_index)
    assert controller.timer.isActive()
    tab.set_stock_hover_previews(False)
    assert not controller.enabled
    assert not controller.timer.isActive()
    tab.close()


def test_stock_hover_delegate_frame_is_cleared_on_stop(app, tmp_path) -> None:
    asset = stock_asset(tmp_path, "animated")
    tab = AssetsTab()
    tab.source_model.replace([asset])
    app.processEvents()
    frame = QImage(160, 90, QImage.Format.Format_RGB32)
    frame.fill(QColor("#ff0000"))

    tab.card_delegate.set_hover_frame(asset.id, QPixmap.fromImage(frame))

    assert tab.card_delegate._hover_asset_id == asset.id
    assert not tab.card_delegate._hover_pixmap.isNull()
    tab.stock_hover_previews.stop()
    assert tab.card_delegate._hover_asset_id == ""
    assert tab.card_delegate._hover_pixmap.isNull()
    tab.close()


def test_assets_splitter_is_wide_and_persists_state(app, tmp_path) -> None:
    QSettings().remove("assets/splitter_state")
    first = AssetsTab()
    first.resize(1400, 720)
    first.show()
    app.processEvents()
    first.splitter.setSizes([760, 620])
    first._save_splitter_state()
    assert first.detail.maximumWidth() > 10000

    second = AssetsTab()
    second.resize(1400, 720)
    second.show()
    app.processEvents()
    sizes = second.splitter.sizes()
    assert sizes[1] > 500
    first.close()
    second.close()
    QSettings().remove("assets/splitter_state")


def test_tag_editor_adds_pasted_tags_deduplicates_and_removes(app) -> None:
    editor = TagEditor(("road", "cracked"))
    changes = []
    editor.tags_changed.connect(lambda: changes.append(editor.tags()))
    editor.input.setText("Wet, ROAD; outdoor\nhero")
    assert editor.tags() == ("road", "cracked", "Wet", "outdoor", "hero")
    editor.remove_tag("CRACKED")
    assert editor.tags() == ("road", "Wet", "outdoor", "hero")
    editor.input.setText("fine detail")
    editor.commit_input()
    assert editor.tags()[-1] == "fine detail"
    assert len(changes) == 3


def test_settings_repairs_legacy_names_in_background(app, tmp_path) -> None:
    import json

    source = tmp_path / "source"
    source.mkdir()
    for filename, color in (("stone_diff_1k.jpg", "#777777"), ("stone_rough_1k.jpg", "#999999")):
        value = QImage(1024, 1024, QImage.Format.Format_RGB32)
        value.fill(QColor(color))
        assert value.save(str(source / filename))
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    legacy = asset.asset_dir.with_name("source_deadbeef")
    asset.asset_dir.rename(legacy)
    manifest_path = legacy / "asset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("naming_version")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = SettingsStore(QSettings(str(tmp_path / "repair.ini"), QSettings.Format.IniFormat))
    settings = store.save(AppSettings(str(library)))
    tab = SettingsTab(store, settings)
    captured = []
    tab.library_repaired.connect(captured.append)
    assert tab._legacy_count == 1
    assert tab.repair_button.isEnabled()
    tab._start_repair()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert len(captured) == 1
    assert LibraryRepository(library).legacy_asset_count() == 0
    assert (library / "textures" / "uncategorized" / "source").is_dir()
