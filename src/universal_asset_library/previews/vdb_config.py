from __future__ import annotations


VDB_TURNTABLE_FRAME_START = 1
VDB_TURNTABLE_FRAME_END = 36
VDB_TURNTABLE_FRAME_COUNT = (
    VDB_TURNTABLE_FRAME_END - VDB_TURNTABLE_FRAME_START + 1
)
VDB_TURNTABLE_MAX_WORKERS = VDB_TURNTABLE_FRAME_COUNT


def normalize_vdb_turntable_workers(value: int) -> int:
    return min(max(1, int(value)), VDB_TURNTABLE_MAX_WORKERS)
