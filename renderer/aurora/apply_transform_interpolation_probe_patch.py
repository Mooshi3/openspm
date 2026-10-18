#!/usr/bin/env python3
"""Add OpenSPM dev136 clean-room GX transform-history interpolation probe.

This is the first production interpolation scaffold after the dev132/dev135
cadence proof. It does not alter rendering yet. It records the renderer-visible
GX transform state used by each draw, pairs stable draw identities across
consecutive completed Wii display-copy frames, and validates whether a safe
sub-frame transform can be generated.

The probe is gated by OPENSPM_M14_INTERP_PROBE=1 and is intentionally built on
the pinned MIT Aurora tree plus OpenSPM's existing adaptations. No WiiCompiled
source is copied; only the generic architecture (per-draw transform history and
sealed-frame replay) is used as an external design reference.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 dev136 GX transform interpolation probe active"
SUCCESSOR_SIGNATURE = "M14 dev138 geometry-identity interpolation probe active"

CP = pathlib.Path("lib/gx/command_processor.cpp")
CP_H = pathlib.Path("lib/gx/command_processor.hpp")
REGS = pathlib.Path("lib/gx/regs.cpp")

CP_INCLUDE_OLD = "#include <cstdint>\n"
CP_INCLUDE_NEW = "#include <cstdint>\n#include <cstdlib>\n#include <unordered_map>\n"

CP_HEADER_OLD = "void clear_draw_cache() noexcept;\n"
CP_HEADER_NEW = """void clear_draw_cache() noexcept;
void m14_interp_probe_frame_boundary() noexcept;
"""

PUSH_MARKER = "static void push_gx_draw("

PROBE_IMPL = r"""struct M14InterpProbeDraw {
  u64 identity = 0;
  Mat4x4<float> projection{};
  std::array<PnMtx, MaxPnMtx> matrices{};
  u32 currentPnMtx = 0;
  bool indexedMatrices = false;
};

std::vector<M14InterpProbeDraw> sM14InterpPrevious;
std::vector<M14InterpProbeDraw> sM14InterpCurrent;
u64 sM14InterpFrameCount = 0;
bool sM14InterpProbeReported = false;

bool m14_interp_probe_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_INTERP_PROBE");
    return value != nullptr && value[0] == '1' && value[1] == '\0';
  }();
  return enabled;
}

u64 m14_interp_mix(u64 seed, u64 value) noexcept {
  value ^= value >> 30;
  value *= UINT64_C(0xBF58476D1CE4E5B9);
  value ^= value >> 27;
  value *= UINT64_C(0x94D049BB133111EB);
  value ^= value >> 31;
  return seed ^ (value + UINT64_C(0x9E3779B97F4A7C15) + (seed << 6) + (seed >> 2));
}

bool m14_interp_finite(const Mat3x4<float>& matrix) noexcept {
  const std::array<Vec4<float>, 3> rows{matrix.m0, matrix.m1, matrix.m2};
  for (const auto& row : rows) {
    for (u32 column = 0; column < 4; ++column) {
      if (!std::isfinite(row[column])) return false;
    }
  }
  return true;
}

bool m14_interp_finite(const Mat4x4<float>& matrix) noexcept {
  const std::array<Vec4<float>, 4> rows{matrix.m0, matrix.m1, matrix.m2, matrix.m3};
  for (const auto& row : rows) {
    for (u32 column = 0; column < 4; ++column) {
      if (!std::isfinite(row[column])) return false;
    }
  }
  return true;
}

bool m14_interp_pair_safe(const M14InterpProbeDraw& previous,
                          const M14InterpProbeDraw& current) noexcept {
  if (previous.currentPnMtx >= MaxPnMtx || current.currentPnMtx >= MaxPnMtx) return false;
  if (!m14_interp_finite(previous.projection) || !m14_interp_finite(current.projection)) return false;
  const auto& a = previous.matrices[previous.currentPnMtx].pos;
  const auto& b = current.matrices[current.currentPnMtx].pos;
  if (!m14_interp_finite(a) || !m14_interp_finite(b)) return false;
  const float dx = b.m0.w() - a.m0.w();
  const float dy = b.m1.w() - a.m1.w();
  const float dz = b.m2.w() - a.m2.w();
  const float distanceSquared = dx * dx + dy * dy + dz * dz;
  return std::isfinite(distanceSquared) && distanceSquared <= 1500.0f * 1500.0f;
}

