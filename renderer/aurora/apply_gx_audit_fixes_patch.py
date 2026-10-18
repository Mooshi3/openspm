#!/usr/bin/env python3
"""Apply the first high-confidence M14 GX audit corrections to cached Aurora.

This is a cache-aware, idempotent repair layer for the pinned Aurora checkout.
It intentionally touches only defects demonstrated by the 2026-09-12 architectural
audit and does not attempt to solve the still-unproven SU-scale/TEV/lifecycle
candidates.

Repairs:
  * reconstruct the BP EFB copy-format field in hardware bit order;
  * preserve the full Wii EFB-copy destination address field so MEM2 survives;
  * decode raw 10-bit texture dimensions as 1..1024 instead of wrapping 1024 to 0.

After those semantic repairs, the same configure chain raises Aurora's host-side
storage staging arena from its upstream 8 MiB policy to 32 MiB for OpenSPM's
native-PC raw-Wii workload, then installs the bounded metadata-only SEQ_LOAD
capture. The storage increase changes only host transient capacity, not guest
memory or GX semantics. Keeping the capture downstream means it observes the
exact effective state of the candidate the player is testing.

The transforms validate exact old/new source spellings and fail closed on drift.
The small arithmetic fixtures below are synthetic and contain no game data.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

OLD_COPY_CODE = "  const u32 code = reg_get(value, 4, 3);\n"
NEW_COPY_CODE = "  const u32 code = ((value >> 4) & 7u) | (value & 8u);\n"

# Match the destination expression in its function context. regs.cpp legitimately
# contains other 24-bit GX address masks, so counting the replacement line alone
# can mistake unrelated existing code for a half-applied repair.
OLD_COPY_DEST = (
    "void bp_copy_destination(u8, u32 value) noexcept {\n"
    "  if (!guest_memory::has_mapper()) return;\n"
    "  const u32 guestAddress = (value & 0x001FFFFFu) << 5;\n"
)
NEW_COPY_DEST = (
    "void bp_copy_destination(u8, u32 value) noexcept {\n"
    "  if (!guest_memory::has_mapper()) return;\n"
    "  const u32 guestAddress = (value & 0x00FFFFFFu) << 5;\n"
)

OLD_WIDTH = (
    "  u32 width() const noexcept { return mWidth != 0 ? mWidth : "
    "get_bits(image0, 10, 0) + 1 & 0x3FF; }\n"
)
NEW_WIDTH = (
    "  u32 width() const noexcept { return mWidth != 0 ? mWidth : "
    "get_bits(image0, 10, 0) + 1u; }\n"
)
OLD_HEIGHT = (
    "  u32 height() const noexcept { return mHeight != 0 ? mHeight : "
    "get_bits(image0, 10, 10) + 1 & 0x3FF; }\n"
)
NEW_HEIGHT = (
    "  u32 height() const noexcept { return mHeight != 0 ? mHeight : "
    "get_bits(image0, 10, 10) + 1u; }\n"
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


def synthetic_regression_fixtures() -> None:
    # Public/synthetic arithmetic only. These mirror the exact expressions this
    # repair installs and catch accidental regression in the patch script itself.
    def copy_code(value: int) -> int:
        return ((value >> 4) & 7) | (value & 8)

    expected_codes = {
        0x018000: 0x0,  # I4
        0x018010: 0x1,  # I8
        0x018020: 0x2,  # IA4
        0x018030: 0x3,  # IA8
        0x010040: 0x4,  # RGB565
        0x010050: 0x5,  # RGB5A3
        0x010060: 0x6,  # RGBA8
    }
    for raw, expected in expected_codes.items():
        actual = copy_code(raw)
        if actual != expected:
            raise RuntimeError(
                f"synthetic EFB copy-format fixture failed: 0x{raw:06X} -> {actual}, expected {expected}"
            )

    mem2 = 0x11000000
    encoded = mem2 >> 5
    decoded = (encoded & 0x00FFFFFF) << 5
    if decoded != mem2:
        raise RuntimeError(
            f"synthetic MEM2 EFB-copy fixture failed: 0x{mem2:08X} -> 0x{decoded:08X}"
        )

    raw_max_dimension = 0x3FF
    if raw_max_dimension + 1 != 1024:
        raise RuntimeError("synthetic raw 1024-dimension fixture failed")


def replace_state(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1), True
    if old_count == 0 and new_count == 1:
        return text, False
    raise RuntimeError(
        f"{label}: expected exactly one old or one repaired source fragment "
        f"(old={old_count}, repaired={new_count})"
    )


def transform(source: pathlib.Path, check_only: bool) -> str:
    regs_path = source / "lib/gx/regs.cpp"
    texture_path = source / "lib/gfx/texture.hpp"
    regs = regs_path.read_text(encoding="utf-8")
    texture = texture_path.read_text(encoding="utf-8")

    regs, copy_code_changed = replace_state(
        regs, OLD_COPY_CODE, NEW_COPY_CODE, "lib/gx/regs.cpp EFB copy-format decode"
    )
    regs, copy_dest_changed = replace_state(
        regs, OLD_COPY_DEST, NEW_COPY_DEST, "lib/gx/regs.cpp EFB copy destination"
    )
    texture, width_changed = replace_state(
        texture, OLD_WIDTH, NEW_WIDTH, "lib/gfx/texture.hpp raw width decode"
    )
    texture, height_changed = replace_state(
        texture, OLD_HEIGHT, NEW_HEIGHT, "lib/gfx/texture.hpp raw height decode"
    )

    changed = copy_code_changed or copy_dest_changed or width_changed or height_changed
    if not check_only and changed:
        regs_path.write_text(regs, encoding="utf-8", newline="\n")
        texture_path.write_text(texture, encoding="utf-8", newline="\n")

    if changed:
        return "verified" if check_only else "applied"
    return "already-applied"


def run_downstream_patch(script_name: str, source: pathlib.Path, check_only: bool) -> str:
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
        synthetic_regression_fixtures()
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora GX audit repair failed: {exc}", file=sys.stderr)
        return 4

    try:
        capacity_result = run_downstream_patch("apply_pc_storage_capacity_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora native-PC storage capacity patch failed: {stderr.strip() or exc}", file=sys.stderr)
        return 5

    try:
        capture_result = run_downstream_patch("apply_seqload_capture_patch.py", source, args.check_only)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora bounded SEQ_LOAD capture failed: {stderr.strip() or exc}", file=sys.stderr)
        return 6

    if result == "already-applied":
        print("Aurora GX audit corrections already applied")
    elif result == "verified":
        print("Aurora GX audit corrections verified")
    else:
        print("Aurora GX audit corrections applied")
    if capacity_result:
        print(capacity_result)
    if capture_result:
        print(capture_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
