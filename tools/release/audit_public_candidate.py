#!/usr/bin/env python3
"""Fail if an OpenSPM public candidate contains player/game-derived material.

This audit is deliberately conservative. A public candidate is expected to
contain OpenSPM executables/libraries, OpenSPM documentation and settings
templates, and redistributable third-party runtime components with their
license texts only. The player-local native module and every form of Wii disc,
extracted executable, REL, archive, texture, generated game payload or
generated LLVM object stay outside the public artifact.

Checks, in order, for every file:
  * extension, file name and directory name deny-lists;
  * size cap (disc images are GBs; OpenSPM's largest runtime DLLs are tens of MB);
  * content sniffing: Wii/GameCube disc headers, WBFS/WIA/RVZ containers,
    Nintendo archive/compression/texture magics, DOL and REL header shapes;
  * .exe/.dll must be PE images, and no PE may export the player-local module
    entry point (openspm_native_module_query), whatever the file is named.

Used by CI (build-m14-native.yml) and by tools/release/make_release_zip.py.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

PROHIBITED_EXTENSIONS = {
    # disc images and containers
    ".wbfs", ".iso", ".rvz", ".wia", ".gcz", ".ciso", ".wdf", ".nkit",
    # executables and modules of the game
    ".dol", ".rel", ".elf", ".wad",
    # disc/partition files (boot.bin, bi2.bin, fst.bin, ticket.bin, tmd.bin,
    # cert.bin, relF.bin) and Nintendo archives, compression and assets
    ".bin", ".arc", ".carc", ".szs", ".u8", ".rarc", ".tpl", ".brres",
    ".brsar", ".brstm", ".bnr", ".thp", ".dsp", ".tik", ".tmd",
    # generated LLVM/C objects of the per-player module
    ".obj", ".o", ".bc", ".ll",
    # save data and state dumps
    ".sav", ".gci", ".raw",
}

PROHIBITED_FILENAMES = {
    "main.dol",
    "boot.bin",
    "bi2.bin",
    "fst.bin",
    "apploader.img",
    "ticket.bin",
    "tmd.bin",
    "cert.bin",
    "openspm_local_native.dll",
    "openspm_local_native.pdb",
    "openspm_local_identity.h",
    "generated.h",
    "r8pe01.h",
    "r8pe01.c",
}

# These directory names are characteristic of an extracted Wii disc, of the
# private/generated M14 cache, or of player content next to the exe. Runtime
# DLL folders are intentionally not here.
PROHIBITED_DIRECTORY_NAMES = {
    "sys",
    "files",
    "disc",
    "generated",
    "generated-direct",
    "r8pe01_generated",
    "dolrecomp-output",
    "llvm-generation",
    "llvm-candidate",
    "native-output",
    "local-module-build",
    "chunks",
    ".openspm",
    "user-openspm",
    "texturepacks",
    "screenshots",
    "saves",
}

# Anything bigger is not an OpenSPM release file (and may be a renamed image).
MAX_FILE_BYTES = 256 * 1024 * 1024

LOCAL_MODULE_EXPORT = b"openspm_native_module_query"

REQUIRED_ROOT_FILES = {"OpenSPMNative.exe"}
REQUIRED_RUNTIME_FILES = {
    "openspm_native_backend.dll",
    "openspm_renderer.dll",
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _looks_like_dol(head: bytes) -> bool:
    """DOL has no magic: 7 text + 11 data (offset, address, size) tables and an
    entry point. Retail DOLs put text0 at file offset 0x100 and load into
    0x80000000..0x81800000."""
    if len(head) < 0xE4 or _be32(head, 0) != 0x100:
        return False
    in_ram = lambda v: 0x80000000 <= v < 0x81800000  # noqa: E731
    addresses = [_be32(head, 0x48 + 4 * i) for i in range(18)]
    if not in_ram(addresses[0]):
        return False
    if any(a and not in_ram(a) for a in addresses):
        return False
    return in_ram(_be32(head, 0xE0))


def _looks_like_rel(head: bytes) -> bool:
    """REL module header: id, prev/next links 0, section count, section-table
    offset right after the header, version 1..3."""
    if len(head) < 0x40:
        return False
    return (
        _be32(head, 0x04) == 0
        and _be32(head, 0x08) == 0
        and 1 <= _be32(head, 0x0C) <= 32
        and _be32(head, 0x10) in (0x40, 0x48, 0x4C)
        and _be32(head, 0x1C) in (1, 2, 3)
    )


def sniff_game_content(head: bytes) -> str | None:
    """Returns a reason when `head` (the first bytes of a file) is game data."""
    if len(head) >= 0x20 and _be32(head, 0x18) == 0x5D1C9EA3:
        return "Wii disc header"
    if len(head) >= 0x20 and _be32(head, 0x1C) == 0xC2339F3D:
        return "GameCube disc header"
    magics = {
        b"WBFS": "WBFS container",
        b"WIA\x01": "WIA container",
        b"RVZ\x01": "RVZ container",
        b"Yaz0": "Yaz0-compressed Nintendo file",
        b"Yay0": "Yay0-compressed Nintendo file",
        b"\x55\xAA\x38\x2D": "U8 archive",
        b"RARC": "RARC archive",
        b"\x00\x20\xAF\x30": "TPL texture",
        b"RSAR": "BRSAR sound archive",
        b"RSTM": "BRSTM stream",
        b"bres": "BRRES archive",
    }
    for magic, what in magics.items():
        if head.startswith(magic):
            return what
    if _looks_like_dol(head):
        return "DOL executable header"
    if _looks_like_rel(head):
        return "REL module header"
    return None


def pe_export_names(data: bytes) -> list[bytes] | None:
    """Export names of a PE image, [] when it has none, None when unparsable."""
    try:
        if data[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return None
        coff = pe + 4
        sections = struct.unpack_from("<H", data, coff + 2)[0]
        optional_size = struct.unpack_from("<H", data, coff + 16)[0]
        optional = coff + 20
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic == 0x20B:
            directories = optional + 112
        elif magic == 0x10B:
            directories = optional + 96
        else:
            return None
        count = struct.unpack_from("<I", data, directories - 4)[0]
        if count < 1:
            return []
        export_rva, export_size = struct.unpack_from("<II", data, directories)
        if not export_rva or not export_size:
            return []
        table = optional + optional_size
        spans = []
        for i in range(sections):
            entry = table + 40 * i
            vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, entry + 8)
            spans.append((vaddr, max(vsize, raw_size), raw_ptr))

        def offset(rva: int) -> int:
            for vaddr, size, raw_ptr in spans:
                if vaddr <= rva < vaddr + size:
                    return raw_ptr + (rva - vaddr)
            raise ValueError("rva outside sections")

        directory = offset(export_rva)
        name_count = struct.unpack_from("<I", data, directory + 0x18)[0]
        names_rva = struct.unpack_from("<I", data, directory + 0x20)[0]
        if name_count > 65536:
            return None
        names = []
        names_at = offset(names_rva) if name_count else 0
        for i in range(name_count):
            start = offset(struct.unpack_from("<I", data, names_at + 4 * i)[0])
            end = data.index(b"\0", start)
            names.append(data[start:end])
        return names
    except (struct.error, ValueError, IndexError):
        return None


def ini_player_path(text: str) -> str:
    """Non-empty [Game] path/wit line of an OpenSPM.ini, or "" (a player's
    settings name their disc image and folders; a release ships the template)."""
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        if line.startswith("["):
            section = line[1:line.find("]")].strip().lower() if "]" in line else None
            continue
        if section == "game" and "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() in ("path", "wit") and value.strip():
                return f"{key.strip()} = {value.strip()}"
    return ""


def audit_file(path: pathlib.Path, relative: pathlib.PurePath) -> None:
    lower_name = path.name.lower()
    lower_parts = {part.lower() for part in relative.parts[:-1]}
    if path.suffix.lower() in PROHIBITED_EXTENSIONS:
        fail(f"prohibited game/disc/generated extension in public candidate: {relative}")
    if lower_name in PROHIBITED_FILENAMES:
        fail(f"prohibited player/game-derived file in public candidate: {relative}")
    bad_dirs = lower_parts & PROHIBITED_DIRECTORY_NAMES
    if bad_dirs:
        fail(
            f"prohibited extracted/private directory in public candidate: "
            f"{relative} ({', '.join(sorted(bad_dirs))})"
        )
    if "key" in lower_name and path.stat().st_size in (16, 32):
        fail(f"file looks like a console key; OpenSPM ships no keys: {relative}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        fail(f"file is too large for an OpenSPM release file ({size} bytes): {relative}")

    with path.open("rb") as source:
        head = source.read(0x100)
    reason = sniff_game_content(head)
    if reason:
        fail(f"game data ({reason}) in public candidate: {relative}")

    if path.suffix.lower() in (".ini", ".example"):
        leaked = ini_player_path(path.read_text(encoding="utf-8", errors="replace"))
        if leaked:
            fail(f"{relative} is a player's settings file ({leaked}); ship the empty template")

    if path.suffix.lower() in (".exe", ".dll"):
        if not head.startswith(b"MZ"):
            fail(f"{relative} is named like a Windows binary but is not a PE image")
        data = path.read_bytes()
        names = pe_export_names(data)
        if names is None:
            fail(f"{relative} is not a well-formed PE image")
        if LOCAL_MODULE_EXPORT in names:
            fail(
                f"{relative} exports {LOCAL_MODULE_EXPORT.decode()}: it is a player-local "
                "native game module and must never be distributed"
            )


def audit(root: pathlib.Path, require_layout: bool) -> None:
    if not root.is_dir():
        fail(f"candidate root does not exist or is not a directory: {root}")

    root = root.resolve()
    files = sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink())
    if not files:
        fail("candidate is empty")

    for path in files:
        relative = path.relative_to(root)
        if path.is_symlink():
            fail(f"symbolic link in public candidate: {relative}")
        audit_file(path, relative)

    if require_layout:
        missing_root = [name for name in REQUIRED_ROOT_FILES if not (root / name).is_file()]
        if missing_root:
            fail(f"missing required candidate file(s): {', '.join(sorted(missing_root))}")

        runtime = root / "runtime"
        if not runtime.is_dir():
            fail("missing required runtime/ directory")
        missing_runtime = [name for name in REQUIRED_RUNTIME_FILES if not (runtime / name).is_file()]
        if missing_runtime:
            fail(
                "missing required runtime file(s): "
                + ", ".join(sorted(missing_runtime))
            )

    print(f"public candidate audit passed: {len(files)} files, no game-derived payloads detected")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument(
        "--require-layout",
        action="store_true",
        help="also require the strict M14 launcher/backend/renderer layout",
    )
    args = parser.parse_args()

    try:
        audit(args.candidate, args.require_layout)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
