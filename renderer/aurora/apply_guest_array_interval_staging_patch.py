#!/usr/bin/env python3
"""Stage only the raw GX guest-array byte interval referenced by each draw.

Heavy SPM scenes prove that the prefix-bounds repair can still copy many MiB per
Aurora frame because each indexed attribute stages [base,max) even when every
referenced index is far above zero.  This layer preserves the existing shader
ABI and guest index values while staging only [minByte,maxByte).

The proven M14 source already includes the NBT3 bounds repair, so record_index
carries a per-slice `within` byte offset.  Both the minimum and maximum interval
bounds include that offset.  The shader still computes:

    arrayStart + guestIndex * stride + within

For raw arrays we set arrayStart to `hostRange.offset - guestBegin` using u32
modular arithmetic, so the final storage address is unchanged.  Source-port
arrays are untouched.  Existing exact snapshots, pointer-indexed lookup and
visibility invalidation remain authoritative.

This migration is exact/idempotent against both a fresh fully-patched checkout
and the runtime-proven v19 player cache.  Unknown or partial source drift fails
closed before modifying files.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw guest-array compact interval staging active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    gx_path = source / "lib/gx/gx.hpp"
    cp_path = source / "lib/gx/command_processor.cpp"
    gx = gx_path.read_text(encoding="utf-8")
    cp = cp_path.read_text(encoding="utf-8")

    if SIGNATURE in cp:
        if "guestCachedBegin" not in gx or "guestCachedEnd" not in gx:
            raise RuntimeError("compact interval marker present without AttrArray interval metadata")
        return "already-applied"

    required_markers = (
        "M14 raw guest-array pointer-indexed exact lookup active",
        "M14 raw guest-array stage profile",
        "M14 gameplay GX audit v1 regression rollback active",
        "guest_array_requirements",
    )
    missing = [marker for marker in required_markers if marker not in cp]
    if missing:
        raise RuntimeError("required proven guest-array layers missing: " + ", ".join(missing))
    if "bool guestMapped = false;" not in gx:
        raise RuntimeError("raw guest-array AttrArray metadata is missing")

    # AttrArray remembers which guest interval its current cachedRange covers.
    gx = once(
        gx,
        "  bool guestMapped = false;\n  gfx::Range cachedRange;\n",
        "  bool guestMapped = false;\n"
        "  // Raw-console guest byte interval represented by cachedRange.\n"
        "  u32 guestCachedBegin = 0;\n"
        "  u32 guestCachedEnd = 0;\n"
        "  gfx::Range cachedRange;\n",
        "gx.hpp guest interval metadata",
    )

    # Keep the existing max-end array ABI and add a parallel minimum-begin array.
    cp = once(
        cp,
        "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n\n"
        "u32 guest_array_element_size",
        "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n"
        "std::array<u32, MaxVtxAttr> sM14GuestArrayStarts{};\n\n"
        "u32 guest_array_element_size",
        "command_processor guest interval starts",
    )
    cp = once(
        cp,
        "  GuestArrayRequirements required{};\n"
        "  const auto& vtxFmt = g_gxState.vtxFmts[fmt];\n",
        "  GuestArrayRequirements required{};\n"
        "  sM14GuestArrayStarts.fill(UINT32_MAX);\n"
        "  const auto& vtxFmt = g_gxState.vtxFmts[fmt];\n",
        "command_processor interval reset",
    )

    # Proven source shape includes the retained NBT3 `within` repair.  Include
    # `within` in BOTH ends of the compact interval; otherwise B/T slices could
    # address before the uploaded range.
    cp = once(
        cp,
        "    const u32 elementSize = guest_array_element_size(fmt, attrIndex);\n"
        "    const uint64_t end = static_cast<uint64_t>(index) * array.stride + within + elementSize;\n"
        "    AURORA_ASSERT(array.data != nullptr, \"raw GX indexed array {} is unmapped\", attrIndex);\n"
        "    AURORA_ASSERT(end <= array.size,\n"
        "                  \"raw GX indexed array {} needs {} bytes but mapped span has {}\", attrIndex, end, array.size);\n"
        "    required[attrIndex] = std::max(required[attrIndex], static_cast<u32>(end));\n",
        "    const u32 elementSize = guest_array_element_size(fmt, attrIndex);\n"
        "    const uint64_t begin = static_cast<uint64_t>(index) * array.stride + within;\n"
        "    const uint64_t end = begin + elementSize;\n"
        "    AURORA_ASSERT(array.data != nullptr, \"raw GX indexed array {} is unmapped\", attrIndex);\n"
        "    AURORA_ASSERT(begin <= UINT32_MAX && end <= array.size,\n"
        "                  \"raw GX indexed array {} interval [{},{}) exceeds mapped span {}\",\n"
        "                  attrIndex, begin, end, array.size);\n"
        "    sM14GuestArrayStarts[attrIndex] =\n"
        "        std::min(sM14GuestArrayStarts[attrIndex], static_cast<u32>(begin));\n"
        "    required[attrIndex] = std::max(required[attrIndex], static_cast<u32>(end));\n",
        "command_processor NBT3-aware interval scan",
    )

    cp = once(
        cp,
        "bool guest_array_cache_covers(const GuestArrayRequirements& required) noexcept {\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n"
        "    if (required[i] == 0) continue;\n"
        "    const auto& array = g_gxState.arrays[i];\n"
        "    if (array.guestMapped && array.cachedRange.size < required[i]) return false;\n"
        "  }\n"
        "  return true;\n"
        "}\n",
        "bool guest_array_cache_covers(const GuestArrayRequirements& required) noexcept {\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n"
        "    if (required[i] == 0) continue;\n"
        "    const auto& array = g_gxState.arrays[i];\n"
        "    if (!array.guestMapped) continue;\n"
        "    const u32 begin = sM14GuestArrayStarts[i];\n"
        "    AURORA_ASSERT(begin != UINT32_MAX && begin < required[i],\n"
        "                  \"raw GX indexed array {} has invalid requested interval [{},{})\",\n"
        "                  i, begin, required[i]);\n"
        "    if (array.cachedRange.size == 0 || array.guestCachedBegin > begin ||\n"
        "        array.guestCachedEnd < required[i]) return false;\n"
        "  }\n"
        "  return true;\n"
        "}\n",
        "command_processor merge interval coverage",
    )

    # Stage helper now accepts an interval.  Existing timing, exact memcmp and
    # pointer-indexed cache logic are preserved around this source pointer.
    cp = once(
        cp,
        "gfx::Range m14_stage_guest_array(AttrArray& array, u32 uploadSize) noexcept {\n"
        "  AURORA_ASSERT(array.guestMapped && array.data != nullptr && uploadSize != 0,\n"
        "                \"M14 guest-array stage called with invalid raw array\");\n"
        "  m14_reset_guest_array_stage_cache_if_needed();\n",
        "gfx::Range m14_stage_guest_array(AttrArray& array, u32 uploadBegin, u32 uploadEnd) noexcept {\n"
        "  AURORA_ASSERT(array.guestMapped && array.data != nullptr && uploadBegin < uploadEnd && uploadEnd <= array.size,\n"
        "                \"M14 guest-array interval stage called with invalid raw array/range\");\n"
        "  const u32 uploadSize = uploadEnd - uploadBegin;\n"
        "  m14_reset_guest_array_stage_cache_if_needed();\n",
        "command_processor interval stage signature",
    )
    cp = once(
        cp,
        "  const auto* source = static_cast<const u8*>(array.data);\n"
        "  const auto candidateRange = sM14GuestArrayStageLookup.equal_range(array.data);\n",
        "  const auto* source = static_cast<const u8*>(array.data) + uploadBegin;\n"
        "  const auto candidateRange = sM14GuestArrayStageLookup.equal_range(source);\n",
        "command_processor interval lookup key",
    )
    cp = once(cp, "  entry.data = array.data;\n  entry.stride = array.stride;\n",
              "  entry.data = source;\n  entry.stride = array.stride;\n",
              "command_processor interval cache entry key")
    cp = once(
        cp,
        "  sM14GuestArrayStageLookup.emplace(array.data, static_cast<u32>(sM14GuestArrayStages.size() - 1u));\n"
        "  sM14GuestArrayStagedBytes += uploadSize;\n",
        "  sM14GuestArrayStageLookup.emplace(source, static_cast<u32>(sM14GuestArrayStages.size() - 1u));\n"
        "  sM14GuestArrayStagedBytes += uploadSize;\n",
        "command_processor interval cache lookup insert",
    )

    # The runtime-proven source deliberately retains a fatal-metadata diagnostic
    # between uploadSize and the assert.  Match that exact shape rather than the
    # older pre-gameplay-audit block which caused the 8f37317 configure failure.
    old_stage = '''    auto& array = state.arrays[i];
    const u32 uploadSize = array.guestMapped ? guestArrays[i] : array.size;
    if (array.guestMapped && uploadSize == 0) {
      Log.error("M14 nonempty raw GX draw missing prefix prim={} fmt={} vtx={} attr={} desc={} base=0x{:X} span={} stride={} cached={} epoch={}",
                static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount, i,
                static_cast<u32>(state.vtxDesc[i]),
                static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),
                array.size, static_cast<u32>(array.stride), array.cachedRange.size,
                sM14GuestArrayVisibilityEpoch);
    }
    AURORA_ASSERT(!array.guestMapped || uploadSize != 0,
                  "raw GX indexed array {} had no referenced prefix", i);
    if (array.cachedRange.size < uploadSize) {
      array.cachedRange = array.guestMapped
                              ? m14_stage_guest_array(array, uploadSize)
                              : gfx::push_storage(static_cast<const uint8_t*>(array.data), uploadSize);
    }
    immediates.arrayStart[i - GX_VA_POS] = array.cachedRange.offset;
'''
    new_stage = '''    auto& array = state.arrays[i];
    if (array.guestMapped) {
      const u32 uploadBegin = sM14GuestArrayStarts[i];
      const u32 uploadEnd = guestArrays[i];
      if (uploadEnd == 0) {
        Log.error("M14 nonempty raw GX draw missing interval prim={} fmt={} vtx={} attr={} desc={} base=0x{:X} span={} stride={} cached={} epoch={}",
                  static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount, i,
                  static_cast<u32>(state.vtxDesc[i]),
                  static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),
                  array.size, static_cast<u32>(array.stride), array.cachedRange.size,
                  sM14GuestArrayVisibilityEpoch);
      }
      AURORA_ASSERT(uploadBegin != UINT32_MAX && uploadBegin < uploadEnd && uploadEnd <= array.size,
                    "raw GX indexed array {} had invalid compact interval [{},{}) of {}",
                    i, uploadBegin, uploadEnd, array.size);
      const bool cachedCovers = array.cachedRange.size != 0 &&
                                array.guestCachedBegin <= uploadBegin &&
                                array.guestCachedEnd >= uploadEnd;
      if (!cachedCovers) {
        array.cachedRange = m14_stage_guest_array(array, uploadBegin, uploadEnd);
        array.guestCachedBegin = uploadBegin;
        array.guestCachedEnd = uploadEnd;
      }
      // Intentional u32 modular bias: the shader adds the original guest
      // index*stride(+within), exactly cancelling -guestCachedBegin.
      immediates.arrayStart[i - GX_VA_POS] = array.cachedRange.offset - array.guestCachedBegin;
    } else {
      const u32 uploadSize = array.size;
      if (array.cachedRange.size < uploadSize) {
        array.cachedRange = gfx::push_storage(static_cast<const uint8_t*>(array.data), uploadSize);
      }
      immediates.arrayStart[i - GX_VA_POS] = array.cachedRange.offset;
    }
'''
    cp = once(cp, old_stage, new_stage, "command_processor compact guest staging")

    cp = once(
        cp,
        "  ++sM14GuestArrayReuseMisses;\n",
        "  ++sM14GuestArrayReuseMisses;\n"
        "  static bool sM14CompactIntervalReported = false;\n"
        "  if (!sM14CompactIntervalReported && uploadBegin != 0u) {\n"
        "    Log.info(\"M14 raw guest-array compact interval staging active begin={} end={} bytes={} prefix_saved={}\",\n"
        "             uploadBegin, uploadEnd, uploadSize, uploadBegin);\n"
        "    sM14CompactIntervalReported = true;\n"
        "  }\n",
        "command_processor compact interval marker",
    )

    if not check_only:
        gx_path.write_text(gx, encoding="utf-8", newline="\n")
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora guest-array interval staging adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-applied": "Aurora compact raw guest-array interval staging already active",
        "verified": "Aurora compact raw guest-array interval staging verified",
        "applied": "Aurora compact raw guest-array interval staging applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
