from __future__ import annotations

import importlib.util
from contextlib import nullcontext
from pathlib import Path

import pytest


ACTIONS_PATH = (
    Path(__file__).parents[1]
    / "src/universal_asset_library/integrations/houdini/plugin/scripts/python/ual_houdini_bridge/actions.py"
)
SPEC = importlib.util.spec_from_file_location("ual_houdini_actions_test", ACTIONS_PATH)
actions = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(actions)


class FakeParm:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value

    @staticmethod
    def menuItems():
        return ("error", "nogeometry")


class FakeCategory:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeType:
    def __init__(self, name, category):
        self._name = name
        self._category = FakeCategory(category)

    def name(self):
        return self._name

    def category(self):
        return self._category


class FakeNode:
    def __init__(self, hou, name, node_type, parent=None, category="Manager"):
        self.hou = hou
        self._name = name
        self._type = FakeType(node_type, category)
        self._parent = parent
        self.children = []
        self.input = None
        self.user_data = {}
        self.parms = {}
        if node_type.startswith("domelight"):
            self.parms = {
                actions.TEXTURE_CONTROL: FakeParm(),
                actions.TEXTURE_VALUE: FakeParm(),
                "primpath": FakeParm(),
            }
        elif node_type == "materiallibrary":
            self.parms = {name: FakeParm() for name in ("matnet", "matpathprefix", "nummaterials", "matnode1", "matpath1")}
        elif node_type == "mtlximage":
            self.parms = {"file": FakeParm(), "filecolorspace": FakeParm()}
            self.input_names = {"texcoord": 3}
        elif node_type == "mtlxplace2d":
            self.input_names = {"texcoord": 0}
        elif node_type == "mtlxstandard_surface":
            self.parms = {"emission": FakeParm()}
            self.input_names = {
                name: index for index, name in enumerate((
                    "base_color", "specular_roughness", "metalness", "specular", "opacity",
                    "emission_color", "subsurface", "normal",
                ))
            }
        elif node_type.startswith("reference"):
            self.parms = {
                name: FakeParm()
                for name in ("primpath", "numreferences", "filepath1", "makeinstanceable1")
            }
        elif node_type.startswith("usdimport"):
            self.parms = {"filepath": FakeParm(), "unpack": FakeParm()}
        elif node_type == "file":
            self.parms = {"file": FakeParm(), "missingframe": FakeParm()}
        elif node_type == "material":
            self.parms = {
                name: FakeParm()
                for name in (
                    "num_materials", "group1", "group2",
                    "shop_materialpath1", "shop_materialpath2",
                )
            }

    def name(self):
        return self._name

    def path(self):
        if self._parent is None:
            return "/"
        return self._parent.path().rstrip("/") + "/" + self._name

    def parent(self):
        return self._parent

    def type(self):
        return self._type

    def parm(self, name):
        return self.parms.get(name)

    def userData(self, name):
        return self.user_data.get(name)

    def setUserData(self, name, value):
        self.user_data[name] = value

    def createNode(self, node_type, name):
        used = {child.name() for child in self.children}
        unique = name
        number = 2
        while unique in used:
            unique = f"{name}{number}"
            number += 1
        if node_type.startswith(("domelight", "reference")):
            category = "Lop"
        elif node_type.startswith("usdimport") or node_type in {"material", "file"}:
            category = "Sop"
        elif node_type == "geo":
            category = "Object"
        else:
            category = "Manager"
        node = FakeNode(self.hou, unique, node_type, self, category)
        self.children.append(node)
        self.hou.nodes[node.path()] = node
        return node

    def setInput(self, index, node, output_index=0):
        self.input = node
        if not hasattr(self, "inputs"):
            self.inputs = {}
        self.inputs[index] = (node, output_index)

    def inputIndex(self, name):
        return getattr(self, "input_names", {}).get(name, -1)

    def setMaterialFlag(self, _value):
        pass

    def setComment(self, value):
        self.comment = value

    def layoutChildren(self, horizontal_spacing=-1, vertical_spacing=-1):
        self.layout_spacing = (horizontal_spacing, vertical_spacing)

    def setDisplayFlag(self, _value):
        pass

    def setRenderFlag(self, _value):
        pass

    def moveToGoodPosition(self):
        pass

    def setSelected(self, value, clear_all_selected=False):
        if clear_all_selected:
            self.hou.selected = []
        if value and self not in self.hou.selected:
            self.hou.selected.append(self)

    def setCurrent(self, *_args, **_kwargs):
        pass

    def destroy(self):
        self._parent.children.remove(self)
        self.hou.nodes.pop(self.path(), None)


