#!/usr/bin/env python3
"""Submit a raw GX readback segment without presenting or clearing its EFB.

Applied after the display-copy snapshot migration. Preservation is one-shot,
only for a synchronous continuation within the same logical display frame;
ordinary host frame starts retain their existing clear policy.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent

PRESERVE = '''  // M14 readback continuation: consume exactly once, never persist across
  // ordinary display frames. No window update/resize occurs across this split.
  if (sM14LoadReadbackContinuation) {
    for (uint32_t i = 0; i < pass.colorAttachmentCount; ++i) {
      pass.colorAttachments[i].loadOp = wgpu::LoadOp::Load;
      pass.colorAttachments[i].clear = false;
    }
    pass.depthLoadOp = wgpu::LoadOp::Load;
    pass.clearDepth = false;
    sM14LoadReadbackContinuation = false;
  }
'''

# Astra's first checkpoint inserted this block immediately before end_frame(),
# which is still inside aurora.cpp's anonymous namespace. That creates an
# internal aurora::{anonymous}::gx::raw symbol instead of the public declaration.
OLD_FLUSH = '''// M14 submit-only readback boundary. Keep ImGui and the logical display
// frame open. The renderer calls this only outside the FIFO parser, after a
// complete command boundary. FIFO caches are packet-local and must be retired.
#ifdef AURORA_ENABLE_GX
namespace gx::raw {
bool submit_for_readback() noexcept {
  gx::fifo::drain();
  if (gfx::is_offscreen()) {
    Log.error("M14 readback cannot split an active offscreen pass");
    return false;
  }
  gx::fifo::end_frame();
  gfx::finish();
  gfx::end_frame([](wgpu::CommandEncoder& encoder,
                    std::vector<gfx::AfterSubmitCallback> callbacks) {
    webgpu::gpu_prof::frame_end(encoder);
    const wgpu::CommandBufferDescriptor desc{.label = "M14 readback segment"};
    const auto buffer = encoder.Finish(&desc);
    g_queue.Submit(1, &buffer);
    webgpu::gpu_prof::after_submit();
    for (auto& callback : callbacks) {
      if (callback) callback();
    }
    gfx::after_submit();
  });
  gfx::synchronize();
  gfx::load_efb_on_readback_continuation();
  if (!gfx::begin_frame()) {
    Log.error("M14 readback continuation staging map failed");
    return false;
  }
  gx::fifo::begin_frame();
  return true;
}
} // namespace gx::raw
#endif

'''

# Brief review checkpoint: public namespace was fixed, but preservation was armed
# after gfx::begin_frame(). begin_frame() creates the initial EFB pass inside
# begin_recording(), so this was too late and would preserve the following frame.
INTERIM_FLUSH = '''// M14 submit-only readback boundary. Keep ImGui and the logical display
// frame open. The renderer calls this only outside the FIFO parser, after a
// complete command boundary. FIFO caches are packet-local and must be retired.
#ifdef AURORA_ENABLE_GX
namespace gx::raw {
namespace {
constexpr Module M14ReadbackLog{"aurora::gx::raw"};
}

bool submit_for_readback() noexcept {
  gx::fifo::drain();
  if (gfx::is_offscreen()) {
    M14ReadbackLog.error("M14 readback cannot split an active offscreen pass");
    return false;
  }
  gx::fifo::end_frame();
  gfx::finish();
  gfx::end_frame([](wgpu::CommandEncoder& encoder,
                    std::vector<gfx::AfterSubmitCallback> callbacks) {
    webgpu::gpu_prof::frame_end(encoder);
    const wgpu::CommandBufferDescriptor desc{.label = "M14 readback segment"};
    const auto buffer = encoder.Finish(&desc);
    webgpu::g_queue.Submit(1, &buffer);
    webgpu::gpu_prof::after_submit();
    for (auto& callback : callbacks) {
      if (callback) callback();
    }
    gfx::after_submit();
  });
  gfx::synchronize();
  if (!gfx::begin_frame()) {
    M14ReadbackLog.error("M14 readback continuation staging map failed");
    return false;
  }
  gfx::load_efb_on_readback_continuation();
  gx::fifo::begin_frame();
  return true;
}
} // namespace gx::raw
#endif

'''

FLUSH = '''// M14 submit-only readback boundary. Keep ImGui and the logical display
// frame open. The renderer calls this only outside the FIFO parser, after a
// complete command boundary. FIFO caches are packet-local and must be retired.
#ifdef AURORA_ENABLE_GX
namespace gx::raw {
namespace {
constexpr Module M14ReadbackLog{"aurora::gx::raw"};
}

bool submit_for_readback() noexcept {
  gx::fifo::drain();
  if (gfx::is_offscreen()) {
    M14ReadbackLog.error("M14 readback cannot split an active offscreen pass");
    return false;
  }
  gx::fifo::end_frame();
  gfx::finish();
  gfx::end_frame([](wgpu::CommandEncoder& encoder,
                    std::vector<gfx::AfterSubmitCallback> callbacks) {
    webgpu::gpu_prof::frame_end(encoder);
    const wgpu::CommandBufferDescriptor desc{.label = "M14 readback segment"};
    const auto buffer = encoder.Finish(&desc);
    webgpu::g_queue.Submit(1, &buffer);
    webgpu::gpu_prof::after_submit();
    for (auto& callback : callbacks) {
      if (callback) callback();
    }
    gfx::after_submit();
  });
  // The render worker must submit the segment before the bbox CopyBufferToBuffer
  // fence is issued on the same ordered queue.
  gfx::synchronize();

  // IMPORTANT: gfx::begin_frame() creates the first EFB RenderPass immediately
  // inside begin_recording(). Arm Load before that happens. If staging acquisition
  // fails before begin_recording(), explicitly cancel the flag so the next normal
  // host frame cannot inherit the continuation policy.
  gfx::load_efb_on_readback_continuation();
  if (!gfx::begin_frame()) {
    gfx::cancel_efb_on_readback_continuation();
    M14ReadbackLog.error("M14 readback continuation staging map failed");
    return false;
  }
  gx::fifo::begin_frame();
  return true;
}
} // namespace gx::raw
#endif

'''

FRAME_DECL_OLD = '''bool begin_frame();\n'''
FRAME_DECL_LOAD_ONLY = '''bool begin_frame();\nvoid load_efb_on_readback_continuation() noexcept;\n'''
FRAME_DECL_NEW = '''bool begin_frame();\nvoid load_efb_on_readback_continuation() noexcept;\nvoid cancel_efb_on_readback_continuation() noexcept;\n'''

RECORDING_DEF_LOAD_ONLY = '''void load_efb_on_readback_continuation() noexcept {\n  sM14LoadReadbackContinuation = true;\n}\n\nvoid finish() {\n'''
RECORDING_DEF_NEW = '''void load_efb_on_readback_continuation() noexcept {\n  sM14LoadReadbackContinuation = true;\n}\n\nvoid cancel_efb_on_readback_continuation() noexcept {\n  sM14LoadReadbackContinuation = false;\n}\n\nvoid finish() {\n'''

AURORA_TAIL = '''} // namespace\n} // namespace aurora\n\n// C API bindings\n'''
AURORA_TAIL_PATCHED = '''} // namespace\n\n''' + FLUSH + '''} // namespace aurora\n\n// C API bindings\n'''


def replace_once_or_present(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if text.count(new) == 1:
        return text, False
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one old/new anchor")
    return text.replace(old, new, 1), True


def validate_result(texts: dict[str, str]) -> None:
    raw_header = texts["include/aurora/gx_raw.hpp"]
    frame_header = texts["lib/gfx/frame.hpp"]
    recording = texts["lib/gfx/recording.cpp"]
    aurora = texts["lib/aurora.cpp"]

    if raw_header.count("bool submit_for_readback() noexcept;") != 1:
        raise RuntimeError("gx_raw.hpp submit_for_readback declaration is not unique")
    if frame_header.count(FRAME_DECL_NEW) != 1:
        raise RuntimeError("frame.hpp readback continuation API is incomplete")
    if recording.count("bool sM14LoadReadbackContinuation = false;") != 1:
        raise RuntimeError("recording.cpp continuation state is missing or duplicated")
    if recording.count(PRESERVE) != 1 or recording.count(RECORDING_DEF_NEW) != 1:
        raise RuntimeError("recording.cpp one-shot continuation implementation is incomplete")
    if aurora.count(FLUSH) != 1 or OLD_FLUSH in aurora or INTERIM_FLUSH in aurora:
        raise RuntimeError("aurora.cpp readback implementation did not converge to the final form")
    if AURORA_TAIL_PATCHED not in aurora:
        raise RuntimeError("submit_for_readback is not in public namespace aurora::gx::raw")
    arm = FLUSH.find("gfx::load_efb_on_readback_continuation();")
    begin = FLUSH.find("if (!gfx::begin_frame())")
    cancel = FLUSH.find("gfx::cancel_efb_on_readback_continuation();")
    if not (0 <= arm < begin < cancel):
        raise RuntimeError("readback continuation arm/begin/cancel ordering is invalid")


def transform(source: Path, check_only: bool = False) -> str:
    paths = {
        "include/aurora/gx_raw.hpp": source / "include/aurora/gx_raw.hpp",
        "lib/gfx/frame.hpp": source / "lib/gfx/frame.hpp",
        "lib/gfx/recording.cpp": source / "lib/gfx/recording.cpp",
        "lib/aurora.cpp": source / "lib/aurora.cpp",
    }
    texts = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    originals = dict(texts)

    texts["include/aurora/gx_raw.hpp"], _ = replace_once_or_present(
        texts["include/aurora/gx_raw.hpp"],
        "const void* display_copy_destination() noexcept;\n",
        "const void* display_copy_destination() noexcept;\n"
        "// Requires an open recording; resumes it without presentation.\n"
        "bool submit_for_readback() noexcept;\n",
        "gx_raw.hpp",
    )

    frame = texts["lib/gfx/frame.hpp"]
    if FRAME_DECL_NEW not in frame:
        if frame.count(FRAME_DECL_LOAD_ONLY) == 1:
            frame = frame.replace(FRAME_DECL_LOAD_ONLY, FRAME_DECL_NEW, 1)
        elif frame.count(FRAME_DECL_OLD) == 1:
            frame = frame.replace(FRAME_DECL_OLD, FRAME_DECL_NEW, 1)
        else:
            raise RuntimeError("frame.hpp readback continuation anchor not found")
    texts["lib/gfx/frame.hpp"] = frame

    recording = texts["lib/gfx/recording.cpp"]
    # dev140 inserts replay state after g_recorder. The existing readback
    # declaration need not remain adjacent; never install a duplicate just
    # because a successor added code between these two declarations.
    if "bool sM14LoadReadbackContinuation = false;" not in recording:
        recording, _ = replace_once_or_present(
            recording,
            "FrameRecorder g_recorder;\n",
            "FrameRecorder g_recorder;\nbool sM14LoadReadbackContinuation = false;\n",
            "recording.cpp state",
        )
    recording, _ = replace_once_or_present(
        recording,
        "  pass.clearDepthValue = gx::clear_depth_value();\n  g_recorder.currentRenderPass = 0;",
        "  pass.clearDepthValue = gx::clear_depth_value();\n" + PRESERVE + "  g_recorder.currentRenderPass = 0;",
        "recording.cpp initial EFB pass",
    )
    recording, _ = replace_once_or_present(
        recording,
        "void shutdown_recording() {\n",
        "void shutdown_recording() {\n  sM14LoadReadbackContinuation = false;\n",
        "recording.cpp shutdown reset",
    )
    if RECORDING_DEF_NEW not in recording:
        if recording.count(RECORDING_DEF_LOAD_ONLY) == 1:
            recording = recording.replace(RECORDING_DEF_LOAD_ONLY, RECORDING_DEF_NEW, 1)
        elif recording.count("void finish() {\n") == 1:
            recording = recording.replace("void finish() {\n", RECORDING_DEF_NEW, 1)
        else:
            raise RuntimeError("recording.cpp continuation helper anchor not found")
    texts["lib/gfx/recording.cpp"] = recording

    aurora = texts["lib/aurora.cpp"]
    for obsolete in (OLD_FLUSH, INTERIM_FLUSH):
        if obsolete in aurora:
            if aurora.count(obsolete) != 1:
                raise RuntimeError("aurora.cpp duplicate obsolete readback implementation")
            aurora = aurora.replace(obsolete, "", 1)
    if FLUSH not in aurora:
        if aurora.count(AURORA_TAIL) != 1:
            raise RuntimeError("aurora.cpp namespace tail anchor not found")
        aurora = aurora.replace(AURORA_TAIL, AURORA_TAIL_PATCHED, 1)
    texts["lib/aurora.cpp"] = aurora

    # Fail before writing if a fresh tree, an Astra-interim tree, or a repeated
    # configure did not converge to the same final source shape.
    validate_result(texts)

    changed = [name for name in paths if texts[name] != originals[name]]
    if not check_only:
        for name in changed:
            paths[name].write_text(texts[name], encoding="utf-8", newline="\n")
    if check_only:
        return "verified"
    return "applied" if changed else "already active"


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != "--check-only"):
        print("usage: apply_readback_boundary_patch.py <aurora-source-dir> [--check-only]", file=sys.stderr)
        return 2
    source = Path(sys.argv[1]).resolve()
    check_only = len(sys.argv) == 3
    try:
        expected = json.loads((HERE / "aurora.lock.json").read_text())["commit"]
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if actual != expected:
            raise RuntimeError(f"Aurora revision mismatch: {actual}")
        print("Aurora submit-only readback " + transform(source, check_only))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM readback adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
