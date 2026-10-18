#!/usr/bin/env python3
"""Retire the failed native GXBeginDisplayList/GXEndDisplayList experiment.

The local-module bridge v18 experiment promoted those two SDK functions back to
player-local native execution and injected post-call recorder hooks. The first
Windows runtime test proved that GXBeginDisplayList enters Wii FIFO/device logic
that OpenSPM does not yet model faithfully: after save discovery the guest spins
at 0x8026BAD8, write-gather traffic stops, and retrace delivery falls to zero
before the first post-Begin hook can run.

Do not require the player to delete .openspm. This migration strips only the
exact hook-bearing call blocks previously emitted by display_list_native_calls.py
back to the ordinary native-direct-call shape. direct_calls.py can then re-host
0x80291DC8/0x80291E78 from the restored host-target manifest. The pass is
idempotent and refuses partial/unknown hook remnants.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

BEGIN_TARGET = "80291DC8"
END_TARGET = "80291E78"
BEGIN_HOOK = "openspm_m14_display_list_begin_after_native"
END_HOOK = "openspm_m14_display_list_end_after_native"

CALL_COMMENT_RE = re.compile(
    r"^(?P<indent>[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>80291DC8|80291E78)[ \t]*$",
    re.IGNORECASE,
)
FUNC_DECL_RE = re.compile(r"void func_(?P<func>[0-9A-Fa-f]{8})\(CPUState\* ctx\);")
LR_RE = re.compile(r"ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;")


def _native_block(indent: str, pc: str, target: str, func: str, ret: str) -> list[str]:
    return [
        f"{indent}// {pc}: bl      0x{target}\n",
        f"{indent}{{\n",
        f"{indent}    void func_{func}(CPUState* ctx);\n",
        f"{indent}    ctx->lr = 0x{ret}u;\n",
        f"{indent}    ctx->pc = 0x{target}u;\n",
        f"{indent}    if (dolrecomp_call_enter()) {{\n",
        f"{indent}        func_{func}(ctx);\n",
        f"{indent}        dolrecomp_call_leave();\n",
        f"{indent}        if (ctx->pc == 0x{ret}u) goto label_{ret};\n",
        f"{indent}    }}\n",
        f"{indent}    return;\n",
        f"{indent}}}\n",
    ]


def retire_text(text: str) -> tuple[str, int, int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    begin_removed = 0
    end_removed = 0

    while i < len(lines):
        match = CALL_COMMENT_RE.match(lines[i].rstrip("\r\n"))
        if match is None or i + 1 >= len(lines) or lines[i + 1].strip() != "{":
            out.append(lines[i])
            i += 1
            continue

        depth = 0
        j = i + 1
        while j < len(lines):
            depth += lines[j].count("{")
            depth -= lines[j].count("}")
            if depth == 0:
                break
            j += 1
        if j >= len(lines):
            raise RuntimeError(
                f"unterminated generated call block after {match.group('pc')}"
            )

        block = "".join(lines[i : j + 1])
        target = match.group("target").upper()
        expected_hook = BEGIN_HOOK if target == BEGIN_TARGET else END_HOOK
        other_hook = END_HOOK if target == BEGIN_TARGET else BEGIN_HOOK

        if expected_hook not in block:
            if other_hook in block:
                raise RuntimeError(
                    f"mismatched GX display-list hook at callsite {match.group('pc')}"
                )
            out.extend(lines[i : j + 1])
            i = j + 1
            continue

        func_match = FUNC_DECL_RE.search(block)
        ret_match = LR_RE.search(block)
        if func_match is None or ret_match is None:
            raise RuntimeError(
                f"cannot retire malformed native GX display-list hook at {match.group('pc')}"
            )

        out.extend(
            _native_block(
                match.group("indent"),
                match.group("pc").upper(),
                target,
                func_match.group("func").upper(),
                ret_match.group("ret").upper(),
            )
        )
        if target == BEGIN_TARGET:
            begin_removed += 1
        else:
            end_removed += 1
        i = j + 1

    rewritten = "".join(out)
    if BEGIN_HOOK in rewritten or END_HOOK in rewritten:
        raise RuntimeError("native GX display-list hook remnant survived retirement")
    return rewritten, begin_removed, end_removed


def retire_tree(root: pathlib.Path, check_only: bool = False) -> dict[str, int]:
    chunks = sorted((root / "chunks").glob("chunk_*.c"))
    if not chunks:
        raise RuntimeError(f"no generated chunks under {root}")

    begin_removed = 0
    end_removed = 0
    changed_files = 0
    for path in chunks:
        text = path.read_text(encoding="utf-8")
        rewritten, begin, end = retire_text(text)
        begin_removed += begin
        end_removed += end
        if rewritten != text:
            changed_files += 1
            if not check_only:
                path.write_text(rewritten, encoding="utf-8", newline="\n")

    return {
        "begin_hooks_removed": begin_removed,
        "end_hooks_removed": end_removed,
        "changed_files": changed_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        stats = retire_tree(args.generated.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"OpenSPM native GX display-list rollback failed: {exc}", file=sys.stderr)
        return 1
    print(
        "retired native GX display-list hooks "
        + " ".join(f"{key}={value}" for key, value in stats.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