class FakeHipFile:
    @staticmethod
    def path():
        return "/project/test.hip"


class FakeUndos:
    @staticmethod
    def group(_label):
        return nullcontext()


class FakeHou:
    def __init__(self):
        self.nodes = {}
        self.selected = []
        self.undos = FakeUndos()
        self.hipFile = FakeHipFile()
        root = FakeNode(self, "", "root")
        self.nodes["/"] = root

    def node(self, path):
        return self.nodes.get(path)

    def selectedNodes(self):
        return tuple(self.selected)

    @staticmethod
    def applicationVersionString():
        return "22.0.368"


def _payload(library: Path, source: Path, asset_id="sky-one"):
    return {
        "asset_id": asset_id,
        "asset_name": "Cloudy Field",
        "resolution": "4K",
        "hdri_path": str(source),
        "library_root": str(library),
    }


def test_creates_stage_and_dome_then_updates_selected_node(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "hdris" / "field" / "maps" / "field.exr"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"exr")
    hou = FakeHou()
    first = actions.create_hdri_dome(hou, _payload(library, source), "session")
    stage = hou.node("/stage")
    assert stage is not None
    dome = hou.node(first["node_path"])
    assert dome.parm(actions.TEXTURE_CONTROL).value == "set"
    assert dome.parm(actions.TEXTURE_VALUE).value == source.as_posix()
    assert dome.parm("primpath").value == "/lights/shotbox_cloudy_field"
    assert dome.userData("shotbox_asset_id") == "sky-one"
    assert hou.selected == [dome]

    # Nodes made by the former product name remain updateable after rebranding.
    dome.user_data.pop(actions.OWNER_KEY)
    dome.setUserData(actions.LEGACY_OWNER_KEY, actions.LEGACY_OWNER_VALUE)
    second_source = source.with_name("field_8k.hdr")
    second_source.write_bytes(b"hdr")
    second = actions.create_hdri_dome(hou, _payload(library, second_source, "sky-two"), "session")
    assert second["node_path"] == first["node_path"]
    assert len(stage.children) == 1
    assert dome.parm(actions.TEXTURE_VALUE).value == second_source.as_posix()
    assert dome.userData("shotbox_asset_id") == "sky-two"


