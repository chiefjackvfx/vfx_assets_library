from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from universal_asset_library.domain import (
    ATLAS_CATEGORIES,
    HDRI_CATEGORIES,
    MODEL_CATEGORIES,
    STOCK_CATEGORIES,
    TEXTURE_CATEGORIES,
)


SCHEMA_VERSION = 1
DEFAULTS_VERSION = 1
GENERIC_ICON_ID = "generic-category"

CATEGORY_FILENAMES = {
    "texture_set": "texture_categories.json",
    "atlas": "atlas_categories.json",
    "hdri": "hdri_categories.json",
    "model": "model_categories.json",
    "stock": "stock_categories.json",
}

DEFAULT_CATEGORY_NAMES = {
    "texture_set": TEXTURE_CATEGORIES,
    "atlas": ATLAS_CATEGORIES,
    "hdri": HDRI_CATEGORIES,
    "model": MODEL_CATEGORIES,
    "stock": STOCK_CATEGORIES,
}

CATEGORY_ICON_IDS = {
    "all",
    GENERIC_ICON_ID,
    "architecture", "asphalt", "atmospheres", "ballistics", "bark", "blood", "branches",
    "brick", "characters", "clean", "coal", "concrete", "construction", "creatures",
    "debris", "decals", "decorative", "dirty",
    "destruction", "dust", "electricity", "explosions", "film-fx", "fire", "flowers", "food",
    "floors", "furniture", "fabric", "glass", "grass", "gravel", "ground",
    "ground-cover", "grout", "impacts", "imperfections", "indoor", "industrial",
    "lasers", "leaves", "lens", "magic", "marble", "metal", "meteors",
    "miscellaneous", "moss", "motion-graphics", "nature", "night", "organic", "other",
    "outdoor", "paper", "particles", "plants", "plaster", "props", "river-debris",
    "rock", "rocks", "roof", "roofing", "sand", "sky", "smoke", "snow", "soil",
    "sparks", "stone", "studio", "tile", "tree", "trees", "uncategorized", "urban",
    "vehicles", "water", "wood",
}

ICON_SHAPES = {
    "all": "grid",
    GENERIC_ICON_ID: "tag",
    "architecture": "structure", "asphalt": "terrain", "atmospheres": "cloud",
    "ballistics": "target", "bark": "nature", "blood": "water", "branches": "nature",
    "brick": "material", "characters": "life", "clean": "light", "coal": "terrain",
    "concrete": "material", "construction": "structure", "creatures": "life",
    "debris": "terrain", "decals": "tag", "decorative": "object", "dirty": "material",
    "destruction": "energy", "dust": "cloud",
    "electricity": "energy", "explosions": "energy", "film-fx": "motion",
    "fire": "energy", "flowers": "nature",
    "food": "object", "floors": "material", "furniture": "object", "fabric": "material",
    "glass": "material", "grass": "nature", "gravel": "terrain", "ground": "terrain",
    "ground-cover": "nature", "grout": "material", "impacts": "target",
    "imperfections": "material",
    "indoor": "structure", "industrial": "structure", "lasers": "energy", "leaves": "nature",
    "lens": "target", "magic": "energy", "marble": "material", "metal": "material",
    "meteors": "motion",
    "miscellaneous": "grid", "moss": "nature", "motion-graphics": "motion",
    "nature": "nature", "night": "light", "organic": "nature", "other": "grid",
    "outdoor": "light", "paper": "material", "particles": "motion", "plants": "nature",
    "plaster": "material", "props": "object", "river-debris": "terrain", "rock": "terrain",
    "rocks": "terrain", "roof": "structure", "roofing": "structure", "sand": "terrain",
    "sky": "cloud", "smoke": "cloud", "snow": "cloud", "soil": "terrain",
    "sparks": "energy", "stone": "terrain", "studio": "light", "tile": "material",
    "tree": "nature", "trees": "nature", "uncategorized": "question",
    "urban": "structure", "vehicles": "vehicle", "water": "water", "wood": "nature",
}

DEFAULT_CATEGORY_ALIASES = {
    "texture_set": {
        "Asphalt": ("road", "roads", "tarmac"),
        "Bark": ("tree bark",),
        "Brick": ("bricks",),
        "Coal": ("charcoal",),
        "Construction": ("building material", "building materials"),
        "Debris": ("rubble",),
        "Fabric": ("cloth", "textile", "furniture fabric", "carpet"),
        "Floors": ("floor", "flooring"),
        "Grass": ("lawn", "turf"),
        "Ground": ("dirt", "earth"),
        "Grout": ("mortar",),
        "Imperfections": (
            "imperfection", "finger prints", "fingerprints", "grunge", "scratch",
            "scratches", "stain", "stains",
        ),
        "Marble": ("marbles",),
        "Metal": ("metallic",),
        "Other": ("misc", "miscellaneous",),
        "Paper": ("cardboard",),
        "River Debris": ("river-debris",),
        "Rock": ("rocks", "cliff", "cliffs"),
        "Roofing": ("roof material", "roof materials"),
        "Soil": ("mud",),
        "Stone": ("cobblestone", "granite", "pavestone"),
        "Tile": ("tiles",),
        "Tree": ("trees",),
        "Wood": ("timber", "plank", "planks", "parquet"),
        "Uncategorized": ("unknown",),
    },
}

