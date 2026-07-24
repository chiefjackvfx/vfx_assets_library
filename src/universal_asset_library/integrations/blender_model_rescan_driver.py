"""Blender-side validator for manually added managed USD files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--usd", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def _inside_asset(path, asset):
    resolved = Path(str(path).split("[", 1)[0]).resolve()
    try:
        relative = resolved.relative_to(asset)
    except ValueError as error:
        raise RuntimeError(f"USD dependency escapes the managed asset: {path}") from error
    return resolved, relative.as_posix()


def validate(entry, asset):
    from pxr import Sdf, Usd, UsdGeom, UsdShade

    entry = entry.resolve(strict=True)
    asset = asset.resolve(strict=True)
    entry.relative_to(asset)
    stage = Usd.Stage.Open(str(entry))
    if stage is None:
        raise RuntimeError("The USD stage could not be opened.")
    dependencies = set()
    for layer in stage.GetUsedLayers():
        real_path = str(getattr(layer, "realPath", "") or "")
        if not real_path or real_path.startswith("anon:"):
            continue
        resolved, relative = _inside_asset(real_path, asset)
        if resolved != entry:
            dependencies.add(relative)
    meshes = 0
    materials = set()
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            meshes += 1
        material, _relationship = UsdShade.MaterialBindingAPI(
            prim
        ).ComputeBoundMaterial()
        if material:
            materials.add(str(material.GetPath()))
        for attribute in prim.GetAttributes():
            value = attribute.Get()
            paths = []
            if isinstance(value, Sdf.AssetPath):
                paths = [value.path]
            elif isinstance(value, (list, tuple)):
                paths = [
                    item.path for item in value if isinstance(item, Sdf.AssetPath)
                ]
            for value_path in paths:
                if not value_path or value_path.startswith("anon:"):
                    continue
                resolved, relative = _inside_asset(
                    entry.parent / value_path, asset
                )
                if not resolved.is_file():
                    raise RuntimeError(
                        f"USD dependency is missing: {value_path}"
                    )
                if resolved != entry:
                    dependencies.add(relative)
    if meshes < 1:
        raise RuntimeError("The USD stage contains no mesh geometry.")
    diagnostics = []
    if not materials:
        diagnostics.append("The USD contains no material bindings.")
    return {
        "ok": True,
        "mesh_count": meshes,
        "material_count": len(materials),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)).upper(),
        "dependencies": sorted(dependencies),
        "diagnostics": diagnostics,
    }


def main():
    arguments = _arguments()
    result_path = Path(arguments.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import bpy

        result = validate(Path(arguments.usd), Path(arguments.asset))
        result["blender_version"] = str(bpy.app.version_string)
    except Exception as error:
        result = {
            "ok": False,
            "diagnostic": str(error),
            "traceback": traceback.format_exc(limit=8),
        }
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
