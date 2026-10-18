#!/usr/bin/env python3
"""Audit PPC call boundaries and materialize only measured native entries.

OpenSPM intentionally has no general PPC interpreter. LLVM regions therefore
need explicit native entry points whenever execution may resume at a call's
PC+4 from outside the current compiled region.

Blanket-partitioning every call return in the game would fragment hot LLVM code
and can hurt the throughput this backend is meant to recover. Dev59/dev60 showed
that adding one measured continuation at a time merely exposed the next return.
Dev61 then fixed every linked-call return inside GXBeginDisplayList and
GXEndDisplayList; the next player run completed 64 matched Begin/End pairs before
becoming stationary at PC=LR=0x800806CC. That proves the SDK bodies are no longer
the frontier. The remaining hole is in their display-list-builder caller.

Dev62 kept the continuation repair bounded to three surfaces:

* the already-proven GXBeginDisplayList/GXEndDisplayList SDK bodies;
* the one player function which contains the verified direct calls at
  0x80080620 -> GXBeginDisplayList and 0x80080648 -> GXEndDisplayList;
* no other SPM functions.

The caller's start is derived from the nearest preceding PowerPC
`stwu r1,-N(r1)` stack-frame prologue. Its exclusive end is the next stack-frame
prologue after at least one real non-linking BCLR return, allowing multiple early
returns without accidentally truncating the function. The newest measured
0x800806C8 -> LR=0x800806CC transfer is also verified explicitly. Generation
fails closed if any of those player-local shapes change.

Dev78 then measured 71.8 million executions of the direct-BL safety net in one
long player run. Its 1/4096 sampler retained two dominant exact source->target
pairs:

* 0x80D1F414 -> 0x802543F4 (relF source; long AFK wedding-heavy run);
* 0x80086848 -> 0x8012191C (main-DOL source).

Dev79 does not blanket-partition every call. It verifies those two measured BL
instructions against the private combined DOL+relF codegen image and promotes
the BL source PCs themselves to native entries. This fixes the exact dev32
failure mode: top-level redispatch can land on a direct BL instruction before
the BL has executed, so partitioning only PC+4 cannot help that event.

Only player-local addresses/opcodes are inspected; no Nintendo code bytes or
assets are persisted in source control.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import struct

GX_BEGIN = 0x80291DC8
GX_END = 0x80291E78
GX_END_SCAN_LIMIT = 0x200
PPC_BLR = 0x4E800020

CALLER_GX_BEGIN_CALL = 0x80080620
CALLER_GX_END_CALL = 0x80080648
CALLER_BACK_SCAN_LIMIT = 0x400
CALLER_FORWARD_SCAN_LIMIT = 0x800
DEV62_MEASURED_RETURN = 0x800806CC
DEV62_MEASURED_CALL = DEV62_MEASURED_RETURN - 4

# dev78 runtime sampler: exact direct-BL source PCs which reached the dev32
# safety net at high frequency. The first lives in the relocated relF text that
# generate_local_llvm.py appends to the private compiler-only DOL; the second is
# in the clean main DOL. Generation fails closed unless both still decode to the
# measured destinations.
DEV79_HOT_BL_SOURCES = (
    (
        0x80D1F414,
        0x802543F4,
        "openspm_dev79_hot_bl_source_80d1f414",
        "dev79 sampled relF direct-BL source",
    ),
    (
        0x80086848,
        0x8012191C,
        "openspm_dev79_hot_bl_source_80086848",
        "dev79 sampled main-DOL direct-BL source",
    ),
)

DEV107_DIRECT_BL_SOURCE_PREFIX = "openspm_dev107_direct_bl_source_"

MEASURED_RETURNS = (
    (
        0x80080630,
        "openspm_dev60_post_gxbegin_return_80080630",
        "dev60 post-GXBegin caller continuation",
    ),
    (
        DEV62_MEASURED_RETURN,
        "openspm_dev62_display_list_caller_return_800806cc",
        "dev62 display-list caller continuation",
    ),
)


def be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def text_sections(data: bytes) -> list[tuple[int, int, int]]:
    if len(data) < 0x100:
        raise RuntimeError("DOL is shorter than its 0x100-byte header")
    sections: list[tuple[int, int, int]] = []
    for index in range(7):
        file_off = be32(data, 0x00 + index * 4)
        address = be32(data, 0x48 + index * 4)
        size = be32(data, 0x90 + index * 4)
        if size == 0:
            continue
        if (address & 3) or (size & 3):
            raise RuntimeError(
                f"text section {index} is not instruction aligned: "
                f"addr=0x{address:08X} size=0x{size:X}"
            )
        if file_off > len(data) or size > len(data) - file_off:
            raise RuntimeError(
                f"text section {index} is outside DOL: "
                f"off=0x{file_off:X} size=0x{size:X}"
            )
        sections.append((file_off, address, size))
    if not sections:
        raise RuntimeError("DOL contains no executable text sections")
    return sections


def instruction_at(
    data: bytes, sections: list[tuple[int, int, int]], address: int
) -> int:
    for file_off, start, size in sections:
        if start <= address < start + size:
            return be32(data, file_off + address - start)
    raise RuntimeError(f"instruction 0x{address:08X} is outside executable DOL text")


def executable(sections: list[tuple[int, int, int]], address: int) -> bool:
    return (address & 3) == 0 and any(
        start <= address < start + size for _file_off, start, size in sections
    )


def is_linked_transfer(raw: int) -> bool:
    """Accept architectural PPC control transfers which write LR=PC+4."""
    primary = raw >> 26
    if (raw & 1) == 0:
        return False
    if primary in (16, 18):  # bc[l], b[l]
        return True
    if primary == 19:
        xo = (raw >> 1) & 0x3FF
        return xo in (16, 528)  # bclr[l], bcctr[l]
    return False


def is_nonlinking_bclr(raw: int) -> bool:
    if raw & 1:
        return False
    return (raw >> 26) == 19 and ((raw >> 1) & 0x3FF) == 16


def is_stack_frame_prologue(raw: int) -> bool:
    """Recognize stwu r1,negative(r1), the normal EABI non-leaf prologue."""
    return (
        (raw >> 26) == 37
        and ((raw >> 21) & 31) == 1
        and ((raw >> 16) & 31) == 1
        and (raw & 0x8000) != 0
    )


def direct_link_target(raw: int, pc: int) -> int | None:
    """Decode direct B/BL target; return None for non-direct/non-linked forms."""
    if (raw >> 26) != 18 or (raw & 1) == 0:
        return None
    displacement = raw & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    if raw & 2:  # AA
        return displacement & 0xFFFFFFFF
    return (pc + displacement) & 0xFFFFFFFF


def verify_direct_bl_source(
    data: bytes,
    sections: list[tuple[int, int, int]],
    call_pc: int,
    expected_target: int,
    label: str,
) -> int:
    """Verify one measured source is an executable direct BL to the expected target."""
    if not executable(sections, call_pc):
        raise RuntimeError(
            f"{label} 0x{call_pc:08X} is no longer executable in the player codegen image"
        )
    if not executable(sections, expected_target):
        raise RuntimeError(
            f"{label} target 0x{expected_target:08X} is no longer executable in the player codegen image"
        )
    raw = instruction_at(data, sections, call_pc)
    actual = direct_link_target(raw, call_pc)
    if actual != expected_target:
        actual_text = "none" if actual is None else f"0x{actual:08X}"
        raise RuntimeError(
            f"{label} shape changed: 0x{call_pc:08X} expected direct BL to "
            f"0x{expected_target:08X}, found raw=0x{raw:08X} target={actual_text}"
        )
    return raw


def derive_hot_bl_source_entries(
    data: bytes, sections: list[tuple[int, int, int]]
) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    for call_pc, target, name, label in DEV79_HOT_BL_SOURCES:
        verify_direct_bl_source(data, sections, call_pc, target, label)
        entries.append((call_pc, name))
    return entries


def scan_direct_bl_returns(data: bytes) -> set[int]:
    returns: set[int] = set()
    for file_off, address, size in text_sections(data):
        end = address + size
        for offset in range(0, size, 4):
            raw = be32(data, file_off + offset)
            if (raw >> 26) != 18 or (raw & 1) == 0:
                continue
            pc = address + offset
            continuation = pc + 4
            if continuation < end:
                returns.add(continuation)
    return returns


def scan_direct_bl_sources(data: bytes) -> dict[int, int]:
    """Return every exact executable direct-BL source and its decoded target."""
    sources: dict[int, int] = {}
    for file_off, address, size in text_sections(data):
        for offset in range(0, size, 4):
            pc = address + offset
            raw = be32(data, file_off + offset)
            target = direct_link_target(raw, pc)
            if target is not None:
                sources[pc] = target
    return sources


def find_function_return(
    data: bytes,
    sections: list[tuple[int, int, int]],
    start: int,
    scan_limit: int,
) -> int:
    """Return the exclusive end immediately after the first non-linking BCLR."""
    if not executable(sections, start):
        raise RuntimeError(f"function entry 0x{start:08X} is not executable")
    for delta in range(0, scan_limit, 4):
        pc = start + delta
        if not executable(sections, pc):
            break
        raw = instruction_at(data, sections, pc)
        if is_nonlinking_bclr(raw):
            return pc + 4
    raise RuntimeError(
        f"no non-linking BCLR return found for function 0x{start:08X} "
        f"within 0x{scan_limit:X} bytes"
    )


def derive_enclosing_nonleaf_function(
    data: bytes,
    sections: list[tuple[int, int, int]],
    anchor_pc: int,
    later_required_pc: int,
) -> tuple[int, int]:
    """Derive [start,end) around a measured non-leaf caller.

    Start is the nearest preceding EABI stack-frame prologue. End is the next
    stack-frame prologue after at least one real BLR and after later_required_pc.
    This tolerates multiple return blocks while staying bounded to one function.
    """
    start: int | None = None
    for delta in range(0, CALLER_BACK_SCAN_LIMIT + 1, 4):
        pc = anchor_pc - delta
        if not executable(sections, pc):
            break
        if is_stack_frame_prologue(instruction_at(data, sections, pc)):
            start = pc
            break
    if start is None:
        raise RuntimeError(
            f"no stack-frame prologue found within 0x{CALLER_BACK_SCAN_LIMIT:X} "
            f"bytes before caller anchor 0x{anchor_pc:08X}"
        )

    saw_return = False
    for delta in range(4, CALLER_FORWARD_SCAN_LIMIT + 1, 4):
        pc = start + delta
        if not executable(sections, pc):
            break
        raw = instruction_at(data, sections, pc)
        if is_nonlinking_bclr(raw):
            saw_return = True
            continue
        if saw_return and pc > later_required_pc and is_stack_frame_prologue(raw):
            return start, pc
    raise RuntimeError(
        f"no next stack-frame prologue found after a caller return within "
        f"0x{CALLER_FORWARD_SCAN_LIMIT:X} bytes of 0x{start:08X}"
    )


def linked_returns_in_range(
    data: bytes,
    sections: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    if start >= end or (start & 3) or (end & 3):
        raise RuntimeError(
            f"invalid continuation scan range [0x{start:08X},0x{end:08X})"
        )
    found: list[tuple[int, int]] = []
    for pc in range(start, end, 4):
        raw = instruction_at(data, sections, pc)
        if is_linked_transfer(raw):
            continuation = pc + 4
            if continuation < end and executable(sections, continuation):
                found.append((continuation, raw))
    return found


def read_map(path: Path) -> dict[int, str]:
    rows: dict[int, str] = {}
    if not path.is_file():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split(maxsplit=2)
        if len(fields) < 3:
            if line.strip():
                raise RuntimeError(f"malformed map line {line_number}: {line!r}")
            continue
        try:
            address = int(fields[0], 16)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid map address on line {line_number}: {fields[0]!r}"
            ) from exc
        rows.setdefault(address, fields[2])
    return rows


def _append_partition(path: Path, address: int, name: str) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{address:08X} 0 {name}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _verify_measured_returns(
    data: bytes, sections: list[tuple[int, int, int]]
) -> list[tuple[int, str]]:
    verified: list[tuple[int, str]] = []
    for return_pc, name, label in MEASURED_RETURNS:
        if not executable(sections, return_pc):
            raise RuntimeError(
                f"{label} 0x{return_pc:08X} is no longer executable in the player codegen image"
            )
        call_pc = return_pc - 4
        raw = instruction_at(data, sections, call_pc)
        if not is_linked_transfer(raw):
            raise RuntimeError(
                f"{label} shape changed: expected a linked PPC transfer at "
                f"0x{call_pc:08X} -> LR=0x{return_pc:08X}; found raw=0x{raw:08X}"
            )
        verified.append((return_pc, name))
    return verified


def derive_gx_continuations(
    data: bytes, sections: list[tuple[int, int, int]]
) -> tuple[int, list[tuple[int, str]]]:
    if not executable(sections, GX_BEGIN) or not executable(sections, GX_END):
        raise RuntimeError(
            "verified GXBeginDisplayList/GXEndDisplayList entries are not executable"
        )
    if GX_BEGIN >= GX_END:
        raise RuntimeError("GXBeginDisplayList/GXEndDisplayList ordering changed")

    gx_end_exclusive = find_function_return(
        data, sections, GX_END, GX_END_SCAN_LIMIT
    )
    if gx_end_exclusive <= GX_END:
        raise RuntimeError("GXEndDisplayList derived an empty body")

    entries: list[tuple[int, str]] = []
    for prefix, start, end in (
        ("gxbegin", GX_BEGIN, GX_END),
        ("gxend", GX_END, gx_end_exclusive),
    ):
        for return_pc, _raw in linked_returns_in_range(data, sections, start, end):
            entries.append(
                (
                    return_pc,
                    f"openspm_dev61_{prefix}_call_return_{return_pc:08x}",
                )
            )
    return gx_end_exclusive, entries


def derive_caller_continuations(
    data: bytes, sections: list[tuple[int, int, int]]
) -> tuple[int, int, list[tuple[int, str]]]:
    for call_pc, target, label in (
        (CALLER_GX_BEGIN_CALL, GX_BEGIN, "GXBeginDisplayList"),
        (CALLER_GX_END_CALL, GX_END, "GXEndDisplayList"),
    ):
        raw = instruction_at(data, sections, call_pc)
        actual = direct_link_target(raw, call_pc)
        if actual != target:
            actual_text = "none" if actual is None else f"0x{actual:08X}"
            raise RuntimeError(
                f"dev62 caller anchor changed: 0x{call_pc:08X} expected direct "
                f"BL to {label}=0x{target:08X}, found raw=0x{raw:08X} "
                f"target={actual_text}"
            )

    caller_start, caller_end = derive_enclosing_nonleaf_function(
        data,
        sections,
        CALLER_GX_BEGIN_CALL,
        DEV62_MEASURED_RETURN,
    )
    for required in (
        CALLER_GX_BEGIN_CALL,
        CALLER_GX_END_CALL,
        DEV62_MEASURED_CALL,
        DEV62_MEASURED_RETURN,
    ):
        if not (caller_start <= required < caller_end):
            raise RuntimeError(
                f"dev62 required caller PC 0x{required:08X} is outside derived "
                f"caller [0x{caller_start:08X},0x{caller_end:08X})"
            )

    entries: list[tuple[int, str]] = []
    for return_pc, _raw in linked_returns_in_range(
        data, sections, caller_start, caller_end
    ):
        entries.append(
            (
                return_pc,
                f"openspm_dev62_display_list_caller_return_{return_pc:08x}",
            )
        )
    return caller_start, caller_end, entries


def augment(dol_path: Path, map_path: Path) -> tuple[int, int, int]:
    """Audit all BLs; add only measured + bounded GX/caller continuations."""
    data = dol_path.read_bytes()
    sections = text_sections(data)
    returns = scan_direct_bl_returns(data)
    rows = read_map(map_path)
    covered = sum(1 for address in returns if address in rows)
    map_before = len(rows)

    verified = _verify_measured_returns(data, sections)
    hot_source_entries = derive_hot_bl_source_entries(data, sections)
    _gx_end_exclusive, gx_entries = derive_gx_continuations(data, sections)
    _caller_start, _caller_end, caller_entries = derive_caller_continuations(
        data, sections
    )

    wanted: dict[int, str] = {}
    for entry_pc, name in (*verified, *hot_source_entries, *gx_entries, *caller_entries):
        wanted.setdefault(entry_pc, name)

    added = 0
    for entry_pc, name in sorted(wanted.items()):
        if entry_pc in rows:
            continue
        _append_partition(map_path, entry_pc, name)
        rows[entry_pc] = name
        added += 1
    return covered, map_before, added


def augment_direct_bl_source_entries(
    dol_path: Path, map_path: Path
) -> tuple[int, int, int]:
    """Make every decoded direct-BL source a legal cold entry without partitioning it."""
    data = dol_path.read_bytes()
    sources = scan_direct_bl_sources(data)
    rows = read_map(map_path)
    added = 0
    already_present = 0
    for source in sorted(sources):
        if source in rows:
            already_present += 1
            continue
        name = f"{DEV107_DIRECT_BL_SOURCE_PREFIX}{source:08x}"
        _append_partition(map_path, source, name)
        rows[source] = name
        added += 1
    return len(sources), added, already_present


def census(dol_path: Path, map_path: Path) -> tuple[int, int, int]:
    data = dol_path.read_bytes()
    returns = scan_direct_bl_returns(data)
    rows = read_map(map_path)
    covered = sum(1 for address in returns if address in rows)
    return len(returns), covered, len(rows)


def self_test() -> None:
    # Broad direct-BL census and bounded helper behavior.
    data = bytearray(0x180)
    struct.pack_into(">I", data, 0x00, 0x100)
    struct.pack_into(">I", data, 0x48, 0x80001000)
    struct.pack_into(">I", data, 0x90, 0x60)
    words = [0x60000000] * 24
    words[0] = 0x9421FFE0  # stwu r1,-0x20(r1)
    words[2] = 0x48000009  # direct BL, return 0x8000100C
    words[4] = PPC_BLR
    words[6] = 0x48000001  # linked transfer, return 0x8000101C
    words[8] = PPC_BLR
    words[10] = 0x9421FFF0  # next function prologue
    words[12] = PPC_BLR
    struct.pack_into(">" + "I" * len(words), data, 0x100, *words)

    found = scan_direct_bl_returns(bytes(data))
    if 0x8000100C not in found or 0x8000101C not in found:
        raise RuntimeError(f"direct-BL continuation self-test failed: {found}")

    sources = scan_direct_bl_sources(bytes(data))
    if sources.get(0x80001008) != 0x80001010:
        raise RuntimeError(f"dev107 direct-BL source census failed: {sources}")

    sections = text_sections(bytes(data))
    # Generic dev79 source verification: words[2] is BL +8 from 0x80001008.
    raw = verify_direct_bl_source(
        bytes(data), sections, 0x80001008, 0x80001010,
        "dev79 synthetic hot BL source"
    )
    if raw != 0x48000009:
        raise RuntimeError(f"dev79 direct-BL source self-test read 0x{raw:08X}")

    end = find_function_return(bytes(data), sections, 0x80001000, 0x20)
    if end != 0x80001014:
        raise RuntimeError(
            f"bounded function-end self-test failed: end=0x{end:08X}"
        )
    caller_start, caller_end = derive_enclosing_nonleaf_function(
        bytes(data), sections, 0x80001008, 0x80001020
    )
    if (caller_start, caller_end) != (0x80001000, 0x80001028):
        raise RuntimeError(
            "caller-bound self-test failed: "
            f"[0x{caller_start:08X},0x{caller_end:08X})"
        )

    if not is_linked_transfer(0x48000001):
        raise RuntimeError("linked-transfer self-test rejected BL")
    if not is_linked_transfer(0x40000001):
        raise RuntimeError("linked-transfer self-test rejected BCL")
    if not is_linked_transfer(0x4E800021):
        raise RuntimeError("linked-transfer self-test rejected BLRL")
    if not is_linked_transfer(0x4E800421):
        raise RuntimeError("linked-transfer self-test rejected BCTRL")
    if is_linked_transfer(PPC_BLR):
        raise RuntimeError("linked-transfer self-test accepted BLR without link")
    if not is_nonlinking_bclr(PPC_BLR):
        raise RuntimeError("function-return self-test rejected BLR")
    if not is_stack_frame_prologue(0x9421FFE0):
        raise RuntimeError("stack-frame prologue self-test rejected stwu r1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dol", required=True, type=Path)
    parser.add_argument("--map", dest="map_path", required=True, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        self_test()
        if args.self_test:
            print("M14 LLVM dev79 hot-BL + dev62 bounded continuation self-test passed")
            return 0
        data = args.dol.resolve().read_bytes()
        sections = text_sections(data)
        gx_end_exclusive, gx_entries = derive_gx_continuations(data, sections)
        caller_start, caller_end, caller_entries = derive_caller_continuations(
            data, sections
        )
        hot_source_entries = derive_hot_bl_source_entries(data, sections)
        total, covered, map_entries = census(
            args.dol.resolve(), args.map_path.resolve()
        )
        print(
            "M14 LLVM direct-BL continuation census: "
            f"candidates={total} already_partitioned={covered} "
            f"semantic_map_entries={map_entries}; "
            f"dev61_gx_entries={len(gx_entries)} "
            f"gxbegin=[0x{GX_BEGIN:08X},0x{GX_END:08X}) "
            f"gxend=[0x{GX_END:08X},0x{gx_end_exclusive:08X}) "
            f"dev62_caller_entries={len(caller_entries)} "
            f"caller=[0x{caller_start:08X},0x{caller_end:08X}); "
            f"dev79_hot_bl_sources={len(hot_source_entries)} "
            f"hot=0x{DEV79_HOT_BL_SOURCES[0][0]:08X}->0x{DEV79_HOT_BL_SOURCES[0][1]:08X}," 
            f"0x{DEV79_HOT_BL_SOURCES[1][0]:08X}->0x{DEV79_HOT_BL_SOURCES[1][1]:08X}"
        )
        return 0
    except (OSError, RuntimeError, struct.error) as exc:
        print(f"error: direct-BL continuation audit failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
