#!/usr/bin/env python3
"""List every place the game reads the current character (MarioWork+0x38) and
what it does with it: the groundwork for real character ids 4+ (docs/CONTENT-API.md).

A read is `lbz rV, 0x38(rM)` where rM holds MarioWork (from marioGetPtr
0x8012191C, or lis/addi 0x804CEE58). Each read is classified by the first uses
of rV:

  compare   cmpwi/cmplwi rV, n   - a per-character check ("is this Luigi")
  index     shifted/multiplied, then used as an offset (lwzx/add/...) - a
            per-character table; the table base is shown when it is a lis/addi
  argument  moved into r3..r10 before a call - passed to a function
  store     written somewhere (copied)
  other     anything else (reported with the instruction)

Addresses only; no game code is printed. Usage:
  character_audit.py --dol codegen.dol [--markdown out.md]
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import table_refs  # noqa: E402

MARIO_GET_PTR = 0x8012191C
MARIO_WORK = 0x804CEE58
CHAR_OFFSET = 0x38


def words_of(sec: table_refs.Section) -> list[int]:
    return list(struct.unpack(f">{len(sec.data) // 4}I", sec.data[: len(sec.data) // 4 * 4]))


def is_mario_base(words: list[int], base_address: int, i: int, reg: int) -> bool:
    """Does `reg` hold MarioWork at instruction i? (backward scan, 40 instructions)"""
    for k in range(i - 1, max(-1, i - 40), -1):
        w = words[k]
        op = table_refs.decode(w)
        if reg in op.writes:
            if op.kind == "addi" and ((w >> 16) & 31) != 0:
                src = (w >> 16) & 31
                # lis src, 0x804D ; addi reg, src, -4520
                for j in range(k - 1, max(-1, k - 12), -1):
                    o2 = table_refs.decode(words[j])
                    if src in o2.writes:
                        return o2.kind == "lis" and ((o2.imm << 16) + op.disp) & 0xFFFFFFFF == MARIO_WORK
                return False
            if w >> 26 == 31 and ((w >> 1) & 0x3FF) == 444 and ((w >> 21) & 31) == ((w >> 11) & 31):  # mr reg, rS
                return is_mario_base(words, base_address, k, (w >> 21) & 31)
            return False
        if w >> 26 == 18 and w & 1 and reg == 3:  # bl: r3 is its return value
            target = (base_address + 4 * k + (((w & 0x03FFFFFC) ^ 0x02000000) - 0x02000000)) & 0xFFFFFFFF
            return target == MARIO_GET_PTR
    return False


def classify(words: list[int], base_address: int, i: int, value: int) -> tuple[str, str]:
    regs = {value}
    shifted = False
    for k in range(i + 1, min(len(words), i + 24)):
        w = words[k]
        op = table_refs.decode(w)
        at = f"0x{base_address + 4 * k:08X}"
        uses = regs & (op.reads | ({op.base} if op.base is not None else set()))
        if uses:
            prim = w >> 26
            if prim in (10, 11):  # cmpli / cmpi
                return "compare", f"{at} with {table_refs.simm(w & 0xFFFF)}"
            if prim == 31 and ((w >> 1) & 0x3FF) in (0, 32):
                return "compare", f"{at} with a register"
            if prim == 31 and ((w >> 1) & 0x3FF) == 954:  # extsb
                regs |= op.writes
                continue
            if prim in (21, 7):  # rlwinm / mulli: scaled -> table index
                regs = (regs - op.writes) | op.writes
                shifted = True
                continue
            if op.kind == "store" or (prim == 31 and ((w >> 1) & 0x3FF) in table_refs.X_STORES):
                if shifted and prim == 31:
                    return "index", f"{at} (indexed store)"
                return "store", at
            if prim == 31 and ((w >> 1) & 0x3FF) in (266, 23, 87, 279, 343, 535, 599):  # add / lwzx / lbzx / lhzx / lhax / lfsx / lfdx
                return ("index" if shifted else "index?"), at + _table_base(words, base_address, k, regs)
            if prim == 31 and ((w >> 1) & 0x3FF) == 444 and op.writes:  # mr
                (dst,) = tuple(op.writes)
                if 3 <= dst <= 10:
                    return "argument", f"{at} -> r{dst}"
                regs |= op.writes
                continue
            return "other", f"{at} word 0x{w:08X}"
        if regs & op.writes:
            regs -= op.writes
            if not regs:
                return "other", f"{at} (value overwritten)"
        if w >> 26 == 18 and w & 1 and 3 in regs:
            return "argument", f"0x{base_address + 4 * k:08X} (in r3)"
        if op.stop:
            break
    return "other", "no use found"


def _table_base(words: list[int], base_address: int, k: int, value_regs: set[int]) -> str:
    w = words[k]
    other = ({(w >> 16) & 31, (w >> 11) & 31} - value_regs) or set()
    for reg in other:
        for j in range(k - 1, max(-1, k - 30), -1):
            op = table_refs.decode(words[j])
            if reg in op.writes:
                if op.kind == "addi":
                    for m in range(j - 1, max(-1, j - 12), -1):
                        o2 = table_refs.decode(words[m])
                        if op.base in o2.writes:
                            if o2.kind == "lis":
                                return f" table 0x{((o2.imm << 16) + op.disp) & 0xFFFFFFFF:08X}"
                            break
                break
    return ""


def audit(sections: list[table_refs.Section]) -> list[dict]:
    out = []
    for sec in sections:
        if not sec.text:
            continue
        words = words_of(sec)
        for i, w in enumerate(words):
            if w >> 26 != 34 or (w & 0xFFFF) != CHAR_OFFSET:  # lbz rV, 0x38(rM)
                continue
            value, base = (w >> 21) & 31, (w >> 16) & 31
            if not is_mario_base(words, sec.address, i, base):
                continue
            kind, detail = classify(words, sec.address, i, value)
            out.append({"address": sec.address + 4 * i, "kind": kind, "detail": detail})
    return out


def markdown(rows: list[dict]) -> str:
    counts = Counter(r["kind"] for r in rows)
    lines = [
        "# Character id audit (generated)",
        "",
        "Every read of the current character (MarioWork+0x38) in R8PE01 rev 2 main.dol + relF,",
        "from `tools/content/character_audit.py`. Kinds: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "| read at | kind | detail |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| 0x{r['address']:08X} | {r['kind']} | {r['detail']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path)
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args(argv)
    rows = audit(table_refs.parse_dol(args.dol.read_bytes()))
    counts = Counter(r["kind"] for r in rows)
    print(f"{len(rows)} character reads: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if args.markdown:
        args.markdown.write_text(markdown(rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
