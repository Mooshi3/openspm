#!/usr/bin/env python3
"""The code patches behind new maps (docs/CUSTOM-LEVELS.md, "Map registry").

The game's map registry `mapData` (0x803C5098) is 468 pointers to MapData
records, read only by mapDataPtr (0x80029364) and mapDataEvtPtr (0x8002942C).
Each counts the non-NULL pointers, asserts the count is exactly 468, then
compares names in order. The array moves to MEM2 with SPARE more slots, each
starting as a copy of slot 0. That is a real record, so the count holds, and
a name lookup still finds the first match, the game's. Registering a map
writes its record into a spare slot. The four bound checks (468) become
468 + SPARE.

  map_registry.py --dol codegen.dol            check the recipes
  map_registry.py --dol codegen.dol --write    write them into
                                                state/openspm-code-patches.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import table_refs  # noqa: E402

MANIFEST = HERE.parents[1] / "state" / "openspm-code-patches.json"
TABLE = 0x803C5098
GAME_MAPS = 468
SPARE = 64
HOME = 0x93E55098  # MEM2, keeps the table's low 16 bits; clear of the character tables

# The bound checks: cmpwi rX, 468 in the count loop and the name loop of each reader.
BOUNDS = [
    (0x800293A8, 3, "mapDataPtr's count check"),
    (0x80029400, 29, "mapDataPtr's name loop"),
    (0x80029470, 3, "mapDataEvtPtr's count check"),
    (0x800294C4, 31, "mapDataEvtPtr's name loop"),
]


def cmpwi(reg: int, imm: int) -> int:
    return 0x2C000000 | (reg << 16) | (imm & 0xFFFF)


def word_at(sections: list[table_refs.Section], address: int) -> int | None:
    for s in sections:
        if s.text and s.address <= address < s.address + len(s.data):
            o = address - s.address
            return int.from_bytes(s.data[o:o + 4], "big")
    return None


def patches(sections: list[table_refs.Section]) -> list[dict]:
    found = table_refs.find(sections, TABLE, GAME_MAPS * 4)
    if found["problems"] or found["data_pointers"]:
        raise RuntimeError(f"map registry: references that cannot move: {found['problems'] or found['data_pointers']}")
    out = list(table_refs.recipes(found, TABLE, HOME, "map-registry"))
    if len(out) != 4:
        raise RuntimeError(f"map registry: expected 4 lis sites, found {len(out)}")
    for i, (address, reg, why) in enumerate(BOUNDS):
        expected, replacement = cmpwi(reg, GAME_MAPS), cmpwi(reg, GAME_MAPS + SPARE)
        if word_at(sections, address) not in (expected, replacement):
            raise RuntimeError(f"map registry: 0x{address:08X} is not cmpwi r{reg}, {GAME_MAPS}")
        out.append({"name": f"map-registry-bound-{i}", "address": f"0x{address:08X}",
                    "expected": f"0x{expected:08X}", "replacement": f"0x{replacement:08X}",
                    "why": f"{why}: {GAME_MAPS} game maps + {SPARE} spare slots."})
    return out


def relocation() -> dict:
    return {"name": "map-registry", "from": f"0x{TABLE:08X}", "size": f"0x{GAME_MAPS * 4:X}", "to": f"0x{HOME:08X}",
            "row_size": "0x4", "rows": GAME_MAPS + SPARE,
            "why": "The map registry (docs/CUSTOM-LEVELS.md): the game's 468 map pointers, then spare slots that start "
                   "as a copy of slot 0 (the count check needs every slot non-NULL; a lookup finds the game's own "
                   "record first) until OpenSPM registers a map in one."}


def write(entries: list[dict]) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["patches"] = [p for p in data["patches"] if not p["name"].startswith("map-registry")] + entries
    data["relocations"] = [r for r in data.get("relocations", []) if r["name"] != "map-registry"] + [relocation()]
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    sections = table_refs.parse_dol(args.dol.read_bytes())
    entries = patches(sections)
    for e in entries:
        print(e["name"], e["address"], e["expected"], "->", e["replacement"])
    if args.write:
        write(entries)
        print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
