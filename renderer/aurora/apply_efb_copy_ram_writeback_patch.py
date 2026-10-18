#!/usr/bin/env python3
"""EFB texture copies that the game moves or reads through guest RAM.

Aurora keeps each GXCopyTex result only as a host texture keyed by its
destination pointer. Real hardware writes the copied pixels to RAM in the GX
texture format. SPM's capture system (0x80034BB0) copies screen rectangles
and a snapshot routine memcpys them on the CPU (0x800766C8), e.g. for the
star-door swing; the snapshot address is never a copy destination, so Aurora
sampled stale RAM and the door stayed static. Dolphin's analogue is "Store EFB
Copies to Texture Only".

Copy tracking (default; OPENSPM_M14_COPY_TAGS=0 disables): read-only. At each
DrawDone (and on a miss for a load shaped like a recent copy) read SPM's
snapshot slots and their capture entries; a texture load of a snapshot buffer
resolves to an in-stream GPU copy of the capture's host texture, frozen at the
first load. Ordinary binds cost one hash lookup. (Revision 1 wrote a tag into
RAM and corrupted game data; revision 2 fingerprinted RAM, scanned per bind and
missed zeroed buffers.)

RAM write-back (opt-in, OPENSPM_M14_EFB_COPY_RAM=1), for code that inspects
copied pixels on the CPU: record RGBA8 / RGB5A3 texture copies in the parser;
at DrawDone read them back on the render worker, downsample to logical size,
encode GX 4x4 tiles and write guest RAM (this overwrites the tag). It waits
for the GPU (~10 ms/frame in the flip/door), so it is a diagnostic.
OPENSPM_M14_EFB_COPY_RAM_MAX caps logical pixels per copy (default 262144).

Exact/idempotent; migrates its earlier revisions in place; fails closed on
unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM M14 EFB copy RAM write-back"

REGS = Path("lib/gx/regs.cpp")
FRAMEBUFFER = Path("lib/dolphin/gx/GXFrameBuffer.cpp")
RAW_HEADER = Path("include/aurora/gx_raw.hpp")

REGS_OLD = ("    copy_tex(g_gxState.texCopyDest, clear);\n  }\n}\n\n"
            "// PE bounding-box BP registers.")
REGS_NEW = ("    copy_tex(g_gxState.texCopyDest, clear);\n"
            "    // " + SIGNATURE + ": record the texture copy for optional RAM write-back.\n"
            "    raw::note_efb_copy_writeback((g_gxState.bpRegCache[0x4B] & 0x00FFFFFFu) << 5,\n"
            "                                 g_gxState.texCopyDest, static_cast<u32>(format));\n"
            "  }\n}\n\n"
            "// PE bounding-box BP registers.")
# First revision (build 7bc95e5) forward-declared the hook inside regs.cpp. bp_efb_copy
# lives in an anonymous namespace, so that declared an undefined local symbol (LNK2019).
REGS_V1_CALL = ("    m14_efb_ram_note_copy((g_gxState.bpRegCache[0x4B] & 0x00FFFFFFu) << 5,\n"
                "                          g_gxState.texCopyDest, static_cast<u32>(format));\n")
REGS_V2_CALL = ("    raw::note_efb_copy_writeback((g_gxState.bpRegCache[0x4B] & 0x00FFFFFFu) << 5,\n"
                "                                 g_gxState.texCopyDest, static_cast<u32>(format));\n")
REGS_V1_DECL = ("// " + SIGNATURE + " (defined in lib/dolphin/gx/GXFrameBuffer.cpp).\n"
                "void m14_efb_ram_note_copy(u32 guestAddress, const void* dest, u32 format) noexcept;\n\n")

HEADER_OLD = "bool submit_for_readback() noexcept;\n"
HEADER_NEW = (HEADER_OLD +
              "// " + SIGNATURE + " (OPENSPM_M14_EFB_COPY_RAM=1). Call outside the FIFO\n"
              "// parser, after submit_for_readback(), before the guest can read RAM.\n"
              "void note_efb_copy_writeback(std::uint32_t guestAddress, const void* dest,\n"
              "                             std::uint32_t format) noexcept;\n"
              "bool efb_copy_writeback_pending() noexcept;\n"
              "bool flush_efb_copy_writeback() noexcept;\n")
HEADER_V1 = HEADER_NEW.replace(
    "void note_efb_copy_writeback(std::uint32_t guestAddress, const void* dest,\n"
    "                             std::uint32_t format) noexcept;\n", "")

FB_INCLUDE_OLD = '#include "../vi/vi_internal.hpp"\n'
FB_INCLUDE_NEW = (FB_INCLUDE_OLD + '#include "../../gfx/render_worker.hpp"\n'
                  '#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_raw.hpp>\n'
                  '#include <chrono>\n#include <cstdlib>\n#include <cstring>\n#include <memory>\n#include <vector>\n')

FB_ANCHOR = '\nextern "C" {\nGXRenderModeObj GXNtsc480IntDf = {\n'
FB_CODE = r'''
// OpenSPM M14 EFB copy RAM write-back (opt-in, OPENSPM_M14_EFB_COPY_RAM=1).
#include <array>
#include <cstdio>
#include <deque>
#include <mutex>
#include <string>
#include <tuple>
#include <utility>
namespace aurora::gx {
namespace {
constexpr Module M14EfbRamLog{"aurora::gx::efb_ram"};
struct M14EfbRamPending {
  u32 guest = 0, format = 0, width = 0, height = 0;
  gfx::TextureHandle handle;
};
struct M14EfbRamStats {
  uint64_t queued = 0, written = 0, bytes = 0, skippedFormat = 0, skippedSize = 0, failed = 0, flushes = 0,
           flushUs = 0;
};
M14EfbRamStats sM14EfbRamStats;
std::vector<M14EfbRamPending> sM14EfbRamPending;

bool m14_efb_ram_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_EFB_COPY_RAM");
    const bool on = value != nullptr && value[0] == '1';
    if (on) M14EfbRamLog.info("M14 EFB copy RAM write-back active (OPENSPM_M14_EFB_COPY_RAM=1): RGBA8/RGB5A3 texture copies are written to guest RAM at DrawDone");
    return on;
  }();
  return enabled;
}
u32 m14_efb_ram_max_pixels() noexcept {
  static const u32 limit = [] {
    const char* value = std::getenv("OPENSPM_M14_EFB_COPY_RAM_MAX");
    if (value == nullptr || *value == '\0') return 262144u;
    const unsigned long parsed = std::strtoul(value, nullptr, 10);
    return parsed > 0ul ? static_cast<u32>(std::min<unsigned long>(parsed, 4194304ul)) : 262144u;
  }();
  return limit;
}
bool m14_efb_ram_should_log(uint64_t n) noexcept { return n <= 8u || (n & (n - 1u)) == 0u; }

// GX 4x4-tiled encodings. rgba is the logical-size image, row-major RGBA8.
void m14_encode_rgba8(const std::vector<uint8_t>& rgba, u32 w, u32 h, std::vector<uint8_t>& out) {
  const u32 tilesX = (w + 3u) / 4u, tilesY = (h + 3u) / 4u;
  out.assign(size_t(tilesX) * tilesY * 64u, 0u);
  for (u32 ty = 0; ty < tilesY; ++ty)
    for (u32 tx = 0; tx < tilesX; ++tx) {
      uint8_t* tile = out.data() + (size_t(ty) * tilesX + tx) * 64u;
      for (u32 i = 0; i < 16u; ++i) {
        const u32 x = tx * 4u + (i & 3u), y = ty * 4u + (i >> 2u);
        if (x >= w || y >= h) continue;
        const uint8_t* p = rgba.data() + (size_t(y) * w + x) * 4u;
        tile[i * 2u] = p[3];
        tile[i * 2u + 1u] = p[0];
        tile[32u + i * 2u] = p[1];
        tile[32u + i * 2u + 1u] = p[2];
      }
    }
}
void m14_encode_rgb5a3(const std::vector<uint8_t>& rgba, u32 w, u32 h, std::vector<uint8_t>& out) {
  const u32 tilesX = (w + 3u) / 4u, tilesY = (h + 3u) / 4u;
  out.assign(size_t(tilesX) * tilesY * 32u, 0u);
  for (u32 ty = 0; ty < tilesY; ++ty)
    for (u32 tx = 0; tx < tilesX; ++tx) {
      uint8_t* tile = out.data() + (size_t(ty) * tilesX + tx) * 32u;
      for (u32 i = 0; i < 16u; ++i) {
        const u32 x = tx * 4u + (i & 3u), y = ty * 4u + (i >> 2u);
        if (x >= w || y >= h) continue;
        const uint8_t* p = rgba.data() + (size_t(y) * w + x) * 4u;
        uint16_t v;
        if (p[3] >= 0xE0u) {
          v = static_cast<uint16_t>(0x8000u | ((p[0] >> 3u) << 10u) | ((p[1] >> 3u) << 5u) | (p[2] >> 3u));
        } else {
          v = static_cast<uint16_t>(((p[3] >> 5u) << 12u) | ((p[0] >> 4u) << 8u) | ((p[1] >> 4u) << 4u) |
                                    (p[2] >> 4u));
        }
        tile[i * 2u] = static_cast<uint8_t>(v >> 8u);
        tile[i * 2u + 1u] = static_cast<uint8_t>(v & 0xFFu);
      }
    }
}
// OpenSPM M14 EFB copy tracking (default on; OPENSPM_M14_COPY_TAGS=0 disables).
// A texture copy exists only as a host texture, but SPM also moves captured
// images with the CPU: the capture system (0x80034BB0, 84-byte entries at
// *(0x8056C920)+4, image buffer at *(*(entry+72))) copies screen rectangles and
// the snapshot routine 0x800765D8 memcpys a capture into its own buffer
// (0x800766C8) for the star door and the flip card. Guest RAM is never written.
// Instead, read the game's snapshot records (0x8056C9D8: count, then 352-byte
// records of three 68-byte slots: +16 capture id, +60 buffer handle, +64 mode)
// and resolve a texture load of a snapshot buffer to a GPU copy of the capture's
// host texture, frozen at the first load so later copies to the capture buffer
// do not change the snapshot. Read-only, exact, O(1) for ordinary textures.
// (Revision 1 stamped a tag into RAM and corrupted a wedding TPL; revision 2
// fingerprinted RAM, which cost a scan per bind and missed zeroed buffers.)
u32 m14_tex_buffer_size(u32 format, u32 width, u32 height) noexcept {
  u32 tw = 4, th = 4, bpp = 16;
  switch (format) {
  case GX_TF_I4: case GX_TF_C4: case GX_TF_CMPR: tw = 8; th = 8; bpp = 4; break;
  case GX_TF_I8: case GX_TF_IA4: case GX_TF_C8: tw = 8; th = 4; bpp = 8; break;
  case GX_TF_RGBA8: bpp = 32; break;
  default: break;
  }
  const uint64_t w = (uint64_t(width) + tw - 1u) / tw * tw, h = (uint64_t(height) + th - 1u) / th * th;
  return static_cast<u32>(std::min<uint64_t>(w * h * bpp / 8u, 0xFFFFFFFFu));
}
constexpr u32 kM14SnapshotOwners = 0x8056C9D8u; // r13-32552
constexpr u32 kM14CaptureTable = 0x8056C920u;   // r13-32736
constexpr u32 kM14OwnerSize = 352u, kM14SlotSize = 68u, kM14CaptureSize = 84u;
struct M14CopyVersion { // an immutable GPU copy of one texture copy, by sync sequence
  uint64_t seq = 0;
  gfx::TextureHandle tex;
  u32 width = 0, height = 0;
};
struct M14GuestCopy { // the texture copies into a guest buffer
  const void* dest = nullptr;
  u32 format = 0, width = 0, height = 0;
  std::deque<M14CopyVersion> versions;
};
struct M14Snapshot {
  u32 buffer = 0, handle = 0, source = 0;
  int32_t capture = -1;
  const void* host = nullptr;
  gfx::TextureHandle frozen;
  u32 frozenWidth = 0, frozenHeight = 0;
  bool live = false;
  gfx::TextureHandle version; // the capture as the game's memcpy saw it
  u32 versionWidth = 0, versionHeight = 0;
};
struct M14SnapshotStats {
  uint64_t copies = 0, scans = 0, found = 0, frozen = 0, aliased = 0, reused = 0, missing = 0, crop = 0;
};
M14SnapshotStats sM14SnapshotStats;
absl::flat_hash_map<u32, M14GuestCopy> sM14GuestCopies;          // guest buffer -> copy
absl::flat_hash_map<uint64_t, uint32_t> sM14CopyShapes;          // (format, bytes) -> seen
absl::flat_hash_map<u32, M14Snapshot> sM14Snapshots;             // slot address -> snapshot
absl::flat_hash_map<const void*, u32> sM14SnapshotByHost;        // buffer host pointer -> slot
uint32_t sM14MissScans = 0;
// FIFO sync sequence: bumped after every renderer sync (DrawDone, present, bbox
// read); versions record it for diagnostics. Captures of many sizes reuse one
// freed buffer, so each copy keeps an immutable version and a snapshot takes the
// newest version of its own size at its first load (see m14_resolve_tagged_copy).
uint64_t sM14SyncSeq = 1, sM14LastCopySeq = 0;
std::deque<u32> sM14VersionOrder; // oldest first, global cap
uint64_t sM14VersionPixels = 0;
std::vector<std::pair<gfx::TextureHandle, std::string>> sM14SnapshotDumps;
// OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1 (launcher: snapcapture): arm the GX capture
// mid-frame at the first sync where SPM's capture table holds a requested RGBA8
// capture (the flip's marioCharOfs, requested by mario_motion at flip start), so
// that frame's Mario draws, the bbox reads building its rect and the copy are
// all logged. The renderer polls m14_take_capture_arm_request() after syncs.
bool sM14SnapshotCapturePending = false;
bool sM14CaptureArmRequest = false;
bool m14_capture_on_snapshot() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_CAPTURE_ON_SNAPSHOT");
    return value != nullptr && value[0] == '1';
  }();
  return enabled;
}
bool m14_snapshot_dump_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_SNAPSHOT_DUMP");
    return value != nullptr && value[0] == '1';
  }();
  return enabled;
}
struct M14FreezeCopy {
  gfx::TextureHandle src, dst;
};
std::mutex sM14FreezeMutex;
std::array<M14FreezeCopy, 256> sM14FreezeRing;
uint32_t sM14FreezeNext = 0;
gfx::EncoderTaskId sM14FreezeTask = gfx::InvalidEncoderTask;

bool m14_copy_tags_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_COPY_TAGS");
    const bool on = value == nullptr || value[0] != '0';
    M14EfbRamLog.info("M14 EFB copy tracking {} (OPENSPM_M14_COPY_TAGS={}): SPM snapshot buffers resolve to frozen "
                      "host copies", on ? "active" : "off", value != nullptr ? value : "default");
    return on;
  }();
  return enabled;
}

bool m14_guest_u32(u32 address, u32& out) noexcept {
  guest_memory::Span span;
  if (!guest_memory::map(address, 4u, &span) || span.data == nullptr || span.size < 4u) return false;
  out = (u32(span.data[0]) << 24u) | (u32(span.data[1]) << 16u) | (u32(span.data[2]) << 8u) | u32(span.data[3]);
  return true;
}
const void* m14_guest_host(u32 address, u32 size) noexcept {
  guest_memory::Span span;
  if (address == 0u || !guest_memory::map(address, size, &span) || span.size < size) return nullptr;
  return span.data;
}

void m14_copy_tag_freeze_task(const gfx::EncoderTaskContext&, const wgpu::CommandEncoder& cmd, const void* payload,
                              size_t payloadSize, void*) {
  if (payload == nullptr || payloadSize != sizeof(uint32_t)) return;
  uint32_t slot = 0;
  std::memcpy(&slot, payload, sizeof(slot));
  M14FreezeCopy copy;
  {
    std::lock_guard lock{sM14FreezeMutex};
    copy = sM14FreezeRing[slot % sM14FreezeRing.size()];
  }
  if (!copy.src || !copy.dst || !copy.src->texture || !copy.dst->texture) return;
  const wgpu::TexelCopyTextureInfo src{.texture = copy.src->texture};
  const wgpu::TexelCopyTextureInfo dst{.texture = copy.dst->texture};
  const wgpu::Extent3D extent{.width = std::min(copy.src->size.width, copy.dst->size.width),
                              .height = std::min(copy.src->size.height, copy.dst->size.height),
                              .depthOrArrayLayers = 1};
  cmd.CopyTextureToTexture(&src, &dst, &extent);
}

// An in-stream GPU copy of the top-left hostW x hostH of src ({} if unavailable).
gfx::TextureHandle m14_freeze_texture(const gfx::TextureHandle& src, u32 hostW, u32 hostH) noexcept {
  if (!src || !src->texture || !(src->texture.GetUsage() & wgpu::TextureUsage::CopySrc)) return {};
  if (sM14FreezeTask == gfx::InvalidEncoderTask)
    sM14FreezeTask = gfx::register_encoder_task_type({.label = "M14 EFB snapshot freeze",
                                                      .callback = m14_copy_tag_freeze_task});
  auto dst = gfx::new_render_texture(std::max<u32>(1u, std::min(hostW, src->size.width)),
                                     std::max<u32>(1u, std::min(hostH, src->size.height)), src->gxFormat,
                                     "M14 Snapshot Copy");
  if (!dst || dst->format != src->format || sM14FreezeTask == gfx::InvalidEncoderTask) return {};
  uint32_t ring = 0;
  {
    std::lock_guard lock{sM14FreezeMutex};
    ring = sM14FreezeNext++ % static_cast<uint32_t>(sM14FreezeRing.size());
    sM14FreezeRing[ring] = M14FreezeCopy{src, dst};
  }
  if (!gfx::push_encoder_task_from_fifo(sM14FreezeTask, &ring, sizeof(ring))) return {};
  return dst;
}

void m14_copy_tag_note(u32 guestAddress, const void* dest, u32 format) noexcept {
  if (!m14_copy_tags_enabled() || dest == nullptr || guestAddress == 0u) return;
  const u32 w = g_gxState.texCopyDstWidth, h = g_gxState.texCopyDstHeight;
  if (sM14GuestCopies.size() > 1024u) {
    sM14GuestCopies.clear();
    sM14VersionOrder.clear();
    sM14VersionPixels = 0;
  }
  // Keyed by physical address: the copy register holds one (0x120856C0), SPM's
  // capture entries hold the cached virtual alias (0x920856C0).
  const u32 key = guestAddress & 0x1FFFFFFFu;
  auto& copy = sM14GuestCopies[key];
  copy.dest = dest;
  copy.format = format;
  copy.width = w;
  copy.height = h;
  sM14LastCopySeq = sM14SyncSeq;
  // Keep an immutable version: the capture buffer is reused by later copies.
  const auto it = g_gxState.copyTextures.find(dest);
  if (it != g_gxState.copyTextures.end() && it->second.handle && uint64_t(w) * h <= 640ull * 528ull) {
    auto& src = it->second.handle;
    if (auto version = m14_freeze_texture(src, src->size.width, src->size.height)) {
      sM14VersionPixels += uint64_t(version->size.width) * version->size.height;
      copy.versions.push_back(M14CopyVersion{sM14SyncSeq, std::move(version), w, h});
      sM14VersionOrder.push_back(key);
      // Bounded by host pixels (~96 MB): captures are rare outside the flip and doors.
      while (sM14VersionOrder.size() > 256u || (sM14VersionPixels > (24ull << 20) && sM14VersionOrder.size() > 1u)) {
        const auto old = sM14GuestCopies.find(sM14VersionOrder.front());
        if (old != sM14GuestCopies.end() && !old->second.versions.empty()) {
          const auto& tex = old->second.versions.front().tex;
          if (tex) sM14VersionPixels -= std::min<uint64_t>(sM14VersionPixels, uint64_t(tex->size.width) * tex->size.height);
          old->second.versions.pop_front();
        }
        sM14VersionOrder.pop_front();
      }
    }
  }
  if (sM14CopyShapes.size() > 256u) sM14CopyShapes.clear();
  sM14CopyShapes[(uint64_t(format) << 32u) | m14_tex_buffer_size(format, w, h)] = 1u;
  ++sM14SnapshotStats.copies;
}

// Read SPM's snapshot slots (mode 1/2 = CPU copy) into sM14Snapshots. A slot
// whose buffer, handle or capture changed is a new snapshot and drops its freeze.
void m14_snapshot_scan() noexcept {
  ++sM14SnapshotStats.scans;
  for (auto& [_, snap] : sM14Snapshots) snap.live = false;
  u32 owners = 0, count = 0, records = 0, captures = 0, captureBase = 0;
  const bool ok = m14_guest_u32(kM14SnapshotOwners, owners) && owners != 0u && m14_guest_u32(owners, count) &&
                  m14_guest_u32(owners + 4u, records) && records != 0u && count <= 64u &&
                  m14_guest_u32(kM14CaptureTable, captures) && captures != 0u &&
                  m14_guest_u32(captures + 4u, captureBase) && captureBase != 0u;
  // What the scan sees, logged when it changes: which check rejects the slots.
  u32 activeOwners = 0, withCapture = 0, direct = 0, cpuCopy = 0, noHandle = 0, badCapture = 0, flagMask = 0;
  for (u32 i = 0; ok && i < count; ++i) {
    const u32 owner = records + i * kM14OwnerSize;
    u32 ownerFlags = 0;
    if (!m14_guest_u32(owner + 204u, ownerFlags)) continue;
    if (ownerFlags & 1u) ++activeOwners;
    for (u32 k = 0; k < 3u; ++k) {
      const u32 slot = owner + k * kM14SlotSize;
      u32 flags = 0, capture = 0, handle = 0, mode = 0, buffer = 0, entryFlags = 0, captureHandle = 0, source = 0;
      if (!m14_guest_u32(slot, flags) || !m14_guest_u32(slot + 16u, capture) || capture == 0xFFFFFFFFu ||
          capture > 4096u || !m14_guest_u32(slot + 64u, mode))
        continue;
      ++withCapture;
      flagMask |= flags;
      if (mode != 1u && mode != 2u) {
        ++direct; // mode 0 samples the capture buffer itself: an ordinary copy hit
        continue;
      }
      ++cpuCopy;
      if (!m14_guest_u32(slot + 60u, handle) || handle == 0u || !m14_guest_u32(handle, buffer) || buffer == 0u) {
        ++noHandle;
        continue;
      }
      const u32 entry = captureBase + capture * kM14CaptureSize;
      if (!m14_guest_u32(entry, entryFlags) || !m14_guest_u32(entry + 72u, captureHandle) || captureHandle == 0u ||
          !m14_guest_u32(captureHandle, source) || source == 0u) {
        ++badCapture;
        continue;
      }
      auto& snap = sM14Snapshots[slot];
      if (snap.buffer != buffer || snap.handle != handle || snap.capture != int32_t(capture) || snap.source != source) {
        snap = M14Snapshot{buffer, handle, source, int32_t(capture), m14_guest_host(buffer, 32u)};
        // Newest version parsed up to this sync (b55ffc4 rule: fixed the star-door chunk).
        if (const auto copy = sM14GuestCopies.find(source & 0x1FFFFFFFu);
            copy != sM14GuestCopies.end() && !copy->second.versions.empty()) {
          const auto& v = copy->second.versions.back();
          snap.version = v.tex;
          snap.versionWidth = v.width;
          snap.versionHeight = v.height;
        }
        if (m14_efb_ram_should_log(++sM14SnapshotStats.found))
          M14EfbRamLog.info("M14 snapshot slot=0x{:08X} flags=0x{:X} buffer=0x{:08X} capture={} capflags=0x{:X} "
                            "source=0x{:08X} mode={} tracked_copy={} seq={} found_total={}",
                            slot, flags, buffer, capture, entryFlags, source, mode,
                            sM14GuestCopies.contains(source & 0x1FFFFFFFu) ? "yes" : "no", sM14SyncSeq,
                            sM14SnapshotStats.found);
      }
      snap.live = true;
    }
  }
  static uint64_t lastDiag = ~0ull;
  static uint32_t diagLines = 0;
  const uint64_t diag = (uint64_t(ok) << 63u) ^ (uint64_t(count) << 48u) ^ (uint64_t(activeOwners) << 40u) ^
                        (uint64_t(withCapture) << 32u) ^ (uint64_t(direct) << 24u) ^ (uint64_t(cpuCopy) << 16u) ^
                        (uint64_t(noHandle) << 8u) ^ badCapture;
  if (diag != lastDiag && diagLines < 64u) {
    lastDiag = diag;
    ++diagLines;
    M14EfbRamLog.info("M14 snapshot scan ok={} owners=0x{:08X} count={} records=0x{:08X} captures=0x{:08X} "
                      "active_owners={} slots_with_capture={} direct={} cpu_copy={} no_handle={} bad_capture={} "
                      "slot_flags=0x{:X} copies={}",
                      ok, owners, count, records, captureBase, activeOwners, withCapture, direct, cpuCopy, noHandle,
                      badCapture, flagMask, sM14SnapshotStats.copies);
  }
  sM14SnapshotByHost.clear();
  for (auto it = sM14Snapshots.begin(); it != sM14Snapshots.end();) {
    if (!it->second.live || it->second.host == nullptr) {
      sM14Snapshots.erase(it++);
      continue;
    }
    sM14SnapshotByHost[it->second.host] = it->first;
    ++it;
  }
}
} // namespace

gfx::TextureHandle m14_resolve_tagged_copy(const void* data, u32 format, u32 width, u32 height) noexcept {
  if (data == nullptr || !m14_copy_tags_enabled()) return {};
  auto slot = sM14SnapshotByHost.find(data);
  if (slot == sM14SnapshotByHost.end()) {
    // A snapshot taken since the last sync scan: only loads shaped like a
    // texture copy rescan, a few times per sync at most.
    if (sM14MissScans >= 4u ||
        !sM14CopyShapes.contains((uint64_t(format) << 32u) | m14_tex_buffer_size(format, width, height)))
      return {};
    ++sM14MissScans;
    m14_snapshot_scan();
    slot = sM14SnapshotByHost.find(data);
    if (slot == sM14SnapshotByHost.end()) {
      const auto* base = static_cast<const uint8_t*>(m14_guest_host(0x80000000u, 4u));
      const auto* at = static_cast<const uint8_t*>(data);
      const u32 guest = base != nullptr && at >= base && at < base + 0x01800000 ? 0x80000000u + u32(at - base) : 0u;
      static uint64_t sUntracked = 0;
      if (m14_efb_ram_should_log(++sUntracked))
        M14EfbRamLog.info("M14 snapshot untracked copy-shaped load guest=0x{:08X} {}x{} fmt={} n={}", guest, width,
                          height, format, sUntracked);
      return {};
    }
  }
  const auto snapIt = sM14Snapshots.find(slot->second);
  if (snapIt == sM14Snapshots.end()) return {};
  auto& snap = snapIt->second;
  if (snap.frozen && snap.frozenWidth == width && snap.frozenHeight == height) {
    ++sM14SnapshotStats.reused;
    return snap.frozen;
  }
  // The version the game's memcpy saw. SPM's snapshot routine (0x800765D8) runs
  // in the draw phase just before the snapshot is drawn, so at its first load
  // the newest copy of the load's size parsed so far is the one it copied.
  // (The capture entry's rectangle is already cleared by then.)
  const auto near = [](u32 a, u32 b) { return (a > b ? a - b : b - a) <= 4u; };
  if (!snap.frozen && (!snap.version || !near(snap.versionWidth, width) || !near(snap.versionHeight, height))) {
    // No version of the load's size at the scan: the newest one parsed so far.
    snap.version = {};
    if (const auto copy = sM14GuestCopies.find(snap.source & 0x1FFFFFFFu); copy != sM14GuestCopies.end())
      for (auto v = copy->second.versions.rbegin(); v != copy->second.versions.rend(); ++v)
        if (near(v->width, width) && near(v->height, height)) {
          snap.version = v->tex;
          snap.versionWidth = v->width;
          snap.versionHeight = v->height;
          break;
        }
  }
  gfx::TextureHandle src = snap.version;
  u32 copyW = snap.versionWidth, copyH = snap.versionHeight;
  const bool versioned = static_cast<bool>(src);
  if (!versioned) {
    const auto copy = sM14GuestCopies.find(snap.source & 0x1FFFFFFFu);
    const auto tex = copy != sM14GuestCopies.end() ? g_gxState.copyTextures.find(copy->second.dest)
                                                   : g_gxState.copyTextures.end();
    if (tex == g_gxState.copyTextures.end() || !tex->second.handle) {
      if (m14_efb_ram_should_log(++sM14SnapshotStats.missing))
        M14EfbRamLog.info("M14 snapshot buffer=0x{:08X} capture={} source=0x{:08X}: no host copy (missing_total={})",
                          snap.buffer, snap.capture, snap.source, sM14SnapshotStats.missing);
      return {};
    }
    src = tex->second.handle;
    copyW = copy->second.width;
    copyH = copy->second.height;
    if (copyW + 4u < width || copyH + 4u < height) {
      // The live copy is another capture (smaller): stretching it shows garbage.
      if (m14_efb_ram_should_log(++sM14SnapshotStats.missing))
        M14EfbRamLog.info("M14 snapshot buffer=0x{:08X} capture={}: live copy {}x{} is not the {}x{} capture "
                          "(missing_total={})", snap.buffer, snap.capture, copyW, copyH, width, height,
                          sM14SnapshotStats.missing);
      return {};
    }
  }
  const bool exact = copyW == width && copyH == height;
  gfx::TextureHandle handle;
  bool frozen = false;
  if (versioned && exact) {
    handle = src; // already an immutable copy
  } else {
    // Host size of the sampled part (the whole texture for an exact shape). A
    // live copy is frozen here since later copies to the buffer reuse it.
    const u32 hostW = exact || copyW == 0u ? src->size.width
                      : std::max<u32>(1u, static_cast<u32>(std::lround(double(width) * src->size.width / copyW)));
    const u32 hostH = exact || copyH == 0u ? src->size.height
                      : std::max<u32>(1u, static_cast<u32>(std::lround(double(height) * src->size.height / copyH)));
    handle = m14_freeze_texture(src, hostW, hostH);
    frozen = static_cast<bool>(handle);
    if (!frozen && exact) handle = src;
  }
  if (!handle) {
    // A crop needs the frozen copy; an alias would stretch the whole capture.
    if (m14_efb_ram_should_log(++sM14SnapshotStats.crop))
      M14EfbRamLog.info("M14 snapshot crop unavailable buffer=0x{:08X} copy={}x{} load={}x{}", snap.buffer, copyW,
                        copyH, width, height);
    return {};
  }
  if (m14_snapshot_dump_enabled()) {
    static uint32_t sDumps = 0;
    if (sDumps < 48u) {
      char name[160];
      std::snprintf(name, sizeof name, "screenshots/snapshot-%03u-buf%08X-cap%d-load%ux%u-%s.png", sDumps, snap.buffer,
                    snap.capture, width, height, versioned ? "version" : "live");
      sM14SnapshotDumps.emplace_back(handle, name);
      // The capture buffer's current copy, for comparison.
      if (const auto copy = sM14GuestCopies.find(snap.source & 0x1FFFFFFFu); copy != sM14GuestCopies.end())
        if (const auto live = g_gxState.copyTextures.find(copy->second.dest);
            live != g_gxState.copyTextures.end() && live->second.handle) {
          std::snprintf(name, sizeof name, "screenshots/snapshot-%03u-buf%08X-livecopy-%ux%u.png", sDumps,
                        snap.buffer, copy->second.width, copy->second.height);
          sM14SnapshotDumps.emplace_back(live->second.handle, name);
        }
      ++sDumps;
    }
  }
  if (frozen) ++sM14SnapshotStats.frozen;
  else ++sM14SnapshotStats.aliased;
  snap.frozen = handle;
  snap.frozenWidth = width;
  snap.frozenHeight = height;
  const uint64_t n = sM14SnapshotStats.frozen + sM14SnapshotStats.aliased;
  if (m14_efb_ram_should_log(n))
    M14EfbRamLog.info("M14 snapshot resolved buffer=0x{:08X} capture={} load={}x{}/fmt={} copy={}x{} {} copies={} "
                      "scans={} frozen={} aliased={} reused={} missing={}",
                      snap.buffer, snap.capture, width, height, format, copyW, copyH,
                      versioned ? "versioned" : frozen ? "frozen" : "aliased", sM14SnapshotStats.copies, sM14SnapshotStats.scans,
                      sM14SnapshotStats.frozen, sM14SnapshotStats.aliased, sM14SnapshotStats.reused,
                      sM14SnapshotStats.missing);
  return handle;
}

namespace raw {
// After every FIFO sync (parser drained, game waiting): refresh the snapshot
// slots while copies are recent, then advance the sync sequence.
bool m14_take_capture_arm_request() noexcept {
  const bool pending = sM14CaptureArmRequest;
  sM14CaptureArmRequest = false;
  return pending;
}

void m14_snapshot_sync() noexcept {
  sM14MissScans = 0;
  if (m14_capture_on_snapshot()) {
    static bool sArmed = false;
    u32 captures = 0, count = 0, base = 0;
    if (!sArmed && m14_guest_u32(kM14CaptureTable, captures) && captures != 0u && m14_guest_u32(captures, count) &&
        m14_guest_u32(captures + 4u, base) && base != 0u && count <= 64u) {
      for (u32 i = 0; i < count && !sArmed; ++i) {
        u32 flags = 0, fmt = 0, group = 0;
        const u32 entry = base + i * kM14CaptureSize;
        if (m14_guest_u32(entry, flags) && (flags & 1u) != 0u && m14_guest_u32(entry + 64u, fmt) &&
            fmt == GX_TF_RGBA8 && m14_guest_u32(entry + 20u, group)) {
          sArmed = true;
          sM14CaptureArmRequest = true;
          M14EfbRamLog.info("M14 capture-request GX capture armed: capture {} fmt=RGBA8 group={} flags=0x{:X}", i,
                            group, flags);
        }
      }
    }
  }
  if (m14_copy_tags_enabled() && (!sM14Snapshots.empty() || sM14SyncSeq - sM14LastCopySeq < 2048u))
    m14_snapshot_scan();
  ++sM14SyncSeq;
}
} // namespace raw


namespace {
// OPENSPM_M14_COPY_CAPTURE=WxH: request the renderer's F8 GX capture after a
// frame that copies a texture of that logical size (72x88 = Mario's flip card).
bool sM14CopyCapturePending = false;
void m14_copy_capture_note() noexcept {
  static const std::pair<u32, u32> wanted = [] {
    const char* value = std::getenv("OPENSPM_M14_COPY_CAPTURE");
    unsigned w = 0, h = 0;
    if (value != nullptr && std::sscanf(value, "%ux%u", &w, &h) == 2 && w != 0u && h != 0u) {
      M14EfbRamLog.info("M14 copy-triggered GX capture watching for {}x{} texture copies", w, h);
      return std::pair<u32, u32>{w, h};
    }
    return std::pair<u32, u32>{0u, 0u};
  }();
  if (wanted.first != 0u && g_gxState.texCopyDstWidth == wanted.first && g_gxState.texCopyDstHeight == wanted.second)
    sM14CopyCapturePending = true;
}
} // namespace

void m14_efb_ram_note_copy(u32 guestAddress, const void* dest, u32 format) noexcept {
  if (dest == nullptr) return;
  m14_copy_capture_note();
  m14_copy_tag_note(guestAddress, dest, format);
  if (!m14_efb_ram_enabled()) return;
  if (format != GX_TF_RGBA8 && format != GX_TF_RGB5A3) {
    if (m14_efb_ram_should_log(++sM14EfbRamStats.skippedFormat))
      M14EfbRamLog.info("M14 EFB copy RAM write-back skipped fmt={} dest=0x{:08X} (unsupported format) skipped_total={}",
                        format, guestAddress, sM14EfbRamStats.skippedFormat);
    return;
  }
  const u32 w = g_gxState.texCopyDstWidth, h = g_gxState.texCopyDstHeight;
  if (w == 0u || h == 0u || uint64_t(w) * h > m14_efb_ram_max_pixels()) {
    ++sM14EfbRamStats.skippedSize;
    return;
  }
  const auto it = g_gxState.copyTextures.find(dest);
  if (it == g_gxState.copyTextures.end() || !it->second.handle) return;
  if (sM14EfbRamPending.size() >= 256u) {
    ++sM14EfbRamStats.failed;
    return;
  }
  sM14EfbRamPending.push_back(M14EfbRamPending{guestAddress, format, w, h, it->second.handle});
  ++sM14EfbRamStats.queued;
}

namespace {
// Minimal PNG writer (RGB8, stored deflate blocks): no dependency, ~4.7 MB at
// 1440x1080, opens everywhere.
uint32_t m14_png_crc(const uint8_t* data, size_t size, uint32_t crc = 0xFFFFFFFFu) noexcept {
  static const std::array<uint32_t, 256> table = [] {
    std::array<uint32_t, 256> t{};
    for (uint32_t n = 0; n < 256u; ++n) {
      uint32_t c = n;
      for (int k = 0; k < 8; ++k) c = (c & 1u) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      t[n] = c;
    }
    return t;
  }();
  for (size_t i = 0; i < size; ++i) crc = table[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
  return crc;
}
void m14_png_u32(std::vector<uint8_t>& out, uint32_t v) {
  out.push_back(uint8_t(v >> 24)); out.push_back(uint8_t(v >> 16)); out.push_back(uint8_t(v >> 8)); out.push_back(uint8_t(v));
}
void m14_png_chunk(std::vector<uint8_t>& out, const char* type, const std::vector<uint8_t>& data) {
  m14_png_u32(out, static_cast<uint32_t>(data.size()));
  const size_t start = out.size();
  out.insert(out.end(), type, type + 4);
  out.insert(out.end(), data.begin(), data.end());
  m14_png_u32(out, m14_png_crc(out.data() + start, out.size() - start) ^ 0xFFFFFFFFu);
}
std::vector<uint8_t> m14_encode_png_rgb(const std::vector<uint8_t>& rows, uint32_t width, uint32_t height) {
  // rows: height * (1 + width*3), each row prefixed with filter byte 0.
  std::vector<uint8_t> z{0x78u, 0x01u};
  uint32_t a = 1, b = 0;
  for (size_t off = 0; off < rows.size() || rows.empty();) {
    const size_t n = std::min<size_t>(65535u, rows.size() - off);
    const bool last = off + n == rows.size();
    z.push_back(last ? 1u : 0u);
    z.push_back(uint8_t(n)); z.push_back(uint8_t(n >> 8));
    z.push_back(uint8_t(~n)); z.push_back(uint8_t((~n) >> 8));
    z.insert(z.end(), rows.begin() + static_cast<std::ptrdiff_t>(off), rows.begin() + static_cast<std::ptrdiff_t>(off + n));
    for (size_t i = off; i < off + n; ++i) { a = (a + rows[i]) % 65521u; b = (b + a) % 65521u; }
    off += n;
    if (last) break;
  }
  m14_png_u32(z, (b << 16) | a);
  std::vector<uint8_t> png{0x89u, 'P', 'N', 'G', 0x0Du, 0x0Au, 0x1Au, 0x0Au};
  std::vector<uint8_t> ihdr;
  m14_png_u32(ihdr, width); m14_png_u32(ihdr, height);
  ihdr.insert(ihdr.end(), {8u, 2u, 0u, 0u, 0u}); // 8-bit RGB
  m14_png_chunk(png, "IHDR", ihdr);
  m14_png_chunk(png, "IDAT", z);
  m14_png_chunk(png, "IEND", {});
  return png;
}
} // namespace

namespace raw {
// OpenSPM F12 screenshot: the presented display copy (the game image without
// letterbox bars) at EFB resolution. Call after the frame is submitted.
bool m14_save_texture_png(const gfx::TextureHandle& tex, const char* path) noexcept;
bool m14_save_screenshot(const char* path) noexcept {
  const void* dest = display_copy_destination();
  if (dest == nullptr || path == nullptr) return false;
  const auto it = g_gxState.copyTextures.find(dest);
  if (it == g_gxState.copyTextures.end() || !it->second.handle || !it->second.handle->texture) return false;
  return m14_save_texture_png(it->second.handle, path);
}

// Read back a host texture (RGBA8/BGRA8) and write it as a PNG. Call outside the
// FIFO parser after the frame that wrote the texture was submitted.
bool m14_save_texture_png(const gfx::TextureHandle& tex, const char* path) noexcept {
  if (!tex || !tex->texture || path == nullptr) return false;
  const auto format = tex->format;
  const bool bgra = format == wgpu::TextureFormat::BGRA8Unorm || format == wgpu::TextureFormat::BGRA8UnormSrgb;
  const bool rgba = format == wgpu::TextureFormat::RGBA8Unorm || format == wgpu::TextureFormat::RGBA8UnormSrgb;
  if ((!bgra && !rgba) || !(tex->texture.GetUsage() & wgpu::TextureUsage::CopySrc)) return false;
  const uint32_t width = tex->size.width, height = tex->size.height;
  const uint32_t bytesPerRow = (width * 4u + 255u) & ~255u;
  std::vector<uint8_t> pixels;
  const auto work = [&] {
    const wgpu::BufferDescriptor bufferDesc{.label = "M14 screenshot staging",
                                            .usage = wgpu::BufferUsage::MapRead | wgpu::BufferUsage::CopyDst,
                                            .size = uint64_t(bytesPerRow) * height};
    auto buffer = webgpu::g_device.CreateBuffer(&bufferDesc);
    const wgpu::CommandEncoderDescriptor encoderDesc{.label = "M14 screenshot"};
    auto encoder = webgpu::g_device.CreateCommandEncoder(&encoderDesc);
    const wgpu::TexelCopyTextureInfo src{.texture = tex->texture};
    const wgpu::TexelCopyBufferInfo dst{
        .layout = wgpu::TexelCopyBufferLayout{.offset = 0, .bytesPerRow = bytesPerRow, .rowsPerImage = height},
        .buffer = buffer};
    const wgpu::Extent3D extent{.width = width, .height = height, .depthOrArrayLayers = 1};
    encoder.CopyTextureToBuffer(&src, &dst, &extent);
    const auto command = encoder.Finish();
    webgpu::g_queue.Submit(1, &command);
    auto status = std::make_shared<wgpu::MapAsyncStatus>(wgpu::MapAsyncStatus::Error);
    const auto future = buffer.MapAsync(wgpu::MapMode::Read, 0, bufferDesc.size, wgpu::CallbackMode::WaitAnyOnly,
                                        [status](wgpu::MapAsyncStatus s, wgpu::StringView) { *status = s; });
    if (webgpu::g_instance.WaitAny(future, 2'000'000'000) != wgpu::WaitStatus::Success ||
        *status != wgpu::MapAsyncStatus::Success)
      return;
    const auto* mapped = static_cast<const uint8_t*>(buffer.GetConstMappedRange(0, bufferDesc.size));
    if (mapped != nullptr) pixels.assign(mapped, mapped + bufferDesc.size);
    buffer.Unmap();
  };
  if (gfx::render_worker::is_worker_thread()) {
    work();
  } else {
    gfx::render_worker::enqueue_work(work);
    gfx::render_worker::synchronize();
  }
  if (pixels.empty()) return false;
  std::vector<uint8_t> rows(size_t(height) * (1u + size_t(width) * 3u));
  for (uint32_t y = 0; y < height; ++y) {
    uint8_t* row = rows.data() + size_t(y) * (1u + size_t(width) * 3u);
    row[0] = 0u;
    const uint8_t* srcRow = pixels.data() + size_t(y) * bytesPerRow;
    for (uint32_t x = 0; x < width; ++x) {
      const uint8_t* px = srcRow + size_t(x) * 4u;
      row[1u + x * 3u] = bgra ? px[2] : px[0];
      row[2u + x * 3u] = px[1];
      row[3u + x * 3u] = bgra ? px[0] : px[2];
    }
  }
  const auto png = m14_encode_png_rgb(rows, width, height);
  FILE* file = std::fopen(path, "wb");
  if (file == nullptr) return false;
  const bool ok = std::fwrite(png.data(), 1, png.size(), file) == png.size();
  return std::fclose(file) == 0 && ok;
}

// OPENSPM_M14_SNAPSHOT_DUMP=1: write each newly resolved snapshot image (and the
// live copy of its capture buffer) to screenshots/ after the frame is presented.
void m14_flush_snapshot_dumps() noexcept {
  if (sM14SnapshotDumps.empty()) return;
  auto dumps = std::move(sM14SnapshotDumps);
  sM14SnapshotDumps.clear();
  for (const auto& [tex, path] : dumps) {
    const bool saved = m14_save_texture_png(tex, path.c_str());
    M14EfbRamLog.info("M14 snapshot dump {} {} ({}x{})", saved ? "saved" : "failed", path,
                      tex ? tex->size.width : 0u, tex ? tex->size.height : 0u);
  }
}

// OpenSPM M14 bbox memo (opt-in, OPENSPM_M14_BBOX_MEMO=1). A CPU bbox read has to
// wait for the GPU (~0.25-3 ms). The star door re-measures the same door halves
// every frame, 4 reads per frame. Key = every bbox register write since the
// last full reset plus every draw since (vertices, indices, matrices,
// projection, viewport/scissor, pipeline, textures): identical inputs give an
// identical box, so a hit returns the remembered box without a GPU sync. Keys
// with depth-tested draws or live EFB-copy textures are never memoized.
namespace {
uint64_t sM14BboxKey = 0;
bool sM14BboxKeyValid = false;
u32 sM14BboxWritten = 0;
absl::flat_hash_map<uint64_t, std::array<uint16_t, 4>> sM14BboxMemo;
struct M14BboxMemoStats { uint64_t hits = 0, misses = 0, unsafe = 0; } sM14BboxMemoStats;
uint64_t m14_bbox_mix(uint64_t key, uint64_t v) noexcept {
  key ^= v + 0x9E3779B97F4A7C15ull + (key << 6) + (key >> 2);
  return key * 0xFF51AFD7ED558CCDull;
}
} // namespace
bool m14_bbox_memo_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_BBOX_MEMO");
    const bool on = value != nullptr && value[0] == '1';
    if (on) M14EfbRamLog.info("M14 bbox memo active (OPENSPM_M14_BBOX_MEMO=1): identical bbox work skips the GPU sync");
    return on;
  }();
  return enabled;
}
void m14_bbox_memo_write(std::uint32_t index, std::uint16_t value) noexcept {
  if (!m14_bbox_memo_enabled()) return;
  if (index == 0u) {
    sM14BboxKey = 0x243F6A8885A308D3ull;
    sM14BboxWritten = 0u;
  }
  sM14BboxKey = m14_bbox_mix(sM14BboxKey, (uint64_t(index) << 32) | value);
  sM14BboxWritten |= 1u << (index & 3u);
  sM14BboxKeyValid = sM14BboxWritten == 0xFu;
}
// Hash draws only while a memo key is being built (bbox written, still memoizable):
// the hash reads every draw's matrices and vertex data (~2x DrawDone cost otherwise).
bool m14_bbox_memo_wants_draw() noexcept { return m14_bbox_memo_enabled() && sM14BboxKeyValid; }
// The GX capture's draw ordinal (command_processor.cpp), for the renderer's bbox trace.
std::uint64_t g_m14_audit_draw_ordinal = 0;
void m14_bbox_memo_draw(std::uint64_t drawHash, bool memoizable) noexcept {
  if (!m14_bbox_memo_enabled()) return;
  sM14BboxKey = m14_bbox_mix(sM14BboxKey, drawHash);
  if (!memoizable) sM14BboxKeyValid = false;
}
bool m14_bbox_memo_lookup(std::array<std::uint16_t, 4>& out) noexcept {
  if (!m14_bbox_memo_enabled()) return false;
  if (!sM14BboxKeyValid) {
    ++sM14BboxMemoStats.unsafe;
    return false;
  }
  const auto it = sM14BboxMemo.find(sM14BboxKey);
  if (it == sM14BboxMemo.end()) {
    ++sM14BboxMemoStats.misses;
    return false;
  }
  out = it->second;
  const uint64_t n = ++sM14BboxMemoStats.hits;
  if (m14_efb_ram_should_log(n))
    M14EfbRamLog.info("M14 bbox memo hits={} misses={} unsafe={} entries={}", n, sM14BboxMemoStats.misses,
                      sM14BboxMemoStats.unsafe, sM14BboxMemo.size());
  return true;
}
void m14_bbox_memo_store(const std::array<std::uint16_t, 4>& bounds) noexcept {
  if (!m14_bbox_memo_enabled() || !sM14BboxKeyValid) return;
  if (sM14BboxMemo.size() >= 4096u) sM14BboxMemo.clear();
  sM14BboxMemo[sM14BboxKey] = bounds;
}

bool m14_take_copy_capture_request() noexcept {
  const bool pending = sM14CopyCapturePending || sM14SnapshotCapturePending;
  sM14CopyCapturePending = false;
  sM14SnapshotCapturePending = false;
  return pending;
}
void note_efb_copy_writeback(std::uint32_t guestAddress, const void* dest, std::uint32_t format) noexcept {
  m14_efb_ram_note_copy(guestAddress, dest, format);
}
bool efb_copy_writeback_pending() noexcept { return !sM14EfbRamPending.empty(); }

bool flush_efb_copy_writeback() noexcept {
  if (sM14EfbRamPending.empty()) return true;
  const auto start = std::chrono::steady_clock::now();
  std::vector<M14EfbRamPending> items;
  items.swap(sM14EfbRamPending);

  struct Readback {
    wgpu::Buffer buffer;
    u32 width = 0, height = 0, bytesPerRow = 0;
    wgpu::TextureFormat format = wgpu::TextureFormat::Undefined;
    std::vector<uint8_t> pixels;
  };
  std::vector<Readback> readbacks(items.size());
  const auto work = [&items, &readbacks] {
    const wgpu::CommandEncoderDescriptor encoderDesc{.label = "M14 EFB copy RAM readback"};
    auto encoder = webgpu::g_device.CreateCommandEncoder(&encoderDesc);
    for (size_t i = 0; i < items.size(); ++i) {
      const auto& tex = items[i].handle;
      auto& rb = readbacks[i];
      if (!tex || !tex->texture) continue;
      if (tex->format != wgpu::TextureFormat::RGBA8Unorm && tex->format != wgpu::TextureFormat::BGRA8Unorm) continue;
      rb.width = tex->size.width;
      rb.height = tex->size.height;
      rb.format = tex->format;
      rb.bytesPerRow = (rb.width * 4u + 255u) & ~255u;
      const wgpu::BufferDescriptor bufferDesc{
          .label = "M14 EFB copy RAM staging",
          .usage = wgpu::BufferUsage::MapRead | wgpu::BufferUsage::CopyDst,
          .size = uint64_t(rb.bytesPerRow) * rb.height,
      };
      rb.buffer = webgpu::g_device.CreateBuffer(&bufferDesc);
      const wgpu::TexelCopyTextureInfo src{.texture = tex->texture};
      const wgpu::TexelCopyBufferInfo dst{
          .layout = wgpu::TexelCopyBufferLayout{.offset = 0, .bytesPerRow = rb.bytesPerRow, .rowsPerImage = rb.height},
          .buffer = rb.buffer,
      };
      const wgpu::Extent3D extent{.width = rb.width, .height = rb.height, .depthOrArrayLayers = 1};
      encoder.CopyTextureToBuffer(&src, &dst, &extent);
    }
    const wgpu::CommandBufferDescriptor commandDesc{.label = "M14 EFB copy RAM readback"};
    const auto command = encoder.Finish(&commandDesc);
    webgpu::g_queue.Submit(1, &command);
    for (auto& rb : readbacks) {
      if (!rb.buffer) continue;
      const uint64_t size = uint64_t(rb.bytesPerRow) * rb.height;
      auto status = std::make_shared<wgpu::MapAsyncStatus>(wgpu::MapAsyncStatus::Error);
      const auto future = rb.buffer.MapAsync(wgpu::MapMode::Read, 0, size, wgpu::CallbackMode::WaitAnyOnly,
                                             [status](wgpu::MapAsyncStatus s, wgpu::StringView) { *status = s; });
      if (webgpu::g_instance.WaitAny(future, 2'000'000'000) != wgpu::WaitStatus::Success ||
          *status != wgpu::MapAsyncStatus::Success)
        continue;
      const auto* mapped = static_cast<const uint8_t*>(rb.buffer.GetConstMappedRange(0, size));
      if (mapped != nullptr) rb.pixels.assign(mapped, mapped + size);
      rb.buffer.Unmap();
    }
  };
  if (gfx::render_worker::is_worker_thread()) {
    work();
  } else {
    gfx::render_worker::enqueue_work(work);
    gfx::render_worker::synchronize();
  }

  // Producer thread, guest paused: downsample, encode and publish to RAM.
  std::vector<uint8_t> logical, encoded;
  bool ok = true;
  for (size_t i = 0; i < items.size(); ++i) {
    const auto& item = items[i];
    const auto& rb = readbacks[i];
    if (rb.pixels.empty()) {
      ++sM14EfbRamStats.failed;
      ok = false;
      continue;
    }
    logical.assign(size_t(item.width) * item.height * 4u, 0u);
    const bool bgra = rb.format == wgpu::TextureFormat::BGRA8Unorm;
    for (u32 y = 0; y < item.height; ++y) {
      const u32 sy = std::min<u32>(rb.height - 1u, (y * 2u + 1u) * rb.height / (item.height * 2u));
      for (u32 x = 0; x < item.width; ++x) {
        const u32 sx = std::min<u32>(rb.width - 1u, (x * 2u + 1u) * rb.width / (item.width * 2u));
        const uint8_t* s = rb.pixels.data() + size_t(sy) * rb.bytesPerRow + size_t(sx) * 4u;
        uint8_t* d = logical.data() + (size_t(y) * item.width + x) * 4u;
        d[0] = bgra ? s[2] : s[0];
        d[1] = s[1];
        d[2] = bgra ? s[0] : s[2];
        d[3] = s[3];
      }
    }
    if (item.format == GX_TF_RGBA8) m14_encode_rgba8(logical, item.width, item.height, encoded);
    else m14_encode_rgb5a3(logical, item.width, item.height, encoded);
    guest_memory::Span span;
    if (!guest_memory::map(item.guest, static_cast<u32>(encoded.size()), &span) || span.size < encoded.size()) {
      ++sM14EfbRamStats.failed;
      ok = false;
      continue;
    }
    std::memcpy(const_cast<uint8_t*>(span.data), encoded.data(), encoded.size());
    ++sM14EfbRamStats.written;
    sM14EfbRamStats.bytes += encoded.size();
    if (m14_efb_ram_should_log(sM14EfbRamStats.written))
      M14EfbRamLog.info("M14 EFB copy RAM write-back dest=0x{:08X} fmt={} logical={}x{} scaled={}x{} bytes={} written_total={}",
                        item.guest, item.format, item.width, item.height, rb.width, rb.height, encoded.size(),
                        sM14EfbRamStats.written);
  }
  ++sM14EfbRamStats.flushes;
  sM14EfbRamStats.flushUs += static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now() - start).count());
  if (m14_efb_ram_should_log(sM14EfbRamStats.flushes))
    M14EfbRamLog.info("M14 EFB copy RAM write-back stats flushes={} queued={} written={} bytes={} failed={} "
                      "skipped_format={} skipped_size={} avg_flush_us={}",
                      sM14EfbRamStats.flushes, sM14EfbRamStats.queued, sM14EfbRamStats.written,
                      sM14EfbRamStats.bytes, sM14EfbRamStats.failed, sM14EfbRamStats.skippedFormat,
                      sM14EfbRamStats.skippedSize, sM14EfbRamStats.flushUs / sM14EfbRamStats.flushes);
  return ok;
}
} // namespace raw
} // namespace aurora::gx
'''


TEXTURE = Path("lib/gx/texture.cpp")
STATE = Path("lib/gx/gx.hpp")
TEX_OLD = ("    } else if (copyRef != nullptr) {\n      handle = copyRef->handle;\n"
           "    } else if (obj.has_data()) {\n      handle = texture::resolve_static_texture(obj);\n    }\n")
TEX_NEW = ("    } else if (copyRef != nullptr) {\n      handle = copyRef->handle;\n"
           "    } else if (obj.has_data()) {\n"
           "      // " + SIGNATURE + ": CPU-moved EFB copy (tagged RAM) -> frozen host texture.\n"
           "      handle = m14_resolve_tagged_copy(obj.data, static_cast<u32>(obj.format()), obj.width(), obj.height());\n"
           "      if (!handle) handle = texture::resolve_static_texture(obj);\n    }\n")
STATE_OLD = "extern GXState g_gxState;\n"
STATE_NEW = (STATE_OLD + "// " + SIGNATURE + " copy tags (defined in lib/dolphin/gx/GXFrameBuffer.cpp).\n"
             "gfx::TextureHandle m14_resolve_tagged_copy(const void* data, u32 format, u32 width, u32 height) noexcept;\n")
FB_MARKER = "\n// OpenSPM M14 EFB copy RAM write-back (opt-in, OPENSPM_M14_EFB_COPY_RAM=1).\n"


COMMAND = Path("lib/gx/command_processor.cpp")
CP_OLD = "  m14_audit_draw(prim, fmt, vtxCount, vertRange, false, cache.shaderInfo);\n"
CP_NEW = CP_OLD + r'''  if (::aurora::gx::raw::m14_bbox_memo_wants_draw()) {
    // OpenSPM M14 bbox memo: fold this draw into the pending bbox key.
    std::uint64_t h = m14_audit_hash(&state.pnMtx, sizeof(state.pnMtx)) ^
                      (m14_audit_hash(&state.proj, sizeof(state.proj)) << 1u);
    h ^= m14_audit_hash(&state.logicalViewport, sizeof(state.logicalViewport)) * 3u;
    h ^= m14_audit_hash(&state.logicalScissor, sizeof(state.logicalScissor)) * 5u;
    h ^= m14_audit_hash(&cache.pipelineRef, sizeof(cache.pipelineRef)) * 7u;
    const auto bindGroup = cache.bindGroups.textureBindGroup;
    h ^= m14_audit_hash(&bindGroup, sizeof(bindGroup)) * 11u;
    std::uint64_t vh = 0, ih = 0;
    (void)gfx::m14_hash_recorded_vertex_range(vertRange, vh);
    (void)gfx::m14_hash_recorded_index_range(idxRange, ih);
    h ^= vh * 13u ^ ih * 17u ^ (std::uint64_t(prim) << 40u) ^ (std::uint64_t(vtxCount) << 20u) ^ std::uint64_t(fmt);
    bool memoizable = !state.depthCompare;
    for (u32 t = 0; memoizable && t < MaxTextures; ++t)
      if (cache.shaderInfo.sampledTextures.test(t) &&
          state.copyTextures.find(state.loadedTextures[t].data) != state.copyTextures.end())
        memoizable = false;
    ::aurora::gx::raw::m14_bbox_memo_draw(h, memoizable);
  }
'''
CP_DECL_V1 = ("namespace aurora::gx::raw {\n"
           "void m14_bbox_memo_draw(std::uint64_t drawHash, bool memoizable) noexcept; // " + SIGNATURE + "\n"
           "}\n")
CP_DECL = ("namespace aurora::gx::raw {\n"
           "void m14_bbox_memo_draw(std::uint64_t drawHash, bool memoizable) noexcept; // " + SIGNATURE + "\n"
           "bool m14_bbox_memo_wants_draw() noexcept;\n"
           "extern std::uint64_t g_m14_audit_draw_ordinal;\n"
           "}\n")
CP_DECL_V2 = CP_DECL.replace("extern std::uint64_t g_m14_audit_draw_ordinal;\n", "")
# Revision 1 hashed every draw, memo on or off.
CP_NEW_V1 = CP_NEW.replace("  if (::aurora::gx::raw::m14_bbox_memo_wants_draw()) {\n", "  {\n", 1)
# Revision 3: publish the GX capture's draw ordinal so the renderer's bbox trace
# lines carry it (the two loggers interleave out of order in launch.log).
CP_NEW_V2 = CP_NEW
CP_NEW = CP_OLD + "  ::aurora::gx::raw::g_m14_audit_draw_ordinal = sM14AuditDrawOrdinal;\n" + CP_NEW[len(CP_OLD):]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def transform(root: Path, check_only: bool = False) -> str:
    files = {REGS: None, FRAMEBUFFER: None, RAW_HEADER: None, TEXTURE: None, STATE: None, COMMAND: None}
    text = {path: (root / path).read_text(encoding="utf-8") for path in files}
    regs, fb, header = text[REGS], text[FRAMEBUFFER], text[RAW_HEADER]
    tex, state, cp = text[TEXTURE], text[STATE], text[COMMAND]
    if (REGS_V2_CALL in regs and REGS_V1_DECL not in regs and HEADER_NEW in header and FB_CODE in fb
            and TEX_NEW in tex and STATE_NEW in state and CP_NEW in cp and CP_DECL in cp):
        return "already active"
    if check_only:
        return "pending"
    fresh = SIGNATURE not in fb
    # regs.cpp: fresh hook, or migrate the v1 local declaration (LNK2019) to the raw API.
    regs = regs.replace(REGS_V1_DECL, "", 1)
    if REGS_V1_CALL in regs:
        regs = replace_once(regs, REGS_V1_CALL, REGS_V2_CALL, "regs.cpp write-back call (migration)")
    elif REGS_V2_CALL not in regs:
        regs = replace_once(regs, REGS_OLD, REGS_NEW, "regs.cpp texture-copy branch")
    if HEADER_NEW not in header:
        header = (replace_once(header, HEADER_V1, HEADER_NEW, "gx_raw.hpp declarations (migration)")
                  if HEADER_V1 in header else
                  replace_once(header, HEADER_OLD, HEADER_NEW, "gx_raw.hpp readback declarations"))
    if fresh:
        fb = replace_once(fb, FB_INCLUDE_OLD, FB_INCLUDE_NEW, "GXFrameBuffer.cpp includes")
        fb = replace_once(fb, FB_ANCHOR, FB_CODE + FB_ANCHOR, "GXFrameBuffer.cpp render-mode table")
    elif FB_CODE not in fb:
        # Earlier revision in the player's cache (never reset): replace the whole helper block.
        begin = fb.find(FB_MARKER)
        finish = fb.find(FB_ANCHOR, begin)
        if begin < 0 or finish < 0:
            raise RuntimeError("EFB copy RAM write-back migration: helper block not found")
        fb = fb[:begin] + FB_CODE + fb[finish:]
    if TEX_NEW not in tex:
        tex = replace_once(tex, TEX_OLD, TEX_NEW, "texture.cpp static-texture fallback")
    if STATE_NEW not in state:
        state = replace_once(state, STATE_OLD, STATE_NEW, "gx.hpp g_gxState declaration")
    if CP_NEW not in cp:
        if CP_NEW_V2 in cp:
            cp = replace_once(cp, CP_NEW_V2, CP_NEW, "command_processor.cpp draw hook (migration v2)")
        elif CP_NEW_V1 in cp:
            cp = replace_once(cp, CP_NEW_V1, CP_NEW, "command_processor.cpp bbox memo hook (migration)")
        else:
            cp = replace_once(cp, CP_OLD, CP_NEW, "command_processor.cpp draw audit")
    if CP_DECL not in cp:
        cp = cp.replace(CP_DECL_V2, "", 1) if CP_DECL_V2 in cp else cp.replace(CP_DECL_V1, "", 1)
        # File-scope declaration, before the file's first namespace.
        ns = cp.find("\nnamespace ")
        if ns < 0:
            raise RuntimeError("command_processor.cpp: no namespace found")
        cp = cp[:ns + 1] + CP_DECL + cp[ns + 1:]
    updated = {REGS: regs, FRAMEBUFFER: fb, RAW_HEADER: header, TEXTURE: tex, STATE: state, COMMAND: cp}
    for path, value in updated.items():
        if value != text[path]:
            (root / path).write_text(value, encoding="utf-8")
    return "applied" if fresh else "migrated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora M14 EFB copy RAM write-back " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
