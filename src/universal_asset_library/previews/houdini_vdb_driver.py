from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback


FILE_NODE_PATH = "/obj/VDB/file1"
RENDER_NODE_PATH = "/stage/usdrender_rop1"
PYRO_SHADER_PATH = "/stage/materiallibrary1/karmacloudmaterial1/kma_pyroshader1"


def _progress(message: str) -> None:
    print(f"SHOTBOX_PROGRESS:{message}", flush=True)


def render(hou, request: dict) -> dict:
    template = Path(str(request["template_path"]))
    output = Path(str(request["output_exr"]))
    vdb_path = str(request["vdb_path"])
    frame = int(request.get("frame", 1))
    mode = str(request.get("mode", "still"))
    if mode not in {"still", "turntable"}:
        raise RuntimeError(f"Unknown VDB preview mode: {mode}")
    frame_start = int(request.get("frame_start", frame))
    frame_end = int(request.get("frame_end", frame))
    density_scale = int(request.get("density_scale", 100))
    if not 10 <= density_scale <= 500:
        raise RuntimeError("VDB preview density must be between 10 and 500.")
    _progress("Loading Houdini VDB preview template")
    hou.hipFile.load(str(template), suppress_save_prompt=True)
    file_node = hou.node(FILE_NODE_PATH)
    if file_node is None:
        raise RuntimeError(f"The template is missing {FILE_NODE_PATH}.")
    file_parm = file_node.parm("file")
    if file_parm is None:
        raise RuntimeError(f"{FILE_NODE_PATH} has no file parameter.")
    render_node = hou.node(RENDER_NODE_PATH)
    if render_node is None:
        raise RuntimeError(f"The template is missing {RENDER_NODE_PATH}.")
    output_parm = render_node.parm("outputimage")
    if output_parm is None:
        raise RuntimeError(f"{RENDER_NODE_PATH} has no outputimage parameter.")
    pyro_shader = hou.node(PYRO_SHADER_PATH)
    if pyro_shader is None:
        raise RuntimeError(f"The template is missing {PYRO_SHADER_PATH}.")
    density_parm = pyro_shader.parm("densityscale")
    if density_parm is None:
        raise RuntimeError(f"{PYRO_SHADER_PATH} has no densityscale parameter.")

    file_parm.set(vdb_path)
    _progress(f"Setting Karma Pyro density to {density_scale}")
    density_parm.set(density_scale)
    hou.setFrame(frame)
    _progress(f"Cooking VDB at frame {frame}")
    file_node.cook(force=True)
    errors = tuple(str(value) for value in file_node.errors() if str(value).strip())
    if errors:
        raise RuntimeError("The VDB File SOP could not cook: " + " ".join(errors))

    output.parent.mkdir(parents=True, exist_ok=True)
    output_parm.set(str(output))
    for name, value in (
        ("trange", 1 if mode == "turntable" else 0),
        ("f1", frame_start),
        ("f2", frame_end),
        ("f3", 1),
    ):
        parm = render_node.parm(name)
        if parm is not None:
            parm.set(value)
    if mode == "turntable":
        _progress(f"Rendering Karma turntable frames {frame_start}–{frame_end}")
        render_node.render(
            frame_range=(frame_start, frame_end, 1),
            verbose=True,
            output_progress=True,
        )
        missing = [
            value
            for value in range(frame_start, frame_end + 1)
            if not Path(str(output).replace("$F4", f"{value:04d}")).is_file()
        ]
        if missing:
            raise RuntimeError(
                "Houdini did not create every turntable EXR; missing frame(s): "
                + ", ".join(str(value) for value in missing[:12])
            )
    else:
        _progress(f"Rendering Karma still at frame {frame}")
        render_node.render(
            frame_range=(frame, frame),
            verbose=True,
            output_progress=True,
        )
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("Houdini completed without creating the requested EXR.")
    return {
        "ok": True,
        "houdini_version": hou.applicationVersionString(),
        "output_exr": str(output),
        "frame": frame,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "fps": float(hou.fps()),
        "mode": mode,
        "density_scale": density_scale,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print("Usage: houdini_vdb_driver.py REQUEST.json RESULT.json", file=sys.stderr)
        return 2
    request_path, result_path = map(Path, arguments)
    result: dict
    try:
        import hou

        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = render(hou, request)
    except Exception as error:
        result = {
            "ok": False,
            "diagnostic": str(error),
            "traceback": traceback.format_exc(limit=8),
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(result_path)
    if not result.get("ok"):
        print(result.get("diagnostic", "Houdini VDB preview failed."), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
