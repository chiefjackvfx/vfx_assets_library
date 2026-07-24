from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from universal_asset_library.app import create_application
from universal_asset_library.domain import (
    LibraryHdriAsset, LibraryHdriFile, LibraryHdriVariant,
    LibraryMap, LibraryModelAsset, LibraryModelFile, LibraryResolution, LibraryTextureAsset,
)
from universal_asset_library.integrations.houdini import HoudiniSession
from universal_asset_library.integrations.blender import BlenderSession
from universal_asset_library.ui.assets_tab import DetailPanel


def test_hdri_inspector_session_and_resolution_controls(tmp_path) -> None:
    app = QApplication.instance() or create_application([])
    asset = LibraryHdriAsset(
        "id", "Sky", "Outdoor", (), "", "", "Unknown", "", tmp_path,
        {
            "8K": LibraryHdriVariant("8K", 8192, 4096, (LibraryHdriFile("maps/8k.exr", "EXR", 1, "a", True),)),
            "4K": LibraryHdriVariant("4K", 4096, 2048, (LibraryHdriFile("maps/4k.hdr", "HDR", 1, "b", True),)),
        },
        None, None, "fp", "now", 2,
    )
    panel = DetailPanel()
    panel.show_asset(asset)
    assert panel.dcc_title.text() == "Export"
    assert panel.dcc_app.currentData() == "blender"
    assert panel.dcc_stack.currentWidget() is panel.blender_dcc_page
    session = HoudiniSession("one", 1234, 10, "22.0", "/shot.hip", "now")
    panel.set_houdini_sessions([session])
    assert panel.houdini_resolution.currentText() == "4K"
    assert panel.houdini_session.isHidden()
    assert panel.houdini_send_button.isEnabled()
    panel.dcc_app.setCurrentIndex(panel.dcc_app.findData("houdini"))
    assert panel.dcc_stack.currentWidget() is panel.houdini_dcc_page
    captured = []
    panel.houdini_send_requested.connect(lambda *args: captured.append(args))
    panel.houdini_send_button.click()
    assert captured[0] == (asset, "4K", "lop", session)
    second = HoudiniSession("two", 1235, 11, "21.0", "", "now")
    panel.set_houdini_sessions([session, second])
    assert not panel.houdini_session.isHidden()

    blender = BlenderSession("blend-one", 2234, 12, "5.2.0", "/shot.blend", "now")
    panel.set_blender_sessions([blender])
    panel.dcc_app.setCurrentIndex(panel.dcc_app.findData("blender"))
    assert panel.dcc_stack.currentWidget() is panel.blender_dcc_page
    assert panel.blender_resolution.currentText() == "4K"
    assert panel.blender_session.isHidden()
    assert panel.blender_world_mode.currentData() == "edit_current"
    captured_blender = []
    panel.blender_send_requested.connect(lambda *args: captured_blender.append(args))
    panel.blender_send_button.click()
    assert captured_blender[0] == (asset, "4K", "edit_current", blender)
    panel.blender_world_mode.setCurrentIndex(1)
    assert panel.blender_world_mode.currentData() == "new"
    panel.deleteLater()
    app.processEvents()


