import os
import json
from threading import Event
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, QItemSelectionModel, QSettings, QThreadPool, Qt
from PyQt6.QtGui import QColor, QCloseEvent, QImage, QPixmap
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

from universal_asset_library.app import create_application
from universal_asset_library.settings import AppSettings, SettingsStore
from universal_asset_library.ui.assets_tab import (
    ASSET_ROLE,
    AssetsTab,
    DetailPanel,
    MaterialEditDialog,
    MetadataUpdateWorker,
    ModelAssetRescanDialog,
    ModelConversionDialog,
    TagEditor,
    StarRatingWidget,
    TextureCardDelegate,
    TextureFilterModel,
    TextureListModel,
    _classification_preview,
    _merge_ai_tags,
)
from universal_asset_library.ai import CategoryGuess, Classification, OllamaStatus, TagGuess
from universal_asset_library.ui.ai_classification import (
    AiBatchWorker,
    AiOrganiseItem,
    AiOrganiserDialog,
    actionable_categories,
    is_fallback_category,
)
from universal_asset_library.ui.batch_metadata import (
    BatchMetadataRequest,
    BatchMetadataWorker,
)
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
    scan_vdb_folder,
)
from universal_asset_library.library import (
    AssetMetadataPatch,
    AssetMetadataUpdate,
    CatalogIndex,
    ImportProgress,
    LibraryRecoveryState,
    LibraryRepository,
    MetadataPatchBatch,
    MetadataPatchOutcome,
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


def vdb_video_asset(
    tmp_path,
    asset_id: str,
    *,
    generated_turntable: bool,
):
    source = tmp_path / f"{asset_id}-source"
    library = tmp_path / f"{asset_id}-library"
    source.mkdir()
    library.mkdir()
    for label in ("Low", "Mid", "High"):
        (source / f"{asset_id}_{label}_Res.vdb").write_bytes(label.encode())
    asset = LibraryRepository(library).import_vdbs(
        scan_vdb_folder(source).materials
    ).imported[0]
    preview = asset.asset_dir / "previews" / f"{asset_id}.mp4"
    preview.parent.mkdir(exist_ok=True)
    preview.write_bytes(b"video")
    return replace(
        asset,
        preview_path=preview,
        preview_render=(
            {
                "status": "ready",
                "mode": "turntable",
                "frame_start": 1,
                "frame_end": 50,
                "fps": 24.0,
                "scrub_optimized": True,
            }
            if generated_turntable
            else {}
        ),
    )


def test_main_window_has_three_tabs_in_order(app, tmp_path) -> None:
    assert shotbox_assets.create_application is create_application
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    window = MainWindow()
    assert window.windowTitle() == "ShotBox Assets"
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == ["Assets", "Importer", "Settings"]
    window.close()


def test_main_window_construction_defers_library_maintenance(
    app, tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    store = SettingsStore(
        QSettings(str(tmp_path / "deferred.ini"), QSettings.Format.IniFormat)
    )
    settings = store.save(AppSettings(str(library)))
    monkeypatch.setattr(
        LibraryRepository,
        "legacy_asset_count",
        lambda _self: pytest.fail("maintenance inspection ran synchronously"),
    )

    tab = SettingsTab(store, settings)

    assert tab._inspection_worker is None
    assert "not been checked" in tab.repair_status.text()
    assert tab.refresh_maintenance_button.isEnabled()


def test_close_is_blocked_while_asset_metadata_update_runs(
    app, monkeypatch,
) -> None:
    window = MainWindow()
    window.assets_tab._metadata_update_worker = object()
    messages = []
    monkeypatch.setattr(
        "universal_asset_library.ui.main_window.QMessageBox.information",
        lambda *_args: messages.append("shown"),
    )
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert messages == ["shown"]
    window.assets_tab._metadata_update_worker = None
    window.close()


def test_thumbnail_size_presets_update_delegate(app) -> None:
    tab = AssetsTab()
    expected = {"small": (198, 228), "medium": (238, 263), "large": (286, 303)}
    for name, dimensions in expected.items():
        tab.set_thumbnail_size(name)
        size = tab.card_delegate.sizeHint(None, None)
        assert (size.width(), size.height()) == dimensions


@pytest.mark.parametrize("dimensions", [(60, 180), (300, 60)])
def test_model_card_thumbnail_contains_non_square_preview(
    app, tmp_path, dimensions,
) -> None:
    source = tmp_path / "model"
    source.mkdir()
    (source / "chair.fbx").write_bytes(b"FBX")
    preview = QImage(*dimensions, QImage.Format.Format_RGB32)
    preview.fill(QColor("#795b42"))
    assert preview.save(str(source / "chair_preview.png"))
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_models(
        scan_model_folder(source).materials
    ).imported[0]
    delegate = TextureCardDelegate()

    thumbnail = delegate._thumbnail(asset)

    assert thumbnail is not None
    assert thumbnail.width() <= delegate.card_size.width() - 10
    assert thumbnail.height() <= delegate.preview_height
    assert thumbnail.width() / thumbnail.height() == pytest.approx(
        dimensions[0] / dimensions[1], rel=0.03
    )


def test_importer_and_catalog_share_asset_type_tabs(app) -> None:
    importer = ImporterTab()
    assets = AssetsTab()
    assert isinstance(importer.import_mode, AssetTypeTabs)
    assert isinstance(assets.section, AssetTypeTabs)
    expected = ["Textures", "Atlases", "HDRIs", "Models", "VDBs", "Stock"]
    assert [importer.import_mode.tabText(index) for index in range(6)] == expected
    assert [assets.section.tabText(index) for index in range(6)] == expected
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
    assert importer.title.text() == "Import VDB Volumes"
    assert assets.title.text() == "VDB Volumes"
    assert assets.channel.toolTip() == "Filter by VDB variant or sequence mode"
    importer.import_mode.setCurrentIndex(5)
    assets.section.setCurrentIndex(5)
    assert importer.title.text() == "Import Stock Footage"
    assert assets.title.text() == "Stock Footage"
    assert assets.channel.toolTip() == "Filter by dimensions, codec, alpha, or audio"


def test_vdb_importer_review_and_catalog_detail_defaults_to_mid(app, tmp_path) -> None:
    source = tmp_path / "clouds"
    source.mkdir()
    for label in ("Low", "Mid", "High"):
        (source / f"cloud_formation_001_{label}_Res.vdb").write_bytes(label.encode())
    result = scan_vdb_folder(source)

    importer = ImporterTab()
    importer.import_mode.setCurrentIndex(importer.import_mode.findData("vdb"))
    importer._scan_finished(0, result)
    assert importer.title.text() == "Import VDB Volumes"
    assert importer.files_heading.text() == "VDB files by variant"
    assert importer.resolution.itemText(1) == "Mid"

    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_vdbs(result.materials).imported[0]
    panel = DetailPanel()
    panel.show_asset(asset)
    assert panel.eyebrow.text() == "VDB · STATIC"
    assert panel.houdini_resolution.currentText() == "Mid"
    assert panel.vdb_preview_variant.currentText() == "Mid"
    assert not panel.vdb_preview_variant.isHidden()
    assert (panel.vdb_density_slider.minimum(), panel.vdb_density_slider.maximum()) == (10, 500)
    assert panel.vdb_density_slider.value() == 100
    assert panel.hdri_render_button.text() == "Generate preview"
    assert panel.hdri_render_button.menu() is panel.vdb_preview_menu
    assert [action.text() for action in panel.vdb_preview_menu.actions()] == [
        "Still", "Generate turn table"
    ]
    assert panel.houdini_send_button.text() == "Create File SOP in Houdini"
    assert panel.dcc_app.isHidden()
    assert panel.dcc_stack.currentWidget() is panel.houdini_dcc_page


def test_vdb_preview_is_manual_only_and_uses_houdini_serial_queue(
    app, tmp_path, monkeypatch
) -> None:
    source = tmp_path / "clouds"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    for label in ("Low", "Mid", "High"):
        (source / f"cloud_formation_001_{label}_Res.vdb").write_bytes(
            label.encode()
        )
    asset = LibraryRepository(library).import_vdbs(
        scan_vdb_folder(source).materials
    ).imported[0]
    tab = AssetsTab()
    tab._library_path = str(library)
    tab._houdini_path = "/opt/hfs22.0.368/bin/hython"
    tab._ffmpeg_path = "/usr/bin/ffmpeg"
    tab._vdb_parallel_renders = 3
    tab._all_assets = [asset]
    tab._reindex_all_assets()
    tab.source_model.replace([asset])
    started = []
    monkeypatch.setattr(
        QThreadPool,
        "start",
        lambda _pool, worker, *_args: started.append(worker),
    )

    assert tab.queue_import_previews((asset,)) == 0
    assert tab.queue_preview_renders((asset,), automatic=False) == 0
    tab.detail.show_asset(asset)
    tab.detail.vdb_preview_variant.setCurrentText("High")
    tab.detail.vdb_density_slider.setValue(275)
    tab.detail.vdb_turntable_action.trigger()

    assert len(started) == 1
    assert started[0].asset_type == "vdb"
    assert started[0].variant == "High"
    assert started[0].density_scale == 275
    assert started[0].mode == "turntable"
    assert started[0].ffmpeg_path == "/usr/bin/ffmpeg"
    assert started[0].parallel_processes == 3
    assert started[0].houdini_path == "/opt/hfs22.0.368/bin/hython"
    assert started[0].preview_session is None
    assert tab._preview_session is None
    assert tab.card_delegate.task_state(asset.id) == (
        "preview_rendering", "Rendering VDB turntable in Houdini"
    )
    tab._hdri_render_failed("fixture")
    app.processEvents()
    tab.shutdown_preview_queue()


def test_multiple_selected_vdbs_offer_bulk_still_and_turntable_previews(
    app, tmp_path, monkeypatch
) -> None:
    source = tmp_path / "clouds"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    for number in (1, 2):
        for label in ("Low", "Mid", "High"):
            (source / (
                f"cloud_formation_{number:03d}_{label}_Res.vdb"
            )).write_bytes(f"{number}-{label}".encode())
    imported = list(
        LibraryRepository(library).import_vdbs(
            scan_vdb_folder(source).materials
        ).imported
    )
    first = imported[0]
    second = replace(
        imported[1],
        variants={"Low": imported[1].variants["Low"]},
    )
    assets = [first, second]
    tab = AssetsTab()
    tab._library_path = str(library)
    tab._all_assets = assets
    tab._reindex_all_assets()
    tab.source_model.replace(assets)
    started = []
    monkeypatch.setattr(
        QThreadPool,
        "start",
        lambda _pool, worker, *_args: started.append(worker),
    )
    selection = tab.view.selectionModel()
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows
    )
    selection.select(tab.proxy.index(0, 0), flags)
    selection.select(tab.proxy.index(1, 0), flags)
    app.processEvents()
    tab.detail.show_asset(first)
    tab.detail.vdb_preview_variant.setCurrentText("High")
    tab.detail.vdb_density_slider.setValue(185)

    assert not tab.bulk_preview_button.isHidden()
    assert tab.bulk_preview_button.text() == "Generate Previews (2)"
    assert tab.bulk_preview_button.menu() is tab.bulk_vdb_preview_menu
    assert [
        action.text() for action in tab.bulk_vdb_preview_menu.actions()
    ] == ["Still previews", "Turntable previews"]
    tab.bulk_preview_button.click()
    assert not started

    tab.bulk_vdb_turntable_action.trigger()

    assert len(started) == 1
    jobs = (
        tab._active_preview_render_job,
        *tab._preview_render_jobs,
    )
    assert {job.asset_id for job in jobs} == {asset.id for asset in assets}
    assert {job.mode for job in jobs} == {"turntable"}
    assert {job.density_scale for job in jobs} == {185}
    variants = {job.asset_id: job.variant for job in jobs}
    assert variants[first.id] == "High"
    assert variants[second.id] == "Low"
    assert started[0].mode == "turntable"
    assert started[0].density_scale == 185
    tab.shutdown_preview_queue()


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
    assert "23 categories" in tab.stock_taxonomy_status.text()
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
    tab.render_texture_on_import.setChecked(False)
    tab.save_texture_preview_blend.setChecked(True)
    assert tab.save_button.isEnabled()
    tab._save()
    assert captured[-1].thumbnail_size == "large"
    assert captured[-1].render_texture_on_import is False
    assert captured[-1].save_texture_preview_blend is True
    tab.default_category.setCurrentText("Wood")
    assert tab.reset_button.isEnabled()
    tab._reset()
    assert tab.default_category.currentText() == "Uncategorized"


def test_texture_inspector_exposes_shader_preview_controls(app, tmp_path) -> None:
    candidate = scan_texture_folder(texture_source(tmp_path, "Preview_Stone")).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(
        library, render_texture_previews=False
    ).import_materials([candidate]).imported[0]
    tab = AssetsTab()

    tab.detail.show_asset(asset)

    assert not tab.detail.hdri_render_button.isHidden()
    assert tab.detail.hdri_render_button.text() == "Render preview"
    assert "disabled" in tab.detail.hdri_render_status.text().casefold()


def test_missing_import_previews_are_rendered_one_at_a_time(
    app, tmp_path, monkeypatch
) -> None:
    first_source = texture_source(tmp_path, "Queue_Stone_A")
    second_source = texture_source(tmp_path, "Queue_Stone_B")
    changed = QImage(1024, 1024, QImage.Format.Format_RGB32)
    changed.fill(QColor("#334455"))
    assert changed.save(
        str(second_source / "Queue_Stone_B_diff_4k.jpg")
    )
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(
        library, render_texture_previews=False
    )
    assets = repository.import_materials([
        scan_texture_folder(first_source).materials[0],
        scan_texture_folder(second_source).materials[0],
    ]).imported
    tab = AssetsTab()
    tab._library_path = str(library)
    tab._all_assets = list(assets)
    tab._reindex_all_assets()
    tab.source_model.replace(list(assets))
    started = []
    monkeypatch.setattr(
        QThreadPool,
        "start",
        lambda _pool, worker, *_args: started.append(worker),
    )

    selection = tab.view.selectionModel()
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows
    )
    selection.select(tab.proxy.index(0, 0), flags)
    selection.select(tab.proxy.index(1, 0), flags)
    app.processEvents()
    assert not tab.bulk_preview_button.isHidden()
    assert tab.bulk_preview_button.text() == "Queue Previews (2)"

    tab.bulk_preview_button.click()

    assert len(started) == 1
    shared_session = started[0].preview_session
    assert shared_session is not None
    queued_ids = {
        tab._active_preview_render_job.asset_id,
        *(job.asset_id for job in tab._preview_render_jobs),
    }
    assert queued_ids == {asset.id for asset in assets}
    assert tab.card_delegate.task_state(
        tab._active_preview_render_job.asset_id
    )[0] == "preview_rendering"
    assert tab.card_delegate.task_state(
        tab._preview_render_jobs[0].asset_id
    )[0] == "preview_queued"
    second_id = tab._preview_render_jobs[0].asset_id
    assert "Preview queue: 2" in tab.preview_queue_status.text()
    assert not tab.preview_queue_clear.isHidden()
    tab._hdri_render_failed("first render failed")
    app.processEvents()
    assert len(started) == 2
    assert tab._active_preview_render_job.asset_id == second_id
    assert started[1].preview_session is shared_session
    assert tab.card_delegate.task_state(second_id)[0] == (
        "preview_rendering"
    )
    assert tab.preview_queue_clear.isHidden()
    tab._hdri_render_failed("second render failed")
    app.processEvents()
    assert tab._preview_session is None
    assert started[-1].__class__.__name__ == "PreviewSessionCloseWorker"
    tab.shutdown_preview_queue()


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


