from __future__ import annotations


BLENDER_PREVIEW_MIN_WORKERS = 1
BLENDER_PREVIEW_MAX_WORKERS = 36
BLENDER_PREVIEW_DEFAULT_WORKERS = 2


def normalize_blender_preview_workers(value: int) -> int:
    return min(
        max(BLENDER_PREVIEW_MIN_WORKERS, int(value)),
        BLENDER_PREVIEW_MAX_WORKERS,
    )