_ICON_BY_NAME = {
    name.casefold(): name.casefold().replace(" ", "-")
    for names in DEFAULT_CATEGORY_NAMES.values()
    for name in names
}
_ICON_BY_NAME.update({
    "ground cover": "ground-cover",
    "motion graphics": "motion-graphics",
})


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    name: str
    icon: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryCatalog:
    asset_type: str
    categories: tuple[CategoryDefinition, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(category.name for category in self.categories)

    def icon_for(self, name: str) -> str:
        folded = name.casefold()
        for category in self.categories:
            if category.name.casefold() == folded:
                return category.icon
        return GENERIC_ICON_ID

    def canonical_name(self, value: str) -> str | None:
        normalized = _normalized_category_text(value)
        if not normalized:
            return None
        for category in self.categories:
            if normalized in {
                _normalized_category_text(candidate)
                for candidate in (category.name, *category.aliases)
            }:
                return category.name
        return None

    def match_text(self, value: str) -> str | None:
        normalized = _normalized_category_text(value)
        if not normalized:
            return None
        matches: list[tuple[int, str]] = []
        for category in self.categories:
            for candidate in (category.name, *category.aliases):
                token = _normalized_category_text(candidate)
                if token and re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", normalized):
                    matches.append((len(token), category.name))
        return max(matches, default=(0, ""), key=lambda item: item[0])[1] or None

    def ordered_used(self, names) -> tuple[str, ...]:
        actual = {str(name).strip().casefold(): str(name).strip() for name in names if str(name).strip()}
        ordered = [
            actual.pop(category.name.casefold())
            for category in self.categories
            if category.name.casefold() in actual
        ]
        ordered.extend(sorted(actual.values(), key=str.casefold))
        return tuple(ordered)


class CategoryConfigStore:
    def __init__(self, library_root: str | Path) -> None:
        self.library_root = Path(library_root).expanduser().absolute()
        self.control_root = self.library_root / ".ual"
        self.last_warnings: list[str] = []

    def path_for(self, asset_type: str) -> Path:
        return self.control_root / CATEGORY_FILENAMES[_validated_asset_type(asset_type)]

    def ensure_defaults(self) -> None:
        self.last_warnings = []
        try:
            self.control_root.mkdir(parents=True, exist_ok=True)
            for asset_type in ("texture_set", "atlas", "hdri", "model"):
                path = self.path_for(asset_type)
                if not path.exists():
                    _atomic_json(path, _catalog_document(default_category_catalog(asset_type)))
            # Stock owns aliases used by classification, so its existing store remains authoritative.
            from universal_asset_library.importer.stock_taxonomy import StockTaxonomyStore
            stock_store = StockTaxonomyStore(self.library_root)
            stock_store.ensure_defaults()
            self.last_warnings.extend(stock_store.last_warnings)
        except OSError as error:
            self.last_warnings.append(
                f"Could not initialize category configuration in {self.control_root}: {error}. "
                "Built-in categories are being used."
            )

    def load(self, asset_type: str) -> CategoryCatalog:
        asset_type = _validated_asset_type(asset_type)
        path = self.path_for(asset_type)
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            return _parse_catalog(document, asset_type, self.last_warnings)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.last_warnings.append(
                f"Could not load {path.name}: {error}. Built-in categories are being used."
            )
            return default_category_catalog(asset_type)

    def load_all(self) -> dict[str, CategoryCatalog]:
        return {asset_type: self.load(asset_type) for asset_type in CATEGORY_FILENAMES}


def category_icon_id(name: str) -> str:
    return _ICON_BY_NAME.get(name.strip().casefold(), GENERIC_ICON_ID)


def default_category_catalog(asset_type: str) -> CategoryCatalog:
    asset_type = _validated_asset_type(asset_type)
    aliases = DEFAULT_CATEGORY_ALIASES.get(asset_type, {})
    return CategoryCatalog(
        asset_type,
        tuple(
            CategoryDefinition(name, category_icon_id(name), tuple(aliases.get(name, ())))
            for name in DEFAULT_CATEGORY_NAMES[asset_type]
        ),
    )


def _validated_asset_type(asset_type: str) -> str:
    value = str(asset_type)
    if value not in CATEGORY_FILENAMES:
        raise ValueError(f"Unsupported asset type: {value}")
    return value


def _catalog_document(catalog: CategoryCatalog) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "defaults_version": DEFAULTS_VERSION,
        "asset_type": catalog.asset_type,
        "categories": [
            {
                "name": category.name,
                "icon": category.icon,
                "aliases": list(category.aliases),
            }
            for category in catalog.categories
        ],
    }


def _parse_catalog(document, asset_type: str, warnings: list[str]) -> CategoryCatalog:
    if not isinstance(document, dict):
        raise ValueError("the document root must be an object")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("asset_type") != asset_type:
        raise ValueError("unsupported category schema or asset type")
    values = document.get("categories")
    if not isinstance(values, list) or not values:
        raise ValueError("categories must be a non-empty array")
    categories: list[CategoryDefinition] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("each category must be an object")
        name = str(value.get("name", "")).strip()
        aliases = value.get("aliases", [])
        if not name or not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise ValueError("each category needs a name and string alias array")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"duplicate category name: {name}")
        seen.add(folded)
        icon = str(value.get("icon", "")).strip() or category_icon_id(name)
        if icon not in CATEGORY_ICON_IDS:
            warnings.append(
                f"Unknown category icon {icon!r} for {name} in {CATEGORY_FILENAMES[asset_type]}; "
                "the generic icon is being used."
            )
            icon = GENERIC_ICON_ID
        categories.append(CategoryDefinition(
            name,
            icon,
            tuple(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
        ))
    return CategoryCatalog(asset_type, tuple(categories))


def _atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalized_category_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()
