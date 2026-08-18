# ShotBox Assets

A lightweight PyQt6 desktop library for PBR textures, cutout atlases, HDRIs, USD-first 3D models, OpenVDB volumes, and video Stock footage, styled to match ShotBox.

The current milestone contains three surfaces:

- **Assets** — searchable Textures, Atlases, HDRIs, Models, VDBs, and Stock sections loaded from portable manifests.
- **Importer** — Texture/Atlas/HDRI/Model/VDB/Stock modes with provider-aware scanning, checked batch import, progress, cancellation, and duplicate protection.
- **Settings** — persistent library path, thumbnail size, importer defaults, Blender, Houdini, and FFmpeg executables, and independent automatic texture/HDRI preview preferences.

## Run locally

Python 3.11 or newer is required.

On Linux, run:

```bash
./run_vfx_asset_library.sh
```

On Windows, double-click `run_vfx_asset_library.bat` or run it from Command Prompt. A clean `main` checkout checks `origin/main` on startup and installs available fast-forward updates before opening the application. Offline startup remains available, and updates are skipped rather than overwriting tracked local changes or a diverged branch. Pass `--no-update` for one launch or set `SHOTBOX_AUTO_UPDATE=0` to disable the check. The launchers create `.venv` when needed, synchronize the editable installation on every start, and then open ShotBox Assets. Git for Windows is optional but required for automatic source updates; an internet connection may be required while updating code or dependencies.

