#!/usr/bin/env python3
"""Apply OpenSPM's raster-feedback adaptations after apply_patches.py.

This second layer deliberately stays separate from the raw-address adaptation:
layer 1 makes an original Wii GX stream readable by Aurora; layer 2 adds the
few GPU observations a statically recompiled game can synchronously observe
(display copy and the PE bounding box).

The transform is exact against the pinned Aurora revision *after* layer 1 has
been applied. Any source drift is a hard failure.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

BBOX_HEADER = r'''#pragma once

#include <array>
#include <cstdint>
#include <webgpu/webgpu_cpp.h>

namespace aurora::gx::bbox {

void initialize() noexcept;
void shutdown() noexcept;
const wgpu::BindGroupLayout& bind_group_layout() noexcept;
const wgpu::BindGroup& bind_group() noexcept;

// Queue one PE bounding-box component write in GX command order. Index order is
// left, right, top, bottom. Outside an active frame this becomes an immediate
// queue write, which is safe because no earlier unsubmitted GX draws exist.
void queue_write(std::uint32_t index, std::uint16_t value) noexcept;

// Read the four inclusive PE bounds back synchronously. The caller must first
// end/drain the active Aurora frame so earlier GX draws have been submitted.
bool read_sync(std::array<std::uint16_t, 4>& out) noexcept;

} // namespace aurora::gx::bbox
'''

BBOX_SOURCE = r'''#include "bbox.hpp"

#include <aurora/gfx.hpp>

#include "../internal.hpp"
#include "../webgpu/gpu.hpp"

#include <array>
#include <cstdint>
#include <cstring>

namespace aurora::gx::bbox {
namespace {
constexpr Module Log{"aurora::gx::bbox"};
constexpr std::uint64_t BufferSize = sizeof(std::uint32_t) * 4u;

wgpu::Buffer sStorage;
wgpu::Buffer sReadback;
wgpu::BindGroupLayout sBindGroupLayout;
wgpu::BindGroup sBindGroup;
gfx::EncoderTaskId sWriteTask = gfx::InvalidEncoderTask;

struct WritePayload {
  std::uint32_t sourceOffset;
  std::uint32_t destinationOffset;
};
static_assert(sizeof(WritePayload) == 8);

void encode_write(const gfx::EncoderTaskContext& ctx,
                  const wgpu::CommandEncoder& cmd,
                  const void* payload,
                  size_t payloadSize,
                  void*) {
  if (payload == nullptr || payloadSize != sizeof(WritePayload) || !sStorage) return;
  WritePayload write{};
  std::memcpy(&write, payload, sizeof(write));
  cmd.CopyBufferToBuffer(ctx.storageBuffer, write.sourceOffset,
                         sStorage, write.destinationOffset,
                         sizeof(std::uint32_t));
}

} // namespace

void initialize() noexcept {
  const wgpu::BufferDescriptor storageDesc{
      .label = "GX PE Bounding Box",
      .usage = wgpu::BufferUsage::Storage | wgpu::BufferUsage::CopySrc |
               wgpu::BufferUsage::CopyDst,
      .size = BufferSize,
  };
  sStorage = webgpu::g_device.CreateBuffer(&storageDesc);

  const wgpu::BufferDescriptor readbackDesc{
      .label = "GX PE Bounding Box Readback",
      .usage = wgpu::BufferUsage::MapRead | wgpu::BufferUsage::CopyDst,
      .size = BufferSize,
  };
  sReadback = webgpu::g_device.CreateBuffer(&readbackDesc);

  constexpr std::array layoutEntries{
      wgpu::BindGroupLayoutEntry{
          .binding = 0,
          .visibility = wgpu::ShaderStage::Fragment,
          .buffer = wgpu::BufferBindingLayout{
              .type = wgpu::BufferBindingType::Storage,
          },
      },
  };
  const wgpu::BindGroupLayoutDescriptor layoutDesc{
      .label = "GX PE Bounding Box Layout",
      .entryCount = layoutEntries.size(),
      .entries = layoutEntries.data(),
  };
  sBindGroupLayout = webgpu::g_device.CreateBindGroupLayout(&layoutDesc);

  const std::array entries{
      wgpu::BindGroupEntry{
          .binding = 0,
          .buffer = sStorage,
          .offset = 0,
          .size = BufferSize,
      },
  };
  const wgpu::BindGroupDescriptor bindDesc{
      .label = "GX PE Bounding Box Bind Group",
      .layout = sBindGroupLayout,
      .entryCount = entries.size(),
      .entries = entries.data(),
  };
  sBindGroup = webgpu::g_device.CreateBindGroup(&bindDesc);

  const std::array<std::uint32_t, 4> reset{1023u, 0u, 1023u, 0u};
  webgpu::g_queue.WriteBuffer(sStorage, 0, reset.data(), sizeof(reset));

  sWriteTask = gfx::register_encoder_task_type({
      .label = "GX PE Bounding Box Write",
      .callback = encode_write,
  });
}

void shutdown() noexcept {
  if (sWriteTask != gfx::InvalidEncoderTask) {
    gfx::unregister_encoder_task_type(sWriteTask);
    sWriteTask = gfx::InvalidEncoderTask;
  }
  sBindGroup = {};
  sBindGroupLayout = {};
  sReadback = {};
  sStorage = {};
}

const wgpu::BindGroupLayout& bind_group_layout() noexcept { return sBindGroupLayout; }
const wgpu::BindGroup& bind_group() noexcept { return sBindGroup; }

void queue_write(std::uint32_t index, std::uint16_t value) noexcept {
  if (!sStorage || index >= 4u) return;

  const std::uint32_t sourceValue = value;
  const auto range = gfx::push_storage(
      reinterpret_cast<const std::uint8_t*>(&sourceValue), sizeof(sourceValue));
  if (range.size == sizeof(sourceValue) && sWriteTask != gfx::InvalidEncoderTask) {
    const WritePayload payload{
        .sourceOffset = range.offset,
        .destinationOffset = index * sizeof(std::uint32_t),
    };
    if (gfx::push_encoder_task(sWriteTask, &payload, sizeof(payload))) return;
  }

  // No active recording pass: there can be no earlier, unsubmitted GX draw in
  // this frame, so a queue write preserves ordering relative to submitted work.
  webgpu::g_queue.WriteBuffer(sStorage, index * sizeof(std::uint32_t),
                              &sourceValue, sizeof(sourceValue));
}

bool read_sync(std::array<std::uint16_t, 4>& out) noexcept {
  if (!sStorage || !sReadback || !webgpu::g_instance) return false;

  constexpr wgpu::CommandEncoderDescriptor encoderDesc{
      .label = "GX PE Bounding Box Readback Encoder",
  };
  auto encoder = webgpu::g_device.CreateCommandEncoder(&encoderDesc);
  encoder.CopyBufferToBuffer(sStorage, 0, sReadback, 0, BufferSize);
  constexpr wgpu::CommandBufferDescriptor commandDesc{
      .label = "GX PE Bounding Box Readback",
  };
  const auto command = encoder.Finish(&commandDesc);
  webgpu::g_queue.Submit(1, &command);

  wgpu::MapAsyncStatus mapStatus = wgpu::MapAsyncStatus::Error;
  const auto future = sReadback.MapAsync(
      wgpu::MapMode::Read, 0, BufferSize, wgpu::CallbackMode::WaitAnyOnly,
      [&mapStatus](wgpu::MapAsyncStatus status, wgpu::StringView message) {
        mapStatus = status;
        if (status != wgpu::MapAsyncStatus::Success) {
          Log.warn("Bounding-box readback map failed: {}", message);
        }
      });
  const auto waitStatus = webgpu::g_instance.WaitAny(future, 5'000'000'000);
  if (waitStatus != wgpu::WaitStatus::Success ||
      mapStatus != wgpu::MapAsyncStatus::Success) {
    return false;
  }

  const auto* mapped = static_cast<const std::uint32_t*>(
      sReadback.GetConstMappedRange(0, BufferSize));
  if (mapped == nullptr) {
    sReadback.Unmap();
    return false;
  }
  for (std::size_t i = 0; i < out.size(); ++i) {
    out[i] = static_cast<std::uint16_t>(mapped[i] & 0x3ffu);
  }
  sReadback.Unmap();
  return true;
}

} // namespace aurora::gx::bbox
'''

RAW_HEADER = r'''#pragma once

#include <array>
#include <cstdint>

namespace aurora::gx::raw {

// Append original big-endian GX FIFO bytes. Callers may provide arbitrarily
// small fragments; drain() must only be called at a known complete-command
// boundary supplied by the host bridge.
void append_fifo(const void* bytes, std::uint32_t size);
void drain();

// Original GX display-copy is a presentation boundary for a native port. The
// command processor records the event; the host-side renderer consumes it
// after draining the complete FIFO boundary.
void notify_display_copy() noexcept;
bool take_display_copy() noexcept;

void write_bbox(std::uint32_t index, std::uint16_t value) noexcept;
bool read_bbox(std::array<std::uint16_t, 4>& out) noexcept;

} // namespace aurora::gx::raw
'''

RAW_SOURCE = r'''#include <aurora/gx_raw.hpp>

#include "bbox.hpp"
#include "fifo.hpp"

#include <atomic>

namespace aurora::gx::raw {
namespace {
std::atomic_bool sDisplayCopy{false};
}

void append_fifo(const void* bytes, std::uint32_t size) {
  if (bytes == nullptr || size == 0u) return;
  fifo::write_data(bytes, size);
}

void drain() { fifo::drain(); }

void notify_display_copy() noexcept {
  sDisplayCopy.store(true, std::memory_order_release);
}

bool take_display_copy() noexcept {
  return sDisplayCopy.exchange(false, std::memory_order_acq_rel);
}

void write_bbox(std::uint32_t index, std::uint16_t value) noexcept {
  bbox::queue_write(index, value);
}

bool read_bbox(std::array<std::uint16_t, 4>& out) noexcept {
  return bbox::read_sync(out);
}

} // namespace aurora::gx::raw
'''

TRANSFORMS: dict[str, list[tuple[str, str]]] = {
    "cmake/aurora_gx.cmake": [
        (
            "        lib/gx/command_processor.cpp\n        lib/gx/guest_memory.cpp\n        lib/gx/regs.cpp\n",
            "        lib/gx/bbox.cpp\n        lib/gx/command_processor.cpp\n        lib/gx/guest_memory.cpp\n        lib/gx/raw.cpp\n        lib/gx/regs.cpp\n",
        ),
    ],
    "lib/gx/regs.cpp": [
        (
            '#include "regs.hpp"\n\n#include <aurora/gx_guest_memory.hpp>\n',
            '#include "regs.hpp"\n\n#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_raw.hpp>\n\n#include "bbox.hpp"\n',
        ),
        (
            "// EFB copy trigger (0x52)\n"
            "void bp_efb_copy(u8, u32 value) noexcept {\n"
            "  const bool clear = reg_get(value, 1, 11) != 0;\n"
            "  if (reg_get(value, 1, 14) != 0) {\n"
            '    Log.warn("STUB: display copy is not implemented");\n'
            "  } else {\n"
            "    copy_tex(g_gxState.texCopyDest, clear);\n"
            "  }\n"
            "}\n",
            "// Decode the original PE texture-copy control value back into the\n"
            "// logical copy format Aurora's resolve path expects. GX's high-level\n"
            "// API encodes intensity-vs-component conversion in ccv_mode, while a\n"
            "// depth copy temporarily switches PE pixel format to GX_PF_Z24.\n"
            "bool decode_raw_copy_format(u32 value, GXTexFmt& out) noexcept {\n"
            "  const u32 code = reg_get(value, 4, 3);\n"
            "  if (g_gxState.pixelFmt == GX_PF_Z24) {\n"
            "    switch (code) {\n"
            "    case 0x1: out = GX_TF_Z8; return true;\n"
            "    case 0x6: out = GX_TF_Z24X8; return true;\n"
            "    case 0xB: out = GX_TF_Z16; return true;\n"
            "    default:\n"
            "      Log.error(\"Unsupported original GX depth-copy format 0x{:X}\", code);\n"
            "      return false;\n"
            "    }\n"
            "  }\n\n"
            "  const u32 ccvMode = reg_get(value, 2, 15);\n"
            "  switch (code) {\n"
            "  case 0x0: out = ccvMode == 3u ? GX_TF_I4 : GX_CTF_R4; return true;\n"
            "  case 0x1:\n"
            "    if (ccvMode == 3u) { out = GX_TF_I8; return true; }\n"
            "    break;\n"
            "  case 0x2: out = ccvMode == 3u ? GX_TF_IA4 : GX_CTF_RA4; return true;\n"
            "  case 0x3: out = ccvMode == 3u ? GX_TF_IA8 : GX_CTF_RA8; return true;\n"
            "  case 0x4: out = GX_TF_RGB565; return true;\n"
            "  case 0x5: out = GX_TF_RGB5A3; return true;\n"
            "  case 0x6:\n"
            "    if (ccvMode != 3u) { out = GX_TF_RGBA8; return true; }\n"
            "    break;\n"
            "  case 0x7: out = GX_CTF_A8; return true;\n"
            "  case 0x8: out = GX_CTF_R8; return true;\n"
            "  case 0x9: out = GX_CTF_G8; return true;\n"
            "  case 0xA: out = GX_CTF_B8; return true;\n"
            "  case 0xB: out = GX_CTF_RG8; return true;\n"
            "  case 0xC: out = GX_CTF_GB8; return true;\n"
            "  default: break;\n"
            "  }\n"
            "  Log.error(\"Unsupported original GX color-copy format 0x{:X} ccv={}\", code, ccvMode);\n"
            "  return false;\n"
            "}\n\n"
            "// EFB copy trigger (0x52)\n"
            "void bp_efb_copy(u8, u32 value) noexcept {\n"
            "  const bool clear = reg_get(value, 1, 11) != 0;\n"
            "  if (reg_get(value, 1, 14) != 0) {\n"
            "    // An original-console display copy becomes the native port's\n"
            "    // presentation boundary. The host consumes the event only after\n"
            "    // the complete FIFO has drained through a known sync marker.\n"
            "    raw::notify_display_copy();\n"
            "    (void)clear;\n"
            "  } else {\n"
            "    GXTexFmt format{};\n"
            "    if (!decode_raw_copy_format(value, format)) return;\n"
            "    if (guest_memory::has_mapper()) {\n"
            "      if (g_gxState.texCopyDest == nullptr) {\n"
            "        Log.error(\"Original GX texture copy has no mapped destination\");\n"
            "        return;\n"
            "      }\n"
            "      // Original hardware stores stride rather than a high-level\n"
            "      // destination rectangle. For Stage-A cache identity, the exact\n"
            "      // EFB copy extent is the useful render-target size; compositing\n"
            "      // into larger backing allocations can be lifted later if SPM\n"
            "      // demonstrates a case that needs the padded storage extent.\n"
            "      g_gxState.texCopyDstWidth = static_cast<u32>(\n"
            "          std::max(g_gxState.texCopySrc.width, 1));\n"
            "      g_gxState.texCopyDstHeight = static_cast<u32>(\n"
            "          std::max(g_gxState.texCopySrc.height, 1));\n"
            "      g_gxState.texCopyDstWide = false;\n"
            "      g_gxState.texCopyFmt = format;\n"
            "    }\n"
            "    copy_tex(g_gxState.texCopyDest, clear);\n"
            "  }\n"
            "}\n\n"
            "// PE bounding-box BP registers. The two ten-bit values in 0x55 are\n"
            "// left/right; 0x56 carries top/bottom. Writes are queued into the\n"
            "// render command stream so a clear between two draws stays ordered.\n"
            "void bp_bbox(u8 reg, u32 value) noexcept {\n"
            "  const u32 base = (reg - 0x55u) * 2u;\n"
            "  bbox::queue_write(base, static_cast<u16>(reg_get(value, 10, 0)));\n"
            "  bbox::queue_write(base + 1u, static_cast<u16>(reg_get(value, 10, 10)));\n"
            "}\n",
        ),
        (
            "  regs[0x51] = {bp_clear_depth};\n"
            "  regs[0x52] = {bp_efb_copy, 0, /* alwaysHandle */ true};\n"
            "  regs[0x64] = {}; // TLUT load address; bp_tlut_load reads from shadow\n",
            "  regs[0x51] = {bp_clear_depth};\n"
            "  regs[0x52] = {bp_efb_copy, 0, /* alwaysHandle */ true};\n"
            "  regs[0x55] = {bp_bbox, 0, /* alwaysHandle */ true};\n"
            "  regs[0x56] = {bp_bbox, 0, /* alwaysHandle */ true};\n"
            "  regs[0x64] = {}; // TLUT load address; bp_tlut_load reads from shadow\n",
        ),
    ],
    "lib/gx/gx.cpp": [
        (
            '#include "gx.hpp"\n\n#include "pipeline.hpp"\n',
            '#include "gx.hpp"\n\n#include "bbox.hpp"\n#include "pipeline.hpp"\n',
        ),
        (
            "void initialize() noexcept {\n  {\n",
            "void initialize() noexcept {\n  bbox::initialize();\n  {\n",
        ),
        (
            "    const std::array layouts{\n"
            "        gfx::detail::resources().staticBindGroupLayout,\n"
            "        gfx::detail::resources().uniformBindGroupLayout,\n"
            "        sTextureBindGroupLayout,\n"
            "    };\n",
            "    const std::array layouts{\n"
            "        gfx::detail::resources().staticBindGroupLayout,\n"
            "        gfx::detail::resources().uniformBindGroupLayout,\n"
            "        sTextureBindGroupLayout,\n"
            "        bbox::bind_group_layout(),\n"
            "    };\n",
        ),
        (
            "void shutdown() noexcept {\n"
            "  // TODO we should probably store this all in g_state.gx instead\n",
            "void shutdown() noexcept {\n"
            "  bbox::shutdown();\n"
            "  // TODO we should probably store this all in g_state.gx instead\n",
        ),
    ],
    "lib/gx/pipeline.cpp": [
        (
            '#include "pipeline.hpp"\n\n#include "../gfx/encoding.hpp"\n',
            '#include "pipeline.hpp"\n\n#include "bbox.hpp"\n#include "../gfx/encoding.hpp"\n',
        ),
        (
            "  if (data.bindGroups.textureBindGroup) {\n"
            "    pass.SetBindGroup(2, gfx::find_bind_group(data.bindGroups.textureBindGroup));\n"
            "  }\n"
            "  pass.SetIndexBuffer(resources.indexBuffer, wgpu::IndexFormat::Uint16, data.idxRange.offset, data.idxRange.size);\n",
            "  if (data.bindGroups.textureBindGroup) {\n"
            "    pass.SetBindGroup(2, gfx::find_bind_group(data.bindGroups.textureBindGroup));\n"
            "  }\n"
            "  pass.SetBindGroup(3, bbox::bind_group());\n"
            "  pass.SetIndexBuffer(resources.indexBuffer, wgpu::IndexFormat::Uint16, data.idxRange.offset, data.idxRange.size);\n",
        ),
    ],
    "lib/gx/shader.cpp": [
        (
            "  if constexpr (EnableNormalVisualization) {\n"
            '    fragmentFn += "\\n    prev = vec4f(in.nrm, prev.a);";\n'
            "  }\n\n"
            "  const auto shaderSource = fmt::format(R\"\"\"(\n",
            "  if constexpr (EnableNormalVisualization) {\n"
            '    fragmentFn += "\\n    prev = vec4f(in.nrm, prev.a);";\n'
            "  }\n"
            "  // PE bounding-box coverage is inclusive and the original GX rasterizer\n"
            "  // touches 2x2 pixel groups. Run after alpha-test discard so discarded\n"
            "  // fragments do not grow the observable box. M14's first renderer keeps\n"
            "  // the EFB at native Wii scale; high-resolution remapping comes later.\n"
            "  fragmentFn +=\n"
            '      "\\n    let bbox_x = min(u32(max(floor(in.pos.x), 0.0)), 1023u);"\n'
            '      "\\n    let bbox_y = min(u32(max(floor(in.pos.y), 0.0)), 1023u);"\n'
            '      "\\n    atomicMin(&gx_bbox[0], bbox_x & 0xfffffffeu);"\n'
            '      "\\n    atomicMax(&gx_bbox[1], min(bbox_x | 1u, 1023u));"\n'
            '      "\\n    atomicMin(&gx_bbox[2], bbox_y & 0xfffffffeu);"\n'
            '      "\\n    atomicMax(&gx_bbox[3], min(bbox_y | 1u, 1023u));";\n\n'
            "  const auto shaderSource = fmt::format(R\"\"\"(\n",
        ),
        (
            "@group(1) @binding(0)\n"
            "var<uniform> ubuf: Uniform;{1}\n\n"
            "struct VertexOutput {{\n",
            "@group(1) @binding(0)\n"
            "var<uniform> ubuf: Uniform;{1}\n"
            "@group(3) @binding(0)\n"
            "var<storage, read_write> gx_bbox: array<atomic<u32>, 4>;\n\n"
            "struct VertexOutput {{\n",
        ),
    ],
}

NEW_FILES = {
    "lib/gx/bbox.hpp": BBOX_HEADER,
    "lib/gx/bbox.cpp": BBOX_SOURCE,
    "include/aurora/gx_raw.hpp": RAW_HEADER,
    "lib/gx/raw.cpp": RAW_SOURCE,
}


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def transform(source: pathlib.Path, check_only: bool) -> None:
    pending: dict[pathlib.Path, str] = {}

    for relative, replacements in TRANSFORMS.items():
        path = source / relative
        text = path.read_text(encoding="utf-8")
        for old, new in replacements:
            count = text.count(old)
            if count != 1:
                raise RuntimeError(
                    f"{relative}: expected raster source fragment exactly once, found {count}"
                )
            text = text.replace(old, new, 1)
        pending[path] = text

    for relative, contents in NEW_FILES.items():
        path = source / relative
        if path.exists():
            raise RuntimeError(f"{relative}: expected file to be absent before raster layer")
        pending[path] = contents

    if check_only:
        return

    for path, contents in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path, help="Aurora checkout after apply_patches.py")
    parser.add_argument("--check-only", action="store_true", help="verify exact raster transforms without writing")
    args = parser.parse_args()

    source = args.source.resolve()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    expected = lock["commit"]

    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    # Layer 1 is expected to make the checkout dirty. Verify its signature so
    # layer 2 cannot accidentally be checked against pristine or unrelated code.
    if not (source / "include/aurora/gx_guest_memory.hpp").exists():
        print("error: raw-GX layer is not applied; run apply_patches.py first", file=sys.stderr)
        return 4

    try:
        transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora raster adaptation contract failed: {exc}", file=sys.stderr)
        return 5

    print("Aurora raster transforms verified" if args.check_only else "Aurora raster transforms applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
