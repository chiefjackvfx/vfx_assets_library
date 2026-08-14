from __future__ import annotations

import importlib
import queue
import sys
import threading
import time
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parents[1] / "src/universal_asset_library/integrations/blender/plugin"
sys.path.insert(0, str(PLUGIN_ROOT))
actions = importlib.import_module("shotbox_assets_bridge.actions")
server_module = importlib.import_module("shotbox_assets_bridge.server")


class Socket:
    def __init__(self, name):
        self.name = name
        self.links = []
        self.default_value = 0.0


class Link:
    def __init__(self, output, input_socket, from_node):
        self.from_socket = output
        self.to_socket = input_socket
        self.from_node = from_node


class Node:
    TYPES = {
        "ShaderNodeTexEnvironment": "TEX_ENVIRONMENT",
        "ShaderNodeBackground": "BACKGROUND",
        "ShaderNodeOutputWorld": "OUTPUT_WORLD",
        "ShaderNodeOutputMaterial": "OUTPUT_MATERIAL",
        "ShaderNodeBsdfPrincipled": "BSDF_PRINCIPLED",
        "ShaderNodeTexCoord": "TEX_COORD",
        "ShaderNodeMapping": "MAPPING",
        "ShaderNodeTexImage": "TEX_IMAGE",
        "ShaderNodeSeparateColor": "SEPARATE_COLOR",
        "ShaderNodeCombineColor": "COMBINE_COLOR",
        "ShaderNodeMixRGB": "MIX_RGB",
        "ShaderNodeNormalMap": "NORMAL_MAP",
        "ShaderNodeBump": "BUMP",
        "ShaderNodeDisplacement": "DISPLACEMENT",
        "ShaderNodeInvert": "INVERT",
        "ShaderNodeMath": "MATH",
    }

    def __init__(self, node_type):
        self.type = self.TYPES[node_type]
        self.name = node_type
        self.image = None
        self.is_active_output = self.type == "OUTPUT_WORLD"
        self.inputs = {}
        self.outputs = {}
        self.properties = {}
        if self.type == "TEX_ENVIRONMENT":
            self.outputs["Color"] = Socket("Color")
        elif self.type == "BACKGROUND":
            self.inputs["Color"] = Socket("Color")
            self.outputs["Background"] = Socket("Background")
        else:
            if self.type == "OUTPUT_WORLD":
                self.inputs["Surface"] = Socket("Surface")
            elif self.type == "OUTPUT_MATERIAL":
                self.inputs.update({name: Socket(name) for name in ("Surface", "Displacement")})
            elif self.type == "BSDF_PRINCIPLED":
                self.inputs.update({name: Socket(name) for name in (
                    "Base Color", "Roughness", "Metallic", "Specular IOR Level", "Alpha",
                    "Emission Color", "Emission Strength", "Subsurface Weight", "Normal",
                )})
                self.outputs["BSDF"] = Socket("BSDF")
            elif self.type == "TEX_COORD":
                self.outputs["UV"] = Socket("UV")
            elif self.type == "MAPPING":
                self.inputs["Vector"] = Socket("Vector")
                self.outputs["Vector"] = Socket("Vector")
            elif self.type == "TEX_IMAGE":
                self.inputs["Vector"] = Socket("Vector")
                self.outputs["Color"] = Socket("Color")
            elif self.type == "SEPARATE_COLOR":
                self.inputs["Color"] = Socket("Color")
                self.outputs.update({name: Socket(name) for name in ("R", "G", "B", "Red", "Green", "Blue")})
            elif self.type == "COMBINE_COLOR":
                self.inputs.update({name: Socket(name) for name in ("R", "G", "B", "Red", "Green", "Blue")})
                self.outputs["Color"] = Socket("Color")
            elif self.type == "MIX_RGB":
                self.inputs.update({index: Socket(str(index)) for index in range(3)})
                self.outputs["Color"] = Socket("Color")
            elif self.type == "NORMAL_MAP":
                self.inputs["Color"] = Socket("Color")
                self.outputs["Normal"] = Socket("Normal")
            elif self.type == "BUMP":
                self.inputs.update({name: Socket(name) for name in ("Height", "Normal")})
                self.outputs["Normal"] = Socket("Normal")
            elif self.type == "DISPLACEMENT":
                self.inputs["Height"] = Socket("Height")
                self.outputs["Displacement"] = Socket("Displacement")
            elif self.type == "INVERT":
                self.inputs["Color"] = Socket("Color")
                self.outputs["Color"] = Socket("Color")
            elif self.type == "MATH":
                self.inputs.update({index: Socket(str(index)) for index in range(2)})
                self.outputs[0] = Socket("Value")

    def __setitem__(self, key, value):
        self.properties[key] = value


class Nodes(list):
    def new(self, node_type):
        node = Node(node_type)
        self.append(node)
        return node


