"""Persistent preview protocol server executed inside Blender."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


PREFIX = "UAL_PREVIEW_EVENT "
GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def _emit(value: dict) -> None:
    print(PREFIX + json.dumps(value, separators=(",", ":")), flush=True)


def _load_module(name: str, filename: str):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load preview driver: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _discover_gpu() -> tuple[str, list[str]]:
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
        return backend, [device.name for device in devices]
    raise RuntimeError(
        "No compatible Cycles GPU device was found. "
        "Enable an OptiX, CUDA, HIP, oneAPI, or Metal device in Blender."
    )


def _open_template(path: str) -> None:
    template = Path(path).resolve()
    if not template.is_file():
        raise RuntimeError(f"Preview template is missing: {template}")
    bpy.ops.wm.open_mainfile(filepath=str(template), load_ui=False)


def main() -> None:
    drivers = {
        "texture": _load_module(
            "shotbox_persistent_texture_driver", "blender_texture_driver.py"
        ),
        "hdri": _load_module(
            "shotbox_persistent_hdri_driver", "blender_hdri_driver.py"
        ),
    }
    current_template = ""
    gpu_metadata: dict = {}
    _emit({"event": "ready", "blender_version": bpy.app.version_string})
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(command, dict):
            continue
        if command.get("command") == "shutdown":
            return
        if command.get("command") != "render":
            continue
        job_id = str(command.get("job_id", ""))
        kind = str(command.get("kind", ""))
        template = str(command.get("template", ""))
        payload = command.get("payload", {})
        if kind not in drivers or not isinstance(payload, dict):
            _emit({
                "event": "result",
                "job_id": job_id,
                "status": "failed",
                "diagnostic": f"Unsupported preview job type: {kind}",
            })
            continue
        baseline_images: set[int] = set()
        try:
            if current_template != template:
                _emit({
                    "event": "progress",
                    "job_id": job_id,
                    "message": f"Loading {kind.upper()} preview template",
                })
                _open_template(template)
                current_template = template
            scene = bpy.context.scene
            if scene.render.engine != "CYCLES":
                raise RuntimeError("The preview template must use the Cycles render engine.")
            if not gpu_metadata:
                backend, devices = _discover_gpu()
                gpu_metadata = {
                    "compute_device_type": backend,
                    "render_device": "GPU",
                    "gpu_devices": devices,
                }
            scene.cycles.device = "GPU"
            baseline_images = {image.as_pointer() for image in bpy.data.images}

            def progress(message: str) -> None:
                _emit({
                    "event": "progress",
                    "job_id": job_id,
                    "message": message,
                })

            metadata = drivers[kind].render_job(
                payload, gpu_metadata, progress=progress
            )
            metadata = {
                "blender_version": bpy.app.version_string,
                **gpu_metadata,
                **metadata,
            }
            _emit({
                "event": "result",
                "job_id": job_id,
                "status": "ready",
                "metadata": metadata,
            })
        except Exception as error:
            traceback.print_exc()
            current_template = ""
            _emit({
                "event": "result",
                "job_id": job_id,
                "status": "failed",
                "diagnostic": f"{type(error).__name__}: {error}",
            })
        finally:
            if baseline_images:
                for image in list(bpy.data.images):
                    if image.as_pointer() not in baseline_images:
                        try:
                            bpy.data.images.remove(image, do_unlink=True)
                        except (ReferenceError, RuntimeError):
                            pass


if __name__ == "__main__":
    main()
