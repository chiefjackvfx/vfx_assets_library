from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from universal_asset_library.importer.adapters import normalize_channel, resolution_label


API_ROOT = "https://api.polyhaven.com"
DEFAULT_DOWNLOAD_HOSTS = frozenset({"dl.polyhaven.org"})
USER_AGENT = "ShotBoxAssets/0.1 (+https://polyhaven.com)"
FORMAT_PRIORITY = {"exr": 0, "tif": 1, "tiff": 1, "png": 2, "webp": 3, "jpg": 4, "jpeg": 4}


class PolyHavenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PolyHavenRemoteFile:
    source_path: str
    url: str
    size: int
    md5: str
    role: str = "dependency"
    channel: str = ""
    file_format: str = ""
    normal_convention: str = ""
    packed_channels: dict[str, str] = field(default_factory=dict)
    preferred: bool = False


@dataclass(frozen=True, slots=True)
class PolyHavenDownloadPlan:
    asset_id: str
    asset_type: str
    slug: str
    kind: str
    resolution: str
    files: tuple[PolyHavenRemoteFile, ...]
    entry_source_path: str = ""
    manifest_updated_at: str = ""
    manifest_fingerprint: str = ""
    catalog: dict = field(default_factory=dict)
    from_cache: bool = False
    asset_name: str = ""

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True, slots=True)
class PolyHavenPackage:
    kind: str
    resolution: str
    entry_path: str
    files: tuple[dict, ...]
    downloaded_at: str


@dataclass(frozen=True, slots=True)
class PolyHavenDownloadResult:
    asset: object
    kind: str
    resolution: str
    downloaded_bytes: int
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class PolyHavenOptions:
    slug: str
    map_resolutions: tuple[str, ...]
    materialx_resolutions: tuple[str, ...]
    usd_resolutions: tuple[str, ...]
    hdri_resolutions: tuple[str, ...]
    catalog: dict
    from_cache: bool = False


