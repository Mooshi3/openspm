#!/usr/bin/env python3
"""Build and run the Stage C Dawn replay-encode benchmark (Null backend).

Uses the prebuilt Dawn that check_renderer_compile.py fetches into its cache
(no GPU, nothing game-derived):

  python3 renderer/aurora/bench_frame_interp_dawn.py --cache <dir> [--draws 3000]

Prints the per-refresh CPU cost of re-encoding N recorded GX draws, of
executing them as render bundles instead, and of the uniform upload, with
Dawn validation on and skipped (release builds skip it).
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    parser.add_argument("--draws", type=int, default=3000)
    args = parser.parse_args()
    dawn = args.cache.resolve() / "dawn"
    if not (dawn / "lib" / "libwebgpu_dawn.a").exists():
        print(f"no prebuilt Dawn in {dawn}: run check_renderer_compile.py --cache {args.cache} first")
        return 2
    cxx = shutil.which("g++") or shutil.which("clang++")
    exe = args.cache.resolve() / "m14_frame_interp_dawn_bench"
    subprocess.run([cxx, "-std=c++20", "-O2", str(ROOT / "tests" / "m14_frame_interp_dawn_bench.cpp"),
                    f"-I{dawn / 'include'}", f"-L{dawn / 'lib'}", "-lwebgpu_dawn", "-ldl", "-lpthread", "-lrt",
                    "-o", str(exe)], check=True)
    for mode in ("", "fast"):
        subprocess.run([str(exe), str(args.draws)] + ([mode] if mode else []), check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
