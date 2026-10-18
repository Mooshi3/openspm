#!/usr/bin/env python3
"""Add dev137 visible, configurable rigid-transform interpolation proof.

Applied after dev136.  The 60 Hz Wii simulation remains authoritative.
When OPENSPM_INTERPOLATION is enabled and OPENSPM_M14_INTERP_VISUAL=midpoint,
matched non-indexed GX draws build a temporary second uniform range whose
projection, position and normal transforms are evaluated between the previous
and current simulation frames.  For this 60 Hz proof the draw command selects
that midpoint range at record time without growing Aurora's fixed 128-byte GX
draw payload.

This is intentionally not the final high-refresh presenter.  It proves the
render-state interpolation itself before scene replay/presentation is
decoupled.  Unmatched, indexed, discontinuous, or invalid draws snap to the
authoritative current state.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 dev137 configurable visual transform interpolation active"

CP = pathlib.Path("lib/gx/command_processor.cpp")

PAIR_SAFE_ANCHOR = r"""bool m14_interp_pair_safe(const M14InterpProbeDraw& previous,
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
"""

PAIR_EXTENSIONS = r"""
struct M14InterpQuaternion {
  float x = 0.0f;
  float y = 0.0f;
  float z = 0.0f;
  float w = 1.0f;
};

std::unordered_map<u64, std::vector<size_t>> sM14InterpPreviousByIdentity;
std::vector<u8> sM14InterpPreviousUsed;
bool sM14InterpVisualReported = false;

bool m14_interp_feature_enabled() noexcept {
  const char* feature = std::getenv("OPENSPM_INTERPOLATION");
  if (feature == nullptr || feature[0] == '\0') return false;
  return std::strcmp(feature, "0") != 0 && std::strcmp(feature, "off") != 0 &&
         std::strcmp(feature, "false") != 0;
}

bool m14_interp_visual_enabled() noexcept {
  if (!m14_interp_feature_enabled()) return false;
  const char* mode = std::getenv("OPENSPM_M14_INTERP_VISUAL");
  return mode != nullptr && std::strcmp(mode, "midpoint") == 0;
}

float m14_interp_visual_alpha() noexcept {
  static const float alpha = [] {
    const char* value = std::getenv("OPENSPM_INTERPOLATION_ALPHA");
    if (value == nullptr || value[0] == '\0') return 0.5f;
    char* end = nullptr;
    const float parsed = std::strtof(value, &end);
    if (end == value || !std::isfinite(parsed)) return 0.5f;
    return std::clamp(parsed, 0.0f, 1.0f);
  }();
  return alpha;
}

u32 m14_interp_target_fps() noexcept {
  static const u32 target = [] {
    const char* value = std::getenv("OPENSPM_INTERPOLATION_TARGET_FPS");
    if (value == nullptr || value[0] == '\0') return 0u;
    char* end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    if (end == value || *end != '\0' || parsed > 1000ul) return 0u;
    return static_cast<u32>(parsed);
  }();
  return target;
}

float m14_interp_translation_distance_squared(const M14InterpProbeDraw& previous,
                                              const M14InterpProbeDraw& current) noexcept {
  if (previous.currentPnMtx >= MaxPnMtx || current.currentPnMtx >= MaxPnMtx ||
      previous.currentPnMtx != current.currentPnMtx) {
    return std::numeric_limits<float>::infinity();
  }
  const auto& a = previous.matrices[previous.currentPnMtx].pos;
  const auto& b = current.matrices[current.currentPnMtx].pos;
  const float dx = b.m0.w() - a.m0.w();
  const float dy = b.m1.w() - a.m1.w();
  const float dz = b.m2.w() - a.m2.w();
  return dx * dx + dy * dy + dz * dz;
}

bool m14_interp_decompose(const Mat3x4<float>& matrix,
                          std::array<float, 3>& scale,
                          std::array<float, 3>& translation,
                          M14InterpQuaternion& quaternion) noexcept {
  if (!m14_interp_finite(matrix)) return false;
  const std::array<Vec4<float>, 3> rows{matrix.m0, matrix.m1, matrix.m2};
  float rotation[3][3]{};
  for (size_t row = 0; row < 3; ++row) translation[row] = rows[row].w();

  for (size_t column = 0; column < 3; ++column) {
    const float x = rows[0][column];
    const float y = rows[1][column];
    const float z = rows[2][column];
    scale[column] = std::sqrt(x * x + y * y + z * z);
    if (!std::isfinite(scale[column]) || scale[column] < 1.0e-6f) return false;
    rotation[0][column] = x / scale[column];
    rotation[1][column] = y / scale[column];
    rotation[2][column] = z / scale[column];
  }

  const auto dot_columns = [&rotation](size_t a, size_t b) {
    return rotation[0][a] * rotation[0][b] +
           rotation[1][a] * rotation[1][b] +
           rotation[2][a] * rotation[2][b];
  };
  if (std::abs(dot_columns(0, 1)) > 0.08f ||
      std::abs(dot_columns(0, 2)) > 0.08f ||
      std::abs(dot_columns(1, 2)) > 0.08f) return false;

  const float determinant =
      rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1]) -
      rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0]) +
      rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0]);
  if (!std::isfinite(determinant) || std::abs(std::abs(determinant) - 1.0f) > 0.12f) return false;
  if (determinant < 0.0f) {
    scale[0] = -scale[0];
    for (size_t row = 0; row < 3; ++row) rotation[row][0] = -rotation[row][0];
  }

  const float trace = rotation[0][0] + rotation[1][1] + rotation[2][2];
  if (trace > 0.0f) {
    const float s = std::sqrt(trace + 1.0f) * 2.0f;
    quaternion.w = 0.25f * s;
    quaternion.x = (rotation[2][1] - rotation[1][2]) / s;
    quaternion.y = (rotation[0][2] - rotation[2][0]) / s;
    quaternion.z = (rotation[1][0] - rotation[0][1]) / s;
  } else if (rotation[0][0] > rotation[1][1] && rotation[0][0] > rotation[2][2]) {
    const float s = std::sqrt(1.0f + rotation[0][0] - rotation[1][1] - rotation[2][2]) * 2.0f;
    quaternion.w = (rotation[2][1] - rotation[1][2]) / s;
    quaternion.x = 0.25f * s;
    quaternion.y = (rotation[0][1] + rotation[1][0]) / s;
    quaternion.z = (rotation[0][2] + rotation[2][0]) / s;
  } else if (rotation[1][1] > rotation[2][2]) {
    const float s = std::sqrt(1.0f + rotation[1][1] - rotation[0][0] - rotation[2][2]) * 2.0f;
    quaternion.w = (rotation[0][2] - rotation[2][0]) / s;
    quaternion.x = (rotation[0][1] + rotation[1][0]) / s;
    quaternion.y = 0.25f * s;
    quaternion.z = (rotation[1][2] + rotation[2][1]) / s;
  } else {
    const float s = std::sqrt(1.0f + rotation[2][2] - rotation[0][0] - rotation[1][1]) * 2.0f;
    quaternion.w = (rotation[1][0] - rotation[0][1]) / s;
    quaternion.x = (rotation[0][2] + rotation[2][0]) / s;
    quaternion.y = (rotation[1][2] + rotation[2][1]) / s;
    quaternion.z = 0.25f * s;
  }
  return std::isfinite(quaternion.x) && std::isfinite(quaternion.y) &&
         std::isfinite(quaternion.z) && std::isfinite(quaternion.w);
}

