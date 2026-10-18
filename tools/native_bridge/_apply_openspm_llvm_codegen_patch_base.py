#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
"""M14 dev78: retire superseded wedding-era full-state cold-escape spills.

dev77 fixed the wedding liveness failure structurally by making the proven
0x80065128 restart coordinate a real LLVM partition entry. The two compiler
experiments layered above the pre-wedding base are therefore no longer justified:

* dev72 widened syncDirtyState() on legacy x86 native-ABI cold escapes from the
  ordinary dirty subset to every state slot used by the native range.
* dev76 then called that widened syncDirtyState() before every external native
  call so an SJLJ longjmp could not skip caller-local state.

Neither experiment fixed the wedding. Together they can force broad architectural
state stores at a very hot native-call boundary, exactly where heavy gameplay
scales with actors/effects. Restore the last pre-experiment compiler transform
(dev71) while retaining dev77's structural restart entry in the player-local
partition map.

This is a performance/correctness cleanup, not a semantic shortcut: unrestricted
native ABI, x86 cold escapes, indirect budget guards, linked-call continuations,
FPU/MTMSR restart entries, inline WPAR gather, and the generated positive
native-region admission cache remain active through the normal composing patch.
"""

from __future__ import annotations

from pathlib import Path

import _apply_openspm_llvm_codegen_patch_base_dev71 as _legacy

COLD_ENTRY_SIGNATURE = _legacy.COLD_ENTRY_SIGNATURE

# apply_openspm_llvm_wpar_codegen_patch.py verifies these contracts by reading
# this active base module's source before delegating. Keep the strings here and
# also prove the preserved dev71 implementation owns them.
_RETAINED_SOURCE_CONTRACTS = (
    "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER",
    '"openspm_m14_llvm_wg_bytes_ptr"',
    '"openspm_m14_flush_wg_llvm"',
    "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);",
    "materialize(current_pc_);",
)

DEV78_SIGNATURE = "OPENSPM_M14_LLVM_RETIRE_FULL_STATE_CALLER_CHECKPOINT"


def _verify_legacy_source() -> None:
    text = Path(_legacy.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in _RETAINED_SOURCE_CONTRACTS if marker not in text]
    if missing:
        raise RuntimeError(
            "dev78 retained pre-wedding DolRecomp base lost required contracts: "
            + ", ".join(missing)
        )

    # These are the two superseded experimental transforms. They must not exist
    # in the restored base source, otherwise dev78 would only relabel the same
    # expensive compiler behavior.
    forbidden = (
        "OPENSPM_M14_LLVM_COLD_ESCAPE_FULL_STATE",
        "OPENSPM_M14_LLVM_CALLER_STATE_CHECKPOINT",
    )
    leaked = [marker for marker in forbidden if marker in text]
    if leaked:
        raise RuntimeError(
            "dev78 pre-wedding base unexpectedly contains retired state-spill "
            "experiments: " + ", ".join(leaked)
        )


def transform(source_root: Path, check_only: bool = False) -> str:
    # OPENSPM_M14_LLVM_RETIRE_FULL_STATE_CALLER_CHECKPOINT
    source_root = source_root.resolve()
    _verify_legacy_source()
    legacy_result = _legacy.transform(source_root, check_only)
    return f"{legacy_result} dev78-state-spills=retired"
