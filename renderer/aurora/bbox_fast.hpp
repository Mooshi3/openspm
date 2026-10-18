// OpenSPM bbox fast path (opt-in OPENSPM_M14_BBOX_FAST=1 | verify): pure model.
//
// A CPU read of the GX PE bounding box costs a GPU round trip (submit + map).
// SPM's star door / flip effects clear the box, draw one object, read it back,
// for every object in the scene - about a thousand reads in one frame, most of
// them empty because the object never produced a pixel.
//
// The PE box only changes when a fragment of a GX draw survives to the bbox
// update. If every draw since the four bbox components were last written is
// proven, on the CPU, to produce no fragment at all, the stored box is exactly
// the values written, and the read can be answered without the GPU. Proofs
// (each exact, never a heuristic):
//   no_draws   - no draw at all since the writes;
//   offscreen  - every vertex of the draw is strictly outside the same clip
//                half-space (x > w, x < -w, y > w, y < -w or w < 0), with a
//                float error bound far above what the GPU's own f32 transform
//                can differ by. The clip volume is convex, so no point of any
//                primitive built from these vertices can be rasterized;
//   degenerate - every vertex feeds the GPU transform bit-identical inputs
//                (same position and matrix, or matrices whose 3x3 part is
//                exactly zero with the same translation), so every primitive
//                has zero area.
// Anything else (lines, points, unknown formats, non-finite values, a vertex
// that might be inside) is "not provably empty" and the read goes to the GPU.
//
// The answer is the stored host-pixel value converted exactly like the GPU
// readback path (apply_bbox_logical_scale_patch.py read_bbox): pixel->logical
// floor, 1023 clamp and the Wii 2x2 granularity for non-empty boxes.
//
// Embedded into Aurora's lib/gx/raw.cpp by apply_bbox_fast_patch.py and unit
// tested by tests/m14_bbox_fast_test.cpp (renderer/aurora/test_display_pacing.py).
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace openspm_bbox_fast {

enum Mode : int { kModeOff = 0, kModeOn = 1, kModeVerify = 2 };

// "1" = answer provably empty reads on the CPU; "verify" = compute the CPU
// answer, still read the GPU, and count/log every difference.
inline int parse_mode(const char* value) noexcept {
  if (value == nullptr) return kModeOff;
  if (std::strcmp(value, "1") == 0) return kModeOn;
  if (std::strcmp(value, "verify") == 0) return kModeVerify;
  return kModeOff;
}

enum Reason : unsigned { kNotEmpty = 0, kNoDraws = 1, kOffscreen = 2, kDegenerate = 3 };

inline const char* reason_name(unsigned reason) noexcept {
  switch (reason) {
  case kNoDraws: return "no_draws";
  case kOffscreen: return "offscreen";
  case kDegenerate: return "degenerate";
  default: return "gpu";
  }
}

// GXCompType values for positions.
constexpr unsigned kU8 = 0, kS8 = 1, kU16 = 2, kS16 = 3, kF32 = 4;

inline unsigned component_size(unsigned type) noexcept {
  switch (type) {
  case kU8: case kS8: return 1u;
  case kU16: case kS16: return 2u;
  case kF32: return 4u;
  default: return 0u;
  }
}

// One position component exactly as Aurora's vertex shader fetches it
// (fetch_{u8,s8,u16,s16,f32}_1): fixed-point value / f32(1 << frac), which is
// exact in f32 for these widths. le = the source bytes are little-endian.
inline bool decode_component(const std::uint8_t* p, unsigned type, unsigned frac, bool le, float& out) noexcept {
  if (frac > 31u) return false;
  const float divisor = static_cast<float>(1u << frac);
  switch (type) {
  case kU8: out = static_cast<float>(p[0]) / divisor; return true;
  case kS8: out = static_cast<float>(static_cast<std::int8_t>(p[0])) / divisor; return true;
  case kU16:
  case kS16: {
    const std::uint16_t raw = le ? static_cast<std::uint16_t>(p[0] | (p[1] << 8))
                                 : static_cast<std::uint16_t>((p[0] << 8) | p[1]);
    const float v = type == kU16 ? static_cast<float>(raw) : static_cast<float>(static_cast<std::int16_t>(raw));
    out = v / divisor;
    return true;
  }
  case kF32: {
    const std::uint32_t raw = le ? (std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
                                    (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24))
                                 : ((std::uint32_t(p[0]) << 24) | (std::uint32_t(p[1]) << 16) |
                                    (std::uint32_t(p[2]) << 8) | std::uint32_t(p[3]));
    std::memcpy(&out, &raw, sizeof(out));
    return true;
  }
  default: return false;
  }
}

// Row-major matrices as GX (and Aurora's uniform) store them: GPU computes
// mv[j] = dot((pos, 1), pos_mtx row j) and clip[r] = dot((mv, 1), proj row r).
struct Mat34 { float m[3][4]; };
struct Mat44 { float m[4][4]; };

// Relative bound on the GPU's f32 transform error: 2^-16 of the summed term
// magnitudes (the f32 dot products are within a few 2^-24 of exact).
constexpr double kRelativeError = 1.0 / 65536.0;

enum OutBits : unsigned {
  kRightOfW = 1u,  // x >  w
  kLeftOfW = 2u,   // x < -w
  kBelowW = 4u,    // y >  w
  kAboveW = 8u,    // y < -w
  kBehind = 16u,   // w <  0
  kAllPlanes = 31u,
};

// Clip-space half-spaces a vertex is provably (beyond the error bound) in.
// Returns false for non-finite input.
inline bool classify_vertex(const float pos[3], const Mat34& mv, const Mat44& proj, unsigned& bits) noexcept {
  double v[3], e[3];
  for (int j = 0; j < 3; ++j) {
    double sum = mv.m[j][3], mag = std::fabs(static_cast<double>(mv.m[j][3]));
    for (int k = 0; k < 3; ++k) {
      const double t = static_cast<double>(mv.m[j][k]) * static_cast<double>(pos[k]);
      sum += t;
      mag += std::fabs(t);
    }
    v[j] = sum;
    e[j] = kRelativeError * mag;
  }
  double c[4] = {0.0, 0.0, 0.0, 0.0}, ce[4] = {0.0, 0.0, 0.0, 0.0};
  for (int r : {0, 1, 3}) {
    double sum = proj.m[r][3], mag = std::fabs(static_cast<double>(proj.m[r][3])), prop = 0.0;
    for (int j = 0; j < 3; ++j) {
      const double pj = static_cast<double>(proj.m[r][j]);
      const double t = pj * v[j];
      sum += t;
      mag += std::fabs(t);
      prop += std::fabs(pj) * e[j];
    }
    c[r] = sum;
    ce[r] = kRelativeError * mag + prop * (1.0 + kRelativeError);
  }
  for (int r : {0, 1, 3}) {
    if (!std::isfinite(c[r]) || !std::isfinite(ce[r])) return false;
  }
  const double x = c[0], y = c[1], w = c[3];
  const double ex = ce[0] + ce[3], ey = ce[1] + ce[3];
  bits = 0u;
  if (x - w > ex) bits |= kRightOfW;
  if (x + w < -ex) bits |= kLeftOfW;
  if (y - w > ey) bits |= kBelowW;
  if (y + w < -ey) bits |= kAboveW;
  if (w < -ce[3]) bits |= kBehind;
  return true;
}

inline bool zero_linear(const Mat34& mv) noexcept {
  for (int j = 0; j < 3; ++j)
    for (int k = 0; k < 3; ++k)
      if (mv.m[j][k] != 0.0f) return false;
  return true;
}

// Accumulates the vertices of one triangle-class draw (quads, triangles,
// strips, fans: every vertex it references is a vertex added here).
class DrawClassifier {
public:
  void reset() noexcept { *this = DrawClassifier{}; }

  // matrix = the position matrix slot the vertex selects (identity of the
  // matrix contents is what matters, so pass the slot index).
  void add(const float pos[3], unsigned matrix, const Mat34& mv, const Mat44& proj) noexcept {
    if (poisoned_) return;
    for (int k = 0; k < 3; ++k) {
      if (!std::isfinite(pos[k])) {
        poisoned_ = true;
        return;
      }
    }
    unsigned bits = 0u;
    if (!classify_vertex(pos, mv, proj, bits)) {
      poisoned_ = true;
      return;
    }
    outside_ &= bits;
    // Degenerate key: the exact GPU transform inputs. With an exactly-zero
    // 3x3 part the output is the translation for every finite position.
    Key key{};
    if (zero_linear(mv)) {
      key.kind = 1u;
      for (int j = 0; j < 3; ++j) std::memcpy(&key.v[j], &mv.m[j][3], sizeof(float));
    } else {
      key.kind = 2u;
      key.matrix = matrix;
      for (int k = 0; k < 3; ++k) std::memcpy(&key.v[k], &pos[k], sizeof(float));
    }
    if (count_ == 0u) first_ = key;
    else if (!(key == first_)) same_ = false;
    ++count_;
  }

  void poison() noexcept { poisoned_ = true; }

  // No further vertex can make the draw provably empty (early out for the
  // common on-screen draw: its first vertices are inside and distinct).
  bool hopeless() const noexcept { return poisoned_ || (outside_ == 0u && !same_); }

  Reason result() const noexcept {
    if (poisoned_ || count_ == 0u) return kNotEmpty;
    if (outside_ != 0u) return kOffscreen;
    if (same_) return kDegenerate;
    return kNotEmpty;
  }

private:
  struct Key {
    unsigned kind = 0u;
    unsigned matrix = 0u;
    std::uint32_t v[3] = {0u, 0u, 0u};
    bool operator==(const Key& o) const noexcept {
      return kind == o.kind && (kind == 1u || matrix == o.matrix) && v[0] == o.v[0] && v[1] == o.v[1] &&
             v[2] == o.v[2];
    }
  };
  unsigned outside_ = kAllPlanes;
  unsigned count_ = 0u;
  bool same_ = true;
  bool poisoned_ = false;
  Key first_{};
};

// The PE bbox storage as the CPU knows it: host-pixel values of components
// written since the last draw that might have touched a pixel.
class Window {
public:
  bool wants_draw() const noexcept { return known_ != 0u; }

  void note_write(unsigned index, std::uint16_t hostValue) noexcept {
    if (index >= 4u) return;
    host_[index] = hostValue;
    known_ |= 1u << index;
    draws_ = offscreen_ = degenerate_ = 0u;
  }

  void note_draw(Reason reason) noexcept {
    if (known_ == 0u) return;
    if (reason == kNotEmpty || reason == kNoDraws) {
      known_ = 0u;  // the GPU may have grown the box
      return;
    }
    ++draws_;
    if (reason == kOffscreen) ++offscreen_;
    else ++degenerate_;
  }

  // The stored values when all four are known, with why no draw changed them.
  Reason stored(std::array<std::uint16_t, 4>& host) const noexcept {
    if (known_ != 0xFu) return kNotEmpty;
    host = host_;
    if (draws_ == 0u) return kNoDraws;
    return offscreen_ != 0u ? kOffscreen : kDegenerate;
  }

private:
  std::array<std::uint16_t, 4> host_{};
  unsigned known_ = 0u;
  unsigned draws_ = 0u, offscreen_ = 0u, degenerate_ = 0u;
};

// Mirror of raw::write_bbox's logical->host conversion (test reference only;
// the runtime receives the already-converted host value from bbox::queue_write).
inline std::uint16_t host_from_logical(std::uint16_t value, bool isMax, double scale) noexcept {
  if (scale == 1.0) return value;
  const double px = isMax ? std::ceil((static_cast<double>(value) + 1.0) * scale) - 1.0
                          : std::ceil(static_cast<double>(value) * scale);
  return static_cast<std::uint16_t>(std::clamp(px, 0.0, 16383.0));
}

// Mirror of raw::read_bbox after bbox::read_sync: 14-bit readback mask,
// host->logical floor with the 1023 clamp, then the Wii 2x2 granularity
// (left/top even, right/bottom odd) for a non-empty box. scaleX/scaleY are
// bbox_axis_scale (1.0 when logical reporting is off or the scale is unknown).
inline std::array<std::uint16_t, 4> logical_from_host(const std::array<std::uint16_t, 4>& host, double scaleX,
                                                      double scaleY, bool logicalEnabled) noexcept {
  std::array<std::uint16_t, 4> out{};
  for (std::uint32_t i = 0; i < 4u; ++i) {
    const std::uint16_t raw = static_cast<std::uint16_t>(host[i] & 0x3fffu);
    const double s = i < 2u ? scaleX : scaleY;
    if (s == 1.0) {
      out[i] = static_cast<std::uint16_t>(std::min<std::uint32_t>(raw, 1023u));
      continue;
    }
    const double logical = std::floor(static_cast<double>(raw) / s);
    out[i] = static_cast<std::uint16_t>(std::clamp(logical, 0.0, 1023.0));
  }
  if (logicalEnabled && out[0] <= out[1] && out[2] <= out[3]) {
    out[0] = static_cast<std::uint16_t>(out[0] & ~1u);
    out[2] = static_cast<std::uint16_t>(out[2] & ~1u);
    out[1] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[1] | 1u, 1023u));
    out[3] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[3] | 1u, 1023u));
  }
  return out;
}

}  // namespace openspm_bbox_fast