M14InterpQuaternion m14_interp_slerp(M14InterpQuaternion a,
                                     M14InterpQuaternion b,
                                     float alpha) noexcept {
  float dot = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
  if (dot < 0.0f) {
    b.x = -b.x; b.y = -b.y; b.z = -b.z; b.w = -b.w;
    dot = -dot;
  }
  dot = std::clamp(dot, -1.0f, 1.0f);
  float wa = 1.0f - alpha;
  float wb = alpha;
  if (dot < 0.9995f) {
    const float theta = std::acos(dot);
    const float sinTheta = std::sin(theta);
    if (std::abs(sinTheta) > 1.0e-6f) {
      wa = std::sin((1.0f - alpha) * theta) / sinTheta;
      wb = std::sin(alpha * theta) / sinTheta;
    }
  }
  M14InterpQuaternion out{
      a.x * wa + b.x * wb,
      a.y * wa + b.y * wb,
      a.z * wa + b.z * wb,
      a.w * wa + b.w * wb,
  };
  const float length = std::sqrt(out.x * out.x + out.y * out.y + out.z * out.z + out.w * out.w);
  if (length > 1.0e-6f) {
    out.x /= length; out.y /= length; out.z /= length; out.w /= length;
  }
  return out;
}

Mat3x4<float> m14_interp_compose(const std::array<float, 3>& scale,
                                 const std::array<float, 3>& translation,
                                 const M14InterpQuaternion& q) noexcept {
  const float xx = q.x * q.x, yy = q.y * q.y, zz = q.z * q.z;
  const float xy = q.x * q.y, xz = q.x * q.z, yz = q.y * q.z;
  const float wx = q.w * q.x, wy = q.w * q.y, wz = q.w * q.z;
  const float r00 = 1.0f - 2.0f * (yy + zz);
  const float r01 = 2.0f * (xy - wz);
  const float r02 = 2.0f * (xz + wy);
  const float r10 = 2.0f * (xy + wz);
  const float r11 = 1.0f - 2.0f * (xx + zz);
  const float r12 = 2.0f * (yz - wx);
  const float r20 = 2.0f * (xz - wy);
  const float r21 = 2.0f * (yz + wx);
  const float r22 = 1.0f - 2.0f * (xx + yy);
  return {
      {r00 * scale[0], r01 * scale[1], r02 * scale[2], translation[0]},
      {r10 * scale[0], r11 * scale[1], r12 * scale[2], translation[1]},
      {r20 * scale[0], r21 * scale[1], r22 * scale[2], translation[2]},
  };
}

