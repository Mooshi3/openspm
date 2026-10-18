#!/usr/bin/env python3
"""Repair Aurora's original-console BP 0x65 TLUT size decoding for OpenSPM.

Nintendo's GXTlutSize value stored in BP LOADTLUT1 is not a literal palette
entry count. It is measured in blocks of 16 entries: GX_TLUT_16=1,
GX_TLUT_32=2, ... GX_TLUT_256=16, ... GX_TLUT_16K=1024. The field is 11 bits
wide in the SDK.

OpenSPM's first raw-Wii TLUT adaptation incorrectly decoded ten bits and used
`encoded + 1`, so a 16-entry palette became two entries and a 256-entry palette
became seventeen. Paletted C4/C8/C14X2 textures could therefore hash/upload an
incomplete TLUT and render as garbage even though their TPL assets were valid.

The later raw-TLUT routing repair replaces the entire intermediate handler. This
script therefore recognizes and validates that final routed implementation as an
already-applied state, making repeated CMake configures safe. Unknown source
drift still fails closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

OLD = "    slot.numEntries = static_cast<u16>(reg_get(value, 10, 10) + 1);\n"
NEW = (
    "    // BP LOADTLUT1 stores GXTlutSize in 16-entry units. Nintendo's SDK\n"
    "    // writes this as an 11-bit field (GX_TLUT_16=1 ... GX_TLUT_16K=1024),\n"
    "    // not as an entry-count-minus-one value.\n"
    "    const u32 encodedTlutSize = reg_get(value, 11, 10);\n"
    "    slot.numEntries = static_cast<u16>(encodedTlutSize * 16u);\n"
)
FINAL_ROUTING_SIGNATURE = "u32 raw_tlut_slot(u32 tmemOffset) noexcept {"
FINAL_SIZE_SIGNATURE = (
    "  const u32 encodedTlutSize = reg_get(value, 11, 10);\n"
    "  slot.numEntries = static_cast<u16>(encodedTlutSize * 16u);\n"
)


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
    path = source / "lib/gx/regs.cpp"
    text = path.read_text(encoding="utf-8")

    # The routing repair deliberately supersedes this intermediate transform.
    # Validate the final handler instead of failing because OLD/NEW vanished.
    if FINAL_ROUTING_SIGNATURE in text:
        if text.count(FINAL_ROUTING_SIGNATURE) != 1 or text.count(FINAL_SIZE_SIGNATURE) != 1:
            raise RuntimeError(
                "lib/gx/regs.cpp: raw TLUT routing is present but final 11-bit size decode is incomplete"
            )
        return "already-applied"

    old_count = text.count(OLD)
    new_count = text.count(NEW)

    if new_count == 1 and old_count == 0:
        return "already-applied"
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            "lib/gx/regs.cpp: expected exactly one OpenSPM raw TLUT size decode to repair"
        )
    if "slot.loadTlut0 = g_gxState.bpRegCache[0x64];" not in text or \
       "guest_memory::map(guestAddress, sourceBytes, &span)" not in text:
        raise RuntimeError("lib/gx/regs.cpp: raw-Wii TLUT guest-memory adaptation is not present")

    if not check_only:
        path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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
        print(f"error: Aurora raw TLUT size adaptation failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora raw TLUT size adaptation already applied")
    elif result == "verified":
        print("Aurora raw TLUT size adaptation verified")
    else:
        print("Aurora raw TLUT size adaptation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
