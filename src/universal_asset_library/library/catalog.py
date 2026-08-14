from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

from universal_asset_library.domain import (
    LibraryExtraFile,
    LibraryHdriAsset,
    LibraryHdriFile,
    LibraryHdriVariant,
    LibraryMap,
    LibraryModelAsset,
    LibraryModelFile,
    LibraryModelTextureSet,
    LibraryProviderPackage,
    LibraryProviderPackageFile,
    LibraryResolution,
    LibraryStockAsset,
    LibraryStockMediaInfo,
    LibraryTextureAsset,
    LibraryUsdDerivative,
    LibraryVdbAsset,
    LibraryVdbFile,
    LibraryVdbVariant,
)


CATALOG_SCHEMA_VERSION = 3
AssetRecord = LibraryTextureAsset | LibraryHdriAsset | LibraryModelAsset | LibraryStockAsset | LibraryVdbAsset

_DATACLASSES = {
    value.__name__: value
    for value in (
        LibraryExtraFile,
        LibraryHdriAsset,
        LibraryHdriFile,
        LibraryHdriVariant,
        LibraryMap,
        LibraryModelAsset,
        LibraryModelFile,
        LibraryModelTextureSet,
        LibraryProviderPackage,
        LibraryProviderPackageFile,
        LibraryResolution,
        LibraryStockAsset,
        LibraryStockMediaInfo,
        LibraryTextureAsset,
        LibraryUsdDerivative,
        LibraryVdbAsset,
        LibraryVdbFile,
        LibraryVdbVariant,
    )
}


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    asset: AssetRecord
    manifest_path: Path
    manifest_size: int
    manifest_mtime_ns: int

    @classmethod
    def from_manifest(cls, asset: AssetRecord, manifest_path: str | Path) -> "CatalogRecord":
        path = Path(manifest_path).absolute()
        stat = path.stat()
        return cls(asset, path, stat.st_size, stat.st_mtime_ns)


