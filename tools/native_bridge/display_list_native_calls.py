#!/usr/bin/env python3
"""Bracket native GX display-list SDK calls with OpenSPM's physical recorder.

Astra's gameplay GX audit identified that hosting the whole R8PE01
GXBeginDisplayList/GXEndDisplayList pair bypasses Nintendo's CPU-side GX shadow
semantics. The real RVL SDK behavior matters here: GXBeginDisplayList flushes
any pending dirty GX state to the *current live FIFO*, optionally saves GXData,
then switches the CPU FIFO to the caller-owned list buffer. GXEndDisplayList
flushes the write-gather pipe, snapshots list FIFO state, restores the previous
CPU FIFO, optionally restores the saved GXData, and clears inDispList.

The ordinary direct-call normalizer now leaves those two functions guest-native.
This second, player-local generated-source transform adds only the physical
recording seam:

* after native GXBeginDisplayList returns, start the neutral bounded recorder
  with the caller's original buffer/capacity; the SDK's pre-list dirty flush has
  already gone to the live renderer before this hook runs;
* let native GXEndDisplayList run while recording remains active, so its final
  GXFlush/write-gather bytes remain part of the list and its FIFO/context restore
  executes normally;
* after native GXEndDisplayList returns, close the neutral recorder and use its
  authoritative aligned/overflow result, since OpenSPM's transitional recorder
  does not advance the emulated PI FIFO write pointer used by GXGetFifoCount.

DolRecomp can lower a same-generated-chunk BL to a goto rather than a C call.
For just these two SDK boundaries, convert that lowering back into the same
bounded native-call form before inserting the hooks. This keeps a fresh generated
tree equivalent to an older cached tree that previously had these targets hosted.
The pass fails closed unless every BL to each target has exactly one hook.

No Nintendo code or data is embedded here. The transform operates only on the
player-local generated C tree inside .openspm and is exact/idempotent against
OpenSPM's own direct_calls.py output.
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
FUNC_RE = re.compile(r"chunk_\d+_[^_]+_([0-9A-Fa-f]{8})\.c$")

NATIVE_DIRECT_RE = re.compile(
    r"(?P<indent>^[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})\n"
    r"(?P=indent)\{\n"
    r"(?P=indent)[ \t]+void func_(?P<func>[0-9A-Fa-f]{8})\(CPUState\* ctx\);\n"
    r"(?P=indent)[ \t]+ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;\n"
    r"(?P=indent)[ \t]+ctx->pc = 0x(?P=target)u;\n"
    r"(?P=indent)[ \t]+if \(dolrecomp_call_enter\(\)\) \{\n"
    r"(?P=indent)[ \t]+func_(?P=func)\(ctx\);\n"
    r"(?P=indent)[ \t]+dolrecomp_call_leave\(\);\n"
    r"(?P=indent)[ \t]+if \(ctx->pc == 0x(?P=ret)u\) goto label_(?P=ret);\n"
    r"(?P=indent)[ \t]+\}\n"
    r"(?P=indent)[ \t]+return;\n"
    r"(?P=indent)\}",
    re.MULTILINE,
)

SAME_CHUNK_BL_RE = re.compile(
    r"(?P<indent>^[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})\n"
    r"(?P=indent)\{\n"
    r"(?P=indent)[ \t]+ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;\n"
    r"(?P=indent)[ \t]+if \(ctx->downcount <= -\(s64\)DOLRECOMP_C_LOOP_CYCLE_BUDGET\) \{\n"
    r"(?P=indent)[ \t]+ctx->pc = 0x(?P=target)u;\n"
    r"(?P=indent)[ \t]+return;\n"
    r"(?P=indent)[ \t]+\}\n"
    r"(?P=indent)[ \t]+goto label_(?P=target);\n"
    r"(?P=indent)\}",
    re.MULTILINE,
)

CALLSITE_RE = re.compile(
    r"^[ \t]*// [0-9A-Fa-f]{8}: bl[ \t]+0x(?P<target>80291DC8|80291E78)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _native_block(indent: str, pc: str, target: str, func: str, ret: str) -> str:
    return (
        f"{indent}// {pc}: bl      0x{target}\n"
        f"{indent}{{\n"
        f"{indent}    void func_{func}(CPUState* ctx);\n"
        f"{indent}    ctx->lr = 0x{ret}u;\n"
        f"{indent}    ctx->pc = 0x{target}u;\n"
        f"{indent}    if (dolrecomp_call_enter()) {{\n"
        f"{indent}        func_{func}(ctx);\n"
        f"{indent}        dolrecomp_call_leave();\n"
        f"{indent}        if (ctx->pc == 0x{ret}u) goto label_{ret};\n"
        f"{indent}    }}\n"
        f"{indent}    return;\n"
        f"{indent}}}"
    )


def _promote_same_chunk(match: re.Match[str], function: str) -> str:
    target = match.group("target").upper()
    if target not in {BEGIN_TARGET, END_TARGET}:
        return match.group(0)
    return _native_block(
        match.group("indent"),
        match.group("pc").upper(),
        target,
        function,
        match.group("ret").upper(),
    )


def hook_block(match: re.Match[str]) -> str:
    indent = match.group("indent")
    inner = indent + "    "
    deeper = inner + "    "
    deepest = deeper + "    "
    pc = match.group("pc").upper()
    target = match.group("target").upper()
    func = match.group("func").upper()
    ret = match.group("ret").upper()

    if target == BEGIN_TARGET:
        return (
            f"{indent}// {pc}: bl      0x{target}\n"
            f"{indent}{{\n"
            f"{inner}void func_{func}(CPUState* ctx);\n"
            f"{inner}void {BEGIN_HOOK}(CPUState* ctx, u32 buffer, u32 capacity);\n"
            f"{inner}const u32 openspm_dl_buffer = ctx->gpr[3];\n"
            f"{inner}const u32 openspm_dl_capacity = ctx->gpr[4];\n"
            f"{inner}ctx->lr = 0x{ret}u;\n"
            f"{inner}ctx->pc = 0x{target}u;\n"
            f"{inner}if (dolrecomp_call_enter()) {{\n"
            f"{deeper}func_{func}(ctx);\n"
            f"{deeper}dolrecomp_call_leave();\n"
            f"{deeper}if (ctx->pc == 0x{ret}u) {{\n"
            f"{deepest}{BEGIN_HOOK}(ctx, openspm_dl_buffer, openspm_dl_capacity);\n"
            f"{deepest}if (ctx->pc == 0x{ret}u) goto label_{ret};\n"
            f"{deeper}}}\n"
            f"{inner}}}\n"
            f"{inner}return;\n"
            f"{indent}}}"
        )

    if target == END_TARGET:
        return (
            f"{indent}// {pc}: bl      0x{target}\n"
            f"{indent}{{\n"
            f"{inner}void func_{func}(CPUState* ctx);\n"
            f"{inner}void {END_HOOK}(CPUState* ctx);\n"
            f"{inner}ctx->lr = 0x{ret}u;\n"
            f"{inner}ctx->pc = 0x{target}u;\n"
            f"{inner}if (dolrecomp_call_enter()) {{\n"
            f"{deeper}func_{func}(ctx);\n"
            f"{deeper}dolrecomp_call_leave();\n"
            f"{deeper}if (ctx->pc == 0x{ret}u) {{\n"
            f"{deepest}{END_HOOK}(ctx);\n"
            f"{deepest}if (ctx->pc == 0x{ret}u) goto label_{ret};\n"
            f"{deeper}}}\n"
            f"{inner}}}\n"
            f"{inner}return;\n"
            f"{indent}}}"
        )

    return match.group(0)


def transform_tree(root: pathlib.Path, check_only: bool = False) -> tuple[int, int, int]:
    chunks = sorted((root / "chunks").glob("chunk_*.c"))
    if not chunks:
        raise RuntimeError(f"no generated chunks under {root}")

    begin_hooks = 0
    end_hooks = 0
    begin_calls = 0
    end_calls = 0
    changed_files = 0

    for path in chunks:
        text = path.read_text(encoding="utf-8")
        function_match = FUNC_RE.search(path.name)
        if function_match is None:
            raise RuntimeError(f"unexpected generated chunk filename: {path.name}")
        function = function_match.group(1).upper()

        # A clean DolRecomp tree can use same-chunk goto lowering. Promote only
        # these two SDK calls back into the bounded native C-call form so there is
        # a concrete post-return point where the physical recorder can be toggled.
        rewritten = SAME_CHUNK_BL_RE.sub(
            lambda match: _promote_same_chunk(match, function), text
        )
        rewritten = NATIVE_DIRECT_RE.sub(hook_block, rewritten)

        begin_hooks += rewritten.count(
            f"{BEGIN_HOOK}(ctx, openspm_dl_buffer, openspm_dl_capacity);"
        )
        end_hooks += rewritten.count(f"{END_HOOK}(ctx);")
        for call_match in CALLSITE_RE.finditer(rewritten):
            if call_match.group("target").upper() == BEGIN_TARGET:
                begin_calls += 1
            else:
                end_calls += 1

        if rewritten != text:
            changed_files += 1
            if not check_only:
                path.write_text(rewritten, encoding="utf-8", newline="\n")

    if begin_calls == 0 or end_calls == 0:
        raise RuntimeError(
            "native GX display-list callsites were not both found after direct-call normalization: "
            f"begin_calls={begin_calls} end_calls={end_calls}"
        )
    if begin_hooks != begin_calls or end_hooks != end_calls:
        raise RuntimeError(
            "not every native GX display-list BL is bracketed by exactly one recorder hook: "
            f"begin_calls={begin_calls} begin_hooks={begin_hooks} "
            f"end_calls={end_calls} end_hooks={end_hooks}"
        )
    return begin_hooks, end_hooks, changed_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.generated.resolve()
    try:
        begin, end, changed = transform_tree(root, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"OpenSPM native GX display-list call adaptation failed: {exc}", file=sys.stderr)
        return 1
    action = "verified" if args.check_only else "active"
    print(
        f"native GX display-list SDK semantics {action}: "
        f"begin_hooks={begin} end_hooks={end} changed_files={changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
