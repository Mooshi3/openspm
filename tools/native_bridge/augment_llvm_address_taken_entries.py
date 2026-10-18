#!/usr/bin/env python3
"""Promote address-taken Broadway code pointers to LLVM native entry points.

Why this exists
---------------
DolRecomp's LLVM entry dispatcher only jumps directly to region leaders. An
aligned PC that lands in the middle of a native region goes through the backend
cold-entry fallback loop instead. That fallback is valid in a runtime which can
interpret one arbitrary PPC instruction.

OpenSPM intentionally has no general PPC interpreter. Its instruction_fallback
callback handles only the privileged/cache/order operations needed by the native
bridge and otherwise fails closed with PC unchanged. Player runs therefore
proved a deterministic class of stalls: a bctrl dispatcher reaches a legitimate
address-taken function which is not already a region leader, the cold-entry
fallback cannot execute the first ordinary instruction, and PC/CTR remain
stationary until that exact address is manually partitioned.

The durable native-only repair is to discover address-taken code before LLVM
codegen. Every aligned 32-bit value stored in a DOL data section which points
into an executable DOL text section is a conservative native-entry candidate.
False positives are harmless extra region boundaries; existing semantic map
entries always win and are never replaced.

The latest measured frontier (0x800332A8 from caller 0x80038960) is also added
explicitly after the player's DOL proves the caller is a linked BCCTR and the
target is executable. This guarantees the measured dev57 regression test even
if that particular function pointer is constructed dynamically rather than
stored literally in a DOL data section.

No Nintendo bytes or assets are written to source control. Only addresses and
synthetic entry names are persisted in the player-local partition map.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct
import tempfile

DEV57_DISPATCH_CALL = 0x80038960
DEV57_DISPATCH_RETURN = DEV57_DISPATCH_CALL + 4
DEV57_MEASURED_TARGET = 0x800332A8
DEV57_MEASURED_NAME = "openspm_dev57_measured_indirect_target_800332a8"
MAX_ADDRESS_TAKEN_ENTRIES = 32768


@dataclass(frozen=True)
class DolSection:
    file_offset: int
    address: int
    size: int


@dataclass(frozen=True)
class AddressTakenStats:
    dispatcher_raw: int
    data_words_scanned: int
    candidates: int
    added: int
    already_present: int
    measured_target_in_data: bool


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _sections(
    data: bytes, count: int, off_base: int, addr_base: int, size_base: int
) -> list[DolSection]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    result: list[DolSection] = []
    for index in range(count):
        file_offset = _be32(data, off_base + index * 4)
        address = _be32(data, addr_base + index * 4)
        size = _be32(data, size_base + index * 4)
        if size == 0:
            continue
        if file_offset > len(data) or size > len(data) - file_offset:
            raise RuntimeError(
                f"DOL section {index} is outside file: "
                f"offset=0x{file_offset:X} size=0x{size:X}"
            )
        result.append(DolSection(file_offset, address, size))
    return result


def _text_sections(data: bytes) -> list[DolSection]:
    result = _sections(data, 7, 0x00, 0x48, 0x90)
    if not result:
        raise RuntimeError("DOL contains no executable text sections")
    for index, section in enumerate(result):
        if (section.address & 3) or (section.size & 3):
            raise RuntimeError(
                f"text section {index} is not instruction aligned: "
                f"address=0x{section.address:08X} size=0x{section.size:X}"
            )
    return result


def _data_sections(data: bytes) -> list[DolSection]:
    return _sections(data, 11, 0x1C, 0x64, 0xAC)


def _in_text(sections: list[DolSection], address: int) -> bool:
    if address & 3:
        return False
    return any(
        section.address <= address < section.address + section.size
        for section in sections
    )


def _instruction_at(data: bytes, sections: list[DolSection], address: int) -> int:
    for section in sections:
        if section.address <= address < section.address + section.size:
            return _be32(data, section.file_offset + address - section.address)
    raise RuntimeError(f"0x{address:08X} is outside executable DOL text")


def _is_linked_bcctr(raw: int) -> bool:
    primary = raw >> 26
    xo = (raw >> 1) & 0x3FF
    bo = (raw >> 21) & 0x1F
    return primary == 19 and xo == 528 and (bo & 4) != 0 and (raw & 1) != 0


def _read_map(path: Path) -> dict[int, tuple[int, str]]:
    rows: dict[int, tuple[int, str]] = {}
    if not path.is_file():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split(maxsplit=2)
        if len(fields) != 3:
            raise RuntimeError(
                f"malformed LLVM partition map line {line_number}: {line!r}"
            )
        try:
            address = int(fields[0], 16)
            size = int(fields[1], 0)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid LLVM partition map line {line_number}: {line!r}"
            ) from exc
        rows.setdefault(address, (size, fields[2]))
    return rows


def _scan_address_taken(
    data: bytes, text: list[DolSection], data_sections: list[DolSection]
) -> tuple[set[int], int]:
    candidates: set[int] = set()
    words = 0
    for section in data_sections:
        # DOL data sections are normally word-aligned, but tolerate byte-sized
        # tails. Scan only naturally aligned guest words so byte strings cannot
        # manufacture overlapping pointer candidates.
        start_skip = (-section.address) & 3
        for relative in range(start_skip, section.size - 3, 4):
            value = _be32(data, section.file_offset + relative)
            words += 1
            if _in_text(text, value):
                candidates.add(value)
    return candidates, words


def augment(dol_path: Path, map_path: Path) -> AddressTakenStats:
    data = dol_path.read_bytes()
    text = _text_sections(data)
    data_sections = _data_sections(data)

    dispatcher_raw = _instruction_at(data, text, DEV57_DISPATCH_CALL)
    _instruction_at(data, text, DEV57_MEASURED_TARGET)
    if not _is_linked_bcctr(dispatcher_raw):
        raise RuntimeError(
            "dev57 dispatcher shape changed: expected a legal linked BCCTR at "
            f"0x{DEV57_DISPATCH_CALL:08X} because the latest two-path frontier "
            f"preserved LR=0x{DEV57_DISPATCH_RETURN:08X}; "
            f"found raw=0x{dispatcher_raw:08X}"
        )

    candidates, words = _scan_address_taken(data, text, data_sections)
    if len(candidates) > MAX_ADDRESS_TAKEN_ENTRIES:
        raise RuntimeError(
            "address-taken executable-pointer census is implausibly large: "
            f"{len(candidates)} > {MAX_ADDRESS_TAKEN_ENTRIES}; refusing to "
            "fragment LLVM regions until the DOL layout is re-audited"
        )

    rows = _read_map(map_path)
    added = 0
    already = 0

    # Guarantee the exact measured frontier. If the generic census already
    # contains it this simply gives the measured entry the stronger provenance
    # name before generic candidates are considered.
    if DEV57_MEASURED_TARGET in rows:
        already += 1
    else:
        rows[DEV57_MEASURED_TARGET] = (0, DEV57_MEASURED_NAME)
        added += 1

    for address in sorted(candidates):
        if address in rows:
            already += 1
            continue
        rows[address] = (0, f"openspm_dev57_addr_taken_{address:08x}")
        added += 1

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        "".join(
            f"{address:08X} {size} {name}\n"
            for address, (size, name) in sorted(rows.items())
        ),
        encoding="utf-8",
        newline="\n",
    )
    return AddressTakenStats(
        dispatcher_raw=dispatcher_raw,
        data_words_scanned=words,
        candidates=len(candidates),
        added=added,
        already_present=already,
        measured_target_in_data=DEV57_MEASURED_TARGET in candidates,
    )


def self_test() -> None:
    text_base = 0x80030000
    text_size = 0x00010000
    text_file = 0x100
    data_base = 0x80300000
    data_size = 0x40
    data_file = text_file + text_size
    blob = bytearray(data_file + data_size)

    # text[0], data[0]
    struct.pack_into(">I", blob, 0x00, text_file)
    struct.pack_into(">I", blob, 0x48, text_base)
    struct.pack_into(">I", blob, 0x90, text_size)
    struct.pack_into(">I", blob, 0x1C, data_file)
    struct.pack_into(">I", blob, 0x64, data_base)
    struct.pack_into(">I", blob, 0xAC, data_size)

    dispatcher_raw = 0x4E800421  # bctrl
    struct.pack_into(
        ">I", blob, text_file + DEV57_DISPATCH_CALL - text_base, dispatcher_raw
    )
    struct.pack_into(
        ">I", blob, text_file + DEV57_MEASURED_TARGET - text_base, 0x9421FFF0
    )
    other = 0x80033340
    struct.pack_into(">I", blob, text_file + other - text_base, 0x4E800020)

    struct.pack_into(">I", blob, data_file + 0x00, DEV57_MEASURED_TARGET)
    struct.pack_into(">I", blob, data_file + 0x04, other)
    struct.pack_into(">I", blob, data_file + 0x08, other | 1)  # unaligned, ignore
    struct.pack_into(">I", blob, data_file + 0x0C, 0x81234560)  # outside text

    with tempfile.TemporaryDirectory(prefix="openspm-dev57-address-taken-") as temp:
        root = Path(temp)
        dol = root / "sample.dol"
        symbol_map = root / "partitions.map"
        dol.write_bytes(blob)
        # Existing semantic entry must win over generic naming.
        symbol_map.write_text(
            f"{DEV57_MEASURED_TARGET:08X} 0 existing_semantic_entry\n",
            encoding="utf-8",
            newline="\n",
        )
        stats = augment(dol, symbol_map)
        text_map = symbol_map.read_text(encoding="utf-8")
        assert stats.dispatcher_raw == dispatcher_raw
        assert stats.candidates == 2
        assert stats.measured_target_in_data
        assert stats.added == 1
        assert stats.already_present >= 1
        assert "existing_semantic_entry" in text_map
        assert f"{other:08X} 0 openspm_dev57_addr_taken_{other:08x}" in text_map
        assert f"{other | 1:08X}" not in text_map
        stats2 = augment(dol, symbol_map)
        assert stats2.added == 0
        assert symbol_map.read_text(encoding="utf-8") == text_map


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dol", type=Path)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        self_test()
        if args.self_test:
            print("M14 dev57 address-taken-entry self-test passed")
            return 0
        if args.dol is None or args.map_path is None:
            parser.error("--dol and --map are required unless --self-test is used")
        stats = augment(args.dol.resolve(), args.map_path.resolve())
        print(
            "M14 dev57 address-taken entries ready: "
            f"dispatcher=0x{DEV57_DISPATCH_CALL:08X} "
            f"raw=0x{stats.dispatcher_raw:08X} "
            f"measured_target=0x{DEV57_MEASURED_TARGET:08X} "
            f"measured_in_data={'yes' if stats.measured_target_in_data else 'no'} "
            f"data_words={stats.data_words_scanned} "
            f"candidates={stats.candidates} added={stats.added} "
            f"already_present={stats.already_present}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: dev57 address-taken-entry augmentation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
