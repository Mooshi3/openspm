// Host test for the OpenSPM bbox fast path model (renderer/aurora/bbox_fast.hpp).
//
// Checks the pieces the CPU answer depends on: the position decode matches the
// vertex shader's fetches, the clip-space proof only claims "offscreen" beyond
// its error bound (and never for a primitive that can reach the clip volume),
// the degenerate proof only for bit-identical transform inputs, the window only
// answers while all four components are known, and the answer is converted
// exactly like the GPU readback (logical floor, 1023 clamp, Wii 2x2 rounding)
// so a cleared box written through raw::write_bbox reads back as 1023,0,1023,0.

#include "bbox_fast.hpp"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>

namespace {

using namespace openspm_bbox_fast;
int gFailures = 0;

#define CHECK(cond)                                                    \
  do {                                                                 \
    if (!(cond)) {                                                     \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
      ++gFailures;                                                     \
    }                                                                  \
  } while (0)

using Box = std::array<std::uint16_t, 4>;

Mat34 identity34() {
  Mat34 m{};
  for (int i = 0; i < 3; ++i) m.m[i][i] = 1.f;
  return m;
}

// GX orthographic projection as xf_load_projection builds it (rows x, y, z, w).
Mat44 ortho(float l, float r, float t, float b) {
  Mat44 p{};
  p.m[0][0] = 2.f / (r - l);
  p.m[0][3] = -(r + l) / (r - l);
  p.m[1][1] = 2.f / (t - b);
  p.m[1][3] = -(t + b) / (t - b);
  p.m[2][2] = 1.f;
  p.m[3][3] = 1.f;
  return p;
}

// GX perspective: w = -z_view.
Mat44 perspective(float fy, float aspect) {
  Mat44 p{};
  p.m[0][0] = fy / aspect;
  p.m[1][1] = fy;
  p.m[2][2] = -1.f;
  p.m[2][3] = -1.f;
  p.m[3][2] = -1.f;
  return p;
}

Reason classify(const std::initializer_list<std::array<float, 3>>& vertices, const Mat34& mv, const Mat44& proj,
                unsigned matrix = 0u) {
  DrawClassifier c;
  for (const auto& v : vertices) c.add(v.data(), matrix, mv, proj);
  return c.result();
}

void test_decode() {
  float out = 0.f;
  const std::uint8_t s16be[] = {0xFF, 0x00};  // -256
  CHECK(decode_component(s16be, kS16, 0, false, out) && out == -256.f);
  CHECK(decode_component(s16be, kS16, 8, false, out) && out == -1.f);
  CHECK(decode_component(s16be, kU16, 8, false, out) && out == 255.f);
  CHECK(decode_component(s16be, kU16, 0, true, out) && out == 255.f);  // little-endian 0x00FF
  const std::uint8_t s8[] = {0x80};
  CHECK(decode_component(s8, kS8, 1, false, out) && out == -64.f);
  CHECK(decode_component(s8, kU8, 7, false, out) && out == 1.f);
  const float f = 12.375f;
  std::uint32_t bits;
  std::memcpy(&bits, &f, 4);
  const std::uint8_t be[] = {std::uint8_t(bits >> 24), std::uint8_t(bits >> 16), std::uint8_t(bits >> 8),
                             std::uint8_t(bits)};
  const std::uint8_t le[] = {be[3], be[2], be[1], be[0]};
  CHECK(decode_component(be, kF32, 5, false, out) && out == f);  // frac never applies to f32
  CHECK(decode_component(le, kF32, 0, true, out) && out == f);
  CHECK(!decode_component(be, 7u, 0, false, out));
  CHECK(!decode_component(be, kS16, 32, false, out));
  CHECK(component_size(kF32) == 4u && component_size(kS16) == 2u && component_size(kU8) == 1u &&
        component_size(9u) == 0u);
}

void test_clip() {
  const Mat34 mv = identity34();
  const Mat44 o = ortho(0.f, 640.f, 0.f, 480.f);
  // Entirely right of the viewport, left, above, below.
  CHECK(classify({{{700, 10, 0}}, {{900, 10, 0}}, {{700, 400, 0}}}, mv, o) == kOffscreen);
  CHECK(classify({{{-5, 10, 0}}, {{-90, 10, 0}}, {{-1, 400, 0}}}, mv, o) == kOffscreen);
  CHECK(classify({{{5, -1, 0}}, {{600, -30, 0}}, {{300, -2, 0}}}, mv, o) == kOffscreen);
  CHECK(classify({{{5, 481, 0}}, {{600, 900, 0}}, {{300, 500, 0}}}, mv, o) == kOffscreen);
  // Straddling or inside: the GPU decides.
  CHECK(classify({{{600, 10, 0}}, {{900, 10, 0}}, {{700, 400, 0}}}, mv, o) == kNotEmpty);
  CHECK(classify({{{10, 10, 0}}, {{20, 10, 0}}, {{10, 20, 0}}}, mv, o) == kNotEmpty);
  // Outside two different planes (right of one, left of another) is not a proof:
  // the triangle spans the whole screen.
  CHECK(classify({{{-100, 10, 0}}, {{900, 10, 0}}, {{900, 400, 0}}}, mv, o) == kNotEmpty);
  // Touching the edge exactly (x == w) is within the error bound: not claimed.
  CHECK(classify({{{640, 10, 0}}, {{900, 10, 0}}, {{700, 400, 0}}}, mv, o) == kNotEmpty);
  CHECK(classify({{{640.5f, 10, 0}}, {{900, 10, 0}}, {{700, 400, 0}}}, mv, o) == kOffscreen);
  // Perspective: behind the camera (w < 0 for all) even when x/y "look" inside.
  const Mat44 p = perspective(1.5f, 4.f / 3.f);
  CHECK(classify({{{0, 0, 5}}, {{1, 0, 5}}, {{0, 1, 6}}}, mv, p) == kOffscreen);
  // In front and centered: may draw.
  CHECK(classify({{{0, 0, -5}}, {{1, 0, -5}}, {{0, 1, -6}}}, mv, p) == kNotEmpty);
  // Crossing the camera plane: one vertex in front, visible part possible.
  CHECK(classify({{{0, 0, -5}}, {{1, 0, 5}}, {{0, 1, 5}}}, mv, p) == kNotEmpty);
  // Far right in front of the camera.
  CHECK(classify({{{50, 0, -5}}, {{60, 0, -5}}, {{55, 1, -6}}}, mv, p) == kOffscreen);
  // Non-finite input never proves anything.
  const float nan = std::nanf("");
  CHECK(classify({{{700, 10, 0}}, {{nan, 10, 0}}, {{700, 400, 0}}}, mv, o) == kNotEmpty);
  CHECK(classify({{{700, 10, 0}}, {{INFINITY, 10, 0}}, {{700, 400, 0}}}, mv, o) == kNotEmpty);
  DrawClassifier empty;
  CHECK(empty.result() == kNotEmpty);
  // Model matrix translation moves an on-screen model off screen.
  Mat34 moved = identity34();
  moved.m[0][3] = 2000.f;
  CHECK(classify({{{10, 10, 0}}, {{20, 10, 0}}, {{10, 20, 0}}}, moved, o) == kOffscreen);

  // Soundness sweep: random triangles; whenever "offscreen" is claimed, no
  // point of the triangle (dense barycentric samples) is inside the clip volume.
  std::mt19937 rng(1234);
  std::uniform_real_distribution<float> coord(-1500.f, 2100.f), depth(-20.f, 20.f);
  int claimed = 0;
  for (int iter = 0; iter < 20000; ++iter) {
    const bool persp = (iter & 1) != 0;
    const Mat44& proj = persp ? p : o;
    std::array<std::array<float, 3>, 3> v{};
    for (auto& vertex : v) {
      vertex = persp ? std::array<float, 3>{coord(rng) / 100.f, coord(rng) / 100.f, depth(rng)}
                     : std::array<float, 3>{coord(rng), coord(rng), 0.f};
    }
    DrawClassifier c;
    for (auto& vertex : v) c.add(vertex.data(), 0u, mv, proj);
    if (c.result() != kOffscreen) continue;
    ++claimed;
    for (int a = 0; a <= 40; ++a) {
      for (int b = 0; a + b <= 40; ++b) {
        const double wa = a / 40.0, wb = b / 40.0, wc = 1.0 - wa - wb;
        double clip[4] = {0, 0, 0, 0};
        for (int r : {0, 1, 3}) {
          for (int k = 0; k < 3; ++k) {
            const double pk = wa * v[0][k] + wb * v[1][k] + wc * v[2][k];
            clip[r] += proj.m[r][k] * pk;
          }
          clip[r] += proj.m[r][3];
        }
        const bool inside = clip[3] >= 0 && std::fabs(clip[0]) <= clip[3] && std::fabs(clip[1]) <= clip[3];
        CHECK(!inside);
      }
    }
  }
  CHECK(claimed > 1000);
}

void test_degenerate() {
  const Mat34 mv = identity34();
  const Mat44 o = ortho(0.f, 640.f, 0.f, 480.f);
  CHECK(classify({{{10, 10, 0}}, {{10, 10, 0}}, {{10, 10, 0}}, {{10, 10, 0}}}, mv, o) == kDegenerate);
  CHECK(classify({{{10, 10, 0}}, {{10, 10, 0}}, {{10, 11, 0}}}, mv, o) == kNotEmpty);
  // Same position through different matrix slots is not bit-identical input.
  DrawClassifier mixed;
  const float a[3] = {10, 10, 0};
  mixed.add(a, 0u, mv, o);
  mixed.add(a, 1u, mv, o);
  mixed.add(a, 0u, mv, o);
  CHECK(mixed.result() == kNotEmpty);
  // A zero-scale model matrix collapses every finite position onto its translation.
  Mat34 zero{};
  zero.m[0][3] = 100.f;
  zero.m[1][3] = 50.f;
  CHECK(classify({{{10, 10, 0}}, {{-400, 3, 7}}, {{12, 99, -1}}}, zero, o) == kDegenerate);
  // ...but not when the 3x3 part is only nearly zero.
  Mat34 tiny = zero;
  tiny.m[0][0] = 1e-30f;
  CHECK(classify({{{10, 10, 0}}, {{-400, 3, 7}}, {{12, 99, -1}}}, tiny, o) == kNotEmpty);
  // Early out: two distinct vertices inside can never become provably empty.
  DrawClassifier early;
  const float in0[3] = {10, 10, 0}, in1[3] = {20, 10, 0}, off[3] = {900, 10, 0};
  early.add(in0, 0u, mv, o);
  CHECK(!early.hopeless());  // still possibly degenerate
  early.add(in0, 0u, mv, o);
  CHECK(!early.hopeless());
  early.add(in1, 0u, mv, o);
  CHECK(early.hopeless() && early.result() == kNotEmpty);
  DrawClassifier far;
  far.add(off, 0u, mv, o);
  far.add(in1, 0u, mv, o);
  CHECK(far.hopeless());
  // Offscreen wins when both hold.
  CHECK(classify({{{700, 10, 0}}, {{700, 10, 0}}, {{700, 10, 0}}}, mv, o) == kOffscreen);
}

void test_window() {
  Window w;
  Box host{};
  CHECK(!w.wants_draw());
  CHECK(w.stored(host) == kNotEmpty);
  w.note_write(0, 2302);
  w.note_write(1, 1);
  CHECK(w.wants_draw());
  CHECK(w.stored(host) == kNotEmpty);  // top/bottom unknown (e.g. after a GPU draw)
  w.note_write(2, 2302);
  w.note_write(3, 1);
  CHECK(w.stored(host) == kNoDraws && host == (Box{2302, 1, 2302, 1}));
  w.note_draw(kOffscreen);
  CHECK(w.stored(host) == kOffscreen);
  w.note_draw(kDegenerate);
  CHECK(w.stored(host) == kOffscreen);
  w.note_draw(kNotEmpty);  // the GPU may have grown the box
  CHECK(w.stored(host) == kNotEmpty && !w.wants_draw());
  w.note_draw(kOffscreen);  // no longer tracked until rewritten
  CHECK(w.stored(host) == kNotEmpty);
  // A later clear of only left/right does not make top/bottom known again.
  w.note_write(0, 2302);
  w.note_write(1, 1);
  CHECK(w.stored(host) == kNotEmpty);
  w.note_write(2, 2302);
  w.note_write(3, 1);
  w.note_draw(kDegenerate);
  CHECK(w.stored(host) == kDegenerate);
  w.note_write(4, 7);  // out of range: ignored
  CHECK(w.stored(host) == kDegenerate);
}

void test_conversion() {
  // Scales: native 640x480, 1440x1080 fit (2.25), 1080p height (2.25), 1.6875, 1.5, 3.375 (2160p).
  const double scales[] = {1.0, 2.25, 1.6875, 1.5, 3.375, 1.0 + 1.0 / 3.0};
  for (const double s : scales) {
    // GXClearBoundingBox: BP 0x55/0x56 = 1023 | 0 << 10 -> left/top 1023, right/bottom 0.
    const Box host{host_from_logical(1023, false, s), host_from_logical(0, true, s), host_from_logical(1023, false, s),
                   host_from_logical(0, true, s)};
    const Box read = logical_from_host(host, s, s, true);
    CHECK(read == (Box{1023, 0, 1023, 0}));
    // Every single component round-trips through write+read at scale >= 1.
    for (std::uint16_t v = 0; v <= 1023; ++v) {
      const Box h{host_from_logical(v, false, s), host_from_logical(v, true, s), 0, 0};
      const Box r = logical_from_host(h, s, s, false);
      CHECK(r[0] == v && r[1] == v);
    }
  }
  // Logical reporting off (scale 1.0 everywhere): raw values clamped to 1023, no rounding.
  CHECK(logical_from_host(Box{2302, 1, 2302, 1}, 1.0, 1.0, false) == (Box{1023, 1, 1023, 1}));
  // 2x2 granularity: left/top even, right/bottom odd, only for non-empty boxes.
  // Host (535,700,11,199) at 2.25 -> floor (237,311,4,88) -> (236,311,4,89).
  CHECK(logical_from_host(Box{535, 700, 11, 199}, 2.25, 2.25, true) == (Box{236, 311, 4, 89}));
  CHECK(logical_from_host(Box{535, 700, 11, 199}, 2.25, 2.25, false) == (Box{237, 311, 4, 88}));
  CHECK(logical_from_host(Box{2302, 2302, 0, 2302}, 2.25, 2.25, true) == (Box{1022, 1023, 0, 1023}));
  // Empty box (left > right) stays exactly as stored.
  CHECK(logical_from_host(Box{2302, 1, 2302, 1}, 2.25, 2.25, true) == (Box{1023, 0, 1023, 0}));
  // Separate axis scales (stretched output).
  CHECK(logical_from_host(Box{400, 403, 300, 301}, 4.0, 2.25, true) == (Box{100, 101, 132, 133}));
  // 14-bit readback mask.
  CHECK(logical_from_host(Box{0xC005, 0x4007, 2, 3}, 1.0, 1.0, false) == (Box{5, 7, 2, 3}));
}

void test_mode() {
  CHECK(parse_mode(nullptr) == kModeOff);
  CHECK(parse_mode("") == kModeOff);
  CHECK(parse_mode("0") == kModeOff);
  CHECK(parse_mode("1") == kModeOn);
  CHECK(parse_mode("verify") == kModeVerify);
  CHECK(parse_mode("yes") == kModeOff);
  CHECK(std::strcmp(reason_name(kOffscreen), "offscreen") == 0 && std::strcmp(reason_name(kNotEmpty), "gpu") == 0);
}

}  // namespace

int main() {
  test_decode();
  test_clip();
  test_degenerate();
  test_window();
  test_conversion();
  test_mode();
  if (gFailures != 0) {
    std::printf("m14_bbox_fast_test: %d failure(s)\n", gFailures);
    return 1;
  }
  std::printf("m14_bbox_fast_test: PASS\n");
  return 0;
}
