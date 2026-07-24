from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from universal_asset_library.importer import scan_texture_folder, unzip_all_zip_files
from universal_asset_library.library import LibraryRepository
import universal_asset_library.importer.scanner as scanner_module


def _image(path: Path, width: int = 1024) -> None:
    image = QImage(width, 8, QImage.Format.Format_RGB32)
    image.fill(0xFFB8B0A0)
    assert image.save(str(path), "JPG", 85)


def _source(
    tmp_path: Path, extension: str = ".rar", external_preview: bool = True
) -> Path:
    source = tmp_path / "Marble"
    source.mkdir()
    if external_preview:
        _image(source / "Marble_001.jpg")
    archive = source / f"Marble_001{extension}"
    if extension == ".zip":
        build = tmp_path / "zip-maps"
        build.mkdir()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for token in ("COL", "GLOSS", "NRM", "REFL"):
                path = build / f"Marble01_{token}_6K.jpg"
                _image(path, 6144)
                package.write(path, f"Marble 001/{path.name}")
        shutil.rmtree(build)
    else:
        archive.write_bytes(b"rar source")
    return source


def _fake_extract(_archive, destination, _extractor, _token):
    destination.mkdir(parents=True)
    paths = []
    for token in ("COL", "GLOSS", "NRM", "REFL"):
        path = destination / f"Marble01_{token}_6K.jpg"
        _image(path, 6144)
        paths.append(path)
    return paths


@pytest.mark.parametrize(("extension", "file_format", "external_preview"), [
    (".rar", "RAR", True),
    (".zip", "ZIP", True),
    (".zip", "ZIP", False),
])
def test_paired_archive_material_scans_and_imports(
    monkeypatch, tmp_path: Path, extension: str, file_format: str,
    external_preview: bool,
) -> None:
    source = _source(tmp_path, extension, external_preview)
    monkeypatch.setattr(scanner_module.shutil, "which", lambda _name: "/fake/bsdtar")
    if extension == ".rar":
        monkeypatch.setattr(scanner_module, "_extract_rar_images", _fake_extract)
    result = scan_texture_folder(source)
    try:
        assert len(result.materials) == 1
        material = result.materials[0]
        assert (material.name, material.category, material.resolution_labels) == (
            "Marble 001", "Marble", ["6K"],
        )
        assert {
            channel for variant in material.resolutions.values() for channel in variant.maps
        } == {"Base Color", "Glossiness", "Normal", "Specular"}
        if external_preview:
            assert material.selected_thumbnail == material.selected_hero == "Marble_001.jpg"
        else:
            assert "COL" in material.selected_thumbnail
            assert material.selected_hero == material.selected_thumbnail

        library = tmp_path / "library"
        library.mkdir()
        repository = LibraryRepository(library)
        preflight = repository.preflight_materials(result.materials)
        summary = repository.import_materials(
            result.materials, preflight_result=preflight
        )
        assert len(summary.imported) == 1
        asset = summary.imported[0]
        document = json.loads((asset.asset_dir / "asset.json").read_text())
        assert document["source"]["package"]["format"] == file_format
        assert document["source"]["package"]["sha256"]
        assert not any(path.suffix.casefold() == ".rar" for path in asset.asset_dir.rglob("*"))
    finally:
        for value in result.temporary_roots:
            shutil.rmtree(value, ignore_errors=True)


def test_changed_rar_is_stale_before_import(monkeypatch, tmp_path: Path) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(scanner_module.shutil, "which", lambda _name: "/fake/bsdtar")
    monkeypatch.setattr(scanner_module, "_extract_rar_images", _fake_extract)
    result = scan_texture_folder(source)
    try:
        archive = source / "Marble_001.rar"
        archive.write_bytes(b"changed rar source")
        library = tmp_path / "library"
        library.mkdir()
        preflight = LibraryRepository(library).preflight_materials(result.materials)
        assert preflight.materials[0].status == "Stale"
        assert "RAR archive changed" in preflight.materials[0].diagnostics[0].message
    finally:
        for value in result.temporary_roots:
            shutil.rmtree(value, ignore_errors=True)


def test_unzip_all_creates_same_name_folders_with_json(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    source.mkdir()
    archive = source / "Marble_004.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("Marble 004/material.json", '{"name":"Marble Four"}')
        package.writestr("Marble 004/readme.txt", "source notes")

    first = unzip_all_zip_files(source)
    target = source / "Marble_004"
    assert first.extracted == [target]
    assert (target / "Marble 004/material.json").is_file()
    assert (target / "Marble 004/readme.txt").is_file()
    assert archive.is_file()

    second = unzip_all_zip_files(source)
    assert not second.extracted
    assert str(archive) in second.skipped
