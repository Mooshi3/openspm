#!/usr/bin/env python3
"""Locate and decode Super Paper Mario's player-owned relF input.

Retail SPM stores the live module as /rel/relF.bin using Nintendo/CX LZSS10.
The game also supports an uncompressed /rel/relF.rel fallback if the compressed
file is absent.  OpenSPM mirrors that preference without depending on Dolphin,
ModernGekko, decomp-toolkit, or an external decompressor.

This module operates only on player-local files under .openspm (or an explicitly
provided path).  It does not contain or redistribute any Nintendo bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

LZ10_MAGIC = 0x10
EXPECTED_RELF_SIZE = 0x31BEC8


@dataclass(frozen=True)
class RelFInput:
    path: Path
    storage: str
    data: bytes
    source_sha256: str
    decoded_sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decompress_lz10(source: bytes) -> bytes:
    if len(source) < 4:
        raise RuntimeError("LZ10 source is shorter than its 4-byte header")
    if source[0] != LZ10_MAGIC:
        raise RuntimeError(
            f"expected Nintendo LZ10 magic 0x10, got 0x{source[0]:02X}"
        )

    expected = source[1] | (source[2] << 8) | (source[3] << 16)
    if expected == 0:
        # SPM relF is small enough for the ordinary 24-bit LZ10 size field.
        # Do not guess at newer/extended Nintendo compression variants here.
        raise RuntimeError("LZ10 header declares zero/extended output size")
    if expected != EXPECTED_RELF_SIZE:
        raise RuntimeError(
            f"retail relF LZ10 size mismatch: expected 0x{EXPECTED_RELF_SIZE:X}, "
            f"header declares 0x{expected:X}"
        )

    output = bytearray()
    cursor = 4
    while len(output) < expected:
        if cursor >= len(source):
            raise RuntimeError("LZ10 stream ended before a flag byte")
        flags = source[cursor]
        cursor += 1
        for bit in range(7, -1, -1):
            if len(output) == expected:
                break
            if (flags & (1 << bit)) == 0:
                if cursor >= len(source):
                    raise RuntimeError("LZ10 stream ended during a literal")
                output.append(source[cursor])
                cursor += 1
                continue

            if cursor + 1 >= len(source):
                raise RuntimeError("LZ10 stream ended during a backreference")
            first = source[cursor]
            second = source[cursor + 1]
            cursor += 2
            length = (first >> 4) + 3
            distance = (((first & 0x0F) << 8) | second) + 1
            if distance > len(output):
                raise RuntimeError(
                    f"LZ10 backreference distance {distance} exceeds "
                    f"decoded prefix {len(output)}"
                )
            if len(output) + length > expected:
                raise RuntimeError(
                    "LZ10 backreference would exceed the declared output size"
                )
            for _ in range(length):
                output.append(output[-distance])

    if len(output) != expected:
        raise RuntimeError(
            f"LZ10 decoded size mismatch: expected {expected}, got {len(output)}"
        )
    return bytes(output)


def find_relf_source(root: Path) -> Path:
    root = root.resolve()
    compressed = [
        path for path in root.rglob("*")
        if path.is_file() and path.name.lower() == "relf.bin"
    ]
    uncompressed = [
        path for path in root.rglob("*")
        if path.is_file() and path.name.lower() == "relf.rel"
    ]

    # Match the game's own policy: compressed retail relF.bin wins.  Ambiguous
    # duplicate matches fail closed rather than selecting a random extraction.
    if compressed:
        if len(compressed) != 1:
            listing = ", ".join(str(path) for path in compressed[:8])
            raise RuntimeError(
                f"expected one retail relF.bin under {root}; found "
                f"{len(compressed)}: {listing}"
            )
        return compressed[0]
    if uncompressed:
        if len(uncompressed) != 1:
            listing = ", ".join(str(path) for path in uncompressed[:8])
            raise RuntimeError(
                f"expected one fallback relF.rel under {root}; found "
                f"{len(uncompressed)}: {listing}"
            )
        return uncompressed[0]
    raise RuntimeError(
        f"no relF.bin or relF.rel was found under extracted game {root}"
    )


def load_relf(path: Path) -> RelFInput:
    path = path.resolve()
    raw = path.read_bytes()
    lower = path.name.lower()
    if lower == "relf.bin":
        decoded = decompress_lz10(raw)
        storage = "retail-lz10"
    elif lower == "relf.rel":
        decoded = raw
        storage = "uncompressed-fallback"
        if len(decoded) != EXPECTED_RELF_SIZE:
            raise RuntimeError(
                f"uncompressed relF size mismatch: expected 0x{EXPECTED_RELF_SIZE:X}, "
                f"got 0x{len(decoded):X}"
            )
    else:
        # Explicit callers may use another filename, but only accept a clearly
        # recognizable LZ10 stream or the exact raw REL size.
        if raw and raw[0] == LZ10_MAGIC:
            decoded = decompress_lz10(raw)
            storage = "explicit-lz10"
        elif len(raw) == EXPECTED_RELF_SIZE:
            decoded = raw
            storage = "explicit-uncompressed"
        else:
            raise RuntimeError(
                f"explicit relF input is neither LZ10 nor exact raw REL size: {path}"
            )

    return RelFInput(
        path=path,
        storage=storage,
        data=decoded,
        source_sha256=sha256(raw),
        decoded_sha256=sha256(decoded),
    )


def load_from_game(root: Path) -> RelFInput:
    return load_relf(find_relf_source(root))
