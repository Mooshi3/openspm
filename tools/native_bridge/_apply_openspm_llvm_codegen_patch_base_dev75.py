#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
"""M14 dev75: keep the native register ABI, but disable legacy x86 SJLJ cold escapes.

The recompcore/x86 cold-escape path uses llvm.eh.sjlj.longjmp to unwind nested
native calls immediately. That skips caller-side acceptNativeResult() merges.
OpenSPM has a reproducible correctness failure where guest GPR updates made in
the native call chain disappear across repeated soft exits. Native-ABI-off is
not a viable fallback because the current relF coverage depends on native calls.

Use DolRecomp's existing non-cold native-ABI return protocol on legacy x86
instead. It returns PC/continuation plus architectural outputs through each
native caller, preserving the same unrestricted native ABI and compiled
coverage. ModernGekko and non-Windows AArch64 retain their existing cold-escape
behavior. No game address or title-specific rule is involved.
"""
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

EMITTER_TARGET = Path("src") / "backend" / "llvm" / "emitter.cpp"
SAFE_ESCAPE_SIGNATURE = "OPENSPM_M14_LLVM_X86_PROPAGATING_ESCAPES"

_ESCAPE_OLD = """  intrinsic_escapes_ = triple.isX86() && !modern_runtime_;
  cold_escapes_ = modern_runtime_ || intrinsic_escapes_ ||
                  (triple.isAArch64() && !triple.isOSWindows());
"""

_ESCAPE_NEW = """  intrinsic_escapes_ = triple.isX86() && !modern_runtime_;
  /* OPENSPM_M14_LLVM_X86_PROPAGATING_ESCAPES
   * Legacy x86 cold escapes use SJLJ longjmp, which bypasses every nested
   * caller's acceptNativeResult() state merge. Keep the native register ABI,
   * but use DolRecomp's ordinary propagating return protocol on legacy x86 so
   * architectural outputs flow through the complete native call chain before
   * control returns to the dispatcher. */
  cold_escapes_ = modern_runtime_ ||
                  (triple.isAArch64() && !triple.isOSWindows());
"""


def _verify_legacy_source() -> None:
    text = Path(_legacy.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in _RETAINED_SOURCE_CONTRACTS if marker not in text]
    if missing:
        raise RuntimeError(
            "dev75 retained DolRecomp base patch lost required source contracts: "
            + ", ".join(missing)
        )


def _verify_escape_shape(text: str) -> None:
    required = (
        SAFE_ESCAPE_SIGNATURE,
        "intrinsic_escapes_ = triple.isX86() && !modern_runtime_;",
        "cold_escapes_ = modern_runtime_ ||",
        "(triple.isAArch64() && !triple.isOSWindows());",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev75 propagating-escape patch: " + ", ".join(missing)
        )
    forbidden = "cold_escapes_ = modern_runtime_ || intrinsic_escapes_ ||"
    if forbidden in text:
        raise RuntimeError(
            "dev75 x86 cold-escape opt-out did not replace the legacy assignment"
        )


def _patch_propagating_escapes(source_root: Path, check_only: bool) -> str:
    path = source_root / EMITTER_TARGET
    text = path.read_text(encoding="utf-8")
    if SAFE_ESCAPE_SIGNATURE in text:
        _verify_escape_shape(text)
        return "already-applied"
    if text.count(_ESCAPE_OLD) != 1:
        raise RuntimeError(
            f"{EMITTER_TARGET}: expected cold-escape assignment exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"
    patched = text.replace(_ESCAPE_OLD, _ESCAPE_NEW, 1)
    _verify_escape_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    source_root = source_root.resolve()
    _verify_legacy_source()
    legacy_result = _legacy.transform(source_root, check_only)
    escape_result = _patch_propagating_escapes(source_root, check_only)
    return f"{legacy_result} x86-propagating-escapes={escape_result}"
