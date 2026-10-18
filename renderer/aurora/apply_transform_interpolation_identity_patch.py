#!/usr/bin/env python3
"""Add OpenSPM dev138 geometry-identity interpolation probe.

dev137 proved two things by failing visibly:
  * rebuilding a second full GX uniform block for every matched draw destroys
    the authoritative producer's frame time in heavy scenes;
  * material + nearest-transform correspondence is not a safe object identity.

dev138 is observation-only. It leaves the 60 Hz draw output untouched, hashes
the final staged vertex/index bytes for each logical GX draw (including merged
draws), and defers cross-frame matching to GXCopyDisp. Exact geometry+material
matches are preferred; repeated exact identities are matched only when the
nearest full transform is unambiguous. A deliberately conservative unique
material fallback is measured separately for geometry-changing draws.

No interpolation uniform is built and no DrawData/pipeline ABI is changed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 dev138 geometry-identity interpolation probe active"

CP = pathlib.Path("lib/gx/command_processor.cpp")
CP_H = pathlib.Path("lib/gx/command_processor.hpp")
REGS = pathlib.Path("lib/gx/regs.cpp")
REC_H = pathlib.Path("lib/gfx/recording.hpp")
REC_CPP = pathlib.Path("lib/gfx/recording.cpp")

STRUCT_MARKER = "struct M14InterpProbeDraw {"
RECORD_START = "void m14_interp_probe_record_draw("
FINISH_START = "void m14_interp_probe_finish_frame() noexcept {"

DEV138_HELPERS = r"""
u64 m14_interp_geometry_signature(u64 vertexSignature, u64 indexSignature,
                                  u32 geometryBytes) noexcept {
  u64 hash = m14_interp_mix(vertexSignature, indexSignature);
  return m14_interp_mix(hash, static_cast<u64>(geometryBytes));
}

u64 m14_interp_material_identity(GXPrimitive prim, GXVtxFmt fmt,
                                 gfx::PipelineRef pipeline,
                                 gfx::BindGroupRef textureBindGroup,
                                 bool indexedMatrices) noexcept {
  u64 identity = UINT64_C(0xBB67AE8584CAA73B);
  identity = m14_interp_mix(identity, static_cast<u64>(pipeline));
  identity = m14_interp_mix(identity, static_cast<u64>(textureBindGroup));
  identity = m14_interp_mix(identity, static_cast<u64>(prim));
  identity = m14_interp_mix(identity, static_cast<u64>(fmt));
  identity = m14_interp_mix(identity, static_cast<u64>(g_gxState.projType));
  identity = m14_interp_mix(identity, indexedMatrices ? 1u : 0u);
  return identity;
}

u64 m14_interp_combined_identity(u64 materialIdentity, u64 geometrySignature,
                                 u32 geometryBytes) noexcept {
  u64 identity = m14_interp_mix(materialIdentity, geometrySignature);
  return m14_interp_mix(identity, static_cast<u64>(geometryBytes));
}

double m14_interp_full_transform_distance(const M14InterpProbeDraw& previous,
                                          const M14InterpProbeDraw& current) noexcept {
  if (previous.currentPnMtx >= MaxPnMtx || current.currentPnMtx >= MaxPnMtx ||
      previous.currentPnMtx != current.currentPnMtx) {
    return std::numeric_limits<double>::infinity();
  }
  const auto& a = previous.matrices[previous.currentPnMtx].pos;
  const auto& b = current.matrices[current.currentPnMtx].pos;
  if (!m14_interp_finite(a) || !m14_interp_finite(b)) {
    return std::numeric_limits<double>::infinity();
  }

  const std::array<Vec4<float>, 3> aRows{a.m0, a.m1, a.m2};
  const std::array<Vec4<float>, 3> bRows{b.m0, b.m1, b.m2};
  double translation = 0.0;
  for (u32 row = 0; row < 3; ++row) {
    const double delta =
        static_cast<double>(bRows[row].w()) - static_cast<double>(aRows[row].w());
    translation += delta * delta;
  }
  double basis = 0.0;
  for (u32 row = 0; row < 3; ++row) {
    for (u32 column = 0; column < 3; ++column) {
      const double delta =
          static_cast<double>(bRows[row][column]) - static_cast<double>(aRows[row][column]);
      basis += delta * delta;
    }
  }
  // Translation is the dominant object-instance discriminator. Basis distance
  // breaks ties between nearby instances without allowing rotation to swamp
  // ordinary SPM per-tick motion.
  return translation * 1024.0 + basis;
}

