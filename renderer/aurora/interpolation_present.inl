// Render-worker-only visible replay queue. Every queued image owns its texture.
using M14InterpClock = std::chrono::steady_clock;
struct M14TimedImage {
  M14PresenterImage image;
  M14InterpClock::time_point due;
  float alpha = 1.0f;
};
std::deque<M14TimedImage> g_m14InterpQueue;
std::vector<M14TimedImage> g_m14InterpPending;
M14PresenterImage g_m14InterpLast;
M14InterpClock::time_point g_m14InterpNext{};
M14InterpClock::time_point g_m14InterpLastSource{};
uint32_t g_m14InterpHealthySourceFrames = 0;
uint64_t g_m14InterpAccepted = 0, g_m14InterpFallback = 0;
uint64_t g_m14InterpPartialTransformFallback = 0;
uint64_t g_m14InterpSourceCadenceFallback = 0;
bool g_m14InterpSafetyReported = false;
bool g_m14CameraPresentValid = false;
float g_m14CameraPresentNdcDx = 0.0f;
float g_m14CameraPresentNdcDy = 0.0f;
M14InterpClock::time_point g_m14CameraPresentStart{};
std::chrono::microseconds g_m14CameraPresentDuration{16667};
uint64_t g_m14CameraPresentSamples = 0;
uint64_t g_m14CameraPresentUpdates = 0;

bool m14_visible_interpolation() {
  static const bool enabled = [] {
    const char* p = std::getenv("OPENSPM_M14_INTERP_PRESENT");
    return p && p[0] == '1';
  }();
  return enabled;
}
bool m14_identity_interpolation() {
  static const bool enabled = [] {
    const char* p = std::getenv("OPENSPM_M14_INTERP_IDENTITY");
    return p && p[0] == '1';
  }();
  return enabled;
}
void m14_clear_interpolation_queue() {
  g_m14InterpQueue.clear();
  g_m14InterpPending.clear();
  g_m14InterpLast = {};
  g_m14InterpNext = {};
}
M14PresenterViewportAdjustment m14_presenter_viewport_adjustment(
    uint32_t, uint32_t) noexcept {
  // dev145: final-XFB translation is intentionally disabled. Camera motion is
  // synthesized inside replayed world draws so HUD/screen-space geometry stays
  // fixed and the completed game image never wobbles as one slab.
  return {};
}

M14PresenterImage m14_choose_interpolation_image(M14PresenterImage fallback) {
  if (!m14_visible_interpolation()) return fallback;
  const auto now = M14InterpClock::now();
  bool advanced = false;
  float selectedAlpha = 1.0f;
  while (!g_m14InterpQueue.empty() && g_m14InterpQueue.front().due <= now) {
    advanced = true;
    selectedAlpha = g_m14InterpQueue.front().alpha;
    g_m14InterpLast = std::move(g_m14InterpQueue.front().image);
    g_m14InterpQueue.pop_front();
  }
  if (advanced) {
    static uint64_t selected = 0, fractional = 0;
    ++selected;
    if (!m14_identity_interpolation() && selectedAlpha > 0.0f && selectedAlpha < 1.0f) ++fractional;
    if (selected <= 8 || (selected & (selected - 1)) == 0)
      Log.info("M14 interpolated image selected={} fractional={} alpha={} queued={}",
               selected, fractional, selectedAlpha, g_m14InterpQueue.size());
  }
  return g_m14InterpLast.texture ? g_m14InterpLast : fallback;
}

// Only replay packets for which replaying draws at packet end cannot observe
// a later mutation of a sampled resource. Effects/readback splits fall back.
const char* m14_preflight_visible(gfx::detail::FramePacket& frame,
                                  const gfx::TextureHandle& display,
                                  uint32_t& lastPass) {
  if (frame.m14ReplayPhases.empty()) return "no-phases";
  // dev145 normal mode publishes only camera-only world replay arenas and
  // reports snapped_uniforms=0. Legacy identity/object diagnostics retain the
  // older fail-closed partial-transform contract.
  if (!m14_identity_interpolation() &&
      frame.m14ReplayMatchedUniforms != 0u &&
      frame.m14ReplaySnappedUniforms != 0u)
    return "partial-transform";
  if (!frame.encoderTasks.empty() || !frame.textureCopies.empty()) return "resource-tasks";
  bool sawDraws = false, foundDisplay = false;
  for (const auto& op : frame.ops) {
    if (op.type != gfx::detail::FrameOpType::RenderPass || !op.renderPass) return "non-render-op";
    const auto& p = *op.renderPass;
    if (sawDraws && !op.textureUploads.empty()) return "late-texture-upload";
    if (!p.paletteConvs.empty()) return "palette-conversion";
    if (!p.m14ReplayEfb && p.hasDraws) return "offscreen-draws";
    if (p.resolveTarget && p.resolveTarget != display) return "efb-texture-copy";
    if (p.resolveTarget == display) {
      if (foundDisplay) return "multiple-display-copies";
      foundDisplay = true;
      lastPass = op.index;
    }
    if (p.hasDraws) sawDraws = true;
  }
  if (!foundDisplay || lastPass >= frame.renderPasses.size()) return "display-boundary-missing";
  for (uint32_t i = 0; i <= lastPass; ++i) {
    const auto& p = frame.renderPasses[i];
    if (!p.m14ReplayEfb || p.discardable) continue;
    for (const auto& command : p.commands) {
      if (command.type != gfx::detail::CommandType::Draw || !command.data.draw.m14ReplayGX) continue;
      gx::DrawData data{};
      std::memcpy(&data, command.data.draw.payload.data(), sizeof(data));
      if (uint64_t(data.uniformRange.offset) + data.uniformRange.size > frame.m14ReplayOriginalUniformBytes)
        return "uniform-range";
    }
  }
  return nullptr;
}

