from __future__ import annotations

import bpy

from .shotbox_assets_bridge import panel, server


def register():
    panel.register()
    if not bpy.app.background:
        try:
            server.start(bpy)
        except Exception as error:
            print(f"ShotBox Assets Blender Bridge did not start: {error}")


def unregister():
    server.stop()
    panel.unregister()
