import json
import os
from pathlib import Path
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from universal_asset_library.importer import scan_texture_folder
from universal_asset_library.library import LibraryRepository
from universal_asset_library.previews import (
    BlenderPreviewSession,
    BlenderPreviewSessionError,
)
from universal_asset_library.previews.texture_renderer import (
    HERO_SIZE,
    THUMBNAIL_SIZE,
    TexturePreviewMap,
    TexturePreviewRequest,
    TexturePreviewResult,
    _last_log_line,
    default_template_path,
    driver_path,
    render_texture_preview,
    select_texture_maps,
    select_texture_variant,
)


def _image(path: Path, color: str = "#777777") -> None:
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))


def _texture_source(
    tmp_path: Path,
    *,
    optional_maps: bool = True,
    source_preview: bool = True,
) -> Path:
    source = tmp_path / "stone_source"
    source.mkdir()
    _image(source / "Stone_BaseColor_1K.jpg", "#876543")
    if optional_maps:
        _image(source / "Stone_Roughness_1K.jpg", "#999999")
        _image(source / "Stone_Normal_DX_1K.jpg", "#8080ff")
    if source_preview:
        _image(source / "Stone_Preview.jpg", "#456789")
    return source


def test_selects_highest_resolution_up_to_4k_and_preferred_maps() -> None:
    resolutions = {
        "1K": {"maps": {"Base Color": [{"path": "one.jpg"}]}},
        "4K": {
            "maps": {
                "Base Color": [
                    {"path": "alternate.jpg"},
                    {"path": "preferred.jpg", "preferred": True},
                ],
                "Roughness": [{"path": "rough.jpg"}],
                "Height": [{"path": "height.exr", "preferred": True}],
                "Packed ARM": [{
                    "path": "arm.png",
                    "packed_channels": {
                        "R": "Ambient Occlusion",
                        "G": "Roughness",
                        "B": "Metalness",
                    },
                }],
            }
        },
        "8K": {"maps": {"Base Color": [{"path": "eight.jpg"}]}},
    }
    label, variant = select_texture_variant(resolutions)
    assert label == "4K"
    selected = select_texture_maps(variant)
    assert selected["Base Color"]["path"] == "preferred.jpg"
    assert selected["Roughness"]["path"] == "rough.jpg"
    assert selected["Height"]["path"] == "height.exr"
    assert selected["Packed ARM"]["path"] == "arm.png"
    assert "Normal" not in selected
    assert select_texture_variant({"8K": resolutions["8K"]})[0] == "8K"


def test_selects_every_plugin_supported_material_channel() -> None:
    channels = (
        "Base Color", "Ambient Occlusion", "Cavity", "Roughness",
        "Glossiness", "Metalness", "Specular", "Normal", "Bump",
        "Height", "Displacement", "Opacity", "Emission", "Translucency",
    )
    variant = {
        "maps": {
            channel: [
                {"path": f"{channel}.alternate"},
                {"path": f"{channel}.preferred", "preferred": True},
            ]
            for channel in channels
        }
    }

    selected = select_texture_maps(variant)

    assert tuple(selected) == tuple(
        sorted(channels, key=str.casefold)
    )
    assert all(
        record["path"].endswith(".preferred")
        for record in selected.values()
    )


def test_missing_base_color_is_nonfatal_and_optional_maps_are_allowed(
    tmp_path, monkeypatch
) -> None:
    result = render_texture_preview(
        TexturePreviewRequest(tmp_path / "out", "Stone", "1K", ())
    )
    assert result.status == "unsupported"
    assert "Base Color" in result.diagnostic

    base = tmp_path / "base.jpg"
    _image(base)
    monkeypatch.setattr(
        "universal_asset_library.previews.texture_renderer.resolve_blender_executable",
        lambda _value="": "",
    )
    result = render_texture_preview(
        TexturePreviewRequest(
            tmp_path / "out",
            "Stone",
            "1K",
            (TexturePreviewMap("Base Color", base, "maps/base.jpg"),),
        )
    )
    assert result.status == "unsupported"
    assert result.sources == {"Base Color": "maps/base.jpg"}


