#!/usr/bin/env python3
"""Model original Wii GX texture-copy half scaling in cached Aurora.

Player runtime at OpenSPM 5820200 exposed a very specific failure immediately
around SPM's flip transition: the raw renderer produced a 608x480 RGB565 EFB
copy, then the game sampled the same copy destination through a 304x240 RGB565
GXTexObj.  The temporary copy-ownership heuristic incorrectly treated that 2:1
shape relation as stale address reuse, but the underlying bug predates that
heuristic: OpenSPM's original-console BP 0x52 adaptation always set Aurora's
logical texture-copy destination size equal to the EFB source rectangle.

On real GX, texture-copy control bit 9 is the SDK's mip-map/half-scale filter:
false is a 1:1 copy; true is a 2:1 box-filtered copy. Dolphin's software GX
encoder models this as a right shift of both source dimensions when half_scale
is set. GXSetTexCopyDst also writes BP 0x4D with the destination tile stride.
The stride is useful evidence and is retained in Aurora's BP shadow, but it
cannot recover an arbitrary exact logical width when the allocation is tile
padded.  Use the hardware 2:1 floor semantics when bit 9 is set and preserve
the existing 1:1 behavior otherwise.

This is intentionally narrower than the rejected SU/cache experiment: it only
changes raw GXCopyTex destination dimensions.  Display-copy/XFB sizing,
ordinary texture coordinates, indirect texturing, ownership policy, and FIFO
scheduling are untouched.  The first half-scale copy logs its source/destination
sizes and BP 0x4D stride so the next player run can prove the path directly.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw GX half-scale texture copy active"

OLD = '''      g_gxState.texCopyDstWidth = static_cast<u32>(
          std::max(g_gxState.texCopySrc.width, 1));
      g_gxState.texCopyDstHeight = static_cast<u32>(
          std::max(g_gxState.texCopySrc.height, 1));
      g_gxState.texCopyDstWide = false;
      g_gxState.texCopyFmt = format;
'''

NEW = '''      const u32 openspmCopySourceWidth =
          static_cast<u32>(std::max(g_gxState.texCopySrc.width, 1));
      const u32 openspmCopySourceHeight =
          static_cast<u32>(std::max(g_gxState.texCopySrc.height, 1));
      const bool openspmHalfScale = reg_get(value, 1, 9) != 0;
      g_gxState.texCopyDstWidth = openspmHalfScale
          ? std::max<u32>(openspmCopySourceWidth >> 1u, 1u)
          : openspmCopySourceWidth;
      g_gxState.texCopyDstHeight = openspmHalfScale
          ? std::max<u32>(openspmCopySourceHeight >> 1u, 1u)
          : openspmCopySourceHeight;
      g_gxState.texCopyDstWide = false;
      g_gxState.texCopyFmt = format;

      if (openspmHalfScale) {
        static bool openspmHalfScaleReported = false;
        if (!openspmHalfScaleReported) {
          const u32 openspmStrideTiles = reg_get(g_gxState.bpRegCache[0x4D], 10, 0);
          Log.info(
              "M14 raw GX half-scale texture copy active src={}x{} dst={}x{} fmt={} stride_tiles={}",
              openspmCopySourceWidth, openspmCopySourceHeight,
              g_gxState.texCopyDstWidth, g_gxState.texCopyDstHeight,
              static_cast<u32>(format), openspmStrideTiles);
          openspmHalfScaleReported = true;
        }
      }
'''


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
    path = source / "lib" / "gx" / "regs.cpp"
    text = path.read_text(encoding="utf-8")

    new_count = text.count(NEW)
    old_count = text.count(OLD)
    if new_count == 1 and old_count == 0:
        return "already-applied"
    if new_count != 0 or old_count != 1:
        raise RuntimeError(
            "regs.cpp: expected exactly one raw texture-copy destination block "
            f"(old={old_count}, half-scale={new_count})"
        )

    updated = text.replace(OLD, NEW, 1)
    if SIGNATURE not in updated:
        raise RuntimeError("half-scale signature missing after transform")
    if "g_gxState.texCopyFmt = GX_TF_RGBA8;" not in updated:
        raise RuntimeError("display-copy XFB sizing block unexpectedly changed")

    if not check_only:
        path.write_text(updated, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != "--check-only"):
        print("usage: apply_raw_copy_half_scale_patch.py <aurora-source-dir> [--check-only]", file=sys.stderr)
        return 2

    source = pathlib.Path(sys.argv[1]).resolve()
    check_only = len(sys.argv) == 3
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 3
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 4

    try:
        result = transform(source, check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora raw GX half-scale copy adaptation failed: {exc}", file=sys.stderr)
        return 5

    print(f"Aurora raw GX texture-copy half scaling {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
