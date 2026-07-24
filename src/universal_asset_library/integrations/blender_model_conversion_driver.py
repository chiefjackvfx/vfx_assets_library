"""Standalone Blender-side driver for static managed model conversion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import traceback


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def _operator(operator, **values):
    try:
        supported = {item.identifier for item in operator.get_rna_type().properties}
        values = {key: value for key, value in values.items() if key in supported}
    except Exception:
        pass
    result = operator(**values)
    if result is not None and "FINISHED" not in result:
        raise RuntimeError("Blender operator did not finish successfully.")


def _import_source(bpy, source):
    path = str(source["path"])
    file_format = str(source["format"]).upper()
    if file_format == "BLEND":
        with bpy.data.libraries.load(path, link=False) as (available, requested):
            requested.objects = list(available.objects)
        loaded = [obj for obj in requested.objects if obj is not None]
        for obj in loaded:
            if not tuple(getattr(obj, "users_collection", ())):
                bpy.context.scene.collection.objects.link(obj)
        if not loaded:
            raise RuntimeError("The BLEND source contains no objects.")
        return
    if file_format == "FBX":
        operator = getattr(getattr(bpy.ops, "wm", None), "fbx_import", None)
        operator = operator or getattr(getattr(bpy.ops, "import_scene", None), "fbx", None)
        if operator is None:
            raise RuntimeError("This Blender installation has no FBX importer.")
        _operator(operator, filepath=path)
    elif file_format == "OBJ":
        _operator(bpy.ops.wm.obj_import, filepath=path)
    elif file_format in {"GLTF", "GLB"}:
        _operator(bpy.ops.import_scene.gltf, filepath=path)
    elif file_format == "ABC":
        _operator(bpy.ops.wm.alembic_import, filepath=path, set_frame_range=False)
    else:
        raise RuntimeError(f"Unsupported source format: {file_format}")


def _remove_non_geometry(bpy):
    for obj in tuple(bpy.data.objects):
        if str(getattr(obj, "type", "")).upper() in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def _validate_static(bpy):
    if any(str(getattr(obj, "type", "")).upper() == "ARMATURE" for obj in bpy.data.objects):
        raise RuntimeError("Static conversion does not support armatures or rigs.")
    if tuple(getattr(bpy.data, "actions", ())):
        raise RuntimeError("Static conversion does not support animation actions.")
    for obj in bpy.data.objects:
        data = getattr(obj, "data", None)
        keys = getattr(data, "shape_keys", None)
        if keys is not None and len(getattr(keys, "key_blocks", ())) > 1:
            raise RuntimeError("Static conversion does not support blend shapes.")


def _normalized(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _material_slots(bpy):
    slots = {}
    meshes = [obj for obj in bpy.data.objects if str(getattr(obj, "type", "")).upper() == "MESH"]
    if not meshes:
        raise RuntimeError("The source did not produce any mesh geometry.")
    for obj in meshes:
        for slot in getattr(obj, "material_slots", ()):
            material = getattr(slot, "material", None)
            name = str(getattr(material, "name", "") or getattr(slot, "name", "")).strip()
            if name:
                slots.setdefault(name, []).append(slot)
    unassigned = [
        obj for obj in meshes
        if not tuple(getattr(obj, "material_slots", ()))
    ]
    return meshes, slots, unassigned


def _match_materials(slots, texture_sets):
    by_name = {}
    for item in texture_sets:
        key = _normalized(item["name"])
        if key in by_name:
            raise RuntimeError(
                f"Managed texture-set names are ambiguous after normalization: {item['name']}"
            )
        by_name[key] = item
    matched = {}
    for name in slots:
        texture_set = by_name.get(_normalized(name))
        if texture_set is not None:
            matched[name] = texture_set
    if len(slots) == 1 and len(texture_sets) == 1 and not matched:
        matched[next(iter(slots))] = texture_sets[0]
    missing = [name for name in slots if name not in matched]
    if missing:
        raise RuntimeError(
            "Managed texture sets could not be matched to material slots: "
            + ", ".join(sorted(missing, key=str.casefold))
        )
    return matched


def _socket(sockets, *names):
    for name in names:
        try:
            return sockets[name]
        except (KeyError, TypeError):
            pass
    raise RuntimeError(f"Blender material socket is unavailable: {names[0]}")


def _link(tree, output, input_socket):
    tree.links.new(output, input_socket)


def _copy_texture(record, texture_set, output_dir, used_names):
    source = Path(record["path"]).resolve(strict=True)
    folder = output_dir / "textures" / _safe_name(texture_set["name"])
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(source.stem)
    name = stem + source.suffix.casefold()
    index = 2
    while (folder / name).as_posix().casefold() in used_names:
        name = f"{stem}_{index}{source.suffix.casefold()}"
        index += 1
    destination = folder / name
    shutil.copy2(source, destination)
    used_names.add(destination.as_posix().casefold())
    return destination


def _build_material(bpy, texture_set, output_dir, used_names, material_name):
    material = bpy.data.materials.new(_safe_name(material_name))
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    _link(tree, _socket(shader.outputs, "BSDF"), _socket(output.inputs, "Surface"))
    sources = {}
    for record in texture_set["maps"]:
        copied = _copy_texture(record, texture_set, output_dir, used_names)
        image = bpy.data.images.load(str(copied), check_existing=True)
        channel = str(record["channel"])
        if channel not in {"Base Color", "Emission"}:
            try:
                image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = f"ShotBox {channel}"
        node.image = image
        if record.get("packed_channels"):
            separate = tree.nodes.new("ShaderNodeSeparateColor")
            _link(tree, _socket(node.outputs, "Color"), _socket(separate.inputs, "Color", "Image"))
            for component, semantic in record["packed_channels"].items():
                names = {"R": ("Red", "R"), "G": ("Green", "G"), "B": ("Blue", "B"), "A": ("Alpha", "A")}
                sources[str(semantic)] = _socket(separate.outputs, *names.get(str(component).upper(), (str(component),)))
        else:
            sources[channel] = _socket(node.outputs, "Color")
    color_output = sources.get("Base Color")
    for semantic in ("Ambient Occlusion", "Cavity"):
        source = sources.get(semantic)
        if source is None:
            continue
        if color_output is None:
            color_output = source
        else:
            multiply = tree.nodes.new("ShaderNodeMixRGB")
            multiply.name = f"ShotBox Multiply {semantic}"
            multiply.blend_type = "MULTIPLY"
            multiply.inputs[0].default_value = 1.0
            _link(tree, color_output, multiply.inputs[1])
            _link(tree, source, multiply.inputs[2])
            color_output = _socket(multiply.outputs, "Color")
    if color_output is not None:
        _link(tree, color_output, _socket(shader.inputs, "Base Color"))
    for channel, names in (
        ("Roughness", ("Roughness",)),
        ("Metalness", ("Metallic",)),
        ("Specular", ("Specular IOR Level", "Specular")),
        ("Opacity", ("Alpha",)),
        ("Emission", ("Emission Color", "Emission")),
    ):
        if channel in sources:
            _link(tree, sources[channel], _socket(shader.inputs, *names))
    normal_output = None
    normal_semantic = _normal_semantic(sources)
    if normal_semantic == "Normal":
        normal_node = tree.nodes.new("ShaderNodeNormalMap")
        normal_node.name = "ShotBox Normal"
        _link(tree, sources["Normal"], _socket(normal_node.inputs, "Color"))
        normal_output = _socket(normal_node.outputs, "Normal")
    elif normal_semantic == "Bump":
        bump_node = tree.nodes.new("ShaderNodeBump")
        bump_node.name = "ShotBox Bump"
        _link(tree, sources["Bump"], _socket(bump_node.inputs, "Height"))
        normal_output = _socket(bump_node.outputs, "Normal")
    if normal_output is not None:
        _link(tree, normal_output, _socket(shader.inputs, "Normal"))
    displacement = sources.get("Displacement")
    if displacement is None:
        displacement = sources.get("Height")
    if displacement is not None:
        displacement_node = tree.nodes.new("ShaderNodeDisplacement")
        displacement_node.name = "ShotBox Displacement"
        _link(tree, displacement, _socket(displacement_node.inputs, "Height"))
        _link(
            tree,
            _socket(displacement_node.outputs, "Displacement"),
            _socket(output.inputs, "Displacement"),
        )
    if "Opacity" in sources:
        try:
            material.surface_render_method = "DITHERED"
        except Exception:
            pass
    return material


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_") or "Model"


def _material_name(asset_name, texture_set_name, material_count):
    return _safe_name(
        asset_name if material_count == 1 else f"{asset_name}_{texture_set_name}"
    )


def _normal_semantic(sources):
    if sources.get("Normal") is not None:
        return "Normal"
    if sources.get("Bump") is not None:
        return "Bump"
    return ""


def _axis_selection(value):
    text = str(value).strip().upper()
    return f"NEGATIVE_{text[1:]}" if text.startswith("-") else text


def _validate_usd(entry, output_dir, up_axis, expected_materials):
    from pxr import Sdf, Usd, UsdGeom, UsdShade

    stage = Usd.Stage.Open(str(entry))
    if stage is None:
        raise RuntimeError("The exported USDC could not be reopened.")
    actual_up = str(UsdGeom.GetStageUpAxis(stage)).upper()
    if actual_up != up_axis.lstrip("-").upper():
        raise RuntimeError(f"USD stage up-axis is {actual_up}, expected {up_axis}.")
    mesh_count = 0
    materials = set()
    root = output_dir.resolve()
    for layer in stage.GetUsedLayers():
        real_path = str(getattr(layer, "realPath", "") or "")
        if not real_path:
            continue
        try:
            Path(real_path).resolve().relative_to(root)
        except ValueError as error:
            raise RuntimeError(
                f"USD layer escapes the derivative folder: {real_path}"
            ) from error
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh_count += 1
        material, _relationship = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        if material:
            materials.add(str(material.GetPath()))
        for attribute in prim.GetAttributes():
            value = attribute.Get()
            paths = []
            if isinstance(value, Sdf.AssetPath):
                paths = [value.path]
            elif isinstance(value, (list, tuple)):
                paths = [item.path for item in value if isinstance(item, Sdf.AssetPath)]
            for path in paths:
                if not path or path.startswith("anon:"):
                    continue
                dependency = (entry.parent / path).resolve()
                try:
                    dependency.relative_to(root)
                except ValueError as error:
                    raise RuntimeError(f"USD dependency escapes the derivative folder: {path}") from error
    if mesh_count < 1:
        raise RuntimeError("The exported USDC contains no meshes.")
    if len(materials) < expected_materials:
        raise RuntimeError(
            f"The exported USDC retained {len(materials)} material bindings; expected {expected_materials}."
        )
    return mesh_count, len(materials)


def _reimport_usd(bpy, entry, expected_materials):
    _operator(bpy.ops.wm.read_factory_settings, use_empty=True)
    _operator(
        bpy.ops.wm.usd_import,
        filepath=str(entry),
        import_cameras=False,
        import_lights=False,
        import_materials=True,
        relative_path=False,
    )
    meshes = [
        obj for obj in bpy.data.objects
        if str(getattr(obj, "type", "")).upper() == "MESH"
    ]
    material_names = {
        str(getattr(getattr(slot, "material", None), "name", ""))
        for obj in meshes
        for slot in getattr(obj, "material_slots", ())
        if getattr(slot, "material", None) is not None
    }
    if not meshes:
        raise RuntimeError("Blender could not reopen any meshes from the generated USDC.")
    if len(material_names) < expected_materials:
        raise RuntimeError(
            "Blender could not reopen all expected materials from the generated USDC."
        )
    return len(meshes), len(material_names)


def convert(bpy, request):
    output_dir = Path(request["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _operator(bpy.ops.wm.read_factory_settings, use_empty=True)
    _import_source(bpy, request["source"])
    _remove_non_geometry(bpy)
    _validate_static(bpy)
    meshes, slots, unassigned = _material_slots(bpy)
    if unassigned and len(request["texture_sets"]) != 1:
        raise RuntimeError(
            "Meshes without material slots require exactly one managed texture set."
        )
    matches = _match_materials(slots, request["texture_sets"]) if slots else {}
    if not slots:
        if len(request["texture_sets"]) != 1:
            raise RuntimeError(
                "A source without material slots requires exactly one managed texture set."
            )
        matches["__unassigned__"] = request["texture_sets"][0]
    elif unassigned:
        matches["__unassigned__"] = request["texture_sets"][0]
    used_names = set()
    built_materials = {}
    unique_sets = {
        _normalized(texture_set["name"])
        for texture_set in matches.values()
    }
    for name, texture_set in matches.items():
        set_key = _normalized(texture_set["name"])
        material = built_materials.get(set_key)
        if material is None:
            material = _build_material(
                bpy,
                texture_set,
                output_dir,
                used_names,
                _material_name(request["asset_name"], texture_set["name"], len(unique_sets)),
            )
            built_materials[set_key] = material
        if name == "__unassigned__":
            for obj in unassigned:
                obj.data.materials.append(material)
        else:
            for slot in slots[name]:
                slot.material = material
    entry = output_dir / f"{_safe_name(request['asset_name'])}.usdc"
    for obj in bpy.data.objects:
        try:
            obj.select_set(True)
        except Exception:
            pass
    _operator(
        bpy.ops.wm.usd_export,
        filepath=str(entry),
        selected_objects_only=False,
        export_materials=True,
        generate_preview_surface=True,
        export_textures_mode="KEEP",
        relative_paths=True,
        convert_orientation=True,
        export_global_forward_selection=_axis_selection(request["forward_axis"]),
        export_global_up_selection=_axis_selection(request["up_axis"]),
        root_prim_path="/" + _safe_name(request["asset_name"]),
        export_cameras=False,
        export_lights=False,
        export_animation=False,
    )
    _validate_usd(
        entry, output_dir, request["up_axis"], len(unique_sets),
    )
    mesh_count, material_count = _reimport_usd(bpy, entry, len(unique_sets))
    return {
        "ok": True,
        "entry_path": entry.relative_to(output_dir).as_posix(),
        "mesh_count": mesh_count,
        "material_count": material_count,
        "blender_version": str(bpy.app.version_string),
        "diagnostics": [],
    }


def main():
    arguments = _arguments()
    result_path = Path(arguments.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import bpy

        request = json.loads(Path(arguments.request).read_text(encoding="utf-8"))
        result = convert(bpy, request)
    except Exception as error:
        result = {
            "ok": False,
            "diagnostic": str(error),
            "traceback": traceback.format_exc(limit=8),
        }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