void m14_interp_probe_record_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
                                  gfx::PipelineRef pipeline,
                                  gfx::BindGroupRef textureBindGroup) noexcept {
  if (!m14_interp_probe_enabled()) return;
  if (!sM14InterpProbeReported) {
    Log.info("M14 dev136 GX transform interpolation probe active: authoritative Wii simulation unchanged; "
             "renderer-visible projection/position/normal history is paired across completed GXCopyDisp frames.");
    sM14InterpProbeReported = true;
  }

  const auto& state = g_gxState;
  const bool indexed = state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE;
  u64 identity = UINT64_C(0xCBF29CE484222325);
  identity = m14_interp_mix(identity, static_cast<u64>(pipeline));
  identity = m14_interp_mix(identity, static_cast<u64>(textureBindGroup));
  identity = m14_interp_mix(identity, static_cast<u64>(prim));
  identity = m14_interp_mix(identity, static_cast<u64>(fmt));
  identity = m14_interp_mix(identity, static_cast<u64>(vtxCount));
  identity = m14_interp_mix(identity, static_cast<u64>(state.projType));
  identity = m14_interp_mix(identity, indexed ? 1u : 0u);

  M14InterpProbeDraw snapshot;
  snapshot.identity = identity;
  snapshot.projection = state.proj;
  snapshot.matrices = state.pnMtx;
  snapshot.currentPnMtx = state.currentPnMtx;
  snapshot.indexedMatrices = indexed;
  sM14InterpCurrent.emplace_back(std::move(snapshot));
}

void m14_interp_probe_finish_frame() noexcept {
  if (!m14_interp_probe_enabled()) return;
  ++sM14InterpFrameCount;

  u64 matches = 0;
  u64 moving = 0;
  u64 safeMidpoints = 0;
  u64 indexedMatches = 0;
  u64 rigidMatches = 0;

  if (!sM14InterpPrevious.empty() && !sM14InterpCurrent.empty()) {
    std::unordered_map<u64, std::vector<size_t>> previousByIdentity;
    previousByIdentity.reserve(sM14InterpPrevious.size());
    for (size_t i = 0; i < sM14InterpPrevious.size(); ++i) {
      previousByIdentity[sM14InterpPrevious[i].identity].push_back(i);
    }
    std::unordered_map<u64, size_t> consumed;
    consumed.reserve(previousByIdentity.size());

    for (const auto& current : sM14InterpCurrent) {
      const auto found = previousByIdentity.find(current.identity);
      if (found == previousByIdentity.end()) continue;
      size_t& occurrence = consumed[current.identity];
      if (occurrence >= found->second.size()) continue;
      const auto& previous = sM14InterpPrevious[found->second[occurrence++]];
      ++matches;
      if (current.indexedMatrices || previous.indexedMatrices) {
        ++indexedMatches;
      } else {
        ++rigidMatches;
      }

      const bool changed =
          std::memcmp(&previous.projection, &current.projection, sizeof(current.projection)) != 0 ||
          std::memcmp(&previous.matrices, &current.matrices, sizeof(current.matrices)) != 0;
      if (changed) ++moving;
      if (m14_interp_pair_safe(previous, current)) ++safeMidpoints;
    }
  }

  const bool report = sM14InterpFrameCount <= 8u || (sM14InterpFrameCount % 120u) == 0u;
  if (report) {
    const u64 candidates = sM14InterpCurrent.size();
    const double matchPercent =
        candidates != 0 ? (100.0 * static_cast<double>(matches) / static_cast<double>(candidates)) : 0.0;
    const double safePercent =
        matches != 0 ? (100.0 * static_cast<double>(safeMidpoints) / static_cast<double>(matches)) : 0.0;
    Log.info("M14 dev136 interpolation pairing frame={} candidates={} previous={} matches={} match_pct={:.1f} "
             "moving={} safe_midpoints={} safe_pct={:.1f} rigid_matches={} indexed_matches={}",
             sM14InterpFrameCount, candidates, sM14InterpPrevious.size(), matches, matchPercent,
             moving, safeMidpoints, safePercent, rigidMatches, indexedMatches);
  }

  sM14InterpPrevious.swap(sM14InterpCurrent);
  sM14InterpCurrent.clear();
}
"""

RECORD_OLD = """  const bool uniformValid = (state.dirty & DirtyUniform) == 0 && cache.uniformRange.size != 0;
"""
RECORD_NEW = """  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, cache.bindGroups.textureBindGroup);

  const bool uniformValid = (state.dirty & DirtyUniform) == 0 && cache.uniformRange.size != 0;