def test_cold_catalog_shows_loading_page_without_synchronous_scan(
    app, tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    LibraryRepository(library).initialize()
    CatalogIndex.for_library(library).clear()
    monkeypatch.setattr(
        LibraryRepository,
        "list_assets",
        lambda *_args, **_kwargs: pytest.fail("load_library must not scan manifests"),
    )
    tab = AssetsTab()

    tab.load_library(str(library))

    assert tab.stack.currentIndex() == 2
    assert "Loading texture catalog" in tab.loading_title.text()
    assert tab.loading_progress.minimum() == 0
    assert tab.loading_progress.maximum() == 0
    tab.shutdown_catalog()


def test_warm_catalog_type_switch_uses_memory_and_keeps_thumbnail_cache(
    app, tmp_path, monkeypatch,
) -> None:
    texture_root = texture_source(tmp_path, "Warm_Stone")
    preview = QImage(256, 256, QImage.Format.Format_RGB32)
    preview.fill(QColor("#777777"))
    assert preview.save(str(texture_root / "Warm_Stone_preview.jpg"))
    texture_candidate = scan_texture_folder(texture_root).materials[0]
    atlas_candidate = scan_atlas_folder(
        texture_source(tmp_path, "Warm_Leaves")
    ).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    texture = repository.import_materials([texture_candidate]).imported[0]
    atlas = repository.import_atlases([atlas_candidate]).imported[0]
    tab = AssetsTab()
    tab.load_library(str(library))
    assert tab.stack.currentIndex() == 0
    assert tab.source_model.assets == [texture]
    assert tab.card_delegate._thumbnail(texture) is not None
    cached_keys = set(tab.card_delegate._pixmaps)
    monkeypatch.setattr(
        LibraryRepository,
        "list_assets",
        lambda *_args, **_kwargs: pytest.fail("type switch must not scan manifests"),
    )
    monkeypatch.setattr(
        LibraryRepository,
        "list_assets_for_type",
        lambda *_args, **_kwargs: pytest.fail("type switch must not scan a section"),
    )

    tab.show_section("atlas")

    assert tab.source_model.assets == [atlas]
    assert cached_keys <= set(tab.card_delegate._pixmaps)
    assert tab.refresh_catalog_button.text() == "Refresh Catalog"
    tab.shutdown_catalog()


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


def test_preflight_status_describes_current_phase_file_and_progress(app) -> None:
    tab = ImporterTab()

    tab._preflight_progressed(ImportProgress(
        "Cloud Formation 014",
        "cloud_formation_014_High_Res.vdb",
        5 * 1024**3,
        18 * 1024**3,
        "Hashing",
        37,
        225,
    ))

    text = tab.status.text()
    assert "Preflight · Hashing" in text
    assert "Cloud Formation 014" in text
    assert "cloud_formation_014_High_Res.vdb" in text
    assert "file 37/225" in text
    assert "5.0 GB / 18.0 GB" in text
    assert tab.import_progress.value() == int(5 / 18 * 1000)


def test_settings_detects_abandoned_staging_for_confirmed_cleanup(app, tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    repository.initialize()
    (library / ".ual" / "staging" / "abandoned").mkdir()
    store = SettingsStore(QSettings(str(tmp_path / "recovery.ini"), QSettings.Format.IniFormat))
    settings = store.save(AppSettings(str(library)))
    tab = SettingsTab(store, settings)
    tab.refresh_maintenance_state()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert "1 abandoned" in tab.recovery_status.text()
    assert tab.cleanup_staging_button.isEnabled()
    assert tab.update_library_button.isEnabled()


def test_settings_maintenance_refresh_coalesces_and_ignores_stale_results(
    app, tmp_path, monkeypatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = SettingsStore(
        QSettings(str(tmp_path / "coalesced.ini"), QSettings.Format.IniFormat)
    )
    settings = store.save(AppSettings(str(first)))
    started = Event()
    release = Event()

    def legacy_count(repository):
        if repository.root == first:
            started.set()
            assert release.wait(5)
            return 1
        return 2

    monkeypatch.setattr(LibraryRepository, "legacy_asset_count", legacy_count)
    monkeypatch.setattr(
        LibraryRepository,
        "library_update_count",
        lambda repository: 10 if repository.root == first else 20,
    )
    monkeypatch.setattr(
        LibraryRepository,
        "recovery_state",
        lambda _repository: LibraryRecoveryState(()),
    )
    tab = SettingsTab(store, settings)
    tab.refresh_maintenance_state()
    assert started.wait(2)

    tab._saved = replace(settings, library_path=str(second))
    tab.refresh_maintenance_state()
    release.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    assert tab._legacy_count == 2
    assert tab._library_update_count == 20
    assert "2 legacy" in tab.repair_status.text()
    assert "20 asset" in tab.update_status.text()


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
    assert tab.metadata_update_active
    assert not tab.task_strip.isHidden()
    assert tab.card_delegate.task_animation_active
    assert tab.card_delegate.task_state(asset.id)[0] == "moving"
    assert tab.toolbar.isEnabled()
    assert tab.stack.isEnabled()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert not tab.metadata_update_active
    assert tab.card_delegate.task_state(asset.id) is None
    assert captured[0].name == "Edited Surface"
    assert tab.detail._asset.name == "Edited Surface"
    assert tab.category.findText("Concrete") >= 0
    assert tab.category.findText("Stone") == -1
    tab.category.setCurrentText("Concrete")
    assert tab.proxy.rowCount() == 1
    assert LibraryRepository(library).list_assets()[0].tags == ("edited", "studio")


def test_rating_filter_sort_and_star_toggle(app, tmp_path) -> None:
    unrated = stock_asset(tmp_path, "unrated")
    two = replace(stock_asset(tmp_path, "two"), name="Zulu", rating=2)
    five_b = replace(stock_asset(tmp_path, "five-b"), name="Beta", rating=5)
    five_a = replace(stock_asset(tmp_path, "five-a"), name="Alpha", rating=5)
    source = TextureListModel([unrated, two, five_b, five_a])
    proxy = TextureFilterModel()
    proxy.setSourceModel(source)
    proxy.setDynamicSortFilter(True)

    proxy.set_filters("", "All", "All", "4+")
    assert proxy.rowCount() == 2
    proxy.set_filters("", "All", "All", "Unrated")
    assert [proxy.index(row, 0).data(ASSET_ROLE).id for row in range(proxy.rowCount())] == [
        "unrated"
    ]
    proxy.set_filters("", "All", "All", "All ratings")
    proxy.set_sort_mode("Rating")
    assert [proxy.index(row, 0).data(ASSET_ROLE).name for row in range(proxy.rowCount())] == [
        "Alpha", "Beta", "Zulu", "Unrated",
    ]

    widget = StarRatingWidget()
    changes = []
    widget.rating_changed.connect(changes.append)
    widget.set_rating(3)
    assert [button.property("filled") for button in widget.buttons] == [
        True, True, True, False, False,
    ]
    QTest.mouseClick(widget.buttons[2], Qt.MouseButton.LeftButton)
    QTest.mouseClick(widget.buttons[4], Qt.MouseButton.LeftButton)
    assert changes == [0, 5]


def test_rating_is_optimistic_while_manifest_save_runs(
    app, tmp_path, monkeypatch,
) -> None:
    source = texture_source(tmp_path, "Optimistic_Rating")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    started = Event()
    release = Event()
    original = LibraryRepository.patch_asset_metadata

    def delayed_patch(repository, asset_id, patch):
        started.set()
        assert release.wait(5)
        return original(repository, asset_id, patch)

    monkeypatch.setattr(
        LibraryRepository, "patch_asset_metadata", delayed_patch
    )
    tab = AssetsTab()
    tab.load_library(str(library))

    assert tab._rate_asset(asset, 5)
    assert started.wait(2)
    assert tab.detail._asset.rating == 5
    assert tab.detail.star_rating.rating == 5
    assert LibraryRepository(library).list_assets()[0].rating == 0

    release.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert LibraryRepository(library).list_assets()[0].rating == 5
    tab.close()


def test_multiple_ratings_queue_without_blocking_clicks(
    app, tmp_path, monkeypatch,
) -> None:
    first_source = texture_source(tmp_path, "Queue_First")
    second_source = texture_source(tmp_path, "Queue_Second")
    distinct = QImage(1024, 1024, QImage.Format.Format_RGB32)
    distinct.fill(QColor("#557799"))
    assert distinct.save(str(second_source / "Queue_Second_diff_4k.jpg"))
    candidates = (
        scan_texture_folder(first_source).materials[0],
        scan_texture_folder(second_source).materials[0],
    )
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    first, second = repository.import_materials(candidates).imported
    started = Event()
    release = Event()
    calls = []
    original = LibraryRepository.patch_asset_metadata

    def delayed_first_patch(repository, asset_id, patch):
        calls.append((asset_id, patch.rating))
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return original(repository, asset_id, patch)

    monkeypatch.setattr(
        LibraryRepository, "patch_asset_metadata", delayed_first_patch
    )
    tab = AssetsTab()
    tab.load_library(str(library))

    assert tab._rate_asset(first, 2)
    assert started.wait(2)
    assert tab._rate_asset(second, 3)
    assert tab._rate_asset(second, 5)
    assert tab._asset_by_id(first.id).rating == 2
    assert tab._asset_by_id(second.id).rating == 5
    assert tab._rating_update_asset_id == first.id
    assert tab._pending_rating_updates == {second.id: 5}

    release.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    ratings = {
        asset.id: asset.rating for asset in LibraryRepository(library).list_assets()
    }
    assert ratings == {first.id: 2, second.id: 5}
    assert calls == [(first.id, 2), (second.id, 5)]
    assert not tab.metadata_update_active
    tab.close()


def test_assets_tab_rating_persists_and_updates_detail(app, tmp_path) -> None:
    source = texture_source(tmp_path, "Rating_Stone")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.resize(900, 700)
    tab.show()
    app.processEvents()

    requested = []
    tab.card_delegate.rating_requested.connect(
        lambda requested_asset, rating: requested.append(
            (requested_asset.id, rating)
        )
    )
    index = tab.proxy.index(0, 0)
    rect = tab.view.visualRect(index)
    # Fourth star in the delegate's rating row.
    QTest.mouseClick(
        tab.view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(rect.left() + 69, rect.top() + 241),
    )
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    updated = LibraryRepository(library).list_assets()[0]
    assert requested == [(asset.id, 4)]
    assert updated.rating == 4
    assert tab.detail._asset.rating == 4
    assert tab.detail.star_rating.rating == 4
    assert not tab.metadata_update_active
    assert tab.card_delegate.task_state(asset.id) is None

    assert tab._rate_asset(updated, 0)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert LibraryRepository(library).list_assets()[0].rating == 0
    tab.close()


def test_rating_failure_retains_previous_value(app, tmp_path, monkeypatch) -> None:
    source = texture_source(tmp_path, "Failed_Rating")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    monkeypatch.setattr(
        LibraryRepository,
        "patch_asset_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected rating failure")
        ),
    )
    tab = AssetsTab()
    tab.load_library(str(library))

    assert tab._rate_asset(asset, 5)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    assert LibraryRepository(library).list_assets()[0].rating == 0
    assert tab.detail._asset.rating == 0
    assert tab.card_delegate.task_state(asset.id) is None
    assert tab.task_retry_button.isHidden()
    assert "injected rating failure" in tab.detail.star_rating.toolTip()
    tab.close()


def test_metadata_move_card_animates_without_blocking_browser(
    app, tmp_path, monkeypatch,
) -> None:
    source = texture_source(tmp_path, "Overlay_Stone")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    started = Event()
    release = Event()
    original = LibraryRepository.update_asset_metadata

    def delayed_update(repository, asset_id, update):
        started.set()
        assert release.wait(5)
        return original(repository, asset_id, update)

    monkeypatch.setattr(
        LibraryRepository, "update_asset_metadata", delayed_update
    )
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.resize(900, 700)
    tab.show()
    app.processEvents()
    try:
        assert tab._save_material_edit(
            asset,
            AssetMetadataUpdate(asset.name, "Concrete", asset.tags),
        )
        assert started.wait(2)
        assert tab.task_strip.isVisible()
        assert tab.toolbar.isEnabled()
        assert tab.stack.isEnabled()
        assert tab.search.isEnabled()
        assert not tab.detail.path_button.isEnabled()
        start_angle = tab.card_delegate._task_angle
        QTest.qWait(120)
        assert tab.card_delegate._task_angle != start_angle
        assert not tab._save_material_edit(
            asset,
            AssetMetadataUpdate(asset.name, "Wood", asset.tags),
        )
    finally:
        release.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    assert not tab.metadata_update_active
    assert tab.card_delegate.task_state(asset.id) is None
    assert tab.toolbar.isEnabled()
    assert LibraryRepository(library).list_assets()[0].category == "Concrete"
    tab.close()


def test_metadata_move_failure_uses_non_modal_card_error(
    app, tmp_path, monkeypatch,
) -> None:
    source = texture_source(tmp_path, "Failed_Move")
    candidate = scan_texture_folder(source).materials[0]
    library = tmp_path / "library"
    library.mkdir()
    asset = LibraryRepository(library).import_materials([candidate]).imported[0]
    original_update = LibraryRepository.update_asset_metadata
    monkeypatch.setattr(
        LibraryRepository,
        "update_asset_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected move failure")
        ),
    )
    tab = AssetsTab()
    tab.load_library(str(library))

    assert tab._save_material_edit(
        asset, AssetMetadataUpdate(asset.name, "Concrete", asset.tags)
    )
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    assert not tab.metadata_update_active
    assert tab.metadata_overlay.isHidden()
    assert tab.toolbar.isEnabled()
    assert not tab.task_strip.isHidden()
    assert not tab.task_retry_button.isHidden()
    assert tab.card_delegate.task_state(asset.id) == (
        "failed", "injected move failure",
    )
    assert asset.asset_dir.is_dir()
    monkeypatch.setattr(
        LibraryRepository, "update_asset_metadata", original_update
    )
    tab._retry_failed_asset(asset.id)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert LibraryRepository(library).list_assets()[0].category == "Concrete"
    assert tab.card_delegate.task_state(asset.id) is None


