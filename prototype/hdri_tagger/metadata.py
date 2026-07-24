from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from typing import Iterable, Literal


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MetadataKind = Literal["managed", "provider"]
SUPPORTED_ASSET_TYPES = {"texture_set", "atlas", "hdri", "model", "stock"}


class MetadataError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AssetRecord:
    name: str
    folder: Path
    metadata_path: Path
    metadata_kind: MetadataKind
    category: str
    tags: tuple[str, ...]
    preview_candidates: tuple[Path, ...]
    preview_path: Path | None = None
    asset_id: str = ""
    library_root: Path | None = None
    diagnostic: str = ""
    asset_type: str = "hdri"

    @property
    def ready(self) -> bool:
        return bool(self.preview_path and not self.diagnostic)


def load_category_names(path: str | Path) -> tuple[str, ...]:
    document = _read_object(Path(path), "category")
    records = document.get("categories")
    if not isinstance(records, list):
        raise MetadataError("Category JSON must contain a categories array.")
    names: list[str] = []
    for record in records:
        name = record.get("name") if isinstance(record, dict) else record
        value = str(name or "").strip()
        if value and value.casefold() not in {item.casefold() for item in names}:
            names.append(value)
    if not names:
        raise MetadataError("Category JSON does not define any category names.")
    return tuple(names)


def load_allowed_tags(path: str | Path) -> tuple[str, ...]:
    document = _read_object(Path(path), "allowed-tags")
    records = document.get("tags")
    if not isinstance(records, list):
        raise MetadataError("Allowed-tags JSON must contain a tags array.")
    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        name = record.get("name") if isinstance(record, dict) else record
        value = str(name or "").strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            names.append(value)
    if len(names) < 5:
        raise MetadataError("Allowed-tags JSON must define at least five distinct tags.")
    return tuple(names)


def discover_assets(root: str | Path, asset_type: str = "hdri") -> list[AssetRecord]:
    scan_root = Path(root).expanduser().absolute()
    if not scan_root.is_dir():
        raise MetadataError("Preview root is not an existing folder.")
    if asset_type not in SUPPORTED_ASSET_TYPES:
        raise MetadataError(f"Unsupported asset type: {asset_type}")

    managed: list[AssetRecord] = []
    managed_roots: list[Path] = []
    provider_by_folder: dict[Path, list[tuple[Path, dict]]] = {}
    for path in _json_paths(scan_root):
        try:
            document = _read_object(path, "metadata")
        except MetadataError:
            continue
        if _is_managed_asset(document, asset_type):
            record = _managed_record(scan_root, path, document, asset_type)
            managed.append(record)
            managed_roots.append(path.parent)
        elif _is_provider_asset(document, asset_type):
            provider_by_folder.setdefault(path.parent, []).append((path, document))

    results = managed
    for folder, candidates in provider_by_folder.items():
        if any(_is_within(folder, managed_root) for managed_root in managed_roots):
            continue
        path, document = candidates[0]
        record = _provider_record(path, document, asset_type)
        if len(candidates) > 1:
            names = ", ".join(item[0].name for item in candidates)
            record = replace(
                record,
                diagnostic=f"Multiple HDRI metadata files found in this folder: {names}.",
            )
        results.append(record)
    return sorted(results, key=lambda item: (item.name.casefold(), str(item.folder).casefold()))


def choose_preview(record: AssetRecord, path: str | Path) -> AssetRecord:
    preview = Path(path).absolute()
    if preview not in record.preview_candidates:
        raise MetadataError("Selected preview is not one of this asset's discovered images.")
    diagnostic = record.diagnostic
    if diagnostic.startswith(("Multiple rendered previews", "No JPG")):
        diagnostic = ""
    return replace(record, preview_path=preview, diagnostic=diagnostic)


