// Host test + synthetic benchmark for OpenSPM Stage C frame interpolation
// (renderer/aurora/frame_interp.hpp). Builds synthetic frame records the way
// the Aurora glue does (GX uniform blocks: prefix, projection, 10 position,
// 10 texture and 10 normal matrices, then other state), with a game camera
// view per camera, and checks:
//  - alpha = 0 reproduces A's matrices and alpha = 1 B's uniform blocks byte
//    for byte, everything but the patched matrices stays B's;
//  - mid-alpha poses equal V(alpha) * M(alpha) for known object/camera motion;
//  - owner snap rules (spawn, count, structure, teleport, rotation);
//  - camera cuts with and without view records, and scene changes;
//  - 2D/UI and untagged draws keep B's block; z-order reshuffles still match;
//  - translation-only cameras give the same result with and without views.
// Then times plan_pair/apply_alpha on a ~3000-draw scene.

#include "frame_interp.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

using namespace openspm::interp;

namespace {

int gFailures = 0;
#define CHECK(cond)                                                                \
  do {                                                                             \
    if (!(cond)) {                                                                 \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++gFailures;                                                                 \
    }                                                                              \
  } while (0)

constexpr uint32_t kBlock = 1600;  // prefix 16 + 1440 matrices + other state, rounded
constexpr uint32_t kAlign = 256;

Mat34 identity() {
  Mat34 m{};
  m.r[0][0] = m.r[1][1] = m.r[2][2] = 1.0f;
  return m;
}

Mat34 rot_y(float deg) {
  const float a = deg * 3.14159265358979f / 180.0f;
  Mat34 m = identity();
  m.r[0][0] = std::cos(a);
  m.r[0][2] = std::sin(a);
  m.r[2][0] = -std::sin(a);
  m.r[2][2] = std::cos(a);
  return m;
}

Mat34 translate(float x, float y, float z) {
  Mat34 m = identity();
  m.r[0][3] = x;
  m.r[1][3] = y;
  m.r[2][3] = z;
  return m;
}

Mat34 scale(float x, float y, float z) {
  Mat34 m{};
  m.r[0][0] = x;
  m.r[1][1] = y;
  m.r[2][2] = z;
  return m;
}

// World -> view for a camera at `pos` rotated `yaw` degrees about y.
Mat34 camera(float yaw, float x, float y, float z) {
  const Mat34 r = rot_y(yaw);
  Mat34 rt{};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) rt.r[i][j] = r.r[j][i];
  return mul(rt, translate(-x, -y, -z));
}

