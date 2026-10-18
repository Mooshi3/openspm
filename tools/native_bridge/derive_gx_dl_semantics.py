#!/usr/bin/env python3
"""Derive the player-local GX display-list CPU-shadow seam.

A PC port must replace Revolution's physical CPU-FIFO/write-gather transport in
GXBeginDisplayList/GXEndDisplayList, but it must not delete the CPU-visible GX
SDK semantics around that transport.  RVL GX flushes pending dirty state before
Begin, optionally snapshots/restores the 0x600-byte GXData shadow, preserves
cpCtrlReg across the restore, and maintains dlistActive.

This scanner proves those same structural fields in the player's own R8PE01 DOL
before emitting any address used by OpenSPM.  It derives the storage slot from
which GXBeginDisplayList loads __GXData and the sole linked helper call between
the gxDirtyFlags test and dlistSave test (the player's __GXSetDirtyState).  It
also requires GXBegin/GXEnd to contain the known 0x5F8/0x5F9/0x5FC/0x008 field
accesses and 0x600-byte copy size.  If the retail shape differs, generation fails
closed instead of importing an address from another SDK revision.

Only derived addresses/offset metadata are written to the private generated
header. No Nintendo code bytes are persisted in source control.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct

GX_BEGIN = 0x80291DC8
GX_END = 0x80291E78
GX_SCAN_LIMIT = 0x180
PPC_BLR = 0x4E800020

EXPR_ABSOLUTE = 0
EXPR_R13 = 1
EXPR_R2 = 2
EXPR_UNAVAILABLE = 0xFFFFFFFF

GXDATA_SIZE = 0x600
GXDATA_CP_CTRL = 0x008
GXDATA_DLIST_ACTIVE = 0x5F8
GXDATA_DLIST_SAVE = 0x5F9
GXDATA_DIRTY = 0x5FC


@dataclass(frozen=True)
class TextSection:
    file_offset: int
    address: int
    size: int


def be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def sections(data: bytes) -> list[TextSection]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    result: list[TextSection] = []
    for index in range(7):
        off = be32(data, index * 4)
        address = be32(data, 0x48 + index * 4)
        size = be32(data, 0x90 + index * 4)
        if size == 0:
            continue
        if (address & 3) or (size & 3):
            raise RuntimeError(f"text section {index} is not instruction aligned")
        if off > len(data) or size > len(data) - off:
            raise RuntimeError(f"text section {index} is outside DOL")
        result.append(TextSection(off, address, size))
    if not result:
        raise RuntimeError("DOL contains no text sections")
    return result


def in_text(sections_: list[TextSection], address: int) -> bool:
    return any(s.address <= address < s.address + s.size for s in sections_)


def instruction(data: bytes, sections_: list[TextSection], address: int) -> int:
    for section in sections_:
        if section.address <= address < section.address + section.size:
            return be32(data, section.file_offset + address - section.address)
    raise RuntimeError(f"instruction 0x{address:08X} is outside DOL text")


def branch_target(raw: int, pc: int) -> int | None:
    if raw >> 26 != 18 or (raw & 1) == 0:
        return None
    disp = raw & 0x03FFFFFC
    if disp & 0x02000000:
        disp -= 0x04000000
    if raw & 2:
        return disp & 0xFFFFFFFF
    return (pc + disp) & 0xFFFFFFFF


def expr_add(expr: tuple[int, int] | None, delta: int) -> tuple[int, int] | None:
    if expr is None:
        return None
    return expr[0], (expr[1] + delta) & 0xFFFFFFFF


def base_expr(regs: list[tuple[int, int] | None], ra: int) -> tuple[int, int] | None:
    return (EXPR_ABSOLUTE, 0) if ra == 0 else regs[ra]


@dataclass
class FunctionFacts:
    end: int
    calls: list[tuple[int, int]]
    dirty_pc: int | None = None
    dirty_base: int | None = None
    dirty_slot: tuple[int, int] | None = None
    save_pc: int | None = None
    active_pc: int | None = None
    size_600_pcs: list[int] | None = None
    cp_ctrl_pcs: list[int] | None = None
    same_slot_accesses: list[tuple[int, int, tuple[int, int] | None]] | None = None


def scan_function(data: bytes, sections_: list[TextSection], start: int) -> FunctionFacts:
    regs: list[tuple[int, int] | None] = [None] * 32
    pointer_slot: list[tuple[int, int] | None] = [None] * 32
    regs[13] = (EXPR_R13, 0)
    regs[2] = (EXPR_R2, 0)
    calls: list[tuple[int, int]] = []
    size_600: list[int] = []
    cp_ctrl: list[int] = []
    special: list[tuple[int, int, tuple[int, int] | None]] = []
    dirty_pc = None
    dirty_base = None
    dirty_slot = None
    save_pc = None
    active_pc = None
    end = None

    for pc in range(start, start + GX_SCAN_LIMIT, 4):
        raw = instruction(data, sections_, pc)
        if raw == PPC_BLR:
            end = pc
            break
        opcode = raw >> 26
        rd_rs = (raw >> 21) & 31
        ra = (raw >> 16) & 31
        rb = (raw >> 11) & 31
        imm = raw & 0xFFFF
        simm = s16(imm)
        target = branch_target(raw, pc)

        if opcode == 14:  # addi / li
            regs[rd_rs] = expr_add(base_expr(regs, ra), simm)
            pointer_slot[rd_rs] = None
            if ra == 0 and rd_rs == 5 and simm == GXDATA_SIZE:
                size_600.append(pc)
        elif opcode == 15:  # addis / lis
            regs[rd_rs] = expr_add(base_expr(regs, ra), simm << 16)
            pointer_slot[rd_rs] = None
        elif opcode == 24:  # ori rA,rS,UIMM
            src = regs[rd_rs]
            regs[ra] = ((EXPR_ABSOLUTE, src[1] | imm)
                        if src is not None and src[0] == EXPR_ABSOLUTE
                        else src if imm == 0 else None)
            pointer_slot[ra] = pointer_slot[rd_rs] if imm == 0 else None
        elif opcode == 25:  # oris rA,rS,UIMM
            src = regs[rd_rs]
            regs[ra] = ((EXPR_ABSOLUTE, src[1] | (imm << 16))
                        if src is not None and src[0] == EXPR_ABSOLUTE
                        else src if imm == 0 else None)
            pointer_slot[ra] = pointer_slot[rd_rs] if imm == 0 else None
        elif opcode == 32:  # lwz
            effective = expr_add(base_expr(regs, ra), simm)
            if simm == GXDATA_DIRTY:
                if dirty_pc is not None:
                    raise RuntimeError(
                        f"GX function 0x{start:08X} has multiple gxDirtyFlags loads"
                    )
                dirty_pc = pc
                dirty_base = ra
                dirty_slot = pointer_slot[ra]
                special.append((pc, GXDATA_DIRTY, pointer_slot[ra]))
            elif simm == GXDATA_CP_CTRL:
                cp_ctrl.append(pc)
                special.append((pc, GXDATA_CP_CTRL, pointer_slot[ra]))
            regs[rd_rs] = None
            pointer_slot[rd_rs] = effective
        elif opcode == 34:  # lbz
            if simm == GXDATA_DLIST_SAVE:
                if save_pc is None:
                    save_pc = pc
                special.append((pc, GXDATA_DLIST_SAVE, pointer_slot[ra]))
            elif simm == GXDATA_DLIST_ACTIVE:
                special.append((pc, GXDATA_DLIST_ACTIVE, pointer_slot[ra]))
            regs[rd_rs] = None
            pointer_slot[rd_rs] = None
        elif opcode == 36:  # stw
            if simm == GXDATA_CP_CTRL:
                cp_ctrl.append(pc)
                special.append((pc, GXDATA_CP_CTRL, pointer_slot[ra]))
        elif opcode == 38:  # stb
            if simm == GXDATA_DLIST_ACTIVE:
                if active_pc is None:
                    active_pc = pc
                special.append((pc, GXDATA_DLIST_ACTIVE, pointer_slot[ra]))
        elif opcode == 31:
            xo = (raw >> 1) & 0x3FF
            if xo == 444 and rd_rs == rb:  # mr = or rA,rS,rS
                regs[ra] = regs[rd_rs]
                pointer_slot[ra] = pointer_slot[rd_rs]
        elif opcode in (7, 8, 10, 11, 12, 13, 33, 35, 40, 41, 42, 43, 44, 45, 46, 47):
            regs[rd_rs] = None
            pointer_slot[rd_rs] = None

        if target is not None:
            calls.append((pc, target))
            # EABI volatile registers do not survive a call. GXData is normally
            # held in a nonvolatile register or reloaded from its global slot.
            regs[0] = None
            pointer_slot[0] = None
            for reg in range(3, 13):
                regs[reg] = None
                pointer_slot[reg] = None

    if end is None:
        raise RuntimeError(
            f"GX function 0x{start:08X} has no BLR within 0x{GX_SCAN_LIMIT:X} bytes"
        )
    return FunctionFacts(
        end=end,
        calls=calls,
        dirty_pc=dirty_pc,
        dirty_base=dirty_base,
        dirty_slot=dirty_slot,
        save_pc=save_pc,
        active_pc=active_pc,
        size_600_pcs=size_600,
        cp_ctrl_pcs=cp_ctrl,
        same_slot_accesses=special,
    )


def derive(dol: Path) -> tuple[int, int, int]:
    data = dol.read_bytes()
    sections_ = sections(data)
    if not in_text(sections_, GX_BEGIN) or not in_text(sections_, GX_END):
        raise RuntimeError("player GXBegin/GXEnd addresses are outside executable DOL text")

    begin = scan_function(data, sections_, GX_BEGIN)
    end = scan_function(data, sections_, GX_END)
    if begin.dirty_pc is None or begin.dirty_slot is None:
        raise RuntimeError("GXBeginDisplayList did not expose a derivable gxDirtyFlags/__GXData slot")
    mode, value = begin.dirty_slot
    if mode not in (EXPR_ABSOLUTE, EXPR_R13, EXPR_R2):
        raise RuntimeError("GXBeginDisplayList __GXData slot expression is unsupported")
    if begin.save_pc is None or begin.active_pc is None:
        raise RuntimeError("GXBeginDisplayList is missing dlistSave/dlistActive accesses")
    if not begin.size_600_pcs:
        raise RuntimeError("GXBeginDisplayList did not materialize the 0x600-byte GXData copy size")

    begin_calls_before_save = [
        (pc, target) for pc, target in begin.calls
        if begin.dirty_pc < pc < begin.save_pc
    ]
    if len(begin_calls_before_save) != 1:
        details = ", ".join(
            f"0x{pc:08X}->0x{target:08X}" for pc, target in begin_calls_before_save
        ) or "none"
        raise RuntimeError(
            "GXBeginDisplayList dirty-state helper shape changed: expected exactly one "
            f"linked call between gxDirtyFlags and dlistSave, found {details}"
        )
    dirty_helper = begin_calls_before_save[0][1]
    if not in_text(sections_, dirty_helper):
        raise RuntimeError(
            f"derived GX dirty-state helper 0x{dirty_helper:08X} is not executable"
        )

    if end.save_pc is None or end.active_pc is None:
        raise RuntimeError("GXEndDisplayList is missing dlistSave/dlistActive accesses")
    if not end.size_600_pcs:
        raise RuntimeError("GXEndDisplayList did not materialize the 0x600-byte GXData copy size")
    if len(end.cp_ctrl_pcs or []) < 2:
        raise RuntimeError("GXEndDisplayList did not expose cpCtrlReg preserve/restore accesses")

    # Every recognized GXData access whose base provenance survived analysis
    # must point through the same player global slot. This catches accidental
    # matches on unrelated structures while allowing post-call reloads.
    proven = [slot for _pc, _off, slot in (begin.same_slot_accesses or []) +
              (end.same_slot_accesses or []) if slot is not None]
    if not proven or any(slot != (mode, value) for slot in proven):
        details = ", ".join(f"{slot[0]}:0x{slot[1]:08X}" for slot in proven) or "none"
        raise RuntimeError(
            "GX display-list shadow accesses do not resolve to one __GXData slot: "
            + details
        )

    return mode, value, dirty_helper


def write_header(path: Path, mode: int, value: int, dirty_helper: int) -> None:
    text = (
        "#ifndef OPENSPM_LOCAL_GX_DL_SEMANTICS_H\n"
        "#define OPENSPM_LOCAL_GX_DL_SEMANTICS_H\n\n"
        "#include <stdint.h>\n\n"
        "/* Derived from the verified player's retail GXBegin/GXEnd bodies. */\n"
        f"#define OPENSPM_LOCAL_GXDL_EXPR_ABSOLUTE UINT32_C({EXPR_ABSOLUTE})\n"
        f"#define OPENSPM_LOCAL_GXDL_EXPR_R13 UINT32_C({EXPR_R13})\n"
        f"#define OPENSPM_LOCAL_GXDL_EXPR_R2 UINT32_C({EXPR_R2})\n"
        f"#define OPENSPM_LOCAL_GXDL_DATA_SLOT_MODE UINT32_C({mode})\n"
        f"#define OPENSPM_LOCAL_GXDL_DATA_SLOT_VALUE UINT32_C(0x{value:08X})\n"
        f"#define OPENSPM_LOCAL_GXDL_DIRTY_HELPER UINT32_C(0x{dirty_helper:08X})\n"
        f"#define OPENSPM_LOCAL_GXDL_DATA_SIZE UINT32_C(0x{GXDATA_SIZE:X})\n"
        f"#define OPENSPM_LOCAL_GXDL_CP_CTRL_OFF UINT32_C(0x{GXDATA_CP_CTRL:X})\n"
        f"#define OPENSPM_LOCAL_GXDL_ACTIVE_OFF UINT32_C(0x{GXDATA_DLIST_ACTIVE:X})\n"
        f"#define OPENSPM_LOCAL_GXDL_SAVE_OFF UINT32_C(0x{GXDATA_DLIST_SAVE:X})\n"
        f"#define OPENSPM_LOCAL_GXDL_DIRTY_OFF UINT32_C(0x{GXDATA_DIRTY:X})\n\n"
        "#endif\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dol", required=True, type=Path)
    parser.add_argument("--header", required=True, type=Path)
    args = parser.parse_args()
    try:
        mode, value, helper = derive(args.dol.resolve())
        write_header(args.header.resolve(), mode, value, helper)
        print(
            "M14 LLVM GX display-list shadow semantics derived: "
            f"data_slot={mode}:0x{value:08X} dirty_helper=0x{helper:08X} "
            f"gxdata_size=0x{GXDATA_SIZE:X}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: GX display-list semantic derivation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
