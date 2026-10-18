#!/usr/bin/env python3
"""OpenSPM M14 dev140b: offscreen interpolation replay encode proof.

dev140a prepares adjacent-frame interpolation uniform arenas while staging
memory is mapped.  dev140b proves those ranges can drive a second GX scene
render on Aurora's render worker without touching the authoritative EFB,
swapchain, guest-visible EFB-copy side effects, or the dev139 presentation
choice.

The proof is dormant unless OPENSPM_M14_INTERP_REPLAY_ENCODE=1.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

FRAME_PACKET = pathlib.Path("lib/gfx/frame_packet.hpp")
RECORDING_CPP = pathlib.Path("lib/gfx/recording.cpp")
ENCODING_H = pathlib.Path("lib/gfx/encoding.hpp")
ENCODING_CPP = pathlib.Path("lib/gfx/encoding.cpp")
FRAME_H = pathlib.Path("lib/gfx/frame.hpp")
FRAME_CPP = pathlib.Path("lib/gfx/frame.cpp")
AURORA_CPP = pathlib.Path("lib/aurora.cpp")

SIGNATURE = "M14 dev140b offscreen replay encode active"

ENCODING_DECL = r'''
struct M14ReplayEncodeStats {
  uint32_t passes = 0;
  uint32_t gxDraws = 0;
  uint32_t otherDraws = 0;
  uint32_t efbCopyPasses = 0;
  uint32_t rejectedPasses = 0;
  uint32_t unsupportedLayoutRejects = 0;
  uint32_t priorLoadRejects = 0;
  uint32_t customDrawRejects = 0;
  uint32_t rangeRejects = 0;
};

bool m14_encode_interpolation_replay(wgpu::CommandEncoder& encoder,
                                     FramePacket& frame,
                                     uint32_t uniformBase,
                                     const wgpu::TextureView& colorView,
                                     const wgpu::TextureView& depthView,
                                     const wgpu::Extent3D& size,
                                     M14ReplayEncodeStats& stats) noexcept;
'''

ENCODING_IMPL = r'''
bool m14_encode_interpolation_replay(wgpu::CommandEncoder& encoder,
                                     FramePacket& frame,
                                     uint32_t uniformBase,
                                     const wgpu::TextureView& colorView,
                                     const wgpu::TextureView& depthView,
                                     const wgpu::Extent3D& size,
                                     M14ReplayEncodeStats& stats) noexcept {
  stats = {};
  if (!colorView || size.width == 0u || size.height == 0u ||
      frame.m14ReplayOriginalUniformBytes == 0u) {
    return false;
  }

  // Validate the whole replay set before emitting any commands.  A partial
  // target would be useless for visual validation and could hide unsupported
  // pass shapes.
  bool firstEfbPass = true;
  for (const auto& passInfo : frame.renderPasses) {
    if (!passInfo.m14ReplayEfb || !passInfo.sealed || passInfo.discardable) {
      continue;
    }
    if (passInfo.resolveTarget) ++stats.efbCopyPasses;
    if (passInfo.colorAttachmentCount != 1u || passInfo.msaaSamples != 1u ||
        passInfo.hasStencil || (passInfo.hasDepth && !depthView)) {
      ++stats.rejectedPasses;
      ++stats.unsupportedLayoutRejects;
      return false;
    }
    const auto& source = passInfo.colorAttachments[SceneColorAttachmentIndex];
    const auto colorLoad =
        source.loadOp != wgpu::LoadOp::Undefined
            ? source.loadOp
            : (source.clear ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
    const auto depthLoad =
        passInfo.depthLoadOp != wgpu::LoadOp::Undefined
            ? passInfo.depthLoadOp
            : (passInfo.clearDepth ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
    // An isolated replay target has no prior-frame EFB contents.  Until dev140
    // deliberately seeds that history, reject streams whose first EFB segment
    // depends on Load rather than silently rendering undefined pixels.
    if (firstEfbPass &&
        (colorLoad == wgpu::LoadOp::Load ||
         (passInfo.hasDepth && depthLoad == wgpu::LoadOp::Load))) {
      ++stats.rejectedPasses;
      ++stats.priorLoadRejects;
      return false;
    }
    for (const auto& command : passInfo.commands) {
      if (command.type == CommandType::CustomDraw) {
        ++stats.rejectedPasses;
        ++stats.customDrawRejects;
        return false;
      }
    }
    firstEfbPass = false;
  }

  firstEfbPass = true;
  for (auto& passInfo : frame.renderPasses) {
    if (!passInfo.m14ReplayEfb || !passInfo.sealed || passInfo.discardable) {
      continue;
    }

    const auto& source = passInfo.colorAttachments[SceneColorAttachmentIndex];
    const auto colorLoad =
        source.loadOp != wgpu::LoadOp::Undefined
            ? source.loadOp
            : (source.clear ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
    const wgpu::RenderPassColorAttachment colorAttachment{
        .view = colorView,
        .loadOp = colorLoad,
        .storeOp = wgpu::StoreOp::Store,
        .clearValue =
            {
                .r = source.clearValue.x(),
                .g = source.clearValue.y(),
                .b = source.clearValue.z(),
                .a = source.clearValue.w(),
            },
    };

    wgpu::RenderPassDepthStencilAttachment depthAttachment{};
    const wgpu::RenderPassDepthStencilAttachment* depthAttachmentPtr = nullptr;
    if (passInfo.hasDepth) {
      const auto depthLoad =
          passInfo.depthLoadOp != wgpu::LoadOp::Undefined
              ? passInfo.depthLoadOp
              : (passInfo.clearDepth ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
      depthAttachment = {
          .view = depthView,
          .depthLoadOp = depthLoad,
          .depthStoreOp = wgpu::StoreOp::Store,
          .depthClearValue = passInfo.clearDepthValue,
          .stencilLoadOp = wgpu::LoadOp::Undefined,
          .stencilStoreOp = wgpu::StoreOp::Undefined,
          .stencilClearValue = 0,
      };
      depthAttachmentPtr = &depthAttachment;
    }

    const wgpu::RenderPassDescriptor descriptor{
        .label = "OpenSPM dev140b interpolation replay",
        .colorAttachmentCount = 1,
        .colorAttachments = &colorAttachment,
        .depthStencilAttachment = depthAttachmentPtr,
    };
    auto pass = encoder.BeginRenderPass(&descriptor);

    g_currentPipeline = UINTPTR_MAX;
    pass.SetBindGroup(0, resources().staticBindGroup);
    pass.SetBindGroup(2, gx::g_emptyTextureBindGroup);

    Viewport currentViewport{};
    ClipRect currentScissor{};
    bool hasViewport = false;
    bool hasScissor = false;
    for (auto& command : passInfo.commands) {
      switch (command.type) {
      case CommandType::SetViewport:
        currentViewport = command.data.setViewport;
        apply_viewport(pass, currentViewport);
        hasViewport = true;
        break;
      case CommandType::SetScissor:
        currentScissor = command.data.setScissor;
        apply_scissor(pass, currentScissor, size);
        hasScissor = true;
        break;
      case CommandType::Draw: {
        auto& draw = command.data.draw;
        if (draw.m14ReplayGX) {
          gx::DrawData data{};
          std::memcpy(&data, draw.payload.data(), sizeof(data));
          const uint64_t originalEnd =
              static_cast<uint64_t>(data.uniformRange.offset) +
              static_cast<uint64_t>(data.uniformRange.size);
          if (originalEnd > frame.m14ReplayOriginalUniformBytes) {
            pass.End();
            ++stats.rejectedPasses;
            ++stats.rangeRejects;
            return false;
          }
          const uint64_t replayOffset =
              static_cast<uint64_t>(uniformBase) +
              static_cast<uint64_t>(data.uniformRange.offset);
          const uint64_t replayEnd =
              replayOffset + static_cast<uint64_t>(data.uniformRange.size);
          if (replayEnd > frame.uniforms.size() ||
              replayOffset > UINT32_MAX) {
            pass.End();
            ++stats.rejectedPasses;
            ++stats.rangeRejects;
            return false;
          }
          data.uniformRange.offset = static_cast<uint32_t>(replayOffset);
          gx::render(data, pass);
          ++stats.gxDraws;
        } else if (draw.encoder != nullptr) {
          // Preserve Aurora-owned clear/utility draws.  Their payloads do not
          // use GX DrawData's dynamic transform uniform offset.
          draw.encoder(draw.payload.data(), pass, passInfo);
          ++stats.otherDraws;
          // A utility draw can invalidate our assumptions about pipeline/binds.
          g_currentPipeline = UINTPTR_MAX;
          pass.SetBindGroup(0, resources().staticBindGroup);
          pass.SetBindGroup(2, gx::g_emptyTextureBindGroup);
          if (hasViewport) apply_viewport(pass, currentViewport);
          if (hasScissor) apply_scissor(pass, currentScissor, size);
        }
      } break;
      case CommandType::CustomDraw:
        // Rejected by the preflight above.
        break;
      case CommandType::DebugMarker:
        break;
      }
    }
    pass.End();
    ++stats.passes;
    firstEfbPass = false;
  }

  return stats.passes != 0u && stats.gxDraws != 0u;
}
'''

AURORA_STATE = r'''
struct M14ReplayEncodeTarget {
  webgpu::TextureWithSampler color;
  webgpu::TextureWithSampler depth;
};

std::array<M14ReplayEncodeTarget, 2> g_m14ReplayEncodeTargets;
uint64_t g_m14ReplayEncodeFrames = 0;
bool g_m14ReplayEncodeReported = false;
bool g_m14ReplayEncodeUnsupportedReported = false;

bool m14_replay_encode_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_INTERP_REPLAY_ENCODE");
    return value != nullptr && value[0] == '1' && value[1] == '\0';
  }();
  return enabled;
}

void m14_reset_replay_encode_targets() noexcept {
  g_m14ReplayEncodeTargets = {};
}

bool m14_ensure_replay_encode_target(uint32_t slot,
                                     uint32_t width,
                                     uint32_t height) {
  if (slot >= g_m14ReplayEncodeTargets.size() || width == 0u || height == 0u) {
    return false;
  }
  auto& target = g_m14ReplayEncodeTargets[slot];
  const auto colorFormat = webgpu::g_graphicsConfig.surfaceConfiguration.format;
  const auto depthFormat = webgpu::g_graphicsConfig.depthFormat;
  const bool current =
      target.color.texture && target.depth.texture &&
      target.color.size.width == width && target.color.size.height == height &&
      target.color.format == colorFormat && target.depth.format == depthFormat;
  if (current) return true;

  target = {};
  target.color = webgpu::create_render_texture(width, height, false);

  const wgpu::Extent3D size{width, height, 1};
  const wgpu::TextureDescriptor depthDescriptor{
      .label = "OpenSPM dev140b replay depth",
      .usage = wgpu::TextureUsage::RenderAttachment,
      .dimension = wgpu::TextureDimension::e2D,
      .size = size,
      .format = depthFormat,
      .mipLevelCount = 1,
      .sampleCount = 1,
  };
  auto depthTexture = g_device.CreateTexture(&depthDescriptor);
  if (!depthTexture) {
    target = {};
    return false;
  }
  auto depthView = depthTexture.CreateView();
  target.depth = webgpu::TextureWithSampler{
      .texture = std::move(depthTexture),
      .view = std::move(depthView),
      .size = size,
      .format = depthFormat,
  };
  return static_cast<bool>(target.color.texture && target.color.view &&
                           target.depth.texture && target.depth.view);
}

void m14_encode_interpolation_replay_proof(wgpu::CommandEncoder& encoder) {
  if (!m14_replay_encode_enabled()) return;
  auto* frame = gfx::detail::m14_end_frame_packet();
  if (frame == nullptr || frame->m14ReplayPhases.empty()) return;

  if (webgpu::g_graphicsConfig.msaaSamples != 1u) {
    if (!g_m14ReplayEncodeUnsupportedReported) {
      Log.warn("M14 dev140b replay encode disabled: MSAA {} is not yet replay-safe",
               webgpu::g_graphicsConfig.msaaSamples);
      g_m14ReplayEncodeUnsupportedReported = true;
    }
    return;
  }

  const auto size = webgpu::g_frameBuffer.size;
  const uint32_t phaseCount = static_cast<uint32_t>(
      std::min<size_t>(frame->m14ReplayPhases.size(),
                       g_m14ReplayEncodeTargets.size()));
  uint32_t encoded = 0;
  uint32_t passes = 0;
  uint32_t gxDraws = 0;
  uint32_t otherDraws = 0;
  uint32_t efbCopyPasses = 0;
  uint32_t rejected = 0;
  uint32_t unsupportedLayout = 0;
  uint32_t priorLoad = 0;
  uint32_t customDraw = 0;
  uint32_t rangeErrors = 0;

  const auto& stagingBuffer = gfx::detail::staging_buffer(frame->stagingBuffer);
  auto& uniformBuffer = gfx::detail::resources().uniformBuffer;
  const uint64_t replayCopyBytes =
      AURORA_ALIGN(static_cast<uint64_t>(frame->m14ReplayOriginalUniformBytes), 4u);

  for (uint32_t i = 0; i < phaseCount; ++i) {
    const auto& phase = frame->m14ReplayPhases[i];
    const uint64_t phaseEnd =
        static_cast<uint64_t>(phase.uniformBase) + replayCopyBytes;
    if (replayCopyBytes == 0u || phaseEnd > gfx::UniformBufferSize ||
        phaseEnd > frame->uniforms.size()) {
      ++rejected;
      ++rangeErrors;
      continue;
    }

    // Replay arenas are appended after many authoritative frame ops have
    // already captured their staging high-water marks. Copy each arena
    // explicitly here, after Aurora has unmapped staging and on the render
    // worker, so replay never depends on a later authoritative pass existing.
    encoder.CopyBufferToBuffer(
        stagingBuffer,
        static_cast<uint64_t>(gfx::VertexBufferSize) + phase.uniformBase,
        uniformBuffer, phase.uniformBase, replayCopyBytes);

    if (!m14_ensure_replay_encode_target(i, size.width, size.height)) {
      ++rejected;
      continue;
    }
    auto& target = g_m14ReplayEncodeTargets[i];
    gfx::detail::M14ReplayEncodeStats stats{};
    if (gfx::detail::m14_encode_interpolation_replay(
            encoder, *frame, phase.uniformBase, target.color.view,
            target.depth.view, size, stats)) {
      ++encoded;
    }
    passes += stats.passes;
    gxDraws += stats.gxDraws;
    otherDraws += stats.otherDraws;
    efbCopyPasses += stats.efbCopyPasses;
    rejected += stats.rejectedPasses;
    unsupportedLayout += stats.unsupportedLayoutRejects;
    priorLoad += stats.priorLoadRejects;
    customDraw += stats.customDrawRejects;
    rangeErrors += stats.rangeRejects;
  }

  ++g_m14ReplayEncodeFrames;
  if (!g_m14ReplayEncodeReported) {
    Log.info("M14 dev140b offscreen replay encode active: visual_present=off "
             "authoritative_efb_untouched=1 render_worker=1");
    g_m14ReplayEncodeReported = true;
  }
  if (g_m14ReplayEncodeFrames <= 8u ||
      (g_m14ReplayEncodeFrames & (g_m14ReplayEncodeFrames - 1u)) == 0u) {
    Log.info("M14 dev140b replay encode frame={} requested_phases={} encoded={} "
             "passes={} gx_draws={} other_draws={} efb_copy_passes={} rejected={} "
             "reject_layout={} reject_prior_load={} reject_custom={} reject_range={}",
             g_m14ReplayEncodeFrames, phaseCount, encoded, passes, gxDraws,
             otherDraws, efbCopyPasses, rejected, unsupportedLayout, priorLoad,
             customDraw, rangeErrors);
  }
}
'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected fragment once, found {count}")
    return text.replace(old, new, 1)


def ensure_include(text: str, include: str, anchor: str, label: str) -> str:
    if include in text:
        return text
    at = text.find(anchor)
    if at < 0:
        raise RuntimeError(f"{label}: include anchor missing")
    return text[:at] + include + text[at:]


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {
        "packet": source / FRAME_PACKET,
        "recording": source / RECORDING_CPP,
        "encoding_h": source / ENCODING_H,
        "encoding_cpp": source / ENCODING_CPP,
        "frame_h": source / FRAME_H,
        "frame_cpp": source / FRAME_CPP,
        "aurora": source / AURORA_CPP,
    }
    text = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    original = dict(text)

    prerequisites = (
        ("packet", "struct M14ReplayPhase {"),
        ("packet", "m14ReplayOriginalUniformBytes"),
        ("recording", "void m14_prepare_interpolation_replay() noexcept"),
        ("frame_h", "FramePacket* m14_end_frame_packet() noexcept;"),
        ("frame_cpp", "g_m14EndFramePacket = &packet;"),
        ("aurora", "m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)"),
    )
    missing = [needle for name, needle in prerequisites if needle not in text[name]]
    if missing:
        raise RuntimeError("dev140b prerequisites missing: " + ", ".join(missing))

    # Tag recorded structures explicitly instead of guessing EFB passes or GX
    # payload types later from labels/function-pointer layout.
    if "bool m14ReplayGX = false;" not in text["packet"]:
        text["packet"] = replace_once(
            text["packet"],
            "  alignas(std::max_align_t) std::array<std::byte, InlineDrawPayloadSize> payload{};\n};\n\nenum class CommandType",
            "  alignas(std::max_align_t) std::array<std::byte, InlineDrawPayloadSize> payload{};\n"
            "  bool m14ReplayGX = false;\n};\n\nenum class CommandType",
            "dev140b DrawCommand tag")
    if "bool m14ReplayEfb = false;" not in text["packet"]:
        text["packet"] = replace_once(
            text["packet"], "  bool sealed = false;\n",
            "  bool sealed = false;\n  bool m14ReplayEfb = false;\n",
            "dev140b RenderPass EFB tag")

    efb_mark = "  pass.m14ReplayEfb = true;\n"
    if efb_mark not in text["recording"]:
        text["recording"] = replace_once(
            text["recording"],
            "void set_efb_targets(RenderPass& pass) {\n",
            "void set_efb_targets(RenderPass& pass) {\n" + efb_mark,
            "dev140b EFB tag assignment")

    # Preserve the EFB marker across the two pass-reconstruction paths.
    resume_marker = "      .m14ReplayEfb = prevPass.m14ReplayEfb,\n"
    if text["recording"].count(resume_marker) < 2:
        initializer_anchor = "      .hasStencil = prevPass.hasStencil,\n"
        needed = 2 - text["recording"].count(resume_marker)
        for _ in range(needed):
            at = text["recording"].find(initializer_anchor)
            while at >= 0:
                end = at + len(initializer_anchor)
                if not text["recording"].startswith(resume_marker, end):
                    text["recording"] = text["recording"][:end] + resume_marker + text["recording"][end:]
                    break
                at = text["recording"].find(initializer_anchor, end)
            else:
                raise RuntimeError("dev140b EFB pass reconstruction anchor missing")

    old_gx = """template <>