def test_selected_lop_is_used_as_input(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "sky.hdr"
    library.mkdir()
    source.write_bytes(b"hdr")
    hou = FakeHou()
    stage = hou.node("/").createNode("lopnet", "stage")
    upstream = FakeNode(hou, "upstream", "sublayer", stage, "Lop")
    stage.children.append(upstream)
    hou.nodes[upstream.path()] = upstream
    hou.selected = [upstream]
    response = actions.create_hdri_dome(hou, _payload(library, source), "session")
    assert hou.node(response["node_path"]).input is upstream


def test_rejects_paths_outside_library_and_wrong_formats(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside.exr"
    outside.write_bytes(b"exr")
    with pytest.raises(actions.ActionError, match="outside"):
        actions.create_hdri_dome(FakeHou(), _payload(library, outside), "session")
    wrong = library / "sky.jpg"
    wrong.write_bytes(b"jpg")
    with pytest.raises(actions.ActionError, match="Only managed HDR"):
        actions.create_hdri_dome(FakeHou(), _payload(library, wrong), "session")


def test_vdb_creates_new_file_sop_with_sequence_and_selected_network(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "vdbs" / "smoke" / "volumes" / "high" / "smoke_high_1001.vdb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"vdb")
    hou = FakeHou()
    obj = hou.node("/").createNode("objnet", "obj")
    geo = obj.createNode("geo", "selected_geo")
    upstream = geo.createNode("file", "upstream")
    hou.selected = [upstream]
    payload = {
        "asset_id": "smoke-one",
        "asset_name": "Hero Smoke",
        "variant": "High",
        "vdb_path": str(source).replace("1001", "$F4"),
        "library_root": str(library),
        "is_sequence": True,
        "frame_start": 1001,
        "frame_end": 1003,
        "padding": 4,
        "missing_frames": [1002],
    }

    first = actions.import_vdb(hou, payload, "session")
    second = actions.import_vdb(hou, payload, "session")

    first_node = hou.node(first["node_path"])
    second_node = hou.node(second["node_path"])
    assert first_node.parent() is geo
    assert second_node.parent() is geo
    assert first_node is not second_node
    assert first_node.parm("file").value.endswith("smoke_high_$F4.vdb")
    assert first_node.parm("missingframe").value == "nogeometry"
    assert first_node.userData("shotbox_role") == "vdb_file_sop"


def test_vdb_falls_back_to_new_geometry_object(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "cloud.vdb"
    library.mkdir()
    source.write_bytes(b"vdb")
    hou = FakeHou()
    response = actions.import_vdb(hou, {
        "asset_id": "cloud-one", "asset_name": "Cloud One", "variant": "Mid",
        "vdb_path": str(source), "library_root": str(library), "is_sequence": False,
    }, "session")
    node = hou.node(response["node_path"])
    assert node.parent().path().startswith("/obj/shotbox_cloud_one")
    assert node.parm("file").value == source.as_posix()


def test_creates_unassigned_solaris_materialx_and_updates_owned_library(tmp_path) -> None:
    library = tmp_path / "library"
    base = library / "base.jpg"
    roughness = library / "rough.exr"
    library.mkdir()
    base.write_bytes(b"base")
    roughness.write_bytes(b"rough")
    request = {
        "asset_id": "stone-id", "asset_name": "Stone Wall", "resolution": "4K",
        "library_root": str(library), "missing_channels": ["Normal"],
        "maps": [
            {"channel": "Base Color", "path": str(base), "color_space": "sRGB", "packed_channels": {}},
            {"channel": "Roughness", "path": str(roughness), "color_space": "Raw", "packed_channels": {}},
        ],
    }
    hou = FakeHou()
    first = actions.create_texture_material(hou, request, "session")
    material_library = hou.node(first["node_path"])
    assert material_library.type().name() == "materiallibrary"
    assert material_library.userData("shotbox_asset_id") == "stone-id"
    assert first["material_path"] == "/materials/stone_wall"
    assert first["assigned_targets"] == []
    assert "created unassigned" in first["diagnostic"]
    builder = next(node for node in material_library.children if node.name() == "shotbox_stone_wall_mtlx")
    child_names = {node.name() for node in builder.children}
    assert {
        "mtlxstandard_surface", "mtlxdisplacement", "surface_output", "displacement_output",
        "uv_coordinates", "uv_control",
    }.issubset(child_names)
    assert "surface_output" in child_names and "mtlxsurfacematerial" not in child_names
    assert builder.layout_spacing == (2.0, 1.25)
    uv_control = next(node for node in builder.children if node.name() == "uv_control")
    uv_coordinates = next(node for node in builder.children if node.name() == "uv_coordinates")
    assert uv_control.inputs[0][0] is uv_coordinates
    images = [node for node in builder.children if node.type().name() == "mtlximage"]
    assert images and all(image.inputs[3][0] is uv_control for image in images)
    stage = hou.node("/stage")
    second = actions.create_texture_material(hou, request, "session")
    assert second["node_path"] == first["node_path"]
    assert len([node for node in stage.children if node.type().name() == "materiallibrary"]) == 1


def _model_payload(library: Path, source: Path, target: str = "lop"):
    return {
        "asset_id": "tree-id",
        "asset_name": "Oak Tree",
        "asset_slug": "oak-tree",
        "variant": "4K · LOD0 · USDC",
        "format": "USDC",
        "resolution": "4K",
        "lod": "LOD0",
        "model_path": str(source),
        "library_root": str(library),
        "target": target,
    }


def test_import_usd_model_creates_new_lop_reference_each_send(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "models" / "oak" / "models" / "oak.usdc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"usd")
    hou = FakeHou()
    stage = hou.node("/").createNode("lopnet", "stage")
    upstream = FakeNode(hou, "upstream", "sublayer", stage, "Lop")
    stage.children.append(upstream)
    hou.nodes[upstream.path()] = upstream
    hou.selected = [upstream]

    first = actions.import_usd_model(hou, _model_payload(library, source), "session")
    first_node = hou.node(first["node_path"])
    assert first_node.input is upstream
    assert first_node.parm("filepath1").value == source.as_posix()
    assert first["prim_path"].startswith("/assets/shotbox_oak_tree")
    assert first_node.userData("shotbox_asset_id") == "tree-id"

    hou.selected = [upstream]
    second = actions.import_usd_model(hou, _model_payload(library, source), "session")
    assert second["node_path"] != first["node_path"]
    assert second["prim_path"] != first["prim_path"]


def test_import_usd_model_creates_packed_sop_with_obj_fallback(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "models" / "oak" / "models" / "oak.usdc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"usd")
    hou = FakeHou()

    result = actions.import_usd_model(hou, _model_payload(library, source, "sop"), "session")
    node = hou.node(result["node_path"])
    assert result["network_path"].startswith("/obj/shotbox_oak_tree")
    assert node.parm("filepath").value == source.as_posix()
    assert node.parm("unpack").value == 0
    assert node.userData("shotbox_target") == "sop"


def test_sop_model_import_builds_and_assigns_managed_materialx(tmp_path) -> None:
    library = tmp_path / "library"
    source = library / "models" / "oak" / "models" / "oak.usdc"
    base = library / "models" / "oak" / "maps" / "Oak_4K_BaseColor.jpg"
    normal = library / "models" / "oak" / "maps" / "Oak_4K_Normal.jpg"
    bump = library / "models" / "oak" / "maps" / "Oak_4K_Bump.jpg"
    source.parent.mkdir(parents=True)
    base.parent.mkdir(parents=True)
    source.write_bytes(b"usd")
    base.write_bytes(b"base")
    normal.write_bytes(b"normal")
    bump.write_bytes(b"bump")
    request = _model_payload(library, source, "sop")
    request["texture_sets"] = [{
        "name": "Default",
        "resolution": "4K",
        "maps": [
            {"channel": "Base Color", "path": str(base), "color_space": "sRGB"},
            {"channel": "Normal", "path": str(normal), "color_space": "Raw"},
            {"channel": "Bump", "path": str(bump), "color_space": "Raw"},
        ],
    }]

    hou = FakeHou()
    result = actions.import_usd_model(hou, request, "session")

    assignment = hou.node(result["node_path"])
    imported = hou.node(result["import_node_path"])
    builder = hou.node(result["material_paths"][0])
    assert assignment.type().name() == "material"
    assert assignment.input is imported
    assert assignment.parm("shop_materialpath1").value == builder.path()
    assert assignment.parm("group1").value == ""
    assert builder.userData("shotbox_role") == "sop_material_builder"
    child_names = {child.name() for child in builder.children}
    assert "normal_map" in child_names
    assert "bump" not in child_names
    assert "MaterialX shader" in result["diagnostic"]


def test_import_usd_model_rejects_outside_library(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = tmp_path / "outside.usdc"
    source.write_bytes(b"usd")
    with pytest.raises(actions.ActionError, match="outside"):
        actions.import_usd_model(FakeHou(), _model_payload(library, source), "session")