def test_metadata_worker_indexes_flat_stock_manifest(
    app, tmp_path, monkeypatch,
) -> None:
    original = stock_asset(tmp_path / "fixture", "flat-stock")
    flat_dir = tmp_path / "library" / "stock" / "atmospheres"
    flat_dir.mkdir(parents=True)
    source = flat_dir / "Hero_Smoke.mov"
    preview = flat_dir / "Hero_Smoke_Preview.mp4"
    thumbnail = flat_dir / "Hero_Smoke_Thumbnail.jpg"
    source.write_bytes(b"source")
    preview.write_bytes(b"preview")
    thumbnail.write_bytes(b"thumbnail")
    (flat_dir / "Hero_Smoke.json").write_text("{}", encoding="utf-8")
    updated = replace(
        original,
        name="Hero Smoke",
        category="Atmospheres",
        asset_dir=flat_dir,
        source_path=source,
        preview_path=preview,
        thumbnail_path=thumbnail,
        hero_path=thumbnail,
    )
    monkeypatch.setattr(
        LibraryRepository,
        "update_asset_metadata",
        lambda *_args, **_kwargs: updated,
    )
    index = CatalogIndex(tmp_path / "catalog.sqlite3", "test-library")
    worker = MetadataUpdateWorker(
        str(tmp_path / "library"),
        original.id,
        AssetMetadataUpdate("Hero Smoke", "Atmospheres"),
        index,
    )

    worker.run()

    assert index.query_section("stock") == [updated]


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
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
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
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    tagged = LibraryRepository(library).list_assets()[0]
    assert tagged.category == "Concrete"
    assert tagged.tags[-5:] == (
        "rough", "outdoor", "weathered", "stone", "matte",
    )


