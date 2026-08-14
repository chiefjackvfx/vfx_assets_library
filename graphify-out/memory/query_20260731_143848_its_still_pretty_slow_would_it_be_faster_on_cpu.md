---
type: "query"
date: "2026-07-31T14:38:48.060588+00:00"
question: "its still pretty slow would it be faster on cpu?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["configure_cycles_gpu()", "render_texture_preview()", "select_texture_variant()", "BlenderPreviewSession"]
---

# Q: its still pretty slow would it be faster on cpu?

## Answer

Expanded via graph vocabulary [blender, preview, render, texture, cycles, device, gpu, session, resolution]. No: host inspection shows Blender is using OptiX on an NVIDIA RTX 5090 while the available CPU is a 64-core Threadripper 9980X. The current driver selects OptiX first and disables CPU rendering. CPU is very unlikely to beat this GPU. Persistent sessions removed startup overhead, but every texture still loads up-to-4K maps, performs two 16-sample Cycles renders, converts PNG outputs to JPEG, and re-hashes source maps. Debug blend saving is disabled in the current settings. The best next speed changes are a 2K preview-source cap and an Eevee preview mode, not CPU Cycles.

## Outcome

- Signal: useful

## Source Nodes

- configure_cycles_gpu()
- render_texture_preview()
- select_texture_variant()
- BlenderPreviewSession