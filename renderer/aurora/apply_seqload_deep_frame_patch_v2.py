#!/usr/bin/env python3
"""Compatibility driver for the DrawDone-bounded SEQ_LOAD deep-frame audit.

The original deep-frame transform was written against Aurora after the bounded
SEQ_LOAD audit but before OpenSPM's later guest-array-bounds/frame-reuse layers.
Those layers intentionally moved `vertexData` ahead of merge selection and added
`guest_array_requirements()` plus the bounded line diagnostic.  The resulting
cached Aurora source is semantically correct, but the original deep-frame script's
exact two-line vertex hook no longer exists, so configure failed before writing
anything.

Keep the original transform intact and adapt only that one source-shape mismatch.
All other exact/fail-closed checks remain owned by apply_seqload_deep_frame_patch.py.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BASE_PATH = HERE / "apply_seqload_deep_frame_patch.py"

spec = importlib.util.spec_from_file_location("openspm_m14_seqload_deep_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit("error: unable to load base SEQ_LOAD deep-frame patch")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

_base_once = base.once

CURRENT_VERTEX_ANCHOR = (
    "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
    "  m14_log_line_draw(prim, fmt, vtxCount, guestArrays);\n\n"
)
DEEP_VERTEX_CALL = (
    "  m14_deep_vertex_stream(prim, fmt, vtxCount, vertexData.data(), vertexData.size());\n"
)


def compatible_once(text: str, old: str, new: str, label: str) -> str:
    if label != "vertex hook":
        return _base_once(text, old, new, label)

    # Fresh/older cached source: use the base script's original exact transform.
    if text.count(old) == 1:
        return text.replace(old, new, 1)

    # Current M14 source: guest-array analysis owns the placement of vertexData.
    # Insert the diagnostic immediately after that analysis/line probe, before
    # merge selection and push_verts. This observes the exact FIFO vertex bytes
    # without changing any renderer state or ordering.
    if DEEP_VERTEX_CALL in text:
        return text
    anchor_count = text.count(CURRENT_VERTEX_ANCHOR)
    if anchor_count != 1:
        raise RuntimeError(
            "vertex hook: expected one legacy hook or one current guest-array anchor "
            f"(legacy={text.count(old)}, current={anchor_count})"
        )
    return text.replace(CURRENT_VERTEX_ANCHOR,
                        CURRENT_VERTEX_ANCHOR + DEEP_VERTEX_CALL, 1)


base.once = compatible_once

if __name__ == "__main__":
    raise SystemExit(base.main())
