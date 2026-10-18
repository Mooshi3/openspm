#pragma once

// OpenSPM draw tags, renderer side (Stage B of docs/PRESENTATION-ARCHITECTURE.md).
//
// Pure C++ (no Aurora, no clocks): decodes the owner markers the LLVM bridge
// writes into the GX FIFO (tools/native_bridge/m14_draw_tags.h has the wire
// format) and keeps per-frame coverage statistics. Embedded into Aurora's
// lib/gfx/recording.cpp by apply_draw_tags_patch.py and unit tested by
// tests/m14_draw_tags_test.cpp together with the bridge encoder.

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <unordered_map>

namespace openspm_draw_tags {

inline constexpr std::uint8_t kOwnerReg = 0x7E;
inline constexpr std::uint8_t kControlReg = 0x7F;
inline constexpr std::uint8_t kViewHiReg = 0x7C;  // camera view record, float bits [31:16]
inline constexpr std::uint8_t kViewLoReg = 0x7D;  // camera view record, float bits [15:0]
inline constexpr std::uint32_t kViewElements = 12;  // row-major 3x4 world->view matrix
inline constexpr std::uint32_t kKindEnd = 0;
inline constexpr std::uint32_t kKindBegin = 1;
inline constexpr std::uint32_t kKindFrame = 2;
inline constexpr std::uint32_t kCameraNone = 0xF;
inline constexpr std::uint32_t kCameraSlots = 16;

struct Tag {
  std::uint32_t owner = 0;  // 0 = no owner
  std::uint8_t camera = static_cast<std::uint8_t>(kCameraNone);
  bool ortho = false;
};

// Per-draw sidecar packing: [3:0] camera, [4] orthographic camera, [5] tagged.
inline std::uint16_t pack_meta(const Tag& tag) noexcept {
  return static_cast<std::uint16_t>((tag.camera & 0xFu) | (tag.ortho ? 0x10u : 0u) |
                                    (tag.owner != 0 ? 0x20u : 0u));
}

// Statistics over the frames of one report window.
struct Window {
  std::uint64_t frames = 0;
  std::uint64_t draws = 0;
  std::uint64_t tagged = 0;
  std::array<std::uint64_t, kCameraSlots> cameraDraws{};
  std::uint32_t orthoCameras = 0;        // bit per camera seen with an ortho projection
  std::uint32_t perspectiveCameras = 0;  // bit per camera seen with a perspective projection
  std::uint64_t begins = 0;
  std::uint64_t ends = 0;
  std::uint64_t malformed = 0;     // control word without its owner word, unknown kind
  std::uint64_t repeatBegins = 0;  // same owner begun twice in one frame
  std::uint64_t frameGaps = 0;     // frame sequence skipped (marker lost)
  std::uint64_t ownersLast = 0;    // distinct owners with draws in the last frame
  std::uint64_t ownersMax = 0;
  std::uint64_t spawned = 0;       // owners with draws now but not in the previous frame
  std::uint64_t despawned = 0;     // owners with draws in the previous frame but not now
  std::uint64_t countChanged = 0;  // owners in both frames whose draw count changed
  std::uint64_t views = 0;         // completed camera view records
  std::uint64_t viewErrors = 0;    // view halves out of order / bad element
};

class Tracker {
public:
  // Feed one BP write to 0x7E/0x7F (value = low 24 bits). Returns true when
  // the owner of subsequent draws changed; the renderer must then not merge a
  // following draw into the previous draw command.
  bool on_bp(std::uint8_t reg, std::uint32_t value) noexcept {
    value &= 0x00FFFFFFu;
    if (reg == kViewHiReg || reg == kViewLoReg) {
      on_view_bp(reg, value);
      return false;
    }
    if (reg == kOwnerReg) {
      mPendingLow = value;
      mPendingValid = true;
      return false;
    }
    if (reg != kControlReg) {
      return false;
    }
    const std::uint32_t kind = (value >> 16) & 3u;
    const bool hadLow = mPendingValid;
    const std::uint32_t low = mPendingLow;
    mPendingValid = false;
    const Tag before = mTag;
    if (kind == kKindBegin && hadLow) {
      mTag.owner = low | ((value & 0xFFu) << 24);
      mTag.camera = static_cast<std::uint8_t>((value >> 8) & 0xFu);
      mTag.ortho = ((value >> 12) & 1u) != 0;
      if (mTag.owner == 0) mTag.owner = 1;  // 0 is reserved for "no owner"
      ++mWindow.begins;
      if (!mBegunThisFrame.emplace(mTag.owner, 0u).second) ++mWindow.repeatBegins;
      if (mTag.camera < kCameraSlots) {
        (mTag.ortho ? mWindow.orthoCameras : mWindow.perspectiveCameras) |= 1u << mTag.camera;
      }
    } else if (kind == kKindFrame && hadLow) {
      finish_frame();
      ++mFrameSerial;
      mViewMask.fill(0);
      mViewHiValid.fill(0);
      if (mHaveFrameSeq && ((mFrameSeq + 1u) & 0x00FFFFFFu) != low) ++mWindow.frameGaps;
      mFrameSeq = low;
      mHaveFrameSeq = true;
      mTag = Tag{};
    } else {
      if (kind != kKindEnd) ++mWindow.malformed;  // fail closed: no owner
      else ++mWindow.ends;
      mTag = Tag{};
    }
    return before.owner != mTag.owner || before.camera != mTag.camera;
  }

