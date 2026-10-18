#!/usr/bin/env python3
"""Use Aurora's already-copied storage staging bytes as exact guest-array snapshots.

Aurora's pinned recording path maps one fixed WebGPU staging buffer per frame.
frame.storage is a non-owning ByteBuffer view over that mapping; ByteBuffer
cannot relocate a non-owned view and aborts rather than growing it. The mapping
remains live until end_frame, after all producer-side GX recording for that
frame is complete.

The proven guest-array cache currently copies each miss twice:
  1. gfx::push_storage(source, size) -> mapped GPU staging bytes;
  2. source -> a separate frame-local CPU snapshot pool.

Those two byte ranges are identical and have the same frame lifetime. This
migration exposes a read-only pointer for an existing storage Range and uses
the already-uploaded staging bytes for the cache's exact memcmp. The separate
snapshot pool and second full memcpy are removed.

Reuse semantics remain unchanged: exact source pointer, stride, sufficient
staged range, and exact byte-for-byte memcmp are all still required. No hash,
epoch, FIFO, GX invalidation, draw ordering, shader addressing, presentation or
guest-memory semantics change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

SIGNATURE = "M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired"
HOTPATH_SIGNATURE = "M14 raw guest-array stage-head index + scan-plan active"
SINGLE_COPY_SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"


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

    if SIGNATURE in cp:
        required = (
            "const uint8_t* storage_range_data(Range range) noexcept;",
            "const uint8_t* storage_range_data(Range range) noexcept {",
            "entry.range.size >= uploadSize",
            "gfx::storage_range_data(entry.range)",
        )
        combined = rec_hpp + "\n" + rec_cpp + "\n" + cp
        missing = [marker for marker in required if marker not in combined]
        forbidden = [
            marker for marker in (
                "std::vector<u8> sM14GuestArraySnapshotBytes;",
                "u32 snapshotOffset = 0;",
                "u32 snapshotSize = 0;",
                "sM14GuestArraySnapshotBytes.insert(",
            )
            if marker in cp
        ]
        if missing or forbidden:
            raise RuntimeError(
                "staging-snapshot marker present with inconsistent source: "
                + ", ".join(missing + forbidden)
            )
        return "already-applied"

    if HOTPATH_SIGNATURE not in cp:
        if SINGLE_COPY_SIGNATURE not in cp:
            # Genuinely fresh pinned Aurora: the historical player-proven
            # guest-array stack has not been installed, so there is nothing for
            # this successor to rewrite. This keeps clean CI/bootstrap builds
            # valid while partial predecessor state still fails closed.
            return "not-ready"
        raise RuntimeError(
            "single-copy snapshot layer is present without dev89 hotpath"
        )
    if SINGLE_COPY_SIGNATURE not in cp:
        raise RuntimeError("single-copy exact snapshot predecessor is missing")

    # Small read-only API for bytes that push_storage already copied into this
    # frame's mapped staging buffer.
    rec_hpp = once(
        rec_hpp,
        "Range push_storage(const uint8_t* data, size_t length);\n"
        "template <typename T>\n"
        "Range push_storage(ArrayRef<T> data) {\n",
        "Range push_storage(const uint8_t* data, size_t length);\n"
        "const uint8_t* storage_range_data(Range range) noexcept;\n"
        "template <typename T>\n"
        "Range push_storage(ArrayRef<T> data) {\n",
        "recording.hpp storage snapshot accessor",
    )

    rec_cpp = once(
        rec_cpp,
        "Range push_storage(const uint8_t* data, size_t length) {\n"
        "  ZoneScoped;\n"
        "  if (!check_recording(\"push_storage\")) {\n"
        "    return {};\n"
        "  }\n"
        "  return push(current_frame_packet().storage, data, length, resources().limits.minStorageBufferOffsetAlignment);\n"
        "}\n\n"
        "Range push_texture_data",
        "Range push_storage(const uint8_t* data, size_t length) {\n"
        "  ZoneScoped;\n"
        "  if (!check_recording(\"push_storage\")) {\n"
        "    return {};\n"
        "  }\n"
        "  return push(current_frame_packet().storage, data, length, resources().limits.minStorageBufferOffsetAlignment);\n"
        "}\n\n"
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
        "Range push_texture_data",
        "recording.cpp storage snapshot accessor",
    )

    cp = once(
        cp,
        "  u32 snapshotOffset = 0;\n"
        "  u32 snapshotSize = 0;\n"
        "  u32 previousBucketEntry = UINT32_MAX;\n"
        "  gfx::Range range{};\n",
        "  u32 previousBucketEntry = UINT32_MAX;\n"
        "  gfx::Range range{};\n",
        "stage entry snapshot metadata",
    )

    cp = once(
        cp,
        "std::vector<u8> sM14GuestArraySnapshotBytes;\n"
        "bool sM14GuestArraySnapshotPoolReported = false;\n",
        "bool sM14GuestArrayStagingSnapshotAliasReported = false;\n",
        "snapshot pool globals",
    )

    cp = once(
        cp,
        "  sM14GuestArraySnapshotBytes.clear();\n"
        "  if (sM14GuestArraySnapshotBytes.capacity() < 8u * 1024u * 1024u)\n"
        "    sM14GuestArraySnapshotBytes.reserve(8u * 1024u * 1024u);\n",
        "",
        "snapshot pool frame reset",
    )

    cp = once(
        cp,
        "    if (entry.data == source && entry.stride == array.stride &&\n"
        "        entry.snapshotSize >= uploadSize) {\n"
        "      AURORA_ASSERT(static_cast<size_t>(entry.snapshotOffset) + uploadSize <=\n"
        "                        sM14GuestArraySnapshotBytes.size(),\n"
        "                    \"raw GX pooled snapshot range exceeds frame pool\");\n"
        "      if (std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset,\n"
        "                      source, uploadSize) == 0) {\n",
        "    if (entry.data == source && entry.stride == array.stride &&\n"
        "        entry.range.size >= uploadSize) {\n"
        "      const u8* snapshot = gfx::storage_range_data(entry.range);\n"
        "      if (std::memcmp(snapshot, source, uploadSize) == 0) {\n",
        "exact staging-backed snapshot lookup",
    )

    old_tail = (
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
        "    Log.info(\"M14 raw guest-array pooled exact snapshots single-copy append active; cache semantics remain byte-exact\");\n"
        "    sM14GuestArraySnapshotPoolReported = true;\n"
        "  }\n"
    )
    new_tail = (
        "  entry.data = source;\n"
        "  entry.stride = array.stride;\n"
        "  entry.range = range;\n"
        "  if (!sM14GuestArrayStagingSnapshotAliasReported) {\n"
        f"    Log.info(\"{SIGNATURE}\");\n"
        "    sM14GuestArrayStagingSnapshotAliasReported = true;\n"
        "  }\n"
    )
    cp = once(cp, old_tail, new_tail, "duplicate CPU snapshot copy")

    for marker in (
        "std::vector<u8> sM14GuestArraySnapshotBytes;",
        "u32 snapshotOffset = 0;",
        "u32 snapshotSize = 0;",
        "sM14GuestArraySnapshotBytes.insert(",
    ):
        if marker in cp:
            raise RuntimeError(f"retired snapshot pool residue remained: {marker}")
    if "gfx::storage_range_data(entry.range)" not in cp:
        raise RuntimeError("staging-backed exact snapshot lookup missing")
    if SIGNATURE not in cp:
        raise RuntimeError("dev91 runtime signature missing")

    if not check_only:
        rec_hpp_path.write_text(rec_hpp, encoding="utf-8", newline="\n")
        rec_cpp_path.write_text(rec_cpp, encoding="utf-8", newline="\n")
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
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
                "already-applied": "Aurora staging-backed exact guest-array snapshots already active",
                "not-ready": "Aurora staging-backed exact guest-array snapshots skipped on fresh source",
                "verified": "Aurora staging-backed exact guest-array snapshots verified",
                "applied": "Aurora staging-backed exact guest-array snapshots applied",
            }[result]
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM staging-backed guest-array snapshot adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
