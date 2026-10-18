#!/usr/bin/env python3
"""Generate an exact OpenSPM LLVM public-dispatch policy map.

Pinned DolRecomp's public dispatcher calls ppc_host_call() before native lookup
for every materialized guest PC. dev79 measured hundreds of millions of
ordinary callbacks which return false.

dev80 removes only callbacks that are statically proven unnecessary. For every
instruction in the private combined DOL+relF compiler image this tool emits two
bits:

  bit 0: exact PC is proven to be a legal LLVM native entry;
  bit 1: exact PC must retain host-first priority.

Only (bit0=1, bit1=0) takes the new native-first fast path. Every other PC keeps
the historical host-first order. There is no learned/runtime-negative cache.

The legal-entry set is deliberately an under-approximation of DolRecomp's
region leaders, reconstructed from inputs that scanRegionLeaders() is guaranteed
to accept:

* every LLVM function/range start;
* every explicit symbol/map entry;
* program entry;
* every static B/BC target in executable code.

Ranges are reconstructed with the same chunk-128 partition algorithm used by
pinned DolRecomp, including linked-call target partitions and BLR+4 boundaries.
Missing some legal entries only loses optimization; it cannot change semantics.

Host-priority is deliberately semantic, not synonymous with partitioning:

* every semantic host target;
* compiler-map rows explicitly labeled as hosted callsites or relF allocation
  state-machine boundaries;
* exact retained bridge semantic coordinates for GX display-list, HOME, DSP
  and post-MTMSR completion.

Frame-phase telemetry is deliberately *not* host-priority in dev81: the compiler
now emits the timing hook at the native block entry. If native lookup ever fails,
the old host callback still runs as the normal fail-closed fallback.

Structural map rows (restart PCs, return continuations, scheduler reentries and
measured direct-BL source partitions) are native metadata, not host semantics.
A direct BL source therefore goes native-first when it is also a proven legal
entry. If that lookup ever fails, generated dispatch immediately falls back to
the unchanged host callback, so the dev32 direct-BL rescue remains authoritative.

The table is exact, not probabilistic: four PPC instructions are packed into one
byte (two bits per instruction). No Nintendo instruction bytes are persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
from pathlib import Path
import csv
import struct
import tempfile

import augment_llvm_bl_continuations as bl
import direct_calls

CHUNK_INSTRUCTIONS = 128
FLAG_NATIVE_ENTRY = 1
FLAG_HOST_PRIORITY = 2

EXTRA_HOST_PRIORITY = {
    0x80291DC8,  # GXBeginDisplayList
    0x80291E78,  # GXEndDisplayList
    0x801A7ABC,  # spmarioMain -> homebuttonMain call
    0x8023F994,  # homebuttonMain
    0x802871E4,  # first DSP task wait-loop semantic completion
    0x80271E34,  # OSDisableInterrupts post-MTMSR
    0x80271E48,  # OSEnableInterrupts post-MTMSR
    0x80271E6C,  # OSRestoreInterrupts post-MTMSR
}

SEMANTIC_MAP_PREFIXES = (
    "openspm_hostcall_call_",
    "openspm_relf_alloc_",
    "openspm_relf_bss_alloc_",
)


def semantic_map_addresses(rows: dict[int, str]) -> set[int]:
    return {
        address
        for address, name in rows.items()
        if any(name.startswith(prefix) for prefix in SEMANTIC_MAP_PREFIXES)
    }


def read_fallback_addresses(path: Path) -> set[int]:
    if not path.is_file():
        raise RuntimeError(f"missing DolRecomp LLVM fallback report: {path}")
    addresses: set[int] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"address", "raw", "opcode", "reason", "detail"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise RuntimeError(
                "unexpected DolRecomp fallback report columns: "
                + repr(reader.fieldnames)
            )
        for row in reader:
            raw_address = (row.get("address") or "").strip()
            if not raw_address:
                raise RuntimeError("DolRecomp fallback report contains empty address")
            try:
                address = int(raw_address, 16)
            except ValueError as exc:
                raise RuntimeError(
                    f"invalid fallback address {raw_address!r} in {path}"
                ) from exc
            if address & 3:
                raise RuntimeError(
                    f"unaligned fallback address 0x{address:08X} in {path}"
                )
            addresses.add(address)
    return addresses



@dataclass(frozen=True)
class TextSection:
    file_off: int
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


def _be32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def text_sections(data: bytes) -> list[TextSection]:
    return [TextSection(*row) for row in bl.text_sections(data)]


def _in_code(sections: list[TextSection], address: int) -> bool:
    return (address & 3) == 0 and any(s.start <= address < s.end for s in sections)


def _branch_target(raw: int, pc: int) -> tuple[int | None, bool]:
    op = raw >> 26
    lk = (raw & 1) != 0
    if op == 18:  # B/BL
        disp = raw & 0x03FFFFFC
        if disp & 0x02000000:
            disp -= 0x04000000
        target = (disp if (raw & 2) else pc + disp) & 0xFFFFFFFF
        return target, lk
    if op == 16:  # BC/BCL
        disp = raw & 0x0000FFFC
        if disp & 0x00008000:
            disp -= 0x00010000
        target = (disp if (raw & 2) else pc + disp) & 0xFFFFFFFF
        return target, lk
    return None, False


def _is_blr(raw: int) -> bool:
    return (
        (raw >> 26) == 19
        and ((raw >> 1) & 0x3FF) == 16
        and (raw & 1) == 0
        and ((raw >> 21) & 31) == 20
    )


def _instruction_rows(data: bytes, sections: list[TextSection]):
    for section in sections:
        for off in range(0, section.size, 4):
            pc = section.start + off
            yield section, pc, _be32(data, section.file_off + off)


def direct_bl_sources(data: bytes, sections: list[TextSection]) -> set[int]:
    result: set[int] = set()
    for _section, pc, raw in _instruction_rows(data, sections):
        target, linked = _branch_target(raw, pc)
        if linked and (raw >> 26) == 18 and target is not None:
            result.add(pc)
    return result


def collect_entry_points(
    data: bytes,
    sections: list[TextSection],
    map_points: set[int],
    program_entry: int,
) -> set[int]:
    points = {address for address in map_points if _in_code(sections, address)}
    if _in_code(sections, program_entry):
        points.add(program_entry)
    for _section, pc, raw in _instruction_rows(data, sections):
        target, _linked = _branch_target(raw, pc)
        if target is not None and _in_code(sections, target):
            points.add(target)
    return points


def collect_partition_points(
    data: bytes,
    sections: list[TextSection],
    map_points: set[int],
    program_entry: int,
) -> set[int]:
    points = {address for address in map_points if _in_code(sections, address)}
    if _in_code(sections, program_entry):
        points.add(program_entry)
    for _section, pc, raw in _instruction_rows(data, sections):
        target, linked = _branch_target(raw, pc)
        if linked and target is not None and _in_code(sections, target):
            points.add(target)
        if _is_blr(raw) and _in_code(sections, pc + 4):
            points.add(pc + 4)
    return points


def collect_range_starts(
    sections: list[TextSection], partition_points: set[int]
) -> set[int]:
    points = sorted(partition_points)
    result: set[int] = set()
    for section in sections:
        start = section.start
        while start < section.end:
            result.add(start)
            nominal_end = min(section.end, start + CHUNK_INSTRUCTIONS * 4)
            # Same semantics as next_llvm_partition_point(): first point strictly
            # greater than start and before nominal_end truncates the range.
            index = bisect_right(points, start)
            end = (
                points[index]
                if index < len(points) and points[index] < nominal_end
                else nominal_end
            )
            if end <= start:
                raise RuntimeError(
                    f"dev80 non-progressing range split at 0x{start:08X}"
                )
            start = end
    return result


def derive_sets(
    dol_path: Path,
    map_path: Path,
    *,
    fallback_addresses: set[int] | None = None,
) -> tuple[bytes, list[TextSection], set[int], set[int], dict[str, int]]:
    data = dol_path.read_bytes()
    sections = text_sections(data)
    if len(data) < 0xE4:
        raise RuntimeError("private codegen DOL is missing program-entry header")
    program_entry = _be32(data, 0xE0)
    map_rows = bl.read_map(map_path)
    mapped = set(map_rows.keys())
    semantic_map = semantic_map_addresses(map_rows)
    hosted = direct_calls.load_host_targets()
    direct_bl = direct_bl_sources(data, sections)

    entry_points = collect_entry_points(data, sections, mapped, program_entry)
    partitions = collect_partition_points(data, sections, mapped, program_entry)
    range_starts = collect_range_starts(sections, partitions)
    legal_before_fallbacks = entry_points | range_starts

    # DolRecomp's LLVM cold-entry switch explicitly excludes region leaders
    # whose DolIR terminator is FALLBACK. The generated *_fallbacks.csv is the
    # compiler's authoritative record of those blocks. A range can still exist
    # and be found by dolrecomp_find_original() while its exact fallback PC is
    # not restartable; marking such a PC native-first creates a zero-progress
    # redispatch loop. Subtract the compiler-reported set rather than attempting
    # to duplicate DolIR fallback classification here.
    fallback_addresses = fallback_addresses or set()
    fallback_in_code = {
        address for address in fallback_addresses if _in_code(sections, address)
    }
    legal = legal_before_fallbacks - fallback_in_code

    # Partition/restart metadata is not automatically a semantic boundary.
    # Keep only explicitly-hosted map rows plus true platform/service entries
    # host-first. Direct-BL sources deliberately remain native-first when legal;
    # patch_dispatch_header() retries the host callback if native lookup fails, which
    # preserves the dev32 rescue as a fallback rather than a permanent tax.
    host_priority = set(semantic_map)
    host_priority.update(hosted)
    host_priority.update(EXTRA_HOST_PRIORITY)

    # Host-priority entries outside this private code image are harmless but
    # cannot be represented by the exact per-text-section table. If such a
    # coordinate is ever also a native entry, generation must fail rather than
    # silently lose host priority.
    outside_host = {a for a in host_priority if not _in_code(sections, a)}
    risky = outside_host & legal
    if risky:
        raise RuntimeError(
            "dev80 host-priority/native-entry address escaped code table: "
            + ",".join(f"0x{x:08X}" for x in sorted(risky))
        )

    stats = {
        "sections": len(sections),
        "map": len(mapped),
        "host_targets": len(hosted),
        "direct_bl_sources": len(direct_bl),
        "semantic_map": len(semantic_map),
        "extra": len(EXTRA_HOST_PRIORITY),
        "entry_points": len(entry_points),
        "range_starts": len(range_starts),
        "legal_before_fallbacks": len(legal_before_fallbacks),
        "fallback_blocks": len(fallback_in_code),
        "fallback_candidates_removed": len(legal_before_fallbacks & fallback_in_code),
        "legal_entries": len(legal),
        "host_priority": len(host_priority),
        "legal_host_priority": len(legal & host_priority),
        "legal_fast": len(legal - host_priority),
    }
    return data, sections, legal, host_priority, stats


def _packed_section(
    section: TextSection, legal: set[int], host_priority: set[int]
) -> list[int]:
    instruction_count = section.size // 4
    packed = [0] * ((instruction_count + 3) // 4)
    for index in range(instruction_count):
        pc = section.start + index * 4
        flags = 0
        if pc in legal:
            flags |= FLAG_NATIVE_ENTRY
        if pc in host_priority:
            flags |= FLAG_HOST_PRIORITY
        packed[index >> 2] |= flags << ((index & 3) * 2)
    return packed


def write_header(
    path: Path,
    sections: list[TextSection],
    legal: set[int],
    host_priority: set[int],
) -> dict[str, int]:
    arrays: list[str] = []
    total_bytes = 0
    for index, section in enumerate(sections):
        packed = _packed_section(section, legal, host_priority)
        total_bytes += len(packed)
        rows = []
        for first in range(0, len(packed), 24):
            rows.append(
                "    " + ", ".join(f"UINT8_C(0x{value:02X})"
                                   for value in packed[first:first + 24])
            )
        arrays.append(
            f"static const uint8_t openspm_m14_dispatch_flags_{index}"
            f"[{len(packed)}] = {{\n"
            + ",\n".join(rows)
            + "\n};\n"
        )

    body = [
        "#ifndef OPENSPM_LOCAL_LLVM_DISPATCH_PRIORITY_H",
        "#define OPENSPM_LOCAL_LLVM_DISPATCH_PRIORITY_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define OPENSPM_M14_DISPATCH_NATIVE_ENTRY UINT32_C({FLAG_NATIVE_ENTRY})",
        f"#define OPENSPM_M14_DISPATCH_HOST_PRIORITY UINT32_C({FLAG_HOST_PRIORITY})",
        "",
        *arrays,
        "static inline uint32_t openspm_m14_dispatch_flags(uint32_t address) {",
        "    uint32_t index;",
    ]
    for i, section in enumerate(sections):
        body.extend([
            f"    if (address >= UINT32_C(0x{section.start:08X}) && "
            f"address < UINT32_C(0x{section.end:08X}) && (address & 3u) == 0u) {{",
            f"        index = (address - UINT32_C(0x{section.start:08X})) >> 2;",
            f"        return (openspm_m14_dispatch_flags_{i}[index >> 2] >> "
            "((index & 3u) * 2u)) & 3u;",
            "    }",
        ])
    body.extend([
        "    return 0u;",
        "}",
        "",
        "#endif",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body), encoding="utf-8", newline="\n")
    return {"bytes": total_bytes, "sections": len(sections)}


def _fast_block(address_expr: str) -> str:
    return (
        "    {\n"
        f"        const u32 openspm_flags = openspm_m14_dispatch_flags({address_expr});\n"
        "        if ((openspm_flags & OPENSPM_M14_DISPATCH_NATIVE_ENTRY) != 0u) {\n"
        "            if ((openspm_flags & OPENSPM_M14_DISPATCH_HOST_PRIORITY) != 0u &&\n"
        f"                ctx->host_call && ppc_host_call(ctx, {address_expr})) return 1;\n"
        f"            if (dolrecomp_call_original(ctx, {address_expr})) return 1;\n"
        "            /* Fail closed if compiler lookup and derived entry proof ever drift. */\n"
        "            if ((openspm_flags & OPENSPM_M14_DISPATCH_HOST_PRIORITY) == 0u &&\n"
        f"                ctx->host_call && ppc_host_call(ctx, {address_expr})) return 1;\n"
        "        } else {\n"
        f"            if (ctx->host_call && ppc_host_call(ctx, {address_expr})) return 1;\n"
        f"            if (dolrecomp_call_original(ctx, {address_expr})) return 1;\n"
        "        }\n"
        "    }"
    )


def patch_dispatch_header(header: Path) -> str:
    text = header.read_text(encoding="utf-8")
    include = '#include "openspm_local_llvm_dispatch_priority.h"\n'
    if include not in text:
        text = include + text

    address_old = (
        "    if (ctx->host_call && ppc_host_call(ctx, address)) return 1;\n"
        "    if (dolrecomp_call_original(ctx, address)) return 1;"
    )
    alias_old = (
        "        if (ctx->host_call && ppc_host_call(ctx, alias)) return 1;\n"
        "        if (dolrecomp_call_original(ctx, alias)) return 1;"
    )
    address_new = _fast_block("address")
    alias_new = "\n".join("    " + line if line else line
                           for line in _fast_block("alias").split("\n"))

    if "openspm_m14_dispatch_flags(address)" not in text:
        if text.count(address_old) != 1:
            raise RuntimeError(
                f"generated header address host/native pair count "
                f"{text.count(address_old)}, expected 1"
            )
        text = text.replace(address_old, address_new, 1)
    if "openspm_m14_dispatch_flags(alias)" not in text:
        if text.count(alias_old) != 1:
            raise RuntimeError(
                f"generated header alias host/native pair count "
                f"{text.count(alias_old)}, expected 1"
            )
        text = text.replace(alias_old, alias_new, 1)

    if text.count("openspm_m14_dispatch_flags(address)") != 1:
        raise RuntimeError("dev80 header address dispatch policy was not applied exactly once")
    if text.count("openspm_m14_dispatch_flags(alias)") != 1:
        raise RuntimeError("dev80 header alias dispatch policy was not applied exactly once")

    header.write_text(text, encoding="utf-8", newline="\n")
    return "applied"


def self_test() -> None:
    # Synthetic DOL: one 0x80-byte section, direct BL target and explicit map
    # entry. This proves exact flag packing plus fail-closed dispatcher-header rewriting.
    data = bytearray(0x180)
    struct.pack_into(">I", data, 0x00, 0x100)
    struct.pack_into(">I", data, 0x48, 0x80001000)
    struct.pack_into(">I", data, 0x90, 0x80)
    struct.pack_into(">I", data, 0xE0, 0x80001000)
    words = [0x60000000] * 32
    words[2] = 0x48000009  # BL 0x80001010 from 0x80001008
    words[4] = 0x4E800020  # BLR -> partition at +4
    struct.pack_into(">" + "I" * len(words), data, 0x100, *words)

    with tempfile.TemporaryDirectory(prefix="openspm-dev80-") as name:
        root = Path(name)
        dol = root / "test.dol"
        mp = root / "test.map"
        header = root / "openspm_local_llvm_dispatch_priority.h"
        generated_header = root / "generated.h"
        dol.write_bytes(data)
        mp.write_text(
            "80001008 0 openspm_dev79_hot_bl_source_test\n"
            "80001018 0 explicit_entry\n"
            "8000101C 0 openspm_hostcall_call_0_8029A500\n",
            encoding="utf-8",
        )

        fallback_csv = root / "test_fallbacks.csv"
        fallback_csv.write_text(
            "address,raw,opcode,reason,detail\n"
            "80001018,00000000,UNKNOWN,unsupported,\n",
            encoding="utf-8",
        )
        fallback_addresses = read_fallback_addresses(fallback_csv)
        _data, sections, legal, host, stats = derive_sets(
            dol, mp, fallback_addresses=fallback_addresses
        )
        for required in (0x80001000, 0x80001008, 0x80001010, 0x80001014, 0x8000101C):
            if required not in legal:
                raise RuntimeError(
                    f"dev80 legal-entry self-test lost 0x{required:08X}"
                )
        if 0x80001018 in legal:
            raise RuntimeError(
                "dev80 fallback-block PC was incorrectly kept as native-first legal"
            )
        if stats["fallback_candidates_removed"] != 1:
            raise RuntimeError(
                "dev80 fallback-removal self-test expected exactly one removed candidate"
            )
        if 0x80001008 in host:
            raise RuntimeError(
                "dev80 structural direct-BL source was incorrectly made host-priority"
            )
        if 0x8000101C not in host:
            raise RuntimeError("dev80 hosted-call map row lost host priority")
        packed = _packed_section(sections[0], legal, host)
        idx = (0x80001008 - sections[0].start) >> 2
        flags = (packed[idx >> 2] >> ((idx & 3) * 2)) & 3
        if flags != FLAG_NATIVE_ENTRY:
            raise RuntimeError(
                f"dev80 direct-BL source expected native-only flags, got {flags}"
            )
        hosted_idx = (0x8000101C - sections[0].start) >> 2
        hosted_flags = (
            packed[hosted_idx >> 2] >> ((hosted_idx & 3) * 2)
        ) & 3
        if hosted_flags != (FLAG_NATIVE_ENTRY | FLAG_HOST_PRIORITY):
            raise RuntimeError(
                f"dev80 hosted callsite expected native+host flags, got {hosted_flags}"
            )

        header_stats = write_header(header, sections, legal, host)
        if header_stats["bytes"] != 8:
            raise RuntimeError("dev80 packed-table byte count changed unexpectedly")

        generated_header.write_text(
            "static inline int dolrecomp_call(CPUState* ctx, u32 address) {\n"
            "    u32 alias;\n"
            "    if (ctx->host_call && ppc_host_call(ctx, address)) return 1;\n"
            "    if (dolrecomp_call_original(ctx, address)) return 1;\n"
            "    if (dolrecomp_physical_pc_alias(ctx, address, &alias)) {\n"
            "        if (ctx->host_call && ppc_host_call(ctx, alias)) return 1;\n"
            "        if (dolrecomp_call_original(ctx, alias)) return 1;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        patch_dispatch_header(generated_header)
        patched = generated_header.read_text(encoding="utf-8")
        if "openspm_m14_dispatch_flags(address)" not in patched:
            raise RuntimeError("dev80 header self-test lost address policy")
        if "openspm_m14_dispatch_flags(alias)" not in patched:
            raise RuntimeError("dev80 header self-test lost alias policy")
        if stats["legal_fast"] == 0:
            raise RuntimeError("dev80 synthetic map produced no fast native entry")


if __name__ == "__main__":
    self_test()
    print("M14 dev80 exact dispatch-policy self-test passed")
