#!/usr/bin/env python3
"""Report the player-local R8PE01 GX display-list SDK call graph.

The public OpenSPM tree does not contain Nintendo code. The user's own WBFS is
recompiled locally, so this configure-time diagnostic reads only DolRecomp's
player-local generated C and reports BL addresses/targets inside
GXBeginDisplayList/GXEndDisplayList. It never copies instruction bodies or data
into the repository.

DolRecomp chunks are address ranges, not one C function per guest SDK function.
These SDK entries therefore normally appear as internal ``label_<address>:``
continuations inside a larger ``func_<chunk-base>`` translation unit. Locate the
entry labels by content and stop at the routine's first guest ``blr`` rather than
assuming a chunk filename/function root exactly matches the SDK entry address.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

FUNCTIONS = {
    "GXBeginDisplayList": "80291DC8",
    "GXEndDisplayList": "80291E78",
}
BL_RE = re.compile(
    r"^[ \t]*//[ \t]+(?P<pc>[0-9A-Fa-f]{8}):[ \t]+bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})[ \t]*$",
    re.MULTILINE,
)
INSTRUCTION_RE = re.compile(
    r"^[ \t]*//[ \t]+(?P<pc>[0-9A-Fa-f]{8}):[ \t]+(?P<op>[^ \t\r\n]+)",
    re.MULTILINE,
)


def find_function_source(root: pathlib.Path, address: str) -> pathlib.Path:
    marker = f"label_{address}:"
    matches: list[pathlib.Path] = []
    for path in sorted((root / "chunks").glob("chunk_*.c")):
        try:
            if marker in path.read_text(encoding="utf-8"):
                matches.append(path)
        except OSError:
            continue
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one generated chunk containing entry label {address}, found {len(matches)}"
        )
    return matches[0]


def function_window(text: str, address: str) -> str:
    marker = f"label_{address}:"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"generated chunk is missing entry label {address}")

    first = INSTRUCTION_RE.search(text, start)
    if first is None or first.group("pc").upper() != address:
        found = "none" if first is None else first.group("pc").upper()
        raise RuntimeError(
            f"entry label {address} did not begin at its matching instruction comment (found {found})"
        )

    for match in INSTRUCTION_RE.finditer(text, first.start()):
        if match.group("op").lower() == "blr":
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            return text[start:line_end]
    raise RuntimeError(f"generated SDK routine {address} contained no terminating blr")


def inspect(root: pathlib.Path) -> list[str]:
    output: list[str] = []
    for name, address in FUNCTIONS.items():
        path = find_function_source(root, address)
        text = path.read_text(encoding="utf-8")
        window = function_window(text, address)
        calls = [
            (m.group("pc").upper(), m.group("target").upper())
            for m in BL_RE.finditer(window)
        ]
        if not calls:
            raise RuntimeError(f"{name} {address} contained no generated BL sites")
        joined = ", ".join(f"{pc}->0x{target}" for pc, target in calls)
        output.append(f"M14 player-local {name} call graph: {joined}")
    return output


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="openspm-dl-inspector-") as temp:
        root = pathlib.Path(temp)
        chunks = root / "chunks"
        chunks.mkdir()
        (chunks / "chunk_0001_text1_80291940.c").write_text(
            "void func_80291940(CPUState* ctx) {\n"
            "label_80291DC8:\n"
            "    // 80291DC8: stwu     r1, -32(r1)\n"
            "    // 80291DD8: bl      0x80253EA4\n"
            "    // 80291E00: bl      0x8028DC08\n"
            "    // 80291E74: blr\n"
            "label_80291E78:\n"
            "    // 80291E78: stwu     r1, -32(r1)\n"
            "    // 80291E88: bl      0x80253EA0\n"
            "    // 80291EA0: bl      0x8028BE8C\n"
            "    // 80291F20: blr\n"
            "label_80291F24:\n"
            "    // 80291F24: bl      0xDEADBEEF\n"
            "}\n",
            encoding="utf-8",
        )
        lines = inspect(root)
        assert lines == [
            "M14 player-local GXBeginDisplayList call graph: 80291DD8->0x80253EA4, 80291E00->0x8028DC08",
            "M14 player-local GXEndDisplayList call graph: 80291E88->0x80253EA0, 80291EA0->0x8028BE8C",
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated_dir", nargs="?", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        try:
            self_test()
        except (AssertionError, OSError, RuntimeError) as exc:
            print(
                f"error: native GX display-list SDK call-graph inspector self-test failed: {exc}",
                file=sys.stderr,
            )
            return 5
        print("native GX display-list SDK call-graph inspector self-test: PASS")
        return 0

    if args.generated_dir is None:
        parser.error("generated_dir is required unless --self-test is used")
    root = args.generated_dir.resolve()
    if not (root / "chunks").is_dir():
        print(f"error: missing generated chunks: {root}", file=sys.stderr)
        return 3

    try:
        for line in inspect(root):
            print(line)
    except (OSError, RuntimeError) as exc:
        print(f"error: native GX display-list SDK call-graph inspection failed: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
