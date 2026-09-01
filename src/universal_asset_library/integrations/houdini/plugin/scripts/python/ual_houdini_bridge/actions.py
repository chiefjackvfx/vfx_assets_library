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
    if action == "import_fbx_model":
        return import_fbx_model(hou, payload, session_id)
    if action == "import_vdb":
        return import_vdb(hou, payload, session_id)
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


def import_vdb(hou, payload, session_id):
    path_expression, _library_root = _validated_vdb_path(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    variant = _required_text(payload, "variant")
    is_sequence = bool(payload.get("is_sequence", False))
    network, created_container = _selected_sop_network(hou, asset_name)
    node = None
    with hou.undos.group("Import VDB from ShotBox Assets"):
        try:
            node = _create_first_node(network, ("file",), f"shotbox_{_slug(asset_name)}_{_slug(variant)}")
            _set_first_parm(node, ("file", "file1"), path_expression)
            if is_sequence:
                _set_missing_frame_no_geometry(node)
            node.setUserData(OWNER_KEY, OWNER_VALUE)
            node.setUserData("shotbox_asset_id", asset_id)
            node.setUserData("shotbox_asset_name", asset_name)
            node.setUserData("shotbox_variant", variant)
            node.setUserData("shotbox_role", "vdb_file_sop")
            try:
                node.setDisplayFlag(True)
                node.setRenderFlag(True)
                node.moveToGoodPosition()
                node.setSelected(True, clear_all_selected=True)
                node.setCurrent(True, clear_all_selected=True)
            except Exception:
                pass
        except Exception:
            if created_container is not None:
                _destroy(created_container)
            elif node is not None:
                _destroy(node)
            raise
    grids = []
    try:
        geometry = node.geometry()
        for primitive in geometry.prims():
            name = str(primitive.stringAttribValue("name") or "").strip()
            if name and name not in grids:
                grids.append(name)
    except Exception:
        pass
    return {
        "ok": True,
        "session_id": session_id,
        "node_path": node.path(),
        "network_path": network.path(),
        "diagnostic": (
            f"Created {node.path()} for {asset_name} ({variant})."
            + (f" Grids: {', '.join(grids)}." if grids else "")
        ),
        "data": {**_session_data(hou), "grids": grids},
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


def import_fbx_model(hou, payload, session_id):
    model_path, library_root = _validated_fbx_path(payload)
    asset_id = _required_text(payload, "asset_id")
    asset_name = _required_text(payload, "asset_name")
    variant = str(payload.get("variant", "")).strip() or "FBX"
    resolution = str(payload.get("resolution", "")).strip()
    target = _required_text(payload, "target").casefold()
    if target != "sop":
        raise ActionError("FBX models can only be imported into Houdini SOPs.")
    texture_sets = _validated_model_texture_sets(payload, library_root)
    instance_id = str(uuid.uuid4())
    imported_root = None
    builders = []
    assignments = []
    native_materials = []
    imported_sops = []
    messages = ""
    with hou.undos.group("Import FBX model from ShotBox Assets"):
        try:
            material_children = {
                id(child) for child in _network_children_by_path(hou, ("/mat", "/shop"))
            }
            result = hou.hipFile.importFBX(
                model_path.as_posix(),
                suppress_save_prompt=True,
                merge_into_scene=True,
                import_cameras=False,
                import_joints_and_skin=False,
                import_geometry=True,
                import_lights=False,
                import_animation=False,
                import_materials=bool(texture_sets),
                convert_file_paths_to_relative=False,
                unlock_geometry=True,
                unlock_deformations=False,
                import_nulls_as_subnets=True,
                import_into_object_subnet=True,
                override_scene_frame_range=False,
            )
            if not isinstance(result, tuple) or not result:
                raise ActionError("Houdini's FBX importer did not return an imported object subnet.")
            imported_root = result[0]
            messages = str(result[1] if len(result) > 1 else "").strip()
            if imported_root is None or imported_root.path() in {"/", "/obj"}:
                raise ActionError("Houdini's FBX importer did not create an isolated object subnet.")
            imported_sops = _fbx_geometry_outputs(imported_root)
            if not imported_sops:
                raise ActionError("The FBX file did not produce any importable SOP geometry.")
            native_materials = [
                child for child in _network_children_by_path(hou, ("/mat", "/shop"))
                if id(child) not in material_children
            ]
            if texture_sets:
                builders, assignments = _setup_fbx_materials(
                    hou, imported_sops, texture_sets,
                    asset_id, asset_name, instance_id, resolution, model_path,
                )
            for node in _descendants(imported_root):
                role = str(node.userData("shotbox_role") or "")
                if not role:
                    role = "fbx_file_sop" if _base_type(node) == "file" else "fbx_import_node"
                _mark_model_node(
                    node, asset_id, asset_name, instance_id, variant,
                    target, model_path, role,
                )
                node.setUserData("shotbox_resolution", resolution)
            _mark_model_node(
                imported_root, asset_id, asset_name, instance_id, variant,
                target, model_path, "fbx_object_subnet",
            )
            imported_root.setUserData("shotbox_resolution", resolution)
            for native in reversed(native_materials):
                _destroy(native)
            try:
                imported_root.moveToGoodPosition()
                imported_root.setSelected(True, clear_all_selected=True)
                imported_root.setCurrent(True, clear_all_selected=True)
            except Exception:
                pass
        except Exception:
            for assignment in reversed(assignments):
                _destroy(assignment)
            for builder in reversed(builders):
                _destroy(builder)
            for native in reversed(native_materials):
                _destroy(native)
            if imported_root is not None and imported_root.path() not in {"/", "/obj"}:
                _destroy(imported_root)
            raise
    diagnostic = (
        f"Imported {asset_name} ({variant}) as live FBX SOP geometry"
        + (f" with {len(builders)} managed MaterialX shader(s)." if builders else " without materials.")
    )
    if messages:
        diagnostic += f" FBX importer: {messages}"
    return {
        "ok": True,
        "session_id": session_id,
        "node_path": imported_root.path(),
        "network_path": imported_root.path(),
        "model_path": model_path.as_posix(),
        "material_paths": [builder.path() for builder in builders],
        "imported_targets": [node.path() for node in imported_sops],
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
    ):
        if semantic in sources:
            _connect_named(surface, input_name, sources[semantic])
    subsurface = sources.get("Translucency") or sources.get("Thickness")
    if subsurface:
        _connect_named(surface, "subsurface", subsurface)
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


def _validated_fbx_path(payload):
    source = Path(_required_text(payload, "model_path")).expanduser()
    library = Path(_required_text(payload, "library_root")).expanduser()
    try:
        resolved_source = source.resolve(strict=True)
        resolved_library = library.resolve(strict=True)
    except OSError as error:
        raise ActionError(f"The managed FBX path is unavailable: {error}") from error
    if not resolved_library.is_dir():
        raise ActionError("The supplied library root is not a directory.")
    try:
        resolved_source.relative_to(resolved_library)
    except ValueError as error:
        raise ActionError("The FBX model is outside the managed library root.") from error
    if resolved_source.suffix.casefold() != ".fbx":
        raise ActionError("Only managed FBX files can use the Houdini FBX importer.")
    if not resolved_source.is_file():
        raise ActionError("The managed FBX model is not a file.")
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


def _setup_fbx_materials(
    hou, imported_sops, texture_sets,
    asset_id, asset_name, instance_id, resolution, model_path,
):
    by_name = {}
    for item in texture_sets:
        key = _normalized_name(item["name"])
        if key in by_name:
            raise ActionError(
                f"Managed texture-set names are ambiguous after normalization: {item['name']}"
            )
        by_name[key] = item

    assignments_by_sop = []
    used_sets = {}
    for sop in imported_sops:
        material_paths, has_unassigned = _fbx_material_bindings(sop)
        if len(texture_sets) == 1:
            item = texture_sets[0]
            assignments_by_sop.append((sop, [("", item)]))
            used_sets[_normalized_name(item["name"])] = item
            continue
        if not material_paths or has_unassigned:
            raise ActionError(
                "FBX geometry without complete material assignments requires exactly one managed texture set."
            )
        matched = []
        missing = []
        for material_path in material_paths:
            item = by_name.get(_normalized_name(material_path.rsplit("/", 1)[-1]))
            if item is None:
                missing.append(material_path)
            else:
                matched.append((material_path, item))
                used_sets[_normalized_name(item["name"])] = item
        if missing:
            raise ActionError(
                "Managed texture sets could not be matched to FBX material slots: "
                + ", ".join(sorted(missing, key=str.casefold))
            )
        assignments_by_sop.append((sop, matched))

    material_network = _material_network(hou)
    builders = []
    assignments = []
    built = {}
    try:
        for key, item in used_sets.items():
            builder, surface, displacement = _create_usd_materialx_builder(
                material_network,
                f"shotbox_{_slug(asset_name)}_{_slug(item['name'])}_{instance_id[:8]}",
            )
            _mark_model_node(
                builder, asset_id, asset_name, instance_id,
                f"{item['resolution']} · FBX Material", "sop", model_path,
                "fbx_material_builder",
            )
            builder.setUserData("shotbox_texture_set", item["name"])
            builder.setUserData("shotbox_resolution", item["resolution"] or resolution)
            sources = _materialx_sources(builder, item["maps"])
            _build_materialx_graph(builder, surface, displacement, sources)
            _layout_materialx_graph(builder)
            builders.append(builder)
            built[key] = builder

        for sop, bindings in assignments_by_sop:
            assignment = _create_first_node(
                sop.parent(), ("material",), f"assign_{_slug(asset_name)}"
            )
            assignment.setInput(0, sop)
            _set_first_parm(
                assignment, ("num_materials", "nummaterials"), len(bindings), required=False
            )
            for index, (source_path, item) in enumerate(bindings, start=1):
                group = ""
                if source_path:
                    escaped = source_path.replace("\\", "\\\\").replace('"', '\\"')
                    group = f'@shop_materialpath="{escaped}"'
                _set_first_parm(assignment, (f"group{index}",), group, required=False)
                _set_first_parm(
                    assignment,
                    (f"shop_materialpath{index}", f"matpath{index}"),
                    built[_normalized_name(item["name"])].path(),
                )
            _mark_model_node(
                assignment, asset_id, asset_name, instance_id,
                "FBX Material Assignment", "sop", model_path,
                "fbx_material_assignment",
            )
            try:
                assignment.setDisplayFlag(True)
                assignment.setRenderFlag(True)
                assignment.moveToGoodPosition()
            except Exception:
                pass
            assignments.append(assignment)
        return builders, assignments
    except Exception:
        for assignment in reversed(assignments):
            _destroy(assignment)
        for builder in reversed(builders):
            _destroy(builder)
        raise


def _fbx_material_bindings(sop):
    paths = []
    has_unassigned = False
    try:
        primitives = tuple(sop.geometry().prims())
    except Exception as error:
        raise ActionError(f"Houdini could not inspect imported FBX geometry: {error}") from error
    if not primitives:
        return [], True
    for primitive in primitives:
        try:
            value = str(primitive.stringAttribValue("shop_materialpath") or "").strip()
        except Exception:
            value = ""
        if not value:
            has_unassigned = True
        elif value not in paths:
            paths.append(value)
    return paths, has_unassigned


def _fbx_geometry_outputs(imported_root):
    outputs = []
    for node in (imported_root, *_descendants(imported_root)):
        if _node_category(node) not in {"object", "obj"} or _base_type(node) != "geo":
            continue
        display = None
        try:
            display = node.displayNode()
        except Exception:
            pass
        if display is None:
            display = next(
                (child for child in reversed(tuple(_children(node))) if _node_category(child) == "sop"),
                None,
            )
        if display is not None and display not in outputs:
            outputs.append(display)
    return outputs


def _network_children_by_path(hou, paths):
    result = []
    for path in paths:
        network = hou.node(path)
        if network is not None:
            result.extend(tuple(_children(network)))
    return result


def _descendants(node):
    result = []
    pending = list(_children(node))
    while pending:
        child = pending.pop(0)
        result.append(child)
        pending.extend(_children(child))
    return result


def _normalized_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


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


def _validated_vdb_path(payload):
    expression = _required_text(payload, "vdb_path")
    library = Path(_required_text(payload, "library_root")).expanduser()
    try:
        resolved_library = library.resolve(strict=True)
    except OSError as error:
        raise ActionError(f"The managed VDB library is unavailable: {error}") from error
    if not resolved_library.is_dir():
        raise ActionError("The supplied library root is not a directory.")
    sample = expression
    if bool(payload.get("is_sequence", False)):
        try:
            frame = int(payload["frame_start"])
            padding = max(1, int(payload.get("padding", 1)))
        except (KeyError, TypeError, ValueError) as error:
            raise ActionError("The VDB sequence has invalid frame metadata.") from error
        sample = sample.replace(f"$F{padding}", f"{frame:0{padding}d}")
    try:
        resolved_sample = Path(sample).expanduser().resolve(strict=True)
        resolved_sample.relative_to(resolved_library)
    except (OSError, ValueError) as error:
        raise ActionError("The VDB path is unavailable or outside the managed library root.") from error
    if resolved_sample.suffix.casefold() != ".vdb" or not resolved_sample.is_file():
        raise ActionError("Only managed .vdb files can be sent to Houdini.")
    return expression, resolved_library


def _set_missing_frame_no_geometry(node):
    for name in ("missingframe", "missingfile"):
        parm = node.parm(name)
        if parm is None:
            continue
        try:
            tokens = tuple(parm.menuItems())
            token = next((value for value in tokens if "nogeo" in value.casefold()), None)
            parm.set(token if token is not None else 1)
        except Exception:
            parm.set(1)
        return


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
        "bridge_version": "0.7.0",
        "capabilities": ["hdri", "texture_material", "usd_model", "fbx_model", "vdb_file"],
    }
