#!/usr/bin/env python3
"""Add the OpenSPM dev139 decoupled host-presenter proof to pinned Aurora.

dev138 established two prerequisites:
- the authoritative 60 Hz producer can again sustain roughly 59-61 completed
  frames/s in heavy gameplay; and
- frame-to-frame geometry/material identity is stable enough to proceed toward
  retained interpolation.

dev139 deliberately does *not* interpolate geometry yet. It proves the other
half of the architecture: surface presentation can run independently of the
60 Hz producer.

When OPENSPM_M14_ASYNC_PRESENTER=1:
- aurora_end_frame submits the authoritative scene/XFB work but does not acquire
  or present the swapchain;
- the copy-time Wii XFB snapshot is copied into a two-slot immutable presenter
  ring before publication;
- one lightweight cadence thread schedules presentation at
  OPENSPM_INTERPOLATION_TARGET_FPS (100 in the proof launcher), while Aurora's
  existing render worker remains the sole owner of WebGPU surface/device work;
- repeated presentation of the latest retained image is expected. Motion still
  reflects 60 Hz poses; this is a cadence/lifetime proof, not the final visual
  interpolation feature.

The two-slot retained ring avoids sampling a copy-texture cache allocation while
the producer rewrites it on a later GXCopyDisp. Producer copies and retained
presentation execute on Aurora's render worker, preserving the backend's native
WebGPU thread/ordering contract while the cadence timer remains independent.

This transform runs after the M14 display-copy snapshot and host-present pacing
patches. It is exact/idempotent and fails closed on unexpected drift.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
TARGET = pathlib.Path("lib/aurora.cpp")
TEXTURE_TARGET = pathlib.Path("lib/gfx/texture.cpp")
SIGNATURE = "M14 dev139 decoupled host presenter active"
TEXTURE_COPY_SRC_OLD = (
    "wgpu::TextureUsage::TextureBinding | wgpu::TextureUsage::CopyDst | "
    "wgpu::TextureUsage::RenderAttachment,"
)
TEXTURE_COPY_SRC_NEW = (
    "wgpu::TextureUsage::TextureBinding | wgpu::TextureUsage::CopySrc | "
    "wgpu::TextureUsage::CopyDst | wgpu::TextureUsage::RenderAttachment,"
)

INCLUDES = (
    "#include <chrono>\n",
    "#include <condition_variable>\n",
    "#include <cstdlib>\n",
    "#include <mutex>\n",
    "#include <thread>\n",
)

STATE = r'''
#ifdef AURORA_ENABLE_GX
struct M14PresenterImage {
  wgpu::Texture texture;
  wgpu::TextureView view;
  wgpu::Extent3D size{};
  wgpu::TextureFormat format = wgpu::TextureFormat::Undefined;
};

std::mutex g_m14PresenterMutex;
std::mutex g_m14PresenterQueueMutex;
std::condition_variable g_m14PresenterCv;
std::thread g_m14PresenterThread;
std::array<M14PresenterImage, 2> g_m14PresenterImages;
uint64_t g_m14PresenterGeneration = 0;
uint64_t g_m14PresenterPresentCount = 0;
uint32_t g_m14PresenterLatestSlot = 0;
bool g_m14PresenterHaveImage = false;
bool g_m14PresenterStop = false;
bool g_m14PresenterWorkQueued = false;

bool m14_async_presenter_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_ASYNC_PRESENTER");
    return value != nullptr && value[0] == '1' && value[1] == '\0';
  }();
  return enabled;
}

uint32_t m14_async_presenter_hz() noexcept {
  static const uint32_t hz = [] {
    const char* value = std::getenv("OPENSPM_INTERPOLATION_TARGET_FPS");
    if (value == nullptr || value[0] == '\0') return 100u;
    char* end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    if (end == value || *end != '\0' || parsed <= 60ul) return 100u;
    return static_cast<uint32_t>(std::clamp<unsigned long>(parsed, 61ul, 360ul));
  }();
  return hz;
}

void m14_ensure_presenter_image(uint32_t slot, const gfx::TextureHandle& source) {
  auto& image = g_m14PresenterImages[slot];
  if (image.texture && image.size.width == source->size.width &&
      image.size.height == source->size.height && image.format == source->format) {
    return;
  }

  const wgpu::TextureDescriptor descriptor{
      .label = slot == 0 ? "OpenSPM dev139 presenter image A" : "OpenSPM dev139 presenter image B",
      .usage = wgpu::TextureUsage::CopyDst | wgpu::TextureUsage::TextureBinding,
      .dimension = wgpu::TextureDimension::e2D,
      .size = source->size,
      .format = source->format,
      .mipLevelCount = 1,
      .sampleCount = 1,
  };
  image.texture = g_device.CreateTexture(&descriptor);
  image.view = image.texture.CreateView();
  image.size = source->size;
  image.format = source->format;
}

uint32_t m14_encode_presenter_snapshot(wgpu::CommandEncoder& encoder,
                                       const gfx::TextureHandle& source) {
  std::lock_guard lock{g_m14PresenterMutex};
  const uint32_t slot = static_cast<uint32_t>((g_m14PresenterGeneration + 1u) & 1u);
  m14_ensure_presenter_image(slot, source);
  const wgpu::TexelCopyTextureInfo src{.texture = source->texture};
  const wgpu::TexelCopyTextureInfo dst{.texture = g_m14PresenterImages[slot].texture};
  encoder.CopyTextureToTexture(&src, &dst, &source->size);
  return slot;
}

struct M14PresenterViewportAdjustment {
  bool active = false;
  float sourceX = 0.0f;
  float sourceY = 0.0f;
  float alpha = 1.0f;
};
M14PresenterViewportAdjustment m14_presenter_viewport_adjustment(
    uint32_t sourceWidth, uint32_t sourceHeight) noexcept;

bool m14_present_retained_image(const M14PresenterImage& image) {
  AURORA_ASSERT(gfx::render_worker::is_worker_thread(),
                "OpenSPM dev139 retained presenter must run on Aurora render worker");
  if (!image.texture || !image.view || !window::is_presentable() || !g_surface) {
    return false;
  }

  wgpu::Texture currentTexture;
  wgpu::TextureView currentView;
  {
    window::SurfaceLock surfaceLock;
    if (!window::is_presentable() || !g_surface) return false;
    wgpu::SurfaceTexture surfaceTexture;
    g_surface.GetCurrentTexture(&surfaceTexture);
    if (surfaceTexture.status != wgpu::SurfaceGetCurrentTextureStatus::SuccessOptimal) {
      return false;
    }
    currentTexture = std::move(surfaceTexture.texture);
    currentView = currentTexture.CreateView();
  }

  const auto viewport =
      webgpu::calculate_present_viewport(webgpu::g_graphicsConfig.surfaceConfiguration.width,
                                         webgpu::g_graphicsConfig.surfaceConfiguration.height,
                                         image.size.width, image.size.height);
  const auto adjustment =
      m14_presenter_viewport_adjustment(image.size.width, image.size.height);
  auto shiftedViewport = viewport;
  if (adjustment.active && image.size.width != 0u && image.size.height != 0u) {
    shiftedViewport.left += adjustment.sourceX *
                            (viewport.width / static_cast<float>(image.size.width));
    shiftedViewport.top += adjustment.sourceY *
                           (viewport.height / static_cast<float>(image.size.height));
  }

  M14PresenterImage previousImage;
  if (adjustment.active) {
    std::lock_guard lock{g_m14PresenterMutex};
    if (g_m14PresenterGeneration >= 2u) {
      previousImage = g_m14PresenterImages[g_m14PresenterLatestSlot ^ 1u];
    }
  }
  const wgpu::CommandEncoderDescriptor encoderDescriptor{
      .label = "OpenSPM dev139 presenter encoder",
  };
  auto encoder = g_device.CreateCommandEncoder(&encoderDescriptor);

  const webgpu::TextureWithSampler retainedSource{
      .texture = image.texture,
      .view = image.view,
      .size = image.size,
      .format = image.format,
      .sampler = webgpu::g_frameBuffer.sampler,
  };
  const auto presentBindGroup = webgpu::create_copy_bind_group(retainedSource);
  const std::array attachments{
      wgpu::RenderPassColorAttachment{
          .view = currentView,
          .loadOp = wgpu::LoadOp::Clear,
          .storeOp = wgpu::StoreOp::Store,
      },
  };
  const wgpu::RenderPassDescriptor passDescriptor{
      .label = "OpenSPM dev139 retained XFB present",
      .colorAttachmentCount = attachments.size(),
      .colorAttachments = attachments.data(),
  };
  const auto pass = encoder.BeginRenderPass(&passDescriptor);
  pass.SetPipeline(webgpu::g_CopyPipeline);
  if (adjustment.active && previousImage.texture && previousImage.view) {
    const webgpu::TextureWithSampler previousSource{
        .texture = previousImage.texture,
        .view = previousImage.view,
        .size = previousImage.size,
        .format = previousImage.format,
        .sampler = webgpu::g_frameBuffer.sampler,
    };
    const auto previousBindGroup = webgpu::create_copy_bind_group(previousSource);
    pass.SetBindGroup(0, previousBindGroup, 0, nullptr);
    set_present_viewport(pass, viewport, webgpu::g_graphicsConfig.surfaceConfiguration.width,
                         webgpu::g_graphicsConfig.surfaceConfiguration.height);
    pass.Draw(3);
  }
  pass.SetBindGroup(0, presentBindGroup, 0, nullptr);
  set_present_viewport(pass, shiftedViewport, webgpu::g_graphicsConfig.surfaceConfiguration.width,
                       webgpu::g_graphicsConfig.surfaceConfiguration.height);
  pass.Draw(3);
  pass.End();

  const wgpu::CommandBufferDescriptor cmdDescriptor{
      .label = "OpenSPM dev139 presenter command buffer",
  };
  const auto buffer = encoder.Finish(&cmdDescriptor);
  {
    std::lock_guard queueLock{g_m14PresenterQueueMutex};
    g_queue.Submit(1, &buffer);
  }

  wgpu::ConvertibleStatus presentStatus = wgpu::Status::Error;
  std::chrono::steady_clock::time_point presentStart;
  std::chrono::steady_clock::time_point presentEnd;
  {
    window::SurfaceLock surfaceLock;
    if (window::is_presentable() && g_surface) {
      presentStart = std::chrono::steady_clock::now();
      presentStatus = g_surface.Present();
      presentEnd = std::chrono::steady_clock::now();
    }
  }
  if (!presentStatus) {
    return false;
  }
  m14_record_successful_present(presentStart, presentEnd);
  gfx::after_present();

  uint64_t presentCount = 0;
  uint64_t sourcePublishes = 0;
  {
    std::lock_guard lock{g_m14PresenterMutex};
    presentCount = ++g_m14PresenterPresentCount;
    sourcePublishes = g_m14PresenterGeneration;
  }
  if (presentCount != 0u && (presentCount & (presentCount - 1u)) == 0u) {
    Log.info("M14 dev139 presenter cadence presents={} source_publishes={} repeats={} target_hz={}",
             presentCount, sourcePublishes,
             presentCount > sourcePublishes ? presentCount - sourcePublishes : 0u,
             m14_async_presenter_hz());
  }
  return true;
}

void m14_presenter_main() {
  const uint32_t hz = m14_async_presenter_hz();
  const auto period = std::chrono::nanoseconds{
      static_cast<int64_t>(1'000'000'000ull / static_cast<uint64_t>(hz))};
  auto nextPresent = std::chrono::steady_clock::now();

  Log.info("M14 dev139 decoupled host presenter active: target_hz={} source=retained-wii-xfb "
           "simulation_hz=60 interpolation=off webgpu_thread=render-worker", hz);

  while (true) {
    {
      std::unique_lock lock{g_m14PresenterMutex};
      g_m14PresenterCv.wait(lock, [] {
        return g_m14PresenterStop || g_m14PresenterHaveImage;
      });
      if (g_m14PresenterStop) break;
    }

    const auto now = std::chrono::steady_clock::now();
    if (now < nextPresent) {
      std::this_thread::sleep_until(nextPresent);
    } else if (now - nextPresent > period * 2) {
      nextPresent = now;
    }

    bool queuePresent = false;
    {
      std::lock_guard lock{g_m14PresenterMutex};
      if (g_m14PresenterStop) break;
      if (!g_m14PresenterWorkQueued) {
        g_m14PresenterWorkQueued = true;
        queuePresent = true;
      }
    }

    if (queuePresent) {
      // The cadence thread never touches Dawn/WebGPU. Aurora's established
      // render worker owns surface acquisition, command encoding, queue submit,
      // Present(), and all referenced WebGPU object lifetimes.
      gfx::render_worker::enqueue_work([] {
        M14PresenterImage image;
        {
          std::lock_guard lock{g_m14PresenterMutex};
          if (g_m14PresenterStop || !g_m14PresenterHaveImage) {
            g_m14PresenterWorkQueued = false;
            return;
          }
          image = g_m14PresenterImages[g_m14PresenterLatestSlot];
        }

        (void)m14_present_retained_image(image);

        {
          std::lock_guard lock{g_m14PresenterMutex};
          g_m14PresenterWorkQueued = false;
        }
      });
    }
    nextPresent += period;
  }
}

void m14_start_async_presenter_locked() {
  if (g_m14PresenterThread.joinable()) return;
  g_m14PresenterStop = false;
  g_m14PresenterThread = std::thread{m14_presenter_main};
}

void m14_publish_presenter_slot(uint32_t slot) {
  if (!m14_async_presenter_enabled()) return;
  {
    std::lock_guard lock{g_m14PresenterMutex};
    g_m14PresenterLatestSlot = slot;
    ++g_m14PresenterGeneration;
    g_m14PresenterHaveImage = true;
    m14_start_async_presenter_locked();
  }
  g_m14PresenterCv.notify_all();
}

void m14_stop_async_presenter() {
  {
    std::lock_guard lock{g_m14PresenterMutex};
    g_m14PresenterStop = true;
  }
  g_m14PresenterCv.notify_all();
  if (g_m14PresenterThread.joinable()) {
    g_m14PresenterThread.join();
  }
  std::lock_guard lock{g_m14PresenterMutex};
  if (g_m14PresenterGeneration != 0u || g_m14PresenterPresentCount != 0u) {
    Log.info("M14 dev139 presenter summary source_publishes={} host_presents={} target_hz={}",
             g_m14PresenterGeneration, g_m14PresenterPresentCount,
             m14_async_presenter_hz());
  }
  g_m14PresenterHaveImage = false;
  g_m14PresenterImages = {};
  g_m14PresenterGeneration = 0;
  g_m14PresenterPresentCount = 0;
  g_m14PresenterLatestSlot = 0;
}
#endif
'''

CALLBACK_INSERT = r'''    if (m14_async_presenter_enabled() && openspmDisplayCopySnapshot) {
      // dev139 cadence/lifetime proof: submit the authoritative producer work,
      // including a copy of the Wii XFB snapshot into an immutable ping-pong
      // presenter slot. The cadence thread only schedules extra presents; all
      // swapchain/WebGPU work stays on Aurora's render worker. No interpolated
      // geometry exists yet.
      const uint32_t m14PresenterSlot =
          m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot);
      webgpu::gpu_prof::frame_end(encoder);
      const wgpu::CommandBufferDescriptor m14CmdDescriptor{
          .label = "OpenSPM dev139 producer command buffer",
      };
      const auto m14Buffer = encoder.Finish(&m14CmdDescriptor);
      {
        ZoneScopedN("Queue Submit");
        std::lock_guard queueLock{g_m14PresenterQueueMutex};
        g_queue.Submit(1, &m14Buffer);
      }
      webgpu::gpu_prof::after_submit();
      for (auto& callback : afterSubmitCallbacks) {
        if (callback) callback();
      }
      gfx::after_submit();
      m14_publish_presenter_slot(m14PresenterSlot);
      return;
    }

'''

def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def ensure_include(text: str, include: str) -> str:
    if include in text:
        return text
    anchor = "#include <magic_enum.hpp>\n"
    at = text.find(anchor)
    if at < 0:
        raise RuntimeError(f"include anchor missing for {include.strip()}")
    return text[:at] + include + text[at:]


def braced_region(text: str, marker: str) -> tuple[int, int]:
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"marker missing: {marker}")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"opening brace missing: {marker}")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise RuntimeError(f"unbalanced braces: {marker}")


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    texture_path = source / TEXTURE_TARGET
    text = path.read_text(encoding="utf-8")
    texture_text = texture_path.read_text(encoding="utf-8")
    original = text
    original_texture = texture_text

    if TEXTURE_COPY_SRC_NEW not in texture_text:
        count = texture_text.count(TEXTURE_COPY_SRC_OLD)
        if count != 1:
            raise RuntimeError(
                f"dev139 render-texture CopySrc anchor expected once, found {count}"
            )
        texture_text = texture_text.replace(
            TEXTURE_COPY_SRC_OLD, TEXTURE_COPY_SRC_NEW, 1
        )

    prerequisites = (
        "OpenSPM M14 Wii display-copy XFB snapshot active",
        "kM14PresentPacingBinUs",
        "openspmDisplayCopySnapshot",
        "m14_record_successful_present",
    )
    missing = [item for item in prerequisites if item not in text]
    if missing:
        raise RuntimeError("dev139 prerequisites missing: " + ", ".join(missing))

    for include in INCLUDES:
        text = ensure_include(text, include)

    # Detect installation by durable state, not by SIGNATURE: the signature
    # intentionally lives inside m14_presenter_main(), which dev139b replaces
    # during migration. Using it as the install sentinel would insert a second
    # STATE block whenever that function is stale or partially migrated.
    state_sentinel = "struct M14PresenterImage {"
    if state_sentinel not in text:
        init = text.find("AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {")
        if init < 0:
            raise RuntimeError("dev139 state insertion anchor missing")
        text = text[:init] + STATE + "\n" + text[init:]
    elif text.count(state_sentinel) != 1:
        raise RuntimeError(
            f"dev139 presenter state sentinel expected once, found {text.count(state_sentinel)}"
        )

    # dev139b: migrate the first proof's cross-thread WebGPU presenter in-place.
    # The cadence thread may exist independently, but all surface/device/encoder
    # work must execute on Aurora's established render worker. Canonicalize both
    # the retained-image presentation function and the cadence function: the
    # first dev139 proof can already be cached with either stale body.
    work_anchor = "bool g_m14PresenterStop = false;\n"
    work_decl = "bool g_m14PresenterWorkQueued = false;\n"
    if work_decl not in text:
        count = text.count(work_anchor)
        if count != 1:
            raise RuntimeError(
                f"dev139 presenter work-state anchor expected once, found {count}"
            )
        text = text.replace(work_anchor, work_anchor + work_decl, 1)

    viewport_decl = """struct M14PresenterViewportAdjustment {
  bool active = false;
  float sourceX = 0.0f;
  float sourceY = 0.0f;
  float alpha = 1.0f;
};
M14PresenterViewportAdjustment m14_presenter_viewport_adjustment(
    uint32_t sourceWidth, uint32_t sourceHeight) noexcept;

