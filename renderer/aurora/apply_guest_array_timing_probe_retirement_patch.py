#!/usr/bin/env python3
"""Retire the old per-stage guest-array timing probe from normal play.

The timing probe was useful when the raw guest-array path was still being
localized, but its measurements already produced the dev89/dev91 optimization
work. Keeping steady_clock reads and aggregate counters inside every
m14_stage_guest_array request now contaminates the narrow 16.667 ms producer
deadline we are trying to recover.

This migration removes diagnostics only. Guest-array interval calculation,
exact byte snapshots/reuse validation, gfx::push_storage uploads, stage-cache
lookup, FIFO ordering, draw ordering, shader addressing and guest-memory
semantics are unchanged.

The transform accepts the proven pre-dev92 source shape and is idempotent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

PROFILE_SIGNATURE = "M14 raw guest-array stage profile"
RETIRED_SIGNATURE = "M14 raw guest-array stage timing probe retired for normal play"
DEV91_SIGNATURE = "M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired"
DEV94_SIGNATURE = "M14 dev94 CPU-cached exact guest-array snapshots restored"
CPU_BASELINE_SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"


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


PROFILE_GLOBALS = """u64 sM14GuestArrayStageNanos = 0;
u64 sM14GuestArrayStageCalls = 0;
u64 sM14GuestArrayProfileFrames = 0;
"""

PROFILE_FRAME_REPORT = """  if (sM14GuestArrayHighWaterReported) {
    const u64 sample = ++sM14GuestArrayProfileFrames;
    if (sample <= 8u || (sample & (sample - 1u)) == 0u) {
      Log.info("M14 raw guest-array stage profile sample={} frame={} staged={} entries={} calls={} hits={} misses={} cpu_us={}",
               sample, sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes,
               sM14GuestArrayStages.size(), sM14GuestArrayStageCalls,
               sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses,
               sM14GuestArrayStageNanos / 1000u);
    }
  }
"""

PROFILE_RESET = """  sM14GuestArrayStageNanos = 0;
  sM14GuestArrayStageCalls = 0;
"""

PROFILE_ENTRY = """  const auto profileStart = std::chrono::steady_clock::now();
  const auto profileFinish = [&]() noexcept {
    const auto elapsed = std::chrono::steady_clock::now() - profileStart;
    sM14GuestArrayStageNanos += static_cast<u64>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count());
    ++sM14GuestArrayStageCalls;
  };

"""


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib" / "gx" / "command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if RETIRED_SIGNATURE in text:
        forbidden = (
            PROFILE_SIGNATURE,
            "sM14GuestArrayStageNanos",
            "sM14GuestArrayStageCalls",
            "sM14GuestArrayProfileFrames",
            "profileFinish",
        )
        residue = [marker for marker in forbidden if marker in text]
        if residue:
            raise RuntimeError(
                "guest-array timing retirement marker present with profiler residue: "
                + ", ".join(residue)
            )
        return "already-retired"

    if (
        DEV91_SIGNATURE not in text
        and DEV94_SIGNATURE not in text
        and CPU_BASELINE_SIGNATURE not in text
    ):
        # Genuinely fresh source has no proven exact guest-array cache frontier
        # yet. dev92 must not guess at an unrelated shape.
        return "not-ready"
    if PROFILE_SIGNATURE not in text:
        raise RuntimeError(
            "proven guest-array cache source is present but guest-array timing profile signature is missing"
        )

    text = once(text, PROFILE_GLOBALS, "", "guest-array timing globals")
    text = once(text, PROFILE_FRAME_REPORT, "", "guest-array timing frame report")
    text = once(text, PROFILE_RESET, "", "guest-array timing frame reset")
    text = once(text, PROFILE_ENTRY, "", "guest-array timing entry clock")

    finish_count = text.count("  profileFinish();\n")
    if finish_count not in (1, 2):
        raise RuntimeError(
            "guest-array timing finish shape drifted: expected 1 or 2 calls, "
            f"found {finish_count}"
        )
    text = text.replace("  profileFinish();\n", "")

    marker_anchor = "u64 sM14GuestArrayStagedBytes = 0;\n"
    text = once(
        text,
        marker_anchor,
        marker_anchor
        + "// M14 raw guest-array stage timing probe retired for normal play.\n",
        "guest-array timing retirement marker",
    )

    forbidden = (
        PROFILE_SIGNATURE,
        "sM14GuestArrayStageNanos",
        "sM14GuestArrayStageCalls",
        "sM14GuestArrayProfileFrames",
        "profileFinish",
        "profileStart",
    )
    residue = [marker for marker in forbidden if marker in text]
    if residue:
        raise RuntimeError(
            "guest-array timing profiler residue remained after retirement: "
            + ", ".join(residue)
        )
    if RETIRED_SIGNATURE not in text:
        raise RuntimeError("guest-array timing retirement marker missing")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified-retire" if check_only else "retired"


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
                "already-retired": "Aurora guest-array timing probe already retired for normal play",
                "not-ready": "Aurora guest-array timing retirement skipped before proven guest-array cache source",
                "verified-retire": "Aurora guest-array timing probe retirement verified",
                "retired": "Aurora guest-array timing probe retired for normal play",
            }[result]
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(
            f"OpenSPM guest-array timing probe retirement failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
