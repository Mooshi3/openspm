#!/usr/bin/env python3
"""Verify measured LLVM cold-entry frontiers, then widen native entries structurally.

dev54 and dev56 remain exact player-proven regression anchors:

* 0x80032B94, reached downstream of the verified direct BL
  0x80032B48 -> 0x8002F278.
* 0x800331AC, reached by the linked BCCTR at 0x80038960 with
  LR=0x80038964.

Two dev56 player runs then advanced past 0x800331AC and independently froze at
0x800332A8 with the same LR=0x80038964. That proved 0x80038960 is a dispatcher
with multiple live computed targets; continuing one-address-at-a-time is not an
architectural solution.

Dev77 covers a different but related restart contract exposed by the wedding
cutscene. The generated-C backend clears the cutscene, while LLVM can return to
OpenSPM with PC=0x80065128 and zero charged guest cycles, then redispatch that
same interior PC until the bridge's 65,536-dispatch safety cap. The retail word
at 0x80065128 is an ordinary LWZ, not a semantic host seam. Promote this proven
external resume PC to a real LLVM partition entry so top-level redispatch starts
a normally analysed native range instead of falling into LLVM's arbitrary-PC
cold-entry fallback loop.

After preserving these measured anchors, this module invokes
augment_llvm_address_taken_entries. That dev57 pass conservatively promotes every
aligned code pointer stored in the player's DOL data sections to a native LLVM
entry. Existing semantic map entries win. This keeps OpenSPM native-only: no PPC
interpreter or emulator fallback is introduced.

No player code bytes or assets leave the local build.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import tempfile

import augment_llvm_address_taken_entries as address_taken

FILESELECT_INDIRECT_CALL = 0x80032B48
FILESELECT_INDIRECT_RETURN = FILESELECT_INDIRECT_CALL + 4
FILESELECT_ROOT_TARGET = 0x8002F278
FILESELECT_INDIRECT_TARGET = 0x80032B94
FILESELECT_ENTRY_NAME = "openspm_dev54_filesel_msgdrv_indirect_target"

COMMON_INDIRECT_CALL = 0x80038960
COMMON_INDIRECT_RETURN = COMMON_INDIRECT_CALL + 4
COMMON_INDIRECT_TARGET = 0x800331AC
COMMON_ENTRY_NAME = "openspm_dev56_common_indirect_target_800331ac"

# dev77: player-proven LLVM-only zero-progress restart frontier. Keep an exact
# retail instruction/loop-tail signature so a different executable revision
# cannot silently inherit an address-specific partition.
WEDDING_RESUME_TARGET = 0x80065128
WEDDING_ENTRY_NAME = "openspm_dev77_wedding_resume_80065128"
WEDDING_LOOP_BRANCH = 0x80065134
WEDDING_LOOP_TARGET = 0x80064E9C
WEDDING_SIGNATURE = (
    (0x80065118, 0x3B180004),  # addi r24,r24,4
    (0x8006511C, 0x3AB50001),  # addi r21,r21,1
    (0x80065120, 0x3AF7000C),  # addi r23,r23,0x0c
    (0x80065124, 0x3AD60034),  # addi r22,r22,0x34
    (0x80065128, 0x806D80A0),  # lwz r3,-0x7f60(r13)
    (0x8006512C, 0x80030004),
    (0x80065130, 0x7C150000),
    (0x80065134, 0x4180FD68),  # conditional backedge -> 0x80064e9c
)

LAST_ADDRESS_TAKEN_STATS: address_taken.AddressTakenStats | None = None


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _text_sections(data: bytes) -> list[tuple[int, int, int]]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    result: list[tuple[int, int, int]] = []
    for index in range(7):
        file_offset = _be32(data, 0x00 + index * 4)
        address = _be32(data, 0x48 + index * 4)
        size = _be32(data, 0x90 + index * 4)
        if size == 0:
            continue
        if (address & 3) or (size & 3):
            raise RuntimeError(
                f"text section {index} is not instruction aligned: "
                f"address=0x{address:08X} size=0x{size:X}"
            )
        if file_offset > len(data) or size > len(data) - file_offset:
            raise RuntimeError(
                f"text section {index} is outside DOL: "
                f"offset=0x{file_offset:X} size=0x{size:X}"
            )
        result.append((file_offset, address, size))
    if not result:
        raise RuntimeError("DOL contains no executable text sections")
    return result


def _instruction_at(
    data: bytes, sections: list[tuple[int, int, int]], address: int
) -> int:
    for file_offset, start, size in sections:
        if start <= address < start + size:
            return _be32(data, file_offset + address - start)
    raise RuntimeError(f"0x{address:08X} is outside executable DOL text")


def _decode_direct_bl(raw: int, pc: int) -> int | None:
    if (raw >> 26) != 18 or (raw & 1) == 0:
        return None
    disp = raw & 0x03FFFFFC
    if disp & 0x02000000:
        disp -= 0x04000000
    if raw & 2:
        return disp & 0xFFFFFFFF
    return (pc + disp) & 0xFFFFFFFF


def _decode_direct_bc(raw: int, pc: int) -> int | None:
    if raw >> 26 != 16:
        return None
    disp = raw & 0x0000FFFC
    if disp & 0x00008000:
        disp -= 0x00010000
    if raw & 2:
        return disp & 0xFFFFFFFF
    return (pc + disp) & 0xFFFFFFFF


def _is_linked_bcctr(raw: int) -> bool:
    primary = raw >> 26
    xo = (raw >> 1) & 0x3FF
    bo = (raw >> 21) & 0x1F
    return primary == 19 and xo == 528 and (bo & 4) != 0 and (raw & 1) != 0


def _encode_bl(pc: int, target: int) -> int:
    disp = target - pc
    if not -(1 << 25) <= disp < (1 << 25) or (disp & 3):
        raise ValueError("self-test BL target out of range")
    return 0x48000001 | (disp & 0x03FFFFFC)


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


def _put_entry(
    rows: dict[int, tuple[int, str]], address: int, name: str, label: str
) -> None:
    existing = rows.get(address)
    if existing is not None and existing[1] != name:
        raise RuntimeError(
            f"{label} indirect target collides with an existing semantic map entry: "
            f"0x{address:08X}={existing[1]}"
        )
    rows[address] = (0, name)


def _verify_wedding_resume(
    data: bytes, sections: list[tuple[int, int, int]]
) -> None:
    for address, expected in WEDDING_SIGNATURE:
        actual = _instruction_at(data, sections, address)
        if actual != expected:
            raise RuntimeError(
                "dev77 wedding restart signature changed: "
                f"0x{address:08X} expected raw=0x{expected:08X}, "
                f"found 0x{actual:08X}; refusing to install the measured "
                "LLVM cold-entry partition on an unknown executable revision"
            )
    branch_raw = _instruction_at(data, sections, WEDDING_LOOP_BRANCH)
    branch_target = _decode_direct_bc(branch_raw, WEDDING_LOOP_BRANCH)
    if branch_target != WEDDING_LOOP_TARGET:
        rendered = "not-a-direct-bc" if branch_target is None else f"0x{branch_target:08X}"
        raise RuntimeError(
            "dev77 wedding loop-tail relationship changed: expected branch "
            f"0x{WEDDING_LOOP_BRANCH:08X} -> 0x{WEDDING_LOOP_TARGET:08X}, "
            f"decoded={rendered}"
        )


def augment(dol_path: Path, map_path: Path) -> tuple[int, int]:
    global LAST_ADDRESS_TAKEN_STATS

    data = dol_path.read_bytes()
    sections = _text_sections(data)

    root_raw = _instruction_at(data, sections, FILESELECT_INDIRECT_CALL)
    root_target = _decode_direct_bl(root_raw, FILESELECT_INDIRECT_CALL)
    _instruction_at(data, sections, FILESELECT_ROOT_TARGET)
    _instruction_at(data, sections, FILESELECT_INDIRECT_TARGET)
    if root_target != FILESELECT_ROOT_TARGET:
        rendered = (
            "not-a-linked-direct-branch"
            if root_target is None
            else f"0x{root_target:08X}"
        )
        raise RuntimeError(
            "dev53 file-select root-call relationship changed: expected linked direct "
            f"branch 0x{FILESELECT_INDIRECT_CALL:08X} -> 0x{FILESELECT_ROOT_TARGET:08X}, "
            f"found raw=0x{root_raw:08X} decoded={rendered}; refusing to add the "
            "measured computed cold entry"
        )

    common_raw = _instruction_at(data, sections, COMMON_INDIRECT_CALL)
    _instruction_at(data, sections, COMMON_INDIRECT_TARGET)
    if not _is_linked_bcctr(common_raw):
        raise RuntimeError(
            "dev56 common indirect caller changed: expected a legal linked BCCTR at "
            f"0x{COMMON_INDIRECT_CALL:08X} because player freezes preserved "
            f"LR=0x{COMMON_INDIRECT_RETURN:08X}; found raw=0x{common_raw:08X}"
        )

    # The non-LLVM/generated-C backend clears this sequence. The LLVM player
    # repeatedly returned from the same ordinary LWZ with downcount=0, proving
    # this is an externally materialized resume coordinate, not a semantic host
    # call. Make it a structural native range start before LLVM ABI analysis.
    _verify_wedding_resume(data, sections)

    rows = _read_map(map_path)
    _put_entry(rows, FILESELECT_INDIRECT_TARGET, FILESELECT_ENTRY_NAME, "dev54")
    _put_entry(rows, COMMON_INDIRECT_TARGET, COMMON_ENTRY_NAME, "dev56")
    _put_entry(rows, WEDDING_RESUME_TARGET, WEDDING_ENTRY_NAME, "dev77 wedding")
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        "".join(
            f"{address:08X} {size} {name}\n"
            for address, (size, name) in sorted(rows.items())
        ),
        encoding="utf-8",
        newline="\n",
    )

    # dev57 class-level repair: guarantee the newest measured target and then
    # discover the DOL's complete data-stored address-taken code surface.
    LAST_ADDRESS_TAKEN_STATS = address_taken.augment(dol_path, map_path)
    stats = LAST_ADDRESS_TAKEN_STATS
    print(
        "[OpenSPM] LLVM dev77 restart-safe native entries: "
        f"wedding_resume=0x{WEDDING_RESUME_TARGET:08X} "
        f"wedding_backedge=0x{WEDDING_LOOP_BRANCH:08X}->0x{WEDDING_LOOP_TARGET:08X}; "
        f"dev57_dispatcher=0x{address_taken.DEV57_DISPATCH_CALL:08X} "
        f"raw=0x{stats.dispatcher_raw:08X} "
        f"measured_target=0x{address_taken.DEV57_MEASURED_TARGET:08X} "
        f"measured_in_data={'yes' if stats.measured_target_in_data else 'no'} "
        f"data_words={stats.data_words_scanned} candidates={stats.candidates} "
        f"added={stats.added} already_present={stats.already_present}",
        flush=True,
    )
    return root_raw, common_raw


def self_test() -> None:
    address_taken.self_test()

    base = 0x8002F000
    # Cover the historical 0x8003xxxx indirect dispatchers plus the dev77
    # wedding loop around 0x80065128 in one synthetic executable section.
    size = 0x00037000
    file_offset = 0x100
    blob = bytearray(file_offset + size)
    struct.pack_into(">I", blob, 0x00, file_offset)
    struct.pack_into(">I", blob, 0x48, base)
    struct.pack_into(">I", blob, 0x90, size)

    expected_root_raw = _encode_bl(FILESELECT_INDIRECT_CALL, FILESELECT_ROOT_TARGET)
    expected_common_raw = 0x4E800421
    struct.pack_into(
        ">I", blob, file_offset + FILESELECT_INDIRECT_CALL - base, expected_root_raw
    )
    struct.pack_into(
        ">I", blob, file_offset + FILESELECT_ROOT_TARGET - base, 0x9421FFF0
    )
    struct.pack_into(
        ">I", blob, file_offset + FILESELECT_INDIRECT_TARGET - base, 0x4E800020
    )
    struct.pack_into(
        ">I", blob, file_offset + COMMON_INDIRECT_CALL - base, expected_common_raw
    )
    struct.pack_into(
        ">I", blob, file_offset + COMMON_INDIRECT_TARGET - base, 0x9421FFF0
    )
    struct.pack_into(
        ">I",
        blob,
        file_offset + address_taken.DEV57_MEASURED_TARGET - base,
        0x9421FFF0,
    )
    for address, raw in WEDDING_SIGNATURE:
        struct.pack_into(">I", blob, file_offset + address - base, raw)

    with tempfile.TemporaryDirectory(prefix="openspm-dev77-indirect-") as temp:
        root = Path(temp)
        dol = root / "sample.dol"
        symbol_map = root / "partitions.map"
        dol.write_bytes(blob)
        symbol_map.write_text(
            "8002F000 0 existing_entry\n",
            encoding="utf-8",
            newline="\n",
        )
        root_raw, common_raw = augment(dol, symbol_map)
        text = symbol_map.read_text(encoding="utf-8")
        assert root_raw == expected_root_raw == 0x4BFFC731
        assert _decode_direct_bl(root_raw, FILESELECT_INDIRECT_CALL) == FILESELECT_ROOT_TARGET
        assert common_raw == expected_common_raw
        assert _is_linked_bcctr(common_raw)
        assert _decode_direct_bc(0x4180FD68, WEDDING_LOOP_BRANCH) == WEDDING_LOOP_TARGET
        assert f"{FILESELECT_INDIRECT_TARGET:08X} 0 {FILESELECT_ENTRY_NAME}" in text
        assert f"{COMMON_INDIRECT_TARGET:08X} 0 {COMMON_ENTRY_NAME}" in text
        assert f"{WEDDING_RESUME_TARGET:08X} 0 {WEDDING_ENTRY_NAME}" in text
        assert f"{address_taken.DEV57_MEASURED_TARGET:08X} 0 " in text
        augment(dol, symbol_map)
        text = symbol_map.read_text(encoding="utf-8")
        assert text.count(FILESELECT_ENTRY_NAME) == 1
        assert text.count(COMMON_ENTRY_NAME) == 1
        assert text.count(WEDDING_ENTRY_NAME) == 1
        assert text.count(f"{address_taken.DEV57_MEASURED_TARGET:08X} ") == 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dol", type=Path)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        self_test()
        if args.self_test:
            print("M14 dev77 indirect/address-taken-entry self-test passed")
            return 0
        if args.dol is None or args.map_path is None:
            parser.error("--dol and --map are required unless --self-test is used")
        root_raw, common_raw = augment(args.dol.resolve(), args.map_path.resolve())
        stats = LAST_ADDRESS_TAKEN_STATS
        print(
            "M14 dev77 restart-safe entries verified: "
            f"dev54_target=0x{FILESELECT_INDIRECT_TARGET:08X} "
            f"dev56_target=0x{COMMON_INDIRECT_TARGET:08X} "
            f"dev57_target=0x{address_taken.DEV57_MEASURED_TARGET:08X} "
            f"wedding_resume=0x{WEDDING_RESUME_TARGET:08X} "
            f"root_raw=0x{root_raw:08X} common_raw=0x{common_raw:08X} "
            f"address_taken={stats.candidates if stats else 0}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: dev77 indirect-entry augmentation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
