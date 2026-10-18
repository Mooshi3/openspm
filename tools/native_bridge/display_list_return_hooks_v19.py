#!/usr/bin/env python3
"""Prepare continuation-safe native GX display-list call hooks for R8PE01.

Local-module bridge v18 placed its physical-recorder callback in the temporary
C wrapper immediately after a recompiled GXBeginDisplayList/GXEndDisplayList
call. That location is not durable: a native guest call can yield when its
DolRecomp downcount expires, unwind the host C stack, and later resume only from
the guest PC. When the SDK eventually returns to LR, execution resumes at the
caller's guest return label and the old post-call C wrapper no longer exists.

This dormant v19 transform moves the post-return seam to that guest continuation
label. A small pre-call hook arms persistent bridge state; a matching label hook
runs after the native SDK function returns whether it completed synchronously or
across any number of native yields. The bridge can therefore keep Nintendo's
CPU-side GXData dirty/save/restore behavior while OpenSPM's neutral recorder owns
only the physical display-list payload/count transport.

The exact R8PE01 revision-2 player source has three calls to each SDK function.
The transform requires exactly those counts and refuses any return continuation
that is also targeted by a guest branch. It operates only on player-local
DolRecomp output under .openspm; no generated game source is committed here.

This file is intentionally NOT in the active normalizer/CMake path yet. The
runtime-proven v17 hosted boundary remains active until the isolated texture-copy
candidate is tested and v19 is explicitly promoted in a later change.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

BEGIN_TARGET = "80291DC8"
END_TARGET = "80291E78"
EXPECTED_BEGIN_CALLS = 3
EXPECTED_END_CALLS = 3

BEGIN_PRE = "openspm_m14_display_list_begin_before_native_v19"
BEGIN_AFTER = "openspm_m14_display_list_begin_after_return_v19"
END_PRE = "openspm_m14_display_list_end_before_native_v19"
END_AFTER = "openspm_m14_display_list_end_after_return_v19"

FUNC_RE = re.compile(r"chunk_\d+_[^_]+_([0-9A-Fa-f]{8})\.c$")
CALLSITE_RE = re.compile(
    r"^[ \t]*// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x"
    r"(?P<target>80291DC8|80291E78)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

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


def _native_block(indent: str, pc: str, target: str, function: str, ret: str) -> str:
    return (
        f"{indent}// {pc}: bl      0x{target}\n"
        f"{indent}{{\n"
        f"{indent}    void func_{function}(CPUState* ctx);\n"
        f"{indent}    ctx->lr = 0x{ret}u;\n"
        f"{indent}    ctx->pc = 0x{target}u;\n"
        f"{indent}    if (dolrecomp_call_enter()) {{\n"
        f"{indent}        func_{function}(ctx);\n"
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


def _arm_call(match: re.Match[str]) -> str:
    target = match.group("target").upper()
    if target not in {BEGIN_TARGET, END_TARGET}:
        return match.group(0)

    indent = match.group("indent")
    inner = indent + "    "
    deeper = inner + "    "
    pc = match.group("pc").upper()
    function = match.group("func").upper()
    ret = match.group("ret").upper()

    if target == BEGIN_TARGET:
        prelude = (
            f"{inner}int {BEGIN_PRE}(CPUState* ctx, u32 buffer, u32 capacity);\n"
            f"{inner}const u32 openspm_dl_buffer = ctx->gpr[3];\n"
            f"{inner}const u32 openspm_dl_capacity = ctx->gpr[4];\n"
            f"{inner}if (!{BEGIN_PRE}(ctx, openspm_dl_buffer, openspm_dl_capacity)) return;\n"
        )
    else:
        prelude = (
            f"{inner}int {END_PRE}(CPUState* ctx);\n"
            f"{inner}if (!{END_PRE}(ctx)) return;\n"
        )

    return (
        f"{indent}// {pc}: bl      0x{target}\n"
        f"{indent}{{\n"
        f"{inner}void func_{function}(CPUState* ctx);\n"
        f"{prelude}"
        f"{inner}ctx->lr = 0x{ret}u;\n"
        f"{inner}ctx->pc = 0x{target}u;\n"
        f"{inner}if (dolrecomp_call_enter()) {{\n"
        f"{deeper}func_{function}(ctx);\n"
        f"{deeper}dolrecomp_call_leave();\n"
        f"{deeper}if (ctx->pc == 0x{ret}u) goto label_{ret};\n"
        f"{inner}}}\n"
        f"{inner}return;\n"
        f"{indent}}}"
    )


def _guest_branch_refs(text: str, ret: str) -> int:
    pattern = re.compile(
        rf"^[ \t]*// [0-9A-Fa-f]{{8}}:[ \t]+b[^ \t]*[ \t]+[^\n]*0x{ret}(?:[ \t]|$)",
        re.MULTILINE | re.IGNORECASE,
    )
    return len(pattern.findall(text))


def _inject_return_label(text: str, ret: str, hook: str) -> str:
    label = f"label_{ret}:\n"
    if text.count(label) != 1:
        raise RuntimeError(
            f"return label {ret}: expected exactly one generated label, found {text.count(label)}"
        )
    block = (
        label
        + "    {\n"
        + f"        int {hook}(CPUState* ctx);\n"
        + f"        if (!{hook}(ctx)) return;\n"
        + "    }\n"
    )
    return text.replace(label, block, 1)


def _hook_counts(texts: dict[pathlib.Path, str]) -> tuple[int, int, int, int]:
    joined = "\n".join(texts.values())
    return (
        joined.count(f"if (!{BEGIN_PRE}("),
        joined.count(f"if (!{BEGIN_AFTER}("),
        joined.count(f"if (!{END_PRE}("),
        joined.count(f"if (!{END_AFTER}("),
    )


def transform_tree(root: pathlib.Path, check_only: bool = False) -> tuple[int, int, int]:
    chunks = sorted((root / "chunks").glob("chunk_*.c"))
    if not chunks:
        raise RuntimeError(f"no generated chunks under {root}")

    texts = {path: path.read_text(encoding="utf-8") for path in chunks}
    calls: list[tuple[pathlib.Path, str, str]] = []
    for path, text in texts.items():
        for match in CALLSITE_RE.finditer(text):
            calls.append((path, match.group("pc").upper(), match.group("target").upper()))

    begin_calls = sum(target == BEGIN_TARGET for _, _, target in calls)
    end_calls = sum(target == END_TARGET for _, _, target in calls)
    if begin_calls != EXPECTED_BEGIN_CALLS or end_calls != EXPECTED_END_CALLS:
        raise RuntimeError(
            "unexpected R8PE01 GX display-list callsite count: "
            f"begin={begin_calls} end={end_calls}; expected "
            f"{EXPECTED_BEGIN_CALLS}/{EXPECTED_END_CALLS}"
        )

    expected_counts = (begin_calls, begin_calls, end_calls, end_calls)
    current_counts = _hook_counts(texts)
    if current_counts == expected_counts:
        return begin_calls, end_calls, 0
    if any(current_counts):
        raise RuntimeError(
            f"partial v19 return-hook state {current_counts}; expected {expected_counts}"
        )

    joined_original = "\n".join(texts.values())
    for _, pc, _ in calls:
        ret = f"{int(pc, 16) + 4:08X}"
        refs = _guest_branch_refs(joined_original, ret)
        if refs != 0:
            raise RuntimeError(
                f"return continuation {ret} is also a guest branch target ({refs} refs)"
            )

    by_file: dict[pathlib.Path, list[tuple[str, str]]] = {}
    for path, pc, target in calls:
        by_file.setdefault(path, []).append((pc, target))

    changed = 0
    for path, items in by_file.items():
        text = texts[path]
        function_match = FUNC_RE.search(path.name)
        if function_match is None:
            raise RuntimeError(f"unexpected generated chunk filename: {path.name}")
        function = function_match.group(1).upper()

        text = SAME_CHUNK_BL_RE.sub(
            lambda match: _promote_same_chunk(match, function), text
        )
        text = NATIVE_DIRECT_RE.sub(_arm_call, text)

        for pc, target in items:
            ret = f"{int(pc, 16) + 4:08X}"
            hook = BEGIN_AFTER if target == BEGIN_TARGET else END_AFTER
            text = _inject_return_label(text, ret, hook)

        texts[path] = text
        changed += 1

    final_counts = _hook_counts(texts)
    if final_counts != expected_counts:
        raise RuntimeError(
            f"incomplete v19 return-hook transform {final_counts}; expected {expected_counts}"
        )

    if not check_only:
        for path in by_file:
            path.write_text(texts[path], encoding="utf-8", newline="\n")
    return begin_calls, end_calls, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.generated.resolve()
    try:
        begin, end, changed = transform_tree(root, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"OpenSPM v19 GX display-list return-hook adaptation failed: {exc}", file=sys.stderr)
        return 1
    action = "verified" if args.check_only else ("active" if changed else "already-active")
    print(
        "continuation-safe native GX display-list hooks "
        f"{action}: begin={begin} end={end} changed_files={changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
