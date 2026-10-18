// OpenSPM Stage C frame interpolation, Aurora glue. Embedded into Aurora's
// lib/aurora.cpp by apply_frame_interp_patch.py, after the Stage A present
// timeline, together with frame_interp.hpp (the pure model).
//
// Opt-in: OPENSPM_M14_INTERP=1 on top of the Stage A timeline in smooth mode
// (OPENSPM_M14_ASYNC_PRESENTER=1, OPENSPM_M14_PRESENT_TIMELINE=1) and the
// Stage B draw tags (OPENSPM_M14_DRAW_TAGS=1). Otherwise nothing here runs.
//
// Retain (render worker, end of every finished frame, in that frame's command
// encoder): the frame's replayable draw list up to the display copy, its exact
// uniform bytes (CPU mirror), its view records, and GPU copies of its vertex,
// index, storage and uniform data into one of three retained buffer sets, so
// the last frames stay replayable after later frames reuse Aurora's shared
// buffers. A planner thread matches the pair (previous, this) by owner and
// precomputes everything alpha-independent (frame_interp.hpp plan_pair).
//
// Present (render worker, once per vblank chosen by the timeline): for the
// pair (A, B) and alpha of the predicted scan-out, write B's patched uniform
// arena for that alpha (apply_alpha), re-encode B's recorded passes against
// the retained buffers into a private EFB, repeat B's display copy into a
// private XFB image and present that. alpha ~ 0 shows A's own image, alpha ~ 1
// B's; anything not replay-safe shows B (never A mid-motion).

namespace oi = openspm::interp;

bool m14_frame_interp_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_INTERP");
    const char* mode = std::getenv("OPENSPM_M14_PRESENT_TIMELINE_MODE");
    return value != nullptr && value[0] == '1' && value[1] == '\0' && m14_present_timeline_enabled() &&
           !(mode != nullptr && std::string_view{mode} == "vanilla");
  }();
  return enabled;
}

struct M14InterpGpuSet {
  uint64_t owner = 0;  // frame id whose data the buffers hold
  wgpu::Buffer verts, index, storage, uniform;
  uint64_t vertsCap = 0, indexCap = 0, storageCap = 0, uniformCap = 0;
  wgpu::BindGroup staticGroup, uniformGroup;
};

struct M14InterpRecord {
  uint64_t id = 0;
  std::shared_ptr<oi::Frame> frame;
  const char* unsafe = nullptr;  // why this frame cannot be replayed (null = replayable)
  bool efbCopy = false;          // the frame rewrote an EFB copy texture
  std::vector<gfx::detail::RenderPass> passes;  // EFB passes to replay; the last one ends with the display copy
  std::vector<wgpu::BindGroup> textures;        // per GX draw (stream order)
  gfx::TextureHandle display;
  uint32_t gpuSet = UINT32_MAX;
  std::shared_ptr<oi::PairPlan> plan;           // pair (id - 1, id)
  bool planCounted = false;
};

// Plans pairs off the render worker (pure CPU on immutable frame records).
// Allocated once and never destroyed: the planner thread idles on its
// condition variable until the process exits (no join during static
// destruction, where the OS may already have ended the thread).
struct M14InterpPlanner {
  std::mutex mutex;
  std::condition_variable cv;
  bool started = false;
  bool stop = false;
  oi::Config config;
  std::shared_ptr<const oi::Frame> a, b;  // pending job (latest wins)
  std::vector<std::shared_ptr<oi::PairPlan>> done;
  uint64_t plans = 0, dropped = 0;
  double planUsTotal = 0.0, planUsMax = 0.0;

  void submit(std::shared_ptr<const oi::Frame> frameA, std::shared_ptr<const oi::Frame> frameB) {
    {
      std::lock_guard lock{mutex};
      if (b) ++dropped;
      a = std::move(frameA);
      b = std::move(frameB);
      if (!started) {
        started = true;
        std::thread([this] { run(); }).detach();
      }
    }
    cv.notify_one();
  }

