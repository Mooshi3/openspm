#!/usr/bin/env python3
"""OpenSPM presenter surface lock for pinned Aurora (lib/webgpu/gpu.cpp/.hpp).

With the present timeline (Stage A/C, frame_mode = smooth / vanilla_timeline)
the swapchain is presented by work the timeline presenter thread queues on
Aurora's render worker, while a window resize reconfigures the same surface on
the main thread (resize_swapchain -> Configure, new frame buffers). Aurora's
window::SurfaceLock only locks on Android, so on Windows a resize could land
between the worker's GetCurrentTexture() and Present() (or during a Stage C
replay) and crash.

This adds one recursive mutex, aurora::webgpu::g_m14SurfaceMutex, held by
- resize_swapchain_internal() (surface Configure + frame buffer recreation),
- refresh_surface() after its GPU synchronize (surface recreation),
- release_surface_locked() (Unconfigure on minimize / surface loss),
and by the timeline presenter's worker job for its whole acquire -> present
span (present_timeline_presenter.inl). Nothing on these paths waits for the
render worker while holding it, so the worker can never block the holder.
The default Vanilla 60 path presents from the thread that resizes and only
takes an uncontended lock.

Runs after apply_turbo_present_patch.py. Exact/idempotent; fails closed on
unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

GPU_CPP = Path("lib/webgpu/gpu.cpp")
GPU_HPP = Path("lib/webgpu/gpu.hpp")
SIGNATURE = "OpenSPM presenter surface lock"

INCLUDE_ANCHOR = "#include <utility>\n"
INCLUDE = "#include <mutex>  // " + SIGNATURE + "\n"

MUTEX_ANCHOR = "namespace aurora::webgpu {\n"
MUTEX = ("// " + SIGNATURE + ": serializes surface reconfiguration (resize, refresh,\n"
         "// release) with the present timeline's render-worker presents.\n"
         "std::recursive_mutex g_m14SurfaceMutex;\n")

HPP_INCLUDE_ANCHOR = "#include <cstdint>\n"
HPP_DECL_ANCHOR = "bool refresh_surface(bool recreate = true);\n"
HPP_DECL = "extern std::recursive_mutex g_m14SurfaceMutex;  // " + SIGNATURE + "\n"
HPP_EDITS = (
    (HPP_INCLUDE_ANCHOR, INCLUDE, "header include"),
    (HPP_DECL_ANCHOR, HPP_DECL, "header declaration"),
)

GUARD = "  std::lock_guard m14SurfaceGuard{g_m14SurfaceMutex};  // " + SIGNATURE + "\n"

EDITS = (
    (INCLUDE_ANCHOR, INCLUDE, "include"),
    (MUTEX_ANCHOR, MUTEX, "mutex"),
    ("static void release_surface_locked() noexcept {\n", GUARD, "release_surface_locked"),
    ("static void resize_swapchain_internal(uint32_t width, uint32_t height, uint32_t nativeWidth, "
     "uint32_t nativeHeight,\n                                      bool force) {\n", GUARD,
     "resize_swapchain_internal"),
    ("bool refresh_surface(bool recreate) {\n  gfx::gpu_synchronize();\n", GUARD, "refresh_surface"),
)


def insert_after(text: str, anchor: str, block: str, label: str) -> str:
    combined = anchor + block
    if combined in text:
        if text.count(combined) != 1:
            raise RuntimeError(f"{label}: inserted block present {text.count(combined)} times")
        return text
    if text.count(anchor) != 1:
        raise RuntimeError(f"{label}: expected one pinned anchor, found {text.count(anchor)}")
    return text.replace(anchor, combined, 1)


def transform(root: Path, check_only: bool = False) -> str:
    originals, updated = {}, {}
    for rel, edits in ((GPU_CPP, EDITS), (GPU_HPP, HPP_EDITS)):
        text = originals[rel] = (root / rel).read_text(encoding="utf-8")
        for anchor, block, label in edits:
            text = insert_after(text, anchor, block, label)
        updated[rel] = text
    if updated[GPU_CPP].count(GUARD) != 3:
        raise RuntimeError(f"expected 3 surface guards, found {updated[GPU_CPP].count(GUARD)}")
    if updated == originals:
        return "already active"
    if check_only:
        return "pending"
    for rel, text in updated.items():
        if text != originals[rel]:
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.source.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora presenter surface lock adaptation failed: {exc}")
        return 4
    print("Aurora presenter surface lock " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
