#!/usr/bin/env python3
"""Emulate original GX Z-before-texture ordering for alpha-tested draws.

The second gameplay GX audit found that Aurora decodes BP PE-control bit 6 into
GXState::zCompLocBeforeTex but never consumes it. Aurora's fragment shader emits
alpha-test failures as WGSL discard, so the existing single-pass WebGPU pipeline
naturally behaves like GX late-Z: an alpha-failed fragment cannot update depth.

On Wii, GXSetZCompLoc(GX_TRUE) performs the Z compare/update before texture/TEV
and alpha compare. For a depth-writing alpha-tested draw, alpha therefore gates
color but does not gate a successful depth update. Portable WebGPU does not give
OpenSPM a dependable cross-backend switch for that PE ordering, so emulate the
observable result with two passes only for the affected state:

  1. depth prepass: same geometry and Z mode, alpha compare disabled, color and
     alpha writes disabled;
  2. color pass: original TEV/alpha compare and blending, depth writes disabled,
     with EQUAL against the depth produced by the prepass (ALWAYS remains ALWAYS).

This is intentionally narrow. Ordinary late-Z draws, draws without alpha test,
draws without Z update, and GX_NEVER retain Aurora's existing path. Draw merging
is disabled only for the emulated early-Z case so a merged color pass cannot
lose its matching depth prepass.

BP 0x43 is also marked DirtyPipeline because zCompLocBeforeTex is now a pipeline
semantic. The transform runs after the established M14 gameplay rollback and is
exact/idempotent against the pinned cached Aurora source shape. Unknown drift
fails closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 GX zCompLocBeforeTex dual-pass emulation active"
ROLLBACK_MARKER = "M14 gameplay GX audit v1 regression rollback active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    regs_path = source / "lib/gx/regs.cpp"
    cp = cp_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")

    if SIGNATURE in cp:
        if cp.count(SIGNATURE) != 1:
            raise RuntimeError("zCompLoc emulation signature is inconsistent")
        if "regs[0x43] = {bp_pe_ctrl, DirtyPipeline};" not in regs:
            raise RuntimeError("zCompLoc emulation is present but BP 0x43 is not pipeline-dirty")
        return "already-applied"

    if ROLLBACK_MARKER not in cp:
        raise RuntimeError("gameplay GX regression rollback must be active first")

    regs = once(
        regs,
        "  regs[0x43] = {bp_pe_ctrl};\n",
        "  // zCompLocBeforeTex changes whether alpha compare may suppress a Z write.\n"
        "  // That is a pipeline/draw semantic in the native renderer.\n"
        "  regs[0x43] = {bp_pe_ctrl, DirtyPipeline};\n",
        "regs.cpp PE-control pipeline dirty",
    )

    cp = once(
        cp,
        "  auto& state = g_gxState;\n"
        "  auto& cache = sDrawCache;\n\n"
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n",
        "  auto& state = g_gxState;\n"
        "  auto& cache = sDrawCache;\n"
        "  const bool m14EarlyDepthAlpha =\n"
        "      state.zCompLocBeforeTex && state.depthCompare && state.depthUpdate &&\n"
        "      state.depthFunc != GX_NEVER && static_cast<bool>(state.alphaCompare);\n\n"
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n",
        "command_processor early-Z state",
    )

    cp = once(
        cp,
        "  const bool cleanState = g_gxState.dirty == 0 && fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n"
        "                          prim != GX_LINES && prim != GX_LINESTRIP && prim != GX_POINTS;\n",
        "  // A dual-pass early-Z draw must remain paired. Do not let the normal\n"
        "  // adjacent-draw merger append geometry only to its trailing color pass.\n"
        "  const bool m14EarlyDepthAlpha =\n"
        "      g_gxState.zCompLocBeforeTex && g_gxState.depthCompare && g_gxState.depthUpdate &&\n"
        "      g_gxState.depthFunc != GX_NEVER && static_cast<bool>(g_gxState.alphaCompare);\n"
        "  const bool cleanState = !m14EarlyDepthAlpha && g_gxState.dirty == 0 &&\n"
        "                          fmt == sDrawCache.lastDrawFmt && sDrawCache.lineMode == 0 &&\n"
        "                          prim != GX_LINES && prim != GX_LINESTRIP && prim != GX_POINTS;\n",
        "command_processor disable merge for early-Z pair",
    )

    draw_old = '''  cache.lastDrawFmt = fmt;
  gfx::push_draw_command(DrawData{
      .pipeline = cache.pipelineRef,
      .vertRange = vertRange,
      .idxRange = idxRange,
      .uniformRange = cache.uniformRange,
      .immediateData = immediates,
      .vtxCount = vtxCount,
      .indexCount = numIndices,
      .instanceCount = instanceCount,
      .bindGroups = cache.bindGroups,
      .dstAlpha = state.dstAlpha,
  });
'''
    draw_new = '''  cache.lastDrawFmt = fmt;
  const auto m14PushDraw = [&](const gfx::PipelineRef& pipeline, u32 dstAlpha) {
    gfx::push_draw_command(DrawData{
        .pipeline = pipeline,
        .vertRange = vertRange,
        .idxRange = idxRange,
        .uniformRange = cache.uniformRange,
        .immediateData = immediates,
        .vtxCount = vtxCount,
        .indexCount = numIndices,
        .instanceCount = instanceCount,
        .bindGroups = cache.bindGroups,
        .dstAlpha = dstAlpha,
    });
  };

  if (m14EarlyDepthAlpha) {
    // GX early-Z: a fragment that passes Z may update Z even when its later
    // texture/TEV alpha compare rejects color. First reproduce that Z-only
    // result without shader discard, then shade against the resulting depth.
    PipelineConfig depthConfig = cache.config;
    depthConfig.shaderConfig.alphaCompare = {};
    depthConfig.colorUpdate = false;
    depthConfig.alphaUpdate = false;
    const auto depthPipeline = gfx::pipeline_ref(depthConfig);
    m14PushDraw(depthPipeline, UINT32_MAX);

    PipelineConfig colorConfig = cache.config;
    colorConfig.depthUpdate = false;
    if (colorConfig.depthFunc != GX_ALWAYS) colorConfig.depthFunc = GX_EQUAL;
    const auto colorPipeline = gfx::pipeline_ref(colorConfig);
    m14PushDraw(colorPipeline, state.dstAlpha);

    static bool m14ZCompLocReported = false;
    if (!m14ZCompLocReported) {
      Log.info("M14 GX zCompLocBeforeTex dual-pass emulation active");
      m14ZCompLocReported = true;
    }
  } else {
    m14PushDraw(cache.pipelineRef, state.dstAlpha);
  }
'''
    cp = once(cp, draw_old, draw_new, "command_processor early-Z draw pair")

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        regs_path.write_text(regs, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]

    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora zCompLoc adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora GX zCompLocBeforeTex dual-pass emulation already active",
        "verified": "Aurora GX zCompLocBeforeTex dual-pass emulation verified",
        "applied": "Aurora GX zCompLocBeforeTex dual-pass emulation applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
