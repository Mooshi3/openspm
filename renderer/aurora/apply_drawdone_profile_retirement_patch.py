#!/usr/bin/env python3
"""Retire the temporary per-draw DrawDone substage profiler for normal play.

The dev87 player run proved that ordinary LLVM host-call pressure can be reduced
by roughly an order of magnitude without recovering the remaining ~30 FPS heavy
scene cliff.  The same run also showed that the temporary DrawDone breakdown
probe is still timing thousands of draws per frame.

That probe was intentionally diagnostic only.  It inserts multiple
std::chrono::steady_clock::now() calls around every draw, guest-index scan,
vertex upload, array-stage loop and selected state rebuilds.  Once the substage
cost split has been measured, keeping those clocks in the shipping hot path can
consume the small 1-3 ms margin that separates a 60 Hz frame from a missed VI
deadline.

This transform removes only the diagnostic timing/counters installed by
apply_drawdone_substage_profile_patch.py and restores the already-proven
"M14 normal-run draw profiler disabled" handle_draw shape.  GX/FIFO ordering,
guest-array requirements/staging, draw merging, pipeline/bind/uniform behavior,
renderer threading and DrawDone semantics are unchanged.

The migration is exact and idempotent. An already-quiet adapted checkout gets
the same retirement marker so later hotpath migrations accept it too.
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

PROFILE_SIGNATURE = "M14 GX DrawDone substage profiler active"
QUIET_SIGNATURE = "M14 normal-run draw profiler disabled"
RETIRED_SIGNATURE = "M14 DrawDone per-draw profiler retired for normal play"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected source fragment exactly once, found {count}"
        )
    return text.replace(old, new, 1)


QUIET_HANDLE = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 normal-run draw profiler disabled: keep ordinary gameplay free of
  // per-draw host-clock reads and high-volume aggregate log writes.
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  draw_prim(prim, fmt, reader.read<u16>(), reader);
}
'''

PROFILED_HANDLE = r'''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
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


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if RETIRED_SIGNATURE in text:
        return "already-retired"
    if PROFILE_SIGNATURE not in text:
        # A quiet adapted tree never needed the temporary profiler. Prove its
        # exact handler and absence of diagnostic residue, then record the same
        # successor state used by actual retirement. dev89 requires this marker.
        if QUIET_HANDLE in text:
            residue = [token for token in (
                "m14_drawdone_profile_finish", "sM14DrawDoneProfile",
                "m14_drawdone_elapsed_ns",
            ) if token in text]
            if residue:
                raise RuntimeError("quiet DrawDone source has profiler residue: " + ", ".join(residue))
            quiet_retired = QUIET_HANDLE.replace(
                "  const auto fmt =", f"  // {RETIRED_SIGNATURE}.\n  const auto fmt =", 1)
            text = once(text, QUIET_HANDLE, quiet_retired, "quiet retirement marker")
            if not check_only:
                path.write_text(text, encoding="utf-8", newline="\n")
            return "verified" if check_only else "retired"
        # Truly fresh upstream source has not received OpenSPM's adapters yet.
        return "not-present"

    text = once(
        text,
        "static void handle_draw(u8 cmd, ByteReader& reader) noexcept;\n"
        "static void handle_aurora(ByteReader& reader) noexcept;\n"
        "static void m14_drawdone_profile_finish() noexcept;\n",
        "static void handle_draw(u8 cmd, ByteReader& reader) noexcept;\n"
        "static void handle_aurora(ByteReader& reader) noexcept;\n",
        "DrawDone profiler forward declaration retirement",
    )

    text = once(
        text,
        "      if (reg_get(value, 8, 24) == GX_BP_REG_DRAWDONE) {\n"
        "        m14_drawdone_profile_finish();\n"
        "        return {static_cast<u32>(reader.offset()), true};\n"
        "      }\n",
        "      if (reg_get(value, 8, 24) == GX_BP_REG_DRAWDONE) {\n"
        "        return {static_cast<u32>(reader.offset()), true};\n"
        "      }\n",
        "DrawDone profiler boundary retirement",
    )

    # Remove the diagnostic accumulator/helpers while retaining the declaration
    # that immediately followed them before the probe was installed.
    pattern = re.compile(
        r"struct M14DrawDoneProfile \{.*?\n\}\n\n"
        r"using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        re.DOTALL,
    )
    text, count = pattern.subn(
        "using GuestArrayRequirements = std::array<u32, MaxVtxAttr>;\n",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(
            f"DrawDone profiler globals retirement: expected one block, found {count}"
        )

    text = once(
        text,
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  const auto m14ArrayStageStart = std::chrono::steady_clock::now();\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "  DrawImmediateData immediates{.vtxStart = vertRange.offset, .currentPnMtx = state.currentPnMtx};\n"
        "  for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n",
        "array-stage timer start retirement",
    )
    text = once(
        text,
        "\n  sM14DrawDoneProfile.arrayStageNanos += m14_drawdone_elapsed_ns(m14ArrayStageStart);\n"
        "  const u8 lineMode = line_mode_for_prim(prim);\n",
        "\n  const u8 lineMode = line_mode_for_prim(prim);\n",
        "array-stage timer finish retirement",
    )

    text = once(
        text,
        "  if (!pipelineValid) {\n"
        "    const auto m14PipelineStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.pipelineCalls;\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "  if (!pipelineValid) {\n"
        "    const bool hadPipeline = cache.hasPipeline;\n",
        "pipeline timer start retirement",
    )
    text = once(
        text,
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "    sM14DrawDoneProfile.pipelineNanos += m14_drawdone_elapsed_ns(m14PipelineStart);\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "    if (!hadPipeline || prevSampledTextures != cache.shaderInfo.sampledTextures ||\n"
        "        prevSampledIndTextures != cache.shaderInfo.sampledIndTextures) {\n"
        "      cache.bindGeneration = 0;\n"
        "    }\n"
        "  }\n\n"
        "  const bool bindGroupsValid =\n",
        "pipeline timer finish retirement",
    )

    text = once(
        text,
        "  if (!bindGroupsValid) {\n"
        "    const auto m14BindStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.bindCalls;\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "  if (!bindGroupsValid) {\n"
        "    const auto prevBindGroup = cache.bindGroups.textureBindGroup;\n",
        "bind timer start retirement",
    )
    text = once(
        text,
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "    sM14DrawDoneProfile.bindNanos += m14_drawdone_elapsed_ns(m14BindStart);\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "    if (cache.bindGroups.textureBindGroup != prevBindGroup) {\n"
        "      state.dirty |= DirtyUniform;\n"
        "    }\n"
        "  }\n\n"
        "  const bool uniformValid =",
        "bind timer finish retirement",
    )

    text = once(
        text,
        "  if (!uniformValid) {\n"
        "    const auto m14UniformStart = std::chrono::steady_clock::now();\n"
        "    ++sM14DrawDoneProfile.uniformCalls;\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "    sM14DrawDoneProfile.uniformNanos += m14_drawdone_elapsed_ns(m14UniformStart);\n"
        "  }\n",
        "  if (!uniformValid) {\n"
        "    cache.uniformRange = build_uniform(cache.shaderInfo);\n"
        "    state.dirty &= ~DirtyUniform;\n"
        "  }\n",
        "uniform timer retirement",
    )

    text = once(
        text,
        "  const auto m14GuestScanStart = std::chrono::steady_clock::now();\n"
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n"
        "  sM14DrawDoneProfile.guestScanNanos += m14_drawdone_elapsed_ns(m14GuestScanStart);\n"
        "  ++sM14DrawDoneProfile.guestScanCalls;\n",
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "guest-index scan timer retirement",
    )

    text = once(
        text,
        "  const auto m14VertPushStart = std::chrono::steady_clock::now();\n"
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n"
        "  sM14DrawDoneProfile.vertPushNanos += m14_drawdone_elapsed_ns(m14VertPushStart);\n",
        "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n",
        "vertex-push timer retirement",
    )

    text = once(
        text,
        "  if (canMerge) {\n"
        "    ++sM14DrawDoneProfile.merged;\n"
        "    u32 numIndices = 0;\n",
        "  if (canMerge) {\n"
        "    u32 numIndices = 0;\n",
        "merge counter retirement",
    )
    text = once(
        text,
        "\n  ++sM14DrawDoneProfile.unmerged;\n"
        "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "\n  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n",
        "unmerged counter retirement",
    )

    text = once(text, PROFILED_HANDLE, QUIET_HANDLE, "draw handler retirement")

    # Leave a stable, zero-cost source marker so configure-time idempotence does
    # not depend on the historical quiet comment surviving future refactors.
    anchor = (
        "  // M14 normal-run draw profiler disabled: keep ordinary gameplay free of\n"
        "  // per-draw host-clock reads and high-volume aggregate log writes.\n"
    )
    text = once(
        text,
        anchor,
        anchor + f"  // {RETIRED_SIGNATURE}.\n",
        "retirement marker",
    )

    forbidden = (
        PROFILE_SIGNATURE,
        "m14_drawdone_profile_finish",
        "sM14DrawDoneProfile",
        "m14_drawdone_elapsed_ns",
    )
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise RuntimeError(
            "DrawDone profiler retirement left diagnostic symbols: "
            + ", ".join(remaining)
        )

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "retired"


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
            raise RuntimeError(
                f"Aurora revision mismatch: expected {expected}, got {actual}"
            )
        result = transform(source, args.check_only)
        print(
            {
                "already-retired": "Aurora DrawDone per-draw profiler already retired",
                "not-present": "Aurora DrawDone per-draw profiler not present",
                "verified": "Aurora DrawDone per-draw profiler retirement verified",
                "retired": "Aurora DrawDone per-draw profiler retired for normal play",
            }[result]
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(
            f"OpenSPM DrawDone profiler retirement failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
