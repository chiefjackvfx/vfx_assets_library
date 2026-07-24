import json
import os
from pathlib import Path
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from universal_asset_library.previews.hdri_renderer import (
    COMPOSITE_SIZE,
    EXPECTED_SCENE_SIZE,
    PANORAMA_SIZE,
    HdriPreviewRequest,
    compose_hdri_preview,
    render_hdri_preview,
    select_hdri_file,
    select_hdri_variant,
)
from universal_asset_library.previews.hdri_renderer import driver_path


def _image(path: Path, size: tuple[int, int], color: str) -> None:
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")


def test_selects_highest_resolution_up_to_4k_and_preferred_exr() -> None:
    resolutions = {
        "1K": {"width": 1024, "files": []},
        "4K": {"width": 4096, "files": [
            {"path": "maps/a.hdr", "format": "HDR", "preferred": True},
            {"path": "maps/a.exr", "format": "EXR", "preferred": True},
        ]},
        "8K": {"width": 8192, "files": []},
    }
    label, variant = select_hdri_variant(resolutions)
    assert label == "4K"
    assert select_hdri_file(variant)["path"] == "maps/a.exr"
    assert select_hdri_variant({"8K": resolutions["8K"]})[0] == "8K"


def test_composite_dimensions_order_and_jpeg_validity(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    scene = tmp_path / "scene.png"
    panorama = tmp_path / "panorama.png"
    hero = tmp_path / "hero.jpg"
    _image(scene, EXPECTED_SCENE_SIZE, "#cc2222")
    _image(panorama, PANORAMA_SIZE, "#2266cc")
    compose_hdri_preview(scene, panorama, hero, ("1K", "4K", "8K"))
    full = QImage(str(hero))
    assert (full.width(), full.height()) == COMPOSITE_SIZE
    assert full.pixelColor(100, 100).blue() > full.pixelColor(100, 100).red()
    assert full.pixelColor(950, 50).blue() > full.pixelColor(950, 50).red()
    assert full.pixelColor(100, 650).red() > full.pixelColor(100, 650).blue()
    assert hero.read_bytes().startswith(b"\xff\xd8\xff")


def test_blender_invocation_is_shell_free_and_outputs_are_validated(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    template = tmp_path / "template.blend"
    hdri = tmp_path / "environment.exr"
    executable = tmp_path / "Blender With Spaces"
    template.write_bytes(b"template")
    hdri.write_bytes(b"hdri")
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    captured = {}

    class FakeProcess:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            args = command[command.index("--") + 1:]
            values = dict(zip(args[::2], args[1::2]))
            _image(Path(values["--scene-output"]), EXPECTED_SCENE_SIZE, "#222222")
            _image(Path(values["--panorama-output"]), PANORAMA_SIZE, "#777777")
            Path(values["--result"]).write_text(json.dumps({
                "blender_version": "5.0.1",
                "render_device": "GPU",
                "compute_device_type": "OPTIX",
                "gpu_devices": ["Test GPU"],
            }), encoding="utf-8")

        def communicate(self, timeout=None):
            return "Blender mock complete", None

    monkeypatch.setattr("universal_asset_library.previews.hdri_renderer.validate_blender_executable", lambda _path: (True, "ok", "Blender 5.0.1"))
    monkeypatch.setattr("universal_asset_library.previews.hdri_renderer.subprocess.Popen", FakeProcess)
    result = render_hdri_preview(HdriPreviewRequest(
        hdri, tmp_path / "output", "Courtyard", ("4K",), "maps/environment.exr",
        blender_path=str(executable), template_path=template,
    ))
    assert result.status == "ready"
    assert result.thumbnail_path == result.hero_path
    assert [path.name for path in result.hero_path.parent.glob("*_HDRI_*.jpg")] == ["Courtyard_HDRI_Preview.jpg"]
    assert captured["command"][:7] == [
        str(executable), "--background", "--factory-startup", "--disable-autoexec",
        "--python-exit-code", "1", str(template),
    ]
    assert captured["kwargs"].get("shell") is None
    assert result.metadata["scene_width"] == 1024
    assert result.metadata["panorama_height"] == 512
    assert result.metadata["height"] == 768
    assert result.metadata["render_device"] == "GPU"
    assert result.metadata["compute_device_type"] == "OPTIX"
    assert result.metadata["gpu_devices"] == ["Test GPU"]


def test_missing_blender_is_nonfatal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("universal_asset_library.previews.hdri_renderer.resolve_blender_executable", lambda _value="": "")
    result = render_hdri_preview(HdriPreviewRequest(
        tmp_path / "missing.exr", tmp_path / "out", "Missing", ("8K",), "maps/missing.exr",
    ))
    assert result.status == "unsupported"
    assert "Settings" in result.diagnostic


def test_driver_uses_world_001_and_writes_source_equirectangular_directly() -> None:
    source = driver_path().read_text(encoding="utf-8")
    assert 'bpy.data.worlds.get("World.001")' in source
    assert 'nodes.get("Environment Texture")' in source
    assert "environment.image.save_render" in source
    assert "actual_size not in {(1024, 256), (2048, 512)}" in source
    assert "scene.render.resolution_x = 1024" in source
    assert "scene.render.resolution_y = 256" in source
    assert 'scene.cycles.device = "GPU"' in source
    assert 'GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")' in source
    assert 'camera_data.type = "PANO"' not in source


@pytest.mark.skipif(shutil.which("blender") is None, reason="Blender is not available on PATH")
def test_real_template_can_render_when_blender_is_available(tmp_path) -> None:
    source = next(Path("/home/gambit/000test").glob("*.exr"), None)
    if source is None:
        pytest.skip("No real HDRI fixture is available")
    app = QApplication.instance() or QApplication([])
    result = render_hdri_preview(HdriPreviewRequest(
        source, tmp_path / "render", "Integration HDRI", ("4K",), source.name,
        timeout_seconds=600,
    ))
    assert result.status == "ready", result.diagnostic or result.log
    assert QImage(str(result.hero_path)).size().width() == 1024
