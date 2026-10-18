"""character_reads.py on synthetic code: MarioWork found from marioGetPtr,
kept through mr and a callee-saved register across a call, passed to a
callee (whose r3 then is MarioWork), and the id's uses classified; a read
off another pointer is not counted."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import character_reads as cr  # noqa: E402
import table_refs  # noqa: E402

BASE = 0x80100000


def bl(at: int, target: int) -> int:
    return 0x48000001 | ((target - at) & 0x03FFFFFC)


def build() -> list[table_refs.Section]:
    f = BASE           # caller
    g = BASE + 0x100   # callee: gets MarioWork in r3
    code = {
        f + 0x00: 0x9421FFF0,                   # stwu r1, -16(r1)
        f + 0x04: bl(f + 0x04, cr.MARIO_GET_PTR),
        f + 0x08: 0x7C7F1B78,                   # mr r31, r3
        f + 0x0C: bl(f + 0x0C, 0x80200000),     # some call: r31 survives
        f + 0x10: 0x881F0038,                   # lbz r0, 0x38(r31)       <- read 1
        f + 0x14: 0x7C000774,                   # extsb r0, r0
        f + 0x18: 0x2C000002,                   # cmpwi r0, 2
        f + 0x1C: 0x7FE3FB78,                   # mr r3, r31
        f + 0x20: bl(f + 0x20, g),
        f + 0x24: 0x88640038,                   # lbz r3, 0x38(r4)  (r4: not MarioWork)
        f + 0x28: 0x4E800020,                   # blr
        g + 0x00: 0x88A30038,                   # lbz r5, 0x38(r3)        <- read 2
        g + 0x04: 0x7CA50774,                   # extsb r5, r5
        g + 0x08: 0x54A5103A,                   # rlwinm r5, r5, 2, 0, 29 (x4)
        g + 0x0C: 0x3C80803E,                   # lis r4, 0x803E
        g + 0x10: 0x38848148,                   # addi r4, r4, -0x7EB8 -> 0x803D8148
        g + 0x14: 0x7C64282E,                   # lwzx r3, r4, r5        (index)
        g + 0x18: 0x4E800020,                   # blr
    }
    text = bytearray(0x200)
    for at, w in code.items():
        struct.pack_into(">I", text, at - BASE, w)
    get_ptr = bytearray(8)
    struct.pack_into(">II", get_ptr, 0, 0x38600000, 0x4E800020)
    other = bytearray(4)
    struct.pack_into(">I", other, 0, 0x4E800020)
    return [table_refs.Section(BASE, bytes(text), True),
            table_refs.Section(cr.MARIO_GET_PTR, bytes(get_ptr), True),
            table_refs.Section(0x80200000, bytes(other), True),
            table_refs.Section(0x80400000, b"\0" * 8, False)]


def test_reads_and_uses() -> None:
    secs = build()
    result = cr.run(secs)
    found = {r["address"] for r in result["reads"]}
    assert found == {BASE + 0x10, BASE + 0x100}, [hex(a) for a in found]
    code = cr.Code(secs)
    u1 = cr.value_uses(code, BASE + 0x10)
    assert u1["extsb"] == {BASE + 0x14} and u1["compare"] == {BASE + 0x18}
    u2 = cr.value_uses(code, BASE + 0x100)
    (pc, (table, scale)), = u2["index"].items()
    assert pc == BASE + 0x114 and table == 0x803D8148 and scale == BASE + 0x108


if __name__ == "__main__":
    test_reads_and_uses()
    print("ok test_reads_and_uses")