// Normal matrix = inverse transpose of the 3x3 part (rotation * scale).
Mat34 normal_of(const Mat34& pn) {
  Mat34 n{};
  const auto& m = pn.r;
  const float det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
                    m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  if (std::fabs(det) < 1e-12f) return identity();
  // inverse transpose = cofactor / det
  n.r[0][0] = (m[1][1] * m[2][2] - m[1][2] * m[2][1]) / det;
  n.r[0][1] = -(m[1][0] * m[2][2] - m[1][2] * m[2][0]) / det;
  n.r[0][2] = (m[1][0] * m[2][1] - m[1][1] * m[2][0]) / det;
  n.r[1][0] = -(m[0][1] * m[2][2] - m[0][2] * m[2][1]) / det;
  n.r[1][1] = (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det;
  n.r[1][2] = -(m[0][0] * m[2][1] - m[0][1] * m[2][0]) / det;
  n.r[2][0] = (m[0][1] * m[1][2] - m[0][2] * m[1][1]) / det;
  n.r[2][1] = -(m[0][0] * m[1][2] - m[0][2] * m[1][0]) / det;
  n.r[2][2] = (m[0][0] * m[1][1] - m[0][1] * m[1][0]) / det;
  return n;
}

struct Object {
  uint32_t owner = 0;
  uint8_t camera = 0;
  bool ortho = false;
  int parts = 1;
  Mat34 world = identity();  // object -> world
  uint32_t vtx = 36;
  bool indexed = false;
  uint32_t seed = 1;         // fills the non-matrix bytes
};

struct SceneFrame {
  uint64_t id = 1;
  std::vector<Object> objects;
  std::vector<Mat34> cams;   // per camera slot: world -> view
  bool sendViews = true;
  bool shuffle = false;
  uint32_t shuffleSeed = 7;
};

Frame build(const SceneFrame& s) {
  Frame f;
  f.id = s.id;
  f.views = s.cams;
  struct Pending {
    const Object* o;
    int part;
  };
  std::vector<Pending> order;
  for (const auto& o : s.objects)
    for (int p = 0; p < o.parts; ++p) order.push_back({&o, p});
  if (s.shuffle) {
    // Reorder draws between owners only (a z sort); parts of one owner keep
    // their relative order, as they do inside one dispDraw callback.
    std::mt19937 rng(s.shuffleSeed);
    std::vector<size_t> objectOrder(s.objects.size());
    for (size_t i = 0; i < objectOrder.size(); ++i) objectOrder[i] = i;
    std::shuffle(objectOrder.begin(), objectOrder.end(), rng);
    order.clear();
    for (size_t k : objectOrder)
      for (int p = 0; p < s.objects[k].parts; ++p) order.push_back({&s.objects[k], p});
  }
  for (const auto& [o, part] : order) {
    Draw d;
    d.owner = o->owner;
    d.camera = o->camera;
    d.ortho = o->ortho;
    d.indexed = o->indexed;
    d.pnSlot = static_cast<uint8_t>(o->indexed ? 0 : part % kMaxPnMtx);
    d.prefix = 16;
    d.view = s.sendViews && !o->ortho && o->camera < s.cams.size() ? static_cast<uint16_t>(o->camera + 1) : 0;
    d.uniformOffset = static_cast<uint32_t>(f.uniforms.size());
    d.uniformSize = kBlock;
    d.vtxCount = o->vtx + static_cast<uint32_t>(part);
    std::vector<uint8_t> block(kBlock);
    std::mt19937 rng(o->seed * 131u + static_cast<uint32_t>(part));
    for (auto& b : block) b = static_cast<uint8_t>(rng());
    const Mat34 view = o->ortho ? identity() : s.cams[o->camera];
    for (uint32_t slot = 0; slot < kMaxPnMtx; ++slot) {
      // part offset in object space, per slot for skinned objects
      const Mat34 local = translate(static_cast<float>(part) * 3.0f + static_cast<float>(slot), 0, 0);
      const Mat34 pn = mul(view, mul(o->world, local));
      store_mat(block.data() + d.prefix + kPosOffset + slot * kMatrixBytes, pn);
      store_mat(block.data() + d.prefix + kNrmOffset + slot * kMatrixBytes, normal_of(pn));
    }
    f.uniforms.insert(f.uniforms.end(), block.begin(), block.end());
    f.uniforms.resize((f.uniforms.size() + kAlign - 1) / kAlign * kAlign, 0);
    f.draws.push_back(d);
  }
  return f;
}

Config config(bool views = true) {
  Config c;
  c.useViews = views;
  c.alignment = kAlign;
  return c;
}

const uint8_t* bound_block(const PairPlan& plan, const Frame& b, size_t draw) {
  const uint32_t off = plan.drawUniform[draw];
  if (off >= plan.arenaBase) return plan.arena.data() + (off - plan.arenaBase);
  return b.uniforms.data() + off;
}

Mat34 bound_pos(const PairPlan& plan, const Frame& b, size_t draw, uint32_t slot = 0) {
  const Draw& d = b.draws[draw];
  return load_mat(bound_block(plan, b, draw) + d.prefix + kPosOffset + slot * kMatrixBytes);
}

bool near(const Mat34& a, const Mat34& b, float eps) {
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 4; ++j)
      if (std::fabs(a.r[i][j] - b.r[i][j]) > eps) return false;
  return true;
}

size_t find_draw(const Frame& f, uint32_t owner, int ordinal = 0) {
  int seen = 0;
  for (size_t i = 0; i < f.draws.size(); ++i)
    if (f.draws[i].owner == owner && seen++ == ordinal) return i;
  std::abort();
}

// A/B scene: camera 0 is 3D, camera 1 is 2D/UI.
SceneFrame scene_a() {
  SceneFrame s;
  s.id = 10;
  s.cams = {camera(0, 0, 50, 600)};
  for (uint32_t i = 0; i < 24; ++i) {
    Object o;
    o.owner = 0x1000 + i;
    o.parts = 1 + static_cast<int>(i % 3);
    o.world = translate(static_cast<float>(i) * 20.0f, 0, 0);
    o.seed = i + 1;
    s.objects.push_back(o);
  }
  Object ui;
  ui.owner = 0x9000;
  ui.camera = 1;
  ui.ortho = true;
  ui.parts = 2;
  ui.seed = 99;
  s.objects.push_back(ui);
  Object untagged;
  untagged.owner = 0;
  untagged.seed = 77;
  s.objects.push_back(untagged);
  return s;
}

