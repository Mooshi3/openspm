#!/usr/bin/env python3
"""The code patches behind real character ids 4+ (docs/CHARACTER-IDS.md).

From the reads character_reads.py finds and each read's uses:

  * "base": a read whose value is sign-extended and then switched on,
    indexed with (in a table a mod does not fill) or passed on gets its
    `extsb rA, rS` replaced by `rlwinm rA, rS, 0, 30, 31` (rA = rS & 3): the
    same value for ids 0-3, Mario (0) for the added ids 4, 8, ... 124;
  * "own": a read that only indexes tables a mod fills (OWN_TABLES) keeps the
    real id; those tables move to MEM2 with ROWS rows (row_size per
    character), via table_refs.py's lis recipes; so does a read compared with
    another character id (identity_compare: "is he already this character");
  * the other paths of the id (SPECIAL): fixed single-instruction patches,
    each checked against the word it replaces.

Every patch leaves ids 0-3 exactly as the game has them.

  character_ids.py --dol codegen.dol            print the plan's counts
  character_ids.py --dol codegen.dol --write    write the main.dol patches and
                                                relocations into
                                                state/openspm-code-patches.json

relF code is placed by the build, so its patches are made there
(relf_patches(), called by tools/native_bridge/openspm_code_patches.py) and
must number exactly what the manifest records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import character_reads as cr  # noqa: E402
import table_refs  # noqa: E402

MANIFEST = HERE.parents[1] / "state" / "openspm-code-patches.json"
RELF_START = 0x80C00000
ROWS = 128  # ids 0..127 (the id is a signed byte)

# Tables a mod fills: address -> (size of the game's 4 rows, row size, name, MEM2 home).
# Homes keep the table's low 16 bits (only lis halves change) and do not
# overlap each other with ROWS rows.
OWN_TABLES = {
    0x803E8148: (0x20, 8, "char-model", 0x93E48148),
    0x803E8168: (0x20, 8, "char-model2", 0x93E58168),
    0x803EB238: (0x20, 8, "char-preload", 0x93E4B238),
    0x803CC5D8: (0x20, 8, "char-dot-model", 0x93E4C5D8),
    0x803D4880: (0x10, 4, "char-pipe-model", 0x93E44880),
    0x803E82E0: (0x30, 12, "char-voice-damage", 0x93E682E0),
    0x803E8588: (0x40, 16, "char-voice-acro", 0x93E78588),
    0x803EA038: (0x40, 16, "char-voice-attack", 0x93E4A038),
    0x803E8708: (0x10, 4, "char-landing", 0x93E88708),
    0x803E8228: (0x50, 20, "char-properties", 0x93E98228),
}

# The id's other paths (docs/CHARACTER-IDS.md, "The design" 4).
SPECIAL = [
    ("char-id-properties-switch", 0x80122118, 0x2C050004, 0x2C05007F,
     "marioApplyCharacterProperties switches on the id for the hitbox size; ids above its bound (cmpwi r5, 4) "
     "skip it. Bound 127: an added character takes Mario's branch."),
    ("char-id-change-files", 0x801473F0, 0x541E1838, 0x541E1EF8,
     "Move 71 (the switch transition) indexes its file table by the change target (rlwinm r30, r0, 3, 0, 28); "
     "the mask keeps the low two bits (rlwinm r30, r0, 3, 27, 28): Mario's files for an added character."),
    ("char-id-screen-box", 0x80153334, 0x7C781B78, 0x547807BE,
     "0x801532DC (a character's screen box, for the flip) takes the id as its first argument; its copy "
     "mr r24, r3 becomes rlwinm r24, r3, 0, 30, 31, which covers every caller."),
    ("char-id-request-assert", 0x80146DDC, 0x2C1E0004, 0x2C1E0080,
     "The character change request (0x80146DB0) asserts 0 <= id < 4; the bound becomes 128."),
    ("char-id-request-assert-2", 0x80146F00, 0x2C1E0004, 0x2C1E0080,
     "The same assert in the second change request (0x80146ED4)."),
]


def mask_to_extsb(word: int) -> int:
    """The inverse of extsb_to_mask (an already-patched site's original)."""
    rs, ra, rc = (word >> 21) & 31, (word >> 16) & 31, word & 1
    return (31 << 26) | (rs << 21) | (ra << 16) | (954 << 1) | rc


def original(word: int) -> int:
    return mask_to_extsb(word) if word >> 26 == 21 else word


def extsb_to_mask(word: int) -> int:
    """extsb[.] rA, rS -> rlwinm[.] rA, rS, 0, 30, 31."""
    rs, ra, rc = (word >> 21) & 31, (word >> 16) & 31, word & 1
    return (21 << 26) | (rs << 21) | (ra << 16) | (30 << 6) | (31 << 1) | rc


def branch_targets(code: cr.Code) -> set[int]:
    """Every direct branch target, and every data word that points into code
    (jump tables): an instruction where control may arrive from elsewhere."""
    targets = set()
    for pc, w in code.words.items():
        major = w >> 26
        if major in (16, 18):
            succ, call = cr._successors(w, pc)
            targets.update(s for s in succ if s != pc + 4)
            if call is not None:
                targets.add(call)
    for s in code.sections:
        if s.text:
            continue
        for off in range(0, len(s.data) - 3, 4):
            v = int.from_bytes(s.data[off:off + 4], "big")
            if v in code.words:
                targets.add(v)
    return targets


def only_id_reaches(code: cr.Code, read: int, extsb: int, budget: int = 200000) -> bool:
    """Does every path from the read's function entry (its stwu prologue) to
    the extsb arrive with the read's value in the extsb's source register?
    A path walk with one bit of state (does the register hold the id now)."""
    rs = (code.words[extsb] >> 21) & 31
    entry = read
    while entry - 4 in code.words and (code.words[entry] >> 16) != 0x9421 and read - entry < 0x4000:
        entry -= 4
    if (code.words[entry] >> 16) != 0x9421:
        return False
    work, seen, reached = [(entry, False)], set(), False
    while work and budget:
        pc, holds = work.pop()
        if (pc, holds) in seen or pc not in code.words:
            continue
        seen.add((pc, holds))
        budget -= 1
        if pc == extsb:
            if not holds:
                return False
            reached = True
        w = code.words[pc]
        succ, call = cr._successors(w, pc)
        if pc == read:
            holds = True
        elif rs in table_refs.decode(w).writes or (call is not None and rs in table_refs.VOLATILE):
            holds = False
        if w == 0x4E800020:
            continue
        work.extend((s, holds) for s in succ)
    return reached and budget > 0


def identity_compare(code: cr.Code, extsb: int, reach: int = 24) -> int | None:
    """The register compare (cmpw / cmplw rA, rB) the sign-extended id meets
    on the straight line after the extsb, if any: a test "is this the same
    character as that one" (the change requests' "already him", the pause
    menu's current entry), which must see the real id, not Mario."""
    live = {(code.words[extsb] >> 16) & 31}
    pc = extsb + 4
    for _ in range(reach):
        w = code.words.get(pc)
        if w is None:
            return None
        if w >> 26 == 31 and ((w >> 1) & 1023) in (0, 32) and live & {(w >> 16) & 31, (w >> 11) & 31}:
            return pc
        live -= set(table_refs.decode(w).writes)
        if not live or w == 0x4E800020 or w >> 26 == 18:
            return None
        pc += 4
    return None


def plan(sections: list[table_refs.Section]) -> dict:
    code = cr.Code(sections)
    reads = cr.run(sections)["reads"]
    targets = branch_targets(code)
    own_bases = set(OWN_TABLES) | {home for _, _, _, home in OWN_TABLES.values()}  # before or after the move
    base, own, leave, problems = [], [], [], []
    for row in reads:
        pc = row["address"]
        uses = cr.value_uses(code, pc)
        index = uses["index"]
        own_idx = [k for k, (t, _) in index.items() if t in own_bases]
        side = bool(uses["argument"] or uses["store"] or uses["other"])
        if not uses["extsb"]:
            if index:
                problems.append((pc, "indexes without a sign extension"))
            leave.append(pc)
            continue
        if own_idx:
            own.append(pc)  # the 16 own reads and the 3 mixed ones (SPECIAL covers their switch)
            continue
        if any(code.words[s] >> 26 == 38 and code.words[s] & 0xFFFF == cr.CHAR_OFFSET for s in uses["store"]):
            own.append(pc)  # saved and written back to +0x38 (MarioWork's re-initialisation): keep the id
            continue
        if any(identity_compare(code, e) is not None for e in uses["extsb"]):
            own.append(pc)  # compared with another character id: keep the real one
            continue
        for e in sorted(uses["extsb"]):
            # Sound either way: (a) a straight line from the read (no instruction in
            # between is a branch or jump-table target, and none writes the register),
            # or (b) every path from the function entry carries the id there.
            rs = (code.words[e] >> 21) & 31
            straight = e - pc <= 64 and not any(a in targets for a in range(pc + 4, e + 4, 4)) and \
                not any(rs in table_refs.decode(code.words[a]).writes for a in range(pc + 4, e, 4))
            if not straight and not only_id_reaches(code, pc, e):
                problems.append((pc, f"extsb at 0x{e:08X} may be reached with another value"))
                continue
            base.append((pc, e, code.words[e]))
        del side
    return {"code": code, "base": base, "own": own, "leave": leave, "problems": problems}


def main_patches(result: dict) -> list[dict]:
    out = []
    for n, (read, e, word) in enumerate(sorted(x for x in result["base"] if x[1] < RELF_START)):
        out.append({"name": f"char-id-{n:03d}", "address": f"0x{e:08X}", "expected": f"0x{original(word):08X}",
                    "replacement": f"0x{extsb_to_mask(original(word)):08X}"})
    code = result["code"]
    for name, address, expected, replacement, why in SPECIAL:
        if code.words.get(address) not in (expected, replacement):
            raise RuntimeError(f"{name}: 0x{address:08X} holds 0x{code.words.get(address, 0):08X}, not 0x{expected:08X}")
        out.append({"name": name, "address": f"0x{address:08X}", "expected": f"0x{expected:08X}",
                    "replacement": f"0x{replacement:08X}", "why": why})
    for table, (size, row, name, home) in OWN_TABLES.items():
        found = table_refs.find(code.sections, table, size)
        if found["data_pointers"]:
            raise RuntimeError(f"{name}: data points into the table")
        for entry in table_refs.recipes(found, table, home, name):
            out.append(entry)
    return out


ITEM_TABLE_END = 0x93E07E78 + 4096 * 0x2C  # OPENSPM_ITEM_DATA_MAX_COUNT entries
SCRATCH_BEGIN = 0x93F00000


def check_homes() -> None:
    """The homes with ROWS rows: inside [item table end, scratch), apart."""
    spans = sorted((home, home + row * ROWS, name) for _, (_, row, name, home) in OWN_TABLES.items())
    for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
        if b0 < a1:
            raise RuntimeError(f"{an} and {bn} overlap in MEM2")
    for table, (_, _, name, home) in OWN_TABLES.items():
        if (table ^ home) & 0xFFFF:
            raise RuntimeError(f"{name}: the home must keep the table's low 16 bits")
    if spans[0][0] < ITEM_TABLE_END or spans[-1][1] > SCRATCH_BEGIN:
        raise RuntimeError("a table home leaves the window between the item table and the scratch memory")


def relocations() -> list[dict]:
    check_homes()
    return [{"name": name, "from": f"0x{table:08X}", "size": f"0x{size:X}", "to": f"0x{home:08X}",
             "row_size": f"0x{row:X}", "rows": ROWS,
             "why": "A per-character table a mod fills (docs/CHARACTER-IDS.md): the game's 4 rows are copied and the "
                    "other rows start as row 0 (Mario) until the roster writes an added character's. Code that "
                    "reaches the table through a base register shared with neighbouring data keeps the original "
                    "(its id reads are masked to 0-3)."}
            for table, (size, row, name, home) in OWN_TABLES.items()]


def relf_patches(sections: list[table_refs.Section]) -> list[dict]:
    """The relF part, for the build (the player's own layout)."""
    result = plan(sections)
    return [{"name": f"char-id-relf-{n:02d}", "address": e, "expected": original(word),
             "replacement": extsb_to_mask(original(word)), "boot": False}
            for n, (read, e, word) in enumerate(sorted(x for x in result["base"] if x[1] >= RELF_START))]


def write(result: dict) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    keep = [p for p in data["patches"] if not p["name"].startswith("char-")]
    data["patches"] = keep + main_patches(result)
    data["relocations"] = [r for r in data.get("relocations", []) if not r["name"].startswith("char-")] + relocations()
    data["relf_character_id_patches"] = sum(1 for x in result["base"] if x[1] >= RELF_START)
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dol", required=True, type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    result = plan(table_refs.parse_dol(args.dol.read_bytes()))
    main_base = sum(1 for x in result["base"] if x[1] < RELF_START)
    print(f"base (extsb -> & 3): {main_base} in main.dol, {len(result['base']) - main_base} in relF; "
          f"own: {len(result['own'])}; left alone: {len(result['leave'])}; problems: {len(result['problems'])}")
    for pc, why in result["problems"]:
        print(f"  0x{pc:08X}: {why}")
    if result["problems"]:
        return 2
    if args.write:
        write(result)
        print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
