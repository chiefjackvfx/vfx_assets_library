from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from PyQt6.QtCore import QSettings

from .domain import MODEL_CATEGORIES, TEXTURE_CATEGORIES
from .categories import CategoryConfigStore


THUMBNAIL_SIZES = ("small", "medium", "large")
DEFAULT_THUMBNAIL_SIZE = "medium"
DEFAULT_IMPORT_CATEGORY = "Uncategorized"


def normalize_library_path(value: str) -> str:
    """Return an absolute native path without resolving symlink aliases."""
    value = value.strip()
    if not value:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(value))
    return os.path.abspath(os.path.normpath(expanded))


def normalize_executable_path(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return os.path.abspath(os.path.normpath(os.path.expandvars(os.path.expanduser(value))))


def validate_library_path(value: str) -> tuple[bool, str]:
    path = normalize_library_path(value)
    if not path:
        return True, "Library path is not configured."
    candidate = Path(path)
    if not candidate.exists():
        return False, "The selected library path does not exist."
    if not candidate.is_dir():
        return False, "The selected library path is not a folder."
    if not os.access(candidate, os.R_OK):
        return False, "The selected library folder is not readable."
    if not os.access(candidate, os.W_OK):
        return True, "The library folder is readable but not writable."
    return True, "Library folder is ready."


@dataclass(frozen=True, slots=True)
class AppSettings:
    library_path: str = ""
    thumbnail_size: str = DEFAULT_THUMBNAIL_SIZE
    default_import_category: str = DEFAULT_IMPORT_CATEGORY
    default_model_category: str = DEFAULT_IMPORT_CATEGORY
    blender_path: str = ""
    houdini_path: str = ""
    render_hdri_on_import: bool = True
    render_texture_on_import: bool = True
    save_texture_preview_blend: bool = False
    ffmpeg_path: str = ""
    stock_hover_previews: bool = True
    vdb_parallel_renders: int = 2

    def normalized(self) -> "AppSettings":
        library_path = normalize_library_path(self.library_path)
        thumbnail_size = self.thumbnail_size if self.thumbnail_size in THUMBNAIL_SIZES else DEFAULT_THUMBNAIL_SIZE
        texture_categories = TEXTURE_CATEGORIES
        if library_path and Path(library_path).is_dir():
            texture_categories = CategoryConfigStore(library_path).load("texture_set").names
        category = self.default_import_category if self.default_import_category in texture_categories else DEFAULT_IMPORT_CATEGORY
        model_category = self.default_model_category if self.default_model_category in MODEL_CATEGORIES else DEFAULT_IMPORT_CATEGORY
        return AppSettings(
            library_path=library_path,
            thumbnail_size=thumbnail_size,
            default_import_category=category,
            default_model_category=model_category,
            blender_path=normalize_executable_path(self.blender_path),
            houdini_path=normalize_executable_path(self.houdini_path),
            render_hdri_on_import=bool(self.render_hdri_on_import),
            render_texture_on_import=bool(self.render_texture_on_import),
            save_texture_preview_blend=bool(
                self.save_texture_preview_blend
            ),
            ffmpeg_path=normalize_executable_path(self.ffmpeg_path),
            stock_hover_previews=bool(self.stock_hover_previews),
            vdb_parallel_renders=max(
                1, min(4, _setting_int(self.vdb_parallel_renders, 2))
            ),
        )


class SettingsStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings if settings is not None else QSettings()

    def load(self) -> AppSettings:
        return AppSettings(
            library_path=str(self._settings.value("library/path", "") or ""),
            thumbnail_size=str(self._settings.value("display/thumbnail_size", DEFAULT_THUMBNAIL_SIZE)),
            default_import_category=str(self._settings.value("import/default_category", DEFAULT_IMPORT_CATEGORY)),
            default_model_category=str(self._settings.value("import/default_model_category", DEFAULT_IMPORT_CATEGORY)),
            blender_path=str(self._settings.value("tools/blender_path", "") or ""),
            houdini_path=str(self._settings.value("tools/houdini_path", "") or ""),
            render_hdri_on_import=_setting_bool(self._settings.value("previews/render_hdri_on_import", True)),
            render_texture_on_import=_setting_bool(
                self._settings.value("previews/render_texture_on_import", True)
            ),
            save_texture_preview_blend=_setting_bool(
                self._settings.value(
                    "previews/save_texture_preview_blend", False
                )
            ),
            ffmpeg_path=str(self._settings.value("tools/ffmpeg_path", "") or ""),
            stock_hover_previews=_setting_bool(
                self._settings.value("display/stock_hover_previews", True)
            ),
            vdb_parallel_renders=_setting_int(
                self._settings.value("previews/vdb_parallel_renders", 2), 2
            ),
        ).normalized()

    def save(self, settings: AppSettings) -> AppSettings:
        normalized = settings.normalized()
        valid, message = validate_library_path(normalized.library_path)
        if not valid:
            raise ValueError(message)
        self._settings.setValue("library/path", normalized.library_path)
        self._settings.setValue("display/thumbnail_size", normalized.thumbnail_size)
        self._settings.setValue("import/default_category", normalized.default_import_category)
        self._settings.setValue("import/default_model_category", normalized.default_model_category)
        self._settings.setValue("tools/blender_path", normalized.blender_path)
        self._settings.setValue("tools/houdini_path", normalized.houdini_path)
        self._settings.setValue("previews/render_hdri_on_import", normalized.render_hdri_on_import)
        self._settings.setValue(
            "previews/render_texture_on_import",
            normalized.render_texture_on_import,
        )
        self._settings.setValue(
            "previews/save_texture_preview_blend",
            normalized.save_texture_preview_blend,
        )
        self._settings.setValue("tools/ffmpeg_path", normalized.ffmpeg_path)
        self._settings.setValue(
            "display/stock_hover_previews", normalized.stock_hover_previews
        )
        self._settings.setValue(
            "previews/vdb_parallel_renders",
            normalized.vdb_parallel_renders,
        )
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            raise OSError("The application settings could not be written.")
        return normalized


def _setting_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _setting_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