// B: camera pans +12 x, object 0x1000 walks +4 x, 0x1001 turns 30 degrees.
SceneFrame scene_b() {
  SceneFrame s = scene_a();
  s.id = 11;
  s.cams = {camera(0, 12, 50, 600)};
  s.objects[0].world = translate(4, 0, 0);
  s.objects[1].world = mul(translate(20, 0, 0), rot_y(30));
  return s;
}

void test_endpoints_byte_exact() {
  const Frame A = build(scene_a());
  const Frame B = build(scene_b());
  for (bool views : {true, false}) {
    PairPlan plan = plan_pair(A, B, config(views));
    CHECK(plan.result == PairResult::Ok);
    CHECK(plan.stats.snappedDraws == 0);
    apply_alpha(plan, 1.0);
    for (size_t i = 0; i < B.draws.size(); ++i)
      CHECK(std::memcmp(bound_block(plan, B, i), B.uniforms.data() + B.draws[i].uniformOffset, kBlock) == 0);
    apply_alpha(plan, 0.0);
    for (size_t i = 0; i < B.draws.size(); ++i) {
      const Draw& db = B.draws[i];
      const uint8_t* got = bound_block(plan, B, i);
      const uint8_t* bb = B.uniforms.data() + db.uniformOffset;
      if (db.owner == 0 || db.ortho) {
        CHECK(plan.drawUniform[i] == db.uniformOffset);  // untouched
        continue;
      }
      const uint8_t* ab = A.uniforms.data() + A.draws[find_draw(A, db.owner, 0)].uniformOffset;
      // matching ordinal in A
      int ordinal = 0;
      for (size_t k = 0; k < i; ++k)
        if (B.draws[k].owner == db.owner) ++ordinal;
      ab = A.uniforms.data() + A.draws[find_draw(A, db.owner, ordinal)].uniformOffset;
      const uint32_t pos = db.prefix + kPosOffset + db.pnSlot * kMatrixBytes;
      const uint32_t nrm = db.prefix + kNrmOffset + db.pnSlot * kMatrixBytes;
      CHECK(std::memcmp(got + pos, ab + pos, kMatrixBytes) == 0);
      CHECK(std::memcmp(got + nrm, ab + nrm, kMatrixBytes) == 0);
      // Everything else is B's.
      for (uint32_t b = 0; b < kBlock; ++b) {
        if ((b >= pos && b < pos + kMatrixBytes) || (b >= nrm && b < nrm + kMatrixBytes)) continue;
        if (got[b] != bb[b]) {
          CHECK(got[b] == bb[b]);
          break;
        }
      }
    }
    // Back to 1 after 0.5: still byte exact (no drift in the arena).
    apply_alpha(plan, 0.5);
    apply_alpha(plan, 1.0);
    for (size_t i = 0; i < B.draws.size(); ++i)
      CHECK(std::memcmp(bound_block(plan, B, i), B.uniforms.data() + B.draws[i].uniformOffset, kBlock) == 0);
  }
}

void test_mid_alpha_pose() {
  const SceneFrame sa = scene_a(), sb = scene_b();
  const Frame A = build(sa), B = build(sb);
  PairPlan plan = plan_pair(A, B, config(true));
  CHECK(plan.result == PairResult::Ok);
  apply_alpha(plan, 0.25);
  const Mat34 v = camera(0, 3, 50, 600);  // camera at 0.25 of its pan
  // Walking object: world x 0 -> 4, at 0.25 = 1.
  CHECK(near(bound_pos(plan, B, find_draw(B, 0x1000)), mul(v, translate(1, 0, 0)), 1e-3f));
  // Turning object: 0 -> 30 degrees about y at x = 20, at 0.25 = 7.5 degrees.
  CHECK(near(bound_pos(plan, B, find_draw(B, 0x1001)), mul(v, mul(translate(20, 0, 0), rot_y(7.5f))), 1e-3f));
  // Static object seen by the moving camera.
  CHECK(near(bound_pos(plan, B, find_draw(B, 0x1005)), mul(v, translate(100, 0, 0)), 1e-3f));
  CHECK(plan.stats.staticDraws > 0);
  // Normal of the turning object follows the rotation (unit scale: N = R).
  const Draw& d = B.draws[find_draw(B, 0x1001)];
  const Mat34 n = load_mat(bound_block(plan, B, find_draw(B, 0x1001)) + d.prefix + kNrmOffset);
  const Mat34 expect = mul(v, rot_y(7.5f));
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) CHECK(std::fabs(n.r[i][j] - expect.r[i][j]) < 1e-3f);
}

