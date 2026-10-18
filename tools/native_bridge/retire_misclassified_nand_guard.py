#!/usr/bin/env python3
"""Remove the retired M14 host guard from R8PE01 nandWriteBannerLoadAllSaves.

Older M14 builds misclassified US2 0x8023CEF0 as a fresh-PC platform bootstrap.
The generated-label guard therefore injected a host_call as the first statement
of that guest function. Runtime proof through Chapter 1 showed this suppressed
SPM's real boot-time load-all task and caused every launch to show four New
Files.

The function is ordinary game logic and must execute natively. This migration is
intentionally surgical: it removes only the exact OpenSPM-injected guard line
immediately following label_8023CEF0. Any other source shape fails closed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

TARGET = 0x8023CEF0
LABEL = f"label_{TARGET:08X}:"
GUARD = f"if (ctx->host_call != 0 && ctx->host_call(ctx, 0x{TARGET:08X}u)) return;"


def retire_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = 0
    found_label = 0
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        if line.strip() == LABEL:
            found_label += 1
            if index + 1 < len(lines) and lines[index + 1].strip() == GUARD:
                index += 1
                changed += 1
        index += 1
    if found_label > 1:
        raise RuntimeError(f"retired NAND label appears {found_label} times in {path}")
    if changed:
        path.write_text("".join(output), encoding="utf-8", newline="\n")
    return changed


def retire_tree(generated: Path) -> dict[str, int]:
    root = generated.resolve()
    chunks = root / "chunks"
    if not (root / "generated.h").is_file() or not chunks.is_dir():
        raise RuntimeError(f"not a generated source tree: {root}")

    labels = 0
    removed = 0
    for path in sorted(chunks.glob("chunk_*.c")):
        text = path.read_text(encoding="utf-8")
        if LABEL in text:
            labels += text.count(LABEL)
        removed += retire_file(path)

    if labels != 1:
        raise RuntimeError(
            f"expected exactly one retired NAND label 0x{TARGET:08X}, found {labels}"
        )
    return {"labels": labels, "removed_guards": removed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated", required=True, type=Path)
    args = parser.parse_args()
    stats = retire_tree(args.generated)
    print("retired-nand-guard " + " ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
