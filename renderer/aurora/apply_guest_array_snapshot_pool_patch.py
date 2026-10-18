#!/usr/bin/env python3
"""Pool exact raw guest-array snapshots without changing GX semantics.

The 16d2b30 hotpath experiment is retired: its no-guest-index fast path caused
player-visible spinner corruption and a door softlock.  However its diagnostic
breakdown showed the already-existing raw guest-array staging/snapshot path was
the dominant CPU bucket in heavy scenes.  The correctness-restored bed1dc run
then reproduced the real enemy-room slowdown without those regressions.

This migration changes allocation strategy only.  Every staged raw guest-array
interval still owns an immutable byte-for-byte snapshot, every reuse still uses
an exact memcmp, same-address mutation remains observable, and gfx::push_storage
receives exactly the same source bytes/range as before.  Instead of allocating a
separate std::vector<u8> for every stage entry, snapshots are appended to one
frame-local byte pool and entries retain offset+size.  Frame reset clears the
pool but keeps capacity, removing hundreds/thousands of heap allocations/frees
from the synchronous renderer hot path.

Unknown/partial source drift fails closed.  This is a performance candidate and
must be Windows-runtime proven before being called a win.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw guest-array pooled exact snapshots active"
SUCCESSOR_SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"
STAGING_ALIAS_SIGNATURE = "M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    # apply_guest_array_snapshot_single_copy_patch.py is a strict successor of
    # this migration.  It intentionally rewrites the runtime log string, so the
    # original SIGNATURE disappears even though the pooled metadata/storage are
    # still present.  Recognize either marker and validate the durable source
    # shape before declaring the pool installed.  This keeps incremental CMake
    # configure idempotent on already-migrated player caches.
    if STAGING_ALIAS_SIGNATURE in text:
        required = (
            "gfx::storage_range_data(entry.range)",
            "entry.range.size >= uploadSize",
        )
        forbidden = (
            "std::vector<u8> sM14GuestArraySnapshotBytes;",
            "u32 snapshotOffset = 0;",
            "u32 snapshotSize = 0;",
        )
        missing = [marker for marker in required if marker not in text]
        residue = [marker for marker in forbidden if marker in text]
        if missing or residue:
            raise RuntimeError(
                "staging-backed snapshot successor has inconsistent source shape: "
                + ", ".join(missing + residue)
            )
        return "already-applied"
    if SIGNATURE in text or SUCCESSOR_SIGNATURE in text:
        required = (
            "u32 snapshotOffset = 0;",
            "u32 snapshotSize = 0;",
            "std::vector<u8> sM14GuestArraySnapshotBytes;",
        )
        missing = [marker for marker in required if marker not in text]
        if missing:
            raise RuntimeError(
                "pooled snapshot marker present with inconsistent source shape: "
                + ", ".join(missing)
            )
        return "already-applied"
    for marker in (
        "M14 raw guest-array compact interval staging active",
        "M14 raw guest-array pointer-indexed exact lookup active",
        "M14 GX draw CPU profile",
    ):
        if marker not in text:
            raise RuntimeError(f"required proven source layer missing: {marker}")
    if "M14 GX hotpath profile" in text:
        raise RuntimeError("retired hotpath experiment is still present; rollback must run first")

    text = once(
        text,
        "  std::vector<u8> snapshot;\n  gfx::Range range{};\n",
        "  u32 snapshotOffset = 0;\n  u32 snapshotSize = 0;\n  gfx::Range range{};\n",
        "stage-entry pooled snapshot metadata",
    )

    text = once(
        text,
        "std::vector<M14GuestArrayStageEntry> sM14GuestArrayStages;\n",
        "std::vector<M14GuestArrayStageEntry> sM14GuestArrayStages;\n"
        "std::vector<u8> sM14GuestArraySnapshotBytes;\n"
        "bool sM14GuestArraySnapshotPoolReported = false;\n",
        "snapshot pool globals",
    )

    text = once(
        text,
        "  sM14GuestArrayStages.clear();\n",
        "  sM14GuestArrayStages.clear();\n"
        "  sM14GuestArraySnapshotBytes.clear();\n"
        "  if (sM14GuestArraySnapshotBytes.capacity() < 8u * 1024u * 1024u)\n"
        "    sM14GuestArraySnapshotBytes.reserve(8u * 1024u * 1024u);\n",
        "frame-local snapshot pool reset",
    )

    text = once(
        text,
        "    if (entry.stride != array.stride || entry.snapshot.size() < uploadSize) continue;\n"
        "    if (std::memcmp(entry.snapshot.data(), source, uploadSize) != 0) continue;\n",
        "    if (entry.stride != array.stride || entry.snapshotSize < uploadSize) continue;\n"
        "    AURORA_ASSERT(static_cast<size_t>(entry.snapshotOffset) + uploadSize <=\n"
        "                      sM14GuestArraySnapshotBytes.size(),\n"
        "                  \"raw GX pooled snapshot range exceeds frame pool\");\n"
        "    if (std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset,\n"
        "                    source, uploadSize) != 0) continue;\n",
        "exact pooled snapshot lookup",
    )

    text = once(
        text,
        "  entry.data = source;\n"
        "  entry.stride = array.stride;\n"
        "  entry.snapshot.assign(source, source + uploadSize);\n"
        "  entry.range = range;\n",
        "  entry.data = source;\n"
        "  entry.stride = array.stride;\n"
        "  const size_t snapshotOffset = sM14GuestArraySnapshotBytes.size();\n"
        "  AURORA_ASSERT(snapshotOffset <= UINT32_MAX &&\n"
        "                uploadSize <= UINT32_MAX - static_cast<u32>(snapshotOffset),\n"
        "                \"raw GX pooled snapshot offset overflow\");\n"
        "  sM14GuestArraySnapshotBytes.resize(snapshotOffset + uploadSize);\n"
        "  std::memcpy(sM14GuestArraySnapshotBytes.data() + snapshotOffset, source, uploadSize);\n"
        "  entry.snapshotOffset = static_cast<u32>(snapshotOffset);\n"
        "  entry.snapshotSize = uploadSize;\n"
        "  entry.range = range;\n"
        "  if (!sM14GuestArraySnapshotPoolReported) {\n"
        "    Log.info(\"M14 raw guest-array pooled exact snapshots active; cache semantics remain byte-exact\");\n"
        "    sM14GuestArraySnapshotPoolReported = true;\n"
        "  }\n",
        "pooled snapshot append",
    )

    if "entry.snapshot." in text or "snapshot.assign(source" in text:
        raise RuntimeError("per-entry snapshot storage remained after pool migration")

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
            raise RuntimeError(f"Aurora revision mismatch: expected {expected}, got {actual}")
        result = transform(source, args.check_only)
        print({
            "already-applied": "Aurora pooled exact guest-array snapshots already active",
            "verified": "Aurora pooled exact guest-array snapshot migration verified",
            "applied": "Aurora pooled exact guest-array snapshots applied",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM guest-array snapshot-pool adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