def test_ai_organiser_targets_fallback_categories_and_excludes_them(
    app, tmp_path,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    assets = []
    for index, (name, category) in enumerate((
        ("Needs_Category", "Uncategorized"),
        ("Other_Surface", "Other"),
        ("Good_Surface", "Stone"),
    )):
        source = texture_source(tmp_path, name)
        unique = QImage(1024, 1024, QImage.Format.Format_RGB32)
        unique.fill(QColor(80 + index * 40, 90, 100))
        assert unique.save(str(source / f"{name}_diff_4k.jpg"))
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = category
        assets.extend(repository.import_materials([candidate]).imported)
    tab = AssetsTab()
    tab.load_library(str(library))
    vocabularies = {
        asset_type: ("rough", "outdoor", "weathered", "stone", "matte")
        for asset_type in tab._category_catalogs
    }

    dialog = AiOrganiserDialog(
        tuple(repository.list_assets()),
        tab._category_catalogs,
        vocabularies,
        _classification_preview,
        current_asset_type="texture_set",
    )

    assert {item.asset.category for item in dialog.items} == {
        "Uncategorized", "Other",
    }
    assert all(
        not is_fallback_category(category)
        for item in dialog.items
        for category in item.categories
    )
    assert actionable_categories(
        ("Stone", "Other", "Miscellaneous", "Uncategorized")
    ) == ("Stone",)


def test_ai_batch_worker_continues_after_failure(app, tmp_path, monkeypatch) -> None:
    preview = tmp_path / "preview.jpg"
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#777777"))
    assert image.save(str(preview))
    assets = [
        type("Asset", (), {
            "id": value,
            "name": value,
            "asset_type": "texture_set",
            "category": "Uncategorized",
            "tags": (),
        })()
        for value in ("good", "bad")
    ]
    items = tuple(
        (
            row,
            AiOrganiseItem(
                asset,
                preview,
                ("Stone", "Concrete"),
                ("rough", "outdoor", "weathered", "stone", "matte"),
            ),
        )
        for row, asset in enumerate(assets)
    )

    def classify(_client, _preview, **kwargs):
        if kwargs["asset_name"] == "bad":
            raise RuntimeError("model failed")
        return Classification(
            "Stone",
            ("rough", "outdoor", "weathered", "stone", "matte"),
            0.9,
            "Visible stone.",
        )

    monkeypatch.setattr(assets_tab_module.OllamaClient, "classify", classify)
    worker = AiBatchWorker(items)
    completed = []
    worker.signals.item_finished.connect(
        lambda row, result, error: completed.append((row, result, error))
    )
    worker.run()

    assert completed[0][1].category == "Stone"
    assert completed[1][1] is None
    assert completed[1][2] == "model failed"


def test_bulk_selection_category_update_preserves_tags(
    app, tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = []
    for index, name in enumerate(("Bulk_Stone_A", "Bulk_Stone_B", "Bulk_Stone_C")):
        source = texture_source(tmp_path, name)
        unique = QImage(1024, 1024, QImage.Format.Format_RGB32)
        unique.fill(QColor(70 + index * 50, 80, 90))
        assert unique.save(str(source / f"{name}_diff_4k.jpg"))
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = "Stone"
        candidate.tags = ["existing"]
        imported.extend(repository.import_materials([candidate]).imported)
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.show()
    QTest.qWait(350)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    selection = tab.view.selectionModel()
    selection.clearSelection()
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows
    )
    selection.select(tab.proxy.index(0, 0), flags)
    selection.select(tab.proxy.index(1, 0), flags)
    app.processEvents()

    assert len(tab._selected_assets()) == 2
    assert not tab.bulk_bar.isHidden()
    tab.bulk_category.setCurrentText("Concrete")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    tab._change_selected_category()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    selected_ids = {asset.id for asset in imported[:2]}
    updated = {
        asset.id: asset for asset in repository.list_assets()
        if asset.id in selected_ids
    }
    assert {asset.category for asset in updated.values()} == {"Concrete"}
    assert {asset.tags for asset in updated.values()} == {("existing",)}
    assert {asset.id for asset in tab._selected_assets()} == selected_ids


def test_batch_move_updates_each_card_while_browser_stays_usable(
    app, tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = []
    for index, name in enumerate(("Background_A", "Background_B", "Background_C")):
        source = texture_source(tmp_path, name)
        unique = QImage(1024, 1024, QImage.Format.Format_RGB32)
        unique.fill(QColor(65 + index * 55, 85, 105))
        assert unique.save(str(source / f"{name}_diff_4k.jpg"))
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = "Stone"
        imported.extend(repository.import_materials([candidate]).imported)
    first, second, unaffected = imported
    second_started = Event()
    release_second = Event()
    original_patch = MetadataPatchBatch.patch

    def delayed_patch(batch, asset_id, patch):
        if asset_id == second.id:
            second_started.set()
            assert release_second.wait(5)
        return original_patch(batch, asset_id, patch)

    monkeypatch.setattr(
        MetadataPatchBatch, "patch", delayed_patch
    )
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.show()
    QTest.qWait(350)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    requests = tuple(
        BatchMetadataRequest(
            asset.id,
            asset.name,
            AssetMetadataPatch(category="Concrete"),
        )
        for asset in (first, second)
    )

    assert tab._start_batch_metadata(
        requests, origin="manual-category", title="Moving assets"
    )
    assert second_started.wait(3)
    QTest.qWait(100)

    assert tab._asset_by_id(first.id).category == "Concrete"
    assert tab.card_delegate.task_state(first.id) is None
    assert tab.card_delegate.task_state(second.id)[0] == "moving"
    assert tab.toolbar.isEnabled()
    assert tab.stack.isEnabled()
    assert tab.search.isEnabled()
    unaffected_index = tab._proxy_index_for_id(unaffected.id)
    tab.view.setCurrentIndex(unaffected_index)
    tab._selected(unaffected_index)
    assert tab.detail.path_button.isEnabled()

    release_second.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert not tab.metadata_update_active
    assert tab._asset_by_id(second.id).category == "Concrete"


def test_cancel_background_move_clears_unprocessed_card_states(
    app, tmp_path, monkeypatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    imported = []
    for index, name in enumerate(("Cancel_Background_A", "Cancel_Background_B")):
        source = texture_source(tmp_path, name)
        unique = QImage(1024, 1024, QImage.Format.Format_RGB32)
        unique.fill(QColor(75 + index * 65, 95, 115))
        assert unique.save(str(source / f"{name}_diff_4k.jpg"))
        candidate = scan_texture_folder(source).materials[0]
        candidate.category = "Stone"
        imported.extend(repository.import_materials([candidate]).imported)
    first, second = imported
    first_started = Event()
    release_first = Event()
    original_patch = MetadataPatchBatch.patch

    def delayed_patch(batch, asset_id, patch):
        if asset_id == first.id:
            first_started.set()
            assert release_first.wait(5)
        return original_patch(batch, asset_id, patch)

    monkeypatch.setattr(
        MetadataPatchBatch, "patch", delayed_patch
    )
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.show()
    QTest.qWait(350)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    requests = tuple(
        BatchMetadataRequest(
            asset.id,
            asset.name,
            AssetMetadataPatch(category="Concrete"),
        )
        for asset in imported
    )
    assert tab._start_batch_metadata(
        requests, origin="manual-category", title="Moving assets"
    )
    assert first_started.wait(3)
    tab._cancel_batch_metadata()
    release_first.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()

    current = {asset.id: asset for asset in repository.list_assets()}
    assert current[first.id].category == "Concrete"
    assert current[second.id].category == "Stone"
    assert tab.card_delegate.task_state(first.id) is None
    assert tab.card_delegate.task_state(second.id) is None
    assert not tab.metadata_update_active
    assert "canceled" in tab.task_status.text().casefold()


def test_incremental_move_respects_active_category_filter(app, tmp_path) -> None:
    source = texture_source(tmp_path, "Filtered_Background")
    candidate = scan_texture_folder(source).materials[0]
    candidate.category = "Stone"
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library)
    asset = repository.import_materials([candidate]).imported[0]
    tab = AssetsTab()
    tab.load_library(str(library))
    tab.show()
    QTest.qWait(350)
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    tab.category.setCurrentText("Stone")
    assert tab.proxy.rowCount() == 1

    updated = repository.patch_asset_metadata(
        asset.id, AssetMetadataPatch(category="Concrete")
    )
    tab.apply_asset_update_incremental(updated)

    assert tab.category.currentText() == "Stone"
    assert tab.proxy.rowCount() == 0
    assert tab.category_rail._counts.get("Stone", 0) == 0


def test_batch_metadata_worker_reports_partial_failures(
    app, tmp_path, monkeypatch,
) -> None:
    requests = (
        BatchMetadataRequest(
            "good", "Good", AssetMetadataPatch(category="Stone")
        ),
        BatchMetadataRequest(
            "bad", "Bad", AssetMetadataPatch(category="Stone")
        ),
    )

    def patch(_batch, asset_id, _patch):
        if asset_id == "bad":
            raise RuntimeError("cannot move")
        updated = type("Updated", (), {
            "id": asset_id,
            "asset_type": "texture_set",
            "asset_dir": Path("/does/not/exist"),
        })()
        return MetadataPatchOutcome(
            updated, Path("/does/not/exist/asset.json")
        )

    class BrokenCatalog:
        def records_for_ids(self, _asset_ids):
            raise RuntimeError("catalog unavailable")

        def writer(self):
            raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(MetadataPatchBatch, "patch", patch)
    worker = BatchMetadataWorker(str(tmp_path), requests, BrokenCatalog())
    results = []
    item_results = []
    worker.signals.item_finished.connect(item_results.append)
    worker.signals.finished.connect(results.append)
    worker.run()

    assert [asset.id for asset in results[0].updated] == ["good"]
    assert results[0].failures == {"bad": "cannot move"}
    assert (results[0].completed, results[0].total) == (2, 2)
    assert not results[0].canceled
    assert item_results[0].request.asset_id == "good"
    assert item_results[0].updated.id == "good"
    assert item_results[1].request.asset_id == "bad"
    assert item_results[1].error == "cannot move"


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
    assert tab.view.selectionMode().name == "ExtendedSelection"
    assert tab.detail.details_scroll.parent() is tab.detail
    assert tab.detail.export_footer.parent() is tab.detail
    assert not tab.detail.details_scroll.isAncestorOf(tab.detail.export_footer)
    assert tab.detail.export_footer.isVisibleTo(tab.detail)
    assert tab.detail.files_section.body.isHidden()
    assert tab.detail.technical_section.body.isHidden()
    tab.close()


def test_main_window_disables_write_tabs_during_background_asset_updates(
    app,
) -> None:
    window = MainWindow()
    importer_index = window.tabs.indexOf(window.importer_tab)
    settings_index = window.tabs.indexOf(window.settings_tab)

    window._library_mutation_busy_changed(True)
    assert not window.tabs.isTabEnabled(importer_index)
    assert not window.tabs.isTabEnabled(settings_index)
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.assets_tab))

    window._library_mutation_busy_changed(False)
    assert window.tabs.isTabEnabled(importer_index)
    assert window.tabs.isTabEnabled(settings_index)
    window.close()


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


