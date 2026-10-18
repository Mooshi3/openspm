#!/usr/bin/env python3
"""Build dev87's exact conservative host-call prefilter.

The active LLVM public dispatcher historically calls CPUState::host_call before
native lookup for every materialized guest PC. dev86 proved that bypassing two
measured ordinary PCs is mechanically safe but not broad enough: the remaining
2-3M callbacks/s are diffuse and still correlate strongly with disp+main time.

dev87 does NOT reorder any address that could require host semantics. Instead it
generates one exact bit per executable guest instruction saying only:

    "this PC may need the existing host-first router"

The may-host set is intentionally a conservative superset:
* every player-local compiler/map entry that is not explicitly an entry-only
  synthetic cold-entry row;
* every checked-in platform host target;
* every direct BL source in the private combined DOL+relF image, preserving the
  generic direct-BL rescue safety net;
* retained exact GX/HOME/DSP/post-MTMSR semantic coordinates.

Addresses outside the known executable image also remain host-first. Only an
in-image PC with a proven zero bit skips ppc_host_call() and proceeds to the
unchanged dolrecomp_call_original() lookup. No learned cache, runtime state
inference, native-first execution or legal-entry reconstruction is involved.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import struct

import apply_llvm_dispatch_priority as legacy_policy
import augment_llvm_bl_continuations as bl
import direct_calls

SIGNATURE = "OPENSPM_M14_DEV87_EXACT_HOSTCALL_PREFILTER"
DEV90_SIGNATURE = "OPENSPM_M14_DEV90_STRUCTURAL_NATIVE_FIRST"
DEV103_SIGNATURE = "OPENSPM_M14_DEV103_ENTRYONLY_HOSTCALL_FILTER"
DEV107_SIGNATURE = "OPENSPM_M14_DEV107_DIRECT_BL_NATIVE_FIRST"
DEV108_SIGNATURE = "OPENSPM_M14_DEV108_PARTITION_ONLY_AB"

# These are not inferred range starts. Every address is an explicit player-local
# compiler-map entry that OpenSPM itself installed as a restart point. dev90
# gives only these exact structural PCs one fail-closed native-first attempt;
# if compiler lookup refuses the PC, generated dispatch immediately falls back
# to dev87's unchanged host-first router.
DEV90_NATIVE_FIRST_ENTRIES = {
    0x80D1F414: "openspm_dev79_hot_bl_source_80d1f414",
    0x80086848: "openspm_dev79_hot_bl_source_80086848",
}

# dev129: scheduler idle remains an exact compiler entry, but is no longer
# unconditionally native-first. The runtime host callback reads Nintendo's live
# RunQueueBits: empty queues side-exit immediately to the VI wait path; runnable
# queues return false and execute SelectThread natively. Force the prefilter bit
# on so this state-sensitive classifier always runs at the exact idle entry.
DEV129_DYNAMIC_IDLE_ENTRY = (
    0x8027551C,
    "openspm_scheduler_idle_reentry",
)

# dev103: dev57's generic address-taken map rows describe *legal LLVM entry
# points*, not host semantics. Treating all of them as "may host" forces every
# dynamic callback through CPUState::host_call before native lookup, creating a
# per-instance C round-trip that scales directly with NPC/display count.
#
# Keep dev97's HP-zero interval host-first until Game Over is revalidated in the
# same candidate; correctness-sensitive synthetic families are not generalized.
# If a dev57 address is independently semantic, it is re-added below by platform
# host targets, direct-BL sources, or EXTRA_MAY_HOST.
ENTRY_ONLY_MAP_PREFIXES = (
    "openspm_dev57_addr_taken_",
)


def map_rows_requiring_host(map_rows: dict[int, str]) -> set[int]:
    return {
        address
        for address, name in map_rows.items()
        if not any(name.startswith(prefix) for prefix in ENTRY_ONLY_MAP_PREFIXES)
    }


# Exact stateful callback coordinates that are not guaranteed to exist in every
# private compiler map revision. Extra false positives are harmless: they only
# retain the historical host-first callback.
EXTRA_MAY_HOST = {
    0x80291DC8,  # GXBeginDisplayList
    0x80291E78,  # GXEndDisplayList
    0x801A7ABC,  # spmarioMain -> homebuttonMain seam
    0x8023F994,  # homebuttonMain
    0x802871E4,  # first DSP task-init semantic completion
    0x80271E34,  # OSDisableInterrupts post-MTMSR completion
    0x80271E48,  # OSEnableInterrupts post-MTMSR completion
    0x80271E6C,  # OSRestoreInterrupts post-MTMSR completion
    0x8017B834,  # US2 seqSetSeq; dev95 observation-only game-over transition trace
}

ADDRESS_PAIR = (
    "    if (ctx->host_call && ppc_host_call(ctx, address)) return 1;\n"
    "    if (dolrecomp_call_original(ctx, address)) return 1;"
)
ALIAS_PAIR = (
    "        if (ctx->host_call && ppc_host_call(ctx, alias)) return 1;\n"
    "        if (dolrecomp_call_original(ctx, alias)) return 1;"
)
ADDRESS_REPLACEMENT = (
    "    if (openspm_m14_dev90_structural_native_first(address)) {\n"
    "        if (dolrecomp_call_original(ctx, address)) {\n"
    "            ++openspm_m14_dev90_native_first_hits;\n"
    "            return 1;\n"
    "        }\n"
    "        ++openspm_m14_dev90_native_first_fallbacks;\n"
    "    }\n"
    "    if (ctx->host_call && openspm_m14_dev87_should_call_host(address) &&\n"
    "        ppc_host_call(ctx, address)) return 1;\n"
    "    if (dolrecomp_call_original(ctx, address)) return 1;"
)
ALIAS_REPLACEMENT = (
    "        if (openspm_m14_dev90_structural_native_first(alias)) {\n"
    "            if (dolrecomp_call_original(ctx, alias)) {\n"
    "                ++openspm_m14_dev90_native_first_hits;\n"
    "                return 1;\n"
    "            }\n"
    "            ++openspm_m14_dev90_native_first_fallbacks;\n"
    "        }\n"
    "        if (ctx->host_call && openspm_m14_dev87_should_call_host(alias) &&\n"
    "            ppc_host_call(ctx, alias)) return 1;\n"
    "        if (dolrecomp_call_original(ctx, alias)) return 1;"
)


def derive_candidates(
    dol_path: Path,
    map_path: Path,
    *,
    fallback_addresses: set[int] | None = None,
) -> tuple[list[legacy_policy.TextSection], set[int], set[int], dict[str, int]]:
    data = dol_path.read_bytes()
    sections = legacy_policy.text_sections(data)
    map_rows = bl.read_map(map_path)

    map_may_host = map_rows_requiring_host(map_rows)
    entry_only_map_entries = len(map_rows) - len(map_may_host)
    if entry_only_map_entries == 0:
        raise RuntimeError(
            "dev103 expected dev57 address-taken entry-only map rows, found zero"
        )
    may_host = set(map_may_host)
    host_targets = set(direct_calls.load_host_targets())
    may_host.update(host_targets)

    direct_bl = legacy_policy.direct_bl_sources(data, sections)
    may_host.update(direct_bl)
    may_host.update(EXTRA_MAY_HOST)
    may_host.add(DEV129_DYNAMIC_IDLE_ENTRY[0])

    def in_code(address: int) -> bool:
        return (address & 3) == 0 and any(
            section.start <= address < section.end for section in sections
        )

    in_image = {address for address in may_host if in_code(address)}

    # dev90's remaining two fast entries must be exact explicit map rows, never guessed
    # from a containing range. They must also remain outside every semantic host
    # classification. The two BL sources are re-decoded against the player's
    # private compiler image so a changed branch target fails generation.
    semantic_map = legacy_policy.semantic_map_addresses(map_rows)
    fallback_addresses = fallback_addresses or set()
    dev107_native_first = {
        address
        for address in direct_bl
        if in_code(address)
        and address not in semantic_map
        and address not in host_targets
        and address not in EXTRA_MAY_HOST
        and address not in fallback_addresses
    }
    for address, expected_name in DEV90_NATIVE_FIRST_ENTRIES.items():
        actual_name = map_rows.get(address)
        if actual_name != expected_name:
            raise RuntimeError(
                f"dev90 structural entry 0x{address:08X} expected map row "
                f"{expected_name!r}, found {actual_name!r}"
            )
        if not in_code(address):
            raise RuntimeError(
                f"dev90 structural entry 0x{address:08X} is outside executable code"
            )
        if address in semantic_map or address in host_targets or address in EXTRA_MAY_HOST:
            raise RuntimeError(
                f"dev90 structural entry 0x{address:08X} collided with host semantics"
            )

    idle_address, idle_name = DEV129_DYNAMIC_IDLE_ENTRY
    actual_idle_name = map_rows.get(idle_address)
    if actual_idle_name != idle_name:
        raise RuntimeError(
            f"dev129 scheduler idle 0x{idle_address:08X} expected map row "
            f"{idle_name!r}, found {actual_idle_name!r}"
        )
    if not in_code(idle_address):
        raise RuntimeError(
            f"dev129 scheduler idle 0x{idle_address:08X} is outside executable code"
        )
    if idle_address in host_targets or idle_address in EXTRA_MAY_HOST:
        raise RuntimeError(
            "dev129 scheduler idle collided with a static semantic host target"
        )

    for call_pc, target, name, label in bl.DEV79_HOT_BL_SOURCES:
        if DEV90_NATIVE_FIRST_ENTRIES.get(call_pc) != name:
            raise RuntimeError(
                f"dev90 hot-BL map contract changed at 0x{call_pc:08X}"
            )
        bl.verify_direct_bl_source(data, [(s.file_off, s.start, s.size) for s in sections],
                                   call_pc, target, label)

    scheduler_raw = bl.instruction_at(
        data, [(s.file_off, s.start, s.size) for s in sections], 0x8027551C
    )
    if bl.is_linked_transfer(scheduler_raw):
        raise RuntimeError(
            "dev90 scheduler-idle structural entry became an LR-writing transfer"
        )

    stats = {
        "sections": len(sections),
        "map_entries": len(map_rows),
        "entry_only_map_entries": entry_only_map_entries,
        "host_targets": len(host_targets),
        "direct_bl_sources": len(direct_bl),
        "extra": len(EXTRA_MAY_HOST),
        "may_host_total": len(may_host),
        "may_host_in_image": len(in_image),
        "dev90_native_first_entries": len(DEV90_NATIVE_FIRST_ENTRIES),
        "dev129_dynamic_idle_entries": 1,
        "dev107_direct_bl_native_first": len(dev107_native_first),
        "dev107_direct_bl_fallback_excluded": len(direct_bl & fallback_addresses),
        "instructions": sum(section.size // 4 for section in sections),
    }
    return sections, in_image, dev107_native_first, stats


def _packed_section(
    section: legacy_policy.TextSection, may_host: set[int]
) -> list[int]:
    instruction_count = section.size // 4
    packed = [0] * ((instruction_count + 7) // 8)
    for index in range(instruction_count):
        pc = section.start + index * 4
        if pc in may_host:
            packed[index >> 3] |= 1 << (index & 7)
    return packed


def _render_bit_arrays(
    sections: list[legacy_policy.TextSection],
    addresses: set[int],
    prefix: str,
) -> tuple[list[str], int]:
    arrays: list[str] = []
    total_bytes = 0
    for index, section in enumerate(sections):
        packed = _packed_section(section, addresses)
        total_bytes += len(packed)
        rows = []
        for first in range(0, len(packed), 24):
            rows.append(
                "    " + ", ".join(
                    f"UINT8_C(0x{value:02X})" for value in packed[first:first + 24]
                )
            )
        arrays.append(
            f"static const uint8_t {prefix}_{index}[{len(packed)}] = {{\n"
            + ",\n".join(rows)
            + "\n};\n"
        )
    return arrays, total_bytes


def write_header(
    path: Path,
    sections: list[legacy_policy.TextSection],
    may_host: set[int],
    dev107_native_first: set[int] | None = None,
) -> dict[str, int]:
    dev107_native_first = dev107_native_first or set()
    host_arrays, host_bytes = _render_bit_arrays(
        sections, may_host, "openspm_m14_dev87_may_host"
    )
    bl_arrays, bl_bytes = _render_bit_arrays(
        sections, dev107_native_first, "openspm_m14_dev107_direct_bl"
    )

    body = [
        "#ifndef OPENSPM_LOCAL_LLVM_HOSTCALL_PREFILTER_H",
        "#define OPENSPM_LOCAL_LLVM_HOSTCALL_PREFILTER_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define {SIGNATURE} 1",
        f"#define {DEV90_SIGNATURE} 1",
        f"#define {DEV103_SIGNATURE} 1",
        f"#define {DEV107_SIGNATURE} 1",
        f"#define {DEV108_SIGNATURE} 1",
        "static uint64_t openspm_m14_dev87_bypass_count;",
        "static uint64_t openspm_m14_dev90_native_first_hits;",
        "static uint64_t openspm_m14_dev90_native_first_fallbacks;",
        "static uint64_t openspm_m14_dev107_direct_bl_native_hits;",
        "static uint64_t openspm_m14_dev107_direct_bl_native_fallbacks;",
        "",
        "static inline int openspm_m14_dev90_structural_native_first(uint32_t address) {",
        "    switch (address) {",
        *[
            f"    case UINT32_C(0x{address:08X}): return 1;"
            for address in sorted(DEV90_NATIVE_FIRST_ENTRIES)
        ],
        "    default: return 0;",
        "    }",
        "}",
        "",
        *bl_arrays,
        "static inline int openspm_m14_dev107_direct_bl_native_first(uint32_t address) {",
        "    uint32_t index;",
    ]
    for i, section in enumerate(sections):
        body.extend([
            f"    if (address >= UINT32_C(0x{section.start:08X}) && "
            f"address < UINT32_C(0x{section.end:08X}) && (address & 3u) == 0u) {{",
            f"        index = (address - UINT32_C(0x{section.start:08X})) >> 2;",
            f"        return (openspm_m14_dev107_direct_bl_{i}[index >> 3] >> "
            " (index & 7u)) & 1u;",
            "    }",
        ])
    body.extend([
        "    return 0;",
        "}",
        "",
        *host_arrays,
        "static inline int openspm_m14_dev87_may_host(uint32_t address) {",
        "    uint32_t index;",
    ])
    for i, section in enumerate(sections):
        body.extend([
            f"    if (address >= UINT32_C(0x{section.start:08X}) && "
            f"address < UINT32_C(0x{section.end:08X}) && (address & 3u) == 0u) {{",
            f"        index = (address - UINT32_C(0x{section.start:08X})) >> 2;",
            f"        return (openspm_m14_dev87_may_host_{i}[index >> 3] >> "
            " (index & 7u)) & 1u;",
            "    }",
        ])
    body.extend([
        "    /* Unknown/alias/out-of-image PCs always retain host-first routing. */",
        "    return 1;",
        "}",
        "",
        "static inline int openspm_m14_dev87_should_call_host(uint32_t address) {",
        "    if (openspm_m14_dev87_may_host(address)) return 1;",
        "    ++openspm_m14_dev87_bypass_count;",
        "    return 0;",
        "}",
        "",
        "#endif",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body), encoding="utf-8", newline="\n")
    return {
        "bytes": host_bytes + bl_bytes,
        "host_bytes": host_bytes,
        "dev107_bytes": bl_bytes,
        "sections": len(sections),
    }

def patch_dispatch_header(header: Path) -> str:
    text = header.read_text(encoding="utf-8")
    include = '#include "openspm_local_llvm_hostcall_prefilter.h"\n'
    if include not in text:
        text = include + text

    if (
        "openspm_m14_dev90_structural_native_first(address)" in text
        and "openspm_m14_dev90_structural_native_first(alias)" in text
        and "openspm_m14_dev107_direct_bl_native_first(address)" not in text
        and "openspm_m14_dev107_direct_bl_native_first(alias)" not in text
        and "openspm_m14_dev87_should_call_host(address)" in text
        and "openspm_m14_dev87_should_call_host(alias)" in text
    ):
        return "already-applied"

    # dev108 A/B: cached dev107 generated.h may already contain the broad
    # direct-BL native-first blocks. Remove those exact blocks before checking
    # for the historical host/native pair; entry legality and the dev107 census
    # table remain untouched.
    if "openspm_m14_dev107_direct_bl_native_first(address)" in text:
        old = ADDRESS_REPLACEMENT.replace(
            "    if (ctx->host_call && openspm_m14_dev87_should_call_host(address) &&\n",
            "    if (openspm_m14_dev107_direct_bl_native_first(address)) {\n"
            "        if (dolrecomp_call_original(ctx, address)) {\n"
            "            ++openspm_m14_dev107_direct_bl_native_hits;\n"
            "            return 1;\n"
            "        }\n"
            "        ++openspm_m14_dev107_direct_bl_native_fallbacks;\n"
            "    }\n"
            "    if (ctx->host_call && openspm_m14_dev87_should_call_host(address) &&\n",
        )
        if old in text:
            text = text.replace(old, ADDRESS_REPLACEMENT, 1)
    if "openspm_m14_dev107_direct_bl_native_first(alias)" in text:
        old = ALIAS_REPLACEMENT.replace(
            "        if (ctx->host_call && openspm_m14_dev87_should_call_host(alias) &&\n",
            "        if (openspm_m14_dev107_direct_bl_native_first(alias)) {\n"
            "            if (dolrecomp_call_original(ctx, alias)) {\n"
            "                ++openspm_m14_dev107_direct_bl_native_hits;\n"
            "                return 1;\n"
            "            }\n"
            "            ++openspm_m14_dev107_direct_bl_native_fallbacks;\n"
            "        }\n"
            "        if (ctx->host_call && openspm_m14_dev87_should_call_host(alias) &&\n",
        )
        if old in text:
            text = text.replace(old, ALIAS_REPLACEMENT, 1)

    if (
        "openspm_m14_dev90_structural_native_first(address)" in text
        and "openspm_m14_dev90_structural_native_first(alias)" in text
        and "openspm_m14_dev107_direct_bl_native_first(address)" not in text
        and "openspm_m14_dev107_direct_bl_native_first(alias)" not in text
        and "openspm_m14_dev87_should_call_host(address)" in text
        and "openspm_m14_dev87_should_call_host(alias)" in text
    ):
        header.write_text(text, encoding="utf-8", newline="\n")
        return "upgraded-dev108"

    if text.count(ADDRESS_PAIR) != 1:
        raise RuntimeError(
            "dev87 expected exactly one address host/native pair in generated.h; "
            f"found {text.count(ADDRESS_PAIR)}"
        )
    if text.count(ALIAS_PAIR) != 1:
        raise RuntimeError(
            "dev87 expected exactly one alias host/native pair in generated.h; "
            f"found {text.count(ALIAS_PAIR)}"
        )

    text = text.replace(ADDRESS_PAIR, ADDRESS_REPLACEMENT, 1)
    text = text.replace(ALIAS_PAIR, ALIAS_REPLACEMENT, 1)
    if text.count("openspm_m14_dev90_structural_native_first(address)") != 1:
        raise RuntimeError("dev90 address structural fast path was not applied exactly once")
    if text.count("openspm_m14_dev90_structural_native_first(alias)") != 1:
        raise RuntimeError("dev90 alias structural fast path was not applied exactly once")
    if "openspm_m14_dev107_direct_bl_native_first(address)" in text:
        raise RuntimeError("dev108 must not execute broad direct-BL native-first address path")
    if "openspm_m14_dev107_direct_bl_native_first(alias)" in text:
        raise RuntimeError("dev108 must not execute broad direct-BL native-first alias path")
    if text.count("openspm_m14_dev87_should_call_host(address)") != 1:
        raise RuntimeError("dev87 address prefilter patch was not applied exactly once")
    if text.count("openspm_m14_dev87_should_call_host(alias)") != 1:
        raise RuntimeError("dev87 alias prefilter patch was not applied exactly once")

    header.write_text(text, encoding="utf-8", newline="\n")
    return "applied"


def self_test() -> None:
    section_start = 0x80001000
    section_size = 0x80
    file_off = 0x100
    data = bytearray(file_off + section_size)
    struct.pack_into(">I", data, 0x00, file_off)
    struct.pack_into(">I", data, 0x48, section_start)
    struct.pack_into(">I", data, 0x90, section_size)
    struct.pack_into(">I", data, 0xE0, section_start)
    words = [0x60000000] * (section_size // 4)
    words[2] = 0x48000009  # direct BL at 0x80001008 -> 0x80001010
    struct.pack_into(">" + "I" * len(words), data, file_off, *words)

    with tempfile.TemporaryDirectory(prefix="openspm-dev87-") as name:
        root = Path(name)
        dol = root / "test.dol"
        mp = root / "test.map"
        generated = root / "generated.h"
        header = root / "openspm_local_llvm_hostcall_prefilter.h"
        dol.write_bytes(data)
        mp.write_text(
            "80001018 0 synthetic_semantic_or_structural_entry\n",
            encoding="utf-8",
        )

        sections = legacy_policy.text_sections(data)
        filtered = map_rows_requiring_host({
            0x80001004: "openspm_dev57_addr_taken_80001004",
            0x80001008: "openspm_dev97_hp0_motion24_interval_80001008",
            0x80001018: "synthetic_semantic_or_structural_entry",
        })
        if filtered != {0x80001008, 0x80001018}:
            raise RuntimeError(
                f"dev102 entry-only map filtering changed: {sorted(filtered)!r}"
            )
        # Independent semantic evidence must be able to re-add a filtered legal
        # entry. This mirrors derive_candidates() unioning direct BL/host sets.
        may_host = set(filtered)
        may_host.add(0x80001008)
        write_header(header, sections, may_host, {0x80001008})
        emitted = header.read_text(encoding="utf-8")
        if SIGNATURE not in emitted:
            raise RuntimeError("dev87 synthetic prefilter signature missing")
        if DEV90_SIGNATURE not in emitted:
            raise RuntimeError("dev90 synthetic structural signature missing")
        if DEV103_SIGNATURE not in emitted:
            raise RuntimeError("dev103 entry-only host-filter signature missing")
        if DEV107_SIGNATURE not in emitted:
            raise RuntimeError("dev107 direct-BL census signature missing")
        if DEV108_SIGNATURE not in emitted:
            raise RuntimeError("dev108 partition-only A/B signature missing")
        if "openspm_m14_dev107_direct_bl_native_first" not in emitted:
            raise RuntimeError("dev107 direct-BL native-first table missing")
        for pc in DEV90_NATIVE_FIRST_ENTRIES:
            if f"case UINT32_C(0x{pc:08X})" not in emitted:
                raise RuntimeError(
                    f"dev90 synthetic structural table lost 0x{pc:08X}"
                )

        generated.write_text(
            "static inline int dolrecomp_call(CPUState* ctx, u32 address) {\n"
            "    u32 alias;\n"
            + ADDRESS_PAIR
            + "\n    if (dolrecomp_physical_pc_alias(ctx, address, &alias)) {\n"
            + ALIAS_PAIR
            + "\n    }\n    return 0;\n}\n",
            encoding="utf-8",
        )
        patch_dispatch_header(generated)
        patched = generated.read_text(encoding="utf-8")
        if patched.count("openspm_m14_dev87_should_call_host(address)") != 1:
            raise RuntimeError("dev87 synthetic address filter missing")
        if patched.count("openspm_m14_dev87_should_call_host(alias)") != 1:
            raise RuntimeError("dev87 synthetic alias filter missing")
        native_first = patched.index(
            "openspm_m14_dev90_structural_native_first(address)"
        )
        host_filter = patched.index(
            "openspm_m14_dev87_should_call_host(address)"
        )
        if not (native_first < host_filter):
            raise RuntimeError(
                "dev90 structural native-first no longer precedes dev87 host routing"
            )
        if "++openspm_m14_dev90_native_first_fallbacks;" not in patched:
            raise RuntimeError("dev90 fail-closed fallback counter missing")
        if "openspm_m14_dev107_direct_bl_native_first(address)" in patched:
            raise RuntimeError("dev108 unexpectedly retained broad direct-BL address execution")
        if "openspm_m14_dev107_direct_bl_native_first(alias)" in patched:
            raise RuntimeError("dev108 unexpectedly retained broad direct-BL alias execution")

        dev107_packed = _packed_section(sections[0], {0x80001008})
        dev107_idx = (0x80001008 - section_start) >> 2
        if ((dev107_packed[dev107_idx >> 3] >> (dev107_idx & 7)) & 1) != 1:
            raise RuntimeError("dev107 direct-BL candidate lost from exact bitset")

        packed = _packed_section(sections[0], may_host)
        for pc in may_host:
            idx = (pc - section_start) >> 2
            if ((packed[idx >> 3] >> (idx & 7)) & 1) != 1:
                raise RuntimeError(f"dev87 synthetic candidate 0x{pc:08X} lost")
        ordinary = 0x8000100C
        idx = (ordinary - section_start) >> 2
        if ((packed[idx >> 3] >> (idx & 7)) & 1) != 0:
            raise RuntimeError("dev87 synthetic ordinary PC was not bypassable")


if __name__ == "__main__":
    self_test()
    print("M14 dev87 exact host-call prefilter self-test passed")
