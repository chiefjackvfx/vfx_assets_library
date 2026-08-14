from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_asset_library.importer import (
    StockTaxonomyStore,
    classify_stock_path,
    default_stock_taxonomy,
)


@pytest.mark.parametrize(
    ("path", "category", "tags"),
    (
        ("04. Couch_Hits/Couch_Hit_Side_01.mov", "Impacts", ("couch", "hit", "side")),
        ("06. Dirt_Charges/Dirt_Charge_01.mov", "Dust", ("charge", "dirt")),
        (
            "11. Muzzle_Flashes/Muzzle_Flash_Straight_01.mov",
            "Ballistics",
            ("muzzle-flash", "straight"),
        ),
        (
            "08. Explosions/Fireball_AtCam_01.mov",
            "Explosions",
            ("at-camera", "fireball"),
        ),
        (
            "EFFECTS/5 - METEORS/01 - Meteors - LAYERS/"
            "01 - Meteors - LIGHT (Screen mode).mov",
            "Meteors",
            ("light-layer", "screen-blend"),
        ),
        ("19. Water/Lens_Splash_01.mov", "Lens", ("splash", "water")),
        ("Future/Magic_Energy_Burst.mov", "Magic", ("burst",)),
        ("Future/Motion_Graphics_Lines.mov", "Motion Graphics", ()),
    ),
)
def test_stock_classification(path: str, category: str, tags: tuple[str, ...]) -> None:
    result = classify_stock_path(path, default_stock_taxonomy())
    assert result.category == category
    assert result.tags == tags
    assert result.evidence


def test_taxonomy_normalizes_aliases_without_substring_matches() -> None:
    taxonomy = default_stock_taxonomy()
    result = classify_stock_path(
        "Sparks/Welding_Fllying_Out-of-Focus_45Cal_Slow_01.mov", taxonomy
    )
    assert result.category == "Sparks"
    assert result.tags == (
        "45-caliber", "flying", "out-of-focus", "slow-motion", "welding",
    )
    assert "light-layer" not in result.tags


def test_loose_bokeh_and_snow_filenames_receive_useful_classification() -> None:
    taxonomy = default_stock_taxonomy()

    bokeh = classify_stock_path("Bokeh20_4K.mp4", taxonomy)
    snow = classify_stock_path("Falling Snow 4K.mov", taxonomy)

    assert bokeh.category == "Lens"
    assert bokeh.tags == ("bokeh",)
    assert snow.category == "Atmospheres"
    assert snow.tags == ("falling", "snow", "weather")


def test_promoted_library_aliases_are_bundled_defaults() -> None:
    taxonomy = default_stock_taxonomy()

    assert classify_stock_path("Fog/Cloud_01.mov", taxonomy).category == "Atmospheres"
    assert classify_stock_path("Light Leaks/Anamorphic_Flare.mov", taxonomy).category == "Lens"
    assert classify_stock_path("Smoke/Plume_01.mov", taxonomy).category == "Smoke"
    assert classify_stock_path("Motion Graphics/Noise_01.mov", taxonomy).category == "Motion Graphics"


@pytest.mark.parametrize(
    "path",
    (
        "Film FX/Leader_01.mov",
        "Transfers/8mm_Grain.mov",
        "Transfers/Super_8_Gate_Weave.mov",
        "Transfers/16mm_Film_Scratch.mov",
        "Transfers/Super16_Light_Leak.mov",
        "Transfers/35_mm_Film_Burn.mov",
        "Transfers/65mm_Damaged_Film.mov",
        "Transfers/70mm_Celluloid.mov",
    ),
)
def test_film_fx_formats_and_artifacts_are_classified(path: str) -> None:
    result = classify_stock_path(path, default_stock_taxonomy())

    assert result.category == "Film FX"


def test_taxonomy_store_creates_portable_files_and_never_overwrites(tmp_path: Path) -> None:
    store = StockTaxonomyStore(tmp_path)
    taxonomy = store.ensure_defaults()
    assert taxonomy.category_names[:3] == ("Atmospheres", "Blood", "Ballistics")
    assert {"Lens", "Magic", "Motion Graphics"}.issubset(taxonomy.category_names)
    assert store.categories_path.is_file()
    assert store.tags_path.is_file()
    category_bytes = store.categories_path.read_bytes()
    tag_bytes = store.tags_path.read_bytes()

    store.ensure_defaults()

    assert store.categories_path.read_bytes() == category_bytes
    assert store.tags_path.read_bytes() == tag_bytes


