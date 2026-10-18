#pragma once

// OpenSPM Stage C frame interpolation, pure model (docs/PRESENTATION-ARCHITECTURE.md).
//
// No Aurora, no WebGPU, no clocks: given the draw lists of two finished game
// frames A and B (each GX draw with its game-provided owner from Stage B and
// its recorded uniform block), plan_pair() decides which draws can be
// interpolated and precomputes everything that does not depend on alpha;
// apply_alpha() then writes the uniform arena for one scan-out time.
//
// Interpolation of a draw's position matrix PN (GX model-view) and normal
// matrix N, with camera views V_A, V_B sent by the game (Stage C view
// records, BP 0x7C/0x7D):
//
//   M_A = V_A^-1 PN_A,  M_B = V_B^-1 PN_B          (object -> world)
//   PN' = V(alpha) * interp(M_A, M_B, alpha)
//   V(alpha): orientation slerp, camera position lerp
//   interp:   rotation slerp + per-axis scale lerp + translation lerp
//             (element-wise lerp when the 3x3 part is sheared, mirrored or
//             changes handedness, e.g. a paper sprite turning around)
//
// Without a valid view pair (camera not reported, or not rigid), V = identity:
// PN itself is interpolated. For a camera that only translates (SPM's 2D side
// scrolling) this is exactly the same result; with camera rotation it differs
// only in second order (view-space chord instead of arc).
//
// Matching is by (owner id, ordinal of the draw within that owner in stream
// order), never by geometry, so the per-frame z sort cannot break it. Safety:
// an owner whose draw count or per-draw structure differs between A and B, or
// that moved/rotated too far in one frame, snaps to its B pose (keeping the
// interpolated camera when views are known). Camera cuts and scene changes
// reject the whole pair. 2D/UI (orthographic) cameras and untagged draws keep
// B's uniform block byte for byte.
//
// Unit tested (byte-exact endpoints, snap rules, cuts, UI, reordering) and
// benchmarked by tests/m14_frame_interp_test.cpp via test_display_pacing.py.

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <vector>

namespace openspm::interp {

inline constexpr uint32_t kMaxPnMtx = 10;
inline constexpr uint32_t kCameraSlots = 16;
inline constexpr uint32_t kCameraNone = 15;
inline constexpr uint32_t kMatrixBytes = 48;  // GX 3x4 float matrix
// Uniform block layout after the viewport prefix (16 or 32 bytes): projection
// (4x4), 10 position + 10 texture matrices, then 10 normal matrices.
inline constexpr uint32_t kPosOffset = 64;
inline constexpr uint32_t kNrmOffset = 64 + 20 * kMatrixBytes;
inline constexpr uint32_t kMatricesEnd = kNrmOffset + kMaxPnMtx * kMatrixBytes;

struct Mat34 {
  float r[3][4];  // x' = r[i][0..2] . x + r[i][3] (GX row-major, Aurora Mat3x4 rows)
};

inline Mat34 load_mat(const uint8_t* p) noexcept {
  Mat34 m;
  std::memcpy(&m, p, sizeof(m));
  return m;
}
inline void store_mat(uint8_t* p, const Mat34& m) noexcept { std::memcpy(p, &m, sizeof(m)); }

inline bool finite(const Mat34& m) noexcept {
  for (const auto& row : m.r)
    for (float v : row)
      if (!std::isfinite(v)) return false;
  return true;
}

inline Mat34 mul(const Mat34& a, const Mat34& b) noexcept {
  Mat34 c;
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 4; ++j) {
      float v = a.r[i][0] * b.r[0][j] + a.r[i][1] * b.r[1][j] + a.r[i][2] * b.r[2][j];
      if (j == 3) v += a.r[i][3];
      c.r[i][j] = v;
    }
  }
  return c;
}

struct Quat {
  float w = 1, x = 0, y = 0, z = 0;
};

inline Quat quat_from_rotation(const float m[3][3]) noexcept {
  Quat q;
  const float trace = m[0][0] + m[1][1] + m[2][2];
  if (trace > 0.0f) {
    const float s = std::sqrt(trace + 1.0f) * 2.0f;
    q.w = 0.25f * s;
    q.x = (m[2][1] - m[1][2]) / s;
    q.y = (m[0][2] - m[2][0]) / s;
    q.z = (m[1][0] - m[0][1]) / s;
  } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
    const float s = std::sqrt(1.0f + m[0][0] - m[1][1] - m[2][2]) * 2.0f;
    q.w = (m[2][1] - m[1][2]) / s;
    q.x = 0.25f * s;
    q.y = (m[0][1] + m[1][0]) / s;
    q.z = (m[0][2] + m[2][0]) / s;
  } else if (m[1][1] > m[2][2]) {
    const float s = std::sqrt(1.0f + m[1][1] - m[0][0] - m[2][2]) * 2.0f;
    q.w = (m[0][2] - m[2][0]) / s;
    q.x = (m[0][1] + m[1][0]) / s;
    q.y = 0.25f * s;
    q.z = (m[1][2] + m[2][1]) / s;
  } else {
    const float s = std::sqrt(1.0f + m[2][2] - m[0][0] - m[1][1]) * 2.0f;
    q.w = (m[1][0] - m[0][1]) / s;
    q.x = (m[0][2] + m[2][0]) / s;
    q.y = (m[1][2] + m[2][1]) / s;
    q.z = 0.25f * s;
  }
  const float n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
  q.w /= n;
  q.x /= n;
  q.y /= n;
  q.z /= n;
  return q;
}