class PolyHavenClient:
    def __init__(
        self,
        *,
        api_root: str = API_ROOT,
        user_agent: str = USER_AGENT,
        timeout: float = 20.0,
        download_hosts: frozenset[str] = DEFAULT_DOWNLOAD_HOSTS,
    ) -> None:
        self.api_root = api_root.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.download_hosts = download_hosts
        self._cache: dict[tuple[str, str], dict] = {}

    def fetch_catalog(self, slug: str, expected_type: int) -> dict:
        slug = _safe_slug(slug)
        info = self._json(f"/info/{slug}")
        actual_type = info.get("type")
        if actual_type is not None and int(actual_type) != expected_type:
            raise PolyHavenError("The Poly Haven asset type does not match this library asset.")
        catalog = self._json(f"/files/{slug}")
        if not isinstance(catalog, dict):
            raise PolyHavenError("Poly Haven returned an invalid file catalog.")
        return catalog

    def _json(self, endpoint: str) -> dict:
        key = (self.api_root, endpoint)
        if key in self._cache:
            return self._cache[key]
        url = self.api_root + endpoint
        response = self._open(url)
        final = urlparse(response.geturl())
        origin = urlparse(self.api_root)
        if final.scheme != "https" or final.hostname != origin.hostname:
            response.close()
            raise PolyHavenError("Poly Haven API redirected to an untrusted host.")
        try:
            payload = response.read(10 * 1024 * 1024 + 1)
        finally:
            response.close()
        if len(payload) > 10 * 1024 * 1024:
            raise PolyHavenError("Poly Haven returned an oversized JSON response.")
        try:
            document = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PolyHavenError("Poly Haven returned malformed JSON.") from error
        if not isinstance(document, dict):
            raise PolyHavenError("Poly Haven returned an invalid JSON document.")
        self._cache[key] = document
        return document

    def download(
        self,
        remote: PolyHavenRemoteFile,
        destination: Path,
        *,
        progress: Callable[[int], None] | None = None,
        cancel: Callable[[], None] | None = None,
        retries: int = 2,
    ) -> tuple[int, str]:
        parsed = urlparse(remote.url)
        if parsed.scheme != "https" or parsed.hostname not in self.download_hosts:
            raise PolyHavenError(f"Refusing an untrusted Poly Haven download URL: {remote.url}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            temporary = destination.with_name(destination.name + ".part")
            temporary.unlink(missing_ok=True)
            try:
                response = self._open(remote.url)
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in self.download_hosts:
                    response.close()
                    raise PolyHavenError("Poly Haven download redirected to an untrusted host.")
                md5 = hashlib.md5(usedforsecurity=False)
                sha256 = hashlib.sha256()
                size = 0
                try:
                    with temporary.open("xb") as handle:
                        while True:
                            if cancel:
                                cancel()
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            md5.update(chunk)
                            sha256.update(chunk)
                            size += len(chunk)
                            if progress:
                                progress(len(chunk))
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    response.close()
                if remote.size and size != remote.size:
                    raise PolyHavenError(f"Downloaded size mismatch for {remote.source_path}.")
                if remote.md5 and md5.hexdigest().casefold() != remote.md5.casefold():
                    raise PolyHavenError(f"MD5 verification failed for {remote.source_path}.")
                temporary.replace(destination)
                return size, sha256.hexdigest()
            except Exception as error:
                temporary.unlink(missing_ok=True)
                last_error = error
                if attempt < retries:
                    time.sleep(0.15 * (attempt + 1))
        raise PolyHavenError(str(last_error or "Poly Haven download failed.")) from last_error

    def _open(self, url: str):
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json, application/octet-stream"})
        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as error:
            raise PolyHavenError(f"Poly Haven returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            raise PolyHavenError(f"Could not contact Poly Haven: {error}") from error


def resolve_polyhaven_slug(provider_id: str, metadata_documents: list[dict]) -> str:
    if provider_id.strip():
        return _safe_slug(provider_id)
    candidates: list[str] = []
    for document in metadata_documents:
        for value in _strings(document):
            parsed = urlparse(value)
            path = unquote(parsed.path)
            match = re.search(r"/ph-assets/(?:Textures|Models)/[^/]+/[^/]+/([^/]+)/", path, re.IGNORECASE)
            if match:
                candidates.append(match.group(1))
                continue
            if "polyhaven" in (parsed.hostname or "") and "/asset_img/" in path:
                candidates.append(Path(path).stem)
    unique = {candidate for candidate in candidates if re.fullmatch(r"[A-Za-z0-9_-]+", candidate)}
    if len(unique) != 1:
        raise PolyHavenError("Could not determine one unambiguous Poly Haven asset ID from its metadata.")
    return _safe_slug(unique.pop())


def options_from_catalog(slug: str, catalog: dict, asset_type: str, *, from_cache: bool = False) -> PolyHavenOptions:
    files = catalog.get("files") if isinstance(catalog.get("files"), dict) else catalog
    map_labels: set[str] = set()
    for key, branch in files.items():
        channel, _normal, _packed = normalize_channel(str(key))
        if not channel or not isinstance(branch, dict):
            continue
        map_labels.update(_labels(branch))
    materialx = _labels(files.get("mtlx", {})) if asset_type == "texture_set" else set()
    usd = _labels(files.get("usd", {})) if asset_type == "model" else set()
    hdri = _labels(files.get("hdri", {})) if asset_type == "hdri" else set()
    return PolyHavenOptions(
        slug=slug,
        map_resolutions=tuple(sorted(map_labels, key=_resolution_key)),
        materialx_resolutions=tuple(sorted(materialx, key=_resolution_key)),
        usd_resolutions=tuple(sorted(usd, key=_resolution_key)),
        hdri_resolutions=tuple(sorted(hdri, key=_resolution_key)),
        catalog=files,
        from_cache=from_cache,
    )


def build_download_plan(
    *,
    asset_id: str,
    asset_type: str,
    slug: str,
    kind: str,
    resolution: str,
    catalog: dict,
    manifest_updated_at: str,
    manifest_fingerprint: str,
    preferred_formats: dict[str, str] | None = None,
    from_cache: bool = False,
    asset_name: str = "",
) -> PolyHavenDownloadPlan:
    files_doc = catalog.get("files") if isinstance(catalog.get("files"), dict) else catalog
    label = resolution_label(resolution) or resolution.upper()
    key = label.casefold()
    remotes: list[PolyHavenRemoteFile] = []
    entry = ""
    if kind == "maps":
        for map_name, branch in files_doc.items():
            channel, normal, packed = normalize_channel(str(map_name))
            if not channel or not isinstance(branch, dict):
                continue
            variant = _case_value(branch, key)
            if not isinstance(variant, dict):
                continue
            alternatives: list[PolyHavenRemoteFile] = []
            for format_name, record in variant.items():
                if not _file_record(record):
                    continue
                alternatives.append(_remote(record, Path(urlparse(str(record["url"])).path).name, "map", channel, str(format_name), normal, packed))
            preferred_format = (preferred_formats or {}).get(channel, "").casefold()
            chosen = min(alternatives, key=lambda item: (0 if item.file_format.casefold() == preferred_format else 1, FORMAT_PRIORITY.get(item.file_format.casefold(), 99), item.source_path.casefold()), default=None)
            for item in alternatives:
                remotes.append(PolyHavenRemoteFile(
                    item.source_path, item.url, item.size, item.md5, item.role, item.channel,
                    item.file_format, item.normal_convention, item.packed_channels, item == chosen,
                ))
    elif kind == "hdri":
        branch = files_doc.get("hdri", {})
        variant = _case_value(branch, key) if isinstance(branch, dict) else None
        if not isinstance(variant, dict):
            raise PolyHavenError(f"Poly Haven does not advertise an HDRI at {label}.")
        alternatives: list[PolyHavenRemoteFile] = []
        for format_name, record in variant.items():
            if _file_record(record):
                alternatives.append(_remote(
                    record,
                    Path(unquote(urlparse(str(record["url"])).path)).name,
                    "environment",
                    "Environment",
                    str(format_name),
                ))
        chosen = min(
            alternatives,
            key=lambda item: (0 if item.file_format.casefold() == "exr" else 1, item.file_format.casefold()),
            default=None,
        )
        for item in alternatives:
            remotes.append(PolyHavenRemoteFile(
                item.source_path, item.url, item.size, item.md5, item.role, item.channel,
                item.file_format, item.normal_convention, item.packed_channels, item == chosen,
            ))
    elif kind in {"materialx", "usd"}:
        branch = files_doc.get("mtlx" if kind == "materialx" else "usd", {})
        variant = _case_value(branch, key) if isinstance(branch, dict) else None
        record = _case_value(variant, "mtlx" if kind == "materialx" else "usd") if isinstance(variant, dict) else None
        if not _file_record(record):
            raise PolyHavenError(f"Poly Haven does not advertise a {kind.upper()} package at {label}.")
        entry = Path(urlparse(str(record["url"])).path).name
        remotes.append(_remote(record, entry, "entry"))
        includes = record.get("include", {})
        if includes is not None and not isinstance(includes, dict):
            raise PolyHavenError("Poly Haven returned an invalid package dependency list.")
        for relative, dependency in sorted((includes or {}).items(), key=lambda item: item[0].casefold()):
            safe = _safe_relative(str(relative))
            if not _file_record(dependency):
                raise PolyHavenError(f"Poly Haven returned an invalid dependency: {relative}")
            remotes.append(_remote(dependency, safe, "dependency"))
    else:
        raise PolyHavenError(f"Unsupported Poly Haven download kind: {kind}")
    if not remotes:
        raise PolyHavenError(f"No Poly Haven {kind} files are available at {label}.")
    urls = [item.url for item in remotes]
    if len(urls) != len(set(urls)):
        raise PolyHavenError("Poly Haven returned duplicate files in the download plan.")
    return PolyHavenDownloadPlan(
        asset_id=asset_id,
        asset_type=asset_type,
        slug=slug,
        kind=kind,
        resolution=label,
        files=tuple(remotes),
        entry_source_path=entry,
        manifest_updated_at=manifest_updated_at,
        manifest_fingerprint=manifest_fingerprint,
        catalog=files_doc,
        from_cache=from_cache,
        asset_name=asset_name,
    )


def load_metadata_documents(asset_dir: Path, source_metadata: tuple[str, ...]) -> list[dict]:
    documents: list[dict] = []
    for relative in source_metadata:
        try:
            value = json.loads((asset_dir / relative).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            documents.append(value)
    cached = asset_dir / "metadata" / "polyhaven-files.json"
    if cached.is_file():
        try:
            value = json.loads(cached.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                documents.insert(0, value)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return documents


def cached_catalog(documents: list[dict]) -> dict | None:
    for document in documents:
        files = document.get("files")
        if isinstance(files, dict):
            return files
        if any(key in document for key in ("Diffuse", "Rough", "usd", "mtlx")):
            return document
    return None


def _remote(record: dict, source_path: str, role: str, channel: str = "", file_format: str = "", normal: str = "", packed: dict[str, str] | None = None) -> PolyHavenRemoteFile:
    return PolyHavenRemoteFile(
        _safe_relative(source_path), str(record["url"]), int(record.get("size", 0) or 0),
        str(record.get("md5", "")), role, channel,
        (file_format or Path(source_path).suffix.lstrip(".")).casefold(), normal, dict(packed or {}), False,
    )


def _file_record(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("url"), str) and bool(value.get("url"))


def _case_value(document: dict, key: str):
    return next((value for name, value in document.items() if str(name).casefold() == key.casefold()), None)


def _labels(branch) -> set[str]:
    if not isinstance(branch, dict):
        return set()
    return {resolution_label(str(value)) or str(value).upper() for value in branch if str(value).casefold().endswith("k")}


def _resolution_key(label: str) -> tuple[int, str]:
    digits = "".join(char for char in label if char.isdigit())
    return (int(digits) if digits else 999999, label.casefold())


def _safe_slug(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise PolyHavenError("The Poly Haven asset ID is invalid.")
    return value


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise PolyHavenError(f"Unsafe Poly Haven package path: {value}")
    return path.as_posix()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