  void run() {
    while (true) {
      std::shared_ptr<const oi::Frame> frameA, frameB;
      oi::Config cfg;
      {
        std::unique_lock lock{mutex};
        cv.wait(lock, [this] { return stop || b != nullptr; });
        if (stop) return;
        frameA = std::move(a);
        frameB = std::move(b);
        a.reset();
        b.reset();
        cfg = config;
      }
      const auto start = std::chrono::steady_clock::now();
      auto plan = std::make_shared<oi::PairPlan>(oi::plan_pair(*frameA, *frameB, cfg));
      const double us =
          std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
      std::lock_guard lock{mutex};
      done.push_back(std::move(plan));
      if (done.size() > 4) done.erase(done.begin());
      ++plans;
      planUsTotal += us;
      planUsMax = std::max(planUsMax, us);
    }
  }
};

struct M14InterpState {
  bool reported = false;
  bool configured = false;
  bool warnedTags = false;
  std::deque<M14InterpRecord> records;  // newest last
  std::array<M14InterpGpuSet, 3> sets;
  uint32_t nextSet = 0;
  // private EFB / XFB
  wgpu::Texture color, resolved, depth;
  wgpu::TextureView colorView, resolvedView, depthView;
  wgpu::Extent3D efbSize{};
  wgpu::TextureFormat efbFormat = wgpu::TextureFormat::Undefined;
  uint32_t samples = 1;
  M14PresenterImage image;
  gfx::TextureHandle imageHandle;
  // once-per-second window
  std::chrono::steady_clock::time_point windowStart{};
  uint64_t presents = 0, interpolated = 0, nativeA = 0, nativeB = 0, retained = 0, unsafeFrames = 0;
  std::vector<std::pair<const char*, uint64_t>> fallbacks;
  uint64_t pairs = 0, owners = 0, matchedOwners = 0, eligible = 0, viewDraws = 0, staticDraws = 0;
  std::array<uint64_t, static_cast<size_t>(oi::Snap::Count)> snapped{};
  double replayUsTotal = 0.0, replayUsMax = 0.0;
  std::atomic<double> replayUsEma{0.0};  // read by the presenter thread (guard)
  uint64_t replayDraws = 0, uploadBytes = 0;
  uint64_t plansAtWindow = 0;
  double planUsAtWindow = 0.0;
};
M14InterpState g_m14Interp;
M14InterpPlanner& g_m14InterpPlanner = *new M14InterpPlanner();  // never destroyed (see above)

void m14_interp_count(std::vector<std::pair<const char*, uint64_t>>& table, const char* reason) {
  for (auto& [name, count] : table) {
    if (std::strcmp(name, reason) == 0) {
      ++count;
      return;
    }
  }
  table.emplace_back(reason, 1u);
}

// Guard the presenter should keep before each vblank: the measured replay
// cost plus GPU/present headroom, within [0.4, 0.85] of the refresh period.
double m14_frame_interp_guard_ns(double displayPeriodNs) noexcept {
  const double ema = g_m14Interp.replayUsEma.load(std::memory_order_relaxed);
  const double cost = ema > 0.0 ? ema * 1000.0 : 2.0e6;
  return std::clamp(cost * 1.25 + 1.5e6, 0.4 * displayPeriodNs, 0.85 * displayPeriodNs);
}

bool m14_interp_ensure_buffer(wgpu::Buffer& buffer, uint64_t& capacity, uint64_t needed, wgpu::BufferUsage usage,
                              const char* label) {
  needed = std::max<uint64_t>(AURORA_ALIGN(needed, 256), 256);
  if (buffer && capacity >= needed) return false;
  const uint64_t size = AURORA_ALIGN(needed + needed / 4, 1u << 20);
  const wgpu::BufferDescriptor descriptor{
      .label = label,
      .usage = usage | wgpu::BufferUsage::CopyDst,
      .size = size,
  };
  buffer = g_device.CreateBuffer(&descriptor);
  capacity = size;
  return true;
}