def encode_asset(asset: AssetRecord) -> str:
    """Encode an allow-listed asset dataclass without executable object data."""
    if type(asset).__name__ not in _DATACLASSES:
        raise TypeError(f"Unsupported catalog asset: {type(asset).__name__}")
    return json.dumps(_encode_value(asset), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def decode_asset(payload: str) -> AssetRecord:
    try:
        value = _decode_value(json.loads(payload))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise CatalogError(f"Invalid catalog asset payload: {error}") from error
    if not isinstance(value, (LibraryTextureAsset, LibraryHdriAsset, LibraryModelAsset, LibraryStockAsset, LibraryVdbAsset)):
        raise CatalogError("Catalog payload does not contain an asset")
    return value


def _encode_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in _DATACLASSES:
            raise TypeError(f"Unsupported catalog dataclass: {name}")
        return {
            "$type": "dataclass",
            "name": name,
            "fields": {field.name: _encode_value(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Path):
        return {"$type": "path", "value": str(value)}
    if isinstance(value, tuple):
        return {"$type": "tuple", "items": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return {"$type": "list", "items": [_encode_value(item) for item in value]}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Catalog dictionaries must have string keys")
        return {
            "$type": "dict",
            "items": [[key, _encode_value(item)] for key, item in value.items()],
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported catalog value: {type(value).__name__}")


def _decode_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("$type"), str):
        raise TypeError("Expected a tagged catalog value")
    kind = value["$type"]
    if kind == "path":
        if set(value) != {"$type", "value"} or not isinstance(value["value"], str):
            raise TypeError("Invalid path value")
        return Path(value["value"])
    if kind in {"tuple", "list"}:
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise TypeError(f"Invalid {kind} value")
        items = [_decode_value(item) for item in value["items"]]
        return tuple(items) if kind == "tuple" else items
    if kind == "dict":
        if set(value) != {"$type", "items"} or not isinstance(value["items"], list):
            raise TypeError("Invalid dictionary value")
        result: dict[str, Any] = {}
        for item in value["items"]:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise TypeError("Invalid dictionary item")
            if item[0] in result:
                raise ValueError("Duplicate dictionary key")
            result[item[0]] = _decode_value(item[1])
        return result
    if kind == "dataclass":
        if set(value) != {"$type", "name", "fields"}:
            raise TypeError("Invalid dataclass value")
        cls = _DATACLASSES.get(value["name"])
        values = value["fields"]
        if cls is None or not isinstance(values, dict):
            raise TypeError("Unsupported catalog dataclass")
        expected = {field.name for field in fields(cls)}
        if set(values) != expected:
            raise TypeError(f"Invalid fields for {cls.__name__}")
        return cls(**{key: _decode_value(item) for key, item in values.items()})
    raise TypeError(f"Unsupported catalog value type: {kind}")


class CatalogIndex:
    """Disposable local SQLite acceleration index for one portable library."""

    def __init__(self, database_path: str | Path, library_key: str) -> None:
        self.database_path = Path(database_path)
        self.library_key = library_key
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_with_recovery()

    @classmethod
    def for_library(
        cls, library_root: str | Path, *, cache_root: str | Path | None = None
    ) -> "CatalogIndex":
        root = Path(library_root).expanduser().absolute()
        identity = _library_identity(root)
        key = f"{identity}\0{os.path.normcase(str(root))}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        parent = Path(cache_root) if cache_root is not None else _default_cache_root()
        return cls(parent / "catalogs" / f"{digest}.sqlite3", key)

    def sections(self) -> dict[str, list[AssetRecord]]:
        result = {name: [] for name in ("texture_set", "atlas", "hdri", "model", "vdb", "stock")}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT asset_type, payload FROM assets ORDER BY name COLLATE NOCASE, asset_id"
            )
            for asset_type, payload in rows:
                result.setdefault(asset_type, []).append(decode_asset(payload))
        return result

    def query_section(self, asset_type: str) -> list[AssetRecord]:
        _validate_asset_type(asset_type)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM assets
                WHERE asset_type = ?
                ORDER BY name COLLATE NOCASE, asset_id
                """,
                (asset_type,),
            )
            return [decode_asset(row[0]) for row in rows]

    def records_for_section(self, asset_type: str) -> dict[Path, CatalogRecord]:
        _validate_asset_type(asset_type)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT manifest_path, manifest_size, manifest_mtime_ns, payload
                FROM assets WHERE asset_type = ?
                """,
                (asset_type,),
            )
            return {
                Path(path): CatalogRecord(decode_asset(payload), Path(path), size, mtime)
                for path, size, mtime, payload in rows
            }

    def records_for_ids(self, asset_ids: Iterable[str]) -> dict[str, CatalogRecord]:
        """Return local manifest hints for asset IDs without scanning the library."""
        requested = tuple(dict.fromkeys(str(asset_id) for asset_id in asset_ids))
        if not requested:
            return {}
        result: dict[str, CatalogRecord] = {}
        with self._connect() as connection:
            for offset in range(0, len(requested), 900):
                chunk = requested[offset:offset + 900]
                placeholders = ",".join("?" for _item in chunk)
                rows = connection.execute(
                    f"""
                    SELECT asset_id, manifest_path, manifest_size,
                           manifest_mtime_ns, payload
                    FROM assets
                    WHERE asset_id IN ({placeholders})
                    """,
                    chunk,
                )
                for asset_id, path, size, mtime, payload in rows:
                    manifest_path = Path(path)
                    result[str(asset_id)] = CatalogRecord(
                        decode_asset(payload),
                        manifest_path,
                        int(size),
                        int(mtime),
                    )
        return result

    def replace_section(self, asset_type: str, records: Iterable[CatalogRecord]) -> None:
        _validate_asset_type(asset_type)
        prepared = [self._record_values(record, expected_type=asset_type) for record in records]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM assets WHERE asset_type = ?", (asset_type,))
            connection.executemany(
                """
                INSERT INTO assets (
                    asset_id, asset_type, name, manifest_path, manifest_size,
                    manifest_mtime_ns, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                prepared,
            )

    def upsert(self, record: CatalogRecord) -> None:
        with self.writer() as writer:
            writer.upsert(record)

    def writer(self) -> CatalogWriter:
        """Reuse one local SQLite connection while committing every item."""
        return CatalogWriter(self)

    def remove_asset(self, asset_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))

    def remove_manifest(self, manifest_path: str | Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM assets WHERE manifest_path = ?",
                (str(Path(manifest_path).absolute()),),
            )

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM assets")

    def _record_values(
        self, record: CatalogRecord, *, expected_type: str | None = None
    ) -> tuple[str, str, str, str, int, int, str]:
        asset_type = record.asset.asset_type
        _validate_asset_type(asset_type)
        if expected_type is not None and asset_type != expected_type:
            raise ValueError(f"Cannot store {asset_type} in the {expected_type} section")
        return (
            record.asset.id,
            asset_type,
            record.asset.name,
            str(record.manifest_path.absolute()),
            int(record.manifest_size),
            int(record.manifest_mtime_ns),
            encode_asset(record.asset),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_with_recovery(self) -> None:
        try:
            self._initialize()
        except (sqlite3.DatabaseError, CatalogError):
            self.database_path.unlink(missing_ok=True)
            self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, CATALOG_SCHEMA_VERSION):
                connection.executescript(
                    "DROP TABLE IF EXISTS assets; DROP TABLE IF EXISTS catalog_metadata;"
                )
                version = 0
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE catalog_metadata (
                        library_key TEXT NOT NULL
                    );
                    CREATE TABLE assets (
                        asset_id TEXT PRIMARY KEY,
                        asset_type TEXT NOT NULL,
                        name TEXT NOT NULL,
                        manifest_path TEXT NOT NULL UNIQUE,
                        manifest_size INTEGER NOT NULL,
                        manifest_mtime_ns INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX assets_by_section_name
                    ON assets(asset_type, name COLLATE NOCASE, asset_id);
                    """
                )
                connection.execute(
                    "INSERT INTO catalog_metadata(library_key) VALUES (?)",
                    (self.library_key,),
                )
                connection.execute(f"PRAGMA user_version = {CATALOG_SCHEMA_VERSION}")
            row = connection.execute(
                "SELECT library_key FROM catalog_metadata LIMIT 1"
            ).fetchone()
            if row is None or row[0] != self.library_key:
                raise CatalogError("Catalog belongs to a different library")
            # Decode once on open so damaged payloads trigger a disposable rebuild.
            for (payload,) in connection.execute("SELECT payload FROM assets"):
                decode_asset(payload)


class CatalogWriter:
    def __init__(self, index: CatalogIndex) -> None:
        self.index = index
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> CatalogWriter:
        self.connection = self.index._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()

    def upsert(self, record: CatalogRecord) -> None:
        if self.connection is None:
            raise RuntimeError("Catalog writer is not active.")
        values = self.index._record_values(record)
        self.connection.execute(
            """
            INSERT INTO assets (
                asset_id, asset_type, name, manifest_path, manifest_size,
                manifest_mtime_ns, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                asset_type=excluded.asset_type,
                name=excluded.name,
                manifest_path=excluded.manifest_path,
                manifest_size=excluded.manifest_size,
                manifest_mtime_ns=excluded.manifest_mtime_ns,
                payload=excluded.payload
            """,
            values,
        )
        self.connection.commit()


def _validate_asset_type(asset_type: str) -> None:
    if asset_type not in {"texture_set", "atlas", "hdri", "model", "vdb", "stock"}:
        raise ValueError(f"Unsupported asset type: {asset_type}")


def _library_identity(root: Path) -> str:
    try:
        document = json.loads((root / ".ual" / "library.json").read_text(encoding="utf-8"))
        identity = str(document.get("id", "")).strip()
    except (OSError, ValueError, TypeError):
        identity = ""
    return identity or str(root)


def _default_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "ShotBox"
