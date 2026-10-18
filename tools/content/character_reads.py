#!/usr/bin/env python3
"""Every read of the current character id (MarioWork +0x38), found by
following the MarioWork pointer through the code: the list real character ids
4+ have to cover completely (docs/CHARACTER-IDS.md).

tools/content/character_audit.py only recognises MarioWork when a read sits
shortly after marioGetPtr (0x8012191C) or the lis/addi of its address; reads
in functions that receive MarioWork as an argument (every move function, for
one) are missed. This analysis instead follows the pointer:

  * sources: marioGetPtr's return value (r3), and lis/addi of 0x804CEE58;
  * within a function, along every path from its entry (fallthrough, both
    sides of a condition, branch targets; jump tables are not followed): mr,
    addi 0, callee-saved registers across calls;
  * across functions: a function whose r3 is MarioWork at some direct call
    site, or that is a move function (the move table 0x803E8718), gets
    MarioWork in r3 at entry. Repeated until nothing changes.

Over-including is deliberate: a function that gets MarioWork from one caller
and something else from another counts as MarioWork. Each read found is then
classified like character_audit.py does (compare / index / argument / store /
other). As a cross-check, every `lbz rX, 0x38(rY)` followed by `extsb rX`
anywhere (the shape of a signed character id) that this analysis does not
explain is listed as "unexplained": those need a look by hand.

Addresses only; no game code is printed. Usage:
  character_reads.py --dol codegen.dol [--json out.json] [--markdown out.md]
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import character_audit  # noqa: E402
import table_refs  # noqa: E402

MARIO_GET_PTR = 0x8012191C
MARIO_WORK = 0x804CEE58
CHAR_OFFSET = 0x38
MOVE_TABLE, MOVE_ROWS = 0x803E8718, 78
# The compiler's register save / restore helpers (_savefpr / _restfpr /
# _savegpr / _restgpr runs): called from prologues and epilogues, they touch
# only r11 (their base) and the saved registers' stack slots.
SAVE_RESTORE = (0x80253DD8, 0x80253F08)
VOLATILE = table_refs.VOLATILE
MW = "MW"  # the value MarioWork
ACCESS_SIZE = {32: 4, 33: 4, 34: 1, 35: 1, 40: 2, 41: 2, 42: 2, 43: 2, 36: 4, 37: 4, 38: 1, 39: 1, 44: 2, 45: 2,
               48: 4, 49: 4, 50: 8, 51: 8, 52: 4, 53: 4, 54: 8, 55: 8}


def xo_access(word: int) -> bool:
    xo = (word >> 1) & 0x3FF
    return xo in table_refs.X_LOADS or xo in table_refs.X_STORES or xo in table_refs.X_FLOAT_LOADS


class Code:
    """All text sections as one indexable list of words."""

    def __init__(self, sections: list[table_refs.Section]):
        self.sections = sections
        self.text = [s for s in sections if s.text]
        self.words: dict[int, int] = {}
        for s in self.text:
            for k in range(len(s.data) // 4):
                self.words[s.address + 4 * k] = struct.unpack_from(">I", s.data, 4 * k)[0]

    def data_word(self, address: int) -> int | None:
        for s in self.sections:
            if s.address <= address < s.address + len(s.data) - 3:
                return struct.unpack_from(">I", s.data, address - s.address)[0]
        return None


def _successors(word: int, pc: int) -> tuple[list[int], int | None]:
    """(next pcs, call target or None)."""
    op = word >> 26
    if op == 18:
        li = word & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        target = li if word & 2 else pc + li
        if word & 1:
            return [pc + 4], target & 0xFFFFFFFF
        return [target & 0xFFFFFFFF], None
    if op == 16:
        bd = word & 0xFFFC
        if bd & 0x8000:
            bd -= 0x10000
        target = (bd if word & 2 else pc + bd) & 0xFFFFFFFF
        bo = (word >> 21) & 31
        if word & 1:
            return [pc + 4], target
        return ([target] if (bo & 0x14) == 0x14 else [pc + 4, target]), None
    if op == 19 and ((word >> 1) & 0x3FF) in (16, 528):
        if word & 1:
            return [pc + 4], None  # blrl / bctrl: an indirect call
        bo = (word >> 21) & 31
        return ([] if (bo & 0x14) == 0x14 else [pc + 4]), None
    return [pc + 4], None


def analyse_function(code: Code, entry: int, r3_is_mw: bool, offsets: bool = False, budget: int = 200000):
    """Walk a function from its entry, along every path (jump tables
    included). Pointers are tracked as MarioWork + k, exact or plus an index
    (an array). Returns (reads, calls, accesses): reads = pcs of
    `lbz rX, 0x38(rMW)`; calls = direct call targets reached with MarioWork in
    r3; accesses (offsets=True only) = (pc, offset, size, store), size -2 for
    an array starting at that offset, -3 for MarioWork + offset passed to a
    function. With offsets=False only exact MarioWork registers are part of a
    path's state, which keeps the walk small; derived pointers are then
    followed along the first path that reaches an instruction."""
    reads, calls, accesses = set(), set(), set()
    start = ((3, (0, False)),) if r3_is_mw else ()
    work = [(entry, start, (), 0)]
    seen = set()
    while work and budget:
        pc, ptr_t, consts_t, ctr_table = work.pop()
        exact_mw = frozenset(r for r, (k, ix) in ptr_t if k == 0 and not ix)
        key = (pc, ptr_t if offsets else exact_mw, ctr_table)
        if key in seen or pc not in code.words:
            continue
        seen.add(key)
        budget -= 1
        w = code.words[pc]
        op = table_refs.decode(w)
        major, rd, ra, rb = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        xo = (w >> 1) & 0x3FF
        ptr = dict(ptr_t)  # register -> (k, indexed): MarioWork + k (+ an index)
        hi = dict(consts_t)  # constants (lis / addi); key | 0x100: an entry of the jump table at
        if major == 34 and (w & 0xFFFF) == CHAR_OFFSET and ptr.get(ra) == (0, False):
            reads.add(pc)
        if offsets:
            if op.kind in ("load", "store") and op.base in ptr:
                k, ix = ptr[op.base]
                accesses.add((pc, k + op.disp, -2 if ix else ACCESS_SIZE.get(major, -1), op.kind == "store"))
            elif major == 31 and xo_access(w) and (ra in ptr or rb in ptr):
                k, _ = ptr[ra if ra in ptr else rb]
                accesses.add((pc, k, -2, False))
        succ, target = _successors(w, pc)
        new_ptr: dict[int, tuple[int, bool]] = {}
        new_hi: dict[int, int] = {}
        if major == 15 and ra == 0:  # lis
            new_hi[rd] = (w & 0xFFFF) << 16
        elif major == 14 and ra != 0 and ra in hi:
            value = (hi[ra] + table_refs.simm(w & 0xFFFF)) & 0xFFFFFFFF
            new_hi[rd] = value
            if value == MARIO_WORK:
                new_ptr[rd] = (0, False)
        elif major == 14 and ra != 0 and ra in ptr:  # addi: MarioWork + k + d
            k, ix = ptr[ra]
            new_ptr[rd] = (k + table_refs.simm(w & 0xFFFF), ix)
        elif major == 31 and xo == 444 and rd == rb and rd in ptr:  # mr
            new_ptr[ra] = ptr[rd]
        elif major == 31 and xo == 266 and (ra in ptr) != (rb in ptr):  # add: plus an index
            new_ptr[rd] = (ptr[ra if ra in ptr else rb][0], True)
        elif major == 31 and xo == 23 and (ra in hi) != (rb in hi):  # lwzx from a constant base: a jump table
            new_hi[rd | 0x100] = hi[ra if ra in hi else rb]
        if major == 31 and xo == 467 and (ra | (rb << 5)) == 9:  # mtctr
            ctr_table = hi.get(rd | 0x100, 0)
        if major == 19 and xo == 528 and not (w & 1) and ctr_table:  # bctr through a jump table: every case
            succ = []
            for n in range(256):
                case = code.data_word(ctr_table + 4 * n)
                if case is None or case not in code.words or abs(case - pc) > 0x10000:
                    break
                succ.append(case)
        if target is not None and SAVE_RESTORE[0] <= target < SAVE_RESTORE[1]:
            pass  # registers kept
        elif target is not None or (major == 19 and w & 1):  # a call
            if target is not None and ptr.get(3) == (0, False):
                calls.add(target)
            if offsets:
                for r in (3, 4, 5, 6, 7, 8, 9, 10):
                    if r in ptr and ptr[r] != (0, False):
                        accesses.add((pc, ptr[r][0], -3, False))  # MarioWork + k passed to a function
            for r in list(ptr):
                if r in VOLATILE:
                    del ptr[r]
            for r in list(hi):
                if (r & 0xFF) in VOLATILE:
                    del hi[r]
            if target == MARIO_GET_PTR:
                ptr[3] = (0, False)
        else:
            for r in op.writes:
                ptr.pop(r, None)
                hi.pop(r, None)
                hi.pop(r | 0x100, None)
            ptr.update(new_ptr)
            hi.update(new_hi)
        if w == 0x4E800020:  # blr
            continue
        for s in succ:
            work.append((s, tuple(sorted(ptr.items())), tuple(sorted(hi.items())), ctr_table))
    return reads, calls, accesses


def function_entries(code: Code) -> set[int]:
    """Direct call targets and stwu r1 prologues."""
    entries = set()
    for pc, w in code.words.items():
        _, target = _successors(w, pc)
        if target is not None and target in code.words:
            entries.add(target)
        if (w >> 16) == 0x9421:
            entries.add(pc)
    return entries


EXTSB = 954
X_INDEXED = {23, 87, 279, 343, 535, 599, 151, 215, 407, 663, 727, 266}  # lwzx lbzx lhzx lhax lfsx lfdx stwx stbx sthx stfsx stfdx add


def _base_constant(code: Code, pc: int, reg: int) -> int | None:
    """The constant (lis / addi) in `reg` at pc, by a short backward scan."""
    value_reg, adds = reg, 0
    for n in range(1, 40):
        w = code.words.get(pc - 4 * n)
        if w is None:
            return None
        op = table_refs.decode(w)
        if value_reg in op.writes:
            major, ra = w >> 26, (w >> 16) & 31
            if major == 14 and ra != 0:
                adds += table_refs.simm(w & 0xFFFF)
                value_reg = ra
                continue
            if major == 15 and ra == 0:
                return ((w & 0xFFFF) << 16) + adds & 0xFFFFFFFF
            return None
    return None


def value_uses(code: Code, read_pc: int, window: int = 200) -> dict:
    """Follow the id loaded at read_pc along every path: where it is
    sign-extended, compared, scaled and used as a table index (with the table
    when its base is a nearby constant), passed to a call, stored, or used
    otherwise."""
    w0 = code.words[read_pc]
    rv = (w0 >> 21) & 31
    uses = {"extsb": set(), "compare": set(), "index": {}, "argument": set(), "store": set(), "other": set()}
    # ids: registers holding the id; scaled: register -> pc of the instruction that scaled it (a row offset)
    work = [(read_pc + 4, 0, frozenset({rv}), ())]
    seen = set()
    while work:
        pc, steps, ids, scaled_t = work.pop()
        if (pc, ids, scaled_t) in seen or pc not in code.words or steps > window or not (ids or scaled_t):
            continue
        seen.add((pc, ids, scaled_t))
        scaled = dict(scaled_t)
        w = code.words[pc]
        op = table_refs.decode(w)
        major, rd, ra, rb = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        xo = (w >> 1) & 0x3FF
        ids = set(ids)
        succ, target = _successors(w, pc)
        live = ids | set(scaled)
        touched = live & (op.reads | ({op.base} if op.base is not None else set()))
        new_ids, new_scaled = set(), {}
        if target is not None and not (SAVE_RESTORE[0] <= target < SAVE_RESTORE[1]) or (major == 19 and w & 1):
            if live & {3, 4, 5, 6, 7, 8, 9, 10}:
                uses["argument"].add(pc)
            ids -= VOLATILE
            for r in list(scaled):
                if r in VOLATILE:
                    del scaled[r]
        elif touched:
            if major == 31 and xo == EXTSB and rd in ids:  # extsb rA, rS
                uses["extsb"].add(pc)
                new_ids.add(ra)
            elif major == 21 and rd in ids and (w & 0xFFFE) == 0x07BE:  # rlwinm rA, rS, 0, 30, 31: already masked
                uses["extsb"].add(pc)
                new_ids.add(ra)
            elif major in (10, 11) or (major == 31 and xo in (0, 32)):
                uses["compare"].add(pc)
            elif major == 31 and xo == 444 and rd == rb:  # mr
                if rd in ids:
                    new_ids.add(ra)
                else:
                    new_scaled[ra] = scaled[rd]
            elif major in (21, 7) or (major == 31 and xo in (24, 235)):  # rlwinm mulli slw mullw: a stride
                dest = ra if major == 21 or (major == 31 and xo == 24) else rd
                src = rd if major == 21 or (major == 31 and xo == 24) else ra
                new_scaled[dest] = scaled.get(src, pc)
            elif major == 31 and xo in X_INDEXED:
                index_reg = ra if ra in live else rb
                other_reg = rb if ra in live else ra
                base = _base_constant(code, pc, other_reg) if not (ra in live and rb in live) else None
                if xo == 266 and base is None:  # add of two offsets: still a row offset
                    new_scaled[rd] = scaled.get(index_reg, pc)
                else:
                    uses["index"][pc] = (base, scaled.get(index_reg))
            elif op.kind == "store" or (major == 31 and xo in table_refs.X_STORES):
                uses["store"].add(pc)
            elif major == 14 and ra in ids:  # addi on the id (id - 1 for a range check)
                new_ids.add(rd)
            elif major == 21 and rd in ids and ((w >> 11) & 31) == 0:  # rlwinm without a shift: a mask
                new_ids.add(ra)
            else:
                uses["other"].add(pc)
        for r in op.writes:
            ids.discard(r)
            scaled.pop(r, None)
        ids |= new_ids
        scaled.update(new_scaled)
        if w == 0x4E800020:
            continue
        for s in succ:
            work.append((s, steps + 1, frozenset(ids), tuple(sorted(scaled.items()))))
    return uses


def run(sections: list[table_refs.Section], offsets: bool = False) -> dict:
    code = Code(sections)
    entries = function_entries(code)
    mw_param = set()
    for row in range(MOVE_ROWS):
        for col in (0, 4):
            f = code.data_word(MOVE_TABLE + 8 * row + col)
            if f and f in code.words:
                mw_param.add(f)
    reads: set[int] = set()
    accesses: set = set()
    done: dict[int, bool] = {}
    changed = True
    while changed:
        changed = False
        for entry in sorted(entries | mw_param):
            want = entry in mw_param
            if done.get(entry) == want:
                continue
            done[entry] = want
            r, calls, _ = analyse_function(code, entry, want)
            reads |= r
            for c in calls:
                if c not in mw_param:
                    mw_param.add(c)
                    changed = True
    if offsets:  # second pass: where MarioWork is accessed, in the functions that have it
        for entry in sorted(mw_param | {e for e in entries if e not in mw_param}):
            _, _, acc = analyse_function(code, entry, entry in mw_param, offsets=True, budget=400000)
            accesses |= acc
    # Classify like character_audit.py, per text section.
    rows = []
    for s in code.text:
        words = character_audit.words_of(s)
        for pc in sorted(a for a in reads if s.address <= a < s.address + 4 * len(words)):
            i = (pc - s.address) // 4
            kind, detail = character_audit.classify(words, s.address, i, (words[i] >> 21) & 31)
            rows.append({"address": pc, "kind": kind, "detail": detail})
    # Cross-check: lbz +0x38 then extsb of the same register, not explained.
    unexplained = []
    for pc, w in sorted(code.words.items()):
        if w >> 26 == 34 and (w & 0xFFFF) == CHAR_OFFSET and pc not in reads and ((w >> 16) & 31) != 1:
            rv = (w >> 21) & 31
            for n in (4, 8, 12):
                w2 = code.words.get(pc + n)
                if w2 is not None and w2 >> 26 == 31 and ((w2 >> 1) & 0x3FF) == 954 and ((w2 >> 21) & 31) == rv:
                    unexplained.append(pc)
                    break
    id_other = sorted({pc for pc, off, size, store in accesses
                       if off is not None and size > 0 and off <= CHAR_OFFSET < off + size and
                       not (size == 1 and not store and pc in reads)})
    unknown = sorted({pc for pc, off, size, store in accesses if off is None or size == -1})
    open_ranges = sorted({off for pc, off, size, store in accesses if off is not None and size in (-2, -3)})
    touched = set()
    for pc, off, size, store in accesses:
        if off is not None and size > 0:
            touched.update(range(off, off + size))
    return {"reads": rows, "unexplained": unexplained, "mw_functions": len(mw_param),
            "id_other_accesses": [f"0x{a:08X}" for a in id_other],
            "unknown_offset_accesses": [f"0x{a:08X}" for a in unknown],
            "touched_offsets": sorted(touched), "open_ranges": open_ranges}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--offsets", action="store_true",
                    help="also map every MarioWork offset the code touches (slow): writes to the id, free bytes")
    args = ap.parse_args(argv)
    result = run(table_refs.parse_dol(args.dol.read_bytes()), args.offsets)
    counts = Counter(r["kind"] for r in result["reads"])
    print(f"{len(result['reads'])} character reads in {result['mw_functions']} functions that get MarioWork: " +
          ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"{len(result['id_other_accesses'])} other accesses covering +0x38 (writes, word loads): " +
          " ".join(result["id_other_accesses"][:40]))
    print(f"{len(result['unknown_offset_accesses'])} MarioWork accesses with an unknown offset")
    print(f"{len(result['unexplained'])} lbz +0x38 / extsb not explained: " +
          " ".join(f"0x{a:08X}" for a in result["unexplained"][:40]))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
