#!/usr/bin/env python3
"""Syntax-compile the active LLVM bridge chain before a player build.

The bridge (legacy_c_adapter_v42.c.in and everything it includes) is only
compiled on the player's PC, against player-local generated headers. This
checker reproduces that translation unit locally so C errors are caught before
a build round-trip:

  python tools/native_bridge/check_bridge_compile.py \
      --generated <private generated dir containing generated.h> \
      --dol <private main.dol or combined analysis DOL> \
      --dolrecomp <DolRecomp checkout at the prepare_dolrecomp_llvm.py PIN>

All inputs stay local; derived headers are written to a temporary directory
and nothing game-derived is ever written into the repository. Compiles with
the host C compiler and, when available, x86_64-w64-mingw32-gcc so the _WIN32
paths are checked too, and clang --target=x86_64-pc-windows-msvc against only
clang's builtin headers + freestanding/include (the clang-freestanding module
toolchain: _MSC_VER paths, no CRT/SDK headers).
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import derive_gx_dl_semantics  # noqa: E402
import derive_v19_llvm_seams  # noqa: E402

MAIN_DOL_SHA256 = "316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0"


def derive_headers(dol: pathlib.Path, out: pathlib.Path) -> None:
    subprocess.run([sys.executable, str(HERE / "make_identity.py"), "--game-id", "R8PE01",
                    "--entry", "0x80006124", "--sha256", MAIN_DOL_SHA256,
                    "--output", str(out / "openspm_local_identity.h")],
                   check=True, stdout=subprocess.DEVNULL)
    derive_v19_llvm_seams.write_header(out / "openspm_local_v19_llvm_seams.h",
                                       derive_v19_llvm_seams.scan(dol))
    mode, value, helper = derive_gx_dl_semantics.derive(dol)
    derive_gx_dl_semantics.write_header(out / "openspm_local_gx_dl_semantics.h",
                                        mode, value, helper)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--generated", type=pathlib.Path, required=True)
    parser.add_argument("--dol", type=pathlib.Path, required=True)
    parser.add_argument("--dolrecomp", type=pathlib.Path, required=True)
    parser.add_argument("--bridge", type=pathlib.Path,
                        default=HERE / "legacy_c_adapter_v42.c.in")
    args = parser.parse_args()

    if not (args.generated / "generated.h").is_file():
        parser.error(f"{args.generated} has no generated.h")
    cpu_root = args.dolrecomp / "src"
    if not (cpu_root / "cpu" / "cpu.h").is_file():
        parser.error(f"{args.dolrecomp} is not a DolRecomp checkout (src/cpu/cpu.h missing)")

    # (label, compiler + dialect flags). The clang entry is the MSVC-mode,
    # freestanding compile of the clang-freestanding module toolchain: clang's
    # builtin headers plus freestanding/include only, no CRT/SDK headers.
    compilers = [(pathlib.Path(c).name, [c, "-std=gnu11"])
                 for c in (shutil.which("cc") or shutil.which("gcc"),
                           shutil.which("x86_64-w64-mingw32-gcc")) if c]
    clang = shutil.which("clang")
    if clang:
        compilers.append(("clang-msvc-freestanding", [
            clang, "--target=x86_64-pc-windows-msvc", "-std=c11", "-nostdlibinc",
            "-isystem", str(HERE / "freestanding" / "include"),
            "-fno-strict-aliasing"]))
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        derived = pathlib.Path(tmp)
        derive_headers(args.dol, derived)
        for label, compiler in compilers:
            cmd = compiler + ["-fsyntax-only", "-x", "c",
                   "-DDOLRECOMP_BACKEND_LLVM",
                   "-DOPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS=256",
                   "-DOPENSPM_M14_LLVM_STATE_IN_MEMORY=1",
                   "-DOPENSPM_M14_HEAVY_DIAGNOSTICS=0",
                   f"-I{ROOT / 'include'}", f"-I{HERE}", f"-I{args.generated}",
                   f"-I{cpu_root}", f"-I{derived}", str(args.bridge)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            errors = [line for line in result.stderr.splitlines() if "error" in line]
            if result.returncode != 0:
                failed = True
                print(f"FAIL {label}:")
                print("\n".join(errors[:20]) or result.stderr[-2000:])
            else:
                print(f"PASS {label}: {args.bridge.name} (LLVM backend) syntax-compiles")
    labels = {label for label, _ in compilers}
    if "x86_64-w64-mingw32-gcc" not in labels:
        print("NOTE: x86_64-w64-mingw32-gcc not found; _WIN32 paths were not compiled")
    if "clang-msvc-freestanding" not in labels:
        print("NOTE: clang not found; the MSVC-mode freestanding compile was skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