class Links:
    def new(self, output, input_socket):
        input_socket.links.clear()
        from_node = next(
            node for node in self.tree.nodes if output in node.outputs.values()
        )
        link = Link(output, input_socket, from_node)
        input_socket.links.append(link)
        return link

    def __init__(self, tree):
        self.tree = tree


class Tree:
    def __init__(self):
        self.nodes = Nodes()
        self.links = Links(self)


class World:
    def __init__(self, name):
        self.name = name
        self.use_nodes = False
        self.node_tree = Tree()
        self.properties = {}

    def __setitem__(self, key, value):
        self.properties[key] = value


class Worlds(list):
    def new(self, name):
        used = {world.name for world in self}
        candidate = name
        number = 1
        while candidate in used:
            candidate = f"{name}.{number:03d}"
            number += 1
        world = World(candidate)
        self.append(world)
        return world


class Image:
    def __init__(self, name):
        self.name = name
        self.colorspace_settings = type("ColorSpace", (), {"name": ""})()


class Images:
    def __init__(self):
        self.values = {}

    def load(self, path, check_existing=False):
        if check_existing and path in self.values:
            return self.values[path]
        image = Image(Path(path).name)
        self.values[path] = image
        return image

    def __iter__(self):
        return iter(self.values.values())

    def remove(self, image, **_kwargs):
        for key, value in tuple(self.values.items()):
            if value is image:
                self.values.pop(key)


class Data:
    def __init__(self):
        self.worlds = Worlds()
        self.images = Images()
        self.materials = Materials()
        self.filepath = "/project/test.blend"


class Material:
    def __init__(self, name):
        self.name = name
        self.use_nodes = False
        self.node_tree = Tree()
        self.properties = {}
        self.surface_render_method = ""

    def __setitem__(self, key, value):
        self.properties[key] = value

    def get(self, key, default=None):
        return self.properties.get(key, default)


class Materials(list):
    def new(self, name):
        material = Material(name)
        self.append(material)
        return material

    def remove(self, material):
        super().remove(material)


class Scene:
    world = None


class Bpy:
    def __init__(self):
        self.data = Data()
        self.context = type("Context", (), {"scene": Scene(), "selected_objects": []})()
        self.app = type("App", (), {"version_string": "5.2.0"})()


def payload(library, source, mode="new"):
    return {
        "asset_id": "sky-id",
        "asset_name": "Cloudy Field",
        "resolution": "4K",
        "hdri_path": str(source),
        "library_root": str(library),
        "world_mode": mode,
    }


def standard_world(bpy, name="Artist World"):
    world = bpy.data.worlds.new(name)
    world.use_nodes = True
    background = world.node_tree.nodes.new("ShaderNodeBackground")
    output = world.node_tree.nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    return world, background


def test_create_new_world_preserves_previous_and_marks_asset(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "hdris" / "sky" / "maps" / "sky.exr"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"exr")
    bpy = Bpy()
    previous, _background = standard_world(bpy)
    bpy.context.scene.world = previous
    response = actions.set_hdri_world(bpy, payload(library, source), "session")
    current = bpy.context.scene.world
    assert current is not previous
    assert current.name == "ShotBox_Cloudy_Field"
    assert previous in bpy.data.worlds
    assert current.properties["shotbox_asset_id"] == "sky-id"
    environment = next(node for node in current.node_tree.nodes if node.type == "TEX_ENVIRONMENT")
    assert environment.image.name == "sky.exr"
    assert response["world_name"] == current.name


def test_edit_current_world_preserves_background_and_replaces_environment(tmp_path) -> None:
    library = tmp_path / "library"
    first = library / "first.hdr"
    second = library / "second.exr"
    library.mkdir()
    first.write_bytes(b"hdr")
    second.write_bytes(b"exr")
    bpy = Bpy()
    world, background = standard_world(bpy)
    bpy.context.scene.world = world
    actions.set_hdri_world(bpy, payload(library, first, "edit_current"), "session")
    environment = next(node for node in world.node_tree.nodes if node.type == "TEX_ENVIRONMENT")
    assert background.inputs["Color"].links[0].from_node is environment
    actions.set_hdri_world(bpy, payload(library, second, "edit_current"), "session")
    assert environment.image.name == "second.exr"
    assert len([node for node in world.node_tree.nodes if node.type == "TEX_ENVIRONMENT"]) == 1


