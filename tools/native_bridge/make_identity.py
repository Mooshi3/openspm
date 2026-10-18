#!/usr/bin/env python3
"""Generate the local-only identity header consumed by the M14 native bridge.

The header contains only an input fingerprint and metadata. It never copies game
bytes or symbols into the OpenSPM repository.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys


def parse_entry(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid entry address: {text}") from exc
    if not 0 <= value <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("entry address must fit in uint32")
    return value


def parse_sha256(text: str) -> bytes:
    text = text.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise argparse.ArgumentTypeError("SHA-256 must be exactly 64 hex characters")
    return bytes.fromhex(text)


def source_digest(path: pathlib.Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-id", required=True,
                        help="verified disc/game ID, e.g. R8PE01")
    parser.add_argument("--entry", required=True, type=parse_entry,
                        help="native source entry address, decimal or 0xHEX")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=pathlib.Path,
                        help="verified player-owned input to hash locally")
    source.add_argument("--sha256", type=parse_sha256,
                        help="already-computed source SHA-256")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,31}", args.game_id):
        parser.error("--game-id must be 1-31 ASCII letters/digits/._-")

    if args.source is not None:
        if not args.source.is_file():
            parser.error(f"source does not exist: {args.source}")
        digest = source_digest(args.source)
    else:
        digest = args.sha256

    bytes_text = ", ".join(f"0x{byte:02x}" for byte in digest)
    header = f"""#ifndef OPENSPM_LOCAL_IDENTITY_H\n#define OPENSPM_LOCAL_IDENTITY_H\n\n#define OPENSPM_LOCAL_GAME_ID \"{args.game_id}\"\n#define OPENSPM_LOCAL_ENTRY_PC UINT32_C(0x{args.entry:08X})\nstatic const uint8_t OPENSPM_LOCAL_SOURCE_SHA256[32] = {{\n    {bytes_text}\n}};\n\n#endif\n"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(header, encoding="utf-8", newline="\n")
    print(f"game_id={args.game_id}")
    print(f"entry=0x{args.entry:08X}")
    print(f"sha256={digest.hex()}")
    print(f"wrote={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