void test_rotating_camera() {
  SceneFrame sa = scene_a(), sb = scene_a();
  sb.id = 11;
  sa.cams = {camera(-10, 0, 50, 600)};
  sb.cams = {camera(10, 0, 50, 600)};  // 20 degree yaw in one frame (below the cut)
  const Frame A = build(sa), B = build(sb);
  PairPlan plan = plan_pair(A, B, config(true));
  CHECK(plan.result == PairResult::Ok);
  apply_alpha(plan, 0.5);
  // Static world: exactly the camera at the half-way orientation.
  CHECK(near(bound_pos(plan, B, find_draw(B, 0x1007)), mul(camera(0, 0, 50, 600), translate(140, 0, 0)), 2e-3f));
}

void test_translation_camera_views_equivalent() {
  const Frame A = build(scene_a()), B = build(scene_b());
  PairPlan withViews = plan_pair(A, B, config(true));
  PairPlan direct = plan_pair(A, B, config(false));
  apply_alpha(withViews, 0.6);
  apply_alpha(direct, 0.6);
  for (uint32_t owner : {0x1000u, 0x1003u, 0x1010u}) {
    const size_t i = find_draw(B, owner);
    CHECK(near(bound_pos(withViews, B, i, B.draws[i].pnSlot), bound_pos(direct, B, i, B.draws[i].pnSlot), 2e-3f));
  }
}

void test_owner_snaps() {
  SceneFrame sa = scene_a(), sb = scene_b();
  sb.objects[2].parts += 1;           // 0x1002: draw count changed
  sb.objects[3].vtx += 5;             // 0x1003: structure changed
  sb.objects[4].world = translate(80 + 500, 0, 0);  // 0x1004: teleport
  sb.objects[5].world = mul(translate(100, 0, 0), rot_y(170));  // 0x1005: rotation too large
  Object spawned;
  spawned.owner = 0x2000;
  spawned.world = translate(33, 0, 0);
  sb.objects.push_back(spawned);
  const Frame A = build(sa), B = build(sb);
  for (bool views : {true, false}) {
    PairPlan plan = plan_pair(A, B, config(views));
    CHECK(plan.result == PairResult::Ok);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::CountChanged)] == 1);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::StructureChanged)] == 1);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::Teleport)] == 1);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::Rotation)] == 1);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::Spawned)] == 1);
    apply_alpha(plan, 0.5);
    const Mat34 vHalf = camera(0, 6, 50, 600);
    for (uint32_t owner : {0x1002u, 0x1003u, 0x1004u, 0x1005u, 0x2000u}) {
      const size_t i = find_draw(B, owner);
      const Draw& d = B.draws[i];
      const Mat34 pnB = load_mat(B.uniforms.data() + d.uniformOffset + d.prefix + kPosOffset + d.pnSlot * kMatrixBytes);
      if (!views) {
        CHECK(plan.drawUniform[i] == d.uniformOffset);  // B's bytes
      } else {
        // B's object pose, camera at alpha: V(0.5) * V_B^-1 * PN_B.
        const Mat34 world = mul(view_inverse(make_view(B.views[0])), pnB);
        CHECK(near(bound_pos(plan, B, i, d.pnSlot), mul(vHalf, world), 2e-3f));
      }
    }
    // Other owners are still interpolated.
    CHECK(plan.drawUniform[find_draw(B, 0x1000)] >= plan.arenaBase);
    // Whole owner snaps: every part of the count-changed owner.
    for (int p = 0; p < sb.objects[2].parts; ++p) {
      const size_t i = find_draw(B, 0x1002, p);
      if (!views) CHECK(plan.drawUniform[i] == B.draws[i].uniformOffset);
    }
  }
}

