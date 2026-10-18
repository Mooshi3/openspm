#!/usr/bin/env python3
"""Guard generated guest labels against invalid Wii-body execution.

Most migration semantics are OpenSPM host side exits. Runtime proof showed that
relying on caller formatting is not strong enough: a hosted target can still be
reached through a same-chunk goto, a different direct-call lowering, or a
top-level dispatch. Those targets are therefore guarded at the generated guest
label itself before any Wii-body instruction executes.

Host-target promotion is reversible. A cached player-local generated tree may
still contain one of OpenSPM's exact first-statement host guards after a target
has been removed from the current manifest. Before applying today's semantic
set, retire only an exact guard whose embedded target matches the immediately
preceding guest label and is no longer hosted. This lets functions such as
GXBeginDisplayList/GXEndDisplayList move back to guest-native execution without
requiring the player to regenerate or delete .openspm.

A very small class of helpers is better represented directly in generated CPU
state instead of paying a backend hostcall. In particular R8PE01's four-
instruction WPAR helper at 0x8028986C is:

    sync; mfspr r3,WPAR; andi. r3,r3,1; blr

Broadway WPAR bit 0 is BNE (Buffer Not Empty). OpenSPM has no hidden Broadway
write-gather buffer after a native FIFO write returns, so the architecturally
correct raw WPAR result is zero. The final ``andi.`` is observable too: it
writes CR0 from the zero result and XER[SO]. Reproduce r3=0 and CR0[EQ]=1,
mirror CR0[SO], then return through LR. This removes the hot physical polling
helper from the temporary 4096-hostcall-per-slice bridge budget without
inventing WPAR hardware state.

The first post-WPAR run stalled at PC=LR=0x802871E4. Exact bytes from the
player's R8PE01 revision-2 main.dol show that this is not __AXServiceVPB at all:
0x8028711C begins __AXOutInitDSP, 0x802871E0 calls DSPAddTask, and
0x802871E4..0x802871EC busy-waits on __AXDSPInitFlag at r13+0x8F90. The tiny
callback at 0x802870A8 sets that flag to one, but it is reached only after Wii
DSP microcode boots and invokes the task init callback. OpenSPM intentionally
has no Wii DSP execution. During first-frame bring-up, __AXOutInitDSP is
therefore a void native-audio semantic: return through LR before constructing
or booting a Wii DSP task. Surrounding AX/NW4R initialization continues and can
expose the next exact audio/platform boundary.

The ac595 runtime escaped that bootstrap wait and immediately reached the real
SDK AI DMA path. Exact revision-2 DOL bytes identify AIInitDMA at 0x80284FE8 and
AIStartDMA at 0x80285064; the observed body accesses 0xCC005030/32/36 to program
Wii audio DMA. OpenSPM does not yet own a PC mixer/output stream, so for the
first-visible-frame milestone these two observed void transport functions are
silent inline returns. Do not synthesize DSP/AI interrupts or pretend samples
were played. AIStopDMA at 0x80285078 remains on the ordinary path until runtime
actually observes it.

The dev21 run reached GXDrawDone and then froze at the BP DRAW_DONE FIFO store.
Exact revision-2 bytes show GXDrawDone emits 0x61/0x45000002, flushes, clears its
DrawDone byte, restores interrupts, disables them again, then begins the wait at
0x8028D8A8:

    while (!DrawDone) OSSleepThread(&FinishQueue);

On Wii the PE finish interrupt sets DrawDone and wakes that queue. The native
renderer already consumes the complete DRAW_DONE marker as a submit/present
boundary, and renderer dev4 no longer forces a generic GPU-idle fence inside
the FIFO store. There is therefore no reason to recreate a Wii PE interrupt
solely to wake this wrapper. At 0x8028D8A8, skip directly to 0x8028D8BC, where
the original function restores the second interrupt-disable token from r30 and
runs its untouched epilogue. This preserves Nintendo's own stack/register
cleanup while removing only the obsolete hardware wait.

An earlier experiment silenced __AXSyncPBs at 0x802874F8. Runtime proved that
boundary is downstream of the actual bootstrap wait and was never reached.
Cached generated trees may still contain that injected one-line return, so this
guard removes that exact deprecated line before applying the current semantic
set. Ordinary __AXSyncPBs code is restored rather than silently carrying a
speculative bypass forward.

Ordinary game-to-game labels are untouched and pay no runtime cost. Unknown
hardware/addresses remain on the ordinary generated path and fail closed at the
existing service boundaries.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import direct_calls

LABEL_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)label_(?P<target>[0-9A-Fa-f]{8}):[ \t]*$")
GUARD_RE = re.compile(
    r"^if \(ctx->host_call != 0 && ctx->host_call\(ctx, 0x([0-9A-Fa-f]{8})u\)\) return;$"
)

R8PE01_AI_INIT_DMA = 0x80284FE8
R8PE01_AI_START_DMA = 0x80285064
R8PE01_AX_OUT_INIT_DSP = 0x8028711C
R8PE01_AX_SYNC_PBS = 0x802874F8
R8PE01_GX_DRAW_DONE_WAIT = 0x8028D8A8
R8PE01_GX_WRITE_GATHER_EMPTY = 0x8028986C
INLINE_SILENT_AUDIO_TARGETS = {
    R8PE01_AI_INIT_DMA,
    R8PE01_AI_START_DMA,
    R8PE01_AX_OUT_INIT_DSP,
}
INLINE_SEMANTIC_TARGETS = INLINE_SILENT_AUDIO_TARGETS | {
    R8PE01_GX_DRAW_DONE_WAIT,
    R8PE01_GX_WRITE_GATHER_EMPTY,
}
INLINE_SILENT_AUDIO_VOID = "ctx->pc = ctx->lr; return;"
INLINE_GX_DRAW_DONE_WAIT = "ctx->pc = 0x8028D8BCu; return;"
INLINE_WG_EMPTY = (
    "ctx->gpr[3] = 0u; "
    "ctx->cr = (ctx->cr & 0x0FFFFFFFu) | 0x20000000u | "
    "((ctx->xer & 0x80000000u) ? 0x10000000u : 0u); "
    "ctx->pc = ctx->lr; return;"
)
DEPRECATED_INLINE_SEMANTICS = {
    R8PE01_AX_SYNC_PBS: "ctx->pc = ctx->lr; return;",
}


def _line_body(line: str) -> str:
    return line.rstrip("\r\n")


def _newline(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"


def _guard_line(indent: str, target: int, newline: str) -> str:
    return (
        f"{indent}    if (ctx->host_call != 0 && "
        f"ctx->host_call(ctx, 0x{target:08X}u)) return;{newline}"
    )


def _inline_semantic(target: int) -> str:
    if target == R8PE01_GX_DRAW_DONE_WAIT:
        return INLINE_GX_DRAW_DONE_WAIT
    if target == R8PE01_GX_WRITE_GATHER_EMPTY:
        return INLINE_WG_EMPTY
    if target in INLINE_SILENT_AUDIO_TARGETS:
        return INLINE_SILENT_AUDIO_VOID
    raise RuntimeError(f"no inline semantic for 0x{target:08X}")


def _inline_line(indent: str, target: int, newline: str) -> str:
    return f"{indent}    {_inline_semantic(target)}{newline}"


def _line_is_expected_semantic(body: str, target: int, hosted: set[int]) -> bool:
    stripped = body.strip()
    if target in INLINE_SEMANTIC_TARGETS:
        return stripped == _inline_semantic(target)
    if target in hosted:
        guard_match = GUARD_RE.match(stripped)
        return bool(guard_match and int(guard_match.group(1), 16) == target)
    return False


def _strip_retired_host_guards(text: str, hosted: set[int]) -> tuple[str, int]:
    """Remove exact OpenSPM label guards for targets no longer hosted.

    Only the first statement immediately after a guest label is eligible and the
    guard's embedded address must equal that label. This cannot remove an
    arbitrary host_call elsewhere in original generated code.
    """
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        match = LABEL_LINE_RE.match(_line_body(line))
        if match and index + 1 < len(lines):
            target = int(match.group("target"), 16)
            next_body = _line_body(lines[index + 1]).strip()
            guard_match = GUARD_RE.match(next_body)
            if (
                guard_match is not None
                and int(guard_match.group(1), 16) == target
                and target not in hosted
                and target not in INLINE_SEMANTIC_TARGETS
            ):
                removed += 1
                index += 2
                continue
        index += 1
    return "".join(output), removed


def _strip_deprecated_inline_semantics(text: str) -> tuple[str, int]:
    """Remove exact one-line semantics that an older migration inserted.

    Only the first statement immediately following the exact deprecated guest
    label is eligible. This keeps cleanup bounded and cannot erase an unrelated
    return elsewhere in the original generated function body.
    """
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        match = LABEL_LINE_RE.match(_line_body(line))
        if match and index + 1 < len(lines):
            target = int(match.group("target"), 16)
            deprecated = DEPRECATED_INLINE_SEMANTICS.get(target)
            if deprecated is not None and _line_body(lines[index + 1]).strip() == deprecated:
                removed += 1
                index += 2
                continue
        index += 1
    return "".join(output), removed


def _guard_text(
    text: str, hosted: set[int]
) -> tuple[str, int, int, dict[int, int], int]:
    """Insert first-statement semantics at migration labels.

    Return rewritten text, newly guarded count, already guarded count, a map of
    semantic labels observed in this generated file, and inline-label count.
    """
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    changed = 0
    already_guarded = 0
    semantic_labels: dict[int, int] = {}
    inline_labels = 0
    guarded_targets = hosted | INLINE_SEMANTIC_TARGETS

    for index, line in enumerate(lines):
        output.append(line)
        match = LABEL_LINE_RE.match(_line_body(line))
        if not match:
            continue

        target = int(match.group("target"), 16)
        if target not in guarded_targets:
            continue
        semantic_labels[target] = semantic_labels.get(target, 0) + 1
        if target in INLINE_SEMANTIC_TARGETS:
            inline_labels += 1

        next_body = ""
        if index + 1 < len(lines):
            next_body = _line_body(lines[index + 1])
        if _line_is_expected_semantic(next_body, target, hosted):
            already_guarded += 1
            continue

        if target in INLINE_SEMANTIC_TARGETS:
            output.append(_inline_line(match.group("indent"), target, _newline(line)))
        else:
            output.append(_guard_line(match.group("indent"), target, _newline(line)))
        changed += 1

    return "".join(output), changed, already_guarded, semantic_labels, inline_labels


def _unguarded_semantic_labels(text: str, hosted: set[int]) -> list[int]:
    """Return migration labels whose first statement is not the expected semantic."""
    lines = text.splitlines(keepends=False)
    unsafe: list[int] = []
    guarded_targets = hosted | INLINE_SEMANTIC_TARGETS
    for index, line in enumerate(lines):
        match = LABEL_LINE_RE.match(line)
        if not match:
            continue
        target = int(match.group("target"), 16)
        if target not in guarded_targets:
            continue
        if index + 1 >= len(lines) or not _line_is_expected_semantic(
            lines[index + 1], target, hosted
        ):
            unsafe.append(target)
    return unsafe


def guard_tree(
    generated: Path,
    host_targets: Iterable[int] | None = None,
    require_targets: Iterable[int] = (),
) -> dict[str, int]:
    root = generated.resolve()
    if not (root / "generated.h").is_file() or not (root / "chunks").is_dir():
        raise RuntimeError(f"not a generated source tree: {root}")

    if host_targets is None:
        hosted = direct_calls.load_host_targets()
    else:
        hosted = {int(value) & 0xFFFFFFFF for value in host_targets}
    required = {int(value) & 0xFFFFFFFF for value in require_targets}
    guarded_targets = hosted | INLINE_SEMANTIC_TARGETS

    changed_files = 0
    guarded_entries = 0
    already_guarded = 0
    labels_found: dict[int, int] = {}
    inline_labels = 0
    retired_host_guards_removed = 0
    deprecated_inline_removed = 0

    for path in sorted((root / "chunks").glob("chunk_*.c")):
        original = path.read_text(encoding="utf-8")
        retired, retired_count = _strip_retired_host_guards(original, hosted)
        cleaned, removed = _strip_deprecated_inline_semantics(retired)
        rewritten, changed, existing, found, inline_found = _guard_text(cleaned, hosted)
        for target, count in found.items():
            labels_found[target] = labels_found.get(target, 0) + count
        guarded_entries += changed
        already_guarded += existing
        inline_labels += inline_found
        retired_host_guards_removed += retired_count
        deprecated_inline_removed += removed
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8", newline="\n")
            changed_files += 1

    missing = sorted(target for target in required if labels_found.get(target, 0) == 0)
    if missing:
        raise RuntimeError(
            "required semantic guest label not found in staged generated source: "
            + ",".join(f"0x{target:08X}" for target in missing)
        )

    duplicates = sorted(target for target, count in labels_found.items() if count != 1)
    if duplicates:
        raise RuntimeError(
            "semantic guest label appears multiple times in staged generated source: "
            + ",".join(f"0x{target:08X}" for target in duplicates)
        )

    unsafe: list[tuple[Path, int]] = []
    for path in sorted((root / "chunks").glob("chunk_*.c")):
        text = path.read_text(encoding="utf-8")
        for target in _unguarded_semantic_labels(text, hosted):
            unsafe.append((path, target))
    if unsafe:
        preview = ",".join(
            f"{path.name}:0x{target:08X}" for path, target in unsafe[:16]
        )
        raise RuntimeError("unguarded semantic guest label remains: " + preview)

    return {
        "host_targets": len(hosted),
        "inline_targets": len(INLINE_SEMANTIC_TARGETS),
        "semantic_targets": len(guarded_targets),
        "changed_files": changed_files,
        "guarded_entries": guarded_entries,
        "already_guarded": already_guarded,
        "retired_host_guards_removed": retired_host_guards_removed,
        "deprecated_inline_removed": deprecated_inline_removed,
        "hosted_labels": sum(labels_found.values()) - inline_labels,
        "inline_labels": inline_labels,
        "semantic_labels": sum(labels_found.values()),
    }
