import os

import pytest
from PyQt6.QtCore import QCoreApplication, QSettings

from universal_asset_library.settings import (
    AppSettings,
    SettingsStore,
    normalize_library_path,
    validate_library_path,
)
from universal_asset_library.app import _migrate_legacy_settings


def make_store(tmp_path) -> SettingsStore:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return SettingsStore(settings)


def test_defaults_and_round_trip(tmp_path) -> None:
    store = make_store(tmp_path)
    assert store.load() == AppSettings()
    library = tmp_path / "library"
    library.mkdir()
    saved = store.save(AppSettings(str(library), "large", "Wood", "Furniture"))
    assert saved.library_path == normalize_library_path(str(library))
    assert store.load() == saved
    assert store.load().default_model_category == "Furniture"


def test_blender_and_hdri_render_preferences_round_trip(tmp_path) -> None:
    store = make_store(tmp_path)
    executable = tmp_path / "Blender"
    executable.write_bytes(b"fixture")
    saved = store.save(AppSettings(blender_path=str(executable), render_hdri_on_import=False))
    assert saved.blender_path == str(executable)
    assert saved.render_hdri_on_import is False
    assert store.load() == saved


def test_ffmpeg_path_round_trip(tmp_path) -> None:
    store = make_store(tmp_path)
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"fixture")
    saved = store.save(AppSettings(ffmpeg_path=str(executable)))
    assert saved.ffmpeg_path == str(executable)
    assert store.load() == saved


def test_stock_hover_preview_preference_defaults_and_round_trips(tmp_path) -> None:
    store = make_store(tmp_path)
    assert store.load().stock_hover_previews is True

    saved = store.save(AppSettings(stock_hover_previews=False))

    assert saved.stock_hover_previews is False
    assert store.load().stock_hover_previews is False


def test_empty_library_path_is_valid_and_persisted(tmp_path) -> None:
    store = make_store(tmp_path)
    assert validate_library_path("")[0]
    assert store.save(AppSettings("", "small", "Metal")).library_path == ""


def test_invalid_path_does_not_replace_saved_settings(tmp_path) -> None:
    store = make_store(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    original = store.save(AppSettings(str(library)))
    with pytest.raises(ValueError, match="does not exist"):
        store.save(AppSettings(str(tmp_path / "missing"), "large", "Wood"))
    assert store.load() == original


def test_file_path_and_unreadable_directory_are_rejected(tmp_path, monkeypatch) -> None:
    file_path = tmp_path / "asset.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    assert validate_library_path(str(file_path)) == (False, "The selected library path is not a folder.")
    library = tmp_path / "library"
    library.mkdir()
    real_access = os.access

    def deny_read(path, mode):
        return False if mode == os.R_OK else real_access(path, mode)

    monkeypatch.setattr("universal_asset_library.settings.os.access", deny_read)
    assert validate_library_path(str(library)) == (False, "The selected library folder is not readable.")


def test_read_only_directory_is_allowed_with_warning(tmp_path, monkeypatch) -> None:
    library = tmp_path / "library"
    library.mkdir()

    def access(_path, mode):
        return mode == os.R_OK

    monkeypatch.setattr("universal_asset_library.settings.os.access", access)
    assert validate_library_path(str(library)) == (True, "The library folder is readable but not writable.")


def test_shotbox_name_migrates_legacy_application_settings(tmp_path) -> None:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    legacy = QSettings("UniversalAssetLibrary", "Universal Asset Library")
    legacy.setValue("library/path", "/legacy/library")
    legacy.sync()
    QCoreApplication.setOrganizationName("ShotBox")
    QCoreApplication.setApplicationName("ShotBox Assets")
    current = QSettings()
    current.clear()
    _migrate_legacy_settings()
    assert current.value("library/path") == "/legacy/library"
