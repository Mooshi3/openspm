#!/usr/bin/env python3
"""OpenSPM dev140a: prepare retained-draw interpolation replay data.

This is intentionally visual-neutral. It proves the parts that must be correct
before extra draw replay is allowed to touch pixels:

* lightweight adjacent-frame GX draw identity using sampled final geometry;
* conservative transform matching with duplicate disambiguation;
* the exact 100 Hz / 60 Hz phase pattern (0,.6 | .2,.8 | .4);
* one or two cloned uniform arenas with projection + pn-matrix patches; and
* FramePacket lifetime through Aurora's end-frame render-worker callback.

The authoritative encoder and dev139 retained-XFB presenter remain unchanged.
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

FRAME_PACKET = pathlib.Path("lib/gfx/frame_packet.hpp")
RECORDING_H = pathlib.Path("lib/gfx/recording.hpp")
RECORDING_CPP = pathlib.Path("lib/gfx/recording.cpp")
FRAME_H = pathlib.Path("lib/gfx/frame.hpp")
FRAME_CPP = pathlib.Path("lib/gfx/frame.cpp")
AURORA_CPP = pathlib.Path("lib/aurora.cpp")
PIPELINE_H = pathlib.Path("lib/gx/pipeline.hpp")
COMMAND_PROCESSOR = pathlib.Path("lib/gx/command_processor.cpp")

SIGNATURE = "M14 dev140 replay prep active"

PACKET_TYPE = r'''struct M14ReplayPhase {
  float alpha = 1.0f;
  uint32_t uniformBase = 0;
};

'''

PACKET_FIELDS = r'''  std::vector<M14ReplayPhase> m14ReplayPhases;
  uint32_t m14ReplayOriginalUniformBytes = 0;
  uint32_t m14ReplayMatchedUniforms = 0;
  uint32_t m14ReplaySnappedUniforms = 0;
  bool m14ReplayCameraValid = false;
  float m14ReplayCameraDx = 0.0f;
  float m14ReplayCameraDy = 0.0f;
  float m14ReplayCameraDz = 0.0f;
  bool m14ReplayCameraScreenValid = false;
  float m14ReplayCameraNdcDx = 0.0f;
  float m14ReplayCameraNdcDy = 0.0f;
  uint32_t m14ReplayCameraSamples = 0;
  uint32_t m14ReplayCameraConsensus = 0;
'''

DRAW_COMMAND_FIELDS = r'''  uint32_t m14ReplayUniformPrefixBytes = 16u;
  bool m14ReplayIndexedMatrices = false;
  bool m14ReplayGX = false;
'''

RECORDING_DECL = r'''void m14_prepare_interpolation_replay() noexcept;
void m14_push_gx_draw_command(gx::DrawData data, uint32_t uniformPrefixBytes,
                              bool indexedMatrices);
'''

STATE = r'''
struct M14ReplaySnapshot {
  bool valid = false;
  bool cameraPatchable = false;
  uint64_t identity = 0;
  Range uniformRange{};
  Mat4x4<float> projection{};
  Mat3x4<float> position{};
  uint32_t currentPnMtx = 0;
  uint32_t uniformPrefixBytes = sizeof(float) * 4u;
  size_t previousIndex = static_cast<size_t>(-1);
};

std::vector<M14ReplaySnapshot> sM14ReplayPrevious;
uint64_t sM14ReplayInterval = 0;
uint64_t sM14ReplayPreparedIntervals = 0;
uint64_t sM14ReplayNoMatchIntervals = 0;
uint64_t sM14ReplayArenaExhaustedIntervals = 0;
uint64_t sM14ReplayCadenceNumerator = 100u;
uint64_t sM14ReplayCadenceDenominator = 1u;
uint64_t sM14ReplayCadenceStep = 60u;
uint64_t sM14ReplayCadencePhase = 0u;
bool sM14ReplayCadenceInitialized = false;
bool sM14ReplayTimelineStarted = false;
bool sM14ReplayReported = false;

bool m14_replay_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_INTERP_REPLAY");
    return value != nullptr && value[0] == '1' && value[1] == '\0';
  }();
  return enabled;
}

bool m14_camera_present_enabled() noexcept {
  static const bool enabled = [] {
    const char* present = std::getenv("OPENSPM_M14_INTERP_PRESENT");
    const char* identity = std::getenv("OPENSPM_M14_INTERP_IDENTITY");
    return present != nullptr && present[0] == '1' &&
           !(identity != nullptr && identity[0] == '1');
  }();
  return enabled;
}

bool m14_replay_finite(const Mat3x4<float>& matrix) noexcept {
  const std::array<Vec4<float>, 3> rows{matrix.m0, matrix.m1, matrix.m2};
  for (const auto& row : rows) {
    for (uint32_t column = 0; column < 4; ++column) {
      if (!std::isfinite(row[column])) return false;
    }
  }
  return true;
}

bool m14_replay_finite(const Mat4x4<float>& matrix) noexcept {
  for (uint32_t row = 0; row < 4; ++row) {
    for (uint32_t column = 0; column < 4; ++column) {
      if (!std::isfinite(matrix[row][column])) return false;
    }
  }
  return true;
}

uint64_t m14_replay_mix(uint64_t seed, uint64_t value) noexcept {
  value ^= value >> 30;
  value *= UINT64_C(0xBF58476D1CE4E5B9);
  value ^= value >> 27;
  value *= UINT64_C(0x94D049BB133111EB);
  value ^= value >> 31;
  return seed ^ (value + UINT64_C(0x9E3779B97F4A7C15) + (seed << 6) + (seed >> 2));
}

template <typename Buffer>
bool m14_replay_sample_range(const Buffer& bytes, Range range,
                             uint64_t& hash) noexcept {
  const size_t begin = static_cast<size_t>(range.offset);
  const size_t size = static_cast<size_t>(range.size);
  if (begin > bytes.size() || size > bytes.size() - begin) return false;
  hash = m14_replay_mix(hash, static_cast<uint64_t>(range.size));
  if (size == 0u) return true;

  constexpr size_t Sample = 32u;
  const size_t sampleSize = std::min(Sample, size);
  hash = static_cast<uint64_t>(xxh3_hash_s(bytes.data() + begin, sampleSize, hash));
  if (size > sampleSize * 2u) {
    const size_t middle = begin + (size - sampleSize) / 2u;
    hash = static_cast<uint64_t>(xxh3_hash_s(bytes.data() + middle, sampleSize, hash));
  }
  if (size > sampleSize) {
    hash = static_cast<uint64_t>(
        xxh3_hash_s(bytes.data() + begin + size - sampleSize, sampleSize, hash));
  }
  return true;
}

size_t m14_replay_projection_offset(const M14ReplaySnapshot& snapshot) noexcept {
  return static_cast<size_t>(snapshot.uniformPrefixBytes);
}

size_t m14_replay_position_offset(const M14ReplaySnapshot& snapshot) noexcept {
  return m14_replay_projection_offset(snapshot) + sizeof(Mat4x4<float>);
}

size_t m14_replay_required_uniform_bytes(const M14ReplaySnapshot& snapshot) noexcept {
  if (snapshot.currentPnMtx >= gx::MaxPnMtx) return SIZE_MAX;
  return m14_replay_position_offset(snapshot) +
         (static_cast<size_t>(snapshot.currentPnMtx) + 1u) *
             sizeof(Mat3x4<float>);
}

size_t m14_replay_all_position_bytes(const M14ReplaySnapshot& snapshot) noexcept {
  return m14_replay_position_offset(snapshot) +
         static_cast<size_t>(gx::MaxPnMtx) * sizeof(Mat3x4<float>);
}

template <typename ReadFrame>
bool m14_replay_read_snapshot(const ReadFrame& frame, const gx::DrawData& data,
                              uint32_t uniformPrefixBytes,
                              bool indexedMatrices,
                              M14ReplaySnapshot& out) noexcept {
  // DrawData is exactly Aurora's 128-byte inline payload. Replay metadata lives
  // on the outer DrawCommand sidecar so the GX payload remains byte-for-byte
  // upstream-compatible.
  if (uniformPrefixBytes != sizeof(float) * 4u &&
      uniformPrefixBytes != sizeof(float) * 8u) return false;

  out.uniformPrefixBytes = uniformPrefixBytes;
  out.currentPnMtx = data.immediateData.currentPnMtx;
  out.uniformRange = data.uniformRange;

  const size_t uniformBegin = static_cast<size_t>(data.uniformRange.offset);
  const size_t uniformSize = static_cast<size_t>(data.uniformRange.size);
  if (uniformBegin > frame.uniforms.size() ||
      uniformSize > frame.uniforms.size() - uniformBegin) return false;

  const uint8_t* uniform = frame.uniforms.data() + uniformBegin;
  const size_t projectionOffset = m14_replay_projection_offset(out);
  const size_t positionOffset = m14_replay_position_offset(out);
  if (uniformSize < projectionOffset + sizeof(out.projection)) return false;
  std::memcpy(&out.projection, uniform + projectionOffset, sizeof(out.projection));
  if (!m14_replay_finite(out.projection)) return false;
  out.cameraPatchable =
      uniformSize >= m14_replay_all_position_bytes(out);
  if (indexedMatrices) return false;
  if (data.uniformRange.size < m14_replay_required_uniform_bytes(out)) return false;

  uint64_t identity = UINT64_C(0x6A09E667F3BCC909);
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.pipeline));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.bindGroups.textureBindGroup));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.vtxCount));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.indexCount));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.vertRange.size));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.idxRange.size));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(data.immediateData.currentPnMtx));
  identity = m14_replay_mix(identity, static_cast<uint64_t>(uniformPrefixBytes));
  if (!m14_replay_sample_range(frame.verts, data.vertRange, identity) ||
      !m14_replay_sample_range(frame.indices, data.idxRange, identity)) return false;

  out.identity = identity;
  out.uniformRange = data.uniformRange;
  out.currentPnMtx = data.immediateData.currentPnMtx;
  if (out.currentPnMtx >= gx::MaxPnMtx) return false;
  std::memcpy(&out.position,
              uniform + positionOffset +
                  static_cast<size_t>(out.currentPnMtx) * sizeof(Mat3x4<float>),
              sizeof(out.position));
  return m14_replay_finite(out.projection) && m14_replay_finite(out.position);
}

bool m14_replay_same_basis(const Mat3x4<float>& a,
                           const Mat3x4<float>& b) noexcept {
  const std::array<Vec4<float>, 3> ar{a.m0, a.m1, a.m2};
  const std::array<Vec4<float>, 3> br{b.m0, b.m1, b.m2};
  for (uint32_t row = 0; row < 3; ++row) {
    for (uint32_t column = 0; column < 3; ++column) {
      if (ar[row][column] != br[row][column]) return false;
    }
  }
  return true;
}

double m14_replay_distance(const M14ReplaySnapshot& previous,
                           const M14ReplaySnapshot& current) noexcept {
  if (previous.currentPnMtx >= gx::MaxPnMtx || current.currentPnMtx >= gx::MaxPnMtx ||
      previous.currentPnMtx != current.currentPnMtx) {
    return std::numeric_limits<double>::infinity();
  }
  // First production interpolation is translation-only. Any projection change
  // (camera cut/FOV/flip) or active basis change (rotation/scale) snaps instead
  // of combining an old translation with a new coordinate frame.
  if (std::memcmp(&previous.projection, &current.projection,
                  sizeof(previous.projection)) != 0) {
    return std::numeric_limits<double>::infinity();
  }
  const auto& a = previous.position;
  const auto& b = current.position;
  if (!m14_replay_same_basis(a, b)) {
    return std::numeric_limits<double>::infinity();
  }
  if (!m14_replay_finite(a) || !m14_replay_finite(b)) {
    return std::numeric_limits<double>::infinity();
  }
  const double dx = static_cast<double>(b.m0.w() - a.m0.w());
  const double dy = static_cast<double>(b.m1.w() - a.m1.w());
  const double dz = static_cast<double>(b.m2.w() - a.m2.w());
  return (dx * dx + dy * dy + dz * dz) * 1024.0;
}

bool m14_replay_matrix_safe(const Mat3x4<float>& previous,
                            const Mat3x4<float>& current) noexcept {
  if (!m14_replay_finite(previous) || !m14_replay_finite(current)) return false;
  const float dx = current.m0.w() - previous.m0.w();
  const float dy = current.m1.w() - previous.m1.w();
  const float dz = current.m2.w() - previous.m2.w();
  constexpr float MaxTranslationPerTick = 256.0f;
  return dx * dx + dy * dy + dz * dz <= MaxTranslationPerTick * MaxTranslationPerTick;
}

struct M14ReplayCameraDelta {
  bool valid = false;
  float x = 0.0f;
  float y = 0.0f;
  float z = 0.0f;
  bool screenValid = false;
  float ndcX = 0.0f;
  float ndcY = 0.0f;
  uint32_t samples = 0u;
  uint32_t consensus = 0u;
  uint32_t screenSamples = 0u;
  bool worldProjectionValid = false;
  Mat4x4<float> worldProjection{};
  uint32_t worldProjectionConsensus = 0u;
};

bool m14_replay_project_origin_ndc(const M14ReplaySnapshot& snapshot,
                                   float& x, float& y) noexcept {
  if (!snapshot.valid || !m14_replay_finite(snapshot.projection) ||
      !m14_replay_finite(snapshot.position)) return false;
  const float ex = snapshot.position.m0.w();
  const float ey = snapshot.position.m1.w();
  const float ez = snapshot.position.m2.w();
  const auto& p = snapshot.projection;
  const float clipX = ex * p.m0.x() + ey * p.m0.y() + ez * p.m0.z() + p.m0.w();
  const float clipY = ex * p.m1.x() + ey * p.m1.y() + ez * p.m1.z() + p.m1.w();
  const float clipW = ex * p.m3.x() + ey * p.m3.y() + ez * p.m3.z() + p.m3.w();
  if (!std::isfinite(clipX) || !std::isfinite(clipY) || !std::isfinite(clipW) ||
      std::abs(clipW) < 1.0e-6f) return false;
  x = clipX / clipW;
  y = clipY / clipW;
  return std::isfinite(x) && std::isfinite(y);
}

M14ReplayCameraDelta m14_replay_estimate_camera_delta(
    const std::vector<M14ReplaySnapshot>& previous,
    const std::vector<M14ReplaySnapshot>& current) noexcept {
  std::vector<float> xs, ys, zs, ndcXs, ndcYs;
  std::vector<size_t> sampleCurrentIndices;
  xs.reserve(current.size());
  ys.reserve(current.size());
  zs.reserve(current.size());
  ndcXs.reserve(current.size());
  ndcYs.reserve(current.size());
  sampleCurrentIndices.reserve(current.size());
  for (size_t currentIndex = 0; currentIndex < current.size(); ++currentIndex) {
    const auto& snapshot = current[currentIndex];
    if (!snapshot.valid || snapshot.previousIndex >= previous.size()) continue;
    const auto& old = previous[snapshot.previousIndex];
    if (!m14_replay_matrix_safe(old.position, snapshot.position)) continue;
    xs.push_back(snapshot.position.m0.w() - old.position.m0.w());
    ys.push_back(snapshot.position.m1.w() - old.position.m1.w());
    zs.push_back(snapshot.position.m2.w() - old.position.m2.w());
    sampleCurrentIndices.push_back(currentIndex);
    float oldX = 0.0f, oldY = 0.0f, curX = 0.0f, curY = 0.0f;
    if (m14_replay_project_origin_ndc(old, oldX, oldY) &&
        m14_replay_project_origin_ndc(snapshot, curX, curY)) {
      ndcXs.push_back(curX - oldX);
      ndcYs.push_back(curY - oldY);
    } else {
      ndcXs.push_back(std::numeric_limits<float>::quiet_NaN());
      ndcYs.push_back(std::numeric_limits<float>::quiet_NaN());
    }
  }

  M14ReplayCameraDelta out{};
  out.samples = static_cast<uint32_t>(xs.size());
  // Camera motion is global. Demand enough independently matched draws that a
  // single animated actor cannot masquerade as the camera.
  if (xs.size() < 6u) return out;

  const auto median = [](std::vector<float> values) {
    const size_t middle = values.size() / 2u;
    std::nth_element(values.begin(), values.begin() + middle, values.end());
    return values[middle];
  };
  const float mx = median(xs);
  const float my = median(ys);
  const float mz = median(zs);

  constexpr float MaxConsensusError = 0.25f;
  constexpr float MaxConsensusError2 = MaxConsensusError * MaxConsensusError;
  double sumX = 0.0, sumY = 0.0, sumZ = 0.0;
  uint32_t consensus = 0u;
  std::vector<float> consensusNdcX, consensusNdcY;
  std::unordered_map<uint64_t, std::pair<uint32_t, size_t>> projectionCounts;
  consensusNdcX.reserve(xs.size());
  consensusNdcY.reserve(xs.size());
  projectionCounts.reserve(8u);
  for (size_t i = 0; i < xs.size(); ++i) {
    const float dx = xs[i] - mx;
    const float dy = ys[i] - my;
    const float dz = zs[i] - mz;
    if (dx * dx + dy * dy + dz * dz > MaxConsensusError2) continue;
    sumX += xs[i];
    sumY += ys[i];
    sumZ += zs[i];
    if (i < ndcXs.size() && std::isfinite(ndcXs[i]) && std::isfinite(ndcYs[i])) {
      consensusNdcX.push_back(ndcXs[i]);
      consensusNdcY.push_back(ndcYs[i]);
    }
    if (i < sampleCurrentIndices.size()) {
      const size_t currentIndex = sampleCurrentIndices[i];
      if (currentIndex < current.size()) {
        const auto& projection = current[currentIndex].projection;
        const uint64_t projectionHash = static_cast<uint64_t>(
            xxh3_hash_s(&projection, sizeof(projection), UINT64_C(0x1451451451451451)));
        auto& bucket = projectionCounts[projectionHash];
        ++bucket.first;
        bucket.second = currentIndex;
      }
    }
    ++consensus;
  }

  out.consensus = consensus;
  if (consensus < 4u || static_cast<size_t>(consensus) * 2u < xs.size()) return out;
  out.x = static_cast<float>(sumX / consensus);
  out.y = static_cast<float>(sumY / consensus);
  out.z = static_cast<float>(sumZ / consensus);
  const float magnitude2 = out.x * out.x + out.y * out.y + out.z * out.z;
  if (!std::isfinite(magnitude2) || magnitude2 > 256.0f * 256.0f) return out;

  uint32_t bestProjectionCount = 0u;
  size_t bestProjectionIndex = static_cast<size_t>(-1);
  for (const auto& [_, bucket] : projectionCounts) {
    if (bucket.first > bestProjectionCount) {
      bestProjectionCount = bucket.first;
      bestProjectionIndex = bucket.second;
    }
  }
  if (bestProjectionIndex < current.size() && bestProjectionCount >= 4u &&
      static_cast<uint64_t>(bestProjectionCount) * 2u >= consensus) {
    out.worldProjection = current[bestProjectionIndex].projection;
    out.worldProjectionConsensus = bestProjectionCount;
    out.worldProjectionValid = true;
  }

  out.screenSamples = static_cast<uint32_t>(consensusNdcX.size());
  if (consensusNdcX.size() >= 4u) {
    out.ndcX = median(consensusNdcX);
    out.ndcY = median(consensusNdcY);
    // A full-screen NDC jump in one source interval is a cut, not scroll.
    if (std::isfinite(out.ndcX) && std::isfinite(out.ndcY) &&
        std::abs(out.ndcX) <= 1.0f && std::abs(out.ndcY) <= 1.0f) {
      out.screenValid = true;
    }
  }
  out.valid = true;
  return out;
}

void m14_replay_patch_camera_uniform(uint8_t* uniform,
                                     const M14ReplaySnapshot& current,
                                     const M14ReplayCameraDelta& camera,
                                     float alpha) noexcept {
  if (!camera.valid || !current.valid || current.currentPnMtx >= gx::MaxPnMtx) return;
  auto pos = current.position;
  const float remaining = 1.0f - alpha;
  // Keep the object's authoritative current local motion, but move its whole
  // model-view matrix through the robust global camera delta. This lets
  // unmatched/animated geometry share the same camera phase without inventing
  // object correspondence.
  pos.m0.w() -= camera.x * remaining;
  pos.m1.w() -= camera.y * remaining;
  pos.m2.w() -= camera.z * remaining;
  const size_t positionOffset = m14_replay_position_offset(current);
  std::memcpy(uniform + positionOffset +
                  static_cast<size_t>(current.currentPnMtx) * sizeof(Mat3x4<float>),
              &pos, sizeof(pos));
}

void m14_replay_patch_camera_arena_all(uint8_t* uniform,
                                        const M14ReplaySnapshot& current,
                                        const M14ReplayCameraDelta& camera,
                                        float alpha) noexcept {
  if (!camera.valid || !camera.worldProjectionValid || !current.cameraPatchable) return;
  if (std::memcmp(&current.projection, &camera.worldProjection,
                  sizeof(current.projection)) != 0) return;
  const float remaining = 1.0f - alpha;
  const size_t positionOffset = m14_replay_position_offset(current);
  for (uint32_t i = 0; i < gx::MaxPnMtx; ++i) {
    Mat3x4<float> pos{};
    std::memcpy(&pos, uniform + positionOffset +
                         static_cast<size_t>(i) * sizeof(Mat3x4<float>),
                sizeof(pos));
    if (!m14_replay_finite(pos)) continue;
    pos.m0.w() -= camera.x * remaining;
    pos.m1.w() -= camera.y * remaining;
    pos.m2.w() -= camera.z * remaining;
    std::memcpy(uniform + positionOffset +
                    static_cast<size_t>(i) * sizeof(Mat3x4<float>),
                &pos, sizeof(pos));
  }
}

void m14_replay_patch_uniform(uint8_t* uniform,
                              const M14ReplaySnapshot& previous,
                              const M14ReplaySnapshot& current,
                              float alpha) noexcept {
  const size_t positionOffset = m14_replay_position_offset(current);
  // Projection stays authoritative/current in dev140. Camera/object scrolling
  // is carried by GX model-view position matrices; blending projection state
  // adds risk during cuts/FOV changes without helping ordinary side-scroll
  // translation.
  const uint32_t i = current.currentPnMtx;
  if (i >= gx::MaxPnMtx || previous.currentPnMtx != i) return;
  if (m14_replay_matrix_safe(previous.position, current.position)) {
    // Non-indexed draws use exactly currentPnMtx. Preserve the current basis
    // and patch only that active matrix's translation; unrelated PN slots stay
    // byte-for-byte authoritative.
    auto pos = current.position;
    pos.m0.w() = previous.position.m0.w() +
                 (current.position.m0.w() - previous.position.m0.w()) * alpha;
    pos.m1.w() = previous.position.m1.w() +
                 (current.position.m1.w() - previous.position.m1.w()) * alpha;
    pos.m2.w() = previous.position.m2.w() +
                 (current.position.m2.w() - previous.position.m2.w()) * alpha;
    std::memcpy(uniform + positionOffset + i * sizeof(Mat3x4<float>),
                &pos, sizeof(pos));
  }
  // Normal matrices intentionally stay authoritative/current in dev140.
  // Translation does not affect normals, and rotation/scale already snaps.
}

uint32_t m14_replay_requested_hz() noexcept {
  const char* value = std::getenv("OPENSPM_INTERPOLATION_TARGET_FPS");
  if (value == nullptr || value[0] == '\0') return 100u;
  char* end = nullptr;
  const unsigned long parsed = std::strtoul(value, &end, 10);
  if (end == value || *end != '\0' || parsed <= 60ul) return 100u;
  return static_cast<uint32_t>(std::clamp<unsigned long>(parsed, 61ul, 360ul));
}

void m14_replay_initialize_cadence() noexcept {
  if (sM14ReplayCadenceInitialized) return;
  sM14ReplayCadenceInitialized = true;

  const uint32_t requestedHz = m14_replay_requested_hz();
  sM14ReplayCadenceNumerator = requestedHz;
  sM14ReplayCadenceDenominator = 1u;

  SDL_Window* windowHandle = window::get_sdl_window();
  if (windowHandle != nullptr) {
    const SDL_DisplayID display = SDL_GetDisplayForWindow(windowHandle);
    if (display != 0) {
      const SDL_DisplayMode* mode = SDL_GetCurrentDisplayMode(display);
      if (mode != nullptr && mode->refresh_rate > 60.0f &&
          std::fabs(mode->refresh_rate - static_cast<float>(requestedHz)) <= 2.0f) {
        if (mode->refresh_rate_numerator > 0u &&
            mode->refresh_rate_denominator > 0u) {
          sM14ReplayCadenceNumerator = mode->refresh_rate_numerator;
          sM14ReplayCadenceDenominator = mode->refresh_rate_denominator;
        } else {
          sM14ReplayCadenceNumerator = static_cast<uint64_t>(
              std::llround(static_cast<double>(mode->refresh_rate) * 1000.0));
          sM14ReplayCadenceDenominator = 1000u;
        }
      }
    }
  }

  sM14ReplayCadenceStep = 60u * sM14ReplayCadenceDenominator;
  if (sM14ReplayCadenceNumerator <= sM14ReplayCadenceStep) {
    sM14ReplayCadenceNumerator = requestedHz;
    sM14ReplayCadenceDenominator = 1u;
    sM14ReplayCadenceStep = 60u;
  }

  const double cadenceHz =
      static_cast<double>(sM14ReplayCadenceNumerator) /
      static_cast<double>(sM14ReplayCadenceDenominator);
  Log.info("M14 dev140 replay cadence requested_hz={} effective_hz={:.6f} precise={}/{} "
           "simulation_hz=60 rational_phase=on",
           requestedHz, cadenceHz, sM14ReplayCadenceNumerator,
           sM14ReplayCadenceDenominator);
}

std::array<float, 8> m14_replay_phases(size_t& count) noexcept {
  m14_replay_initialize_cadence();
  std::array<float, 8> phases{};
  count = 0u;

  while (sM14ReplayCadencePhase < sM14ReplayCadenceNumerator) {
    if (count >= phases.size()) {
      // Requested presenter rates are clamped to 360 Hz, so at 60 Hz source
      // this should never need more than six samples. Fail closed if that
      // invariant changes.
      count = 0u;
      sM14ReplayCadencePhase = 0u;
      return phases;
    }
    phases[count++] = static_cast<float>(
        static_cast<double>(sM14ReplayCadencePhase) /
        static_cast<double>(sM14ReplayCadenceNumerator));
    sM14ReplayCadencePhase += sM14ReplayCadenceStep;
  }

  // target > 60 Hz guarantees step < numerator, so the loop overshoots by
  // less than one simulation interval and a single subtraction preserves the
  // exact rational remainder for the next 60 Hz interval.
  sM14ReplayCadencePhase -= sM14ReplayCadenceNumerator;
  return phases;
}
'''

PREPARE = r'''
void m14_prepare_interpolation_replay() noexcept {
  if (!m14_replay_enabled() || !g_recorder.active()) return;
  const auto prepareStart = std::chrono::steady_clock::now();
  if (!sM14ReplayReported) {
    Log.info("M14 dev145 replay prep active: simulation_hz=60 phase_source=rational-display-grid normal_mode=world-camera-only final_xfb_shift=off");
    sM14ReplayReported = true;
  }
  auto& frame = g_recorder.frame();
  frame.m14ReplayPhases.clear();
  frame.m14ReplayOriginalUniformBytes = 0u;
  frame.m14ReplayMatchedUniforms = 0u;
  frame.m14ReplaySnappedUniforms = 0u;
  frame.m14ReplayCameraValid = false;
  frame.m14ReplayCameraDx = 0.0f;
  frame.m14ReplayCameraDy = 0.0f;
  frame.m14ReplayCameraDz = 0.0f;
  frame.m14ReplayCameraScreenValid = false;
  frame.m14ReplayCameraNdcDx = 0.0f;
  frame.m14ReplayCameraNdcDy = 0.0f;
  frame.m14ReplayCameraSamples = 0u;
  frame.m14ReplayCameraConsensus = 0u;

  // Prefer write-time CPU mirrors; never repeatedly read write-combined upload
  // memory. The bounded bulk fallback also supports older/incomplete adapters.
  struct ReadFrame { std::vector<uint8_t> uniforms, verts, indices; };
  static ReadFrame readFrame;
  const auto copyRead = [](auto& dst, const auto& src, const auto& mirror) {
    dst.resize(src.size());
    if (!dst.empty()) std::memcpy(dst.data(), mirror.size() == src.size() ? mirror.data() : src.data(), src.size());
  };
  copyRead(readFrame.uniforms, frame.uniforms, frame.m14UniformRead);
  copyRead(readFrame.verts, frame.verts, frame.m14VertexRead);
  copyRead(readFrame.indices, frame.indices, frame.m14IndexRead);
  const auto copiedAt = std::chrono::steady_clock::now();
  std::vector<M14ReplaySnapshot> current;
  current.reserve(2048u);
  uint32_t m14TotalGxDraws = 0u;
  uint32_t m14IndexedSnaps = 0u;
  uint32_t m14SnapshotRejects = 0u;
  for (auto& pass : frame.renderPasses) {
    for (auto& command : pass.commands) {
      if (command.type != CommandType::Draw ||
          command.data.draw.encoder != encode_draw<gx::render, gx::DrawData>) continue;
      ++m14TotalGxDraws;
      auto& draw = command.data.draw;
      const auto& data = inline_payload<gx::DrawData>(draw.payload.data());
      if (draw.m14ReplayIndexedMatrices) {
        ++m14IndexedSnaps;
      }
      M14ReplaySnapshot snapshot;
      // Keep every uniform consumer, even when its geometry/layout cannot be
      // matched. A matched draw must not interpolate an arena also used by an
      // unmatched draw (Aurora can reuse uniform ranges across draws).
      snapshot.uniformRange = data.uniformRange;
      snapshot.valid = m14_replay_read_snapshot(readFrame, data, draw.m14ReplayUniformPrefixBytes,
                                                 draw.m14ReplayIndexedMatrices, snapshot);
      if (!snapshot.valid && !draw.m14ReplayIndexedMatrices) ++m14SnapshotRejects;
      current.emplace_back(std::move(snapshot));
    }
  }

  const auto snapshotsAt = std::chrono::steady_clock::now();
  size_t phaseCount = 0u;
  std::array<float, 8> phases{};
  if (sM14ReplayTimelineStarted) {
    phases = m14_replay_phases(phaseCount);
    ++sM14ReplayInterval;
  } else {
    m14_replay_initialize_cadence();
    sM14ReplayTimelineStarted = true;
  }

  if (current.empty()) {
    sM14ReplayPrevious.clear();
    return;
  }
  if (sM14ReplayPrevious.empty()) {
    sM14ReplayPrevious = std::move(current);
    return;
  }

  std::unordered_map<uint64_t, std::vector<size_t>> previousByIdentity;
  previousByIdentity.reserve(sM14ReplayPrevious.size());
  for (size_t i = 0; i < sM14ReplayPrevious.size(); ++i) {
    if (sM14ReplayPrevious[i].valid)
      previousByIdentity[sM14ReplayPrevious[i].identity].push_back(i);
  }
  std::vector<uint8_t> used(sM14ReplayPrevious.size(), 0u);
  uint32_t m14IdentityMisses = 0u;
  uint32_t m14DiscontinuitySnaps = 0u;
  uint32_t m14DuplicateExhaustedSnaps = 0u;
  uint32_t m14AmbiguousSnaps = 0u;

  for (auto& snapshot : current) {
    if (!snapshot.valid) continue;
    const auto found = previousByIdentity.find(snapshot.identity);
    if (found == previousByIdentity.end()) {
      ++m14IdentityMisses;
      continue;
    }
    size_t chosen = static_cast<size_t>(-1);
    double best = std::numeric_limits<double>::infinity();
    double second = std::numeric_limits<double>::infinity();
    size_t available = 0u;
    for (const size_t index : found->second) {
      if (index >= used.size() || used[index] != 0u) continue;
      ++available;
      const double distance = m14_replay_distance(sM14ReplayPrevious[index], snapshot);
      if (distance < best) { second = best; best = distance; chosen = index; }
      else if (distance < second) second = distance;
    }
    if (available == 0u) {
      ++m14DuplicateExhaustedSnaps;
      continue;
    }
    if (chosen == static_cast<size_t>(-1) || !std::isfinite(best)) {
      ++m14DiscontinuitySnaps;
      continue;
    }
    if (available > 1u && std::isfinite(second) &&
        !(second > best * 1.05 + 1.0e-6)) {
      ++m14AmbiguousSnaps;
      continue;
    }
    snapshot.previousIndex = chosen;
    used[chosen] = 1u;
  }

  const auto m14CameraDelta =
      m14_replay_estimate_camera_delta(sM14ReplayPrevious, current);
  frame.m14ReplayCameraValid = m14CameraDelta.valid;
  frame.m14ReplayCameraDx = m14CameraDelta.x;
  frame.m14ReplayCameraDy = m14CameraDelta.y;
  frame.m14ReplayCameraDz = m14CameraDelta.z;
  frame.m14ReplayCameraScreenValid = m14CameraDelta.screenValid;
  frame.m14ReplayCameraNdcDx = m14CameraDelta.ndcX;
  frame.m14ReplayCameraNdcDy = m14CameraDelta.ndcY;
  frame.m14ReplayCameraSamples = m14CameraDelta.samples;
  frame.m14ReplayCameraConsensus = m14CameraDelta.consensus;

  uint32_t m14MatchedDraws = 0u;
  uint32_t m14UnmatchedDraws = 0u;
  for (const auto& snapshot : current) {
    if (snapshot.previousIndex == static_cast<size_t>(-1)) ++m14UnmatchedDraws;
    else ++m14MatchedDraws;
  }

  if (m14_camera_present_enabled()) {
    const size_t originalBytes = frame.uniforms.size();
    if (originalBytes == 0u || originalBytes > UniformBufferSize) {
      sM14ReplayPrevious = std::move(current);
      return;
    }
    frame.m14ReplayOriginalUniformBytes = static_cast<uint32_t>(originalBytes);

    std::unordered_map<uint32_t, size_t> worldArenaByOffset;
    worldArenaByOffset.reserve(current.size());
    if (m14CameraDelta.valid && m14CameraDelta.worldProjectionValid) {
      for (size_t i = 0; i < current.size(); ++i) {
        const auto& snapshot = current[i];
        if (!snapshot.cameraPatchable) continue;
        if (std::memcmp(&snapshot.projection, &m14CameraDelta.worldProjection,
                        sizeof(snapshot.projection)) != 0) continue;
        if (static_cast<size_t>(snapshot.uniformRange.offset) +
                m14_replay_all_position_bytes(snapshot) > originalBytes) continue;
        worldArenaByOffset.emplace(snapshot.uniformRange.offset, i);
      }
    }

    frame.m14ReplayMatchedUniforms =
        static_cast<uint32_t>(worldArenaByOffset.size());
    frame.m14ReplaySnappedUniforms = 0u;

    const uint32_t alignment = std::max<uint32_t>(
        1u, static_cast<uint32_t>(resources().limits.minUniformBufferOffsetAlignment));
    if (!worldArenaByOffset.empty()) {
      for (size_t phaseIndex = 0; phaseIndex < phaseCount; ++phaseIndex) {
        const size_t aligned = AURORA_ALIGN(frame.uniforms.size(), alignment);
        const size_t finishReserve = static_cast<size_t>(gx::MaxUniformSize);
        if (finishReserve > UniformBufferSize ||
            aligned > UniformBufferSize - finishReserve ||
            originalBytes > UniformBufferSize - finishReserve - aligned) {
          ++sM14ReplayArenaExhaustedIntervals;
          break;
        }
        if (aligned > frame.uniforms.size())
          frame.uniforms.append_zeroes(aligned - frame.uniforms.size());
        const uint32_t base = static_cast<uint32_t>(frame.uniforms.size());
        frame.uniforms.append(readFrame.uniforms.data(), originalBytes);
        for (const auto& [uniformOffset, currentIndex] : worldArenaByOffset) {
          if (currentIndex >= current.size()) continue;
          m14_replay_patch_camera_arena_all(
              frame.uniforms.data() + base + uniformOffset,
              current[currentIndex], m14CameraDelta, phases[phaseIndex]);
        }
        frame.m14ReplayPhases.push_back(
            M14ReplayPhase{.alpha = phases[phaseIndex], .uniformBase = base});
      }
    }

    ++sM14ReplayPreparedIntervals;
    const uint64_t ordinal = sM14ReplayInterval;
    if (ordinal <= 8u || (ordinal & (ordinal - 1u)) == 0u) {
      const auto prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now() - prepareStart).count();
      Log.info("M14 dev145 world-camera replay interval={} draws={} matched_draws={} unmatched_draws={} "
               "camera_valid={} camera_samples={} camera_consensus={} camera_dx={} camera_dy={} camera_dz={} "
               "world_projection={} projection_consensus={} world_arenas={} indexed_draws={} "
               "phases={} original_uniform_bytes={} staged_uniform_bytes={} prep_us={} "
               "read_copy_us={} snapshot_us={} match_us={} prepared_total={}",
               ordinal, current.size(), m14MatchedDraws, m14UnmatchedDraws,
               m14CameraDelta.valid, m14CameraDelta.samples, m14CameraDelta.consensus,
               m14CameraDelta.x, m14CameraDelta.y, m14CameraDelta.z,
               m14CameraDelta.worldProjectionValid,
               m14CameraDelta.worldProjectionConsensus,
               worldArenaByOffset.size(), m14IndexedSnaps,
               frame.m14ReplayPhases.size(), frame.m14ReplayOriginalUniformBytes,
               frame.uniforms.size(), prepareUs,
               std::chrono::duration_cast<std::chrono::microseconds>(copiedAt - prepareStart).count(),
               std::chrono::duration_cast<std::chrono::microseconds>(snapshotsAt - copiedAt).count(),
               std::chrono::duration_cast<std::chrono::microseconds>(
                   std::chrono::steady_clock::now() - snapshotsAt).count(),
               sM14ReplayPreparedIntervals);
    }
    sM14ReplayPrevious = std::move(current);
    return;
  }

  struct Patch { size_t currentIndex = 0u; bool conflict = false; };
  std::unordered_map<uint32_t, Patch> patchByUniform;
  patchByUniform.reserve(current.size());
  std::unordered_map<uint32_t, std::vector<size_t>> cameraConsumersByUniform;
  std::unordered_map<uint32_t, bool> cameraUnsafeByUniform;
  cameraConsumersByUniform.reserve(current.size());
  cameraUnsafeByUniform.reserve(current.size());
  for (size_t i = 0; i < current.size(); ++i) {
    const auto& snapshot = current[i];
    const bool matched = snapshot.valid &&
                         snapshot.previousIndex < sM14ReplayPrevious.size();
    if (snapshot.valid) {
      cameraConsumersByUniform[snapshot.uniformRange.offset].push_back(i);
    } else {
      cameraUnsafeByUniform[snapshot.uniformRange.offset] = true;
    }
    const auto inserted = patchByUniform.emplace(snapshot.uniformRange.offset,
                                                 Patch{.currentIndex = i,
                                                       .conflict = !matched});
    if (!inserted.second) {
      if (!matched || inserted.first->second.conflict) {
        inserted.first->second.conflict = true;
        continue;
      }
      const auto& firstCurrent = current[inserted.first->second.currentIndex];
      const auto& a = sM14ReplayPrevious[firstCurrent.previousIndex];
      const auto& b = sM14ReplayPrevious[snapshot.previousIndex];
      if (a.currentPnMtx != b.currentPnMtx ||
          firstCurrent.uniformRange.size != snapshot.uniformRange.size ||
          firstCurrent.uniformPrefixBytes != snapshot.uniformPrefixBytes ||
          std::memcmp(&a.projection, &b.projection, sizeof(a.projection)) != 0 ||
          std::memcmp(&a.position, &b.position, sizeof(a.position)) != 0)
        inserted.first->second.conflict = true;
    }
  }

  // The presentation phase cursor was advanced above before all matching
  // early-outs so empty/transition/no-match frames cannot shift the display
  // grid relative to authoritative 60 Hz frame boundaries.
  const size_t originalBytes = frame.uniforms.size();
  if (originalBytes == 0u || originalBytes > UniformBufferSize) {
    sM14ReplayPrevious = std::move(current);
    return;
  }
  frame.m14ReplayOriginalUniformBytes = static_cast<uint32_t>(originalBytes);
  for (const auto& [_, patch] : patchByUniform) {
    if (patch.conflict) ++frame.m14ReplaySnappedUniforms;
    else ++frame.m14ReplayMatchedUniforms;
  }

  const uint32_t m14OriginalSnappedUniforms = frame.m14ReplaySnappedUniforms;
  uint32_t m14CameraPatchedUniforms = 0u;
  bool m14CameraCoherent = m14OriginalSnappedUniforms == 0u;
  if (!m14CameraCoherent && m14CameraDelta.valid) {
    m14CameraCoherent = true;
    for (const auto& [uniformOffset, patch] : patchByUniform) {
      if (!patch.conflict) continue;
      const auto found = cameraConsumersByUniform.find(uniformOffset);
      if (found == cameraConsumersByUniform.end() || found->second.empty() ||
          cameraUnsafeByUniform.find(uniformOffset) != cameraUnsafeByUniform.end()) {
        m14CameraCoherent = false;
        break;
      }
      for (const size_t index : found->second) {
        if (index >= current.size() ||
            static_cast<size_t>(uniformOffset) +
                m14_replay_required_uniform_bytes(current[index]) > originalBytes) {
          m14CameraCoherent = false;
          break;
        }
      }
      if (!m14CameraCoherent) break;
      ++m14CameraPatchedUniforms;
    }
  }
  // The visible path interprets snapped_uniforms==0 as a transform-coherent
  // replay. Once every formerly snapped arena is covered by the same robust
  // camera phase, it is safe to publish while keeping object-local motion
  // authoritative on those arenas.
  if (m14CameraCoherent && m14OriginalSnappedUniforms != 0u)
    frame.m14ReplaySnappedUniforms = 0u;

  // Do not copy one or two complete uniform arenas when no transform range can
  // actually be patched.  The visible presenter can fall back to the native
  // retained XFB for this interval while the phase cursor still advances.
  if (frame.m14ReplayMatchedUniforms == 0u) {
    ++sM14ReplayNoMatchIntervals;
    if (sM14ReplayNoMatchIntervals <= 8u ||
        (sM14ReplayNoMatchIntervals & (sM14ReplayNoMatchIntervals - 1u)) == 0u) {
      Log.info("M14 dev140 replay prep skip interval={} reason=no_matches draws={} gx_draws={} "
               "matched_draws={} unmatched_draws={} indexed_snap={} snapshot_reject={} "
               "identity_miss={} discontinuity_snap={} duplicate_exhausted={} ambiguous_snap={} "
               "previous={} no_match_total={}",
               sM14ReplayInterval, current.size(), m14TotalGxDraws,
               m14MatchedDraws, m14UnmatchedDraws, m14IndexedSnaps,
               m14SnapshotRejects, m14IdentityMisses, m14DiscontinuitySnaps,
               m14DuplicateExhaustedSnaps, m14AmbiguousSnaps,
               sM14ReplayPrevious.size(), sM14ReplayNoMatchIntervals);
    }
    sM14ReplayPrevious = std::move(current);
    return;
  }

  const auto matchingAt = std::chrono::steady_clock::now();
  const uint32_t alignment = std::max<uint32_t>(
      1u, static_cast<uint32_t>(resources().limits.minUniformBufferOffsetAlignment));

  for (size_t phaseIndex = 0; phaseIndex < phaseCount; ++phaseIndex) {
    const size_t aligned = AURORA_ALIGN(frame.uniforms.size(), alignment);
    // gfx::finish() still appends gx::MaxUniformSize bytes after replay
    // preparation for the depth-snapshot resolve path. Keep that mandatory
    // authoritative tail available instead of filling the staging arena to the
    // last byte with interpolation copies.
    const size_t finishReserve = static_cast<size_t>(gx::MaxUniformSize);
    if (finishReserve > UniformBufferSize ||
        aligned > UniformBufferSize - finishReserve ||
        originalBytes > UniformBufferSize - finishReserve - aligned) {
      ++sM14ReplayArenaExhaustedIntervals;
      Log.warn("M14 dev140 replay uniform arena exhausted: original={} current={} requested_phases={} completed={} finish_reserve={} exhausted_total={}",
               originalBytes, frame.uniforms.size(), phaseCount,
               frame.m14ReplayPhases.size(), finishReserve,
               sM14ReplayArenaExhaustedIntervals);
      break;
    }
    if (aligned > frame.uniforms.size()) frame.uniforms.append_zeroes(aligned - frame.uniforms.size());
    const uint32_t base = static_cast<uint32_t>(frame.uniforms.size());
    frame.uniforms.append(readFrame.uniforms.data(), originalBytes);

    for (const auto& [uniformOffset, patch] : patchByUniform) {
      if (!patch.conflict) {
        if (patch.currentIndex >= current.size()) continue;
        const auto& cur = current[patch.currentIndex];
        if (cur.previousIndex >= sM14ReplayPrevious.size()) continue;
        if (static_cast<size_t>(uniformOffset) +
                m14_replay_required_uniform_bytes(cur) > originalBytes) continue;
        m14_replay_patch_uniform(frame.uniforms.data() + base + uniformOffset,
                                 sM14ReplayPrevious[cur.previousIndex], cur,
                                 phases[phaseIndex]);
        continue;
      }
      if (!m14CameraCoherent) continue;
      const auto found = cameraConsumersByUniform.find(uniformOffset);
      if (found == cameraConsumersByUniform.end()) continue;
      for (const size_t index : found->second) {
        if (index >= current.size()) continue;
        const auto& cur = current[index];
        if (static_cast<size_t>(uniformOffset) +
                m14_replay_required_uniform_bytes(cur) > originalBytes) continue;
        m14_replay_patch_camera_uniform(frame.uniforms.data() + base + uniformOffset,
                                        cur, m14CameraDelta, phases[phaseIndex]);
      }
    }
    frame.m14ReplayPhases.push_back(M14ReplayPhase{.alpha = phases[phaseIndex],
                                                   .uniformBase = base});
  }

  ++sM14ReplayPreparedIntervals;
  const uint64_t ordinal = sM14ReplayInterval;
  if (ordinal <= 8u || (ordinal & (ordinal - 1u)) == 0u) {
    const auto prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - prepareStart).count();
    Log.info("M14 dev140 replay prep interval={} draws={} gx_draws={} matched_draws={} unmatched_draws={} "
             "indexed_snap={} snapshot_reject={} identity_miss={} discontinuity_snap={} "
             "duplicate_exhausted={} ambiguous_snap={} previous={} matched_uniforms={} "
             "snapped_uniforms={} camera_original_snapped={} camera_patched={} camera_coherent={} "
             "camera_samples={} camera_consensus={} camera_dx={} camera_dy={} camera_dz={} "
             "phases={} original_uniform_bytes={} staged_uniform_bytes={} "
             "prepared_total={} no_match_total={} arena_exhausted_total={} prep_us={} read_copy_us={} snapshot_us={} match_us={} clone_us={}",
             ordinal, current.size(), m14TotalGxDraws, m14MatchedDraws,
             m14UnmatchedDraws, m14IndexedSnaps, m14SnapshotRejects,
             m14IdentityMisses, m14DiscontinuitySnaps,
             m14DuplicateExhaustedSnaps, m14AmbiguousSnaps,
             sM14ReplayPrevious.size(),
             frame.m14ReplayMatchedUniforms, frame.m14ReplaySnappedUniforms,
             m14OriginalSnappedUniforms, m14CameraPatchedUniforms,
             m14CameraCoherent, m14CameraDelta.samples, m14CameraDelta.consensus,
             m14CameraDelta.x, m14CameraDelta.y, m14CameraDelta.z,
             frame.m14ReplayPhases.size(), frame.m14ReplayOriginalUniformBytes,
             frame.uniforms.size(), sM14ReplayPreparedIntervals,
             sM14ReplayNoMatchIntervals, sM14ReplayArenaExhaustedIntervals, prepareUs,
             std::chrono::duration_cast<std::chrono::microseconds>(copiedAt - prepareStart).count(),
             std::chrono::duration_cast<std::chrono::microseconds>(snapshotsAt - copiedAt).count(),
             std::chrono::duration_cast<std::chrono::microseconds>(matchingAt - snapshotsAt).count(),
             std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - matchingAt).count());
  }
  sM14ReplayPrevious = std::move(current);
}

void m14_push_gx_draw_command(gx::DrawData data, uint32_t uniformPrefixBytes,
                              bool indexedMatrices) {
  auto command = make_draw_command<gx::render>(data);
  command.m14ReplayUniformPrefixBytes = uniformPrefixBytes;
  command.m14ReplayIndexedMatrices = indexedMatrices;
  command.m14ReplayGX = true;
  push_draw_command(command);
}

'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def ensure_include(text: str, include: str, anchor: str, label: str) -> str:
    if include in text:
        return text
    at = text.find(anchor)
    if at < 0:
        raise RuntimeError(f"{label}: include anchor missing")
    return text[:at] + include + text[at:]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected fragment once, found {count}")
    return text.replace(old, new, 1)


def canonicalize_region(text: str, start: str, last_function: str,
                        replacement: str, label: str) -> str:
    """Replace a known generated region, including stale cached implementations.

    These generated functions have unindented closing braces. Require both
    boundaries to be unique and ordered; never use a replaceable log as the
    installation sentinel or silently append a second implementation.
    """
    if text.count(start) != 1 or text.count(last_function) != 1:
        raise RuntimeError(f"{label}: generated region boundaries are not unique")
    begin = text.index(start)
    last = text.index(last_function)
    end = text.find("\n}\n", last)
    if last < begin or end < 0:
        raise RuntimeError(f"{label}: generated region boundaries are invalid")
    end += len("\n}\n")
    return text[:begin] + replacement.strip() + "\n" + text[end:]


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {
        "packet": source / FRAME_PACKET,
        "recording_h": source / RECORDING_H,
        "recording_cpp": source / RECORDING_CPP,
        "frame_h": source / FRAME_H,
        "frame_cpp": source / FRAME_CPP,
        "aurora": source / AURORA_CPP,
        "pipeline_h": source / PIPELINE_H,
        "cp": source / COMMAND_PROCESSOR,
    }
    text = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    original = dict(text)

    abi_assert = (
        "static_assert(sizeof(DrawData) == gfx::InlineDrawPayloadSize, "
        "\"OpenSPM dev140 must not grow GX DrawData beyond Aurora inline payload\");\n"
    )
    if abi_assert not in text["pipeline_h"]:
        anchor = "};\n\nconstexpr uint32_t GXPipelineConfigVersion"
        text["pipeline_h"] = replace_once(
            text["pipeline_h"], anchor,
            "};\n" + abi_assert + "\nconstexpr uint32_t GXPipelineConfigVersion",
            "dev140 GX DrawData ABI assertion")

    # Replay metadata belongs on the outer command, never inside the fixed
    # 128-byte GX payload. Remove both historical inline layouts from caches.
    for field in ("m14ReplayUniformPrefixBytes", "m14ReplayIndexedMatrices"):
        text["pipeline_h"] = re.sub(
            rf"^  (?:uint8_t|uint32_t|bool) {field} = [^\n]+;\n", "",
            text["pipeline_h"], flags=re.MULTILINE)
        text["cp"] = re.sub(rf"^[ \t]*\.{field} = [^\n]+,\n", "",
                            text["cp"], flags=re.MULTILINE)
        if field in text["pipeline_h"]:
            raise RuntimeError("dev140 could not retire inline DrawData metadata")

    # Canonicalize only DrawCommand; CustomDrawCommand has a similar payload.
    packet = text["packet"]
    if packet.count("struct DrawCommand {\n") != 1:
        raise RuntimeError("dev140 outer DrawCommand declaration is not unique")
    begin = packet.index("struct DrawCommand {\n")
    end = packet.find("\n};", begin)
    if end < 0:
        raise RuntimeError("dev140 outer DrawCommand declaration is unterminated")
    declaration = packet[begin:end + 1]
    for field in ("m14ReplayUniformPrefixBytes", "m14ReplayIndexedMatrices", "m14ReplayGX"):
        declaration = re.sub(rf"^  (?:uint32_t|bool) {field} = [^\n]+;\n", "",
                             declaration, flags=re.MULTILINE)
    text["packet"] = packet[:begin] + declaration + DRAW_COMMAND_FIELDS + packet[end + 1:]

    old_call = "gfx::push_draw_command(DrawData{"
    new_call = "gfx::detail::m14_push_gx_draw_command(DrawData{"
    if new_call not in text["cp"]:
        if text["cp"].count(old_call) != 1:
            raise RuntimeError("dev140 GX recording call is not unique")
        begin = text["cp"].index(old_call)
        end = text["cp"].find("});", begin)
        if end < 0:
            raise RuntimeError("dev140 GX recording call is unterminated")
        text["cp"] = (text["cp"][:begin] + new_call +
                       text["cp"][begin + len(old_call):end] +
                       "}, 16u + (cache.lineMode != 0u ? 16u : 0u), "
                       "state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE);" + text["cp"][end + 3:])
    if text["cp"].count(new_call) != 1:
        raise RuntimeError("dev140 metadata recording call is not unique")

    if "struct M14ReplayPhase {" not in text["packet"]:
        text["packet"] = replace_once(text["packet"], "struct FramePacket {\n",
                                      PACKET_TYPE + "struct FramePacket {\n",
                                      "dev140 packet type")
    if "m14ReplayPhases" not in text["packet"]:
        text["packet"] = replace_once(text["packet"], "  AuroraStats stats{};\n",
                                      "  AuroraStats stats{};\n" + PACKET_FIELDS,
                                      "dev140 packet fields")
    if "m14ReplayCameraValid" not in text["packet"]:
        text["packet"] = replace_once(
            text["packet"],
            "  uint32_t m14ReplaySnappedUniforms = 0;\n",
            "  uint32_t m14ReplaySnappedUniforms = 0;\n"
            "  bool m14ReplayCameraValid = false;\n"
            "  float m14ReplayCameraDx = 0.0f;\n"
            "  float m14ReplayCameraDy = 0.0f;\n"
            "  float m14ReplayCameraDz = 0.0f;\n"
            "  bool m14ReplayCameraScreenValid = false;\n"
            "  float m14ReplayCameraNdcDx = 0.0f;\n"
            "  float m14ReplayCameraNdcDy = 0.0f;\n"
            "  uint32_t m14ReplayCameraSamples = 0;\n"
            "  uint32_t m14ReplayCameraConsensus = 0;\n",
            "dev143 camera packet migration")

    if "m14ReplayCameraScreenValid" not in text["packet"]:
        text["packet"] = replace_once(
            text["packet"],
            "  float m14ReplayCameraDz = 0.0f;\n",
            "  float m14ReplayCameraDz = 0.0f;\n"
            "  bool m14ReplayCameraScreenValid = false;\n"
            "  float m14ReplayCameraNdcDx = 0.0f;\n"
            "  float m14ReplayCameraNdcDy = 0.0f;\n",
            "dev144 screen camera packet migration")

    if "std::vector<uint8_t> m14UniformRead" not in text["packet"]:
        text["packet"] = replace_once(text["packet"], "  std::vector<M14ReplayPhase> m14ReplayPhases;\n",
            "  std::vector<M14ReplayPhase> m14ReplayPhases;\n"
            "  std::vector<uint8_t> m14UniformRead, m14VertexRead, m14IndexRead;\n",
            "dev141 CPU read mirrors")

    forward = "namespace aurora::gx { struct DrawData; }\n"
    if forward not in text["recording_h"]:
        text["recording_h"] = forward + text["recording_h"]
    if "void m14_prepare_interpolation_replay() noexcept;" not in text["recording_h"]:
        text["recording_h"] = replace_once(
            text["recording_h"], "void increment_merged_draw_count() noexcept;\n",
            "void increment_merged_draw_count() noexcept;\n" + RECORDING_DECL,
            "dev140 recording declaration")
    elif "void m14_push_gx_draw_command(" not in text["recording_h"]:
        text["recording_h"] = text["recording_h"].replace(
            "void m14_prepare_interpolation_replay() noexcept;\n", RECORDING_DECL, 1)

    for include in ("#include <SDL3/SDL_video.h>\n", "#include <algorithm>\n", "#include <chrono>\n", "#include <cmath>\n", "#include <cstdlib>\n", "#include <limits>\n", "#include <unordered_map>\n"):
        text["recording_cpp"] = ensure_include(text["recording_cpp"], include,
                                                "#include <array>\n",
                                                "dev140 recording includes")
    if '#include "hash.hpp"\n' not in text["recording_cpp"]:
        text["recording_cpp"] = ensure_include(text["recording_cpp"], '#include "hash.hpp"\n',
                                                '#include "frame.hpp"\n',
                                                "dev140 hash include")
    if "struct M14ReplaySnapshot {" not in text["recording_cpp"]:
        text["recording_cpp"] = replace_once(text["recording_cpp"], "FrameRecorder g_recorder;\n",
                                              "FrameRecorder g_recorder;\n" + STATE,
                                              "dev140 recording state")
    text["recording_cpp"] = canonicalize_region(
        text["recording_cpp"], "struct M14ReplaySnapshot {",
        ("std::array<float, 8> m14_replay_phases(" if "std::array<float, 8> m14_replay_phases(" in text["recording_cpp"]
         else "std::array<float, 2> m14_replay_phases("), STATE,
        "dev140 recording state")
    if "void m14_prepare_interpolation_replay() noexcept {" not in text["recording_cpp"]:
        anchor = "void increment_merged_draw_count() noexcept {"
        at = text["recording_cpp"].find(anchor)
        if at < 0:
            raise RuntimeError("dev140 prepare insertion anchor missing")
        text["recording_cpp"] = text["recording_cpp"][:at] + PREPARE + text["recording_cpp"][at:]
    text["recording_cpp"] = canonicalize_region(
        text["recording_cpp"], "void m14_prepare_interpolation_replay() noexcept {",
        ("void m14_push_gx_draw_command(" if "void m14_push_gx_draw_command(" in text["recording_cpp"]
         else "void m14_prepare_interpolation_replay() noexcept {"), PREPARE,
        "dev140 preparation function")

    gx_spec_marker = "template <>\nvoid push_draw_command(gx::DrawData data) {"
    gx_spec_start = text["recording_cpp"].find(gx_spec_marker)
    if gx_spec_start < 0:
        raise RuntimeError("dev140 GX push specialization anchor missing")
    gx_spec_end = text["recording_cpp"].find("\n}\n", gx_spec_start)
    if gx_spec_end < 0:
        raise RuntimeError("dev140 GX push specialization end missing")
    gx_spec_end += len("\n}\n")
    canonical_gx_spec = (
        "template <>\n"
        "void push_draw_command(gx::DrawData data) {\n"
        "  detail::m14_push_gx_draw_command(data, 16u, false);\n"
        "}\n"
    )
    text["recording_cpp"] = (
        text["recording_cpp"][:gx_spec_start] +
        canonical_gx_spec +
        text["recording_cpp"][gx_spec_end:]
    )

    # Keep the just-recorded packet alive until Aurora's render-worker callback
    # returns. This is a no-op for dev139 today and is the required lifetime for
    # dev140b draw replay.
    accessor_decl = "FramePacket* m14_end_frame_packet() noexcept;\n"
    if accessor_decl not in text["frame_h"]:
        text["frame_h"] = replace_once(text["frame_h"],
                                       "const wgpu::Buffer& staging_buffer(size_t slot);\n",
                                       "const wgpu::Buffer& staging_buffer(size_t slot);\n" + accessor_decl,
                                       "dev140 frame accessor declaration")
    if "g_m14EndFramePacket" not in text["frame_cpp"]:
        ns = "namespace aurora::gfx {\n"
        at = text["frame_cpp"].find(ns)
        if at < 0:
            raise RuntimeError("dev140 frame namespace anchor missing")
        at += len(ns)
        text["frame_cpp"] = text["frame_cpp"][:at] + "thread_local detail::FramePacket* g_m14EndFramePacket = nullptr;\n\n" + text["frame_cpp"][at:]
    if "FramePacket* m14_end_frame_packet() noexcept" not in text["frame_cpp"]:
        anchor = "size_t acquire_frame_slot() {"
        at = text["frame_cpp"].find(anchor)
        if at < 0:
            raise RuntimeError("dev140 frame accessor body anchor missing")
        body = "namespace detail {\nFramePacket* m14_end_frame_packet() noexcept { return g_m14EndFramePacket; }\n}\n\n"
        text["frame_cpp"] = text["frame_cpp"][:at] + body + text["frame_cpp"][at:]

    pre_reset = """    auto afterSubmitCallbacks = std::move(packet.afterSubmitCallbacks);\n    packet = {};\n    g_resources.stats.drawCallCount = stats.drawCallCount;\n"""
    post_reset = """    auto afterSubmitCallbacks = std::move(packet.afterSubmitCallbacks);\n    g_resources.stats.drawCallCount = stats.drawCallCount;\n"""
    if pre_reset in text["frame_cpp"]:
        text["frame_cpp"] = text["frame_cpp"].replace(pre_reset, post_reset, 1)
    elif post_reset not in text["frame_cpp"]:
        raise RuntimeError("dev140 pre-callback packet reset shape missing")

    old_callback = """    if (callback) {\n      callback(encoder, std::move(afterSubmitCallbacks));\n    }\n    g_frameSlots.release(frameSlot);\n"""
    new_callback = """    g_m14EndFramePacket = &packet;\n    if (callback) {\n      callback(encoder, std::move(afterSubmitCallbacks));\n    }\n    g_m14EndFramePacket = nullptr;\n    packet = {};\n    g_frameSlots.release(frameSlot);\n"""
    if "g_m14EndFramePacket = &packet;" not in text["frame_cpp"]:
        text["frame_cpp"] = replace_once(text["frame_cpp"], old_callback, new_callback,
                                          "dev140 packet lifetime")

    prepare_call = "  gfx::detail::m14_prepare_interpolation_replay();\n"
    # Replay arenas must be appended while staging memory is mapped AND before
    # gfx::finish() seals the final pass/captures its high-water mark. Repair an
    # interrupted draft that inserted the call after imgui::freeze().
    text["aurora"] = text["aurora"].replace(prepare_call, "")
    # Readback segmentation also calls gfx::finish(). Only a displayed frame
    # advances interpolation history; never prepare at the readback boundary.
    finish_anchor = "  gfx::finish();\n  auto imguiDrawData = imgui::freeze();\n"
    text["aurora"] = replace_once(
        text["aurora"], finish_anchor, prepare_call + finish_anchor,
        "dev140 prepare-before-finish call")

    required = (
        "struct M14ReplayPhase {",
        "m14ReplayPhases",
        "M14 dev145 replay prep active",
        "phase_source=rational-display-grid",
        "M14 dev140 replay cadence requested_hz={}",
        "sM14ReplayCadencePhase",
        "SDL_GetCurrentDisplayMode",
        "m14_prepare_interpolation_replay();",
        "FramePacket* m14_end_frame_packet() noexcept",
        "g_m14EndFramePacket = &packet;",
        "m14ReplayUniformPrefixBytes = 16u",
        "m14ReplayIndexedMatrices = false",
        "m14ReplayGX = false",
        "OpenSPM dev140 must not grow GX DrawData beyond Aurora inline payload",
        "m14_push_gx_draw_command",
        "16u + (cache.lineMode != 0u ? 16u : 0u)",
        "state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE",
        "finishReserve = static_cast<size_t>(gx::MaxUniformSize)",
        "Non-indexed draws use exactly currentPnMtx. Preserve the current basis",
        "patch only that active matrix's translation",
        "Mat3x4<float> position{}",
        "Projection stays authoritative/current in dev140.",
        "Any projection change",
        "m14_replay_same_basis",
        "empty/transition/no-match frames cannot shift the display",
        "if (frame.m14ReplayMatchedUniforms == 0u)",
        "M14 dev140 replay prep skip interval=",
        "no_match_total={}",
        "matched_draws={}",
        "unmatched_draws={}",
        "identity_miss={}",
        "discontinuity_snap={}",
        "duplicate_exhausted={}",
        "ambiguous_snap={}",
        "indexed_snap={}",
        "snapshot_reject={}",
        "arena_exhausted_total={}",
        "M14ReplayCameraDelta",
        "m14_replay_estimate_camera_delta",
        "m14_replay_patch_camera_uniform",
        "camera_original_snapped={}",
        "camera_patched={}",
        "camera_coherent={}",
        "m14ReplayCameraValid",
        "m14ReplayCameraDx",
        "m14_camera_present_enabled",
        "M14 dev145 world-camera replay interval={}",
        "m14ReplayCameraScreenValid",
        "m14ReplayCameraNdcDx",
        "m14_replay_project_origin_ndc",
        "world_projection={}",
        "world_arenas={}",
    )
    combined = "\n".join(text.values())
    missing = [item for item in required if item not in combined]
    if missing:
        raise RuntimeError("dev140 migration incomplete: " + ", ".join(missing))

    prepare_pos = text["aurora"].find(prepare_call)
    finish_pos = text["aurora"].find("  gfx::finish();\n")
    freeze_pos = text["aurora"].find("  auto imguiDrawData = imgui::freeze();\n")
    if prepare_pos < 0 or finish_pos < 0 or freeze_pos < 0 or not (prepare_pos < finish_pos < freeze_pos):
        raise RuntimeError(
            "dev140 high-water invariant failed: replay prep must run before gfx::finish/imgui freeze"
        )
    if text["pipeline_h"].count(
        "OpenSPM dev140 must not grow GX DrawData beyond Aurora inline payload"
    ) != 1:
        raise RuntimeError("dev140 GX DrawData ABI assertion is not unique")
    if "m14ReplayUniformPrefixBytes" in text["pipeline_h"] or \
       "m14ReplayIndexedMatrices" in text["pipeline_h"]:
        raise RuntimeError("dev140 replay metadata leaked into 128-byte GX DrawData")
    if text["packet"].count("m14ReplayUniformPrefixBytes = 16u") != 1:
        raise RuntimeError("dev140 DrawCommand uniform-prefix sidecar is not unique")
    if text["packet"].count("m14ReplayIndexedMatrices = false") != 1:
        raise RuntimeError("dev140 DrawCommand indexed-matrix sidecar is not unique")
    if text["packet"].count("m14ReplayGX = false") != 1:
        raise RuntimeError("dev140 DrawCommand GX tag is not unique")
    if text["cp"].count("gfx::detail::m14_push_gx_draw_command(DrawData{") != 1:
        raise RuntimeError("dev140 metadata-aware GX draw call is not unique")
    if ".m14ReplayUniformPrefixBytes =" in text["cp"] or \
       ".m14ReplayIndexedMatrices =" in text["cp"]:
        raise RuntimeError("dev140 stale DrawData replay initializer survived migration")

    if check_only:
        return "ok"
    changed = False
    for name, path in paths.items():
        if text[name] != original[name]:
            path.write_text(text[name], encoding="utf-8")
            changed = True
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    expected = lock.get("commit")
    if not expected:
        print("aurora.lock.json has no commit", file=sys.stderr)
        return 2
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=args.source).stdout.strip()
    except Exception as exc:
        print(f"failed to verify Aurora checkout: {exc}", file=sys.stderr)
        return 3
    if actual != expected:
        print(f"Aurora checkout mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 4
    try:
        result = transform(args.source, args.check)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 5
    print(f"dev140 replay-prep patch: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
