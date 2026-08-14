"""Executed inside Blender; do not import bpy from the desktop application."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import bpy


GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")
_ACTIONS = None


def arguments():
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--maps-json", default="")
    # Compatibility with preview jobs launched by an application process that
    # was already running when the all-map request format was installed.
    parser.add_argument("--base-color", default="")
    parser.add_argument("--roughness", default="")
    parser.add_argument("--normal", default="")
    parser.add_argument("--normal-convention", default="")
    parser.add_argument("--displacement", default="")
    parser.add_argument("--thumbnail-output", required=True)
    parser.add_argument("--hero-output", required=True)
    parser.add_argument("--blend-output", default="")
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def configure_cycles_gpu(scene) -> tuple[str, list[str]]:
    if scene.render.engine != "CYCLES":
        raise RuntimeError(
            "The texture preview template must use the Cycles render engine."
        )
    preferences = bpy.context.preferences.addons["cycles"].preferences
    for backend in GPU_BACKENDS:
        try:
            preferences.compute_device_type = backend
            preferences.refresh_devices()
        except (AttributeError, RuntimeError, TypeError):
            continue
        devices = [
            device for device in preferences.devices if device.type == backend
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


def _material_actions():
    global _ACTIONS
    if _ACTIONS is not None:
        return _ACTIONS
    path = (
        Path(__file__).resolve().parents[1]
        / "integrations"
        / "blender"
        / "plugin"
        / "shotbox_assets_bridge"
        / "actions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "shotbox_preview_material_actions", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Could not load the Blender material builder: {path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _ACTIONS = module
    return _ACTIONS


def _map_records(path: str) -> list[dict]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, list) or not document:
        raise RuntimeError("The texture preview request contains no maps.")
    records = []
    for value in document:
        if not isinstance(value, dict):
            raise RuntimeError("Texture preview map records must be objects.")
        source = Path(str(value.get("path", ""))).resolve()
        if not source.is_file():
            raise RuntimeError(f"Texture preview source is missing: {source}")
        records.append(
            {
                "channel": str(value.get("channel", "")),
                "path": source,
                "color_space": str(value.get("color_space", "")),
                "normal_convention": str(
                    value.get("normal_convention", "")
                ),
                "packed_channels": (
                    dict(value.get("packed_channels", {}))
                    if isinstance(value.get("packed_channels"), dict)
                    else {}
                ),
            }
        )
    return records


def _legacy_map_records(args) -> list[dict]:
    if not args.base_color:
        raise RuntimeError(
            "The texture preview request contains neither --maps-json nor "
            "a legacy --base-color map. Restart ShotBox and try again."
        )
    values = (
        ("Base Color", args.base_color, "sRGB", ""),
        ("Roughness", args.roughness, "Non-Color", ""),
        (
            "Normal",
            args.normal,
            "Non-Color",
            args.normal_convention,
        ),
        ("Displacement", args.displacement, "Non-Color", ""),
    )
    records = []
    for channel, value, color_space, convention in values:
        if not value:
            continue
        source = Path(value).resolve()
        if not source.is_file():
            raise RuntimeError(f"Texture preview source is missing: {source}")
        records.append(
            {
                "channel": channel,
                "path": source,
                "color_space": color_space,
                "normal_convention": convention,
                "packed_channels": {},
            }
        )
    return records


def _build_material(material, records: list[dict]) -> None:
    actions = _material_actions()
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "ShotBox Material Output"
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    shader.name = "ShotBox Principled BSDF"
    actions._link(
        tree,
        actions._socket(shader.outputs, "BSDF"),
        actions._socket(output.inputs, "Surface"),
    )
    sources = actions._texture_sources(
        tree, bpy.data.images, records
    )
    actions._build_principled_graph(
        tree, material, shader, output, sources
    )
    actions._layout_texture_material(tree)


def render_job(
    payload: dict,
    gpu_metadata: dict,
    *,
    progress=None,
) -> dict:
    scene = bpy.context.scene
    if scene.render.engine != "CYCLES":
        raise RuntimeError(
            "The texture preview template must use the Cycles render engine."
        )
    thumbnail_camera = bpy.data.objects.get("render_ball")
    hero_camera = bpy.data.objects.get("render_plane")
    if thumbnail_camera is None or thumbnail_camera.type != "CAMERA":
        raise RuntimeError(
            "The texture preview template must contain the render_ball camera."
        )
    if hero_camera is None or hero_camera.type != "CAMERA":
        raise RuntimeError(
            "The texture preview template must contain the render_plane camera."
        )
    actual_size = (
        round(scene.render.resolution_x * scene.render.resolution_percentage / 100),
        round(scene.render.resolution_y * scene.render.resolution_percentage / 100),
    )
    if actual_size != (512, 512):
        raise RuntimeError(
            "The texture preview template must render at 512x512, "
            f"not {actual_size[0]}x{actual_size[1]}."
        )
    material = bpy.data.materials.get("Material.001")
    if material is None or material.node_tree is None:
        raise RuntimeError(
            "The texture preview template must contain the node material Material.001."
        )
    maps_json = str(payload.get("maps_json", ""))
    records = payload.get("_records")
    if records is None:
        if not maps_json:
            raise RuntimeError("The texture preview request contains no maps JSON.")
        records = _map_records(maps_json)
    if not any(record["channel"] == "Base Color" for record in records):
        raise RuntimeError(
            "A Base Color map is required for a texture preview."
        )
    _build_material(material, records)

    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.camera = thumbnail_camera
    scene.render.filepath = str(
        Path(str(payload["thumbnail_output"])).resolve()
    )
    if progress:
        progress("Rendering texture thumbnail")
    bpy.ops.render.render(write_still=True)
    scene.camera = hero_camera
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 512
    scene.render.filepath = str(Path(str(payload["hero_output"])).resolve())
    if progress:
        progress("Rendering texture hero")
    bpy.ops.render.render(write_still=True)
    blend_output = str(payload.get("blend_output", ""))
    if blend_output:
        if progress:
            progress("Saving texture preview scene")
        for image in bpy.data.images:
            if image.source == "FILE" and not image.packed_file:
                image.pack()
        bpy.ops.wm.save_as_mainfile(
            filepath=str(Path(blend_output).resolve()),
            copy=True,
        )
    scene.camera = thumbnail_camera
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    return {
        **gpu_metadata,
        "thumbnail_camera": thumbnail_camera.name,
        "hero_camera": hero_camera.name,
        "map_channels": [record["channel"] for record in records],
    }


def main() -> None:
    args = arguments()
    scene = bpy.context.scene
    gpu_backend, gpu_devices = configure_cycles_gpu(scene)
    metadata = render_job(
        {
            "maps_json": args.maps_json,
            "_records": (
                None if args.maps_json else _legacy_map_records(args)
            ),
            "thumbnail_output": args.thumbnail_output,
            "hero_output": args.hero_output,
            "blend_output": args.blend_output,
        },
        {
            "compute_device_type": gpu_backend,
            "render_device": "GPU",
            "gpu_devices": gpu_devices,
        },
    )
    Path(args.result).write_text(
        json.dumps({"blender_version": bpy.app.version_string, **metadata}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
