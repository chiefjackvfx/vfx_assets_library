from universal_asset_library.domain.textures import PBR_CHANNELS, TextureAsset, sample_textures


def test_sample_assets_have_unique_ids_and_required_maps() -> None:
    assets = sample_textures()
    assert len({asset.id for asset in assets}) == len(assets)
    assert all("Base Color" in asset.channels for asset in assets)
    assert all("Roughness" in asset.channels for asset in assets)
    assert all("Normal" in asset.channels for asset in assets)


def test_search_matches_names_tags_categories_and_channels() -> None:
    asset = sample_textures()[0]
    assert asset.matches("concrete")
    assert asset.matches("architectural")
    assert asset.matches("displacement")
    assert not asset.matches("metalness")


def test_category_and_channel_filters_are_combined() -> None:
    steel = next(asset for asset in sample_textures() if asset.name == "Brushed Steel")
    assert steel.matches(category="Metal", channel="Metalness")
    assert not steel.matches(category="Wood", channel="Metalness")
    assert not steel.matches(category="Metal", channel="Opacity")


def test_query_is_trimmed_and_case_insensitive() -> None:
    asset = sample_textures()[2]
    assert asset.matches("  FOREST  ")
    assert asset.matches("")


def test_channel_vocabulary_has_no_duplicates() -> None:
    assert len(PBR_CHANNELS) == len(set(PBR_CHANNELS))


def test_texture_asset_is_immutable() -> None:
    asset = sample_textures()[0]
    try:
        asset.name = "Changed"
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("TextureAsset should be immutable")
