// Synthetic recorder for the actual C++ emitted by the dev140 adapter.
// Compiled by test_interpolation_replay_prep.py; never linked into the game.
#include <aurora/math.hpp>
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <unordered_map>
#include <vector>
using namespace aurora;
constexpr size_t UniformBufferSize = 24u * 1024u * 1024u;
#define AURORA_ALIGN(value, alignment) (((value) + (alignment) - 1u) & ~((alignment) - 1u))
struct Range { uint32_t offset = 0, size = 0; };
struct ByteBuffer {
  std::vector<uint8_t> bytes = std::vector<uint8_t>(UniformBufferSize);
  size_t length = 0;
  uint8_t* data() { return bytes.data(); }
  const uint8_t* data() const { return bytes.data(); }
  size_t size() const { return length; }
  void append(const void* src, size_t count) {
    assert(length + count <= bytes.size());
    std::memcpy(data() + length, src, count); length += count;
  }
  void append_zeroes(size_t count) {
    assert(length + count <= bytes.size());
    std::memset(data() + length, 0, count); length += count;
  }
};
// Deterministic hash stand-in: matching exercises production range selection,
// identity construction, duplicate handling and uniform ownership, not XXH3.
uint64_t xxh3_hash_s(const void* data, size_t size, uint64_t hash) {
  auto* p = static_cast<const uint8_t*>(data);
  for (size_t i = 0; i < size; ++i) hash = (hash ^ p[i]) * UINT64_C(1099511628211);
  return hash;
}
namespace gx {
constexpr uint32_t MaxPnMtx = 10, MaxTexMtx = 10, MaxUniformSize = 4096;
struct PnMtx { Mat3x4<float> pos{}, nrm{}; };
struct DrawData {
  uint64_t pipeline = 0;
  struct { uint64_t textureBindGroup = 0; } bindGroups;
  uint32_t vtxCount = 3, indexCount = 3;
  Range uniformRange{}, vertRange{}, idxRange{};
  struct { uint32_t currentPnMtx = 0; } immediateData;
};
void render() {}
}
template <auto, typename> void encode_draw() {}
template <typename T> const T& inline_payload(const void* ptr) { return *static_cast<const T*>(ptr); }
enum class CommandType { Draw };
struct Command {
  CommandType type = CommandType::Draw;
  struct {
    struct {
      uint32_t m14ReplayUniformPrefixBytes = 16;
      bool m14ReplayIndexedMatrices = false, m14ReplayGX = true;
      void (*encoder)() = encode_draw<gx::render, gx::DrawData>;
      alignas(gx::DrawData) std::array<std::byte, sizeof(gx::DrawData)> payload{};
    } draw;
  } data;
};
struct Pass { std::vector<Command> commands; };
// GENERATED_PACKET_TYPE
struct FramePacket {
  std::vector<Pass> renderPasses;
  ByteBuffer uniforms, verts, indices;
  std::vector<uint8_t> m14UniformRead, m14VertexRead, m14IndexRead;
  std::vector<M14ReplayPhase> m14ReplayPhases;
  uint32_t m14ReplayOriginalUniformBytes = 0, m14ReplayMatchedUniforms = 0,
           m14ReplaySnappedUniforms = 0;
};
struct Recorder {
  FramePacket packet;
  bool active() const { return true; }
  FramePacket& frame() { return packet; }
} g_recorder;
struct Resources { struct { uint32_t minUniformBufferOffsetAlignment = 256; } limits; } res;
Resources& resources() { return res; }
struct Logger {
  template <typename... T> void info(const char*, T...) {}
  template <typename... T> void warn(const char*, T...) {}
} Log;
template <auto F, typename T> auto make_draw_command(T data) {
  decltype(Command{}.data.draw) command;
  std::memcpy(command.payload.data(), &data, sizeof(data));
  return command;
}
void push_draw_command(decltype(Command{}.data.draw) draw) {
  Command command; command.data.draw = draw;
  g_recorder.frame().renderPasses[0].commands.push_back(command);
}
struct SDL_Window {};
using SDL_DisplayID = uint32_t;
struct SDL_DisplayMode { uint32_t refresh_rate_numerator, refresh_rate_denominator; float refresh_rate; };
namespace window { SDL_Window* get_sdl_window() { return nullptr; } }
SDL_DisplayID SDL_GetDisplayForWindow(SDL_Window*) { return 0; }
const SDL_DisplayMode* SDL_GetCurrentDisplayMode(SDL_DisplayID) { return nullptr; }
// GENERATED_REPLAY_CODE

