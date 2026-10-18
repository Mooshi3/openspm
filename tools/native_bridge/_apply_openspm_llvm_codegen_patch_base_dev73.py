#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
'''M14 dev73: retain dev72 and defer one proven-bounded wedding effect budget edge.

The dev70/dev72 logs prove the deterministic wedding stall occurs only when the
x86 cold-escape scheduler repeatedly materializes target 0x80065128 with
LR=0x80064E34. The effect table count at that point is 0x4C (76), so this guest
work is bounded. Preserve the entire prior compiler patch stack and prevent the
direct u32 budget guard from unwinding exactly at that target; the guest call and
all guest instructions still execute normally, and later ordinary guards remain
eligible to yield.
'''
from __future__ import annotations

from pathlib import Path

import _apply_openspm_llvm_codegen_patch_base_dev72 as _legacy

COLD_ENTRY_SIGNATURE = _legacy.COLD_ENTRY_SIGNATURE

_RETAINED_SOURCE_CONTRACTS = (
    "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER",
    '"openspm_m14_llvm_wg_bytes_ptr"',
    '"openspm_m14_flush_wg_llvm"',
    "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);",
    "materialize(current_pc_);",
)

TIMING_TARGET = Path("src") / "backend" / "llvm" / "timing.cpp"
BUDGET_DEFER_SIGNATURE = "OPENSPM_M14_LLVM_EFF_SUB_BUDGET_DEFER"
EFF_SUB_TARGET = 0x80065128

_GUARD_OLD = '''void FunctionEmitter::emitBudgetGuard(u32 pc) {
  Value *cycles = builder_.CreateAdd(
'''

_GUARD_NEW = '''void FunctionEmitter::emitBudgetGuard(u32 pc) {
  /* OPENSPM_M14_LLVM_EFF_SUB_BUDGET_DEFER
   * dev70/dev72 prove 0x80065128 is the deterministic wedding soft-yield
   * frontier and its authoritative effect count is only 76. Yielding before
   * this direct call unwinds the x86 native caller and repeatedly loses its
   * loop-local architectural state. Let this one bounded call proceed and
   * retain all later ordinary budget guards unchanged. */
  if (pc == 0x80065128u)
    return;
  Value *cycles = builder_.CreateAdd(
'''


def _verify_legacy_source() -> None:
    text = Path(_legacy.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in _RETAINED_SOURCE_CONTRACTS if marker not in text]
    if missing:
        raise RuntimeError(
            "dev73 retained DolRecomp base patch lost required source contracts: "
            + ", ".join(missing)
        )


def _verify_budget_defer_shape(text: str) -> None:
    required = (
        BUDGET_DEFER_SIGNATURE,
        "void FunctionEmitter::emitBudgetGuard(u32 pc)",
        "if (pc == 0x80065128u)",
        "return;",
        "Value *cycles = builder_.CreateAdd(",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev73 eff_sub budget-defer patch: " + ", ".join(missing)
        )


def _patch_budget_defer(source_root: Path, check_only: bool) -> str:
    path = source_root / TIMING_TARGET
    text = path.read_text(encoding="utf-8")
    if BUDGET_DEFER_SIGNATURE in text:
        _verify_budget_defer_shape(text)
        return "already-applied"
    if text.count(_GUARD_OLD) != 1:
        raise RuntimeError(
            f"{TIMING_TARGET}: expected direct budget-guard anchor exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"
    patched = text.replace(_GUARD_OLD, _GUARD_NEW, 1)
    _verify_budget_defer_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    source_root = source_root.resolve()
    _verify_legacy_source()
    legacy_result = _legacy.transform(source_root, check_only)
    defer_result = _patch_budget_defer(source_root, check_only)
    return f"{legacy_result} eff-sub-budget-defer={defer_result}"
