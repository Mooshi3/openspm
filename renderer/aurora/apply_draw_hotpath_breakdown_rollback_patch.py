#!/usr/bin/env python3
"""Roll back the regressive M14 GX hotpath breakdown/fast-path experiment.

Player runtime at repo 16d2b30d6454 reported two correctness regressions that
were absent in the prior synchronous build: Chapter 1-2 rotating devices smeared
Mario again, and entering a door could leave Mario standing in the interaction
with the event apparently waiting forever. The only renderer semantic delta
since the previous player-proven run was apply_draw_hotpath_breakdown_patch.py,
which also introduced a no-guest-index scan fast path.

Do not try to rationalize those reports away. Restore the exact coarse-profiler
source shape first, so ordinary M14-UPDATE-RUN heals an already-patched cached
Aurora checkout without deleting .openspm. The 181358/233530 hotpath timings are
still useful evidence, but this experiment is retired until its correctness
interaction is understood.

This transform is intentionally exact/idempotent. Unknown partial source drift
fails closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
HOT_SIGNATURE = "M14 GX hotpath profile"
COARSE_SIGNATURE = "M14 GX draw CPU profile"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


COARSE_HANDLE = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
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

HOT_HANDLE = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
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


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if HOT_SIGNATURE not in text:
        if COARSE_SIGNATURE in text:
            return "already-rolled-back"
        raise RuntimeError("neither hotpath nor coarse draw-profiler source shape is present")

    # Remove the shared hotpath accumulator block while preserving the
    # GuestArrayRequirements declaration that followed it before the experiment.
    pattern = re.compile(
        r"struct M14DrawHotProfile \{.*?\n\}\n\n"
        r"using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        re.DOTALL,
    )
    text, n = pattern.subn(
        "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError(f"hotpath profile globals: expected one block, found {n}")

    text = once(
        text,
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  const auto m14ArrayStageStart = std::chrono::steady_clock::now();\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "array-stage timer start rollback",
    )
    text = once(
        text,
        "\n  sM14DrawHotProfile.arrayStageNanos += m14_draw_hot_elapsed_ns(m14ArrayStageStart);\n"
        "  const u8 lineMode = line_mode_for_prim(prim);\n",
        "\n  const u8 lineMode = line_mode_for_prim(prim);\n",
        "array-stage timer finish rollback",
    )

    text = once(
        text,
        "  if (!pipelineValid) {\n"
        "    const auto m14PipelineStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.pipelineCalls;\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "  if (!pipelineValid) {\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "pipeline timer start rollback",
    )
    text = once(
        text,
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "    sM14DrawHotProfile.pipelineNanos += m14_draw_hot_elapsed_ns(m14PipelineStart);\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "pipeline timer finish rollback",
    )

    text = once(
        text,
        "  if (!bindGroupsValid) {\n"
        "    const auto m14BindStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.bindCalls;\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "  if (!bindGroupsValid) {\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "bind timer start rollback",
    )
    text = once(
        text,
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "    sM14DrawHotProfile.bindNanos += m14_draw_hot_elapsed_ns(m14BindStart);\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "bind timer finish rollback",
    )

    text = once(
        text,
        "  if (!uniformValid) {\n"
        "    const auto m14UniformStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawHotProfile.uniformCalls;\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "    sM14DrawHotProfile.uniformNanos += m14_draw_hot_elapsed_ns(m14UniformStart);\n"
        "  }\n",
        "  if (!uniformValid) {\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "  }\n",
        "uniform timer rollback",
    )

    fast_path = (
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
        "  }\n"
    )
    text = once(
        text,
        fast_path,
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "guest-index fast-path rollback",
    )

    text = once(
        text,
        "  const auto m14VertPushStart = std::chrono::steady_clock::now();\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
        "  sM14DrawHotProfile.vertPushNanos += m14_draw_hot_elapsed_ns(m14VertPushStart);\n",
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n",
        "vertex timer rollback",
    )
    text = once(
        text,
        "  if (canMerge) {\n"
        "    ++sM14DrawHotProfile.merged;\n"
        "    u32 numIndices = 0;\n",
        "  if (canMerge) {\n"
        "    u32 numIndices = 0;\n",
        "merge counter rollback",
    )
    text = once(
        text,
        "\n  ++sM14DrawHotProfile.unmerged;\n"
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "\n  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "unmerged counter rollback",
    )

    text = once(text, HOT_HANDLE, COARSE_HANDLE, "draw profiler rollback")

    if HOT_SIGNATURE in text:
        raise RuntimeError("hotpath signature remained after rollback")
    if COARSE_SIGNATURE not in text:
        raise RuntimeError("coarse profiler signature missing after rollback")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "rolled-back"


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
            "already-rolled-back": "Aurora GX hotpath experiment already absent",
            "verified": "Aurora GX hotpath rollback verified",
            "rolled-back": "Aurora GX hotpath experiment rolled back",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM GX hotpath rollback failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
