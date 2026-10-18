#!/usr/bin/env python3
"""Derive LLVM callsite seams for OpenSPM-hosted R8PE01 services.

Generated-C normalizes every direct BL to a semantic host target back into a
side exit, even if DolRecomp would otherwise lower it as a same-chunk/direct
native call. LLVM objects need the equivalent guarantee at compiler partition
boundaries: a direct native call must not enter Nintendo's Wii/IOS body and
silently bypass OpenSPM's hostcall service.

This scanner consumes only player-local code bytes. It scans the clean main DOL
and the already-relocated relF executable image for linked branch instructions
whose targets appear in state/m14-host-targets.json. It emits call/return
partition rows plus a generated header describing each exact callsite. No game
bytes are persisted in source control.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_relf_codegen_dol as relf_builder  # noqa: E402
import direct_calls  # noqa: E402
import relf_input  # noqa: E402

REQUIRED_BOOT_TARGETS = {0x8029A500}  # NANDCheckAsync capacity completion


@dataclass(frozen=True, order=True)
class HostCallSeam:
    call: int
    target: int
    return_pc: int


def be32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def branch_target(raw: int, pc: int) -> int | None:
    """Return a PPC linked-branch target, or None for non-BL instructions."""
    if raw >> 26 != 18 or (raw & 1) == 0:
        return None
    disp = raw & 0x03FFFFFC
    if disp & 0x02000000:
        disp -= 0x04000000
    if raw & 2:  # AA
        return disp & 0xFFFFFFFF
    return (pc + disp) & 0xFFFFFFFF


def dol_text_ranges(data: bytes) -> list[tuple[int, bytes]]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    ranges: list[tuple[int, bytes]] = []
    for index in range(7):
        file_off = be32(data, 0x00 + index * 4)
        address = be32(data, 0x48 + index * 4)
        size = be32(data, 0x90 + index * 4)
        if size == 0:
            continue
        if (address & 3) or (size & 3):
            raise RuntimeError(f"DOL text{index} is not instruction aligned")
        if file_off > len(data) or size > len(data) - file_off:
            raise RuntimeError(f"DOL text{index} is outside the file")
        ranges.append((address, data[file_off:file_off + size]))
    return ranges


def scan_ranges(ranges: list[tuple[int, bytes]], hosted: set[int]) -> list[HostCallSeam]:
    seams: list[HostCallSeam] = []
    seen_calls: set[int] = set()
    for base, code in ranges:
        if len(code) & 3:
            raise RuntimeError(f"code range 0x{base:08X} is not instruction aligned")
        for off in range(0, len(code), 4):
            pc = (base + off) & 0xFFFFFFFF
            target = branch_target(be32(code, off), pc)
            if target not in hosted:
                continue
            if pc in seen_calls:
                raise RuntimeError(f"duplicate hosted callsite 0x{pc:08X}")
            seen_calls.add(pc)
            seams.append(HostCallSeam(pc, target, (pc + 4) & 0xFFFFFFFF))
    seams.sort()
    return seams


def scan(main_dol: Path, relf: relf_input.RelFInput) -> tuple[list[HostCallSeam], dict[int, int]]:
    hosted = direct_calls.load_host_targets()
    main_bytes = main_dol.read_bytes()

    rel_bytes = bytearray(relf.data)
    sections, meta = relf_builder.parse_sections(rel_bytes)
    relf_builder.relocate(rel_bytes, sections, meta)
    execute = sections[relf_builder.REL_EXEC_INDEX]
    rel_exec = bytes(rel_bytes[
        execute.file_offset:execute.file_offset + execute.size
    ])

    seams = scan_ranges(
        dol_text_ranges(main_bytes) + [(execute.address, rel_exec)], hosted
    )
    counts: dict[int, int] = {}
    for seam in seams:
        counts[seam.target] = counts.get(seam.target, 0) + 1

    missing = sorted(target for target in REQUIRED_BOOT_TARGETS if counts.get(target, 0) == 0)
    if missing:
        raise RuntimeError(
            "verified player code has no direct callsite for required boot host target(s): "
            + ",".join(f"0x{target:08X}" for target in missing)
        )
    return seams, counts


def write_header(path: Path, seams: list[HostCallSeam]) -> None:
    rows = "\n".join(
        "    { UINT32_C(0x%08X), UINT32_C(0x%08X), UINT32_C(0x%08X) }," %
        (seam.call, seam.target, seam.return_pc)
        for seam in seams
    )
    text = (
        "#ifndef OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS_H\n"
        "#define OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS_H\n\n"
        "#include <stdint.h>\n\n"
        "typedef struct openspm_local_llvm_hostcall_seam {\n"
        "    uint32_t call_pc;\n"
        "    uint32_t target;\n"
        "    uint32_t return_pc;\n"
        "} openspm_local_llvm_hostcall_seam;\n\n"
        f"#define OPENSPM_LOCAL_LLVM_HOSTCALL_SEAM_COUNT UINT32_C({len(seams)})\n"
        f"static const openspm_local_llvm_hostcall_seam OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[{len(seams)}] = {{\n"
        + rows + "\n};\n\n#endif\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def append_map(path: Path, seams: list[HostCallSeam]) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    addresses: dict[int, str] = {}
    for line in existing.splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) >= 3:
            try:
                addresses[int(fields[0], 16)] = fields[2]
            except ValueError:
                pass

    added: list[tuple[int, str]] = []
    for index, seam in enumerate(seams):
        for address, label in (
            (seam.call, f"openspm_hostcall_call_{index}_{seam.target:08X}"),
            (seam.return_pc, f"openspm_hostcall_return_{index}_{seam.target:08X}"),
        ):
            if address not in addresses:
                addresses[address] = label
                added.append((address, label))

    rows = [(address, name) for address, name in addresses.items()]
    rows.sort()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{address:08X} 0 {name}\n" for address, name in rows),
        encoding="utf-8", newline="\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-dol", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--relf", type=Path)
    source.add_argument("--game-dir", type=Path)
    parser.add_argument("--header", required=True, type=Path)
    parser.add_argument("--map", dest="symbol_map", required=True, type=Path)
    args = parser.parse_args()
    try:
        loaded = (
            relf_input.load_relf(args.relf.resolve())
            if args.relf
            else relf_input.load_from_game(args.game_dir.resolve())
        )
        seams, counts = scan(args.main_dol.resolve(), loaded)
        write_header(args.header.resolve(), seams)
        append_map(args.symbol_map.resolve(), seams)
        nand_count = counts.get(0x8029A500, 0)
        print(
            "M14 LLVM hosted-call seams derived: "
            f"calls={len(seams)} targets={len(counts)} nand_check_calls={nand_count}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: LLVM hosted-call seam derivation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
