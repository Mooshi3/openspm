#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
'''M14 dev72: retain the proven OpenSPM DolRecomp patch stack and repair
native-ABI cold-escape state materialization on Windows/x86.

Dev70 proved the wedding eff_sub loop resumes at 0x80065128 with sane mapped
effect state while r21-r24 remain at their pre-increment values across repeated
soft-budget yields. On the legacy recompcore runtime, x86 uses the native ABI's
setjmp/longjmp cold-escape path. Preserve the entire prior patch transform, but
make syncDirtyState() commit every state slot the native range actually uses
before a cold escape instead of trusting only the ABI dirty/liveness subset.
This adds stores only at materialization boundaries; the hot native loop is
unchanged.
'''
from __future__ import annotations

from pathlib import Path

import _apply_openspm_llvm_codegen_patch_base_dev71 as _legacy

# Re-export the one retained-base symbol consumed by the composing front-end.
COLD_ENTRY_SIGNATURE = _legacy.COLD_ENTRY_SIGNATURE

# The composing front-end reads this module's source to prove these historical
# contracts still exist. Verify the byte-preserved legacy module really owns
# them rather than carrying inert compatibility comments here.
_RETAINED_SOURCE_CONTRACTS = (
    "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER",
    '"openspm_m14_llvm_wg_bytes_ptr"',
    '"openspm_m14_flush_wg_llvm"',
    "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);",
    "materialize(current_pc_);",
)

EXITS_TARGET = Path("src") / "backend" / "llvm" / "exits.cpp"
COLD_ESCAPE_SIGNATURE = "OPENSPM_M14_LLVM_COLD_ESCAPE_FULL_STATE"

_SYNC_OLD = '''  for (u32 slot = 0; slot < DOLIR_STATE_COUNT; slot++) {
    if (!dirty_[slot] || slot == DOLIR_STATE_FPSCR)
      continue;
    auto stateSlot = static_cast<DolIRStateSlot>(slot);
    if (slotInMemory(stateSlot))
      continue;
    storeContext(
        stateSlot,
        builder_.CreateLoad(type(dolir_state_type(stateSlot)), state_[slot]));
  }
  materializeFPRF();
  if (dirty_[DOLIR_STATE_FPSCR])
    storeContext(DOLIR_STATE_FPSCR, stateValue(DOLIR_STATE_FPSCR));
'''

_SYNC_NEW = '''  /* OPENSPM_M14_LLVM_COLD_ESCAPE_FULL_STATE
   * On legacy x86 native-ABI builds, a soft yield unwinds nested native calls
   * with longjmp. There is no caller-side acceptNativeResult() pass after that
   * unwind, so CPUState must be a complete restart image at the materialization
   * boundary. dev70 observed r21-r24 staying stale at 0x80065128 even though
   * the immediately preceding guest instructions modified them. Commit every
   * state slot this native range uses on cold escape; keep the ordinary dirty
   * subset for all other paths. This executes only when syncDirtyState() runs,
   * not in the hot native loop. */
  const bool openspmFullColdEscapeState = native_abi_ && cold_escapes_;
  for (u32 slot = 0; slot < DOLIR_STATE_COUNT; slot++) {
    const bool openspmCommit =
        dirty_[slot] || (openspmFullColdEscapeState && used_[slot]);
    if (!openspmCommit || slot == DOLIR_STATE_FPSCR)
      continue;
    auto stateSlot = static_cast<DolIRStateSlot>(slot);
    if (slotInMemory(stateSlot))
      continue;
    storeContext(
        stateSlot,
        builder_.CreateLoad(type(dolir_state_type(stateSlot)), state_[slot]));
  }
  materializeFPRF();
  if (dirty_[DOLIR_STATE_FPSCR] ||
      (openspmFullColdEscapeState && used_[DOLIR_STATE_FPSCR]))
    storeContext(DOLIR_STATE_FPSCR, stateValue(DOLIR_STATE_FPSCR));
'''


def _verify_legacy_source() -> None:
    text = Path(_legacy.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in _RETAINED_SOURCE_CONTRACTS if marker not in text]
    if missing:
        raise RuntimeError(
            "dev72 retained DolRecomp base patch lost required source contracts: "
            + ", ".join(missing)
        )


def _verify_cold_escape_shape(text: str) -> None:
    required = (
        COLD_ESCAPE_SIGNATURE,
        "native_abi_ && cold_escapes_",
        "openspmFullColdEscapeState && used_[slot]",
        "openspmFullColdEscapeState && used_[DOLIR_STATE_FPSCR]",
        "storeContext(",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev72 cold-escape state patch: " + ", ".join(missing)
        )


def _patch_cold_escape_state(source_root: Path, check_only: bool) -> str:
    path = source_root / EXITS_TARGET
    text = path.read_text(encoding="utf-8")
    if COLD_ESCAPE_SIGNATURE in text:
        _verify_cold_escape_shape(text)
        return "already-applied"
    if text.count(_SYNC_OLD) != 1:
        raise RuntimeError(
            f"{EXITS_TARGET}: expected legacy syncDirtyState anchor exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"
    patched = text.replace(_SYNC_OLD, _SYNC_NEW, 1)
    _verify_cold_escape_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    source_root = source_root.resolve()
    _verify_legacy_source()
    legacy_result = _legacy.transform(source_root, check_only)
    cold_result = _patch_cold_escape_state(source_root, check_only)
    return f"{legacy_result} cold-escape-full-state={cold_result}"
