#!/usr/bin/env python3
"""Patch a staged DolRecomp CPU runtime with OpenSPM's narrow WPAR store hook.

The player-local M14 module already gathers Broadway's 0xCC008000..0xCC00801F
write-gather stores into exact 32-byte GX FIFO chunks.  DolRecomp's generic C
memory helpers still run every one of those stores through resolve_addr() and an
indirect CPUState::external_write callback before that gather code sees them.
Heavy SPM scenes issue millions of such stores per second.

This transform changes only the private staged copy used to build the player's
local native module.  It inserts one exact WPAR-window hook before generic
address resolution in mem_write8/16/32/64.  The hook itself lives in the M14
bridge and preserves the existing 32-byte gather/flush/order/error semantics.
All non-WPAR stores and runtimes without the OpenSPM bulk GX service fall through
to DolRecomp's original path unchanged.

The transform is exact, idempotent, and fails closed if the selected runtime's
write-helper source shape is not the expected DolRecomp C runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

TARGET = Path("cpu") / "cpu.c"
SIGNATURE = "OPENSPM_M14_DIRECT_WPAR_STORE_HOOK"

DECL_ANCHOR = "PPCMemWriteJournal g_mem_write_journal = NULL;\n"
DECL_INSERT = """/* OPENSPM_M14_DIRECT_WPAR_STORE_HOOK\n * Player-local OpenSPM bridge hook. Returns nonzero only when the exact Wii\n * Broadway write-gather window consumed the store; every other address keeps\n * DolRecomp's stock memory path. */\nextern int openspm_m14_try_wg_write(CPUState* cpu, u32 addr, u64 value, u8 size);\n\nPPCMemWriteJournal g_mem_write_journal = NULL;\n"""

HELPERS = (
    ("void mem_write64(CPUState* cpu, u32 addr, u64 value) {\n",
     "void mem_write64(CPUState* cpu, u32 addr, u64 value) {\n"
     "    if ((addr & 0xFFFFFFE0u) == 0xCC008000u &&\n"
     "        openspm_m14_try_wg_write(cpu, addr, value, 8))\n"
     "        return;\n"),
    ("void mem_write32(CPUState* cpu, u32 addr, u32 value) {\n",
     "void mem_write32(CPUState* cpu, u32 addr, u32 value) {\n"
     "    if ((addr & 0xFFFFFFE0u) == 0xCC008000u &&\n"
     "        openspm_m14_try_wg_write(cpu, addr, value, 4))\n"
     "        return;\n"),
    ("void mem_write16(CPUState* cpu, u32 addr, u16 value) {\n",
     "void mem_write16(CPUState* cpu, u32 addr, u16 value) {\n"
     "    if ((addr & 0xFFFFFFE0u) == 0xCC008000u &&\n"
     "        openspm_m14_try_wg_write(cpu, addr, value, 2))\n"
     "        return;\n"),
    ("void mem_write8(CPUState* cpu, u32 addr, u8 value) {\n",
     "void mem_write8(CPUState* cpu, u32 addr, u8 value) {\n"
     "    if ((addr & 0xFFFFFFE0u) == 0xCC008000u &&\n"
     "        openspm_m14_try_wg_write(cpu, addr, value, 1))\n"
     "        return;\n"),
)


def transform(runtime_root: Path, check_only: bool = False) -> str:
    path = runtime_root / TARGET
    text = path.read_text(encoding="utf-8")

    if SIGNATURE in text:
        required = [
            "extern int openspm_m14_try_wg_write",
            "openspm_m14_try_wg_write(cpu, addr, value, 8)",
            "openspm_m14_try_wg_write(cpu, addr, value, 4)",
            "openspm_m14_try_wg_write(cpu, addr, value, 2)",
            "openspm_m14_try_wg_write(cpu, addr, value, 1)",
        ]
        missing = [item for item in required if item not in text]
        if missing:
            raise RuntimeError("incomplete staged WPAR fastpath: " + ", ".join(missing))
        return "already-applied"

    if text.count(DECL_ANCHOR) != 1:
        raise RuntimeError(
            f"{TARGET}: expected journal declaration anchor exactly once, found {text.count(DECL_ANCHOR)}"
        )
    for old, _new in HELPERS:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(
                f"{TARGET}: expected write-helper anchor {old.split('(')[0].strip()} exactly once, found {count}"
            )

    text = text.replace(DECL_ANCHOR, DECL_INSERT, 1)
    for old, new in HELPERS:
        text = text.replace(old, new, 1)

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.runtime_root.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: staged DolRecomp WPAR fastpath failed: {exc}", file=sys.stderr)
        return 2
    print({
        "applied": "staged DolRecomp CPU direct WPAR store fastpath applied",
        "verified": "staged DolRecomp CPU direct WPAR store fastpath verified",
        "already-applied": "staged DolRecomp CPU direct WPAR store fastpath already applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