void push_draw_command(gx::DrawData data) {
  push_draw_command(make_draw_command<gx::render>(data));
}
"""
    new_gx = """template <>
void push_draw_command(gx::DrawData data) {
  auto command = make_draw_command<gx::render>(data);
  command.m14ReplayGX = true;
  push_draw_command(command);
}
"""
    if new_gx not in text["recording"] and "detail::m14_push_gx_draw_command(data, 16u, false);" not in text["recording"]:
        text["recording"] = replace_once(
            text["recording"], old_gx, new_gx, "dev140b GX draw tag")

    decl_marker = "struct M14ReplayEncodeStats {"
    decl_end_anchor = "void encode_op"
    canonical_decl = ENCODING_DECL.strip("\n") + "\n\n"
    if decl_marker not in text["encoding_h"]:
        text["encoding_h"] = replace_once(
            text["encoding_h"],
            "namespace detail {\n",
            "namespace detail {\n" + canonical_decl,
            "dev140b encoding declaration")
    else:
        decl_start = text["encoding_h"].find(decl_marker)
        decl_end = text["encoding_h"].find(decl_end_anchor, decl_start)
        if decl_end < 0:
            raise RuntimeError("dev140b stale declaration migration end anchor missing")
        text["encoding_h"] = (
            text["encoding_h"][:decl_start] +
            canonical_decl +
            text["encoding_h"][decl_end:]
        )

    impl_marker = "bool m14_encode_interpolation_replay("
    impl_end_anchor = "} // namespace detail\n\nbool bind_pipeline"
    canonical_impl = ENCODING_IMPL.strip("\n") + "\n"
    impl_start = text["encoding_cpp"].find(impl_marker)
    impl_end = text["encoding_cpp"].find(impl_end_anchor)
    if impl_end < 0:
        raise RuntimeError("dev140b replay encoder end anchor missing")
    if impl_start < 0:
        text["encoding_cpp"] = (
            text["encoding_cpp"][:impl_end] +
            canonical_impl +
            text["encoding_cpp"][impl_end:]
        )
    else:
        if impl_start > impl_end:
            raise RuntimeError("dev140b stale replay encoder migration ordering invalid")
        text["encoding_cpp"] = (
            text["encoding_cpp"][:impl_start] +
            canonical_impl +
            text["encoding_cpp"][impl_end:]
        )

    text["aurora"] = ensure_include(
        text["aurora"], '#include "gfx/encoding.hpp"\n',
        '#include "gfx/resources.hpp"\n', "dev140b encoding include")
    text["aurora"] = ensure_include(
        text["aurora"], "#include <cstdlib>\n",
        "#include <magic_enum.hpp>\n", "dev140b cstdlib include")
    text["aurora"] = ensure_include(
        text["aurora"], "#include <algorithm>\n",
        "#include <magic_enum.hpp>\n", "dev140b algorithm include")
    text["aurora"] = ensure_include(
        text["aurora"], "#include <array>\n",
        "#include <magic_enum.hpp>\n", "dev140b array include")

    state_marker = "struct M14ReplayEncodeTarget {"
    init_marker = "AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {"
    canonical_state = AURORA_STATE.strip("\n") + "\n\n"
    init = text["aurora"].find(init_marker)
    if init < 0:
        raise RuntimeError("dev140b Aurora state insertion anchor missing")
    state_start = text["aurora"].find(state_marker)
    if state_start < 0:
        text["aurora"] = text["aurora"][:init] + canonical_state + text["aurora"][init:]
    else:
        if state_start > init:
            raise RuntimeError("dev140b stale Aurora state migration ordering invalid")
        text["aurora"] = (
            text["aurora"][:state_start] +
            canonical_state +
            text["aurora"][init:]
        )

    call = "      m14_encode_interpolation_replay_proof(encoder);\n"
    if call not in text["aurora"]:
        anchor = (
            "      const uint32_t m14PresenterSlot =\n"
            "          m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot);\n"
        )
        text["aurora"] = replace_once(
            text["aurora"], anchor, call + anchor,
            "dev140b replay proof callback")

    # Release proof-only render targets before the WebGPU device is destroyed.
    reset_call = "  m14_reset_replay_encode_targets();\n"
    if reset_call not in text["aurora"]:
        anchor = "  imgui::shutdown();\n  gfx::shutdown();\n"
        text["aurora"] = replace_once(
            text["aurora"], anchor,
            "  imgui::shutdown();\n" + reset_call + "  gfx::shutdown();\n",
            "dev140b replay target shutdown")

    combined = "\n".join(text.values())
    required = (
        SIGNATURE,
        'std::getenv("OPENSPM_M14_INTERP_REPLAY_ENCODE")',
        "bool m14ReplayGX = false;",
        "bool m14ReplayEfb = false;",
        "command.m14ReplayGX = true;",
        "pass.m14ReplayEfb = true;",
        "M14ReplayEncodeStats",
        "m14_encode_interpolation_replay(",
        "authoritative_efb_untouched=1",
        "m14_encode_interpolation_replay_proof(encoder);",
        "m14_reset_replay_encode_targets();",
        "reject_prior_load={}",
        "efb_copy_passes={}",
        "encoder.CopyBufferToBuffer(",
        "staging high-water marks",
        '#include "gfx/encoding.hpp"',
    )
    missing = [item for item in required if item not in combined]
    if missing:
        raise RuntimeError("dev140b migration incomplete: " + ", ".join(missing))

    if text["packet"].count("bool m14ReplayGX = false;") != 1:
        raise RuntimeError("dev140b GX draw tag is not unique")
    if text["packet"].count("bool m14ReplayEfb = false;") != 1:
        raise RuntimeError("dev140b EFB pass tag is not unique")
    expected_gx_tags = 1 if "detail::m14_push_gx_draw_command(data, 16u, false);" in text["recording"] else (2 if "void m14_push_gx_draw_command(" in text["recording"] else 1)
    if text["recording"].count("command.m14ReplayGX = true;") != expected_gx_tags:
        raise RuntimeError("dev140b GX tag assignment is not unique")
    if text["recording"].count("pass.m14ReplayEfb = true;") != 1:
        raise RuntimeError("dev140b base EFB tag assignment is not unique")
    if text["recording"].count(".m14ReplayEfb = prevPass.m14ReplayEfb,") < 2:
        raise RuntimeError("dev140b EFB marker is not preserved across pass reconstruction")

    if check_only:
        return "verified"
    changed = False
    for name, path in paths.items():
        if text[name] != original[name]:
            path.write_text(text[name], encoding="utf-8", newline="\n")
            changed = True
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=args.source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {args.source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3
    try:
        result = transform(args.source.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora dev140b replay-encode adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "verified": "Aurora dev140b replay-encode proof verified",
        "applied": "Aurora dev140b replay-encode proof applied",
        "already-applied": "Aurora dev140b replay-encode proof already applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