void test_camera_cuts() {
  {  // with views: camera jumps
    SceneFrame sa = scene_a(), sb = scene_a();
    sb.id = 11;
    sb.cams = {camera(0, 900, 50, 600)};
    CHECK(plan_pair(build(sa), build(sb), config(true)).result == PairResult::CameraCut);
    // without views the same frames are a cut by median motion
    CHECK(plan_pair(build(sa), build(sb), config(false)).result == PairResult::CameraCut);
  }
  {  // with views: camera turns 90 degrees
    SceneFrame sa = scene_a(), sb = scene_a();
    sb.id = 11;
    sb.cams = {camera(90, 0, 50, 600)};
    CHECK(plan_pair(build(sa), build(sb), config(true)).result == PairResult::CameraCut);
  }
  {  // a normal pan is not a cut
    CHECK(plan_pair(build(scene_a()), build(scene_b()), config(true)).result == PairResult::Ok);
    CHECK(plan_pair(build(scene_a()), build(scene_b()), config(false)).result == PairResult::Ok);
  }
  {  // one object teleporting is not a camera cut
    SceneFrame sb = scene_b();
    sb.objects[7].world = translate(5000, 0, 0);
    const PairPlan plan = plan_pair(build(scene_a()), build(sb), config(false));
    CHECK(plan.result == PairResult::Ok);
    CHECK(plan.stats.snappedOwners[static_cast<size_t>(Snap::Teleport)] == 1);
  }
  {  // scene change: every owner new
    SceneFrame sb = scene_b();
    for (auto& o : sb.objects) o.owner += (o.owner != 0 ? 0x100000u : 0u);
    CHECK(plan_pair(build(scene_a()), build(sb), config(true)).result == PairResult::SceneChange);
  }
}

void test_ui_untouched() {
  const Frame A = build(scene_a()), B = build(scene_b());
  PairPlan plan = plan_pair(A, B, config(true));
  apply_alpha(plan, 0.37);
  for (size_t i = 0; i < B.draws.size(); ++i) {
    if (B.draws[i].ortho || B.draws[i].owner == 0) {
      CHECK(plan.drawUniform[i] == B.draws[i].uniformOffset);
    } else {
      CHECK(plan.drawUniform[i] >= plan.arenaBase);
      CHECK(plan.drawUniform[i] % kAlign == 0);
    }
  }
  // No draw is left without a block, and no block overflows the arena.
  for (size_t i = 0; i < B.draws.size(); ++i) {
    if (plan.drawUniform[i] >= plan.arenaBase)
      CHECK(plan.drawUniform[i] - plan.arenaBase + B.draws[i].uniformSize <= plan.arena.size());
  }
}

void test_reordered_draws_match() {
  SceneFrame sb = scene_b();
  sb.shuffle = true;
  const Frame A = build(scene_a()), Bs = build(sb), B = build(scene_b());
  PairPlan shuffled = plan_pair(A, Bs, config(true));
  PairPlan plain = plan_pair(A, B, config(true));
  CHECK(shuffled.result == PairResult::Ok);
  CHECK(shuffled.stats.matched == plain.stats.matched);
  CHECK(shuffled.stats.snappedDraws == 0);
  apply_alpha(shuffled, 0.4);
  apply_alpha(plain, 0.4);
  for (uint32_t owner : {0x1000u, 0x1001u, 0x1002u, 0x1017u}) {
    for (int p = 0; p < 3; ++p) {
      bool exists = false;
      for (const auto& d : B.draws) exists |= d.owner == owner;
      int count = 0;
      for (const auto& d : B.draws) count += d.owner == owner;
      if (!exists || p >= count) continue;
      const size_t is = find_draw(Bs, owner, p), ip = find_draw(B, owner, p);
      CHECK(near(bound_pos(shuffled, Bs, is, Bs.draws[is].pnSlot), bound_pos(plain, B, ip, B.draws[ip].pnSlot), 1e-5f));
    }
  }
}

void test_sprite_flip_and_indexed() {
  SceneFrame sa = scene_a(), sb = scene_b();
  sa.objects[6].world = mul(translate(120, 0, 0), scale(1, 1, 1));
  sb.objects[6].world = mul(translate(120, 0, 0), scale(-0.6f, 1, 1));  // turning around
  sa.objects[8].indexed = sb.objects[8].indexed = true;
  sb.objects[8].world = translate(164, 0, 0);
  const Frame A = build(sa), B = build(sb);
  PairPlan plan = plan_pair(A, B, config(true));
  CHECK(plan.result == PairResult::Ok);
  CHECK(plan.stats.snappedDraws == 0);
  apply_alpha(plan, 0.5);
  const Mat34 v = camera(0, 6, 50, 600);
  // x scale 1 -> -0.6 lerps through 0.2 at the half.
  CHECK(near(bound_pos(plan, B, find_draw(B, 0x1006)), mul(v, mul(translate(120, 0, 0), scale(0.2f, 1, 1))), 2e-3f));
  // Skinned: all 10 slots follow the object (160 -> 164, half = 162).
  const size_t i = find_draw(B, 0x1008);
  for (uint32_t slot = 0; slot < kMaxPnMtx; ++slot)
    CHECK(near(bound_pos(plan, B, i, slot), mul(v, translate(162.0f + static_cast<float>(slot), 0, 0)), 2e-3f));
}

