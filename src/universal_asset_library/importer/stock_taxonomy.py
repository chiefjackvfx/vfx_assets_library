from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import unicodedata


SCHEMA_VERSION = 1
DEFAULTS_VERSION = 2
CATEGORY_FILENAME = "stock_categories.json"
TAG_FILENAME = "stock_tags.json"
SPECIALIST_CATEGORIES = {"Lens", "Magic", "Motion Graphics"}
RELATED_TAGS = {
    "rain": ("weather",),
    "snow": ("weather",),
}


@dataclass(frozen=True, slots=True)
class StockCategoryRule:
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StockTagRule:
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StockTaxonomy:
    categories: tuple[StockCategoryRule, ...]
    tags: tuple[StockTagRule, ...]
    stop_words: tuple[str, ...] = ()

    @property
    def category_names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self.categories)

    @property
    def tag_names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self.tags)


@dataclass(frozen=True, slots=True)
class StockClassification:
    category: str
    tags: tuple[str, ...]
    evidence: tuple[str, ...]


DEFAULT_CATEGORY_RULES = (
    StockCategoryRule("Atmospheres", ("atmosphere", "atmospheres", "snow", "snowfall")),
    StockCategoryRule("Blood", ("blood",)),
    StockCategoryRule("Ballistics", ("ballistic", "ballistics", "bullet", "bullets", "muzzle flash", "muzzle flashes", "shell", "shells")),
    StockCategoryRule("Debris", ("debris",)),
    StockCategoryRule("Destruction", ("destruction", "large scale", "ground crack", "ground cracks")),
    StockCategoryRule("Dust", ("dust", "dust element", "dust elements", "dirt charge", "dirt charges")),
    StockCategoryRule("Electricity", ("electricity", "electric")),
    StockCategoryRule("Explosions", ("explosion", "explosions", "blast", "blasts", "charge", "charges", "fireball", "fireballs")),
    StockCategoryRule("Fire", ("fire", "flame", "flames", "torch")),
    StockCategoryRule("Glass", ("glass", "windshield", "windshields")),
    StockCategoryRule("Impacts", ("impact", "impacts", "hit", "hits", "couch hit", "couch hits", "wall hit", "wall hits", "particle hit", "particle hits", "powder hit", "powder hits")),
    StockCategoryRule("Lasers", ("laser", "lasers")),
    StockCategoryRule("Lens", ("lens", "lens effect", "lens effects", "bokeh")),
    StockCategoryRule("Magic", ("magic", "magical", "spell", "spells")),
    StockCategoryRule("Meteors", ("meteor", "meteors")),
    StockCategoryRule("Motion Graphics", ("motion graphic", "motion graphics", "mograph", "graphic element", "graphic elements", "chaotic", "duality", "reveal")),
    StockCategoryRule("Particles", ("particle", "particles")),
    StockCategoryRule("Smoke", ("smoke", "smoke charge", "smoke charges")),
    StockCategoryRule("Sparks", ("spark", "sparks", "welding spark", "welding sparks")),
    StockCategoryRule("Water", ("water", "wet charge", "wet charges", "rain")),
    StockCategoryRule("Miscellaneous", ("misc", "miscellaneous")),
    StockCategoryRule("Uncategorized", ("uncategorized",)),
)


def _tag(name: str, *aliases: str) -> StockTagRule:
    return StockTagRule(name, tuple(dict.fromkeys((name, *aliases))))