constexpr uint32_t Block = 2048;
void reset_frame(size_t bytes = Block * 2) {
  auto& f = g_recorder.frame();
  f.renderPasses.clear(); f.renderPasses.emplace_back();
  f.uniforms.length = f.verts.length = f.indices.length = 0;
  f.m14UniformRead.clear(); f.m14VertexRead.clear(); f.m14IndexRead.clear();
  f.uniforms.append_zeroes(bytes);
}
void add_draw(uint64_t identity, uint32_t uniform, float x, bool invalid = false, uint32_t prefix = 16) {
  auto& f = g_recorder.frame();
  gx::DrawData data;
  data.pipeline = identity;
  data.uniformRange = {uniform, Block};
  if (invalid) data.vertRange = {123, 128}; // Outside the empty vertex arena.
  Mat4x4<float> projection{};
  projection.m0[0] = projection.m1[1] = projection.m2[2] = projection.m3[3] = 1;
  auto* bytes = f.uniforms.data() + uniform;
  std::memcpy(bytes + prefix, &projection, sizeof(projection));
  Mat3x4<float> position{};
  position.m0[0] = position.m1[1] = position.m2[2] = 1;
  position.m0.w() = x;
  std::memcpy(bytes + prefix + sizeof(projection), &position, sizeof(position));
  Command command;
  command.data.draw.m14ReplayUniformPrefixBytes = prefix;
  std::memcpy(command.data.draw.payload.data(), &data, sizeof(data));
  f.renderPasses[0].commands.push_back(command);
}
float phase_x(size_t phase, uint32_t offset = 0, uint32_t prefix = 16) {
  const auto& f = g_recorder.frame();
  Mat3x4<float> result{};
  std::memcpy(&result, f.uniforms.data() + f.m14ReplayPhases[phase].uniformBase +
              offset + prefix + sizeof(Mat4x4<float>), sizeof(result));
  return result.m0.w();
}
void seed(bool two = true, bool shared = false) {
  sM14ReplayPrevious.clear(); sM14ReplayInterval = 0;
  sM14ReplayCadencePhase = 0; sM14ReplayTimelineStarted = false;
  reset_frame(); add_draw(1, 0, 10);
  if (two) add_draw(2, shared ? 0 : Block, shared ? 10 : 20);
  m14_prepare_interpolation_replay();
}
int main() {
  setenv("OPENSPM_M14_INTERP_REPLAY", "1", 1);
  auto& f = g_recorder.frame();
  // Bounds must cover the selected PN slot, not only slot zero.
  reset_frame(); add_draw(1, 0, 10);
  gx::DrawData shortData;
  std::memcpy(&shortData, f.renderPasses[0].commands[0].data.draw.payload.data(), sizeof(shortData));
  shortData.immediateData.currentPnMtx = gx::MaxPnMtx - 1;
  shortData.uniformRange.size = 16 + sizeof(Mat4x4<float>) + sizeof(Mat3x4<float>);
  M14ReplaySnapshot rejected;
  assert(!m14_replay_read_snapshot(f, shortData, 16, false, rejected));
  shortData.uniformRange.size = Block;
  assert(m14_replay_read_snapshot(f, shortData, 16, false, rejected));
  // One matched + one unmatched draw sharing a uniform: neither may move.
  for (bool reverse : {false, true}) {
    seed(); reset_frame();
    if (reverse) add_draw(3, 0, 30);
    add_draw(1, 0, 30);
    if (!reverse) add_draw(3, 0, 30);
    m14_prepare_interpolation_replay();
    assert(f.m14ReplayMatchedUniforms == 0 && f.m14ReplaySnappedUniforms == 1);
    assert(f.m14ReplayPhases.empty());
    assert(f.uniforms.size() == Block * 2);
  }
  // Invalid geometry/layout is still a consumer of its uniform block.
  seed(); reset_frame(); add_draw(1, 0, 30); add_draw(2, 0, 30, true);
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayMatchedUniforms == 0 && f.m14ReplaySnappedUniforms == 1);
  assert(f.m14ReplayPhases.empty());
  // Two valid matches with contradictory prior poses must snap their alias.
  seed(); reset_frame(); add_draw(1, 0, 30); add_draw(2, 0, 30);
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayMatchedUniforms == 0 && f.m14ReplaySnappedUniforms == 1);
  assert(f.m14ReplayPhases.empty());
  // Consistent aliases are safe; originals and unrelated uniform bytes stay exact.
  seed(true, true); reset_frame(); add_draw(1, 0, 30); add_draw(2, 0, 30);
  std::vector<uint8_t> original(f.uniforms.data(), f.uniforms.data() + f.uniforms.size());
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayMatchedUniforms == 1 && f.m14ReplaySnappedUniforms == 0);
  assert(phase_x(0) == 10 && std::abs(phase_x(1) - 22) < .001);
  assert(std::memcmp(original.data(), f.uniforms.data(), original.size()) == 0);
  auto clone = std::vector<uint8_t>(f.uniforms.data() + f.m14ReplayPhases[0].uniformBase,
                                   f.uniforms.data() + f.m14ReplayPhases[0].uniformBase + original.size());
  std::memcpy(clone.data() + 16, original.data() + 16, 64 + 48 * gx::MaxPnMtx);
  assert(clone == original);
  // Separate unmatched blocks count toward the denominator and stay current.
  seed(); reset_frame(); add_draw(1, 0, 30); add_draw(3, Block, 40);
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayMatchedUniforms == 1 && f.m14ReplaySnappedUniforms == 1);
  assert(phase_x(0) == 10 && phase_x(0, Block) == 40);
  // Replay cadence and the extra line/point prefix use the emitted code.
  seed(false);
  for (int i = 0; i < 3; ++i) {
    reset_frame(); add_draw(1, 0, 30 + i);
    m14_prepare_interpolation_replay();
    assert(f.m14ReplayPhases.size() == (i == 2 ? 1u : 2u));
    for (auto phase : f.m14ReplayPhases) assert(phase.uniformBase % 256 == 0);
  }
  sM14ReplayPrevious.clear(); reset_frame(); add_draw(1, 0, 10, false, 32);
  m14_prepare_interpolation_replay(); sM14ReplayInterval = 0; sM14ReplayCadencePhase = 0;
  reset_frame(); add_draw(1, 0, 30, false, 32); m14_prepare_interpolation_replay();
  assert(phase_x(0, 0, 32) == 10);
  // Complete CPU mirrors are used even when upload reads would differ.
  seed(false); reset_frame(); add_draw(1, 0, 30);
  f.m14UniformRead.assign(f.uniforms.data(), f.uniforms.data() + f.uniforms.size());
  Mat3x4<float> poison{}; poison.m0.w() = 999;
  std::memcpy(f.uniforms.data() + 16 + sizeof(Mat4x4<float>), &poison, sizeof(poison));
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayMatchedUniforms == 1 && phase_x(0) == 10);
  // Arena pressure leaves the authoritative finish scratch available.
  seed(false); reset_frame(UniformBufferSize / 2); add_draw(1, 0, 30);
  m14_prepare_interpolation_replay();
  assert(f.m14ReplayPhases.empty());
  assert(f.uniforms.size() + gx::MaxUniformSize <= UniformBufferSize);
  // Empty frame breaks history; do not interpolate across a gap.
  reset_frame(); m14_prepare_interpolation_replay();
  assert(sM14ReplayPrevious.empty());
}
