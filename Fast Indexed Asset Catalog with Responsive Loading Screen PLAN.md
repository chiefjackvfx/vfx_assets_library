# Fast Indexed Asset Catalog with Responsive Loading Screen

## Summary

Replace synchronous full-library reloads with a persistent local catalog index and background refresh worker. The current 2,207-asset library takes about 26 seconds to scan, and that scan is currently repeated on every asset-type tab change. After this change, cached launches and tab switches should be effectively immediate while the network library is verified in the background.

Portable `asset.json` manifests remain authoritative; the local index is disposable acceleration data only.

## Key Changes

- Add a local SQLite catalog index under the operating system’s ShotBox cache directory, keyed by library path/ID rather than stored on the network share.
  - Store safely encoded library asset dataclasses, manifest paths, asset type, manifest size, and modification time.
  - Reject incompatible or corrupt cache versions and rebuild automatically.
  - Never use pickle or treat cached metadata as authoritative.
- Add an asynchronous catalog refresh worker.
  - On first use, scan the currently selected asset type first, publish that complete section, then index the remaining types.
  - On later launches, show cached assets immediately and verify manifests in the background.
  - Reuse unchanged indexed records; fully parse and validate only new or changed manifests.
  - Remove cached records only after a section scan confirms their manifests are gone.
  - Cancel stale workers when the library changes or the application closes.
- Stop calling `LibraryRepository.list_assets()` on asset-type tab changes.
  - Load category configuration once per library selection.
  - Keep all indexed assets in memory and switch sections by replacing the visible model from that cache.
  - Preserve each section’s selection where possible.
  - Update the in-memory and SQLite record directly after imports, metadata edits, AI guesses, downloads, model conversions, rescans, and other ShotBox-managed mutations.
- Add **Refresh Catalog** to the Assets toolbar.
  - Refresh automatically once at startup and after ShotBox mutations.
  - Do not perform periodic network scans.
  - Manual refresh covers files changed externally.

## Loading Experience

- Add a dedicated loading page to the existing Assets stack.
  - Show it only when the selected section has no cached/indexed results yet.
  - Display the asset type, current phase, discovered/processed counts, and an indeterminate progress bar during slow network discovery.
  - Keep the application, tabs, Settings, and window controls responsive.
- When cached assets already exist, keep the grid visible and show a compact **Refreshing catalog…** status rather than replacing it with the loading screen.
- If refresh fails:
  - Continue showing cached assets with a warning when cache data exists.
  - Show a retryable error page when no usable cache exists.
- Avoid continually reshuffling the grid: publish the selected section as one complete batch.
- Replace the unbounded original-resolution thumbnail dictionary with a bounded LRU cache of scaled thumbnails, retained across asset-type switches and cleared only when paths change or the library changes.

## Interfaces

- Add a non-Qt `CatalogIndex` service for cache creation, schema migration/rebuild, section queries, transactional replacement, targeted upserts, removals, and safe dataclass JSON encoding.
- Add a targeted repository listing interface that scans one canonical asset type/container instead of the complete library.
- Add a cancellable `CatalogRefreshWorker` with phase, progress, section-ready, finished, and failed signals.
- Split `AssetsTab.load_library()` into library initialization, cached-section display, background refresh, and targeted asset-update paths.

## Test Plan

- Round-trip every texture, atlas, HDRI, model, and Stock asset/nested dataclass through the safe cache encoding.
- Verify corrupt, missing, and old-schema indexes rebuild without affecting library files.
- Verify a cold build prioritizes the selected asset type and shows the loading page without blocking the event loop.
- Verify a warm launch displays cached assets before network refresh completes.
- Verify tab switching performs no manifest scan and retains the thumbnail cache.
- Verify background refresh detects added, changed, moved, and removed manifests and preserves cached data on partial failure.
- Verify imports, edits, AI changes, downloads, conversions, and rescans update the index immediately.
- Verify manual refresh, cancellation, library switching, progress text, retry UI, and shutdown.
- Add a synthetic thousands-of-assets test proving tab switches issue no filesystem reads, then run the complete deterministic regression suite.

## Assumptions

- Startup plus manual refresh is preferred over periodic scanning.
- A full loading screen is used only when the selected section has no cached data; warm refreshes remain visible in the grid.
- The current network-mounted library remains the source of truth.
- SQLite is stored locally, never on `/Volumes`, avoiding network-filesystem locking problems.
- The existing mutable `/home/gambit/000test` integration fixture remains excluded from deterministic regression verification.