inline void rotation_from_quat(const Quat& q, float m[3][3]) noexcept {
  const float xx = q.x * q.x, yy = q.y * q.y, zz = q.z * q.z;
  const float xy = q.x * q.y, xz = q.x * q.z, yz = q.y * q.z;
  const float wx = q.w * q.x, wy = q.w * q.y, wz = q.w * q.z;
  m[0][0] = 1 - 2 * (yy + zz);
  m[0][1] = 2 * (xy - wz);
  m[0][2] = 2 * (xz + wy);
  m[1][0] = 2 * (xy + wz);
  m[1][1] = 1 - 2 * (xx + zz);
  m[1][2] = 2 * (yz - wx);
  m[2][0] = 2 * (xz - wy);
  m[2][1] = 2 * (yz + wx);
  m[2][2] = 1 - 2 * (xx + yy);
}

inline float quat_dot(const Quat& a, const Quat& b) noexcept {
  return a.w * b.w + a.x * b.x + a.y * b.y + a.z * b.z;
}

// Rotation angle between two orientations, degrees.
inline float quat_angle_deg(const Quat& a, const Quat& b) noexcept {
  const float d = std::min(1.0f, std::fabs(quat_dot(a, b)));
  return 2.0f * std::acos(d) * 57.29577951308232f;
}

inline Quat slerp(const Quat& a, Quat b, float t) noexcept {
  float d = quat_dot(a, b);
  if (d < 0.0f) {  // shortest path
    b = {-b.w, -b.x, -b.y, -b.z};
    d = -d;
  }
  float wa, wb;
  if (d > 0.9995f) {  // nearly identical: normalized lerp
    wa = 1.0f - t;
    wb = t;
  } else {
    const float theta = std::acos(d);
    const float s = std::sin(theta);
    wa = std::sin((1.0f - t) * theta) / s;
    wb = std::sin(t * theta) / s;
  }
  Quat q{wa * a.w + wb * b.w, wa * a.x + wb * b.x, wa * a.y + wb * b.y, wa * a.z + wb * b.z};
  const float n = std::sqrt(quat_dot(q, q));
  q.w /= n;
  q.x /= n;
  q.y /= n;
  q.z /= n;
  return q;
}

// Affine 3x4 = R * diag(s) (+ t), R a proper rotation.
struct Rigid {
  Quat q;
  float s[3] = {1, 1, 1};
  float t[3] = {0, 0, 0};
};

// Fails for degenerate, sheared or left-handed bases (callers then lerp
// element-wise, which is also the right thing for a sprite whose x scale
// flips sign).
inline bool decompose(const Mat34& m, Rigid& out) noexcept {
  float c[3][3];  // c[j] = column j
  for (int j = 0; j < 3; ++j) {
    for (int i = 0; i < 3; ++i) c[j][i] = m.r[i][j];
    const float n = std::sqrt(c[j][0] * c[j][0] + c[j][1] * c[j][1] + c[j][2] * c[j][2]);
    if (!(n > 1.0e-6f) || !std::isfinite(n)) return false;
    out.s[j] = n;
    for (int i = 0; i < 3; ++i) c[j][i] /= n;
  }
  for (int a = 0; a < 3; ++a) {
    for (int b = a + 1; b < 3; ++b) {
      const float d = c[a][0] * c[b][0] + c[a][1] * c[b][1] + c[a][2] * c[b][2];
      if (std::fabs(d) > 2.0e-3f) return false;  // shear
    }
  }
  const float det = c[0][0] * (c[1][1] * c[2][2] - c[1][2] * c[2][1]) -
                    c[1][0] * (c[0][1] * c[2][2] - c[0][2] * c[2][1]) +
                    c[2][0] * (c[0][1] * c[1][2] - c[0][2] * c[1][1]);
  if (det < 0.0f) return false;  // mirrored
  float rot[3][3];
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) rot[i][j] = c[j][i];
  out.q = quat_from_rotation(rot);
  for (int i = 0; i < 3; ++i) out.t[i] = m.r[i][3];
  return true;
}

