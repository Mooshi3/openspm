"""table_refs.py on synthetic code: a clean table relocates, a lis register
shared with other data, an opaque use or a data pointer into the table each
refuse, and recipes only change the lis half."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import table_refs  # noqa: E402

TABLE, SIZE = 0x803B7E78, 0x100
LIS_R3 = 0x3C60803B  # lis r3, 0x803B
ADDI_R3 = 0x38637E78  # addi r3, r3, 0x7E78
LWZ_R4_R3 = 0x80837E80  # lwz r4, 0x7E80(r3) (inside the table)
LWZ_R4_OTHER = 0x80830010  # lwz r4, 0x10(r3) -> 0x803B0010 (outside)
ADD_R5_R3 = 0x7CA31A14  # add r5, r3, r3 (opaque use)
BLR = 0x4E800020


def dol(code: list[int], data: list[int] = ()) -> list[table_refs.Section]:
    text = b"".join(struct.pack(">I", w) for w in code)
    blob = b"".join(struct.pack(">I", w) for w in data) or b"\0" * 4
    return [table_refs.Section(0x80004000, text, True), table_refs.Section(0x80400000, blob, False)]


def run(code, data=()):
    return table_refs.find(dol(code, data), TABLE, SIZE)


def test_clean() -> None:
    r = run([LIS_R3, ADDI_R3, BLR, LIS_R3, LWZ_R4_R3, BLR])
    assert len(r["patches"]) == 2 and not r["problems"] and not r["data_pointers"]
    (a, b) = table_refs.recipes(r, TABLE, 0x93E07E78, "t")
    assert a["replacement"] == "0x3C6093E0" and b["address"] == "0x8000400C"


def test_shared_register_refuses() -> None:
    r = run([LIS_R3, LWZ_R4_OTHER, ADDI_R3, BLR])
    assert not r["patches"] and r["problems"] and "something else" in r["problems"][0]["reason"]


def test_opaque_use_refuses() -> None:
    r = run([LIS_R3, ADD_R5_R3, ADDI_R3, BLR])
    assert not r["patches"] and r["problems"]


def test_redefinition_ends_the_range() -> None:
    li_r3 = 0x38600000  # li r3, 0
    r = run([LIS_R3, ADDI_R3, li_r3, ADD_R5_R3, BLR])
    assert len(r["patches"]) == 1 and not r["problems"]


def test_derived_pointer_outside_refuses() -> None:
    # lis r31; addi r31, r31, table; addi r4, r31, 0x200 (past the table): the
    # second address comes from the same lis, so it would move too.
    lis_r31, addi_r31 = 0x3FE0803B, 0x3BFF7E78
    addi_r4_out = 0x389F0200
    r = run([lis_r31, addi_r31, addi_r4_out, BLR])
    assert not r["patches"] and r["problems"]


def test_indexed_row_inside() -> None:
    # lis r3; addi r3, r3, table; add r3, r3, r5 (row index); lwz r4, 4(r3)
    add_r3_r3_r5 = 0x7C632A14
    lwz_r4_4_r3 = 0x80830004
    r = run([LIS_R3, ADDI_R3, add_r3_r3_r5, lwz_r4_4_r3, 0x38600000, BLR])
    assert len(r["patches"]) == 1 and not r["problems"]


def test_data_pointer_found() -> None:
    r = run([LIS_R3, ADDI_R3, BLR], data=[TABLE + 0x2C])
    assert r["data_pointers"] == ["0x80400000"]


def test_low_half_must_match() -> None:
    r = run([LIS_R3, ADDI_R3, BLR])
    try:
        table_refs.recipes(r, TABLE, 0x93E00000, "t")
    except ValueError:
        return
    raise AssertionError("a different low half must be refused")


if __name__ == "__main__":
    for fn in (test_clean, test_shared_register_refuses, test_opaque_use_refuses, test_redefinition_ends_the_range,
               test_derived_pointer_outside_refuses, test_indexed_row_inside, test_data_pointer_found,
               test_low_half_must_match):
        fn()
        print("ok", fn.__name__)
