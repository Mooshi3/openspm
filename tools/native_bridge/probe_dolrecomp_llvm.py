#!/usr/bin/env python3
"""Fail-closed capability probe for DolRecomp's native LLVM object backend.

The CLI can know about --backend=llvm even when the DolRecomp binary was built
without LLVM development libraries. A help-text check is therefore insufficient.
This probe creates a tiny synthetic GameCube DOL and asks the selected DolRecomp
executable to generate host LLVM objects from it. No player-owned data is read.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


def write_probe_dol(path: Path) -> None:
    data = bytearray(0x200)
    struct.pack_into(">I", data, 0x00, 0x100)
    struct.pack_into(">I", data, 0x48, 0x80003100)
    struct.pack_into(">I", data, 0x90, 0x20)
    struct.pack_into(">I", data, 0xE0, 0x80003100)
    instructions = [
        0x38600000, 0x38630001, 0x60000000, 0x4E800020,
        0x60000000, 0x60000000, 0x60000000, 0x60000000,
    ]
    for index, raw in enumerate(instructions):
        struct.pack_into(">I", data, 0x100 + index * 4, raw)
    path.write_bytes(data)


def _find_generated(output: Path) -> Path | None:
    direct = output / "generated"
    if (direct.is_dir() and (direct / "generated.h").is_file()):
        return direct
    matches = [p.parent for p in output.rglob("generated.h")
               if (p.parent / "chunks").is_dir()]
    return matches[0] if len(matches) == 1 else None


def native_objects(generated: Path | None) -> list[Path]:
    return sorted((generated / "chunks").glob("*.o")) if generated else []


def object_magic_ok(path: Path) -> bool:
    magic = path.read_bytes()[:4]
    if os.name == "nt":
        return len(magic) >= 2 and magic[:2] == b"\x64\x86"
    return magic == b"\x7fELF"


def probe(executable: Path, keep_output: Path | None = None,
          timeout: int = 120) -> bool:
    exe = executable.resolve()
    if not exe.is_file():
        raise RuntimeError(f"DolRecomp executable not found: {exe}")

    with tempfile.TemporaryDirectory(prefix="openspm-dolllvm-probe-") as temp_name:
        temp = Path(temp_name)
        dol = temp / "probe.dol"
        output = temp / "out"
        write_probe_dol(dol)
        command = [
            str(exe), "--gamecube", "--backend=llvm", "--targets=host", "-j1",
            str(dol), str(output),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        generated = _find_generated(output)
        objects = native_objects(generated)
        header = generated / "generated.h" if generated else None
        manifest = generated / "generated.c" if generated else None
        header_text = header.read_text(encoding="utf-8", errors="replace") if header and header.is_file() else ""
        manifest_text = manifest.read_text(encoding="utf-8", errors="replace") if manifest and manifest.is_file() else ""
        capable = bool(
            completed.returncode == 0
            and objects
            and all(object_magic_ok(obj) for obj in objects)
            and "DOLRECOMP_BACKEND_LLVM" in header_text
            and "DOLRECOMP_MODULE_ABI_V4" in header_text
            and "// object: chunks/" in manifest_text
        )
        if capable and keep_output is not None:
            destination = keep_output.resolve()
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(output, destination)
        if not capable:
            tail = "\n".join(completed.stdout.splitlines()[-20:])
            print(
                "DolRecomp LLVM capability: NO\n"
                f"exit={completed.returncode} objects={len(objects)} "
                f"header={int(bool(header and header.is_file()))} "
                f"manifest={int(bool(manifest and manifest.is_file()))}\n{tail}",
                file=sys.stderr,
            )
        return capable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dolrecomp", type=Path)
    parser.add_argument("--keep-output", type=Path)
    args = parser.parse_args()
    try:
        capable = probe(args.dolrecomp, args.keep_output)
        if capable:
            print(f"DolRecomp LLVM capability: YES format={'COFF' if os.name == 'nt' else 'ELF'}")
            return 0
        return 2
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError) as exc:
        print(f"error: DolRecomp LLVM capability probe failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
