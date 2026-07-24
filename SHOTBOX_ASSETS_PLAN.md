# ShotBox Assets — Lightweight Asset Library Plan

## Goal

Build a focused ShotBox desktop library for PBR texture sets, HDRIs, and models before adding heavier indexing.

## Current milestone: UI prototype

### Assets tab

- Provide separate Texture and HDRI sections backed by portable manifests.
- Present responsive texture cards with name, category, resolution, format, and available maps.
- Search across names, categories, tags, and PBR channels.
- Filter by category and required channel.
- Sort by name, category, or resolution.
- Show the selected texture's description, metadata, maps, and tags in a detail panel.
- Edit one JSON-controlled category and chip-based tags without changing managed payload paths except when the primary category folder changes.
- Keep filesystem-dependent actions disabled and clearly identified.

### Importer tab

- Scan one material folder or a parent folder in a background worker without modifying it.
- Build a single source inventory with structured diagnostics, progress, cancellation, and source-change snapshots.
- Normalize Megascans, Poly Haven, and filename-inferred material metadata.
- Group available resolutions under one material and retain format alternatives.
- Preserve all other safe regular files under `extras/`, keeping their relative paths and recording hashes in the manifest so the same convention can be shared by future importers.
- Show editable name, category, tags, author, descriptions, preferred maps, and previews.
- Warn about missing JSON-declared files, unknown schemas, and preview fallbacks.
- Import checked reviewed materials through atomic staging without changing source files.
- Show copy/hash progress, support cancellation, and skip duplicate provider IDs or fingerprints.
- Require asynchronous preflight before import, block stale sources, and surface changed provider IDs as explicit conflicts.
- Store readable asset folders and normalized `<Asset>_<Resolution>_<Channel>` map filenames while keeping UUIDs in `asset.json`.
- Switch between Texture and HDRI scanning without adding another top-level tab.
- Group local HDR/EXR panorama variants, preserve their source filenames, and retain provider JSON, `.blend`, and every other safe companion file.
- Convert readable HDRI preview images into genuine JPEG thumbnails and create a JPEG placeholder when none is available.
- Store HDR/EXR variants directly under `maps/`; keep their resolution grouping in the manifest rather than one-file folders.
- Optionally run Blender against the immutable `templates/hdri_preview.blend` scene during HDRI import, composing its saved 2048×512 sphere render beneath a 2048×1024 equirectangular panorama.
- Persist rendered-preview status and diagnostics in the HDRI manifest, retain fallbacks on failure, and allow safe background regeneration from the HDRI inspector.

### Settings tab

- Persist an optional existing library folder through `QSettings`.
- Validate that configured paths exist and are readable, while warning about read-only locations.
- Apply small, medium, or large asset-card sizes without restarting.
- Persist the default category used for new importer reviews.
- Repair legacy UUID-suffixed folders and maps through a confirmed, staged maintenance action.
- Detect abandoned staging data and library locks and expose confirmed recovery actions.
- Validate and upgrade older library layouts through a confirmed Update/Fix action.
- Configure and validate an optional Blender executable and control automatic HDRI preview rendering.

## Technology

- Python 3.11+
- PyQt6 and Qt's model/view framework
- In-memory typed texture records for the prototype
- Dark, compact interface designed for VFX workstations
- Windows and Linux as the eventual supported platforms

## Next milestone

1. Add richer companion-file inspection and per-asset validation/repair.
2. Add configurable HDRI preview templates and render-device overrides.
3. Add SQLite full-text indexing when manifest scanning becomes too slow.

## Deferred

- SQLite/FTS indexing and shared network-library coordination
- Stock footage, audio, and LUTs
- Video and model previews
- Collections, favorites, versioning, and publishing
- Advanced model placement controls, USD variants, payloads, unpacking, and drag-and-drop
- Packaging and deployment automation

## Acceptance criteria

- The application launches into a three-tab PyQt6 window: Assets, Importer, and Settings.
- Assets can be searched, filtered, sorted, selected, and inspected without touching disk.
- The Importer can asynchronously scan and review real local texture and HDRI folders.
- A 10,000-file/1,000-material synthetic scan remains responsive and completes without repeated directory walks.
- Checked materials import into the configured library and immediately appear as real Assets cards.
- Settings survive restart and apply to the active UI after Save.
- The UI never claims that demo assets or selected folders were imported.
- Domain filtering behavior is covered by automated tests.
