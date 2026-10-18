// Synthetic Dawn (Null backend) benchmark for OpenSPM Stage C: CPU cost of
// re-encoding a GX-like replay of N draws per refresh (the call pattern of
// m14_encode_frame_interp: pipeline, immediates, dynamic uniform offset,
// texture/bbox bind groups, index buffer, draw, with viewport/scissor
// segments), against pre-encoded render bundles (a possible later
// optimization), plus the per-refresh uniform upload. No GPU is needed; the
// Null backend measures Dawn's front end, so real drivers add their own
// recording cost on top. Run: python3 renderer/aurora/bench_frame_interp_dawn.py
// --cache <check_renderer_compile cache> [draws].
#include <dawn/native/DawnNative.h>
#include <webgpu/webgpu_cpp.h>

#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <vector>

using Clock = std::chrono::steady_clock;

static const char* kShader = R"(
struct U { proj: mat4x4f, pos: array<mat3x4f, 20>, nrm: array<mat3x4f, 10>, pad: array<vec4f, 12> };
struct Imm { vtx: u32, pn: u32, fog: u32, pad: u32, a0: vec4u, a1: vec4u, a2: vec4u };
@group(0) @binding(0) var<storage, read> verts: array<vec4f>;
@group(0) @binding(1) var<storage, read> store: array<u32>;
@group(1) @binding(0) var<uniform> ubuf: U;
@group(2) @binding(0) var samp: sampler;
@group(2) @binding(1) var tex: texture_2d<f32>;
@group(3) @binding(0) var<storage, read_write> bbox: array<u32>;
var<immediate> imm: Imm;
@vertex fn vs(@builtin(vertex_index) vi: u32) -> @builtin(position) vec4f {
  let p = verts[imm.vtx + vi];
  let mv = vec4f(p.xyz, 1.0) * ubuf.pos[imm.pn];
  return vec4f(mv, 1.0) * ubuf.proj + vec4f(f32(store[0]), 0.0, 0.0, 0.0);
}
@fragment fn fs() -> @location(0) vec4f { return textureSampleLevel(tex, samp, vec2f(0.5), 0.0); }
)";