inline Mat34 compose(const Rigid& g) noexcept {
  float rot[3][3];
  rotation_from_quat(g.q, rot);
  Mat34 m;
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 3; ++j) m.r[i][j] = rot[i][j] * g.s[j];
    m.r[i][3] = g.t[i];
  }
  return m;
}

inline float lerpf(float a, float b, float t) noexcept { return a + (b - a) * t; }

// A camera view matrix (world -> view), accepted only when rigid.
struct View {
  bool valid = false;
  Quat q;          // orientation of R
  float c[3] = {};  // camera position in world space (-R^T t)
  float rot[3][3] = {};
};

inline View make_view(const Mat34& v) noexcept {
  View out;
  if (!finite(v)) return out;
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) out.rot[i][j] = v.r[i][j];
  for (int a = 0; a < 3; ++a) {
    const float n = out.rot[a][0] * out.rot[a][0] + out.rot[a][1] * out.rot[a][1] + out.rot[a][2] * out.rot[a][2];
    if (std::fabs(n - 1.0f) > 2.0e-3f) return out;
    for (int b = a + 1; b < 3; ++b) {
      const float d = out.rot[a][0] * out.rot[b][0] + out.rot[a][1] * out.rot[b][1] + out.rot[a][2] * out.rot[b][2];
      if (std::fabs(d) > 2.0e-3f) return out;
    }
  }
  const auto& m = out.rot;
  const float det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
                    m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
                    m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  if (det < 0.0f) return out;
  for (int j = 0; j < 3; ++j)
    out.c[j] = -(m[0][j] * v.r[0][3] + m[1][j] * v.r[1][3] + m[2][j] * v.r[2][3]);
  out.q = quat_from_rotation(out.rot);
  out.valid = true;
  return out;
}

// V^-1 as an affine matrix: [R^T | c].
inline Mat34 view_inverse(const View& v) noexcept {
  Mat34 m;
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 3; ++j) m.r[i][j] = v.rot[j][i];
    m.r[i][3] = v.c[i];
  }
  return m;
}

// V(alpha): slerped orientation, lerped camera position.
inline Mat34 view_at(const View& a, const View& b, float t) noexcept {
  const Quat q = slerp(a.q, b.q, t);
  float rot[3][3];
  rotation_from_quat(q, rot);
  float c[3];
  for (int i = 0; i < 3; ++i) c[i] = lerpf(a.c[i], b.c[i], t);
  Mat34 m;
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 3; ++j) m.r[i][j] = rot[i][j];
    m.r[i][3] = -(rot[i][0] * c[0] + rot[i][1] * c[1] + rot[i][2] * c[2]);
  }
  return m;
}

// ---------------------------------------------------------------- records

struct Draw {
  uint32_t owner = 0;          // Stage B owner, 0 = untagged
  uint8_t camera = kCameraNone;
  bool ortho = false;          // 2D/UI camera
  bool indexed = false;        // per-vertex PN matrix index (skinned)
  uint8_t pnSlot = 0;          // current PN matrix
  uint8_t prefix = 16;         // uniform prefix bytes (16, or 32 for lines/points)
  uint16_t view = 0;           // 1-based index into Frame::views, 0 = no view record
  uint32_t uniformOffset = 0;  // into Frame::uniforms
  uint32_t uniformSize = 0;
  uint32_t vtxCount = 0;       // structure signature
  uint32_t indexCount = 0;
};

struct Frame {
  uint64_t id = 0;
  std::vector<Draw> draws;       // GX draws in stream order
  std::vector<uint8_t> uniforms; // exact copy of the frame's uniform arena
  std::vector<Mat34> views;      // view records in stream order
};

struct Config {
  float teleportDistance = 100.0f;  // object moves farther in one frame: snap owner
  float cutDistance = 150.0f;       // camera (or median draw, without views) jump: reject pair
  float cutAngleDeg = 45.0f;        // camera rotation in one frame: reject pair
  float maxObjectAngleDeg = 150.0f; // object rotation in one frame: snap owner
  float maxLinearChange = 1.5f;     // element-wise lerp only for modest 3x3 changes (a sprite turning around is ~1.15)
  float minMatchedShare = 0.25f;    // fewer matched 3D draws: scene change
  uint32_t minSceneDraws = 16;      // scene-change rule needs this many 3D draws
  bool useViews = true;             // OPENSPM_M14_INTERP_VIEW=0: interpolate PN directly
  uint32_t alignment = 256;         // uniform dynamic-offset alignment
};

