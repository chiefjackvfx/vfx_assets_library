from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


RESOLUTIONS = ("1K", "2K", "4K", "8K", "16K", "Highest available")


def polyhaven_download_directory() -> Path:
    """Resolve the user's Downloads location without creating any directories."""
    downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    root = Path(downloads) if downloads else Path.home() / "Downloads"
    return root / "ShotBox" / "PolyHaven"


@dataclass(frozen=True)
class PolyHavenSyncPreferences:
    hdris: bool = True
    textures: bool = True
    models: bool = True
    hdri_resolution: str = "4K"
    texture_resolution: str = "4K"
    model_resolution: str = "4K"

    def normalized(self) -> PolyHavenSyncPreferences:
        return PolyHavenSyncPreferences(
            bool(self.hdris),
            bool(self.textures),
            bool(self.models),
            *(
                value if value in RESOLUTIONS else "4K"
                for value in (
                    self.hdri_resolution,
                    self.texture_resolution,
                    self.model_resolution,
                )
            ),
        )

    def selected_types(self) -> dict[str, str]:
        return {
            kind: resolution
            for kind, enabled, resolution in (
                ("hdri", self.hdris, self.hdri_resolution),
                ("texture_set", self.textures, self.texture_resolution),
                ("model", self.models, self.model_resolution),
            )
            if enabled
        }