M14PresenterImage m14_copy_replayed_xfb(wgpu::CommandEncoder& encoder,
                                       const M14ReplayEncodeTarget& replay,
                                       const gfx::detail::RenderPass& copy,
                                       const gfx::TextureHandle& display) {
  const wgpu::TextureDescriptor desc{
      .label = "OpenSPM interpolated XFB",
      .usage = wgpu::TextureUsage::CopyDst | wgpu::TextureUsage::TextureBinding |
               wgpu::TextureUsage::RenderAttachment,
      .dimension = wgpu::TextureDimension::e2D,
      .size = display->size, .format = display->format,
      .mipLevelCount = 1, .sampleCount = 1,
  };
  auto texture = g_device.CreateTexture(&desc);
  if (!texture) return {};
  auto view = texture.CreateView();
  auto target = std::make_shared<gfx::TextureRef>(texture, view, view, display->size,
                                                display->format, 1u, display->gxFormat);
  const bool scaling = display->size.width != uint32_t(copy.resolveRect.width) ||
                       display->size.height != uint32_t(copy.resolveRect.height);
  const gfx::tex_copy_conv::ConvRequest request{
      .fmt = copy.resolveFormat, .srcView = replay.color.view,
      .uniformRange = copy.resolveUniformRange, .dst = target,
      .sampleFilter = scaling ? gfx::tex_copy_conv::SampleFilter::Linear :
                               gfx::tex_copy_conv::SampleFilter::Nearest,
  };
  if (gfx::tex_copy_conv::needs_conversion(copy.resolveFormat)) {
    gfx::tex_copy_conv::run(encoder, request);
  } else if (scaling) {
    gfx::tex_copy_conv::blit(encoder, request);
  } else {
    const wgpu::TexelCopyTextureInfo src{.texture = replay.color.texture,
      .origin = {uint32_t(copy.resolveRect.x), uint32_t(copy.resolveRect.y), 0}};
    const wgpu::TexelCopyTextureInfo dst{.texture = texture};
    encoder.CopyTextureToTexture(&src, &dst, &display->size);
  }
  return {texture, view, display->size, display->format};
}

