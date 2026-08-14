from __future__ import annotations

import json
import os
from pathlib import Path

from .model_scanner import MODEL_FORMATS
from .scanner import _filename_channel


def detect_asset_type(source: str | Path) -> tuple[str, str]:
    """Return a conservative importer mode and a short user-facing reason."""
    root = Path(source).expanduser().absolute()
    model_files = 0
    hdr_files = 0
    exr_files = 0
    pbr_maps = 0
    stock_files = 0
    vdb_files = 0
    archive_packages: set[tuple[str, str]] = set()
    zip_packages: set[tuple[str, str]] = set()
    image_previews: set[tuple[str, str]] = set()
    json_paths: list[Path] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories[:] = [
            name for name in directories
            if not name.startswith(".") and name.casefold() not in {"__pycache__", "node_modules"}
            and not (Path(current) / name).is_symlink()
        ]
        for name in files:
            path = Path(current) / name
            if name.startswith(".") or path.is_symlink():
                continue
            suffix = path.suffix.casefold()
            package_key = (str(path.parent).casefold(), path.stem.casefold())
            if suffix in {".rar", ".zip"}:
                archive_packages.add(package_key)
                if suffix == ".zip":
                    zip_packages.add(package_key)
            elif suffix in {".jpg", ".jpeg", ".png", ".webp"}:
                image_previews.add(package_key)
            if suffix in MODEL_FORMATS:
                model_files += 1
            elif suffix == ".vdb":
                vdb_files += 1
            elif suffix in {".mov", ".mp4"}:
                stock_files += 1
            elif suffix == ".hdr":
                hdr_files += 1
            elif suffix == ".exr":
                exr_files += 1
            if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".webp", ".exr", ".hdr"}:
                if _filename_channel(name)[0]:
                    pbr_maps += 1
            elif suffix == ".json" and len(json_paths) < 32:
                json_paths.append(path)

    declared: str | None = None
    for path in json_paths:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        asset_type = document.get("type")
        type_name = asset_type.casefold() if isinstance(asset_type, str) else ""
        if isinstance(document.get("meshes"), list) or asset_type == 2 or type_name in {"model", "3d_model"}:
            return "model", f"provider metadata in {path.name} declares a model"
        if type_name in {"stock", "footage", "video"}:
            return "stock", f"provider metadata in {path.name} declares Stock footage"
        semantic = document.get("semanticTags") if isinstance(document.get("semanticTags"), dict) else {}
        categories = {str(value).strip().casefold() for value in document.get("categories", []) if value}
        asset_categories = document.get("assetCategories")
        atlas_tree = isinstance(asset_categories, dict) and "atlas" in {
            str(value).casefold() for value in asset_categories
        }
        declarations = [
            item for key in ("maps", "components")
            for item in (document.get(key) if isinstance(document.get(key), list) else [])
            if isinstance(item, dict)
        ]
        cutout = any(
            str(item.get("type") or item.get("name") or "").strip().casefold()
            in {"opacity", "translucency"}
            for item in declarations
        )
        if (
            str(semantic.get("asset_type") or "").strip().casefold() == "atlas"
            or cutout and ("atlas" in categories or atlas_tree)
        ):
            return "atlas", f"provider metadata in {path.name} declares a Megascans atlas"
        if ("maps" in document or "components" in document) and "semanticTags" in document:
            declared = "texture_set"
        files = document.get("files")
        if isinstance(files, dict) and isinstance(files.get("hdri"), dict):
            declared = "hdri"
        if asset_type == 1 or type_name in {"texture", "material"}:
            declared = "texture_set"
        elif asset_type == 0 or type_name in {"hdri", "environment"}:
            declared = "hdri"
    if declared:
        label = "an HDRI" if declared == "hdri" else "a texture material"
        return declared, f"provider metadata declares {label}"
    paired_archives = archive_packages & image_previews
    if paired_archives:
        return (
            "texture_set",
            f"found {len(paired_archives)} paired archive material package(s)",
        )
    if zip_packages:
        return "texture_set", f"found {len(zip_packages)} ZIP package(s) to inspect"
    if model_files:
        return "model", f"found {model_files} supported 3D model file(s)"
    if vdb_files:
        return "vdb", f"found {vdb_files} OpenVDB file(s)"
    if stock_files:
        return "stock", f"found {stock_files} MOV/MP4 video file(s)"
    if hdr_files:
        return "hdri", f"found {hdr_files} HDR panorama file(s)"
    if exr_files and not pbr_maps:
        return "hdri", "found EXR images without PBR channel names"
    if pbr_maps:
        return "texture_set", f"found {pbr_maps} texture map file(s)"
    return "texture_set", "no stronger model or HDRI signature was found"
