#!/usr/bin/env python3
"""Avoid pipeline invalidation only for an exactly unchanged raw CP mapping.

The existing cp_arraybase duplicate guard preserves staged bytes, but handle_cp
sets DirtyPipeline before invoking it. Each duplicate therefore still forces
populate_pipeline_config, build_shader_info, pipeline lookup and uniform setup.
Move that same conservative dirty operation into cp_arraybase and undo only
this invocation's bit on the existing proven duplicate return. Nonduplicates,
mapping failures and source-port calls retain the old dirty policy. Never clear
a dirty bit that was present before this command. This does not restore the
earlier broad base-write/visibility-epoch experiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SIGNATURE = "M14 exact duplicate CP array-base pipeline reuse active"
TABLE_OLD = "    regs[0xA0 + i] = {cp_arraybase, DirtyPipeline, /* alwaysHandle */ true};"
TABLE_NEW = "    regs[0xA0 + i] = {cp_arraybase, 0, /* alwaysHandle */ true};"
ENTRY_OLD = "void cp_arraybase(u8 addr, u32 value) noexcept {\n"
ENTRY_NEW = ENTRY_OLD + """  // Preserve the historical dirty policy on every path except the exact
  // duplicate guard below. In particular, retain all pre-existing dirty bits.
  const auto dirtyBeforeBase = g_gxState.dirty;
  g_gxState.dirty |= DirtyPipeline;
"""
DUPLICATE_OLD = """      Log.info("M14 raw CP array-base duplicate write preserved staged cache");
      m14DuplicateBaseReported = true;
    }
    return;
"""
DUPLICATE_NEW = """      Log.info("M14 raw CP array-base duplicate write preserved staged cache");
      m14DuplicateBaseReported = true;
    }
    g_gxState.dirty = dirtyBeforeBase;
    static u64 m14DuplicatePipelineSkips = 0;
    const u64 skipped = ++m14DuplicatePipelineSkips;
    if ((skipped & (skipped - 1u)) == 0u)
      Log.info("M14 exact duplicate CP array-base pipeline reuse active skipped={}", skipped);
    return;
"""


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"CP duplicate pipeline source drift: expected one {old!r}")
    return text.replace(old, new, 1)


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/regs.cpp"
    text = path.read_text(encoding="utf-8")
    duplicate_guard = (
        "if (array.data == span.data && array.size == span.size && "
        "!array.le && array.guestMapped) {"
    )
    if text.count(duplicate_guard) != 1:
        raise RuntimeError("existing exact raw CP mapping guard is missing or ambiguous")
    if SIGNATURE in text:
        if any(text.count(part) != 1 for part in (TABLE_NEW, ENTRY_NEW, DUPLICATE_NEW)):
            raise RuntimeError("partial CP duplicate pipeline adaptation")
        if TABLE_OLD in text:
            raise RuntimeError("CP dispatch still dirties duplicates before decoding")
        return "already-applied"
    text = once(text, TABLE_OLD, TABLE_NEW)
    text = once(text, ENTRY_OLD, ENTRY_NEW)
    text = once(text, DUPLICATE_OLD, DUPLICATE_NEW)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        source = args.source.resolve()
        expected = json.loads((HERE / "aurora.lock.json").read_text())["commit"]
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if actual != expected:
            raise RuntimeError(f"Aurora revision mismatch: expected {expected}, got {actual}")
        print("Aurora exact duplicate CP pipeline adaptation: " + transform(source, args.check_only))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM CP duplicate pipeline adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
