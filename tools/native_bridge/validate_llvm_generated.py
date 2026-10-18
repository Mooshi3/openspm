#!/usr/bin/env python3
"""Validate a DolRecomp LLVM generated tree before OpenSPM links it.

The validator is intentionally strict and read-only. It checks the public LLVM
markers, parses every native-object path from generated.c, rejects paths that
escape the generated tree, verifies every object exists, and verifies Windows
AMD64 COFF / ELF object magic as appropriate.

For the OpenSPM-patched candidate it can additionally prove that the generated
native object set really references every inline-WPAR bridge symbol. This keeps
an otherwise-valid stock/stale LLVM object set from contaminating the A/B: the
build must contain the private emitter's fast-path globals and flush/overflow
callbacks before it is allowed to link.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

OBJECT_RE = re.compile(r"^// object: (?P<path>chunks/[^\s]+\.o)\s*$", re.MULTILINE)

# These are ordinary undefined/link references in patched COFF/ELF objects. Long
# COFF names live in the string table and ELF names in .strtab, so a raw-byte
# membership check is format-independent and does not require llvm-nm on the
# player's machine. Requiring the complete set proves both the direct append
# state and the completed-line/overflow callbacks survived LLVM optimization.
OPENSPM_WPAR_SYMBOLS = (
    b"openspm_m14_llvm_wg_have_fifo_ptr",
    b"openspm_m14_llvm_wg_bytes_ptr",
    b"openspm_m14_llvm_wg_size_ptr",
    b"openspm_m14_llvm_wg_pending_ptr",
    b"openspm_m14_flush_wg_llvm",
    b"openspm_m14_try_wg_write_llvm",
)


def _prove_openspm_wpar(objects: list[pathlib.Path]) -> None:
    remaining = set(OPENSPM_WPAR_SYMBOLS)
    # Stop as soon as every symbol has been observed. This normally touches only
    # the first few store-heavy chunks rather than rereading the entire module.
    for candidate in objects:
        if not remaining:
            break
        blob = candidate.read_bytes()
        remaining.difference_update(symbol for symbol in tuple(remaining)
                                    if symbol in blob)
    if remaining:
        names = ", ".join(symbol.decode("ascii") for symbol in sorted(remaining))
        raise RuntimeError(
            "LLVM object set does not prove the OpenSPM inline-WPAR emitter; "
            f"missing link symbol(s): {names}"
        )


def validate(root: pathlib.Path, *, require_openspm_wpar: bool = False,
             coff: bool | None = None) -> list[pathlib.Path]:
    """coff: expect AMD64 COFF objects (default: on Windows, else ELF; a Linux
    cross build of the Windows module passes True)."""
    root = root.resolve()
    header = root / "generated.h"
    manifest = root / "generated.c"
    if not header.is_file() or not manifest.is_file():
        raise RuntimeError("LLVM generated tree requires generated.h and generated.c")

    header_text = header.read_text(encoding="utf-8")
    if "DOLRECOMP_BACKEND_LLVM" not in header_text:
        raise RuntimeError("generated.h does not declare DOLRECOMP_BACKEND_LLVM")
    if "DOLRECOMP_MODULE_ABI_V4" not in header_text:
        raise RuntimeError("generated.h does not declare DOLRECOMP_MODULE_ABI_V4")

    manifest_text = manifest.read_text(encoding="utf-8")
    rel_paths = OBJECT_RE.findall(manifest_text)
    if not rel_paths:
        raise RuntimeError("generated.c contains no // object: chunks/*.o entries")
    if len(rel_paths) != len(set(rel_paths)):
        raise RuntimeError("generated.c contains duplicate native object entries")

    objects: list[pathlib.Path] = []
    for relative in rel_paths:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"native object escapes generated root: {relative}") from exc
        if not candidate.is_file():
            raise RuntimeError(f"native object is missing: {relative}")
        magic = candidate.read_bytes()[:4]
        if (os.name == "nt") if coff is None else coff:
            if len(magic) < 2 or magic[:2] != b"\x64\x86":
                raise RuntimeError(f"native object is not AMD64 COFF: {relative}")
        elif magic != b"\x7fELF":
            raise RuntimeError(f"native object is not ELF: {relative}")
        objects.append(candidate)

    if require_openspm_wpar:
        _prove_openspm_wpar(objects)
    return objects


def cmake_path(path: pathlib.Path) -> str:
    # CMake/Ninja accept forward slashes on Windows. Avoid feeding a generated
    # semicolon list containing native backslashes, where sequences such as \t
    # or \n can become parser/escape hazards after variable expansion.
    return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated_dir", type=pathlib.Path)
    parser.add_argument("--print-cmake", action="store_true",
                        help="print validated CMake-safe object paths separated by semicolons")
    parser.add_argument(
        "--require-openspm-wpar", action="store_true",
        help="require native objects to reference the OpenSPM inline-WPAR glue",
    )
    args = parser.parse_args()
    try:
        objects = validate(
            args.generated_dir,
            require_openspm_wpar=args.require_openspm_wpar,
        )
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(f"error: LLVM generated-tree validation failed: {exc}", file=sys.stderr)
        return 1
    if args.print_cmake:
        print(";".join(cmake_path(path) for path in objects))
    else:
        proof = " openspm_wpar=proven" if args.require_openspm_wpar else ""
        print(
            "M14 DolRecomp LLVM generated tree valid: "
            f"objects={len(objects)} format={'COFF' if os.name == 'nt' else 'ELF'}{proof}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())