def test_blender_invocation_writes_valid_jpeg_and_passes_normal_convention(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    template = tmp_path / "template.blend"
    executable = tmp_path / "Blender With Spaces"
    base = tmp_path / "base.jpg"
    roughness = tmp_path / "rough.jpg"
    normal = tmp_path / "normal.jpg"
    template.write_bytes(b"template")
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    for path in (base, roughness, normal):
        _image(path)
    captured = {}

    class FakeSession:
        blender_version = "5.2.0"
        log = "Blender mock complete"

        def render(
            self, kind, selected_template, payload, **_kwargs
        ):
            captured["kind"] = kind
            captured["template"] = selected_template
            captured["payload"] = payload
            thumbnail = QImage(
                THUMBNAIL_SIZE[0],
                THUMBNAIL_SIZE[1],
                QImage.Format.Format_RGB32,
            )
            thumbnail.fill(QColor("#556677"))
            hero = QImage(
                HERO_SIZE[0],
                HERO_SIZE[1],
                QImage.Format.Format_RGB32,
            )
            hero.fill(QColor("#776655"))
            assert thumbnail.save(payload["thumbnail_output"], "PNG")
            assert hero.save(payload["hero_output"], "PNG")
            if payload["blend_output"]:
                Path(payload["blend_output"]).write_bytes(
                    b"BLENDER-v520-debug-fixture"
                )
            return {
                "blender_version": "5.2.0",
                "render_device": "GPU",
                "compute_device_type": "OPTIX",
                "gpu_devices": ["Test GPU"],
                "thumbnail_camera": "render_ball",
                "hero_camera": "render_plane",
            }

        def template_hash(self, _template):
            return "template-hash"

    result = render_texture_preview(
        TexturePreviewRequest(
            tmp_path / "output",
            "Stone",
            "4K",
            (
                TexturePreviewMap("Base Color", base, "maps/base.jpg"),
                TexturePreviewMap("Roughness", roughness, "maps/rough.jpg"),
                TexturePreviewMap(
                    "Normal",
                    normal,
                    "maps/normal.jpg",
                    normal_convention="DirectX",
                ),
            ),
            blender_path=str(executable),
            template_path=template,
            save_blend_file=True,
        ),
        session=FakeSession(),
    )
    assert result.status == "ready"
    assert result.thumbnail_path != result.hero_path
    assert result.thumbnail_path.name == "Stone_Texture_Thumbnail.jpg"
    assert result.hero_path.name == "Stone_Texture_Preview.jpg"
    assert result.hero_path.read_bytes().startswith(b"\xff\xd8\xff")
    assert captured["kind"] == "texture"
    maps_path = Path(captured["payload"]["maps_json"])
    maps = json.loads(maps_path.read_text(encoding="utf-8"))
    assert [item["channel"] for item in maps] == [
        "Base Color", "Roughness", "Normal",
    ]
    assert maps[-1]["normal_convention"] == "DirectX"
    assert captured["template"] == template
    assert result.metadata["resolution"] == "4K"
    assert result.metadata["compute_device_type"] == "OPTIX"
    assert result.metadata["thumbnail_camera"] == "render_ball"
    assert result.metadata["hero_camera"] == "render_plane"
    assert result.metadata["thumbnail_width"] == 512
    assert result.metadata["hero_width"] == 1024
    assert result.blend_path is not None
    assert result.blend_path.name == "Stone_Texture_Preview.blend"
    assert result.blend_path.is_file()
    assert result.metadata["debug_blend"] == result.blend_path.name
    assert captured["payload"]["blend_output"]


def test_driver_contract_reuses_plugin_material_graph() -> None:
    source = driver_path().read_text(encoding="utf-8")
    assert 'bpy.data.materials.get("Material.001")' in source
    assert "shotbox_assets_bridge" in source
    assert "actions._texture_sources" in source
    assert "actions._build_principled_graph" in source
    assert "actions._layout_texture_material" in source
    assert 'parser.add_argument("--base-color", default="")' in source
    assert "_legacy_map_records(args)" in source
    assert 'bpy.data.objects.get("render_ball")' in source
    assert 'bpy.data.objects.get("render_plane")' in source
    assert 'scene.cycles.device = "GPU"' in source
    assert "image.pack()" in source
    assert "bpy.ops.wm.save_as_mainfile" in source
    assert "actual_size != (512, 512)" in source
    assert default_template_path().name == "shader_preview.blend"
    assert default_template_path().is_file()


def test_blender_diagnostic_prefers_root_exception_over_script_wrapper() -> None:
    output = """
Traceback (most recent call last):
  File "blender_texture_driver.py", line 1, in main
RuntimeError: The texture preview template has no active camera.
Error: script failed, file: 'blender_texture_driver.py', exiting.
"""
    assert _last_log_line(output) == (
        "RuntimeError: The texture preview template has no active camera."
    )


def test_texture_render_cancellation_terminates_blender(tmp_path, monkeypatch) -> None:
    template = tmp_path / "template.blend"
    executable = tmp_path / "blender"
    base = tmp_path / "base.jpg"
    template.write_bytes(b"template")
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    _image(base)
    state = {"canceled": False}

    class FakeSession:
        blender_version = "5.2.0"
        log = "terminated"

        def render(self, *_args, cancel_token=None, **_kwargs):
            state["canceled"] = bool(cancel_token.cancelled)
            raise BlenderPreviewSessionError(
                "canceled", "Preview rendering was canceled.", self.log
            )

        def template_hash(self, _template):
            return "template-hash"

    token = type("Canceled", (), {"cancelled": True})()
    result = render_texture_preview(
        TexturePreviewRequest(
            tmp_path / "out",
            "Stone",
            "1K",
            (TexturePreviewMap("Base Color", base, "maps/base.jpg"),),
            blender_path=str(executable),
            template_path=template,
        ),
        cancel_token=token,
        session=FakeSession(),
    )
    assert result.status == "canceled"
    assert state["canceled"]


def test_texture_import_publishes_shader_preview_metadata(
    tmp_path, monkeypatch
) -> None:
    source = _texture_source(tmp_path, source_preview=False)
    library = tmp_path / "library"
    library.mkdir()

    def fake_render(request, progress=None, cancel_token=None, session=None):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hero = request.output_dir / "Stone_Texture_Preview.jpg"
        thumbnail = request.output_dir / "Stone_Texture_Thumbnail.jpg"
        image = QImage(512, 512, QImage.Format.Format_RGB32)
        image.fill(QColor("#234567"))
        assert image.save(str(hero), "JPG", 90)
        assert image.save(str(thumbnail), "JPG", 90)
        blend = request.output_dir / "Stone_Texture_Preview.blend"
        if request.save_blend_file:
            blend.write_bytes(b"portable debug scene")
        metadata = {
            "type": "texture_shader",
            "status": "ready",
            "resolution": request.resolution,
            "sources": {
                item.channel: item.source_relative for item in request.maps
            },
            "generated_at": "now",
            "blender_version": "5.2.0",
            "template_sha256": "abc",
            "diagnostic": "",
        }
        return TexturePreviewResult(
            "ready",
            thumbnail,
            hero,
            request.resolution,
            metadata["sources"],
            metadata=metadata,
            blend_path=blend if request.save_blend_file else None,
        )

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_texture_preview",
        fake_render,
    )
    candidate = scan_texture_folder(source).materials[0]
    asset = LibraryRepository(
        library, save_texture_preview_blend=True
    ).import_materials([candidate]).imported[0]
    assert asset.preview_render["status"] == "ready"
    assert asset.preview_render["resolution"] == candidate.resolution_labels[0]
    assert asset.hero_path != asset.thumbnail_path
    assert asset.hero_path.name == "Stone_Texture_Preview.jpg"
    assert asset.thumbnail_path.name == "Stone_Texture_Thumbnail.jpg"
    debug_blend = asset.asset_dir / asset.preview_render["debug_blend"]
    assert debug_blend.name == "Stone_Texture_Preview.blend"
    assert debug_blend.read_bytes() == b"portable debug scene"
    assert any(
        path.name != "Stone_Texture_Preview.jpg"
        for path in (asset.asset_dir / "previews").iterdir()
    )