DEFAULT_TAG_RULES = (
    _tag("45-caliber", "45cal", "45 cal", "45 caliber"),
    _tag("8mm", "8 mm"),
    _tag("add-blend", "add mode", "add blend"),
    _tag("at-camera", "atcam", "at cam", "at camera"),
    _tag("atmosphere", "atmospheres"),
    _tag("automatic"),
    _tag("big"),
    _tag("blast", "blasts"),
    _tag("blood"),
    _tag("bokeh"),
    _tag("body-layer", "body"),
    _tag("bouncing", "bounce"),
    _tag("burst", "bursts"),
    _tag("bullet", "bullets"),
    _tag("car"),
    _tag("ceiling"),
    _tag("cement"),
    _tag("charge", "charges"),
    _tag("close", "closeup", "close up"),
    _tag("collapse", "collapsing"),
    _tag("cork"),
    _tag("couch"),
    _tag("crack", "cracks"),
    _tag("chaotic"),
    _tag("dark"),
    _tag("day"),
    _tag("debris"),
    _tag("destruction"),
    _tag("dirt"),
    _tag("droplets", "drop", "drops"),
    _tag("dust"),
    _tag("duality"),
    _tag("electricity", "electric"),
    _tag("explosion", "explosions"),
    _tag("falling", "fall"),
    _tag("fire"),
    _tag("fireball", "fire ball"),
    _tag("firecracker", "fire cracker"),
    _tag("flying", "fllying"),
    _tag("foam"),
    _tag("front"),
    _tag("ground"),
    _tag("glass"),
    _tag("guard-layer", "guard"),
    _tag("hit", "hits"),
    _tag("large-scale", "large scale"),
    _tag("laser", "lasers"),
    _tag("lens"),
    _tag("light-layer", "light"),
    _tag("lightbulb", "light bulb"),
    _tag("magic", "magical"),
    _tag("meteor", "meteors"),
    _tag("motion-graphics", "motion graphic", "motion graphics", "mograph"),
    _tag("muzzle-flash", "muzzle flash", "muzzle flashes"),
    _tag("out-of-focus", "out of focus", "out-of-focus"),
    _tag("particle", "particles"),
    _tag("powder"),
    _tag("puffy"),
    _tag("rain"),
    _tag("rock", "rocks"),
    _tag("reveal"),
    _tag("screen-blend", "screen mode", "screen blend"),
    _tag("shell", "shells"),
    _tag("shatter", "shattered", "smash", "smashed"),
    _tag("side"),
    _tag("slow-motion", "slow", "slow motion"),
    _tag("smoke"),
    _tag("snow"),
    _tag("spark", "sparks"),
    _tag("splat", "splats"),
    _tag("splatter", "splatters"),
    _tag("splash", "splashes"),
    _tag("squirt", "squirts"),
    _tag("straight"),
    _tag("stream", "streams"),
    _tag("suppressed"),
    _tag("torch"),
    _tag("turbulent"),
    _tag("wall"),
    _tag("water"),
    _tag("wave", "waves"),
    _tag("weather"),
    _tag("welding", "weld"),
    _tag("wet"),
    _tag("wide"),
    _tag("windshield", "windshields"),
    _tag("window", "windows"),
    _tag("windy", "wind"),
    _tag("wisp", "wisps"),
    _tag("wood"),
)

DEFAULT_STOP_WORDS = (
    "effect", "effects", "layer", "layers", "mode", "new folder",
    "preview", "previews", "proxy", "proxies", "stock", "thumbnail",
    "thumbnails", "video", "videos",
)


def default_stock_taxonomy() -> StockTaxonomy:
    return StockTaxonomy(DEFAULT_CATEGORY_RULES, DEFAULT_TAG_RULES, DEFAULT_STOP_WORDS)


