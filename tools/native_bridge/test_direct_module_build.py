#!/usr/bin/env python3
"""The CMake-free module build (direct_module_build.py) runs exactly what the
CMake project generates for clang-freestanding, and its flags compile the
OpenSPM runtime sources.

- test_matches_cmake: configures this directory's CMake project for a Windows
  cross build (fake generated tree) and compares every compile command and
  the link flags with the driver's. Skipped without cmake, ninja and an LLVM
  with clang-cl / lld-link / llvm-dlltool.
- test_build_plan: the driver's commands on a fake tree (no tools run): the
  import libraries, five compiles (ThinLTO on the bridge and cpu.c only), and
  a response-file link listing every payload object.
- test_runtime_sources_compile: the audio sources and the CRT shim compile
  with the driver's flags (the bridge and cpu.c need the player's generated
  headers). Skipped without clang-cl.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import direct_module_build as dmb
import build_local_llvm_module

WPAR = b" ".join(dmb.validate_llvm_generated.OPENSPM_WPAR_SYMBOLS)


def llvm_bin() -> Path | None:
    for candidate in sorted(glob.glob("/usr/lib/llvm-*/bin"), reverse=True) + [os.environ.get("OPENSPM_LLVM_BIN", "")]:
        if candidate and all((Path(candidate) / t).exists() for t in ("clang-cl", "lld-link", "llvm-dlltool")):
            return Path(candidate)
    return None


def fake_tree(root: Path, magic: bytes, count: int = 3) -> dmb.ModuleInputs:
    gen, cpu, ident = root / "gen", root / "cpu", root / "id"
    (gen / "chunks").mkdir(parents=True)
    (cpu / "cpu").mkdir(parents=True)
    (cpu / "common").mkdir(parents=True)
    ident.mkdir()
    (gen / "generated.h").write_text("#define DOLRECOMP_BACKEND_LLVM 1\n#define DOLRECOMP_MODULE_ABI_V4 1\n")
    (gen / "generated.c").write_text("".join(f"// object: chunks/c{i}.o\n" for i in range(count)))
    for i in range(count):
        (gen / "chunks" / f"c{i}.o").write_bytes(magic + b"\0" * 12 + (WPAR if i == 0 else b""))
    for name in ("openspm_local_v19_llvm_seams.h", "openspm_local_gx_dl_semantics.h"):
        (gen / name).write_text("")
    (cpu / "cpu" / "cpu.c").write_text("")
    (cpu / "common" / "types.h").write_text("")
    (ident / "openspm_local_identity.h").write_text("")
    return dmb.ModuleInputs(gen, cpu, ident, 256, 1, 0)


class DirectModuleBuildTest(unittest.TestCase):
    def test_build_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = fake_tree(root, b"\x64\x86", count=3)
            tools = dmb.Toolchain(root / "clang-cl", root / "lld-link", root / "llvm-dlltool")
            for tool in (tools.clang_cl, tools.lld_link, tools.dlltool):
                tool.write_text("")
            ran: list[list[str]] = []

            def fake_run(command, check):
                ran.append(list(command))
                if command[0] == str(tools.lld_link):
                    (root / "out" / "openspm_local_native.dll").write_bytes(b"MZ")
                return subprocess.CompletedProcess(command, 0)

            module = dmb.build(tools, inputs, root / "out", jobs=2, coff_objects=True, run=fake_run)
            self.assertEqual(module.name, "openspm_local_native.dll")
            dll = [c for c in ran if c[0] == str(tools.dlltool)]
            compiles = [c for c in ran if c[0] == str(tools.clang_cl)]
            links = [c for c in ran if c[0] == str(tools.lld_link)]
            self.assertEqual(len(dll), 2)
            self.assertEqual(len(compiles), 5)
            self.assertEqual(len(links), 1)
            lto = {Path(c[-1]).name: "-flto=thin" in c for c in compiles}
            self.assertEqual(lto, {"legacy_c_adapter_v42.c.in": True, "cpu.c": True, "openspm_audio_device.c": False,
                                   "openspm_ax_hle.c": False, "m14_freestanding_crt.c": False})
            self.assertIn("-TC", next(c for c in compiles if c[-1].endswith(".c.in")))
            rsp = (root / "out" / "link.rsp").read_text()
            for i in range(3):
                self.assertIn(f"c{i}.o", rsp)
            for flag in ("/NODEFAULTLIB", "/MAP", "/dll", "/machine:x64", "openspm_freestanding_kernel32.lib",
                         "openspm_freestanding_ucrtbase.lib"):
                self.assertIn(flag, rsp)
            # ELF objects are refused for the Windows module.
            with self.assertRaises(RuntimeError):
                dmb.build(tools, fake_tree(root / "elf", b"\x7fELF"), root / "out2", coff_objects=True, run=fake_run)

    @unittest.skipUnless(shutil.which("cmake") and shutil.which("ninja") and llvm_bin(), "needs cmake, ninja, LLVM")
    def test_matches_cmake(self) -> None:
        bin_dir = llvm_bin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = fake_tree(root, b"\x7fELF" if os.name != "nt" else b"\x64\x86")
            build = root / "b"
            subprocess.run(["cmake", "-S", str(dmb.HERE), "-B", str(build), "-G", "Ninja", "-DCMAKE_SYSTEM_NAME=Windows",
                            "-DCMAKE_BUILD_TYPE=Release", f"-DOPENSPM_ROOT={dmb.ROOT}",
                            f"-DOPENSPM_GENERATED_DIR={inputs.generated}", f"-DOPENSPM_CPU_RUNTIME_DIR={inputs.cpu_runtime}",
                            f"-DOPENSPM_IDENTITY_DIR={inputs.identity}", "-DOPENSPM_GENERATED_BACKEND=llvm",
                            "-DOPENSPM_LLVM_CHUNK_INSTRUCTIONS=256", "-DOPENSPM_LLVM_STATE_IN_MEMORY=1",
                            "-DOPENSPM_LLVM_HEAVY_DIAGNOSTICS=0"]
                           + build_local_llvm_module.toolchain_cmake_args("clang-freestanding", bin_dir),
                           check=True, capture_output=True)
            commands = subprocess.run(["ninja", "-t", "commands", "openspm_local_native"], cwd=build, check=True,
                                      capture_output=True, text=True).stdout.splitlines()
            tools = dmb.Toolchain.from_bin(bin_dir)
            cmake_compiles = {}
            for line in commands:
                words = line.split()
                if words and words[0].endswith("clang-cl"):
                    source = words[-1]
                    flags = [w for w in words[1:] if not (w.startswith("/Fo") or w.startswith("/Fd"))]
                    flags = flags[:flags.index("/showIncludes")] + flags[flags.index("/showIncludes") + 1:]
                    cmake_compiles[Path(source).name] = flags[:flags.index("-c")]
            self.assertEqual(len(cmake_compiles), 5)
            for source, lto in dmb.sources(inputs):
                ours = dmb.compile_command(tools, inputs, source, lto, root / "x.obj")[1:]
                ours = [w for w in ours if not w.startswith("/Fo")]
                ours = ours[:ours.index("-c")]
                self.assertEqual(ours, cmake_compiles[source.name], source.name)
            link = next(line for line in commands if "lld-link" in line and "/NODEFAULTLIB" in line)

            def normalize(word: str) -> str:
                # Paths differ (CMake's are relative to its build directory):
                # compare the option and the file name.
                if word.startswith("/") and ":" in word:
                    option, value = word.split(":", 1)
                    return f"{option}:{Path(value).name}" if "/" in value or "\\" in value or "." in value else word
                return Path(word).name if word.endswith(".lib") else word

            cmake_flags = [normalize(w) for w in link.split("/out:", 1)[1].split()[1:] if w not in ("&&", ":")]
            ours = dmb.link_arguments([], [], root)
            ours_flags = [normalize(w) for w in ours[1:]]  # after /out:
            self.assertEqual(ours_flags, cmake_flags)

    @unittest.skipUnless(llvm_bin(), "needs clang-cl")
    def test_runtime_sources_compile(self) -> None:
        tools = dmb.Toolchain.from_bin(llvm_bin())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = fake_tree(root, b"\x64\x86")
            for source, lto in dmb.sources(inputs)[2:]:
                obj = root / (source.name + ".obj")
                subprocess.run(dmb.compile_command(tools, inputs, source, lto, obj), check=True, capture_output=True)
                self.assertEqual(obj.read_bytes()[:2], b"\x64\x86", source.name)  # AMD64 COFF


if __name__ == "__main__":
    unittest.main()
