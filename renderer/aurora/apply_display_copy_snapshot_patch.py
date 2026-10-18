#!/usr/bin/env python3
"""Give raw Wii display copies real copy-time XFB semantics in cached Aurora.

OpenSPM's first raw-raster bridge treated BP 0x52 display copies as a boolean
presentation notification.  That was enough to choose a host frame boundary,
but it skipped the operation the Wii actually performs: snapshot the current
EFB into an XFB and, when requested, clear the EFB *after* that snapshot.
Presenting Aurora's live EFB later at scheduler idle therefore exposes whatever
the game drew after GXCopyDisp, including a post-copy clear / partial rebuild.

This migration reuses Aurora's existing ordered copy_tex() resolve path for a
host-native XFB-equivalent snapshot.  The raw bridge records the mapped display
copy destination, and aurora_end_frame() presents that copy-time texture for the current
asynchronous frame instead of the later live EFB. The cache allocation may be
reused by a later GXCopyDisp, so this is not cross-frame immutable history.  copy_tex() already records the optional
color/alpha/depth clear after the resolve, preserving command order.

The patch is exact, cache-aware, and idempotent against the pinned Aurora tree
after OpenSPM's raw+raster and later M14 adaptations.  It does not change SU
coordinates, TEV, texgen recovery, guest-array staging, or DrawDone timing.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "OpenSPM M14 Wii display-copy XFB snapshot active"

RAW_HEADER_OLD = '''void notify_display_copy() noexcept;\nbool take_display_copy() noexcept;\n'''
RAW_HEADER_NEW = '''void notify_display_copy(const void* destination) noexcept;\nbool take_display_copy() noexcept;\nconst void* display_copy_destination() noexcept;\n'''

RAW_STATE_OLD = '''std::atomic_bool sDisplayCopy{false};\n'''
RAW_STATE_NEW = '''std::atomic_bool sDisplayCopy{false};\nstd::atomic<const void*> sDisplayCopyDestination{nullptr};\n'''

RAW_NOTIFY_OLD = '''void notify_display_copy() noexcept {\n  sDisplayCopy.store(true, std::memory_order_release);\n}\n\nbool take_display_copy() noexcept {\n  return sDisplayCopy.exchange(false, std::memory_order_acq_rel);\n}\n'''
RAW_NOTIFY_NEW = '''void notify_display_copy(const void* destination) noexcept {\n  sDisplayCopyDestination.store(destination, std::memory_order_release);\n  sDisplayCopy.store(true, std::memory_order_release);\n}\n\nbool take_display_copy() noexcept {\n  return sDisplayCopy.exchange(false, std::memory_order_acq_rel);\n}\n\nconst void* display_copy_destination() noexcept {\n  return sDisplayCopyDestination.load(std::memory_order_acquire);\n}\n'''

REGS_DISPLAY_OLD = '''    raw::notify_display_copy();\n    (void)clear;\n'''
REGS_DISPLAY_NEW = '''    if (g_gxState.texCopyDest == nullptr) {\n      Log.error("Original GX display copy has no mapped destination");\n      raw::notify_display_copy(nullptr);\n      return;\n    }\n\n    // OpenSPM M14 Wii display-copy XFB snapshot active. GXCopyDisp snapshots\n    // the EFB first and only then performs the optional EFB clear. Reuse the\n    // same ordered resolve machinery as GXCopyTex and retain the resulting\n    // copy texture as the host-native XFB equivalent used for presentation.\n    g_gxState.texCopyDstWidth = static_cast<u32>(\n        std::max(g_gxState.texCopySrc.width, 1));\n    g_gxState.texCopyDstHeight = static_cast<u32>(\n        std::max(g_gxState.texCopySrc.height, 1));\n    g_gxState.texCopyDstWide = false;\n    g_gxState.texCopyFmt = GX_TF_RGBA8;\n    copy_tex(g_gxState.texCopyDest, clear);\n    raw::notify_display_copy(g_gxState.texCopyDest);\n'''

AURORA_INCLUDE_OLD = '''#include "gx/gx.hpp"\n#include "gx/texture.hpp"\n'''
AURORA_INCLUDE_NEW = '''#include "gx/gx.hpp"\n#include "gx/texture.hpp"\n#include <aurora/gx_raw.hpp>\n'''

AURORA_FINISH_OLD = '''  gx::fifo::drain();\n  gx::fifo::end_frame();\n  gx::texture::end_frame();\n  gfx::finish();\n  auto imguiDrawData = imgui::freeze();\n\n  const auto& presentSource = webgpu::present_source();\n  const auto viewport = webgpu::calculate_present_viewport(webgpu::g_graphicsConfig.surfaceConfiguration.width,\n                                                           webgpu::g_graphicsConfig.surfaceConfiguration.height,\n                                                           presentSource.size.width, presentSource.size.height);\n'''
AURORA_FINISH_NEW = '''  gx::fifo::drain();\n  gx::fifo::end_frame();\n  gx::texture::end_frame();\n\n  // A raw Wii GXCopyDisp is an EFB -> XFB snapshot. The parser has already\n  // resolved that copy into Aurora's copy-texture cache at the exact BP 0x52\n  // command position. Hold a shared reference across asynchronous submission so\n  // later EFB work cannot change what this presentation displays.\n  gfx::TextureHandle openspmDisplayCopySnapshot;\n  if (const void* displayCopyDestination = gx::raw::display_copy_destination();\n      displayCopyDestination != nullptr) {\n    const auto it = gx::g_gxState.copyTextures.find(displayCopyDestination);\n    if (it != gx::g_gxState.copyTextures.end() && it->second.handle) {\n      openspmDisplayCopySnapshot = it->second.handle;\n    }\n  }\n\n  gfx::finish();\n  auto imguiDrawData = imgui::freeze();\n\n  const auto& livePresentSource = webgpu::present_source();\n  const uint32_t presentWidth = openspmDisplayCopySnapshot\n                                    ? openspmDisplayCopySnapshot->size.width\n                                    : livePresentSource.size.width;\n  const uint32_t presentHeight = openspmDisplayCopySnapshot\n                                     ? openspmDisplayCopySnapshot->size.height\n                                     : livePresentSource.size.height;\n  const auto viewport = webgpu::calculate_present_viewport(webgpu::g_graphicsConfig.surfaceConfiguration.width,\n                                                           webgpu::g_graphicsConfig.surfaceConfiguration.height,\n                                                           presentWidth, presentHeight);\n'''

AURORA_CAPTURE_OLD = '''  gfx::end_frame([rmlBindGroup = std::move(rmlBindGroup), rmlOverlay, viewport,\n                  imguiDrawData = std::move(imguiDrawData)](\n'''
AURORA_CAPTURE_NEW = '''  gfx::end_frame([rmlBindGroup = std::move(rmlBindGroup), rmlOverlay, viewport,\n                  openspmDisplayCopySnapshot = std::move(openspmDisplayCopySnapshot),\n                  imguiDrawData = std::move(imguiDrawData)](\n'''

AURORA_BIND_OLD = '''      if (rmlBindGroup && !rmlOverlay) {\n        presentBindGroup = rmlBindGroup;\n      } else {\n        const auto& resampledSource = webgpu::resample_present_source(encoder, viewport);\n        presentBindGroup = webgpu::create_copy_bind_group(resampledSource);\n      }\n'''
AURORA_BIND_NEW = '''      if (rmlBindGroup && !rmlOverlay) {\n        presentBindGroup = rmlBindGroup;\n      } else if (openspmDisplayCopySnapshot) {\n        // OpenSPM M14 Wii display-copy XFB snapshot active: present the\n        // copy-time resolve, not the live EFB after its optional clear and any\n        // subsequent scene construction. The framebuffer sampler preserves the\n        // native present filtering policy.\n        const webgpu::TextureWithSampler snapshotSource{\n            .texture = openspmDisplayCopySnapshot->texture,\n            .view = openspmDisplayCopySnapshot->sampleTextureView,\n            .size = openspmDisplayCopySnapshot->size,\n            .format = openspmDisplayCopySnapshot->format,\n            .sampler = webgpu::g_frameBuffer.sampler,\n        };\n        presentBindGroup = webgpu::create_copy_bind_group(snapshotSource);\n      } else {\n        const auto& resampledSource = webgpu::resample_present_source(encoder, viewport);\n        presentBindGroup = webgpu::create_copy_bind_group(resampledSource);\n      }\n'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    old_count = text.count(old)
    new_count = text.count(new)
    # Some successor fragments intentionally contain the old text as a prefix
    # (for example adding one include or one adjacent atomic). Recognize the
    # complete new fragment first so a second configure is a clean no-op.
    if new_count == 1:
        return text, False
    if old_count == 1:
        return text.replace(old, new, 1), True
    raise RuntimeError(
        f"{label}: expected exactly one old or one snapshot source fragment "
        f"(old={old_count}, snapshot={new_count})"
    )


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {
        "raw_header": source / "include" / "aurora" / "gx_raw.hpp",
        "raw_source": source / "lib" / "gx" / "raw.cpp",
        "regs": source / "lib" / "gx" / "regs.cpp",
        "aurora": source / "lib" / "aurora.cpp",
    }
    texts = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}

    # dev140 prepares cloned uniform arenas immediately before this finish.
    # Recognize its exact successor fragment on the next CMake configure;
    # otherwise this predecessor rejects a correctly patched player cache.
    finish_new = AURORA_FINISH_NEW
    replay_call = "  gfx::detail::m14_prepare_interpolation_replay();\n"
    if replay_call in texts["aurora"]:
        if texts["aurora"].count(replay_call) != 1:
            raise RuntimeError("display-copy snapshot: duplicate dev140 preparation call")
        finish_new = finish_new.replace("  gfx::finish();\n", replay_call + "  gfx::finish();\n", 1)

    changed = False
    texts["raw_header"], did = replace_once(
        texts["raw_header"], RAW_HEADER_OLD, RAW_HEADER_NEW, "gx_raw.hpp display-copy API"
    )
    changed |= did
    texts["raw_source"], did = replace_once(
        texts["raw_source"], RAW_STATE_OLD, RAW_STATE_NEW, "raw.cpp display-copy destination state"
    )
    changed |= did
    texts["raw_source"], did = replace_once(
        texts["raw_source"], RAW_NOTIFY_OLD, RAW_NOTIFY_NEW, "raw.cpp display-copy API"
    )
    changed |= did
    texts["regs"], did = replace_once(
        texts["regs"], REGS_DISPLAY_OLD, REGS_DISPLAY_NEW, "regs.cpp BP 0x52 display copy"
    )
    changed |= did
    texts["aurora"], did = replace_once(
        texts["aurora"], AURORA_INCLUDE_OLD, AURORA_INCLUDE_NEW, "aurora.cpp raw snapshot include"
    )
    changed |= did
    texts["aurora"], did = replace_once(
        texts["aurora"], AURORA_FINISH_OLD, finish_new, "aurora.cpp snapshot selection"
    )
    changed |= did
    texts["aurora"], did = replace_once(
        texts["aurora"], AURORA_CAPTURE_OLD, AURORA_CAPTURE_NEW, "aurora.cpp snapshot lifetime"
    )
    changed |= did
    texts["aurora"], did = replace_once(
        texts["aurora"], AURORA_BIND_OLD, AURORA_BIND_NEW, "aurora.cpp snapshot presentation"
    )
    changed |= did

    # A partial migration is more dangerous than refusing to configure: every
    # touched source must end in the new API/semantics together.
    required = (
        RAW_HEADER_NEW,
        RAW_STATE_NEW,
        RAW_NOTIFY_NEW,
        REGS_DISPLAY_NEW,
        AURORA_INCLUDE_NEW,
        finish_new,
        AURORA_CAPTURE_NEW,
        AURORA_BIND_NEW,
    )
    joined = "\n".join(texts.values())
    if any(fragment not in joined for fragment in required):
        raise RuntimeError("display-copy snapshot migration ended in a partial source state")
    if SIGNATURE not in texts["aurora"] or SIGNATURE not in texts["regs"]:
        raise RuntimeError("display-copy snapshot signature missing after migration")

    if not check_only and changed:
        for name, path in paths.items():
            path.write_text(texts[name], encoding="utf-8", newline="\n")

    if check_only:
        return "verified"
    return "applied" if changed else "already-applied"


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != "--check-only"):
        print("usage: apply_display_copy_snapshot_patch.py <aurora-source-dir> [--check-only]", file=sys.stderr)
        return 2

    source = pathlib.Path(sys.argv[1]).resolve()
    check_only = len(sys.argv) == 3
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 3
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 4

    try:
        result = transform(source, check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora display-copy snapshot adaptation failed: {exc}", file=sys.stderr)
        return 5

    print(f"Aurora Wii display-copy XFB snapshot {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
