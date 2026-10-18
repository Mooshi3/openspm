#!/usr/bin/env python3
"""Build a player-local codegen-only DOL containing the proven live relF text.

OpenSPM boots and verifies the clean R8PE01 main.dol. This tool does *not* make
another boot image. It creates a private compiler input under .openspm so one
DolRecomp invocation can see the same executable addresses the native runtime
will use.

The layout is deliberately revision-specific and fail-closed. Two controlled
LLVM proof runs on the verified R8PE01 revision established the deterministic
Revolution-MEM layout used here:

    module id       1
    REL version     3
    section count   18
    file size       0x31BEC8
    live file base  0x80CE7220
    exec section    1
    exec file off   0x000000DC
    exec address    0x80CE72FC
    exec size       0x00063D88
    live BSS base   0x80CE6340
    BSS size        0x00000EBC

The relF image and BSS are separate HEAP_MAIN allocations. OSLink maps
file-backed sections relative to the relF file pointer and maps the BSS section
to the separately supplied BSS pointer. Therefore BSS is intentionally *not*
modelled as base+fix_size.

Relocations follow the subset used by current DolRecomp. Module-0 imports are
already absolute main-DOL addresses; self imports resolve through the proven
live section addresses above. Imports from another REL are rejected rather than
guessed. Only the relocated executable section is appended to an unused DOL
text slot, and the parser requires it to be the REL's only non-empty executable
section. No Nintendo-derived output is committed or distributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import struct
import sys

REL_BASE = 0x80CE7220
REL_BSS_BASE = 0x80CE6340
REL_BSS_SIZE = 0x00000EBC
REL_MODULE_ID = 1
REL_VERSION = 3
REL_SECTION_COUNT = 18
REL_FILE_SIZE = 0x31BEC8
REL_EXEC_INDEX = 1
REL_EXEC_OFFSET = 0xDC
REL_EXEC_ADDRESS = REL_BASE + REL_EXEC_OFFSET
REL_EXEC_SIZE = 0x63D88
REL_PROLOG_OFFSET = 0x0003E11C
REL_PROLOG_ADDRESS = REL_BASE + REL_PROLOG_OFFSET

R_PPC_NONE = 0
R_PPC_ADDR32 = 1
R_PPC_ADDR24 = 2
R_PPC_ADDR16 = 3
R_PPC_ADDR16_LO = 4
R_PPC_ADDR16_HI = 5
R_PPC_ADDR16_HA = 6
R_PPC_ADDR14 = 7
R_PPC_ADDR14_BRTAKEN = 8
R_PPC_ADDR14_BRNTAKEN = 9
R_PPC_REL24 = 10
R_PPC_REL14 = 11
R_DOLPHIN_NOP = 201
R_DOLPHIN_SECTION = 202
R_DOLPHIN_END = 203


@dataclass
class Section:
    index: int
    file_offset: int
    size: int
    executable: bool
    bss: bool
    address: int


def be16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def be32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def put16(data: bytearray, off: int, value: int) -> None:
    struct.pack_into(">H", data, off, value & 0xFFFF)


def put32(data: bytearray, off: int, value: int) -> None:
    struct.pack_into(">I", data, off, value & 0xFFFFFFFF)


def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sections(rel: bytearray) -> tuple[list[Section], dict[str, int]]:
    if len(rel) != REL_FILE_SIZE:
        raise RuntimeError(
            f"relF size mismatch: got 0x{len(rel):X}, expected 0x{REL_FILE_SIZE:X}"
        )

    module_id = be32(rel, 0x00)
    section_count = be32(rel, 0x0C)
    section_table = be32(rel, 0x10)
    version = be32(rel, 0x1C)
    bss_size = be32(rel, 0x20)
    relocation_offset = be32(rel, 0x24)
    import_offset = be32(rel, 0x28)
    import_size = be32(rel, 0x2C)
    bss_alignment = be32(rel, 0x44) if len(rel) >= 0x48 else 4
    fix_size = be32(rel, 0x48) if version >= 3 and len(rel) >= 0x4C else len(rel)

    if module_id != REL_MODULE_ID:
        raise RuntimeError(f"relF module id mismatch: {module_id} != {REL_MODULE_ID}")
    if version != REL_VERSION:
        raise RuntimeError(f"relF version mismatch: {version} != {REL_VERSION}")
    if section_count != REL_SECTION_COUNT:
        raise RuntimeError(
            f"relF section count mismatch: {section_count} != {REL_SECTION_COUNT}"
        )
    if bss_size != REL_BSS_SIZE:
        raise RuntimeError(
            f"relF BSS size mismatch: 0x{bss_size:X} != 0x{REL_BSS_SIZE:X}"
        )
    if bss_alignment and REL_BSS_BASE % bss_alignment:
        raise RuntimeError(
            f"proven relF BSS base 0x{REL_BSS_BASE:08X} violates "
            f"header alignment 0x{bss_alignment:X}"
        )
    if section_table > len(rel) or section_count * 8 > len(rel) - section_table:
        raise RuntimeError("relF section table is outside the file")
    if not (0 < fix_size <= len(rel)):
        raise RuntimeError("relF fix_size is outside the file")
    if import_size % 8 or import_offset > len(rel) or import_size > len(rel) - import_offset:
        raise RuntimeError("relF import table is outside the file")
    if relocation_offset >= len(rel):
        raise RuntimeError("relF relocation table starts outside the file")

    sections: list[Section] = []
    for index in range(section_count):
        raw_offset = be32(rel, section_table + index * 8)
        size = be32(rel, section_table + index * 8 + 4)
        executable = bool(raw_offset & 1)
        file_offset = raw_offset & ~3
        bss = size != 0 and file_offset == 0

        if size == 0:
            address = 0
        elif bss:
            # Revolution OSLink assigns the separately supplied BSS allocation
            # to the REL's zero-offset BSS section. Do not synthesize it after
            # fix_size; v25 proved the live allocation is 0x80CE6340.
            address = REL_BSS_BASE
        else:
            if file_offset > len(rel) or size > len(rel) - file_offset:
                raise RuntimeError(f"relF section {index} data is outside the file")
            address = REL_BASE + file_offset

        sections.append(Section(index, file_offset, size, executable, bss, address))

    bss_sections = [section for section in sections if section.bss]
    if len(bss_sections) != 1 or bss_sections[0].size != REL_BSS_SIZE:
        details = ", ".join(
            f"{section.index}@0x{section.address:08X}+0x{section.size:X}"
            for section in bss_sections
        ) or "none"
        raise RuntimeError(
            "relF BSS-section shape changed; expected exactly one section of "
            f"0x{REL_BSS_SIZE:X} bytes, found {details}"
        )

    execute = sections[REL_EXEC_INDEX]
    if not execute.executable or execute.bss:
        raise RuntimeError("relF section 1 is not file-backed executable code")
    if execute.file_offset != REL_EXEC_OFFSET:
        raise RuntimeError(
            f"relF exec offset mismatch: 0x{execute.file_offset:X} != 0x{REL_EXEC_OFFSET:X}"
        )
    if execute.address != REL_EXEC_ADDRESS:
        raise RuntimeError(
            f"relF exec address mismatch: 0x{execute.address:08X} != 0x{REL_EXEC_ADDRESS:08X}"
        )
    if execute.size != REL_EXEC_SIZE:
        raise RuntimeError(
            f"relF exec size mismatch: 0x{execute.size:X} != 0x{REL_EXEC_SIZE:X}"
        )

    executable_sections = [
        section for section in sections if section.size != 0 and section.executable
    ]
    if len(executable_sections) != 1 or executable_sections[0].index != REL_EXEC_INDEX:
        details = ", ".join(
            f"{section.index}@0x{section.address:08X}+0x{section.size:X}"
            for section in executable_sections
        ) or "none"
        raise RuntimeError(
            "relF executable-section shape changed; expected only section 1, "
            f"found {details}"
        )

    return sections, {
        "section_table": section_table,
        "relocation_offset": relocation_offset,
        "import_offset": import_offset,
        "import_size": import_size,
        "fix_size": fix_size,
        "bss_alignment": bss_alignment,
        "bss_size": bss_size,
    }


def section_target(sections: list[Section], index: int, offset: int) -> int:
    if index >= len(sections):
        raise RuntimeError(f"relocation references bad section {index}")
    section = sections[index]
    if section.size == 0 or offset > section.size:
        raise RuntimeError(
            f"relocation references section {index} outside its range: 0x{offset:X}"
        )
    return (section.address + offset) & 0xFFFFFFFF


def require_patch(section: Section, offset: int, size: int) -> int:
    if section.bss or section.file_offset == 0:
        raise RuntimeError(f"relocation attempts to patch BSS section {section.index}")
    if offset > section.size or size > section.size - offset:
        raise RuntimeError(f"relocation patch is outside section {section.index}")
    return section.file_offset + offset


def apply_one(
    rel: bytearray,
    section: Section,
    offset: int,
    patch_address: int,
    reloc_type: int,
    target: int,
) -> None:
    if reloc_type == R_PPC_NONE:
        return

    patch_size = 2 if R_PPC_ADDR16 <= reloc_type <= R_PPC_ADDR16_HA else 4
    patch = require_patch(section, offset, patch_size)

    if reloc_type == R_PPC_ADDR32:
        put32(rel, patch, target)
    elif reloc_type == R_PPC_ADDR24:
        if target & 3:
            raise RuntimeError("ADDR24 target is not aligned")
        put32(rel, patch, (be32(rel, patch) & 0xFC000003) | (target & 0x03FFFFFC))
    elif reloc_type in (R_PPC_ADDR16, R_PPC_ADDR16_LO):
        put16(rel, patch, target)
    elif reloc_type == R_PPC_ADDR16_HI:
        put16(rel, patch, target >> 16)
    elif reloc_type == R_PPC_ADDR16_HA:
        put16(rel, patch, (target + 0x8000) >> 16)
    elif reloc_type in (R_PPC_ADDR14, R_PPC_ADDR14_BRTAKEN, R_PPC_ADDR14_BRNTAKEN):
        if target & 3:
            raise RuntimeError("ADDR14 target is not aligned")
        put32(rel, patch, (be32(rel, patch) & 0xFFFF0003) | (target & 0x0000FFFC))
    elif reloc_type in (R_PPC_REL24, R_PPC_REL14):
        delta = ((target - patch_address + 0x80000000) & 0xFFFFFFFF) - 0x80000000
        if delta & 3:
            raise RuntimeError("relative relocation target is not aligned")
        if reloc_type == R_PPC_REL24:
            if not (-0x02000000 <= delta <= 0x01FFFFFC):
                raise RuntimeError("REL24 target is out of range")
            put32(
                rel,
                patch,
                (be32(rel, patch) & 0xFC000003) | (delta & 0x03FFFFFC),
            )
        else:
            if not (-0x8000 <= delta <= 0x7FFC):
                raise RuntimeError("REL14 target is out of range")
            put32(
                rel,
                patch,
                (be32(rel, patch) & 0xFFFF0003) | (delta & 0x0000FFFC),
            )
    else:
        raise RuntimeError(f"unsupported REL relocation type {reloc_type}")


def relocate(
    rel: bytearray,
    sections: list[Section],
    meta: dict[str, int],
) -> dict[str, int]:
    import_offset = meta["import_offset"]
    import_size = meta["import_size"]
    relocation_floor = meta["relocation_offset"]
    applied = 0
    imports = 0

    for import_index in range(import_size // 8):
        entry = import_offset + import_index * 8
        import_module = be32(rel, entry)
        cursor = be32(rel, entry + 4)
        imports += 1

        if cursor < relocation_floor or cursor > len(rel) - 8:
            raise RuntimeError(f"REL import {import_index} relocation pointer is invalid")
        if import_module not in (0, REL_MODULE_ID):
            raise RuntimeError(
                f"relF imports unsupported REL module {import_module}; "
                "refusing guessed codegen image"
            )

        current_section = 0
        current_offset = 0
        saw_end = False
        while cursor <= len(rel) - 8:
            offset_delta = be16(rel, cursor)
            reloc_type = rel[cursor + 2]
            symbol_section = rel[cursor + 3]
            symbol_offset = be32(rel, cursor + 4)
            cursor += 8

            if reloc_type == R_DOLPHIN_SECTION:
                if symbol_section >= len(sections):
                    raise RuntimeError(f"REL relocation selects bad section {symbol_section}")
                current_section = symbol_section
                current_offset = 0
                continue
            if reloc_type == R_DOLPHIN_END:
                saw_end = True
                break

            current_offset += offset_delta
            if current_offset > 0xFFFFFFFF:
                raise RuntimeError("REL relocation offset overflow")
            if reloc_type == R_DOLPHIN_NOP:
                continue
            if current_section >= len(sections):
                raise RuntimeError("REL relocation has no valid patch section")

            patch_section = sections[current_section]
            patch_address = section_target(sections, current_section, current_offset)
            if import_module == 0:
                target = symbol_offset
            else:
                target = section_target(sections, symbol_section, symbol_offset)
            apply_one(
                rel,
                patch_section,
                current_offset,
                patch_address,
                reloc_type,
                target,
            )
            applied += 1

        if not saw_end:
            raise RuntimeError(f"REL import {import_index} relocation list has no END")

    return {"imports": imports, "relocations": applied}


def augment_dol(main_dol: bytes, exec_bytes: bytes) -> tuple[bytes, dict[str, int]]:
    if len(main_dol) < 0x100:
        raise RuntimeError("main.dol is shorter than the DOL header")
    out = bytearray(main_dol)

    for index in (0, 1):
        if be32(out, index * 4) == 0 or be32(out, 0x90 + index * 4) == 0:
            raise RuntimeError(f"main.dol expected populated text{index}")
    if be32(out, 2 * 4) != 0 or be32(out, 0x90 + 2 * 4) != 0:
        raise RuntimeError("main.dol text2 is already occupied")

    new_start = REL_EXEC_ADDRESS
    new_end = new_start + len(exec_bytes)
    for count, addr_base, size_base in ((7, 0x48, 0x90), (11, 0x64, 0xAC)):
        for index in range(count):
            address = be32(out, addr_base + index * 4)
            size = be32(out, size_base + index * 4)
            if not size:
                continue
            end = address + size
            if new_start < end and address < new_end:
                raise RuntimeError(
                    f"relF text overlaps existing DOL section at "
                    f"0x{address:08X}-0x{end:08X}"
                )

    file_offset = align_up(len(out), 0x20)
    if file_offset > len(out):
        out.extend(b"\0" * (file_offset - len(out)))
    out.extend(exec_bytes)
    put32(out, 0x00 + 2 * 4, file_offset)
    put32(out, 0x48 + 2 * 4, REL_EXEC_ADDRESS)
    put32(out, 0x90 + 2 * 4, len(exec_bytes))

    return bytes(out), {
        "text2_file_offset": file_offset,
        "text2_address": REL_EXEC_ADDRESS,
        "text2_size": len(exec_bytes),
    }


def find_relf(root: Path) -> Path:
    matches = [
        p for p in root.rglob("*") if p.is_file() and p.name.lower() == "relf.rel"
    ]
    if len(matches) != 1:
        listing = ", ".join(str(p) for p in matches[:8]) or "none"
        raise RuntimeError(
            f"expected exactly one relF.rel under {root}; found {len(matches)}: {listing}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-dol", required=True, type=Path)
    rel = parser.add_mutually_exclusive_group(required=True)
    rel.add_argument("--relf", type=Path)
    rel.add_argument(
        "--game-dir",
        type=Path,
        help="recursively locate the unique relF.rel in an extracted game",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    try:
        main_path = args.main_dol.resolve()
        rel_path = (
            args.relf.resolve()
            if args.relf
            else find_relf(args.game_dir.resolve())
        )
        main_bytes = main_path.read_bytes()
        rel_source = rel_path.read_bytes()
        rel_bytes = bytearray(rel_source)
        sections, meta = parse_sections(rel_bytes)
        relocation_stats = relocate(rel_bytes, sections, meta)
        exec_section = sections[REL_EXEC_INDEX]
        exec_bytes = bytes(
            rel_bytes[
                exec_section.file_offset : exec_section.file_offset + exec_section.size
            ]
        )
        augmented, dol_meta = augment_dol(main_bytes, exec_bytes)

        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(augmented)
        manifest = {
            "schema_version": 2,
            "purpose": "player-local codegen-only DOL; never boot or distribute",
            "main_dol": str(main_path),
            "main_dol_sha256": sha256(main_bytes),
            "relf": str(rel_path),
            "relf_sha256": sha256(rel_source),
            "rel_base": f"0x{REL_BASE:08X}",
            "rel_exec_address": f"0x{REL_EXEC_ADDRESS:08X}",
            "rel_exec_size": REL_EXEC_SIZE,
            "rel_bss_base": f"0x{REL_BSS_BASE:08X}",
            "rel_bss_size": REL_BSS_SIZE,
            "rel_prolog_address": f"0x{REL_PROLOG_ADDRESS:08X}",
            "relocated_exec_sha256": sha256(exec_bytes),
            "augmented_dol_sha256": sha256(augmented),
            **relocation_stats,
            **dol_meta,
        }
        manifest_path = (
            args.manifest.resolve()
            if args.manifest
            else output.with_suffix(output.suffix + ".json")
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "M14 relF codegen DOL ready: "
            f"rel=0x{REL_BASE:08X} "
            f"exec=0x{REL_EXEC_ADDRESS:08X}+0x{REL_EXEC_SIZE:X} "
            f"bss=0x{REL_BSS_BASE:08X}+0x{REL_BSS_SIZE:X} "
            f"prolog=0x{REL_PROLOG_ADDRESS:08X} "
            f"relocations={relocation_stats['relocations']} output={output}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: relF codegen DOL build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
