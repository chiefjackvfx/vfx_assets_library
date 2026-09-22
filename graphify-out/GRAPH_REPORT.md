# Graph Report - vfx_assets_library  (2026-09-22)

## Corpus Check
- 151 files · ~169,865 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3144 nodes · 10961 edges · 121 communities (96 shown, 25 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 2017 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d840ee53`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- AssetRecord
- ImporterTab
- test_texture_preview.py
- repository.py
- CategoryCatalog
- MaterialCandidate
- ual_houdini_bridge/actions.py
- assets_tab.py
- .clear
- LibraryRepository
- DetailPanel
- AssetsTab
- test_houdini_plugin.py
- test_ui.py
- ShotBox Assets
- SettingsTab
- scan_stock_folder
- AiOrganiserDialog
- PolyHavenSyncPreferences
- scanner.py
- model_export.py
- LibraryTextureAsset
- domain/__init__.py
- shotbox_assets_bridge/actions.py
- mixed_scanner.py
- CancelToken
- importer/__init__.py
- TagEditor
- BridgeServer
- model_scanner.py
- ModelExportPayload
- houdini/bridge.py
- test_importer_scanner.py
- TextureCardDelegate
- BlenderPluginInstaller
- MainWindow
- .__init__
- blender_model_conversion_driver.py
- AppSettings
- adapters.py
- BlenderPreviewSession
- hdri_renderer.py
- BlenderBridgeWorker
- PolyHavenPanel
- test_windows_auto_update.py
- settings_tab.py
- settings.py
- CategoryRail
- QuickLookPopup
- .start
- LibraryInspectionWorker
- test_polyhaven.py
- test_blender_plugin.py
- .get
- scan_vdb_folder
- AssetQuickLookController
- LibraryModelAsset
- previews/__init__.py
- PanZoomViewport
- Node
- test_vdb_preview.py
- ModelAssetRescanDialog
- sample_textures
- test_model_importer.py
- FakePopup
- OllamaClient
- ._open_ai_organiser
- blender_texture_driver.py
- universal_asset_library/app.py
- Links
- stock_video.py
- CenterResizeGrip
- Images
- quick_look.py
- blender_model_rescan_driver.py
- .new
- blender_hdri_driver.py
- list
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
- PolyHavenClient
- TextureListModel
- test_model_conversion.py
- Material
- scan_hdri_folder
- blender_preview_server.py
- .__enter__
- integrations/__init__.py
- application_arguments
- Q: Implement the planned shared five-star asset rating system
- Q: Make the five-star rating apply instantly and look cute
- Q: Allow rating multiple assets while background saves are still running
- Q: Make the rating control look really cute
- Q: Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras
- Q: with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras.
- Q: PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching
- Q: its still pretty slow would it be faster on cpu?
- test_polyhaven_ui.py
- ObjectLinks
- run_vfx_asset_library.sh script
- scripts/__init__.py
- .__init__
- model_rescan.py
- scan_atlas_folder
- Path
- render_hdri_preview
- decode_asset

## God Nodes (most connected - your core abstractions)
1. `LibraryRepository` - 274 edges
2. `AssetsTab` - 218 edges
3. `CancelToken` - 118 edges
4. `CategoryCatalog` - 95 edges
5. `scan_texture_folder()` - 94 edges
6. `DetailPanel` - 91 edges
7. `ImporterTab` - 87 edges
8. `CategoryConfigStore` - 85 edges
9. `SettingsTab` - 82 edges
10. `LibraryError` - 79 edges

## Surprising Connections (you probably didn't know these)
- `test_asset_type_auto_detection_and_provider_override()` --calls--> `detect_asset_type()`  [INFERRED]
  tests/test_importer_scanner.py → src/universal_asset_library/importer/detection.py
- `Expandable Category Rail` --semantically_similar_to--> `Asset Catalog Category Rail`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md
- `Per-Type Category Configuration` --semantically_similar_to--> `Portable Category Taxonomy Files`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md
- `Root-Relative Folder Context` --semantically_similar_to--> `Stock Folder and Filename Taxonomy`  [INFERRED] [semantically similar]
  Contextual Names for Numeric Stock Clips plan.md → README.md
- `Local SQLite Catalog Index` --semantically_similar_to--> `Deferred SQLite Full-Text Indexing`  [INFERRED] [semantically similar]
  Fast Indexed Asset Catalog with Responsive Loading Screen PLAN.md → SHOTBOX_ASSETS_PLAN.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Portable Manifest-Backed Asset Catalog** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_manifest_authority, readme_portable_manifests, shotbox_assets_plan_ui_prototype [INFERRED 0.85]
- **Shared Category Metadata Flow** — asset_category_icon_sidebar_plan_category_configuration, readme_category_taxonomy_files, prototype_hdri_tagger_readme_allowed_taxonomy [INFERRED 0.85]
- **Responsive Background Workflows** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_refresh_worker, readme_preview_render_queue, shotbox_assets_plan_background_importer [INFERRED 0.75]
- **Physical Asset Category Icons** — src_universal_asset_library_ui_icons_object_object_cube, src_universal_asset_library_ui_icons_structure_building, src_universal_asset_library_ui_icons_terrain_mountain_landscape, src_universal_asset_library_ui_icons_vehicle_car, src_universal_asset_library_ui_icons_water_water_droplet [INFERRED 0.85]

## Communities (121 total, 25 thin omitted)

### Community 0 - "AssetRecord"
Cohesion: 0.05
Nodes (50): AnalysisRequest, AnalysisThread, HdriTaggerWindow, PullThread, QCheckBox, QMainWindow, Standalone Ollama-powered HDRI metadata tagger., main() (+42 more)

### Community 1 - "ImporterTab"
Cohesion: 0.06
Nodes (14): QListWidgetItem, _cleanup_scan_workspaces(), _display_bytes(), ImporterTab, _paths_overlap(), Path, QWidget, Compatibility wrapper for callers that only configure HDRI previews. (+6 more)

### Community 2 - "test_texture_preview.py"
Cohesion: 0.20
Nodes (23): default_template_path(), driver_path(), Path, render_texture_preview(), _sha256(), TexturePreviewMap, TexturePreviewRequest, _image() (+15 more)

### Community 3 - "repository.py"
Cohesion: 0.07
Nodes (89): _apply_polyhaven_manifest(), _asset_container(), _asset_from_manifest(), _asset_manifest_paths(), _asset_manifest_paths_for_type(), _atomic_json(), _build_repaired_asset(), _canonical_metadata_category() (+81 more)

### Community 4 - "CategoryCatalog"
Cohesion: 0.13
Nodes (70): _atomic_json(), _catalog_document(), category_icon_id(), CategoryCatalog, CategoryConfigStore, CategoryDefinition, default_category_catalog(), _normalized_category_text() (+62 more)

### Community 5 - "MaterialCandidate"
Cohesion: 0.09
Nodes (18): MaterialCandidate, SourceCompanion, _candidate_source_paths(), _contains(), _material_source_bytes(), _portable_candidate_diagnostics(), _primary_file_records(), Type-neutral entry point; preflight_materials remains for compatibility. (+10 more)

### Community 6 - "ual_houdini_bridge/actions.py"
Cohesion: 0.08
Nodes (61): Start the ShotBox Assets bridge after the Houdini 21 UI is ready., Start the ShotBox Assets bridge after the Houdini 22 UI is ready., ActionError, _base_type(), _build_materialx_graph(), _child_node(), _children(), _clear_children() (+53 more)

### Community 7 - "assets_tab.py"
Cohesion: 0.08
Nodes (47): QTabBar, AiGuessWorker, GuessConfirmationDialog, OllamaSetupDialog, QDialog, Modal setup surface; expensive pull work remains in the global thread pool., AssetTypeTabs, Compact shared section switch used by the catalog and importer. (+39 more)

### Community 9 - "LibraryRepository"
Cohesion: 0.09
Nodes (46): LibraryRepository, Discover manifests only inside one canonical asset container., Apply narrow metadata changes against the latest manifest., Create one locked, indexed session for a sequence of metadata patches., image(), parametrize, Path, source_material() (+38 more)

### Community 10 - "DetailPanel"
Cohesion: 0.06
Nodes (3): DetailPanel, _media_time(), _resolution_number()

### Community 11 - "AssetsTab"
Cohesion: 0.06
Nodes (13): AssetsTab, Make the inspector's current batch target visible to the user., Resolve a right-hand inspector action to its intended selection., Queue missing previews after imports have been published., test_assets_splitter_is_wide_and_persists_state(), test_category_rail_expansion_persists_and_asset_type_switch_resets_filter(), test_cold_catalog_shows_loading_page_without_synchronous_scan(), test_deadline_regeneration_discards_stale_local_preview_lock() (+5 more)

### Community 12 - "test_houdini_plugin.py"
Cohesion: 0.06
Nodes (33): FakeCategory, FakeGeometry, FakeHipFile, FakeHou, FakeNode, FakeParm, FakePrimitive, FakeType (+25 more)

### Community 13 - "test_ui.py"
Cohesion: 0.09
Nodes (54): scan_texture_folder(), parametrize, stock_asset(), test_ai_category_and_tags_confirm_through_repository(), test_ai_confirmation_cancel_does_not_write(), test_ai_organiser_targets_fallback_categories_and_excludes_them(), test_asset_inspector_ai_buttons_use_managed_preview(), test_asset_trash_button_confirms_and_moves_managed_folder() (+46 more)

### Community 14 - "ShotBox Assets"
Cohesion: 0.05
Nodes (48): AND Filter Semantics, Per-Type Category Configuration, Expandable Category Rail, Asset Category Icon Sidebar Plan, Fixed Category Icon Registry, Live Category Result Counts, Safe Category Configuration Fallback, Contextual Names for Numeric Stock Clips Plan (+40 more)

### Community 15 - "SettingsTab"
Cohesion: 0.08
Nodes (3): QWidget, Compatibility wrapper for callers that request a maintenance refresh., SettingsTab

### Community 16 - "scan_stock_folder"
Cohesion: 0.06
Nodes (81): StockMediaInfo, StockPreviewCandidate, _alpha_state(), _display_name(), infer_stock_display_name(), _inside_preview_tree(), _integer(), _is_preview_directory_name() (+73 more)

### Community 17 - "AiOrganiserDialog"
Cohesion: 0.19
Nodes (3): AiOrganiserDialog, _asset_type_label(), QCheckBox

### Community 18 - "PolyHavenSyncPreferences"
Cohesion: 0.10
Nodes (28): PolyHavenSyncReview, PolyHavenSyncService, polyhaven_download_directory(), PolyHavenSyncPreferences, Path, Resolve the user's Downloads location without creating any directories., PolyHavenSyncWorker, QComboBox (+20 more)

### Community 19 - "scanner.py"
Cohesion: 0.15
Nodes (34): _discover_model_roots(), scan_model_folder(), ArchiveSource, PreviewCandidate, ScanProgress, _archive_name_tokens(), _archive_preview_pairs(), _archive_preview_score() (+26 more)

### Community 20 - "model_export.py"
Cohesion: 0.09
Nodes (28): _lod_rank(), model_export_label(), model_export_options(), ModelExportError, ModelExportFile, ModelExportTextureSet, _preferred_map(), prepare_model_export() (+20 more)

### Community 21 - "LibraryTextureAsset"
Cohesion: 0.07
Nodes (6): LibraryHdriAsset, LibraryStockAsset, LibraryTextureAsset, Publish managed mutations to the disposable index without risking library…, Scan only the canonical container for one asset type., test_stock_inspector_has_player_controls_and_no_dcc_footer()

### Community 22 - "domain/__init__.py"
Cohesion: 0.11
Nodes (30): LibraryExtraFile, LibraryHdriFile, LibraryHdriVariant, LibraryMap, LibraryModelFile, LibraryModelTextureSet, LibraryProviderPackage, LibraryProviderPackageFile (+22 more)

### Community 23 - "shotbox_assets_bridge/actions.py"
Cohesion: 0.15
Nodes (41): ActionError, _active_background(), _assign_fbx_materials(), _build_principled_graph(), _configure_principled_material(), _copy_value(), create_texture_material(), _cursor_location() (+33 more)

### Community 24 - "mixed_scanner.py"
Cohesion: 0.13
Nodes (31): detect_asset_type(), Path, Return a conservative importer mode and a short user-facing reason., _assign_hdri_preview(), _declared_hdri_files(), _discover_hdri_roots(), _display_name(), _environment_resolution() (+23 more)

### Community 25 - "CancelToken"
Cohesion: 0.10
Nodes (27): CatalogIndex, CatalogRecord, CatalogWriter, Disposable local SQLite acceleration index for one portable library., Return local manifest hints for asset IDs without scanning the library., Reuse one local SQLite connection while committing every item., CatalogRefreshSignals, CatalogRefreshWorker (+19 more)

### Community 26 - "importer/__init__.py"
Cohesion: 0.15
Nodes (25): _extract_rar_atomically(), _extract_zip_atomically(), Path, _safe_archive_member_path(), _safe_member_path(), unzip_all_zip_files(), ZipExtractionProgress, ZipExtractionSummary (+17 more)

### Community 27 - "TagEditor"
Cohesion: 0.10
Nodes (9): Orientations, QLayout, QLayoutItem, QRect, QSize, FlowLayout, Small wrapping layout used by the tag-chip editor., TagEditor (+1 more)

### Community 28 - "BridgeServer"
Cohesion: 0.10
Nodes (15): register(), unregister(), Runtime package for the ShotBox Assets Blender extension., SHOTBOX_PT_assets_bridge, decode_payload(), encode_message(), _receive_exact(), receive_message() (+7 more)

### Community 29 - "model_scanner.py"
Cohesion: 0.12
Nodes (40): _basename(), _component_name(), _diagnostics_for_root(), _display_name(), _flatten_values(), _has_explicit_model_preview_signal(), _integer(), _lod_label() (+32 more)

### Community 30 - "ModelExportPayload"
Cohesion: 0.20
Nodes (17): BlenderBridgeClient, BlenderBridgeError, BlenderBridgeResponse, BlenderSession, encode_message(), Any, Path, RuntimeError (+9 more)

### Community 31 - "houdini/bridge.py"
Cohesion: 0.09
Nodes (37): BridgeRequest, BridgeResponse, choose_hdri_file(), choose_vdb_variant(), _default_resolution(), encode_message(), HoudiniBridgeClient, HoudiniBridgeError (+29 more)

### Community 32 - "test_importer_scanner.py"
Cohesion: 0.13
Nodes (27): image(), parametrize, Path, skipif, test_alpha_masked_filename_is_scanned_as_opacity(), test_asset_name_channel_word_does_not_override_trailing_map_or_preview(), test_asset_type_auto_detection_and_provider_override(), test_collection_manifest_does_not_merge_materials() (+19 more)

### Community 33 - "TextureCardDelegate"
Cohesion: 0.09
Nodes (14): QEvent, QModelIndex, QPainter, QPersistentModelIndex, QPixmap, QRectF, QStyledItemDelegate, QListView (+6 more)

### Community 34 - "BlenderPluginInstaller"
Cohesion: 0.18
Nodes (10): BlenderInstallation, BlenderPluginInstaller, BlenderPluginStatus, CompletedProcess, Path, config_path(), data_dir(), Path (+2 more)

### Community 35 - "MainWindow"
Cohesion: 0.12
Nodes (6): QCloseEvent, MainWindow, QMainWindow, test_close_is_blocked_while_asset_metadata_update_runs(), test_main_window_disables_write_tabs_during_background_asset_updates(), test_main_window_has_three_tabs_in_order()

### Community 36 - ".__init__"
Cohesion: 0.13
Nodes (13): QLabel, QScrollArea, BusyOverlay, CircularSpinner, CollapsibleSection, QComboBox, QFrame, QToolButton (+5 more)

### Community 37 - "blender_model_conversion_driver.py"
Cohesion: 0.20
Nodes (22): _arguments(), _axis_selection(), _build_material(), convert(), _copy_texture(), _import_source(), _link(), main() (+14 more)

### Community 38 - "AppSettings"
Cohesion: 0.10
Nodes (35): QSettings, QSpinBox, QWheelEvent, _migrate_legacy_settings(), Preserve user preferences from the application's former product name., AppSettings, SettingsStore, HoudiniBridgeSignals (+27 more)

### Community 39 - "adapters.py"
Cohesion: 0.24
Nodes (14): _as_int(), _flatten_tags(), JsonMetadataAdapter, MapDeclaration, _megascans_asset_type(), MegascansAdapter, MetadataFacts, PolyHavenAdapter (+6 more)

### Community 40 - "BlenderPreviewSession"
Cohesion: 0.08
Nodes (11): BlenderPreviewSession, Path, Popen, One background Blender process serving serial texture and HDRI jobs., server_path(), _FakeProcess, _InputStream, _OutputStream (+3 more)

### Community 41 - "hdri_renderer.py"
Cohesion: 0.13
Nodes (21): BlenderPreviewSessionError, RuntimeError, compose_hdri_preview(), default_template_path(), driver_path(), Path, resolve_blender_executable(), _sha256() (+13 more)

### Community 42 - "BlenderBridgeWorker"
Cohesion: 0.10
Nodes (11): BlenderBridgeSignals, BlenderBridgeWorker, HoudiniBridgeWorker, LibraryInspectionSignals, LibraryUpdateWorker, MaintenanceSignals, MaintenanceWorker, QObject (+3 more)

### Community 43 - "PolyHavenPanel"
Cohesion: 0.18
Nodes (3): PolyHavenPanel, QFrame, size_text()

### Community 44 - "test_windows_auto_update.py"
Cohesion: 0.13
Nodes (37): Namespace, _arguments(), attempt_update(), _detail(), _github_repository(), is_expected_origin(), is_project_checkout(), main() (+29 more)

### Community 45 - "settings_tab.py"
Cohesion: 0.15
Nodes (29): normalize_vdb_turntable_workers(), default_template_path(), _drain_output(), driver_path(), _driver_payload(), _executable_name(), _failure(), _filename_token() (+21 more)

### Community 46 - "settings.py"
Cohesion: 0.24
Nodes (9): normalize_blender_preview_workers(), normalize_executable_path(), normalize_library_path(), Return an absolute native path without resolving symlink aliases., _setting_bool(), _setting_int(), validate_library_path(), test_file_path_and_unreadable_directory_are_rejected() (+1 more)

### Community 47 - "CategoryRail"
Cohesion: 0.18
Nodes (7): QIcon, _category_icon(), CategoryRail, QFrame, QToolButton, QWidget, _setting_bool()

### Community 48 - "QuickLookPopup"
Cohesion: 0.13
Nodes (4): QShortcut, Path, QuickLookPopup, Frameless, modeless still/video preview window.

### Community 50 - "LibraryInspectionWorker"
Cohesion: 0.18
Nodes (7): LibraryInspectionResult, LibraryInspectionWorker, _lock_age(), Queue a coalesced maintenance inspection for the saved library., Start the post-paint audit unless the user already requested one., Inspect a potentially remote library without blocking the GUI thread., Ignore completion when Qt has already destroyed the receiver.

### Community 51 - "test_polyhaven.py"
Cohesion: 0.33
Nodes (9): FakeClient, _image(), Path, _record(), test_client_sends_identity_and_rejects_untrusted_download_host(), test_hdri_download_adds_hdr_and_exr_without_replacing_local_variants(), test_model_usd_resolutions_coexist_and_latest_is_preferred(), test_texture_maps_and_materialx_are_published_and_cataloged() (+1 more)

### Community 52 - "test_blender_plugin.py"
Cohesion: 0.18
Nodes (17): Bpy, _fbx_request(), _model_request(), ModelBpy, payload(), Scene, standard_world(), test_bridge_handles_ping_from_blender_timer_without_threads() (+9 more)

### Community 54 - "scan_vdb_folder"
Cohesion: 0.15
Nodes (23): VdbFile, VdbVariant, _image_dimensions(), _candidate(), _category_for(), _display_name(), _normalize_key(), _parse_vdb() (+15 more)

### Community 55 - "AssetQuickLookController"
Cohesion: 0.17
Nodes (4): ModelConversionDialog, AssetQuickLookController, QObject, Connect the asset list selection to a reusable Quick Look popup.

### Community 56 - "LibraryModelAsset"
Cohesion: 0.14
Nodes (9): LibraryModelAsset, _lod_key(), Path, _resolution_key(), _asset(), Path, test_model_export_includes_managed_material_maps_for_houdini_sop(), test_model_export_rejects_unknown_missing_and_outside_paths() (+1 more)

### Community 57 - "previews/__init__.py"
Cohesion: 0.24
Nodes (21): resolve_ffmpeg(), deadline_debug(), deadline_driver_path(), deadline_frame_count(), deadline_frame_paths(), deadline_frame_signature(), _drain_output(), export_deadline_usds() (+13 more)

### Community 58 - "PanZoomViewport"
Cohesion: 0.22
Nodes (4): QPoint, PanZoomViewport, QWidget, Frameless viewport with pointer-centred zoom and drag panning.

### Community 60 - "test_vdb_preview.py"
Cohesion: 0.05
Nodes (36): _debug(), export_batch(), main(), Path, _required_node(), _required_parm(), _validate_flattened_usd(), main() (+28 more)

### Community 62 - "sample_textures"
Cohesion: 0.29
Nodes (7): sample_textures(), TextureAsset, test_category_and_channel_filters_are_combined(), test_query_is_trimmed_and_case_insensitive(), test_sample_assets_have_unique_ids_and_required_maps(), test_search_matches_names_tags_categories_and_channels(), test_texture_asset_is_immutable()

### Community 63 - "test_model_importer.py"
Cohesion: 0.33
Nodes (11): image(), model_source(), Path, test_fix_library_registers_manual_previews_without_managed_placeholder(), test_model_preview_discovery_requires_preview_evidence(), test_model_preview_discovery_uses_folders_names_and_rejects_texture_images(), test_model_preview_metadata_uses_relative_path_when_basenames_repeat(), test_model_scan_prefers_usd_and_excludes_renderer_archives_and_hidden_data() (+3 more)

### Community 64 - "FakePopup"
Cohesion: 0.14
Nodes (11): app(), _asset(), FakePopup, fixture, Path, QObject, test_asset_media_ignores_missing_previews(), test_asset_media_prefers_one_still_and_adds_motion_preview() (+3 more)

### Community 65 - "OllamaClient"
Cohesion: 0.05
Nodes (48): Local vision classification used by the catalog and standalone prototype., CategoryGuess, Classification, _confidence_and_rationale(), OllamaClient, OllamaError, OllamaStatus, _prompt() (+40 more)

### Community 66 - "._open_ai_organiser"
Cohesion: 0.19
Nodes (5): _classification_preview(), _merge_ai_tags(), Path, test_ai_tag_merge_is_case_insensitive_and_stable(), test_asset_inspector_ai_buttons_disable_without_still_preview()

### Community 67 - "blender_texture_driver.py"
Cohesion: 0.36
Nodes (9): arguments(), _build_material(), configure_cycles_gpu(), _legacy_map_records(), main(), _map_records(), _material_actions(), Executed inside Blender; do not import bpy from the desktop application. (+1 more)

### Community 68 - "universal_asset_library/app.py"
Cohesion: 0.31
Nodes (5): Public ShotBox Assets application namespace., create_application(), main(), app(), fixture

### Community 70 - "stock_video.py"
Cohesion: 0.30
Nodes (12): StockPreviewProfile, CancellationToken, generate_midpoint_thumbnail(), generate_stock_preview(), Path, Protocol, Queue, RuntimeError (+4 more)

### Community 71 - "CenterResizeGrip"
Cohesion: 0.27
Nodes (7): QMouseEvent, QSizeGrip, QSlider, CenterResizeGrip, Seek immediately when the user clicks or drags on the timeline., Resize a frameless popup around its centre point., SeekSlider

### Community 73 - "quick_look.py"
Cohesion: 0.38
Nodes (5): quick_look_media_for_asset(), QuickLookEntry, QuickLookMedia, Quick Look-style previews for assets in the library browser., Resolve the managed still and video previews belonging to an asset.

### Community 74 - "blender_model_rescan_driver.py"
Cohesion: 0.53
Nodes (5): _arguments(), _inside_asset(), main(), Blender-side validator for manually added managed USD files., validate()

### Community 75 - ".new"
Cohesion: 0.19
Nodes (4): ModelCollection, ModelCollections, ModelObjects, ModelWmOps

### Community 76 - "blender_hdri_driver.py"
Cohesion: 0.53
Nodes (5): arguments(), configure_cycles_gpu(), main(), Executed inside Blender; do not import bpy from the desktop application., render_job()

### Community 77 - "list"
Cohesion: 0.29
Nodes (5): list, Data, Materials, Nodes, Worlds

### Community 78 - "Object Cube Icon"
Cohesion: 0.67
Nodes (3): Object Cube Icon, Building Structure Icon, Car Vehicle Icon

### Community 92 - "PolyHavenClient"
Cohesion: 0.10
Nodes (35): normalize_channel(), ModelPackageSource, A provider scene and its dependency paths relative to that scene., build_download_plan(), cached_catalog(), _case_value(), _file_record(), _labels() (+27 more)

### Community 93 - "TextureListModel"
Cohesion: 0.25
Nodes (6): QAbstractListModel, QSortFilterProxyModel, _largest_resolution(), TextureFilterModel, TextureListModel, test_rating_filter_sort_and_star_toggle()

### Community 94 - "test_model_conversion.py"
Cohesion: 0.25
Nodes (18): _asset(), _imported_repository(), Path, _source_model(), _successful_runner(), test_conversion_request_rejects_unknown_source_orientation_and_missing_maps(), test_conversion_request_selects_source_resolution_and_orientation(), test_headless_runner_builds_secure_blender_command_and_reads_result() (+10 more)

### Community 95 - "Material"
Cohesion: 0.25
Nodes (3): Material, Tree, World

### Community 96 - "scan_hdri_folder"
Cohesion: 0.30
Nodes (14): scan_hdri_folder(), _exr_header(), _hdr(), _hdri_source(), Path, test_hdri_import_generates_real_jpeg_and_preserves_all_safe_files(), test_hdri_import_publishes_composite_render_metadata_when_blender_succeeds(), test_hdri_render_exception_never_fails_import_or_replaces_fallback() (+6 more)

### Community 97 - "blender_preview_server.py"
Cohesion: 0.48
Nodes (6): _discover_gpu(), _emit(), _load_module(), main(), _open_template(), Persistent preview protocol server executed inside Blender.

### Community 98 - ".__enter__"
Cohesion: 0.20
Nodes (3): _lock_is_local_stale(), _pid_exists(), _read_lock_payload()

### Community 99 - "integrations/__init__.py"
Cohesion: 0.15
Nodes (19): Optional integrations with external DCC applications., _blender_version(), _conversion_map(), _conversion_resolution(), _last_log_line(), _lod_rank(), _managed_file(), model_conversion_sources() (+11 more)

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

### Community 109 - "test_polyhaven_ui.py"
Cohesion: 0.32
Nodes (7): app(), entry(), fixture, test_external_busy_blocks_check(), test_selection_totals_ambiguity_and_invalidation(), test_worker_background_cancel_and_retry(), wait_until()

### Community 116 - "model_rescan.py"
Cohesion: 0.21
Nodes (14): _check_cancel(), _component_name(), _discoverable_model(), inventory_model_asset(), _lod_label(), _model_role(), ModelRescanItem, ModelUsdValidation (+6 more)

### Community 117 - "scan_atlas_folder"
Cohesion: 0.32
Nodes (11): Scan PBR cutout packages while explicitly classifying them as atlases., scan_atlas_folder(), _select_atlas_category(), _component_source(), _image(), Path, skipif, test_atlas_import_uses_distinct_manifest_container_and_duplicate_scope() (+3 more)

### Community 120 - "render_hdri_preview"
Cohesion: 0.29
Nodes (16): QApplication, _failure(), _filename_token(), HdriPreviewRequest, HdriPreviewResult, render_hdri_preview(), _hdr(), _image() (+8 more)

### Community 127 - "decode_asset"
Cohesion: 0.10
Nodes (18): Connection, CatalogError, decode_asset(), _decode_value(), _default_cache_root(), encode_asset(), _encode_value(), _library_identity() (+10 more)

## Knowledge Gaps
- **52 isolated node(s):** `shotbox-assets`, `run_vfx_asset_library.sh script`, `Answer`, `Outcome`, `Source Nodes` (+47 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

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

- **Why does `LibraryRepository` connect `LibraryRepository` to `AssetRecord`, `ImporterTab`, `test_texture_preview.py`, `repository.py`, `CategoryCatalog`, `MaterialCandidate`, `assets_tab.py`, `.clear`, `AssetsTab`, `test_ui.py`, `scan_stock_folder`, `PolyHavenSyncPreferences`, `scanner.py`, `LibraryTextureAsset`, `domain/__init__.py`, `CancelToken`, `importer/__init__.py`, `test_importer_scanner.py`, `TextureCardDelegate`, `AppSettings`, `BlenderBridgeWorker`, `settings_tab.py`, `.start`, `LibraryInspectionWorker`, `test_polyhaven.py`, `scan_vdb_folder`, `previews/__init__.py`, `test_vdb_preview.py`, `test_model_importer.py`, `PolyHavenClient`, `test_model_conversion.py`, `scan_hdri_folder`, `.__enter__`, `model_rescan.py`, `scan_atlas_folder`?**
  _High betweenness centrality (0.120) - this node is a cross-community bridge._
- **Why does `CategoryCatalog` connect `CategoryCatalog` to `TextureCardDelegate`, `repository.py`, `.__init__`, `assets_tab.py`, `.clear`, `LibraryRepository`, `DetailPanel`, `AssetsTab`, `test_ui.py`, `scanner.py`, `ModelAssetRescanDialog`, `TextureListModel`, `AssetQuickLookController`, `mixed_scanner.py`, `CancelToken`, `TagEditor`, `model_scanner.py`?**
  _High betweenness centrality (0.074) - this node is a cross-community bridge._
- **Why does `AssetsTab` connect `AssetsTab` to `CategoryCatalog`, `assets_tab.py`, `.clear`, `DetailPanel`, `test_ui.py`, `AiOrganiserDialog`, `model_export.py`, `TagEditor`, `TextureCardDelegate`, `MainWindow`, `.__init__`, `AppSettings`, `CategoryRail`, `.start`, `AssetQuickLookController`, `._open_ai_organiser`, `integrations/__init__.py`, `._send_hdri_to_blender`, `._start_model_conversion`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 166 inferred relationships involving `LibraryRepository` (e.g. with `apply_classification()` and `CatalogRefreshSignals`) actually correct?**
  _`LibraryRepository` has 166 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `AssetsTab` (e.g. with `CategoryCatalog` and `AiGuessWorker`) actually correct?**
  _`AssetsTab` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 44 inferred relationships involving `CancelToken` (e.g. with `CatalogRefreshSignals` and `CatalogRefreshWorker`) actually correct?**
  _`CancelToken` has 44 INFERRED edges - model-reasoned connections that need verification._
- **Are the 63 inferred relationships involving `CategoryCatalog` (e.g. with `StockTaxonomyStore` and `_InventoryEntry`) actually correct?**
  _`CategoryCatalog` has 63 INFERRED edges - model-reasoned connections that need verification._