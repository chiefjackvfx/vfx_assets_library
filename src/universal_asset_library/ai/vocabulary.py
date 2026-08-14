from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path


TAG_FILENAMES = {
    "texture_set": "allowed_tags_texture.json",
    "atlas": "allowed_tags_texture.json",
    "hdri": "allowed_tags.json",
    "model": "allowed_tags_model.json",
    "vdb": "allowed_tags_vdb.json",
    "stock": "allowed_tags_stock.json",
}


def bundled_tag_path(asset_type: str) -> Path:
    try:
        name = TAG_FILENAMES[asset_type]
    except KeyError as error:
        raise ValueError(f"Unsupported asset type: {asset_type}") from error
    return Path(str(files("universal_asset_library.ai.tags").joinpath(name)))


def load_tag_vocabulary(
    asset_type: str, library_root: str | Path | None = None
) -> tuple[str, ...]:
    if asset_type == "stock" and library_root:
        stock_path = Path(library_root) / ".ual" / "stock_tags.json"
        if stock_path.is_file():
            return _load_names(stock_path)
    return _load_names(bundled_tag_path(asset_type))


def _load_names(path: Path) -> tuple[str, ...]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    records = document.get("tags") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"Tag vocabulary {path} must contain a tags array.")
    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        value = record.get("name") if isinstance(record, dict) else record
        name = str(value or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    if len(names) < 5:
        raise ValueError(f"Tag vocabulary {path} must define at least five tags.")
    return tuple(names)
