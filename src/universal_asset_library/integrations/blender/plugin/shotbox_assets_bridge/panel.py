from __future__ import annotations

import bpy

from . import server


class SHOTBOX_PT_assets_bridge(bpy.types.Panel):
    bl_label = "ShotBox Assets"
    bl_idname = "SHOTBOX_PT_assets_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ShotBox Assets"

    def draw(self, context):
        layout = self.layout
        bridge = server.instance()
        if bridge is None:
            layout.label(text="Bridge disconnected", icon="ERROR")
            return
        layout.label(text=f"Connected · v{server.BRIDGE_VERSION}", icon="LINKED")
        scene_name = bpy.path.basename(bpy.data.filepath) if bpy.data.filepath else "Untitled"
        layout.label(text=scene_name, icon="FILE_BLEND")
        layout.separator()
        layout.label(text="Last received asset")
        layout.label(text="HDRIs · materials · USD models", icon="ASSET_MANAGER")
        layout.label(text=bridge.last_asset or "None")
        if bridge.last_resolution:
            layout.label(text=f"Resolution: {bridge.last_resolution}")
        if bridge.last_world:
            layout.label(text=f"World: {bridge.last_world}")
        status = layout.box()
        status.label(text=bridge.last_result)


CLASSES = (SHOTBOX_PT_assets_bridge,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
