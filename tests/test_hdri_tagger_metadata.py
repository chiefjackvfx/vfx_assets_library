from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtGui import QColor, QImage

from prototype.hdri_tagger.metadata import (
    AssetRecord,
    apply_classification,
    choose_preview,
    discover_assets,
    load_allowed_tags,
    load_category_names,
    merge_tags,
)


def image(path: Path, color: str = "#65809a") -> None:
    value = QImage(64, 32, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    assert value.save(str(path))


def provider_document(**updates) -> dict:
    document = {
        "name": "Courtyard",
        "categories": ["Outdoor"],
        "tags": ["old", "Sunny"],
        "description": "Preserve me.",
        "files": {"hdri": {"1k": {"hdr": {"url": "example.hdr"}}}},
    }
    document.update(updates)
    return document


def test_load_category_and_allowed_tag_names(tmp_path) -> None:
    categories = tmp_path / "categories.json"
    categories.write_text(json.dumps({
        "categories": [{"name": "Outdoor"}, {"name": "Indoor"}, "Studio"],
    }), encoding="utf-8")
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({
        "tags": [{"name": "sunny"}, "cloudy", "urban", "rural", "studio"],
    }), encoding="utf-8")

    assert load_category_names(categories) == ("Outdoor", "Indoor", "Studio")
    assert load_allowed_tags(tags) == ("sunny", "cloudy", "urban", "rural", "studio")


def test_discover_provider_metadata_and_choose_ambiguous_preview(tmp_path) -> None:
    asset = tmp_path / "courtyard"
    asset.mkdir()
    metadata = asset / "info.json"
    metadata.write_text(json.dumps(provider_document()), encoding="utf-8")
    image(asset / "courtyard_preview.jpg")
    image(asset / "courtyard_hero.png")
    (asset / "notes.json").write_text('{"unrelated": true}', encoding="utf-8")

    records = discover_assets(tmp_path)

    assert len(records) == 1
    record = records[0]
    assert record.metadata_path == metadata
    assert record.metadata_kind == "provider"
    assert record.preview_path is None
    assert len(record.preview_candidates) == 2
    assert "Multiple rendered previews" in record.diagnostic

    selected = choose_preview(record, record.preview_candidates[1])
    assert selected.preview_path == record.preview_candidates[1]
    assert not selected.diagnostic


def test_discovery_refuses_ambiguous_provider_metadata(tmp_path) -> None:
    asset = tmp_path / "courtyard"
    asset.mkdir()
    for name in ("info.json", "metadata.json"):
        (asset / name).write_text(json.dumps(provider_document()), encoding="utf-8")
    image(asset / "preview.jpg")

    record = discover_assets(tmp_path)[0]

    assert "Multiple HDRI metadata files" in record.diagnostic


def test_managed_asset_hides_nested_preserved_provider_metadata(tmp_path) -> None:
    library = tmp_path / "library"
    asset = library / "hdris" / "outdoor" / "courtyard"
    (library / ".ual").mkdir(parents=True)
    (library / ".ual" / "library.json").write_text("{}", encoding="utf-8")
    (asset / "previews").mkdir(parents=True)
    (asset / "metadata").mkdir()
    image(asset / "previews" / "thumb.jpg")
    (asset / "asset.json").write_text(json.dumps({
        "id": "asset-id",
        "type": "hdri",
        "name": "Courtyard",
        "category": "Outdoor",
        "tags": [],
        "previews": {"thumbnail": "previews/thumb.jpg", "hero": "previews/thumb.jpg"},
    }), encoding="utf-8")
    (asset / "metadata" / "info.json").write_text(
        json.dumps(provider_document()), encoding="utf-8"
    )

    records = discover_assets(library)

    assert len(records) == 1
    assert records[0].metadata_kind == "managed"
    assert records[0].library_root == library
    assert records[0].preview_path == asset / "previews" / "thumb.jpg"


def test_discover_managed_texture_model_and_flat_stock(tmp_path) -> None:
    library = tmp_path / "library"
    (library / ".ual").mkdir(parents=True)
    (library / ".ual" / "library.json").write_text("{}", encoding="utf-8")
    fixtures = (
        ("texture_set", library / "textures" / "ground" / "stone", "previews/thumb.jpg"),
        ("model", library / "models" / "props" / "chair", "previews/thumb.jpg"),
    )
    for asset_type, folder, relative in fixtures:
        (folder / "previews").mkdir(parents=True)
        image(folder / relative)
        (folder / "asset.json").write_text(json.dumps({
            "id": f"{asset_type}-id",
            "type": asset_type,
            "name": asset_type,
            "category": "Uncategorized",
            "tags": [],
            "previews": {"thumbnail": relative},
        }), encoding="utf-8")
        records = discover_assets(library, asset_type)
        assert len(records) == 1
        assert records[0].asset_type == asset_type
        assert records[0].preview_path == folder / relative

    stock_folder = library / "stock" / "uncategorized"
    stock_folder.mkdir(parents=True)
    image(stock_folder / "Blast_Thumbnail.jpg")
    (stock_folder / "Blast.json").write_text(json.dumps({
        "id": "stock-id",
        "type": "stock",
        "name": "Blast",
        "category": "Uncategorized",
        "tags": [],
        "preview": {"thumbnail": "Blast_Thumbnail.jpg", "video": "Blast_Preview.mp4"},
    }), encoding="utf-8")

    stock = discover_assets(library, "stock")
    assert len(stock) == 1
    assert stock[0].metadata_path.name == "Blast.json"
    assert stock[0].preview_path.name == "Blast_Thumbnail.jpg"


