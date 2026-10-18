#!/usr/bin/env python3
"""Bound original-Wii GX vertex-array uploads to bytes referenced by each draw.

OpenSPM's raw CP array-base adapter maps a Wii address through the generic guest
memory mapper. The mapper correctly returns the entire remaining contiguous RAM
span, but Aurora's source-port AttrArray::size means the *declared vertex array
size*. Treating the mapper span as that declared size made the first indexed use
upload megabytes of unrelated Wii RAM into Aurora's fixed 8 MiB storage staging
buffer.

This adaptation keeps the mapper span only as a fail-closed upper bound. For raw
guest arrays, each draw scans its INDEX8/INDEX16 fields, derives the highest
referenced byte for each attribute from index + CP stride + element format, and
uploads only that prefix. A draw is not merged with its predecessor when it
needs a larger guest-array prefix, so an earlier draw never observes a replaced
storage base. Aurora source-port arrays keep their existing explicit-size path.

The transform is exact and idempotent against the pinned Aurora revision after
OpenSPM's raw/raster/FIFO adaptations. Unknown source drift fails closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURES = {
    "lib/gx/gx.hpp": "bool guestMapped = false;",
    "lib/gx/regs.cpp": "array.guestMapped = true;",
    "lib/gx/command_processor.cpp": "guest_array_requirements",
}


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def replace_once(text: str, old: str, new: str, relative: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {relative: source / relative for relative in SIGNATURES}
    texts = {relative: path.read_text(encoding="utf-8") for relative, path in paths.items()}
    states = {relative: signature in texts[relative] for relative, signature in SIGNATURES.items()}
    if all(states.values()):
        return "already-applied"
    if any(states.values()):
        detail = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in states.items())
        raise RuntimeError(f"partial guest-array bounds adaptation detected: {detail}")

    gx = texts["lib/gx/gx.hpp"]
    regs = texts["lib/gx/regs.cpp"]
    cp = texts["lib/gx/command_processor.cpp"]

    gx = replace_once(
        gx,
        "struct AttrArray {\n"
        "  const void* data;\n"
        "  u32 size;\n"
        "  u8 stride;\n"
        "  bool le = true;\n"
        "  gfx::Range cachedRange;\n"
        "};\n"
        "inline bool operator==(const AttrArray& lhs, const AttrArray& rhs) {\n"
        "  return lhs.data == rhs.data && lhs.size == rhs.size && lhs.stride == rhs.stride && lhs.le == rhs.le;\n"
        "}\n",
        "struct AttrArray {\n"
        "  const void* data;\n"
        "  u32 size;\n"
        "  u8 stride;\n"
        "  bool le = true;\n"
        "  // OpenSPM raw-console adapter: size is the mapper's bounded span, not\n"
        "  // a source-port-declared array length. Draw decoding derives the\n"
        "  // actually referenced prefix before staging it.\n"
        "  bool guestMapped = false;\n"
        "  gfx::Range cachedRange;\n"
        "};\n"
        "inline bool operator==(const AttrArray& lhs, const AttrArray& rhs) {\n"
        "  return lhs.data == rhs.data && lhs.size == rhs.size && lhs.stride == rhs.stride && lhs.le == rhs.le &&\n"
        "         lhs.guestMapped == rhs.guestMapped;\n"
        "}\n",
        "lib/gx/gx.hpp",
    )

    regs = replace_once(
        regs,
        "  if (!guest_memory::map(value, 1u, &span)) {\n"
        "    array.data = nullptr;\n"
        "    array.size = 0;\n"
        "    array.cachedRange = {};\n",
        "  if (!guest_memory::map(value, 1u, &span)) {\n"
        "    array.data = nullptr;\n"
        "    array.size = 0;\n"
        "    array.guestMapped = true;\n"
        "    array.cachedRange = {};\n",
        "lib/gx/regs.cpp",
    )
    regs = replace_once(
        regs,
        "  array.data = span.data;\n"
        "  array.size = span.size;\n"
        "  array.le = false;\n"
        "  array.cachedRange = {};\n",
        "  array.data = span.data;\n"
        "  // span.size is only the fail-closed maximum bytes reachable from the\n"
        "  // raw CP base. It is not the logical GX vertex-array length.\n"
        "  array.size = span.size;\n"
        "  array.le = false;\n"
        "  array.guestMapped = true;\n"
        "  array.cachedRange = {};\n",
        "lib/gx/regs.cpp",
    )

    cp = replace_once(
        cp,
        "    if (array.data != newData || array.size != arraySize || array.le != le) {\n",
        "    if (array.data != newData || array.size != arraySize || array.le != le || array.guestMapped) {\n",
        "lib/gx/command_processor.cpp",
    )
    cp = replace_once(
        cp,
        "      array.data = newData;\n"
        "      array.size = arraySize;\n"
        "      array.le = le;\n"
        "      array.cachedRange = {};\n",
        "      array.data = newData;\n"
        "      array.size = arraySize;\n"
        "      array.le = le;\n"
        "      array.guestMapped = false;\n"
        "      array.cachedRange = {};\n",
        "lib/gx/command_processor.cpp",
    )

    helper_anchor = (
        "static void push_gx_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange, gfx::Range idxRange,\n"
        "                         u32 numIndices) noexcept {\n"
    )
    helper = r'''using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;

u32 guest_array_element_size(GXVtxFmt fmt, int attrIndex) noexcept {
  const auto attr = static_cast<GXAttr>(attrIndex);
  const auto& attrFmt = g_gxState.vtxFmts[fmt].attrs[attrIndex];
  // GX_NRM_NBT3 carries three independent indices (N/B/T). Each index points
  // at one normal vector, not one packed 9-component NBT record.
  if (attrIndex == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3) {
    return comp_type_size(attr, attrFmt.type) * 3u;
  }
  return comp_type_size(attr, attrFmt.type) * comp_cnt_count(attr, attrFmt.cnt);
}

GuestArrayRequirements guest_array_requirements(GXVtxFmt fmt, u16 vtxCount, std::span<const u8> vertexData) noexcept {
  GuestArrayRequirements required{};
  const auto& vtxFmt = g_gxState.vtxFmts[fmt];
  size_t cursor = 0;

  const auto record_index = [&](int attrIndex, u32 index) {
    if (attrIndex < GX_VA_POS || attrIndex > GX_VA_TEX7) return;
    auto& array = g_gxState.arrays[attrIndex];
    if (!array.guestMapped) return;
    const u32 elementSize = guest_array_element_size(fmt, attrIndex);
    const uint64_t end = static_cast<uint64_t>(index) * array.stride + elementSize;
    AURORA_ASSERT(array.data != nullptr, "raw GX indexed array {} is unmapped", attrIndex);
    AURORA_ASSERT(end <= array.size,
                  "raw GX indexed array {} needs {} bytes but mapped span has {}", attrIndex, end, array.size);
    required[attrIndex] = std::max(required[attrIndex], static_cast<u32>(end));
  };

  for (u16 vertex = 0; vertex < vtxCount; ++vertex) {
    for (int i = GX_VA_PNMTXIDX; i <= GX_VA_TEX7; ++i) {
      const auto& attrFmt = vtxFmt.attrs[i];
      switch (g_gxState.vtxDesc[i]) {
      case GX_NONE:
        break;
      case GX_DIRECT: {
        const auto attr = static_cast<GXAttr>(i);
        const size_t bytes = comp_type_size(attr, attrFmt.type) * comp_cnt_count(attr, attrFmt.cnt);
        AURORA_ASSERT(cursor <= vertexData.size() && bytes <= vertexData.size() - cursor,
                      "raw GX direct attribute scan overrun: attr={}, need={}, remaining={}", i, bytes,
                      vertexData.size() - std::min(cursor, vertexData.size()));
        cursor += bytes;
        break;
      }
      case GX_INDEX8: {
        const u32 indexCount = i == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3 ? 3u : 1u;
        AURORA_ASSERT(cursor <= vertexData.size() && indexCount <= vertexData.size() - cursor,
                      "raw GX INDEX8 scan overrun: attr={}, need={}, remaining={}", i, indexCount,
                      vertexData.size() - std::min(cursor, vertexData.size()));
        for (u32 n = 0; n < indexCount; ++n) record_index(i, vertexData[cursor++]);
        break;
      }
      case GX_INDEX16: {
        const u32 indexCount = i == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3 ? 3u : 1u;
        const size_t bytes = static_cast<size_t>(indexCount) * sizeof(u16);
        AURORA_ASSERT(cursor <= vertexData.size() && bytes <= vertexData.size() - cursor,
                      "raw GX INDEX16 scan overrun: attr={}, need={}, remaining={}", i, bytes,
                      vertexData.size() - std::min(cursor, vertexData.size()));
        for (u32 n = 0; n < indexCount; ++n) {
          record_index(i, read_bits<u16>(vertexData.data() + cursor));
          cursor += sizeof(u16);
        }
        break;
      }
      default:
        FATAL("raw GX array scan encountered unsupported vertex descriptor {} for attr {}",
              static_cast<u32>(g_gxState.vtxDesc[i]), i);
      }
    }
  }
  AURORA_ASSERT(cursor == vertexData.size(),
                "raw GX array scan consumed {} bytes, expected {}", cursor, vertexData.size());
  return required;
}

bool guest_array_cache_covers(const GuestArrayRequirements& required) noexcept {
  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {
    if (required[i] == 0) continue;
    const auto& array = g_gxState.arrays[i];
    if (array.guestMapped && array.cachedRange.size < required[i]) return false;
  }
  return true;
}

static void push_gx_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange, gfx::Range idxRange,
                         u32 numIndices, const GuestArrayRequirements& guestArrays) noexcept {
'''
    cp = replace_once(cp, helper_anchor, helper, "lib/gx/command_processor.cpp")

    cp = replace_once(
        cp,
        "    auto& array = state.arrays[i];\n"
        "    if (array.cachedRange.size == 0) {\n"
        "      array.cachedRange = gfx::push_storage(static_cast<const uint8_t*>(array.data), array.size);\n"
        "    }\n"
        "    immediates.arrayStart[i - GX_VA_POS] = array.cachedRange.offset;\n",
        "    auto& array = state.arrays[i];\n"
        "    const u32 uploadSize = array.guestMapped ? guestArrays[i] : array.size;\n"
        "    AURORA_ASSERT(!array.guestMapped || uploadSize != 0,\n"
        "                  \"raw GX indexed array {} had no referenced prefix\", i);\n"
        "    if (array.cachedRange.size < uploadSize) {\n"
        "      array.cachedRange = gfx::push_storage(static_cast<const uint8_t*>(array.data), uploadSize);\n"
        "    }\n"
        "    immediates.arrayStart[i - GX_VA_POS] = array.cachedRange.offset;\n",
        "lib/gx/command_processor.cpp",
    )

    cp = replace_once(
        cp,
        "static void handle_draw_unmerged(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange) noexcept {\n",
        "static void handle_draw_unmerged(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange,\n"
        "                                 const GuestArrayRequirements& guestArrays) noexcept {\n",
        "lib/gx/command_processor.cpp",
    )
    cp = replace_once(
        cp,
        "  push_gx_draw(prim, fmt, vtxCount, vertRange, idxRange, numIndices);\n",
        "  push_gx_draw(prim, fmt, vtxCount, vertRange, idxRange, numIndices, guestArrays);\n",
        "lib/gx/command_processor.cpp",
    )

    draw_old = (
        "  const bool cleanState = g_gxState.dirty == 0 && fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n"
        "                          prim != GX_LINES && prim != GX_LINESTRIP && prim != GX_POINTS;\n"
        "  auto* lastDraw = cleanState ? gfx::get_last_draw_command<DrawData>() : nullptr;\n"
        "  const bool canMerge = lastDraw != nullptr && lastDraw->instanceCount == 1;\n"
        "\n"
        "  // Push raw vertex data to buffer. Merged draws must remain contiguous with the previous range.\n"
        "  const auto vertexData = reader.take(totalVtxBytes);\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
    )
    draw_new = (
        "  // Original-Wii indexed arrays have no explicit byte length in CP state.\n"
        "  // Inspect this draw's raw index fields before deciding whether it may\n"
        "  // merge with the previous draw or how much guest array data to stage.\n"
        "  const auto vertexData = reader.take(totalVtxBytes);\n"
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
        "\n"
        "  const bool cleanState = g_gxState.dirty == 0 && fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n"
        "                          prim != GX_LINES && prim != GX_LINESTRIP && prim != GX_POINTS;\n"
        "  auto* lastDraw = cleanState ? gfx::get_last_draw_command<DrawData>() : nullptr;\n"
        "  const bool canMerge = lastDraw != nullptr && lastDraw->instanceCount == 1 &&\n"
        "                        guest_array_cache_covers(guestArrays);\n"
        "\n"
        "  // Push raw vertex data to buffer. Merged draws must remain contiguous with the previous range.\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
    )
    cp = replace_once(cp, draw_old, draw_new, "lib/gx/command_processor.cpp")
    cp = replace_once(
        cp,
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange);\n",
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "lib/gx/command_processor.cpp",
    )

    pending = {
        paths["lib/gx/gx.hpp"]: gx,
        paths["lib/gx/regs.cpp"]: regs,
        paths["lib/gx/command_processor.cpp"]: cp,
    }
    if not check_only:
        for path, contents in pending.items():
            path.write_text(contents, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path, help="pinned, already-adapted Aurora checkout")
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
    if not (source / "include/aurora/gx_guest_memory.hpp").exists():
        print("error: raw-GX guest-memory layer is not applied", file=sys.stderr)
        return 4

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora guest-array bounds adaptation failed: {exc}", file=sys.stderr)
        return 5

    if result == "already-applied":
        print("Aurora guest-array bounds adaptation already applied")
    elif result == "verified":
        print("Aurora guest-array bounds transforms verified")
    else:
        print("Aurora guest-array bounds adaptation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
