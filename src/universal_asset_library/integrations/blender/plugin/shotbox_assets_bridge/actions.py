from __future__ import annotations

import re
import uuid
from pathlib import Path


OWNER_KEY = "shotbox_assets_owner"
OWNER_VALUE = "shotbox_assets"


class ActionError(RuntimeError):
    pass


def execute(bpy, action, payload, session_id):
    if action == "ping":
        return {
            "ok": True,
            "session_id": session_id,
            "diagnostic": "ShotBox Assets Blender Bridge is ready.",
            "data": session_data(bpy),
        }
    if action == "set_hdri_world":
        return set_hdri_world(bpy, payload, session_id)
    if action == "create_texture_material":
        return create_texture_material(bpy, payload, session_id)
    if action == "import_usd_model":
        return import_usd_model(bpy, payload, session_id)
    raise ActionError(f"Unsupported bridge action: {action}")


def set_hdri_world(bpy, payload, session_id):
    hdri_path, _library_root = _validated_paths(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    resolution = _required_text(payload, "resolution")
    world_mode = _required_text(payload, "world_mode")
    if world_mode not in {"new", "edit_current"}:
        raise ActionError(f"Unsupported World mode: {world_mode}")
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    if scene is None:
        raise ActionError("Blender has no active scene.")
    image = bpy.data.images.load(str(hdri_path), check_existing=True)
    if world_mode == "new":
        world = bpy.data.worlds.new(f"ShotBox_{_identifier(asset_name)}")
        world.use_nodes = True
        tree = world.node_tree
        tree.nodes.clear()
        environment = tree.nodes.new("ShaderNodeTexEnvironment")
        environment.name = "ShotBox Environment"
        background = tree.nodes.new("ShaderNodeBackground")
        background.name = "ShotBox Background"
        output = tree.nodes.new("ShaderNodeOutputWorld")
        output.name = "ShotBox World Output"
        environment.image = image
        tree.links.new(environment.outputs["Color"], background.inputs["Color"])
        tree.links.new(background.outputs["Background"], output.inputs["Surface"])
        scene.world = world
    else:
        world = scene.world
        if world is None:
            raise ActionError("The active scene has no World. Choose Create new World instead.")
        world.use_nodes = True
        tree = world.node_tree
        output, background = _active_background(tree)
        if output is None or background is None:
            raise ActionError(
                "The current World does not have a standard Background connected to its active World Output. "
                "Choose Create new World to preserve this custom node graph."
            )
        environment = _environment_for_background(background)
        if environment is None:
            environment = tree.nodes.new("ShaderNodeTexEnvironment")
            environment.name = "ShotBox Environment"
            tree.links.new(environment.outputs["Color"], background.inputs["Color"])
        environment.image = image
    _mark(world, environment, asset_id, asset_name, resolution, hdri_path)
    mode_label = "created" if world_mode == "new" else "updated"
    return {
        "ok": True,
        "session_id": session_id,
        "world_name": world.name,
        "image_name": image.name,
        "diagnostic": f"{asset_name} {resolution} {mode_label} World {world.name}.",
        "data": session_data(bpy),
    }


def create_texture_material(bpy, payload, session_id):
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    resolution = _required_text(payload, "resolution")
    records, _library = _validated_texture_maps(payload)
    materials = bpy.data.materials
    material = next((item for item in materials if _property(item, "shotbox_asset_id") == asset_id), None)
    created = material is None
    if created:
        material = materials.new(f"ShotBox_{_identifier(asset_name)}")
    try:
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.name = "ShotBox Material Output"
        shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
        shader.name = "ShotBox Principled BSDF"
        _link(tree, _socket(shader.outputs, "BSDF"), _socket(output.inputs, "Surface"))
        sources = _texture_sources(tree, bpy.data.images, records)
        _build_principled_graph(tree, material, shader, output, sources)
        _layout_texture_material(tree)
        values = {
            OWNER_KEY: OWNER_VALUE,
            "shotbox_asset_id": asset_id,
            "shotbox_asset_name": asset_name,
            "shotbox_resolution": resolution,
        }
        for key, value in values.items():
            material[key] = value
            shader[key] = value
            output[key] = value
    except Exception:
        if created:
            try:
                materials.remove(material)
            except Exception:
                pass
        raise

    assigned = []
    for obj in tuple(getattr(getattr(bpy, "context", None), "selected_objects", ())):
        if str(getattr(obj, "type", "")).upper() != "MESH" or getattr(obj, "data", None) is None:
            continue
        slots = obj.data.materials
        if slots:
            index = min(max(int(getattr(obj, "active_material_index", 0)), 0), len(slots) - 1)
            slots[index] = material
        else:
            slots.append(material)
            index = 0
        try:
            obj.active_material_index = index
        except Exception:
            pass
        assigned.append(str(getattr(obj, "name", "Mesh")))
    missing = [str(value) for value in payload.get("missing_channels", []) if value]
    assignment = f"assigned to {len(assigned)} selected mesh object(s)" if assigned else "created unassigned"
    diagnostic = f"{asset_name} {resolution} {assignment}."
    if missing:
        diagnostic += " Missing channels: " + ", ".join(missing) + "."
    return {
        "ok": True,
        "session_id": session_id,
        "material_name": material.name,
        "assigned_targets": assigned,
        "diagnostic": diagnostic,
        "data": session_data(bpy),
    }


def import_usd_model(bpy, payload, session_id):
    model_path, _library = _validated_model_path(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    variant = str(payload.get("variant", "")).strip() or str(payload.get("format", "USD")).strip()
    instance_id = str(uuid.uuid4())
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    if scene is None:
        raise ActionError("Blender has no active scene.")
    before = {
        name: _identity_set(getattr(getattr(bpy, "data", None), name, ()))
        for name in ("objects", "collections", "materials", "images")
    }
    container = None
    root = None
    try:
        result = bpy.ops.wm.usd_import(
            filepath=str(model_path),
            import_cameras=False,
            import_lights=False,
            import_materials=True,
            create_collection=False,
            relative_path=False,
        )
        if result is not None and "FINISHED" not in result:
            raise ActionError("Blender's USD importer did not finish successfully.")
        imported = _new_data(bpy, "objects", before["objects"])
        for item in tuple(imported):
            if str(getattr(item, "type", "")).upper() in {"CAMERA", "LIGHT"}:
                _remove_data(getattr(bpy.data, "objects", None), item)
                imported.remove(item)
        if not imported:
            raise ActionError("The USD file did not produce any importable geometry.")

        identifier = f"ShotBox_{_identifier(asset_name)}"
        container = bpy.data.collections.new(identifier)
        scene.collection.children.link(container)
        root = bpy.data.objects.new(identifier, None)
        container.objects.link(root)
        roots = [item for item in imported if getattr(item, "parent", None) not in imported]
        for item in roots:
            world_matrix = _copy_value(getattr(item, "matrix_world", None))
            item.parent = root
            if world_matrix is not None:
                item.matrix_world = world_matrix
            _link_object(container, item)
        root.location = _cursor_location(scene)
        for owner in (container, root):
            _mark_model_owner(owner, asset_id, asset_name, instance_id, variant, model_path)
        try:
            root.select_set(True)
            bpy.context.view_layer.objects.active = root
        except Exception:
            pass
    except Exception:
        _rollback_model_import(bpy, before, root, container)
        raise
    names = [str(getattr(item, "name", "Object")) for item in imported]
    return {
        "ok": True,
        "session_id": session_id,
        "model_path": model_path.as_posix(),
        "collection_name": str(getattr(container, "name", "")),
        "root_object": str(getattr(root, "name", "")),
        "imported_targets": names,
        "diagnostic": f"Imported {asset_name} ({variant}) as {len(names)} Blender object(s).",
        "data": session_data(bpy),
    }


def _texture_sources(tree, images, records):
    explicit = {record["channel"] for record in records if not record["packed_channels"]}
    sources = {}
    image_nodes = {}
    texture_coordinates = tree.nodes.new("ShaderNodeTexCoord")
    texture_coordinates.name = "ShotBox Texture Coordinate"
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.name = "ShotBox Mapping"
    _link(
        tree,
        _socket(texture_coordinates.outputs, "UV"),
        _socket(mapping.inputs, "Vector"),
    )
    for record in records:
        image = images.load(str(record["path"]), check_existing=True)
        _set_image_color_space(image, record["color_space"], record["channel"])
        node = image_nodes.get(record["path"])
        if node is None:
            node = tree.nodes.new("ShaderNodeTexImage")
            node.name = f"ShotBox {record['channel']}"
            node.label = record["channel"]
            node.image = image
            node["shotbox_source_path"] = record["path"].as_posix()
            _link(
                tree,
                _socket(mapping.outputs, "Vector"),
                _socket(node.inputs, "Vector"),
            )
            image_nodes[record["path"]] = node
        if record["packed_channels"]:
            separate = tree.nodes.new("ShaderNodeSeparateColor")
            separate.name = f"ShotBox Split {record['channel']}"
            _link(tree, _socket(node.outputs, "Color"), _socket(separate.inputs, "Color", "Image"))
            for component, semantic in record["packed_channels"].items():
                if semantic not in explicit:
                    names = {
                        "R": ("Red", "R"), "G": ("Green", "G"),
                        "B": ("Blue", "B"), "A": ("Alpha", "A"),
                    }.get(component.upper(), (component,))
                    sources[semantic] = (_socket(separate.outputs, *names), record)
        else:
            sources[record["channel"]] = (_socket(node.outputs, "Color"), record)
    return sources


def _build_principled_graph(tree, material, shader, output, sources):
    base = sources.get("Base Color")
    color_output = base[0] if base else None
    for semantic in ("Ambient Occlusion", "Cavity"):
        source = sources.get(semantic)
        if source:
            if color_output is None:
                color_output = source[0]
            else:
                multiply = tree.nodes.new("ShaderNodeMixRGB")
                multiply.name = f"ShotBox Multiply {semantic}"
                multiply.blend_type = "MULTIPLY"
                multiply.inputs[0].default_value = 1.0
                _link(tree, color_output, multiply.inputs[1])
                _link(tree, source[0], multiply.inputs[2])
                color_output = _socket(multiply.outputs, "Color")
    if color_output is not None:
        _link(tree, color_output, _socket(shader.inputs, "Base Color"))

    roughness = sources.get("Roughness")
    if roughness:
        _link(tree, roughness[0], _socket(shader.inputs, "Roughness"))
    elif sources.get("Glossiness"):
        invert = tree.nodes.new("ShaderNodeInvert")
        invert.name = "ShotBox Invert Glossiness"
        _link(tree, sources["Glossiness"][0], _socket(invert.inputs, "Color"))
        _link(tree, _socket(invert.outputs, "Color"), _socket(shader.inputs, "Roughness"))
    for semantic, names in (
        ("Metalness", ("Metallic",)),
        ("Specular", ("Specular IOR Level", "Specular")),
        ("Opacity", ("Alpha",)),
        ("Emission", ("Emission Color", "Emission")),
        ("Translucency", ("Subsurface Weight", "Subsurface")),
    ):
        if semantic in sources:
            _link(tree, sources[semantic][0], _socket(shader.inputs, *names))
    if "Emission" in sources:
        strength = _optional_socket(shader.inputs, "Emission Strength")
        if strength is not None:
            strength.default_value = 1.0
    if "Opacity" in sources and hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"

    normal_output = None
    if "Normal" in sources:
        source, record = sources["Normal"]
        if str(record["normal_convention"]).casefold() in {"directx", "dx"}:
            source = _flip_green(tree, source)
        normal = tree.nodes.new("ShaderNodeNormalMap")
        normal.name = "ShotBox Normal Map"
        _link(tree, source, _socket(normal.inputs, "Color"))
        normal_output = _socket(normal.outputs, "Normal")
    bump_source = sources.get("Bump") or sources.get("Height")
    if bump_source and normal_output is None:
        bump = tree.nodes.new("ShaderNodeBump")
        bump.name = "ShotBox Bump"
        _link(tree, bump_source[0], _socket(bump.inputs, "Height"))
        normal_output = _socket(bump.outputs, "Normal")
    if normal_output is not None:
        _link(tree, normal_output, _socket(shader.inputs, "Normal"))
    displacement_source = sources.get("Displacement") or sources.get("Height")
    if displacement_source:
        displacement = tree.nodes.new("ShaderNodeDisplacement")
        displacement.name = "ShotBox Displacement"
        _link(tree, displacement_source[0], _socket(displacement.inputs, "Height"))
        _link(tree, _socket(displacement.outputs, "Displacement"), _socket(output.inputs, "Displacement"))
        _enable_bump_and_displacement(material)


def _enable_bump_and_displacement(material):
    try:
        material.displacement_method = "BOTH"
        return
    except Exception:
        pass
    try:
        material.cycles.displacement_method = "BOTH"
    except Exception:
        pass


def _layout_texture_material(tree):
    columns = {
        "TEX_COORD": -1400,
        "MAPPING": -1150,
        "TEX_IMAGE": -850,
        "SEPARATE_COLOR": -540,
        "COMBINE_COLOR": -300,
        "MATH": -300,
        "INVERT": -300,
        "MIX_RGB": -260,
        "NORMAL_MAP": -220,
        "BUMP": -220,
        "DISPLACEMENT": 40,
        "BSDF_PRINCIPLED": 120,
        "OUTPUT_MATERIAL": 520,
    }
    rows = {}
    for node in tree.nodes:
        node_type = str(getattr(node, "type", ""))
        column = columns.get(node_type, -20)
        row = rows.get(column, 0)
        try:
            node.location = (column, -row * 260)
            if node_type == "TEX_IMAGE":
                node.width = 260
        except Exception:
            pass
        rows[column] = row + 1


def _flip_green(tree, source):
    separate = tree.nodes.new("ShaderNodeSeparateColor")
    combine = tree.nodes.new("ShaderNodeCombineColor")
    invert = tree.nodes.new("ShaderNodeMath")
    separate.name = "ShotBox Split DirectX Normal"
    combine.name = "ShotBox Combine OpenGL Normal"
    invert.name = "ShotBox Invert Normal Green"
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    _link(tree, source, _socket(separate.inputs, "Color", "Image"))
    _link(tree, _socket(separate.outputs, "Red", "R"), _socket(combine.inputs, "Red", "R"))
    _link(tree, _socket(separate.outputs, "Green", "G"), invert.inputs[1])
    _link(tree, invert.outputs[0], _socket(combine.inputs, "Green", "G"))
    _link(tree, _socket(separate.outputs, "Blue", "B"), _socket(combine.inputs, "Blue", "B"))
    return _socket(combine.outputs, "Color", "Image")


def _validated_texture_maps(payload):
    library = Path(_required_text(payload, "library_root")).expanduser().resolve(strict=True)
    if not library.is_dir():
        raise ActionError("The supplied library root is not a directory.")
    values = payload.get("maps")
    if not isinstance(values, list) or not values:
        raise ActionError("The texture material request has no maps.")
    records = []
    for value in values:
        if not isinstance(value, dict):
            raise ActionError("Texture map records must be objects.")
        path = Path(str(value.get("path", ""))).expanduser().resolve(strict=True)
        try:
            path.relative_to(library)
        except ValueError as error:
            raise ActionError("A texture map is outside the managed library root.") from error
        if not path.is_file():
            raise ActionError(f"Managed texture is not a file: {path}")
        records.append({
            "channel": _required_text(value, "channel"),
            "path": path,
            "color_space": str(value.get("color_space", "")),
            "normal_convention": str(value.get("normal_convention", "")),
            "packed_channels": dict(value.get("packed_channels", {})) if isinstance(value.get("packed_channels", {}), dict) else {},
        })
    return records, library


def _validated_model_path(payload):
    source = Path(_required_text(payload, "model_path")).expanduser()
    library = Path(_required_text(payload, "library_root")).expanduser()
    try:
        resolved_source = source.resolve(strict=True)
        resolved_library = library.resolve(strict=True)
    except OSError as error:
        raise ActionError(f"The managed USD path is unavailable: {error}") from error
    if not resolved_library.is_dir():
        raise ActionError("The supplied library root is not a directory.")
    try:
        resolved_source.relative_to(resolved_library)
    except ValueError as error:
        raise ActionError("The USD model is outside the managed library root.") from error
    if resolved_source.suffix.casefold() not in {".usd", ".usda", ".usdc", ".usdz"}:
        raise ActionError("Only managed USD, USDA, USDC, and USDZ models can be sent to Blender.")
    if not resolved_source.is_file():
        raise ActionError("The managed USD model is not a file.")
    return resolved_source, resolved_library


def _identity_set(values):
    try:
        return {id(value) for value in values}
    except TypeError:
        return set()


def _new_data(bpy, name, before):
    return [value for value in tuple(getattr(getattr(bpy, "data", None), name, ())) if id(value) not in before]


def _remove_data(collection, value):
    if collection is None:
        return
    try:
        collection.remove(value, do_unlink=True)
    except TypeError:
        try:
            collection.remove(value)
        except Exception:
            pass
    except Exception:
        pass


def _rollback_model_import(bpy, before, root, container):
    for value in reversed(_new_data(bpy, "objects", before["objects"])):
        _remove_data(getattr(bpy.data, "objects", None), value)
    for name in ("collections", "materials", "images"):
        values = list(reversed(_new_data(bpy, name, before[name])))
        for value in values:
            _remove_data(getattr(bpy.data, name, None), value)


def _copy_value(value):
    try:
        return value.copy()
    except Exception:
        return value


def _cursor_location(scene):
    try:
        return scene.cursor.location.copy()
    except Exception:
        return (0.0, 0.0, 0.0)


def _link_object(collection, obj):
    try:
        if obj not in collection.objects:
            collection.objects.link(obj)
    except Exception:
        pass


def _mark_model_owner(owner, asset_id, asset_name, instance_id, variant, model_path):
    for key, value in (
        (OWNER_KEY, OWNER_VALUE),
        ("shotbox_asset_id", asset_id),
        ("shotbox_asset_name", asset_name),
        ("shotbox_instance_id", instance_id),
        ("shotbox_variant", variant),
        ("shotbox_model_path", model_path.as_posix()),
    ):
        owner[key] = value


def _set_image_color_space(image, declared, channel):
    color = str(declared).casefold()
    preferred = "sRGB" if "srgb" in color or (not color and channel in {"Base Color", "Emission"}) else "Non-Color"
    try:
        image.colorspace_settings.name = preferred
    except Exception:
        if preferred == "Non-Color":
            try:
                image.colorspace_settings.name = "Raw"
            except Exception:
                pass


def _socket(sockets, *names):
    socket = _optional_socket(sockets, *names)
    if socket is None:
        raise ActionError("The installed Blender shader is missing socket: " + " / ".join(names))
    return socket


def _optional_socket(sockets, *names):
    for name in names:
        try:
            value = sockets.get(name)
        except AttributeError:
            value = None
        if value is not None:
            return value
    return None


def _link(tree, output, input_socket):
    tree.links.new(output, input_socket)


def _property(owner, key):
    try:
        return owner.get(key)
    except Exception:
        return None


def _active_background(tree):
    outputs = [node for node in tree.nodes if getattr(node, "type", "") == "OUTPUT_WORLD"]
    outputs.sort(key=lambda node: not bool(getattr(node, "is_active_output", False)))
    for output in outputs:
        surface = output.inputs.get("Surface")
        links = tuple(getattr(surface, "links", ())) if surface is not None else ()
        if links and getattr(links[0].from_node, "type", "") == "BACKGROUND":
            return output, links[0].from_node
    return None, None


def _environment_for_background(background):
    color = background.inputs.get("Color")
    for link in tuple(getattr(color, "links", ())) if color is not None else ():
        if getattr(link.from_node, "type", "") == "TEX_ENVIRONMENT":
            return link.from_node
    return None


def _mark(world, environment, asset_id, asset_name, resolution, hdri_path):
    values = {
        OWNER_KEY: OWNER_VALUE,
        "shotbox_asset_id": asset_id,
        "shotbox_asset_name": asset_name,
        "shotbox_resolution": resolution,
        "shotbox_hdri_path": hdri_path.as_posix(),
    }
    for key, value in values.items():
        world[key] = value
        environment[key] = value


def _validated_paths(payload):
    source = Path(_required_text(payload, "hdri_path")).expanduser()
    library = Path(_required_text(payload, "library_root")).expanduser()
    try:
        resolved_source = source.resolve(strict=True)
        resolved_library = library.resolve(strict=True)
    except OSError as error:
        raise ActionError(f"The managed HDRI path is unavailable: {error}") from error
    if not resolved_library.is_dir():
        raise ActionError("The supplied library root is not a directory.")
    try:
        resolved_source.relative_to(resolved_library)
    except ValueError as error:
        raise ActionError("The HDRI path is outside the managed library root.") from error
    if resolved_source.suffix.casefold() not in {".hdr", ".exr"}:
        raise ActionError("Only managed HDR and EXR files can be sent to Blender.")
    if not resolved_source.is_file():
        raise ActionError("The managed HDRI is not a file.")
    return resolved_source, resolved_library


def _required_text(payload, key):
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ActionError(f"Bridge request is missing {key}.")
    return value


def _identifier(value):
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_")
    return cleaned or "HDRI"


def session_data(bpy):
    version = str(getattr(getattr(bpy, "app", None), "version_string", "Unknown"))
    blend_file = str(getattr(getattr(bpy, "data", None), "filepath", ""))
    return {
        "blender_version": version,
        "blend_file": blend_file,
        "bridge_version": "0.4.1",
        "capabilities": ["hdri", "texture_material", "usd_model"],
    }