def merge_tags(existing: Iterable[str], suggested: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item in (*tuple(existing), *tuple(suggested)):
        value = str(item).strip()
        folded = value.casefold()
        if value and folded not in seen:
            seen.add(folded)
            values.append(value)
    return tuple(values)


def apply_classification(
    record: AssetRecord,
    category: str,
    suggested_tags: Iterable[str],
    *,
    preview_root: str | Path,
    backup_stamp: str | None = None,
) -> AssetRecord:
    if record.diagnostic:
        raise MetadataError(record.diagnostic)
    merged = merge_tags(record.tags, suggested_tags)
    if record.metadata_kind == "provider":
        document = _read_object(record.metadata_path, "provider metadata")
        if not _is_provider_asset(document, record.asset_type):
            raise MetadataError(
                "Provider metadata changed or no longer describes the selected asset type."
            )
        backup_root = Path(preview_root).absolute() / ".hdri-tagger-backups"
        stamp = backup_stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        relative = _safe_relative(record.metadata_path, Path(preview_root).absolute())
        backup_path = backup_root / stamp / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if not backup_path.exists():
            shutil.copy2(record.metadata_path, backup_path)
        document["categories"] = [category]
        document["tags"] = list(merged)
        _atomic_json(record.metadata_path, document)
        return replace(record, category=category, tags=merged)

    if not record.library_root or not record.asset_id:
        raise MetadataError("Managed HDRI is not inside a recognizable ShotBox library.")
    from universal_asset_library.library import AssetMetadataUpdate, LibraryRepository

    document = _read_object(record.metadata_path, "managed metadata")
    repository = LibraryRepository(record.library_root)
    updated = repository.update_asset_metadata(
        record.asset_id,
        AssetMetadataUpdate(
            name=str(document.get("name", record.name)),
            category=category,
            tags=merged,
            author=str(document.get("author", "")),
            description=str(document.get("description", "")),
            physical_size=str(document.get("physical_size", "")),
        ),
    )
    new_manifest = _managed_manifest_path(
        updated.asset_dir, record.asset_id, record.metadata_path.name
    )
    previews = tuple(path for path in (updated.thumbnail_path, updated.hero_path) if path)
    return replace(
        record,
        folder=updated.asset_dir,
        metadata_path=new_manifest,
        category=updated.category,
        tags=updated.tags,
        preview_candidates=previews,
        preview_path=updated.thumbnail_path or updated.hero_path,
    )


def _managed_record(
    scan_root: Path, path: Path, document: dict, asset_type: str
) -> AssetRecord:
    folder = path.parent
    explicit: list[Path] = []
    previews = document.get("previews")
    if isinstance(previews, dict):
        for key in ("thumbnail", "hero"):
            value = str(previews.get(key, "") or "")
            candidate = folder / value
            if value and candidate.is_file() and candidate.suffix.casefold() in IMAGE_EXTENSIONS:
                explicit.append(candidate.absolute())
    preview = document.get("preview")
    if isinstance(preview, dict):
        value = str(preview.get("thumbnail", "") or "")
        candidate = folder / value
        if value and candidate.is_file() and candidate.suffix.casefold() in IMAGE_EXTENSIONS:
            explicit.append(candidate.absolute())
    candidates, selected = _preview_selection(folder, explicit)
    root = _find_library_root(folder)
    diagnostic = ""
    if not root:
        diagnostic = "Managed asset.json is not inside a recognizable ShotBox library."
    elif not selected:
        diagnostic = _preview_diagnostic(candidates)
    return AssetRecord(
        name=str(document.get("name") or folder.name),
        folder=folder,
        metadata_path=path,
        metadata_kind="managed",
        category=str(document.get("category", "")),
        tags=_strings(document.get("tags")),
        preview_candidates=candidates,
        preview_path=selected,
        asset_id=str(document.get("id", "")),
        library_root=root,
        diagnostic=diagnostic,
        asset_type=asset_type,
    )


def _provider_record(path: Path, document: dict, asset_type: str) -> AssetRecord:
    folder = path.parent
    candidates, selected = _preview_selection(folder, ())
    categories = _strings(document.get("categories"))
    semantic = document.get("semanticTags")
    semantic_name = (
        str(semantic.get("name", "")).strip() if isinstance(semantic, dict) else ""
    )
    return AssetRecord(
        name=str(document.get("name") or semantic_name or folder.name),
        folder=folder,
        metadata_path=path,
        metadata_kind="provider",
        category=categories[0] if categories else "",
        tags=_strings(document.get("tags")),
        preview_candidates=candidates,
        preview_path=selected,
        diagnostic="" if selected else _preview_diagnostic(candidates),
        asset_type=asset_type,
    )


def _preview_selection(folder: Path, explicit: Iterable[Path]) -> tuple[tuple[Path, ...], Path | None]:
    explicit_unique = _unique_paths(explicit)
    if explicit_unique:
        return explicit_unique, explicit_unique[0]
    images = _unique_paths(
        path.absolute()
        for path in folder.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in IMAGE_EXTENSIONS
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    )
    named = tuple(
        path for path in images
        if any(token in path.stem.casefold() for token in ("preview", "thumbnail", "thumb", "hero", "render"))
    )
    candidates = named or images
    return candidates, candidates[0] if len(candidates) == 1 else None


def _preview_diagnostic(candidates: tuple[Path, ...]) -> str:
    if not candidates:
        return "No JPG, PNG, or WebP rendered preview was found."
    return "Multiple rendered previews found; choose one before analysis."


def _find_library_root(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if (candidate / ".ual" / "library.json").is_file():
            return candidate
    return None


def _json_paths(root: Path):
    for path in root.rglob("*.json"):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            yield path


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MetadataError(f"Could not read {label} JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise MetadataError(f"{label.title()} JSON must contain an object.")
    return value


def _is_managed_asset(document: dict, asset_type: str) -> bool:
    return (
        str(document.get("type", "")).casefold() == asset_type
        and bool(str(document.get("id", "")).strip())
        and isinstance(document.get("tags", []), list)
    )


def _is_provider_asset(document: dict, asset_type: str) -> bool:
    files = document.get("files")
    if asset_type == "hdri":
        return isinstance(files, dict) and isinstance(files.get("hdri"), dict)
    if asset_type == "stock":
        return False
    semantic = document.get("semanticTags")
    if isinstance(semantic, dict) and "id" in document:
        has_models = isinstance(document.get("meshes"), list)
        has_textures = isinstance(document.get("maps"), list) or isinstance(
            document.get("components"), list
        )
        if asset_type == "model":
            return has_models
        if asset_type in {"texture_set", "atlas"} and has_textures:
            is_atlas = _megascans_is_atlas(document, semantic)
            return is_atlas == (asset_type == "atlas")
    if not isinstance(files, dict) or not isinstance(document.get("authors"), dict):
        return False
    if asset_type == "model":
        return document.get("type") == 2
    if asset_type in {"texture_set", "atlas"}:
        return "max_resolution" in document and "hdri" not in files
    return False


def _megascans_is_atlas(document: dict, semantic: dict) -> bool:
    if str(semantic.get("asset_type", "")).strip().casefold() == "atlas":
        return True
    raw_categories = document.get("categories")
    categories = {
        str(value).casefold()
        for value in raw_categories
    } if isinstance(raw_categories, list) else set()
    asset_categories = document.get("assetCategories")
    atlas_tree = isinstance(asset_categories, dict) and "atlas" in {
        str(value).casefold() for value in asset_categories
    }
    records = []
    for key in ("maps", "components"):
        value = document.get(key)
        if isinstance(value, list):
            records.extend(value)
    cutout = any(
        isinstance(item, dict)
        and str(item.get("type") or item.get("name") or "").casefold()
        in {"opacity", "translucency"}
        for item in records
    )
    return cutout and ("atlas" in categories or atlas_tree)


def _managed_manifest_path(asset_dir: Path, asset_id: str, prior_name: str) -> Path:
    direct = asset_dir / prior_name
    if direct.is_file():
        try:
            if str(_read_object(direct, "managed metadata").get("id", "")) == asset_id:
                return direct
        except MetadataError:
            pass
    for candidate in asset_dir.glob("*.json"):
        try:
            if str(_read_object(candidate, "managed metadata").get("id", "")) == asset_id:
                return candidate
        except MetadataError:
            continue
    return asset_dir / "asset.json"


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return tuple(result)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_relative(path: Path, root: Path) -> Path:
    try:
        return path.absolute().relative_to(root)
    except ValueError as error:
        raise MetadataError("Metadata path is outside the selected preview root.") from error


def _atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_name = handle.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
