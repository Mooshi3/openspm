#!/usr/bin/env python3
"""Instrument R8PE01 revision-2's real top-level frame loop in local generated C.

SPM's main loop is:

    DEMOBeforeRender();
    spmarioDisp();
    spmarioMain();
    DEMODoneRender();

The existing M14 fps/input diagnostics are VI/retrace-adjacent proxies and can
remain 60 while the real game loop falls behind.  Keep the durable spmarioMain
entry counter, and also mark the four real loop-entry boundaries so the bridge
can measure wall time spent between them even when a phase yields/resumes across
multiple native slices.

This operates only on player-local generated source under .openspm.  No Nintendo
source or generated game code is committed to the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# R8PE01 / NTSC-U revision 2 symbols from Seeky's public symbol map.
TARGETS = {
    "801A8A9C": ("DEMOBeforeRender", 0),
    "801A7D1C": ("spmarioDisp", 1),
    "801A7AAC": ("spmarioMain", 2),
    "801A8B64": ("DEMODoneRender", 3),
}
MAIN_TARGET = "801A7AAC"
MAIN_HOOK = "openspm_m14_note_spmario_main_v20"
PHASE_HOOK = "openspm_m14_note_frame_phase_v20"
MAIN_SIGNATURE = f"{MAIN_HOOK}(ctx);"


def _phase_signature(phase: int) -> str:
    return f"{PHASE_HOOK}(ctx, {phase}u);"


def transform(root: Path, check_only: bool = False) -> tuple[str, list[str]]:
    chunks = sorted((root / "chunks").glob("chunk_*.c"))
    if not chunks:
        raise RuntimeError(f"no generated chunks under {root}")

    texts = {path: path.read_text(encoding="utf-8") for path in chunks}
    locations: dict[str, Path] = {}
    for target in TARGETS:
        label = f"label_{target}:\n"
        matches = [path for path, text in texts.items() if label in text]
        if len(matches) != 1:
            raise RuntimeError(
                f"R8PE01 rev2 loop label {target}: expected once, found {len(matches)}"
            )
        locations[target] = matches[0]

    joined = "\n".join(texts.values())
    if joined.count(MAIN_SIGNATURE) > 1:
        raise RuntimeError("spmarioMain cadence hook appears more than once")
    for _, phase in TARGETS.values():
        if joined.count(_phase_signature(phase)) > 1:
            raise RuntimeError(f"frame phase {phase} hook appears more than once")

    changed: set[Path] = set()
    for target, (name, phase) in TARGETS.items():
        path = locations[target]
        text = texts[path]
        label = f"label_{target}:\n"
        additions: list[str] = []

        phase_sig = _phase_signature(phase)
        if phase_sig not in text:
            additions.extend(
                [
                    "    {\n",
                    f"        void {PHASE_HOOK}(CPUState* ctx, u32 phase);\n",
                    f"        {phase_sig}\n",
                    "    }\n",
                ]
            )

        if target == MAIN_TARGET and MAIN_SIGNATURE not in text:
            additions.extend(
                [
                    "    {\n",
                    f"        void {MAIN_HOOK}(CPUState* ctx);\n",
                    f"        {MAIN_SIGNATURE}\n",
                    "    }\n",
                ]
            )

        if additions:
            replacement = label + "".join(additions)
            text = text.replace(label, replacement, 1)
            texts[path] = text
            changed.add(path)

    final_joined = "\n".join(texts.values())
    if final_joined.count(MAIN_SIGNATURE) != 1:
        raise RuntimeError("failed to create exactly one spmarioMain cadence hook")
    for _, phase in TARGETS.values():
        if final_joined.count(_phase_signature(phase)) != 1:
            raise RuntimeError(f"failed to create exactly one frame phase {phase} hook")

    if not check_only:
        for path in changed:
            path.write_text(texts[path], encoding="utf-8", newline="\n")

    state = "verified" if check_only else ("active" if changed else "already-active")
    chunks_used = sorted({path.name for path in locations.values()})
    return state, chunks_used


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        state, chunks = transform(args.generated.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"OpenSPM main-loop cadence adaptation failed: {exc}", file=sys.stderr)
        return 1
    print(
        "M14 real main-loop phase hooks "
        f"{state}: before=0x801A8A9C disp=0x801A7D1C "
        f"main=0x801A7AAC done=0x801A8B64 chunks={','.join(chunks)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