int main(int argc, char** argv) {
  const int draws = argc > 1 ? std::atoi(argv[1]) : 3000;
  const bool skipValidation = argc > 2 && std::strcmp(argv[2], "fast") == 0;
  wgpu::InstanceDescriptor instanceDesc{};
  static constexpr auto kTimed = wgpu::InstanceFeatureName::TimedWaitAny;
  instanceDesc.requiredFeatureCount = 1;
  instanceDesc.requiredFeatures = &kTimed;
  auto instance = wgpu::CreateInstance(&instanceDesc);
  wgpu::RequestAdapterOptions options{.backendType = wgpu::BackendType::Null};
  wgpu::Adapter adapter;
  instance.WaitAny(instance.RequestAdapter(&options, wgpu::CallbackMode::WaitAnyOnly,
                                           [&](wgpu::RequestAdapterStatus s, wgpu::Adapter a, wgpu::StringView m) {
                                             if (s == wgpu::RequestAdapterStatus::Success) adapter = std::move(a);
                                             else std::printf("adapter: %.*s\n", int(m.length), m.data);
                                           }),
                   UINT64_MAX);
  if (!adapter) return 1;
  std::array<const char*, 2> toggles{"skip_validation", "allow_unsafe_apis"};
  wgpu::DawnTogglesDescriptor togglesDesc{};
  togglesDesc.enabledToggleCount = skipValidation ? 2 : 1;
  togglesDesc.enabledToggles = skipValidation ? toggles.data() : toggles.data() + 1;
  wgpu::Limits limits{};
  limits.maxImmediateSize = 64;
  wgpu::DeviceDescriptor deviceDesc{};
  deviceDesc.nextInChain = &togglesDesc;
  deviceDesc.requiredLimits = &limits;
  deviceDesc.SetUncapturedErrorCallback([](const wgpu::Device&, wgpu::ErrorType, wgpu::StringView m) {
    std::printf("error: %.*s\n", int(m.length), m.data);
  });
  wgpu::Device device;
  instance.WaitAny(adapter.RequestDevice(&deviceDesc, wgpu::CallbackMode::WaitAnyOnly,
                                         [&](wgpu::RequestDeviceStatus s, wgpu::Device d, wgpu::StringView m) {
                                           if (s == wgpu::RequestDeviceStatus::Success) device = std::move(d);
                                           else std::printf("device: %.*s\n", int(m.length), m.data);
                                         }),
                   UINT64_MAX);
  if (!device) return 1;
  auto queue = device.GetQueue();

  auto buffer = [&](wgpu::BufferUsage usage, uint64_t size) {
    wgpu::BufferDescriptor d{.usage = usage | wgpu::BufferUsage::CopyDst, .size = size};
    return device.CreateBuffer(&d);
  };
  auto verts = buffer(wgpu::BufferUsage::Storage, 5u << 20);
  auto storage = buffer(wgpu::BufferUsage::Storage, 1u << 20);
  auto index = buffer(wgpu::BufferUsage::Index, 2u << 20);
  const uint64_t block = 1792;
  auto uniforms = buffer(wgpu::BufferUsage::Uniform, block * (draws + 1));
  auto bboxBuf = buffer(wgpu::BufferUsage::Storage, 16);

  auto bgl = [&](std::vector<wgpu::BindGroupLayoutEntry> entries) {
    wgpu::BindGroupLayoutDescriptor d{.entryCount = entries.size(), .entries = entries.data()};
    return device.CreateBindGroupLayout(&d);
  };
  auto l0 = bgl({{.binding = 0, .visibility = wgpu::ShaderStage::Vertex,
                  .buffer = {.type = wgpu::BufferBindingType::ReadOnlyStorage}},
                 {.binding = 1, .visibility = wgpu::ShaderStage::Vertex | wgpu::ShaderStage::Fragment,
                  .buffer = {.type = wgpu::BufferBindingType::ReadOnlyStorage}}});
  auto l1 = bgl({{.binding = 0, .visibility = wgpu::ShaderStage::Vertex | wgpu::ShaderStage::Fragment,
                  .buffer = {.type = wgpu::BufferBindingType::Uniform, .hasDynamicOffset = true}}});
  auto l2 = bgl({{.binding = 0, .visibility = wgpu::ShaderStage::Fragment, .sampler = {.type = wgpu::SamplerBindingType::Filtering}},
                 {.binding = 1, .visibility = wgpu::ShaderStage::Fragment, .texture = {.sampleType = wgpu::TextureSampleType::Float}}});
  auto l3 = bgl({{.binding = 0, .visibility = wgpu::ShaderStage::Fragment,
                  .buffer = {.type = wgpu::BufferBindingType::Storage}}});
  auto group = [&](const wgpu::BindGroupLayout& layout, std::vector<wgpu::BindGroupEntry> entries) {
    wgpu::BindGroupDescriptor d{.layout = layout, .entryCount = entries.size(), .entries = entries.data()};
    return device.CreateBindGroup(&d);
  };
  auto g0 = group(l0, {{.binding = 0, .buffer = verts}, {.binding = 1, .buffer = storage}});
  auto g1 = group(l1, {{.binding = 0, .buffer = uniforms, .size = block}});
  auto g3 = group(l3, {{.binding = 0, .buffer = bboxBuf}});
  wgpu::SamplerDescriptor sd{};
  auto sampler = device.CreateSampler(&sd);
  std::vector<wgpu::BindGroup> textures;
  for (int i = 0; i < 64; ++i) {
    wgpu::TextureDescriptor td{.usage = wgpu::TextureUsage::TextureBinding, .size = {64, 64, 1},
                               .format = wgpu::TextureFormat::RGBA8Unorm};
    auto t = device.CreateTexture(&td);
    textures.push_back(group(l2, {{.binding = 0, .sampler = sampler}, {.binding = 1, .textureView = t.CreateView()}}));
  }
  std::array<wgpu::BindGroupLayout, 4> layouts{l0, l1, l2, l3};
  wgpu::PipelineLayoutDescriptor pld{.bindGroupLayoutCount = 4, .bindGroupLayouts = layouts.data(), .immediateSize = 64};
  auto pipelineLayout = device.CreatePipelineLayout(&pld);
  wgpu::ShaderSourceWGSL wgsl{};
  wgsl.code = kShader;
  wgpu::ShaderModuleDescriptor smd{.nextInChain = &wgsl};
  auto module = device.CreateShaderModule(&smd);
  const auto colorFormat = wgpu::TextureFormat::BGRA8Unorm;
  const auto depthFormat = wgpu::TextureFormat::Depth32Float;
  std::vector<wgpu::RenderPipeline> pipelines;
  for (int i = 0; i < 32; ++i) {
    wgpu::ColorTargetState target{.format = colorFormat};
    wgpu::FragmentState fs{.module = module, .entryPoint = "fs", .targetCount = 1, .targets = &target};
    wgpu::DepthStencilState ds{.format = depthFormat, .depthWriteEnabled = (i & 1) != 0,
                               .depthCompare = wgpu::CompareFunction::LessEqual};
    wgpu::RenderPipelineDescriptor rpd{.layout = pipelineLayout,
                                       .vertex = {.module = module, .entryPoint = "vs"},
                                       .primitive = {.cullMode = (i & 2) ? wgpu::CullMode::Back : wgpu::CullMode::None},
                                       .depthStencil = &ds, .fragment = &fs};
    pipelines.push_back(device.CreateRenderPipeline(&rpd));
  }
  wgpu::TextureDescriptor ctd{.usage = wgpu::TextureUsage::RenderAttachment, .size = {1440, 1080, 1}, .format = colorFormat};
  auto color = device.CreateTexture(&ctd).CreateView();
  wgpu::TextureDescriptor dtd{.usage = wgpu::TextureUsage::RenderAttachment, .size = {1440, 1080, 1}, .format = depthFormat};
  auto depth = device.CreateTexture(&dtd).CreateView();

  struct Imm { uint32_t v[16]; };
  auto record = [&](auto& enc, int first, int count) {
    enc.SetBindGroup(0, g0);
    enc.SetBindGroup(3, g3);
    for (int i = first; i < first + count; ++i) {
      enc.SetPipeline(pipelines[(i / 7) % pipelines.size()]);
      Imm imm{};
      imm.v[0] = uint32_t(i) * 12u;
      enc.SetImmediates(0, &imm, sizeof(imm));
      const uint32_t offset = uint32_t(i) * uint32_t(block);
      enc.SetBindGroup(1, g1, 1, &offset);
      enc.SetBindGroup(2, textures[(i / 3) % textures.size()]);
      enc.SetIndexBuffer(index, wgpu::IndexFormat::Uint16, uint64_t(i % 512) * 256u, 256u);
      enc.DrawIndexed(96, 1);
    }
  };
  auto beginPass = [&](wgpu::CommandEncoder& ce) {
    wgpu::RenderPassColorAttachment ca{.view = color, .loadOp = wgpu::LoadOp::Clear, .storeOp = wgpu::StoreOp::Store};
    wgpu::RenderPassDepthStencilAttachment da{.view = depth, .depthLoadOp = wgpu::LoadOp::Clear,
                                              .depthStoreOp = wgpu::StoreOp::Store, .depthClearValue = 1.0f};
    wgpu::RenderPassDescriptor rp{.colorAttachmentCount = 1, .colorAttachments = &ca, .depthStencilAttachment = &da};
    return ce.BeginRenderPass(&rp);
  };
  std::vector<uint8_t> upload(block * draws, 0);
  const int segments = 24;  // viewport/scissor/blend-constant boundaries
  auto measure = [&](const char* label, auto body) {
    for (int w = 0; w < 5; ++w) body();
    const int reps = 40;
    const auto t0 = Clock::now();
    for (int r = 0; r < reps; ++r) body();
    const double us = std::chrono::duration<double, std::micro>(Clock::now() - t0).count() / reps;
    std::printf("%-44s %8.1f us\n", label, us);
    return us;
  };
  std::printf("draws=%d validation=%s backend=null\n", draws, skipValidation ? "skipped" : "on");
  measure("direct pass encode + submit", [&] {
    auto ce = device.CreateCommandEncoder();
    auto pass = beginPass(ce);
    const int per = draws / segments;
    for (int s = 0; s < segments; ++s) {
      pass.SetViewport(0, 0, 1440, 1080, 0, 1);
      pass.SetScissorRect(0, 0, 1440, 1080);
      record(pass, s * per, per);
    }
    pass.End();
    auto cb = ce.Finish();
    queue.Submit(1, &cb);
  });
  std::vector<wgpu::RenderBundle> bundles;
  measure("bundle encode (once per game frame)", [&] {
    bundles.clear();
    wgpu::RenderBundleEncoderDescriptor bd{.colorFormatCount = 1, .colorFormats = &colorFormat,
                                          .depthStencilFormat = depthFormat};
    const int per = draws / segments;
    for (int s = 0; s < segments; ++s) {
      auto be = device.CreateRenderBundleEncoder(&bd);
      record(be, s * per, per);
      bundles.push_back(be.Finish());
    }
  });
  measure("bundle execute + uniform upload + submit", [&] {
    queue.WriteBuffer(uniforms, 0, upload.data(), upload.size());
    auto ce = device.CreateCommandEncoder();
    auto pass = beginPass(ce);
    for (auto& b : bundles) {
      pass.SetViewport(0, 0, 1440, 1080, 0, 1);
      pass.SetScissorRect(0, 0, 1440, 1080);
      pass.ExecuteBundles(1, &b);
    }
    pass.End();
    auto cb = ce.Finish();
    queue.Submit(1, &cb);
  });
  measure("uniform upload only (WriteBuffer)", [&] { queue.WriteBuffer(uniforms, 0, upload.data(), upload.size()); });
  device.Tick();
  return 0;
}
