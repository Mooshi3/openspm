#!/usr/bin/env python3
"""Refresh M14's private staged DolRecomp CPU runtime for incremental builds.

Older home-test caches point OPENSPM_CPU_RUNTIME_DIR directly at the user's local
DolRecomp/compiler runtime.  Never patch that checkout in place.  Recover the
path from CMakeCache.txt once, remember it beside the staging directory, copy
only cpu/ and common/ into the private M14 build stage, apply the exact WPAR
fastpath transform there, and let the normal updater repoint the existing CMake
build to that staged root.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stage_cpu_wg_fastpath  # noqa: E402

CACHE_KEY = "OPENSPM_CPU_RUNTIME_DIR"
ORIGIN_FILE = "cpu_runtime_origin.txt"


def read_cache_path(cache: Path) -> Path:
    prefix = CACHE_KEY + ":"
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(prefix):
            continue
        _name_type, sep, value = line.partition("=")
        if sep and value.strip():
            return Path(value.strip()).resolve()
    raise RuntimeError(f"{cache}: missing {CACHE_KEY}")


def copy_runtime(source: Path, destination: Path) -> None:
    cpu = source / "cpu"
    common = source / "common"
    if not (cpu / "cpu.c").is_file() or not (cpu / "cpu.h").is_file():
        raise RuntimeError(f"{source}: missing cpu/cpu.c or cpu/cpu.h")
    if not (common / "types.h").is_file():
        raise RuntimeError(f"{source}: missing common/types.h")

    temp = destination.with_name(destination.name + ".refresh")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cpu, temp / "cpu")
    shutil.copytree(common, temp / "common")
    stage_cpu_wg_fastpath.transform(temp)

    if destination.exists():
        shutil.rmtree(destination)
    temp.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cmake-cache", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()

    cache = args.cmake_cache.resolve()
    destination = args.destination.resolve()
    origin_file = destination.parent / ORIGIN_FILE
    try:
        cached = read_cache_path(cache)
        if cached == destination:
            if not origin_file.is_file():
                raise RuntimeError(
                    "local CMake cache already points at staged CPU runtime but original path marker is missing"
                )
            source = Path(origin_file.read_text(encoding="utf-8").strip()).resolve()
        else:
            source = cached
            destination.parent.mkdir(parents=True, exist_ok=True)
            origin_file.write_text(str(source) + "\n", encoding="utf-8", newline="\n")

        if source == destination:
            raise RuntimeError("refusing to use staged destination as its own source")
        copy_runtime(source, destination)
    except (OSError, RuntimeError) as exc:
        print(f"error: could not refresh staged CPU runtime: {exc}", file=sys.stderr)
        return 2

    print(f"M14 staged CPU runtime source: {source}")
    print(f"M14 staged CPU runtime ready: {destination}")
    print("M14 direct WPAR CPU store transform: active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
