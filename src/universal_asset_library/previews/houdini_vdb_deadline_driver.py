from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback


FILE_NODE_PATH = "/obj/VDB/file1"
PYRO_SHADER_PATH = "/stage/materiallibrary1/karmacloudmaterial1/kma_pyroshader1"
RENDER_SETTINGS_PATH = "/stage/karmarendersettings"
CAMERA_NODE_PATH = "/stage/camera1"
USD_ROP_PATH = "/stage/usd_rop1"
RENDER_SETTINGS_PRIM_PATH = "/Render/karmarendersettings"

CAMERA_SHUTTER_PARMS = (
    "xn__shutteropen_0ta",
    "xn__shutterclose_nva",
)


def _debug(message: str) -> None:
    print(f"SHOTBOX_DEBUG:{message}", flush=True)


def _required_node(hou, path: str):
    node = hou.node(path)
    if node is None:
        raise RuntimeError(f"The template is missing {path}.")
    return node


def _required_parm(node, name: str):
    parm = node.parm(name)
    if parm is None:
        raise RuntimeError(f"{node.path()} has no {name} parameter.")
    return parm


def _validate_flattened_usd(usd_path: Path, template_path: Path) -> None:
    from pxr import Usd, UsdUtils

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError("The exported USD could not be opened for validation.")
    root_layer = stage.GetRootLayer()
    if root_layer.subLayerPaths:
        raise RuntimeError(
            "The exported USD still contains sublayers instead of a flattened stage."
        )
    settings = stage.GetPrimAtPath(RENDER_SETTINGS_PRIM_PATH)
    if (
        not settings.IsValid()
        or not settings.IsDefined()
        or settings.GetTypeName() != "RenderSettings"
    ):
        raise RuntimeError(
            f"The exported USD does not define {RENDER_SETTINGS_PRIM_PATH}."
        )
    layers, asset_paths, unresolved = UsdUtils.ComputeAllDependencies(
        str(usd_path)
    )
    if unresolved:
        raise RuntimeError(
            "The exported USD has unresolved dependencies: "
            + ", ".join(str(path) for path in unresolved)
        )
    template_dir = template_path.parent.resolve()
    dependencies = [
        Path(layer.realPath)
        for layer in layers
        if getattr(layer, "realPath", "")
    ]
    dependencies.extend(
        Path(str(path)) for path in asset_paths if Path(str(path)).is_absolute()
    )
    local_dependencies = [
        path
        for path in dependencies
        if path != usd_path and path.is_relative_to(template_dir)
    ]
    if local_dependencies:
        raise RuntimeError(
            "The exported USD still depends on template-local files: "
            + ", ".join(str(path) for path in local_dependencies)
        )


