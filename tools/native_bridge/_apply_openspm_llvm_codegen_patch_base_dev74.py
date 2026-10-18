#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
'''M14 dev74: repair native-ABI inputs for the wedding internal resume entry.

Dev73 disproved the budget-edge theory: suppressing the scalar budget guard at
0x80065128 did not change the freeze. Decoding the stationary branch proves the
actual counted-loop header is 0x80064E9C (0x80065134 -> 0x80064E9C), while the
runtime repeatedly cold-resumes at 0x80065128 with r21-r24 reset to their
pre-increment values.

That shape matches a native-ABI alternate-entry liveness defect. DolRecomp's
range ABI analysis computes inputs from the normal range entry. An internal
resume/cold-entry can therefore skip earlier definitions in the same range even
though those values are live at the internal entry. For the one proven wedding
frontier range, conservatively treat every state slot the range may define as an
input too. This preserves unrestricted native ABI everywhere else and keeps the
dev72 full-state cold-escape materialization underneath it.
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

ABI_TARGET = Path("src") / "backend" / "llvm" / "abi_liveness.cpp"
INTERNAL_ENTRY_SIGNATURE = "OPENSPM_M14_LLVM_INTERNAL_ENTRY_INPUT_REPAIR"
WEDDING_RESUME_PC = 0x80065128

_ABI_TAIL_OLD = '''  for (u32 word = 0; word < DOLIR_STATE_MASK_WORDS; word++) {
    range->must_def_state[word] = hasExit ? exitDefs[word] : 0u;
    range->input_state[word] =
        range->semantic_input_state[word] |
        (range->may_def_state[word] & ~range->must_def_state[word]);
    range->output_state[word] = range->may_def_state[word];
    range->callsite_live_after[word] = 0;
    range->semantic_output_state[word] = 0;
  }
  range->abi_flags = nativeABIFlags(*function, &range->abi_blockers);
  return true;
}
'''

_ABI_TAIL_NEW = '''  for (u32 word = 0; word < DOLIR_STATE_MASK_WORDS; word++) {
    range->must_def_state[word] = hasExit ? exitDefs[word] : 0u;
    range->input_state[word] =
        range->semantic_input_state[word] |
        (range->may_def_state[word] & ~range->must_def_state[word]);
    range->output_state[word] = range->may_def_state[word];
    range->callsite_live_after[word] = 0;
    range->semantic_output_state[word] = 0;
  }

  /* OPENSPM_M14_LLVM_INTERNAL_ENTRY_INPUT_REPAIR
   * The wedding effect loop can resume directly at 0x80065128 after state was
   * defined earlier in the containing native range. Whole-range ABI liveness
   * assumes the normal range entry, so those earlier definitions can suppress
   * required native inputs for the internal entry. For this one proven range,
   * make every may-defined slot an input as well. Existing semantic inputs are
   * retained; outputs remain unchanged. This is conservative but local to one
   * range and preserves the unrestricted native ABI everywhere else. */
  if (range->start <= 0x80065128u && 0x80065128u < range->end) {
    for (u32 word = 0; word < DOLIR_STATE_MASK_WORDS; word++) {
      range->semantic_input_state[word] |= range->may_def_state[word];
      range->input_state[word] |= range->may_def_state[word];
    }
  }

  range->abi_flags = nativeABIFlags(*function, &range->abi_blockers);
  return true;
}
'''


def _verify_legacy_source() -> None:
    text = Path(_legacy.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in _RETAINED_SOURCE_CONTRACTS if marker not in text]
    if missing:
        raise RuntimeError(
            "dev74 retained DolRecomp base patch lost required source contracts: "
            + ", ".join(missing)
        )


def _verify_internal_entry_shape(text: str) -> None:
    required = (
        INTERNAL_ENTRY_SIGNATURE,
        "range->start <= 0x80065128u && 0x80065128u < range->end",
        "range->semantic_input_state[word] |= range->may_def_state[word]",
        "range->input_state[word] |= range->may_def_state[word]",
        "range->output_state[word] = range->may_def_state[word]",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev74 internal-entry ABI repair: " + ", ".join(missing)
        )


def _patch_internal_entry_inputs(source_root: Path, check_only: bool) -> str:
    path = source_root / ABI_TARGET
    text = path.read_text(encoding="utf-8")
    if INTERNAL_ENTRY_SIGNATURE in text:
        _verify_internal_entry_shape(text)
        return "already-applied"
    if text.count(_ABI_TAIL_OLD) != 1:
        raise RuntimeError(
            f"{ABI_TARGET}: expected ABI-analysis tail exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"
    patched = text.replace(_ABI_TAIL_OLD, _ABI_TAIL_NEW, 1)
    _verify_internal_entry_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    source_root = source_root.resolve()
    _verify_legacy_source()
    legacy_result = _legacy.transform(source_root, check_only)
    abi_result = _patch_internal_entry_inputs(source_root, check_only)
    return f"{legacy_result} internal-entry-input-repair={abi_result}"
