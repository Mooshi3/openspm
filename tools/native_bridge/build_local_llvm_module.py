#!/usr/bin/env python3
"""Build the opt-in LLVM-native OpenSPM player module end to end.

The resulting openspm_local_native.dll is still an OpenSPM native module. The
pinned DolRecomp executable is used only during this build to emit native COFF/
ELF objects. No Dolphin, ModernGekko or RecompCore runtime is launched or linked.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
OPENSPM_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import build_local_module  # noqa: E402
import validate_llvm_generated  # noqa: E402

BROADWAY_SPR_SIGNATURE = "OPENSPM_M14_BROADWAY_SPR_DELEGATION"
BROADWAY_SPR_READ_OLD = """    if (spr < 1024 && (ppc_spr_access[spr] & SPR_READ) && cpu->spr_read)\n        return cpu->spr_read(cpu, spr, cia);\n\n    ppc_program_exception(cpu, PPC_PROGRAM_ILLEGAL, cia);\n    return 0;\n}\n\nvoid ppc_mtspr"""
BROADWAY_SPR_READ_NEW = """    /* OPENSPM_M14_BROADWAY_SPR_DELEGATION\n     * Historical standalone M14 routed non-core supervisor mfspr through the\n     * OpenSPM Wii CPU service. LLVM lowers SPRs directly to ppc_mfspr(), so\n     * restore that policy here after the core CPUState switch instead of\n     * rejecting Broadway-only implementation SPRs with the Gekko-era table. */\n    if (spr < 1024 && cpu->spr_read)\n        return cpu->spr_read(cpu, spr, cia);\n\n    ppc_program_exception(cpu, PPC_PROGRAM_ILLEGAL, cia);\n    return 0;\n}\n\nvoid ppc_mtspr"""
BROADWAY_SPR_WRITE_OLD = """    if (spr < 1024 && (ppc_spr_access[spr] & SPR_WRITE) && cpu->spr_write) {\n        cpu->spr_write(cpu, spr, value, cia);\n        return;\n    }\n\n    ppc_program_exception(cpu, PPC_PROGRAM_ILLEGAL, cia);\n}\n\nstatic bool wrapped_register_range_contains"""
BROADWAY_SPR_WRITE_NEW = """    /* Match early M14's privileged-SPR side-exit contract at the LLVM runtime\n     * helper boundary. Core SPRs above retain exact CPUState semantics; every\n     * remaining supervisor SPR is owned by OpenSPM's bounded 1024-entry Wii\n     * SPR service. User-mode privilege checks at function entry remain intact. */\n    if (spr < 1024 && cpu->spr_write) {\n        cpu->spr_write(cpu, spr, value, cia);\n        return;\n    }\n\n    ppc_program_exception(cpu, PPC_PROGRAM_ILLEGAL, cia);\n}\n\nstatic bool wrapped_register_range_contains"""


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


LOCAL_TOOLCHAINS = ("msvc", "clang", "clang-freestanding")
DEFAULT_LOCAL_TOOLCHAIN = "msvc"


def llvm_tool(llvm_bin: Path, name: str) -> Path:
    """Resolve one executable from the pinned LLVM bin directory."""
    for candidate in (llvm_bin / f"{name}.exe", llvm_bin / name):
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"pinned LLVM is missing {name}: {llvm_bin}")


def toolchain_cmake_args(toolchain: str, llvm_bin: Path | None) -> list[str]:
    """CMake cache arguments for the opt-in clang-cl/lld-link module build.

    msvc (the default) adds nothing, so the proven cl.exe /GL + link /LTCG
    configure line is byte-for-byte unchanged. The clang toolchains take
    clang-cl, lld-link and llvm-lib from the same pinned LLVM that builds
    DolRecomp, so the bridge, cpu.c and the payload objects all come from one
    LLVM version.
    """
    if toolchain not in LOCAL_TOOLCHAINS:
        raise RuntimeError(f"unknown native-module toolchain: {toolchain}")
    if toolchain == "msvc":
        return []
    if llvm_bin is None:
        raise RuntimeError(f"--toolchain {toolchain} requires --llvm-bin")
    args = [
        f"-DOPENSPM_LOCAL_TOOLCHAIN={toolchain}",
        f"-DCMAKE_C_COMPILER={llvm_tool(llvm_bin, 'clang-cl').as_posix()}",
        f"-DCMAKE_LINKER={llvm_tool(llvm_bin, 'lld-link').as_posix()}",
        f"-DCMAKE_AR={llvm_tool(llvm_bin, 'llvm-lib').as_posix()}",
    ]
    if toolchain == "clang-freestanding":
        args += [
            f"-DOPENSPM_LLVM_DLLTOOL={llvm_tool(llvm_bin, 'llvm-dlltool').as_posix()}",
            # CMake's compiler probe would otherwise link a test .exe against
            # the CRT this toolchain deliberately does not have.
            "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
        ]
    return args


def module_build_dir_name(toolchain: str) -> str:
    # A CMake cache is bound to one compiler; keep each toolchain separate so
    # switching never reuses (or wipes) the default MSVC tree.
    return "module-llvm" if toolchain == "msvc" else f"module-llvm-{toolchain}"


def find_generated(root: Path) -> Path:
    matches = [p.parent for p in root.rglob("generated.h")
               if (p.parent / "chunks").is_dir()]
    if len(matches) != 1:
        listing = ", ".join(str(p) for p in matches) or "none"
        raise RuntimeError(f"expected one LLVM generated tree, found {listing}")
    return matches[0]


def verify_codegen_provenance(
    generated: Path,
    expected_chunk_instructions: int,
    expected_state_in_memory: bool,
) -> None:
    provenance = generated / "openspm_llvm_codegen_provenance.txt"
    if not provenance.is_file():
        raise RuntimeError(
            f"LLVM generated tree is missing OpenSPM provenance: {provenance}"
        )
    fields = {}
    for raw in provenance.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        fields[key.strip()] = value.strip()
    actual = fields.get("chunk_instructions")
    expected = str(expected_chunk_instructions)
    if actual != expected:
        raise RuntimeError(
            "stale/wrong LLVM chunk geometry: "
            f"expected chunk_instructions={expected}, got {actual!r}"
        )
    actual_state = fields.get("state_in_memory")
    expected_state = "1" if expected_state_in_memory else "0"
    if actual_state != expected_state:
        raise RuntimeError(
            "stale/wrong LLVM state-memory mode: "
            f"expected state_in_memory={expected_state}, got {actual_state!r}"
        )
    if fields.get("dispatch_lookup") != "indexed":
        raise RuntimeError(
            "dev113 requires the retained indexed dispatcher; "
            f"got dispatch_lookup={fields.get('dispatch_lookup')!r}"
        )
    print(
        "[OpenSPM] LLVM provenance PASS: "
        f"chunk_instructions={expected} state_in_memory={expected_state} "
        "dispatch_lookup=indexed",
        flush=True,
    )


def patch_staged_broadway_runtime(staged_cpu: Path) -> None:
    """Restore M14's proven non-core SPR delegation at LLVM's helper boundary.

    Early generated-C M14 deliberately side-exited privileged mfspr/mtspr and
    routed non-core SPRs to OpenSPM's Wii CPU service while preserving core
    CPUState semantics. Current LLVM objects instead call ppc_mfspr/ppc_mtspr
    directly. Pinned DolRecomp 40637c gates those callbacks through a Gekko-era
    static access table, so Broadway HID4 and BAT4-7 fault before the proven M14
    bridge fallback can run. Patch only the staged runtime copy: keep the helper's
    privilege checks and core switch intact, but let any remaining SPR < 1024
    reach OpenSPM's existing bounded Wii SPR callback before declaring illegal.
    """
    cpu_c = staged_cpu / "cpu" / "cpu.c"
    text = cpu_c.read_text(encoding="utf-8")

    if BROADWAY_SPR_SIGNATURE in text:
        if BROADWAY_SPR_READ_OLD in text or BROADWAY_SPR_WRITE_OLD in text:
            raise RuntimeError("incomplete staged Broadway SPR delegation patch")
        return

    if text.count(BROADWAY_SPR_READ_OLD) != 1:
        raise RuntimeError(
            "pinned DolRecomp ppc_mfspr tail changed; refusing to guess at historical M14 SPR delegation"
        )
    if text.count(BROADWAY_SPR_WRITE_OLD) != 1:
        raise RuntimeError(
            "pinned DolRecomp ppc_mtspr tail changed; refusing to guess at historical M14 SPR delegation"
        )

    patched = text.replace(BROADWAY_SPR_READ_OLD, BROADWAY_SPR_READ_NEW, 1)
    patched = patched.replace(BROADWAY_SPR_WRITE_OLD, BROADWAY_SPR_WRITE_NEW, 1)
    if BROADWAY_SPR_SIGNATURE not in patched:
        raise RuntimeError("Broadway SPR delegation marker missing after staged-runtime patch")
    if "(ppc_spr_access[spr] & SPR_READ) && cpu->spr_read" in patched:
        raise RuntimeError("Broadway SPR read callback is still gated by the old access table")
    if "(ppc_spr_access[spr] & SPR_WRITE) && cpu->spr_write" in patched:
        raise RuntimeError("Broadway SPR write callback is still gated by the old access table")

    cpu_c.write_text(patched, encoding="utf-8", newline="\n")
    print(
        "[OpenSPM] Staged DolRecomp runtime restored historical M14 Broadway SPR delegation: "
        "core SPRs stay CPUState-native; other supervisor mfspr/mtspr route to the OpenSPM Wii SPR service.",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openspm-root", type=Path, default=OPENSPM_ROOT_DEFAULT)
    parser.add_argument("--dolrecomp", required=True, type=Path)
    parser.add_argument("--dolrecomp-source", required=True, type=Path,
                        help="matching pinned DolRecomp checkout containing src/cpu/cpu.c")
    parser.add_argument("--game-dir", required=True, type=Path)
    parser.add_argument("--main-dol", type=Path,
                        help="defaults to <game-dir>/sys/main.dol")
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--game-id", default="R8PE01")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--generator", default=None)
    parser.add_argument("--config", default="Release")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--skip-llvm-probe", action="store_true")
    parser.add_argument(
        "--chunk-instructions",
        type=int,
        choices=(128, 256, 512),
        default=256,
        help="LLVM generic chunk ceiling; 256 is dev113, 128 baseline, 512 experimental transition-pressure A/B",
    )
    parser.add_argument(
        "--state-in-memory",
        action="store_true",
        help="opt into DolRecomp CPUState-resident non-native state for dev114 A/B",
    )
    parser.add_argument(
        "--heavy-diagnostics",
        action="store_true",
        help="enable dev111/dev112/dev115 per-callback profiling; normal play leaves it off",
    )
    parser.add_argument(
        "--toolchain",
        choices=LOCAL_TOOLCHAINS,
        default=DEFAULT_LOCAL_TOOLCHAIN,
        help="module compiler/linker: msvc (default, cl /GL + link /LTCG), clang "
             "(clang-cl ThinLTO + lld-link, MSVC CRT/SDK) or clang-freestanding "
             "(clang-cl + lld-link, no CRT/SDK; Stage 2 spike)",
    )
    parser.add_argument(
        "--no-cmake",
        action="store_true",
        help="build the module with direct_module_build.py (clang-cl + lld-link + llvm-dlltool "
             "only, no CMake / Ninja / Visual Studio; release Stage 3); needs "
             "--toolchain clang-freestanding and --llvm-bin",
    )
    parser.add_argument(
        "--llvm-bin",
        type=Path,
        help="pinned LLVM bin directory holding clang-cl/lld-link/llvm-lib "
             "(required for the clang toolchains)",
    )
    args = parser.parse_args()

    try:
        root = args.openspm_root.resolve()
        dolrecomp = args.dolrecomp.resolve()
        tool_source = args.dolrecomp_source.resolve()
        runtime_source = tool_source / "src"
        game_dir = args.game_dir.resolve()
        main_dol = args.main_dol.resolve() if args.main_dol else game_dir / "sys" / "main.dol"
        build_root = args.build_dir.resolve()
        output_dir = args.output_dir.resolve()

        if not (root / "include" / "openspm" / "native_api.h").is_file():
            raise RuntimeError("--openspm-root is not an M14 OpenSPM checkout")
        if not dolrecomp.is_file():
            raise RuntimeError(f"DolRecomp executable not found: {dolrecomp}")
        if not (runtime_source / "cpu" / "cpu.c").is_file() or not (
            runtime_source / "common" / "types.h"
        ).is_file():
            raise RuntimeError(
                "--dolrecomp-source must be the pinned checkout containing "
                "src/cpu/cpu.c and src/common/types.h"
            )
        if not main_dol.is_file():
            raise RuntimeError(f"clean main DOL not found: {main_dol}")
        if not game_dir.is_dir():
            raise RuntimeError(f"extracted game directory not found: {game_dir}")
        if not 1 <= args.jobs <= 256:
            raise RuntimeError("--jobs must be 1..256")

        generation = build_root / "llvm-generation"
        generator_script = SCRIPT_DIR / "generate_local_llvm.py"
        command = [
            sys.executable, str(generator_script),
            "--dolrecomp", str(dolrecomp),
            "--main-dol", str(main_dol),
            "--game-dir", str(game_dir),
            "--work-dir", str(generation),
            "--game-id", args.game_id,
            "--jobs", str(args.jobs),
            "--chunk-instructions", str(args.chunk_instructions),
        ]
        if args.state_in_memory:
            command.append("--state-in-memory")
        if args.skip_llvm_probe:
            command.append("--skip-probe")
        run(command)
        generated = find_generated(generation / "dolrecomp-output")
        verify_codegen_provenance(
            generated, args.chunk_instructions, args.state_in_memory
        )
        objects = validate_llvm_generated.validate(
            generated, require_openspm_wpar=True
        )

        stage = build_root / "stage-llvm"
        staged_cpu = stage / "cpu_runtime"
        identity_dir = stage / "identity"
        # DolRecomp 40637c keeps its public runtime include tree under src/.
        # Reuse the existing immutable-copy staging helper by giving it that
        # runtime root; this preserves the local WPAR fallback hook without
        # mutating the compiler checkout.
        build_local_module.stage_cpu_runtime(runtime_source, staged_cpu)
        patch_staged_broadway_runtime(staged_cpu)
        build_local_module.write_identity(
            identity_dir / "openspm_local_identity.h",
            args.game_id,
            build_local_module.dol_entry(main_dol),
            build_local_module.sha256_file(main_dol),
        )

        if args.no_cmake:
            if args.toolchain != "clang-freestanding" or args.llvm_bin is None:
                raise RuntimeError("--no-cmake needs --toolchain clang-freestanding and --llvm-bin")
            import direct_module_build

            built = direct_module_build.build(
                direct_module_build.Toolchain.from_bin(args.llvm_bin.resolve()),
                direct_module_build.ModuleInputs(
                    generated, staged_cpu, identity_dir, args.chunk_instructions,
                    1 if args.state_in_memory else 0, 1 if args.heavy_diagnostics else 0),
                build_root / "direct-clang-freestanding", args.jobs)
        else:
            built = None
        module_build = build_root / module_build_dir_name(args.toolchain)
        configure = [
            args.cmake,
            "-S", str(SCRIPT_DIR),
            "-B", str(module_build),
            f"-DCMAKE_BUILD_TYPE={args.config}",
            f"-DOPENSPM_ROOT={root}",
            f"-DOPENSPM_GENERATED_DIR={generated}",
            f"-DOPENSPM_CPU_RUNTIME_DIR={staged_cpu}",
            f"-DOPENSPM_IDENTITY_DIR={identity_dir}",
            "-DOPENSPM_GENERATED_BACKEND=llvm",
            f"-DOPENSPM_LLVM_CHUNK_INSTRUCTIONS={args.chunk_instructions}",
            f"-DOPENSPM_LLVM_STATE_IN_MEMORY={1 if args.state_in_memory else 0}",
            f"-DOPENSPM_LLVM_HEAVY_DIAGNOSTICS={1 if args.heavy_diagnostics else 0}",
        ]
        configure += toolchain_cmake_args(
            args.toolchain, args.llvm_bin.resolve() if args.llvm_bin else None
        )
        if args.generator:
            configure += ["-G", args.generator]
        if built is None:
            run(configure)
            run([
                args.cmake, "--build", str(module_build), "--config", args.config,
                "--target", "openspm_local_native", "--parallel", str(args.jobs),
            ])
            built = build_local_module.locate_module(module_build)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / (
            "openspm_local_native.dll" if built.suffix.lower() == ".dll" else built.name
        )
        shutil.copy2(built, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        # Cache stamp (module_stamp.py): what this module was built from, so a
        # later launch can tell a stale module from a current one.
        import module_stamp

        stamp = module_stamp.write(output_dir, module_stamp.expected(
            build_local_module.sha256_file(main_dol).hex(), args.toolchain, args.chunk_instructions,
            1 if args.state_in_memory else 0, 1 if args.heavy_diagnostics else 0, root))
        print("M14 LLVM local module built successfully")
        print(f"llvm_objects={len(objects)}")
        print(f"module_toolchain={args.toolchain}{' (no CMake)' if args.no_cmake else ''}")
        print("openspm_inline_wpar_symbols=proven")
        print(f"clean_source_identity={build_local_module.sha256_file(main_dol).hex()}")
        print(f"module={destination}")
        print(f"module_sha256={digest}")
        print(f"module_stamp={stamp}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: LLVM local-module build failed: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
