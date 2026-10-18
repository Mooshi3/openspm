#!/usr/bin/env python3
"""The code patches behind new enemies (docs/CUSTOM-LEVELS.md, "Enemies").

An enemy is an NPC made from a template (npcEnemyTemplates, 0x8040B128,
435 rows of 0x68: tribe id, AI scripts, flags), whose tribe (npcTribes,
0x803FD7D0, 535 rows of 0x68: model, animations, HP, parts, drops, score)
gives the model and the numbers. Both tables move to MEM2 with SPARE more
rows, each starting as a copy of row 0 (a working Goomba) until OpenSPM
registers an enemy in it. The template number's assert (< 435, npcdrv.c)
becomes 435 + SPARE.

What stays on the MEM1 copies, which are unchanged:
  * data words that point at particular game tribes / templates (scripts
    naming one enemy);
  * the loop at 0x801B60C8 that looks a tribe up by model name over the 535
    game tribes. An added tribe uses a game model, so a match is still
    found.

  enemy_tables.py --dol codegen.dol            check the recipes
  enemy_tables.py --dol codegen.dol --write    write them into
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
RELF_START = 0x80C00000
ROW = 0x68
SPARE = 64

# (name, table, game rows, MEM2 home keeping the low 16 bits)
TABLES = [
    ("enemy-tribes", 0x803FD7D0, 535, 0x93E9D7D0),
    ("enemy-templates", 0x8040B128, 435, 0x93E6B128),
]
# The shared-register site table_refs flags in the tribe table: a NULL test
# of the base and the by-model-name loop over the game's 535 tribes.
TRIBE_LOOP_LIS = 0x801B60C8
TEMPLATE_BOUND = (0x801BEAF0, 14)  # cmpwi r14, 435 ("nTemplateNo<NPCTEMPLATE_MAX")


def cmpwi(reg: int, imm: int) -> int:
    return 0x2C000000 | (reg << 16) | (imm & 0xFFFF)


def word_at(sections: list[table_refs.Section], address: int) -> int | None:
    for s in sections:
        if s.text and s.address <= address < s.address + len(s.data):
            o = address - s.address
            return int.from_bytes(s.data[o:o + 4], "big")
    return None


def plan(sections: list[table_refs.Section]) -> tuple[list[dict], list[dict]]:
    patches, relocations = [], []
    for name, table, rows, home in TABLES:
        found = table_refs.find(sections, table, rows * ROW)
        problems = [p for p in found["problems"] if int(p["lis"], 16) != TRIBE_LOOP_LIS]
        if problems:
            raise RuntimeError(f"{name}: references that cannot move: {problems}")
        main = [p for p in found["patches"] if p["address"] < RELF_START]
        relf = [p for p in found["patches"] if p["address"] >= RELF_START]
        sub = dict(found, patches=main)
        patches += list(table_refs.recipes(sub, table, home, name))
        relocations.append({
            "name": name, "from": f"0x{table:08X}", "size": f"0x{rows * ROW:X}", "to": f"0x{home:08X}",
            "row_size": f"0x{ROW:X}", "rows": rows + SPARE, "relf_references": len(relf),
            "why": f"Enemy tables (docs/CUSTOM-LEVELS.md): the game's {rows} rows, then {SPARE} spare rows that start "
                   "as a copy of row 0 until OpenSPM registers an enemy in one. Data words naming particular game "
                   "rows keep reading the unchanged MEM1 copy."})
    address, reg = TEMPLATE_BOUND
    expected, replacement = cmpwi(reg, 435), cmpwi(reg, 435 + SPARE)
    if word_at(sections, address) not in (expected, replacement):
        raise RuntimeError(f"0x{address:08X} is not cmpwi r{reg}, 435")
    patches.append({"name": "enemy-template-bound", "address": f"0x{address:08X}", "expected": f"0x{expected:08X}",
                    "replacement": f"0x{replacement:08X}",
                    "why": f"npcdrv.c asserts a template number < 435 (NPCTEMPLATE_MAX); 435 + {SPARE} spare rows."})
    return patches, relocations


def write(patches: list[dict], relocations: list[dict]) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["patches"] = [p for p in data["patches"] if not p["name"].startswith("enemy-")] + patches
    data["relocations"] = [r for r in data.get("relocations", []) if not r["name"].startswith("enemy-")] + relocations
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    patches, relocations = plan(table_refs.parse_dol(args.dol.read_bytes()))
    for p in patches:
        print(p["name"], p["address"], p["expected"], "->", p["replacement"])
    for r in relocations:
        print(r["name"], r["from"], "->", r["to"], "rows", r["rows"], "relF references", r["relf_references"])
    if args.write:
        write(patches, relocations)
        print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
