#!/usr/bin/env python3
"""Game-data-free checks for M14 legacy bridge control-instruction decoding."""

from __future__ import annotations


def x_form(primary: int, reg: int, xo: int) -> int:
    return ((primary & 0x3F) << 26) | ((reg & 0x1F) << 21) | ((xo & 0x3FF) << 1)


def xfx_form(primary: int, reg: int, field: int, xo: int) -> int:
    # SPR/TBR fields encode their low five bits in instruction bits 16..20 and
    # high five bits in 11..15; bridge_decode_spr reverses that split.
    encoded = ((field & 0x1F) << 5) | ((field >> 5) & 0x1F)
    return x_form(primary, reg, xo) | ((encoded & 0x3FF) << 11)


def decode(word: int) -> tuple[int, int, int, int]:
    primary = word >> 26
    reg = (word >> 21) & 0x1F
    xo = (word >> 1) & 0x3FF
    encoded = (word >> 11) & 0x3FF
    field = ((encoded & 0x1F) << 5) | ((encoded >> 5) & 0x1F)
    return primary, reg, xo, field


def main() -> int:
    cases = [
        ("mfmsr", x_form(31, 7, 83), (31, 7, 83, 0)),
        ("mtmsr", x_form(31, 9, 146), (31, 9, 146, 0)),
        ("mftbl", xfx_form(31, 5, 268, 371), (31, 5, 371, 268)),
        ("mftbu", xfx_form(31, 6, 269, 371), (31, 6, 371, 269)),
        ("sync", x_form(31, 0, 598), (31, 0, 598, 0)),
        ("eieio", x_form(31, 0, 854), (31, 0, 854, 0)),
        ("isync", x_form(19, 0, 150), (19, 0, 150, 0)),
    ]
    for name, word, expected in cases:
        actual = decode(word)
        if actual != expected:
            raise AssertionError(f"{name}: word=0x{word:08X} expected={expected} got={actual}")
    print(f"control decode: PASS ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