bool m14_interp_affine(const Mat3x4<float>& previous,
                       const Mat3x4<float>& current,
                       float alpha,
                       Mat3x4<float>& output) noexcept {
  if (std::memcmp(&previous, &current, sizeof(current)) == 0) {
    output = current;
    return true;
  }
  std::array<float, 3> previousScale{}, currentScale{};
  std::array<float, 3> previousTranslation{}, currentTranslation{};
  M14InterpQuaternion previousQ{}, currentQ{};
  if (!m14_interp_decompose(previous, previousScale, previousTranslation, previousQ) ||
      !m14_interp_decompose(current, currentScale, currentTranslation, currentQ)) {
    return false;
  }
  float dot = previousQ.x * currentQ.x + previousQ.y * currentQ.y +
              previousQ.z * currentQ.z + previousQ.w * currentQ.w;
  if (std::abs(dot) < 0.5f) return false;

  std::array<float, 3> scale{}, translation{};
  for (size_t i = 0; i < 3; ++i) {
    scale[i] = previousScale[i] + (currentScale[i] - previousScale[i]) * alpha;
    translation[i] = previousTranslation[i] +
                     (currentTranslation[i] - previousTranslation[i]) * alpha;
  }
  output = m14_interp_compose(scale, translation,
                              m14_interp_slerp(previousQ, currentQ, alpha));
  return m14_interp_finite(output);
}

Mat4x4<float> m14_interp_projection(const Mat4x4<float>& previous,
                                    const Mat4x4<float>& current,
                                    float alpha) noexcept {
  Mat4x4<float> out{};
  for (size_t row = 0; row < 4; ++row) {
    for (size_t column = 0; column < 4; ++column) {
      out[row][column] = previous[row][column] +
                         (current[row][column] - previous[row][column]) * alpha;
    }
  }
  return out;
}

