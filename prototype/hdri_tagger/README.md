# Asset AI Tagger Prototype

This is a standalone review tool. It does not add controls to the main ShotBox
Assets interface.

## Launch

From the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m prototype.hdri_tagger
```

Choose:

1. An asset type: HDRIs, textures, atlases/decals, 3D models, or Stock footage.
2. A root containing managed assets or source asset folders and their rendered
   JPG, PNG, or WebP previews.
3. The matching ShotBox `.ual/*_categories.json`.
4. The suggested type-specific allowed-tags file from the shared application
   package, or an edited JSON file with the same shape. Stock uses the library's
   existing `stock_tags.json` when it is available.

Select **Scan Preview Folder**, resolve any ambiguous preview choices, then
analyze selected rows or the complete list. The model's category, five tags,
confidence, and rationale remain editable until **Apply Approved** is selected.

## Supported metadata

- Managed ShotBox manifests for HDRIs, textures, atlases, models, and Stock.
  This includes loose `<Asset>.json` Stock manifests.
- Recognized Poly Haven and Megascans source metadata for HDRIs, textures,
  atlases, and models.

Unknown JSON is never edited. Managed assets are updated through
`LibraryRepository`, including the category-folder move. Provider metadata is
backed up beneath `.hdri-tagger-backups/<UTC timestamp>/` in the selected root,
then replaced atomically.

The five proposed tags must come from the configured allow-list. Existing tags
are retained and merged case-insensitively, so the final tag count can exceed
five.

## Ollama

The default model is `ministral-3:8b`. The prompt identifies the selected asset
type so its category and tag choices are evaluated in the correct context. The
tool can start a local `ollama serve`
process and can download the selected model only after confirmation. If the
tool starts the server, it terminates that process when its window closes. An
already-running Ollama server is left alone.
