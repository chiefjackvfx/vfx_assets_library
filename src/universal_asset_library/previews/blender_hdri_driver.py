"""Executed inside Blender; do not import bpy from the desktop application."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import bpy


GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--hdri", required=True)
    parser.add_argument("--scene-output", required=True)
    parser.add_argument("--panorama-output", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def configure_cycles_gpu(scene) -> tuple[str, list[str]]:
    if scene.render.engine != "CYCLES":
        raise RuntimeError("The HDRI preview template must use the Cycles render engine.")
    preferences = bpy.context.preferences.addons["cycles"].preferences
    for backend in GPU_BACKENDS:
        try:
            preferences.compute_device_type = backend
            preferences.refresh_devices()
        except (AttributeError, RuntimeError, TypeError):
            continue
        devices = [
            device for device in preferences.devices
            if device.type == backend
        ]
        if not devices:
            continue
        for device in preferences.devices:
            device.use = device.type == backend
        scene.cycles.device = "GPU"
        return backend, [device.name for device in devices]
    raise RuntimeError(
        "No compatible Cycles GPU device was found. "
        "Enable an OptiX, CUDA, HIP, oneAPI, or Metal device in Blender."
    )


def main() -> None:
    args = arguments()
    scene = bpy.context.scene
    if scene.camera is None:
        raise RuntimeError("The HDRI preview template has no active camera.")
    actual_size = (
        round(scene.render.resolution_x * scene.render.resolution_percentage / 100),
        round(scene.render.resolution_y * scene.render.resolution_percentage / 100),
    )
    if actual_size not in {(1024, 256), (2048, 512)}:
        raise RuntimeError(
            "The template must render at 1024x256 "
            f"(or the legacy 2048x512), not {actual_size[0]}x{actual_size[1]}."
        )
    world = bpy.data.worlds.get("World.001")
    if world is None:
        raise RuntimeError("The HDRI preview template must contain the World.001 World.")
    if scene.world != world:
        raise RuntimeError("The template scene must use World.001 as its configured World.")
    if world is None or world.node_tree is None:
        raise RuntimeError("World.001 must use nodes.")
    environment = world.node_tree.nodes.get("Environment Texture")
    if environment is None or environment.bl_idname != "ShaderNodeTexEnvironment":
        raise RuntimeError("World.001 must contain an Environment Texture image node named 'Environment Texture'.")
    environment.image = bpy.data.images.load(str(Path(args.hdri).resolve()), check_existing=False)
    gpu_backend, gpu_devices = configure_cycles_gpu(scene)

    original = {
        "camera": scene.camera,
        "filepath": scene.render.filepath,
        "format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "percentage": scene.render.resolution_percentage,
        "film_transparent": scene.render.film_transparent,
    }
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    scene.render.filepath = str(Path(args.scene_output).resolve())
    bpy.ops.render.render(write_still=True)

    # The upper preview is the source equirectangular image itself. Do not project it
    # through a camera or onto geometry. Scaling this newly loaded image datablock is
    # safe because the template is never saved.
    environment.image.scale(1024, 512)
    environment.image.save_render(str(Path(args.panorama_output).resolve()), scene=scene)

    scene.camera = original["camera"]
    scene.render.filepath = original["filepath"]
    scene.render.image_settings.file_format = original["format"]
    scene.render.image_settings.color_mode = original["color_mode"]
    scene.render.resolution_x = original["resolution_x"]
    scene.render.resolution_y = original["resolution_y"]
    scene.render.resolution_percentage = original["percentage"]
    scene.render.film_transparent = original["film_transparent"]
    Path(args.result).write_text(json.dumps({
        "blender_version": bpy.app.version_string,
        "compute_device_type": gpu_backend,
        "render_device": "GPU",
        "gpu_devices": gpu_devices,
    }), encoding="utf-8")


if __name__ == "__main__":
    main()
