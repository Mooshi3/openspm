#!/usr/bin/env python3
"""Apply a measured, fail-closed native-first fast path to two hot ordinary PCs.

dev85 proved the legacy LLVM voluntary budget guard is not the source of the
multi-million-per-second public callbacks: exact budget exits remained zero while
ordinary callbacks stayed in the millions. The 1/4096 heavy-hitter sampler again
retained 0x8025CABC and 0x8025CAD4, both internal PCs in the player revision's
MSL strcmp body.

This patch is intentionally *not* dev80's broad dispatcher policy. It changes
only those two exact measured PCs. For either PC the generated public dispatcher
tries dolrecomp_call_original() first. If compiler lookup rejects the PC, control
immediately falls through to the unchanged host-first path. Every other address
keeps historical host-first ordering.

Generation also proves that the two PCs are executable, are not linked control
transfers requiring the BL-rescue semantic path, and do not collide with any
known static OpenSPM semantic host target/map row.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import struct

import augment_llvm_bl_continuations as bl
import direct_calls

SIGNATURE = "OPENSPM_M14_DEV86_HOT_ORDINARY_NATIVE_FIRST"
HOT_ORDINARY_PCS = (0x8025CABC, 0x8025CAD4)

SEMANTIC_MAP_PREFIXES = (
    "openspm_hostcall_call_",
    "openspm_relf_alloc_",
    "openspm_relf_bss_alloc_",
)

# Exact retained bridge semantic coordinates that are not necessarily represented
# as host-target rows in every private map revision.
EXTRA_SEMANTIC_PCS = {
    0x801A8A9C,  # DEMOBeforeRender telemetry entry
    0x801A7D1C,  # spmarioDisp telemetry entry
    0x801A7AAC,  # spmarioMain telemetry entry
    0x801A8B64,  # DEMODoneRender telemetry entry
    0x80291DC8,  # GXBeginDisplayList
    0x80291E78,  # GXEndDisplayList
    0x801A7ABC,  # spmarioMain -> homebuttonMain seam
    0x8023F994,  # homebuttonMain
    0x802871E4,  # first DSP task-init completion
    0x80271E34,  # post-MTMSR interrupt completion
    0x80271E48,
    0x80271E6C,
}

ADDRESS_PAIR = (
    "    if (ctx->host_call && ppc_host_call(ctx, address)) return 1;\n"
    "    if (dolrecomp_call_original(ctx, address)) return 1;"
)

FAST_ADDRESS_PAIR = (
    f"    /* {SIGNATURE}\n"
    "     * Measured-only fast path. These two exact player PCs dominated the\n"
    "     * ordinary callback sample and are build-time proven non-semantic.\n"
    "     * If native lookup ever rejects either PC, immediately retain the\n"
    "     * historical host-first path below. */\n"
    "    if ((address == 0x8025CABCu || address == 0x8025CAD4u) &&\n"
    "        dolrecomp_call_original(ctx, address)) return 1;\n"
    + ADDRESS_PAIR
)


def semantic_map_addresses(rows: dict[int, str]) -> set[int]:
    return {
        address
        for address, name in rows.items()
        if any(name.startswith(prefix) for prefix in SEMANTIC_MAP_PREFIXES)
    }


def verify_private_inputs(dol_path: Path, map_path: Path) -> dict[int, int]:
    data = dol_path.read_bytes()
    sections = bl.text_sections(data)
    rows = bl.read_map(map_path)
    semantic = set(direct_calls.load_host_targets())
    semantic.update(semantic_map_addresses(rows))
    semantic.update(EXTRA_SEMANTIC_PCS)

    raw_by_pc: dict[int, int] = {}
    for pc in HOT_ORDINARY_PCS:
        if not bl.executable(sections, pc):
            raise RuntimeError(
                f"dev86 hot ordinary PC 0x{pc:08X} is no longer executable"
            )
        if pc in semantic:
            raise RuntimeError(
                f"dev86 hot ordinary PC 0x{pc:08X} collides with a semantic host boundary"
            )
        raw = bl.instruction_at(data, sections, pc)
        if bl.is_linked_transfer(raw):
            raise RuntimeError(
                f"dev86 hot ordinary PC 0x{pc:08X} became an LR-writing control "
                f"transfer raw=0x{raw:08X}; refusing to bypass host-first rescue semantics"
            )
        raw_by_pc[pc] = raw
    return raw_by_pc


def patch_dispatch_header(header: Path) -> str:
    text = header.read_text(encoding="utf-8")
    if SIGNATURE in text:
        if text.count(SIGNATURE) != 1:
            raise RuntimeError("dev86 dispatcher fast-path marker count changed")
        if text.count("address == 0x8025CABCu") != 1 or text.count(
            "address == 0x8025CAD4u"
        ) != 1:
            raise RuntimeError("dev86 dispatcher fast path lost one measured PC")
        return "already-applied"

    if text.count(ADDRESS_PAIR) != 1:
        raise RuntimeError(
            "generated dispatcher host/native address pair count "
            f"{text.count(ADDRESS_PAIR)}, expected 1"
        )

    patched = text.replace(ADDRESS_PAIR, FAST_ADDRESS_PAIR, 1)
    if patched.count(SIGNATURE) != 1:
        raise RuntimeError("dev86 dispatcher fast path was not applied exactly once")
    header.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def apply(dol_path: Path, map_path: Path, header: Path) -> dict[int, int]:
    raw_by_pc = verify_private_inputs(dol_path, map_path)
    patch_dispatch_header(header)
    return raw_by_pc


def self_test() -> None:
    # Minimal private DOL section spanning both measured PCs. Instructions are
    # ordinary non-linking PPC words; no Nintendo bytes are embedded.
    section_start = 0x8025C000
    section_size = 0xC00
    file_off = 0x100
    data = bytearray(file_off + section_size)
    struct.pack_into(">I", data, 0x00, file_off)
    struct.pack_into(">I", data, 0x48, section_start)
    struct.pack_into(">I", data, 0x90, section_size)
    struct.pack_into(">I", data, 0xE0, section_start)
    for pc in HOT_ORDINARY_PCS:
        struct.pack_into(">I", data, file_off + pc - section_start, 0x60000000)

    with tempfile.TemporaryDirectory(prefix="openspm-dev86-") as name:
        root = Path(name)
        dol = root / "test.dol"
        mp = root / "test.map"
        header = root / "generated.h"
        dol.write_bytes(data)
        mp.write_text("8025C000 0 synthetic_entry\n", encoding="utf-8")
        header.write_text(
            "static inline int dolrecomp_call(CPUState* ctx, u32 address) {\n"
            + ADDRESS_PAIR
            + "\n    return 0;\n}\n",
            encoding="utf-8",
        )

        # self_test intentionally avoids direct_calls' repository-owned target
        # table and exercises the header rewrite + DOL instruction proof shape.
        sections = bl.text_sections(dol.read_bytes())
        for pc in HOT_ORDINARY_PCS:
            raw = bl.instruction_at(dol.read_bytes(), sections, pc)
            if bl.is_linked_transfer(raw):
                raise RuntimeError("dev86 synthetic hot PC unexpectedly linked")
        patch_dispatch_header(header)
        patched = header.read_text(encoding="utf-8")
        if patched.count(SIGNATURE) != 1:
            raise RuntimeError("dev86 synthetic dispatcher marker missing")
        if patched.index("dolrecomp_call_original(ctx, address)") > patched.index(
            "ppc_host_call(ctx, address)"
        ):
            raise RuntimeError("dev86 synthetic dispatcher did not put hot native attempt first")


if __name__ == "__main__":
    self_test()
    print("M14 dev86 measured hot-ordinary dispatcher fast-path self-test passed")