def test_texture_inspector_uses_dcc_material_controls_and_capabilities(tmp_path) -> None:
    app = QApplication.instance() or create_application([])
    asset = LibraryTextureAsset(
        "texture-id", "Stone", "Stone", (), "", "", "", "Folder", "", tmp_path,
        {
            "8K": LibraryResolution("8K", 8192, 8192, {"Base Color": (LibraryMap("Base Color", "8k.jpg", "JPG", 1, "a", preferred=True),)}),
            "4K": LibraryResolution("4K", 4096, 4096, {"Base Color": (LibraryMap("Base Color", "4k.jpg", "JPG", 1, "b", preferred=True),)}),
        },
        None, None, "fp", "now", 2,
    )
    panel = DetailPanel()
    panel.show_asset(asset)
    assert not panel.dcc_title.isHidden()
    assert panel.blender_resolution.currentText() == "4K"
    assert panel.blender_world_mode.isHidden()
    assert panel.blender_send_button.text() == "Send material to Blender"
    old = BlenderSession("old", 1, 1, "5.1", "", "now")
    panel.set_blender_sessions([old])
    assert not panel.blender_send_button.isEnabled()
    assert "Update the Blender plug-in" in panel.blender_status.text()
    current = BlenderSession("new", 1, 1, "5.2", "", "now", "0.3.0", ("hdri", "texture_material"))
    panel.set_blender_sessions([current])
    assert panel.blender_send_button.isEnabled()
    panel.dcc_app.setCurrentIndex(panel.dcc_app.findData("houdini"))
    current_houdini = HoudiniSession("new", 1, 1, "22.0", "", "now", "0.4.0", ("hdri", "texture_material"))
    panel.set_houdini_sessions([current_houdini])
    assert panel.houdini_send_button.isEnabled()
    assert panel.houdini_send_button.text() == "Send material to Houdini"
    panel.deleteLater()
    app.processEvents()


def test_model_inspector_selects_usd_variant_and_houdini_target(tmp_path) -> None:
    app = QApplication.instance() or create_application([])
    asset = LibraryModelAsset(
        id="tree-id", name="Oak Tree", category="Nature", tags=(), description="", author="",
        physical_size="", provider="Poly Haven", provider_id="oak-tree", asset_dir=tmp_path,
        model_files=(
            LibraryModelFile("usd/oak_2k.usdc", "", "USDC", "primary", "LOD1", "", None, False, 1, "a", "2K"),
            LibraryModelFile("usd/oak_4k.usdc", "", "USDC", "primary", "LOD0", "", None, True, 1, "b", "4K"),
        ),
        texture_sets={}, thumbnail_path=None, hero_path=None, fingerprint="fp",
        created_at="now", total_size=2,
    )
    panel = DetailPanel()
    panel.show_asset(asset)
    blender = BlenderSession("blend", 1, 1, "5.2", "", "now", "0.3.0", ("hdri", "texture_material", "usd_model"))
    houdini = HoudiniSession("houdini", 1, 1, "22.0", "", "now", "0.4.0", ("hdri", "texture_material", "usd_model"))
    panel.set_blender_sessions([blender])
    panel.set_houdini_sessions([houdini])
    assert panel.blender_resolution.currentData() == "usd/oak_4k.usdc"
    assert panel.blender_send_button.text() == "Import model into Blender"
    assert panel.blender_send_button.isEnabled()
    panel.dcc_app.setCurrentIndex(panel.dcc_app.findData("houdini"))
    assert not panel.houdini_target.isHidden()
    panel.houdini_target.setCurrentIndex(panel.houdini_target.findData("sop"))
    captured = []
    panel.houdini_send_requested.connect(lambda *args: captured.append(args))
    panel.houdini_send_button.click()
    assert captured[0] == (asset, "usd/oak_4k.usdc", "sop", houdini)

    no_usd = LibraryModelAsset(
        id="fbx-id", name="Legacy Chair", category="Props", tags=(), description="", author="",
        physical_size="", provider="Folder", provider_id="", asset_dir=tmp_path,
        model_files=(
            LibraryModelFile("models/chair.fbx", "", "FBX", "primary", "", "", None, True, 1, "c"),
        ),
        texture_sets={}, thumbnail_path=None, hero_path=None, fingerprint="fbx",
        created_at="now", total_size=1,
    )
    panel.show_asset(no_usd)
    panel.set_blender_sessions([blender])
    assert panel.export_footer.isVisibleTo(panel)
    assert not panel.blender_send_button.isEnabled()
    assert "no managed USD" in panel.blender_status.text()
    panel.deleteLater()
    app.processEvents()
