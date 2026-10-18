#!/usr/bin/env python3
"""Cache stamp for the player-local native module (docs/RELEASE-PLAN.md
Stage 3, docs/OPENSPM-PORT-ROADMAP.md Gate B: "a stale cache should rebuild
automatically rather than fail mysteriously").

Next to openspm_local_native.dll the build writes openspm_local_native.stamp.json:

    {"schema": 1,
     "game_sha256": "<main.dol>",           the player's game
     "translator": "<DolRecomp pin>",       prepare_dolrecomp_llvm.PIN
     "openspm_abi": 1, "native_abi": 1,     include/openspm/abi.h, native_api.h
     "transform": "<sha256>",               every OpenSPM file that goes into the module
     "toolchain": "clang-freestanding", "arch": "x86_64",
     "options": {"chunk_instructions": 256, "state_in_memory": 1, "heavy_diagnostics": 0}}

A module is current only when every field matches what this OpenSPM would
build now; `status()` says which field differs, so the log can say why a
rebuild happens. Holds hashes and version numbers only, nothing from the
game but its SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCHEMA = 1
STAMP_NAME = "openspm_local_native.stamp.json"

# What the module is made of on OpenSPM's side: the bridge chain and its
# headers, the freestanding CRT, the codegen transforms (Python), the public
# headers the bridge includes, and the audio sources linked in. Tests and
# docs never change the module and are left out.
_TRANSFORM_GLOBS = (
    ("tools/native_bridge", ("*.c.in", "*.h", "*.inc", "*.c", "*.def", "*.py", "*.json", "CMakeLists.txt")),
    ("tools/native_bridge/freestanding/include", ("*.h",)),
    ("include/openspm", ("*.h", "*.inc")),
    ("backend/audio", ("*.c", "*.h")),
    ("state", ("m14-host-targets.json", "openspm-hooks.json", "openspm-code-patches.json")),
    ("tools/content", ("*.py",)),  # the build finds relF's character-id patches with these
)


def transform_files(root: Path = ROOT) -> list[Path]:
    files: set[Path] = set()
    for folder, patterns in _TRANSFORM_GLOBS:
        for pattern in patterns:
            for path in (root / folder).glob(pattern):
                if path.is_file() and not path.name.startswith("test_") and "__pycache__" not in path.parts:
                    files.add(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def transform_hash(root: Path = ROOT) -> str:
    """sha256 over (relative path, content) of every transform file; line
    endings normalised so a git checkout with CRLF hashes the same."""
    digest = hashlib.sha256()
    for path in transform_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _header_version(header: Path, name: str) -> int:
    match = re.search(rf"#define {name} (\d+)u", header.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"{header.name}: {name} not found")
    return int(match.group(1))


def _translator_pin(root: Path) -> str:
    match = re.search(r'^PIN = "([0-9a-f]{40})"', (root / "tools/native_bridge/prepare_dolrecomp_llvm.py").read_text(
        encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError("prepare_dolrecomp_llvm.py: PIN not found")
    return match.group(1)


def expected(game_sha256: str, toolchain: str, chunk_instructions: int, state_in_memory: int,
             heavy_diagnostics: int = 0, root: Path = ROOT) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", game_sha256):
        raise RuntimeError("game_sha256 must be 64 lowercase hex digits")
    return {
        "schema": SCHEMA,
        "game_sha256": game_sha256,
        "translator": _translator_pin(root),
        "openspm_abi": _header_version(root / "include/openspm/abi.h", "OPENSPM_ABI_VERSION"),
        "native_abi": _header_version(root / "include/openspm/native_api.h", "OPENSPM_NATIVE_ABI_VERSION"),
        "transform": transform_hash(root),
        "toolchain": toolchain,
        "arch": "x86_64",
        "options": {"chunk_instructions": int(chunk_instructions), "state_in_memory": int(state_in_memory),
                    "heavy_diagnostics": int(heavy_diagnostics)},
    }


def write(module_dir: Path, stamp: dict) -> Path:
    path = module_dir / STAMP_NAME
    path.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def status(module_dir: Path, want: dict) -> tuple[bool, str]:
    """(current, reason). Not current when the module or stamp is missing or
    any field differs from what would be built now."""
    if not (module_dir / "openspm_local_native.dll").is_file():
        return False, "no native module yet"
    path = module_dir / STAMP_NAME
    if not path.is_file():
        return False, "the module has no stamp (built before stamps)"
    try:
        have = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"unreadable stamp ({exc})"
    for key in ("schema", "game_sha256", "translator", "openspm_abi", "native_abi", "transform", "toolchain",
                "arch", "options"):
        if have.get(key) != want.get(key):
            names = {"game_sha256": "the game", "translator": "the recompiler", "transform": "OpenSPM's module sources",
                     "openspm_abi": "the OpenSPM ABI", "native_abi": "the native ABI", "options": "the build options"}
            return False, f"{names.get(key, key)} changed"
    return True, "current"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module_dir", type=Path)
    parser.add_argument("--game-sha256", required=True)
    parser.add_argument("--toolchain", default="clang-freestanding")
    parser.add_argument("--chunk-instructions", type=int, default=256)
    parser.add_argument("--state-in-memory", type=int, default=1)
    args = parser.parse_args()
    ok, why = status(args.module_dir, expected(args.game_sha256, args.toolchain, args.chunk_instructions,
                                               args.state_in_memory))
    print(("current: " if ok else "rebuild: ") + why)
    raise SystemExit(0 if ok else 1)
