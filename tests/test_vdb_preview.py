from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
import pytest

from universal_asset_library.importer import scan_vdb_folder
from universal_asset_library.library import LibraryRepository
from universal_asset_library.previews import (
    VdbPreviewRequest,
    VdbPreviewResult,
    render_vdb_preview,
    resolve_houdini_executable,
    validate_houdini_executable,
)
from universal_asset_library.previews.houdini_vdb_driver import render as render_driver


@pytest.fixture(autouse=True)
def _local_vdb_preview_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "temp_cache"
    monkeypatch.setattr(
        "universal_asset_library.library.repository.VDB_PREVIEW_CACHE_ROOT",
        cache,
    )
    return cache


def _import_static_vdb(tmp_path: Path, *, preview_video: bool = False):
    source = tmp_path / "source"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    for label in ("Low", "Mid", "High"):
        (source / f"cloud_formation_001_{label}_Res.vdb").write_bytes(
            label.encode()
        )
    if preview_video:
        (source / "cloud_formation_001_preview.mp4").write_bytes(b"video")
    asset = LibraryRepository(library).import_vdbs(
        scan_vdb_folder(source).materials
    ).imported[0]
    return library, asset


def _ready_render(request: VdbPreviewRequest) -> VdbPreviewResult:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    preview = request.output_dir / "Cloud_Formation_001_VDB_Preview.jpg"
    image = QImage(1280, 720, QImage.Format.Format_RGB32)
    image.fill(QColor("#657789"))
    assert image.save(str(preview), "JPG", 90)
    video = None
    if request.mode == "turntable":
        video = request.output_dir / "Cloud_Formation_001_VDB_Turntable.mp4"
        video.write_bytes(b"generated turntable")
    metadata = {
        "type": "vdb_still",
        "status": "ready",
        "variant": request.variant,
        "source": request.source_relative,
        "source_sha256": request.source_sha256,
        "frame": 1,
        "density_scale": request.density_scale,
        "mode": request.mode,
        "scrub_optimized": request.mode == "turntable",
        "color_transform": (
            "houdini_iconvert_auto"
            if request.mode == "turntable"
            else ""
        ),
        "video_color_space": "bt709" if request.mode == "turntable" else "",
        "generated_at": "2026-08-14T12:00:00+00:00",
        "houdini_version": "22.0.368",
        "template_sha256": "template-hash",
        "diagnostic": "",
    }
    return VdbPreviewResult(
        "ready",
        preview,
        preview,
        request.variant,
        request.source_relative,
        1,
        "22.0.368",
        "template-hash",
        metadata=metadata,
        video_path=video,
        mode=request.mode,
    )


