#!/usr/bin/env python3
"""Add a narrow provisional GX_LO_OR fallback for the M14 opening frontier.

Runtime at 030a00165b67 survived the former 8 MiB staging crash with the 32 MiB
host arena, then failed later in the opening at Aurora's upstream
`unsupported logic op 7`. Aurora's GX enum defines value 7 as GX_LO_OR.

WebGPU exposes fixed-function arithmetic blending but not native GX-style
per-channel bitwise logic operations. For frontier advancement only, map
GX_LO_OR to component-wise Max. This is exact for binary/coverage-style channel
values (0/1 in normalized space) but is NOT a general replacement for 8-bit
bitwise OR. Keep the warning marker and do not treat this as the permanent
renderer solution. The permanent path should emulate destination-aware bitwise
logic exactly, likely by snapshotting the EFB/render target and combining in a
shader/ordered pass.

This transform is pinned and fail-closed. It changes only the previously-fatal
GX_LO_OR case; all other unsupported logic ops remain fatal so later evidence is
not hidden.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

OLD = """    case GX_LO_NOOP:\n      colorBlendComponent = {\n          .operation = wgpu::BlendOperation::Add,\n          .srcFactor = wgpu::BlendFactor::Zero,\n          .dstFactor = wgpu::BlendFactor::One,\n      };\n      break;\n    }\n"""

NEW = """    case GX_LO_NOOP:\n      colorBlendComponent = {\n          .operation = wgpu::BlendOperation::Add,\n          .srcFactor = wgpu::BlendFactor::Zero,\n          .dstFactor = wgpu::BlendFactor::One,\n      };\n      break;\n    case GX_LO_OR:\n      // OpenSPM M14 provisional GX_LO_OR fallback. WebGPU has no native\n      // destination bitwise logic op in this path. Component-wise Max is exact\n      // for binary/coverage channels but is not general 8-bit bitwise OR.\n      Log.warn(\"OpenSPM M14 provisional GX_LO_OR fallback active: component-wise Max, not general bitwise OR\");\n      colorBlendComponent = {\n          .operation = wgpu::BlendOperation::Max,\n          .srcFactor = wgpu::BlendFactor::One,\n          .dstFactor = wgpu::BlendFactor::One,\n      };\n      break;\n    }\n"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/gx.cpp"
    text = path.read_text(encoding="utf-8")

    # The old idempotence check counted a human-readable signature string that
    # intentionally appears twice in NEW (once in the comment and once in the
    # runtime warning).  That made the *second* configure falsely report a
    # duplicate patch even though the source contained exactly one patched
    # switch arm.  Treat the exact old/new source fragments as the state machine
    # instead: one old block means apply, one new block means already applied,
    # and every mixed/duplicate/unknown state fails closed.
    old_count = text.count(OLD)
    new_count = text.count(NEW)
    if old_count == 0 and new_count == 1:
        return "already-applied"
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            "lib/gx/gx.cpp: expected exactly one unpatched or one patched GX_LO_OR switch tail "
            f"(old={old_count}, patched={new_count})"
        )
    if not check_only:
        path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
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
        print(f"error: Aurora GX_LO_OR frontier adaptation failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora provisional GX_LO_OR fallback already applied")
    elif result == "verified":
        print("Aurora provisional GX_LO_OR fallback verified")
    else:
        print("Aurora provisional GX_LO_OR fallback applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
