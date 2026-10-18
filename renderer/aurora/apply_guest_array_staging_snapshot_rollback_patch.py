#!/usr/bin/env python3
"""Restore CPU-cached exact guest-array snapshots after the dev91 mapped-staging A/B.

The dev91 player run proved the mapped GPU staging bytes are safe but expensive
to read on the CPU hot path: DrawDone producer windows regressed from roughly
3.8-4.4 ms at the dev88 baseline to sustained 6-8 ms. On D3D12 the persistently
mapped upload/staging buffer is an appropriate CPU-write/GPU-read target, not a
good source for repeated CPU memcmp reads.

This migration restores the player-proven frame-local CPU snapshot pool while
retaining dev89's fixed bucket index / scan plan and dev92's retired timing
probe. Reuse remains exact: source pointer, stride, snapshot size and byte-for-
byte memcmp must match. GPU upload bytes/ranges, FIFO order and guest mutation
visibility are unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

ALIAS_SIGNATURE = "M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired"
RESTORED_SIGNATURE = "M14 dev94 CPU-cached exact guest-array snapshots restored"
SINGLE_COPY_SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"
HOTPATH_SIGNATURE = "M14 raw guest-array stage-head index + scan-plan active"


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


def transform(source: Path, check_only: bool = False) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    rec_hpp_path = source / "lib/gfx/recording.hpp"
    rec_cpp_path = source / "lib/gfx/recording.cpp"

    cp = cp_path.read_text(encoding="utf-8")
    rec_hpp = rec_hpp_path.read_text(encoding="utf-8")
    rec_cpp = rec_cpp_path.read_text(encoding="utf-8")

    if RESTORED_SIGNATURE in cp:
        required = (
            "std::vector<u8> sM14GuestArraySnapshotBytes;",
            "u32 snapshotOffset = 0;",
            "u32 snapshotSize = 0;",
            "sM14GuestArraySnapshotBytes.insert(",
            "std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset",
            HOTPATH_SIGNATURE,
        )
        combined = cp
        missing = [marker for marker in required if marker not in combined]
        forbidden = [
            marker for marker in (
                ALIAS_SIGNATURE,
                "gfx::storage_range_data(entry.range)",
                "const uint8_t* storage_range_data(Range range) noexcept;",
                "const uint8_t* storage_range_data(Range range) noexcept {",
            )
            if marker in (cp + "\n" + rec_hpp + "\n" + rec_cpp)
        ]
        if missing or forbidden:
            raise RuntimeError(
                "dev94 restored snapshot marker has inconsistent source: "
                + ", ".join(missing + forbidden)
            )
        return "already-restored"

    if ALIAS_SIGNATURE not in cp:
        # Fresh or already-baseline source: require the proven CPU pool shape.
        if (
            SINGLE_COPY_SIGNATURE in cp
            and "std::vector<u8> sM14GuestArraySnapshotBytes;" in cp
            and "sM14GuestArraySnapshotBytes.insert(" in cp
        ):
            return "baseline"
        if HOTPATH_SIGNATURE not in cp:
            return "not-ready"
        raise RuntimeError(
            "dev89 hotpath is active but neither dev91 alias nor exact CPU snapshot baseline is present"
        )

    rec_hpp = once(
        rec_hpp,
        "Range push_storage(const uint8_t* data, size_t length);\n"
        "const uint8_t* storage_range_data(Range range) noexcept;\n"
        "template <typename T>\n",
        "Range push_storage(const uint8_t* data, size_t length);\n"
        "template <typename T>\n",
        "recording.hpp staging readback declaration",
    )

    helper = (
        "const uint8_t* storage_range_data(Range range) noexcept {\n"
        "  auto& storage = current_frame_packet().storage;\n"
        "  AURORA_ASSERT(static_cast<size_t>(range.offset) <= storage.size() &&\n"
        "                    static_cast<size_t>(range.size) <=\n"
        "                        storage.size() - static_cast<size_t>(range.offset),\n"
        "                \"storage range [{},{}) exceeds current mapped staging size {}\",\n"
        "                range.offset, static_cast<uint64_t>(range.offset) + range.size,\n"
        "                storage.size());\n"
        "  return storage.data() + range.offset;\n"
        "}\n\n"
    )
    rec_cpp = once(rec_cpp, helper, "", "recording.cpp staging readback helper")

    cp = once(
        cp,
        "  u32 previousBucketEntry = UINT32_MAX;\n"
        "  gfx::Range range{};\n",
        "  u32 snapshotOffset = 0;\n"
        "  u32 snapshotSize = 0;\n"
        "  u32 previousBucketEntry = UINT32_MAX;\n"
        "  gfx::Range range{};\n",
        "stage CPU snapshot metadata",
    )

    cp = once(
        cp,
        "bool sM14GuestArrayStagingSnapshotAliasReported = false;\n",
        "std::vector<u8> sM14GuestArraySnapshotBytes;\n"
        "bool sM14GuestArraySnapshotPoolReported = false;\n"
        "bool sM14GuestArrayCpuSnapshotRestoreReported = false;\n",
        "CPU snapshot pool globals",
    )

    cp = once(
        cp,
        "  sM14GuestArrayStageHeads.fill(UINT32_MAX);\n",
        "  sM14GuestArrayStageHeads.fill(UINT32_MAX);\n"
        "  sM14GuestArraySnapshotBytes.clear();\n"
        "  if (sM14GuestArraySnapshotBytes.capacity() < 8u * 1024u * 1024u)\n"
        "    sM14GuestArraySnapshotBytes.reserve(8u * 1024u * 1024u);\n",
        "CPU snapshot pool frame reset",
    )

    cp = once(
        cp,
        "    if (entry.data == source && entry.stride == array.stride &&\n"
        "        entry.range.size >= uploadSize) {\n"
        "      const u8* snapshot = gfx::storage_range_data(entry.range);\n"
        "      if (std::memcmp(snapshot, source, uploadSize) == 0) {\n",
        "    if (entry.data == source && entry.stride == array.stride &&\n"
        "        entry.snapshotSize >= uploadSize) {\n"
        "      AURORA_ASSERT(static_cast<size_t>(entry.snapshotOffset) + uploadSize <=\n"
        "                        sM14GuestArraySnapshotBytes.size(),\n"
        "                    \"raw GX pooled snapshot range exceeds frame pool\");\n"
        "      if (std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset,\n"
        "                      source, uploadSize) == 0) {\n",
        "CPU-cached exact snapshot lookup",
    )

    old_tail = (
        "  entry.data = source;\n"
        "  entry.stride = array.stride;\n"
        "  entry.range = range;\n"
        "  if (!sM14GuestArrayStagingSnapshotAliasReported) {\n"
        f"    Log.info(\"{ALIAS_SIGNATURE}\");\n"
        "    sM14GuestArrayStagingSnapshotAliasReported = true;\n"
        "  }\n"
    )
    new_tail = (
        "  entry.data = source;\n"
        "  entry.stride = array.stride;\n"
        "  const size_t snapshotOffset = sM14GuestArraySnapshotBytes.size();\n"
        "  AURORA_ASSERT(snapshotOffset <= UINT32_MAX &&\n"
        "                uploadSize <= UINT32_MAX - static_cast<u32>(snapshotOffset),\n"
        "                \"raw GX pooled snapshot offset overflow\");\n"
        "  sM14GuestArraySnapshotBytes.insert(\n"
        "      sM14GuestArraySnapshotBytes.end(), source, source + uploadSize);\n"
        "  entry.snapshotOffset = static_cast<u32>(snapshotOffset);\n"
        "  entry.snapshotSize = uploadSize;\n"
        "  entry.range = range;\n"
        "  if (!sM14GuestArraySnapshotPoolReported) {\n"
        f"    Log.info(\"{SINGLE_COPY_SIGNATURE}; cache semantics remain byte-exact\");\n"
        "    sM14GuestArraySnapshotPoolReported = true;\n"
        "  }\n"
        "  if (!sM14GuestArrayCpuSnapshotRestoreReported) {\n"
        f"    Log.info(\"{RESTORED_SIGNATURE}\");\n"
        "    sM14GuestArrayCpuSnapshotRestoreReported = true;\n"
        "  }\n"
    )
    cp = once(cp, old_tail, new_tail, "restore CPU snapshot miss path")

    combined = cp + "\n" + rec_hpp + "\n" + rec_cpp
    for marker in (
        ALIAS_SIGNATURE,
        "gfx::storage_range_data(entry.range)",
        "const uint8_t* storage_range_data(Range range) noexcept;",
        "const uint8_t* storage_range_data(Range range) noexcept {",
    ):
        if marker in combined:
            raise RuntimeError(f"dev91 mapped-staging read residue remained: {marker}")
    for marker in (
        RESTORED_SIGNATURE,
        "std::vector<u8> sM14GuestArraySnapshotBytes;",
        "sM14GuestArraySnapshotBytes.insert(",
    ):
        if marker not in cp:
            raise RuntimeError(f"dev94 CPU snapshot restore missing: {marker}")

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        rec_hpp_path.write_text(rec_hpp, encoding="utf-8", newline="\n")
        rec_cpp_path.write_text(rec_cpp, encoding="utf-8", newline="\n")
    return "verified-restore" if check_only else "restored"


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
                "already-restored": "Aurora CPU-cached exact guest-array snapshots already restored",
                "baseline": "Aurora CPU-cached exact guest-array snapshot baseline already active",
                "not-ready": "Aurora CPU-cached guest-array snapshot restore skipped on fresh source",
                "verified-restore": "Aurora CPU-cached exact guest-array snapshot restore verified",
                "restored": "Aurora CPU-cached exact guest-array snapshots restored; dev91 mapped-staging CPU reads retired",
            }[result]
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(
            f"OpenSPM guest-array CPU snapshot restore failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
