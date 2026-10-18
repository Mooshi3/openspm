#!/usr/bin/env python3
"""Regression contract for DolRecomp Wii/title-aware generated layout."""
from __future__ import annotations

from pathlib import Path
import tempfile

import generate_local_llvm as generator


def test_wii_manifest_replaces_stale_titleless_aliases() -> None:
    with tempfile.TemporaryDirectory(prefix="openspm-llvm-layout-") as temp_name:
        output = Path(temp_name) / "dolrecomp-output"
        title_dir = output / "R8PE01_generated"
        chunks = title_dir / "chunks"
        chunks.mkdir(parents=True)

        # This is the exact migration shape which caused the v24 link failure:
        # the old titleless tree had already been renamed into R8PE01_generated,
        # leaving generic manifests beside newly written Wii/title-aware files.
        (title_dir / "generated.h").write_text("STALE HEADER\n", encoding="utf-8")
        (title_dir / "generated.c").write_text(
            "// stale 14414-object manifest\n", encoding="utf-8"
        )
        current_header = "#define DOLRECOMP_BACKEND_LLVM 1\n#define CURRENT_WII_HEADER 1\n"
        current_manifest = (
            "// object: chunks/chunk_0000_test.o\n"
            "// current 14416-object manifest shape\n"
        )
        current_symbols = "#define CURRENT_WII_SYMBOLS 1\n"
        (title_dir / "R8PE01.h").write_text(current_header, encoding="utf-8")
        (title_dir / "R8PE01.c").write_text(current_manifest, encoding="utf-8")
        (title_dir / "R8PE01_symbols.h").write_text(current_symbols, encoding="utf-8")

        located = generator.locate_generated(output, "R8PE01")
        assert located == title_dir
        assert (title_dir / "generated.h").read_text(encoding="utf-8") == current_header
        assert (title_dir / "generated.c").read_text(encoding="utf-8") == current_manifest
        assert (
            title_dir / "generated_symbols.h"
        ).read_text(encoding="utf-8") == current_symbols
        assert "STALE" not in (title_dir / "generated.h").read_text(encoding="utf-8")
        assert "14414" not in (title_dir / "generated.c").read_text(encoding="utf-8")


def test_incomplete_wii_layout_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="openspm-llvm-layout-incomplete-") as temp_name:
        output = Path(temp_name) / "dolrecomp-output"
        title_dir = output / "R8PE01_generated"
        (title_dir / "chunks").mkdir(parents=True)
        (title_dir / "generated.h").write_text("STALE HEADER\n", encoding="utf-8")
        (title_dir / "generated.c").write_text("// stale manifest\n", encoding="utf-8")
        (title_dir / "R8PE01.h").write_text("CURRENT HEADER\n", encoding="utf-8")

        try:
            generator.locate_generated(output, "R8PE01")
        except RuntimeError as exc:
            assert "Wii title-aware output is incomplete" in str(exc)
            assert "R8PE01.c" in str(exc)
        else:
            raise AssertionError("incomplete Wii output unexpectedly fell back to stale aliases")


def main() -> int:
    test_wii_manifest_replaces_stale_titleless_aliases()
    test_incomplete_wii_layout_fails_closed()
    print("PASS: current Wii/title-aware DolRecomp manifests replace stale titleless aliases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