class StockTaxonomyStore:
    def __init__(self, library_root: str | Path) -> None:
        self.library_root = Path(library_root).expanduser().absolute()
        self.control_root = self.library_root / ".ual"
        self.categories_path = self.control_root / CATEGORY_FILENAME
        self.tags_path = self.control_root / TAG_FILENAME
        self.last_warnings: list[str] = []

    def ensure_defaults(self) -> StockTaxonomy:
        self.last_warnings = []
        try:
            self.control_root.mkdir(parents=True, exist_ok=True)
            _create_json_once(self.categories_path, _categories_document(default_stock_taxonomy()))
            _create_json_once(self.tags_path, _tags_document(default_stock_taxonomy()))
            self._upgrade_defaults()
        except OSError as error:
            self.last_warnings.append(
                f"Could not initialize the Stock taxonomy in {self.control_root}: {error}. "
                "Built-in defaults are being used."
            )
        return self.load()

    def load(self) -> StockTaxonomy:
        previous = list(self.last_warnings)
        self.last_warnings = previous
        defaults = default_stock_taxonomy()
        categories = defaults.categories
        tags = defaults.tags
        stop_words = defaults.stop_words
        try:
            categories = _merge_rules(
                defaults.categories, _parse_categories(_read_json(self.categories_path))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.last_warnings.append(
                f"Could not load {self.categories_path.name}: {error}. Built-in categories are being used."
            )
        try:
            custom_tags, custom_stop_words = _parse_tags(_read_json(self.tags_path))
            tags = _merge_rules(defaults.tags, custom_tags)
            stop_words = tuple(dict.fromkeys((*defaults.stop_words, *custom_stop_words)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.last_warnings.append(
                f"Could not load {self.tags_path.name}: {error}. Built-in tags are being used."
            )
        return StockTaxonomy(categories, tags, stop_words)

    def _upgrade_defaults(self) -> None:
        defaults = default_stock_taxonomy()
        try:
            category_document = _read_json(self.categories_path)
            categories = _merge_rules(defaults.categories, _parse_categories(category_document))
            upgraded_categories = _categories_document(
                StockTaxonomy(categories, defaults.tags, defaults.stop_words)
            )
            if category_document != upgraded_categories:
                _atomic_replace_json(self.categories_path, upgraded_categories)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.last_warnings.append(
                f"Could not update {self.categories_path.name} with new defaults: {error}."
            )
        try:
            tag_document = _read_json(self.tags_path)
            custom_tags, custom_stop_words = _parse_tags(tag_document)
            tags = _merge_rules(defaults.tags, custom_tags)
            stop_words = tuple(dict.fromkeys((*defaults.stop_words, *custom_stop_words)))
            upgraded_tags = _tags_document(StockTaxonomy(defaults.categories, tags, stop_words))
            if tag_document != upgraded_tags:
                _atomic_replace_json(self.tags_path, upgraded_tags)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.last_warnings.append(
                f"Could not update {self.tags_path.name} with new defaults: {error}."
            )


def classify_stock_path(relative_path: str | Path, taxonomy: StockTaxonomy) -> StockClassification:
    path = Path(relative_path)
    folder_match: StockCategoryRule | None = None
    folder_value = ""
    for part in reversed(path.parts[:-1]):
        normalized = normalize_taxonomy_text(part)
        if not normalized or normalized in {_normalize(value) for value in taxonomy.stop_words}:
            continue
        match = _best_rule(normalized, taxonomy.categories)
        if match:
            folder_match = match
            folder_value = part
            break

    stem = path.stem
    normalized_stem = normalize_taxonomy_text(stem)
    filename_matches = _matching_rules(normalized_stem, taxonomy.categories)
    specialist = next((rule for rule in filename_matches if rule.name in SPECIALIST_CATEGORIES), None)
    filename_match = filename_matches[0] if filename_matches else None
    categories: list[str] = []
    evidence: list[str] = []
    if specialist:
        categories.append(specialist.name)
        evidence.append(f'Filename “{stem}” → {specialist.name}')
        if folder_match and folder_match.name != specialist.name:
            categories.append(folder_match.name)
            evidence.append(f'Folder “{folder_value}” → {folder_match.name}')
    elif folder_match:
        categories.append(folder_match.name)
        evidence.append(f'Folder “{folder_value}” → {folder_match.name}')
    elif filename_match:
        categories.append(filename_match.name)
        evidence.append(f'Filename “{stem}” → {filename_match.name}')
    else:
        categories.append("Miscellaneous")
        evidence.append("No category rule matched → Miscellaneous")

    source_text = " ".join((*path.parts[:-1], stem))
    normalized_source = normalize_taxonomy_text(source_text)
    matched_tags = _matching_rules(normalized_source, taxonomy.tags)
    category_equivalents = {
        equivalent
        for category in categories
        for equivalent in _category_equivalents(category)
    }
    tag_names = {
        rule.name
        for rule in matched_tags
        if _normalize(rule.name) not in category_equivalents
    }
    for tag in tuple(tag_names):
        tag_names.update(RELATED_TAGS.get(tag, ()))
    tags = sorted(tag_names, key=str.casefold)
    if tags:
        evidence.append(f"Path and filename tags → {', '.join(tags)}")
    primary = categories[0] if categories else "Miscellaneous"
    secondary_tags = tuple(
        value.casefold()
        for value in categories[1:]
        if value.casefold() not in {"surface", primary.casefold()}
    )
    return StockClassification(
        primary,
        tuple(dict.fromkeys((*tags, *secondary_tags))),
        tuple(evidence),
    )


def normalize_taxonomy_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", value)
    value = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", value)
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _best_rule(value: str, rules) -> StockCategoryRule | StockTagRule | None:
    matches = _matching_rules(value, rules)
    return matches[0] if matches else None


def _matching_rules(value: str, rules) -> list:
    matches: list[tuple[int, int, object]] = []
    padded = f" {value} "
    for index, rule in enumerate(rules):
        lengths = [
            len(normalized.split())
            for alias in (rule.name, *rule.aliases)
            if (normalized := _normalize(alias)) and f" {normalized} " in padded
        ]
        if lengths:
            matches.append((max(lengths), -index, rule))
    matches.sort(key=lambda item: (-item[0], -item[1]))
    return [item[2] for item in matches]


def _normalize(value: str) -> str:
    return normalize_taxonomy_text(value)


def _category_equivalents(value: str) -> set[str]:
    normalized = _normalize(value)
    equivalents = {normalized}
    words = normalized.split()
    if words and words[-1].endswith("s") and len(words[-1]) > 3:
        equivalents.add(" ".join((*words[:-1], words[-1][:-1])))
    return equivalents


def _categories_document(taxonomy: StockTaxonomy) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "defaults_version": DEFAULTS_VERSION,
        "asset_type": "stock",
        "categories": [
            {"name": rule.name, "aliases": list(rule.aliases)}
            for rule in taxonomy.categories
        ],
    }


def _tags_document(taxonomy: StockTaxonomy) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "defaults_version": DEFAULTS_VERSION,
        "asset_type": "stock",
        "stop_words": list(taxonomy.stop_words),
        "tags": [
            {"name": rule.name, "aliases": list(rule.aliases)}
            for rule in taxonomy.tags
        ],
    }


def _read_json(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("the document root must be an object")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("asset_type") != "stock":
        raise ValueError("unsupported taxonomy schema or asset type")
    return document


def _parse_categories(document: dict) -> tuple[StockCategoryRule, ...]:
    rules = _parse_rules(document.get("categories"), StockCategoryRule, "category")
    names = {rule.name.casefold() for rule in rules}
    if "uncategorized" not in names or "miscellaneous" not in names:
        raise ValueError("categories must include Miscellaneous and Uncategorized")
    return rules


def _parse_tags(document: dict) -> tuple[tuple[StockTagRule, ...], tuple[str, ...]]:
    rules = _parse_rules(document.get("tags"), StockTagRule, "tag")
    stop_words_value = document.get("stop_words", [])
    if not isinstance(stop_words_value, list) or not all(isinstance(value, str) for value in stop_words_value):
        raise ValueError("stop_words must be an array of strings")
    return rules, tuple(dict.fromkeys(value.strip() for value in stop_words_value if value.strip()))


def _parse_rules(values, rule_type, label: str):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} rules must be a non-empty array")
    names: set[str] = set()
    aliases: dict[str, str] = {}
    rules = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"each {label} rule must be an object")
        name = str(value.get("name", "")).strip()
        raw_aliases = value.get("aliases", [])
        if not name or not isinstance(raw_aliases, list) or not all(isinstance(item, str) for item in raw_aliases):
            raise ValueError(f"each {label} needs a name and string alias array")
        normalized_name = _normalize(name)
        if not normalized_name or normalized_name in names:
            raise ValueError(f"duplicate {label} name: {name}")
        names.add(normalized_name)
        cleaned_aliases = tuple(dict.fromkeys(item.strip() for item in raw_aliases if item.strip()))
        for alias in (name, *cleaned_aliases):
            normalized_alias = _normalize(alias)
            owner = aliases.get(normalized_alias)
            if owner and owner != name:
                raise ValueError(f"{label} alias {alias!r} is shared by {owner} and {name}")
            aliases[normalized_alias] = name
        rules.append(rule_type(name, cleaned_aliases))
    return tuple(rules)


def _merge_rules(defaults, custom):
    default_by_name = {_normalize(rule.name): rule for rule in defaults}
    merged = []
    custom_names: set[str] = set()
    for rule in custom:
        key = _normalize(rule.name)
        custom_names.add(key)
        default = default_by_name.get(key)
        if default is None:
            merged.append(rule)
            continue
        aliases = tuple(dict.fromkeys((*default.aliases, *rule.aliases)))
        merged.append(type(rule)(rule.name, aliases))
    merged.extend(rule for rule in defaults if _normalize(rule.name) not in custom_names)
    return tuple(merged)


def _create_json_once(path: Path, document: dict) -> None:
    if path.exists():
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        except OSError:
            if not path.exists():
                os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace_json(path: Path, document: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.upgrade.tmp")
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