"""
    if "struct M14PresenterViewportAdjustment {" not in text:
        anchor = "bool m14_present_retained_image(const M14PresenterImage& image) {"
        count = text.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"dev143 presenter viewport hook anchor expected once, found {count}"
            )
        text = text.replace(anchor, viewport_decl + anchor, 1)

    # dev144 migration: cached dev143 Aurora trees already contain the viewport
    # hook struct, so the insertion guard above does not run. Canonicalize the
    # forward declaration separately before replacing the retained-present body.
    old_viewport_decl = (
        "M14PresenterViewportAdjustment m14_presenter_viewport_adjustment() noexcept;\n"
    )
    new_viewport_decl = (
        "M14PresenterViewportAdjustment m14_presenter_viewport_adjustment(\n"
        "    uint32_t sourceWidth, uint32_t sourceHeight) noexcept;\n"
    )
    if old_viewport_decl in text:
        count = text.count(old_viewport_decl)
        if count != 1:
            raise RuntimeError(
                f"dev144 stale viewport declaration expected once, found {count}"
            )
        text = text.replace(old_viewport_decl, new_viewport_decl, 1)
    elif new_viewport_decl not in text:
        raise RuntimeError("dev144 presenter viewport declaration missing")

    state_retained_start = STATE.find(
        "bool m14_present_retained_image(const M14PresenterImage& image) {"
    )
    state_retained_end = STATE.find(
        "\n\nvoid m14_presenter_main()", state_retained_start
    )
    if state_retained_start < 0 or state_retained_end < 0:
        raise RuntimeError("dev139 canonical retained-present body missing")
    canonical_retained = STATE[state_retained_start:state_retained_end]
    retained_start, retained_end = braced_region(
        text, "bool m14_present_retained_image(const M14PresenterImage& image)"
    )
    if text[retained_start:retained_end] != canonical_retained:
        text = text[:retained_start] + canonical_retained + text[retained_end:]

    state_presenter_start = STATE.find("void m14_presenter_main() {")
    state_presenter_end = STATE.find(
        "\n\nvoid m14_start_async_presenter_locked()", state_presenter_start
    )
    if state_presenter_start < 0 or state_presenter_end < 0:
        raise RuntimeError("dev139 canonical presenter body missing")
    canonical_presenter = STATE[state_presenter_start:state_presenter_end]
    presenter_start, presenter_end = braced_region(text, "void m14_presenter_main()")
    if text[presenter_start:presenter_end] != canonical_presenter:
        text = text[:presenter_start] + canonical_presenter + text[presenter_end:]

    callback_marker = (
        "                     wgpu::CommandEncoder& encoder, "
        "std::vector<gfx::AfterSubmitCallback> afterSubmitCallbacks) {\n"
    )
    callback_at = text.find(callback_marker)
    if callback_at < 0:
        raise RuntimeError("dev139 end-frame callback anchor missing")
    body_at = callback_at + len(callback_marker)
    if "m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)" not in text[body_at:body_at + 5000]:
        text = text[:body_at] + CALLBACK_INSERT + text[body_at:]

    shutdown_base = (
        "  gx::fifo::shutdown();\n"
        "  gfx::render_worker::synchronize();\n"
        '  m14_log_present_pacing("shutdown");\n'
    )
    shutdown_legacy = (
        "  gx::fifo::shutdown();\n"
        "  gfx::render_worker::synchronize();\n"
        "  m14_stop_async_presenter();\n"
        '  m14_log_present_pacing("shutdown");\n'
    )
    shutdown_safe = (
        "  gx::fifo::shutdown();\n"
        "  m14_stop_async_presenter();\n"
        "  gfx::render_worker::synchronize();\n"
        '  m14_log_present_pacing("shutdown");\n'
    )
    if shutdown_safe not in text:
        candidates = [
            candidate
            for candidate in (shutdown_legacy, shutdown_base)
            if text.count(candidate) == 1
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                "dev139 shutdown anchor expected exactly one legacy/base shape"
            )
        text = text.replace(candidates[0], shutdown_safe, 1)

    required = (
        SIGNATURE,
        'std::getenv("OPENSPM_M14_ASYNC_PRESENTER")',
        'std::getenv("OPENSPM_INTERPOLATION_TARGET_FPS")',
        "m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)",
        "m14_publish_presenter_slot(m14PresenterSlot);",
        "m14_stop_async_presenter();",
        "g_m14PresenterQueueMutex",
        "g_m14PresenterWorkQueued",
        "gfx::render_worker::enqueue_work",
        "OpenSPM dev139 retained presenter must run on Aurora render worker",
        "webgpu_thread=render-worker",
        "M14 dev139 presenter cadence presents={}",
        "M14 dev139 presenter summary source_publishes={}",
        "OpenSPM dev139 retained XFB present",
        "M14PresenterViewportAdjustment",
        "m14_presenter_viewport_adjustment",
        "previousImage = g_m14PresenterImages[g_m14PresenterLatestSlot ^ 1u]",
        "shiftedViewport.left +=",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError("dev139 migration incomplete: " + ", ".join(missing))

    if text.count("struct M14PresenterImage {") != 1:
        raise RuntimeError("dev139 presenter state block is not unique")
    if text.count("bool m14_present_retained_image(const M14PresenterImage& image)") != 1:
        raise RuntimeError("dev139 retained-present function is not unique")
    if text.count("void m14_presenter_main()") != 1:
        raise RuntimeError("dev139 presenter main is not unique")
    if text.count("m14_stop_async_presenter();") != 1:
        raise RuntimeError("dev139 shutdown hook is not unique")
    if text.count("m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)") != 1:
        raise RuntimeError("dev139 producer branch is not unique")
    if TEXTURE_COPY_SRC_NEW not in texture_text:
        raise RuntimeError("dev139 retained XFB source is not CopySrc-capable")

    changed = text != original or texture_text != original_texture
    if not check_only:
        if text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
        if texture_text != original_texture:
            texture_path.write_text(texture_text, encoding="utf-8", newline="\n")
    if check_only:
        return "verified"
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser()
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
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora dev139 presenter adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "verified": "Aurora dev139 decoupled presenter verified",
        "applied": "Aurora dev139 decoupled presenter applied",
        "already-applied": "Aurora dev139 decoupled presenter already applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