enum class PairResult : uint8_t {
  Ok,
  NoDraws,      // no tagged 3D draws in B
  CameraCut,
  SceneChange,
  Count
};
inline const char* pair_result_name(PairResult r) noexcept {
  switch (r) {
  case PairResult::Ok: return "ok";
  case PairResult::NoDraws: return "no-3d-draws";
  case PairResult::CameraCut: return "camera-cut";
  case PairResult::SceneChange: return "scene-change";
  default: return "?";
  }
}

enum class Snap : uint8_t { Spawned, CountChanged, StructureChanged, Teleport, Rotation, Count };

enum class Mode : uint8_t {
  Rigid,   // slerp rotation, lerp scale and translation
  Linear,  // element-wise lerp of the 3x3 part, lerp translation
  Snap,    // object pose of B (with the interpolated camera when views are known)
  Keep,    // leave B's bytes
};

struct Slot {
  uint32_t pos = 0;             // arena byte offset of the position matrix
  uint32_t nrm = 0;             // arena byte offset of the normal matrix
  uint16_t viewPair = 0xFFFF;   // index into PairPlan::viewPairs, 0xFFFF = none
  Mode posMode = Mode::Keep;
  Mode nrmMode = Mode::Keep;
  Mat34 posA{}, posB{};          // object space (world when a view pair is known)
  Mat34 nrmA{}, nrmB{};          // normal 3x3 with the camera rotation removed
  Rigid rposA, rposB, rnrmA, rnrmB;
  std::array<uint8_t, kMatrixBytes> rawPosA{}, rawPosB{}, rawNrmA{}, rawNrmB{};
};

struct ViewPair {
  View a, b;
};

struct PairStats {
  uint32_t draws = 0;           // GX draws in B
  uint32_t eligible = 0;        // tagged 3D draws in B
  uint32_t matched = 0;         // interpolated draws
  uint32_t snappedDraws = 0;
  uint32_t owners = 0;          // tagged 3D owners in B
  uint32_t matchedOwners = 0;
  std::array<uint32_t, static_cast<size_t>(Snap::Count)> snappedOwners{};
  uint32_t blocks = 0;
  uint32_t slots = 0;
  uint32_t viewDraws = 0;       // eligible draws with a valid view pair
  uint32_t staticDraws = 0;     // matched draws whose world pose did not change (view check)
  uint32_t arenaBytes = 0;
};

struct PairPlan {
  PairResult result = PairResult::NoDraws;
  uint64_t idA = 0, idB = 0;
  uint32_t arenaBase = 0;              // absolute offset of the arena in B's uniform buffer
  std::vector<uint32_t> drawUniform;   // per B draw: uniform offset to bind
  std::vector<uint8_t> arena;          // patched blocks (template = B's bytes)
  std::vector<Slot> slots;
  std::vector<ViewPair> viewPairs;
  PairStats stats;
};

namespace detail {

inline uint32_t align_up(uint32_t v, uint32_t a) noexcept { return a <= 1 ? v : (v + a - 1) / a * a; }

inline bool eligible(const Draw& d, const Frame& f) noexcept {
  if (d.owner == 0 || d.ortho || d.camera >= kCameraNone) return false;
  if (d.prefix != 16 && d.prefix != 32) return false;
  const uint64_t end = uint64_t(d.uniformOffset) + d.uniformSize;
  return d.uniformSize >= d.prefix + kMatricesEnd && end <= f.uniforms.size();
}

inline bool same_structure(const Draw& a, const Draw& b) noexcept {
  return a.indexed == b.indexed && a.pnSlot == b.pnSlot && a.prefix == b.prefix && a.vtxCount == b.vtxCount &&
         a.indexCount == b.indexCount && a.uniformSize == b.uniformSize && a.camera == b.camera;
}

inline const Mat34* view_of(const Draw& d, const Frame& f) noexcept {
  return d.view != 0 && d.view <= f.views.size() ? &f.views[d.view - 1] : nullptr;
}

inline float dist3(const float a[3], const float b[3]) noexcept {
  const float x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2];
  return std::sqrt(x * x + y * y + z * z);
}

inline float mat_translation_delta(const Mat34& a, const Mat34& b) noexcept {
  const float x = a.r[0][3] - b.r[0][3], y = a.r[1][3] - b.r[1][3], z = a.r[2][3] - b.r[2][3];
  return std::sqrt(x * x + y * y + z * z);
}

// Remove the camera rotation from a normal matrix: R_V^T * N (3x3 only).
inline Mat34 unrotate(const View& v, const Mat34& n) noexcept {
  Mat34 m = n;
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) m.r[i][j] = v.rot[0][i] * n.r[0][j] + v.rot[1][i] * n.r[1][j] + v.rot[2][i] * n.r[2][j];
  return m;
}

