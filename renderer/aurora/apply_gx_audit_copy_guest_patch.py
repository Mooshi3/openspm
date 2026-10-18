#!/usr/bin/env python3
"""OpenSPM GX capture: log each EFB copy's guest destination address.

The deep-frame capture (apply_seqload_deep_frame_patch.py) logs every EFB copy
with its host destination pointer only. To follow a copy into the textures
that sample it and into the game's snapshot buffers (flip card, star door),
the analyzer (tools/diag/analyze_gx_capture.py --passes) also needs the guest
address the game programmed (BP 0x4B, address >> 5). This appends
guest=0x........ to that log line. Diagnostic only: the line is printed only
while a capture is armed.

Runs after the deep-frame patch (which owns the line and never rewrites it
once applied). Exact/idempotent; fails closed on unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path("lib/gx/command_processor.cpp")

OLD = ('  Log.info("M14 GX deep EFB copy value=0x{:06X} kind={} clear={} src={},{},{}x{} dst=0x{:X} '
       'dst_size={}x{} fmt={} pixel_fmt={}",\n')
NEW = ('  Log.info("M14 GX deep EFB copy value=0x{:06X} kind={} clear={} src={},{},{}x{} dst=0x{:X} '
       'dst_size={}x{} fmt={} pixel_fmt={} guest=0x{:08X}",\n')
ARGS_OLD = ("           state.texCopyDstWidth, state.texCopyDstHeight, static_cast<u32>(state.texCopyFmt),\n"
            "           static_cast<u32>(state.pixelFmt));\n")
ARGS_NEW = ("           state.texCopyDstWidth, state.texCopyDstHeight, static_cast<u32>(state.texCopyFmt),\n"
            "           static_cast<u32>(state.pixelFmt), (state.bpRegCache[0x4B] & 0x00FFFFFFu) << 5);\n")


def transform(root: Path, check_only: bool = False) -> str:
    path = root / TARGET
    text = path.read_text(encoding="utf-8")
    if NEW in text:
        if text.count(NEW) != 1:
            raise RuntimeError("guest copy log present more than once")
        tail = text.split(NEW, 1)[1]
        if not tail.split(";\n", 1)[0].endswith("(state.bpRegCache[0x4B] & 0x00FFFFFFu) << 5)"):
            raise RuntimeError("guest copy log format present without its argument")
        return "already active"
    if text.count(OLD) != 1:
        raise RuntimeError(f"expected one deep EFB copy log line, found {text.count(OLD)}")
    block_old = OLD + text.split(OLD, 1)[1].split(ARGS_OLD, 1)[0] + ARGS_OLD
    if ARGS_OLD not in text.split(OLD, 1)[1] or block_old.count(";\n") != 1:
        raise RuntimeError("deep EFB copy log arguments drifted")
    block_new = NEW + block_old[len(OLD):-len(ARGS_OLD)] + ARGS_NEW
    if check_only:
        return "pending"
    path.write_text(text.replace(block_old, block_new, 1), encoding="utf-8", newline="\n")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.source.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora GX capture copy guest-address adaptation failed: {exc}")
        return 4
    print("Aurora GX capture copy guest address " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
