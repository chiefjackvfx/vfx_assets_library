# Asset Category Icon Sidebar

## Summary

Replace the Assets toolbar’s category dropdown with a scrollable, expandable category rail on the left of the asset grid. Selecting one category filters the current asset type while continuing to combine with search and facet filters.

The rail starts collapsed, remembers its expanded state across launches, and resets to **All** whenever the user switches between Textures, Atlases, HDRIs, Models, or Stock.

## Implementation Changes

- Add the category rail beside the existing asset-grid/detail splitter:
  - Collapsed width approximately 52 px; expanded width approximately 190 px.
  - An **All** entry followed by categories currently used by the selected asset type.
  - Exclusive single-category selection with the existing orange selected styling.
  - Icon, category name when expanded, live result count, and tooltip.
  - A chevron toggles expansion; save state under `assets/category_rail_expanded` in `QSettings`.
  - Categories found in manifests but absent from configuration appear with a generic category icon.
- Remove the toolbar category combo box and route rail selection into the existing proxy model’s category filter.
  - Search, category, and channel/facet filters retain AND semantics.
  - Live category counts apply search and channel/facet filters but ignore the currently selected category.
  - Multi-category assets count and appear under every assigned category.
  - If filtering removes the selected card, clear the inspector or select the first visible card consistently.
  - Reloading a library resets to **All** if the selected category no longer exists.
- Bundle themeable monochrome SVGs:
  - Provide a semantic pictogram for every built-in Texture, Atlas, Model, and Stock category.
  - Introduce HDRI defaults: Outdoor, Indoor, Studio, Nature, Urban, Night, Sky, and Uncategorized.
  - Include shared `all`, `uncategorized`, and `generic-category` icons.
  - Resolve icons through a fixed icon-ID registry; unknown or missing IDs use the generic icon. JSON cannot reference arbitrary filesystem paths.
- Use the configured category definitions for importer/edit-dialog suggestions as well as the rail, replacing the current hard-coded category selection. This also corrects HDRIs currently falling back to texture-category suggestions.

## `.ual` Category Configuration

Create these portable files when a library is initialized or first loaded:

- `.ual/texture_categories.json`
- `.ual/atlas_categories.json`
- `.ual/hdri_categories.json`
- `.ual/model_categories.json`
- `.ual/stock_categories.json`

Use this common shape:

```json
{
  "schema_version": 1,
  "defaults_version": 1,
  "asset_type": "texture_set",
  "categories": [
    {
      "name": "Wood",
      "icon": "wood",
      "aliases": []
    }
  ]
}
```

- Preserve the existing Stock category names and classification aliases while adding optional `icon` values.
- Existing Stock files without icons remain valid; icons are inferred by category name.
- Create missing files from application defaults without replacing user-edited files.
- Preserve JSON order for the rail and suggestions, although the rail displays only categories present in current manifests.
- Validate asset type, schema version, unique non-empty names, string aliases, and known icon IDs.
- On missing, unreadable, or invalid configuration, use built-in defaults, retain the original file unchanged, and expose a non-blocking library warning.
- Category configuration affects presentation and suggestions only; existing asset manifests and primary-category folder placement remain unchanged.

## Tests

- Verify each asset type loads its corresponding JSON, preserves configured ordering, and uses bundled or fallback icons.
- Verify clicking a rail entry shows assets assigned to that category, including secondary categories, and **All** clears only the category filter.
- Verify search and facet filters combine correctly and live counts update without being constrained by the selected category.
- Verify switching asset types resets the category to **All**, while rail expansion persists through `QSettings`.
- Verify only categories present in manifests appear, with custom categories appended after configured categories and given the generic icon.
- Verify missing, malformed, mismatched, duplicate, and unknown-icon configuration falls back safely without modifying existing files.
- Extend Stock taxonomy tests to confirm legacy files without `icon` remain compatible and aliases still drive Stock classification.
- Add Qt layout tests for collapsed/expanded widths, tooltips, selection styling, empty results, inspector selection, and removal of the old dropdown.

## Assumptions

- Category filtering remains single-select; multi-select category filtering is out of scope.
- Category files are user-editable but no category-management editor is added in this change.
- Custom SVG paths and user-supplied image files are out of scope; users select from bundled icon IDs.
- Importer classification behavior remains unchanged except that category suggestions come from the new per-type configuration.
