#!/usr/bin/env python3
"""Honor original-Wii BP texture-cache invalidation in the raw GX path.

Nintendo's GXInvalidateTexAll emits two writes to BP register 0x66
(TMEMTEXINVALIDATE): 0x66001000 and 0x66001100.  Aurora's source-port
GXInvalidateTexAll is intentionally a no-op because its high-level texture
objects carry host-side version metadata, but an original Wii FIFO has no such
metadata.  CPU-written texture memory therefore needs the hardware invalidate
command to force Aurora to re-resolve the bound guest bytes.

For the original-console path only (guest-memory mapper active), treat any BP
0x66 write as a conservative full texture-binding invalidation.  Aurora's
content cache remains content-addressed, so unchanged textures still reuse
existing host textures; only the binding/source resolution is forced again.
Repeated writes are side effects and must not be shadow-register deduplicated.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 original-Wii BP 0x66 texture invalidate active"

HANDLER_ANCHOR = "// TEV color combiner stages (0xC0, 0xC2, ... 0xDE)\n"
HANDLER = r'''std::uint64_t sM14RawTexInvalidateCount = 0;

// Original Wii TMEM texture-cache invalidate (BP 0x66). Source-port Aurora
// normally relies on GX_AURORA texture-object versions and therefore makes
// GXInvalidateTexAll a no-op. A raw console FIFO has no host metadata: when
// guest code modifies texture bytes in-place, this command is the coherence
// boundary that makes the next draw observe those bytes.
void bp_tex_invalidate(u8, u32 value) noexcept {
  if (!guest_memory::has_mapper()) return;
  texture::invalidate_bindings();
  const std::uint64_t n = ++sM14RawTexInvalidateCount;
  if (n <= 8u || (n & (n - 1u)) == 0u) {
    Log.info("M14 original-Wii BP 0x66 texture invalidate active n={} value=0x{:06X}",
             n, value & 0x00FFFFFFu);
  }
}

'''

REG_OLD = "  regs[0x65] = {bp_tlut_load, DirtyTextures, /* alwaysHandle */ true};\n"
REG_NEW = REG_OLD + "  regs[0x66] = {bp_tex_invalidate, DirtyTextures, /* alwaysHandle */ true};\n"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/regs.cpp"
    text = path.read_text(encoding="utf-8")

    handler_present = SIGNATURE in text
    reg_present = "regs[0x66] = {bp_tex_invalidate, DirtyTextures, /* alwaysHandle */ true};" in text
    if handler_present and reg_present:
        if text.count(SIGNATURE) != 1 or text.count("regs[0x66] = {bp_tex_invalidate") != 1:
            raise RuntimeError("lib/gx/regs.cpp: duplicate raw texture-invalidate adaptation")
        return "already-applied"
    if handler_present or reg_present:
        raise RuntimeError("lib/gx/regs.cpp: partial raw texture-invalidate adaptation")

    if text.count(HANDLER_ANCHOR) != 1:
        raise RuntimeError("lib/gx/regs.cpp: expected TEV anchor exactly once")
    if text.count(REG_OLD) != 1:
        raise RuntimeError("lib/gx/regs.cpp: expected BP 0x65 registration exactly once")

    text = text.replace(HANDLER_ANCHOR, HANDLER + HANDLER_ANCHOR, 1)
    text = text.replace(REG_OLD, REG_NEW, 1)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
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
        print(f"error: Aurora raw texture-invalidate adaptation failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora raw BP texture invalidation already applied")
    elif result == "verified":
        print("Aurora raw BP texture invalidation verified")
    else:
        print("Aurora raw BP texture invalidation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
