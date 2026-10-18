#!/usr/bin/env python3
"""Derive player-local LLVM seams from a verified R8PE01 DOL.

Generated-C v19 injects hooks around GXBeginDisplayList/GXEndDisplayList. LLVM
objects instead need explicit partition PCs so those hooks can be materialized by
the OpenSPM bridge. The same local map carries diagnostic frame entries.

M14's relocatable-relF work also needs architecture seams which cannot be
allowed to disappear into LLVM direct-call lowering: relMain's unique retail
`__memAlloc(HEAP_MAIN, decoded_relF_size)` call, the later BSS allocation call,
and both PC+4 continuations. The scanner verifies the exact retail relF call and
derives the single later __memAlloc in the verified relMain body.

The same rule applies to every semantic host service. Generated-C already turns
direct BLs to hosted targets back into side exits. LLVM must not silently lower
a NAND/DVD/input/audio/platform call into a host-native call to Nintendo's Wii
body. For the real player game tree this scanner therefore derives every direct
BL to the checked-in host-target manifest from both main.dol and relocated relF,
and adds the call/return PCs to the compiler partition map and generated seam
header. Only addresses/names leave the player-owned files; no game bytes are
written to source control.

LLVM also needs explicit partition points for host-driven *mid-function* guest
re-entry. M14's fixed-rate retrace bridge deliberately parks the persistent CPU
at Revolution OS SelectThread's no-runnable-thread idle continuation, runs the VI
interrupt handler out-of-band, then resumes exactly at that continuation. The C
backend can dispatch arbitrary continuation labels directly; an LLVM native
region must be split at the same PC so scheduler RAM changed by the interrupt is
observed from a fresh native entry rather than re-entering an interior block of a
larger SelectThread region.

The same non-local rule applies when Nintendo switches threads. SelectThread
calls OSSaveContext(current_thread); a later OSLoadContext/rfi restores that
thread at the instruction immediately after the linked OSSaveContext call. That
PC is not a normal function entry, so LLVM must materialize it explicitly just
like the idle-loop re-entry. The scanner derives the call/PC+4 pair from the
verified R8PE01 revision-2 DOL and fails closed if the SDK shape changes.

The post-frame LLVM stall is traced at VIWaitForRetrace without perturbing guest
control flow. The scanner requires the player's own function to bracket exactly
one middle linked call between the proven OSDisableInterrupts and
OSRestoreInterrupts calls; that middle call is therefore the player-proven
OSSleepThread. A tiny constant-expression pass derives the queue passed in r3 at
that call and, when uniquely visible, the retraceCount word loaded on both sides
of the sleep. Expressions may be absolute, r13-relative, or r2-relative. The
bridge can then observe Nintendo's actual queue/count around host retraces with
no scheduler stubs, fake exceptions, or diagnostic side exits.
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys
from dataclasses import dataclass

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import derive_hostcall_llvm_seams as hostcall_seams  # noqa: E402

GX_BEGIN = 0x80291DC8
GX_END = 0x80291E78
EXPECTED_CALLS = 3
RELF_ALLOC_CALL = 0x8023C978
RELF_ALLOC_RETURN = 0x8023C97C
RELF_ALLOC_TARGET = 0x801A59A8
RELF_ALLOC_RAW = 0x4BF69031
RELF_MAIN_END = 0x8023CA1C
FRAME_ENTRIES = {
    0x801A8A9C: "openspm_phase_DEMOBeforeRender",
    0x801A7D1C: "openspm_phase_spmarioDisp",
    0x801A7AAC: "openspm_phase_spmarioMain",
    0x801A8B64: "openspm_phase_DEMODoneRender",
}
LLVM_REENTRY_POINTS = {
    0x8027551C: "openspm_scheduler_idle_reentry",
}
SELECT_THREAD_START = 0x802753D0
SELECT_THREAD_END = 0x802755F8
OS_SET_CURRENT_CONTEXT = 0x8026E57C
OS_SAVE_CONTEXT = 0x8026E5E4
OS_LOAD_CONTEXT = 0x8026E664

VI_WAIT_FOR_RETRACE = 0x802816F8
VI_WAIT_SCAN_END = VI_WAIT_FOR_RETRACE + 0x100
OS_DISABLE_INTERRUPTS = 0x80271E28
OS_RESTORE_INTERRUPTS = 0x80271E50
PPC_BLR = 0x4E800020

# Address-expression modes emitted to the player-local header.
VI_EXPR_ABSOLUTE = 0
VI_EXPR_R13 = 1
VI_EXPR_R2 = 2
VI_EXPR_UNAVAILABLE = 0xFFFFFFFF

_last_relf_bss_alloc: tuple[int, int] | None = None
_last_hostcall_seams: list[hostcall_seams.HostCallSeam] = []
_last_scheduler_context_seam: tuple[int, int] | None = None
# (VIWait call PC, derived OSSleepThread target, call return PC)
_last_vi_sleep_seam: tuple[int, int, int] | None = None
# (queue_mode, queue_value, retrace_count_mode, retrace_count_value)
_last_vi_wait_state: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class DolTextSection:
    file_offset: int
    address: int
    size: int


def _read_be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _sections(data: bytes) -> list[DolTextSection]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    result: list[DolTextSection] = []
    for index in range(7):
        file_offset = _read_be32(data, 0x00 + index * 4)
        address = _read_be32(data, 0x48 + index * 4)
        size = _read_be32(data, 0x90 + index * 4)
        if size == 0:
            continue
        if file_offset > len(data) or size > len(data) - file_offset:
            raise RuntimeError(
                f"text section {index} is outside DOL: off=0x{file_offset:X} size=0x{size:X}"
            )
        if (address & 3) != 0 or (size & 3) != 0:
            raise RuntimeError(
                f"text section {index} is not instruction aligned: addr=0x{address:08X} size=0x{size:X}"
            )
        result.append(DolTextSection(file_offset, address, size))
    if not result:
        raise RuntimeError("DOL contains no text sections")
    return result


def _address_in_text(sections: list[DolTextSection], address: int) -> bool:
    return any(section.address <= address < section.address + section.size for section in sections)


def _instruction_at(data: bytes, sections: list[DolTextSection], address: int) -> int:
    for section in sections:
        if section.address <= address < section.address + section.size:
            offset = section.file_offset + (address - section.address)
            return _read_be32(data, offset)
    raise RuntimeError(f"instruction 0x{address:08X} is outside executable DOL text")


def _branch_target(raw: int, pc: int) -> int | None:
    if raw >> 26 != 18 or (raw & 1) == 0:
        return None
    displacement = raw & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    if raw & 2:
        return displacement & 0xFFFFFFFF
    return (pc + displacement) & 0xFFFFFFFF


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _expr_add(expr: tuple[int, int] | None, delta: int) -> tuple[int, int] | None:
    if expr is None:
        return None
    return (expr[0], (expr[1] + delta) & 0xFFFFFFFF)


def _expr_base(regs: list[tuple[int, int] | None], ra: int) -> tuple[int, int] | None:
    if ra == 0:
        return (VI_EXPR_ABSOLUTE, 0)
    return regs[ra]


def _derive_vi_wait_expressions(
    data: bytes,
    sections: list[DolTextSection],
    end_pc: int,
    disable_pc: int,
    sleep_call: int,
    restore_pc: int,
) -> tuple[int, int, int, int]:
    """Constant-propagate the tiny VIWaitForRetrace address setup.

    This is intentionally not a PPC emulator. It recognizes only the integer
    address-building forms used by SDK code and invalidates ABI-volatile values
    after linked calls. The queue expression is required. retraceCount is
    optional: it is accepted only when one effective lwz address appears both
    before and after OSSleepThread inside the interrupt bracket.
    """
    regs: list[tuple[int, int] | None] = [None] * 32
    regs[13] = (VI_EXPR_R13, 0)
    regs[2] = (VI_EXPR_R2, 0)
    queue_expr: tuple[int, int] | None = None
    loads_before: list[tuple[int, int]] = []
    loads_after: list[tuple[int, int]] = []

    pc = VI_WAIT_FOR_RETRACE
    while pc < end_pc:
        raw = _instruction_at(data, sections, pc)
        opcode = raw >> 26
        rd_rs = (raw >> 21) & 31
        ra = (raw >> 16) & 31
        imm = raw & 0xFFFF
        target = _branch_target(raw, pc)

        if pc == sleep_call:
            queue_expr = regs[3]

        if opcode == 14:  # addi
            regs[rd_rs] = _expr_add(_expr_base(regs, ra), _s16(imm))
        elif opcode == 15:  # addis / lis
            regs[rd_rs] = _expr_add(_expr_base(regs, ra), _s16(imm) << 16)
        elif opcode == 24:  # ori rA,rS,UIMM
            src = regs[rd_rs]
            if src is not None and src[0] == VI_EXPR_ABSOLUTE:
                regs[ra] = (VI_EXPR_ABSOLUTE, src[1] | imm)
            elif imm == 0:
                regs[ra] = src
            else:
                regs[ra] = None
        elif opcode == 25:  # oris rA,rS,UIMM
            src = regs[rd_rs]
            if src is not None and src[0] == VI_EXPR_ABSOLUTE:
                regs[ra] = (VI_EXPR_ABSOLUTE, src[1] | (imm << 16))
            elif imm == 0:
                regs[ra] = src
            else:
                regs[ra] = None
        elif opcode == 32:  # lwz
            effective = _expr_add(_expr_base(regs, ra), _s16(imm))
            if effective is not None:
                if disable_pc < pc < sleep_call:
                    loads_before.append(effective)
                elif sleep_call < pc < restore_pc:
                    loads_after.append(effective)
            regs[rd_rs] = None
        elif opcode == 31:
            xo = (raw >> 1) & 0x3FF
            rb = (raw >> 11) & 31
            if xo == 444:  # or; mr is or rA,rS,rS
                regs[ra] = regs[rd_rs] if rd_rs == rb else None
        elif opcode in (7, 8, 10, 11, 12, 13, 33, 34, 35, 40, 41, 42, 43, 44, 45, 46, 47):
            # Common GPR-writing D-form instructions not used as address builders.
            regs[rd_rs] = None

        if target is not None:
            # SysV/EABI volatile GPRs after a linked call. Nonvolatile address
            # bases intentionally survive, matching the real SDK calling ABI.
            regs[0] = None
            for reg in range(3, 13):
                regs[reg] = None
        pc += 4

    if queue_expr is None:
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace queue expression could not be derived at "
            f"sleep call 0x{sleep_call:08X}"
        )

    common = sorted(set(loads_before).intersection(loads_after))
    if len(common) == 1:
        count_mode, count_value = common[0]
    else:
        count_mode, count_value = VI_EXPR_UNAVAILABLE, 0
    return queue_expr[0], queue_expr[1], count_mode, count_value


def _require_relf_bss_seam() -> tuple[int, int]:
    if _last_relf_bss_alloc is None:
        raise RuntimeError("relF BSS allocation seam requested before verified DOL scan")
    return _last_relf_bss_alloc


def _require_scheduler_context_seam() -> tuple[int, int]:
    if _last_scheduler_context_seam is None:
        raise RuntimeError(
            "scheduler context-resume seam requested before verified DOL scan"
        )
    return _last_scheduler_context_seam


def _require_vi_sleep_seam() -> tuple[int, int, int]:
    if _last_vi_sleep_seam is None:
        raise RuntimeError("VI sleep seam requested before verified DOL scan")
    return _last_vi_sleep_seam


def _require_vi_wait_state() -> tuple[int, int, int, int]:
    if _last_vi_wait_state is None:
        raise RuntimeError("VI wait state requested before verified DOL scan")
    return _last_vi_wait_state


def _derive_hostcalls(path: pathlib.Path, data: bytes) -> list[hostcall_seams.HostCallSeam]:
    game_dir = path.parent.parent
    if path.parent.name.lower() == "sys" and (game_dir / "rel").is_dir():
        loaded = hostcall_seams.relf_input.load_from_game(game_dir)
        seams, _counts = hostcall_seams.scan(path, loaded)
        return seams
    hosted = hostcall_seams.direct_calls.load_host_targets()
    return hostcall_seams.scan_ranges(hostcall_seams.dol_text_ranges(data), hosted)


def scan(path: pathlib.Path) -> dict[int, list[int]]:
    global _last_relf_bss_alloc, _last_hostcall_seams
    global _last_scheduler_context_seam, _last_vi_sleep_seam, _last_vi_wait_state
    _last_relf_bss_alloc = None
    _last_hostcall_seams = []
    _last_scheduler_context_seam = None
    _last_vi_sleep_seam = None
    _last_vi_wait_state = None

    data = path.read_bytes()
    sections = _sections(data)
    for address in (
        *FRAME_ENTRIES.keys(),
        *LLVM_REENTRY_POINTS.keys(),
        RELF_ALLOC_CALL,
        RELF_ALLOC_RETURN,
        RELF_MAIN_END - 4,
        SELECT_THREAD_START,
        SELECT_THREAD_END - 4,
        OS_SET_CURRENT_CONTEXT,
        OS_SAVE_CONTEXT,
        OS_LOAD_CONTEXT,
        VI_WAIT_FOR_RETRACE,
        OS_DISABLE_INTERRUPTS,
        OS_RESTORE_INTERRUPTS,
    ):
        if not _address_in_text(sections, address):
            raise RuntimeError(
                f"R8PE01 seam 0x{address:08X} is not inside an executable DOL section"
            )

    found = {GX_BEGIN: [], GX_END: []}
    relf_alloc_raw: int | None = None
    relf_bss_calls: list[tuple[int, int]] = []
    scheduler_context_calls: list[tuple[int, int]] = []
    vi_wait_calls: list[tuple[int, int, int]] = []
    vi_wait_blr: int | None = None

    for section in sections:
        for offset in range(0, section.size, 4):
            raw = _read_be32(data, section.file_offset + offset)
            pc = section.address + offset
            if pc == RELF_ALLOC_CALL:
                relf_alloc_raw = raw
            target = _branch_target(raw, pc)
            if target in found:
                found[target].append(pc)
            if (RELF_ALLOC_RETURN <= pc < RELF_MAIN_END and
                    target == RELF_ALLOC_TARGET):
                relf_bss_calls.append((pc, raw))
            if (SELECT_THREAD_START <= pc < SELECT_THREAD_END and
                    target == OS_SAVE_CONTEXT):
                scheduler_context_calls.append((pc, raw))
            if VI_WAIT_FOR_RETRACE <= pc < VI_WAIT_SCAN_END and vi_wait_blr is None:
                if raw == PPC_BLR:
                    vi_wait_blr = pc
                elif target is not None:
                    vi_wait_calls.append((pc, target, raw))

    for target, calls in found.items():
        calls.sort()
        if len(calls) != EXPECTED_CALLS:
            raise RuntimeError(
                f"R8PE01 v19 seam scan target 0x{target:08X}: "
                f"found {len(calls)} linked calls, expected {EXPECTED_CALLS}"
            )
        if len(set(calls)) != len(calls):
            raise RuntimeError(f"duplicate v19 callsites for target 0x{target:08X}")

    if relf_alloc_raw != RELF_ALLOC_RAW or _branch_target(RELF_ALLOC_RAW, RELF_ALLOC_CALL) != RELF_ALLOC_TARGET:
        observed = "missing" if relf_alloc_raw is None else f"0x{relf_alloc_raw:08X}"
        raise RuntimeError(
            "R8PE01 relF allocation seam changed: "
            f"0x{RELF_ALLOC_CALL:08X}={observed}, expected 0x{RELF_ALLOC_RAW:08X} "
            f"(bl 0x{RELF_ALLOC_TARGET:08X})"
        )
    if len(relf_bss_calls) != 1:
        details = ", ".join(f"0x{pc:08X}" for pc, _raw in relf_bss_calls) or "none"
        raise RuntimeError(
            "R8PE01 relF BSS allocation seam changed: expected exactly one later "
            f"bl __memAlloc before 0x{RELF_MAIN_END:08X}, found {details}"
        )
    if len(scheduler_context_calls) != 1:
        details = ", ".join(
            f"0x{pc:08X}" for pc, _raw in scheduler_context_calls
        ) or "none"
        raise RuntimeError(
            "R8PE01 SelectThread OSSaveContext seam changed: expected exactly one "
            f"bl 0x{OS_SAVE_CONTEXT:08X} in "
            f"[0x{SELECT_THREAD_START:08X},0x{SELECT_THREAD_END:08X}), found {details}"
        )

    if vi_wait_blr is None:
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace seam changed: no BLR found within the first "
            f"0x{VI_WAIT_SCAN_END - VI_WAIT_FOR_RETRACE:X} bytes"
        )
    disable_calls = [item for item in vi_wait_calls if item[1] == OS_DISABLE_INTERRUPTS]
    restore_calls = [item for item in vi_wait_calls if item[1] == OS_RESTORE_INTERRUPTS]
    if len(disable_calls) != 1 or len(restore_calls) != 1:
        details = ", ".join(
            f"0x{pc:08X}->0x{target:08X}" for pc, target, _raw in vi_wait_calls
        ) or "none"
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace interrupt-bracket shape changed: expected one "
            f"OSDisableInterrupts and one OSRestoreInterrupts before BLR 0x{vi_wait_blr:08X}; "
            f"calls={details}"
        )
    disable_pc = disable_calls[0][0]
    restore_pc = restore_calls[0][0]
    if not disable_pc < restore_pc:
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace interrupt calls are out of order: "
            f"disable=0x{disable_pc:08X} restore=0x{restore_pc:08X}"
        )
    middle_calls = [
        item for item in vi_wait_calls
        if disable_pc < item[0] < restore_pc and
        item[1] not in (OS_DISABLE_INTERRUPTS, OS_RESTORE_INTERRUPTS)
    ]
    if len(middle_calls) != 1:
        details = ", ".join(
            f"0x{pc:08X}->0x{target:08X}" for pc, target, _raw in middle_calls
        ) or "none"
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace sleep seam changed: expected exactly one linked "
            f"call between disable 0x{disable_pc:08X} and restore 0x{restore_pc:08X}; "
            f"middle={details}"
        )
    sleep_call, sleep_target, _sleep_raw = middle_calls[0]
    if not _address_in_text(sections, sleep_target):
        raise RuntimeError(
            "R8PE01 VIWaitForRetrace derived sleep target is not executable: "
            f"call=0x{sleep_call:08X} target=0x{sleep_target:08X}"
        )

    vi_state = _derive_vi_wait_expressions(
        data, sections, vi_wait_blr,
        disable_pc, sleep_call, restore_pc,
    )

    _last_relf_bss_alloc = relf_bss_calls[0]
    save_call, _save_raw = scheduler_context_calls[0]
    _last_scheduler_context_seam = (save_call, save_call + 4)
    _last_vi_sleep_seam = (sleep_call, sleep_target, sleep_call + 4)
    _last_vi_wait_state = vi_state
    _last_hostcall_seams = _derive_hostcalls(path, data)
    return found


def _array(name: str, values: list[int]) -> str:
    body = ", ".join(f"UINT32_C(0x{value:08X})" for value in values)
    return f"static const uint32_t {name}[{len(values)}] = {{ {body} }};\n"


def _hostcall_array() -> str:
    rows = "\n".join(
        "    { UINT32_C(0x%08X), UINT32_C(0x%08X), UINT32_C(0x%08X) }," %
        (seam.call, seam.target, seam.return_pc)
        for seam in _last_hostcall_seams
    )
    return (
        "\ntypedef struct openspm_local_llvm_hostcall_seam {\n"
        "    uint32_t call_pc;\n"
        "    uint32_t target;\n"
        "    uint32_t return_pc;\n"
        "} openspm_local_llvm_hostcall_seam;\n"
        f"#define OPENSPM_LOCAL_LLVM_HOSTCALL_SEAM_COUNT UINT32_C({len(_last_hostcall_seams)})\n"
        f"static const openspm_local_llvm_hostcall_seam OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[{len(_last_hostcall_seams)}] = {{\n"
        + rows + "\n};\n"
    )


def write_header(path: pathlib.Path, found: dict[int, list[int]]) -> None:
    begin_calls = found[GX_BEGIN]
    end_calls = found[GX_END]
    begin_returns = [pc + 4 for pc in begin_calls]
    end_returns = [pc + 4 for pc in end_calls]
    bss_call, bss_raw = _require_relf_bss_seam()
    scheduler_idle = next(iter(LLVM_REENTRY_POINTS))
    scheduler_save_call, scheduler_context_reentry = _require_scheduler_context_seam()
    vi_sleep_call, vi_sleep_target, vi_sleep_return = _require_vi_sleep_seam()
    queue_mode, queue_value, count_mode, count_value = _require_vi_wait_state()
    text = (
        "#ifndef OPENSPM_LOCAL_V19_LLVM_SEAMS_H\n"
        "#define OPENSPM_LOCAL_V19_LLVM_SEAMS_H\n\n"
        "#include <stdint.h>\n\n"
        "/* Player-local R8PE01 addresses derived from verified player code. */\n"
        f"#define OPENSPM_LOCAL_V19_GX_BEGIN UINT32_C(0x{GX_BEGIN:08X})\n"
        f"#define OPENSPM_LOCAL_V19_GX_END UINT32_C(0x{GX_END:08X})\n"
        f"#define OPENSPM_LOCAL_RELF_ALLOC_CALL UINT32_C(0x{RELF_ALLOC_CALL:08X})\n"
        f"#define OPENSPM_LOCAL_RELF_ALLOC_RETURN UINT32_C(0x{RELF_ALLOC_RETURN:08X})\n"
        f"#define OPENSPM_LOCAL_RELF_BSS_ALLOC_CALL UINT32_C(0x{bss_call:08X})\n"
        f"#define OPENSPM_LOCAL_RELF_BSS_ALLOC_RETURN UINT32_C(0x{bss_call + 4:08X})\n"
        f"#define OPENSPM_LOCAL_RELF_BSS_ALLOC_RAW UINT32_C(0x{bss_raw:08X})\n"
        f"#define OPENSPM_LOCAL_SCHEDULER_IDLE_REENTRY UINT32_C(0x{scheduler_idle:08X})\n"
        f"#define OPENSPM_LOCAL_SCHEDULER_CONTEXT_SAVE_CALL UINT32_C(0x{scheduler_save_call:08X})\n"
        f"#define OPENSPM_LOCAL_SCHEDULER_CONTEXT_REENTRY UINT32_C(0x{scheduler_context_reentry:08X})\n"
        f"#define OPENSPM_LOCAL_VI_WAIT_FOR_RETRACE UINT32_C(0x{VI_WAIT_FOR_RETRACE:08X})\n"
        f"#define OPENSPM_LOCAL_OS_SLEEP_THREAD UINT32_C(0x{vi_sleep_target:08X})\n"
        f"#define OPENSPM_LOCAL_VI_SLEEP_CALL UINT32_C(0x{vi_sleep_call:08X})\n"
        f"#define OPENSPM_LOCAL_VI_SLEEP_RETURN UINT32_C(0x{vi_sleep_return:08X})\n"
        f"#define OPENSPM_LOCAL_VI_EXPR_ABSOLUTE UINT32_C({VI_EXPR_ABSOLUTE})\n"
        f"#define OPENSPM_LOCAL_VI_EXPR_R13 UINT32_C({VI_EXPR_R13})\n"
        f"#define OPENSPM_LOCAL_VI_EXPR_R2 UINT32_C({VI_EXPR_R2})\n"
        f"#define OPENSPM_LOCAL_VI_EXPR_UNAVAILABLE UINT32_C(0x{VI_EXPR_UNAVAILABLE:08X})\n"
        f"#define OPENSPM_LOCAL_VI_SLEEP_QUEUE_MODE UINT32_C(0x{queue_mode:08X})\n"
        f"#define OPENSPM_LOCAL_VI_SLEEP_QUEUE_VALUE UINT32_C(0x{queue_value:08X})\n"
        f"#define OPENSPM_LOCAL_VI_RETRACE_COUNT_MODE UINT32_C(0x{count_mode:08X})\n"
        f"#define OPENSPM_LOCAL_VI_RETRACE_COUNT_VALUE UINT32_C(0x{count_value:08X})\n"
        f"#define OPENSPM_LOCAL_V19_BEGIN_COUNT UINT32_C({len(begin_calls)})\n"
        f"#define OPENSPM_LOCAL_V19_END_COUNT UINT32_C({len(end_calls)})\n"
        + _array("OPENSPM_LOCAL_V19_BEGIN_CALLS", begin_calls)
        + _array("OPENSPM_LOCAL_V19_BEGIN_RETURNS", begin_returns)
        + _array("OPENSPM_LOCAL_V19_END_CALLS", end_calls)
        + _array("OPENSPM_LOCAL_V19_END_RETURNS", end_returns)
        + _hostcall_array()
        + "\n#endif\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_map(path: pathlib.Path, found: dict[int, list[int]]) -> None:
    bss_call, _bss_raw = _require_relf_bss_seam()
    scheduler_save_call, scheduler_context_reentry = _require_scheduler_context_seam()
    vi_sleep_call, vi_sleep_target, vi_sleep_return = _require_vi_sleep_seam()
    rows: list[tuple[int, str]] = [
        (GX_BEGIN, "openspm_v19_GXBeginDisplayList"),
        (GX_END, "openspm_v19_GXEndDisplayList"),
        (RELF_ALLOC_CALL, "openspm_relf_alloc_call"),
        (RELF_ALLOC_RETURN, "openspm_relf_alloc_return"),
        (bss_call, "openspm_relf_bss_alloc_call"),
        (bss_call + 4, "openspm_relf_bss_alloc_return"),
        (scheduler_save_call, "openspm_scheduler_context_save_call"),
        (scheduler_context_reentry, "openspm_scheduler_context_reentry"),
        (VI_WAIT_FOR_RETRACE, "openspm_vi_wait_for_retrace"),
        (vi_sleep_call, "openspm_vi_sleep_call"),
        (vi_sleep_return, "openspm_vi_sleep_return"),
        (vi_sleep_target, "openspm_os_sleep_thread"),
        *FRAME_ENTRIES.items(),
        *LLVM_REENTRY_POINTS.items(),
    ]
    for kind, target in (("begin", GX_BEGIN), ("end", GX_END)):
        for index, pc in enumerate(found[target]):
            rows.append((pc, f"openspm_v19_{kind}_call_{index}"))
            rows.append((pc + 4, f"openspm_v19_{kind}_return_{index}"))
    for index, seam in enumerate(_last_hostcall_seams):
        rows.append((seam.call, f"openspm_hostcall_call_{index}_{seam.target:08X}"))
        rows.append((seam.return_pc, f"openspm_hostcall_return_{index}_{seam.target:08X}"))

    unique: dict[int, str] = {}
    for address, name in rows:
        unique.setdefault(address, name)
    rows = sorted(unique.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{address:08X} 0 {name}\n" for address, name in rows),
        encoding="utf-8",
        newline="\n",
    )


def _encode_bl(pc: int, target: int) -> int:
    disp = target - pc
    if not -(1 << 25) <= disp < (1 << 25) or (disp & 3):
        raise ValueError("self-test branch out of range")
    return 0x48000001 | (disp & 0x03FFFFFC)


def _encode_d(opcode: int, rd_rs: int, ra: int, imm: int) -> int:
    return ((opcode & 0x3F) << 26) | ((rd_rs & 31) << 21) | ((ra & 31) << 16) | (imm & 0xFFFF)


def self_test() -> None:
    import tempfile

    base = 0x801A0000
    end = 0x80293000
    size = end - base
    file_offset = 0x100
    blob = bytearray(file_offset + size)
    struct.pack_into(">I", blob, 0x00, file_offset)
    struct.pack_into(">I", blob, 0x48, base)
    struct.pack_into(">I", blob, 0x90, size)
    pcs = [0x80200000, 0x80200008, 0x80200010, 0x80200020, 0x80200028, 0x80200030]
    targets = [GX_BEGIN] * 3 + [GX_END] * 3
    for pc, target in zip(pcs, targets):
        struct.pack_into(">I", blob, file_offset + (pc - base), _encode_bl(pc, target))
    struct.pack_into(">I", blob, file_offset + (RELF_ALLOC_CALL - base),
                     _encode_bl(RELF_ALLOC_CALL, RELF_ALLOC_TARGET))
    bss_call = RELF_ALLOC_RETURN + 4
    struct.pack_into(">I", blob, file_offset + (bss_call - base),
                     _encode_bl(bss_call, RELF_ALLOC_TARGET))
    scheduler_save_call = SELECT_THREAD_START + 0x80
    struct.pack_into(">I", blob, file_offset + (scheduler_save_call - base),
                     _encode_bl(scheduler_save_call, OS_SAVE_CONTEXT))

    queue_address = 0x8056F000
    count_address = 0x8056D600
    vi_disable_call = VI_WAIT_FOR_RETRACE + 0x10
    vi_sleep_call = VI_WAIT_FOR_RETRACE + 0x1C
    vi_restore_call = VI_WAIT_FOR_RETRACE + 0x28
    vi_sleep_target = 0x802760E0
    vi_blr = VI_WAIT_FOR_RETRACE + 0x2C
    vi_words = {
        VI_WAIT_FOR_RETRACE + 0x00: _encode_d(15, 30, 0, 0x8057),  # lis r30,0x8057
        VI_WAIT_FOR_RETRACE + 0x04: _encode_d(14, 30, 30, -0x1000),
        VI_WAIT_FOR_RETRACE + 0x08: _encode_d(15, 29, 0, 0x8057),  # lis r29,0x8057
        VI_WAIT_FOR_RETRACE + 0x0C: _encode_d(14, 29, 29, -0x2A00),
        vi_disable_call: _encode_bl(vi_disable_call, OS_DISABLE_INTERRUPTS),
        VI_WAIT_FOR_RETRACE + 0x14: _encode_d(32, 28, 29, 0),  # lwz r28,0(r29)
        VI_WAIT_FOR_RETRACE + 0x18: _encode_d(14, 3, 30, 0),   # addi r3,r30,0
        vi_sleep_call: _encode_bl(vi_sleep_call, vi_sleep_target),
        VI_WAIT_FOR_RETRACE + 0x20: _encode_d(32, 0, 29, 0),  # lwz r0,0(r29)
        VI_WAIT_FOR_RETRACE + 0x24: _encode_d(14, 3, 0, 1),
        vi_restore_call: _encode_bl(vi_restore_call, OS_RESTORE_INTERRUPTS),
        vi_blr: PPC_BLR,
    }
    for pc, raw in vi_words.items():
        struct.pack_into(">I", blob, file_offset + (pc - base), raw)

    assert _read_be32(blob, file_offset + (RELF_ALLOC_CALL - base)) == RELF_ALLOC_RAW

    with tempfile.TemporaryDirectory(prefix="openspm-v19-seams-") as temp:
        dol = pathlib.Path(temp) / "sample.dol"
        header = pathlib.Path(temp) / "seams.h"
        symbol_map = pathlib.Path(temp) / "seams.map"
        dol.write_bytes(blob)
        found = scan(dol)
        assert found[GX_BEGIN] == pcs[:3]
        assert found[GX_END] == pcs[3:]
        assert _require_relf_bss_seam()[0] == bss_call
        assert _require_scheduler_context_seam() == (
            scheduler_save_call, scheduler_save_call + 4
        )
        assert _require_vi_sleep_seam() == (
            vi_sleep_call, vi_sleep_target, vi_sleep_call + 4
        )
        assert _require_vi_wait_state() == (
            VI_EXPR_ABSOLUTE, queue_address,
            VI_EXPR_ABSOLUTE, count_address,
        )
        write_header(header, found)
        write_map(symbol_map, found)
        text = header.read_text(encoding="utf-8")
        map_text = symbol_map.read_text(encoding="utf-8")
        assert "0x80200004" in text and "0x80200034" in text
        assert "OPENSPM_LOCAL_RELF_ALLOC_CALL" in text
        assert "OPENSPM_LOCAL_RELF_BSS_ALLOC_CALL" in text
        assert "OPENSPM_LOCAL_RELF_BSS_ALLOC_RAW" in text
        assert "OPENSPM_LOCAL_SCHEDULER_IDLE_REENTRY" in text
        assert "OPENSPM_LOCAL_SCHEDULER_CONTEXT_SAVE_CALL" in text
        assert "OPENSPM_LOCAL_SCHEDULER_CONTEXT_REENTRY" in text
        assert "OPENSPM_LOCAL_VI_WAIT_FOR_RETRACE" in text
        assert "OPENSPM_LOCAL_OS_SLEEP_THREAD" in text
        assert "OPENSPM_LOCAL_VI_SLEEP_CALL" in text
        assert "OPENSPM_LOCAL_VI_SLEEP_QUEUE_MODE" in text
        assert "OPENSPM_LOCAL_VI_RETRACE_COUNT_MODE" in text
        assert "OPENSPM_LOCAL_LLVM_HOSTCALL_SEAM_COUNT" in text
        assert "openspm_v19_begin_return_0" in map_text
        assert "openspm_phase_spmarioMain" in map_text
        assert "openspm_relf_alloc_call" in map_text
        assert "openspm_relf_alloc_return" in map_text
        assert "openspm_relf_bss_alloc_call" in map_text
        assert "openspm_relf_bss_alloc_return" in map_text
        assert "8027551C 0 openspm_scheduler_idle_reentry" in map_text
        assert f"{scheduler_save_call:08X} 0 openspm_scheduler_context_save_call" in map_text
        assert f"{scheduler_save_call + 4:08X} 0 openspm_scheduler_context_reentry" in map_text
        assert f"{vi_sleep_call:08X} 0 openspm_vi_sleep_call" in map_text
        assert f"{vi_sleep_target:08X} 0 openspm_os_sleep_thread" in map_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("main_dol", nargs="?", type=pathlib.Path)
    parser.add_argument("--header", type=pathlib.Path)
    parser.add_argument("--map", dest="symbol_map", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("v19/relF/hostcall/scheduler-reentry/vi-wait LLVM DOL seam scanner self-test: PASS")
            return 0
        if args.main_dol is None or args.header is None or args.symbol_map is None:
            parser.error("main_dol, --header and --map are required")
        found = scan(args.main_dol.resolve())
        bss_call, _bss_raw = _require_relf_bss_seam()
        scheduler_save_call, scheduler_context_reentry = _require_scheduler_context_seam()
        vi_sleep_call, vi_sleep_target, vi_sleep_return = _require_vi_sleep_seam()
        queue_mode, queue_value, count_mode, count_value = _require_vi_wait_state()
        write_header(args.header.resolve(), found)
        write_map(args.symbol_map.resolve(), found)
        nand_calls = sum(1 for seam in _last_hostcall_seams if seam.target == 0x8029A500)
        scheduler_idle = next(iter(LLVM_REENTRY_POINTS))
        print(
            "M14 LLVM seams derived: "
            f"begin_calls={','.join(f'0x{x:08X}' for x in found[GX_BEGIN])} "
            f"end_calls={','.join(f'0x{x:08X}' for x in found[GX_END])} "
            f"phase_entries={len(FRAME_ENTRIES)} "
            f"scheduler_idle_reentry=0x{scheduler_idle:08X} "
            f"scheduler_context_save=0x{scheduler_save_call:08X}/0x{scheduler_context_reentry:08X} "
            f"vi_wait_sleep=0x{vi_sleep_call:08X}->0x{vi_sleep_target:08X}/0x{vi_sleep_return:08X} "
            f"vi_queue={queue_mode}:0x{queue_value:08X} "
            f"vi_count={count_mode}:0x{count_value:08X} "
            f"relf_alloc=0x{RELF_ALLOC_CALL:08X}/0x{RELF_ALLOC_RETURN:08X} "
            f"relf_bss_alloc=0x{bss_call:08X}/0x{bss_call + 4:08X} "
            f"hosted_calls={len(_last_hostcall_seams)} nand_check_calls={nand_calls}"
        )
        return 0
    except (OSError, RuntimeError, AssertionError, ValueError) as exc:
        print(f"error: LLVM seam derivation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
