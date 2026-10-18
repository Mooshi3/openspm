#!/usr/bin/env python3
"""Raise Aurora's host-side GX storage arena for OpenSPM's native PC runtime.

Aurora upstream reserves a fixed 8 MiB per-frame storage region. That value is a
host implementation policy, not a Wii hardware limit. OpenSPM's raw-Wii path
keeps guest indexed-array source bytes alive in host-visible storage for the
lifetime of a native frame; complex SPM model frames can legitimately require
more host staging than upstream Aurora's default even after semantic array
bounding and exact per-frame content reuse.

Runtime proof first exhausted 8 MiB in a complex model frame, so M14 raised the
PC-native transient arena to 32 MiB. The first Chapter 1 door transition then
proved 32 MiB is still an arbitrary ceiling: Aurora reached 33,464,320 bytes and
requested 33,609,118 bytes before its intentional non-owned ByteBuffer abort.
The symbolized stack runs directly through m14_stage_guest_array/push_gx_draw.

Use 128 MiB for the native-PC correctness frontier. This does not alter guest
memory, Wii addresses, GX semantics, texture formats, or shipped proprietary
content; it only increases a host transient buffer. Existing high-water/fail-fast
diagnostics remain active so a genuine runaway allocation is still visible.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
UPSTREAM_8 = "inline constexpr uint64_t StorageBufferSize = 8388608;  // 8 MiB\n"
OLD_32 = "inline constexpr uint64_t StorageBufferSize = 33554432; // 32 MiB (OpenSPM native PC)\n"
NEW_128 = "inline constexpr uint64_t StorageBufferSize = 134217728; // 128 MiB (OpenSPM native PC)\n"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gfx/resources.hpp"
    text = path.read_text(encoding="utf-8")
    counts = {
        "8": text.count(UPSTREAM_8),
        "32": text.count(OLD_32),
        "128": text.count(NEW_128),
    }
    if counts["128"] == 1 and counts["8"] == 0 and counts["32"] == 0:
        return "already-applied"
    if counts["128"] != 0 or counts["8"] + counts["32"] != 1:
        raise RuntimeError(
            "lib/gfx/resources.hpp: expected exactly one upstream 8 MiB, old OpenSPM 32 MiB, "
            f"or final OpenSPM 128 MiB storage definition (counts={counts})"
        )
    old = UPSTREAM_8 if counts["8"] == 1 else OLD_32
    if not check_only:
        path.write_text(text.replace(old, NEW_128, 1), encoding="utf-8", newline="\n")
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
        print(f"error: Aurora native-PC storage capacity adaptation failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora native-PC storage capacity already applied (128 MiB)")
    elif result == "verified":
        print("Aurora native-PC storage capacity verified (128 MiB)")
    else:
        print("Aurora native-PC storage capacity applied (128 MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
