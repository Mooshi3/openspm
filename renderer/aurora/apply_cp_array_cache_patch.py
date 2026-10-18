#!/usr/bin/env python3
"""Preserve staged raw GX vertex arrays across identical CP base-register writes.

The original-console CP array-base registers do not themselves invalidate the
vertex cache. GX has a separate invalidate-vertex-cache command for that. The
raw OpenSPM adapter previously remapped the same guest base and unconditionally
cleared Aurora's staged cachedRange on every write, which can make complex model
rendering repeatedly upload the same tens-of-KiB array prefixes until Aurora's
fixed per-frame storage staging region is exhausted.

This patch is deliberately narrow: after mapping an original-Wii CP array base,
if the resulting host span is identical to the already-active raw guest mapping,
leave cachedRange and dirty state intact. A real base/mapping transition keeps
the existing invalidation path. Explicit GX_CMD_INVL_VC remains authoritative
for same-address guest-memory mutation.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw CP array-base duplicate write preserved staged cache"

OLD = r'''  if (array.le) g_gxState.dirty |= DirtyPipeline;
  array.data = span.data;
  // span.size is only the fail-closed maximum bytes reachable from the
  // raw CP base. It is not the logical GX vertex-array length.
  array.size = span.size;
  array.le = false;
  array.guestMapped = true;
  array.cachedRange = {};
  g_gxState.dirty |= DirtyImmediates;
'''

NEW = r'''  // Rewriting an unchanged CP array base is state redundancy, not a
  // vertex-cache invalidation. Preserve the staged bytes until GX_CMD_INVL_VC,
  // a real base change, or a frame boundary explicitly invalidates them.
  if (array.data == span.data && array.size == span.size && !array.le && array.guestMapped) {
    static bool m14DuplicateBaseReported = false;
    if (!m14DuplicateBaseReported) {
      Log.info("M14 raw CP array-base duplicate write preserved staged cache");
      m14DuplicateBaseReported = true;
    }
    return;
  }

  if (array.le) g_gxState.dirty |= DirtyPipeline;
  array.data = span.data;
  // span.size is only the fail-closed maximum bytes reachable from the
  // raw CP base. It is not the logical GX vertex-array length.
  array.size = span.size;
  array.le = false;
  array.guestMapped = true;
  array.cachedRange = {};
  g_gxState.dirty |= DirtyImmediates;
'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/regs.cpp"
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        if text.count(SIGNATURE) != 1:
            raise RuntimeError("duplicate raw CP array-cache signature")
        return "already-applied"
    if "bool guestMapped = false;" not in (source / "lib/gx/gx.hpp").read_text(encoding="utf-8"):
        raise RuntimeError("guest-array bounds adaptation must be applied first")
    count = text.count(OLD)
    if count != 1:
        raise RuntimeError(f"lib/gx/regs.cpp: expected current raw CP array-base fragment once, found {count}")
    text = text.replace(OLD, NEW, 1)
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
        print(f"error: Aurora raw CP array-cache adaptation failed: {exc}", file=sys.stderr)
        return 4
    if result == "already-applied":
        print("Aurora raw CP array-cache adaptation already applied")
    elif result == "verified":
        print("Aurora raw CP array-cache adaptation verified")
    else:
        print("Aurora raw CP array-cache adaptation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