void test_math() {
  const Mat34 m = mul(translate(3, -2, 7), mul(rot_y(40), scale(2, 3, 0.5f)));
  Rigid r;
  CHECK(decompose(m, r));
  CHECK(near(compose(r), m, 1e-4f));
  Rigid mirrored;
  CHECK(!decompose(scale(-1, 1, 1), mirrored));
  const View va = make_view(camera(0, 0, 0, 100)), vb = make_view(camera(30, 50, 0, 100));
  CHECK(va.valid && vb.valid);
  CHECK(near(view_at(va, vb, 0.0f), camera(0, 0, 0, 100), 1e-4f));
  CHECK(near(view_at(va, vb, 1.0f), camera(30, 50, 0, 100), 1e-4f));
  CHECK(near(view_at(va, vb, 0.5f), camera(15, 25, 0, 100), 1e-4f));
  CHECK(!make_view(scale(2, 2, 2)).valid);
}

// ~3000 draws: 2400 3D (10% skinned) in 600 owners, 600 UI draws.
void bench() {
  SceneFrame sa, sb;
  sa.id = 1;
  sb.id = 2;
  sa.cams = {camera(0, 0, 50, 600)};
  sb.cams = {camera(1, 8, 50, 600)};
  for (uint32_t i = 0; i < 600; ++i) {
    Object o;
    o.owner = 0x10000 + i;
    o.parts = 4;
    o.indexed = i % 10 == 0;
    o.seed = i + 3;
    o.world = translate(static_cast<float>(i % 40) * 25.0f, static_cast<float>(i / 40) * 10.0f, 0);
    sa.objects.push_back(o);
    if (i % 3 == 0) o.world = mul(o.world, rot_y(5));
    sb.objects.push_back(o);
  }
  for (uint32_t i = 0; i < 150; ++i) {
    Object ui;
    ui.owner = 0x90000 + i;
    ui.camera = 1;
    ui.ortho = true;
    ui.parts = 4;
    ui.seed = 5000 + i;
    sa.objects.push_back(ui);
    sb.objects.push_back(ui);
  }
  sb.shuffle = true;
  const Frame A = build(sa), B = build(sb);
  using Clock = std::chrono::steady_clock;
  PairPlan plan;
  const int planReps = 20;
  auto t0 = Clock::now();
  for (int r = 0; r < planReps; ++r) plan = plan_pair(A, B, config(true));
  const double planUs = std::chrono::duration<double, std::micro>(Clock::now() - t0).count() / planReps;
  CHECK(plan.result == PairResult::Ok);
  const int applyReps = 200;
  t0 = Clock::now();
  for (int r = 0; r < applyReps; ++r) apply_alpha(plan, 0.1 + 0.8 * (r % 7) / 7.0);
  const double applyUs = std::chrono::duration<double, std::micro>(Clock::now() - t0).count() / applyReps;
  std::printf("bench: draws=%zu 3d=%u owners=%u blocks=%u slots=%u arena_kb=%.0f plan_us=%.0f (once per game frame) "
              "apply_us=%.0f (per refresh)\n",
              B.draws.size(), plan.stats.eligible, plan.stats.owners, plan.stats.blocks, plan.stats.slots,
              plan.arena.size() / 1024.0, planUs, applyUs);
  // Budget guard (generous: debug-ish CI machines): the per-refresh part must
  // stay far below a 144 Hz refresh (6.9 ms).
  CHECK(applyUs < 3000.0);
}

} // namespace

int main(int argc, char** argv) {
  test_math();
  test_endpoints_byte_exact();
  test_mid_alpha_pose();
  test_rotating_camera();
  test_translation_camera_views_equivalent();
  test_owner_snaps();
  test_camera_cuts();
  test_ui_untouched();
  test_reordered_draws_match();
  test_sprite_flip_and_indexed();
  if (!(argc > 1 && std::string(argv[1]) == "--no-bench")) bench();
  if (gFailures != 0) {
    std::fprintf(stderr, "frame interp test: %d failure(s)\n", gFailures);
    return 1;
  }
  std::printf("frame interp test: all checks passed\n");
  return 0;
}
