#!/usr/bin/env python3
"""Optimize the proven raw guest-array hot path without changing GX semantics.

dev88 removed the temporary per-draw clocks and player testing reported a clear
improvement, but the heaviest scene still crosses the 16.667 ms VI deadline.
The last diagnostic run before retirement measured the two largest synchronous
DrawDone CPU buckets as exact guest-array staging/cache work and the mandatory
guest-index scan.

This migration makes two host-only representation/loop changes:

1. Replace the per-stage unordered_multimap nodes with a fixed frame-local
   bucket array. Entries chain through indices stored in the existing stage
   vector, so lookup performs no hash-node allocation. Hash collisions are
   harmless metadata candidates: reuse still requires the exact source pointer,
   stride, snapshot size and byte-for-byte memcmp. Snapshot bytes, upload
   bytes/ranges, ordering and mutation visibility are unchanged.

2. Build the fixed vertex-record layout once per draw, then scan only indexed
   raw guest attributes across vertices. The old routine rediscovered every
   attribute descriptor for every vertex. The new routine consumes exactly the
   same vertex bytes and computes the same min/max interval, including NBT3
   per-slice offsets.

No visibility-epoch shortcut, hash-for-memcmp substitution, renderer threading
change, FIFO reordering, draw merge change or guest-memory semantic change is
introduced. Unknown/partial source drift fails closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

SIGNATURE = "M14 raw guest-array stage-head index + scan-plan active"
FUSED_SIGNATURE = "M14 raw guest-array fused scan-plan active"
REQUIRED = (
    "M14 gameplay GX audit v1 regression rollback active",
    "M14 raw guest-array pointer-indexed exact lookup active",
    "M14 raw guest-array compact interval staging active",
    "M14 raw guest-array pooled exact snapshots single-copy append active",
    "M14 DrawDone per-draw profiler retired for normal play",
)


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected source fragment exactly once, found {count}"
        )
    return text.replace(old, new, 1)


def replace_function(
    text: str, signature: str, next_signature: str, replacement: str
) -> str:
    start = text.find(signature)
    if start < 0:
        raise RuntimeError(f"missing function signature: {signature}")
    end = text.find(next_signature, start)
    if end < 0:
        raise RuntimeError(f"missing function successor: {next_signature}")
    old = text[start:end]
    required_shape = (
        "sM14GuestArrayStarts.fill(UINT32_MAX);",
        "record_index",
        "GX_INDEX8",
        "GX_INDEX16",
        "cursor == vertexData.size()",
    )
    missing = [marker for marker in required_shape if marker not in old]
    if missing:
        raise RuntimeError(
            "guest-array scan source shape drifted: " + ", ".join(missing)
        )
    return text[:start] + replacement + "\n\n" + text[end:]


SCAN_FUNCTION = r'''GuestArrayRequirements guest_array_requirements(
    GXVtxFmt fmt, u16 vtxCount, std::span<const u8> vertexData) noexcept {
  static bool sM14GuestHotpathReported = false;
  if (!sM14GuestHotpathReported) {
    Log.info("M14 raw guest-array stage-head index + scan-plan active");
    Log.info("M14 raw guest-array fused scan-plan active");
    sM14GuestHotpathReported = true;
  }
  GuestArrayRequirements required{};
  sM14GuestArrayStarts.fill(UINT32_MAX);
  const auto& vtxFmt = g_gxState.vtxFmts[fmt];

  struct M14GuestIndexScan {
    int attrIndex = 0;
    size_t byteOffset = 0;
    u32 indexCount = 0;
    u32 elementSize = 0;
    bool index16 = false;
    u32 minIndex = UINT32_MAX;
    u32 maxIndex = 0u;
    u32 minByte = UINT32_MAX;
    u32 maxByte = 0u;
  };
  std::array<M14GuestIndexScan, MaxVtxAttr> scans{};
  u32 scanCount = 0;
  size_t recordBytes = 0;

  // Descriptor/layout work is invariant for every vertex in this draw. Build
  // it once instead of rediscovering all attributes inside the vertex loop.
  for (int i = GX_VA_PNMTXIDX; i <= GX_VA_TEX7; ++i) {
    const auto& attrFmt = vtxFmt.attrs[i];
    const auto desc = g_gxState.vtxDesc[i];
    switch (desc) {
    case GX_NONE:
      break;
    case GX_DIRECT: {
      const auto attr = static_cast<GXAttr>(i);
      recordBytes +=
          comp_type_size(attr, attrFmt.type) * comp_cnt_count(attr, attrFmt.cnt);
      break;
    }
    case GX_INDEX8:
    case GX_INDEX16: {
      const u32 indexCount =
          i == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3 ? 3u : 1u;
      if (i >= GX_VA_POS && i <= GX_VA_TEX7 &&
          g_gxState.arrays[i].guestMapped) {
        AURORA_ASSERT(scanCount < scans.size(),
                      "raw GX guest scan-plan overflow");
        scans[scanCount++] = {
            i,
            recordBytes,
            indexCount,
            guest_array_element_size(fmt, i),
            desc == GX_INDEX16,
        };
      }
      recordBytes += static_cast<size_t>(indexCount) *
                     (desc == GX_INDEX16 ? sizeof(u16) : sizeof(u8));
      break;
    }
    default:
      FATAL("raw GX array scan encountered unsupported vertex descriptor {} for attr {}",
            static_cast<u32>(desc), i);
    }
  }

  AURORA_ASSERT(
      recordBytes * static_cast<size_t>(vtxCount) == vertexData.size(),
      "raw GX scan-plan record size {} * {} vertices does not match {} bytes",
      recordBytes, vtxCount, vertexData.size());

  // dev109: walk each interleaved vertex record once and update every indexed
  // attribute's exact interval in that pass. The previous dev89 shape walked
  // the full vertex stream once per indexed attribute. This changes only host
  // loop organization: index bytes, NBT3 slice offsets, bounds checks and the
  // resulting guest-memory intervals are identical.
  size_t vertexBase = 0u;
  for (u16 vertex = 0; vertex < vtxCount; ++vertex, vertexBase += recordBytes) {
    for (u32 s = 0; s < scanCount; ++s) {
      auto& scan = scans[s];
      auto& array = g_gxState.arrays[scan.attrIndex];
      AURORA_ASSERT(array.data != nullptr,
                    "raw GX indexed array {} is unmapped", scan.attrIndex);
      const size_t indexBytes = scan.index16 ? sizeof(u16) : sizeof(u8);

      if (scan.indexCount == 1u) {
        const size_t indexOffset = vertexBase + scan.byteOffset;
        AURORA_ASSERT(
            indexOffset <= vertexData.size() &&
                indexBytes <= vertexData.size() - indexOffset,
            "raw GX fused scan-plan index overrun attr={} need={} remaining={}",
            scan.attrIndex, indexBytes,
            vertexData.size() - std::min(indexOffset, vertexData.size()));
        const u32 index = scan.index16
                              ? static_cast<u32>(
                                    read_bits<u16>(vertexData.data() + indexOffset))
                              : static_cast<u32>(vertexData[indexOffset]);
        scan.minIndex = std::min(scan.minIndex, index);
        scan.maxIndex = std::max(scan.maxIndex, index);
        continue;
      }

      for (u32 n = 0; n < scan.indexCount; ++n) {
        const size_t indexOffset =
            vertexBase + scan.byteOffset +
            static_cast<size_t>(n) * indexBytes;
        AURORA_ASSERT(
            indexOffset <= vertexData.size() &&
                indexBytes <= vertexData.size() - indexOffset,
            "raw GX fused scan-plan NBT3 index overrun attr={} need={} remaining={}",
            scan.attrIndex, indexBytes,
            vertexData.size() - std::min(indexOffset, vertexData.size()));
        const u32 index = scan.index16
                              ? static_cast<u32>(
                                    read_bits<u16>(vertexData.data() + indexOffset))
                              : static_cast<u32>(vertexData[indexOffset]);
        const u32 within = n * scan.elementSize;
        const uint64_t begin =
            static_cast<uint64_t>(index) * array.stride + within;
        const uint64_t finish = begin + scan.elementSize;
        AURORA_ASSERT(
            begin <= UINT32_MAX && finish <= array.size,
            "raw GX indexed array {} interval [{},{}) exceeds mapped span {}",
            scan.attrIndex, begin, finish, array.size);
        scan.minByte = std::min(scan.minByte, static_cast<u32>(begin));
        scan.maxByte = std::max(scan.maxByte, static_cast<u32>(finish));
      }
    }
  }

  for (u32 s = 0; s < scanCount; ++s) {
    const auto& scan = scans[s];
    auto& array = g_gxState.arrays[scan.attrIndex];

    // Legal zero-vertex primitives consume no indexed bytes.
    if (vtxCount == 0u) continue;

    if (scan.indexCount == 1u) {
      AURORA_ASSERT(scan.minIndex != UINT32_MAX,
                    "raw GX indexed array {} scan produced no indices",
                    scan.attrIndex);
      const uint64_t begin =
          static_cast<uint64_t>(scan.minIndex) * array.stride;
      const uint64_t finish =
          static_cast<uint64_t>(scan.maxIndex) * array.stride +
          scan.elementSize;
      AURORA_ASSERT(
          begin <= UINT32_MAX && finish <= array.size,
          "raw GX indexed array {} interval [{},{}) exceeds mapped span {}",
          scan.attrIndex, begin, finish, array.size);
      sM14GuestArrayStarts[scan.attrIndex] = static_cast<u32>(begin);
      required[scan.attrIndex] = static_cast<u32>(finish);
      continue;
    }

    AURORA_ASSERT(scan.minByte != UINT32_MAX,
                  "raw GX NBT3 array {} scan produced no indices",
                  scan.attrIndex);
    sM14GuestArrayStarts[scan.attrIndex] = scan.minByte;
    required[scan.attrIndex] = scan.maxByte;
  }

  return required;
}'''


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if SIGNATURE in text:
        # The first dev89 transform accidentally declared stageBucket both at
        # lookup entry and again on the miss insertion path. MSVC correctly
        # rejects that as a same-scope redefinition. Heal that exact cached
        # source shape in-place; there must be exactly one declaration after
        # migration and the miss path reuses it.
        bucket_decl = (
            "  const u32 stageBucket = m14_guest_array_stage_bucket(source);\n"
        )
        bucket_count = text.count(bucket_decl)
        repaired_duplicate_bucket = False
        if bucket_count == 2:
            first = text.find(bucket_decl)
            second = text.find(bucket_decl, first + len(bucket_decl))
            if first < 0 or second < 0:
                raise RuntimeError(
                    "dev89 duplicate stageBucket repair could not locate both declarations"
                )
            text = text[:second] + text[second + len(bucket_decl):]
            repaired_duplicate_bucket = True
        elif bucket_count != 1:
            raise RuntimeError(
                "dev89 applied source must contain exactly one stageBucket declaration; "
                f"found {bucket_count}"
            )

        repaired_zero_vertex_guard = False
        # dev109's fused scanner moved the zero-vertex guard out of the old
        # one-index inner block (4-space indentation instead of dev89's
        # 6-space guard). Detect the semantic guard independent of indentation.
        # Only attempt the legacy dev89 repair when the fused scanner is not
        # already present; an already-fused cached checkout must be idempotent.
        zero_vertex_guard = "if (vtxCount == 0u) continue;"
        if FUSED_SIGNATURE not in text and zero_vertex_guard not in text:
            zero_guard_anchor = (
                "    if (scan.indexCount == 1u) {\n"
                "      u32 minIndex = UINT32_MAX;\n"
            )
            if text.count(zero_guard_anchor) != 1:
                raise RuntimeError(
                    "dev89 applied source is missing zero-vertex guard and "
                    "the exact one-index scan anchor is not unique"
                )
            text = text.replace(
                zero_guard_anchor,
                "    if (scan.indexCount == 1u) {\n"
                "      // Legal zero-vertex primitives consume no indexed bytes and therefore\n"
                "      // require no guest-array interval, matching the pre-dev89 scanner.\n"
                + "      if (vtxCount == 0u) continue;\n"
                + "      u32 minIndex = UINT32_MAX;\n",
                1,
            )
            repaired_zero_vertex_guard = True

        repaired_fused_scan = False
        if FUSED_SIGNATURE not in text:
            scan_start = text.find("GuestArrayRequirements guest_array_requirements(")
            scan_end = text.find("bool guest_array_cache_covers(", scan_start)
            if scan_start < 0 or scan_end < 0:
                raise RuntimeError("dev109 fused-scan upgrade could not locate scan function")
            old_scan = text[scan_start:scan_end]
            required_old_scan = (
                "struct M14GuestIndexScan",
                "for (u32 s = 0; s < scanCount; ++s)",
                "for (u16 vertex = 0; vertex < vtxCount; ++vertex)",
                "if (scan.indexCount == 1u)",
                "raw GX scan-plan index overrun",
            )
            missing_old_scan = [
                marker for marker in required_old_scan if marker not in old_scan
            ]
            if missing_old_scan:
                raise RuntimeError(
                    "dev109 fused-scan predecessor shape drifted: "
                    + ", ".join(missing_old_scan)
                )
            text = text[:scan_start] + SCAN_FUNCTION + "\n\n" + text[scan_end:]
            repaired_fused_scan = True

        required_applied = (
            "constexpr u32 kM14GuestArrayStageBuckets = 2048u;",
            "std::array<u32, kM14GuestArrayStageBuckets> sM14GuestArrayStageHeads{};",
            "u32 previousBucketEntry = UINT32_MAX;",
            "sM14GuestArrayStageHeads.fill(UINT32_MAX);",
            "struct M14GuestIndexScan",
            "if (scan.indexCount == 1u)",
            "if (vtxCount == 0u) continue;",
            'Log.info("M14 raw guest-array stage-head index + scan-plan active");',
            'Log.info("M14 raw guest-array fused scan-plan active");',
            "for (u16 vertex = 0; vertex < vtxCount; ++vertex, vertexBase += recordBytes)",
        )
        missing_applied = [
            marker for marker in required_applied if marker not in text
        ]
        forbidden_applied = [
            marker for marker in (
                "std::unordered_multimap<const void*, u32> sM14GuestArrayStageLookup;",
                "sM14GuestArrayStageLookup.equal_range(source)",
            )
            if marker in text
        ]
        if missing_applied or forbidden_applied:
            raise RuntimeError(
                "guest-array hotpath marker present with inconsistent source: "
                + ", ".join(missing_applied + forbidden_applied)
            )
        if text.count(bucket_decl) != 1:
            raise RuntimeError(
                "dev89 applied source still has an invalid stageBucket declaration count"
            )
        if repaired_duplicate_bucket or repaired_zero_vertex_guard or repaired_fused_scan:
            if not check_only:
                path.write_text(text, encoding="utf-8", newline="\n")
            if check_only:
                return "verified-applied-repair"
            return "applied-repaired"
        return "already-applied"

    missing = [marker for marker in REQUIRED if marker not in text]
    if missing:
        present = [marker for marker in REQUIRED if marker in text]
        if not present:
            # A freshly reset pinned Aurora checkout has not accumulated the
            # player-proven incremental M14 renderer layers yet. This
            # optimization must not make that recovery path fail; simply leave
            # genuinely fresh source untouched.
            return "not-ready"
        raise RuntimeError(
            "partial predecessor guest-array/profiler layers detected; missing: "
            + ", ".join(missing)
        )

    # Fixed bucket heads eliminate the per-entry/per-source hash-node allocator
    # entirely. Exact pointer/stride/snapshot checks remain authoritative.
    text = once(
        text,
        "std::unordered_multimap<const void*, u32> sM14GuestArrayStageLookup;\n",
        "// M14 raw guest-array stage-head index + scan-plan active.\n"
        "constexpr u32 kM14GuestArrayStageBuckets = 2048u;\n"
        "std::array<u32, kM14GuestArrayStageBuckets> sM14GuestArrayStageHeads{};\n"
        "u32 m14_guest_array_stage_bucket(const void* source) noexcept {\n"
        "  std::uintptr_t value = reinterpret_cast<std::uintptr_t>(source);\n"
        "  value ^= value >> 11;\n"
        "  value ^= value >> 23;\n"
        "  return static_cast<u32>((value >> 4) & (kM14GuestArrayStageBuckets - 1u));\n"
        "}\n",
        "stage lookup container",
    )

    text = once(
        text,
        "  sM14GuestArrayStageLookup.clear();\n"
        "  sM14GuestArrayStageLookup.reserve(1024u);\n",
        "  sM14GuestArrayStageHeads.fill(UINT32_MAX);\n",
        "stage bucket reset",
    )

    text = once(
        text,
        "  u32 snapshotOffset = 0;\n"
        "  u32 snapshotSize = 0;\n"
        "  gfx::Range range{};\n",
        "  u32 snapshotOffset = 0;\n"
        "  u32 snapshotSize = 0;\n"
        "  u32 previousBucketEntry = UINT32_MAX;\n"
        "  gfx::Range range{};\n",
        "stage linked-index metadata",
    )

    lookup_pattern = re.compile(
        r"  const auto candidateRange = "
        r"sM14GuestArrayStageLookup\.equal_range\(source\);\n"
        r"  for \(auto lookupIt = candidateRange\.first; "
        r"lookupIt != candidateRange\.second; \+\+lookupIt\) \{\n"
        r".*?"
        r"  \}\n\n"
        r"  \+\+sM14GuestArrayReuseMisses;",
        re.DOTALL,
    )
    match = lookup_pattern.search(text)
    if match is None:
        raise RuntimeError(
            "exact guest-array multimap lookup block was not found"
        )
    old_lookup = match.group(0)
    for marker in (
        "entry.snapshotSize < uploadSize",
        "std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset",
        "M14 raw guest-array pointer-indexed exact lookup active",
    ):
        if marker not in old_lookup:
            raise RuntimeError(
                f"guest-array lookup block drifted; missing {marker}"
            )

    new_lookup = r'''  const u32 stageBucket = m14_guest_array_stage_bucket(source);
  u32 candidateIndex = sM14GuestArrayStageHeads[stageBucket];
  while (candidateIndex != UINT32_MAX) {
    AURORA_ASSERT(candidateIndex < sM14GuestArrayStages.size(),
                  "raw GX stage bucket index exceeds stage vector");
    const auto& entry = sM14GuestArrayStages[candidateIndex];
    const u32 previous = entry.previousBucketEntry;
    if (entry.data == source && entry.stride == array.stride &&
        entry.snapshotSize >= uploadSize) {
      AURORA_ASSERT(static_cast<size_t>(entry.snapshotOffset) + uploadSize <=
                        sM14GuestArraySnapshotBytes.size(),
                    "raw GX pooled snapshot range exceeds frame pool");
      if (std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset,
                      source, uploadSize) == 0) {
        ++sM14GuestArrayReuseHits;
        if (!sM14GuestArrayReuseReported) {
          Log.info("M14 raw guest-array pointer-indexed exact lookup active");
          sM14GuestArrayReuseReported = true;
        }
        return entry.range;
      }
    }
    candidateIndex = previous;
  }

  ++sM14GuestArrayReuseMisses;'''
    text = text[:match.start()] + new_lookup + text[match.end():]

    insert_old = (
        "  sM14GuestArrayStages.emplace_back(std::move(entry));\n"
        "  sM14GuestArrayStageLookup.emplace(source, "
        "static_cast<u32>(sM14GuestArrayStages.size() - 1u));\n"
    )
    insert_new = (
        "  entry.previousBucketEntry = sM14GuestArrayStageHeads[stageBucket];\n"
        "  sM14GuestArrayStages.emplace_back(std::move(entry));\n"
        "  sM14GuestArrayStageHeads[stageBucket] = "
        "static_cast<u32>(sM14GuestArrayStages.size() - 1u);\n"
    )
    text = once(text, insert_old, insert_new, "stage-head insertion")

    text = replace_function(
        text,
        "GuestArrayRequirements guest_array_requirements(",
        "bool guest_array_cache_covers(",
        SCAN_FUNCTION,
    )

    if "unordered_multimap" in text:
        raise RuntimeError("per-stage unordered_multimap remained after migration")
    if "candidateRange = sM14GuestArrayStageLookup.equal_range" in text:
        raise RuntimeError("old equal_range stage lookup remained after migration")
    if "previousBucketEntry" not in text:
        raise RuntimeError("stage-bucket linked index metadata missing")
    if "sM14GuestArrayStageHeads.fill(UINT32_MAX)" not in text:
        raise RuntimeError("stage-bucket frame reset missing")
    if SIGNATURE not in text:
        raise RuntimeError("hotpath signature missing after migration")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
        if actual != expected:
            raise RuntimeError(
                f"Aurora revision mismatch: expected {expected}, got {actual}"
            )
        result = transform(source, args.check_only)
        print(
            {
                "already-applied": "Aurora guest-array stage-head + scan-plan optimization already active",
                "applied-repaired": "Aurora guest-array stage-head + scan-plan applied-source repairs installed",
                "verified-applied-repair": "Aurora guest-array stage-head + scan-plan applied-source repairs verified",
                "not-ready": "Aurora guest-array stage-head + scan-plan optimization skipped on fresh source",
                "verified": "Aurora guest-array stage-head + scan-plan optimization verified",
                "applied": "Aurora guest-array stage-head + scan-plan optimization applied",
            }[result]
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM guest-array hotpath optimization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
