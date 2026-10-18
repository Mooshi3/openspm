#!/usr/bin/env python3
"""Localize and trim the remaining synchronous raw-GX draw cost.

The Windows run at dda366ec proved that compact guest-array intervals removed
our old staging high-water, but heavy gameplay still executes tens of thousands
of GX primitive commands per second and can spend most wall time inside
handle_draw -> draw_prim.  The first coarse profiler also became noisy at that
rate (one line per 2048 draws).

This migration keeps rendering semantics unchanged while doing two things:

* skip the full raw guest-index scan when the draw has no INDEX8/INDEX16
  attribute backed by guestMapped memory.  For direct attributes and Aurora
  source-port arrays GuestArrayRequirements is necessarily all-zero, so this is
  an exact fast path rather than a heuristic;
* replace the coarse 2048-draw logger with a 65536-draw breakdown covering
  guest-index scanning, vertex staging, indexed-array staging, pipeline rebuild,
  texture/bind rebuild and uniform rebuild, plus merge counts.  Individual slow
  draws remain visible through max_us without per-draw log spam.

Unknown/partial source drift fails closed.  The profiler is temporary evidence
gathering; the direct/no-guest-index fast path is safe to retain independently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 GX hotpath profile"
OLD_SIGNATURE = "M14 GX draw CPU profile"


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
    if SIGNATURE in text:
        return "already-applied"
    if OLD_SIGNATURE not in text:
        raise RuntimeError("coarse GX draw profiler must be active first")
    if "M14 raw guest-array compact interval staging active" not in text:
        raise RuntimeError("compact guest-array interval staging must be active first")

    # Shared counters live beside GuestArrayRequirements so draw_prim and
    # push_gx_draw can contribute to one low-spam window.
    profile_globals = r'''struct M14DrawHotProfile {
  u64 calls = 0;
  u64 windowTotalNanos = 0;
  u64 windowMaxNanos = 0;
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
};

M14DrawHotProfile sM14DrawHotProfile;
constexpr u64 M14DrawHotWindow = 65536;

inline u64 m14_draw_hot_elapsed_ns(std::chrono::steady_clock::time_point start) noexcept {
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start).count();
  return elapsed > 0 ? static_cast<u64>(elapsed) : 0u;
}

void m14_draw_hot_finish(u64 nanos) noexcept {
  auto& p = sM14DrawHotProfile;
  ++p.calls;
  p.windowTotalNanos += nanos;
  p.windowMaxNanos = std::max(p.windowMaxNanos, nanos);
  if ((p.calls % M14DrawHotWindow) != 0u) return;

  Log.info("M14 GX hotpath profile calls={} window={} total_us={} avg_us={} max_us={} scan_us={} scan_calls={} verts_us={} arrays_us={} pipe_us={} pipe_calls={} bind_us={} bind_calls={} uniform_us={} uniform_calls={} merged={} unmerged={}",
           p.calls, M14DrawHotWindow,
           p.windowTotalNanos / 1000u,
           (p.windowTotalNanos / M14DrawHotWindow) / 1000u,
           p.windowMaxNanos / 1000u,
           p.guestScanNanos / 1000u, p.guestScanCalls,
           p.vertPushNanos / 1000u,
           p.arrayStageNanos / 1000u,
           p.pipelineNanos / 1000u, p.pipelineCalls,
           p.bindNanos / 1000u, p.bindCalls,
           p.uniformNanos / 1000u, p.uniformCalls,
           p.merged, p.unmerged);

  p.windowTotalNanos = 0;
  p.windowMaxNanos = 0;
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
        "hotpath profile globals",
    )

    # Array staging inside push_gx_draw.  This includes the compact raw interval
    # stage/cache path and the ordinary source-port cachedRange path.
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
        "\n  sM14DrawHotProfile.arrayStageNanos += m14_draw_hot_elapsed_ns(m14ArrayStageStart);\n"
        "  const u8 lineMode = line_mode_for_prim(prim);\n",
        "array-stage timer finish",
    )

    # Pipeline creation/cache lookup branch.
    text = once(
        text,
        "  if (!pipelineValid) {\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "  if (!pipelineValid) {\n"
        "    const auto m14PipelineStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.pipelineCalls;\n"
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
        "    sM14DrawHotProfile.pipelineNanos += m14_draw_hot_elapsed_ns(m14PipelineStart);\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "pipeline timer finish",
    )

    # Texture resolution + bind group reconstruction.
    text = once(
        text,
        "  if (!bindGroupsValid) {\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "  if (!bindGroupsValid) {\n"
        "    const auto m14BindStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.bindCalls;\n"
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
        "    sM14DrawHotProfile.bindNanos += m14_draw_hot_elapsed_ns(m14BindStart);\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "bind timer finish",
    )

    # Uniform rebuild branch.
    text = once(
        text,
        "  if (!uniformValid) {\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "  }\n",
        "  if (!uniformValid) {\n"
        "    const auto m14UniformStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.uniformCalls;\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "    sM14DrawHotProfile.uniformNanos += m14_draw_hot_elapsed_ns(m14UniformStart);\n"
        "  }\n",
        "uniform timer",
    )

    # Exact fast path: only raw indexed attributes can contribute guest byte
    # requirements. Direct attributes and source-port arrays need no scan.
    text = once(
        text,
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "  GuestArrayRequirements guestArrays{};\n"
        "  bool m14NeedsGuestIndexScan = false;\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n"
        "    const auto desc = g_gxState.vtxDesc[i];\n"
        "    if (g_gxState.arrays[i].guestMapped &&\n"
        "        (desc == GX_INDEX8 || desc == GX_INDEX16)) {\n"
        "      m14NeedsGuestIndexScan = true;\n"
        "      break;\n"
        "    }\n"
        "  }\n"
        "  if (m14NeedsGuestIndexScan) {\n"
        "    const auto m14GuestScanStart = std::chrono::steady_clock::now();\n"
        "    guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
        "    sM14DrawHotProfile.guestScanNanos += m14_draw_hot_elapsed_ns(m14GuestScanStart);\n"
        "    ++sM14DrawHotProfile.guestScanCalls;\n"
        "  }\n",
        "guest-index scan fast path",
    )

    text = once(
        text,
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n",
        "  const auto m14VertPushStart = std::chrono::steady_clock::now();\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
        "  sM14DrawHotProfile.vertPushNanos += m14_draw_hot_elapsed_ns(m14VertPushStart);\n",
        "vertex-push timer",
    )

    text = once(
        text,
        "  if (canMerge) {\n"
        "    u32 numIndices = 0;\n",
        "  if (canMerge) {\n"
        "    ++sM14DrawHotProfile.merged;\n"
        "    u32 numIndices = 0;\n",
        "merge counter",
    )
    text = once(
        text,
        "\n  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "\n  ++sM14DrawHotProfile.unmerged;\n"
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "unmerged counter",
    )

    # Replace the noisy coarse profiler, retaining one start/end clock per draw
    # so max_us still catches the rare tens-of-ms primitive.
    old_handle = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 GX draw CPU profile: diagnostic only. Keep timing outside draw_prim so
  // the sample includes vertex parsing, raw-array staging and draw preparation
  // exactly as the synchronous DrawDone drain experiences them.
  static u64 sM14DrawCpuCalls = 0;
  static u64 sM14DrawCpuWindowNanos = 0;
  static u64 sM14DrawCpuWindowMaxNanos = 0;
  constexpr u64 M14DrawCpuWindow = 2048;

  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  const u16 vtxCount = reader.read<u16>();
  const auto start = std::chrono::steady_clock::now();
  draw_prim(prim, fmt, vtxCount, reader);
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start).count();
  const u64 nanos = elapsed > 0 ? static_cast<u64>(elapsed) : 0u;
  ++sM14DrawCpuCalls;
  sM14DrawCpuWindowNanos += nanos;
  sM14DrawCpuWindowMaxNanos = std::max(sM14DrawCpuWindowMaxNanos, nanos);

  if ((sM14DrawCpuCalls % M14DrawCpuWindow) == 0u) {
    Log.info("M14 GX draw CPU profile calls={} window={} cpu_us={} avg_us={} max_us={}",
             sM14DrawCpuCalls, M14DrawCpuWindow,
             sM14DrawCpuWindowNanos / 1000u,
             (sM14DrawCpuWindowNanos / M14DrawCpuWindow) / 1000u,
             sM14DrawCpuWindowMaxNanos / 1000u);
    sM14DrawCpuWindowNanos = 0u;
    sM14DrawCpuWindowMaxNanos = 0u;
  }
}
'''
    new_handle = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 GX hotpath profile: low-spam diagnostic around the exact synchronous
  // draw path. Substage counters are accumulated by draw_prim/push_gx_draw.
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  const u16 vtxCount = reader.read<u16>();
  const auto start = std::chrono::steady_clock::now();
  draw_prim(prim, fmt, vtxCount, reader);
  m14_draw_hot_finish(m14_draw_hot_elapsed_ns(start));
}
'''
    text = once(text, old_handle, new_handle, "coarse-profiler migration")

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
            "already-applied": "Aurora GX hotpath breakdown already active",
            "verified": "Aurora GX hotpath breakdown verified",
            "applied": "Aurora GX hotpath breakdown applied",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM GX hotpath breakdown adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
