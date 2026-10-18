#!/usr/bin/env python3
"""Build the player-local native module without CMake (release Stage 3).

docs/RELEASE-PLAN.md: the release zip carries clang-cl, lld-link and
llvm-dlltool from the pinned LLVM, not CMake, Ninja or Visual Studio. This
driver runs the same compile and link the CMake project in this directory
generates for OPENSPM_LOCAL_TOOLCHAIN=clang-freestanding (the only toolchain a
release uses), command for command:

  llvm-dlltool -m i386:x86-64 -d m14_freestanding_<dll>.def -l <dll>.lib
  clang-cl <COMMON> [-TC] [-flto=thin] -c <source> /Fo<obj>      (5 sources)
  lld-link @link.rsp   (payload objects, bridge objects, /MAP, /NODEFAULTLIB, 2 libs)

test_direct_module_build.py compares these flags with the ones CMake
generates (when CMake and Ninja are installed) so the two never drift, and
compiles the runtime sources with them.

Everything here is OpenSPM code; the inputs (the generated tree, the staged
DolRecomp runtime, the identity header) are the player's own, made on their
PC.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validate_llvm_generated  # noqa: E402

ACTIVE_BRIDGE = HERE / "legacy_c_adapter_v42.c.in"
FREESTANDING_INCLUDE = HERE / "freestanding" / "include"
CRT_SOURCE = HERE / "m14_freestanding_crt.c"
IMPORT_DLLS = ("kernel32", "ucrtbase")
MODULE_NAME = "openspm_local_native"

# The contracts CMakeLists.txt checks in the selected bridge before it builds.
BRIDGE_MARKERS = (
    "M14 native retrace phase-lock active: fixed 60 Hz deadline grid; elapsed deadlines are skipped, never burst-delivered.",
    "m14-dev49-llvm-pc-display-list-return",
    "m14-dev51-llvm-test-ready-gx-input",
    "m14-dev52-llvm-frame-phase-continuation",
)


@dataclass(frozen=True)
class ModuleInputs:
    generated: Path     # DolRecomp LLVM output (generated.h, generated.c, chunks/*.o)
    cpu_runtime: Path   # staged runtime root (cpu/cpu.c, common/types.h)
    identity: Path      # directory holding openspm_local_identity.h
    chunk_instructions: int = 256
    state_in_memory: int = 1
    heavy_diagnostics: int = 0


@dataclass(frozen=True)
class Toolchain:
    clang_cl: Path
    lld_link: Path
    dlltool: Path

    @staticmethod
    def from_bin(llvm_bin: Path) -> "Toolchain":
        exe = ".exe" if os.name == "nt" else ""
        return Toolchain(llvm_bin / f"clang-cl{exe}", llvm_bin / f"lld-link{exe}", llvm_bin / f"llvm-dlltool{exe}")


def sources(inputs: ModuleInputs) -> list[tuple[Path, bool]]:
    """(source, ThinLTO) in CMake's order; only the bridge and cpu.c are bitcode."""
    return [
        (ACTIVE_BRIDGE, True),
        (inputs.cpu_runtime / "cpu" / "cpu.c", True),
        (ROOT / "backend" / "audio" / "openspm_audio_device.c", False),
        (ROOT / "backend" / "audio" / "openspm_ax_hle.c", False),
        (CRT_SOURCE, False),  # never bitcode: codegen may call memset/__chkstk after LTO
    ]


def common_flags(inputs: ModuleInputs) -> list[str]:
    """The flags CMake gives every source (Release, clang-freestanding)."""
    return [
        "/nologo",
        f"-DOPENSPM_M14_HEAVY_DIAGNOSTICS={inputs.heavy_diagnostics}",
        f"-DOPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS={inputs.chunk_instructions}",
        f"-DOPENSPM_M14_LLVM_STATE_IN_MEMORY={inputs.state_in_memory}",
        f"-D{MODULE_NAME}_EXPORTS",
        f"-I{ROOT / 'include'}",
        f"-I{HERE}",
        f"-I{inputs.generated}",
        f"-I{inputs.cpu_runtime}",
        f"-I{inputs.identity}",
        f"-imsvc{FREESTANDING_INCLUDE}",
        "/DWIN32", "/D_WINDOWS", "/O2", "/Ob2", "/DNDEBUG", "/std:c11", "-MD",
        "/O2", "/clang:-fno-strict-aliasing", "/X", "/Zl", "/GS-",
    ]


