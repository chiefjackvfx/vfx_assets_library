---
type: "query"
date: "2026-07-31T14:34:07.681082+00:00"
question: "PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching"
contributor: "graphify"
outcome: "useful"
source_nodes: ["BlenderPreviewSession", "render_texture_preview()", "render_hdri_preview()", "LibraryRepository", "AssetsTab"]
---

# Q: PLEASE IMPLEMENT THIS PLAN: Faster Persistent Blender Preview Batching

## Answer

Expanded from the code graph via [batch, blender, preview, render, texture, hdri, worker, queue, process, session, protocol, repository]. Implemented BlenderPreviewSession with a prefixed JSON-lines protocol, one persistent Blender process for serial texture/HDRI jobs, lazy template switching, cached template hashes and GPU discovery, per-job cleanup, cancellation/crash recovery, repository batch reuse, and UI queue ownership with asynchronous retirement. Real Blender tests passed for two textures, two HDRIs, and alternating texture/HDRI jobs in one process. Full repository suite passed outside the socket-restricted sandbox; model importer regression tests also passed.

## Outcome

- Signal: useful

## Source Nodes

- BlenderPreviewSession
- render_texture_preview()
- render_hdri_preview()
- LibraryRepository
- AssetsTab