#!/usr/bin/env python3
"""Decode M14 fail-closed EXI diagnostic lines without changing guest behavior.

This is an investigation aid. Recognized DATA words are source/navigation hints;
backend behavior is added only after a live path has been independently proven.
"""

import argparse
import pathlib
import re
import sys
from typing import List, Optional, Tuple

_DIAG_RE = re.compile(
    r"M14 EXI(?P<channel>\d+) rejected "
    r"control=0x(?P<control>[0-9A-Fa-f]{1,8}) "
    r"csr=0x(?P<csr>[0-9A-Fa-f]{1,8}) "
    r"data=0x(?P<data>[0-9A-Fa-f]{1,8}) "
    r"result=(?P<result>-?\d+)"
)

_KNOWN_DATA = {
    (1, 0x20000100): "matching RVL OS SRAM read beginning at SRAM offset 4 (already bounded in core)",
    (1, 0x21000800): "matching RVL __OSGetRTCFlags command; normally followed by immediate read4",
    (1, 0xA1000800): "matching RVL __OSClearRTCFlags command; normally followed by write4 zero",
    (1, 0x30000100): "matching RVL EUART QueueLength command; normally followed by immediate read4",
    (1, 0xB0000100): "matching RVL EUART transmit command; following write4 values are payload",
    (1, 0x07F3C000): "runtime-proven RVL OSFont ANSI ReadROM command: internal ROM offset 0x001FCF00 (hosted by dev17)",
}


def _selected_device(csr: int) -> Tuple[Optional[int], int]:
    raw = (csr >> 7) & 0x7
    if raw == 1:
        return 0, raw
    if raw == 2:
        return 1, raw
    if raw == 4:
        return 2, raw
    return None, raw


def _transfer(control: int) -> Tuple[bool, bool, int, int, str]:
    start = bool(control & 0x1)
    dma = bool(control & 0x2)
    rw = (control >> 2) & 0x3
    length = ((control >> 4) & 0x3) + 1
    rw_name = {0: "read", 1: "write"}.get(rw, "other({})".format(rw))
    return start, dma, rw, length, rw_name


def decode(channel: int, control: int, csr: int, data: int, result: int) -> List[str]:
    device, raw_select = _selected_device(csr)
    freq = (csr >> 4) & 0x7
    start, dma, _rw, length, rw_name = _transfer(control)

    device_text = "dev{}".format(device) if device is not None else "unresolved-select(0b{:03b})".format(raw_select)
    mode = "DMA" if dma else "immediate"
    lines = [
        "EXI{}: {}, freq={}, {} {}{}, TSTART={}, CSR=0x{:08X}, DATA=0x{:08X}, CR=0x{:08X}, result={}".format(
            channel,
            device_text,
            freq,
            mode,
            rw_name,
            "" if dma else str(length),
            int(start),
            csr,
            data,
            control,
            result,
        )
    ]

    hint = _KNOWN_DATA.get((device, data)) if device is not None else None
    if data == 0x20011300:
        hint = (
            "matching RVL EXIUart ProbeBarnacle command; selected channel/device and live call path "
            "must still be proven before adding behavior"
        )
    if hint is not None:
        lines.append("hint: {}".format(hint))
    else:
        lines.append("hint: no exact command match in the current early-boot lookup table")

    if device == 1 and data == 0x07F3C000 and control == 0x35:
        lines.append("status: this exact boundary was proven by the dev16 player trace and is intentionally hosted in dev17")
    else:
        lines.append("policy: unknown EXI behavior remains fail-closed")
    return lines


def _parse_latest(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    match = None
    for match in _DIAG_RE.finditer(text):
        pass
    if match is None:
        return None
    return (
        int(match.group("channel"), 10),
        int(match.group("control"), 16),
        int(match.group("csr"), 16),
        int(match.group("data"), 16),
        int(match.group("result"), 10),
    )


def _self_test() -> None:
    rtc = decode(0, 0x35, 0x100, 0x21000800, -3)
    assert "dev1" in rtc[0] and "immediate write4" in rtc[0]
    assert "__OSGetRTCFlags" in rtc[1]

    barnacle = decode(0, 0x35, 0x200, 0x20011300, -3)
    assert "dev2" in barnacle[0]
    assert "ProbeBarnacle" in barnacle[1]
    assert "must still be proven" in barnacle[1]

    font = decode(0, 0x35, 0x130, 0x07F3C000, -3)
    assert "dev1" in font[0] and "freq=3" in font[0]
    assert "0x001FCF00" in font[1]
    assert "hosted in dev17" in font[2]

    unknown = decode(0, 0x35, 0x80, 0x12345678, -3)
    assert "dev0" in unknown[0]
    assert "no exact command match" in unknown[1]

    parsed = _parse_latest(
        "M14 EXI0 rejected control=0x00000035 csr=0x00000080 data=0x11111111 result=-3\n"
        "M14 EXI0 rejected control=0x00000035 csr=0x00000130 data=0x07F3C000 result=-3\n"
    )
    assert parsed == (0, 0x35, 0x130, 0x07F3C000, -3)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=pathlib.Path, help="launch.log to inspect")
    parser.add_argument("--self-test", action="store_true", help="run decoder regression checks")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        print("M14 EXI diagnostic decoder: PASS")
        return 0

    if args.log is None:
        parser.error("provide --log or --self-test")

    try:
        text = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print("M14 EXI diagnostic decoder: could not read {}: {}".format(args.log, exc), file=sys.stderr)
        return 2

    values = _parse_latest(text)
    if values is None:
        print("M14 EXI diagnostic decoder: no fail-closed EXI diagnostic line found")
        return 0

    print("M14 EXI diagnostic decode:")
    for line in decode(*values):
        print("  {}".format(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
