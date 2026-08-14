from __future__ import annotations

from dataclasses import dataclass


PBR_CHANNELS = (
    "Base Color",
    "Ambient Occlusion",
    "Roughness",
    "Glossiness",
    "Normal",
    "Displacement",
    "Height",
    "Bump",
    "Cavity",
    "Metalness",
    "Specular",
    "Opacity",
    "Emission",
    "Translucency",
    "Packed ARM",
)

TEXTURE_CATEGORIES = (
    "Asphalt",
    "Bark",
    "Brick",
    "Coal",
    "Concrete",
    "Construction",
    "Debris",
    "Fabric",
    "Floors",
    "Grass",
    "Gravel",
    "Ground",
    "Grout",
    "Imperfections",
    "Marble",
    "Metal",
    "Moss",
    "Paper",
    "Plaster",
    "River Debris",
    "Rock",
    "Roof",
    "Roofing",
    "Sand",
    "Snow",
    "Soil",
    "Stone",
    "Tile",
    "Tree",
    "Wood",
    "Clean",
    "Dirty",
    "Other",
    "Uncategorized",
)

ATLAS_CATEGORIES = (
    "Plants",
    "Grass",
    "Trees",
    "Leaves",
    "Flowers",
    "Moss",
    "Debris",
    "Branches",
    "Ground Cover",
    "Decals",
    "Miscellaneous",
    "Uncategorized",
)

HDRI_CATEGORIES = (
    "Outdoor",
    "Indoor",
    "Studio",
    "Nature",
    "Urban",
    "Night",
    "Sky",
    "Uncategorized",
)

MODEL_CATEGORIES = (
    "Architecture",
    "Furniture",
    "Props",
    "Decorative",
    "Nature",
    "Plants",
    "Rocks",
    "Vehicles",
    "Characters",
    "Creatures",
    "Food",
    "Industrial",
    "Miscellaneous",
    "Uncategorized",
)

STOCK_CATEGORIES = (
    "Atmospheres",
    "Blood",
    "Ballistics",
    "Debris",
    "Destruction",
    "Dust",
    "Electricity",
    "Explosions",
    "Fire",
    "Film FX",
    "Glass",
    "Impacts",
    "Lasers",
    "Lens",
    "Magic",
    "Meteors",
    "Motion Graphics",
    "Particles",
    "Smoke",
    "Sparks",
    "Water",
    "Miscellaneous",
    "Uncategorized",
)

VDB_CATEGORIES = (
    "Clouds",
    "Smoke",
    "Fire",
    "Explosions",
    "Fog",
    "Dust",
    "Water",
    "Magic",
    "Miscellaneous",
    "Uncategorized",
)


@dataclass(frozen=True, slots=True)
class TextureAsset:
    id: str
    name: str
    category: str
    resolution: str
    file_format: str
    channels: tuple[str, ...]
    tags: tuple[str, ...]
    description: str
    palette: tuple[str, str]
    size_label: str = ""

    def matches(self, query: str = "", category: str = "All", channel: str = "All") -> bool:
        haystack = " ".join((self.name, self.category, *self.tags, *self.channels)).casefold()
        return (
            (not query.strip() or query.strip().casefold() in haystack)
            and (category == "All" or self.category == category)
            and (channel == "All" or channel in self.channels)
        )


def sample_textures() -> list[TextureAsset]:
    return [
        TextureAsset("tx-001", "Weathered Concrete", "Concrete", "4K", "EXR", ("Base Color", "Roughness", "Normal", "Displacement", "Ambient Occlusion"), ("weathered", "architectural", "rough"), "A worn concrete surface with fine aggregate and subtle cracks.", ("#69727a", "#343a40"), "184 MB"),
        TextureAsset("tx-002", "Red Brick Wall", "Brick", "8K", "TIFF", ("Base Color", "Roughness", "Normal", "Displacement", "Ambient Occlusion"), ("brick", "wall", "masonry"), "Deep red masonry with aged mortar and varied brick faces.", ("#984c3d", "#432723"), "612 MB"),
        TextureAsset("tx-003", "Forest Moss", "Organic", "4K", "EXR", ("Base Color", "Roughness", "Normal", "Displacement", "Ambient Occlusion", "Opacity"), ("moss", "forest", "ground"), "Dense green moss suitable for forest floors and rocky surfaces.", ("#607643", "#26351f"), "231 MB"),
        TextureAsset("tx-004", "Brushed Steel", "Metal", "4K", "PNG", ("Base Color", "Roughness", "Normal", "Metalness"), ("steel", "brushed", "industrial"), "Directional brushed steel with restrained surface wear.", ("#abb3b7", "#545d63"), "96 MB"),
        TextureAsset("tx-005", "Desert Sand", "Ground", "8K", "EXR", ("Base Color", "Roughness", "Normal", "Displacement"), ("sand", "desert", "dunes"), "Fine wind-shaped sand with broad, soft rippling.", ("#d4aa6d", "#8b6139"), "428 MB"),
        TextureAsset("tx-006", "Dark Oak Planks", "Wood", "4K", "TIFF", ("Base Color", "Roughness", "Normal", "Displacement", "Ambient Occlusion"), ("oak", "planks", "floor"), "Dark-stained oak boards with visible grain and edge wear.", ("#76533c", "#2f211b"), "205 MB"),
        TextureAsset("tx-007", "Wet Cobblestone", "Stone", "8K", "EXR", ("Base Color", "Roughness", "Normal", "Displacement", "Ambient Occlusion"), ("wet", "street", "historic"), "Uneven old cobbles with puddled joints and a damp finish.", ("#586069", "#252b31"), "544 MB"),
        TextureAsset("tx-008", "Painted Plaster", "Plaster", "2K", "PNG", ("Base Color", "Roughness", "Normal"), ("paint", "interior", "subtle"), "Soft off-white painted plaster with delicate roller variation.", ("#d9d3c5", "#9e978b"), "38 MB"),
    ]
