"""OpenSPM content patches: the JSON (build side) and include/openspm/code_patches.h
(boot side) must list the same recipes; the applier fails closed and is idempotent."""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import openspm_code_patches  # noqa: E402

def test_header_matches_manifest() -> None:
    text = openspm_code_patches.HEADER.read_text(encoding="utf-8")
    assert text == openspm_code_patches.header_text(), (
        "include/openspm/code_patches_table.inc is stale: run "
        "tools/native_bridge/openspm_code_patches.py --write-header"
    )


def test_item_relocation_is_complete() -> None:
    patches = openspm_code_patches.load()
    items = [p for p in patches if p["name"].startswith("item-data-")]
    assert len(items) == 76  # main.dol; the 10 in relF are found at build time
    for p in items:
        assert p["expected"] >> 26 == 15 and (p["expected"] >> 16) & 31 == 0  # lis rX
        assert p["expected"] & 0xFFFF == 0x803B and p["replacement"] == (p["expected"] & 0xFFFF0000) | 0x93E0
        assert p["boot"] and p["address"] < 0x80C00000
    (reloc,) = [r for r in openspm_code_patches.load_relocations() if r["name"] == "item-data"]
    assert reloc["from"] == 0x803B7E78 and reloc["size"] == 538 * 0x2C and reloc["to"] == 0x93E07E78
    assert reloc["relf_references"] == 10
    assert reloc["to"] & 0xFFFF == reloc["from"] & 0xFFFF  # only the lis half changes


SECTIONS = ((0x80004000, 0x00300000), (0x80C00000, 0x00080000))


def fake_dol(words: dict[int, int]) -> bytes:
    """Two sections (main code, relF) holding `words`."""
    header = bytearray(0x100)
    body = bytearray()
    for i, (base, size) in enumerate(SECTIONS):
        struct.pack_into(">I", header, 4 * i, 0x100 + len(body))
        struct.pack_into(">I", header, 72 + 4 * i, base)
        struct.pack_into(">I", header, 144 + 4 * i, size)
        body += bytearray(size)
    image = bytearray(header + body)
    for address, word in words.items():
        for i, (base, size) in enumerate(SECTIONS):
            if base <= address < base + size:
                off = struct.unpack_from(">I", image, 4 * i)[0] + address - base
                struct.pack_into(">I", image, off, word)
    return bytes(image)


def word_at(image: bytes, address: int) -> int:
    for i, (base, size) in enumerate(SECTIONS):
        if base <= address < base + size:
            off = struct.unpack_from(">I", image, 4 * i)[0] + address - base
            return struct.unpack_from(">I", image, off)[0]
    raise KeyError(address)


RELF_BASE = 0x80C00100


def relf_words(count: int, hi: int = 0x803B, lo: int = 0x7E78, base: int = RELF_BASE) -> dict[int, int]:
    """relF-like code: `lis r3, hi` + `addi r3, r3, lo` + blr, `count` times."""
    words = {}
    for n in range(count):
        a = base + 16 * n
        words[a] = 0x3C600000 | hi
        words[a + 4] = 0x38630000 | lo
        words[a + 8] = 0x4E800020
    return words


RELF_TEMPLATES = RELF_BASE + 0x1000


def relf_tables() -> dict[int, int]:
    """The relF references of the relocated tables in R8PE01 rev 2: 10 to the
    item table, 1 to the enemy templates."""
    words = relf_words(10)
    words.update(relf_words(1, 0x8041, 0xB128, RELF_TEMPLATES))
    return words


def test_character_id_patches() -> None:
    """docs/CHARACTER-IDS.md: each mask replaces an extsb with rlwinm rA, rS, 0, 30, 31
    (same registers, same record bit), the table moves keep the low 16 bits, and the
    per-character relocations have 128 rows."""
    patches = openspm_code_patches.load()
    masks = [p for p in patches if p["name"].startswith("char-id-") and p["name"][8:].isdigit()]
    assert len(masks) == 203
    for p in masks:
        e, r = p["expected"], p["replacement"]
        assert e >> 26 == 31 and (e >> 1) & 0x3FF == 954, p["name"]  # extsb
        assert r >> 26 == 21 and (r >> 6) & 31 == 30 and (r >> 1) & 31 == 31 and (r >> 11) & 31 == 0
        assert (e >> 16) & 0x3FF == (r >> 16) & 0x3FF and e & 1 == r & 1  # rS, rA, Rc kept
    rows = [r for r in openspm_code_patches.load_relocations() if r["name"].startswith("char-")]
    assert len(rows) == 10 and all(r["rows"] == 128 and r["size"] == 4 * r["row_size"] for r in rows)
    assert all(r["to"] & 0xFFFF == r["from"] & 0xFFFF for r in rows)
    assert openspm_code_patches.relf_character_id_patches() == 30