def test_vdb_turntable_hover_maps_horizontal_position_and_coalesces_seeks(
    app, tmp_path
) -> None:
    asset = vdb_video_asset(
        tmp_path, "cloud-turntable", generated_turntable=True
    )
    tab = AssetsTab()
    tab.source_model.replace([asset])
    app.processEvents()
    controller = tab.stock_hover_previews
    index = tab.proxy.index(0, 0)
    preview_rect = QRect(10, 10, 101, 180)
    sources = []
    positions = []
    plays = []
    pauses = []
    controller.player.setSource = sources.append
    controller.player.setPosition = positions.append
    controller.player.play = lambda: plays.append(True)
    controller.player.pause = lambda: pauses.append(True)
    controller.player.stop = lambda: None

    assert controller._turntable_position(asset, 0.0) == 0
    assert controller._turntable_position(asset, 0.5) == 1042
    assert controller._turntable_position(asset, 1.0) == 2042

    controller._scrub_at(index, preview_rect.left(), preview_rect)
    controller._scrub_at(index, preview_rect.center().x(), preview_rect)
    assert positions == []
    controller._scrub_decoder_became_ready()
    assert positions == [1042]

    controller.scrub_timer.stop()
    controller._scrub_at(index, preview_rect.right(), preview_rect)
    controller.scrub_timer.stop()
    controller._apply_pending_scrub()

    assert positions == [1042, 2042]
    assert len([source for source in sources if not source.isEmpty()]) == 1
    assert not plays
    assert pauses

    frame = QImage(160, 90, QImage.Format.Format_RGB32)
    frame.fill(QColor("#ff0000"))
    tab.card_delegate.set_hover_frame(asset.id, QPixmap.fromImage(frame))
    controller.eventFilter(tab.view.viewport(), QEvent(QEvent.Type.Leave))
    assert controller.active_asset_id == ""
    assert tab.card_delegate._hover_asset_id == ""
    tab.close()


