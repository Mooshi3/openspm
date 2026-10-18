#!/usr/bin/env python3
"""M14 dev97: repair the HP-zero callback cold-entry surface.

The player log proves bottomless death reaches HP=0 and switches from
MOT_BOTTOMLESS (25) to MOT_24 (24), but next_seq never becomes SEQ_GAMEOVER (4).

Seeky's public symbols identify the relevant US2 motion table at 0x803E8718.
The player's own DOL therefore gives exact local function pointers for MOT_24,
MOT_BOTTOMLESS and MOT_30 without guessing regional text addresses.

The first dev97 revision incorrectly assumed the adjacent HP-zero helper would
contain a direct seqSetSeq(4,0,0) call. The retail US2 build disproved that
assumption: no such direct call exists in the bounded MOT_24 neighborhood.

Public EU0 symbols instead show this compact layout:
  mot_bottomless ... mot_24 ... onHpEquals0 ... mot_30
The exact addresses are region-specific, but that *ordering* can be verified
from the player's US2 motion table. dev97 therefore promotes every aligned
instruction in the bounded [MOT_24, MOT_30) executable interval as a legal LLVM
partition entry. That interval contains MOT_24 plus its immediately-adjacent
HP-zero helper surface while stopping exactly at the next known motion main
function from the same player-owned table.

This remains strictly a native-entry repair: no guest HP, motion, sequence,
registers, renderer or timing state are changed. No Nintendo bytes are persisted.
Generation fails closed if the motion-table relationship is not locally plausible.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import tempfile

MARIO_MOT_TBL = 0x803E8718
MOTION_ENTRY_SIZE = 8
MOT_24 = 24
MOT_BOTTOMLESS = 25
MOT_30 = 30

# EU0 public symbols put mot_24 -> onHpEquals0 -> mot_30 within ~0x60c bytes.
# Keep the player-derived US2 bound generous enough for regional drift but far
# too small to blanket-partition unrelated gameplay code.
MIN_HP0_WINDOW_BYTES = 0x80
MAX_HP0_WINDOW_BYTES = 0x1000
MAX_BOTTOMLESS_TO_MOT24_BYTES = 0x2000
ENTRY_PREFIX = "openspm_dev97_hp0_motion24_interval_"


@dataclass(frozen=True)
class DolSection:
    file_offset: int
    address: int
    size: int


@dataclass(frozen=True)
class Dev97Stats:
    mot24_main: int
    bottomless_main: int
    mot30_main: int
    window_start: int
    window_end: int
    added: int
    already_present: int


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _sections(
    data: bytes,
    count: int,
    off_base: int,
    addr_base: int,
    size_base: int,
) -> list[DolSection]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    out: list[DolSection] = []
    for index in range(count):
        file_offset = _be32(data, off_base + index * 4)
        address = _be32(data, addr_base + index * 4)
        size = _be32(data, size_base + index * 4)
        if size == 0:
            continue
        if file_offset > len(data) or size > len(data) - file_offset:
            raise RuntimeError(
                f"DOL section {index} outside file: "
                f"off=0x{file_offset:X} size=0x{size:X}"
            )
        out.append(DolSection(file_offset, address, size))
    return out


def _text_sections(data: bytes) -> list[DolSection]:
    out = _sections(data, 7, 0x00, 0x48, 0x90)
    if not out:
        raise RuntimeError("DOL contains no executable text sections")
    for section in out:
        if (section.address & 3) or (section.size & 3):
            raise RuntimeError(
                "DOL executable section is not instruction aligned: "
                f"0x{section.address:08X}/0x{section.size:X}"
            )
    return out


def _data_sections(data: bytes) -> list[DolSection]:
    return _sections(data, 11, 0x1C, 0x64, 0xAC)


def _word_at(data: bytes, sections: list[DolSection], address: int) -> int:
    for section in sections:
        if section.address <= address <= section.address + section.size - 4:
            return _be32(data, section.file_offset + address - section.address)
    raise RuntimeError(f"0x{address:08X} is outside the requested DOL sections")


def _executable(sections: list[DolSection], address: int) -> bool:
    return (address & 3) == 0 and any(
        section.address <= address < section.address + section.size
        for section in sections
    )


def _read_map(path: Path) -> dict[int, tuple[int, str]]:
    rows: dict[int, tuple[int, str]] = {}
    if not path.is_file():
        return rows
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
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


def _write_map(path: Path, rows: dict[int, tuple[int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f"{address:08X} {size} {name}\n"
            for address, (size, name) in sorted(rows.items())
        ),
        encoding="utf-8",
        newline="\n",
    )


def _motion_main(
    data: bytes, data_sections: list[DolSection], motion: int
) -> int:
    entry = MARIO_MOT_TBL + motion * MOTION_ENTRY_SIZE
    return _word_at(data, data_sections, entry)


def _derive_interval(
    data: bytes, text: list[DolSection], data_sections: list[DolSection]
) -> tuple[int, int, int]:
    mot24_main = _motion_main(data, data_sections, MOT_24)
    bottomless_main = _motion_main(data, data_sections, MOT_BOTTOMLESS)
    mot30_main = _motion_main(data, data_sections, MOT_30)

    for label, address in (
        ("MOT_24 main", mot24_main),
        ("MOT_BOTTOMLESS main", bottomless_main),
        ("MOT_30 main", mot30_main),
    ):
        if not _executable(text, address):
            raise RuntimeError(
                f"dev97 {label} 0x{address:08X} is not executable in the "
                "player-owned US2 DOL"
            )

    if len({mot24_main, bottomless_main, mot30_main}) != 3:
        raise RuntimeError(
            "dev97 motion table aliases one of MOT_24/MOT_BOTTOMLESS/MOT_30"
        )

    # Public EU0 ordering is bottomless < mot24 < onHpEquals0 < mot30. Require
    # the retail US2 table to preserve the structural order before applying the
    # regional-independent interval repair.
    if not bottomless_main < mot24_main < mot30_main:
        raise RuntimeError(
            "dev97 US2 motion-table ordering changed: expected "
            "MOT_BOTTOMLESS < MOT_24 < MOT_30, got "
            f"0x{bottomless_main:08X} < 0x{mot24_main:08X} < 0x{mot30_main:08X}"
        )

    bottomless_gap = mot24_main - bottomless_main
    if bottomless_gap > MAX_BOTTOMLESS_TO_MOT24_BYTES:
        raise RuntimeError(
            "dev97 MOT_BOTTOMLESS->MOT_24 gap is implausibly large: "
            f"0x{bottomless_gap:X} > 0x{MAX_BOTTOMLESS_TO_MOT24_BYTES:X}"
        )

    interval = mot30_main - mot24_main
    if not MIN_HP0_WINDOW_BYTES <= interval <= MAX_HP0_WINDOW_BYTES:
        raise RuntimeError(
            "dev97 MOT_24->MOT_30 interval is outside the bounded HP-zero "
            f"surface: 0x{interval:X}, expected "
            f"0x{MIN_HP0_WINDOW_BYTES:X}..0x{MAX_HP0_WINDOW_BYTES:X}"
        )

    # Every byte in the interval should stay within executable DOL text. Refuse
    # to bridge a section boundary or data hole.
    for address in range(mot24_main, mot30_main, 4):
        if not _executable(text, address):
            raise RuntimeError(
                "dev97 MOT_24->MOT_30 interval crosses non-executable memory at "
                f"0x{address:08X}"
            )

    return mot24_main, bottomless_main, mot30_main


def augment(dol_path: Path, map_path: Path) -> Dev97Stats:
    data = dol_path.read_bytes()
    text = _text_sections(data)
    data_sections = _data_sections(data)
    mot24_main, bottomless_main, mot30_main = _derive_interval(
        data, text, data_sections
    )

    rows = _read_map(map_path)
    added = 0
    already = 0
    for address in range(mot24_main, mot30_main, 4):
        if address in rows:
            already += 1
            continue
        rows[address] = (0, f"{ENTRY_PREFIX}{address:08x}")
        added += 1

    if added == 0 and already == 0:
        raise RuntimeError("dev97 HP-zero motion interval produced no entries")
    _write_map(map_path, rows)

    return Dev97Stats(
        mot24_main=mot24_main,
        bottomless_main=bottomless_main,
        mot30_main=mot30_main,
        window_start=mot24_main,
        window_end=mot30_main,
        added=added,
        already_present=already,
    )


def self_test() -> None:
    text_base = 0x80160000
    text_size = 0x00004000
    text_file = 0x100
    data_base = 0x803E8700
    data_size = 0x200
    data_file = text_file + text_size
    blob = bytearray(data_file + data_size)

    struct.pack_into(">I", blob, 0x00, text_file)
    struct.pack_into(">I", blob, 0x48, text_base)
    struct.pack_into(">I", blob, 0x90, text_size)
    struct.pack_into(">I", blob, 0x1C, data_file)
    struct.pack_into(">I", blob, 0x64, data_base)
    struct.pack_into(">I", blob, 0xAC, data_size)

    bottomless = 0x80160980
    mot24 = 0x80161100
    mot30 = 0x80161780
    table_file = data_file + MARIO_MOT_TBL - data_base
    struct.pack_into(">I", blob, table_file + MOT_BOTTOMLESS * 8, bottomless)
    struct.pack_into(">I", blob, table_file + MOT_24 * 8, mot24)
    struct.pack_into(">I", blob, table_file + MOT_30 * 8, mot30)

    def put(pc: int, raw: int = 0x60000000) -> None:
        struct.pack_into(">I", blob, text_file + pc - text_base, raw)

    put(bottomless, 0x9421FFF0)
    put(mot24, 0x9421FFF0)
    put(mot30, 0x9421FFF0)

    with tempfile.TemporaryDirectory(prefix="openspm-dev97-hp0-") as temp:
        root = Path(temp)
        dol = root / "sample.dol"
        symbol_map = root / "partitions.map"
        dol.write_bytes(blob)

        preserved = mot24 + 0x40
        symbol_map.write_text(
            f"{preserved:08X} 0 existing_semantic_entry\n",
            encoding="utf-8",
            newline="\n",
        )
        stats = augment(dol, symbol_map)
        text_map = symbol_map.read_text(encoding="utf-8")
        assert stats.mot24_main == mot24
        assert stats.bottomless_main == bottomless
        assert stats.mot30_main == mot30
        assert stats.window_start == mot24
        assert stats.window_end == mot30
        assert "existing_semantic_entry" in text_map
        assert f"{mot24:08X} 0 {ENTRY_PREFIX}{mot24:08x}" in text_map
        assert f"{mot30:08X} " not in text_map

        before = text_map
        stats2 = augment(dol, symbol_map)
        assert stats2.added == 0
        assert symbol_map.read_text(encoding="utf-8") == before


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dol", type=Path)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        self_test()
        if args.self_test:
            print("M14 dev97 HP-zero motion-interval self-test passed")
            return 0
        if args.dol is None or args.map_path is None:
            parser.error("--dol and --map are required unless --self-test is used")
        stats = augment(args.dol.resolve(), args.map_path.resolve())
        print(
            "M14 dev97 HP-zero motion interval ready: "
            f"bottomless=0x{stats.bottomless_main:08X} "
            f"mot24=0x{stats.mot24_main:08X} "
            f"mot30=0x{stats.mot30_main:08X} "
            f"window=[0x{stats.window_start:08X},0x{stats.window_end:08X}) "
            f"added={stats.added} already_present={stats.already_present}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: dev97 HP-zero motion-interval derivation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
