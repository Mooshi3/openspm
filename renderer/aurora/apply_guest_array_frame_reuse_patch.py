#!/usr/bin/env python3
"""Reuse identical raw-Wii guest-array staging within one Aurora frame.

The first bounded guest-array repair stopped treating the guest mapper's entire
remaining RAM span as an Aurora array length. It still stages a referenced
prefix into Aurora's fixed 8 MiB storage region. Complex SPM model frames can
switch CP array bases among the same meshes repeatedly; AttrArray::cachedRange
only remembers the currently-selected base, so returning A -> B -> A can stage
A again in the same native frame. Explicit GX vertex-cache invalidation can
also clear the per-slot range even when the bytes did not actually change.

This layer adds an exact per-frame content cache for *raw guest-mapped arrays*.
A staged range may be reused after base switches or GX_CMD_INVL_VC only when the
current source prefix byte-compares equal to a snapshot captured with that
range. Thus same-address mutation remains correct without relying on hashes.
All entries are discarded when Aurora's frame id changes; source-port arrays are
untouched. This reduces redundant staging rather than enlarging the fixed
buffer.

It also adds bounded metadata-only GX_LINES/GX_LINESTRIP diagnostics for the
opening Dark Prognosticus wireframe investigation. No guest array bytes are
logged.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw guest-array per-frame content reuse active"
INDEXED_SIGNATURE = "M14 raw guest-array pointer-indexed exact lookup active"
LINE_SIGNATURE = "M14 GX line draw"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    # The later pointer-index optimization deliberately replaces this layer's
    # one-time reuse log string. Treat that successor marker as proof that the
    # frame-reuse transform is already present; otherwise an incremental CMake
    # rerun mistakes a fully migrated player cache for a fresh checkout and
    # tries to add <cstring>/<utility> a second time.
    if SIGNATURE in text or INDEXED_SIGNATURE in text:
        if text.count(LINE_SIGNATURE) != 1:
            raise RuntimeError("guest-array frame-reuse line signature is inconsistent")
        if SIGNATURE in text and text.count(SIGNATURE) != 1:
            raise RuntimeError("guest-array frame-reuse signature is inconsistent")
        if INDEXED_SIGNATURE in text and text.count(INDEXED_SIGNATURE) != 1:
            raise RuntimeError("guest-array indexed-successor signature is inconsistent")
        return "already-applied"

    if "guest_array_requirements" not in text:
        raise RuntimeError("guest-array bounds adaptation must be applied first")

    # This layer is valid both before and after the optional SEQ_LOAD diagnostic
    # patch, so fresh Aurora checkouts and already-patched player caches use the
    # same configure chain.
    if "#include <aurora/gx_audit.hpp>\n" in text:
        text = replace_once(
            text,
            "#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_audit.hpp>\n",
            "#include <aurora/gfx.hpp>\n#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_audit.hpp>\n",
            "command_processor.cpp gfx include after audit",
        )
    else:
        text = replace_once(
            text,
            "#include <aurora/gx_guest_memory.hpp>\n",
            "#include <aurora/gfx.hpp>\n#include <aurora/gx_guest_memory.hpp>\n",
            "command_processor.cpp gfx include before audit",
        )
    text = replace_once(
        text,
        "#include <cstdint>\n#include <span>\n",
        "#include <cstdint>\n#include <cstring>\n#include <span>\n#include <utility>\n",
        "command_processor.cpp staging includes",
    )

    anchor = (
        "static void push_gx_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange, gfx::Range idxRange,\n"
        "                         u32 numIndices, const GuestArrayRequirements& guestArrays) noexcept {\n"
    )
    helper = r'''struct M14GuestArrayStageEntry {
  const void* data = nullptr;
  u8 stride = 0;
  std::vector<u8> snapshot;
  gfx::Range range{};
};

std::vector<M14GuestArrayStageEntry> sM14GuestArrayStages;
u32 sM14GuestArrayStageFrame = UINT32_MAX;
u64 sM14GuestArrayReuseHits = 0;
u64 sM14GuestArrayReuseMisses = 0;
u64 sM14GuestArrayStagedBytes = 0;
bool sM14GuestArrayReuseReported = false;
bool sM14GuestArrayHighWaterReported = false;
u64 sM14LineDrawCount = 0;

void m14_reset_guest_array_stage_cache_if_needed() noexcept {
  const u32 frame = gfx::current_frame();
  if (sM14GuestArrayStageFrame == frame) return;
  sM14GuestArrayStages.clear();
  sM14GuestArrayStageFrame = frame;
  sM14GuestArrayReuseHits = 0;
  sM14GuestArrayReuseMisses = 0;
  sM14GuestArrayStagedBytes = 0;
  sM14GuestArrayHighWaterReported = false;
}

gfx::Range m14_stage_guest_array(AttrArray& array, u32 uploadSize) noexcept {
  AURORA_ASSERT(array.guestMapped && array.data != nullptr && uploadSize != 0,
                "M14 guest-array stage called with invalid raw array");
  m14_reset_guest_array_stage_cache_if_needed();

  const auto* source = static_cast<const u8*>(array.data);
  for (auto it = sM14GuestArrayStages.rbegin(); it != sM14GuestArrayStages.rend(); ++it) {
    if (it->data != array.data || it->stride != array.stride || it->snapshot.size() < uploadSize) continue;
    if (std::memcmp(it->snapshot.data(), source, uploadSize) != 0) continue;
    ++sM14GuestArrayReuseHits;
    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array per-frame content reuse active");
      sM14GuestArrayReuseReported = true;
    }
    return it->range;
  }

  ++sM14GuestArrayReuseMisses;
  if (!sM14GuestArrayHighWaterReported && sM14GuestArrayStagedBytes + uploadSize >= 7u * 1024u * 1024u) {
    Log.info("M14 raw guest-array staging high-water frame={} staged={} next={} entries={} hits={} misses={}",
             sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,
             sM14GuestArrayStages.size(), sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses);
    sM14GuestArrayHighWaterReported = true;
  }

  const gfx::Range range = gfx::push_storage(source, uploadSize);
  M14GuestArrayStageEntry entry;
  entry.data = array.data;
  entry.stride = array.stride;
  entry.snapshot.assign(source, source + uploadSize);
  entry.range = range;
  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  return range;
}

void m14_log_line_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
                       const GuestArrayRequirements& guestArrays) noexcept {
  if (prim != GX_LINES && prim != GX_LINESTRIP) return;
  const u64 n = ++sM14LineDrawCount;
  if (n > 32u && (n & (n - 1u)) != 0u) return;
  Log.info(
      "M14 GX line draw n={} frame={} prim={} fmt={} vtx={} line_width={} depth={}/{} blend={} pos_req={} nrm_req={} clr0_req={} tex0_req={}",
      n, gfx::current_frame(), static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount,
      static_cast<u32>(g_gxState.lineWidth), g_gxState.depthCompare ? 1u : 0u,
      g_gxState.depthUpdate ? 1u : 0u, static_cast<u32>(g_gxState.blendMode),
      guestArrays[GX_VA_POS], guestArrays[GX_VA_NRM], guestArrays[GX_VA_CLR0],
      guestArrays[GX_VA_TEX0]);
}

static void push_gx_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange, gfx::Range idxRange,
                         u32 numIndices, const GuestArrayRequirements& guestArrays) noexcept {
'''
    text = replace_once(text, anchor, helper, "command_processor.cpp guest stage helper")

    text = replace_once(
        text,
        "    if (array.cachedRange.size < uploadSize) {\n"
        "      array.cachedRange = gfx::push_storage(static_cast<const uint8_t*>(array.data), uploadSize);\n"
        "    }\n",
        "    if (array.cachedRange.size < uploadSize) {\n"
        "      array.cachedRange = array.guestMapped\n"
        "                              ? m14_stage_guest_array(array, uploadSize)\n"
        "                              : gfx::push_storage(static_cast<const uint8_t*>(array.data), uploadSize);\n"
        "    }\n",
        "command_processor.cpp guest-array staging call",
    )

    text = replace_once(
        text,
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n\n"
        "  const bool cleanState = g_gxState.dirty == 0 && fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n",
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
        "  m14_log_line_draw(prim, fmt, vtxCount, guestArrays);\n\n"
        "  const bool cleanState = g_gxState.dirty == 0 && fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n",
        "command_processor.cpp line diagnostic",
    )

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3
    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora guest-array frame-reuse adaptation failed: {exc}", file=sys.stderr)
        return 4
    if result == "already-applied":
        print("Aurora raw guest-array frame reuse already applied")
    elif result == "verified":
        print("Aurora raw guest-array frame reuse verified")
    else:
        print("Aurora raw guest-array frame reuse applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())