def compile_command(tools: Toolchain, inputs: ModuleInputs, source: Path, lto: bool, obj: Path) -> list[str]:
    command = [str(tools.clang_cl)] + common_flags(inputs)
    if source.suffix == ".in":
        command.insert(1 + common_flags(inputs).index(f"-imsvc{FREESTANDING_INCLUDE}") + 1, "-TC")
    if lto:
        command.append("-flto=thin")
    return command + [f"/Fo{obj}", "-c", "--", str(source)]


def import_lib_commands(tools: Toolchain, out_dir: Path) -> list[list[str]]:
    return [[str(tools.dlltool), "-m", "i386:x86-64", "-d", str(HERE / f"m14_freestanding_{dll}.def"),
             "-l", str(out_dir / f"openspm_freestanding_{dll}.lib")] for dll in IMPORT_DLLS]


def link_arguments(payload: list[Path], objects: list[Path], out_dir: Path) -> list[str]:
    """lld-link's arguments (written to a response file: ~14k payload objects)."""
    return (
        [str(p) for p in payload] + [str(o) for o in objects] +
        [f"/out:{out_dir / (MODULE_NAME + '.dll')}", f"/implib:{out_dir / (MODULE_NAME + '.lib')}",
         f"/pdb:{out_dir / (MODULE_NAME + '.pdb')}", "/dll", "/version:0.0", "/machine:x64",
         "/INCREMENTAL:NO", "/MAP", "/NODEFAULTLIB"] +
        [str(out_dir / f"openspm_freestanding_{dll}.lib") for dll in IMPORT_DLLS]
    )


def _rsp_quote(argument: str) -> str:
    return '"' + argument.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in argument for c in ' \t"') else argument


def check_bridge(bridge: Path = ACTIVE_BRIDGE) -> None:
    text = bridge.read_text(encoding="utf-8")
    for marker in BRIDGE_MARKERS:
        if marker not in text:
            raise RuntimeError(f"{bridge.name} lacks the contract marker {marker!r}")


def build(tools: Toolchain, inputs: ModuleInputs, out_dir: Path, jobs: int = 4,
          coff_objects: bool | None = None, run=subprocess.run) -> Path:
    """Builds <out_dir>/openspm_local_native.dll; returns its path."""
    for tool in (tools.clang_cl, tools.lld_link, tools.dlltool):
        if not tool.is_file():
            raise RuntimeError(f"toolchain file missing: {tool}")
    check_bridge()
    payload = validate_llvm_generated.validate(inputs.generated, require_openspm_wpar=True, coff=coff_objects)
    out_dir.mkdir(parents=True, exist_ok=True)
    obj_dir = out_dir / "obj"
    obj_dir.mkdir(exist_ok=True)
    for command in import_lib_commands(tools, out_dir):
        run(command, check=True)
    jobs_list = []
    for index, (source, lto) in enumerate(sources(inputs)):
        if not source.is_file():
            raise RuntimeError(f"module source missing: {source}")
        jobs_list.append(compile_command(tools, inputs, source, lto, obj_dir / f"{index:02d}_{source.name}.obj"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for future in [pool.submit(run, command, check=True) for command in jobs_list]:
            future.result()
    objects = [Path(command[-4][3:]) for command in jobs_list]  # the /Fo<obj> argument
    rsp = out_dir / "link.rsp"
    rsp.write_text("\n".join(_rsp_quote(a) for a in link_arguments(payload, objects, out_dir)) + "\n", encoding="utf-8")
    run([str(tools.lld_link), "/nologo", f"@{rsp}"], check=True)
    module = out_dir / (MODULE_NAME + ".dll")
    if not module.is_file():
        raise RuntimeError("lld-link finished without producing the module")
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llvm-bin", required=True, type=Path, help="pinned LLVM bin (clang-cl, lld-link, llvm-dlltool)")
    parser.add_argument("--generated", required=True, type=Path)
    parser.add_argument("--cpu-runtime", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--chunk-instructions", type=int, choices=(128, 256, 512), default=256)
    parser.add_argument("--state-in-memory", type=int, choices=(0, 1), default=1)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    try:
        module = build(Toolchain.from_bin(args.llvm_bin.resolve()),
                       ModuleInputs(args.generated.resolve(), args.cpu_runtime.resolve(), args.identity.resolve(),
                                    args.chunk_instructions, args.state_in_memory),
                       args.out_dir.resolve(), args.jobs)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: direct module build failed: {exc}", file=sys.stderr)
        return 1
    print(f"module={module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
