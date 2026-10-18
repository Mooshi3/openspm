#!/usr/bin/env python3
"""Compatibility entry point for the M14 copy-texture migration chain.

Runtime at 5820200 showed the width/height ownership heuristic rejects valid
scaled EFB feedback immediately after SPM's Wii-A flip transition. Keep the
historical filename in the configure chain so existing player caches are healed
without a .openspm wipe.

First retire the invalid ownership heuristic. Then apply the source-backed raw
GXCopyTex half-scale semantic: BP copy-control bit 9 is the Wii 2:1 box-filter
flag, so a 608x480 EFB source can legitimately become a 304x240 texture. Keeping
both migrations behind this existing entry point makes fresh and cached Aurora
checkouts converge on the same source state during an ordinary update-run.

The 65536-draw hotpath breakdown/no-guest-index fast-path experiment introduced
player-visible correctness regressions at repo 16d2b30d6454: rotating devices
smeared Mario again and a door interaction could softlock. It is retired. Heal
only cached Aurora trees that actually contain the hotpath marker, retain the
player-proven coarse profiler, then apply the player-proven pooled exact snapshot
layer and its narrower single-copy append successor. Both preserve exact
byte-for-byte guest mutation validation; the successor only removes the pool's
redundant zero-fill before copying snapshot bytes.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
HOTPATH_MARKER = "M14 GX hotpath profile"


def run(name: str) -> int:
    command = [sys.executable, str(HERE / name), *sys.argv[1:]]
    return int(subprocess.run(command).returncode)


def cached_aurora_has_hotpath() -> bool:
    if len(sys.argv) < 2:
        return False
    path = pathlib.Path(sys.argv[1]).resolve() / "lib/gx/command_processor.cpp"
    try:
        return HOTPATH_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def main() -> int:
    rc = run("apply_copy_texture_ownership_rollback_patch.py")
    if rc != 0:
        return rc
    rc = run("apply_raw_copy_half_scale_patch.py")
    if rc != 0:
        return rc

    # Existing player caches may already contain the hotpath-transformed
    # handle_draw source, which the older coarse-profiler exact anchor correctly
    # rejects. Heal those caches first. Fresh pinned checkouts skip this rollback
    # and flow directly through the ordinary coarse-profiler install.
    if cached_aurora_has_hotpath():
        rc = run("apply_draw_hotpath_breakdown_rollback_patch.py")
        if rc != 0:
            return rc
    rc = run("apply_draw_cpu_profile_patch.py")
    if rc != 0:
        return rc
    rc = run("apply_guest_array_snapshot_pool_patch.py")
    if rc != 0:
        return rc
    return run("apply_guest_array_snapshot_single_copy_patch.py")


if __name__ == "__main__":
    raise SystemExit(main())