def test_repository_batch_import_reuses_one_preview_session(
    tmp_path, monkeypatch
) -> None:
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = _texture_source(first_parent, source_preview=False)
    second = _texture_source(second_parent, source_preview=False)
    _image(second / "Stone_BaseColor_1K.jpg", "#123456")
    sessions = []

    def fake_render(request, progress=None, cancel_token=None, session=None):
        sessions.append(session)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hero = request.output_dir / "Stone_Texture_Preview.jpg"
        thumbnail = request.output_dir / "Stone_Texture_Thumbnail.jpg"
        _image(hero, "#112233")
        _image(thumbnail, "#223344")
        metadata = {
            "type": "texture_shader",
            "status": "ready",
            "resolution": request.resolution,
            "sources": {
                item.channel: item.source_relative for item in request.maps
            },
        }
        return TexturePreviewResult(
            "ready", thumbnail, hero, request.resolution,
            metadata["sources"], metadata=metadata,
        )

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_texture_preview",
        fake_render,
    )
    library = tmp_path / "library"
    library.mkdir()
    summary = LibraryRepository(library).import_materials([
        scan_texture_folder(first).materials[0],
        scan_texture_folder(second).materials[0],
    ])

    assert len(summary.imported) == 2
    assert len(sessions) == 2
    assert sessions[0] is sessions[1]
    assert isinstance(sessions[0], BlenderPreviewSession)