inline float linear_change(const Mat34& a, const Mat34& b) noexcept {
  float diff = 0.0f, norm = 0.0f;
  for (int i = 0; i < 3; ++i) {
    for (int j = 0; j < 3; ++j) {
      const float d = a.r[i][j] - b.r[i][j];
      diff += d * d;
      norm += b.r[i][j] * b.r[i][j];
    }
  }
  return norm > 0.0f ? std::sqrt(diff / norm) : (diff > 0.0f ? 1.0e9f : 0.0f);
}

// Choose how one matrix pair is interpolated. Rigid needs both decompositions
// and a plausible rotation; otherwise element-wise lerp while the change is
// modest; otherwise the object pose snaps.
inline Mode choose_mode(const Mat34& a, const Mat34& b, Rigid& ra, Rigid& rb, const Config& cfg,
                        bool& rotationSnap) noexcept {
  rotationSnap = false;
  if (decompose(a, ra) && decompose(b, rb)) {
    if (quat_angle_deg(ra.q, rb.q) <= cfg.maxObjectAngleDeg) return Mode::Rigid;
    rotationSnap = true;
    return Mode::Snap;
  }
  if (linear_change(a, b) <= cfg.maxLinearChange) return Mode::Linear;
  rotationSnap = true;
  return Mode::Snap;
}

inline Mat34 interp(const Mat34& a, const Mat34& b, const Rigid& ra, const Rigid& rb, Mode mode,
                    float t) noexcept {
  if (mode == Mode::Rigid) {
    Rigid g;
    g.q = slerp(ra.q, rb.q, t);
    for (int i = 0; i < 3; ++i) {
      g.s[i] = lerpf(ra.s[i], rb.s[i], t);
      g.t[i] = lerpf(ra.t[i], rb.t[i], t);
    }
    return compose(g);
  }
  if (mode == Mode::Linear) {
    Mat34 m;
    for (int i = 0; i < 3; ++i)
      for (int j = 0; j < 4; ++j) m.r[i][j] = lerpf(a.r[i][j], b.r[i][j], t);
    return m;
  }
  return b;
}

inline float median(std::vector<float>& v) noexcept {
  const size_t mid = v.size() / 2;
  std::nth_element(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(mid), v.end());
  return v[mid];
}

} // namespace detail

