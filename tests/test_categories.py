from __future__ import annotations

import json

from universal_asset_library.categories import (
    GENERIC_ICON_ID,
    CategoryCatalog,
    CategoryConfigStore,
    CategoryDefinition,
    default_category_catalog,
)
from universal_asset_library.importer.scanner import _select_category


def test_category_store_creates_all_portable_files_without_replacing_edits(tmp_path) -> None:
    store = CategoryConfigStore(tmp_path)
    store.ensure_defaults()

    expected = {
        "texture_categories.json",
        "atlas_categories.json",
        "hdri_categories.json",
        "model_categories.json",
        "stock_categories.json",
    }
    assert expected.issubset({path.name for path in (tmp_path / ".ual").iterdir()})

    path = store.path_for("texture_set")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["categories"].insert(0, {
        "name": "Custom Surface",
        "icon": "material",
        "aliases": [],
    })
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()

    store.ensure_defaults()

    assert path.read_bytes() == before
    assert store.load("texture_set").names[0] == "Custom Surface"


def test_category_store_preserves_order_and_falls_back_for_unknown_icons(tmp_path) -> None:
    store = CategoryConfigStore(tmp_path)
    store.ensure_defaults()
    path = store.path_for("hdri")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["categories"] = [
        {"name": "My Skies", "icon": "not-installed", "aliases": ["sky"]},
        {"name": "Studio", "icon": "light", "aliases": []},
    ]
    path.write_text(json.dumps(document), encoding="utf-8")

    catalog = store.load("hdri")

    assert catalog.names == ("My Skies", "Studio")
    assert catalog.icon_for("My Skies") == GENERIC_ICON_ID
    assert any("Unknown category icon" in warning for warning in store.last_warnings)


def test_category_store_invalid_document_uses_defaults_without_rewriting(tmp_path) -> None:
    store = CategoryConfigStore(tmp_path)
    store.ensure_defaults()
    path = store.path_for("model")
    path.write_text('{"schema_version": 99}', encoding="utf-8")

    catalog = store.load("model")

    assert "Architecture" in catalog.names
    assert path.read_text(encoding="utf-8") == '{"schema_version": 99}'
    assert any("Built-in categories" in warning for warning in store.last_warnings)


def test_texture_defaults_cover_real_library_primary_categories() -> None:
    catalog = default_category_catalog("texture_set")

    assert {
        "Imperfections", "Rock", "Fabric", "Soil", "Marble", "Grass",
        "Floors", "Bark", "Roofing", "River Debris", "Uncategorized",
    }.issubset(catalog.names)
    assert catalog.canonical_name("carpet") == "Fabric"
    assert catalog.canonical_name("finger prints") == "Imperfections"
    assert catalog.canonical_name("cliffs") == "Rock"


def test_texture_scanner_selection_uses_json_catalog_names_and_aliases() -> None:
    catalog = CategoryCatalog("texture_set", (
        CategoryDefinition("Textiles", "fabric", ("carpet", "cloth")),
        CategoryDefinition("Uncategorized", "uncategorized"),
    ))

    assert _select_category(["carpet"], "Provider Asset", "Uncategorized", catalog) == "Textiles"
    assert _select_category([], "Worn_Cloth_04", "Uncategorized", catalog) == "Textiles"
    assert _select_category([], "Unknown_04", "Missing", catalog) == "Uncategorized"
