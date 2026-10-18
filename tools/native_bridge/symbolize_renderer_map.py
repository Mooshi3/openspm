#!/usr/bin/env python3
"""Resolve OpenSPM renderer RVAs from launch.log using the MSVC linker map."""

from __future__ import annotations

import argparse
import bisect
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

MODULE_NAME = "openspm_renderer.dll"
OFFSET_RE = re.compile(r"openspm_renderer\.dll\+0x([0-9A-Fa-f]+)", re.IGNORECASE)
PREFERRED_RE = re.compile(r"Preferred load address is\s+([0-9A-Fa-f]+)", re.IGNORECASE)
SYMBOL_RE = re.compile(
    r"^\s*[0-9A-Fa-f]+:[0-9A-Fa-f]+\s+(\S+)\s+([0-9A-Fa-f]{8,16})(?:\s|$)"
)


@dataclass(frozen=True)
class Symbol:
    rva: int
    name: str


def parse_map(path: str) -> list[Symbol]:
    preferred: int | None = None
    rows: list[tuple[int, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if preferred is None:
                match = PREFERRED_RE.search(line)
                if match:
                    preferred = int(match.group(1), 16)
            match = SYMBOL_RE.match(line)
            if match:
                rows.append((int(match.group(2), 16), match.group(1)))

    if preferred is None:
        raise RuntimeError("MSVC map is missing its preferred load address")

    symbols: dict[int, str] = {}
    for absolute, name in rows:
        rva = absolute - preferred
        if 0 <= rva <= 0xFFFFFFFF:
            symbols.setdefault(rva, name)
    if not symbols:
        raise RuntimeError("MSVC map contained no public symbol RVAs")
    return [Symbol(rva, symbols[rva]) for rva in sorted(symbols)]


def demangle(name: str) -> str:
    if not name.startswith("?"):
        return name
    tool = shutil.which("undname.exe") or shutil.which("undname")
    if not tool:
        return name
    try:
        result = subprocess.run(
            [tool, name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return name
    for line in result.stdout.splitlines():
        if " == " in line:
            candidate = line.split(" == ", 1)[1].strip()
            if candidate:
                return candidate
    return name


def collect_offsets(log_path: str) -> list[int]:
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    values: list[int] = []
    seen: set[int] = set()
    for match in OFFSET_RE.finditer(text):
        value = int(match.group(1), 16)
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def symbolize(symbols: list[Symbol], offsets: list[int]) -> list[str]:
    rvas = [symbol.rva for symbol in symbols]
    output: list[str] = []
    for offset in offsets:
        index = bisect.bisect_right(rvas, offset) - 1
        if index < 0:
            output.append(
                f"[OpenSPMSymbols] {MODULE_NAME}+0x{offset:X} -> before first mapped public symbol"
            )
            continue
        symbol = symbols[index]
        delta = offset - symbol.rva
        name = demangle(symbol.name)
        output.append(
            f"[OpenSPMSymbols] {MODULE_NAME}+0x{offset:X} -> {name}+0x{delta:X}"
        )
    return output


def self_test() -> int:
    sample_map = """
 Preferred load address is 0000000180000000
  Address         Publics by Value              Rva+Base               Lib:Object
 0001:00000000       ?alpha@@YAXXZ               0000000180001000 f   demo.obj
 0001:00000040       ?beta@@YAXXZ                0000000180001040 f   demo.obj
"""
    preferred = PREFERRED_RE.search(sample_map)
    rows = [SYMBOL_RE.match(line) for line in sample_map.splitlines()]
    parsed = [row for row in rows if row]
    if preferred is None or len(parsed) != 2:
        print("[OpenSPMSymbols] self-test FAIL: map parser", file=sys.stderr)
        return 2
    base = int(preferred.group(1), 16)
    symbols = [
        Symbol(int(row.group(2), 16) - base, row.group(1)) for row in parsed
    ]
    line = symbolize(symbols, [0x1048])[0]
    if "+0x8" not in line:
        print("[OpenSPMSymbols] self-test FAIL: nearest-symbol lookup", file=sys.stderr)
        return 2
    print("[OpenSPMSymbols] self-test PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--log")
    parser.add_argument("--map", dest="map_path")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.log or not args.map_path:
        parser.error("--log and --map are required")

    if not os.path.exists(args.map_path):
        print(f"[OpenSPMSymbols] renderer map missing: {args.map_path}")
        return 2
    if not os.path.exists(args.log):
        print(f"[OpenSPMSymbols] launch log missing: {args.log}")
        return 2

    try:
        symbols = parse_map(args.map_path)
        offsets = collect_offsets(args.log)
    except (OSError, RuntimeError) as exc:
        print(f"[OpenSPMSymbols] symbolization failed: {exc}")
        return 2

    if not offsets:
        print("[OpenSPMSymbols] no openspm_renderer.dll RVAs found in launch log")
        return 0

    print(
        f"[OpenSPMSymbols] resolving {len(offsets)} renderer RVAs against {len(symbols)} public symbols"
    )
    for line in symbolize(symbols, offsets):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
