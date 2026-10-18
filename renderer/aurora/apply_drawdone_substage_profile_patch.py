#!/usr/bin/env python3
"""Profile synchronous Aurora GX draw preparation without changing semantics.

The 050525 Windows run proved that the direct Broadway WPAR store shortcut does
not remove the scene-dependent choppiness.  DrawDone still costs roughly 4-8 ms
in the heaviest sampled windows while presentation itself is effectively free.

A historical 65536-draw experiment localized most draw CPU to indexed-array
staging, but that experiment bundled a rejected no-guest-index optimization that
caused rotating-device corruption and door softlocks.  Never restore that
optimization.

This patch resurrects measurements ONLY.  It keeps the exact existing
`guest_array_requirements()` call, guest-array cache/snapshot semantics, FIFO
order, draw merging, pipeline state, and renderer threading model.  All clocks
run on the existing synchronous Drain/producer path.  One aggregate line is
emitted per 256 GXDrawDone boundaries so it aligns with OpenSPM's producer-sync
window without per-draw log spam.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 GX DrawDone substage profiler active"
RETIRED_SIGNATURE = "M14 DrawDone per-draw profiler retired for normal play"
QUIET_SIGNATURE = "M14 normal-run draw profiler disabled"
POOL_SIGNATURE = "M14 raw guest-array pooled exact snapshots single-copy append active"
REJECTED_SIGNATURE = "M14 GX hotpath profile"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if RETIRED_SIGNATURE in text:
        # dev88 deliberately removed this measurement from normal play.
        # Configure-time compatibility chains must never resurrect it.
        return "already-retired"
    if SIGNATURE in text:
        return "already-applied"
    # Fresh pinned home-test checkouts may not yet carry the incremental M14
    # performance layers.  Do not make those builds fail merely for a temporary
    # profiler; the current player cache does carry these markers and is the
    # authoritative target for this diagnostic.
    if QUIET_SIGNATURE not in text or POOL_SIGNATURE not in text:
        return "not-ready"
    if REJECTED_SIGNATURE in text:
        raise RuntimeError("retired semantic hotpath experiment is present; refusing to profile it")
    if "M14 raw guest-array compact interval staging active" not in text:
        raise RuntimeError("compact exact guest-array staging must be active first")
    if "#include <chrono>" not in text:
        raise RuntimeError("expected existing producer-thread chrono diagnostics are missing")

    # process() sees GXDrawDone before the profile implementation below, so add
    # a declaration beside the existing command handlers.
    text = once(
        text,
        "static void handle_draw(u8 cmd, ByteReader& reader) noexcept;\n"
        "static void handle_aurora(ByteReader& reader) noexcept;\n",
        "static void handle_draw(u8 cmd, ByteReader& reader) noexcept;\n"
        "static void handle_aurora(ByteReader& reader) noexcept;\n"
        "static void m14_drawdone_profile_finish() noexcept;\n",
        "DrawDone profiler forward declaration",
    )

    # Report immediately after the real BP DrawDone command has been handled,
    # before process() returns to fifo::drain().  This changes diagnostics only.
    text = once(
        text,
        "      if (reg_get(value, 8, 24) == GX_BP_REG_DRAWDONE) {\n"
        "        return {static_cast<u32>(reader.offset()), true};\n"
        "      }\n",
        "      if (reg_get(value, 8, 24) == GX_BP_REG_DRAWDONE) {\n"
        "        m14_drawdone_profile_finish();\n"
        "        return {static_cast<u32>(reader.offset()), true};\n"
        "      }\n",
        "DrawDone profile boundary",
    )

    profile_globals = r'''struct M14DrawDoneProfile {
  u64 totalFrames = 0;
  u64 windowFrames = 0;
  u64 draws = 0;
  u64 vertices = 0;
  u64 drawNanos = 0;
  u64 maxDrawNanos = 0;
  u64 guestScanNanos = 0;
  u64 guestScanCalls = 0;
  u64 vertPushNanos = 0;
  u64 arrayStageNanos = 0;
  u64 pipelineNanos = 0;
  u64 pipelineCalls = 0;
  u64 bindNanos = 0;
  u64 bindCalls = 0;
  u64 uniformNanos = 0;
  u64 uniformCalls = 0;
  u64 merged = 0;
  u64 unmerged = 0;
  bool reported = false;
};

M14DrawDoneProfile sM14DrawDoneProfile;
constexpr u64 M14DrawDoneProfileWindow = 256;

inline u64 m14_drawdone_elapsed_ns(std::chrono::steady_clock::time_point start) noexcept {
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start).count();
  return elapsed > 0 ? static_cast<u64>(elapsed) : 0u;
}

static void m14_drawdone_profile_finish() noexcept {
  auto& p = sM14DrawDoneProfile;
  ++p.totalFrames;
  ++p.windowFrames;
  if (!p.reported) {
    Log.info("M14 GX DrawDone substage profiler active; producer-thread timing only, rendering semantics unchanged");
    p.reported = true;
  }
  if (p.windowFrames < M14DrawDoneProfileWindow) return;

  const u64 measuredNanos = p.guestScanNanos + p.vertPushNanos + p.arrayStageNanos +
                            p.pipelineNanos + p.bindNanos + p.uniformNanos;
  Log.info("M14 GX DrawDone substage profile total_frames={} frames={} draws={} vertices={} draw_us={} avg_frame_draw_us={} max_draw_us={} measured_us={} scan_us={} scan_calls={} verts_us={} arrays_us={} pipe_us={} pipe_calls={} bind_us={} bind_calls={} uniform_us={} uniform_calls={} merged={} unmerged={}",
           p.totalFrames, p.windowFrames, p.draws, p.vertices,
           p.drawNanos / 1000u,
           p.windowFrames != 0u ? (p.drawNanos / p.windowFrames) / 1000u : 0u,
           p.maxDrawNanos / 1000u, measuredNanos / 1000u,
           p.guestScanNanos / 1000u, p.guestScanCalls,
           p.vertPushNanos / 1000u, p.arrayStageNanos / 1000u,
           p.pipelineNanos / 1000u, p.pipelineCalls,
           p.bindNanos / 1000u, p.bindCalls,
           p.uniformNanos / 1000u, p.uniformCalls,
           p.merged, p.unmerged);

  p.windowFrames = 0;
  p.draws = 0;
  p.vertices = 0;
  p.drawNanos = 0;
  p.maxDrawNanos = 0;
  p.guestScanNanos = 0;
  p.guestScanCalls = 0;
  p.vertPushNanos = 0;
  p.arrayStageNanos = 0;
  p.pipelineNanos = 0;
  p.pipelineCalls = 0;
  p.bindNanos = 0;
  p.bindCalls = 0;
  p.uniformNanos = 0;
  p.uniformCalls = 0;
  p.merged = 0;
  p.unmerged = 0;
}

'''
    text = once(
        text,
        "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        profile_globals + "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        "DrawDone profile globals",
    )

    # Time the indexed-array upload/cache loop as a whole.  This preserves the
    # exact proven snapshot/memcmp path, unlike the retired fast-path experiment.
    text = once(
        text,
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  const auto m14ArrayStageStart = std::chrono::steady_clock::now();\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "array-stage timer start",
    )
    text = once(
        text,
        "\n  const u8 lineMode = line_mode_for_prim(prim);\n",
        "\n  sM14DrawDoneProfile.arrayStageNanos += m14_drawdone_elapsed_ns(m14ArrayStageStart);\n"
        "  const u8 lineMode = line_mode_for_prim(prim);\n",
        "array-stage timer finish",
    )

    # Time state rebuild branches without changing their validity tests.
    text = once(
        text,
        "  if (!pipelineValid) {\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "  if (!pipelineValid) {\n"
        "    const auto m14PipelineStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.pipelineCalls;\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "pipeline timer start",
    )
    text = once(
        text,
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "    sM14DrawDoneProfile.pipelineNanos += m14_drawdone_elapsed_ns(m14PipelineStart);\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "pipeline timer finish",
    )
    text = once(
        text,
        "  if (!bindGroupsValid) {\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "  if (!bindGroupsValid) {\n"
        "    const auto m14BindStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.bindCalls;\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "bind timer start",
    )
    text = once(
        text,
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "    sM14DrawDoneProfile.bindNanos += m14_drawdone_elapsed_ns(m14BindStart);\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "bind timer finish",
    )
    text = once(
        text,
        "  if (!uniformValid) {\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "  }\n",
        "  if (!uniformValid) {\n"
        "    const auto m14UniformStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.uniformCalls;\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "    sM14DrawDoneProfile.uniformNanos += m14_drawdone_elapsed_ns(m14UniformStart);\n"
        "  }\n",
        "uniform timer",
    )

    # Measure the mandatory guest-index scan without skipping it.
    text = once(
        text,
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "  const auto m14GuestScanStart = std::chrono::steady_clock::now();\n"
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
        "  sM14DrawDoneProfile.guestScanNanos += m14_drawdone_elapsed_ns(m14GuestScanStart);\n"
        "  ++sM14DrawDoneProfile.guestScanCalls;\n",
        "guest-index scan timer",
    )
    text = once(
        text,
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n",
        "  const auto m14VertPushStart = std::chrono::steady_clock::now();\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
        "  sM14DrawDoneProfile.vertPushNanos += m14_drawdone_elapsed_ns(m14VertPushStart);\n",
        "vertex-push timer",
    )
    text = once(
        text,
        "  if (canMerge) {\n"
        "    u32 numIndices = 0;\n",
        "  if (canMerge) {\n"
        "    ++sM14DrawDoneProfile.merged;\n"
        "    u32 numIndices = 0;\n",
        "merge counter",
    )
    text = once(
        text,
        "\n  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "\n  ++sM14DrawDoneProfile.unmerged;\n"
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "unmerged counter",
    )

    quiet_handle = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 normal-run draw profiler disabled: keep ordinary gameplay free of
  // per-draw host-clock reads and high-volume aggregate log writes.
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  draw_prim(prim, fmt, reader.read<u16>(), reader);
}
'''
    profiled_handle = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 GX DrawDone substage profiler active: temporary producer-thread-only
  // timing.  No draw, cache, guest-memory or FIFO behavior is changed.
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  const u16 vtxCount = reader.read<u16>();
  const auto m14DrawStart = std::chrono::steady_clock::now();
  draw_prim(prim, fmt, vtxCount, reader);
  const u64 m14DrawNanos = m14_drawdone_elapsed_ns(m14DrawStart);
  ++sM14DrawDoneProfile.draws;
  sM14DrawDoneProfile.vertices += vtxCount;
  sM14DrawDoneProfile.drawNanos += m14DrawNanos;
  sM14DrawDoneProfile.maxDrawNanos = std::max(sM14DrawDoneProfile.maxDrawNanos, m14DrawNanos);
}
'''
    text = once(text, quiet_handle, profiled_handle, "quiet draw handler profiler")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
        if actual != expected:
            raise RuntimeError(f"Aurora revision mismatch: expected {expected}, got {actual}")
        result = transform(source, args.check_only)
        print({
            "already-retired": "Aurora DrawDone substage profiler remains retired for normal play",
            "already-applied": "Aurora DrawDone substage profiler already active",
            "not-ready": "Aurora DrawDone substage profiler skipped on pre-frontier source",
            "verified": "Aurora DrawDone substage profiler verified",
            "applied": "Aurora DrawDone substage profiler applied",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM DrawDone substage profiler adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