def export_batch(hou, request: dict) -> dict:
    template = Path(str(request["template_path"]))
    frame_start = int(request.get("frame_start", 1))
    frame_end = int(request.get("frame_end", 36))
    items = tuple(request.get("items", []))
    _debug(
        f"Loading VDB template {template} for {len(items)} asset(s), "
        f"frames {frame_start}-{frame_end}"
    )
    hou.hipFile.load(str(template), suppress_save_prompt=True)
    file_node = _required_node(hou, FILE_NODE_PATH)
    density_parm = _required_parm(
        _required_node(hou, PYRO_SHADER_PATH), "densityscale"
    )
    picture_parm = _required_parm(
        _required_node(hou, RENDER_SETTINGS_PATH), "picture"
    )
    camera_node = _required_node(hou, CAMERA_NODE_PATH)
    for parm_name in CAMERA_SHUTTER_PARMS:
        _required_parm(camera_node, parm_name).set(0.0)
    for parm_name in ("sample_shutterrange1", "sample_shutterrange2"):
        parm = camera_node.parm(parm_name)
        if parm is not None:
            parm.set(0.0)
    _debug("Camera shutter authored at 0.0/0.0; motion blur disabled")
    usd_rop = _required_node(hou, USD_ROP_PATH)
    output_parm = _required_parm(usd_rop, "lopoutput")
    _required_parm(usd_rop, "savestyle").set("flattenstage")
    _required_parm(usd_rop, "flattenfilelayers").set(1)
    _required_parm(usd_rop, "flattensoplayers").set(1)
    _debug(
        "Export USD ROP configured for a self-contained flattened stage "
        "with relative SOP volume sidecars"
    )
    results = []
    for item_index, item in enumerate(items, 1):
        result = {"asset_id": str(item.get("asset_id", "")), "ok": False}
        try:
            density = int(item.get("density_scale", 100))
            if not 10 <= density <= 500:
                raise RuntimeError("VDB preview density must be between 10 and 500.")
            usd_path = Path(str(item["usd_path"]))
            exr_pattern = Path(str(item["exr_pattern"]))
            usd_path.parent.mkdir(parents=True, exist_ok=True)
            exr_pattern.parent.mkdir(parents=True, exist_ok=True)
            asset_name = item.get("asset_name", "VDB")
            _debug(
                f"[{item_index}/{len(items)}] Configuring {asset_name}: "
                f"density={density}, VDB={item['vdb_path']}"
            )
            _debug(f"[{item_index}/{len(items)}] USD output: {usd_path}")
            _debug(f"[{item_index}/{len(items)}] EXR output: {exr_pattern}")
            _required_parm(file_node, "file").set(str(item["vdb_path"]))
            density_parm.set(density)
            picture_parm.set(str(exr_pattern))
            output_parm.set(str(usd_path))
            for name, value in (
                ("trange", 1),
                ("f1", frame_start),
                ("f2", frame_end),
                ("f3", 1),
                ("fileperframe", 0),
                ("savetimeinfo", 1),
            ):
                parm = usd_rop.parm(name)
                if parm is not None:
                    parm.set(value)
            hou.setFrame(frame_start)
            file_node.cook(force=True)
            errors = tuple(str(value) for value in file_node.errors() if str(value).strip())
            if errors:
                raise RuntimeError("The VDB File SOP could not cook: " + " ".join(errors))
            print(
                f"SHOTBOX_PROGRESS:[{item_index}/{len(items)}] "
                f"Exporting {asset_name} USD",
                flush=True,
            )
            usd_rop.render(
                frame_range=(frame_start, frame_end, 1),
                verbose=True,
                output_progress=True,
            )
            if not usd_path.is_file() or usd_path.stat().st_size <= 0:
                raise RuntimeError("The Export USD ROP did not create a readable USD file.")
            _validate_flattened_usd(usd_path, template)
            _debug(
                f"[{item_index}/{len(items)}] Cross-platform USD validation passed: "
                f"{RENDER_SETTINGS_PRIM_PATH} is defined"
            )
            _debug(
                f"[{item_index}/{len(items)}] Export complete: {usd_path} "
                f"({usd_path.stat().st_size} bytes)"
            )
            result.update({
                "ok": True,
                "usd_path": str(usd_path),
                "exr_pattern": str(exr_pattern),
            })
        except Exception as error:
            _debug(
                f"[{item_index}/{len(items)}] Export failed for "
                f"{item.get('asset_name', 'VDB')}: {error}"
            )
            result.update({
                "diagnostic": str(error),
                "traceback": traceback.format_exc(limit=8),
            })
        results.append(result)
    _debug(
        f"Houdini USD export finished: "
        f"{sum(bool(item.get('ok')) for item in results)}/{len(results)} succeeded"
    )
    return {
        "ok": any(item.get("ok") for item in results),
        "houdini_version": hou.applicationVersionString(),
        "fps": float(hou.fps()),
        "items": results,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print(
            "Usage: houdini_vdb_deadline_driver.py REQUEST.json RESULT.json",
            file=sys.stderr,
        )
        return 2
    request_path, result_path = map(Path, arguments)
    try:
        import hou

        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = export_batch(hou, request)
    except Exception as error:
        result = {
            "ok": False,
            "diagnostic": str(error),
            "traceback": traceback.format_exc(limit=8),
            "items": [],
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(result_path)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
