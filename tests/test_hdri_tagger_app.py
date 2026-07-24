from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from prototype.hdri_tagger.app import HdriTaggerWindow


def test_standalone_window_scans_provider_asset(tmp_path, monkeypatch) -> None:
    application = QApplication.instance() or QApplication([])
    asset = tmp_path / "asset"
    asset.mkdir()
    preview = QImage(64, 32, QImage.Format.Format_RGB32)
    preview.fill(QColor("#56758e"))
    assert preview.save(str(asset / "asset_preview.jpg"))
    (asset / "info.json").write_text(json.dumps({
        "name": "Asset",
        "categories": ["Outdoor"],
        "tags": [],
        "files": {"hdri": {}},
    }), encoding="utf-8")
    categories = tmp_path / "categories.json"
    categories.write_text(json.dumps({
        "categories": [{"name": "Outdoor"}, {"name": "Indoor"}, {"name": "Uncategorized"}],
    }), encoding="utf-8")
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({
        "tags": ["sunny", "urban", "midday", "hard-light", "high-contrast"],
    }), encoding="utf-8")
    monkeypatch.setattr(HdriTaggerWindow, "refresh_ollama", lambda self: None)

    window = HdriTaggerWindow()
    window.root_edit.setText(str(tmp_path))
    window.categories_edit.setText(str(categories))
    window.tags_edit.setText(str(tags))
    window.scan()

    assert window.table.rowCount() == 1
    assert window.records[0].preview_path == asset / "asset_preview.jpg"
    assert window.table.item(0, 8).text() == "Ready"
    window.close()
    application.processEvents()


def test_asset_type_profile_uses_type_specific_files(tmp_path, monkeypatch) -> None:
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(HdriTaggerWindow, "refresh_ollama", lambda self: None)
    window = HdriTaggerWindow()

    window.asset_type_combo.setCurrentIndex(
        window.asset_type_combo.findData("model")
    )

    assert window.categories_edit.text().endswith("model_categories.json") or not window.categories_edit.text()
    assert window.tags_edit.text().endswith("allowed_tags_model.json")
    window.close()
    application.processEvents()
