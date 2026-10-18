#!/usr/bin/env python3
"""Configure-time wrapper for the M14 Aurora repair chain.

The original repair driver is preserved in apply_bytebuffer_abort_diag_patch_impl.py.
This watched wrapper runs that full semantic/diagnostic chain first, then applies
OpenSPM's evidence-backed original-Wii BP texture-cache invalidation, native-PC
storage-capacity policy, the narrow runtime-frontier GX_LO_OR fallback, the
component-aware TEV register-liveness repair, and DrawDone-bounded GX diagnostics.
The deep-frame compatibility driver preserves the original exact transform while
adapting its one stale vertex-hook source shape to the current guest-array draw
path. The atlas probe records the non-identity UI texture matrix, direct S16/U16
vertex values, and effective TEVREG0 used by the suspect 512x256 I4 path. The
one-shot heavy-scene probe reuses that same diagnostic machinery when a raw
vertex-array frame first crosses the runtime-proven 7 MiB staging high-water.
The final timing probe samples elapsed time spent in raw-array staging while
removing recurring per-frame high-water log spam.

The prologue runtime proved that Aurora can expose a shader-visible texgen with
its internal GX_MAX_TEXGENSRC sentinel even though real GXInit initializes all
eight slots to TEX0..TEX7. Later gameplay proved the draw PipelineConfig could
still hold that sentinel while the raw XF shadow already contained the valid
TEX0 setup. apply_tcg_init_recovery_patch.py therefore snapshots all eight
decoded TCG slots into each draw's pipeline state (ShaderInfo still controls
which are live) and retains the narrow shader-boundary GXInit fallback plus
numTexGens/raw-XF diagnostics for genuinely uninitialized slots.

The second Astra gameplay audit is applied after the older repair chain by
apply_gameplay_gx_audit_patch.py. The first runtime batch mixed correctness
repairs with SU-scaling and aggressive cache/pipeline candidates. Player runtime
at 52a439e regressed globally and hard-stalled before the previous frontier, so
apply_gameplay_gx_regression_rollback_patch.py migrates those risky candidates
back to the prior runtime-proven behavior while preserving only the narrow
correctness repairs: legal zero/small primitives, GX vertex-cache
invalidation/merge barriers, NBT3 staging bounds and bounded fatal metadata.

After that stable rollback shape is guaranteed, later migrations remain
strictly isolated from the rejected candidates:
* apply_guest_array_lookup_index_patch.py keeps exact memcmp/snapshot semantics
  but indexes candidates by guest pointer and pre-reserves the heavy-scene
  working set, reducing the O(N^2)-ish host lookup overhead seen in Flipside;
* apply_guest_array_interval_staging_patch.py is the post-v19 performance
  candidate. Runtime proved ~15.5 MiB/frame of distinct raw guest-array prefixes
  with no reuse. It stages only each draw's actually referenced [min,max) byte
  interval and biases the existing shader arrayStart by -min using u32 modular
  arithmetic, preserving the exact final storage byte for every guest index;
* apply_efb_persistence_patch.py is now a rollback migration only: it removes
  the rejected cross-host-frame LoadOp persistence experiment from player caches;
* apply_zcomp_loc_patch.py implements only GX Z-before-texture ordering for the
  alpha-tested depth-writing case identified by the gameplay audit. It does not
  touch SU coordinates, EFB lifetime, texture decode or guest-array staging;
* apply_display_copy_snapshot_patch.py gives raw BP 0x52 display copies their
  missing copy-time XFB snapshot + optional post-copy EFB clear, and makes host
  presentation use that stable snapshot instead of the later live EFB;
* apply_copy_texture_ownership_patch.py is a compatibility entry that retires
  the invalid old shape-ownership heuristic and preserves the runtime-proven raw
  GX half-scale texture-copy semantic;
* apply_display_list_continuation_patch.py fixes Astra's source-confirmed nested
  CALL_DL loss path: commands after an internal DrawDone are consumed instead of
  being silently discarded when the parent list resumes;
* apply_blocking_raw_gx_pipeline_patch.py is now a rollback migration. Runtime at
  6b99582 showed 0.44-0.52 second first-use waits without changing the white/Tippi
  material defect, so the blocking experiment is removed until a real pipeline
  prewarm/replay design can preserve correctness without freezing gameplay;
* apply_readback_boundary_patch.py keeps PE bbox CPU readback distinct from
  display presentation and preserves the EFB exactly once across that split.

Important migration rule: an existing player cache may already contain the
regressive gameplay-v1 source shape. In that case the rollback MUST run before
older exact/idempotent patch scripts inspect command_processor.cpp; otherwise an
older layer can mistake the modified source for a fresh checkout and fail its
exact anchors. New optimizations always run only after the rollback marker is
present.

Keeping these steps here ensures normal M14-UPDATE-RUN reconfiguration refreshes
an existing cached Aurora checkout without a wipe.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GAMEPLAY_V1_MARKER = "M14 gameplay GX audit v1: vertex visibility epoch active"
ROLLBACK_MARKER = "M14 gameplay GX audit v1 regression rollback active"


def run_script(name: str) -> int:
    completed = subprocess.run([sys.executable, str(HERE / name), *sys.argv[1:]])
    return int(completed.returncode)


def aurora_command_processor_text() -> str:
    if len(sys.argv) < 2:
        return ""
    source = pathlib.Path(sys.argv[1]).resolve()
    path = source / "lib/gx/command_processor.cpp"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main() -> int:
    # Heal an already-patched player cache before any older exact transform sees
    # the gameplay-v1 source shape. This is intentionally conditional so a fresh
    # pinned Aurora checkout still flows through the ordinary chain below.
    cached_cp = aurora_command_processor_text()
    if GAMEPLAY_V1_MARKER in cached_cp and ROLLBACK_MARKER not in cached_cp:
        rc = run_script("apply_gameplay_gx_regression_rollback_patch.py")
        if rc != 0:
            return rc

    rc = run_script("apply_bytebuffer_abort_diag_patch_impl.py")
    if rc != 0:
        return rc
    rc = run_script("apply_raw_tex_invalidate_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_pc_storage_capacity_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_logic_or_frontier_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_tcg_init_recovery_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_tev_component_liveness_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_seqload_deep_frame_patch_v2.py")
    if rc != 0:
        return rc
    rc = run_script("apply_seqload_texmtx_probe_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_heavy_scene_audit_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_guest_array_timing_probe_patch.py")
    if rc != 0:
        return rc

    # Existing rollback-marked caches already contain the retained correctness
    # subset. Fresh checkouts receive gameplay-v1 once and immediately migrate
    # its rejected SU/cache/base candidates back out before any newer layer runs.
    cached_cp = aurora_command_processor_text()
    if ROLLBACK_MARKER not in cached_cp:
        rc = run_script("apply_gameplay_gx_audit_patch.py")
        if rc != 0:
            return rc
        rc = run_script("apply_gameplay_gx_regression_rollback_patch.py")
        if rc != 0:
            return rc

    rc = run_script("apply_guest_array_lookup_index_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_guest_array_interval_staging_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_efb_persistence_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_zcomp_loc_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_display_copy_snapshot_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_copy_texture_ownership_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_display_list_continuation_patch.py")
    if rc != 0:
        return rc
    rc = run_script("apply_blocking_raw_gx_pipeline_patch.py")
    if rc != 0:
        return rc
    return run_script("apply_readback_boundary_patch.py")


if __name__ == "__main__":
    raise SystemExit(main())
