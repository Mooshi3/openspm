#!/usr/bin/env python3
"""Find and check every code reference to a game table, and emit relocation recipes.

docs/CONTENT-API.md: a fixed-size game table becomes dynamic by moving it to
OpenSPM-owned memory. The recompiled code has the table's address baked into
`lis rX, HI` / `addi rY, rX, LO` (or `lwz rY, LO(rX)`) pairs, so the move is a
set of single-instruction patches to those `lis` immediates. That is only safe
when:

  * every use of each such `lis` register (until it is redefined) is an access
    inside the table: the register serves nothing else;
  * no data word points into the table (a pointer can not be patched);
  * the new base has the same low 16 bits as the old one, so only the `lis`
    halves change.

This tool decodes the player's own codegen DOL (main.dol + relF, as the build
makes it), finds every `lis` whose accesses land in [table, table + size),
checks the rules above and prints either the recipes (JSON, the format of
state/openspm-code-patches.json) or the exact reasons the table can not move.
It is conservative: an instruction it does not model counts as an unknown use
of the register, which rejects the table rather than risking a wrong patch.

Example (the item table, reproduces the item-data-NN recipes):
  table_refs.py --dol codegen.dol --table 0x803B7E78 --size 0x5C78 \
      --to 0x93E07E78 --name item-data
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

REL_START = 0x80C00000  # relF: patched in the recompiled module only ("boot": false)

# ---- DOL ------------------------------------------------------------------------


@dataclass
class Section:
    address: int
    data: bytes
    text: bool


def parse_dol(dol: bytes) -> list[Section]:
    offsets = struct.unpack(">18I", dol[0:72])
    addresses = struct.unpack(">18I", dol[72:144])
    sizes = struct.unpack(">18I", dol[144:216])
    out = []
    for i, (off, addr, size) in enumerate(zip(offsets, addresses, sizes)):
        if size:
            out.append(Section(addr, bytes(dol[off : off + size]), i < 7))
    return out


# ---- minimal PowerPC register-use model -------------------------------------------

D_LOADS_GPR = {32, 33, 34, 35, 40, 41, 42, 43, 46}  # lwz lwzu lbz lbzu lhz lhzu lha lhau lmw
D_LOADS_FPR = {48, 49, 50, 51}
D_STORES = {36, 37, 38, 39, 44, 45, 47, 52, 53, 54, 55}
D_UPDATE = {33, 35, 41, 43, 37, 39, 45, 49, 51, 53, 55}
RA_WRITERS = {20, 21, 23, 24, 25, 26, 27, 28, 29}  # rlwimi rlwinm rlwnm ori oris xori xoris andi. andis.
RD_FROM_RA = {7, 8, 12, 13}  # mulli subfic addic addic.
X_STORES = {151, 183, 215, 247, 407, 439, 150, 662, 918, 663, 695, 727, 759, 983}
X_LOADS = {23, 55, 87, 119, 279, 311, 343, 375, 534, 790, 20}
X_FLOAT_LOADS = {535, 567, 599, 631}
X_RA_WRITERS = {28, 60, 444, 412, 124, 316, 476, 284, 24, 536, 792, 824, 26, 954, 922}
X_RD_WRITERS = {266, 40, 235, 491, 104, 10, 138, 8, 136, 202, 75, 11, 459, 234, 200, 232, 339, 371, 19, 83}
X_NO_WRITE = {0, 32, 467, 144, 146, 86, 54, 246, 278, 1014, 470, 982, 598, 854, 4}


@dataclass
class Op:
    kind: str  # "lis" "addi" "load" "store" "other"
    writes: set[int] = field(default_factory=set)
    reads: set[int] = field(default_factory=set)
    base: int | None = None  # D-form base register
    disp: int = 0
    rd: int = 0
    imm: int = 0
    stop: bool = False  # blr / unconditional branch: end of this straight line


def simm(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def decode(word: int) -> Op:
    op = word >> 26
    rd, ra, rb = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    lo = word & 0xFFFF
    if op == 15:  # addis
        if ra == 0:
            return Op("lis", {rd}, set(), rd=rd, imm=lo)
        return Op("other", {rd}, {ra})
    if op == 14:  # addi
        if ra == 0:
            return Op("other", {rd})  # li
        return Op("addi", {rd}, {ra}, base=ra, disp=simm(lo), rd=rd)
    if op in D_LOADS_GPR or op in D_LOADS_FPR:
        writes = {rd} if op in D_LOADS_GPR else set()
        if op == 46:  # lmw: rd..r31
            writes = set(range(rd, 32))
        if op in D_UPDATE:
            writes.add(ra)
        return Op("load", writes, {ra} if ra else set(), base=ra or None, disp=simm(lo), rd=rd)
    if op in D_STORES:
        reads = {ra} if ra else set()
        if op in (36, 37, 38, 39, 44, 45):
            reads.add(rd)
        if op == 47:
            reads |= set(range(rd, 32))
        return Op("store", {ra} if op in D_UPDATE else set(), reads, base=ra or None, disp=simm(lo))
    if op in RA_WRITERS:
        return Op("other", {ra}, {rd} | ({ra} if op == 20 else set()) | ({rb} if op == 23 else set()))
    if op in RD_FROM_RA:
        return Op("other", {rd}, {ra})
    if op in (10, 11):  # cmpli cmpi
        return Op("other", set(), {ra})
    if op == 18:
        return Op("other", stop=not (word & 1))  # b (bl continues)
    if op == 16:
        return Op("other")
    if op == 19:
        xo = (word >> 1) & 0x3FF
        bo = (word >> 21) & 31
        if xo in (16, 528):  # bclr / bcctr
            return Op("other", stop=(bo & 0x14) == 0x14 and not (word & 1))
        return Op("other")
    if op in (56, 57):  # psq_l / psq_lu: FPR, 12-bit displacement
        d12 = word & 0xFFF
        return Op("load", {ra} if op == 57 else set(), {ra} if ra else set(), base=ra or None,
                  disp=d12 - 0x1000 if d12 & 0x800 else d12)
    if op in (60, 61):  # psq_st / psq_stu
        d12 = word & 0xFFF
        return Op("store", {ra} if op == 61 else set(), {ra} if ra else set(), base=ra or None,
                  disp=d12 - 0x1000 if d12 & 0x800 else d12)
    if op in (59, 63, 4, 17, 3):
        return Op("other")
    if op == 31:
        xo = (word >> 1) & 0x3FF
        if xo == 467:  # mtspr: reads rS only (the SPR number sits in ra/rb)
            return Op("other", set(), {rd})
        if xo in (339, 371, 19, 83):  # mfspr mftb mfcr mfmsr: write rD, read no GPR
            return Op("other", {rd}, set())
        if xo in X_STORES:
            return Op("store", set(), {rd, ra, rb})
        if xo in X_LOADS:
            return Op("other", {rd}, {ra, rb})
        if xo in X_FLOAT_LOADS:
            return Op("other", set(), {ra, rb})
        if xo in X_RA_WRITERS:
            return Op("other", {ra}, {rd, rb})
        if xo in X_RD_WRITERS:
            return Op("other", {rd}, {ra, rb})
        if xo in X_NO_WRITE:
            return Op("other", set(), {rd, ra, rb})
    # Not modelled: reads everything it could name, writes nothing (conservative).
    return Op("other", set(), {rd, ra, rb})


# ---- analysis ---------------------------------------------------------------------


VOLATILE = {0} | set(range(3, 13))  # r0, r3-r12: not kept across a call


def _is_call(word: int) -> bool:
    op = word >> 26
    if op == 18 or op == 16:
        return bool(word & 1)  # bl / bcl
    if op == 19 and ((word >> 1) & 0x3FF) in (16, 528):
        return bool(word & 1)  # blrl / bctrl
    return False


def _is_return(word: int) -> bool:
    return word == 0x4E800020  # blr: r3 leaves the function


def _branch(word: int, k: int) -> tuple[list[int], bool]:
    """(successor indexes within the section, opaque): the next instructions
    after the one at index k. Calls continue at k + 1."""
    op = word >> 26
    if op == 18:  # b / bl
        if word & 1:
            return [k + 1], False
        if word & 2:
            return [], True  # absolute: not followed
        li = word & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        return [k + li // 4], False
    if op == 16:  # bc
        if word & 1:
            return [k + 1], False
        if word & 2:
            return [k + 1], True
        bd = word & 0xFFFC
        if bd & 0x8000:
            bd -= 0x10000
        target = k + bd // 4
        bo = (word >> 21) & 31
        return ([target] if (bo & 0x14) == 0x14 else [k + 1, target]), False
    if op == 19 and ((word >> 1) & 0x3FF) in (16, 528):
        if word & 1:
            return [k + 1], False  # blrl / bctrl: a call
        bo = (word >> 21) & 31
        unconditional = (bo & 0x14) == 0x14
        if ((word >> 1) & 0x3FF) == 528:  # bctr: a jump table, not followed
            return ([] if unconditional else [k + 1]), True
        return ([] if unconditional else [k + 1]), False  # blr
    return [k + 1], False


def _prologue_consts(words, ops, i: int) -> tuple:
    """Constants (lis / addi) the function holds in callee-saved registers
    (r14-r31) when it reaches index i: a straight pass from the function's
    `stwu r1` (within 2048 instructions back), forgetting a register on any
    other write. Used only to find jump tables."""
    start = i
    while start > 0 and i - start < 2048 and (words[start] >> 16) != 0x9421:
        start -= 1
    if (words[start] >> 16) != 0x9421:
        return ()
    consts: dict[int, int] = {}
    for k in range(start, i):
        w = words[k]
        major, rd, ra = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        new = None
        if major == 15 and ra == 0:
            new = ((w & 0xFFFF) << 16) & 0xFFFFFFFF
        elif major == 14 and ra in consts:
            new = (consts[ra] + simm(w & 0xFFFF)) & 0xFFFFFFFF
        for r in ops[k].writes:
            consts.pop(r, None)
        if new is not None and rd >= 14:
            consts[rd] = new
    return tuple(sorted(consts.items()))


def find(sections: list[Section], table: int, size: int, window: int = 400) -> dict:
    """Every `lis` whose value reaches the table, with every address derived
    from it, along every path from the lis (fallthrough, both sides of a
    condition, branch targets; up to `window` instructions per path). Values
    are followed through `addi` (a pointer at a known address), `add` with one
    unknown operand (a pointer plus an index: a row), `mr`, and update-form
    loads. Each address the code forms or accesses through them (a load, a
    store, a pointer passed to a call, returned, or stored) is classified:
    inside the table, or outside it. A `lis` is patchable only when
    everything derived from it is inside; anything the model does not follow
    (an unmodelled use, a jump table while a derived pointer is live) counts
    as outside and refuses."""
    end = table + size
    patches, problems, lis_seen = [], [], 0
    neighbours = []

    def data_word(address: int) -> int | None:
        for s in sections:
            if s.address <= address < s.address + len(s.data) - 3:
                return struct.unpack_from(">I", s.data, address - s.address)[0]
        return None

    for sec in sections:
        if not sec.text:
            continue
        words = struct.unpack(f">{len(sec.data) // 4}I", sec.data[: len(sec.data) // 4 * 4])
        ops = [decode(w) for w in words]
        for i, op in enumerate(ops):
            if op.kind != "lis":
                continue
            hi = op.imm << 16
            if not (hi - 0x8000 < end and hi + 0x8000 > table):
                continue
            in_table, other, direct_in = [], [], []

            def note(k: int, address: int | None, direct: bool = False) -> None:
                at = sec.address + 4 * k
                if address is not None and table <= address < end:
                    in_table.append((at, address))
                    if direct:
                        direct_in.append(at)
                else:
                    other.append((at, address))

            # Paths: (index, steps, register -> (address, indexed), registers still the bare lis
            # half, other registers' known constants (lis/addi), the jump table in ctr).
            work = [(i + 1, 0, {op.rd: (hi, False)}, frozenset({op.rd}), _prologue_consts(words, ops, i), 0)]
            seen: set = set()
            budget = 20000
            while work and budget:
                k, steps, vals, bare, consts_t, ctr_table = work.pop()
                if k < 0 or k >= len(ops) or steps >= window or not vals:
                    continue
                key = (k, tuple(sorted(vals.items())), bare, consts_t, ctr_table)
                if key in seen:
                    continue
                seen.add(key)
                budget -= 1
                use, w = ops[k], words[k]
                xo = (w >> 1) & 0x3FF
                major = w >> 26
                rd, ra, rb = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
                vals = dict(vals)
                bare_set = set(bare)
                consts = dict(consts_t)
                successors, opaque = _branch(w, k)
                # Other registers' constants, for jump tables: lis / addi / lwzx base + index / mtctr.
                new_consts: dict[int, int] = {}
                jt_load = None
                if major == 15 and ra == 0:
                    new_consts[rd] = ((w & 0xFFFF) << 16) & 0xFFFFFFFF
                elif major == 14 and ra in consts:
                    new_consts[rd] = (consts[ra] + simm(w & 0xFFFF)) & 0xFFFFFFFF
                elif major == 31 and xo == 23 and (ra in consts) != (rb in consts):  # lwzx from a const base
                    jt_load = (rd, consts[ra if ra in consts else rb])
                if major == 31 and xo == 467 and (((w >> 16) & 31) | (((w >> 11) & 31) << 5)) == 9:  # mtctr
                    ctr_table = consts.get(rd | 0x100, 0)
                if opaque and major == 19 and xo == 528 and ctr_table:
                    # A switch: every case in the jump table (code words in this section).
                    successors, opaque = [], False
                    for n in range(256):
                        target = data_word(ctr_table + 4 * n)
                        if target is None or not (sec.address <= target < sec.address + 4 * len(ops)):
                            break
                        successors.append((target - sec.address) // 4)
                    if not successors:
                        opaque = True
                if _is_call(w) or _is_return(w):
                    # Pointers in argument / return registers leave: their address counts.
                    for r in list(vals):
                        if r in (3, 4, 5, 6, 7, 8, 9, 10) and r not in bare_set:
                            note(k, vals[r][0])
                    for r in list(vals):
                        if r in VOLATILE:
                            del vals[r]
                            bare_set.discard(r)
                elif opaque:
                    if any(r not in bare_set for r in vals):
                        other.append((sec.address + 4 * k, None))  # control flow not followed
                    continue
                else:
                    touched = [r for r in vals if r in use.reads or use.base == r]
                    new_vals: dict[int, tuple[int, bool]] = {}
                    if touched:
                        if use.kind == "addi" and use.base in vals:
                            address, indexed = vals[use.base]
                            new_vals[use.rd] = ((address + use.disp) & 0xFFFFFFFF, indexed)
                            note(k, new_vals[use.rd][0], use.base in bare_set)
                        elif use.kind in ("load", "store") and use.base in vals:
                            address, indexed = vals[use.base]
                            target = (address + use.disp) & 0xFFFFFFFF
                            note(k, target, use.base in bare_set)
                            if use.kind == "store":
                                for r in _store_value_regs(w):
                                    if r in vals:  # the pointer itself goes to memory
                                        note(k, vals[r][0])
                            if major in D_UPDATE:
                                new_vals[use.base] = (target, indexed)
                        elif major == 31 and xo == 266 and (ra in vals) != (rb in vals):  # add: pointer + index
                            address, _ = vals[ra if ra in vals else rb]
                            new_vals[rd] = (address, True)
                            note(k, address)
                        elif major == 31 and xo == 444 and rd == rb and rd in vals and len(touched) == 1:  # mr
                            new_vals[ra] = vals[rd]
                        elif major == 31 and (xo in X_LOADS or xo in X_FLOAT_LOADS or xo in X_STORES) and \
                                (ra in vals) != (rb in vals) and not (xo in X_STORES and rd in vals):
                            note(k, vals[ra if ra in vals else rb][0])  # indexed access: the row
                        else:
                            for _ in touched:
                                other.append((sec.address + 4 * k, None))  # a use not modelled
                    for r in use.writes:
                        vals.pop(r, None)
                        bare_set.discard(r)
                    for r in new_vals:
                        bare_set.discard(r)
                    vals.update(new_vals)
                for r in use.writes:
                    consts.pop(r, None)
                    consts.pop(r | 0x100, None)
                if _is_call(w):
                    for r in list(consts):
                        if (r & 0xFF) in VOLATILE:
                            del consts[r]
                consts.update(new_consts)
                if jt_load:
                    consts[jt_load[0] | 0x100] = jt_load[1]  # rD holds an entry of the table at this base
                if _is_return(w):
                    continue
                for s in successors:
                    work.append((s, steps + 1, vals, frozenset(bare_set), tuple(sorted(consts.items())), ctr_table))
            address = sec.address + 4 * i
            if not direct_in:
                if in_table:  # another table's lis whose walk reaches past its own end
                    neighbours.append(f"0x{address:08X}")
                continue
            lis_seen += 1
            if other:
                problems.append(
                    {
                        "lis": f"0x{address:08X}",
                        "reason": "register also used for something else",
                        "uses": [f"0x{a:08X}" + (f"->0x{t:08X}" if t is not None else " (opaque)") for a, t in other[:6]],
                    }
                )
                continue
            patches.append({"address": address, "expected": words[i], "hi": op.imm})
    pointers = []
    for sec in sections:
        if sec.text:
            continue
        for off in range(0, len(sec.data) - 3, 4):
            (v,) = struct.unpack_from(">I", sec.data, off)
            if table <= v < end:
                pointers.append(f"0x{sec.address + off:08X}")
    return {"patches": patches, "problems": problems, "data_pointers": pointers, "references": lis_seen,
            "neighbours": neighbours}


def _store_value_regs(word: int) -> set[int]:
    """Registers a store writes to memory (its value operand), for D-form stores."""
    op = word >> 26
    rd = (word >> 21) & 31
    if op in (36, 37, 38, 39, 44, 45):
        return {rd}
    if op == 47:
        return set(range(rd, 32))
    return set()


def recipes(result: dict, table: int, new_base: int, name: str) -> list[dict]:
    if (table ^ new_base) & 0xFFFF:
        raise ValueError("the new base must have the same low 16 bits as the table (only lis halves change)")
    delta_hi = ((new_base - table) >> 16) & 0xFFFF
    out = []
    for n, p in enumerate(sorted(result["patches"], key=lambda p: p["address"])):
        new_hi = (p["hi"] + delta_hi) & 0xFFFF
        entry = {
            "name": f"{name}-{n:02d}",
            "address": f"0x{p['address']:08X}",
            "expected": f"0x{p['expected']:08X}",
            "replacement": f"0x{(p['expected'] & 0xFFFF0000) | new_hi:08X}",
        }
        if p["address"] >= REL_START:
            entry["boot"] = False
        out.append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path, help="the player's codegen DOL (main.dol + relF)")
    ap.add_argument("--table", required=True, type=lambda s: int(s, 0))
    ap.add_argument("--size", required=True, type=lambda s: int(s, 0))
    ap.add_argument("--to", type=lambda s: int(s, 0),
                    help="new base (same low 16 bits as the table; default 0x93D0xxxx)")
    ap.add_argument("--name", required=True)
    args = ap.parse_args(argv)
    if args.to is None:
        args.to = 0x93D00000 | (args.table & 0xFFFF)
    if (args.to ^ args.table) & 0xFFFF:
        ap.error("--to must have the same low 16 bits as --table (only the lis halves change)")
    result = find(parse_dol(args.dol.read_bytes()), args.table, args.size)
    ok = not result["problems"] and not result["data_pointers"]
    report = {
        "table": f"0x{args.table:08X}",
        "size": f"0x{args.size:X}",
        "references": result["references"],
        "relocatable": ok,
        "problems": result["problems"],
        "data_pointers": result["data_pointers"][:32],
    }
    if ok:
        report["patches"] = recipes(result, args.table, args.to, args.name)
    print(json.dumps(report, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