  // One GX draw command recorded under the current tag.
  void on_draw() noexcept {
    ++mWindow.draws;
    if (mTag.owner == 0) return;
    ++mWindow.tagged;
    if (mTag.camera < kCameraSlots) ++mWindow.cameraDraws[mTag.camera];
    ++mCurrent[mTag.owner];
  }

  const Tag& current() const noexcept { return mTag; }
  // Frame markers seen so far (view records are valid until the next one).
  std::uint64_t frame_serial() const noexcept { return mFrameSerial; }
  // After on_bp: the camera whose view record just completed (-1 = none).
  int take_view(std::array<float, kViewElements>& out) noexcept {
    const int camera = mCompletedView;
    if (camera < 0) return -1;
    mCompletedView = -1;
    std::memcpy(out.data(), mViewBits[static_cast<std::size_t>(camera)].data(), sizeof(float) * kViewElements);
    return camera;
  }
  bool frame_ended() noexcept {
    const bool ended = mFrameEnded;
    mFrameEnded = false;
    return ended;
  }
  const std::unordered_map<std::uint32_t, std::uint32_t>& last_frame_owners() const noexcept {
    return mPrevious;
  }

  // Close the report window when at least intervalNs passed since it opened.
  // Call only right after a frame marker so windows hold whole frames.
  bool take_report(std::uint64_t nowNs, std::uint64_t intervalNs, Window& out) noexcept {
    if (!mWindowOpen) {
      mWindowOpen = true;
      mWindowStartNs = nowNs;
      return false;
    }
    if (nowNs - mWindowStartNs < intervalNs) return false;
    out = mWindow;
    mWindow = Window{};
    mWindowStartNs = nowNs;
    return true;
  }

