#!/usr/bin/env python3
"""dev140 adapter regressions; optional compiled uniform-preparation behavior.

The normal LLVM source gate calls test_transform() without a compiler. Run
this file with --aurora-source PATH --cxx g++ to also exercise the generated
C++ preparation code against a synthetic recorder and pinned Aurora math.
No game data, window, graphics device, or GPU is required.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def load_adapter(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture() -> dict[str, str]:
    return {
        "lib/gfx/frame_packet.hpp": "struct DrawCommand {\n  int payload;\n};\nstruct FramePacket {\n  AuroraStats stats{};\n};\n",
        "lib/gfx/recording.hpp": "void increment_merged_draw_count() noexcept;\n",
        "lib/gfx/recording.cpp": '#include "frame.hpp"\n#include <array>\n'
        'FrameRecorder g_recorder;\nbool sM14LoadReadbackContinuation = false;\n'
        'void increment_merged_draw_count() noexcept {\n}\n'
        'template <>\nvoid push_draw_command(gx::DrawData data) {\n  push_draw_command(make_draw_command<gx::render>(data));\n}\n',
        "lib/gfx/frame.hpp": "const wgpu::Buffer& staging_buffer(size_t slot);\n",
        "lib/gfx/frame.cpp": "namespace aurora::gfx {\nsize_t acquire_frame_slot() {\n}\n"
        "    auto afterSubmitCallbacks = std::move(packet.afterSubmitCallbacks);\n"
        "    packet = {};\n    g_resources.stats.drawCallCount = stats.drawCallCount;\n"
        "    if (callback) {\n      callback(encoder, std::move(afterSubmitCallbacks));\n"
        "    }\n    g_frameSlots.release(frameSlot);\n",
        "lib/aurora.cpp": "void end_frame() {\n  gfx::finish();\n"
        "  auto imguiDrawData = imgui::freeze();\n}\n"
        "bool submit_for_readback() {\n  gfx::finish();\n}\n",
        "lib/gx/pipeline.hpp": "struct DrawData {\n  uint32_t dstAlpha;\n};\n"
        "\nconstexpr uint32_t GXPipelineConfigVersion = 13;\nstruct PipelineConfig {\n  uint32_t dstAlpha;\n};\n",
        "lib/gx/command_processor.cpp": "  auto m14PushDraw = [&](uint32_t dstAlpha) {\n"
        "    gfx::push_draw_command(DrawData{\n        .dstAlpha = dstAlpha,\n    });\n  };\n",
    }


def test_transform() -> None:
    adapter = load_adapter("apply_interpolation_replay_prep_patch")
    for original_initializer in (False, True):
        with tempfile.TemporaryDirectory(prefix="openspm-dev140-") as tmp:
            root = Path(tmp)
            sources = fixture()
            if original_initializer:
                sources["lib/gx/command_processor.cpp"] = "gfx::push_draw_command(DrawData{\n      .dstAlpha = state.dstAlpha,\n});\n"
            for name, content in sources.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            assert adapter.transform(root, True) == "ok"
            assert all((root / name).read_text() == content for name, content in sources.items())
            assert adapter.transform(root, False) == "applied"
            first = {name: (root / name).read_text() for name in sources}
            assert adapter.transform(root, False) == "already-applied"
            assert first == {name: (root / name).read_text() for name in sources}
            assert "m14Replay" not in first["lib/gx/pipeline.hpp"]
            assert first["lib/gfx/frame_packet.hpp"].count("m14ReplayUniformPrefixBytes") == 1
            assert "\nconstexpr uint32_t GXPipelineConfigVersion = 13;\nstruct PipelineConfig {\n  uint32_t dstAlpha;\n};" in first["lib/gx/pipeline.hpp"]
            assert "bool submit_for_readback() {\n  gfx::finish();\n}" in first["lib/aurora.cpp"]

            # Reproduce a cache with older state and an older preparation body.
            recording = root / "lib/gfx/recording.cpp"
            stale = first["lib/gfx/recording.cpp"].replace(
                adapter.STATE.strip(), adapter.STATE.strip().replace("bool valid = false;", "bool stale = false;"))
            stale = stale.replace(adapter.PREPARE.strip(),
                                  "void m14_prepare_interpolation_replay() noexcept {\n  stale_impl();\n}")
            recording.write_text(stale)
            assert adapter.transform(root, False) == "applied"
            assert recording.read_text() == first["lib/gfx/recording.cpp"]
            assert adapter.transform(root, False) == "already-applied"

            # Both historical inline layouts retire into the outer sidecar.
            pipeline = root / "lib/gx/pipeline.hpp"
            for missing_index in (False, True):
                fields = "  uint32_t m14ReplayUniformPrefixBytes = sizeof(float) * 4u;\n"
                if not missing_index:
                    fields += "  bool m14ReplayIndexedMatrices = false;\n"
                pipeline.write_text(first["lib/gx/pipeline.hpp"].replace(
                    "  uint32_t dstAlpha;\n", "  uint32_t dstAlpha;\n" + fields, 1))
                assert adapter.transform(root, False) == "applied"
                assert pipeline.read_text() == first["lib/gx/pipeline.hpp"]

            # Unknown duplicate regions must fail before any file is written.
            recording.write_text(recording.read_text() + adapter.STATE)
            before = {name: (root / name).read_text() for name in sources}
            try:
                adapter.transform(root, False)
            except RuntimeError as error:
                assert "not unique" in str(error)
            else:
                raise AssertionError("accepted duplicate replay state")
            assert before == {name: (root / name).read_text() for name in sources}


def test_snapshot_successor() -> None:
    adapter = load_adapter("apply_display_copy_snapshot_patch")
    with tempfile.TemporaryDirectory(prefix="openspm-dev140-snapshot-") as tmp:
        root = Path(tmp)
        call = "  gfx::detail::m14_prepare_interpolation_replay();\n"
        sources = {
            "include/aurora/gx_raw.hpp": adapter.RAW_HEADER_NEW,
            "lib/gx/raw.cpp": adapter.RAW_STATE_NEW + adapter.RAW_NOTIFY_NEW,
            "lib/gx/regs.cpp": adapter.REGS_DISPLAY_NEW,
            "lib/aurora.cpp": adapter.AURORA_INCLUDE_NEW +
            adapter.AURORA_FINISH_NEW.replace("  gfx::finish();\n", call + "  gfx::finish();\n") +
            adapter.AURORA_CAPTURE_NEW + adapter.AURORA_BIND_NEW,
        }
        for name, content in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        assert adapter.transform(root, False) == "already-applied"
        assert all((root / name).read_text() == content for name, content in sources.items())


def test_readback_successor() -> None:
    adapter = load_adapter("apply_readback_boundary_patch")
    # Use its exact already-migrated fixture surfaces, separated by dev140 state.
    with tempfile.TemporaryDirectory(prefix="openspm-dev140-readback-") as tmp:
        root = Path(tmp)
        sources = {
            "include/aurora/gx_raw.hpp": "const void* display_copy_destination() noexcept;\n"
            "// Requires an open recording; resumes it without presentation.\n"
            "bool submit_for_readback() noexcept;\n",
            "lib/gfx/frame.hpp": adapter.FRAME_DECL_NEW,
            "lib/gfx/recording.cpp": "FrameRecorder g_recorder;\nstruct M14ReplaySnapshot {};\n"
            "bool sM14LoadReadbackContinuation = false;\n"
            "  pass.clearDepthValue = gx::clear_depth_value();\n" + adapter.PRESERVE +
            "  g_recorder.currentRenderPass = 0;\n"
            "void shutdown_recording() {\n  sM14LoadReadbackContinuation = false;\n}\n" +
            adapter.RECORDING_DEF_NEW,
            "lib/aurora.cpp": adapter.AURORA_TAIL_PATCHED,
        }
        for name, content in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        adapter.transform(root, False)
        assert all((root / name).read_text() == content for name, content in sources.items())


def test_cpp(aurora_source: Path, cxx: str) -> None:
    adapter = load_adapter("apply_interpolation_replay_prep_patch")
    harness = (HERE / "test_interpolation_replay_runtime.cpp").read_text()
    harness = harness.replace("// GENERATED_PACKET_TYPE", adapter.PACKET_TYPE)
    harness = harness.replace("// GENERATED_REPLAY_CODE", adapter.STATE + adapter.PREPARE)
    with tempfile.TemporaryDirectory(prefix="openspm-dev140-cpp-") as tmp:
        source = Path(tmp) / "test.cpp"
        binary = Path(tmp) / "test"
        source.write_text(harness)
        subprocess.run([cxx, "-std=c++20", "-O1", "-g", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer", "-I" + str(aurora_source / "include"),
                        str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aurora-source", type=Path)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    test_transform()
    test_snapshot_successor()
    test_readback_successor()
    if args.aurora_source:
        test_cpp(args.aurora_source.resolve(), args.cxx)
    print("PASS: dev140 replay preparation regressions")


if __name__ == "__main__":
    main()
