---
type: "query"
date: "2026-07-31T13:56:41.925154+00:00"
question: "with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras."
contributor: "graphify"
outcome: "useful"
source_nodes: ["scan_texture_folder()", "scanner.py", "TextureMap"]
---

# Q: with textures ALPHAMASKED is like opacity so that needs to be set up properly in the scan atm its just getting put into extras.

## Answer

Expanded from original query via graph vocab: [alpha, channel, extra, map, scan, semantic, texture]. Updated TOKEN_PATTERNS in scanner.py so ALPHAMASKED and ALPHA_MASKED map to the canonical Opacity channel. Added a regression test proving the files enter the 2K Opacity map list and are absent from extra_paths. Full importer scanner suite passed: 19 passed, 1 skipped.

## Outcome

- Signal: useful

## Source Nodes

- scan_texture_folder()
- scanner.py
- TextureMap