  static std::string format(const Window& w) {
    char line[1024];
    int used = std::snprintf(line, sizeof(line),
                             "M14 draw tags: frames=%llu draws=%llu tagged=%llu (%.1f%%) untagged=%llu cameras=",
                             static_cast<unsigned long long>(w.frames), static_cast<unsigned long long>(w.draws),
                             static_cast<unsigned long long>(w.tagged),
                             w.draws != 0 ? 100.0 * static_cast<double>(w.tagged) / static_cast<double>(w.draws)
                                          : 0.0,
                             static_cast<unsigned long long>(w.draws - w.tagged));
    bool any = false;
    for (std::uint32_t c = 0; c < kCameraSlots && used > 0 && used < static_cast<int>(sizeof(line)) - 48; ++c) {
      if (w.cameraDraws[c] == 0) continue;
      const bool ortho = (w.orthoCameras >> c) & 1u;
      const bool persp = (w.perspectiveCameras >> c) & 1u;
      used += std::snprintf(line + used, sizeof(line) - static_cast<std::size_t>(used), "%s%u:%s:%.1f%%",
                            any ? "," : "", c, ortho && persp ? "mixed" : (ortho ? "2D" : "3D"),
                            w.draws != 0 ? 100.0 * static_cast<double>(w.cameraDraws[c]) /
                                               static_cast<double>(w.draws)
                                         : 0.0);
      any = true;
    }
    if (!any && used > 0 && used < static_cast<int>(sizeof(line)) - 8) {
      used += std::snprintf(line + used, sizeof(line) - static_cast<std::size_t>(used), "none");
    }
    if (used > 0 && used < static_cast<int>(sizeof(line))) {
      std::snprintf(line + used, sizeof(line) - static_cast<std::size_t>(used),
                    " owners=%llu owners_max=%llu spawned=%llu despawned=%llu count_changed=%llu begins=%llu "
                    "ends=%llu repeat_begins=%llu malformed=%llu frame_gaps=%llu views=%llu view_errors=%llu",
                    static_cast<unsigned long long>(w.ownersLast), static_cast<unsigned long long>(w.ownersMax),
                    static_cast<unsigned long long>(w.spawned), static_cast<unsigned long long>(w.despawned),
                    static_cast<unsigned long long>(w.countChanged), static_cast<unsigned long long>(w.begins),
                    static_cast<unsigned long long>(w.ends), static_cast<unsigned long long>(w.repeatBegins),
                    static_cast<unsigned long long>(w.malformed), static_cast<unsigned long long>(w.frameGaps),
                    static_cast<unsigned long long>(w.views), static_cast<unsigned long long>(w.viewErrors));
    }
    return line;
  }

private:
  void on_view_bp(std::uint8_t reg, std::uint32_t value) noexcept {
    const std::uint32_t camera = (value >> 20) & 0xFu;
    const std::uint32_t element = (value >> 16) & 0xFu;
    const std::uint32_t half = value & 0xFFFFu;
    if (element >= kViewElements || camera >= kCameraNone) {
      ++mWindow.viewErrors;
      return;
    }
    const std::uint16_t bit = static_cast<std::uint16_t>(1u << element);
    if (reg == kViewHiReg) {
      mViewHi[camera][element] = static_cast<std::uint16_t>(half);
      mViewHiValid[camera] = static_cast<std::uint16_t>(mViewHiValid[camera] | bit);
      return;
    }
    if ((mViewHiValid[camera] & bit) == 0) {  // low half without its high half
      ++mWindow.viewErrors;
      return;
    }
    mViewHiValid[camera] = static_cast<std::uint16_t>(mViewHiValid[camera] & ~bit);
    mViewBits[camera][element] = (static_cast<std::uint32_t>(mViewHi[camera][element]) << 16) | half;
    mViewMask[camera] = static_cast<std::uint16_t>(mViewMask[camera] | bit);
    if (mViewMask[camera] == (1u << kViewElements) - 1u) {
      mViewMask[camera] = 0;
      mCompletedView = static_cast<int>(camera);
      ++mWindow.views;
    }
  }

  void finish_frame() noexcept {
    if (mSeenFrame) {
      ++mWindow.frames;
      std::uint64_t spawned = 0;
      std::uint64_t changed = 0;
      std::uint64_t kept = 0;
      for (const auto& [owner, count] : mCurrent) {
        const auto it = mPrevious.find(owner);
        if (it == mPrevious.end()) {
          ++spawned;
        } else {
          ++kept;
          if (it->second != count) ++changed;
        }
      }
      if (mHavePrevious) {  // the first complete frame has nothing to compare with
        mWindow.spawned += spawned;
        mWindow.countChanged += changed;
        mWindow.despawned += mPrevious.size() - kept;
      }
      mHavePrevious = true;
      mWindow.ownersLast = mCurrent.size();
      if (mWindow.ownersLast > mWindow.ownersMax) mWindow.ownersMax = mWindow.ownersLast;
      mFrameEnded = true;
    }
    mSeenFrame = true;
    mPrevious.swap(mCurrent);
    mCurrent.clear();
    mBegunThisFrame.clear();
  }

  Tag mTag{};
  std::uint64_t mFrameSerial = 0;
  std::array<std::array<std::uint32_t, kViewElements>, kCameraSlots> mViewBits{};
  std::array<std::array<std::uint16_t, kViewElements>, kCameraSlots> mViewHi{};
  std::array<std::uint16_t, kCameraSlots> mViewHiValid{};
  std::array<std::uint16_t, kCameraSlots> mViewMask{};
  int mCompletedView = -1;
  std::uint32_t mPendingLow = 0;
  bool mPendingValid = false;
  bool mSeenFrame = false;
  bool mHavePrevious = false;
  bool mFrameEnded = false;
  bool mHaveFrameSeq = false;
  std::uint32_t mFrameSeq = 0;
  bool mWindowOpen = false;
  std::uint64_t mWindowStartNs = 0;
  Window mWindow{};
  std::unordered_map<std::uint32_t, std::uint32_t> mCurrent;
  std::unordered_map<std::uint32_t, std::uint32_t> mPrevious;
  std::unordered_map<std::uint32_t, std::uint32_t> mBegunThisFrame;
};

} // namespace openspm_draw_tags
