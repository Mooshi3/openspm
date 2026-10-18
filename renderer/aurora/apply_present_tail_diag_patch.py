#!/usr/bin/env python3
"""Add tail-only logging to the existing M14 Aurora Present pacing probe.

The aggregate probe already timestamps every successful surface Present(). dev122
only emits a line when the already-computed interval exceeds 25 ms, so it adds no
new clocks and essentially no normal-frame cost.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
TARGET = pathlib.Path("lib/aurora.cpp")
SIGNATURE = "g_m14PresentTailCount"

STATE_OLD = """uint64_t g_m14PresentOver33Ms = 0u;
uint64_t g_m14PresentCallTotalUs = 0u;
"""
STATE_NEW = """uint64_t g_m14PresentOver33Ms = 0u;
uint64_t g_m14PresentTailCount = 0u;
uint64_t g_m14PresentCallTotalUs = 0u;
"""

TAIL_OLD = """    if (intervalUs > 18000u) ++g_m14PresentOver18Ms;
    if (intervalUs > 25000u) ++g_m14PresentOver25Ms;
    if (intervalUs > 33000u) ++g_m14PresentOver33Ms;
    uint64_t bin = intervalUs / kM14PresentPacingBinUs;
"""
TAIL_NEW = """    if (intervalUs > 18000u) ++g_m14PresentOver18Ms;
    if (intervalUs > 25000u) {
      ++g_m14PresentOver25Ms;
      const uint64_t tail = ++g_m14PresentTailCount;
      Log.info("M14 host present tail n={} present={} interval_us={} call_us={}",
               tail, g_m14PresentCount, intervalUs, callUs);
    }
    if (intervalUs > 33000u) ++g_m14PresentOver33Ms;
    uint64_t bin = intervalUs / kM14PresentPacingBinUs;
"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once_or_present(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exact old/new anchor once")
    return text.replace(old, new, 1), True


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")
    if "kM14PresentPacingBinUs" not in text:
        raise RuntimeError("existing M14 present pacing probe is not applied")

    original = text
    text, _ = replace_once_or_present(text, STATE_OLD, STATE_NEW, "tail state")
    text, _ = replace_once_or_present(text, TAIL_OLD, TAIL_NEW, "tail recorder")

    required = (
        SIGNATURE,
        "M14 host present tail n={}",
        "interval_us={}",
        "call_us={}",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError("present-tail transform incomplete: " + ", ".join(missing))

    changed = text != original
    if not check_only and changed:
        path.write_text(text, encoding="utf-8", newline="\n")
    if check_only:
        return "verified"
    return "applied" if changed else "already-applied"


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
        print(f"error: Aurora present-tail adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "verified": "Aurora present-tail diagnostics verified",
        "already-applied": "Aurora present-tail diagnostics already applied",
        "applied": "Aurora present-tail diagnostics applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