def test_houdini_path_normalizes_houdini_and_hbatch_to_hython(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "hython"
    binary.write_bytes(b"fixture")
    binary.chmod(0o755)
    for alias in ("houdini", "hbatch"):
        path = tmp_path / alias
        path.write_bytes(b"fixture")
        path.chmod(0o755)

    assert resolve_houdini_executable(str(tmp_path / "houdini")) == str(binary)
    assert resolve_houdini_executable(str(tmp_path / "hbatch")) == str(binary)
    assert resolve_houdini_executable(str(binary)) == str(binary)


def test_houdini_check_rejects_version_21(tmp_path: Path, monkeypatch) -> None:
    hython = tmp_path / "hython"
    iconvert = tmp_path / "iconvert"
    for path in (hython, iconvert):
        path.write_bytes(b"fixture")
        path.chmod(0o755)

    class Completed:
        returncode = 0
        stdout = "21.0.700\n"
        stderr = ""

    monkeypatch.setattr(
        "universal_asset_library.previews.vdb_renderer.subprocess.run",
        lambda *_args, **_kwargs: Completed(),
    )

    valid, message, version = validate_houdini_executable(str(hython))

    assert not valid
    assert "22 or newer" in message
    assert version == "21.0.700"


class _Parm:
    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = value


class _Node:
    def __init__(self, *, creates_output: bool = False) -> None:
        self.parms = {
            name: _Parm()
            for name in (
                "file", "outputimage", "trange", "f1", "f2", "f3", "densityscale"
            )
        }
        self.creates_output = creates_output
        self.cook_calls = []
        self.render_calls = []

    def parm(self, name):
        return self.parms.get(name)

    def cook(self, **kwargs) -> None:
        self.cook_calls.append(kwargs)

    def errors(self):
        return ()

    def render(self, **kwargs) -> None:
        self.render_calls.append(kwargs)
        output = Path(self.parms["outputimage"].value)
        output.parent.mkdir(parents=True, exist_ok=True)
        frame_range = kwargs.get("frame_range", ())
        if "$F4" in str(output) and len(frame_range) == 3:
            for frame in range(frame_range[0], frame_range[1] + 1):
                Path(str(output).replace("$F4", f"{frame:04d}")).write_bytes(b"EXR")
        else:
            output.write_bytes(b"EXR")


class _HipFile:
    def __init__(self) -> None:
        self.loads = []
        self.saves = []

    def load(self, path, **kwargs) -> None:
        self.loads.append((path, kwargs))

    def save(self, *args, **kwargs) -> None:
        self.saves.append((args, kwargs))


class _Hou:
    def __init__(self) -> None:
        self.hipFile = _HipFile()
        self.file_node = _Node()
        self.render_node = _Node(creates_output=True)
        self.pyro_shader = _Node()
        self.frames = []

    def node(self, path):
        return {
            "/obj/VDB/file1": self.file_node,
            "/stage/usdrender_rop1": self.render_node,
            "/stage/materiallibrary1/karmacloudmaterial1/kma_pyroshader1": self.pyro_shader,
        }.get(path)

    def setFrame(self, frame) -> None:
        self.frames.append(frame)

    @staticmethod
    def applicationVersionString() -> str:
        return "22.0.368"

    @staticmethod
    def fps() -> float:
        return 24.0


def test_houdini_driver_uses_exact_nodes_frame_and_output_without_saving(
    tmp_path: Path,
) -> None:
    hou = _Hou()
    template = tmp_path / "template.hip"
    template.write_bytes(b"HIP")
    output = tmp_path / "staging" / "preview.exr"

    result = render_driver(hou, {
        "template_path": str(template),
        "vdb_path": "/library/cloud_mid_$F4.vdb",
        "output_exr": str(output),
        "frame": 1,
        "density_scale": 275,
    })

    assert result["ok"] is True
    assert result["density_scale"] == 275
    assert hou.hipFile.loads == [
        (str(template), {"suppress_save_prompt": True})
    ]
    assert not hou.hipFile.saves
    assert hou.file_node.parms["file"].value == "/library/cloud_mid_$F4.vdb"
    assert hou.file_node.cook_calls == [{"force": True}]
    assert hou.frames == [1]
    assert hou.render_node.parms["outputimage"].value == str(output)
    assert hou.render_node.parms["trange"].value == 0
    assert hou.render_node.parms["f1"].value == 1
    assert hou.render_node.parms["f2"].value == 1
    assert hou.pyro_shader.parms["densityscale"].value == 275
    assert hou.render_node.render_calls == [{
        "frame_range": (1, 1),
        "verbose": True,
        "output_progress": True,
    }]


def test_houdini_driver_renders_complete_turntable_range(tmp_path: Path) -> None:
    hou = _Hou()
    template = tmp_path / "template.hip"
    template.write_bytes(b"HIP")
    output = tmp_path / "frames" / "turntable.$F4.exr"

    result = render_driver(hou, {
        "template_path": str(template),
        "vdb_path": "/library/cloud_mid.vdb",
        "output_exr": str(output),
        "frame": 1,
        "frame_start": 1,
        "frame_end": 50,
        "mode": "turntable",
        "density_scale": 100,
    })

    assert result["mode"] == "turntable"
    assert (result["frame_start"], result["frame_end"]) == (1, 50)
    assert result["fps"] == 24.0
    assert hou.render_node.parms["trange"].value == 1
    assert hou.render_node.parms["f1"].value == 1
    assert hou.render_node.parms["f2"].value == 50
    assert hou.render_node.parms["f3"].value == 1
    assert hou.render_node.render_calls == [{
        "frame_range": (1, 50, 1),
        "verbose": True,
        "output_progress": True,
    }]
    assert Path(str(output).replace("$F4", "0001")).is_file()
    assert Path(str(output).replace("$F4", "0050")).is_file()


def test_renderer_converts_exr_and_reports_metadata(tmp_path: Path, monkeypatch) -> None:
    tool_dir = tmp_path / "hfs" / "bin"
    tool_dir.mkdir(parents=True)
    hython = tool_dir / "hython"
    iconvert = tool_dir / "iconvert"
    for tool in (hython, iconvert):
        tool.write_bytes(b"tool")
        tool.chmod(0o755)
    template = tmp_path / "VDB_preview_v001.hip"
    template.write_bytes(b"template")
    source = tmp_path / "cloud_mid.vdb"
    source.write_bytes(b"vdb")
    calls = []

    monkeypatch.setattr(
        "universal_asset_library.previews.vdb_renderer.validate_houdini_executable",
        lambda _path: (True, "ready", "22.0.368"),
    )

    def fake_process(command, _timeout, _token, progress):
        calls.append(command)
        if command[0] == str(hython):
            request_doc = json.loads((tmp_path / "stage" / "houdini-vdb-request.json").read_text())
            assert request_doc["template_path"] == str(
                (tmp_path / "stage" / template.name).resolve()
            )
            assert request_doc["density_scale"] == 250
            Path(request_doc["template_path"]).write_bytes(b"staged mutation")
            Path(request_doc["output_exr"]).write_bytes(b"EXR")
            (tmp_path / "stage" / "houdini-vdb-result.json").write_text(
                json.dumps({"ok": True, "houdini_version": "22.0.368"})
            )
            if progress:
                progress("Rendering Karma still at frame 1")
        else:
            image = QImage(1280, 720, QImage.Format.Format_RGB32)
            image.fill(QColor("#334455"))
            assert image.save(command[-1], "JPG", 90)
        return "ok"

    monkeypatch.setattr(
        "universal_asset_library.previews.vdb_renderer._run_process",
        fake_process,
    )
    updates = []
    result = render_vdb_preview(VdbPreviewRequest(
        source_path=source,
        vdb_path=str(source),
        source_relative="volumes/Mid/cloud_mid.vdb",
        source_sha256="source-hash",
        output_dir=tmp_path / "stage",
        asset_name="Cloud Formation 001",
        variant="Mid",
        density_scale=250,
        houdini_path=str(hython),
        template_path=template,
    ), progress=updates.append)

    assert result.status == "ready"
    assert result.metadata["variant"] == "Mid"
    assert result.metadata["frame"] == 1
    assert result.metadata["density_scale"] == 250
    assert result.metadata["houdini_version"] == "22.0.368"
    assert result.metadata["template_sha256"]
    assert template.read_bytes() == b"template"
    assert QImage(str(result.hero_path)).size().width() == 1280
    assert calls[1][:4] == [str(iconvert), "-d", "8", "-g"]
    assert "Rendering Karma still at frame 1" in updates


def test_turntable_renders_50_frames_and_encodes_mp4(
    tmp_path: Path, monkeypatch
) -> None:
    tool_dir = tmp_path / "hfs" / "bin"
    tool_dir.mkdir(parents=True)
    hython = tool_dir / "hython"
    iconvert = tool_dir / "iconvert"
    ffmpeg = tool_dir / "ffmpeg"
    for tool in (hython, iconvert, ffmpeg):
        tool.write_bytes(b"tool")
        tool.chmod(0o755)
    template = tmp_path / "VDB_preview_v001.hip"
    template.write_bytes(b"template")
    source = tmp_path / "cloud_mid.vdb"
    source.write_bytes(b"vdb")
    calls = []
    worker_ranges = []
    monkeypatch.setattr(
        "universal_asset_library.previews.vdb_renderer.validate_houdini_executable",
        lambda _path: (True, "ready", "22.0.368"),
    )

    def fake_process(command, _timeout, _token, _progress):
        calls.append(command)
        if command[0] == str(hython):
            request_doc = json.loads(Path(command[-2]).read_text())
            assert request_doc["mode"] == "turntable"
            worker_range = (
                request_doc["frame_start"], request_doc["frame_end"]
            )
            worker_ranges.append(worker_range)
            pattern = request_doc["output_exr"]
            for frame in range(worker_range[0], worker_range[1] + 1):
                path = Path(pattern.replace("$F4", f"{frame:04d}"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"EXR")
            Path(command[-1]).write_text(
                json.dumps({
                    "ok": True,
                    "houdini_version": "22.0.368",
                    "fps": 24.0,
                })
            )
        elif command[0] == str(iconvert):
            image = QImage(854, 480, QImage.Format.Format_RGB32)
            image.fill(QColor("#334455"))
            image_format = "PNG" if command[-1].endswith(".png") else "JPG"
            assert image.save(command[-1], image_format, 90)
        else:
            assert command[0] == str(ffmpeg)
            assert command[command.index("-i") + 1].endswith(
                "video_frames/vdb-turntable.%04d.png"
            )
            assert command[command.index("-frames:v") + 1] == "50"
            assert command[command.index("-vf") + 1] == (
                "pad=ceil(iw/2)*2:ceil(ih/2)*2,"
                "scale=iw:ih:in_range=full:out_range=tv:"
                "out_color_matrix=bt709"
            )
            assert command[command.index("-g") + 1] == "1"
            assert command[command.index("-keyint_min") + 1] == "1"
            assert command[command.index("-sc_threshold") + 1] == "0"
            assert command[command.index("-color_range") + 1] == "tv"
            assert command[command.index("-colorspace") + 1] == "bt709"
            assert command[command.index("-color_primaries") + 1] == "bt709"
            assert command[command.index("-color_trc") + 1] == "bt709"
            Path(command[-1]).write_bytes(b"mp4")
        return "ok"

    monkeypatch.setattr(
        "universal_asset_library.previews.vdb_renderer._run_process",
        fake_process,
    )
    result = render_vdb_preview(VdbPreviewRequest(
        source_path=source,
        vdb_path=str(source),
        source_relative="volumes/Mid/cloud_mid.vdb",
        source_sha256="source-hash",
        output_dir=tmp_path / "stage",
        asset_name="Cloud Formation 001",
        variant="Mid",
        density_scale=175,
        mode="turntable",
        ffmpeg_path=str(ffmpeg),
        houdini_path=str(hython),
        template_path=template,
        parallel_processes=2,
    ))

    assert result.status == "ready"
    assert result.mode == "turntable"
    assert result.video_path is not None
    assert result.video_path.read_bytes() == b"mp4"
    assert result.metadata["frame_start"] == 1
    assert result.metadata["frame_end"] == 50
    assert result.metadata["fps"] == 24.0
    assert result.metadata["scrub_optimized"] is True
    assert result.metadata["color_transform"] == "houdini_iconvert_auto"
    assert result.metadata["video_color_space"] == "bt709"
    assert (result.metadata["width"], result.metadata["height"]) == (854, 480)
    assert result.metadata["parallel_processes"] == 2
    assert sorted(worker_ranges) == [(1, 25), (26, 50)]
    assert [command[0] for command in calls].count(str(hython)) == 2
    assert [command[0] for command in calls].count(str(iconvert)) == 51
    assert [command[0] for command in calls][-2:] == [
        str(iconvert), str(ffmpeg)
    ]


def test_repository_publishes_one_jpeg_and_retains_video(
    tmp_path: Path, monkeypatch
) -> None:
    library, asset = _import_static_vdb(tmp_path, preview_video=True)
    captured = {}

    def fake_render(request, progress=None, cancel_token=None):
        captured["request"] = request
        return _ready_render(request)

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_vdb_preview",
        fake_render,
    )

    update = LibraryRepository(library).render_vdb_preview(
        asset.id, "Mid", density_scale=240
    )

    assert update.render.status == "ready"
    assert update.asset.hero_path == update.asset.thumbnail_path
    assert update.asset.hero_path.name == "Cloud_Formation_001_VDB_Preview.jpg"
    assert QImage(str(update.asset.hero_path)).size().height() == 720
    assert update.asset.preview_path is not None
    assert update.asset.preview_path.read_bytes() == b"video"
    assert captured["request"].vdb_path.endswith(
        "/volumes/MID/cloud_formation_001_mid_res.vdb"
    )
    assert captured["request"].density_scale == 240
    assert update.asset.preview_render["density_scale"] == 240
    assert update.asset.preview_render["source_sha256"]


def test_repository_publishes_generated_turntable_video(
    tmp_path: Path, monkeypatch
) -> None:
    library, asset = _import_static_vdb(tmp_path, preview_video=True)
    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_vdb_preview",
        lambda request, **_kwargs: _ready_render(request),
    )

    update = LibraryRepository(
        library, ffmpeg_path="/usr/bin/ffmpeg"
    ).render_vdb_preview(asset.id, "Mid", mode="turntable")

    assert update.asset.preview_path is not None
    assert update.asset.preview_path.name == "Cloud_Formation_001_VDB_Turntable.mp4"
    assert update.asset.preview_path.read_bytes() == b"generated turntable"
    assert update.asset.preview_render["mode"] == "turntable"
    assert update.asset.preview_render["scrub_optimized"] is True
    assert update.render.video_path == update.asset.preview_path