def test_texture_import_disabled_or_failed_retains_provider_preview(
    tmp_path, monkeypatch
) -> None:
    source = _texture_source(tmp_path, source_preview=False)
    candidate = scan_texture_folder(source).materials[0]
    disabled_library = tmp_path / "disabled"
    disabled_library.mkdir()
    disabled = LibraryRepository(
        disabled_library, render_texture_previews=False
    ).import_materials([candidate]).imported[0]
    assert disabled.preview_render["status"] == "pending"
    assert disabled.hero_path.name != "Stone_Texture_Preview.jpg"

    failed_library = tmp_path / "failed"
    failed_library.mkdir()
    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_texture_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("renderer exploded")
        ),
    )
    summary = LibraryRepository(failed_library).import_materials([candidate])
    assert not summary.failed
    failed = summary.imported[0]
    assert failed.preview_render["status"] == "failed"
    assert failed.hero_path.is_file()


def test_texture_import_with_source_preview_skips_automatic_blender(
    tmp_path, monkeypatch
) -> None:
    source = _texture_source(tmp_path, source_preview=True)
    library = tmp_path / "library"
    library.mkdir()
    called = []
    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_texture_preview",
        lambda *_args, **_kwargs: called.append(True),
    )

    asset = LibraryRepository(library).import_materials(
        [scan_texture_folder(source).materials[0]]
    ).imported[0]

    assert called == []
    assert asset.preview_render["status"] == "source"
    assert asset.hero_path.name != "Stone_Texture_Preview.jpg"
    assert "automatic Blender rendering was skipped" in (
        asset.preview_render["diagnostic"]
    )


def test_manual_texture_regeneration_rejects_changed_map_and_keeps_preview(
    tmp_path, monkeypatch
) -> None:
    source = _texture_source(tmp_path, optional_maps=False)
    library = tmp_path / "library"
    library.mkdir()
    repository = LibraryRepository(library, render_texture_previews=False)
    asset = repository.import_materials(
        [scan_texture_folder(source).materials[0]]
    ).imported[0]
    previous = asset.hero_path.read_bytes()

    def fake_render(request, progress=None, cancel_token=None, session=None):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        hero = request.output_dir / "Stone_Texture_Preview.jpg"
        request.maps[0].path.write_bytes(
            request.maps[0].path.read_bytes() + b"changed"
        )
        _image(hero, "#112233")
        metadata = {
            "type": "texture_shader",
            "status": "ready",
            "resolution": request.resolution,
            "sources": {
                item.channel: item.source_relative for item in request.maps
            },
        }
        return TexturePreviewResult(
            "ready",
            hero,
            hero,
            request.resolution,
            metadata["sources"],
            metadata=metadata,
        )

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_texture_preview",
        fake_render,
    )
    with pytest.raises(Exception, match="changed while its preview was rendering"):
        repository.render_texture_preview(asset.id)
    assert asset.hero_path.read_bytes() == previous


@pytest.mark.skipif(
    shutil.which("blender") is None, reason="Blender is not available on PATH"
)
def test_real_shader_template_can_render_when_blender_is_available(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    base = tmp_path / "base.jpg"
    roughness = tmp_path / "roughness.jpg"
    normal = tmp_path / "normal.jpg"
    _image(base, "#876543")
    _image(roughness, "#999999")
    _image(normal, "#8080ff")
    result = render_texture_preview(
        TexturePreviewRequest(
            tmp_path / "render",
            "Integration Texture",
            "1K",
            (
                TexturePreviewMap("Base Color", base, "maps/base.jpg"),
                TexturePreviewMap("Roughness", roughness, "maps/roughness.jpg"),
                TexturePreviewMap("Normal", normal, "maps/normal.jpg"),
            ),
            timeout_seconds=600,
        )
    )
    assert result.status == "ready", result.diagnostic or result.log
    image = QImage(str(result.hero_path))
    assert (image.width(), image.height()) == HERO_SIZE
    thumbnail = QImage(str(result.thumbnail_path))
    assert (thumbnail.width(), thumbnail.height()) == THUMBNAIL_SIZE
    assert result.thumbnail_path != result.hero_path


@pytest.mark.skipif(
    shutil.which("blender") is None, reason="Blender is not available on PATH"
)
def test_real_session_reuses_blender_for_two_texture_renders(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    executable = shutil.which("blender") or ""
    session = BlenderPreviewSession(executable)
    results = []
    try:
        for index, color in enumerate(("#884422", "#226688"), start=1):
            base = tmp_path / f"base-{index}.jpg"
            _image(base, color)
            results.append(render_texture_preview(
                TexturePreviewRequest(
                    tmp_path / f"render-{index}",
                    f"Integration Texture {index}",
                    "1K",
                    (TexturePreviewMap(
                        "Base Color", base, f"maps/base-{index}.jpg"
                    ),),
                    blender_path=executable,
                    timeout_seconds=600,
                ),
                session=session,
            ))
    finally:
        session.close()

    assert [result.status for result in results] == ["ready", "ready"], [
        result.diagnostic or result.log for result in results
    ]
    assert session.start_count == 1
    assert results[0].hero_path != results[1].hero_path
