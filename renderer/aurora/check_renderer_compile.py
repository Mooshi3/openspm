#!/usr/bin/env python3
"""Syntax-compile the patched Aurora renderer without a Windows build.

Player builds are the only place the renderer is compiled, and CI minutes are
not always available. This checker reproduces the renderer translation units
locally:

1. fetch pinned Aurora (aurora.lock.json) and header-only dependencies into a
   cache directory outside the repository (git clones + Dawn's prebuilt
   headers; nothing game-derived is involved);
2. copy Aurora and apply the full OpenSPM patch chain in CMake order
   (apply_patches.py, apply_raster_patch.py, then every configure-time patch
   listed in CMakeLists.txt);
3. compile the OpenSPM adapter (openspm_renderer_aurora_v6.cpp) and every
   Aurora source file the chain modified to objects, then check their symbols:
   an undefined symbol in an anonymous namespace can never link, and every
   undefined OpenSPM (m14/openspm-named) symbol must be defined by one of the
   compiled objects or by an unmodified Aurora source.

  python renderer/aurora/check_renderer_compile.py --cache <dir>

Host compiler only (Windows-only #ifdef branches are not covered here).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

DEPS = {  # name -> (git url, ref); header-only use
    "abseil-cpp": ("https://github.com/abseil/abseil-cpp.git", "20240722.0"),
    "fmt": ("https://github.com/fmtlib/fmt.git", "12.1.0"),
    "xxHash": ("https://github.com/Cyan4973/xxHash.git", "v0.8.3"),
    "tracy": ("https://github.com/wolfpld/tracy.git", "master"),
    "SDL": ("https://github.com/libsdl-org/SDL.git", "release-3.4.10"),
    "imgui": ("https://github.com/ocornut/imgui.git", "v1.91.9b-docking"),  # Aurora extern pin
}
DAWN_URL = ("https://github.com/encounter/dawn/releases/download/"
            "v20260807.225922/dawn-linux-x86_64.tar.gz")
DEFINES = ["-DAURORA", "-DTARGET_PC", "-DAURORA_ENABLE_GX", "-DWEBGPU_DAWN", "-DFMT_HEADER_ONLY",
           "-DDAWN_ENABLE_BACKEND_VULKAN"]  # real builds always enable one backend


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)


def fetch(cache: pathlib.Path) -> pathlib.Path:
    lock = json.loads((HERE / "aurora.lock.json").read_text(encoding="utf-8"))
    aurora = cache / f"aurora-{lock['commit'][:12]}"
    if not (aurora / "CMakeLists.txt").exists():
        aurora.mkdir(parents=True, exist_ok=True)
        run(["git", "-C", str(aurora), "init", "-q"])
        run(["git", "-C", str(aurora), "remote", "add", "origin", lock["repository"]])
        run(["git", "-C", str(aurora), "fetch", "-q", "--depth", "1", "origin", lock["commit"]])
        run(["git", "-C", str(aurora), "checkout", "-q", "FETCH_HEAD"])
    for name, (url, ref) in DEPS.items():
        if not (cache / name).exists():
            run(["git", "clone", "-q", "--depth", "1", "--branch", ref, url, str(cache / name)])
    dawn = cache / "dawn"
    if not (dawn / "include" / "webgpu" / "webgpu_cpp.h").exists():
        dawn.mkdir(parents=True, exist_ok=True)
        archive = cache / "dawn.tar.gz"
        run(["curl", "-sSL", "-o", str(archive), DAWN_URL])
        run(["tar", "-xzf", str(archive), "-C", str(dawn)])
    return aurora


def chain_scripts() -> list[str]:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    ordered = re.findall(r'renderer/aurora/(apply_[a-z0-9_]+\.py)"', cmake)
    return ["apply_patches.py", "apply_raster_patch.py"] + [s for s in ordered
                                                           if s not in ("apply_patches.py", "apply_raster_patch.py")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    parser.add_argument("--pass1", action="append", default=[], metavar="SCRIPT=PATH",
                        help="use an older version of a chain script for pass 1 only, to test "
                             "that the current script migrates a player cache patched by it")
    args = parser.parse_args()
    overrides = dict(item.split("=", 1) for item in args.pass1)
    cache = args.cache.resolve()
    if ROOT in cache.parents or cache == ROOT:
        parser.error("--cache must be outside the repository")
    cache.mkdir(parents=True, exist_ok=True)
    pristine = fetch(cache)

    work = cache / "aurora-patched"
    if work.exists():
        shutil.rmtree(work)
    # Keep .git: apply_patches.py verifies the pinned revision, and git status
    # afterwards lists exactly the files the chain modified.
    shutil.copytree(pristine, work, symlinks=True, ignore=shutil.ignore_patterns("build"))
    # Pass 1 = a fresh player cache. Pass 2 = every later build: the player's
    # Aurora cache is patched in place and CMake re-runs the configure-time chain
    # over already-patched sources, so every transform must accept its own and
    # every later transform's output.
    scripts = chain_scripts()
    for attempt, batch in ((1, scripts), (2, scripts[2:])):
        for script in batch:
            path = pathlib.Path(overrides[script]) if attempt == 1 and script in overrides else HERE / script
            result = subprocess.run([sys.executable, str(path), str(work)],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                print(f"FAIL chain pass {attempt} {script}: {(result.stderr or result.stdout).strip()[-400:]}")
                return 1
    print(f"PASS chain: fresh apply + in-place re-configure ({len(scripts)} patches)")
    changed = subprocess.run(["git", "-C", str(work), "status", "--porcelain"],
                             capture_output=True, text=True, check=True).stdout.split("\n")
    sources = sorted({line[3:].strip() for line in changed
                      if line[3:].strip().endswith(".cpp") and line[3:].strip().startswith("lib/")})

    cxx = shutil.which("g++") or shutil.which("clang++")
    includes = [f"-I{work / 'include'}", f"-I{work / 'lib'}", f"-I{ROOT / 'include'}",
                f"-I{cache / 'abseil-cpp'}", f"-I{cache / 'fmt' / 'include'}", f"-I{cache / 'xxHash'}",
                f"-I{cache / 'tracy' / 'public'}", f"-I{cache / 'dawn' / 'include'}",
                f"-I{cache / 'SDL' / 'include'}", f"-I{cache / 'imgui'}",
                '-DIMGUI_USER_CONFIG="aurora/imgui_config.h"']
    targets = [(work / s, s) for s in sources] + [(HERE / "openspm_renderer_aurora_v6.cpp",
                                                   "renderer/aurora/openspm_renderer_aurora_v6.cpp")]
    objdir = cache / "objects"
    if objdir.exists():
        shutil.rmtree(objdir)
    objdir.mkdir()
    failed = 0
    objects: list[tuple[pathlib.Path, str]] = []
    for index, (path, label) in enumerate(targets):
        obj = objdir / f"{index:02d}_{path.stem}.o"
        result = subprocess.run([cxx, "-std=c++20", "-c", "-O0", *DEFINES, *includes, str(path), "-o", str(obj)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            failed += 1
            errors = [l for l in result.stderr.splitlines() if "error" in l]
            print(f"FAIL {label}\n  " + "\n  ".join(errors[:12]))
        else:
            print(f"PASS {label}")
            objects.append((obj, label))
    print(f"{len(targets) - failed}/{len(targets)} renderer translation units compile "
          f"after {len(chain_scripts())} chain patches")
    if failed:
        return 1
    return 1 if not check_symbols(objects) else 0


def symbols(obj: pathlib.Path) -> tuple[set[str], set[str]]:
    out = subprocess.run(["nm", "-C", str(obj)], capture_output=True, text=True, check=True).stdout
    defined: set[str] = set()
    undefined: set[str] = set()
    for line in out.splitlines():
        parts = line.split(None, 2) if not line.startswith(" ") else ["", *line.split(None, 1)]
        if len(parts) < 3:
            continue
        kind, name = parts[1], parts[2]
        (undefined if kind == "U" else defined).add(name)
    return defined, undefined


def check_symbols(objects: list[tuple[pathlib.Path, str]]) -> bool:
    """Catch link errors a syntax check cannot (e.g. LNK2019 on a hook declared
    in the wrong namespace). Only OpenSPM-added symbols are held to "defined in
    a compiled object"; Aurora's own cross-TU symbols live in unmodified files."""
    table = {label: symbols(obj) for obj, label in objects}
    defined = set().union(*(d for d, _ in table.values()))
    ok = True
    for label, (_, undefined) in table.items():
        for name in sorted(undefined):
            anonymous = "(anonymous namespace)" in name
            ours = re.search(r"m14|openspm|efb_copy_writeback", name, re.IGNORECASE) is not None
            if (anonymous or ours) and name not in defined:
                ok = False
                print(f"FAIL link {label}: unresolved {name}")
    print("PASS link: OpenSPM symbols resolve across compiled objects" if ok
          else "FAIL link: unresolved OpenSPM symbols (would be LNK2019 on Windows)")
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