def test_edit_rejects_missing_or_custom_world_and_unsafe_paths(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "sky.exr"
    source.write_bytes(b"exr")
    bpy = Bpy()
    with pytest.raises(actions.ActionError, match="no World"):
        actions.set_hdri_world(bpy, payload(library, source, "edit_current"), "session")
    custom = bpy.data.worlds.new("Custom")
    custom.use_nodes = True
    bpy.context.scene.world = custom
    with pytest.raises(actions.ActionError, match="standard Background"):
        actions.set_hdri_world(bpy, payload(library, source, "edit_current"), "session")
    outside = tmp_path / "outside.hdr"
    outside.write_bytes(b"hdr")
    with pytest.raises(actions.ActionError, match="outside"):
        actions.set_hdri_world(bpy, payload(library, outside), "session")


def test_expired_request_is_discarded_before_bpy_action(monkeypatch) -> None:
    bridge = object.__new__(server_module.BridgeServer)
    bridge.bpy = object()
    bridge.session_id = "session"
    bridge.pending = queue.Queue()
    bridge.stopping = threading.Event()
    bridge.cached_session_data = {"blender_version": "5.2.0", "blend_file": ""}
    bridge.last_result = ""
    bridge.last_asset = ""
    bridge.last_resolution = ""
    bridge.last_world = ""
    request = {
        "request_id": "expired",
        "action": "set_hdri_world",
        "payload": {"asset_name": "Sky", "resolution": "4K"},
    }
    pending = server_module.PendingRequest(request, time.monotonic() - 1)
    bridge.pending.put(pending)
    called = []
    monkeypatch.setattr(server_module.actions, "execute", lambda *_args: called.append(True))
    assert bridge.process_pending() == 0.1
    assert pending.ready.is_set()
    assert pending.response["ok"] is False
    assert "expired" in pending.response["diagnostic"]
    assert called == []


def test_texture_material_is_created_unassigned_and_updated_by_asset_id(tmp_path) -> None:
    library = tmp_path / "library"
    base = library / "base.jpg"
    arm = library / "arm.png"
    displacement = library / "displacement.exr"
    library.mkdir()
    base.write_bytes(b"base")
    arm.write_bytes(b"arm")
    displacement.write_bytes(b"displacement")
    request = {
        "asset_id": "stone-id", "asset_name": "Stone Wall", "resolution": "4K",
        "library_root": str(library), "missing_channels": ["Emission"],
        "maps": [
            {"channel": "Base Color", "path": str(base), "color_space": "sRGB", "normal_convention": "", "packed_channels": {}},
            {"channel": "Packed ARM", "path": str(arm), "color_space": "Raw", "normal_convention": "", "packed_channels": {"R": "Ambient Occlusion", "G": "Roughness", "B": "Metalness"}},
            {"channel": "Displacement", "path": str(displacement), "color_space": "Raw", "normal_convention": "", "packed_channels": {}},
        ],
    }
    bpy = Bpy()
    first = actions.create_texture_material(bpy, request, "session")
    assert first["material_name"] == "ShotBox_Stone_Wall"
    assert first["assigned_targets"] == []
    assert "created unassigned" in first["diagnostic"]
    material = bpy.data.materials[0]
    assert material.get("shotbox_asset_id") == "stone-id"
    assert material.displacement_method == "DISPLACEMENT"
    assert any(node.type == "BSDF_PRINCIPLED" for node in material.node_tree.nodes)
    assert any(node.type == "SEPARATE_COLOR" for node in material.node_tree.nodes)
    texture_coordinates = next(
        node for node in material.node_tree.nodes if node.type == "TEX_COORD"
    )
    mapping = next(node for node in material.node_tree.nodes if node.type == "MAPPING")
    images = [node for node in material.node_tree.nodes if node.type == "TEX_IMAGE"]
    assert mapping.inputs["Vector"].links[0].from_node is texture_coordinates
    assert images and all(
        image.inputs["Vector"].links[0].from_node is mapping for image in images
    )
    assert texture_coordinates.location[0] < mapping.location[0] < images[0].location[0]
    assert len({image.location[1] for image in images}) == len(images)
    second = actions.create_texture_material(bpy, request, "session")
    assert second["material_name"] == first["material_name"]
    assert len(bpy.data.materials) == 1


def test_texture_material_rejects_outside_library_path(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"bad")
    request = {
        "asset_id": "id", "asset_name": "Bad", "resolution": "1K", "library_root": str(library),
        "maps": [{"channel": "Base Color", "path": str(outside)}],
    }
    with pytest.raises(actions.ActionError, match="outside"):
        actions.create_texture_material(Bpy(), request, "session")


def test_texture_material_replaces_active_slot_without_adding_or_reassigning_faces(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "base.jpg"
    library.mkdir()
    source.write_bytes(b"base")
    request = {
        "asset_id": "wood-id", "asset_name": "Wood", "resolution": "2K", "library_root": str(library),
        "maps": [{"channel": "Base Color", "path": str(source), "color_space": "sRGB", "packed_channels": {}}],
    }
    bpy = Bpy()
    first = bpy.data.materials.new("Existing One")
    second = bpy.data.materials.new("Existing Two")
    polygons = [type("Polygon", (), {"material_index": 0})(), type("Polygon", (), {"material_index": 1})()]
    mesh = type("Mesh", (), {"materials": [first, second], "polygons": polygons})()
    obj = type("Object", (), {
        "name": "Cube", "type": "MESH", "data": mesh, "active_material_index": 1,
    })()
    bpy.context.selected_objects = [obj]
    response = actions.create_texture_material(bpy, request, "session")
    created = next(material for material in bpy.data.materials if material.name == response["material_name"])
    assert mesh.materials == [first, created]
    assert len(mesh.materials) == 2
    assert [polygon.material_index for polygon in polygons] == [0, 1]
    assert response["assigned_targets"] == ["Cube"]


class ModelObject:
    def __init__(self, name, data=None, object_type="MESH"):
        self.name = name
        self.data = data
        self.type = object_type if data is not None else "EMPTY"
        self.parent = None
        self.matrix_world = (1, 0, 0, 1)
        self.location = (0.0, 0.0, 0.0)
        self.properties = {}

    def __setitem__(self, key, value):
        self.properties[key] = value

    def get(self, key, default=None):
        return self.properties.get(key, default)

    def select_set(self, _value):
        pass


class ModelObjects(list):
    def new(self, name, data):
        used = {item.name for item in self}
        candidate = name
        number = 1
        while candidate in used:
            candidate = f"{name}.{number:03d}"
            number += 1
        item = ModelObject(candidate, data)
        self.append(item)
        return item

    def remove(self, item, **_kwargs):
        super().remove(item)


class ObjectLinks(list):
    def link(self, item):
        if item not in self:
            self.append(item)


class ChildLinks(ObjectLinks):
    pass


class ModelCollection:
    def __init__(self, name):
        self.name = name
        self.objects = ObjectLinks()
        self.children = ChildLinks()
        self.properties = {}

    def __setitem__(self, key, value):
        self.properties[key] = value

    def get(self, key, default=None):
        return self.properties.get(key, default)


class ModelCollections(list):
    def new(self, name):
        used = {item.name for item in self}
        candidate = name
        number = 1
        while candidate in used:
            candidate = f"{name}.{number:03d}"
            number += 1
        item = ModelCollection(candidate)
        self.append(item)
        return item

    def remove(self, item, **_kwargs):
        super().remove(item)


class ModelWmOps:
    def __init__(self, bpy):
        self.bpy = bpy
        self.calls = []

    def usd_import(self, **kwargs):
        self.calls.append(kwargs)
        parent = self.bpy.data.objects.new("Oak", object())
        child = self.bpy.data.objects.new("Leaves", object())
        child.parent = parent
        return {"FINISHED"}


class ModelBpy(Bpy):
    def __init__(self):
        super().__init__()
        self.data.objects = ModelObjects()
        self.data.collections = ModelCollections()
        self.context.scene.collection = ModelCollection("Scene Collection")
        vector = type("Vector", (), {"copy": lambda self: (3.0, 4.0, 5.0)})()
        self.context.scene.cursor = type("Cursor", (), {"location": vector})()
        self.context.view_layer = type("ViewLayer", (), {
            "objects": type("ActiveObjects", (), {"active": None})(),
        })()
        self.ops = type("Ops", (), {})()
        self.ops.wm = ModelWmOps(self)


def _model_request(library, source):
    return {
        "asset_id": "oak-id",
        "asset_name": "Oak Tree",
        "asset_slug": "oak-tree",
        "variant": "4K · LOD0 · USDC",
        "format": "USDC",
        "resolution": "4K",
        "lod": "LOD0",
        "model_path": str(source),
        "library_root": str(library),
    }


def test_import_usd_model_creates_cursor_root_and_new_instance_each_send(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "models" / "oak.usdc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"usd")
    bpy = ModelBpy()

    first = actions.import_usd_model(bpy, _model_request(library, source), "session")
    root = next(item for item in bpy.data.objects if item.name == first["root_object"])
    imported = [item for item in bpy.data.objects if item.name in first["imported_targets"]]
    assert root.location == (3.0, 4.0, 5.0)
    assert imported[0].parent is root
    assert imported[1].parent is imported[0]
    assert root.get("shotbox_asset_id") == "oak-id"
    assert bpy.ops.wm.calls[0]["import_cameras"] is False
    assert bpy.ops.wm.calls[0]["import_lights"] is False

    second = actions.import_usd_model(bpy, _model_request(library, source), "session")
    assert second["collection_name"] != first["collection_name"]
    assert second["root_object"] != first["root_object"]


def test_import_usd_model_rejects_outside_library(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = tmp_path / "outside.usdc"
    source.write_bytes(b"usd")
    with pytest.raises(actions.ActionError, match="outside"):
        actions.import_usd_model(ModelBpy(), _model_request(library, source), "session")
