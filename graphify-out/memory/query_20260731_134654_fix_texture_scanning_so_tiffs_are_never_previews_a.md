---
type: "query"
date: "2026-07-31T13:46:54.498986+00:00"
question: "Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras"
contributor: "graphify"
outcome: "useful"
source_nodes: ["scan_texture_folder", "_archive_preview_pairs", "_scan_material", "_assign_previews"]
---

# Q: Fix texture scanning so TIFFs are never previews and JPEGs next to ZIPs are previews instead of extras

## Answer

Expanded from graph vocabulary: texture scanner preview extras zip jpeg jpg png tiff. Restricted archive previews to JPG/JPEG/PNG, prevented TIFF/TIFF candidates from entering the general preview pool, added sibling ZIP-preview matching with normalized package-name scoring and a single-sibling fallback, and kept strong preview candidates out of extras while retaining unrelated reference images. Scanner, archive, and repository tests pass.

## Outcome

- Signal: useful

## Source Nodes

- scan_texture_folder
- _archive_preview_pairs
- _scan_material
- _assign_previews