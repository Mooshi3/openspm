#!/usr/bin/env python3
"""Generate temporary M14 C source from the player's local main.dol.

This is development/migration tooling. It invokes a locally available DolRecomp
binary but never stores generated game-derived source in the OpenSPM repository.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys


def locate_generated(root: pathlib.Path) -> pathlib.Path:
    matches = []
    for header in root.rglob("generated.h"):
        candidate = header.parent
        if candidate.joinpath("chunks").is_dir():
            matches.append(candidate)
    if len(matches) != 1:
        listing = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected exactly one generated.h/chunks tree, found: {listing}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dolrecomp", required=True, type=pathlib.Path,
                        help="local dolrecomp executable")
    parser.add_argument("--main-dol", required=True, type=pathlib.Path)
    parser.add_argument("--work-dir", required=True, type=pathlib.Path)
    parser.add_argument("--game-id", default="R8PE01")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    dolrecomp = args.dolrecomp.resolve()
    main_dol = args.main_dol.resolve()
    work_dir = args.work_dir.resolve()
    if not dolrecomp.is_file():
        parser.error("--dolrecomp does not exist")
    if not main_dol.is_file():
        parser.error("--main-dol does not exist")
    if args.jobs < 1 or args.jobs > 256:
        parser.error("--jobs must be 1..256")

    generated_root = work_dir / "dolrecomp-output"
    if generated_root.exists():
        shutil.rmtree(generated_root)
    generated_root.mkdir(parents=True)

    command = [
        str(dolrecomp),
        f"-j{args.jobs}",
        "--cpu", "broadway",
        "--backend", "c",
        "--semantics", "exact",
        str(main_dol),
        args.game_id,
        str(generated_root),
    ]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)

    generated = locate_generated(generated_root)
    chunk_count = len(list(generated.joinpath("chunks").glob("chunk_*.c")))
    if chunk_count == 0:
        raise RuntimeError("generated tree contains no chunk_*.c files")
    print(f"generated={generated}")
    print(f"chunks={chunk_count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"dolrecomp failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
