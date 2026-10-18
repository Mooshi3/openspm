#!/usr/bin/env python3
"""Prepare the player's game for OpenSPM: one entry point, no CMake, git or
network (docs/RELEASE-PLAN.md Stage 3).

    python prepare_game.py [--app-root <OpenSPM folder>]

In a release folder:

    OpenSPMNative.exe
    User-OpenSPM\\game\\sys\\main.dol              the player's extracted game (the exe's picker)
    User-OpenSPM\\native\\                         the module is installed here
    recompiler\\dolrecomp.exe                      pinned DolRecomp (LLVM backend)
    recompiler\\dolrecomp-runtime\\src\\cpu\\cpu.c   its runtime sources
    recompiler\\llvm\\bin\\clang-cl, lld-link, llvm-dlltool

Steps: check the game is the one supported disc (R8PE01 rev 2, by the
main.dol SHA-256); skip everything when the installed module's stamp says it
is current (module_stamp.py); otherwise recompile and build the module with
the CMake-free driver (build_local_llvm_module.py --no-cmake) and install it.

Progress goes to stdout as lines the exe's progress window reads:

    OPENSPM-PROGRESS <step>/<steps> <text>
    OPENSPM-RESULT ok|error <text>

Nothing here is game data: the only game fact is the SHA-256 of the one
supported main.dol, which identifies the player's own copy.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import module_stamp  # noqa: E402

# R8PE01 revision 2 (US) main.dol, the one disc OpenSPM supports.
SUPPORTED_MAIN_DOL_SHA256 = "316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0"
TOOLCHAIN = "clang-freestanding"
CHUNK_INSTRUCTIONS = 256
STATE_IN_MEMORY = 1  # the options M14-VANILLA-RUN.cmd plays with (AUTO 256 1 0)
STEPS = 4


def progress(step: int, text: str) -> None:
    print(f"OPENSPM-PROGRESS {step}/{STEPS} {text}", flush=True)


def result(ok: bool, text: str) -> int:
    print(f"OPENSPM-RESULT {'ok' if ok else 'error'} {text}", flush=True)
    return 0 if ok else 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def layout(app_root: Path) -> dict[str, Path]:
    user = app_root / "User-OpenSPM"
    recompiler = app_root / "recompiler"
    exe = ".exe" if sys.platform == "win32" else ""
    return {
        "game": user / "game",
        "main_dol": user / "game" / "sys" / "main.dol",
        "native": user / "native",
        "work": user / "cache" / "native-build",
        "dolrecomp": recompiler / f"dolrecomp{exe}",
        "dolrecomp_source": recompiler / "dolrecomp-runtime",
        "llvm_bin": recompiler / "llvm" / "bin",
    }


def prepare(app_root: Path, force: bool = False, jobs: int = 4, run=subprocess.run) -> int:
    paths = layout(app_root)
    progress(1, "Checking your game")
    if not paths["main_dol"].is_file():
        return result(False, f"no extracted game at {paths['game']} (pick your .wbfs / .iso in OpenSPMNative first)")
    game_sha = sha256_file(paths["main_dol"])
    if game_sha != SUPPORTED_MAIN_DOL_SHA256:
        return result(False, "this is not Super Paper Mario R8PE01 revision 2 (US), the version OpenSPM supports")
    want = module_stamp.expected(game_sha, TOOLCHAIN, CHUNK_INSTRUCTIONS, STATE_IN_MEMORY)
    current, why = module_stamp.status(paths["native"], want)
    if current and not force:
        progress(STEPS, "Already prepared")
        return result(True, "the native module is current")
    progress(2, f"Preparing ({why})")
    for key in ("dolrecomp", "llvm_bin"):
        if not paths[key].exists():
            return result(False, f"this OpenSPM folder is missing {paths[key]}")
    if not (paths["dolrecomp_source"] / "src" / "cpu" / "cpu.c").is_file():
        return result(False, f"this OpenSPM folder is missing {paths['dolrecomp_source'] / 'src'}")
    progress(3, "Recompiling the game for this PC (this takes a while, once)")
    command = [
        sys.executable, str(HERE / "build_local_llvm_module.py"),
        "--dolrecomp", str(paths["dolrecomp"]),
        "--dolrecomp-source", str(paths["dolrecomp_source"]),
        "--game-dir", str(paths["game"]),
        "--build-dir", str(paths["work"]),
        "--output-dir", str(paths["native"]),
        "--chunk-instructions", str(CHUNK_INSTRUCTIONS),
        "--state-in-memory",
        "--toolchain", TOOLCHAIN,
        "--llvm-bin", str(paths["llvm_bin"]),
        "--no-cmake",
        "--jobs", str(jobs),
    ]
    completed = run(command)
    if completed.returncode != 0:
        return result(False, f"the module build failed (exit {completed.returncode}); see the log above")
    progress(4, "Checking the result")
    current, why = module_stamp.status(paths["native"], want)
    if not current:
        return result(False, f"the build finished but the module is not current: {why}")
    return result(True, "prepared")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-root", type=Path, default=HERE.parents[1],
                        help="the OpenSPM folder (default: two folders above this script)")
    parser.add_argument("--force", action="store_true", help="rebuild even when the module is current")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    return prepare(args.app_root.resolve(), args.force, args.jobs)


if __name__ == "__main__":
    raise SystemExit(main())