"""

CLEAR_OLD = """void clear_draw_cache() noexcept {
"""
CLEAR_NEW = """void m14_interp_probe_frame_boundary() noexcept { m14_interp_probe_finish_frame(); }

void clear_draw_cache() noexcept {
"""

REGS_INCLUDE_OLD = '#include "regs.hpp"\n'
REGS_INCLUDE_NEW = '#include "regs.hpp"\n#include "command_processor.hpp"\n'

REGS_BOUNDARY_OLD = """    copy_tex(g_gxState.texCopyDest, clear);
    raw::notify_display_copy(g_gxState.texCopyDest);
"""
REGS_BOUNDARY_NEW = """    copy_tex(g_gxState.texCopyDest, clear);
    raw::notify_display_copy(g_gxState.texCopyDest);
    m14_interp_probe_frame_boundary();
"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / CP
    cp_h_path = source / CP_H
    regs_path = source / REGS
    cp = cp_path.read_text(encoding="utf-8")
    cp_h = cp_h_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")

    # dev138 supersedes this scaffold. Its canonicalization intentionally
    # replaces the dev136 signature/helper block, so treating that absence as
    # "not installed" would re-apply dev136 on every CMake configure and
    # accumulate GXCopyDisp boundary calls in regs.cpp.
    if SUCCESSOR_SIGNATURE in cp:
        required = (
            "m14_interp_probe_record_draw",
            "m14_interp_probe_frame_boundary",
        )
        joined = cp + "\n" + cp_h + "\n" + regs
        missing = [fragment for fragment in required if fragment not in joined]
        if missing:
            raise RuntimeError(
                "dev138 successor interpolation seam incomplete before dev136 no-op: "
                + ", ".join(missing)
            )
        return "already-applied"

    if SIGNATURE in cp:
        required = (
            'std::getenv("OPENSPM_M14_INTERP_PROBE")',
            "m14_interp_probe_record_draw",
            "m14_interp_probe_frame_boundary",
            "safe_midpoints",
            "m14_interp_probe_frame_boundary();",
        )
        joined = cp + "\n" + cp_h + "\n" + regs
        missing = [fragment for fragment in required if fragment not in joined]
        if missing:
            raise RuntimeError("dev136 interpolation probe signature incomplete: " + ", ".join(missing))
        return "already-applied"

    if "M14 raw guest-array fused scan-plan active" not in cp:
        raise RuntimeError("dev109 guest-array hotpath must be applied before dev136")
    if "OpenSPM M14 Wii display-copy XFB snapshot active" not in regs:
        raise RuntimeError("display-copy snapshot migration must be applied before dev136")

    if "#include <cstdlib>\n" not in cp:
        cp = replace_once(cp, CP_INCLUDE_OLD, CP_INCLUDE_NEW, "command_processor interpolation includes")
    cp_h = replace_once(cp_h, CP_HEADER_OLD, CP_HEADER_NEW, "command_processor interpolation declaration")
    push_count = cp.count(PUSH_MARKER)
    if push_count != 1:
        raise RuntimeError(
            f"command_processor interpolation helper insertion: expected one push_gx_draw, found {push_count}"
        )
    push_at = cp.index(PUSH_MARKER)
    cp = cp[:push_at] + PROBE_IMPL + "\n" + cp[push_at:]
    cp = replace_once(cp, RECORD_OLD, RECORD_NEW, "command_processor interpolation draw capture")
    cp = replace_once(cp, CLEAR_OLD, CLEAR_NEW, "command_processor interpolation frame boundary")

    if '#include "command_processor.hpp"\n' not in regs:
        regs = replace_once(regs, REGS_INCLUDE_OLD, REGS_INCLUDE_NEW, "regs interpolation include")
    regs = replace_once(regs, REGS_BOUNDARY_OLD, REGS_BOUNDARY_NEW, "GXCopyDisp interpolation boundary")

    joined = cp + "\n" + cp_h + "\n" + regs
    required = (
        SIGNATURE,
        'std::getenv("OPENSPM_M14_INTERP_PROBE")',
        "m14_interp_probe_record_draw",
        "m14_interp_probe_finish_frame",
        "m14_interp_probe_frame_boundary",
        "safe_midpoints",
    )
    missing = [fragment for fragment in required if fragment not in joined]
    if missing:
        raise RuntimeError("dev136 interpolation migration ended incomplete: " + ", ".join(missing))

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        cp_h_path.write_text(cp_h, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora dev136 interpolation probe adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora dev136 transform interpolation probe already applied",
        "verified": "Aurora dev136 transform interpolation probe verified",
        "applied": "Aurora dev136 transform interpolation probe applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
