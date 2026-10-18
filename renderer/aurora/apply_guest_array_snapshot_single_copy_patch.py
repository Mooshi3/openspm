#!/usr/bin/env python3
"""Remove redundant zero-fill from M14's pooled exact guest-array snapshots.

Runtime at repo 5d24d64880ba player-proved that pooling per-entry immutable
snapshots is a real performance win while keeping the v19 graphics/spinner/door
correctness frontier intact.  The first pool implementation appends a snapshot
with vector::resize() followed by memcpy().  For std::vector<u8>, resize()
value-initializes every new byte before memcpy immediately overwrites the same
range, so every cache miss performs an unnecessary full-range zero write.

This successor changes only how the already-proven immutable snapshot bytes are
appended: vector::insert(end, source, source + size) copies the exact source bytes
once into the pre-reserved frame-local pool.  Snapshot offsets/sizes, exact
memcmp reuse validation, gfx::push_storage bytes/ranges, guest mutation
visibility, and every GX semantic remain unchanged.

Unknown/partial source drift fails closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
POOL_SIGNATURE = "M14 raw guest-array pooled exact snapshots active; cache semantics remain byte-exact"
SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"
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

    if STAGING_ALIAS_SIGNATURE in text:
        if "gfx::storage_range_data(entry.range)" not in text:
            raise RuntimeError("staging-backed snapshot successor marker is inconsistent")
        return "already-applied"
    if SIGNATURE in text:
        return "already-applied"
    if POOL_SIGNATURE not in text:
        raise RuntimeError("pooled exact guest-array snapshot layer must be active first")
    if "M14 GX hotpath profile" in text:
        raise RuntimeError("retired hotpath experiment is still present")

    text = once(
        text,
        "  sM14GuestArraySnapshotBytes.resize(snapshotOffset + uploadSize);\n"
        "  std::memcpy(sM14GuestArraySnapshotBytes.data() + snapshotOffset, source, uploadSize);\n",
        "  sM14GuestArraySnapshotBytes.insert(\n"
        "      sM14GuestArraySnapshotBytes.end(), source, source + uploadSize);\n",
        "single-copy pooled snapshot append",
    )

    text = once(
        text,
        '    Log.info("M14 raw guest-array pooled exact snapshots active; cache semantics remain byte-exact");\n',
        '    Log.info("M14 raw guest-array pooled exact snapshots single-copy append active; cache semantics remain byte-exact");\n',
        "single-copy runtime marker",
    )

    if "sM14GuestArraySnapshotBytes.resize(snapshotOffset + uploadSize)" in text:
        raise RuntimeError("redundant pooled snapshot resize remained after migration")

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
            "already-applied": "Aurora single-copy pooled guest-array snapshots already active",
            "verified": "Aurora single-copy pooled guest-array snapshot migration verified",
            "applied": "Aurora single-copy pooled guest-array snapshots applied",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM guest-array single-copy snapshot adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