// Plan the interpolation of pair (A, B). Everything alpha-independent is done
// here, once per game frame; apply_alpha() is the per-refresh part.
inline PairPlan plan_pair(const Frame& A, const Frame& B, const Config& cfg) {
  using namespace detail;
  PairPlan plan;
  plan.idA = A.id;
  plan.idB = B.id;
  plan.stats.draws = static_cast<uint32_t>(B.draws.size());
  plan.arenaBase = align_up(static_cast<uint32_t>(B.uniforms.size()), cfg.alignment);
  plan.drawUniform.resize(B.draws.size());
  for (size_t i = 0; i < B.draws.size(); ++i) plan.drawUniform[i] = B.draws[i].uniformOffset;

  // Owner -> eligible draw indices in stream order.
  std::unordered_map<uint32_t, std::vector<uint32_t>> ownersA, ownersB;
  std::vector<uint32_t> orderB;  // owners in first-seen order (deterministic)
  ownersA.reserve(A.draws.size());
  ownersB.reserve(B.draws.size());
  for (uint32_t i = 0; i < A.draws.size(); ++i)
    if (eligible(A.draws[i], A)) ownersA[A.draws[i].owner].push_back(i);
  for (uint32_t i = 0; i < B.draws.size(); ++i) {
    if (!eligible(B.draws[i], B)) continue;
    auto& list = ownersB[B.draws[i].owner];
    if (list.empty()) orderB.push_back(B.draws[i].owner);
    list.push_back(i);
    ++plan.stats.eligible;
  }
  plan.stats.owners = static_cast<uint32_t>(orderB.size());
  if (plan.stats.eligible == 0) {
    plan.result = PairResult::NoDraws;
    return plan;
  }

  // View pairs: one per distinct (view record in A, view record in B).
  std::unordered_map<uint32_t, uint16_t> viewPairIndex;
  const auto view_pair = [&](const Draw& da, const Draw& db) -> uint16_t {
    if (!cfg.useViews) return 0xFFFF;
    const Mat34* va = view_of(da, A);
    const Mat34* vb = view_of(db, B);
    if (va == nullptr || vb == nullptr) return 0xFFFF;
    const uint32_t key = (uint32_t(da.view) << 16) | db.view;
    const auto it = viewPairIndex.find(key);
    if (it != viewPairIndex.end()) return it->second;
    ViewPair pair{make_view(*va), make_view(*vb)};
    uint16_t index = 0xFFFF;
    if (pair.a.valid && pair.b.valid && plan.viewPairs.size() < 0xFFFF) {
      index = static_cast<uint16_t>(plan.viewPairs.size());
      plan.viewPairs.push_back(pair);
    }
    viewPairIndex.emplace(key, index);
    return index;
  };

  // Pass 1: owner matching and safety rules.
  struct OwnerMatch {
    bool matched = false;
  };
  std::unordered_map<uint32_t, OwnerMatch> ownerState;
  ownerState.reserve(orderB.size());
  std::array<std::vector<float>, kCameraSlots> cameraDeltas;  // direct-mode cut detection
  std::array<int, kCameraSlots> cameraViewPair;
  cameraViewPair.fill(-1);
  for (const uint32_t owner : orderB) {
    const auto& listB = ownersB[owner];
    const auto found = ownersA.find(owner);
    Snap snap = Snap::Count;
    if (found == ownersA.end()) {
      snap = Snap::Spawned;
    } else if (found->second.size() != listB.size()) {
      snap = Snap::CountChanged;
    } else {
      for (size_t k = 0; k < listB.size() && snap == Snap::Count; ++k)
        if (!same_structure(A.draws[found->second[k]], B.draws[listB[k]])) snap = Snap::StructureChanged;
    }
    if (snap == Snap::Count) {
      // Teleport / rotation on each draw's current matrix.
      for (size_t k = 0; k < listB.size() && snap == Snap::Count; ++k) {
        const Draw& da = A.draws[found->second[k]];
        const Draw& db = B.draws[listB[k]];
        const uint32_t off = db.prefix + kPosOffset + db.pnSlot * kMatrixBytes;
        Mat34 pa = load_mat(A.uniforms.data() + da.uniformOffset + off);
        Mat34 pb = load_mat(B.uniforms.data() + db.uniformOffset + off);
        if (!finite(pa) || !finite(pb)) continue;
        const uint16_t vp = view_pair(da, db);
        if (vp != 0xFFFF) {
          pa = mul(view_inverse(plan.viewPairs[vp].a), pa);
          pb = mul(view_inverse(plan.viewPairs[vp].b), pb);
          if (db.camera < kCameraSlots && cameraViewPair[db.camera] < 0) cameraViewPair[db.camera] = vp;
        } else if (db.camera < kCameraSlots) {
          cameraDeltas[db.camera].push_back(mat_translation_delta(pa, pb));
        }
        if (mat_translation_delta(pa, pb) > cfg.teleportDistance) snap = Snap::Teleport;
        Rigid ra, rb;
        bool rotationSnap = false;
        (void)choose_mode(pa, pb, ra, rb, cfg, rotationSnap);
        if (rotationSnap) snap = Snap::Rotation;
      }
    }
    if (snap == Snap::Count) {
      ownerState[owner].matched = true;
      ++plan.stats.matchedOwners;
    } else {
      ++plan.stats.snappedOwners[static_cast<size_t>(snap)];
    }
  }

  // Camera cuts: with views, the camera itself; without, the median motion.
  for (uint32_t c = 0; c < kCameraSlots; ++c) {
    if (cameraViewPair[c] >= 0) {
      const auto& pair = plan.viewPairs[static_cast<size_t>(cameraViewPair[c])];
      if (dist3(pair.a.c, pair.b.c) > cfg.cutDistance || quat_angle_deg(pair.a.q, pair.b.q) > cfg.cutAngleDeg) {
        plan.result = PairResult::CameraCut;
        return plan;
      }
    }
    if (cameraDeltas[c].size() >= 4 && median(cameraDeltas[c]) > cfg.cutDistance) {
      plan.result = PairResult::CameraCut;
      return plan;
    }
  }

  // Pass 2: blocks and slots. Draws sharing B's uniform block and A's block
  // (same key) share one patched block.
  std::unordered_map<uint64_t, uint32_t> blockIndex;  // key -> index in blockDst
  std::vector<uint32_t> blockDst, blockSlots;          // arena offset, slot bitmask
  {
    size_t arenaBound = 0, slotBound = 0;
    for (const auto& [owner, list] : ownersB) {
      for (const uint32_t i : list) {
        arenaBound += align_up(B.draws[i].uniformSize, cfg.alignment);
        slotBound += B.draws[i].indexed ? kMaxPnMtx : 1;
      }
    }
    plan.arena.reserve(arenaBound);
    plan.slots.reserve(slotBound);
    blockIndex.reserve(plan.stats.eligible);
    blockDst.reserve(plan.stats.eligible);
    blockSlots.reserve(plan.stats.eligible);
  }
  uint32_t matchedDraws = 0;
  for (const uint32_t owner : orderB) {
    const auto& listB = ownersB[owner];
    const bool matched = ownerState[owner].matched;
    const std::vector<uint32_t>* listA = nullptr;
    if (matched) listA = &ownersA[owner];
    for (size_t k = 0; k < listB.size(); ++k) {
      const uint32_t ib = listB[k];
      const Draw& db = B.draws[ib];
      const Draw* da = matched ? &A.draws[(*listA)[k]] : nullptr;
      const uint16_t vp = da != nullptr ? view_pair(*da, db) : 0xFFFF;
      if (!matched && !cfg.useViews) {
        ++plan.stats.snappedDraws;
        continue;  // snapped without views = B's bytes
      }
      // A snapped owner keeps its B pose; with views it still follows the
      // interpolated camera, which needs the view pair of (any A, this B).
      uint16_t snapPair = 0xFFFF;
      if (!matched) {
        if (db.camera < kCameraSlots && cameraViewPair[db.camera] >= 0 && view_of(db, B) != nullptr) {
          // Same camera's pair; B's own record must be the pair's B view.
          const auto& pair = plan.viewPairs[static_cast<size_t>(cameraViewPair[db.camera])];
          const View vb = make_view(*view_of(db, B));
          if (vb.valid && std::memcmp(vb.c, pair.b.c, sizeof(vb.c)) == 0 &&
              std::memcmp(&vb.q, &pair.b.q, sizeof(vb.q)) == 0)
            snapPair = static_cast<uint16_t>(cameraViewPair[db.camera]);
        }
        ++plan.stats.snappedDraws;
        if (snapPair == 0xFFFF) continue;
      } else {
        ++matchedDraws;
        if (vp != 0xFFFF) ++plan.stats.viewDraws;
      }
      const uint32_t aOff = da != nullptr ? da->uniformOffset : UINT32_MAX;
      const uint64_t key = (uint64_t(db.uniformOffset) << 32) | aOff;
      auto [it, inserted] = blockIndex.emplace(key, static_cast<uint32_t>(blockDst.size()));
      if (inserted) {
        const uint32_t dst = align_up(static_cast<uint32_t>(plan.arena.size()), cfg.alignment);
        plan.arena.resize(dst + db.uniformSize);
        std::memcpy(plan.arena.data() + dst, B.uniforms.data() + db.uniformOffset, db.uniformSize);
        blockDst.push_back(dst);
        blockSlots.push_back(0);
      }
      const uint32_t block = it->second;
      plan.drawUniform[ib] = plan.arenaBase + blockDst[block];
      uint32_t wanted = db.indexed ? (1u << kMaxPnMtx) - 1u : (1u << db.pnSlot);
      wanted &= ~blockSlots[block];
      blockSlots[block] |= wanted;
      for (uint32_t s = 0; s < kMaxPnMtx; ++s) {
        if ((wanted & (1u << s)) == 0) continue;
        Slot slot;
        const uint32_t posRel = db.prefix + kPosOffset + s * kMatrixBytes;
        const uint32_t nrmRel = db.prefix + kNrmOffset + s * kMatrixBytes;
        slot.pos = blockDst[block] + posRel;
        slot.nrm = blockDst[block] + nrmRel;
        const uint8_t* bBytes = B.uniforms.data() + db.uniformOffset;
        std::memcpy(slot.rawPosB.data(), bBytes + posRel, kMatrixBytes);
        std::memcpy(slot.rawNrmB.data(), bBytes + nrmRel, kMatrixBytes);
        const Mat34 pb = load_mat(bBytes + posRel);
        const Mat34 nb = load_mat(bBytes + nrmRel);
        if (!finite(pb)) continue;  // Keep
        if (!matched) {
          const auto& pair = plan.viewPairs[snapPair];
          slot.viewPair = snapPair;
          slot.posMode = Mode::Snap;
          slot.posB = mul(view_inverse(pair.b), pb);
          if (finite(nb)) {
            slot.nrmMode = Mode::Snap;
            slot.nrmB = unrotate(pair.b, nb);
          }
          std::memcpy(slot.rawPosA.data(), slot.rawPosB.data(), kMatrixBytes);
          std::memcpy(slot.rawNrmA.data(), slot.rawNrmB.data(), kMatrixBytes);
          plan.slots.push_back(slot);
          continue;
        }
        const uint8_t* aBytes = A.uniforms.data() + da->uniformOffset;
        std::memcpy(slot.rawPosA.data(), aBytes + posRel, kMatrixBytes);
        std::memcpy(slot.rawNrmA.data(), aBytes + nrmRel, kMatrixBytes);
        Mat34 pa = load_mat(aBytes + posRel);
        Mat34 na = load_mat(aBytes + nrmRel);
        if (!finite(pa)) continue;  // Keep
        Mat34 ob = pb, oa = pa;
        if (vp != 0xFFFF) {
          const auto& pair = plan.viewPairs[vp];
          oa = mul(view_inverse(pair.a), pa);
          ob = mul(view_inverse(pair.b), pb);
          slot.viewPair = vp;
        }
        slot.posA = oa;
        slot.posB = ob;
        bool rotationSnap = false;
        slot.posMode = choose_mode(oa, ob, slot.rposA, slot.rposB, cfg, rotationSnap);
        if (detail::mat_translation_delta(oa, ob) > cfg.teleportDistance) slot.posMode = Mode::Snap;
        if (slot.posMode == Mode::Snap && vp == 0xFFFF) slot.posMode = Mode::Keep;
        if (s == db.pnSlot && vp != 0xFFFF && mat_translation_delta(oa, ob) < 1.0e-3f &&
            linear_change(oa, ob) < 1.0e-4f)
          ++plan.stats.staticDraws;
        if (finite(na) && finite(nb)) {
          Mat34 nA = na, nB = nb;
          if (vp != 0xFFFF) {
            nA = unrotate(plan.viewPairs[vp].a, na);
            nB = unrotate(plan.viewPairs[vp].b, nb);
          }
          slot.nrmA = nA;
          slot.nrmB = nB;
          bool nrmSnap = false;
          slot.nrmMode = choose_mode(nA, nB, slot.rnrmA, slot.rnrmB, cfg, nrmSnap);
          if (slot.nrmMode == Mode::Snap && vp == 0xFFFF) slot.nrmMode = Mode::Keep;
        }
        plan.slots.push_back(slot);
      }
    }
  }
  plan.arena.resize(align_up(static_cast<uint32_t>(plan.arena.size()), 4));  // GPU uploads are 4-byte multiples
  plan.stats.matched = matchedDraws;
  plan.stats.blocks = static_cast<uint32_t>(blockDst.size());
  plan.stats.slots = static_cast<uint32_t>(plan.slots.size());
  plan.stats.arenaBytes = static_cast<uint32_t>(plan.arena.size());
  if (plan.stats.eligible >= cfg.minSceneDraws &&
      static_cast<float>(matchedDraws) < cfg.minMatchedShare * static_cast<float>(plan.stats.eligible)) {
    plan.result = PairResult::SceneChange;
    return plan;
  }
  plan.result = PairResult::Ok;
  return plan;
}

