# Graph Report - .  (2026-07-31)

## Corpus Check
- 134 files · ~122,204 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2309 nodes · 8001 edges · 92 communities (73 shown, 19 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 1476 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Asset Discovery and Metadata
- Import Workflow UI
- Preview Rendering
- Import Cancellation and Errors
- Library Data Models
- Asset Repository Operations
- Houdini Plugin Actions
- Asset Browser Navigation
- Asset Display and Loading
- Texture Scanner Tests
- Asset Detail Panel
- Ollama AI Client
- Houdini Plugin Test Doubles
- Repository Tests
- Product Plans and Taxonomy
- Settings and Repair UI
- Stock Taxonomy
- AI Organiser UI
- Stock Import Scanning
- Atlas Scanner Diagnostics
- DCC Export Integrations
- Catalog Index Refresh
- Library Domain Models
- ShotBox Bridge Actions
- Mixed and HDRI Scanning
- Catalog Cache Operations
- Metadata Editing Workflows
- Tag Editor UI
- Bridge Server
- Model Scanning
- Blender Bridge
- Houdini Bridge
- Importer UI Models
- Texture Card Rendering
- Blender Plugin Installer
- Application Settings
- Reusable UI Widgets
- Blender Model Conversion
- Houdini Plugin Installer
- Texture Channel Adapters
- Model Conversion Pipeline
- Category Configuration
- Library Maintenance Workers
- Stock Hover Preview
- Model Export Tests
- Poly Haven Downloads
- Settings Validation
- Category Rail UI
- Model Conversion Tests
- Model Rescan Validation
- Library Inspection
- Blender Plugin Tests
- Blender Material Doubles
- HDRI Import Tests
- Poly Haven Tests
- Texture Library Queries
- Preview Render Queue
- Model Conversion Dialogs
- Blender Node Doubles
- Atomic Import Locking
- Application Entry Points
- Texture Sampling Prototype
- Model Import Scanning
- Houdini Paths Configuration
- RAR Import Tests
- Blender Texture Driver
- Atlas Import Tests
- Blender Material Fixtures
- Model Rescan Dialog
- Batch Metadata Worker
- Blender Image Doubles
- Blender Object Doubles
- Blender Rescan Driver
- Blender Collection Doubles
- Blender HDRI Driver
- Category Text Matching
- Physical Asset Icons
- Prototype Package
- AI Tag Vocabularies
- Core Package
- Energy and Motion Icons
- Grid and Material Icons
- Life and Nature Icons
- Terrain and Water Icons
- Package Metadata
- Cloud Icon
- Light Icon
- Help Icon
- Tag Icon
- Target Icon

## God Nodes (most connected - your core abstractions)
1. `LibraryRepository` - 200 edges
2. `AssetsTab` - 150 edges
3. `CancelToken` - 92 edges
4. `ImporterTab` - 82 edges
5. `CategoryCatalog` - 77 edges
6. `DetailPanel` - 74 edges
7. `scan_texture_folder()` - 72 edges
8. `SettingsTab` - 70 edges
9. `CategoryConfigStore` - 69 edges
10. `LibraryError` - 66 edges

## Surprising Connections (you probably didn't know these)
- `test_asset_type_auto_detection_and_provider_override()` --calls--> `detect_asset_type()`  [INFERRED]
  tests/test_importer_scanner.py → src/universal_asset_library/importer/detection.py
- `test_extension_installer_builds_installs_and_removes_package()` --calls--> `BlenderPluginInstaller`  [INFERRED]
  tests/test_blender_bridge.py → src/universal_asset_library/integrations/blender/installer.py
- `Expandable Category Rail` --semantically_similar_to--> `Asset Catalog Category Rail`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md
- `Per-Type Category Configuration` --semantically_similar_to--> `Portable Category Taxonomy Files`  [INFERRED] [semantically similar]
  Asset Category Icon Sidebar plan.md → README.md
- `Root-Relative Folder Context` --semantically_similar_to--> `Stock Folder and Filename Taxonomy`  [INFERRED] [semantically similar]
  Contextual Names for Numeric Stock Clips plan.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Portable Manifest-Backed Asset Catalog** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_manifest_authority, readme_portable_manifests, shotbox_assets_plan_ui_prototype [INFERRED 0.85]
- **Shared Category Metadata Flow** — asset_category_icon_sidebar_plan_category_configuration, readme_category_taxonomy_files, prototype_hdri_tagger_readme_allowed_taxonomy [INFERRED 0.85]
- **Responsive Background Workflows** — fast_indexed_asset_catalog_with_responsive_loading_screen_plan_refresh_worker, readme_preview_render_queue, shotbox_assets_plan_background_importer [INFERRED 0.75]
- **Physical Asset Category Icons** — src_universal_asset_library_ui_icons_object_object_cube, src_universal_asset_library_ui_icons_structure_building, src_universal_asset_library_ui_icons_terrain_mountain_landscape, src_universal_asset_library_ui_icons_vehicle_car, src_universal_asset_library_ui_icons_water_water_droplet [INFERRED 0.85]

## Communities (92 total, 19 thin omitted)

### Community 0 - "Asset Discovery and Metadata"
Cohesion: 0.05
Nodes (52): AnalysisRequest, AnalysisThread, HdriTaggerWindow, PullThread, QCheckBox, QMainWindow, Standalone Ollama-powered HDRI metadata tagger., main() (+44 more)

### Community 1 - "Import Workflow UI"
Cohesion: 0.05
Nodes (15): QListWidgetItem, ImporterTab, ImportSignals, ImportWorker, _paths_overlap(), PreflightWorker, Path, QObject (+7 more)

### Community 2 - "Preview Rendering"
Cohesion: 0.07
Nodes (69): QApplication, Queue, StockMediaInfo, StockPreviewProfile, _media_agrees(), preview_is_compatible(), compose_hdri_preview(), default_template_path() (+61 more)

### Community 3 - "Import Cancellation and Errors"
Cohesion: 0.10
Nodes (44): _asset_container(), _atomic_json(), _build_repaired_asset(), CancelToken, _claim_asset_destination(), _clean_tags(), _copy_hash(), _copy_verified() (+36 more)

### Community 4 - "Library Data Models"
Cohesion: 0.20
Nodes (46): DuplicateConflict, HdriCandidate, MaterialCandidate, MaterialPreflight, ModelCandidate, PreflightResult, SourceFileSnapshot, StockCandidate (+38 more)

### Community 5 - "Asset Repository Operations"
Cohesion: 0.09
Nodes (51): _apply_polyhaven_manifest(), _asset_from_manifest(), _asset_manifest_paths(), _asset_manifest_paths_for_type(), _candidate_source_paths(), _contains(), _convert_webp_to_jpeg(), _hash_source() (+43 more)

### Community 6 - "Houdini Plugin Actions"
Cohesion: 0.08
Nodes (50): Start the ShotBox Assets bridge after the Houdini 21 UI is ready., Start the ShotBox Assets bridge after the Houdini 22 UI is ready., ActionError, _base_type(), _build_materialx_graph(), _child_node(), _children(), _clear_children() (+42 more)

### Community 7 - "Asset Browser Navigation"
Cohesion: 0.11
Nodes (36): QAbstractListModel, QSortFilterProxyModel, QTabBar, CategoryCatalog, AiGuessWorker, GuessConfirmationDialog, OllamaSetupDialog, QDialog (+28 more)

### Community 8 - "Asset Display and Loading"
Cohesion: 0.07
Nodes (11): _asset_filter_categories(), AssetsTab, Apply ShotBox-managed mutations immediately without rescanning., Replace one catalog record without resetting the visible grid., test_assets_splitter_is_wide_and_persists_state(), test_category_rail_expansion_persists_and_asset_type_switch_resets_filter(), test_cold_catalog_shows_loading_page_without_synchronous_scan(), test_importer_and_catalog_share_asset_type_tabs() (+3 more)

### Community 9 - "Texture Scanner Tests"
Cohesion: 0.09
Nodes (52): Event, scan_texture_folder(), image(), Path, skipif, test_asset_type_auto_detection_and_provider_override(), test_corrupt_empty_and_symlink_images_are_excluded_with_diagnostics(), test_duplicate_basenames_do_not_receive_ambiguous_json_semantics() (+44 more)

### Community 10 - "Asset Detail Panel"
Cohesion: 0.07
Nodes (5): DetailPanel, _media_time(), _resolution_number(), test_model_conversion_dialog_and_inspector_expose_headless_workflow(), test_polyhaven_hdri_download_controls()

### Community 11 - "Ollama AI Client"
Cohesion: 0.09
Nodes (32): Local vision classification used by the catalog and standalone prototype., CategoryGuess, Classification, _confidence_and_rationale(), OllamaClient, OllamaError, OllamaStatus, _prompt() (+24 more)

### Community 12 - "Houdini Plugin Test Doubles"
Cohesion: 0.08
Nodes (18): FakeCategory, FakeHipFile, FakeHou, FakeNode, FakeParm, FakeType, FakeUndos, _model_payload() (+10 more)

### Community 13 - "Repository Tests"
Cohesion: 0.10
Nodes (38): LibraryRepository, Apply category and additive-tag changes against the latest manifest., Create one locked, indexed session for a sequence of metadata patches., image(), Path, source_material(), test_atomic_import_manifest_catalog_and_source_integrity(), test_cancel_and_active_lock_leave_no_partial_asset() (+30 more)

### Community 14 - "Product Plans and Taxonomy"
Cohesion: 0.05
Nodes (48): AND Filter Semantics, Per-Type Category Configuration, Expandable Category Rail, Asset Category Icon Sidebar Plan, Fixed Category Icon Registry, Live Category Result Counts, Safe Category Configuration Fallback, Contextual Names for Numeric Stock Clips Plan (+40 more)

### Community 15 - "Settings and Repair UI"
Cohesion: 0.09
Nodes (3): QWidget, Compatibility wrapper for callers that request a maintenance refresh., SettingsTab

### Community 16 - "Stock Taxonomy"
Cohesion: 0.11
Nodes (38): _atomic_replace_json(), _best_rule(), _categories_document(), _category_equivalents(), classify_stock_path(), _create_json_once(), default_stock_taxonomy(), _matching_rules() (+30 more)

### Community 17 - "AI Organiser UI"
Cohesion: 0.08
Nodes (19): QTableWidgetItem, actionable_categories(), AiBatchSignals, AiBatchWorker, AiGuessSignals, AiOrganiseItem, AiOrganiserDialog, _asset_type_label() (+11 more)

### Community 18 - "Stock Import Scanning"
Cohesion: 0.12
Nodes (41): StockPreviewCandidate, _alpha_state(), _display_name(), infer_stock_display_name(), _inside_preview_tree(), _integer(), _is_preview_directory_name(), _is_preview_path() (+33 more)

### Community 19 - "Atlas Scanner Diagnostics"
Cohesion: 0.15
Nodes (39): ArchiveSource, Diagnostic, PreviewCandidate, ScanProgress, _archive_preview_pairs(), _assign_previews(), _base_color_fallback(), _build_inventory() (+31 more)

### Community 20 - "DCC Export Integrations"
Cohesion: 0.11
Nodes (29): Optional integrations with external DCC applications., _lod_rank(), model_export_label(), model_export_options(), ModelExportError, ModelExportFile, ModelExportTextureSet, _preferred_map() (+21 more)

### Community 21 - "Catalog Index Refresh"
Cohesion: 0.09
Nodes (23): CatalogIndex, CatalogWriter, Disposable local SQLite acceleration index for one portable library., Reuse one local SQLite connection while committing every item., CatalogRefreshSignals, CatalogRefreshWorker, CatalogSectionResult, QObject (+15 more)

### Community 22 - "Library Domain Models"
Cohesion: 0.12
Nodes (18): LibraryExtraFile, LibraryHdriAsset, LibraryHdriFile, LibraryHdriVariant, LibraryMap, LibraryModelFile, LibraryModelTextureSet, LibraryProviderPackage (+10 more)

### Community 23 - "ShotBox Bridge Actions"
Cohesion: 0.15
Nodes (35): fixture, ActionError, _active_background(), _build_principled_graph(), _copy_value(), create_texture_material(), _cursor_location(), _enable_displacement_only() (+27 more)

### Community 24 - "Mixed and HDRI Scanning"
Cohesion: 0.13
Nodes (30): detect_asset_type(), Path, Return a conservative importer mode and a short user-facing reason., _assign_hdri_preview(), _declared_hdri_files(), _discover_hdri_roots(), _display_name(), _environment_resolution() (+22 more)

### Community 25 - "Catalog Cache Operations"
Cohesion: 0.08
Nodes (19): Connection, CatalogError, decode_asset(), _decode_value(), _default_cache_root(), encode_asset(), _encode_value(), _library_identity() (+11 more)

### Community 26 - "Metadata Editing Workflows"
Cohesion: 0.11
Nodes (5): _classification_preview(), _merge_ai_tags(), Path, test_ai_tag_merge_is_case_insensitive_and_stable(), test_asset_inspector_ai_buttons_disable_without_still_preview()

### Community 27 - "Tag Editor UI"
Cohesion: 0.11
Nodes (9): Orientations, QLayout, QLayoutItem, QRect, QSize, FlowLayout, Small wrapping layout used by the tag-chip editor., TagEditor (+1 more)

### Community 28 - "Bridge Server"
Cohesion: 0.10
Nodes (13): register(), unregister(), Runtime package for the ShotBox Assets Blender extension., SHOTBOX_PT_assets_bridge, encode_message(), _receive_exact(), receive_message(), BridgeServer (+5 more)

### Community 29 - "Model Scanning"
Cohesion: 0.17
Nodes (27): _basename(), _component_name(), _display_name(), _flatten_values(), _integer(), _lod_label(), _lod_number(), _material_name() (+19 more)

### Community 30 - "Blender Bridge"
Cohesion: 0.17
Nodes (18): BlenderBridgeClient, BlenderBridgeError, BlenderBridgeResponse, BlenderSession, encode_message(), _process_exists(), Any, Path (+10 more)

### Community 31 - "Houdini Bridge"
Cohesion: 0.20
Nodes (19): BridgeRequest, BridgeResponse, choose_hdri_file(), _default_resolution(), encode_message(), HoudiniBridgeClient, HoudiniBridgeError, HoudiniSession (+11 more)

### Community 32 - "Importer UI Models"
Cohesion: 0.15
Nodes (15): _extract_zip_atomically(), Path, _safe_member_path(), unzip_all_zip_files(), ZipExtractionProgress, ZipExtractionSummary, RuntimeError, ScanCancellationToken (+7 more)

### Community 33 - "Texture Card Rendering"
Cohesion: 0.15
Nodes (7): QModelIndex, QPainter, QPixmap, QRectF, QStyledItemDelegate, _largest_resolution(), TextureCardDelegate

### Community 34 - "Blender Plugin Installer"
Cohesion: 0.18
Nodes (9): CompletedProcess, BlenderInstallation, BlenderPluginInstaller, BlenderPluginStatus, Path, config_path(), data_dir(), Path (+1 more)

### Community 35 - "Application Settings"
Cohesion: 0.12
Nodes (12): QSettings, SettingsStore, MainWindow, QMainWindow, test_main_window_construction_defers_library_maintenance(), test_main_window_disables_write_tabs_during_background_asset_updates(), test_main_window_has_three_tabs_in_order(), test_settings_detects_abandoned_staging_for_confirmed_cleanup() (+4 more)

### Community 36 - "Reusable UI Widgets"
Cohesion: 0.17
Nodes (9): QLabel, BusyOverlay, CircularSpinner, CollapsibleSection, QFrame, QWidget, Compact inspector section with a clickable disclosure header., Small indeterminate spinner drawn with the current ShotBox accent. (+1 more)

### Community 37 - "Blender Model Conversion"
Cohesion: 0.20
Nodes (22): _arguments(), _axis_selection(), _build_material(), convert(), _copy_texture(), _import_source(), _link(), main() (+14 more)

### Community 38 - "Houdini Plugin Installer"
Cohesion: 0.18
Nodes (12): _atomic_json(), HoudiniInstallation, HoudiniPluginInstaller, PluginStatus, Path, _asset(), Path, test_client_discovers_authenticated_session() (+4 more)

### Community 39 - "Texture Channel Adapters"
Cohesion: 0.23
Nodes (15): _as_int(), _flatten_tags(), JsonMetadataAdapter, MapDeclaration, _megascans_asset_type(), MegascansAdapter, MetadataFacts, normalize_channel() (+7 more)

### Community 40 - "Model Conversion Pipeline"
Cohesion: 0.19
Nodes (17): _blender_version(), _conversion_map(), _conversion_resolution(), _last_log_line(), _lod_rank(), _managed_file(), model_conversion_sources(), ModelConversionMap (+9 more)

### Community 41 - "Category Configuration"
Cohesion: 0.20
Nodes (14): _atomic_json(), _catalog_document(), category_icon_id(), CategoryConfigStore, CategoryDefinition, default_category_catalog(), _parse_catalog(), Path (+6 more)

### Community 42 - "Library Maintenance Workers"
Cohesion: 0.16
Nodes (12): BlenderBridgeSignals, BlenderBridgeWorker, HoudiniBridgeSignals, HoudiniBridgeWorker, LibraryInspectionSignals, LibraryUpdateWorker, MaintenanceSignals, MaintenanceWorker (+4 more)

### Community 43 - "Stock Hover Preview"
Cohesion: 0.17
Nodes (5): QEvent, QListView, QPersistentModelIndex, Own one muted decoder and paints its frames through the card delegate., StockHoverPreviewController

### Community 44 - "Model Export Tests"
Cohesion: 0.14
Nodes (9): LibraryModelAsset, _lod_key(), Path, _resolution_key(), _asset(), Path, test_model_export_includes_managed_material_maps_for_houdini_sop(), test_model_export_rejects_unknown_missing_and_outside_paths() (+1 more)

### Community 45 - "Poly Haven Downloads"
Cohesion: 0.20
Nodes (16): build_download_plan(), cached_catalog(), _case_value(), _file_record(), _labels(), load_metadata_documents(), options_from_catalog(), PolyHavenPackage (+8 more)

### Community 46 - "Settings Validation"
Cohesion: 0.22
Nodes (15): AppSettings, normalize_executable_path(), normalize_library_path(), Return an absolute native path without resolving symlink aliases., _setting_bool(), validate_library_path(), make_store(), test_blender_and_hdri_render_preferences_round_trip() (+7 more)

### Community 47 - "Category Rail UI"
Cohesion: 0.18
Nodes (7): QIcon, QToolButton, _category_icon(), CategoryRail, QFrame, QWidget, _setting_bool()

### Community 48 - "Model Conversion Tests"
Cohesion: 0.25
Nodes (18): _asset(), _imported_repository(), Path, _source_model(), _successful_runner(), test_conversion_request_rejects_unknown_source_orientation_and_missing_maps(), test_conversion_request_selects_source_resolution_and_orientation(), test_headless_runner_builds_secure_blender_command_and_reads_result() (+10 more)

### Community 49 - "Model Rescan Validation"
Cohesion: 0.21
Nodes (14): _check_cancel(), _component_name(), _discoverable_model(), inventory_model_asset(), _lod_label(), _model_role(), ModelRescanItem, ModelUsdValidation (+6 more)

### Community 50 - "Library Inspection"
Cohesion: 0.18
Nodes (7): LibraryInspectionResult, LibraryInspectionWorker, _lock_age(), Queue a coalesced maintenance inspection for the saved library., Start the post-paint audit unless the user already requested one., Inspect a potentially remote library without blocking the GUI thread., Ignore completion when Qt has already destroyed the receiver.

### Community 52 - "Blender Plugin Tests"
Cohesion: 0.25
Nodes (13): Bpy, _model_request(), ModelBpy, payload(), Scene, standard_world(), test_create_new_world_preserves_previous_and_marks_asset(), test_edit_current_world_preserves_background_and_replaces_environment() (+5 more)

### Community 53 - "Blender Material Doubles"
Cohesion: 0.16
Nodes (7): list, Data, Materials, ModelCollections, ModelObjects, ModelWmOps, Worlds

### Community 54 - "HDRI Import Tests"
Cohesion: 0.33
Nodes (13): scan_hdri_folder(), _exr_header(), _hdr(), _hdri_source(), Path, test_hdri_import_generates_real_jpeg_and_preserves_all_safe_files(), test_hdri_import_publishes_composite_render_metadata_when_blender_succeeds(), test_hdri_render_exception_never_fails_import_or_replaces_fallback() (+5 more)

### Community 55 - "Poly Haven Tests"
Cohesion: 0.31
Nodes (10): FakeClient, _image(), Path, _record(), test_client_sends_identity_and_rejects_untrusted_download_host(), test_hdri_download_adds_hdr_and_exr_without_replacing_local_variants(), test_model_usd_resolutions_coexist_and_latest_is_preferred(), test_slug_recovery_and_unsafe_package_paths() (+2 more)

### Community 58 - "Model Conversion Dialogs"
Cohesion: 0.19
Nodes (4): _human_size(), MaterialEditDialog, ModelConversionDialog, QDialog

### Community 59 - "Blender Node Doubles"
Cohesion: 0.15
Nodes (5): Link, Links, Node, Nodes, Socket

### Community 60 - "Atomic Import Locking"
Cohesion: 0.20
Nodes (3): _lock_is_local_stale(), _pid_exists(), _read_lock_payload()

### Community 61 - "Application Entry Points"
Cohesion: 0.29
Nodes (6): Public ShotBox Assets application namespace., create_application(), main(), _migrate_legacy_settings(), Preserve user preferences from the application's former product name., test_shotbox_name_migrates_legacy_application_settings()

### Community 62 - "Texture Sampling Prototype"
Cohesion: 0.29
Nodes (7): sample_textures(), TextureAsset, test_category_and_channel_filters_are_combined(), test_query_is_trimmed_and_case_insensitive(), test_sample_assets_have_unique_ids_and_required_maps(), test_search_matches_names_tags_categories_and_channels(), test_texture_asset_is_immutable()

### Community 63 - "Model Import Scanning"
Cohesion: 0.35
Nodes (10): _diagnostics_for_root(), _discover_model_roots(), scan_model_folder(), image(), model_source(), Path, test_model_scan_prefers_usd_and_excludes_renderer_archives_and_hidden_data(), test_non_usd_model_imports_with_best_available_fallback() (+2 more)

### Community 64 - "Houdini Paths Configuration"
Cohesion: 0.44
Nodes (7): bridge_config_path(), legacy_bridge_config_path(), legacy_runtime_dir(), Path, runtime_dir(), user_config_dir(), user_data_dir()

### Community 66 - "RAR Import Tests"
Cohesion: 0.47
Nodes (9): _fake_extract(), _image(), parametrize, Path, _source(), test_changed_rar_is_stale_before_import(), test_megascans_code_is_removed_from_archive_material_name(), test_paired_archive_material_scans_and_imports() (+1 more)

### Community 67 - "Blender Texture Driver"
Cohesion: 0.39
Nodes (8): arguments(), _build_material(), configure_cycles_gpu(), _legacy_map_records(), main(), _map_records(), _material_actions(), Executed inside Blender; do not import bpy from the desktop application.

### Community 68 - "Atlas Import Tests"
Cohesion: 0.44
Nodes (8): _component_source(), _image(), Path, skipif, test_atlas_import_uses_distinct_manifest_container_and_duplicate_scope(), test_megascans_components_detect_and_normalize_atlas(), test_mixed_scan_keeps_unrelated_megascans_decal_as_texture(), test_supplied_megascans_atlas_fixture()

### Community 69 - "Blender Material Fixtures"
Cohesion: 0.22
Nodes (3): Material, Tree, World

### Community 71 - "Batch Metadata Worker"
Cohesion: 0.43
Nodes (4): BatchMetadataItemResult, BatchMetadataProgress, BatchMetadataSignals, QObject

### Community 74 - "Blender Rescan Driver"
Cohesion: 0.53
Nodes (5): _arguments(), _inside_asset(), main(), Blender-side validator for manually added managed USD files., validate()

### Community 75 - "Blender Collection Doubles"
Cohesion: 0.40
Nodes (3): ChildLinks, ModelCollection, ObjectLinks

### Community 76 - "Blender HDRI Driver"
Cohesion: 0.60
Nodes (4): arguments(), configure_cycles_gpu(), main(), Executed inside Blender; do not import bpy from the desktop application.

### Community 78 - "Physical Asset Icons"
Cohesion: 0.67
Nodes (3): Object Cube Icon, Building Structure Icon, Car Vehicle Icon

## Knowledge Gaps
- **27 isolated node(s):** `shotbox-assets`, `Fixed Category Icon Registry`, `AND Filter Semantics`, `Live Category Result Counts`, `Contextual Names for Numeric Stock Clips Plan` (+22 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LibraryRepository` connect `Repository Tests` to `Asset Discovery and Metadata`, `Import Workflow UI`, `Preview Rendering`, `Import Cancellation and Errors`, `Library Data Models`, `Asset Repository Operations`, `Asset Browser Navigation`, `Asset Display and Loading`, `Texture Scanner Tests`, `Asset Detail Panel`, `Stock Import Scanning`, `Atlas Scanner Diagnostics`, `Catalog Index Refresh`, `Catalog Cache Operations`, `Importer UI Models`, `Application Settings`, `Category Configuration`, `Library Maintenance Workers`, `Poly Haven Downloads`, `Model Conversion Tests`, `Model Rescan Validation`, `Library Inspection`, `HDRI Import Tests`, `Poly Haven Tests`, `Texture Library Queries`, `Atomic Import Locking`, `Model Import Scanning`, `Poly Haven UI`, `RAR Import Tests`, `Atlas Import Tests`, `Batch Metadata Worker`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Why does `CategoryCatalog` connect `Asset Browser Navigation` to `Texture Card Rendering`, `Import Cancellation and Errors`, `Library Data Models`, `Asset Repository Operations`, `Reusable UI Widgets`, `Model Rescan Dialog`, `Asset Display and Loading`, `Category Configuration`, `Texture Scanner Tests`, `Asset Detail Panel`, `Stock Hover Preview`, `Category Text Matching`, `Repository Tests`, `Atlas Scanner Diagnostics`, `Mixed and HDRI Scanning`, `Model Conversion Dialogs`, `Tag Editor UI`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `AssetsTab` connect `Asset Display and Loading` to `Poly Haven UI`, `Texture Card Rendering`, `Application Settings`, `Model Conversion Dialogs`, `Reusable UI Widgets`, `Asset Browser Navigation`, `Texture Scanner Tests`, `Asset Detail Panel`, `Stock Hover Preview`, `Category Rail UI`, `AI Organiser UI`, `DCC Send Actions`, `Preview Render Queue`, `Metadata Editing Workflows`, `Tag Editor UI`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Are the 116 inferred relationships involving `LibraryRepository` (e.g. with `apply_classification()` and `CatalogRefreshSignals`) actually correct?**
  _`LibraryRepository` has 116 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `AssetsTab` (e.g. with `CategoryCatalog` and `AiGuessWorker`) actually correct?**
  _`AssetsTab` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `CancelToken` (e.g. with `CatalogRefreshSignals` and `CatalogRefreshWorker`) actually correct?**
  _`CancelToken` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `ImporterTab` (e.g. with `CategoryConfigStore` and `AssetTypeTabs`) actually correct?**
  _`ImporterTab` has 4 INFERRED edges - model-reasoned connections that need verification._