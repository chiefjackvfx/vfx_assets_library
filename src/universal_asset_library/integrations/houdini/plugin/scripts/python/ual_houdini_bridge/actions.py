from __future__ import annotations

import re
import uuid
from pathlib import Path


OWNER_KEY = "shotbox_assets_owner"
OWNER_VALUE = "shotbox_assets"
LEGACY_OWNER_KEY = "ual_owner"
LEGACY_OWNER_VALUE = "universal_asset_library"
TEXTURE_CONTROL = "xn__inputstexturefile_control_shbh"
TEXTURE_VALUE = "xn__inputstexturefile_r3ah"


class ActionError(RuntimeError):
    pass


def execute(hou, action, payload, session_id):
    if action == "ping":
        return {
            "ok": True,
            "session_id": session_id,
            "diagnostic": "ShotBox Assets Houdini Bridge is ready.",
            "data": _session_data(hou),
        }
    if action == "create_hdri_dome":
        return create_hdri_dome(hou, payload, session_id)
    if action == "create_texture_material":
        return create_texture_material(hou, payload, session_id)
    if action == "import_usd_model":
        return import_usd_model(hou, payload, session_id)
    raise ActionError(f"Unsupported bridge action: {action}")


def create_hdri_dome(hou, payload, session_id):
    hdri_path, library_root = _validated_paths(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    resolution = _required_text(payload, "resolution")
    selected = _selected_lop(hou)
    with hou.undos.group("Send HDRI from ShotBox Assets"):
        owned = selected is not None and (
            selected.userData(OWNER_KEY) == OWNER_VALUE
            or selected.userData(LEGACY_OWNER_KEY) == LEGACY_OWNER_VALUE
        )
        if owned and _is_dome_light(selected):
            node = selected
        else:
            parent = selected.parent() if selected is not None else _stage_network(hou)
            node = parent.createNode("domelight::3.0", f"shotbox_{_slug(asset_name)}")
            if selected is not None:
                node.setInput(0, selected)
        control = node.parm(TEXTURE_CONTROL)
        texture = node.parm(TEXTURE_VALUE)
        primpath = node.parm("primpath")
        missing = [name for name, parm in ((TEXTURE_CONTROL, control), (TEXTURE_VALUE, texture), ("primpath", primpath)) if parm is None]
        if missing:
            if selected is None or node is not selected:
                try:
                    node.destroy()
                except Exception:
                    pass
            raise ActionError("The installed Dome Light is incompatible; missing parameter(s): " + ", ".join(missing))
        prim_path = f"/lights/{node.name()}"
        control.set("set")
        texture.set(hdri_path.as_posix())
        primpath.set(prim_path)
        node.setUserData(OWNER_KEY, OWNER_VALUE)
        node.setUserData("shotbox_asset_id", asset_id)
        node.setUserData("shotbox_asset_name", asset_name)
        node.setUserData("shotbox_resolution", resolution)
        try:
            node.setDisplayFlag(True)
            node.moveToGoodPosition()
            node.setSelected(True, clear_all_selected=True)
            node.setCurrent(True, clear_all_selected=True)
        except Exception:
            pass
    return {
        "ok": True,
        "session_id": session_id,
        "node_path": node.path(),
        "prim_path": prim_path,
        "diagnostic": f"{asset_name} was assigned to {node.path()}.",
        "data": _session_data(hou),
    }


def create_texture_material(hou, payload, session_id):
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    resolution = _required_text(payload, "resolution")
    records, _library_root = _validated_texture_maps(payload)
    selected = _selected_lop(hou)
    stage = _stage_network(hou)
    material_path = f"/materials/{_slug(asset_name)}"
    material_library = _find_owned(stage, asset_id, "material_library")
    created_library = material_library is None
    assignment = _find_owned(stage, asset_id, "material_assignment")
    created_assignment = False
    with hou.undos.group("Send texture material from ShotBox Assets"):
        try:
            if material_library is None:
                parent = selected.parent() if selected is not None else stage
                material_library = parent.createNode("materiallibrary", f"shotbox_{_slug(asset_name)}")
                if selected is not None:
                    material_library.setInput(0, selected)
            _clear_children(material_library)
            builder, surface, displacement = _create_usd_materialx_builder(
                material_library, f"shotbox_{_slug(asset_name)}_mtlx"
            )
            builder.setUserData(OWNER_KEY, OWNER_VALUE)
            builder.setUserData("shotbox_asset_id", asset_id)
            builder.setUserData("shotbox_role", "material_builder")
            sources = _materialx_sources(builder, records)
            _build_materialx_graph(builder, surface, displacement, sources)
            _layout_materialx_graph(builder)
            _set_first_parm(material_library, ("matnet",), ".", required=False)
            _set_first_parm(material_library, ("matpathprefix", "containerpath"), "/materials/", required=False)
            _set_first_parm(material_library, ("nummaterials",), 1, required=False)
            _set_first_parm(material_library, ("matnode1", "matvop1"), builder.path(), required=False)
            _set_first_parm(material_library, ("matpath1", "matpath"), material_path, required=False)
            _mark_houdini_node(material_library, asset_id, asset_name, resolution, "material_library")

            selected_prims = _selected_scene_prims(hou)
            if selected_prims:
                if assignment is None:
                    assignment = material_library.parent().createNode("assignmaterial", f"assign_{_slug(asset_name)}")
                    created_assignment = True
                assignment.setInput(0, material_library)
                _set_first_parm(assignment, ("nummaterials",), 1, required=False)
                _set_first_parm(assignment, ("primpattern1", "primpattern"), " ".join(selected_prims))
                _set_first_parm(assignment, ("matspecpath1", "matspecpath"), material_path)
                _mark_houdini_node(assignment, asset_id, asset_name, resolution, "material_assignment")
                result_node = assignment
            else:
                result_node = material_library
            try:
                result_node.setDisplayFlag(True)
                result_node.moveToGoodPosition()
                result_node.setSelected(True, clear_all_selected=True)
                result_node.setCurrent(True, clear_all_selected=True)
            except Exception:
                pass
        except Exception:
            if created_assignment and assignment is not None:
                _destroy(assignment)
            if created_library and material_library is not None:
                _destroy(material_library)
            raise
    assigned = list(_selected_scene_prims(hou))
    state = f"assigned to {len(assigned)} selected USD primitive(s)" if assigned else "created unassigned"
    missing = [str(value) for value in payload.get("missing_channels", []) if value]
    diagnostic = f"{asset_name} {resolution} MaterialX material {state}."
    if missing:
        diagnostic += " Missing channels: " + ", ".join(missing) + "."
    return {
        "ok": True,
        "session_id": session_id,
        "node_path": result_node.path(),
        "material_name": builder.name(),
        "material_path": material_path,
        "assigned_targets": assigned,
        "diagnostic": diagnostic,
        "data": _session_data(hou),
    }


def import_usd_model(hou, payload, session_id):
    model_path, library_root = _validated_model_path(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    variant = str(payload.get("variant", "")).strip() or str(payload.get("format", "USD")).strip()
    target = _required_text(payload, "target").casefold()
    if target not in {"lop", "sop"}:
        raise ActionError(f"Unsupported Houdini model target: {target}")
    instance_id = str(uuid.uuid4())
    node = None
    import_node = None
    created_container = None
    created_materials = []
    prim_path = ""
    with hou.undos.group("Import USD model from ShotBox Assets"):
        try:
            if target == "lop":
                selected = _selected_lop(hou)
                parent = selected.parent() if selected is not None else _stage_network(hou)
                node = _create_first_node(parent, ("reference::2.0", "reference"), f"shotbox_{_slug(asset_name)}")
                if selected is not None:
                    node.setInput(0, selected)
                prim_path = f"/assets/{node.name()}"
                _set_first_parm(node, ("primpath", "destprimpath"), prim_path)
                _set_first_parm(node, ("numreferences",), 1, required=False)
                _set_first_parm(node, ("filepath1", "filepath", "file"), model_path.as_posix())
                _set_first_parm(node, ("makeinstanceable1", "makeinstanceable", "instanceable"), 0, required=False)
                role = "usd_reference_lop"
            else:
                network, created_container = _selected_sop_network(hou, asset_name)
                import_node = _create_first_node(
                    network, ("usdimport", "usdimport::2.0"), f"shotbox_{_slug(asset_name)}"
                )
                _set_first_parm(import_node, ("filepath", "filepath1", "file"), model_path.as_posix())
                _set_first_parm(import_node, ("unpack", "unpacktopolygons"), 0, required=False)
                node, created_materials = _setup_sop_materials(
                    hou, network, import_node, payload, library_root, asset_id, asset_name
                )
                role = "usd_import_sop"
            _mark_model_node(
                import_node or node, asset_id, asset_name, instance_id, variant,
                target, model_path, role
            )
            if node is not import_node:
                _mark_model_node(
                    node, asset_id, asset_name, instance_id, variant, target,
                    model_path, "sop_material_assignment"
                )
            try:
                node.setDisplayFlag(True)
                node.setRenderFlag(True)
                node.moveToGoodPosition()
                node.setSelected(True, clear_all_selected=True)
                node.setCurrent(True, clear_all_selected=True)
            except Exception:
                pass
        except Exception:
            for material in reversed(created_materials):
                _destroy(material)
            if created_container is not None:
                _destroy(created_container)
            else:
                if node is not None:
                    _destroy(node)
                if import_node is not None and import_node is not node:
                    _destroy(import_node)
            raise
    network_path = node.parent().path()
    diagnostic = (
        f"Referenced {asset_name} ({variant}) at {prim_path}."
        if target == "lop"
        else (
            f"Imported {asset_name} ({variant}) as packed USD primitives and "
            f"assigned {len(created_materials)} MaterialX shader(s) in {node.path()}."
        )
    )
    return {
        "ok": True,
        "session_id": session_id,
        "node_path": node.path(),
        "import_node_path": (import_node or node).path(),
        "material_paths": [material.path() for material in created_materials],
        "network_path": network_path,
        "prim_path": prim_path,
        "model_path": model_path.as_posix(),
        "imported_targets": [prim_path or node.path()],
        "diagnostic": diagnostic,
        "data": _session_data(hou),
    }


def _materialx_sources(builder, records):
    explicit = {record["channel"] for record in records if not record["packed_channels"]}
    sources = {}
    texcoord = builder.createNode("mtlxtexcoord", "uv_coordinates")
    uv_control = builder.createNode("mtlxplace2d", "uv_control")
    _connect_named(uv_control, "texcoord", (texcoord, 0, {}))
    try:
        uv_control.setComment("Adjust Scale X/Y to tile all ShotBox texture maps together.")
    except Exception:
        pass
    for index, record in enumerate(records):
        image = builder.createNode("mtlximage", f"image_{index + 1}_{_slug(record['channel'])}")
        _set_first_parm(image, ("file", "filename"), record["path"].as_posix())
        _connect_named(image, "texcoord", (uv_control, 0, {}))
        colorspace = "srgb_texture" if "srgb" in record["color_space"].casefold() else "raw"
        _set_first_parm(image, ("filecolorspace", "colorspace"), colorspace, required=False)
        image.setUserData("shotbox_source_path", record["path"].as_posix())
        if record["packed_channels"]:
            separate = builder.createNode("mtlxseparate3", f"split_{index + 1}")
            separate.setInput(0, image)
            for component, semantic in record["packed_channels"].items():
                if semantic not in explicit:
                    sources[semantic] = (separate, {"R": 0, "G": 1, "B": 2}.get(component.upper(), 0), record)
        else:
            sources[record["channel"]] = (image, 0, record)
    return sources


def _build_materialx_graph(builder, surface, displacement, sources):
    base = sources.get("Base Color")
    color_source = base
    for semantic in ("Ambient Occlusion", "Cavity"):
        source = sources.get(semantic)
        if source:
            if color_source is None:
                color_source = source
            else:
                multiply = builder.createNode("mtlxmultiply", f"multiply_{_slug(semantic)}")
                multiply.setInput(0, color_source[0], color_source[1])
                multiply.setInput(1, source[0], source[1])
                color_source = (multiply, 0, {})
    if color_source:
        _connect_named(surface, "base_color", color_source)
    if "Roughness" in sources:
        _connect_named(surface, "specular_roughness", sources["Roughness"])
    elif "Glossiness" in sources:
        invert = builder.createNode("mtlxinvert", "invert_glossiness")
        invert.setInput(0, sources["Glossiness"][0], sources["Glossiness"][1])
        _connect_named(surface, "specular_roughness", (invert, 0, {}))
    for semantic, input_name in (
        ("Metalness", "metalness"), ("Specular", "specular"),
        ("Opacity", "opacity"), ("Emission", "emission_color"),
        ("Translucency", "subsurface"),
    ):
        if semantic in sources:
            _connect_named(surface, input_name, sources[semantic])
    if "Emission" in sources:
        _set_named_input_default(surface, "emission", 1.0)
    normal_source = None
    if "Normal" in sources:
        source = sources["Normal"]
        if str(source[2].get("normal_convention", "")).casefold() in {"directx", "dx"}:
            source = _materialx_flip_green(builder, source)
        normal = builder.createNode("mtlxnormalmap", "normal_map")
        normal.setInput(0, source[0], source[1])
        normal_source = (normal, 0, {})
    bump_source = sources.get("Bump") or sources.get("Height")
    if bump_source and normal_source is None:
        bump = builder.createNode("mtlxbump", "bump")
        bump.setInput(0, bump_source[0], bump_source[1])
        normal_source = (bump, 0, {})
    if normal_source:
        _connect_named(surface, "normal", normal_source)
    displacement_source = sources.get("Displacement") or sources.get("Height")
    if displacement_source:
        displacement.setInput(0, displacement_source[0], displacement_source[1])


def _create_usd_materialx_builder(parent, name):
    """Create Houdini's standard USD MaterialX Builder scaffold."""
    try:
        import voptoolutils
    except ImportError:
        voptoolutils = None
    if voptoolutils is not None:
        builder = voptoolutils._setupMtlXBuilderSubnet(
            destination_node=parent,
            name=name,
            mask=voptoolutils.MTLX_TAB_MASK,
            folder_label="USD MaterialX Builder",
            render_context="mtlx",
        )
        if builder is None:
            raise ActionError("Houdini could not create a USD MaterialX Builder.")
    else:
        # Lightweight fallback for bridge tests outside Houdini. Production
        # Houdini uses the official helper above so the native builder UI,
        # connector types, shader language, and tab mask are configured.
        builder = parent.createNode("subnet", name)
        try:
            builder.setShaderLanguageName("MaterialX")
        except Exception:
            pass
        surface = builder.createNode("mtlxstandard_surface", "mtlxstandard_surface")
        displacement = builder.createNode("mtlxdisplacement", "mtlxdisplacement")
        surface_output = builder.createNode("subnetconnector", "surface_output")
        displacement_output = builder.createNode("subnetconnector", "displacement_output")
        surface_output.setInput(0, surface, 0)
        displacement_output.setInput(0, displacement, 0)
        try:
            builder.setMaterialFlag(True)
        except Exception:
            pass
    surface = _child_node(builder, "mtlxstandard_surface")
    displacement = _child_node(builder, "mtlxdisplacement")
    if surface is None or displacement is None:
        raise ActionError("The USD MaterialX Builder is missing its standard surface or displacement node.")
    return builder, surface, displacement


def _layout_materialx_graph(builder):
    """Arrange the complete shader by dependency with comfortable spacing."""
    try:
        builder.layoutChildren(horizontal_spacing=2.0, vertical_spacing=1.25)
    except TypeError:
        builder.layoutChildren()
    except Exception:
        # Layout is cosmetic and must not invalidate an otherwise usable material.
        pass


def _child_node(parent, name):
    try:
        found = parent.node(name)
        if found is not None:
            return found
    except Exception:
        pass
    return next((child for child in _children(parent) if child.name() == name), None)


def _materialx_flip_green(builder, source):
    separate = builder.createNode("mtlxseparate3", "split_directx_normal")
    invert = builder.createNode("mtlxinvert", "invert_normal_green")
    combine = builder.createNode("mtlxcombine3", "combine_opengl_normal")
    separate.setInput(0, source[0], source[1])
    invert.setInput(0, separate, 1)
    combine.setInput(0, separate, 0)
    combine.setInput(1, invert, 0)
    combine.setInput(2, separate, 2)
    return combine, 0, source[2]


def _connect_named(destination, input_name, source):
    try:
        index = destination.inputIndex(input_name)
    except Exception as error:
        raise ActionError(f"The installed MaterialX node is missing input {input_name}.") from error
    if index is None or index < 0:
        raise ActionError(f"The installed MaterialX node is missing input {input_name}.")
    destination.setInput(index, source[0], source[1])


def _set_named_input_default(node, input_name, value):
    try:
        parameter = node.parm(input_name)
        if parameter is not None:
            parameter.set(value)
    except Exception:
        pass


def _validated_texture_maps(payload):
    library = Path(_required_text(payload, "library_root")).expanduser().resolve(strict=True)
    values = payload.get("maps")
    if not library.is_dir() or not isinstance(values, list) or not values:
        raise ActionError("The texture material request has no valid library or maps.")
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
            "channel": _required_text(value, "channel"), "path": path,
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
        raise ActionError("Only managed USD, USDA, USDC, and USDZ models can be sent to Houdini.")
    if not resolved_source.is_file():
        raise ActionError("The managed USD model is not a file.")
    return resolved_source, resolved_library


def _setup_sop_materials(
    hou, network, import_node, payload, library_root, asset_id, asset_name
):
    texture_sets = _validated_model_texture_sets(payload, library_root)
    if not texture_sets:
        return import_node, []
    material_network = _material_network(hou)
    builders = []
    assignment = None
    try:
        for texture_set in texture_sets:
            builder, surface, displacement = _create_usd_materialx_builder(
                material_network,
                f"shotbox_{_slug(asset_name)}_{_slug(texture_set['name'])}",
            )
            builder.setUserData(OWNER_KEY, OWNER_VALUE)
            builder.setUserData("shotbox_asset_id", asset_id)
            builder.setUserData("shotbox_role", "sop_material_builder")
            sources = _materialx_sources(builder, texture_set["maps"])
            _build_materialx_graph(builder, surface, displacement, sources)
            _layout_materialx_graph(builder)
            builders.append(builder)
        assignment = _create_first_node(
            network, ("material",), f"assign_{_slug(asset_name)}"
        )
        assignment.setInput(0, import_node)
        _set_first_parm(
            assignment, ("num_materials", "nummaterials"), len(builders), required=False
        )
        for index, (builder, texture_set) in enumerate(
            zip(builders, texture_sets), start=1
        ):
            group = ""
            if len(builders) > 1:
                usd_name = _usd_material_name(
                    asset_name, texture_set["name"], len(builders)
                )
                group = f"@usdmaterialpath=*{usd_name}*"
            _set_first_parm(
                assignment, (f"group{index}",), group, required=False
            )
            _set_first_parm(
                assignment,
                (f"shop_materialpath{index}", f"matpath{index}"),
                builder.path(),
            )
        assignment.setUserData(OWNER_KEY, OWNER_VALUE)
        assignment.setUserData("shotbox_asset_id", asset_id)
        assignment.setUserData("shotbox_role", "sop_material_assignment")
        return assignment, builders
    except Exception:
        if assignment is not None:
            _destroy(assignment)
        for builder in reversed(builders):
            _destroy(builder)
        raise


def _validated_model_texture_sets(payload, library_root):
    values = payload.get("texture_sets", [])
    if values in (None, []):
        return []
    if not isinstance(values, list):
        raise ActionError("Model texture_sets must be a list.")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ActionError("Model texture-set records must be objects.")
        name = _required_text(value, "name")
        records, _unused = _validated_texture_maps({
            "library_root": library_root.as_posix(),
            "maps": value.get("maps"),
        })
        result.append({
            "name": name,
            "resolution": str(value.get("resolution", "")),
            "maps": records,
        })
    return result


def _material_network(hou):
    network = hou.node("/mat")
    if network is not None:
        return network
    root = hou.node("/")
    if root is None:
        raise ActionError("Houdini's root network is unavailable.")
    return _create_first_node(root, ("matnet",), "mat")


def _usd_material_name(asset_name, texture_set_name, material_count):
    value = asset_name if material_count == 1 else f"{asset_name}_{texture_set_name}"
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "Model"


def _find_owned(root, asset_id, role):
    pending = list(_children(root))
    while pending:
        node = pending.pop()
        if node.userData("shotbox_asset_id") == asset_id and node.userData("shotbox_role") == role:
            return node
        pending.extend(_children(node))
    return None


def _clear_children(node):
    for child in tuple(_children(node)):
        _destroy(child)


def _children(node):
    value = getattr(node, "children", ())
    return value() if callable(value) else value


def _destroy(node):
    try:
        node.destroy()
    except Exception:
        pass


def _mark_houdini_node(node, asset_id, asset_name, resolution, role):
    for key, value in (
        (OWNER_KEY, OWNER_VALUE), ("shotbox_asset_id", asset_id),
        ("shotbox_asset_name", asset_name), ("shotbox_resolution", resolution),
        ("shotbox_role", role),
    ):
        node.setUserData(key, value)


def _mark_model_node(node, asset_id, asset_name, instance_id, variant, target, model_path, role):
    for key, value in (
        (OWNER_KEY, OWNER_VALUE),
        ("shotbox_asset_id", asset_id),
        ("shotbox_asset_name", asset_name),
        ("shotbox_instance_id", instance_id),
        ("shotbox_variant", variant),
        ("shotbox_target", target),
        ("shotbox_model_path", model_path.as_posix()),
        ("shotbox_role", role),
    ):
        node.setUserData(key, value)


def _create_first_node(parent, node_types, name):
    errors = []
    for node_type in node_types:
        try:
            return parent.createNode(node_type, name)
        except Exception as error:
            errors.append(str(error))
    raise ActionError(f"Houdini could not create {' or '.join(node_types)}: {'; '.join(errors)}")


def _selected_sop_network(hou, asset_name):
    try:
        selected = tuple(hou.selectedNodes())
    except Exception:
        selected = ()
    for node in reversed(selected):
        category = _node_category(node)
        if category == "sop":
            return node.parent(), None
        if category in {"object", "obj"} and _base_type(node) == "geo":
            return node, None
    obj = hou.node("/obj")
    if obj is None:
        root = hou.node("/")
        if root is None:
            raise ActionError("Houdini's root network is unavailable.")
        obj = _create_first_node(root, ("objnet",), "obj")
    name = f"shotbox_{_slug(asset_name)}"
    try:
        container = obj.createNode("geo", name, run_init_scripts=False)
    except TypeError:
        container = obj.createNode("geo", name)
    except Exception as error:
        raise ActionError(f"Houdini could not create the SOP Geometry container: {error}") from error
    return container, container


def _node_category(node):
    try:
        return node.type().category().name().casefold()
    except Exception:
        return ""


def _base_type(node):
    try:
        return node.type().name().split("::", 1)[0].casefold()
    except Exception:
        return ""


def _set_first_parm(node, names, value, required=True):
    for name in names:
        parameter = node.parm(name)
        if parameter is not None:
            parameter.set(value)
            return
    if required:
        raise ActionError("The installed Houdini node is missing parameter: " + " / ".join(names))


def _selected_scene_prims(hou):
    try:
        viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
        return tuple(str(path) for path in viewer.currentSceneGraphSelection()) if viewer is not None else ()
    except Exception:
        return ()


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
        raise ActionError("Only managed HDR and EXR files can be sent to Houdini.")
    if not resolved_source.is_file():
        raise ActionError("The managed HDRI is not a file.")
    return resolved_source, resolved_library


def _required_text(payload, key):
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ActionError(f"Bridge request is missing {key}.")
    return value


def _stage_network(hou):
    stage = hou.node("/stage")
    if stage is not None:
        return stage
    root = hou.node("/")
    if root is None:
        raise ActionError("Houdini's root network is unavailable.")
    try:
        return root.createNode("stage", "stage")
    except Exception as error:
        raise ActionError(f"Could not create the /stage Solaris network: {error}") from error


def _selected_lop(hou):
    try:
        selected = tuple(hou.selectedNodes())
    except Exception:
        return None
    for node in reversed(selected):
        try:
            if node.type().category().name().casefold() == "lop":
                return node
        except Exception:
            continue
    return None


def _is_dome_light(node):
    try:
        return node.type().name().split("::", 1)[0].casefold() == "domelight"
    except Exception:
        return False


def _slug(value):
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
    return slug or "hdri"


def _session_data(hou):
    try:
        version = hou.applicationVersionString()
    except Exception:
        version = "Unknown"
    try:
        hip_file = hou.hipFile.path()
    except Exception:
        hip_file = ""
    return {
        "houdini_version": version,
        "hip_file": hip_file,
        "bridge_version": "0.5.0",
        "capabilities": ["hdri", "texture_material", "usd_model"],
    }