def test_discover_megascans_texture_and_model_provider_metadata(tmp_path) -> None:
    texture = tmp_path / "texture"
    texture.mkdir()
    image(texture / "texture_preview.jpg")
    (texture / "meta.json").write_text(json.dumps({
        "id": "texture-id",
        "semanticTags": {"name": "Stone"},
        "categories": ["Stone"],
        "tags": [],
        "maps": [{"type": "albedo", "uri": "stone.jpg"}],
    }), encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    image(model / "model_preview.jpg")
    (model / "meta.json").write_text(json.dumps({
        "id": "model-id",
        "semanticTags": {"name": "Chair"},
        "categories": ["Furniture"],
        "tags": [],
        "meshes": [],
    }), encoding="utf-8")

    textures = discover_assets(tmp_path, "texture_set")
    models = discover_assets(tmp_path, "model")

    assert [record.name for record in textures] == ["Stone"]
    assert textures[0].asset_type == "texture_set"
    assert [record.name for record in models] == ["Chair"]
    assert models[0].asset_type == "model"


def test_merge_tags_is_stable_and_case_insensitive() -> None:
    assert merge_tags(("old", "Sunny"), ("sunny", "urban", "Night")) == (
        "old", "Sunny", "urban", "Night",
    )


def test_apply_provider_classification_preserves_payload_and_makes_backup(tmp_path) -> None:
    asset = tmp_path / "courtyard"
    asset.mkdir()
    metadata = asset / "info.json"
    original = provider_document()
    metadata.write_text(json.dumps(original), encoding="utf-8")
    preview = asset / "preview.jpg"
    image(preview)
    record = discover_assets(tmp_path)[0]

    updated = apply_classification(
        record,
        "Urban",
        ("sunny", "street", "midday", "hard-light", "high-contrast"),
        preview_root=tmp_path,
        backup_stamp="20260724T120000Z",
    )

    document = json.loads(metadata.read_text(encoding="utf-8"))
    backup = (
        tmp_path / ".hdri-tagger-backups" / "20260724T120000Z"
        / "courtyard" / "info.json"
    )
    assert document["categories"] == ["Urban"]
    assert document["tags"] == [
        "old", "Sunny", "street", "midday", "hard-light", "high-contrast",
    ]
    assert document["description"] == "Preserve me."
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert updated.category == "Urban"


def test_apply_managed_classification_uses_repository_update(tmp_path, monkeypatch) -> None:
    library = tmp_path / "library"
    asset = library / "hdris" / "outdoor" / "courtyard"
    asset.mkdir(parents=True)
    (asset / "asset.json").write_text(json.dumps({
        "id": "asset-id",
        "type": "hdri",
        "name": "Courtyard",
        "category": "Outdoor",
        "tags": ["old"],
        "author": "Artist",
        "description": "Text",
    }), encoding="utf-8")
    record = AssetRecord(
        "Courtyard", asset, asset / "asset.json", "managed", "Outdoor", ("old",),
        (), asset_id="asset-id", library_root=library,
    )
    calls = []
    moved = library / "hdris" / "urban" / "courtyard"

    class FakeRepository:
        def __init__(self, root):
            assert Path(root) == library

        def update_asset_metadata(self, asset_id, update):
            calls.append((asset_id, update))
            return SimpleNamespace(
                asset_dir=moved,
                category=update.category,
                tags=tuple(update.tags),
                thumbnail_path=None,
                hero_path=None,
            )

    monkeypatch.setattr(
        "universal_asset_library.library.LibraryRepository", FakeRepository
    )

    updated = apply_classification(
        record,
        "Urban",
        ("street", "sunny", "midday", "hard-light", "high-contrast"),
        preview_root=library,
    )

    assert calls[0][0] == "asset-id"
    assert calls[0][1].name == "Courtyard"
    assert calls[0][1].tags[0] == "old"
    assert updated.folder == moved
    assert updated.category == "Urban"