To set up and run the app manually instead:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
shotbox-assets
```

Alternatively:

```bash
python -m shotbox_assets
```

## Tests

```bash
python -m pip install -e '.[dev]'
pytest
```

## HDRI composite previews

HDRI imports can use the immutable `templates/hdri_preview.blend` scene to generate one clean 1024×768 preview without text or resolution badges: the source equirectangular image is directly resized and tone-mapped to 1024×512 above a 1024×256 sphere render from the template. It is not projected through another camera or mapped to geometry. Both the catalog card and inspector use this same `_HDRI_Preview.jpg`; no separate generated thumbnail is stored. When the source package already includes a WebP preview, that image is converted directly to JPEG and the Blender render is skipped. The driver replaces only `World.001`'s `Environment Texture` image and explicitly renders with the first available Cycles GPU backend in OptiX, CUDA, HIP, oneAPI, then Metal order; the template's camera, geometry, materials, render engine, sampling, denoising, and color management remain authoritative.

Choose Blender in **Settings**, or leave the executable empty to search `PATH`. The **Check Blender** action reports its detected version. Automatic rendering is enabled by default, but Blender remains optional: unavailable or failed rendering never fails an import and the existing fallback preview is retained. HDRI details provide **Render preview**, **Regenerate preview**, and cancellation controls. Regeneration renders outside the library lock, then verifies the source hash and unchanged manifest before atomically publishing the result.

## Texture shader previews

Texture-set imports can use the immutable `templates/shader_preview.blend` scene to render a square 512×512 catalog thumbnail and a landscape 1024×512 inspector preview. ShotBox selects the highest managed resolution up to 4K (or the smallest larger variant), then rebuilds `Material.001` with the same complete Principled graph used by **Send material**. Every preferred supported map is included: Base Color, AO, Cavity, Roughness or inverted Glossiness, Metalness, Specular, Normal, Bump, Height, Displacement, Opacity, Emission, and Translucency, including supported channels extracted from packed textures. Base Color is required and DirectX normals are converted to the OpenGL convention. The template remains authoritative for its geometry, lighting, cameras, Cycles sampling, denoising, and color management.

The `render_ball` camera produces `_Texture_Thumbnail.jpg` for catalog cards, while `render_plane` produces `_Texture_Preview.jpg` for the inspector hero. During import, Blender is requested only when the source contains no usable provider preview; an existing thumbnail or hero is retained unchanged. Desktop imports publish the asset first and place missing texture and HDRI renders into one background queue, which runs one Blender process at a time. Manual inspector requests move ahead of pending automatic jobs. Multiple texture or HDRI cards can also be selected and added together with **Queue Previews**. Queued and rendering cards display an animated loading circle, the toolbar reports queue length and can clear pending work, and selecting a queued asset allows that individual job to be canceled. Queue errors remain non-blocking and processing continues with the next asset. Missing-preview rendering has its own default-on Settings preference. A separate default-off debugging preference saves a self-contained `_Texture_Preview.blend` beside generated images in the managed asset's `previews/` folder; the material maps are packed into it so the exact render state can be opened after temporary import files are removed. Blender remains optional: a failed or unavailable render never fails the texture import. Texture details expose **Render preview** for a retained source image, or **Regenerate preview** after a shader render, with progress, cancellation, and diagnostics. Manual rendering verifies the unchanged manifest and every selected map hash before atomically publishing both images and, when enabled, the debug scene. Atlases retain their source previews.

## Houdini VDB still previews

VDB details provide a manual Low/Mid/High variant selector, a Karma Pyro density slider from 10–500 (default 100), and a **Generate preview** menu. **Still** renders timeline frame 1 to the existing JPEG preview. **Generate turn table** renders the template timeline from frame 1 through 50, uses the first frame as the JPEG hero/thumbnail, and encodes all 50 frames into a managed H.264 MP4 at the HIP frame rate. ShotBox launches Houdini 22 or newer headlessly, opens a staging copy of `templates/VDB_preview_v001.hip`, assigns the managed file to `/obj/VDB/file1`, sets `kma_pyroshader1`'s Density Scale, and renders `/stage/usdrender_rop1`. The authoritative template remains untouched and controls camera, animation, lighting, materials, Karma settings, sampling, and output resolution. Any readable resolution is accepted; odd dimensions are padded by at most one pixel for H.264 compatibility. Sequence variants use their padded `$F` expression and must contain source frame 1.

Choose Houdini, hbatch, or hython in **Settings**, or leave the path empty to search installed Houdini 22+ versions. **Check Houdini** verifies the version and companion `iconvert` tool; turntables also require the configured or auto-detected FFmpeg. **Parallel VDB turntable renders** allows one to four simultaneous Houdini instances and defaults to two. The 50 frames are divided into contiguous ranges, each instance loads a separate staged HIP, and any worker failure or cancellation stops the complete group before encoding or publication. More instances require sufficient Houdini licenses, RAM, and GPU capacity. Temporary HIP copies, EXRs, and encoding files are written beneath the application folder's local `temp_cache/vdb-preview-<id>/` directory, keeping frame traffic off the managed library or network share. The job directory is discarded after success, failure, or cancellation; only the final JPEG and MP4 are atomically copied into the managed asset. VDB jobs share the serial preview queue but are manual-only: imports and bulk **Queue Previews** do not enqueue them. Cancellation terminates every active headless process tree. Failed, canceled, timed-out, or stale renders retain the previous preview, and neither the template, VDB, nor any HIP file is saved or modified.

## Houdini HDRI Bridge

ShotBox Assets can send an imported HDRI directly to Solaris in a running local Houdini 21 or 22 session:

1. Open **Settings → Houdini Bridge**.
2. Select the detected Houdini versions and choose **Install / Update Plugin**.
3. Restart Houdini. The package uses Houdini's supported `uiready.py` startup hook and does not modify `houdini.env`.
4. Open an HDRI in the Assets inspector, select a resolution, and choose **Send to Houdini**.

The bridge creates `/stage` when needed and creates a `domelight::3.0` after the selected LOP. If the selected node is already owned by ShotBox Assets, it updates that node instead. The scene is never saved automatically. ShotBox Assets defaults to the highest available resolution up to 4K and prefers EXR over HDR; the inspector can override the resolution.

Each Houdini process advertises an ephemeral loopback port, so multiple Houdini sessions can be selected and Quixel Bridge can continue using port `13290`. Requests are authenticated, limited to known JSON actions, and cannot execute arbitrary Python. Both applications must run as the same operating-system user and Houdini must be able to read the configured library path.

If no session appears, use **Refresh Connection** in Settings and check that Houdini was restarted after installation. Reinstall after updating ShotBox Assets if Settings reports an older plugin version. **Uninstall Plugin** removes the Houdini package entry; restart Houdini to unload an already-running bridge.

## Blender and Houdini asset bridges

ShotBox Assets can send imported HDRIs, PBR texture materials, managed USD models, and VDB volumes into running Blender 5.1/5.2 and Houdini 21/22 sessions. VDB sending is Houdini-only in this release:

1. Open **Settings → Blender Bridge**, select the detected Blender versions, and choose **Install / Update Plugin**.
2. Restart the DCC. The installed bridge reports its connection and last received asset.
3. Select an HDRI in ShotBox Assets, choose **Blender** from the **Send to DCC** menu, select its resolution and either **Edit current World** or **Create new World**, then choose **Send to Blender**.

**Edit current World** is the default. It replaces or adds the Environment Texture only when the active World has a standard Background-to-World-Output graph; custom graphs are rejected with a diagnostic instead of being rebuilt. Background strength and Blender's HDR/EXR color handling are preserved. Choose **Create new World** explicitly to assign a clean ShotBox World while keeping the previous World datablock untouched. The `.blend` file is never saved automatically.

For a texture set, choose a resolution and **Send material**. Blender creates or updates a ShotBox-owned Principled BSDF material and replaces the active material slot on each selected mesh, appending only when an object has no material slots. Its image textures share one **Texture Coordinate (UV) → Mapping** chain, and the complete graph is spaced into readable columns so Scale, Rotation, or Location can be adjusted once for every map. Materials with a managed Displacement or Height map automatically use Blender's **Displacement Only** surface mode. Houdini starts from its native **USD MaterialX Builder** scaffold, wires the PBR maps into the provided Standard Surface and Displacement nodes, and assigns it to selected scene-graph primitives. Every MaterialX image shares one `uv_coordinates → uv_control` chain, so changing Scale, Rotate, or Offset on `uv_control` transforms the complete texture set together. With no suitable selection, the material is created unassigned. Preferred managed maps are referenced directly, packed maps are split by their declared channels, and repeat sends update the material carrying the same asset ID.

For a USD-ready model, choose the preferred or another managed USD variant and **Import model**. Blender imports editable geometry and authored materials beneath a new ShotBox root at the 3D cursor; each send creates a separate placement and excludes USD cameras and lights. Houdini can create a live Reference LOP in Solaris or a packed USD File Import SOP in the selected geometry network, falling back to `/stage` or a new `/obj` Geometry container as needed. SOP imports also build managed MaterialX shaders under `/mat` and append a Material SOP assignment; Normal takes priority over Bump and Displacement remains separate. DCC scenes are never saved automatically and managed library files are never copied or modified by the bridge.

For a VDB, choose Low, Mid, High, or another discovered quality variant and select **Create File SOP in Houdini**. Each send creates a new File SOP in the selected SOP network or Geometry object, falling back to a new Geometry object under `/obj`. Static volumes reference their managed file directly. Sequences use a padded `$F` expression, retain their source frame range, and use Houdini's **No Geometry** missing-frame policy. The bridge does not create shaders or pyro networks and never saves the HIP file.

Static models with a managed BLEND, FBX, OBJ, GLTF, GLB, or Alembic source expose **Convert to USD**. ShotBox runs the configured Blender 5.1+ executable headlessly, resets Blender to an empty factory scene, imports only the selected model, matches managed texture sets to material slots, copies the used maps into a portable derivative, and publishes the preferred USDC directly under `usd/`. LOD0 is selected by default whenever it is available, including for rebuilds. Normal maps take precedence over Bump maps; Bump is used only when no Normal map exists, while Displacement remains separate. A single exported material uses the asset name; multi-material assets use `<AssetName>_<TextureSetName>`. The default USD Interchange preset exports −Z forward and Y up; Z-up is also available. Rigs, animation, blend shapes, missing materials, ambiguous matches, stale assets, and invalid exports fail without changing the prior asset or USD.

Model assets also expose **Rescan asset** for files created or corrected manually. Place USD, USDA, USDC, or USDZ files beneath the managed asset's `usd/` folder; place BLEND, FBX, OBJ, Alembic, glTF, or Maya sources beneath `models/`. Rescan reviews the discovered changes and requires an explicit preferred file. USD-family additions are opened and inspected by the configured Blender before adoption; malformed stages, stages without meshes, and dependencies outside the managed asset are rejected. Imported and application-generated records remain protected, while manually registered files can later be refreshed or removed. A manually preferred USD remains preferred when the headless USD is rebuilt.

The bridge is separate from Blender-based HDRI preview rendering. Each interactive Blender process advertises its own authenticated ephemeral loopback port, and all Blender data changes are queued onto Blender's main thread. If a session is missing, restart Blender after installing, use **Refresh Connection**, and confirm both applications run as the same user with access to the managed library.


## Current limitations

- SQLite indexing is deferred; the catalog currently scans portable manifests.
- Blender must be installed separately for rendered texture and HDRI previews; without it, imports retain their provider or generated fallback preview.
- Houdini 22 or newer is required only for manual VDB still rendering; VDB import and Houdini 21/22 bridge sending remain available independently.
- Blender model imports are editable snapshots; Houdini LOP and packed-SOP imports retain their external managed-USD reference.

## Importer behavior

Choose **Textures**, **Atlases**, **HDRIs**, **Models**, **VDBs**, or **Stock**, then select a single asset folder or a parent containing several assets. The scanner:

- reads supported local images and JSON without modifying them;
- recognizes Megascans and Poly Haven metadata;
- falls back to common PBR filename tokens for other folders;
- renames managed Model payloads from provider codes to asset-based names such as `Donut_LOD0.fbx` and `Donut_4K_BaseColor.jpg`, while preserving original filenames in `asset.json`;
- groups 1K, 2K, 4K, 8K, and 16K files as variants of one material;
- retains alternative formats and marks a preferred representation;
- selects editable thumbnail and hero-preview candidates;
- retains every other safe regular source file as an asset companion;
- reports missing declared files and unrecognized metadata for review.

The Importer starts with **Auto detect** enabled. A selected parent can contain a mixture of texture, Atlas, HDRI, and model packages: every immediate asset folder is classified independently from provider metadata and local file signatures, while loose root-level HDR/EXR panoramas become individual HDRI candidates. Detection reasons and asset types appear in the review list. A selected folder can be reclassified and rescanned from its **Detected type** control; clicking a top-level section disables automatic selection and forces that type for the complete scan.

Atlas mode recognizes old Megascans `maps` metadata and newer nested `components` metadata. Explicit Atlas declarations are honored, while legacy decal records are treated as Atlases only when their categories and Opacity/Translucency maps identify a cutout package. Reduced maps under Megascans `Thumbs/` folders remain retained companions rather than catalog resolution variants. Imported Atlases live under `atlases/<category>/<asset>/`, keep `"type": "atlas"` in their manifest, and reuse the existing Blender/Houdini PBR material export.

HDRI mode recognizes local HDR and EXR panoramas, including Poly Haven `info.json`, and groups formats into local resolution variants. HDR and EXR dimensions are read from their native headers and assigned to practical 1K, 2K, 4K, 8K, or 16K width bands. Loose pairs such as `Beach_Noon.exr` and `Beach_Noon_sm.exr` become one HDRI with separate full and small resolution variants. Remote JSON URLs are never accessed. HDRI source filenames remain unchanged so retained `.blend` companions have the best chance of keeping relative references intact. Imported thumbnails are always genuine JPEG files.

HDRI files are stored directly in the asset's `maps/` directory rather than inside one-file resolution folders. Resolution grouping remains available through `asset.json` and the UI.

Model mode recognizes USD/USD variants, FBX, OBJ, Alembic, glTF/GLB, Blender, and Maya scenes. One selected source folder becomes one model asset with a uniform managed layout: model files are flattened into `models/`, texture maps into `maps/<resolution>/`, previews into `previews/`, provider JSON into `metadata/`, and ordinary companions into `extras/`. Source subfolders are not recreated. USD is always preferred when locally available and is marked **USD Ready**. Assets without USD still import using the best available Blender, Maya, FBX, OBJ, Alembic, or glTF representation and are marked **No USD**.

VDB mode scans `.vdb` files without loading OpenVDB grids. Quality tokens such as Low, Mid, and High become selectable variants on one asset. A numeric run after a quality token is treated as a sequence frame only when multiple matching frames exist; static numbered collections such as `cloud_formation_001_Low_Res.vdb` remain separate assets. Sequence padding, source frame range, and gaps are preserved in `asset.json`. Managed files live under `vdbs/<category>/<asset>/volumes/<variant>/`. Supplied JPG, PNG, WebP, MOV, and MP4 previews are paired by normalized names; FFmpeg extracts a midpoint thumbnail when available, otherwise the retained video uses a volume placeholder. Portable VDB categories are stored in `.ual/vdb_categories.json`.

Stock mode treats every MOV or MP4 source as its own catalog asset. FFprobe records codec, dimensions, frame rate, duration, audio, and alpha state, while provider previews in `Previews`, `Video_Thumbnails`, `Thumbnails`, or `Proxies` are paired by normalized filename and media agreement. A preview tree beside the selected source folder is discovered automatically. Videos below a recognized preview/proxy/thumbnail folder at any depth are never offered as source assets, even when that folder is selected directly. When provider filenames differ, structured effect-folder and clip-number matching is allowed only when duration and frame counts agree; ambiguous matches stay unassigned. Compatible H.264 4:2:0 previews are preserved unchanged. Missing or incompatible previews become full-duration, no-upscale 480p H.264 MP4 files with AAC audio when present; transparent sources are composited over a neutral checkerboard. Every import receives a JPEG thumbnail extracted at exactly the preview midpoint. Managed Stock files are stored loose in `stock/<category>/` as `<Asset>.<ext>`, `<Asset>_Preview.<ext>`, `<Asset>_Thumbnail.jpg`, and `<Asset>.json`; new Stock imports intentionally omit sidecars and extras. Stock cards play their managed preview silently as soon as they are hovered using one shared decoder; this can be disabled in Settings, and manually started inspector playback takes priority. The Assets inspector provides non-autoplay playback, seeking, looping, mute, volume, and an external-player fallback. FFmpeg is mandatory for Stock import because midpoint thumbnails are always generated.

Stock scans infer one editable category and canonical tags from folder and filename vocabulary. The portable rules live in `.ual/stock_categories.json` and `.ual/stock_tags.json`; they are created from defaults once and never overwrite user edits. Broad categories include Ballistics, Film FX, Impacts, Lens, Magic, Motion Graphics, Explosions, Dust, Smoke, and other common VFX groups. Film FX recognizes motion-picture formats such as 8mm, Super 8, 16mm, Super 16, 35mm, 65mm, and 70mm, along with film grain, damage, scratches, burns, light leaks, gate weave, leaders, and related photochemical artifacts. Aliases normalize terms such as `AtCam`, `Muzzle_Flash`, `Out-of-Focus`, and blend-mode layer names. A specialist filename category can lead while the folder classification becomes a tag—for example, `Water/Lens_Splash_01.mov` uses category Lens and tag `water`. Review shows the matching evidence and allows the category and tags to be changed before import.

ZIP and other packaged archives, `.rat`, renderer proxies/caches such as `.rs`, `.ass`, and `.vrmesh`, hidden files/directories (including `.mayaSwatches`), OS junk, temporary files, and symlinks are never copied. The model review identifies every exclusion and preserves ordinary notes, licenses, metadata, previews, and safe companions. USD-ready catalog assets can be opened through the operating system's associated viewer. Local USD conversion, rendered previews, and an embedded turntable remain future work; Poly Haven models can add provider-supplied USD packages from the inspector.

Checked assets are copied atomically into the configured library. Texture, Atlas, HDRI, and model imports contain their primary payloads, preview, original provider JSON, retained companion files, and a normalized `asset.json`. Companion files keep their source-relative structure under `extras/` and are recorded with their format, size, and SHA-256. Stock uses its compact four-file layout instead. Hidden/system junk, corrupt images, and symlinks remain excluded. Existing provider IDs and matching primary-content fingerprints are skipped rather than overwritten.

Before Import is enabled, **Preflight checked** validates portable paths and available space, hashes the reviewed maps, detects duplicates/provider conflicts, and verifies that source files have not changed since scanning. Scan and preflight work run in the background with progress and safe cancellation. Per-material Ready, Warning, Invalid, Stale, Duplicate, Conflict, Imported, and Failed states keep partial batch problems visible without stopping valid materials.

The scanner builds one source inventory, ignores symlinks and corrupt/empty images, contains JSON-adapter failures as diagnostics, and matches provider declarations by relative path before using an unambiguous basename fallback. Settings reports abandoned staging folders and library locks and provides confirmed recovery actions.

Imported folders and files use human-readable names. For example, `Aerial Asphalt 01` is stored in `aerial-asphalt-01/` with maps such as `Aerial_Asphalt_01_2K_BaseColor.jpg`. A genuinely different same-name material receives a readable `-2` suffix. Stable UUIDs remain inside `asset.json`; untouched provider records remain under `metadata/`.

Texture scanning securely extracts every ZIP found under the selected import folder into a temporary review workspace. A same-name external image such as `Marble_001.jpg` is preferred as the preview for `Marble_001.zip`; otherwise an internal preview or Base Color fallback is used. Same-name JPEG/RAR pairs use the same workflow. Common archive tokens including `COL`, `GLOSS`, `NRM`, and `REFL` normalize to Base Color, Glossiness, Normal, and Specular, and non-power-of-two labels such as 6K are retained. WebP texture maps and previews are converted to genuine JPEG files when they are published to the managed library, while their original source paths remain recorded in the manifest. The complete archive is not duplicated into the managed asset; its original path, format, size, and preflight hash remain in source provenance. The separate **Extract archives** action expands every ZIP or RAR into a same-name sibling folder without overwriting existing folders. ZIP support is built in; RAR scanning and extraction require `bsdtar`.

Use **Extract archives** beside **Scan folder** to expand every ZIP or RAR under the selected source into its own same-name sibling folder before scanning. The complete archive contents, including provider JSON and text metadata, are preserved. Existing destination folders are skipped and never overwritten; extraction happens in a temporary sibling and is published only after that archive completes successfully.

The Settings tab includes confirmed **Update / Fix Library** and **Rename existing assets** actions. Update/Fix validates manifests, converts older HDRI/model layouts, reorganizes legacy Poly Haven USD downloads, and safely flattens legacy per-asset Stock folders. Existing Stock metadata and extras are preserved with asset-prefixed loose filenames during migration. All updates use the same lock, staging, validation, and rollback safeguards.

After a successful batch, the app reloads and opens the Assets tab automatically. Imported cards use real thumbnails, and their detail panels can copy or reveal the managed asset path.

The Assets inspector also provides **Edit material…** for updating the display name, category, tags, author, physical size, and description. Every asset has exactly one category selected from its asset type's `.ual` JSON and any number of searchable tags. Changing the category moves the complete managed asset into that category's filesystem folder, while tag edits never move files. Tags use removable chips with Enter/comma input, multi-value paste, suggestions, and automatic duplicate prevention. The provider boilerplate term `surface` is never retained as a category or tag.

The selected asset inspector also provides **Guess Category** and **Guess Tags**.
These actions send the managed still preview to a local Ollama
`ministral-3:8b` vision model and show a read-only current-versus-suggested
confirmation before changing metadata. Categories are restricted to the
asset-type category JSON; five guessed tags are restricted to the bundled
type-specific vocabulary, with Stock using `.ual/stock_tags.json`. Confirmed
tags merge with existing tags, while confirmed categories use the normal atomic
metadata update and category-folder move. If Ollama or the model is unavailable,
the setup dialog can start the local server and download the model explicitly.
Preview images remain on the local computer.

The Assets catalog uses an expandable category rail beside the asset grid. It starts as an icon strip, can expand to show category names and live result counts, and combines its single-category selection with the current search and technical facet. Only primary categories used by the selected asset type are shown. Switching asset types resets the rail to **All**.

Portable category order, suggestions, aliases, and bundled icon IDs live in `.ual/texture_categories.json`, `.ual/atlas_categories.json`, `.ual/hdri_categories.json`, `.ual/model_categories.json`, `.ual/vdb_categories.json`, and `.ual/stock_categories.json`. Missing files are created from defaults without replacing existing edits. Unknown icon IDs use the generic icon, and invalid configuration falls back to built-in categories with a library warning. Stock aliases continue to drive filename and folder classification.

**Settings → Update / Fix Library** migrates older `categories` arrays by preserving the manifest's primary `category`, moving useful secondary values into lowercase tags, removing `surface`, and atomically publishing the validated manifest. Unknown primary categories are reported for taxonomy attention rather than guessed.

See [SHOTBOX_ASSETS_PLAN.md](SHOTBOX_ASSETS_PLAN.md) for the deliberately reduced roadmap.
