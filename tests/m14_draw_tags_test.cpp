// Synthetic FIFO test for OpenSPM draw tags (Stage B).
//
// Drives the bridge encoder (tools/native_bridge/m14_draw_tags.h) exactly the
// way legacy_c_adapter_v42_dev69.c.in does around SPM's dispDraw loop, writes
// the markers into a byte FIFO interleaved with stand-in draw commands, parses
// that FIFO like Aurora's command processor and feeds the renderer tracker
// (renderer/aurora/draw_tags.hpp). Checks that owners are exact and stable
// when the z-sort reorders entries every frame, when objects spawn/despawn
// (shifting every later display-list entry pointer), when a callback registers
// twice, and when a callback's end marker is lost to an outer-dispatch yield.

#include "m14_draw_tags.h"
#include "draw_tags.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <random>
#include <set>
#include <vector>

static_assert(openspm_draw_tags::kOwnerReg == OPENSPM_M14_DRAW_TAG_OWNER_REG);
static_assert(openspm_draw_tags::kControlReg == OPENSPM_M14_DRAW_TAG_CONTROL_REG);
static_assert(openspm_draw_tags::kKindBegin == OPENSPM_M14_DRAW_TAG_KIND_BEGIN);
static_assert(openspm_draw_tags::kKindEnd == OPENSPM_M14_DRAW_TAG_KIND_END);
static_assert(openspm_draw_tags::kKindFrame == OPENSPM_M14_DRAW_TAG_KIND_FRAME);
static_assert(openspm_draw_tags::kCameraNone == OPENSPM_M14_DRAW_TAG_CAMERA_NONE);
static_assert(openspm_draw_tags::kViewHiReg == OPENSPM_M14_DRAW_TAG_VIEW_HI_REG);
static_assert(openspm_draw_tags::kViewLoReg == OPENSPM_M14_DRAW_TAG_VIEW_LO_REG);
static_assert(openspm_draw_tags::kViewElements == OPENSPM_M14_DRAW_TAG_VIEW_ELEMENTS);

namespace {

constexpr std::uint8_t kDrawOpcode = 0x98;  // stand-in primitive: opcode + u16 object id
int gFailures = 0;

#define CHECK(cond)                                                              \
  do {                                                                           \
    if (!(cond)) {                                                               \
      std::fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++gFailures;                                                               \
    }                                                                            \
  } while (0)

struct Object {
  int id;
  std::uint32_t callback;
  std::uint32_t param;
  std::uint32_t camera;
  int draws;
};

struct Harness {
  m14_draw_tag_occurrences occurrences{};
  std::vector<std::uint8_t> fifo;
  std::uint32_t frameSeq = 0;
  bool open = false;