// Replay-safety of one finished frame, and its draw list for the planner.
const char* m14_interp_collect(gfx::detail::FramePacket& packet, const gfx::TextureHandle& display,
                               M14InterpRecord& record, oi::Frame& frame) {
  using gfx::detail::CommandType;
  using gfx::detail::FrameOpType;
  const char* unsafe = nullptr;
  const auto mark = [&](const char* reason) {
    if (unsafe == nullptr) unsafe = reason;
  };
  if (!packet.textureCopies.empty()) {
    record.efbCopy = true;
    mark("texture-copy");
  }
  if (!packet.encoderTasks.empty()) mark("encoder-task");  // bbox writes and other GPU tasks
  // The bbox priority patch keeps this set from a CPU bounding-box read until
  // the next publish, which comes right after this retain.
  if (gx::bbox::readback_pending()) mark("bbox-read");
  if (frame.uniforms.size() != packet.uniforms.size()) mark("uniform-mirror");

  std::vector<uint32_t> replayPasses;
  bool found = false;
  bool sawDraws = false;
  size_t uploads = 0;
  for (const auto& op : packet.ops) {
    if (op.type != FrameOpType::RenderPass || op.renderPass == nullptr) {
      if (!found) mark("non-render-op");
      continue;
    }
    const auto& p = *op.renderPass;
    if (p.resolveTarget && p.resolveTarget != display) {
      record.efbCopy = true;  // also after the display copy: later replays of earlier frames must know
      if (!found) mark("efb-copy");
    }
    if (found) {
      if (p.resolveTarget == display) mark("multiple-display-copies");  // the image is not one copy
      continue;
    }
    if (sawDraws && op.highWater.textureUploadCount > uploads) mark("mid-frame-upload");
    uploads = op.highWater.textureUploadCount;
    if (!p.paletteConvs.empty()) mark("palette-conv");
    if (!p.m14ReplayEfb) {
      if (p.hasDraws) mark("offscreen-draws");
      continue;
    }
    if (p.snapshotColorDst || p.snapshotDepthDst || p.captureDepthSnapshot) mark("pass-snapshot");
    if (p.sealed && !p.discardable) {
      if (p.colorAttachmentCount != 1 || p.hasStencil || p.msaaSamples != g_m14Interp.samples ||
          p.colorAttachments[gfx::SceneColorAttachmentIndex].format != g_m14Interp.efbFormat ||
          p.colorAttachments[gfx::SceneColorAttachmentIndex].size.width != g_m14Interp.efbSize.width ||
          p.colorAttachments[gfx::SceneColorAttachmentIndex].size.height != g_m14Interp.efbSize.height)
        mark("layout");
      if (replayPasses.empty()) {
        const auto& c = p.colorAttachments[gfx::SceneColorAttachmentIndex];
        const auto colorLoad = c.loadOp != wgpu::LoadOp::Undefined ? c.loadOp
                                                                    : (c.clear ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
        const auto depthLoad = p.depthLoadOp != wgpu::LoadOp::Undefined
                                   ? p.depthLoadOp
                                   : (p.clearDepth ? wgpu::LoadOp::Clear : wgpu::LoadOp::Load);
        // A private EFB has no earlier frame in it (readback continuation).
        if (colorLoad == wgpu::LoadOp::Load || (p.hasDepth && depthLoad == wgpu::LoadOp::Load)) mark("prior-load");
      }
      replayPasses.push_back(op.index);
    }
    if (p.hasDraws) sawDraws = true;
    if (p.resolveTarget == display) {
      found = true;
      if (gx::is_depth_format(p.resolveFormat)) mark("copy-format");
    }
  }
  if (!found || replayPasses.empty() || replayPasses.back() >= packet.renderPasses.size() ||
      packet.renderPasses[replayPasses.back()].resolveTarget != display)
    mark("no-display-copy");

  // Draw list (always: the frame can still be the A side of the next pair).
  const bool tagsOn = [] {
    const char* v = std::getenv("OPENSPM_M14_DRAW_TAGS");
    return v != nullptr && v[0] == '1';
  }();
  gfx::BindGroupRef lastRef{};
  wgpu::BindGroup lastGroup;
  for (const uint32_t index : replayPasses) {
    if (index >= packet.renderPasses.size()) continue;
    const auto& p = packet.renderPasses[index];
    for (const auto& command : p.commands) {
      if (command.type == CommandType::CustomDraw) mark("custom-draw");
      if (command.type != CommandType::Draw || !command.data.draw.m14ReplayGX) continue;
      const auto& draw = command.data.draw;
      gx::DrawData data{};
      std::memcpy(&data, draw.payload.data(), sizeof(data));
      oi::Draw d;
      const bool tagged = tagsOn && (draw.m14DrawMeta & 0x20u) != 0;
      d.owner = tagged ? draw.m14DrawOwner : 0u;
      d.camera = static_cast<uint8_t>(draw.m14DrawMeta & 0xFu);
      d.ortho = (draw.m14DrawMeta & 0x10u) != 0;
      d.indexed = draw.m14ReplayIndexedMatrices;
      d.pnSlot = static_cast<uint8_t>(std::min<uint32_t>(data.immediateData.currentPnMtx, oi::kMaxPnMtx - 1));
      if (data.immediateData.currentPnMtx >= oi::kMaxPnMtx) d.owner = 0;
      d.prefix = static_cast<uint8_t>(std::min<uint32_t>(draw.m14ReplayUniformPrefixBytes, 255u));
      d.view = draw.m14DrawView;
      d.uniformOffset = data.uniformRange.offset;
      d.uniformSize = data.uniformRange.size;
      d.vtxCount = data.vtxCount;
      d.indexCount = data.indexCount;
      frame.draws.push_back(d);
      // Resolved now (the frame was just encoded with them), so later cache
      // expiry cannot drop a group the replay still needs.
      if (data.bindGroups.textureBindGroup && data.bindGroups.textureBindGroup != lastRef) {
        lastRef = data.bindGroups.textureBindGroup;
        lastGroup = gfx::find_bind_group(lastRef);
      }
      record.textures.push_back(data.bindGroups.textureBindGroup ? lastGroup : wgpu::BindGroup{});
    }
  }
  if (unsafe == nullptr) {
    record.passes.reserve(replayPasses.size());
    for (const uint32_t index : replayPasses) record.passes.push_back(packet.renderPasses[index]);
  }
  return unsafe;
}

void m14_interp_ensure_targets() {
  const auto size = webgpu::g_frameBuffer.size;
  const auto format = webgpu::g_frameBuffer.format;
  const uint32_t samples = webgpu::g_graphicsConfig.msaaSamples;
  auto& st = g_m14Interp;
  if (st.color && st.efbSize.width == size.width && st.efbSize.height == size.height && st.efbFormat == format &&
      st.samples == samples)
    return;
  st.efbSize = size;
  st.efbFormat = format;
  st.samples = samples;
  const wgpu::Extent3D extent{size.width, size.height, 1};
  const wgpu::TextureDescriptor color{
      .label = "OpenSPM interpolated EFB",
      // Multisampled: render target only (the resolve texture is the copy source).
      .usage = samples > 1 ? wgpu::TextureUsage::RenderAttachment
                           : wgpu::TextureUsage::RenderAttachment | wgpu::TextureUsage::CopySrc |
                                 wgpu::TextureUsage::TextureBinding,
      .size = extent,
      .format = format,
      .sampleCount = samples,
  };
  st.color = g_device.CreateTexture(&color);
  st.colorView = st.color.CreateView();
  st.resolved = {};
  st.resolvedView = {};
  if (samples > 1) {
    const wgpu::TextureDescriptor resolved{
        .label = "OpenSPM interpolated EFB resolve",
        .usage = wgpu::TextureUsage::RenderAttachment | wgpu::TextureUsage::CopySrc | wgpu::TextureUsage::TextureBinding,
        .size = extent,
        .format = format,
    };
    st.resolved = g_device.CreateTexture(&resolved);
    st.resolvedView = st.resolved.CreateView();
  }
  const wgpu::TextureDescriptor depth{
      .label = "OpenSPM interpolated EFB depth",
      .usage = wgpu::TextureUsage::RenderAttachment,
      .size = extent,
      .format = webgpu::g_graphicsConfig.depthFormat,
      .sampleCount = samples,
  };
  st.depth = g_device.CreateTexture(&depth);
  st.depthView = st.depth.CreateView();
}

void m14_frame_interp_retain(wgpu::CommandEncoder& encoder, const gfx::TextureHandle& display) {
  if (!m14_frame_interp_enabled()) return;
  auto* packet = gfx::detail::m14_end_frame_packet();
  if (packet == nullptr || !display) return;
  auto& st = g_m14Interp;
  if (!st.configured) {
    st.configured = true;
    st.windowStart = std::chrono::steady_clock::now();
    oi::Config config;
    const char* view = std::getenv("OPENSPM_M14_INTERP_VIEW");
    config.useViews = !(view != nullptr && view[0] == '0');
    config.alignment = std::max<uint32_t>(64u, gfx::detail::resources().limits.minUniformBufferOffsetAlignment);
    std::lock_guard lock{g_m14InterpPlanner.mutex};
    g_m14InterpPlanner.config = config;
  }
  m14_interp_ensure_targets();
  if (!st.reported) {
    st.reported = true;
    const char* tags = std::getenv("OPENSPM_M14_DRAW_TAGS");
    Log.info("M14 frame interp active: stage=C pairs=owner+ordinal (Stage B tags) camera={} efb={}x{} msaa={} "
             "retained_frames=3 replay=re-encode recorded passes, uniforms rewritten (matrices only) "
             "fallback=B (never A mid-motion) draw_tags={}",
             g_m14InterpPlanner.config.useViews ? "game view records (V_A,V_B)" : "direct (OPENSPM_M14_INTERP_VIEW=0)",
             st.efbSize.width, st.efbSize.height, st.samples, tags != nullptr && tags[0] == '1' ? "on" : "OFF");
    if (tags == nullptr || tags[0] != '1')
      Log.warn("M14 frame interp: OPENSPM_M14_DRAW_TAGS is off, no draw has an owner: every frame shows natively");
  }
  uint64_t id = 0;
  {
    std::lock_guard lock{g_m14PresenterMutex};
    id = g_m14PresenterGeneration + 1u;  // the publish that follows this encode
  }
  if (!st.records.empty() && id <= st.records.back().id) st.records.clear();  // presenter restarted
  M14InterpRecord record;
  record.id = id;
  record.display = display;
  auto frame = std::make_shared<oi::Frame>();
  frame->id = id;
  frame->uniforms = std::move(packet->m14InterpUniforms);
  packet->m14InterpUniforms = {};
  frame->views.reserve(packet->m14DrawViews.size());
  for (const auto& v : packet->m14DrawViews) {
    oi::Mat34 m;
    std::memcpy(&m, v.data(), sizeof(m));
    frame->views.push_back(m);
  }
  record.unsafe = m14_interp_collect(*packet, display, record, *frame);
  ++st.retained;
  if (record.unsafe != nullptr) ++st.unsafeFrames;

  if (record.unsafe == nullptr) {
    // Upper bound of B's patched arena: every 3D-eligible draw gets a block.
    uint64_t arena = 0;
    const uint32_t alignment = g_m14InterpPlanner.config.alignment;
    for (const auto& d : frame->draws)
      if (d.owner != 0 && !d.ortho) arena += AURORA_ALIGN(uint64_t(d.uniformSize), alignment);
    const uint64_t uniformBytes = AURORA_ALIGN(uint64_t(packet->uniforms.size()), alignment);
    auto& set = st.sets[st.nextSet];
    record.gpuSet = st.nextSet;
    st.nextSet = (st.nextSet + 1u) % static_cast<uint32_t>(st.sets.size());
    bool rebind = false;
    rebind |= m14_interp_ensure_buffer(set.verts, set.vertsCap, packet->verts.size(), wgpu::BufferUsage::Storage,
                                       "OpenSPM interp vertices");
    rebind |= m14_interp_ensure_buffer(set.storage, set.storageCap, packet->storage.size(), wgpu::BufferUsage::Storage,
                                       "OpenSPM interp storage");
    m14_interp_ensure_buffer(set.index, set.indexCap, packet->indices.size(), wgpu::BufferUsage::Index,
                             "OpenSPM interp indices");
    const bool newUniform = m14_interp_ensure_buffer(set.uniform, set.uniformCap, uniformBytes + arena + gx::MaxUniformSize,
                                                     wgpu::BufferUsage::Uniform, "OpenSPM interp uniforms");
    if (rebind || !set.staticGroup) {
      const std::array entries{
          wgpu::BindGroupEntry{.binding = 0, .buffer = set.verts},
          wgpu::BindGroupEntry{.binding = 1, .buffer = set.storage},
      };
      const wgpu::BindGroupDescriptor descriptor{
          .label = "OpenSPM interp static bind group",
          .layout = gfx::detail::resources().staticBindGroupLayout,
          .entryCount = entries.size(),
          .entries = entries.data(),
      };
      set.staticGroup = g_device.CreateBindGroup(&descriptor);
    }
    if (newUniform || !set.uniformGroup) {
      const wgpu::BindGroupEntry entry{.binding = 0, .buffer = set.uniform, .size = gx::MaxUniformSize};
      const wgpu::BindGroupDescriptor descriptor{
          .label = "OpenSPM interp uniform bind group",
          .layout = gfx::detail::resources().uniformBindGroupLayout,
          .entryCount = 1,
          .entries = &entry,
      };
      set.uniformGroup = g_device.CreateBindGroup(&descriptor);
    }
    // The staging buffer holds exactly this frame's bytes (Aurora's layout:
    // vertices, uniforms, indices, storage); copy them in this frame's own
    // command stream, after every one of its passes.
    const auto& staging = gfx::detail::staging_buffer(packet->stagingBuffer);
    const auto copy = [&](uint64_t offset, const wgpu::Buffer& dst, size_t bytes) {
      if (bytes != 0) encoder.CopyBufferToBuffer(staging, offset, dst, 0, AURORA_ALIGN(uint64_t(bytes), 4u));
    };
    constexpr uint64_t uniformOffset = gfx::VertexBufferSize;
    constexpr uint64_t indexOffset = uniformOffset + gfx::UniformBufferSize;
    constexpr uint64_t storageOffset = indexOffset + gfx::IndexBufferSize;
    copy(0, set.verts, packet->verts.size());
    copy(uniformOffset, set.uniform, packet->uniforms.size());
    copy(indexOffset, set.index, packet->indices.size());
    copy(storageOffset, set.storage, packet->storage.size());
    set.owner = id;
  }

  // The planner gets (previous, this) when this frame is replayable.
  if (record.unsafe == nullptr && !st.records.empty() && st.records.back().id + 1u == id && st.records.back().frame)
    g_m14InterpPlanner.submit(st.records.back().frame, frame);
  record.frame = std::move(frame);
  st.records.push_back(std::move(record));
  while (st.records.size() > 4) {
    auto& old = st.records.front().frame;
    if (old && old.use_count() == 1) gfx::detail::m14_frame_interp_recycle(std::move(old->uniforms));  // planner done with it
    st.records.pop_front();
  }
}

M14InterpRecord* m14_interp_find(uint64_t id) {
  for (auto& record : g_m14Interp.records)
    if (record.id == id) return &record;
  return nullptr;
}

void m14_interp_collect_plans() {
  std::vector<std::shared_ptr<oi::PairPlan>> done;
  {
    std::lock_guard lock{g_m14InterpPlanner.mutex};
    done.swap(g_m14InterpPlanner.done);
  }
  for (auto& plan : done) {
    if (auto* record = m14_interp_find(plan->idB); record != nullptr) record->plan = std::move(plan);
  }
}

void m14_interp_log_window(std::chrono::steady_clock::time_point now) {
  auto& st = g_m14Interp;
  uint64_t plans = 0, dropped = 0;
  double planUs = 0.0, planMax = 0.0;
  {
    std::lock_guard lock{g_m14InterpPlanner.mutex};
    plans = g_m14InterpPlanner.plans;
    planUs = g_m14InterpPlanner.planUsTotal;
    planMax = g_m14InterpPlanner.planUsMax;
    dropped = g_m14InterpPlanner.dropped;
    g_m14InterpPlanner.planUsMax = 0.0;
  }
  std::string fallbacks;
  for (const auto& [reason, count] : st.fallbacks) {
    if (!fallbacks.empty()) fallbacks += ',';
    fallbacks += fmt::format("{}:{}", reason, count);
  }
  if (fallbacks.empty()) fallbacks = "none";
  const uint64_t windowPlans = plans - st.plansAtWindow;
  const auto share = [](uint64_t part, uint64_t whole) { return whole != 0 ? 100.0 * double(part) / double(whole) : 0.0; };
  const auto& s = st.snapped;
  Log.info("M14 frame interp: presents={} interpolated={} native_a={} native_b={} fallbacks={} frames={} unsafe_frames={} "
           "pairs={} owners={} matched_owners={} snapped[spawn,count,structure,teleport,rotation]={},{},{},{},{} "
           "view_draws={:.1f}% view_static={:.1f}% replay_us_avg={:.0f} replay_us_max={:.0f} draws_per_replay={} "
           "upload_kb_per_replay={} plans={} plan_us_avg={:.0f} plan_us_max={:.0f} plans_superseded={}",
           st.presents, st.interpolated, st.nativeA, st.nativeB, fallbacks, st.retained, st.unsafeFrames, st.pairs,
           st.owners, st.matchedOwners, s[0], s[1], s[2], s[3], s[4], share(st.viewDraws, st.eligible),
           share(st.staticDraws, st.viewDraws), st.interpolated != 0 ? st.replayUsTotal / double(st.interpolated) : 0.0,
           st.replayUsMax, st.interpolated != 0 ? st.replayDraws / st.interpolated : 0u,
           st.interpolated != 0 ? st.uploadBytes / st.interpolated / 1024u : 0u, windowPlans,
           windowPlans != 0 ? (planUs - st.planUsAtWindow) / double(windowPlans) : 0.0, planMax, dropped);
  st.plansAtWindow = plans;
  st.planUsAtWindow = planUs;
  st.presents = st.interpolated = st.nativeA = st.nativeB = st.retained = st.unsafeFrames = 0;
  st.pairs = st.owners = st.matchedOwners = st.eligible = st.viewDraws = st.staticDraws = 0;
  st.snapped = {};
  st.fallbacks.clear();
  st.replayUsTotal = st.replayUsMax = 0.0;
  st.replayDraws = st.uploadBytes = 0;
  st.windowStart = now;
}

bool m14_interp_ensure_image(const gfx::TextureHandle& display) {
  auto& st = g_m14Interp;
  if (st.image.texture && st.image.size.width == display->size.width && st.image.size.height == display->size.height &&
      st.image.format == display->format)
    return true;
  const wgpu::TextureDescriptor descriptor{
      .label = "OpenSPM interpolated XFB",
      .usage = wgpu::TextureUsage::CopyDst | wgpu::TextureUsage::TextureBinding | wgpu::TextureUsage::RenderAttachment,
      .size = display->size,
      .format = display->format,
  };
  auto texture = g_device.CreateTexture(&descriptor);
  if (!texture) return false;
  auto view = texture.CreateView();
  st.imageHandle = std::make_shared<gfx::TextureRef>(texture, view, view, display->size, display->format, 1u,
                                                     display->gxFormat);
  st.image = {texture, view, display->size, display->format};
  return true;
}

// Worker thread, one present: the image to show for pair (a, b) at alpha.
M14PresenterImage m14_frame_interp_choose(uint64_t a, uint64_t b, double alpha, const M14PresenterImage& imageA,
                                          const M14PresenterImage& imageB) {
  auto& st = g_m14Interp;
  const auto now = std::chrono::steady_clock::now();
  if (st.windowStart.time_since_epoch().count() == 0) st.windowStart = now;
  if (now - st.windowStart >= std::chrono::seconds{1}) m14_interp_log_window(now);
  ++st.presents;
  constexpr double kEdge = 1.0e-3;  // closer than this to a frame: that frame's own image
  if (a == b || !(alpha > kEdge)) {
    if (imageA.texture) {
      ++st.nativeA;
      return imageA;
    }
    ++st.nativeB;
    return imageB;
  }
  if (alpha >= 1.0 - kEdge || !imageB.texture) {
    ++st.nativeB;
    return imageB.texture ? imageB : imageA;
  }
  m14_interp_collect_plans();
  M14InterpRecord* recordB = m14_interp_find(b);
  const char* reason = nullptr;
  if (recordB == nullptr) {
    reason = "not-retained";
  } else if (recordB->unsafe != nullptr) {
    reason = recordB->unsafe;
  } else if (recordB->gpuSet >= st.sets.size() || st.sets[recordB->gpuSet].owner != b) {
    reason = "evicted";
  } else if (recordB->passes.empty() ||
             recordB->passes.front().colorAttachments[gfx::SceneColorAttachmentIndex].size.width != st.efbSize.width ||
             recordB->passes.front().colorAttachments[gfx::SceneColorAttachmentIndex].size.height != st.efbSize.height) {
    reason = "resized";
  } else if (m14_interp_find(a) == nullptr) {
    reason = "no-previous";
  } else if (!recordB->plan) {
    reason = "plan-pending";
  } else if (recordB->plan->idA != a) {
    reason = "plan-pair";
  } else if (recordB->plan->result != oi::PairResult::Ok) {
    reason = oi::pair_result_name(recordB->plan->result);
  } else {
    for (const auto& later : st.records)
      if (later.id > b && later.efbCopy) reason = "later-copy";  // copy textures B samples may have changed
  }
  if (recordB != nullptr && recordB->plan && !recordB->planCounted) {
    recordB->planCounted = true;
    const auto& ps = recordB->plan->stats;
    ++st.pairs;
    st.owners += ps.owners;
    st.matchedOwners += ps.matchedOwners;
    st.eligible += ps.eligible;
    st.viewDraws += ps.viewDraws;
    st.staticDraws += ps.staticDraws;
    for (size_t i = 0; i < st.snapped.size(); ++i) st.snapped[i] += ps.snappedOwners[i];
  }
  if (reason == nullptr && !m14_interp_ensure_image(recordB->display)) reason = "image";
  if (reason != nullptr) {
    m14_interp_count(st.fallbacks, reason);
    ++st.nativeB;
    return imageB;
  }

  const auto start = std::chrono::steady_clock::now();
  auto& plan = *recordB->plan;
  auto& set = st.sets[recordB->gpuSet];
  oi::apply_alpha(plan, alpha);
  if (!plan.arena.empty()) g_queue.WriteBuffer(set.uniform, plan.arenaBase, plan.arena.data(), AURORA_ALIGN(plan.arena.size(), 4u));

  const wgpu::CommandEncoderDescriptor encoderDescriptor{.label = "OpenSPM interpolated frame"};
  auto encoder = g_device.CreateCommandEncoder(&encoderDescriptor);
  gfx::detail::M14InterpReplay replay{
      .passes = &recordB->passes,
      .drawUniform = plan.drawUniform.data(),
      .drawTextures = recordB->textures.data(),
      .drawCount = plan.drawUniform.size(),
      .staticGroup = set.staticGroup,
      .uniformGroup = set.uniformGroup,
      .index = set.index,
      .color = st.colorView,
      .resolve = st.resolvedView,
      .depth = st.depthView,
  };
  gfx::detail::M14InterpReplayStats replayStats{};
  const bool encoded = gfx::detail::m14_encode_frame_interp(encoder, replay, replayStats);
  if (encoded) {
    // B's display copy, from the private EFB into the private XFB image.
    const auto& copy = recordB->passes.back();
    const auto& display = recordB->display;
    const bool scaling = display->size.width != static_cast<uint32_t>(copy.resolveRect.width) ||
                         display->size.height != static_cast<uint32_t>(copy.resolveRect.height);
    const bool resolvedSource = st.samples > 1;
    const gfx::tex_copy_conv::ConvRequest request{
        .fmt = copy.resolveFormat,
        .srcView = resolvedSource ? st.resolvedView : st.colorView,
        .uniformRange = copy.resolveUniformRange,
        .dst = st.imageHandle,
        .sampleFilter = scaling ? gfx::tex_copy_conv::SampleFilter::Linear : gfx::tex_copy_conv::SampleFilter::Nearest,
        .m14UniformBuffer = set.uniform,
    };
    if (gfx::tex_copy_conv::needs_conversion(copy.resolveFormat)) {
      gfx::tex_copy_conv::run(encoder, request);
    } else if (scaling) {
      gfx::tex_copy_conv::blit(encoder, request);
    } else {
      const wgpu::TexelCopyTextureInfo src{
          .texture = resolvedSource ? st.resolved : st.color,
          .origin = {static_cast<uint32_t>(copy.resolveRect.x), static_cast<uint32_t>(copy.resolveRect.y), 0},
      };
      const wgpu::TexelCopyTextureInfo dst{.texture = st.image.texture};
      const wgpu::Extent3D extent{static_cast<uint32_t>(copy.resolveRect.width),
                                  static_cast<uint32_t>(copy.resolveRect.height), 1};
      encoder.CopyTextureToTexture(&src, &dst, &extent);
    }
  }
  const auto buffer = encoder.Finish();
  {
    std::lock_guard queueLock{g_m14PresenterQueueMutex};
    g_queue.Submit(1, &buffer);
  }
  if (!encoded) {
    m14_interp_count(st.fallbacks, "replay-encode");
    ++st.nativeB;
    return imageB;
  }
  const double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
  ++st.interpolated;
  st.replayUsTotal += us;
  st.replayUsMax = std::max(st.replayUsMax, us);
  const double ema = st.replayUsEma.load(std::memory_order_relaxed);
  st.replayUsEma.store(ema == 0.0 ? us : ema + (us - ema) / 32.0, std::memory_order_relaxed);
  st.replayDraws += replayStats.gxDraws;
  st.uploadBytes += plan.arena.size();
  return st.image;
}
