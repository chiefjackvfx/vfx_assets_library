---
type: "query"
date: "2026-07-31T11:17:55.888244+00:00"
question: "Allow rating multiple assets while background saves are still running"
contributor: "graphify"
outcome: "useful"
source_nodes: ["AssetsTab", "RatingUpdateWorker", "AssetMetadataPatch"]
---

# Q: Allow rating multiple assets while background saves are still running

## Answer

Expanded from graph vocabulary: rating update worker queue asset metadata lock. Replaced the single active-rating gate with optimistic multi-asset queuing. Ratings render immediately, writes remain sequential behind the manifest lock, and repeated pending clicks coalesce by asset to the latest value. Confirmed values and failure rollback are tracked independently. Relevant repository, catalog, and UI suites pass.

## Outcome

- Signal: useful

## Source Nodes

- AssetsTab
- RatingUpdateWorker
- AssetMetadataPatch