bool m14_interp_apply_visual(const M14InterpProbeDraw& previous,
                             GXState& current,
                             float alpha) noexcept {
  if (previous.indexedMatrices || current.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE) return false;
  if (previous.currentPnMtx >= MaxPnMtx || current.currentPnMtx >= MaxPnMtx ||
      previous.currentPnMtx != current.currentPnMtx) return false;
  if (!m14_interp_finite(previous.projection) || !m14_interp_finite(current.proj)) return false;

  const u32 matrix = current.currentPnMtx;
  const float distanceSquared = [&] {
    const auto& a = previous.matrices[matrix].pos;
    const auto& b = current.pnMtx[matrix].pos;
    const float dx = b.m0.w() - a.m0.w();
    const float dy = b.m1.w() - a.m1.w();
    const float dz = b.m2.w() - a.m2.w();
    return dx * dx + dy * dy + dz * dz;
  }();
  // SPM's ordinary per-tick motion is tiny relative to this guard. A larger
  // discontinuity is a room/cut/teleport and should snap, not smear.
  if (!std::isfinite(distanceSquared) || distanceSquared > 256.0f * 256.0f) return false;

  Mat3x4<float> pos{}, nrm{};
  if (!m14_interp_affine(previous.matrices[matrix].pos, current.pnMtx[matrix].pos, alpha, pos) ||
      !m14_interp_affine(previous.matrices[matrix].nrm, current.pnMtx[matrix].nrm, alpha, nrm)) {
    return false;
  }

  current.proj = m14_interp_projection(previous.projection, current.proj, alpha);
  current.pnMtx[matrix].pos = pos;
  current.pnMtx[matrix].nrm = nrm;
  return true;
}
"""

STRUCT_OLD = """struct M14InterpProbeDraw {
  u64 identity = 0;
  Mat4x4<float> projection{};
  std::array<PnMtx, MaxPnMtx> matrices{};
  u32 currentPnMtx = 0;
  bool indexedMatrices = false;
};
"""
STRUCT_NEW = """struct M14InterpProbeDraw {
  u64 identity = 0;
  Mat4x4<float> projection{};
  std::array<PnMtx, MaxPnMtx> matrices{};
  u32 currentPnMtx = 0;
  bool indexedMatrices = false;
  size_t previousIndex = static_cast<size_t>(-1);
  bool pairSafe = false;
};
"""

RECORD_FUNCTION_START = """void m14_interp_probe_record_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
"""
RECORD_FUNCTION_END = """void m14_interp_probe_finish_frame() noexcept {
"""
RECORD_FUNCTION_NEW = r"""const M14InterpProbeDraw* m14_interp_probe_record_draw(
    GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
    gfx::PipelineRef pipeline, gfx::BindGroupRef textureBindGroup) noexcept {
  if (!m14_interp_probe_enabled() && !m14_interp_visual_enabled()) return nullptr;
  if (!sM14InterpProbeReported) {
    Log.info("M14 dev136 GX transform interpolation probe active: authoritative Wii simulation unchanged; "
             "renderer-visible projection/position/normal history is paired across completed GXCopyDisp frames.");
    sM14InterpProbeReported = true;
  }
  if (m14_interp_visual_enabled() && !sM14InterpVisualReported) {
    Log.info("M14 dev137 configurable visual transform interpolation active: mode=midpoint alpha={:.3f} "
             "target_fps={} (0=display); authoritative simulation remains 60 Hz; "
             "unmatched/indexed/discontinuous draws snap to current.",
             m14_interp_visual_alpha(), m14_interp_target_fps());
    sM14InterpVisualReported = true;
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

  const auto found = sM14InterpPreviousByIdentity.find(identity);
  if (found != sM14InterpPreviousByIdentity.end()) {
    float bestDistance = std::numeric_limits<float>::infinity();
    size_t bestIndex = static_cast<size_t>(-1);
    for (const size_t index : found->second) {
      if (index >= sM14InterpPrevious.size() || index >= sM14InterpPreviousUsed.size() ||
          sM14InterpPreviousUsed[index] != 0u) {
        continue;
      }
      const auto& previous = sM14InterpPrevious[index];
      if (previous.indexedMatrices != snapshot.indexedMatrices ||
          previous.currentPnMtx != snapshot.currentPnMtx ||
          !m14_interp_pair_safe(previous, snapshot)) {
        continue;
      }
      const float distance = m14_interp_translation_distance_squared(previous, snapshot);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    }
    if (bestIndex != static_cast<size_t>(-1)) {
      snapshot.previousIndex = bestIndex;
      snapshot.pairSafe = true;
      sM14InterpPreviousUsed[bestIndex] = 1u;
    }
  }

  sM14InterpCurrent.emplace_back(std::move(snapshot));
  const auto& recorded = sM14InterpCurrent.back();
  if (recorded.previousIndex == static_cast<size_t>(-1) ||
      recorded.previousIndex >= sM14InterpPrevious.size()) {
    return nullptr;
  }
  return &sM14InterpPrevious[recorded.previousIndex];
}

"""

FINISH_FUNCTION_START = """void m14_interp_probe_finish_frame() noexcept {
"""
FINISH_FUNCTION_END = """}
"""
# The old finish function is located structurally in transform() because its
# body contains nested braces.

def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def ensure_pair_extensions(text: str) -> str:
    """Repair the known cached dev136 duplicate-pair-safe shape, then ensure dev137 helpers once.

    A previous cumulative migration can leave two byte-identical copies of the
    dev136 m14_interp_pair_safe helper while the rest of the probe remains
    singular. Two identical static helpers are never semantically useful and
    will not compile, so this exact fragment is safe to deduplicate. Unknown or
    partially recognizable dev137 helper drift still fails closed.
    """
    pair_count = text.count(PAIR_SAFE_ANCHOR)
    if pair_count == 0:
        raise RuntimeError("dev137 interpolation math/helpers: dev136 pair-safe helper missing")
    if pair_count > 1:
        first = text.find(PAIR_SAFE_ANCHOR)
        cursor = first + len(PAIR_SAFE_ANCHOR)
        while True:
            duplicate = text.find(PAIR_SAFE_ANCHOR, cursor)
            if duplicate < 0:
                break
            text = text[:duplicate] + text[duplicate + len(PAIR_SAFE_ANCHOR):]
            cursor = duplicate

    # Exact duplicate dev137 extension blocks are likewise a known interrupted
    # migration shape. Keep the first byte-identical block and discard only
    # exact later copies; never guess at structurally different C++.
    extension_count = text.count(PAIR_EXTENSIONS)
    if extension_count > 1:
        first = text.find(PAIR_EXTENSIONS)
        cursor = first + len(PAIR_EXTENSIONS)
        while True:
            duplicate = text.find(PAIR_EXTENSIONS, cursor)
            if duplicate < 0:
                break
            text = text[:duplicate] + text[duplicate + len(PAIR_EXTENSIONS):]
            cursor = duplicate

    helper_markers = (
        "struct M14InterpQuaternion {",
        "std::unordered_map<u64, std::vector<size_t>> sM14InterpPreviousByIdentity;",
        "bool m14_interp_feature_enabled() noexcept {",
        "M14InterpQuaternion m14_interp_slerp(",
        "bool m14_interp_apply_visual(",
    )
    helper_counts = [text.count(marker) for marker in helper_markers]
    if all(count == 0 for count in helper_counts):
        if text.count(PAIR_SAFE_ANCHOR) != 1:
            raise RuntimeError(
                "dev137 interpolation math/helpers: pair-safe normalization did not converge"
            )
        return text.replace(PAIR_SAFE_ANCHOR, PAIR_SAFE_ANCHOR + PAIR_EXTENSIONS, 1)

    if not all(count == 1 for count in helper_counts):
        details = ", ".join(
            f"{marker}={count}" for marker, count in zip(helper_markers, helper_counts)
        )
        raise RuntimeError(
            "dev137 interpolation math/helpers: partial or duplicate helper drift after exact repair: "
            + details
        )

    return text


def ensure_include(text: str, include: str, anchor: str, label: str) -> str:
    """Insert one include after a stable anchor without requiring anchor uniqueness.

    M14's cached Aurora tree is the cumulative result of many idempotent source
    migrations, so generic STL includes such as <unordered_map> can legitimately
    appear more than once. Include presence is what matters here; requiring a
    generic include anchor to be unique makes the migration cache-fragile.
    """
    if include in text:
        return text
    at = text.find(anchor)
    if at < 0:
        raise RuntimeError(f"{label}: stable include anchor missing")
    at += len(anchor)
    return text[:at] + include + text[at:]


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


def ensure_field_in_braced_region(text: str, marker: str, anchor: str,
                                  field: str, label: str) -> str:
    start, end = braced_region(text, marker, label)
    region = text[start:end]
    if field.strip() in region:
        return text
    at = region.find(anchor)
    if at < 0:
        raise RuntimeError(f"{label}: field anchor missing")
    at += len(anchor)
    region = region[:at] + field + region[at:]
    return text[:start] + region + text[end:]


def ensure_after_all_in_braced_region(text: str, marker: str, anchor: str,
                                      addition: str, label: str) -> str:
    start, end = braced_region(text, marker, label)
    region = text[start:end]
    count = region.count(anchor)
    if count == 0:
        raise RuntimeError(f"{label}: anchor missing")
    # Apply to every matching initializer/assignment in the function. This is
    # important for M14's zCompLoc dual-pass lambda, which reshaped the pristine
    # one-shot DrawData initializer.
    region = region.replace(anchor + addition, anchor)
    region = region.replace(anchor, anchor + addition)
    return text[:start] + region + text[end:]


def select_visual_uniform_in_push(text: str) -> str:
    start, end = braced_region(text, "static void push_gx_draw(",
                               "dev137 DrawData uniform selection")
    region = text[start:end]
    authoritative = ".uniformRange = cache.uniformRange,"
    selected = (
        ".uniformRange = (m14_interp_visual_enabled() && m14InterpUniformRange.size != 0)\n"
        "                            ? m14InterpUniformRange\n"
        "                            : cache.uniformRange,"
    )
    if "m14InterpUniformRange.size != 0" in region:
        return text
    count = region.count(authoritative)
    if count == 0:
        raise RuntimeError("dev137 DrawData uniform selection: authoritative initializer missing")
    region = region.replace(authoritative, selected)
    return text[:start] + region + text[end:]


def ensure_after_braced_region(text: str, marker: str, addition: str,
                               signature: str, label: str) -> str:
    if signature in text:
        return text
    _start, end = braced_region(text, marker, label)
    return text[:end] + addition + text[end:]


def insert_after_statement_block(text: str, function_marker: str,
                                 statement_marker: str, addition: str,
                                 signature: str, label: str) -> str:
    if signature in text:
        return text
    start, end = braced_region(text, function_marker, label)
    statement = text.find(statement_marker, start, end)
    if statement < 0:
        raise RuntimeError(f"{label}: statement marker missing")
    block = text.find("{", statement, end)
    if block < 0:
        raise RuntimeError(f"{label}: statement block missing")
    depth = 0
    block_end = -1
    for i in range(block, end):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                block_end = i + 1
                break
    if block_end < 0:
        raise RuntimeError(f"{label}: statement braces are unbalanced")
    return text[:block_end] + addition + text[block_end:]


def replace_function(text: str, start_marker: str, next_marker: str,
                     replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker missing")
    end = text.find(next_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: successor marker missing")
    return text[:start] + replacement + text[end:]


def replace_finish_function(text: str) -> str:
    start = text.find(FINISH_FUNCTION_START)
    if start < 0:
        raise RuntimeError("dev136 finish function missing")
    brace = text.find("{", start)
    depth = 0
    end = -1
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise RuntimeError("dev136 finish function braces are unbalanced")
    replacement = r"""void m14_interp_probe_finish_frame() noexcept {
  if (!m14_interp_probe_enabled() && !m14_interp_visual_enabled()) return;
  ++sM14InterpFrameCount;

  u64 matches = 0;
  u64 moving = 0;
  u64 safeMidpoints = 0;
  u64 indexedMatches = 0;
  u64 rigidMatches = 0;

  for (const auto& current : sM14InterpCurrent) {
    if (current.previousIndex == static_cast<size_t>(-1) ||
        current.previousIndex >= sM14InterpPrevious.size()) {
      continue;
    }
    const auto& previous = sM14InterpPrevious[current.previousIndex];
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
    if (current.pairSafe) ++safeMidpoints;
  }

  const bool report = sM14InterpFrameCount <= 8u || (sM14InterpFrameCount % 120u) == 0u;
  if (report) {
    const u64 candidates = sM14InterpCurrent.size();
    const double matchPercent =
        candidates != 0 ? (100.0 * static_cast<double>(matches) / static_cast<double>(candidates)) : 0.0;
    const double safePercent =
        matches != 0 ? (100.0 * static_cast<double>(safeMidpoints) / static_cast<double>(matches)) : 0.0;
    Log.info("M14 dev137 interpolation pairing frame={} candidates={} previous={} matches={} match_pct={:.1f} "
             "moving={} safe_midpoints={} safe_pct={:.1f} rigid_matches={} indexed_matches={}",
             sM14InterpFrameCount, candidates, sM14InterpPrevious.size(), matches, matchPercent,
             moving, safeMidpoints, safePercent, rigidMatches, indexedMatches);
  }

  sM14InterpPrevious.swap(sM14InterpCurrent);
  sM14InterpCurrent.clear();
  sM14InterpPreviousByIdentity.clear();
  sM14InterpPreviousByIdentity.reserve(sM14InterpPrevious.size());
  for (size_t i = 0; i < sM14InterpPrevious.size(); ++i) {
    sM14InterpPreviousByIdentity[sM14InterpPrevious[i].identity].push_back(i);
  }
  sM14InterpPreviousUsed.assign(sM14InterpPrevious.size(), 0u);
}"""
    return text[:start] + replacement + text[end:]


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / CP
    cp = cp_path.read_text(encoding="utf-8")

    if SIGNATURE in cp and "m14InterpUniformRange.size != 0" in cp:
        joined = cp
        required = (
            "m14_interp_slerp",
            "sM14InterpPreviousByIdentity",
            "OPENSPM_INTERPOLATION_ALPHA",
            "OPENSPM_INTERPOLATION_TARGET_FPS",
            "m14InterpUniformRange",
        )
        missing = [item for item in required if item not in joined]
        if missing:
            raise RuntimeError("dev137 signature incomplete: " + ", ".join(missing))
        return "already-applied"

    if "M14 dev136 GX transform interpolation probe active" not in cp:
        raise RuntimeError("dev136 transform history must be applied first")

    # dev137 uses std::strcmp/memcmp and numeric_limits in the hot-path helpers.
    # Generic STL includes can appear multiple times in the cumulative cached
    # Aurora source after older M14 migrations. Anchor additions on Tracy's
    # stable local include, but never require that anchor/include neighborhood
    # to be globally unique.
    stable_include_anchor = "#include <tracy/Tracy.hpp>\n"
    cp = ensure_include(cp, "#include <cstring>\n", stable_include_anchor,
                        "command_processor cstring include")
    cp = ensure_include(cp, "#include <limits>\n", stable_include_anchor,
                        "command_processor limits include")

    # Cumulative M14 patches have legitimately reshaped push_gx_draw (notably
    # zCompLoc's m14PushDraw lambda). Keep the visual proof local to that
    # function so Aurora's already-full 128-byte DrawData payload is untouched.
    cp = replace_once(cp, STRUCT_OLD, STRUCT_NEW, "dev136 draw pairing metadata")
    cp = ensure_pair_extensions(cp)
    cp = replace_function(cp, RECORD_FUNCTION_START, RECORD_FUNCTION_END,
                          RECORD_FUNCTION_NEW, "dev137 draw matcher")
    cp = replace_finish_function(cp)

    record_call_old = (
        "  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, "
        "cache.bindGroups.textureBindGroup);\n"
    )
    record_call_new = (
        "  const auto* m14InterpPrevious =\n"
        "      m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef, "
        "cache.bindGroups.textureBindGroup);\n"
    )
    push_start, push_end = braced_region(cp, "static void push_gx_draw(", "dev137 push_gx_draw")
    push_region = cp[push_start:push_end]
    if "const auto* m14InterpPrevious =" not in push_region:
        if record_call_old not in push_region:
            raise RuntimeError("dev137 draw capture: dev136 call missing in push_gx_draw")
        push_region = push_region.replace(record_call_old, record_call_new, 1)
        cp = cp[:push_start] + push_region + cp[push_end:]

    uniform_addition = (
        "\n\n  gfx::Range m14InterpUniformRange{};\n"
        "  if (m14_interp_visual_enabled() && m14InterpPrevious != nullptr) {\n"
        "    const auto savedProjection = state.proj;\n"
        "    const auto savedMatrices = state.pnMtx;\n"
        "    if (m14_interp_apply_visual(*m14InterpPrevious, state, m14_interp_visual_alpha())) {\n"
        "      m14InterpUniformRange = build_uniform(cache.shaderInfo);\n"
        "    }\n"
        "    state.proj = savedProjection;\n"
        "    state.pnMtx = savedMatrices;\n"
        "  }"
    )
    cp = insert_after_statement_block(
        cp, "static void push_gx_draw(", "if (!uniformValid) {", uniform_addition,
        "m14InterpUniformRange = build_uniform(cache.shaderInfo);",
        "dev137 interpolated uniform staging")

    # zCompLoc may wrap DrawData construction in a dual-pass lambda. Select the
    # temporary midpoint uniform through the existing DrawData.uniformRange
    # field so the 128-byte inline payload stays ABI-identical.
    cp = select_visual_uniform_in_push(cp)

    joined = cp
    required = (
        SIGNATURE,
        "m14_interp_slerp",
        "sM14InterpPreviousByIdentity",
        "distanceSquared > 256.0f * 256.0f",
        "OPENSPM_INTERPOLATION_ALPHA",
        "OPENSPM_INTERPOLATION_TARGET_FPS",
        "m14InterpUniformRange",
        "m14InterpUniformRange.size != 0",
    )
    missing = [item for item in required if item not in joined]
    if missing:
        raise RuntimeError("dev137 migration incomplete: " + ", ".join(missing))

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora dev137 interpolation adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-applied": "Aurora dev137 visual interpolation already applied",
        "verified": "Aurora dev137 visual interpolation verified",
        "applied": "Aurora dev137 visual interpolation applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