def test_only_generated_vdb_turntables_scrub_and_stock_still_autoplays(
    app, tmp_path
) -> None:
    generated = vdb_video_asset(
        tmp_path, "generated", generated_turntable=True
    )
    supplied = vdb_video_asset(
        tmp_path, "supplied", generated_turntable=False
    )
    stock = stock_asset(tmp_path, "stock-hover")
    tab = AssetsTab()
    controller = tab.stock_hover_previews

    assert controller._is_scrubbable_turntable(generated)
    assert not controller._is_scrubbable_turntable(supplied)
    assert not controller._is_scrubbable_turntable(stock)

    tab.source_model.replace([stock])
    app.processEvents()
    plays = []
    controller.player.stop = lambda: None
    controller.player.setSource = lambda _source: None
    controller.player.setPosition = lambda _position: None
    controller.player.play = lambda: plays.append(True)
    controller.schedule(tab.proxy.index(0, 0))

    assert plays == [True]
    assert not controller._scrub_active
    tab.close()


def test_vdb_mp4_cards_show_3d_preview_badge_only_for_volume_video(
    app, tmp_path
) -> None:
    vdb = vdb_video_asset(
        tmp_path, "badged-vdb", generated_turntable=True
    )
    stock = stock_asset(tmp_path, "unbadged-stock")
    mov_preview = vdb.preview_path.with_suffix(".mov")
    mov_preview.write_bytes(b"video")
    delegate = TextureCardDelegate()

    assert delegate._has_3d_preview(vdb)
    assert not delegate._has_3d_preview(stock)
    assert not delegate._has_3d_preview(
        replace(vdb, preview_path=mov_preview)
    )
    assert not delegate._has_3d_preview(
        replace(vdb, preview_path=None)
    )

    preview = QRect(10, 10, 220, 180)
    badge = delegate._preview_3d_badge_rect(preview)
    assert preview.contains(badge.toAlignedRect())
    assert badge.right() == preview.right() - 8
    assert badge.bottom() == preview.bottom() - 8


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
    tab.refresh_maintenance_state()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert tab._legacy_count == 1
    assert tab.repair_button.isEnabled()
    tab._start_repair()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    assert len(captured) == 1
    assert LibraryRepository(library).legacy_asset_count() == 0
    assert (library / "textures" / "uncategorized" / "source").is_dir()