def test_taxonomy_initialization_does_not_touch_existing_manifests(tmp_path: Path) -> None:
    manifest = tmp_path / "stock" / "smoke" / "existing" / "asset.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"existing": true}\n')
    before = manifest.read_bytes()

    StockTaxonomyStore(tmp_path).ensure_defaults()

    assert manifest.read_bytes() == before


def test_custom_taxonomy_round_trip_and_corrupt_fallback(tmp_path: Path) -> None:
    store = StockTaxonomyStore(tmp_path)
    store.ensure_defaults()
    document = json.loads(store.categories_path.read_text(encoding="utf-8"))
    document["categories"].insert(0, {"name": "Energy", "aliases": ["energy fx"]})
    store.categories_path.write_text(json.dumps(document), encoding="utf-8")

    loaded = store.load()

    assert loaded.category_names[0] == "Energy"
    store.tags_path.write_text("{broken", encoding="utf-8")
    fallback = store.load()
    assert "at-camera" in fallback.tag_names
    assert any("Built-in tags" in warning for warning in store.last_warnings)
    assert store.tags_path.read_text(encoding="utf-8") == "{broken"


def test_taxonomy_upgrade_moves_film_aliases_out_of_lens(tmp_path: Path) -> None:
    store = StockTaxonomyStore(tmp_path)
    store.ensure_defaults()
    document = json.loads(store.categories_path.read_text(encoding="utf-8"))
    document["defaults_version"] = 3
    document["categories"] = [
        category for category in document["categories"]
        if category["name"] != "Film FX"
    ]
    lens = next(category for category in document["categories"] if category["name"] == "Lens")
    lens["aliases"].extend(["film burn", "light leaks"])
    store.categories_path.write_text(json.dumps(document), encoding="utf-8")

    taxonomy = store.ensure_defaults()
    upgraded = json.loads(store.categories_path.read_text(encoding="utf-8"))
    upgraded_lens = next(
        category for category in upgraded["categories"] if category["name"] == "Lens"
    )

    assert "Film FX" in taxonomy.category_names
    assert "film burn" not in upgraded_lens["aliases"]
    assert "light leaks" not in upgraded_lens["aliases"]
    assert classify_stock_path("Transfers/16mm_Film_Burn.mov", taxonomy).category == "Film FX"


def test_duplicate_alias_uses_defaults_with_warning(tmp_path: Path) -> None:
    store = StockTaxonomyStore(tmp_path)
    store.ensure_defaults()
    document = json.loads(store.categories_path.read_text(encoding="utf-8"))
    document["categories"][1]["aliases"].append("atmosphere")
    store.categories_path.write_text(json.dumps(document), encoding="utf-8")

    taxonomy = store.load()

    assert taxonomy.category_names[0] == "Atmospheres"
    assert any("shared" in warning for warning in store.last_warnings)


@pytest.mark.skipif(
    not Path("/home/gambit/000test").is_dir(),
    reason="Stock folder vocabulary fixture unavailable",
)
def test_known_sample_folders_have_controlled_categories() -> None:
    taxonomy = default_stock_taxonomy()
    paths = (
        "01. Atmospheres/Example.mov",
        "02. Blood/Example.mov",
        "03. Charges/Example.mov",
        "04. Couch_Hits/Example.mov",
        "05. Debris/Example.mov",
        "06. Dirt_Charges/Example.mov",
        "07. Dust_Elements_A/Example.mov",
        "08. Explosions/Example.mov",
        "09. Fire/Example.mov",
        "10. Glass/Example.mov",
        "11. Muzzle_Flashes/Example.mov",
        "12. Particle_Hits/Example.mov",
        "13. Powder_Hits/Example.mov",
        "14. Smoke/Example.mov",
        "15. Smoke_Charges/Example.mov",
        "16. Sparks/Example.mov",
        "17. Wall_Hits/Example.mov",
        "18. Shells/Example.mov",
        "19. Water/Example.mov",
        "EFFECTS/2 - LASERS/Example.mov",
        "EFFECTS/4 - ELECTRICITY/Example.mov",
        "EFFECTS/5 - METEORS/Example.mov",
        "EFFECTS/7 - LARGE SCALE/Example.mov",
        "EFFECTS/8 - DESTRUCTION/Example.mov",
    )
    assert all(
        classify_stock_path(path, taxonomy).category != "Uncategorized"
        for path in paths
    )
