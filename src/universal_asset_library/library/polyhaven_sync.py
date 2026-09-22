"""Catalogue discovery and transactional imports of new Poly Haven assets."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable
from urllib.parse import unquote, urlparse

from universal_asset_library.polyhaven_settings import (
    PolyHavenSyncPreferences,
    polyhaven_download_directory,
)
from universal_asset_library.categories import CategoryConfigStore
from universal_asset_library.importer import (
    scan_hdri_folder,
    scan_model_folder,
    scan_texture_folder,
)
from universal_asset_library.importer.models import ModelPackageSource
from .polyhaven import (
    PolyHavenClient,
    PolyHavenError,
    PolyHavenRemoteFile,
    _file_record,
    _remote,
    _safe_relative,
    _safe_slug,
    build_download_plan,
    load_metadata_documents,
)
from .repository import CancelToken, ImportCancelled, LibraryRepository, SPACE_CUSHION


TYPES = {"hdri": 0, "texture_set": 1, "model": 2}


@dataclass(frozen=True)
class PolyHavenSyncEntry:
    slug: str
    name: str
    asset_type: str
    resolution: str
    formats: str
    files: tuple[PolyHavenRemoteFile, ...]
    metadata: dict
    packages: tuple[ModelPackageSource, ...] = ()
    possible_matches: tuple[str, ...] = ()
    note: str = ""

    @property
    def total_size(self) -> int:
        return sum({item.url: item.size for item in self.files}.values())

    @property
    def staging_size(self) -> int:
        return sum(item.size for item in self.files)


@dataclass
class PolyHavenSyncReview:
    entries: list[PolyHavenSyncEntry] = field(default_factory=list)
    owned: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


@dataclass(frozen=True)
class PolyHavenSyncProgress:
    phase: str
    name: str = ""
    completed: int = 0
    total: int = 0
    bytes_completed: int = 0
    bytes_total: int = 0


@dataclass
class PolyHavenSyncResult:
    imported: list = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


def choose_resolution(labels, requested: str) -> str:
    available = sorted(
        {
            str(label).upper()
            for label in labels
            if re.fullmatch(r"\d+[kK]", str(label))
        },
        key=lambda label: int(label[:-1]),
    )
    if not available:
        raise PolyHavenError("No supported resolution is advertised.")
    if requested == "Highest available":
        return available[-1]
    lower = [label for label in available if int(label[:-1]) <= int(requested[:-1])]
    return lower[-1] if lower else available[0]


def _identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def _url_slugs(documents: list[dict]) -> set[str]:
    found = set()
    for value in _strings(documents):
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        if not any(
            host == domain or host.endswith("." + domain)
            for domain in ("polyhaven.com", "polyhaven.org")
        ):
            continue
        path = unquote(parsed.path)
        match = re.search(r"/a/([\w-]+)(?:/|$)", path)
        if match:
            found.add(match[1])
        elif "/asset_img/" in path:
            found.add(Path(path).stem)
        elif match := re.search(
            r"/ph-assets/(?:Textures|Models)/[^/]+/[^/]+/([^/]+)/", path, re.I
        ):
            found.add(match[1])
        elif "/ph-assets/HDRIs/" in path:
            found.add(re.sub(r"_\d+k$", "", Path(path).stem, flags=re.I))
    return found


def ownership(
    assets, token: CancelToken
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    owned: set[tuple[str, str]] = set()
    possible: dict[tuple[str, str], list[str]] = {}
    for asset in assets:
        token.check()
        if asset.asset_type not in TYPES:
            continue
        provider = _identity(asset.provider)
        known_provider = provider in {
            "polyhaven",
            "hdrihaven",
            "texturehaven",
            "3dmodelhaven",
            "modelhaven",
        }
        if known_provider and asset.provider_id:
            owned.add((asset.asset_type, asset.provider_id.casefold()))
        documents = load_metadata_documents(asset.asset_dir, asset.source_metadata)
        slugs = _url_slugs(documents)
        if len(slugs) == 1:
            owned.add((asset.asset_type, next(iter(slugs)).casefold()))
        hints = {asset.name, asset.asset_dir.name}
        # Original filenames survive managed renaming and help review legacy imports.
        try:
            manifest = json.loads(
                (asset.asset_dir / "asset.json").read_text(encoding="utf-8-sig")
            )

            def originals(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "original_path" and isinstance(child, str):
                            yield child
                        elif isinstance(child, (dict, list)):
                            yield from originals(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from originals(child)

            for original in originals(manifest):
                stem = Path(original).stem
                hints.add(
                    re.sub(
                        r"(?:_(?:diff|rough|nor_gl|nor_dx|disp|ao))?_\d+k$",
                        "",
                        stem,
                        flags=re.I,
                    )
                )
        except (OSError, ValueError):
            pass
        for hint in hints:
            possible.setdefault((asset.asset_type, _identity(hint)), []).append(
                asset.name
            )
    return owned, possible


def plan_entry(
    slug: str, info: dict, catalog: dict, asset_type: str, requested: str
) -> PolyHavenSyncEntry:
    _safe_slug(slug)
    files: list[PolyHavenRemoteFile] = []
    packages = []
    labels = []
    note = ""
    if asset_type == "model":
        for kind in ("usd", "blend"):
            branch = catalog.get(kind, {})
            if not isinstance(branch, dict) or not branch:
                continue
            label = choose_resolution(branch, requested)
            variant = branch.get(label.casefold(), branch.get(label, {}))
            record = variant.get(kind) if isinstance(variant, dict) else None
            if not _file_record(record):
                raise PolyHavenError(f"Invalid {kind.upper()} package.")
            entry = _safe_relative(Path(unquote(urlparse(record["url"]).path)).name)
            files.append(_remote(record, entry, "entry", file_format=kind))
            includes = record.get("include", {})
            if not isinstance(includes, dict):
                raise PolyHavenError("Invalid model dependency list.")
            dependencies = {}
            for relative, dependency in includes.items():
                relative = _safe_relative(relative)
                if not _file_record(dependency):
                    raise PolyHavenError(f"Invalid model dependency: {relative}")
                files.append(_remote(dependency, relative, "dependency"))
                dependencies[relative] = relative
            packages.append(ModelPackageSource(kind, label, entry, dependencies))
            labels.append(label)
        if not packages:
            raise PolyHavenError("No USD or Blender package is available.")
        if len(packages) == 1:
            note = f"Only {packages[0].kind.upper()} is available."
    else:
        kind = "hdri" if asset_type == "hdri" else "maps"
        if kind == "hdri":
            available = catalog.get("hdri", {})
        else:
            from .polyhaven import options_from_catalog

            available = options_from_catalog(slug, catalog, asset_type).map_resolutions
        label = choose_resolution(available, requested)
        plan = build_download_plan(
            asset_id="",
            asset_type=asset_type,
            slug=slug,
            kind=kind,
            resolution=label,
            catalog=catalog,
            manifest_updated_at="",
            manifest_fingerprint="",
        )
        files = [item for item in plan.files if item.preferred]
        labels.append(label)
    unique: dict[str, PolyHavenRemoteFile] = {}
    for item in files:
        path = _safe_relative(unquote(item.source_path))
        if path.casefold() == "info.json":
            raise PolyHavenError("A package file conflicts with its metadata filename.")
        if item.size <= 0:
            raise PolyHavenError(f"No valid download size is advertised for {path}.")
        previous = unique.get(path.casefold())
        if previous and (previous.url, previous.md5) != (item.url, item.md5):
            raise PolyHavenError(f"Packages disagree about dependency {path}.")
        unique[path.casefold()] = item
    if not unique:
        raise PolyHavenError("No supported files are available.")
    metadata = dict(info)
    metadata["files"] = catalog
    metadata["polyhaven_id"] = slug
    metadata["source_url"] = f"https://polyhaven.com/a/{slug}"
    if not metadata.get("categories") and isinstance(metadata.get("category"), str):
        metadata["categories"] = [
            part.strip() for part in metadata["category"].split("/") if part.strip()
        ]
    metadata.setdefault("authors", {})
    metadata.setdefault("max_resolution", [])
    formats = (
        " + ".join(package.kind.upper() for package in packages)
        if packages
        else ", ".join(sorted({item.file_format.upper() for item in unique.values()}))
    )
    return PolyHavenSyncEntry(
        slug,
        str(info.get("name") or slug),
        asset_type,
        ", ".join(dict.fromkeys(labels)),
        formats,
        tuple(unique.values()),
        metadata,
        tuple(packages),
        note=note,
    )


class PolyHavenSyncService:
    def __init__(
        self,
        repository: LibraryRepository,
        *,
        client=None,
        token: CancelToken | None = None,
        default_category: str = "Uncategorized",
        default_model_category: str = "Uncategorized",
    ):
        self.repository = repository
        self.token = token or CancelToken()
        self.client = client or PolyHavenClient(cancel=self.token.check)
        self.default_category = default_category
        self.default_model_category = default_model_category

    def discover(
        self, preferences: PolyHavenSyncPreferences, progress: Callable | None = None
    ) -> PolyHavenSyncReview:
        from dataclasses import replace

        result = PolyHavenSyncReview()
        try:
            self._emit(progress, PolyHavenSyncProgress("Reading library"))
            owned, possible = ownership(self.repository.list_assets(), self.token)
            self._emit(progress, PolyHavenSyncProgress("Fetching Poly Haven catalogue"))
            self.token.check()
            catalog = self.client.fetch_assets()
            selected = preferences.normalized().selected_types()
            ordered = sorted(
                catalog.items(),
                key=lambda item: (-(item[1].get("date_published") or 0), item[0]),
            )
            for index, (slug, info) in enumerate(ordered):
                self.token.check()
                kind = next(
                    (kind for kind in selected if TYPES[kind] == info.get("type")), None
                )
                if not kind:
                    continue
                if (kind, slug.casefold()) in owned:
                    result.owned += 1
                    continue
                self._emit(
                    progress,
                    PolyHavenSyncProgress(
                        "Checking files",
                        str(info.get("name") or slug),
                        index,
                        len(ordered),
                    ),
                )
                try:
                    entry = plan_entry(
                        slug, info, self.client.fetch_files(slug), kind, selected[kind]
                    )
                    matches = set(possible.get((kind, _identity(slug)), []))
                    matches.update(possible.get((kind, _identity(entry.name)), []))
                    result.entries.append(
                        replace(entry, possible_matches=tuple(sorted(matches)))
                    )
                except ImportCancelled:
                    raise
                except Exception as error:
                    result.failed[slug] = str(error)
        except ImportCancelled:
            result.canceled = True
        return result

    def install(
        self, entries: list[PolyHavenSyncEntry], progress: Callable | None = None
    ) -> PolyHavenSyncResult:
        result = PolyHavenSyncResult()
        total = sum(entry.total_size for entry in entries)
        completed = 0
        try:
            self.token.check()
            self.repository.initialize()
            # Download locally before importing into a potentially remote library.
            staging = polyhaven_download_directory()
            owned, _ = ownership(self.repository.list_assets(), self.token)
            for index, entry in enumerate(entries):
                self.token.check()
                if (entry.asset_type, entry.slug.casefold()) in owned:
                    result.skipped.append(f"{entry.name}: already owned")
                    continue
                try:
                    staging.mkdir(parents=True, exist_ok=True)
                    if staging.resolve().is_relative_to(self.repository.root.resolve()):
                        raise PolyHavenError(
                            "The local Downloads folder must be outside the asset library. "
                            "Keep Settings → Library pointed at your main library."
                        )
                    # Source + import copy + dependency compatibility copies, plus remaining assets.
                    remaining = sum(item.staging_size for item in entries[index:])
                    required = remaining + 3 * entry.staging_size + SPACE_CUSHION
                    if shutil.disk_usage(self.repository.root).free < required:
                        raise OSError(
                            "Insufficient free space in the library for imported assets."
                        )
                    if (
                        shutil.disk_usage(staging).free
                        < entry.staging_size + SPACE_CUSHION
                    ):
                        raise OSError(
                            f"Insufficient free space in local Downloads: {staging}"
                        )
                    with tempfile.TemporaryDirectory(
                        prefix="shotbox-polyhaven-", dir=staging
                    ) as temporary:
                        source = Path(temporary) / _safe_slug(entry.slug)
                        source.mkdir()
                        downloaded: dict[str, Path] = {}
                        for remote in entry.files:
                            self.token.check()
                            destination = source / _safe_relative(remote.source_path)
                            if remote.url in downloaded:
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(downloaded[remote.url], destination)
                            else:

                                def advance(count):
                                    nonlocal completed
                                    completed += count
                                    self._emit(
                                        progress,
                                        PolyHavenSyncProgress(
                                            "Downloading",
                                            entry.name,
                                            index,
                                            len(entries),
                                            completed,
                                            total,
                                        ),
                                    )

                                self.client.download(
                                    remote,
                                    destination,
                                    progress=advance,
                                    cancel=self.token.check,
                                )
                                downloaded[remote.url] = destination
                        self.token.check()
                        (source / "info.json").write_text(
                            json.dumps(entry.metadata), encoding="utf-8"
                        )
                        self._emit(
                            progress,
                            PolyHavenSyncProgress(
                                "Scanning",
                                entry.name,
                                index,
                                len(entries),
                                completed,
                                total,
                            ),
                        )
                        if entry.asset_type == "model":
                            scan = scan_model_folder(
                                source,
                                self.default_model_category,
                                cancel_token=self.token,
                            )
                        elif entry.asset_type == "hdri":
                            scan = scan_hdri_folder(source, cancel_token=self.token)
                        else:
                            scan = scan_texture_folder(
                                source,
                                self.default_category,
                                cancel_token=self.token,
                                category_catalog=CategoryConfigStore(
                                    self.repository.root
                                ).load("texture_set"),
                            )
                        self.token.check()
                        if len(scan.materials) != 1:
                            raise PolyHavenError(
                                "Downloaded asset did not produce exactly one import candidate."
                            )
                        candidate = scan.materials[0]
                        candidate.provider = "Poly Haven"
                        candidate.provider_id = entry.slug
                        if entry.packages:
                            candidate.provider_packages = list(entry.packages)
                        # Recheck metadata-derived ownership immediately before repository's locked ID check.
                        current, _ = ownership(
                            self.repository.list_assets(), self.token
                        )
                        if (entry.asset_type, entry.slug.casefold()) in current:
                            result.skipped.append(f"{entry.name}: already owned")
                            owned.update(current)
                            continue

                        def importing(value):
                            self._emit(
                                progress,
                                PolyHavenSyncProgress(
                                    value.phase or "Importing",
                                    entry.name,
                                    index,
                                    len(entries),
                                    completed,
                                    total,
                                ),
                            )

                        imported = self.repository.import_assets(
                            [candidate], cancel_token=self.token, progress=importing
                        )
                        result.imported.extend(imported.imported)
                        result.skipped.extend(imported.skipped)
                        result.failed.update(imported.failed)
                        if imported.imported:
                            owned.add((entry.asset_type, entry.slug.casefold()))
                        if imported.canceled:
                            result.canceled = True
                            break
                except ImportCancelled:
                    raise
                except Exception as error:
                    result.failed[entry.name] = str(error)
        except ImportCancelled:
            result.canceled = True
        except Exception as error:
            result.failed["Sync"] = str(error)
        self._emit(
            progress,
            PolyHavenSyncProgress(
                "Canceled" if result.canceled else "Finished",
                completed=len(entries),
                total=len(entries),
                bytes_completed=completed,
                bytes_total=total,
            ),
        )
        return result

    def _emit(self, callback, value):
        if callback:
            callback(value)
