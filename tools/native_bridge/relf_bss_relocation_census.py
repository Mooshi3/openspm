#!/usr/bin/env python3
"""Classify relF self-relocations from executable code into BSS.

This is a read-only architecture census for the M14 LLVM relF rebase. SPM loads
relF's file image and BSS as separate HEAP_MAIN allocations before OSLink. A
stable file-image base is therefore not sufficient for LLVM codegen if relocated
instructions in relF text embed addresses from the separately allocated BSS.

The codegen builder already validates and applies the full relocation stream.
This helper deliberately performs a second, non-mutating pass over the same
verified metadata and counts only self-import relocations whose patch section is
executable and whose target section is BSS. No proprietary bytes or addresses
from player files are persisted in source control.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_relf_codegen_dol as relf_builder  # noqa: E402
import relf_input  # noqa: E402


def census(rel: bytes | bytearray,
           sections: list[relf_builder.Section],
           meta: dict[str, int]) -> dict[str, int]:
    import_offset = meta["import_offset"]
    import_size = meta["import_size"]
    relocation_floor = meta["relocation_offset"]
    dependencies = 0
    target_section_mask = 0
    patch_section_mask = 0

    for import_index in range(import_size // 8):
        entry = import_offset + import_index * 8
        import_module = relf_builder.be32(rel, entry)
        cursor = relf_builder.be32(rel, entry + 4)
        if cursor < relocation_floor or cursor > len(rel) - 8:
            raise RuntimeError(
                f"REL import {import_index} relocation pointer is invalid"
            )
        if import_module not in (0, relf_builder.REL_MODULE_ID):
            raise RuntimeError(
                f"relF imports unsupported REL module {import_module}; "
                "refusing guessed BSS census"
            )

        current_section = 0
        current_offset = 0
        saw_end = False
        while cursor <= len(rel) - 8:
            offset_delta = relf_builder.be16(rel, cursor)
            reloc_type = rel[cursor + 2]
            symbol_section = rel[cursor + 3]
            cursor += 8

            if reloc_type == relf_builder.R_DOLPHIN_SECTION:
                if symbol_section >= len(sections):
                    raise RuntimeError(
                        f"REL relocation selects bad section {symbol_section}"
                    )
                current_section = symbol_section
                current_offset = 0
                continue
            if reloc_type == relf_builder.R_DOLPHIN_END:
                saw_end = True
                break

            current_offset += offset_delta
            if current_offset > 0xFFFFFFFF:
                raise RuntimeError("REL relocation offset overflow")
            if reloc_type in (relf_builder.R_DOLPHIN_NOP,
                              relf_builder.R_PPC_NONE):
                continue
            if current_section >= len(sections):
                raise RuntimeError("REL relocation has no valid patch section")
            if import_module != relf_builder.REL_MODULE_ID:
                continue
            if symbol_section >= len(sections):
                raise RuntimeError(
                    f"REL self relocation references bad section {symbol_section}"
                )

            patch_section = sections[current_section]
            target_section = sections[symbol_section]
            if patch_section.executable and target_section.bss:
                dependencies += 1
                patch_section_mask |= 1 << current_section
                target_section_mask |= 1 << symbol_section

        if not saw_end:
            raise RuntimeError(
                f"REL import {import_index} relocation list has no END"
            )

    return {
        "exec_self_bss_relocations": dependencies,
        "exec_self_bss_patch_section_mask": patch_section_mask,
        "exec_self_bss_target_section_mask": target_section_mask,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--relf", type=Path)
    source.add_argument("--game-dir", type=Path)
    args = parser.parse_args()

    try:
        loaded = (
            relf_input.load_relf(args.relf.resolve())
            if args.relf
            else relf_input.load_from_game(args.game_dir.resolve())
        )
        image = bytearray(loaded.data)
        sections, meta = relf_builder.parse_sections(image)
        stats = census(image, sections, meta)
        print(
            "M14 relF executable->BSS relocation census: "
            f"count={stats['exec_self_bss_relocations']} "
            f"patch_mask=0x{stats['exec_self_bss_patch_section_mask']:X} "
            f"target_mask=0x{stats['exec_self_bss_target_section_mask']:X}"
        )
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"error: relF BSS relocation census failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
