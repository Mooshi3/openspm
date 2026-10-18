#!/usr/bin/env python3
"""Count static DolRecomp memory-helper call sites in player-local generated C.

This is configure-time diagnostics only. It never reads game data beyond generated
C source text, emits counts only, and does not modify the generated source or the
runtime. The purpose is to size the cross-TU CPU-memory boundary before/after the
MSVC IPO experiment without adding clocks/counters to the hot path.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import sys

PATTERNS = {
    "read8": re.compile(r"\bmem_read8\s*\("),
    "read16": re.compile(r"\bmem_read16\s*\("),
    "read32": re.compile(r"\bmem_read32\s*\("),
    "read64": re.compile(r"\bmem_read64\s*\("),
    "write8": re.compile(r"\bmem_write8\s*\("),
    "write16": re.compile(r"\bmem_write16\s*\("),
    "write32": re.compile(r"\bmem_write32\s*\("),
    "write64": re.compile(r"\bmem_write64\s*\("),
    "psq_load": re.compile(r"\bppc_psq_load_inline\s*\("),
    "psq_store": re.compile(r"\bppc_psq_store_inline\s*\("),
}


def count_calls(generated_dir: Path) -> tuple[int, Counter[str]]:
    chunks_dir = generated_dir / "chunks"
    chunks = sorted(chunks_dir.glob("*.c"))
    if not chunks:
        raise RuntimeError(f"no generated C chunks found below {chunks_dir}")

    counts: Counter[str] = Counter()
    for path in chunks:
        text = path.read_text(encoding="utf-8", errors="strict")
        for name, pattern in PATTERNS.items():
            counts[name] += len(pattern.findall(text))
    return len(chunks), counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated_dir", type=Path)
    args = parser.parse_args()

    try:
        chunks, counts = count_calls(args.generated_dir.resolve())
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f"error: generated memory-helper census failed: {exc}", file=sys.stderr)
        return 2

    scalar_reads = sum(counts[name] for name in ("read8", "read16", "read32", "read64"))
    scalar_writes = sum(counts[name] for name in ("write8", "write16", "write32", "write64"))
    total = scalar_reads + scalar_writes + counts["psq_load"] + counts["psq_store"]

    print(
        "M14 generated CPU memory callsites: "
        f"chunks={chunks} total={total} "
        f"reads={scalar_reads} writes={scalar_writes} "
        f"r8={counts['read8']} r16={counts['read16']} "
        f"r32={counts['read32']} r64={counts['read64']} "
        f"w8={counts['write8']} w16={counts['write16']} "
        f"w32={counts['write32']} w64={counts['write64']} "
        f"psq_load={counts['psq_load']} psq_store={counts['psq_store']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