def test_sequence_uses_padded_frame_one_expression_and_rejects_missing_frame_one(
    tmp_path: Path, monkeypatch
) -> None:
    def import_sequence(folder: Path, frames: tuple[int, ...]):
        source = folder / "source"
        library = folder / "library"
        source.mkdir(parents=True)
        library.mkdir(parents=True)
        for frame in frames:
            (source / f"smoke_high_{frame:04d}.vdb").write_bytes(str(frame).encode())
        asset = LibraryRepository(library).import_vdbs(
            scan_vdb_folder(source).materials
        ).imported[0]
        return library, asset

    captured = []

    def fake_render(request, progress=None, cancel_token=None):
        captured.append(request.vdb_path)
        return _ready_render(request)

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_vdb_preview",
        fake_render,
    )
    valid_library, valid_asset = import_sequence(tmp_path / "valid", (1, 2))
    LibraryRepository(valid_library).render_vdb_preview(valid_asset.id, "High")
    assert len(captured) == 1
    assert captured[0].endswith("smoke_high_$F4.vdb")

    invalid_library, invalid_asset = import_sequence(
        tmp_path / "invalid", (1001, 1002)
    )
    with pytest.raises(Exception, match="no source frame 1"):
        LibraryRepository(invalid_library).render_vdb_preview(
            invalid_asset.id, "High"
        )
    assert len(captured) == 1


def test_stale_vdb_source_retains_previous_preview_and_cleans_staging(
    tmp_path: Path, monkeypatch, _local_vdb_preview_cache: Path
) -> None:
    library, asset = _import_static_vdb(tmp_path)
    old_preview = asset.thumbnail_path.read_bytes()

    def fake_render(request, progress=None, cancel_token=None):
        request.source_path.write_bytes(b"changed while rendering")
        return _ready_render(request)

    monkeypatch.setattr(
        "universal_asset_library.library.repository.render_vdb_preview",
        fake_render,
    )

    with pytest.raises(Exception, match="changed while its preview was rendering"):
        LibraryRepository(library).render_vdb_preview(asset.id, "Low")

    assert asset.thumbnail_path.read_bytes() == old_preview
    assert not list(_local_vdb_preview_cache.glob("vdb-preview-*"))
