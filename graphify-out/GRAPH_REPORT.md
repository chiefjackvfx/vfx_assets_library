# Graph Report - vfx_assets_library  (2026-09-07)

## Corpus Check
- 146 files · ~161,533 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2974 nodes · 10280 edges · 120 communities (99 shown, 21 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 1871 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `843eb7ab`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- HdriTaggerWindow
- ImporterTab
- test_texture_preview.py
- LibraryError
- CategoryConfigStore
- repository.py
- ual_houdini_bridge/actions.py
- assets_tab.py
- test_ui.py
- DetailPanel
- MaterialCandidate
- FakeNode
- LibraryRepository
- ShotBox Assets
- SettingsTab
- scan_stock_folder
- AiOrganiserDialog
- test_vdb_preview.py
- scanner.py
- integrations/__init__.py
- LibraryVdbAsset
- domain/__init__.py
- shotbox_assets_bridge/actions.py
- hdri_scanner.py
- test_catalog_index.py
- .replace
- TagEditor
- BridgeServer
- model_scanner.py
- ModelExportPayload
- houdini/bridge.py
- importer_tab.py
- TextureCardDelegate
- BlenderPluginInstaller
- SettingsStore
- .__init__
- blender_model_conversion_driver.py
- .update_library
- adapters.py
- BlenderPreviewSession
- categories.py
- .__init__
- StockHoverPreviewController
- test_windows_auto_update.py
- vdb_renderer.py
- settings_tab.py
- .__init__
- QuickLookPopup
- LibraryInspectionWorker
- hdri_renderer.py
- test_blender_plugin.py
- ModelObject
- scan_vdb_folder
- ._download_polyhaven
- LibraryModelAsset
- previews/__init__.py
- PanZoomViewport
- Links
- TextureFilterModel
- ModelAssetRescanDialog
- sample_textures
- scan_model_folder
- FakePopup
- OllamaClient
- CatalogRefreshWorker
- blender_texture_driver.py
- importer/__init__.py
- Material
- stock_video.py
- CenterResizeGrip
- Images
- quick_look.py
- blender_model_rescan_driver.py
- .new
- blender_hdri_driver.py
- batch_metadata.py
- Object Cube Icon
- prototype/__init__.py
- tags/__init__.py
- universal_asset_library/__init__.py
- Lightning Bolt Energy Icon
- Four-Tile Grid Icon
- Human Figure Life Icon
- Mountain Terrain Icon
- shotbox-assets
- Cloud Outline Icon
- Radiating Sun Light Icon
- Help Question Icon
- Label Tag Icon
- Bullseye Target Icon
- polyhaven.py
- AssetsTab
- test_model_conversion.py
- AssetRecord
- AppSettings
- blender_preview_server.py
- .__enter__
- model_conversion.py
- application_arguments
- Q: Implement the planned shared five-star asset rating system
- Q: Make the five-star rating apply instantly and look cute
- Q: Allow rating multiple assets while background saves are still running
- Q: Make the rating control look really cute
- Q: Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras
- Q: with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras.
- Q: PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching
- Q: its still pretty slow would it be faster on cpu?
- _classification_preview
- ScanResult
- run_vfx_asset_library.sh script
- scripts/__init__.py
- .__init__
- model_rescan.py
- render_hdri_preview
- list
- universal_asset_library/app.py

## God Nodes (most connected - your core abstractions)
1. `LibraryRepository` - 257 edges
2. `AssetsTab` - 195 edges
3. `CancelToken` - 101 edges
4. `scan_texture_folder()` - 90 edges
5. `CategoryCatalog` - 89 edges
6. `DetailPanel` - 88 edges
7. `ImporterTab` - 87 edges
8. `LibraryError` - 77 edges
9. `SettingsTab` - 77 edges
10. `CategoryConfigStore` - 71 edges

## Surprising Connections (you probably didn't know these)
- `test_asset_type_auto_detection_and_provider_override()` --calls--> `detect_asset_type()`  [INFERRED]
  tests/test_importer_scanner.py → src/universal_asset_library/importer/detection.py
- `test_extension_installer_builds_installs_and_removes_package()` --calls--> `BlenderPluginInstaller`  [INFERRED]
  tests/test_blender_bridge.py → src/universal_asset_library/integrations/blender/installer.py
- `test_corrupt_and_old_schema_catalogs_rebuild()` --calls--> `CatalogIndex`  [INFERRED]
  tests/test_catalog_index.py → src/universal_asset_library/library/catalog.py
- `Expandable Category Rail` --semantically_similar_to--> `Asset Catalog Category Rail`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md
- `Per-Type Category Configuration` --semantically_similar_to--> `Portable Category Taxonomy Files`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Portable Manifest-Backed Asset Catalog** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_manifest_authority, readme_portable_manifests, shotbox_assets_plan_ui_prototype [INFERRED 0.85]
- **Shared Category Metadata Flow** — asset_category_icon_sidebar_plan_category_configuration, readme_category_taxonomy_files, prototype_hdri_tagger_readme_allowed_taxonomy [INFERRED 0.85]
- **Responsive Background Workflows** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_refresh_worker, readme_preview_render_queue, shotbox_assets_plan_background_importer [INFERRED 0.75]
- **Physical Asset Category Icons** — src_universal_asset_library_ui_icons_object_object_cube, src_universal_asset_library_ui_icons_structure_building, src_universal_asset_library_ui_icons_terrain_mountain_landscape, src_universal_asset_library_ui_icons_vehicle_car, src_universal_asset_library_ui_icons_water_water_droplet [INFERRED 0.85]

## Communities (120 total, 21 thin omitted)

### Community 0 - "HdriTaggerWindow"
Cohesion: 0.05
Nodes (51): AnalysisRequest, AnalysisThread, HdriTaggerWindow, PullThread, QCheckBox, QMainWindow, Standalone Ollama-powered HDRI metadata tagger., main() (+43 more)

### Community 1 - "ImporterTab"
Cohesion: 0.05
Nodes (20): QListWidgetItem, _display_bytes(), ImporterTab, ImportSignals, ImportWorker, _paths_overlap(), PreflightWorker, Path (+12 more)

### Community 2 - "test_texture_preview.py"
Cohesion: 0.20
Nodes (23): default_template_path(), driver_path(), Path, render_texture_preview(), _sha256(), TexturePreviewMap, TexturePreviewRequest, _image() (+15 more)

### Community 3 - "LibraryError"
Cohesion: 0.16
Nodes (18): _asset_from_manifest(), _copy_verified(), _deadline_managed_path(), _directory_size(), LibraryError, RuntimeError, Render frame one outside the lock and atomically publish a VDB still., Export shared USDs for a VDB batch without rendering Karma locally. (+10 more)

### Community 4 - "CategoryConfigStore"
Cohesion: 0.20
Nodes (57): CategoryConfigStore, DuplicateConflict, HdriCandidate, MaterialPreflight, ModelCandidate, PreflightResult, RuntimeError, ScanCancelled (+49 more)

### Community 5 - "repository.py"
Cohesion: 0.09
Nodes (70): cached_catalog(), _apply_polyhaven_manifest(), _atomic_json(), _build_repaired_asset(), CancelToken, _claim_asset_destination(), _clean_tags(), _contains() (+62 more)

### Community 6 - "ual_houdini_bridge/actions.py"
Cohesion: 0.08
Nodes (61): Start the ShotBox Assets bridge after the Houdini 21 UI is ready., Start the ShotBox Assets bridge after the Houdini 22 UI is ready., ActionError, _base_type(), _build_materialx_graph(), _child_node(), _children(), _clear_children() (+53 more)

### Community 7 - "assets_tab.py"
Cohesion: 0.10
Nodes (53): QAbstractListModel, QTabBar, CategoryCatalog, AiGuessWorker, GuessConfirmationDialog, OllamaSetupDialog, Modal setup surface; expensive pull work remains in the global thread pool., AssetTypeTabs (+45 more)

### Community 9 - "test_ui.py"
Cohesion: 0.08
Nodes (65): scan_texture_folder(), image(), parametrize, Path, skipif, test_alpha_masked_filename_is_scanned_as_opacity(), test_asset_name_channel_word_does_not_override_trailing_map_or_preview(), test_asset_type_auto_detection_and_provider_override() (+57 more)

### Community 10 - "DetailPanel"
Cohesion: 0.06
Nodes (3): DetailPanel, _media_time(), test_model_conversion_dialog_and_inspector_expose_headless_workflow()

### Community 11 - "MaterialCandidate"
Cohesion: 0.14
Nodes (11): MaterialCandidate, _candidate_source_paths(), _material_source_bytes(), _portable_candidate_diagnostics(), _primary_file_records(), Type-neutral entry point; preflight_materials remains for compatibility., Type-neutral entry point; import_materials remains for compatibility., _source_manifest() (+3 more)

### Community 12 - "FakeNode"
Cohesion: 0.07
Nodes (26): FakeCategory, FakeGeometry, FakeHipFile, FakeHou, FakeNode, FakeParm, FakePrimitive, FakeType (+18 more)

### Community 13 - "LibraryRepository"
Cohesion: 0.09
Nodes (44): LibraryRepository, Apply narrow metadata changes against the latest manifest., Create one locked, indexed session for a sequence of metadata patches., image(), parametrize, Path, source_material(), test_asset_rating_patch_rejects_invalid_values() (+36 more)

### Community 14 - "ShotBox Assets"
Cohesion: 0.05
Nodes (48): AND Filter Semantics, Per-Type Category Configuration, Expandable Category Rail, Asset Category Icon Sidebar Plan, Fixed Category Icon Registry, Live Category Result Counts, Safe Category Configuration Fallback, Contextual Names for Numeric Stock Clips Plan (+40 more)

### Community 16 - "scan_stock_folder"
Cohesion: 0.06
Nodes (79): _alpha_state(), _display_name(), infer_stock_display_name(), _inside_preview_tree(), _integer(), _is_preview_directory_name(), _is_preview_path(), _matching_sidecars() (+71 more)

### Community 17 - "AiOrganiserDialog"
Cohesion: 0.12
Nodes (8): QPixmap, QTableWidgetItem, AiOrganiserDialog, _escape(), Path, QCheckBox, QDialog, QWidget

### Community 18 - "test_vdb_preview.py"
Cohesion: 0.05
Nodes (36): _debug(), export_batch(), main(), Path, _required_node(), _required_parm(), _validate_flattened_usd(), main() (+28 more)

### Community 19 - "scanner.py"
Cohesion: 0.15
Nodes (37): MapDeclaration, ArchiveSource, PreviewCandidate, _archive_name_tokens(), _archive_preview_pairs(), _archive_preview_score(), _assign_previews(), _base_color_fallback() (+29 more)

### Community 20 - "integrations/__init__.py"
Cohesion: 0.10
Nodes (29): Optional integrations with external DCC applications., _lod_rank(), model_export_label(), model_export_options(), ModelExportError, ModelExportFile, ModelExportTextureSet, _preferred_map() (+21 more)

### Community 21 - "LibraryVdbAsset"
Cohesion: 0.08
Nodes (5): LibraryHdriAsset, LibraryTextureAsset, LibraryVdbAsset, Publish managed mutations to the disposable index without risking library…, Scan only the canonical container for one asset type.

### Community 22 - "domain/__init__.py"
Cohesion: 0.13
Nodes (23): LibraryExtraFile, LibraryHdriFile, LibraryHdriVariant, LibraryMap, LibraryModelFile, LibraryModelTextureSet, LibraryProviderPackage, LibraryProviderPackageFile (+15 more)

### Community 23 - "shotbox_assets_bridge/actions.py"
Cohesion: 0.15
Nodes (41): ActionError, _active_background(), _assign_fbx_materials(), _build_principled_graph(), _configure_principled_material(), _copy_value(), create_texture_material(), _cursor_location() (+33 more)

### Community 24 - "hdri_scanner.py"
Cohesion: 0.16
Nodes (27): _assign_hdri_preview(), _declared_hdri_files(), _discover_hdri_roots(), _display_name(), _environment_resolution(), _hdri_metadata(), hdri_resolution_label(), Path (+19 more)

### Community 25 - "test_catalog_index.py"
Cohesion: 0.07
Nodes (24): Connection, CatalogError, CatalogWriter, decode_asset(), _decode_value(), _default_cache_root(), encode_asset(), _encode_value() (+16 more)

### Community 26 - ".replace"
Cohesion: 0.23
Nodes (11): stock_asset(), test_asset_inspector_ai_buttons_disable_without_still_preview(), test_assets_category_rail_filters_primary_categories_and_live_counts(), test_metadata_worker_indexes_flat_stock_manifest(), test_multiple_selected_vdbs_offer_bulk_still_and_turntable_previews(), test_only_generated_vdb_turntables_scrub_and_stock_still_autoplays(), test_stock_hover_delegate_frame_is_cleared_on_stop(), test_stock_hover_preview_scheduling_and_inspector_priority() (+3 more)

### Community 27 - "TagEditor"
Cohesion: 0.10
Nodes (8): Orientations, QLayout, QLayoutItem, QRect, FlowLayout, Small wrapping layout used by the tag-chip editor., TagEditor, test_tag_editor_adds_pasted_tags_deduplicates_and_removes()

### Community 28 - "BridgeServer"
Cohesion: 0.10
Nodes (15): register(), unregister(), Runtime package for the ShotBox Assets Blender extension., SHOTBOX_PT_assets_bridge, decode_payload(), encode_message(), _receive_exact(), receive_message() (+7 more)

### Community 29 - "model_scanner.py"
Cohesion: 0.13
Nodes (36): _basename(), _component_name(), _diagnostics_for_root(), _discover_model_roots(), _display_name(), _flatten_values(), _has_explicit_model_preview_signal(), _integer() (+28 more)

### Community 30 - "ModelExportPayload"
Cohesion: 0.19
Nodes (18): BlenderBridgeClient, BlenderBridgeError, BlenderBridgeResponse, BlenderSession, encode_message(), Any, Path, RuntimeError (+10 more)

### Community 31 - "houdini/bridge.py"
Cohesion: 0.09
Nodes (37): BridgeRequest, BridgeResponse, choose_hdri_file(), choose_vdb_variant(), _default_resolution(), encode_message(), HoudiniBridgeClient, HoudiniBridgeError (+29 more)

### Community 32 - "importer_tab.py"
Cohesion: 0.13
Nodes (25): _extract_rar_atomically(), _extract_zip_atomically(), Path, _safe_archive_member_path(), _safe_member_path(), unzip_all_zip_files(), ZipExtractionProgress, ZipExtractionSummary (+17 more)

### Community 33 - "TextureCardDelegate"
Cohesion: 0.16
Nodes (7): QPainter, QRectF, QSize, QStyledItemDelegate, Return the viewport-space rectangle painted as the card preview., TextureCardDelegate, test_vdb_detail_offers_deadline_regeneration_for_missing_preview()

### Community 34 - "BlenderPluginInstaller"
Cohesion: 0.19
Nodes (9): BlenderInstallation, BlenderPluginInstaller, BlenderPluginStatus, CompletedProcess, Path, config_path(), data_dir(), Path (+1 more)

### Community 35 - "SettingsStore"
Cohesion: 0.11
Nodes (13): QSettings, SettingsStore, MainWindow, QMainWindow, test_main_window_construction_defers_library_maintenance(), test_main_window_disables_write_tabs_during_background_asset_updates(), test_main_window_has_three_tabs_in_order(), test_settings_detects_abandoned_staging_for_confirmed_cleanup() (+5 more)

### Community 36 - ".__init__"
Cohesion: 0.10
Nodes (10): QComboBox, QLabel, QScrollArea, CircularSpinner, Path, QFrame, QToolButton, QWidget (+2 more)

### Community 37 - "blender_model_conversion_driver.py"
Cohesion: 0.20
Nodes (22): _arguments(), _axis_selection(), _build_material(), convert(), _copy_texture(), _import_source(), _link(), main() (+14 more)

### Community 38 - ".update_library"
Cohesion: 0.13
Nodes (13): _asset_container(), _asset_manifest_paths(), _asset_manifest_paths_for_type(), _canonical_metadata_category(), _managed_asset_manifest_paths(), _merge_preview_repair(), _normalized_values(), Validate manifests and safely update legacy HDRI, model, and Stock layouts. (+5 more)

### Community 39 - "adapters.py"
Cohesion: 0.24
Nodes (14): _as_int(), _flatten_tags(), JsonMetadataAdapter, _megascans_asset_type(), MegascansAdapter, MetadataFacts, normalize_channel(), PolyHavenAdapter (+6 more)

### Community 40 - "BlenderPreviewSession"
Cohesion: 0.08
Nodes (11): BlenderPreviewSession, Path, Popen, One background Blender process serving serial texture and HDRI jobs., server_path(), _FakeProcess, _InputStream, _OutputStream (+3 more)

### Community 41 - "categories.py"
Cohesion: 0.14
Nodes (14): _atomic_json(), _catalog_document(), category_icon_id(), CategoryDefinition, default_category_catalog(), _normalized_category_text(), _parse_catalog(), Path (+6 more)

### Community 42 - ".__init__"
Cohesion: 0.09
Nodes (13): BlenderBridgeSignals, BlenderBridgeWorker, HoudiniBridgeSignals, HoudiniBridgeWorker, LibraryInspectionSignals, LibraryUpdateWorker, MaintenanceSignals, MaintenanceWorker (+5 more)

### Community 43 - "StockHoverPreviewController"
Cohesion: 0.13
Nodes (6): QEvent, QModelIndex, QPersistentModelIndex, QListView, Own one muted decoder and paints its frames through the card delegate., StockHoverPreviewController

### Community 44 - "test_windows_auto_update.py"
Cohesion: 0.13
Nodes (37): Namespace, _arguments(), attempt_update(), _detail(), _github_repository(), is_expected_origin(), is_project_checkout(), main() (+29 more)

### Community 45 - "vdb_renderer.py"
Cohesion: 0.16
Nodes (29): normalize_vdb_turntable_workers(), default_template_path(), _drain_output(), driver_path(), _driver_payload(), _executable_name(), _failure(), _filename_token() (+21 more)

### Community 46 - "settings_tab.py"
Cohesion: 0.22
Nodes (9): normalize_blender_preview_workers(), normalize_executable_path(), normalize_library_path(), Return an absolute native path without resolving symlink aliases., _setting_bool(), _setting_int(), validate_library_path(), test_file_path_and_unreadable_directory_are_rejected() (+1 more)

### Community 47 - ".__init__"
Cohesion: 0.16
Nodes (5): QIcon, _category_icon(), QToolButton, QWidget, _setting_bool()

### Community 48 - "QuickLookPopup"
Cohesion: 0.12
Nodes (4): QShortcut, QuickLookPopup, Frameless, modeless still/video preview window., test_popup_formats_short_and_long_durations()

### Community 50 - "LibraryInspectionWorker"
Cohesion: 0.18
Nodes (7): LibraryInspectionResult, LibraryInspectionWorker, _lock_age(), Queue a coalesced maintenance inspection for the saved library., Start the post-paint audit unless the user already requested one., Inspect a potentially remote library without blocking the GUI thread., Ignore completion when Qt has already destroyed the receiver.

### Community 51 - "hdri_renderer.py"
Cohesion: 0.13
Nodes (21): BlenderPreviewSessionError, RuntimeError, compose_hdri_preview(), default_template_path(), driver_path(), Path, resolve_blender_executable(), _sha256() (+13 more)

### Community 52 - "test_blender_plugin.py"
Cohesion: 0.16
Nodes (18): Bpy, _fbx_request(), _model_request(), ModelBpy, payload(), Scene, standard_world(), test_bridge_handles_ping_from_blender_timer_without_threads() (+10 more)

### Community 54 - "scan_vdb_folder"
Cohesion: 0.16
Nodes (22): VdbFile, VdbVariant, _candidate(), _category_for(), _display_name(), _normalize_key(), _parse_vdb(), _ParsedVdb (+14 more)

### Community 56 - "LibraryModelAsset"
Cohesion: 0.14
Nodes (9): LibraryModelAsset, _lod_key(), Path, _resolution_key(), _asset(), Path, test_model_export_includes_managed_material_maps_for_houdini_sop(), test_model_export_rejects_unknown_missing_and_outside_paths() (+1 more)

### Community 57 - "previews/__init__.py"
Cohesion: 0.23
Nodes (21): resolve_ffmpeg(), deadline_debug(), deadline_driver_path(), deadline_frame_count(), deadline_frame_paths(), deadline_frame_signature(), _drain_output(), export_deadline_usds() (+13 more)

### Community 58 - "PanZoomViewport"
Cohesion: 0.22
Nodes (4): QPoint, PanZoomViewport, QWidget, Frameless viewport with pointer-centred zoom and drag panning.

### Community 59 - "Links"
Cohesion: 0.15
Nodes (5): Link, Links, Node, Nodes, Socket

### Community 60 - "TextureFilterModel"
Cohesion: 0.18
Nodes (6): QSortFilterProxyModel, _asset_filter_categories(), _largest_resolution(), _rating_filter_matches(), TextureFilterModel, test_rating_filter_sort_and_star_toggle()

### Community 61 - "ModelAssetRescanDialog"
Cohesion: 0.21
Nodes (4): _human_size(), ModelAssetRescanDialog, ModelConversionDialog, QDialog

### Community 62 - "sample_textures"
Cohesion: 0.29
Nodes (7): sample_textures(), TextureAsset, test_category_and_channel_filters_are_combined(), test_query_is_trimmed_and_case_insensitive(), test_sample_assets_have_unique_ids_and_required_maps(), test_search_matches_names_tags_categories_and_channels(), test_texture_asset_is_immutable()

### Community 63 - "scan_model_folder"
Cohesion: 0.38
Nodes (12): scan_model_folder(), image(), model_source(), Path, test_fix_library_registers_manual_previews_without_managed_placeholder(), test_model_preview_discovery_requires_preview_evidence(), test_model_preview_discovery_uses_folders_names_and_rejects_texture_images(), test_model_preview_metadata_uses_relative_path_when_basenames_repeat() (+4 more)

### Community 64 - "FakePopup"
Cohesion: 0.19
Nodes (10): app(), _asset(), FakePopup, fixture, Path, QObject, test_asset_media_ignores_missing_previews(), test_asset_media_prefers_one_still_and_adds_motion_preview() (+2 more)

### Community 65 - "OllamaClient"
Cohesion: 0.06
Nodes (46): Local vision classification used by the catalog and standalone prototype., CategoryGuess, Classification, _confidence_and_rationale(), OllamaClient, OllamaError, OllamaStatus, _prompt() (+38 more)

### Community 66 - "CatalogRefreshWorker"
Cohesion: 0.16
Nodes (12): CatalogRefreshSignals, CatalogRefreshWorker, CatalogSectionResult, QObject, QRunnable, Prioritized, cancellable QRunnable wrapper around catalog refreshes., Emit unless Qt has already torn down the worker's signal object., Refresh one complete section while retaining last-good records on parse errors. (+4 more)

### Community 67 - "blender_texture_driver.py"
Cohesion: 0.36
Nodes (9): arguments(), _build_material(), configure_cycles_gpu(), _legacy_map_records(), main(), _map_records(), _material_actions(), Executed inside Blender; do not import bpy from the desktop application. (+1 more)

### Community 68 - "importer/__init__.py"
Cohesion: 0.12
Nodes (32): detect_asset_type(), Path, Return a conservative importer mode and a short user-facing reason., loose_hdri_key(), Collapse size suffixes so `name.exr` and `name_sm.exr` are one HDRI asset., _emit(), _loose_hdri_group(), _loose_texture() (+24 more)

### Community 69 - "Material"
Cohesion: 0.22
Nodes (3): Material, Tree, World

### Community 70 - "stock_video.py"
Cohesion: 0.30
Nodes (13): StockMediaInfo, StockPreviewProfile, CancellationToken, generate_midpoint_thumbnail(), generate_stock_preview(), Path, Protocol, Queue (+5 more)

### Community 71 - "CenterResizeGrip"
Cohesion: 0.27
Nodes (7): QMouseEvent, QSizeGrip, QSlider, CenterResizeGrip, Seek immediately when the user clicks or drags on the timeline., Resize a frameless popup around its centre point., SeekSlider

### Community 73 - "quick_look.py"
Cohesion: 0.15
Nodes (6): Path, quick_look_media_for_asset(), QuickLookEntry, QuickLookMedia, Quick Look-style previews for assets in the library browser., Resolve the managed still and video previews belonging to an asset.

### Community 74 - "blender_model_rescan_driver.py"
Cohesion: 0.53
Nodes (5): _arguments(), _inside_asset(), main(), Blender-side validator for manually added managed USD files., validate()

### Community 75 - ".new"
Cohesion: 0.31
Nodes (3): ModelCollection, ModelCollections, ModelWmOps

### Community 76 - "blender_hdri_driver.py"
Cohesion: 0.53
Nodes (5): arguments(), configure_cycles_gpu(), main(), Executed inside Blender; do not import bpy from the desktop application., render_job()

### Community 77 - "batch_metadata.py"
Cohesion: 0.36
Nodes (4): BatchMetadataItemResult, BatchMetadataProgress, BatchMetadataSignals, QObject

### Community 78 - "Object Cube Icon"
Cohesion: 0.67
Nodes (3): Object Cube Icon, Building Structure Icon, Car Vehicle Icon

### Community 92 - "polyhaven.py"
Cohesion: 0.15
Nodes (25): build_download_plan(), _case_value(), _file_record(), _labels(), load_metadata_documents(), options_from_catalog(), PolyHavenPackage, PolyHavenRemoteFile (+17 more)

### Community 93 - "AssetsTab"
Cohesion: 0.07
Nodes (11): AssetsTab, Queue missing previews after imports have been published., test_assets_splitter_is_wide_and_persists_state(), test_category_rail_expansion_persists_and_asset_type_switch_resets_filter(), test_cold_catalog_shows_loading_page_without_synchronous_scan(), test_deadline_regeneration_discards_stale_local_preview_lock(), test_importer_and_catalog_share_asset_type_tabs(), test_model_importer_review_and_catalog_usd_controls() (+3 more)

### Community 94 - "test_model_conversion.py"
Cohesion: 0.28
Nodes (15): _imported_repository(), Path, _source_model(), _successful_runner(), test_conversion_request_rejects_unknown_source_orientation_and_missing_maps(), test_missing_manual_model_remains_loadable_for_rescan(), test_model_rescan_adopts_reviewed_manual_files_and_preserves_manual_preference(), test_model_rescan_discovers_every_supported_model_extension() (+7 more)

### Community 95 - "AssetRecord"
Cohesion: 0.10
Nodes (3): AssetRecord, Apply ShotBox-managed mutations immediately without rescanning., Replace one catalog record without resetting the visible grid.

### Community 96 - "AppSettings"
Cohesion: 0.37
Nodes (12): AppSettings, make_store(), parametrize, test_blender_and_hdri_render_preferences_round_trip(), test_blender_parallel_render_count_is_clamped(), test_deadline_husk_paths_round_trip(), test_defaults_and_round_trip(), test_empty_library_path_is_valid_and_persisted() (+4 more)

### Community 97 - "blender_preview_server.py"
Cohesion: 0.48
Nodes (6): _discover_gpu(), _emit(), _load_module(), main(), _open_template(), Persistent preview protocol server executed inside Blender.

### Community 98 - ".__enter__"
Cohesion: 0.20
Nodes (3): _lock_is_local_stale(), _pid_exists(), _read_lock_payload()

### Community 99 - "model_conversion.py"
Cohesion: 0.19
Nodes (17): _conversion_map(), _conversion_resolution(), _last_log_line(), _lod_rank(), _managed_file(), model_conversion_sources(), ModelConversionMap, ModelConversionRequest (+9 more)

### Community 100 - "application_arguments"
Cohesion: 0.47
Nodes (5): application_arguments(), main(), Remove flags consumed by the source-checkout launcher., Use the project's virtual environment when one is available., _restart_in_project_venv()

### Community 101 - "Q: Implement the planned shared five-star asset rating system"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Implement the planned shared five-star asset rating system, Source Nodes

### Community 102 - "Q: Make the five-star rating apply instantly and look cute"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Make the five-star rating apply instantly and look cute, Source Nodes

### Community 103 - "Q: Allow rating multiple assets while background saves are still running"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Allow rating multiple assets while background saves are still running, Source Nodes

### Community 104 - "Q: Make the rating control look really cute"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Make the rating control look really cute, Source Nodes

### Community 105 - "Q: Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras, Source Nodes

### Community 106 - "Q: with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras."
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras., Source Nodes

### Community 107 - "Q: PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching, Source Nodes

### Community 108 - "Q: its still pretty slow would it be faster on cpu?"
Cohesion: 0.40
Nodes (4): Answer, Outcome, Q: its still pretty slow would it be faster on cpu?, Source Nodes

### Community 109 - "_classification_preview"
Cohesion: 0.27
Nodes (3): _classification_preview(), _merge_ai_tags(), test_ai_tag_merge_is_case_insensitive_and_stable()

### Community 116 - "model_rescan.py"
Cohesion: 0.18
Nodes (16): _blender_version(), validate_model_conversion_blender(), _check_cancel(), _component_name(), _discoverable_model(), inventory_model_asset(), _lod_label(), _model_role() (+8 more)

### Community 120 - "render_hdri_preview"
Cohesion: 0.29
Nodes (16): QApplication, _failure(), _filename_token(), HdriPreviewRequest, HdriPreviewResult, render_hdri_preview(), _hdr(), _image() (+8 more)

### Community 123 - "list"
Cohesion: 0.24
Nodes (6): list, ChildLinks, Data, Materials, ObjectLinks, Worlds

### Community 125 - "universal_asset_library/app.py"
Cohesion: 0.23
Nodes (8): Public ShotBox Assets application namespace., create_application(), main(), _migrate_legacy_settings(), Preserve user preferences from the application's former product name., test_shotbox_name_migrates_legacy_application_settings(), app(), fixture

## Knowledge Gaps
- **52 isolated node(s):** `shotbox-assets`, `run_vfx_asset_library.sh script`, `Answer`, `Outcome`, `Source Nodes` (+47 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Work-memory lessons

**Preferred sources** — corroborated by past sessions; start here.
- `AssetsTab` (4× useful, score=3.989435012) _(code changed — re-verify)_
- `TextureCardDelegate` (3× useful, score=2.98959002) _(code changed — re-verify)_
- `LibraryRepository` (2× useful, score=1.995997822) _(code changed — re-verify)_
- `theme.py` (2× useful, score=1.993536064) _(code changed — re-verify)_
- `AssetMetadataPatch` (2× useful, score=1.99285496) _(code changed — re-verify)_
- `DetailPanel` (2× useful, score=1.992690143) _(code changed — re-verify)_

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LibraryRepository` connect `LibraryRepository` to `HdriTaggerWindow`, `ImporterTab`, `test_texture_preview.py`, `LibraryError`, `CategoryConfigStore`, `repository.py`, `assets_tab.py`, `.clear`, `test_ui.py`, `DetailPanel`, `MaterialCandidate`, `scan_stock_folder`, `test_vdb_preview.py`, `LibraryVdbAsset`, `hdri_scanner.py`, `.replace`, `importer_tab.py`, `TextureCardDelegate`, `SettingsStore`, `.update_library`, `.__init__`, `settings_tab.py`, `.start`, `LibraryInspectionWorker`, `scan_vdb_folder`, `._download_polyhaven`, `previews/__init__.py`, `scan_model_folder`, `CatalogRefreshWorker`, `importer/__init__.py`, `batch_metadata.py`, `polyhaven.py`, `AssetsTab`, `test_model_conversion.py`, `AssetRecord`, `.__enter__`, `model_rescan.py`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **Why does `CategoryCatalog` connect `assets_tab.py` to `TextureCardDelegate`, `LibraryError`, `CategoryConfigStore`, `importer/__init__.py`, `repository.py`, `.update_library`, `.__init__`, `categories.py`, `test_ui.py`, `DetailPanel`, `StockHoverPreviewController`, `LibraryRepository`, `scanner.py`, `ModelAssetRescanDialog`, `TagEditor`, `TextureFilterModel`, `AssetsTab`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Why does `CatalogIndex` connect `CategoryConfigStore` to `CatalogRefreshWorker`, `LibraryError`, `.__init__`, `repository.py`, `assets_tab.py`, `LibraryRepository`, `batch_metadata.py`, `domain/__init__.py`, `test_catalog_index.py`, `.replace`?**
  _High betweenness centrality (0.071) - this node is a cross-community bridge._
- **Are the 154 inferred relationships involving `LibraryRepository` (e.g. with `apply_classification()` and `CatalogRefreshSignals`) actually correct?**
  _`LibraryRepository` has 154 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `AssetsTab` (e.g. with `CategoryCatalog` and `AiGuessWorker`) actually correct?**
  _`AssetsTab` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `CancelToken` (e.g. with `CatalogRefreshSignals` and `CatalogRefreshWorker`) actually correct?**
  _`CancelToken` has 35 INFERRED edges - model-reasoned connections that need verification._
- **Are the 76 inferred relationships involving `scan_texture_folder()` (e.g. with `scan_mixed_folder()` and `.run()`) actually correct?**
  _`scan_texture_folder()` has 76 INFERRED edges - model-reasoned connections that need verification._