bool m14_interp_unambiguous(double best, double second) noexcept {
  if (!std::isfinite(best)) return false;
  if (!std::isfinite(second)) return true;
  constexpr double AbsoluteMargin = 1.0e-6;
  constexpr double RelativeMargin = 1.05;
  return second > best * RelativeMargin + AbsoluteMargin;
}

void m14_interp_probe_extend_last_geometry(gfx::Range appendedVertRange,
                                           gfx::Range appendedIdxRange,
                                           u32 vtxCount) noexcept {
  if (!m14_interp_probe_enabled() || !sM14InterpCaptureActive ||
      sM14InterpCurrent.empty()) return;
  auto& snapshot = sM14InterpCurrent.back();
  if (!gfx::m14_hash_recorded_vertex_range(appendedVertRange, snapshot.vertexSignature) ||
      !gfx::m14_hash_recorded_index_range(appendedIdxRange, snapshot.indexSignature)) {
    snapshot.geometrySignature = 0u;
    snapshot.combinedIdentity = 0u;
    return;
  }
  snapshot.geometryBytes += appendedVertRange.size + appendedIdxRange.size;
  snapshot.vtxCount = vtxCount;
  snapshot.geometrySignature =
      m14_interp_geometry_signature(snapshot.vertexSignature, snapshot.indexSignature,
                                    snapshot.geometryBytes);
  snapshot.combinedIdentity =
      m14_interp_combined_identity(snapshot.materialIdentity, snapshot.geometrySignature,
                                   snapshot.geometryBytes);
}
"""

DEV138_RECORD = r"""void m14_interp_probe_record_draw(
    GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
    gfx::PipelineRef pipeline, gfx::BindGroupRef textureBindGroup,
    gfx::Range vertRange, gfx::Range idxRange) noexcept {
  if (!m14_interp_probe_enabled() || !sM14InterpCaptureActive) return;
  if (!sM14InterpProbeReported) {
    Log.info("M14 dev138 geometry-identity interpolation probe active: sampled two adjacent "
             "frames every {} display copies; authoritative output remains 60 Hz; ordinary "
             "frames do no geometry hashing/matching; no visual substitution.",
             m14_interp_probe_interval());
    sM14InterpProbeReported = true;
  }

  const auto& state = g_gxState;
  const bool indexed = state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE;
  const u64 material =
      m14_interp_material_identity(prim, fmt, pipeline, textureBindGroup, indexed);
  M14InterpProbeDraw snapshot;
  snapshot.materialIdentity = material;
  snapshot.vertexSignature = M14InterpHashOffset;
  snapshot.indexSignature = M14InterpHashOffset;
  snapshot.geometryBytes = vertRange.size + idxRange.size;
  const bool geometryValid =
      gfx::m14_hash_recorded_vertex_range(vertRange, snapshot.vertexSignature) &&
      gfx::m14_hash_recorded_index_range(idxRange, snapshot.indexSignature);
  snapshot.geometrySignature =
      geometryValid
          ? m14_interp_geometry_signature(snapshot.vertexSignature, snapshot.indexSignature,
                                          snapshot.geometryBytes)
          : 0u;
  snapshot.combinedIdentity =
      geometryValid
          ? m14_interp_combined_identity(material, snapshot.geometrySignature,
                                         snapshot.geometryBytes)
          : 0u;
  snapshot.vtxCount = vtxCount;
  snapshot.projection = state.proj;
  snapshot.matrices = state.pnMtx;
  snapshot.currentPnMtx = state.currentPnMtx;
  snapshot.indexedMatrices = indexed;
  sM14InterpCurrent.emplace_back(std::move(snapshot));
}
"""

DEV138_FINISH = r"""void m14_interp_probe_finish_frame() noexcept {
  if (!m14_interp_probe_enabled()) return;
  ++sM14InterpFrameCount;

  if (!sM14InterpCaptureActive) {
    if (sM14InterpCaptureCooldown > 0u) --sM14InterpCaptureCooldown;
    if (sM14InterpCaptureCooldown == 0u) {
      sM14InterpPrevious.clear();
      sM14InterpCurrent.clear();
      sM14InterpCaptureActive = true;
    }
    return;
  }

  // Do not burn the first half of a sample pair on startup/transition frames
  // that contain no GX draws. Once a non-empty frame arrives, keep it as the
  // immutable previous side and capture the immediately following frame.
  if (sM14InterpPrevious.empty()) {
    if (sM14InterpCurrent.empty()) return;
    sM14InterpPrevious.swap(sM14InterpCurrent);
    sM14InterpCurrent.clear();
    return;
  }

  struct MatchBucket {
    std::vector<size_t> indices;
    size_t currentCount = 0;
  };

  std::unordered_map<u64, MatchBucket> exactPrevious;
  std::unordered_map<u64, MatchBucket> materialPrevious;
  exactPrevious.reserve(sM14InterpPrevious.size());
  materialPrevious.reserve(sM14InterpPrevious.size());

  for (size_t i = 0; i < sM14InterpPrevious.size(); ++i) {
    const auto& draw = sM14InterpPrevious[i];
    if (draw.combinedIdentity != 0u) exactPrevious[draw.combinedIdentity].indices.push_back(i);
    materialPrevious[draw.materialIdentity].indices.push_back(i);
  }

  std::unordered_map<u64, size_t> exactCurrentCounts;
  std::unordered_map<u64, size_t> materialCurrentCounts;
  exactCurrentCounts.reserve(sM14InterpCurrent.size());
  materialCurrentCounts.reserve(sM14InterpCurrent.size());
  for (const auto& draw : sM14InterpCurrent) {
    if (draw.combinedIdentity != 0u) ++exactCurrentCounts[draw.combinedIdentity];
    ++materialCurrentCounts[draw.materialIdentity];
  }

  std::vector<u8> used(sM14InterpPrevious.size(), 0u);
  u64 exactUnique = 0;
  u64 exactDuplicateResolved = 0;
  u64 exactAmbiguous = 0;
  u64 materialFallbackUnique = 0;
  u64 unmatched = 0;
  u64 movingMatched = 0;
  u64 geometryHashFailures = 0;
  u64 duplicateGroups = 0;
  u64 indexedMatches = 0;
  double acceptedDistanceSum = 0.0;
  double acceptedDistanceMax = 0.0;

  for (const auto& [identity, bucket] : exactPrevious) {
    const auto currentIt = exactCurrentCounts.find(identity);
    if (bucket.indices.size() > 1u || (currentIt != exactCurrentCounts.end() && currentIt->second > 1u)) {
      ++duplicateGroups;
    }
  }

  for (const auto& current : sM14InterpCurrent) {
    if (current.combinedIdentity == 0u && current.geometryBytes != 0u) {
      ++geometryHashFailures;
    }

    size_t chosen = static_cast<size_t>(-1);
    double chosenDistance = std::numeric_limits<double>::infinity();
    bool exact = false;
    bool duplicateResolved = false;

    if (current.combinedIdentity != 0u) {
      const auto exactIt = exactPrevious.find(current.combinedIdentity);
      if (exactIt != exactPrevious.end()) {
        double secondDistance = std::numeric_limits<double>::infinity();
        size_t available = 0;
        for (const size_t index : exactIt->second.indices) {
          if (index >= used.size() || used[index] != 0u) continue;
          ++available;
          const double distance =
              m14_interp_full_transform_distance(sM14InterpPrevious[index], current);
          if (distance < chosenDistance) {
            secondDistance = chosenDistance;
            chosenDistance = distance;
            chosen = index;
          } else if (distance < secondDistance) {
            secondDistance = distance;
          }
        }
        if (chosen != static_cast<size_t>(-1)) {
          if (available == 1u) {
            exact = true;
          } else if (m14_interp_unambiguous(chosenDistance, secondDistance)) {
            exact = true;
            duplicateResolved = true;
          } else {
            chosen = static_cast<size_t>(-1);
            chosenDistance = std::numeric_limits<double>::infinity();
            ++exactAmbiguous;
          }
        }
      }
    }

    // Geometry-changing fallback is deliberately conservative for this probe:
    // material identity must be unique in BOTH adjacent frames.
    if (chosen == static_cast<size_t>(-1)) {
      const auto previousMaterial = materialPrevious.find(current.materialIdentity);
      const auto currentMaterial = materialCurrentCounts.find(current.materialIdentity);
      if (previousMaterial != materialPrevious.end() &&
          previousMaterial->second.indices.size() == 1u &&
          currentMaterial != materialCurrentCounts.end() &&
          currentMaterial->second == 1u) {
        const size_t index = previousMaterial->second.indices.front();
        if (index < used.size() && used[index] == 0u) {
          const double distance =
              m14_interp_full_transform_distance(sM14InterpPrevious[index], current);
          if (std::isfinite(distance)) {
            chosen = index;
            chosenDistance = distance;
            ++materialFallbackUnique;
          }
        }
      }
    }

    if (chosen == static_cast<size_t>(-1)) {
      ++unmatched;
      continue;
    }

    used[chosen] = 1u;
    if (exact) {
      if (duplicateResolved) ++exactDuplicateResolved;
      else ++exactUnique;
    }
    if (current.indexedMatrices || sM14InterpPrevious[chosen].indexedMatrices) ++indexedMatches;
    const auto& previous = sM14InterpPrevious[chosen];
    const bool changed =
        std::memcmp(&previous.projection, &current.projection, sizeof(current.projection)) != 0 ||
        std::memcmp(&previous.matrices, &current.matrices, sizeof(current.matrices)) != 0;
    if (changed) ++movingMatched;
    if (std::isfinite(chosenDistance)) {
      acceptedDistanceSum += chosenDistance;
      acceptedDistanceMax = std::max(acceptedDistanceMax, chosenDistance);
    }
  }

  const u64 accepted = exactUnique + exactDuplicateResolved + materialFallbackUnique;
  const u64 candidates = sM14InterpCurrent.size();
  const double acceptedPct =
      candidates != 0u ? 100.0 * static_cast<double>(accepted) / static_cast<double>(candidates) : 0.0;
  const double exactPct =
      candidates != 0u
          ? 100.0 * static_cast<double>(exactUnique + exactDuplicateResolved) /
                static_cast<double>(candidates)
          : 0.0;
  const double distanceAvg = accepted != 0u ? acceptedDistanceSum / static_cast<double>(accepted) : 0.0;

  ++sM14InterpSampleCount;
  Log.info("M14 dev138 identity sample={} frame={} interval={} candidates={} previous={} "
           "accepted={} accepted_pct={:.1f} exact={} exact_pct={:.1f} exact_unique={} "
           "duplicate_resolved={} ambiguous={} fallback_unique={} unmatched={} "
           "duplicate_groups={} moving={} indexed={} hash_failures={} "
           "distance_avg={:.3f} distance_max={:.3f}",
           sM14InterpSampleCount, sM14InterpFrameCount, m14_interp_probe_interval(),
           candidates, sM14InterpPrevious.size(), accepted, acceptedPct,
           exactUnique + exactDuplicateResolved, exactPct, exactUnique, exactDuplicateResolved,
           exactAmbiguous, materialFallbackUnique, unmatched, duplicateGroups, movingMatched,
           indexedMatches, geometryHashFailures, distanceAvg, acceptedDistanceMax);

  sM14InterpPrevious.clear();
  sM14InterpCurrent.clear();
  sM14InterpCaptureActive = false;
  sM14InterpCaptureCooldown = m14_interp_probe_interval() - 2u;
}
"""


DEV138_BASE = r"""constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);