def test_apply_is_checked_and_idempotent() -> None:
    # relF character-id sites are found by the analysis on a real image
    # (test_character_ids.py); the fake relF here has none.
    real = openspm_code_patches.relf_character_id_patches
    openspm_code_patches.relf_character_id_patches = lambda *a: 0
    try:
        _apply_is_checked_and_idempotent()
    finally:
        openspm_code_patches.relf_character_id_patches = real


def _apply_is_checked_and_idempotent() -> None:
    patches = openspm_code_patches.load()
    words = {p["address"]: p["expected"] for p in patches}
    words.update(relf_tables())
    dol = fake_dol(words)
    out, names = openspm_code_patches.apply(dol)
    assert names[: len(patches)] == [p["name"] for p in patches]
    assert len(names) == len(patches) + 11
    for n in range(10):
        assert word_at(out, RELF_BASE + 16 * n) == 0x3C6093E0
    assert word_at(out, RELF_TEMPLATES) == 0x3C6093E7
    for p in patches:
        assert word_at(out, p["address"]) == p["replacement"]
    again, _ = openspm_code_patches.apply(out)
    assert again == out
    # relF with a different number of references: the build stops.
    words = {p["address"]: p["expected"] for p in patches}
    words.update(relf_tables())
    for a in list(relf_words(1, base=RELF_BASE + 16 * 9)):
        del words[a]
    try:
        openspm_code_patches.apply(fake_dol(words))
    except RuntimeError as exc:
        assert "relF has 9" in str(exc)
    else:
        raise AssertionError("an unexpected relF must fail the build")
    wrong = fake_dol({p["address"]: 0x60000000 for p in patches})
    try:
        openspm_code_patches.apply(wrong)
    except RuntimeError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("a foreign word must fail the build")


def test_generators_apply_patches() -> None:
    for name in ("generate_local_llvm.py", "generate_local_llvm_dev70.py"):
        text = (HERE / name).read_text(encoding="utf-8")
        a = text.index("build_relf_codegen_dol.augment_dol(")
        b = text.index("openspm_code_patches.apply(augmented)")
        c = text.index("codegen_dol.write_bytes(augmented)")
        assert a < b < c, name



def test_mem2_homes_do_not_overlap() -> None:
    """OpenSPM's MEM2 (0x93E00000 up): the item table, every moved table with
    its rows, the extended file table (include/openspm/fst_overlay.h) and the
    guest scratch area (0x93F00000) do not overlap."""
    spans = [(0x93E07E78, 0x93E07E78 + 4096 * 0x2C, "item table"),
             (0x93EB0000, 0x93EB0000 + 0x50000, "file table"),
             (0x93F00000, 0x94000000, "guest scratch")]
    for r in openspm_code_patches.load_relocations():
        if r["to"] == 0x93E07E78:
            continue  # the item table (above)
        end = r["to"] + (r["row_size"] * r["rows"] if r["rows"] else r["size"])
        spans.append((r["to"], end, r["name"]))
    spans.sort()
    for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
        assert a1 <= b0, f"{an} and {bn} overlap in MEM2"
    assert spans[0][0] >= 0x93E00000 and spans[-1][1] <= 0x94000000


if __name__ == "__main__":
    for fn in (
        test_header_matches_manifest,
        test_item_relocation_is_complete,
        test_character_id_patches,
        test_apply_is_checked_and_idempotent,
        test_generators_apply_patches,
        test_mem2_homes_do_not_overlap,
    ):
        fn()
        print("ok", fn.__name__)
