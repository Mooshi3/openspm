#!/usr/bin/env python3
"""Build the temporary M14 whole-DOL native bridge entirely from local inputs.

Nothing from the player-owned game is copied into the OpenSPM source tree. The
only persistent output is the locally generated native module and build cache.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import struct
import subprocess
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
OPENSPM_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import direct_calls  # noqa: E402
import stage_cpu_wg_fastpath  # noqa: E402


def dol_entry(path: pathlib.Path) -> int:
    with path.open("rb") as source:
        header = source.read(0x100)
    if len(header) < 0x100:
        raise RuntimeError("main.dol is shorter than the 0x100-byte DOL header")
    entry = struct.unpack_from(">I", header, 0xE0)[0]
    if entry == 0:
        raise RuntimeError("main.dol has a zero entry point")
    return entry


def sha256_file(path: pathlib.Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def write_identity(output: pathlib.Path, game_id: str, entry: int, digest: bytes) -> None:
    byte_text = ", ".join(f"0x{value:02x}" for value in digest)
    text = f"""#ifndef OPENSPM_LOCAL_IDENTITY_H
#define OPENSPM_LOCAL_IDENTITY_H

#define OPENSPM_LOCAL_GAME_ID \"{game_id}\"
#define OPENSPM_LOCAL_ENTRY_PC UINT32_C(0x{entry:08X})
static const uint8_t OPENSPM_LOCAL_SOURCE_SHA256[32] = {{
    {byte_text}
}};

#endif
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def locate_module(build_dir: pathlib.Path) -> pathlib.Path:
    candidates = list(build_dir.rglob("openspm_local_native.dll"))
    if not candidates:
        candidates = list(build_dir.rglob("openspm_local_native.so"))
    if not candidates:
        candidates = list(build_dir.rglob("openspm_local_native.dylib"))
    if not candidates:
        raise RuntimeError("build succeeded but openspm_local_native was not found")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def stage_cpu_runtime(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Copy only the compiler runtime pieces consumed by the local C build.

    The WPAR optimization is OpenSPM/player-local and must never mutate the
    user's DolRecomp checkout. cpu/cpu.c includes common/types.h and generated
    chunks include the same runtime headers, so these two directories are the
    complete private runtime surface used by this CMake target.
    """
    cpu_dir = source / "cpu"
    common_dir = source / "common"
    if not (cpu_dir / "cpu.c").is_file() or not (cpu_dir / "cpu.h").is_file():
        raise RuntimeError("CPU runtime staging requires cpu/cpu.c and cpu/cpu.h")
    if not (common_dir / "types.h").is_file():
        raise RuntimeError("CPU runtime staging requires common/types.h")

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cpu_dir, destination / "cpu")
    shutil.copytree(common_dir, destination / "common")

    result = stage_cpu_wg_fastpath.transform(destination)
    # Incremental M14 updates see CMake pointing at this private staged root.
    # Preserve the immutable source runtime path beside it so refreshes can
    # recopy pristine cpu/common files rather than copying an already patched
    # stage onto itself.
    (destination.parent / "cpu_runtime_origin.txt").write_text(
        str(source.resolve()) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"cpu_runtime_stage={result}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openspm-root", type=pathlib.Path, default=OPENSPM_ROOT_DEFAULT)
    parser.add_argument("--generated", required=True, type=pathlib.Path,
                        help="local DolRecomp generated/ directory")
    parser.add_argument("--cpu-runtime", required=True, type=pathlib.Path,
                        help="local compiler runtime root containing cpu/cpu.c")
    parser.add_argument("--main-dol", required=True, type=pathlib.Path,
                        help="player-owned extracted sys/main.dol")
    parser.add_argument("--build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path,
                        help="normally User-OpenSPM/native")
    parser.add_argument("--game-id", default="R8PE01")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--generator", default=None,
                        help="optional CMake generator, e.g. Ninja")
    parser.add_argument("--config", default="Release")
    parser.add_argument("--skip-direct-calls", action="store_true",
                        help=("input was already direct-call converted; normalize it against "
                              "the current host-target manifest before building"))
    args = parser.parse_args()

    openspm_root = args.openspm_root.resolve()
    generated = args.generated.resolve()
    cpu_runtime = args.cpu_runtime.resolve()
    main_dol = args.main_dol.resolve()
    build_root = args.build_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not (openspm_root / "include" / "openspm" / "native_api.h").is_file():
        parser.error("--openspm-root is not an M14 OpenSPM source checkout")
    if not (generated / "generated.h").is_file() or not (generated / "chunks").is_dir():
        parser.error("--generated must contain generated.h and chunks/")
    if not (cpu_runtime / "cpu" / "cpu.c").is_file():
        parser.error("--cpu-runtime must contain cpu/cpu.c")
    if not main_dol.is_file():
        parser.error("--main-dol does not exist")

    entry = dol_entry(main_dol)
    digest = sha256_file(main_dol)
    stage = build_root / "stage"
    staged_generated = stage / "generated"
    staged_cpu_runtime = stage / "cpu_runtime"
    identity_dir = stage / "identity"
    identity = identity_dir / "openspm_local_identity.h"

    # Always run the normalizer. On fresh DolRecomp output it creates native
    # direct calls while preserving semantic host exits. On a saved/generated-
    # direct tree it is idempotent, but can re-host callsites that became host
    # services after the archive was created. The legacy flag remains the home-
    # test signal that the input is already direct, and is reported in stats.
    stats = direct_calls.rewrite_tree(generated, staged_generated)
    stats["mode"] = (
        "already-direct-normalized" if args.skip_direct_calls else "direct-calls"
    )

    # Compile a private copy of the selected compiler runtime. This lets M14
    # bypass DolRecomp's generic resolve_addr()/indirect external_write path only
    # for Broadway's exact WPAR window without modifying the user's toolchain.
    stage_cpu_runtime(cpu_runtime, staged_cpu_runtime)
    write_identity(identity, args.game_id, entry, digest)

    module_build = build_root / "module"
    configure = [
        args.cmake,
        "-S", str(openspm_root / "tools" / "native_bridge"),
        "-B", str(module_build),
        f"-DCMAKE_BUILD_TYPE={args.config}",
        f"-DOPENSPM_ROOT={openspm_root}",
        f"-DOPENSPM_GENERATED_DIR={staged_generated}",
        f"-DOPENSPM_CPU_RUNTIME_DIR={staged_cpu_runtime}",
        f"-DOPENSPM_IDENTITY_DIR={identity_dir}",
    ]
    if args.generator:
        configure += ["-G", args.generator]
    run(configure)
    run([args.cmake, "--build", str(module_build), "--config", args.config,
         "--target", "openspm_local_native"])

    built = locate_module(module_build)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = built.suffix.lower()
    output_name = "openspm_local_native.dll" if suffix == ".dll" else built.name
    destination = output_dir / output_name
    shutil.copy2(built, destination)

    print(f"game_id={args.game_id}")
    print(f"dol_entry=0x{entry:08X}")
    print(f"main_dol_sha256={digest.hex()}")
    print("rewrite=" + " ".join(f"{key}={value}" for key, value in stats.items()))
    print(f"module={destination}")
    print(f"module_sha256={hashlib.sha256(destination.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"build command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