struct M14InterpProbeDraw {
  u64 materialIdentity = 0;
  u64 vertexSignature = UINT64_C(1469598103934665603);
  u64 indexSignature = UINT64_C(1469598103934665603);
  u64 geometrySignature = 0;
  u64 combinedIdentity = 0;
  u32 geometryBytes = 0;
  u32 vtxCount = 0;
  Mat4x4<float> projection{};
  std::array<PnMtx, MaxPnMtx> matrices{};
  u32 currentPnMtx = 0;
  bool indexedMatrices = false;
};

std::vector<M14InterpProbeDraw> sM14InterpPrevious;
std::vector<M14InterpProbeDraw> sM14InterpCurrent;
u64 sM14InterpFrameCount = 0;
u64 sM14InterpSampleCount = 0;
u32 sM14InterpCaptureCooldown = 0;
bool sM14InterpCaptureActive = true;
bool sM14InterpProbeReported = false;

u32 m14_interp_probe_interval() noexcept {
  static const u32 interval = [] {
    const char* value = std::getenv("OPENSPM_M14_INTERP_PROBE_INTERVAL");
    if (value == nullptr || value[0] == '\0') return 300u;
    char* end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    if (end == value || *end != '\0') return 300u;
    return static_cast<u32>(std::clamp<unsigned long>(parsed, 2ul, 36000ul));
  }();
  return interval;
}

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
"""

DEV138_CANONICAL = DEV138_BASE + DEV138_HELPERS + "\n" + DEV138_RECORD + "\n" + DEV138_FINISH


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def braced_region(text: str, marker: str, label: str) -> tuple[int, int]:
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"{label}: marker missing")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise RuntimeError(f"{label}: braces are unbalanced")


def replace_function(text: str, marker: str, replacement: str, label: str) -> str:
    start, end = braced_region(text, marker, label)
    return text[:start] + replacement + text[end:]


def ensure_cpp_include(text: str, include: str) -> str:
    if include in text:
        return text
    anchor = "#include <cstdint>\n"
    if anchor not in text:
        raise RuntimeError(f"dev138 include anchor missing for {include.strip()}")
    return text.replace(anchor, anchor + include, 1)


def canonicalize_interpolation_region(text: str) -> str:
    """Replace every cumulative dev136/dev137/dev138 helper block with one dev138 block."""
    push = text.find("static void push_gx_draw(")
    if push < 0:
        raise RuntimeError("dev138 canonicalization: push_gx_draw missing")
    first = text.find("struct M14InterpProbeDraw {", 0, push)
    if first < 0:
        raise RuntimeError("dev138 canonicalization: dev136 interpolation scaffold missing")

    # DEV138_CANONICAL begins with the hash seed immediately before the probe
    # struct. Treat that seed as part of the replaceable interpolation region
    # instead of deleting it from the preserved prefix; deleting only the token
    # leaves its surrounding newline behind and makes successive passes differ
    # only by whitespace.
    seed = "constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);"
    region_start = first
    cursor = 0
    while True:
        at = text.find(seed, cursor, first)
        if at < 0:
            break
        region_start = min(region_start, at)
        cursor = at + len(seed)

    return text[:region_start] + DEV138_CANONICAL + "\n\n" + text[push:]


def normalize_boundary_surfaces(cp_h: str, regs: str) -> tuple[str, str]:
    """Collapse accumulated dev136 header/call-site seams to exactly one."""
    declaration = "void m14_interp_probe_frame_boundary() noexcept;\n"
    cp_h = cp_h.replace(declaration, "")
    header_anchor = "void clear_draw_cache() noexcept;\n"
    if header_anchor not in cp_h:
        raise RuntimeError("dev138 boundary surface cleanup: clear_draw_cache declaration missing")
    cp_h = cp_h.replace(header_anchor, header_anchor + declaration, 1)

    include = '#include "command_processor.hpp"\n'
    regs = regs.replace(include, "")
    include_anchor = '#include "regs.hpp"\n'
    if include_anchor not in regs:
        raise RuntimeError("dev138 boundary surface cleanup: regs include anchor missing")
    regs = regs.replace(include_anchor, include_anchor + include, 1)

    call = "    m14_interp_probe_frame_boundary();\n"
    regs = regs.replace(call, "")
    notify = "    raw::notify_display_copy(g_gxState.texCopyDest);\n"
    count = regs.count(notify)
    if count != 1:
        raise RuntimeError(
            f"dev138 boundary surface cleanup: expected one display-copy notify, found {count}"
        )
    regs = regs.replace(notify, notify + call, 1)
    return cp_h, regs


def normalize_frame_boundary(text: str) -> str:
    marker = "void m14_interp_probe_frame_boundary() noexcept"
    while marker in text:
        start, end = braced_region(text, marker, "dev138 frame-boundary duplicate repair")
        while end < len(text) and text[end] in "\r\n":
            end += 1
        text = text[:start] + text[end:]
    clear = text.find("void clear_draw_cache() noexcept {")
    if clear < 0:
        raise RuntimeError("dev138 frame-boundary repair: clear_draw_cache missing")
    wrapper = (
        "void m14_interp_probe_frame_boundary() noexcept { "
        "m14_interp_probe_finish_frame(); }\n\n"
    )
    return text[:clear] + wrapper + text[clear:]


def normalize_recording_header(rec_h: str) -> str:
    vertex_decl = "bool m14_hash_recorded_vertex_range(Range range, uint64_t& hash) noexcept;\n"
    index_decl = "bool m14_hash_recorded_index_range(Range range, uint64_t& hash) noexcept;\n"

    # An earlier abandoned dev138 draft exposed Aurora's anonymous-namespace
    # current_frame_packet() through recording.hpp. That creates a second
    # aurora::gfx declaration and makes every existing unqualified call inside
    # recording.cpp ambiguous. Remove that poisoned public declaration
    # unconditionally; the active design uses only the two narrow hash APIs.
    rec_h = re.sub(
        r"^[ \t]*(?:detail::)?FramePacket&[ \t]+current_frame_packet\(\);[ \t]*\r?\n",
        "",
        rec_h,
        flags=re.MULTILINE,
    )

    rec_h = rec_h.replace(vertex_decl, "")
    rec_h = rec_h.replace(index_decl, "")
    anchor = "Range push_indices(const uint8_t* data, size_t length, size_t alignment);\n"
    if anchor not in rec_h:
        raise RuntimeError("dev138 recording header: push_indices anchor missing")
    return rec_h.replace(anchor, anchor + vertex_decl + index_decl, 1)


def normalize_recording_cpp(rec_cpp: str) -> str:
    include = '#include "hash.hpp"\n'
    if include not in rec_cpp:
        anchor = '#include "frame.hpp"\n'
        if anchor not in rec_cpp:
            raise RuntimeError("dev138 recording cpp: frame include anchor missing")
        rec_cpp = rec_cpp.replace(anchor, anchor + include, 1)

    # Remove an earlier copy if this migration is being replayed.
    for marker in (
        "bool m14_hash_recorded_range(",
        "bool m14_hash_recorded_vertex_range(",
        "bool m14_hash_recorded_index_range(",
    ):
        while marker in rec_cpp:
            start, end = braced_region(rec_cpp, marker, "dev138 recording hash helper")
            while end < len(rec_cpp) and rec_cpp[end] in "\r\n":
                end += 1
            rec_cpp = rec_cpp[:start] + rec_cpp[end:]

    anchor = "} // namespace\n\nnamespace detail {\n"
    at = rec_cpp.find(anchor)
    if at < 0:
        raise RuntimeError("dev138 recording cpp: anonymous/detail namespace seam missing")
    insert_at = at + len("} // namespace\n")
    helpers = r"""