  void emit(const std::uint32_t* words, std::uint32_t count) {
    std::uint8_t bytes[OPENSPM_M14_DRAW_TAG_VIEW_WORDS * OPENSPM_M14_DRAW_TAG_WORD_BYTES];
    const std::uint32_t n = m14_draw_tags_encode(words, count, bytes);
    fifo.insert(fifo.end(), bytes, bytes + n);
  }
  void state_write() {  // unrelated BP state between callbacks (scissor)
    const std::uint8_t bp[] = {0x61, 0x20, 0x00, 0x10, 0x00};
    fifo.insert(fifo.end(), bp, bp + sizeof(bp));
  }
  void draw(int object) {
    fifo.push_back(kDrawOpcode);
    fifo.push_back(static_cast<std::uint8_t>(object >> 8));
    fifo.push_back(static_cast<std::uint8_t>(object));
  }
  void frame() {  // spmarioDisp entry
    std::uint32_t words[2];
    ++frameSeq;
    m14_draw_tags_new_frame(&occurrences);
    open = false;
    emit(words, m14_draw_tags_frame_words(frameSeq, words));
  }
  // Like the bridge: resolve dispEntry's pool in registration order up to
  // this entry, then use its registration-order occurrence.
  void begin(const std::vector<Object>& pool, std::size_t index, bool ortho) {
    std::uint32_t words[2];
    while (occurrences.scanned <= index) {
      const Object& e = pool[occurrences.scanned];
      if (!m14_draw_tags_scan_entry(&occurrences, occurrences.scanned, e.callback, e.param, e.camera)) std::abort();
    }
    const Object& o = pool[index];
    const std::uint32_t occ = m14_draw_tags_registered_occurrence(&occurrences, static_cast<std::uint32_t>(index));
    const std::uint32_t owner = m14_draw_tags_owner(o.callback, o.param, o.camera, occ);
    emit(words, m14_draw_tags_begin_words(owner, o.camera, ortho ? 1u : 0u, words));
    open = true;
  }
  // Like the bridge before a 3D camera's first owner: its view record.
  void view(std::uint32_t camera, const float matrix[12]) {
    std::uint32_t bits[12], words[OPENSPM_M14_DRAW_TAG_VIEW_WORDS];
    std::memcpy(bits, matrix, sizeof(bits));
    emit(words, m14_draw_tags_view_words(camera, bits, words));
  }
  void end() {
    std::uint32_t words[1];
    if (!open) return;
    emit(words, m14_draw_tags_end_words(words));
    open = false;
  }
};

struct Parsed {
  // object id -> owners it was drawn under in one frame
  std::map<int, std::set<std::uint32_t>> ownerByObject;
  int untaggedObjectDraws = 0;
  int taggedOutsideDraws = 0;
  std::vector<std::pair<int, std::array<float, 12>>> views;  // completed records in stream order
};

// Parse like Aurora: BP writes go to handle_bp, draws are recorded with the
// currently latched tag. Object id 0xFFFF = draw outside any callback.
Parsed parse(const std::vector<std::uint8_t>& fifo, openspm_draw_tags::Tracker& tracker,
             std::vector<openspm_draw_tags::Window>& reports, std::uint64_t& nowNs) {
  Parsed out;
  for (std::size_t i = 0; i < fifo.size();) {
    const std::uint8_t op = fifo[i];
    if (op == 0x61) {
      const std::uint32_t v = (std::uint32_t(fifo[i + 1]) << 24) | (std::uint32_t(fifo[i + 2]) << 16) |
                              (std::uint32_t(fifo[i + 3]) << 8) | fifo[i + 4];
      const std::uint8_t reg = static_cast<std::uint8_t>(v >> 24);
      if (reg == openspm_draw_tags::kOwnerReg || reg == openspm_draw_tags::kControlReg ||
          reg == openspm_draw_tags::kViewHiReg || reg == openspm_draw_tags::kViewLoReg) {
        tracker.on_bp(reg, v & 0xFFFFFFu);
        std::array<float, 12> view{};
        if (const int camera = tracker.take_view(view); camera >= 0) out.views.emplace_back(camera, view);
        if (tracker.frame_ended()) {
          nowNs += 16'666'667;
          openspm_draw_tags::Window w;
          if (tracker.take_report(nowNs, 1'000'000'000ull, w)) reports.push_back(w);
        }
      }
      i += 5;
    } else if (op == kDrawOpcode) {
      const int object = (fifo[i + 1] << 8) | fifo[i + 2];
      tracker.on_draw();
      const auto owner = tracker.current().owner;
      if (object == 0xFFFF) {
        if (owner != 0) ++out.taggedOutsideDraws;
      } else if (owner == 0) {
        ++out.untaggedObjectDraws;
      } else {
        out.ownerByObject[object].insert(owner);
      }
      i += 3;
    } else {
      std::fprintf(stderr, "unexpected FIFO byte 0x%02X\n", op);
      std::exit(2);
    }
  }
  return out;
}

// One SPM frame: dispEntry registration order -> qsort by z -> camDraw walks
// cameras -> dispDraw walks the sorted list for that camera.
void run_frame(Harness& h, const std::vector<Object>& registered, std::mt19937& rng, bool escapes,
               int outsideDraws) {
  std::vector<std::size_t> sorted;  // registration indices, z-sorted
  for (std::size_t i = 0; i < registered.size(); ++i) sorted.push_back(i);
  std::shuffle(sorted.begin(), sorted.end(), rng);  // z changes every frame
  h.frame();
  std::bernoulli_distribution escape(escapes ? 0.3 : 0.0);
  for (std::uint32_t camera : {5u, 8u}) {  // 3D world, then 2D UI
    bool viewSent = false;
    for (const std::size_t index : sorted) {
      const Object* o = &registered[index];
      if (o->camera != camera) continue;
      h.state_write();
      if (camera == 5u && !viewSent) {  // 3D camera only, once per frame
        const float view[12] = {1, 0, 0, -static_cast<float>(h.frameSeq), 0, 1, 0, -50, 0, 0, 1, -600};
        h.view(camera, view);
        viewSent = true;
      }
      h.begin(registered, index, camera == 8u);
      for (int d = 0; d < o->draws; ++d) h.draw(o->id);
      if (!escape(rng)) h.end();  // an escaped callback never gets its end marker
      h.state_write();
    }
  }
  if (h.open) h.end();  // DEMODoneRender
  for (int d = 0; d < outsideDraws; ++d) h.draw(0xFFFF);
}

void test_stable_owners_under_reordering() {
  std::mt19937 rng(1234);
  std::vector<Object> objects;
  int id = 0;
  for (int i = 0; i < 300; ++i) {  // map objects: one callback, many params
    objects.push_back({id++, 0x8008533Cu, 0x80A00000u + 0x40u * i, 5u, 1 + i % 3});
  }
  for (int i = 0; i < 16; ++i) {  // NPCs with two callbacks each
    objects.push_back({id++, 0x80100000u, 0x80B00000u + 0x748u * i, 5u, 4});
    objects.push_back({id++, 0x80100400u, 0x80B00000u + 0x748u * i, 5u, 1});
  }
  objects.push_back({id++, 0x80200000u, 0x80C00000u, 8u, 6});  // HUD
  objects.push_back({id++, 0x80200000u, 0x80C00000u, 8u, 2});  // same callback+param registered twice
  objects.push_back({id++, 0x80210000u, 0x80C00000u, 5u, 1});  // same param, other camera

  Harness h;
  openspm_draw_tags::Tracker tracker;
  std::vector<openspm_draw_tags::Window> reports;
  std::uint64_t now = 0;
  std::map<int, std::uint32_t> ownerOf;
  const int despawnId = 10;
  const int spawnedId = id;
  for (int frame = 0; frame < 240; ++frame) {
    std::vector<Object> registered = objects;
    if (frame >= 120) {  // object 10 despawns; a new one spawns early in the pool
      registered.erase(std::remove_if(registered.begin(), registered.end(),
                                      [&](const Object& o) { return o.id == despawnId; }),
                       registered.end());
      registered.insert(registered.begin() + 3, Object{spawnedId, 0x80300000u, 0x80D00000u, 5u, 2});
    }
    if (frame >= 180) {
      for (auto& o : registered) {
        if (o.id == 20) o.draws = 7;  // model/part-count change
      }
    }
    h.fifo.clear();
    run_frame(h, registered, rng, frame % 2 == 1, 3);
    const Parsed parsed = parse(h.fifo, tracker, reports, now);
    CHECK(parsed.untaggedObjectDraws == 0);
    CHECK(parsed.taggedOutsideDraws == 0);
    std::set<std::uint32_t> ownersThisFrame;
    for (const auto& [object, owners] : parsed.ownerByObject) {
      CHECK(owners.size() == 1);  // every draw of one callback has one owner
      const std::uint32_t owner = *owners.begin();
      CHECK(ownersThisFrame.insert(owner).second);  // distinct objects, distinct owners
      const auto it = ownerOf.find(object);
      if (it == ownerOf.end()) {
        ownerOf.emplace(object, owner);
      } else {
        CHECK(it->second == owner);  // stable across frames, z order and spawn/despawn
      }
    }
    CHECK(parsed.ownerByObject.size() == registered.size());
    // One exact view record per frame for the 3D camera.
    CHECK(parsed.views.size() == 1);
    if (parsed.views.size() == 1) {
      CHECK(parsed.views[0].first == 5);
      CHECK(parsed.views[0].second[3] == -static_cast<float>(h.frameSeq));
      CHECK(parsed.views[0].second[11] == -600.0f);
    }
  }
  CHECK(ownerOf.count(spawnedId) == 1);
  CHECK(!reports.empty());
  std::uint64_t spawned = 0, despawned = 0, changed = 0, frames = 0, draws = 0, tagged = 0;
  for (const auto& w : reports) {
    spawned += w.spawned;
    despawned += w.despawned;
    changed += w.countChanged;
    frames += w.frames;
    draws += w.draws;
    tagged += w.tagged;
    CHECK(w.malformed == 0);
    CHECK(w.repeatBegins == 0);
    CHECK(w.frameGaps == 0);
    CHECK(w.views == w.frames);
    CHECK(w.viewErrors == 0);
    CHECK(w.cameraDraws[5] > 0 && w.cameraDraws[8] > 0);
    CHECK(((w.perspectiveCameras >> 5) & 1u) && ((w.orthoCameras >> 8) & 1u));
  }
  CHECK(spawned == 1);    // frame 120: the new object
  CHECK(despawned == 1);  // frame 120: object 10
  CHECK(changed == 1);    // frame 180: object 20 draws 7 instead of 3
  CHECK(frames >= 150);
  CHECK(tagged < draws);  // the 3 DEMODoneRender draws per frame stay untagged
  const std::string line = openspm_draw_tags::Tracker::format(reports.front());
  std::printf("%s\n", line.c_str());
  CHECK(line.find("M14 draw tags: frames=") == 0);
  CHECK(line.find("5:3D:") != std::string::npos);
  CHECK(line.find("8:2D:") != std::string::npos);
  CHECK(line.find(" count_changed=") != std::string::npos);
}

void test_fail_closed_decoding() {
  openspm_draw_tags::Tracker tracker;
  // A begin control word without its owner word is malformed -> no owner.
  std::uint32_t words[2];
  m14_draw_tags_begin_words(0x12345678u, 5u, 0u, words);
  CHECK(!tracker.on_bp(0x7F, words[1] & 0xFFFFFFu));
  CHECK(tracker.current().owner == 0);
  CHECK(tracker.on_bp(0x7E, words[0] & 0xFFFFFFu) == false);
  CHECK(tracker.on_bp(0x7F, words[1] & 0xFFFFFFu));
  CHECK(tracker.current().owner == 0x12345678u);
  CHECK(tracker.current().camera == 5u);
  CHECK(openspm_draw_tags::pack_meta(tracker.current()) == 0x25u);
  m14_draw_tags_end_words(words);
  CHECK(tracker.on_bp(0x7F, words[0] & 0xFFFFFFu));
  CHECK(tracker.current().owner == 0);
  // Owner ids never collide with "no owner", and the key fields all matter.
  CHECK(m14_draw_tags_owner(0, 0, 0, 0) != 0);
  CHECK(m14_draw_tags_owner(1, 2, 5, 0) != m14_draw_tags_owner(1, 2, 5, 1));
  CHECK(m14_draw_tags_owner(1, 2, 5, 0) != m14_draw_tags_owner(1, 2, 8, 0));
  CHECK(m14_draw_tags_owner(1, 2, 5, 0) != m14_draw_tags_owner(2, 1, 5, 0));
}

// View records: exact bits (including negative zero and NaN payloads), no
// completion from a partial record, errors for halves out of order.
void test_view_records() {
  openspm_draw_tags::Tracker tracker;
  std::uint32_t bits[12], words[OPENSPM_M14_DRAW_TAG_VIEW_WORDS];
  for (std::uint32_t e = 0; e < 12; ++e) bits[e] = 0x3F800000u + e * 0x01010101u;
  bits[4] = 0x80000000u;  // -0.0f
  bits[7] = 0x7FC01234u;  // NaN payload survives
  const std::uint32_t n = m14_draw_tags_view_words(3u, bits, words);
  CHECK(n == 24u);
  std::array<float, 12> out{};
  for (std::uint32_t i = 0; i < n; ++i) {
    CHECK(!tracker.on_bp(static_cast<std::uint8_t>(words[i] >> 24), words[i] & 0xFFFFFFu));  // never an owner change
    const int camera = tracker.take_view(out);
    CHECK(camera == (i + 1 == n ? 3 : -1));
  }
  CHECK(std::memcmp(out.data(), bits, sizeof(bits)) == 0);
  // A frame marker in the middle drops the partial record.
  std::uint32_t frame[2];
  m14_draw_tags_frame_words(1u, frame);
  for (std::uint32_t i = 0; i < 10; ++i) tracker.on_bp(static_cast<std::uint8_t>(words[i] >> 24), words[i] & 0xFFFFFFu);
  const std::uint64_t serial = tracker.frame_serial();
  tracker.on_bp(0x7E, frame[0] & 0xFFFFFFu);
  tracker.on_bp(0x7F, frame[1] & 0xFFFFFFu);
  CHECK(tracker.frame_serial() == serial + 1);
  for (std::uint32_t i = 10; i < n; ++i) {
    tracker.on_bp(static_cast<std::uint8_t>(words[i] >> 24), words[i] & 0xFFFFFFu);
    CHECK(tracker.take_view(out) == -1);
  }
  // Low half without high half, bad element, camera 15: errors, no record.
  openspm_draw_tags::Tracker other;
  other.on_bp(0x7D, words[1] & 0xFFFFFFu);
  other.on_bp(0x7C, (words[0] & 0xF0FFFFu) | (13u << 16));
  other.on_bp(0x7C, (words[0] & 0x0FFFFFu) | (15u << 20));
  CHECK(other.take_view(out) == -1);
  openspm_draw_tags::Window w;
  CHECK(!other.take_report(0, 1, w));
}

}  // namespace

int main() {
  test_stable_owners_under_reordering();
  test_fail_closed_decoding();
  test_view_records();
  if (gFailures != 0) {
    std::fprintf(stderr, "m14_draw_tags_test: %d failure(s)\n", gFailures);
    return 1;
  }
  std::printf("m14_draw_tags_test: PASS\n");
  return 0;
}