void m14_encode_visible_interpolation(wgpu::CommandEncoder& encoder,
                                       const gfx::TextureHandle& display) {
  if (!m14_visible_interpolation()) return;
  if (!g_m14InterpSafetyReported) {
    Log.info("M14 dev145 world-camera replay interpolation active: final_xfb_shift=off object_interp=off world_projection=dominant camera_patch=all-pn-matrices");
    g_m14InterpSafetyReported = true;
  }
  g_m14InterpPending.clear();
  const auto sourceNow = M14InterpClock::now();
  int64_t sourceGapUs = -1;
  if (g_m14InterpLastSource.time_since_epoch().count() != 0) {
    sourceGapUs = std::chrono::duration_cast<std::chrono::microseconds>(
        sourceNow - g_m14InterpLastSource).count();
    // Keep source cadence as telemetry only. dev142 anchors each replay
    // interval to the source frame that actually produced it, so a late source
    // frame cannot accumulate an already-due synthetic backlog.
    if (sourceGapUs > 0 && sourceGapUs <= 20000) {
      g_m14InterpHealthySourceFrames =
          std::min<uint32_t>(g_m14InterpHealthySourceFrames + 1u, 120u);
    } else {
      g_m14InterpHealthySourceFrames = 0u;
    }
  }
  g_m14InterpLastSource = sourceNow;

  auto* frame = gfx::detail::m14_end_frame_packet();

  uint32_t lastPass = 0;
  const char* reason =
      frame ? m14_preflight_visible(*frame, display, lastPass) : "no-packet";
  if (!reason && (webgpu::g_graphicsConfig.msaaSamples != 1u ||
                   frame->m14ReplayPhases.size() > g_m14ReplayEncodeTargets.size())) reason = "unsupported-samples";
  if (!reason) {
    const auto& copy = frame->renderPasses[lastPass];
    if (gx::is_depth_format(copy.resolveFormat) ||
        copy.colorAttachments[gfx::SceneColorAttachmentIndex].format !=
            webgpu::g_graphicsConfig.surfaceConfiguration.format)
      reason = "copy-format";
  }
  if (!reason) {
    if (g_m14InterpLast.texture &&
        (g_m14InterpLast.size.width != display->size.width ||
         g_m14InterpLast.size.height != display->size.height)) m14_clear_interpolation_queue();
    frame->m14ReplayLastPass = lastPass;
    const auto& copy = frame->renderPasses[lastPass];
    const auto size = copy.colorAttachments[gfx::SceneColorAttachmentIndex].size;
    const auto now = M14InterpClock::now();
    // Start this adjacent-pose interval at the source frame's real arrival
    // time. This preserves the one-source-frame visual delay but never lets
    // variable producer cadence build a stale fixed-60-Hz deadline backlog.
    g_m14InterpQueue.clear();
    g_m14InterpNext = now;
    const auto& staging = gfx::detail::staging_buffer(frame->stagingBuffer);
    for (uint32_t i = 0; i < frame->m14ReplayPhases.size(); ++i) {
      const auto phase = frame->m14ReplayPhases[i];
      const uint32_t base = m14_identity_interpolation() ? 0u : phase.uniformBase;
      const uint64_t count = AURORA_ALIGN(uint64_t(frame->m14ReplayOriginalUniformBytes), 4u);
      if (uint64_t(base) + count > frame->uniforms.size()) { reason = "arena-range"; break; }
      if (!m14_identity_interpolation())
        encoder.CopyBufferToBuffer(staging, uint64_t(gfx::VertexBufferSize) + base,
                                    gfx::detail::resources().uniformBuffer, base, count);
      if (!m14_ensure_replay_encode_target(i, size.width, size.height)) { reason = "target"; break; }
      gfx::detail::M14ReplayEncodeStats stats{};
      if (!gfx::detail::m14_encode_interpolation_replay(encoder, *frame, base,
            g_m14ReplayEncodeTargets[i].color.view, g_m14ReplayEncodeTargets[i].depth.view, size, stats)) {
        reason = "pass-preflight"; break;
      }
      auto image = m14_copy_replayed_xfb(encoder, g_m14ReplayEncodeTargets[i], copy, display);
      if (!image.texture) { reason = "xfb-target"; break; }
      const auto offset = std::chrono::nanoseconds{int64_t(double(phase.alpha) * 16'666'667.0)};
      g_m14InterpPending.push_back({std::move(image), g_m14InterpNext + offset, phase.alpha});
    }
    frame->m14ReplayLastPass = UINT32_MAX;
  }
  if (reason) {
    if (std::strcmp(reason, "partial-transform") == 0)
      ++g_m14InterpPartialTransformFallback;
    else if (std::strcmp(reason, "source-cadence") == 0)
      ++g_m14InterpSourceCadenceFallback;
    m14_clear_interpolation_queue();
    ++g_m14InterpFallback;
  } else ++g_m14InterpAccepted;
  const auto n = g_m14InterpAccepted + g_m14InterpFallback;
  if (n <= 8 || (n & (n - 1)) == 0 || (reason && g_m14InterpFallback <= 8))
    Log.info("M14 visible interpolation frames={} accepted={} fallback={} reason={} phases={} identity={} simulation_hz=60 source_gap_us={} healthy_source={} partial_fallback={} cadence_fallback={}",
             n, g_m14InterpAccepted, g_m14InterpFallback, reason ? reason : "ok",
             g_m14InterpPending.size(), m14_identity_interpolation(), sourceGapUs,
             g_m14InterpHealthySourceFrames, g_m14InterpPartialTransformFallback,
             g_m14InterpSourceCadenceFallback);
}
void m14_publish_interpolation_images() {
  if (!m14_visible_interpolation()) return;
  // Called only after queue submission. Later worker presents are ordered
  // after these copies; no packet pointers or staging ranges leave the callback.
  for (auto& image : g_m14InterpPending) g_m14InterpQueue.push_back(std::move(image));
  g_m14InterpPending.clear();
  while (g_m14InterpQueue.size() > 8) g_m14InterpQueue.pop_front();
}