bool m14_hash_recorded_range(const ByteBuffer& bytes, Range range, uint64_t& hash) noexcept {
  const size_t begin = static_cast<size_t>(range.offset);
  const size_t size = static_cast<size_t>(range.size);
  if (begin > bytes.size() || size > bytes.size() - begin) return false;
  if (size != 0u) {
    hash = static_cast<uint64_t>(xxh3_hash_s(bytes.data() + begin, size, hash));
  }
  hash = static_cast<uint64_t>(xxh3_hash_s(&range.size, sizeof(range.size), hash));
  return true;
}

bool m14_hash_recorded_vertex_range(Range range, uint64_t& hash) noexcept {
  return m14_hash_recorded_range(g_recorder.frame().verts, range, hash);
}

bool m14_hash_recorded_index_range(Range range, uint64_t& hash) noexcept {
  return m14_hash_recorded_range(g_recorder.frame().indices, range, hash);
}

"""
    return rec_cpp[:insert_at] + helpers + rec_cpp[insert_at:]


def normalize_push_after_dev137(text: str) -> str:
    """Remove rejected dev137 locals/selection and install one canonical dev138 record call."""
    start, end = braced_region(text, "static void push_gx_draw(", "dev138 push cleanup")
    region = text[start:end]

    canonical_call_tail = "cache.bindGroups.textureBindGroup, vertRange, idxRange);"
    rejected_tokens = (
        "m14InterpUniformRange",
        "m14InterpPrevious",
        "m14_interp_visual_enabled",
        "m14_interp_apply_visual",
        "m14_interp_visual_alpha",
    )
    if (
        region.count("m14_interp_probe_record_draw(") == 1
        and canonical_call_tail in region
        and all(token not in region for token in rejected_tokens)
    ):
        return text

    # Remove every prior interpolation record statement, including dev137's
    # split m14InterpPrevious assignment form.
    while "m14_interp_probe_record_draw(" in region:
        pos = region.find("m14_interp_probe_record_draw(")
        line_start = region.rfind("\n", 0, pos) + 1
        previous_line_start = region.rfind("\n", 0, max(0, line_start - 1)) + 1
        if "m14InterpPrevious" in region[previous_line_start:line_start]:
            line_start = previous_line_start
        semi = region.find(";", pos)
        if semi < 0:
            close = region.find(")\n", pos)
            if close < 0:
                raise RuntimeError("dev138 push cleanup: unterminated interpolation record call")
            stmt_end = close + 1
        else:
            stmt_end = semi + 1
        region = region[:line_start] + region[stmt_end:]

    # Remove dev137's temporary midpoint range and the following visual block.
    marker = "gfx::Range m14InterpUniformRange{};"
    while marker in region:
        at = region.find(marker)
        line_start = region.rfind("\n", 0, at) + 1
        visual = region.find("if (m14_interp_visual_enabled()", at)
        if visual < 0:
            raise RuntimeError("dev138 push cleanup: midpoint range without visual block")
        brace = region.find("{", visual)
        if brace < 0:
            raise RuntimeError("dev138 push cleanup: visual block opening brace missing")
        depth = 0
        block_end = -1
        for i in range(brace, len(region)):
            if region[i] == "{":
                depth += 1
            elif region[i] == "}":
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
        if block_end < 0:
            raise RuntimeError("dev138 push cleanup: visual block is unbalanced")
        region = region[:line_start] + region[block_end:]

    # Restore zCompLoc's DrawData initializer from dev137's midpoint ternary.
    visual_uniform = re.compile(
        r"\.uniformRange\s*=\s*\(m14_interp_visual_enabled\(\)\s*&&\s*"
        r"m14InterpUniformRange\.size\s*!=\s*0\)\s*"
        r"\?\s*m14InterpUniformRange\s*:\s*cache\.uniformRange,"
    )
    region = visual_uniform.sub(".uniformRange = cache.uniformRange,", region)

    stale = (
        "m14InterpUniformRange",
        "m14InterpPrevious",
        "m14_interp_visual_enabled",
        "m14_interp_apply_visual",
        "m14_interp_visual_alpha",
    )
    leftovers = [token for token in stale if token in region]
    if leftovers:
        raise RuntimeError(
            "dev138 push cleanup left rejected dev137 state: " + ", ".join(leftovers)
        )

    anchor = "  const bool uniformValid ="
    at = region.find(anchor)
    if at < 0:
        raise RuntimeError("dev138 push cleanup: uniformValid anchor missing")
    call = (
        "  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, "
        "cache.bindGroups.textureBindGroup, vertRange, idxRange);\n\n"
    )
    region = region[:at] + call + region[at:]
    return text[:start] + region + text[end:]


def repair_record_call_semicolon(text: str) -> str:
    start, end = braced_region(text, "static void push_gx_draw(", "dev138 record-call repair")
    region = text[start:end]
    broken = "cache.bindGroups.textureBindGroup, vertRange, idxRange)\n"
    fixed = "cache.bindGroups.textureBindGroup, vertRange, idxRange);\n"
    region = region.replace(broken, fixed)
    return text[:start] + region + text[end:]


def patch_record_call(text: str) -> str:
    start, end = braced_region(text, "static void push_gx_draw(", "dev138 push_gx_draw")
    region = text[start:end]
    old = (
        "m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, "
        "cache.bindGroups.textureBindGroup);"
    )
    # dev137 formats this over two lines but the semantic tail is stable.
    call_start = region.find("m14_interp_probe_record_draw(")
    if call_start < 0:
        raise RuntimeError("dev138 push_gx_draw: interpolation record call missing")
    call_end = region.find(");", call_start)
    if call_end < 0:
        raise RuntimeError("dev138 push_gx_draw: interpolation record call unterminated")
    call_end += 2
    current_call = region[call_start:call_end]
    if "vertRange, idxRange" not in current_call:
        replacement = (
            "m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, "
            "cache.bindGroups.textureBindGroup, vertRange, idxRange);"
        )
        region = region[:call_start] + replacement + region[call_end:]
    return text[:start] + region + text[end:]


def patch_merge_refresh(text: str) -> str:
    start, end = braced_region(text, "static void draw_prim(", "dev138 draw_prim")
    region = text[start:end]
    signature = "m14_interp_probe_extend_last_geometry(vertRange, idxRange, lastDraw->vtxCount);"
    if signature in region:
        return text
    anchor = "    lastDraw->indexCount += numIndices;\n"
    if anchor not in region:
        raise RuntimeError("dev138 draw_prim: merged draw counter anchor missing")
    region = region.replace(
        anchor,
        anchor + "    " + signature + "\n",
        1,
    )
    return text[:start] + region + text[end:]


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / CP
    cp_h_path = source / CP_H
    regs_path = source / REGS
    rec_h_path = source / REC_H
    rec_cpp_path = source / REC_CPP
    cp = cp_path.read_text(encoding="utf-8")
    cp_h = cp_h_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")
    rec_h = rec_h_path.read_text(encoding="utf-8")
    rec_cpp = rec_cpp_path.read_text(encoding="utf-8")

    # dev136 is the only prerequisite. dev137 is rejected and deliberately not
    # required: replace the entire cumulative interpolation helper region with
    # one canonical observation-only dev138 implementation.
    if "M14 dev136 GX transform interpolation probe active" not in cp and \
       "struct M14InterpProbeDraw {" not in cp:
        raise RuntimeError("dev136 interpolation scaffold must be present before dev138")

    cp = ensure_cpp_include(cp, "#include <cstdlib>\n")
    cp = ensure_cpp_include(cp, "#include <cstring>\n")
    cp = ensure_cpp_include(cp, "#include <limits>\n")
    cp = ensure_cpp_include(cp, "#include <unordered_map>\n")
    cp = cp.replace('#include "../gfx/frame_packet.hpp"\n', "")
    cp = canonicalize_interpolation_region(cp)
    cp = normalize_frame_boundary(cp)
    cp_h, regs = normalize_boundary_surfaces(cp_h, regs)
    cp = normalize_push_after_dev137(cp)
    rec_h = normalize_recording_header(rec_h)
    rec_cpp = normalize_recording_cpp(rec_cpp)
    cp = patch_merge_refresh(cp)

    required = (
        SIGNATURE,
        "M14 dev138 identity sample=",
        "m14_interp_probe_record_draw(prim, fmt, vtxCount",
        "cache.bindGroups.textureBindGroup, vertRange, idxRange);",
        "m14_interp_probe_extend_last_geometry(vertRange, idxRange",
        "gfx::m14_hash_recorded_vertex_range",
        "gfx::m14_hash_recorded_index_range",
    )
    joined = cp + "\n" + cp_h + "\n" + regs + "\n" + rec_h + "\n" + rec_cpp
    if "current_frame_packet" in rec_h:
        raise RuntimeError(
            "dev138 recording header cleanup failed: stale current_frame_packet declaration remains"
        )
    missing = [item for item in required if item not in joined]
    if missing:
        raise RuntimeError("dev138 migration incomplete: " + ", ".join(missing))

    forbidden = (
        "M14 dev137 configurable visual transform interpolation active",
        "m14InterpUniformRange",
        "m14_interp_visual_enabled",
        "sM14InterpPreviousByIdentity",
        "const auto* m14InterpPrevious",
        "current_frame_packet()",
    )
    stale = [item for item in forbidden if item in cp]
    if stale:
        raise RuntimeError("dev138 canonicalization left rejected dev137 state: " + ", ".join(stale))

    if cp_h.count("void m14_interp_probe_frame_boundary() noexcept;") != 1:
        raise RuntimeError("dev138 boundary surface cleanup left duplicate header declarations")
    if regs.count("    m14_interp_probe_frame_boundary();\n") != 1:
        raise RuntimeError("dev138 boundary surface cleanup left duplicate GXCopyDisp calls")

    if cp.count("constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);") != 1:
        raise RuntimeError("dev138 canonical uniqueness failed: M14InterpHashOffset seed is not unique")

    uniqueness = {
        "M14InterpProbeDraw": cp.count("struct M14InterpProbeDraw {"),
        "history": cp.count("std::vector<M14InterpProbeDraw> sM14InterpPrevious;"),
        "finish": cp.count("void m14_interp_probe_finish_frame() noexcept {"),
        "boundary": cp.count("void m14_interp_probe_frame_boundary() noexcept"),
        "record": cp.count("void m14_interp_probe_record_draw("),
    }
    bad = [f"{name}={count}" for name, count in uniqueness.items() if count != 1]
    if bad:
        raise RuntimeError("dev138 canonical uniqueness failed: " + ", ".join(bad))

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        cp_h_path.write_text(cp_h, encoding="utf-8", newline="\n")
        regs_path.write_text(regs, encoding="utf-8", newline="\n")
        rec_h_path.write_text(rec_h, encoding="utf-8", newline="\n")
        rec_cpp_path.write_text(rec_cpp, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora dev138 identity adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora dev138 geometry identity probe already applied",
        "verified": "Aurora dev138 geometry identity probe verified",
        "applied": "Aurora dev138 geometry identity probe applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
