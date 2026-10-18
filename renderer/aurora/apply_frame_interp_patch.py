#!/usr/bin/env python3
"""OpenSPM Stage C frame interpolation (docs/PRESENTATION-ARCHITECTURE.md).

Opt-in (OPENSPM_M14_INTERP=1 on top of the Stage A present timeline in smooth
mode and the Stage B draw tags). With the variable unset nothing new runs: no
uniform mirror is kept, no frame is retained, and the presenter shows exactly
what Stage A shows.

What this adds to pinned Aurora:
- lib/gfx/frame_packet.hpp: FramePacket::m14InterpUniforms, an exact CPU copy
  of the frame's uniform arena (filled while recording, never read back from
  mapped upload memory);
- lib/gfx/recording.hpp/.cpp: the copy hook in push() and a small pool of
  mirror buffers;
- lib/gfx/tex_copy_conv.hpp/.cpp: an optional uniform-buffer override for a
  copy conversion (the replayed display copy reads its UV uniform from the
  retained buffers);
- lib/gfx/encoding.hpp/.cpp: m14_encode_frame_interp(), which re-encodes a
  retained frame's recorded EFB passes exactly like Aurora's render_pass(),
  but against the retained vertex/index/storage/uniform buffers, a private
  EFB and per-draw uniform offsets (interpolated matrices);
- lib/aurora.cpp: frame_interp.hpp (pure model, tested by
  test_display_pacing.py) and frame_interp_presenter.inl (retain, planner
  thread, per-vblank replay) between "// OpenSPM frame interp BEGIN/END"
  markers, refreshed from the repository on every configure (own-revision
  migration), their declarations, and the retain call in end_frame.

The presenter side (present_timeline_presenter.inl) calls
m14_frame_interp_choose() for each vblank when interpolation is on.

Revision 2 moves the push() and end_frame hooks off the anchors earlier
chain scripts re-check on every configure (revision 1 made each configure
duplicate them) and collapses caches patched by revision 1.

Runs after apply_draw_tags_patch.py (last in the CMake chain).
Exact/idempotent; fails closed on drift.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

PACKET = pathlib.Path("lib/gfx/frame_packet.hpp")
RECORDING_H = pathlib.Path("lib/gfx/recording.hpp")
RECORDING = pathlib.Path("lib/gfx/recording.cpp")
CONV_H = pathlib.Path("lib/gfx/tex_copy_conv.hpp")
CONV = pathlib.Path("lib/gfx/tex_copy_conv.cpp")
ENCODING_H = pathlib.Path("lib/gfx/encoding.hpp")
ENCODING = pathlib.Path("lib/gfx/encoding.cpp")
AURORA = pathlib.Path("lib/aurora.cpp")
FILES = (PACKET, RECORDING_H, RECORDING, CONV_H, CONV, ENCODING_H, ENCODING, AURORA)

TAG = "// OpenSPM frame interp"

# --- frame_packet.hpp
PACKET_ANCHOR = "  size_t stagingBuffer = 0;\n"
PACKET_FIELD = "  std::vector<uint8_t> m14InterpUniforms;  // OpenSPM frame interp: exact CPU copy of `uniforms`\n"

# --- recording.hpp
RECORDING_H_ANCHOR = "void increment_merged_draw_count() noexcept;\n"
RECORDING_H_BLOCK = (
    "// OpenSPM frame interp declarations BEGIN\n"
    "bool m14_frame_interp_recording() noexcept;\n"
    "std::vector<uint8_t> m14_frame_interp_take_mirror() noexcept;\n"
    "void m14_frame_interp_recycle(std::vector<uint8_t>&& mirror) noexcept;\n"
    "// OpenSPM frame interp declarations END\n"
)
RECORDING_H_INCLUDES = ("#include <cstdint>\n", "#include <vector>\n")

# --- recording.cpp
MIRROR_ANCHOR = "FrameRecorder g_recorder;\n"
MIRROR_BLOCK = r'''// OpenSPM frame interp mirror BEGIN
// Exact CPU copy of the uniform arena, written as the bytes are recorded
// (the staging memory itself is write-combined and unmapped at encode time).
void m14_frame_interp_mirror(const ByteBuffer& target, size_t begin, const uint8_t* data, size_t length) noexcept {
  if (!m14_frame_interp_recording() || !g_recorder.active()) {
    return;
  }
  auto& frame = g_recorder.frame();
  if (&target != &frame.uniforms) {
    return;
  }
  auto& mirror = frame.m14InterpUniforms;
  if (mirror.capacity() == 0) {
    mirror = m14_frame_interp_take_mirror();
  }
  mirror.resize(begin + length);  // value-initializes the alignment padding, like append_zeroes()
  std::memcpy(mirror.data() + begin, data, length);
}
// OpenSPM frame interp mirror END
'''
PUSH_OLD = ("    target.append(data, length);\n"
            "  }\n"
            "  return {static_cast<uint32_t>(begin), static_cast<uint32_t>(length)};\n"
            "}\n"
            "\n"
            "Range map(")
# The hook goes AFTER target.append(): apply_interpolation_present_patch.py
# (earlier in the chain) keeps its replay mirror block right before
# target.append() and re-inserts it on every configure when anything sits in
# between. Revision 1 put the hook there, so each configure duplicated both.
MIRROR_CALL = "    m14_frame_interp_mirror(target, begin, data, length);  // OpenSPM frame interp\n"
PUSH_NEW = PUSH_OLD.replace("    target.append(data, length);\n", "    target.append(data, length);\n" + MIRROR_CALL, 1)
# apply_interpolation_present_patch.py's block, duplicated by revision 1 (migration only).
REPLAY_MIRROR = r'''    // M14 cacheable replay mirrors: copy the original CPU input, never mapped upload reads.
    if (m14_replay_enabled()) {
      auto& frame = g_recorder.frame();
      auto* mirror = &target == &frame.uniforms ? &frame.m14UniformRead :
                     &target == &frame.verts ? &frame.m14VertexRead :
                     &target == &frame.indices ? &frame.m14IndexRead : nullptr;
      if (mirror) {
        mirror->resize(begin + length, 0);
        std::memcpy(mirror->data() + begin, data, length);
      }
    }
'''
POOL_ANCHOR = "void m14_push_gx_draw_command(gx::DrawData data, uint32_t uniformPrefixBytes,\n"
POOL_BLOCK = r'''// OpenSPM frame interp pool BEGIN
namespace {
std::mutex g_m14InterpMirrorMutex;
std::vector<std::vector<uint8_t>> g_m14InterpMirrorPool;
} // namespace

bool m14_frame_interp_recording() noexcept {
  static const bool enabled = [] {
    const auto on = [](const char* name) {
      const char* value = std::getenv(name);
      return value != nullptr && value[0] == '1' && value[1] == '\0';
    };
    return on("OPENSPM_M14_INTERP") && on("OPENSPM_M14_ASYNC_PRESENTER") && on("OPENSPM_M14_PRESENT_TIMELINE");
  }();
  return enabled;
}

std::vector<uint8_t> m14_frame_interp_take_mirror() noexcept {
  std::vector<uint8_t> mirror;
  {
    std::lock_guard lock{g_m14InterpMirrorMutex};
    if (!g_m14InterpMirrorPool.empty()) {
      mirror = std::move(g_m14InterpMirrorPool.back());
      g_m14InterpMirrorPool.pop_back();
    }
  }
  mirror.clear();
  if (mirror.capacity() == 0) {
    mirror.reserve(size_t{8} << 20);
  }
  return mirror;
}

void m14_frame_interp_recycle(std::vector<uint8_t>&& mirror) noexcept {
  if (mirror.capacity() == 0) {
    return;
  }
  mirror.clear();
  std::lock_guard lock{g_m14InterpMirrorMutex};
  if (g_m14InterpMirrorPool.size() < 4) {
    g_m14InterpMirrorPool.push_back(std::move(mirror));
  }
}
// OpenSPM frame interp pool END
'''
RECORDING_INCLUDES = ("#include <cstdlib>\n", "#include <cstring>\n", "#include <mutex>\n", "#include <vector>\n")

# --- tex_copy_conv
CONV_H_OLD = "  SampleFilter sampleFilter = SampleFilter::Nearest;\n"
CONV_H_NEW = CONV_H_OLD + "  wgpu::Buffer m14UniformBuffer;  // OpenSPM frame interp: read uniformRange from this buffer\n"
CONV_OLD = "            .buffer = detail::resources().uniformBuffer,\n"
CONV_NEW = ("            .buffer = req.m14UniformBuffer ? req.m14UniformBuffer\n"
            "                                           : detail::resources().uniformBuffer,  // OpenSPM frame interp\n")

# --- encoding
ENCODING_H_ANCHOR = "void encode_op(wgpu::CommandEncoder& encoder, FramePacket& frame, const FrameOp& op);\n"
ENCODING_H_BLOCK = r'''// OpenSPM frame interp declarations BEGIN
// Re-encode a retained frame's EFB passes (Stage C interpolation): like
// render_pass(), but GX draws bind the retained buffers and drawUniform[i] as
// the uniform offset of the i-th GX draw in stream order.
struct M14InterpReplay {
  std::vector<RenderPass>* passes = nullptr;
  const uint32_t* drawUniform = nullptr;
  const wgpu::BindGroup* drawTextures = nullptr;
  size_t drawCount = 0;
  wgpu::BindGroup staticGroup;
  wgpu::BindGroup uniformGroup;
  wgpu::Buffer index;
  wgpu::TextureView color;
  wgpu::TextureView resolve;  // MSAA resolve target, or null
  wgpu::TextureView depth;
};
struct M14InterpReplayStats {
  uint32_t passes = 0;
  uint32_t gxDraws = 0;
  uint32_t otherDraws = 0;
};
bool m14_encode_frame_interp(wgpu::CommandEncoder& encoder, const M14InterpReplay& replay,
                             M14InterpReplayStats& stats) noexcept;
// OpenSPM frame interp declarations END
'''
ENCODING_ANCHOR = "  g_currentPipeline = ref;\n  return true;\n}\n"  # end of bind_pipeline(), the last function
ENCODING_BLOCK = r'''// OpenSPM frame interp BEGIN
namespace {
void m14_interp_render_gx(const gx::DrawData& data, const wgpu::RenderPassEncoder& pass,
                          const detail::M14InterpReplay& replay, uint32_t uniformOffset,
                          const wgpu::BindGroup& texture) {
  // gx::render(), bound to the retained buffers.
  if (!bind_pipeline(data.pipeline, pass)) {
    return;
  }
  pass.SetImmediates(0, &data.immediateData, sizeof(data.immediateData));
  pass.SetBindGroup(1, replay.uniformGroup, 1, &uniformOffset);
  if (data.bindGroups.textureBindGroup && texture) {
    pass.SetBindGroup(2, texture);
  }
  pass.SetBindGroup(3, gx::bbox::bind_group());
  pass.SetIndexBuffer(replay.index, wgpu::IndexFormat::Uint16, data.idxRange.offset, data.idxRange.size);
  if (data.dstAlpha != UINT32_MAX) {
    const wgpu::Color color{0.f, 0.f, 0.f, data.dstAlpha / 255.f};
    pass.SetBlendConstant(&color);
  }
  if (data.indexCount == 0) {
    pass.Draw(data.vtxCount, data.instanceCount);
  } else {
    pass.DrawIndexed(data.indexCount, data.instanceCount);
  }
}
} // namespace

namespace detail {
bool m14_encode_frame_interp(wgpu::CommandEncoder& encoder, const M14InterpReplay& replay,
                             M14InterpReplayStats& stats) noexcept {
  gx::bbox::ReplayScope privateBbox;  // replayed draws never touch the game's PE bbox
  stats = {};
  if (replay.passes == nullptr || !replay.color || !replay.staticGroup || !replay.uniformGroup || !replay.index) {
    return false;
  }
  size_t draw = 0;
  for (auto& passInfo : *replay.passes) {
    const auto& source = passInfo.colorAttachments[SceneColorAttachmentIndex];
    const wgpu::RenderPassColorAttachment color{
        .view = replay.color,
        .resolveTarget = replay.resolve,
        .loadOp = source.loadOp != wgpu::LoadOp::Undefined ? source.loadOp
                                                           : (source.clear ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load),
        .storeOp = wgpu::StoreOp::Store,
        .clearValue = {.r = source.clearValue.x(), .g = source.clearValue.y(), .b = source.clearValue.z(),
                       .a = source.clearValue.w()},
    };
    wgpu::RenderPassDepthStencilAttachment depth{};
    const wgpu::RenderPassDepthStencilAttachment* depthPtr = nullptr;
    if (passInfo.depthStencilView && replay.depth) {
      depth = {
          .view = replay.depth,
          .depthLoadOp = passInfo.hasDepth ? (passInfo.depthLoadOp != wgpu::LoadOp::Undefined
                                                  ? passInfo.depthLoadOp
                                                  : (passInfo.clearDepth ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load))
                                           : wgpu::LoadOp::Undefined,
          .depthStoreOp = passInfo.hasDepth ? wgpu::StoreOp::Store : wgpu::StoreOp::Undefined,
          .depthClearValue = passInfo.clearDepthValue,
          .stencilLoadOp = wgpu::LoadOp::Undefined,
          .stencilStoreOp = wgpu::StoreOp::Undefined,
      };
      depthPtr = &depth;
    }
    const wgpu::RenderPassDescriptor descriptor{
        .label = "OpenSPM interpolated replay",
        .colorAttachmentCount = 1,
        .colorAttachments = &color,
        .depthStencilAttachment = depthPtr,
    };
    auto pass = encoder.BeginRenderPass(&descriptor);
    g_currentPipeline = UINTPTR_MAX;
    pass.SetBindGroup(0, replay.staticGroup);
    pass.SetBindGroup(2, gx::g_emptyTextureBindGroup);
    for (auto& command : passInfo.commands) {
      switch (command.type) {
      case CommandType::SetViewport:
        apply_viewport(pass, command.data.setViewport);
        break;
      case CommandType::SetScissor:
        apply_scissor(pass, command.data.setScissor, source.size);
        break;
      case CommandType::Draw: {
        auto& d = command.data.draw;
        if (d.m14ReplayGX) {
          if (draw >= replay.drawCount) {
            pass.End();
            return false;
          }
          gx::DrawData data{};
          std::memcpy(&data, d.payload.data(), sizeof(data));
          m14_interp_render_gx(data, pass, replay, replay.drawUniform[draw], replay.drawTextures[draw]);
          ++draw;
          ++stats.gxDraws;
        } else if (d.encoder != nullptr) {
          d.encoder(d.payload.data(), pass, passInfo);  // Aurora utility draws (clears), as render_pass()
          ++stats.otherDraws;
        }
      } break;
      case CommandType::CustomDraw:  // never retained (not replay-safe)
        pass.End();
        return false;
      case CommandType::DebugMarker:
        break;
      }
    }
    pass.End();
    ++stats.passes;
  }
  return draw == replay.drawCount && stats.passes != 0;
}
} // namespace detail
// OpenSPM frame interp END
'''

# --- aurora.cpp
AURORA_DECL_ANCHOR = "struct M14PresenterImage {\n"
AURORA_DECL_BLOCK = (
    "// OpenSPM frame interp declarations BEGIN\n"
    "struct M14PresenterImage;\n"
    "bool m14_frame_interp_enabled() noexcept;\n"
    "double m14_frame_interp_guard_ns(double displayPeriodNs) noexcept;\n"
    "void m14_frame_interp_retain(wgpu::CommandEncoder& encoder, const gfx::TextureHandle& display);\n"
    "M14PresenterImage m14_frame_interp_choose(uint64_t a, uint64_t b, double alpha, const M14PresenterImage& imageA,\n"
    "                                          const M14PresenterImage& imageB);\n"
    "// OpenSPM frame interp declarations END\n"
)
INIT_ANCHOR = "AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {"
RETAIN_OLD = ("      const uint32_t m14PresenterSlot =\n"
              "          m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot);\n")
# After the presenter snapshot: apply_interpolation_replay_encode_patch.py
# (earlier in the chain) re-inserts its proof call right before RETAIN_OLD on
# configure; revision 1 sat in between, so each configure added another retain
# (and Stage C then cleared its retained frames twice per frame).
RETAIN_CALL = "      m14_frame_interp_retain(encoder, openspmDisplayCopySnapshot);  // OpenSPM Stage C (no-op unless enabled)\n"
RETAIN_NEW = RETAIN_OLD + RETAIN_CALL
PROOF_CALL = "      m14_encode_interpolation_replay_proof(encoder);\n"  # dev140b (migration only)
AURORA_INCLUDES = ("#include <atomic>\n", "#include <cstring>\n", "#include <memory>\n", "#include <string>\n", "#include <vector>\n",
                   "#include <deque>\n", "#include <unordered_map>\n", "#include <condition_variable>\n",
                   "#include <thread>\n", "#include \"gfx/resource_cache.hpp\"\n")

# Strings other chain scripts anchor on; embedded code must never contain them.
FOREIGN_ANCHORS = (
    "void m14_presenter_main()",
    "(void)m14_present_retained_image(image);",
    "if (g_m14PresenterStop || !g_m14PresenterHaveImage",
    "m14_stop_async_presenter();",
    "void m14_publish_presenter_slot(",
    "void m14_start_async_presenter_locked(",
    "m14_encode_presenter_snapshot(encoder",
    "struct M14PresenterImage {",
    "M14 visible interpolation helpers",
    "M14 present timeline BEGIN",
    INIT_ANCHOR,
)


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def aurora_region() -> str:
    header = (HERE / "frame_interp.hpp").read_text(encoding="utf-8")
    glue = (HERE / "frame_interp_presenter.inl").read_text(encoding="utf-8")
    lines = [line for line in header.splitlines(keepends=True)
             if not line.startswith("#include") and line.strip() != "#pragma once"]
    body = "".join(lines) + "\n" + glue
    for anchor in FOREIGN_ANCHORS:
        if anchor in body:
            raise RuntimeError(f"frame interp code contains a foreign chain anchor: {anchor}")
    if "#include" in body:
        raise RuntimeError("frame interp code must not contain #include lines")
    return (TAG + " BEGIN\n#ifdef AURORA_ENABLE_GX\n" + body + "#endif\n" + TAG + " END\n")


def ensure_include(text: str, include: str, anchor: str, what: str) -> str:
    if include in text:
        return text
    if text.count(anchor) != 1:
        raise RuntimeError(f"frame interp include anchor missing in {what} for {include.strip()}")
    return text.replace(anchor, include + anchor, 1)


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        if text.count(new) != 1:
            raise RuntimeError(f"frame interp {what} is not unique")
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"frame interp {what} anchor expected once, found {text.count(old)}")
    return text.replace(old, new, 1)


def replace_all_exact(text: str, old: str, new: str, count: int, what: str) -> str:
    if text.count(new) == count and old not in text.replace(new, ""):
        return text
    if text.count(new) != 0 or text.count(old) != count:
        raise RuntimeError(f"frame interp {what}: expected {count} anchors, found {text.count(old)}")
    return text.replace(old, new)


def replace_marked(text: str, begin: str, end: str, block: str, anchor: str, what: str,
                   after: bool = False) -> str:
    if begin in text:
        if text.count(begin) != 1 or text.count(end) != 1:
            raise RuntimeError(f"frame interp {what} markers are not unique")
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        return text[:start] + block + text[stop:]
    if text.count(anchor) != 1:
        raise RuntimeError(f"frame interp {what} anchor expected once, found {text.count(anchor)}")
    at = text.index(anchor) + (len(anchor) if after else 0)
    return text[:at] + block + ("" if after else "\n") + text[at:]


def patch_packet(text: str) -> str:
    if "m14InterpUniforms" in text and PACKET_FIELD not in text:
        raise RuntimeError("frame interp FramePacket mirror has an unknown shape")
    return replace_once(text, PACKET_ANCHOR, PACKET_ANCHOR + PACKET_FIELD, "FramePacket mirror")


def patch_recording_h(text: str) -> str:
    for include in RECORDING_H_INCLUDES:
        text = ensure_include(text, include, "#include <string>\n", "recording.hpp")
    return replace_marked(text, "// OpenSPM frame interp declarations BEGIN\n",
                          "// OpenSPM frame interp declarations END\n", RECORDING_H_BLOCK,
                          RECORDING_H_ANCHOR, "recording declarations", after=True)


def patch_recording(text: str) -> str:
    for include in RECORDING_INCLUDES:
        text = ensure_include(text, include, "#include <array>\n", "recording.cpp")
    text = replace_marked(text, "// OpenSPM frame interp mirror BEGIN\n", "// OpenSPM frame interp mirror END\n",
                          MIRROR_BLOCK, MIRROR_ANCHOR, "uniform mirror", after=True)
    if PUSH_NEW not in text and MIRROR_CALL in text:  # revision 1 (hook before target.append)
        text = text.replace(MIRROR_CALL, "")
        while REPLAY_MIRROR + REPLAY_MIRROR in text:
            text = text.replace(REPLAY_MIRROR + REPLAY_MIRROR, REPLAY_MIRROR)
    text = replace_once(text, PUSH_OLD, PUSH_NEW, "push() mirror hook")
    if text.count(MIRROR_CALL) != 1 or text.count(REPLAY_MIRROR + REPLAY_MIRROR) != 0:
        raise RuntimeError("frame interp push() mirror hook is not unique")
    text = replace_marked(text, "// OpenSPM frame interp pool BEGIN\n", "// OpenSPM frame interp pool END\n",
                          POOL_BLOCK, POOL_ANCHOR, "mirror pool")
    order = (text.index(MIRROR_ANCHOR), text.index("// OpenSPM frame interp mirror BEGIN"), text.index(PUSH_NEW),
             text.index("// OpenSPM frame interp pool BEGIN"), text.index(POOL_ANCHOR))
    if list(order) != sorted(order):
        raise RuntimeError("frame interp recording.cpp edits are out of place")
    return text


def patch_encoding_h(text: str) -> str:
    text = ensure_include(text, "#include <vector>\n", "#include \"frame_packet.hpp\"\n", "encoding.hpp")
    return replace_marked(text, "// OpenSPM frame interp declarations BEGIN\n",
                          "// OpenSPM frame interp declarations END\n", ENCODING_H_BLOCK, ENCODING_H_ANCHOR,
                          "encoding declarations", after=True)


def patch_encoding(text: str) -> str:
    if '#include "../gx/bbox.hpp"' not in text:
        raise RuntimeError("frame interp encoding.cpp prerequisite missing: bbox include")
    return replace_marked(text, TAG + " BEGIN\n", TAG + " END\n", ENCODING_BLOCK, ENCODING_ANCHOR,
                          "replay encoder", after=True)


def patch_aurora(text: str) -> str:
    for include in AURORA_INCLUDES:
        text = ensure_include(text, include, "#include <magic_enum.hpp>\n", "aurora.cpp")
    text = replace_marked(text, "// OpenSPM frame interp declarations BEGIN\n",
                          "// OpenSPM frame interp declarations END\n", AURORA_DECL_BLOCK, AURORA_DECL_ANCHOR,
                          "aurora declarations")
    text = replace_marked(text, TAG + " BEGIN\n", TAG + " END\n", aurora_region(), INIT_ANCHOR, "presenter code")
    if RETAIN_NEW not in text and RETAIN_CALL in text:  # revision 1 (retain before the snapshot)
        text = text.replace(RETAIN_CALL, "")
        while PROOF_CALL + PROOF_CALL in text:
            text = text.replace(PROOF_CALL + PROOF_CALL, PROOF_CALL)
    text = replace_once(text, RETAIN_OLD, RETAIN_NEW, "retain hook")
    if text.count(RETAIN_CALL) != 1:
        raise RuntimeError("frame interp retain hook is not unique")
    order = (text.index("// OpenSPM frame interp declarations BEGIN"), text.index(AURORA_DECL_ANCHOR),
             text.index("// M14 present timeline END"), text.index(TAG + " BEGIN"), text.index(INIT_ANCHOR),
             text.index(RETAIN_NEW))
    if list(order) != sorted(order):
        raise RuntimeError("frame interp aurora.cpp edits are out of place")
    return text


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {name: source / name for name in FILES}
    original = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    prerequisites = (
        (PACKET, "struct FramePacket {"),
        (PACKET, "uint16_t m14DrawView = 0;"),  # Stage B view records (apply_draw_tags_patch.py)
        (PACKET, "bool m14ReplayGX = false;"),
        (RECORDING, "Range push(ByteBuffer& target, const uint8_t* data, size_t length, size_t alignment) {"),
        (RECORDING, "command.m14ReplayGX = true;"),
        (CONV, "static void execute(const wgpu::CommandEncoder& cmd, const ConvRequest& req,"),
        (ENCODING, "void apply_scissor(const wgpu::RenderPassEncoder& pass, const ClipRect& sc, const wgpu::Extent3D& size) {"),
        (ENCODING, "PipelineRef g_currentPipeline;"),
        (AURORA, "gfx::detail::m14_end_frame_packet()"),
        (AURORA, "// M14 present timeline END\n"),
        (AURORA, "g_m14PresenterQueueMutex"),
        (AURORA, "gx::bbox::readback_pending()"),  # bbox priority: set from a CPU bbox read until the next publish
    )
    missing = [f"{name}: {needle[:50]}" for name, needle in prerequisites if needle not in original[name]]
    if missing:
        raise RuntimeError("frame interp prerequisites missing: " + "; ".join(missing))
    updated = dict(original)
    updated[PACKET] = patch_packet(original[PACKET])
    updated[RECORDING_H] = patch_recording_h(original[RECORDING_H])
    updated[RECORDING] = patch_recording(original[RECORDING])
    updated[CONV_H] = replace_once(original[CONV_H], CONV_H_OLD, CONV_H_NEW, "copy conversion buffer override")
    updated[CONV] = replace_all_exact(original[CONV], CONV_OLD, CONV_NEW, 2, "copy conversion uniform binding")
    updated[ENCODING_H] = patch_encoding_h(original[ENCODING_H])
    updated[ENCODING] = patch_encoding(original[ENCODING])
    updated[AURORA] = patch_aurora(original[AURORA])
    required = (
        (AURORA, 'std::getenv("OPENSPM_M14_INTERP")'),
        (AURORA, "inline PairPlan plan_pair("),
        (AURORA, "M14 frame interp: presents="),
        (AURORA, RETAIN_NEW),
        (RECORDING, PUSH_NEW),
        (RECORDING, 'on("OPENSPM_M14_INTERP")'),
        (ENCODING, "bool m14_encode_frame_interp("),
        (CONV, "req.m14UniformBuffer ? req.m14UniformBuffer"),
    )
    missing = [needle[:50] for name, needle in required if needle not in updated[name]]
    if missing:
        raise RuntimeError("frame interp migration incomplete: " + ", ".join(missing))
    changed = [name for name in FILES if updated[name] != original[name]]
    if check_only:
        return "verified" if not changed else "needs-apply"
    for name in changed:
        paths[name].write_text(updated[name], encoding="utf-8", newline="\n")
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
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
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: Aurora frame interp adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "verified": "Aurora M14 frame interp verified",
        "needs-apply": "Aurora M14 frame interp not yet applied",
        "applied": "Aurora M14 frame interp applied",
        "already-applied": "Aurora M14 frame interp already applied",
    }[result])
    return 0 if result != "needs-apply" else 5


if __name__ == "__main__":
    raise SystemExit(main())
