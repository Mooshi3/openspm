#!/usr/bin/env python3
"""Instrument Aurora's exact non-owned ByteBuffer growth abort for M14.

Aurora maps each native frame's GPU staging regions into non-owned ByteBuffers.
Upstream intentionally calls abort() if one of those fixed mappings is asked to
grow. The current M14 Windows fail-fast stack contains ByteBuffer::reserve_extra
and gfx::push candidates, but the debugger stack scan is not a formal unwind.
Print the exact ByteBuffer length/capacity/request immediately before that one
upstream abort so the player run proves the exact failing capacity/request.

The probe directly proved the opening-story failure is the 8 MiB storage staging
buffer receiving an ~8.28 MiB single append. The same configure hook now also
runs the separate semantic guest-array-bounds transform: raw Wii CP array bases
must not reinterpret the generic mapper's whole remaining-RAM span as a declared
Aurora vertex-array size.

The ByteBuffer instrumentation itself does not change allocation/growth/failure
semantics. Guest-array behavior lives only in apply_guest_array_bounds_patch.py.
The small indexed-draw repair below is intentionally separate: the first guest-
array transform changed push_gx_draw's signature but missed Aurora's source-port-
only GX_AURORA_DRAW_INDEXED caller. A downstream CP-array-cache repair preserves
staged raw vertex data when the guest redundantly rewrites an unchanged CP base;
explicit GX vertex-cache invalidation remains authoritative for same-address
memory mutation. The per-frame raw-array reuse layer then deduplicates identical
content across A -> B -> A base switches and explicit invalidations by exact byte
comparison, without enlarging Aurora's fixed storage staging region. The chained
TLUT repairs first correct raw Wii BP 0x65 size units, then translate hardware
TLUT TMEM regions/selectors into Aurora's logical palette slots. The final audit
layer applies the first verified 2026-09-12 GX corrections: EFB copy-format bit
order, full MEM2-capable copy addresses, and raw 1024-pixel texture dimensions.
That audit layer also chains the bounded metadata-only SEQ_LOAD capture. This file
is intentionally a CMake configure dependency, so changing the downstream
candidate here forces a normal incremental M14 build to refresh cached Aurora
without deleting .openspm.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "[OpenSPM:AuroraByteBuffer] non-owned growth abort"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/internal.hpp"
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        return "already-applied"

    include_old = "#include <cstdint>\n#include <cstring>\n"
    include_new = "#include <cstdint>\n#include <cstdio>\n#include <cstring>\n"
    abort_old = (
        "      if (!m_owned) {\n"
        "        abort();\n"
        "      }\n"
    )
    abort_new = (
        "      if (!m_owned) {\n"
        "        std::fprintf(stderr,\n"
        "                     \"[OpenSPM:AuroraByteBuffer] non-owned growth abort this=%p data=%p length=%zu capacity=%zu requested=%zu zeroed=%u\\n\",\n"
        "                     static_cast<void*>(this), static_cast<void*>(m_data),\n"
        "                     m_length, m_capacity, size, zeroed ? 1u : 0u);\n"
        "        std::fflush(stderr);\n"
        "        abort();\n"
        "      }\n"
    )

    if text.count(include_old) != 1:
        raise RuntimeError("lib/internal.hpp: expected pinned include fragment exactly once")
    if text.count(abort_old) != 1:
        raise RuntimeError("lib/internal.hpp: expected pinned non-owned abort fragment exactly once")

    text = text.replace(include_old, include_new, 1)
    text = text.replace(abort_old, abort_new, 1)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def run_guest_array_bounds(source: pathlib.Path, check_only: bool) -> str:
    command = [
        sys.executable,
        str(HERE / "apply_guest_array_bounds_patch.py"),
        str(source),
    ]
    if check_only:
        command.append("--check-only")
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def repair_guest_array_indexed_draw(source: pathlib.Path, check_only: bool) -> str:
    """Complete the dev guest-array signature change on Aurora's custom indexed draw path."""
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    old = "      push_gx_draw(prim, fmt, vtxCount, vertRange, idxRange, indexCount);\n"
    new = (
        "      // Source-port Aurora arrays carry explicit sizes; no raw-Wii prefix\n"
        "      // requirements are needed for this custom indexed-draw command.\n"
        "      push_gx_draw(prim, fmt, vtxCount, vertRange, idxRange, indexCount, GuestArrayRequirements{});\n"
    )
    old_count = text.count(old)
    new_count = text.count(new)
    if new_count == 1 and old_count == 0:
        return "already-applied"
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            "lib/gx/command_processor.cpp: expected exactly one unrepaired GX_AURORA_DRAW_INDEXED push_gx_draw call"
        )
    if "guest_array_requirements" not in text:
        raise RuntimeError("lib/gx/command_processor.cpp: guest-array bounds transform is not present")
    if not check_only:
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def run_patch(script_name: str, source: pathlib.Path, check_only: bool) -> str:
    command = [sys.executable, str(HERE / script_name), str(source)]
    if check_only:
        command.append("--check-only")
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path, help="pinned Aurora checkout")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora ByteBuffer abort diagnostic adaptation failed: {exc}", file=sys.stderr)
        return 4

    try:
        array_result = run_guest_array_bounds(source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora guest-array bounds adaptation failed: {stderr.strip() or exc}", file=sys.stderr)
        return 5

    try:
        repair_result = repair_guest_array_indexed_draw(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora guest-array indexed-draw repair failed: {exc}", file=sys.stderr)
        return 6

    try:
        cp_cache_result = run_patch("apply_cp_array_cache_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora raw CP array-cache adaptation failed: {stderr.strip() or exc}", file=sys.stderr)
        return 7

    try:
        frame_reuse_result = run_patch("apply_guest_array_frame_reuse_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora raw guest-array frame-reuse adaptation failed: {stderr.strip() or exc}", file=sys.stderr)
        return 8

    try:
        tlut_result = run_patch("apply_tlut_size_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora raw TLUT size adaptation failed: {stderr.strip() or exc}", file=sys.stderr)
        return 9

    try:
        tlut_routing_result = run_patch("apply_raw_tlut_routing_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora raw TLUT routing adaptation failed: {stderr.strip() or exc}", file=sys.stderr)
        return 10

    try:
        audit_result = run_patch("apply_gx_audit_fixes_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora GX audit corrections failed: {stderr.strip() or exc}", file=sys.stderr)
        return 11

    if result == "already-applied":
        print("Aurora ByteBuffer abort diagnostic already applied")
    elif result == "verified":
        print("Aurora ByteBuffer abort diagnostic transform verified")
    else:
        print("Aurora ByteBuffer abort diagnostic applied")
    if array_result:
        print(array_result)
    if repair_result == "already-applied":
        print("Aurora guest-array indexed-draw repair already applied")
    elif repair_result == "verified":
        print("Aurora guest-array indexed-draw repair verified")
    else:
        print("Aurora guest-array indexed-draw repair applied")
    if cp_cache_result:
        print(cp_cache_result)
    if frame_reuse_result:
        print(frame_reuse_result)
    if tlut_result:
        print(tlut_result)
    if tlut_routing_result:
        print(tlut_routing_result)
    if audit_result:
        print(audit_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())