// Write the arena for one scan-out time. alpha <= 0 writes A's matrices and
// alpha >= 1 B's, byte for byte (snapped slots: their B object pose seen from
// the camera at alpha). Everything outside the patched matrices stays B's.
inline void apply_alpha(PairPlan& plan, double alphaIn) noexcept {
  using namespace detail;
  const float t = static_cast<float>(alphaIn);
  const bool atA = !(alphaIn > 0.0);
  const bool atB = alphaIn >= 1.0;
  uint8_t* arena = plan.arena.data();
  if (atB) {
    for (const Slot& s : plan.slots) {
      if (s.posMode != Mode::Keep) std::memcpy(arena + s.pos, s.rawPosB.data(), kMatrixBytes);
      if (s.nrmMode != Mode::Keep) std::memcpy(arena + s.nrm, s.rawNrmB.data(), kMatrixBytes);
    }
    return;
  }
  // Camera at this alpha, once per view pair.
  std::vector<Mat34> views(plan.viewPairs.size());
  for (size_t i = 0; i < plan.viewPairs.size(); ++i)
    views[i] = view_at(plan.viewPairs[i].a, plan.viewPairs[i].b, atA ? 0.0f : t);
  for (const Slot& s : plan.slots) {
    const bool haveView = s.viewPair != 0xFFFF;
    if (s.posMode != Mode::Keep) {
      if (atA && s.posMode != Mode::Snap) {
        std::memcpy(arena + s.pos, s.rawPosA.data(), kMatrixBytes);
      } else {
        const Mat34 m = interp(s.posA, s.posB, s.rposA, s.rposB, s.posMode, t);
        store_mat(arena + s.pos, haveView ? mul(views[s.viewPair], m) : m);
      }
    }
    if (s.nrmMode != Mode::Keep) {
      if (atA && s.nrmMode != Mode::Snap) {
        std::memcpy(arena + s.nrm, s.rawNrmA.data(), kMatrixBytes);
      } else {
        const Mat34 m = interp(s.nrmA, s.nrmB, s.rnrmA, s.rnrmB, s.nrmMode, t);
        Mat34 out = load_mat(s.rawNrmB.data());  // B's (unused) translation column
        for (int i = 0; i < 3; ++i) {
          for (int j = 0; j < 3; ++j) {
            out.r[i][j] = haveView ? views[s.viewPair].r[i][0] * m.r[0][j] + views[s.viewPair].r[i][1] * m.r[1][j] +
                                         views[s.viewPair].r[i][2] * m.r[2][j]
                                   : m.r[i][j];
          }
        }
        store_mat(arena + s.nrm, out);
      }
    }
  }
}

} // namespace openspm::interp
