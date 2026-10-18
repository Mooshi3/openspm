#!/usr/bin/env python3
"""Vanilla display-pacing policy + native clock-reader contracts.

Compiles the pure display_pacing.hpp policy with the host C++ compiler and
checks it against real panel refresh rates, then checks that the renderer
publishes and the native bridge consumes the same OPENSPM_M14_RETRACE_PERIOD_NS
contract with matching bounds.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

CASES = r'''
#include "display_pacing.hpp"
#include <cassert>
#include <cstdio>
using namespace openspm::pacing;
static void locked(uint64_t n, uint64_t d, uint32_t k, uint64_t period) {
  const auto p = choose_vanilla_pacing(n, d);
  if (!p.locked || p.multiple != k || p.period_ns != period) {
    std::printf("unexpected %llu/%llu -> locked=%d k=%u period=%llu\n",
                (unsigned long long)n, (unsigned long long)d, p.locked, p.multiple,
                (unsigned long long)p.period_ns);
    assert(false);
  }
}
static void unlocked(uint64_t n, uint64_t d) { assert(!choose_vanilla_pacing(n, d).locked); }
int main() {
  locked(59978, 1000, 1, 16672780);   // LG ultrawide 59.978 Hz mode
  locked(60000, 1001, 1, 16683333);   // NTSC 59.94
  locked(60, 1, 1, 16666667);
  locked(120000, 1001, 2, 16683333);  // 119.88
  locked(120, 1, 2, 16666667);
  locked(240, 1, 4, 16666667);
  unlocked(99943, 1000);              // not a multiple of 60: judder, stay 60.000
  unlocked(74991, 1000);
  unlocked(50, 1);
  unlocked(144, 1);
  unlocked(165, 1);
  unlocked(0, 1);
  unlocked(60, 0);
  // Exclusive fullscreen refresh choice at the desktop resolution.
  const RefreshCandidate lg[] = {{99943, 1000}, {74991, 1000}, {59978, 1000}, {50, 1}};
  assert(choose_exclusive_refresh(lg, 4, 0.0) == 2);    // auto -> 59.978 (vanilla lock)
  assert(choose_exclusive_refresh(lg, 4, 75.0) == 1);   // explicit request -> nearest
  assert(choose_exclusive_refresh(lg, 4, 144.0) == -1); // not offered
  const RefreshCandidate hi[] = {{60, 1}, {120, 1}, {144, 1}, {240, 1}, {165, 1}};
  assert(choose_exclusive_refresh(hi, 5, 0.0) == 3);    // highest locked multiple: 240
  const RefreshCandidate ntsc[] = {{60000, 1001}, {60, 1}};
  assert(choose_exclusive_refresh(ntsc, 2, 0.0) == 1);  // tie -> closest to 60.000
  const RefreshCandidate none[] = {{144, 1}, {165, 1}};
  assert(choose_exclusive_refresh(none, 2, 0.0) == -1); // keep desktop refresh
  return 0;
}
'''


def test_policy() -> None:
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("SKIP: no C++ compiler for display pacing policy")
        return
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "pacing.cpp"
        exe = pathlib.Path(tmp) / "pacing"
        src.write_text(CASES, encoding="utf-8")
        subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", f"-I{HERE}",
                        str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


def test_contract() -> None:
    renderer = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    bridge = (ROOT / "tools" / "native_bridge" / "legacy_c_adapter_v7.c.in").read_text(encoding="utf-8")
    assert '#include "display_pacing.hpp"' in renderer
    assert 'm14_publish_env("OPENSPM_M14_RETRACE_PERIOD_NS", period);' in renderer
    assert "out_api->initialize = renderer_initialize_display_pacing;" in renderer
    assert 'std::getenv("OPENSPM_WINDOW_MODE")' in renderer
    assert "AuroraSetViewportPolicy(output.stretch_policy ? AURORA_VIEWPORT_STRETCH : AURORA_VIEWPORT_FIT);" in renderer
    assert "openspm::aspect::choose_output(stretch, widescreen);" in renderer
    assert renderer.index("m14_apply_output_aspect();") < renderer.index("m14_apply_display_pacing();\n    }")
    assert "choose_exclusive_refresh(" in renderer
    # Window mode is applied before the pacing decision reads the active mode.
    assert renderer.index("m14_apply_window_mode(window, display, desktop)") < renderer.index(
        "choose_vanilla_pacing(numerator, denominator)")
    assert 'bridge_m14_read_env("OPENSPM_M14_RETRACE_PERIOD_NS"' in bridge
    assert "GetEnvironmentVariableA" in bridge
    # The bridge must accept every period the policy can emit (59.7..60.3 Hz).
    lo = int(re.search(r"MIN_SYNCED_RETRACE_PERIOD_NS UINT64_C\((\d+)\)", bridge).group(1))
    hi = int(re.search(r"MAX_SYNCED_RETRACE_PERIOD_NS UINT64_C\((\d+)\)", bridge).group(1))
    assert lo <= round(1e9 / 60.3) and hi >= int(1e9 / 59.7)
    # dev132 diagnostic keeps priority; default stays exactly 60 Hz.
    assert "OPENSPM_M14_DEFAULT_RETRACE_PERIOD_NS UINT64_C(16666667)" in bridge
    body = bridge[bridge.index("static uint64_t bridge_m14_retrace_period_ns(void)"):]
    assert body.index("g_m14_dev132_half_monitor_cadence) return") < body.index(
        "g_m14_display_synced_period_ns != 0u) return")


def test_present_timeline() -> None:
    """Stage A present timeline: scheduler simulation + opt-in wiring contract."""
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("SKIP: no C++ compiler for present timeline simulation")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "present_timeline"
            subprocess.run([cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{HERE}",
                            str(ROOT / "tests" / "m14_present_timeline_test.cpp"), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe), "-v"], capture_output=True, text=True)
            print(result.stdout, end="")
            assert result.returncode == 0, "present timeline simulation failed"
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    # Last presenter patch: after dev139, bbox priority and visible interpolation.
    order = [cmake.index(f"renderer/aurora/{name}") for name in (
        "apply_async_presenter_probe_patch.py", "apply_bbox_priority_patch.py",
        "apply_interpolation_present_patch.py", "apply_present_timeline_patch.py")]
    assert order == sorted(order)
    assert cmake.index("apply_present_timeline_patch.py") < cmake.index(
        'add_subdirectory("${OPENSPM_AURORA_SOURCE_DIR}"')
    glue = (HERE / "present_timeline_presenter.inl").read_text(encoding="utf-8")
    patch = (HERE / "apply_present_timeline_patch.py").read_text(encoding="utf-8")
    # Opt-in only, and only on top of the async presenter.
    assert 'std::getenv("OPENSPM_M14_PRESENT_TIMELINE")' in glue
    assert "return m14_async_presenter_enabled() && value != nullptr && value[0] == '1'" in glue
    assert "return m14_present_timeline_enabled() ? 4u : 2u;" in glue  # dev139 ring unchanged when off
    # WebGPU stays on the render worker; FIFO present return is the vblank clock.
    assert "gfx::render_worker::enqueue_work([show, frameA, frameB, alpha] {" in glue
    assert "g_m14Timeline.timeline.on_present(returnedNs);" in glue
    assert "gx::bbox::readback_pending()" in glue
    assert "if (m14_present_timeline_enabled()) {" in patch
    renderer = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    assert 'm14_publish_env("OPENSPM_M14_DISPLAY_REFRESH", refresh);' in renderer
    assert ('m14_env_is_on("OPENSPM_M14_ASYNC_PRESENTER") && !present_timeline) skip = '
            '"interpolated/async presenter run";') in renderer
    # The display rational is only published for timeline runs (default path unchanged).
    publish = renderer.index('m14_publish_env("OPENSPM_M14_DISPLAY_REFRESH"')
    assert renderer.rindex("if (present_timeline && numerator != 0u", 0, publish) > renderer.index(
        "void m14_apply_display_pacing()")
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRESENT_TIMELINE="' in launcher
    assert launcher.index('set "OPENSPM_M14_ASYNC_PRESENTER="') < launcher.index(
        'set "OPENSPM_M14_PRESENT_TIMELINE=1"')
    assert 'if /i "%%~A"=="timeline" set "OPENSPM_TIMELINE_ARG=smooth"' in launcher


def test_draw_tags() -> None:
    """Stage B draw tags: synthetic FIFO round trip + opt-in wiring contract."""
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("SKIP: no C++ compiler for draw tags test")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "draw_tags"
            subprocess.run([cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{HERE}",
                            f"-I{ROOT / 'tools' / 'native_bridge'}",
                            str(ROOT / "tests" / "m14_draw_tags_test.cpp"), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(result.stdout, end="")
            assert result.returncode == 0, "draw tags test failed: " + result.stderr[-2000:]
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    # Last in the chain, after the present timeline, before Aurora is added.
    assert cmake.index("apply_present_timeline_patch.py") < cmake.index(
        "renderer/aurora/apply_draw_tags_patch.py") < cmake.index('add_subdirectory("${OPENSPM_AURORA_SOURCE_DIR}"')
    patch = (HERE / "apply_draw_tags_patch.py").read_text(encoding="utf-8")
    header = (HERE / "draw_tags.hpp").read_text(encoding="utf-8")
    assert 'std::getenv("OPENSPM_M14_DRAW_TAGS")' in patch
    assert "if (!runtime.enabled) {\n    return;" in patch  # stamp is a no-op when off
    assert "regs[0x7E] = {bp_m14_draw_tag" in patch and "regs[0x7F] = {bp_m14_draw_tag" in patch
    assert "g_gxState.dirty |= DirtyImmediates;" in patch  # no merge across owners
    assert "inline constexpr std::uint8_t kOwnerReg = 0x7E;" in header
    bridge = (ROOT / "tools" / "native_bridge" / "legacy_c_adapter_v42_dev69.c.in").read_text(encoding="utf-8")
    assert 'getenv("OPENSPM_M14_DRAW_TAGS")' in bridge
    assert ("    if (!bridge_m14_draw_tags_enabled())\n"
            "        return bridge_dev105_cpustate_nested_dispatch(cpu, target, continuation);") in bridge
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DRAW_TAGS="' in launcher
    assert 'if /i "%%~A"=="drawtags" set "OPENSPM_M14_DRAW_TAGS=1"' in launcher


def test_frame_interp() -> None:
    """Stage C frame interpolation: model tests + benchmark, opt-in wiring contract."""
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("SKIP: no C++ compiler for frame interp test")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "frame_interp"
            subprocess.run([cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{HERE}",
                            str(ROOT / "tests" / "m14_frame_interp_test.cpp"), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(result.stdout, end="")
            assert result.returncode == 0, "frame interp test failed: " + result.stderr[-2000:]
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    # Last in the chain: after the timeline and the draw tags it builds on.
    assert cmake.index("renderer/aurora/apply_draw_tags_patch.py") < cmake.index(
        "renderer/aurora/apply_frame_interp_patch.py") < cmake.index('add_subdirectory("${OPENSPM_AURORA_SOURCE_DIR}"')
    patch = (HERE / "apply_frame_interp_patch.py").read_text(encoding="utf-8")
    glue = (HERE / "frame_interp_presenter.inl").read_text(encoding="utf-8")
    timeline = (HERE / "present_timeline_presenter.inl").read_text(encoding="utf-8")
    # Opt-in on top of the smooth timeline; recording keeps no mirror otherwise.
    assert 'std::getenv("OPENSPM_M14_INTERP")' in glue and "m14_present_timeline_enabled()" in glue
    assert 'on("OPENSPM_M14_INTERP") && on("OPENSPM_M14_ASYNC_PRESENTER") && on("OPENSPM_M14_PRESENT_TIMELINE")' in patch
    assert "if (!m14_frame_interp_enabled()) return;" in glue
    # Presenter: interpolated image per vblank, B (never A) when not replay-safe.
    assert "if (interp && haveB) image = m14_frame_interp_choose(frameA, frameB, alpha, imageA, imageB);" in timeline
    assert glue.count("return imageB;") >= 3
    # WebGPU work stays on the render worker; replayed draws use a private bbox.
    assert "gx::bbox::ReplayScope privateBbox;" in patch
    assert "g_queue.Submit(1, &buffer);" in glue and "g_m14PresenterQueueMutex" in glue
    # Replay-unsafe frames: EFB copies, bbox reads, tasks, prior loads.
    for reason in ('"efb-copy"', '"texture-copy"', '"bbox-read"', '"encoder-task"', '"prior-load"',
                   '"later-copy"', '"no-display-copy"'):
        assert reason in glue, reason
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_INTERP="' in launcher
    assert 'if /i "%%~A"=="interp" set "OPENSPM_M14_INTERP=1"' in launcher
    body = launcher[launcher.index('if "%OPENSPM_M14_INTERP%"=="1" ('):]
    assert body.index('set "OPENSPM_TIMELINE_ARG=smooth"') < body.index('set "OPENSPM_M14_DRAW_TAGS=1"') < body.index(
        "if defined OPENSPM_TIMELINE_ARG (")
    # The bridge sends the game's camera views the model interpolates.
    bridge = (ROOT / "tools" / "native_bridge" / "legacy_c_adapter_v42_dev69.c.in").read_text(encoding="utf-8")
    assert "#define OPENSPM_M14_DRAW_TAGS_CAM_VIEW_OFF UINT32_C(0x00000054)" in bridge
    assert "if (!ortho) bridge_m14_draw_tags_view(instance, cpu, camera, cam);" in bridge
    assert "g_m14_draw_tags.view_sent = 0u;" in bridge
    tags = (HERE / "apply_draw_tags_patch.py").read_text(encoding="utf-8")
    assert "regs[0x7C] = {bp_m14_draw_tag" in tags and "regs[0x7D] = {bp_m14_draw_tag" in tags


def test_bbox_fast() -> None:
    """bbox fast path: model unit test (proofs, 2x2 rounding) + opt-in wiring contract."""
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("SKIP: no C++ compiler for bbox fast test")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "bbox_fast"
            subprocess.run([cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{HERE}",
                            str(ROOT / "tests" / "m14_bbox_fast_test.cpp"), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(result.stdout, end="")
            assert result.returncode == 0, "bbox fast test failed: " + result.stdout[-2000:]
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    # Last in the chain: after the bbox logical scale / memo patches it builds on.
    assert cmake.index("apply_efb_copy_ram_writeback_patch.py") < cmake.index(
        "renderer/aurora/apply_frame_interp_patch.py") < cmake.index(
        "renderer/aurora/apply_bbox_fast_patch.py") < cmake.index('add_subdirectory("${OPENSPM_AURORA_SOURCE_DIR}"')
    patch = (HERE / "apply_bbox_fast_patch.py").read_text(encoding="utf-8")
    header = (HERE / "bbox_fast.hpp").read_text(encoding="utf-8")
    # Opt-in: "1" or "verify" only; every hook returns at once when off.
    assert 'std::getenv("OPENSPM_M14_BBOX_FAST")' in patch
    assert 'if (std::strcmp(value, "1") == 0) return kModeOn;' in header
    assert "if (runtime.mode == openspm_bbox_fast::kModeOff || !runtime.window.wants_draw()) return;" in patch
    assert "if (runtime.mode != openspm_bbox_fast::kModeOff) runtime.window.note_write(index, hostValue);" in patch
    # Every draw is classified from what the GPU reads: the recorded draw's
    # immediates (matrix slot, position array start) and the staged storage bytes.
    assert "recorded.immediateData.arrayStart[0]" in patch and "recorded.immediateData.currentPnMtx" in patch
    assert "gfx::m14_bbox_fast_storage_bytes(address, elementSize)" in patch
    assert "return kNotEmpty;  // lines and points are widened in screen space" in patch
    # Both draw paths are hooked, and every bbox store (BP + CPU) passes queue_write.
    assert "m14_bbox_fast_draw(prim, fmt, vtxCount, vertexData, lastDraw);" in patch
    assert "m14_bbox_fast_draw(prim, fmt, vtxCount, vertexData, nullptr);" in patch
    assert "fifo::m14_bbox_fast_note_write(index, value);" in patch
    logical = (HERE / "apply_bbox_logical_scale_patch.py").read_text(encoding="utf-8")
    assert "raw::write_bbox(base, static_cast<u16>(reg_get(value, 10, 0)));" in logical
    assert "bbox::queue_write(index, static_cast<std::uint16_t>(std::clamp(px, 0.0, 16383.0)));" in logical
    # The CPU answer is converted exactly like read_bbox (same statements).
    for statement in ("const double logical = std::floor(static_cast<double>(",
                      "out[i] = static_cast<std::uint16_t>(std::clamp(logical, 0.0, 1023.0));",
                      "out[i] = static_cast<std::uint16_t>(std::min<std::uint32_t>(",
                      "if (s == 1.0) {",
                      "out[0] = static_cast<std::uint16_t>(out[0] & ~1u);",
                      "out[2] = static_cast<std::uint16_t>(out[2] & ~1u);",
                      "out[1] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[1] | 1u, 1023u));",
                      "out[3] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[3] | 1u, 1023u));"):
        assert statement in header and statement in logical, statement
    assert "& 0x3fffu" in header and "mapped[i] & 0x3fffu" in logical
    assert "logical_from_host(host, bbox_axis_scale(0), bbox_axis_scale(2), bbox_logical_enabled())" in patch
    renderer = (HERE / "openspm_renderer_aurora.cpp").read_text(encoding="utf-8")
    body = renderer[renderer.index("int OPENSPM_CALL read_bounds("):renderer.index("int OPENSPM_CALL write_bound(")]
    # The FIFO is parsed (every draw classified) before the CPU answer; verify
    # never trusts the memo and compares with the GPU readback.
    assert body.index("aurora::gx::raw::drain();") < body.index("aurora::gx::raw::m14_bbox_fast_read(") < body.index(
        "aurora::gx::raw::submit_for_readback()")
    assert "if (fast_hit && fast_mode == 1) {" in body
    assert "fast_mode != 2 && state->frame_open && aurora::gx::raw::m14_bbox_memo_lookup(bounds)" in body
    assert "if (fast_hit) m14_bbox_fast_verify(fast_bounds, fast_reason, bounds);" in body
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    assert '"M14 bbox fast: mode=%s reads=%llu cached=%llu fast=%llu (no_draws=%llu "' in v6
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    assert 'if /i "%%~A"=="bboxfast" set "OPENSPM_M14_BBOX_FAST=1"' in launcher
    assert 'if /i "%%~A"=="bboxfastverify" set "OPENSPM_M14_BBOX_FAST=verify"' in launcher
    tests = (ROOT / "docs" / "TEST-LIST.md").read_text(encoding="utf-8")
    assert "bboxfastverify" in tests and "M14 bbox fast:" in tests


def test_finish_before_retrace_contract() -> None:
    """A completed DrawDone must be delivered before a due retrace (33 ms drop)."""
    nb = ROOT / "tools" / "native_bridge"
    v7 = (nb / "legacy_c_adapter_v7.c.in").read_text(encoding="utf-8")
    v37 = (nb / "legacy_c_adapter_v37.c.in").read_text(encoding="utf-8")
    service = v7[v7.index("static int bridge_m14_service_retrace("):]
    hook = service.index("g_m14_pre_retrace_finish_hook(instance)")
    assert service.index("OPENSPM_M14_MSR_EE) == 0u) return OPENSPM_OK;") < hook
    assert hook < service.index("OPENSPM_M14_VI_RETRACE_HANDLER")
    assert "OPENSPM_M14_FINISH_BEFORE_RETRACE" in v7
    assert "g_m14_pre_retrace_finish_hook = bridge_dev47_pre_retrace_finish;" in v37
    assert "g_m14_pre_retrace_finish_hook = NULL;" in v37
    # Bounded hold: a finish woke the main thread but it has not reached
    # VIWaitForRetrace yet; hold (never skip) the grid-anchored retrace.
    hold = service.index("g_m14_finish_since_retrace &&")
    assert hook < hold < service.index("OPENSPM_M14_VI_RETRACE_HANDLER")
    assert "OPENSPM_M14_FINISH_HOLD_MAX_NS UINT64_C(4000000)" in v7
    assert "g_m14_finish_since_retrace = 0u;\n    /* No catch-up burst." in v7
    assert "if (g_m14_finish_since_retrace == 0u) g_m14_finish_since_retrace = 1u;" in v37
    # v7 must precede v37 in the single-TU include chain so the hook is declared.
    chain, name = [], "legacy_c_adapter_v42.c.in"
    while name:
        chain.append(name)
        m = re.search(r'#include "(legacy_c_adapter[^"]*)"', (nb / name).read_text(encoding="utf-8"))
        name = m.group(1) if m else None
    assert chain.index("legacy_c_adapter_v37.c.in") < chain.index("legacy_c_adapter_v7.c.in")


def test_bbox_logical_contract() -> None:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake.index("apply_bbox_priority_patch.py") < cmake.index(
        "apply_bbox_logical_scale_patch.py") < cmake.index("apply_interpolation_replay_prep_patch.py")
    patch = (HERE / "apply_bbox_logical_scale_patch.py").read_text(encoding="utf-8")
    # Wii bbox granularity: left/top even, right/bottom odd, in the game's space.
    assert "out[0] = static_cast<std::uint16_t>(out[0] & ~1u);" in patch
    assert "std::min<std::uint32_t>(out[1] | 1u, 1023u)" in patch and "RAW_NEW_V2 in raw" in patch
    # Vec2 exposes x/y fields: structured bindings, never accessor calls.
    assert "const auto [logicalWidth, logicalHeight] = logical_fb_size();" in patch
    assert ".x()" not in patch and ".y()" not in patch
    assert cmake.index("apply_bbox_logical_scale_patch.py") < cmake.index(
        "apply_copy_trace_diag_patch.py") < cmake.index("apply_interpolation_replay_prep_patch.py")
    trace = (HERE / "apply_copy_trace_diag_patch.py").read_text(encoding="utf-8")
    assert "OPENSPM_M14_COPY_TRACE" in trace and "value[0] == '1'" in trace  # opt-in only
    assert cmake.index("apply_copy_trace_diag_patch.py") < cmake.index(
        "apply_efb_copy_ram_writeback_patch.py") < cmake.index("apply_interpolation_replay_prep_patch.py")
    ram = (HERE / "apply_efb_copy_ram_writeback_patch.py").read_text(encoding="utf-8")
    assert "OPENSPM_M14_EFB_COPY_RAM" in ram and "value[0] == '1'" in ram  # opt-in only
    assert "gfx::render_worker::enqueue_work(work)" in ram  # Dawn work stays on the render worker
    renderer = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    hook = renderer.index("aurora::gx::raw::efb_copy_writeback_pending()")
    assert hook < renderer.index("result = dev7_present_completed_copy_after_drawdone(*state);")


def test_copy_effects_contract() -> None:
    import math
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake.index("apply_efb_copy_ram_writeback_patch.py") < cmake.index(
        "apply_copy_pixel_exact_patch.py") < cmake.index("apply_interpolation_replay_prep_patch.py")
    exact = (HERE / "apply_copy_pixel_exact_patch.py").read_text(encoding="utf-8")
    assert "OPENSPM_M14_COPY_PIXEL_EXACT" in exact and "value[0] != '0'" in exact  # off-switch
    # Fill-rule model: first covered pixel = ceil(v*s - 0.5); star door x=238 at 2.25x.
    first = lambda v, s: math.ceil(v * s - 0.5)
    assert first(238, 2.25) == 535 and first(1, 2.25) == 2 and first(3, 2.25) == 7 and first(5, 1.0) == 5
    assert round(88 * 2.25) == 198
    ram = (HERE / "apply_efb_copy_ram_writeback_patch.py").read_text(encoding="utf-8")
    assert "OPENSPM_M14_COPY_TAGS" in ram and "value == nullptr || value[0] != '0'" in ram  # off-switch
    # Copy tracking must never write guest RAM (the RAM tag corrupted the wedding TPL),
    # and ordinary texture binds must not hash or scan (the fingerprint scan cost ~15 fps).
    tracking = ram.split("void m14_copy_tag_note(")[1].split("namespace raw {")[0]
    assert "memcpy(const_cast" not in tracking and "fingerprint" not in tracking
    assert "const u32 key = guestAddress & 0x1FFFFFFFu;" in ram and "find(snap.source & 0x1FFFFFFFu)" in ram
    assert "0x8056C9D8u" in ram and "0x8056C920u" in ram  # r13-32552 / r13-32736 (r13 = 0x80574900)
    resolve = tracking.split("gfx::TextureHandle m14_resolve_tagged_copy(")[1]
    assert resolve.index("sM14SnapshotByHost.find(data)") < resolve.index("sM14MissScans >= 4u")
    renderer = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    base = (HERE / "openspm_renderer_aurora.cpp").read_text(encoding="utf-8")
    # bbox memo: opt-in; the FIFO is parsed before the lookup so the key covers every prior draw.
    assert "value != nullptr && value[0] == '1'" in ram.split("bool m14_bbox_memo_enabled()")[1][:300]
    assert base.index("aurora::gx::raw::drain();\n        aurora::gx::raw::m14_snapshot_sync();") > 0
    # Snapshots take the newest capture version of their size at their first load.
    # ...and only a version the size of its capture rectangle (captures of other sizes reuse the buffer).
    assert "if (near(v->width, width) && near(v->height, height)) {" in ram
    assert "sM14VersionPixels > (24ull << 20)" in ram
    assert "bool memoizable = !state.depthCompare;" in ram
    # The per-draw memo hash reads matrices + vertex data: never on the default path (44 fps).
    assert "CP_OLD + r'''  if (::aurora::gx::raw::m14_bbox_memo_wants_draw()) {" in ram
    assert "bool m14_take_copy_capture_request() noexcept;" in renderer
    assert renderer.index("aurora::gx::raw::m14_snapshot_sync();") < renderer.index(
        "if (aurora::gx::raw::efb_copy_writeback_pending()) {")
    assert "bool m14_take_copy_capture_request() noexcept {" in ram


def test_viewport_origin_contract() -> None:
    """GX viewport origin 342 (game SDK + scissor), revision 2 of the copy patch."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("copy_exact_patch", HERE / "apply_copy_pixel_exact_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    assert "OPENSPM_M14_VIEWPORT_ORIGIN" in patch.ORIGIN_BODY and "value[0] == '0' ? 340.0f : 342.0f" in patch.ORIGIN_BODY
    # The reopened namespace must not duplicate the draw-tags anchor.
    assert "namespace aurora::gx::fifo {\nnamespace {\n" not in patch.REGS_HELPER

    # Model: SDK encode ox = left + w/2 + 342 (0x802923D0, 342.0 at 0x80575B84);
    # Aurora decode left = ox - origin - w/2. Star door: capture rect from bbox of
    # the drawn door, halves drawn at the capture rect -> offset vs the scene door.
    def door_offset(origin: float) -> float:
        intended, pad = 322.0, 2.0
        drawn = (intended + 43.0 + 342.0) - origin - 43.0  # every draw's viewport shift
        rect = drawn - pad                                  # bbox of what was drawn, padded
        halves = (rect + 43.0 + 342.0) - origin - 43.0      # quad at the rect, same shift
        return (halves + pad) - drawn                       # halves' door vs scene door
    assert door_offset(340.0) == 2.0 and door_offset(340.0) * 2.25 == 4.5  # the bug: 4.5 host px
    assert door_offset(342.0) == 0.0

    # Transform: fresh, idempotent, and migration from a revision-1 cache.
    regs_src = ("namespace aurora::gx::fifo {\nnamespace {\n" + patch.REGS_ANCHOR +
                "  set_logical_viewport({\n" + patch.REGS_VIEWPORT_OLD + "  });\n}\n} // namespace\n}\n")
    xform_src = patch.TRANSFORM_ANCHOR + "\nvoid f() {\n" + patch.TRANSFORM_OLD + "}\n"
    fb_fresh = patch.INCLUDE_ANCHOR + "void copy_tex() {\n" + patch.RECT_OLD + "}\n"
    fb_rev1 = patch.HELPER + "void copy_tex() {\n" + patch.RECT_NEW + "}\n"
    for fb_src in (fb_fresh, fb_rev1):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for rel, text in ((patch.FRAMEBUFFER, fb_src), (patch.REGS, regs_src), (patch.TRANSFORM, xform_src)):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            assert patch.transform(root, check_only=True) == "pending"
            assert patch.transform(root) == "applied"
            assert patch.transform(root) == "already active"
            regs = (root / patch.REGS).read_text(encoding="utf-8")
            xform = (root / patch.TRANSFORM).read_text(encoding="utf-8")
            assert "340.0f" not in regs.replace("? 340.0f : 342.0f", "")
            assert "340.0f" not in xform.replace("? 340.0f : 342.0f", "")
            assert regs.count("namespace aurora::gx::fifo {\nnamespace {\n") == 1
            assert (root / patch.FRAMEBUFFER).read_text(encoding="utf-8").count(patch.RECT_NEW) == 1


def test_settings_contract() -> None:
    import shutil
    import subprocess
    import tempfile
    native_main = (ROOT / "launcher" / "native_main.c").read_text(encoding="utf-8")
    assert '#include "native_settings.h"' in native_main
    # Settings become environment defaults before any DLL (and its CRT) loads.
    assert native_main.index("native_apply_settings(app);") < native_main.index("LoadLibraryW(backend_path)")
    # Mods are opt-in and wrap the game session.
    # First-run game picker: only without --game (the .cmd flow passes a folder,
    # used as given); OpenSPM.ini [Game] path is read before any DLL loads.
    assert '#include "native_game.h"' in native_main
    assert "if (!file_exists(arg)) root = _wcsdup(arg);" in native_main
    assert native_main.index("native_resolve_game(app,") < native_main.index("LoadLibraryW(backend_path)")
    assert '"Game", "path"' in native_main and "GetOpenFileNameW" in native_main
    assert "--psel DATA --pmode NONE" in native_main  # same wit extraction as tools/m14_lab/resume_m14.py
    assert "[Game]" in (ROOT / "OpenSPM.ini.example").read_text(encoding="utf-8")
    assert "if (native_mods_enabled()) {" in native_main and "value[0] == '1'" in native_main
    assert (native_main.index("OPENSPM_MOD_EVENT_RUNTIME_RUNNING") < native_main.index("rc = backend.run(")
            < native_main.index("OPENSPM_MOD_EVENT_RUNTIME_STOPPED"))
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    assert 'OPENSPM_SETTINGS=%~dp0OpenSPM.ini' in launcher
    assert 'set "OPENSPM_ASPECT=4:3"' not in launcher and 'set "OPENSPM_WINDOW_MODE=windowed"' not in launcher
    example = (ROOT / "OpenSPM.ini.example").read_text(encoding="utf-8")
    settings = (ROOT / "launcher" / "native_settings.h").read_text(encoding="utf-8")
    # [Display] frame_mode sets the presenter group the .cmd flags set; the .cmd
    # clears (never zeroes) it without flags, so the setting reaches the renderer.
    group = settings[settings.index("openspm_frame_mode_env[OPENSPM_FRAME_MODE_VARS] = {"):]
    group = group[:group.index("};")]
    for var in ("OPENSPM_M14_ASYNC_PRESENTER", "OPENSPM_M14_PRESENT_TIMELINE", "OPENSPM_M14_PRESENT_TIMELINE_MODE",
                "OPENSPM_M14_DRAW_TAGS", "OPENSPM_M14_INTERP"):
        assert f'"{var}"' in group, var
        assert f'set "{var}="' in launcher and f'set "{var}=0"' not in launcher, var
    assert '{"smooth", {"1", "1", "smooth", "1", "1"}}' in settings
    assert '{"vanilla", {NULL, NULL, NULL, NULL, NULL}}' in settings
    for key in ("window_mode", "refresh_hz", "aspect", "widescreen", "display_sync", "frame_mode",
                "interp_camera_views", "copy_tags", "copy_pixel_exact", "bbox_logical"):
        assert f"{key} =" in example and f'"{key}"' in settings
    base = (HERE / "openspm_renderer_aurora.cpp").read_text(encoding="utf-8")
    assert "SDL_SCANCODE_F12" in base and "m14_save_screenshot(path)" in (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    assert "SDL_SCANCODE_F1)" in base and "OPENSPM_OVERLAY" in overlay
    # Every OpenSPM.ini setting is editable from the menu. Keyboard / gamepad
    # navigation only while the game is paused behind the menu (the backend
    # reads game keys globally, so without the pause arrows/Enter would drive both).
    import re
    table = re.findall(r'\{"(\w+)", "(\w+)", "OPENSPM_\w+", "', settings)
    assert len(table) >= 11
    for section, key in table:
        assert f'"{section}", "{key}"' in overlay, f"OpenSPM menu cannot edit [{section}] {key}"
    nav = overlay.split("inline void sync_navigation()")[1].split("\n}\n")[0]
    assert "g_nav_active = g_open && menu_pauses_game();" in nav
    assert "io.ConfigFlags &= ~nav;" in nav and overlay.count("ImGuiConfigFlags_NavEnable") == 2
    assert 'env_value("OPENSPM_PAUSE_GAME")' in overlay.split("inline bool menu_pauses_game()")[1].split("}")[0]
    assert overlay.count('begin_tab("') == 9 and "ImGui::BeginTabItem(\"" not in overlay
    # ImGui only between Aurora's begin/end frame; viewport policy only after it.
    assert v6.index("openspm_overlay::draw();") < v6.index("aurora_end_frame();", v6.index("openspm_overlay::draw();"))
    assert v6.index("openspm_overlay::after_frame();") > v6.index("openspm_overlay::draw();")
    assert "AuroraSetViewportPolicy" not in overlay.split("inline void after_frame()")[0]
    assert "OPENSPM_HIDE_CURSOR" in base and "SDL_HideCursor()" in base
    # Mode switches and settled window drags apply the new size explicitly.
    assert "m14_request_graphics_resize();" in base.split("void toggle_fullscreen_hotkey()")[1]
    assert "if (event->type == AURORA_WINDOW_RESIZED) m14_note_window_resized(event->windowSize);" in base
    # Aurora posts WINDOW_RESIZED for our own apply: same size must not re-arm it (300 ms loop).
    assert "if (size.native_fb_width == s_width && size.native_fb_height == s_height) return;" in base
    assert base.index("namespace aurora::window {") < base.index("namespace {")
    assert "openspm_renderer_gamepad_state" in v6
    assert "SDL_SCANCODE_F11" in base and "SDL_KMOD_ALT" not in base and "OPENSPM_FULLSCREEN_HOTKEY" in base
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "settings_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                            str(ROOT / "tests" / "m14_native_settings_test.c")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)
            cxx = shutil.which("g++") or shutil.which("clang++")
            if cxx:
                ini = pathlib.Path(tmp) / "ini_edit_test"
                subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o",
                                str(ini), str(ROOT / "tests" / "m14_ini_edit_test.cpp")], check=True)
                subprocess.run([str(ini)], check=True, capture_output=True)
            game = pathlib.Path(tmp) / "game_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-o", str(game),
                            str(ROOT / "tests" / "m14_native_game_test.c")], check=True)
            subprocess.run([str(game)], check=True, capture_output=True)
            pad = pathlib.Path(tmp) / "pad_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-o", str(pad),
                            str(ROOT / "tests" / "m14_pad_map_test.c")], check=True)
            subprocess.run([str(pad)], check=True, capture_output=True)
    v9 = (ROOT / "backend" / "native" / "openspm_native_backend_v9.c").read_text(encoding="utf-8")
    assert '#include "openspm_pad_xinput.h"' in v9 and "uint32_t down = openspm_pad_down();" in v9
    assert "if (!v9_keyboard_focus_ok()) return down;" in v9 and "OPENSPM_INPUT_BACKGROUND" in v9


def test_cheats_contract() -> None:
    """Cheats: the menu publishes a table, the backend applies it at the safepoint."""
    header = (ROOT / "include" / "openspm" / "cheats.h").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    v9 = (ROOT / "backend" / "native" / "openspm_native_backend_v9.c").read_text(encoding="utf-8")
    ini = (HERE / "openspm_ini.hpp").read_text(encoding="utf-8")
    # Verified R8PE01 rev 2 addresses (pouchGetPtr/pouchSetCoin, SEQ_WORK).
    for fact in ("OPENSPM_CHEAT_POUCH UINT32_C(0x804D0428)", "OPENSPM_CHEAT_POUCH_HP UINT32_C(0x0C)",
                 "OPENSPM_CHEAT_POUCH_MAX_HP UINT32_C(0x10)", "OPENSPM_CHEAT_POUCH_COINS UINT32_C(0x1C)",
                 "OPENSPM_CHEAT_SEQ_WORK UINT32_C(0x804D0D60)"):
        assert fact in header, fact
    assert '#define OPENSPM_RENDERER_CHEATS_EXPORT "openspm_renderer_cheats"' in header
    # Renderer: exported copy of the table; the menu never writes guest memory.
    assert "extern \"C\" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_cheats(openspm_cheat_table *out)" in v6
    assert "std::lock_guard<std::mutex> lock(g_cheat_mutex);" in overlay.split("inline int copy_cheat_table")[1]
    cheats_ui = overlay.split("// Cheats never write guest memory from here.")[1].split("inline std::vector<std::string> g_mod_files")[0]
    assert "guest_memory::map" not in cheats_ui and "span.data" not in cheats_ui
    # Off by default: only "1" enables; nothing enabled -> the export returns 0.
    assert 'get(text, "Cheats", "infinite_hp") == "1"' in ini and '"max_coins"' not in ini.split("namespace cheats")[1]
    assert "if (g_cheat_table.flags == 0u && g_cheat_table.write_count == 0u && g_cheat_table.gift_serial == 0u) return 0;" in overlay
    # Backend: resolved like the gamepad export, applied only at PRESENT_SAFEPOINT.
    assert 'GetProcAddress(renderer, OPENSPM_RENDERER_CHEATS_EXPORT)' in v9
    assert v9.count("v9_apply_cheats();") == 1
    safepoint = v9.split("if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {")[1].split("\n    }\n")[0]
    assert "v9_latch_input_frame();" in safepoint and "v9_apply_cheats();" in safepoint
    assert "if (!get_cheats(&table)) return; /* every cheat off */" in v9
    assert "!openspm_cheat_address_ok(address) || !v9_guest_backing_attached(address)" in v9
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "cheats_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o", str(exe),
                            str(ROOT / "tests" / "m14_cheats_test.c")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_ax_hle_contract() -> None:
    """Stage-3 audio: the host AX HLE mixer (library only, not wired into the game yet)."""
    header = (ROOT / "backend" / "audio" / "openspm_ax_hle.h").read_text(encoding="utf-8")
    # Layout facts confirmed against the R8PE01 rev-2 binary (docs/AUDIO-PLAN.md).
    for fact in ("#define AX_HLE_FRAME_SAMPLES 96u", "#define AX_HLE_PB_SIZE 0x140u",
                 "AX_CMD_PROCESS = 0x04", "AX_CMD_OUTPUT = 0x0B", "AX_CMD_END = 0x0E",
                 "AXPB_CUR_ADDR = 0x7A", "AXPB_SRC_RATIO = 0xA6", "AXPB_RMT_ON = 0xD6"):
        assert fact in header, fact
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "ax_hle_test"
            subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                            str(ROOT / "tests" / "m14_ax_hle_test.c"),
                            str(ROOT / "backend" / "audio" / "openspm_ax_hle.c"), "-lm"], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_audio_device_contract() -> None:
    """Stage-2 audio: AI/DSP device model + renderer SDL stream, opt-in (OPENSPM_AUDIO=1)."""
    header = (ROOT / "backend" / "audio" / "openspm_audio_device.h").read_text(encoding="utf-8")
    out_h = (ROOT / "include" / "openspm" / "audio_out.h").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    for fact in ("#define OPENSPM_AUDIO_FRAME_NS UINT64_C(3000000)", "OPENSPM_AUDIO_MAIL_AX_LIST_HI 0xBABEu",
                 "OPENSPM_AUDIO_MAIL_DSP_RESUME UINT32_C(0xDCD10001)", "UINT32_C(0xCC00503A)"):
        assert fact in header, fact
    assert '#define OPENSPM_RENDERER_AUDIO_PUSH_EXPORT "openspm_renderer_audio_push"' in out_h
    # The renderer owns the device: one SDL3 stream at the Wii rate, never blocking.
    push = v6[v6.index("openspm_renderer_audio_push(const int16_t *lr"):]
    push = push[:push.index("\n}\n")]
    assert "SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK" in v6 and "spec.format = SDL_AUDIO_S16;" in v6
    assert "openspm_audio_pacer_push(" in push and "SDL_PutAudioStreamData" in push
    assert "SDL_SetAudioStreamFrequencyRatio" in push and "sleep" not in push.lower() and "Flush" not in push
    # Menu pause: silent at once (the guest clock is frozen by the backend).
    pause = v6[v6.index("int m14_menu_pause_frame() {"):]
    assert pause.index("m14_audio_menu_pause();") < pause.index("pump_events(state);")
    assert 'begin_tab("Audio"' in overlay and "g_audio_volume_live" in v6
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "audio_device_test"
            subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}",
                            "-o", str(exe), str(ROOT / "tests" / "m14_audio_device_test.c"),
                            str(ROOT / "backend" / "audio" / "openspm_audio_device.c"),
                            str(ROOT / "backend" / "audio" / "openspm_ax_hle.c"), "-lm"], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_mod_tick_contract() -> None:
    import shutil
    import subprocess
    import tempfile
    v9 = (ROOT / "backend" / "native" / "openspm_native_backend_v9.c").read_text(encoding="utf-8")
    launcher = (ROOT / "launcher" / "native_main.c").read_text(encoding="utf-8")
    debug = (ROOT / "mods" / "debug" / "openspm_debug.c").read_text(encoding="utf-8")
    # Backend: the per-frame mod tick runs at PRESENT_SAFEPOINT right after the
    # cheats, behind one flag check; PRESENT_END follows the renderer's present.
    safepoint = v9.split("if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {")[1].split("\n    }\n")[0]
    assert "v9_apply_cheats();\n        /* Mods: per-frame tick (guest memory open) right after the cheats. */\n" \
           "        if (g_v9_mod_state != 2) v9_mod_tick();" in safepoint
    assert "if (result == OPENSPM_OK && g_v9_mod_state == 1) v9_mod_present();" in safepoint
    assert v9.count("v9_mod_tick();") == 1
    tick = v9.split("static void v9_mod_tick(void) {")[1].split("\n}\n")[0]
    assert tick.index("g_v9_guest_window.active = 1;") < tick.index("OPENSPM_RUNTIME_EVENT_TICK_BEGIN") \
           < tick.index("g_v9_guest_window.active = 0;")
    assert "GetCurrentThreadId() != g_v9_mod_tick_thread" in v9
    assert "strcmp(service_id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0" in v9
    # Launcher: sink installed only with mods loaded, cleared after run().
    assert "if (mods.count != 0u) {" in launcher and "host.runtime_event = host_runtime_event;" in launcher
    assert launcher.index("host.runtime_event = host_runtime_event;") < launcher.index("rc = backend.run(")
    assert launcher.index("host.runtime_event = NULL;") > launcher.index("rc = backend.run(")
    # Reference mod reads SEQ_WORK through the service on TICK_BEGIN.
    assert "OPENSPM_GUEST_MEMORY_SERVICE_ID" in debug and "DEBUG_TICK_LOG_INTERVAL 600u" in debug
    assert "write8(" not in debug and "write16(" not in debug and "write32(" not in debug
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "mod_tick_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o", str(exe),
                            str(ROOT / "tests" / "m14_mod_tick_test.c")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_texture_pack_contract() -> None:
    import importlib.util
    import shutil
    import subprocess
    import tempfile
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    # Chains after the EFB copy tag lookup it relies on, before the replay/interpolation patches.
    assert cmake.index("apply_efb_copy_ram_writeback_patch.py") < cmake.index(
        "apply_texture_pack_patch.py") < cmake.index("apply_copy_pixel_exact_patch.py") < cmake.index(
        "apply_interpolation_replay_prep_patch.py")
    assert "renderer/aurora/openspm_texture_pack_names.hpp" in cmake.split("apply_texture_pack_patch.py")[0][-800:]
    spec = importlib.util.spec_from_file_location("texpack_patch", HERE / "apply_texture_pack_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    # Exact/idempotent on a synthetic tree built from the pinned fragments; the
    # embedded naming block migrates; drift fails closed.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for rel, text in ((patch.REPLACEMENT, "#include <vector>\n" + patch.EMBED_ANCHOR + patch.BUDGET_OLD +
                           patch.GLOBALS_OLD + patch.HIT_OLD + "}\nbool dump() {\n" + patch.DUMP_OLD + "}\n" +
                           patch.LOAD_OLD + "}\n" + patch.API_ANCHOR),
                          (patch.HEADER, "namespace aurora::texture {\n" + patch.DECL_ANCHOR + "}\n"),
                          (patch.GX_TEXTURE, "".join(f * n + "\n" for f, n in patch.GX_REQUIRED.items()))):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        assert patch.transform(root) == "applied"
        first = (root / patch.REPLACEMENT).read_text(encoding="utf-8")
        assert patch.transform(root) == "already active" and patch.transform(root, True) == "already active"
        names = (HERE / "openspm_texture_pack_names.hpp").read_text(encoding="utf-8")
        assert first.count(patch.BEGIN) == 1 and names in first
        (root / patch.REPLACEMENT).write_text(first.replace(names, "// an earlier revision\n"), encoding="utf-8")
        assert patch.transform(root) == "applied" and (root / patch.REPLACEMENT).read_text(encoding="utf-8") == first
        gx = (root / patch.GX_TEXTURE).read_text(encoding="utf-8")
        (root / patch.GX_TEXTURE).write_text(gx.replace("resolve_static_texture", "resolve_other"), encoding="utf-8")
        try:
            patch.transform(root)
            raise AssertionError("texture pack patch accepted a drifted texture.cpp")
        except RuntimeError:
            pass
    # Dumps are Dolphin-named PNGs with Dolphin's "_m" rule (TEX_MODE0 mipmap_filter).
    assert "GXTexObj_::get_bits(obj.mode0, 2, 5) != 0" in patch.DUMP_NEW
    assert "openspm_texpack::encode_png_rgba8" in patch.DUMP_NEW
    # Off-switch: enabled=0 or no folder registers nothing; dumps are opt-in.
    packs = (HERE / "openspm_texture_packs.hpp").read_text(encoding="utf-8")
    apply_body = packs.split("inline void apply(const std::string &ini) {")[1].split("\n}\n")[0]
    assert apply_body.index("if (!master_enabled(ini)) {") < apply_body.index("load_replacement_directory(")
    startup = packs.split("inline void startup_impl() {")[1].split("\n}\n")[0]
    # The noexcept entry runs the whole body under catch-and-continue.
    entry = packs.split("void startup() noexcept {")[1].split("\n}\n")[0]
    assert entry.index("try {") < entry.index("startup_impl();") < entry.index("catch (...)")
    assert 'env("OPENSPM_TEXTURE_PACKS")' in startup and 'env("OPENSPM_TEXTURE_DUMP") == "1"' in startup
    assert startup.index("is_directory(s.folder, ec)") < startup.index("apply(ini);")
    base = (HERE / "openspm_renderer_aurora_dev7_base.cpp").read_text(encoding="utf-8")
    init = base.split("AuroraInfo openspm_aurora_initialize_logged(")[1].split("\n}\n")[0]
    assert "dump != nullptr && dump[0] == '1'" in init
    assert init.index("aurora_initialize(argc, argv, &captured);") < init.index("openspm_texpacks::startup();")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    assert '#include "openspm_texture_packs.hpp"' in overlay and 'begin_tab("Texture Packs"' in overlay
    assert "SDL_OpenURL(url.c_str())" in overlay.split("inline void open_folder(")[1][:600]
    # Host unit test: Dolphin names, XXH64, TLUT range, PNG writer.
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "texture_pack_test"
            subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                            str(ROOT / "tests" / "m14_texture_pack_test.cpp")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_menu_control_contract() -> None:
    """HOME opens the OpenSPM menu, the game pauses while it is open, keys rebind."""
    import shutil
    import subprocess
    import tempfile
    v2 = (ROOT / "backend" / "native" / "openspm_native_backend_v2.c").read_text(encoding="utf-8")
    v9 = (ROOT / "backend" / "native" / "openspm_native_backend_v9.c").read_text(encoding="utf-8")
    v10 = (ROOT / "backend" / "native" / "openspm_native_backend_v10.c").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    header = (ROOT / "include" / "openspm" / "menu_control.h").read_text(encoding="utf-8")
    pad = (ROOT / "backend" / "native" / "openspm_pad_xinput.h").read_text(encoding="utf-8")
    # HOME: Guide (SDL export) and the [Keyboard] home key; stripped before the game always.
    assert "{SDL_GAMEPAD_BUTTON_GUIDE, 0x0400u}" in v6
    assert "if (buttons & OPENSPM_XI_GUIDE) down |= OPENSPM_PAD_WPAD_HOME;" in pad
    assert "uint32_t down = v9_menu_filter(v9_keyboard_down());" in v9
    menu_filter = v9.split("static uint32_t v9_menu_filter(uint32_t down) {")[1].split("\n}\n")[0]
    assert menu_filter.index("down &= ~OPENSPM_KEYMAP_WPAD_HOME;") < menu_filter.index("if (g_v9_menu == NULL) return down;")
    assert "g_v9_home_menu = !(home != NULL && home[0] == '0');" in v9
    assert "return g_v9_menu_open ? 0u : down;" in menu_filter  # no game input while the menu is open
    assert "if (g_v9_menu_open) {" in v10.split("static void v10_latch_pointer(void) {")[1][:900]
    # Pause: only with pause_game on and a renderer menu export; the module then
    # gets the pause-aware clock, installed where the runtime is created.
    assert "runtime.host = g_native_runtime_host_filter != NULL" in v2
    assert "g_native_runtime_host_filter = v9_runtime_host;" in v9
    host = v9.split("static const openspm_host_api *v9_runtime_host(")[1].split("\n}\n")[0]
    assert host.index("if (value != NULL && value[0] == '0') {") < host.index("return &g_v9_clock_host;")
    assert host.index("g_v9_pause_enabled = 0;") < host.index("g_v9_pause_enabled = pause;")
    assert host.index("pause = 0;") < host.index("g_v9_pause_enabled = pause;")
    assert "g_v9_clock_host.monotonic_ns = v9_paused_monotonic_ns;" in host
    safepoint = v9.split("if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {")[1].split("\n    }\n")[0]
    assert safepoint.index("OPENSPM_GX_SYNC_PRESENT_SAFEPOINT") < safepoint.index(
        "if (result == OPENSPM_OK && g_v9_pause_enabled) v9_menu_pause();")
    pause = v9.split("static void v9_menu_pause(void) {")[1].split("\n}\n")[0]
    assert pause.index("openspm_pause_clock_pause(") < pause.index("OPENSPM_MENU_OP_PAUSE_FRAME")
    assert pause.rindex("openspm_pause_clock_resume(") > pause.rindex("OPENSPM_MENU_OP_PAUSE_FRAME")
    # Renderer: an open game frame (every SPM safepoint) is drained and ended at
    # the safepoint, then the menu is presented over the last copy.
    frame = v6.split("int m14_menu_pause_frame() {")[1].split("\n}\n")[0]
    split = frame.split("if (state.frame_open) {", 1)[1].split("\n    }\n", 1)[0]
    assert 'std::getenv("OPENSPM_MENU_PAUSE_SPLIT")' in split and "return 0;" in split
    assert split.index("aurora::gx::raw::drain();") < split.index("aurora_end_frame();") < split.index(
        "state.frame_open = false;")
    assert frame.index("if (state.frame_open) {") < frame.index("aurora_begin_frame()")
    assert frame.index("openspm_overlay::draw();") < frame.rindex("aurora_end_frame();") < frame.index(
        "openspm_overlay::after_frame();")
    assert 'extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_menu(unsigned op)' in v6
    assert '#define OPENSPM_RENDERER_MENU_EXPORT "openspm_renderer_menu"' in header
    # Keyboard rebinding: the menu uses the backend's parser and publishes live.
    assert '#include "openspm/keymap.h"' in overlay and '#include "openspm/keymap.h"' in v9
    assert 'save_setting("Keyboard", "map", text)' in overlay and "++g_keymap_generation;" in overlay
    assert "ImGui::GetKeyName(key)" in overlay and 'ImGui::Button("Default keys")' in overlay
    assert "GetAsyncKeyState(VK_" not in v9  # every key goes through the map
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "keymap_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o", str(exe),
                            str(ROOT / "tests" / "m14_keymap_test.c")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


ASPECT_CASES = r'''
#include "output_aspect.hpp"
#include <cassert>
using namespace openspm::aspect;
static_assert(!choose_output(false, false).stretch_policy && choose_output(false, false).stretch_aspect == 0.f);
static_assert(choose_output(true, false).stretch_policy && choose_output(true, false).stretch_aspect == 0.f);
static_assert(choose_output(false, true).stretch_policy && choose_output(false, true).stretch_aspect == 16.f / 9.f);
static_assert(choose_output(true, true).stretch_policy && choose_output(true, true).stretch_aspect == 0.f);
static_assert(widescreen_value("1") && !widescreen_value("0") && !widescreen_value("10") && !widescreen_value(nullptr) &&
              !widescreen_value(""));
int main() { return 0; }
'''


def test_widescreen_contract() -> None:
    """[Display] widescreen: SYSCONF IPL.AR = 16:9 for the game, 16:9 presentation in the renderer."""
    settings = (ROOT / "launcher" / "native_settings.h").read_text(encoding="utf-8")
    assert '{"Display", "widescreen", "OPENSPM_WIDESCREEN", "0|1"}' in settings
    launcher = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    for arg in ("2", "3", "4"):
        assert f'if /i "%~{arg}"=="widescreen" set "OPENSPM_WIDESCREEN=1"' in launcher
    assert 'set "OPENSPM_WIDESCREEN=0"' not in launcher  # no flag: OpenSPM.ini decides
    # Backend: the hosted SCInit publishes an emulated SYSCONF cache (no game-code patch).
    v12 = (ROOT / "backend" / "native" / "openspm_native_backend_v12.c").read_text(encoding="utf-8")
    assert '#include "openspm_sysconf.h"' in v12
    assert "#define R8PE01_SC_INIT                UINT32_C(0x8029A7E0)" in v12
    assert "#define R8PE01_SC_CACHE               UINT32_C(0x80524360)" in v12
    assert "#define R8PE01_SC_ID_IPL_AR           UINT32_C(1)" in v12
    hook = v12[v12.index("static int OPENSPM_CALL v12_handle_hostcall("):]
    hook = hook[:hook.index("\n}\n")]
    assert "if (target == R8PE01_SC_INIT) {" in hook
    assert hook.index("result = v10_handle_hostcall_base(context, target, state);", hook.index("R8PE01_SC_INIT")) < \
        hook.index("v12_publish_sysconf();")
    publish = v12[v12.index("static void v12_publish_sysconf(void) {"):]
    publish = publish[:publish.index("\n}\n")]
    assert publish.index("if (!v12_widescreen_requested()) return;") < publish.index("v8_guest_ptr(R8PE01_SC_CACHE")
    assert 'openspm_sysconf_set_u8(cache, OPENSPM_SC_BUFFER_SIZE, "IPL.AR", OPENSPM_SC_ASPECT_16_9)' in publish
    for glob in ("R8PE01_SC_FREE_BYTES", "R8PE01_SC_ITEM_COUNT", "R8PE01_SC_INDEX_OFFSET", "R8PE01_SC_KNOWN_IDS"):
        assert f"v8_write32({glob}," in publish, glob
    host_targets = (ROOT / "tools" / "native_bridge" / "m14_host_targets.inc").read_text(encoding="utf-8")
    assert "OPENSPM_M14_HOST_TARGET(0x8029A7E0u)" in host_targets  # SCInit stays hosted
    # Renderer: stretch + 16:9 fit through the Aurora patch, also from the F1 menu.
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake.index("apply_bbox_fast_patch.py") < cmake.index("apply_widescreen_fit_patch.py")
    assert "add_executable(m14_sysconf_test tests/m14_sysconf_test.c)" in cmake
    renderer = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    body = renderer[renderer.index("void m14_apply_output_aspect() {"):]
    assert body.index("AuroraSetStretchAspect(output.stretch_aspect);") < body.index("AuroraSetViewportPolicy(")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    assert 'toggle_setting("Widescreen 16:9", "Display", "widescreen", "OPENSPM_WIDESCREEN", false,' in overlay
    after = overlay[overlay.index("inline void after_frame()"):]
    assert after.index("AuroraSetStretchAspect(g_pending_stretch_aspect);") < after.index("AuroraSetViewportPolicy(")
    assert "AuroraSetStretchAspect" not in overlay.split("inline void after_frame()")[0]
    patch = (HERE / "apply_widescreen_fit_patch.py").read_text(encoding="utf-8")
    assert "void AuroraSetStretchAspect(float aspect);" in patch and "g_frameBufferStretchAspect > 0.f" in patch
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    cc = shutil.which("gcc") or shutil.which("clang")
    with tempfile.TemporaryDirectory() as tmp:
        if cxx:
            src = pathlib.Path(tmp) / "aspect.cpp"
            src.write_text(ASPECT_CASES, encoding="utf-8")
            subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", f"-I{HERE}", str(src), "-o",
                            str(pathlib.Path(tmp) / "aspect")], check=True)
        if cc:
            exe = pathlib.Path(tmp) / "sysconf_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                            str(ROOT / "tests" / "m14_sysconf_test.c")], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


def test_pointer_input_contract() -> None:
    """Wii pointer over the game image, right-stick pointer, rumble forwarding."""
    import shutil
    import subprocess
    import tempfile
    header = (ROOT / "include" / "openspm" / "pointer_input.h").read_text(encoding="utf-8")
    v10 = (ROOT / "backend" / "native" / "openspm_native_backend_v10.c").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    settings = (ROOT / "launcher" / "native_settings.h").read_text(encoding="utf-8")
    for export in ("openspm_renderer_image_rect", "openspm_renderer_pointer_stick", "openspm_renderer_gamepad_rumble"):
        assert f'"{export}"' in header and f"OPENSPM_CALL {export}(" in v6, export
    # The mouse maps over the image rect the renderer reports (bars = off-screen).
    latch = v10.split("static void v10_latch_pointer(void) {")[1].split("\n}\n")[0]
    assert "openspm_pointer_from_client(have_image ? &image : NULL," in latch
    assert latch.index("openspm_pointer_from_client(") < latch.index("openspm_pointer_arbitrate(")
    assert "AuroraGetRenderSize(&content_w, &content_h);" in v6 and "SDL_GetWindowSizeInPixels(window" in v6
    # Rumble: the game's own state read at the safepoint (verified R8PE01 rev 2 addresses).
    for fact in ("R8PE01_WPADMGR_WP_PTR        UINT32_C(0x8056CB98)", "R8PE01_WPAD_RUMBLE_REQUEST   UINT32_C(0x44)",
                 "R8PE01_RUMBLE_SUPPRESS_WP    UINT32_C(0x8056CB78)", "R8PE01_RUMBLE_SUPPRESS       UINT32_C(0x1B00)",
                 "R8PE01_CURSOR_PULSE_WP       UINT32_C(0x8056CBC0)", "R8PE01_CURSOR_PULSE_COUNTER  UINT32_C(0x18)"):
        assert fact in v10, fact
    safepoint = v10.split("if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {")[1]
    assert safepoint.index("v10_latch_pointer();") < safepoint.index("v10_forward_rumble();")
    rumble = v10.split("static void v10_forward_rumble(void) {")[1].split("\n}\n")[0]
    assert "if (!g_v9_menu_open) {" in rumble  # no rumble while the menu is open
    assert "SDL_RumbleGamepad(pad, level, level, want ? 250u : 0u)" in v6  # self-expiring while on
    for key, env in (("stick_pointer", "OPENSPM_STICK_POINTER"), ("pointer_speed", "OPENSPM_POINTER_SPEED"),
                     ("pointer_deadzone", "OPENSPM_POINTER_DEADZONE"), ("rumble", "OPENSPM_RUMBLE"),
                     ("rumble_strength", "OPENSPM_RUMBLE_STRENGTH")):
        assert f'{{"Controller", "{key}", "{env}", ' in settings, key
        assert f'"{env}"' in overlay, env
    assert 'ImGui::Button("Test rumble")' in overlay
    cc = shutil.which("gcc") or shutil.which("clang")
    if cc:
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "pointer_test"
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o", str(exe),
                            str(ROOT / "tests" / "m14_pointer_input_test.c"), "-lm"], check=True)
            subprocess.run([str(exe)], check=True, capture_output=True)


TURBO_GATE_CASES = r"""
#include "turbo_pacing.hpp"
#include <cassert>
#include <cstdint>
using openspm::turbo::PresentGate;
static unsigned shown(PresentGate &gate, double frame_ns, double seconds, double start = 1e9) {
  unsigned n = 0;
  for (double t = start; t < start + seconds * 1e9; t += frame_ns) n += gate.should_present(static_cast<uint64_t>(t));
  return n;
}
int main() {
  const double p60 = 1e9 / 60.0;
  PresentGate gate;
  gate.reset(p60);                       // 2x on 60 Hz: every 2nd frame, 60 shown/s
  unsigned n = shown(gate, p60 / 2.0, 10.0);
  assert(n >= 599 && n <= 601);
  gate.reset(p60);                       // 4x
  n = shown(gate, p60 / 4.0, 10.0);
  assert(n >= 599 && n <= 601);
  gate.reset(1e9 * 1000.0 / 143999.0);   // unlimited (~1100 frames/s) on 144 Hz
  n = shown(gate, 0.9e6, 10.0);
  assert(n >= 1437 && n <= 1441);
  gate.reset(p60);                       // the game cannot keep up: every frame is shown
  n = shown(gate, 20e6, 1.0);
  assert(n == 50);
  gate.reset(0.0);                       // unknown display: 60 Hz
  assert(gate.period_ns() == p60);
  assert(gate.should_present(5000000000u) && !gate.should_present(5000000001u));
  assert(gate.should_present(1000u));    // host clock went back: start over
  return 0;
}
"""


def test_turbo_contract() -> None:
    """Fast-forward: rate-scaled guest clock, every pace point follows it, no FIFO throttle, audio muted."""
    header = (ROOT / "include" / "openspm" / "guest_clock.h").read_text(encoding="utf-8")
    menu = (ROOT / "include" / "openspm" / "menu_control.h").read_text(encoding="utf-8")
    host_api = (ROOT / "include" / "openspm" / "host_api.h").read_text(encoding="utf-8")
    keymap = (ROOT / "include" / "openspm" / "keymap.h").read_text(encoding="utf-8")
    pad = (ROOT / "backend" / "native" / "openspm_pad_xinput.h").read_text(encoding="utf-8")
    v9 = (ROOT / "backend" / "native" / "openspm_native_backend_v9.c").read_text(encoding="utf-8")
    v7 = (ROOT / "tools" / "native_bridge" / "legacy_c_adapter_v7.c.in").read_text(encoding="utf-8")
    v4 = (HERE / "openspm_renderer_aurora_v4.cpp").read_text(encoding="utf-8")
    v6 = (HERE / "openspm_renderer_aurora_v6.cpp").read_text(encoding="utf-8")
    overlay = (HERE / "openspm_overlay.hpp").read_text(encoding="utf-8")
    settings = (ROOT / "launcher" / "native_settings.h").read_text(encoding="utf-8")
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    patch = (HERE / "apply_turbo_present_patch.py").read_text(encoding="utf-8")
    # The pause is rate 0 of the same guest clock; zero-initialized = identity.
    assert "typedef openspm_guest_clock openspm_pause_clock;" in menu
    assert "if (!configured) return host_now;" in header
    assert '#define OPENSPM_RENDERER_TURBO_EXPORT "openspm_renderer_turbo"' in menu
    # Appended host API field; the module checks struct_size before reading it.
    assert host_api.index("openspm_runtime_event_sink_fn runtime_event;") < host_api.index(
        "openspm_clock_skip_to_fn clock_skip_to;")
    assert "host->header.struct_size < offsetof(openspm_host_api, clock_skip_to) + sizeof(host->clock_skip_to)" in v7
    # The module's only host wait (scheduler idle before the VI deadline) is skipped only when the
    # host moved the clock to the deadline; the deadline grid and EE gate are unchanged.
    service = v7[v7.index("static int bridge_m14_service_retrace("):]
    service = service[:service.index("\n}\n")]
    skip = service.index("if (bridge_m14_clock_skip_to(instance, g_m14_next_retrace_ns, &now))")
    assert service.index("bridge_m14_idle_has_runnable(&instance->cpu)") < skip < service.index("Sleep(1u);")
    assert skip < service.index("m14_retrace_due:") < service.index("if ((instance->cpu.msr & OPENSPM_M14_MSR_EE) == 0u)")
    # nothing else in the module waits: every host sleep follows the skip, one clock read
    assert v7.count("Sleep(0u);") + v7.count("Sleep(1u);") == 3
    assert service.count("Sleep(0u);") + service.count("Sleep(1u);") == 3 and service.index("Sleep(0u);") > skip
    assert v7.count("host->monotonic_ns(host->context)") == 1
    # Backend: one clock for pause and turbo, installed when either is on; hotkeys never reach the game.
    host = v9.split("static const openspm_host_api *v9_runtime_host(")[1].split("\n}\n")[0]
    assert host.index("if ((!pause && !g_v9_turbo_enabled)") < host.index("return &g_v9_clock_host;")
    assert "if (g_v9_turbo_enabled) g_v9_clock_host.clock_skip_to = v9_clock_skip_to;" in host
    assert "g_v9_clock_host.header.struct_size = (uint32_t)sizeof g_v9_clock_host;" in host
    menu_filter = v9.split("static uint32_t v9_menu_filter(uint32_t down) {")[1].split("\n}\n")[0]
    assert menu_filter.index("down &= ~OPENSPM_KEYMAP_WPAD_TURBO;") < menu_filter.index("if (g_v9_menu == NULL) return down;")
    assert "OPENSPM_KEYMAP_WPAD_TURBO UINT32_C(0x40000000)" in keymap and "OPENSPM_PAD_WPAD_TURBO UINT32_C(0x40000000)" in pad
    latch = v9.split("static void v9_latch_input_frame(void) {")[1].split("\n}\n")[0]
    assert latch.index("v9_menu_filter(v9_keyboard_down())") < latch.index("v9_turbo_update();")
    update = v9.split("static void v9_turbo_update(void) {")[1].split("\n}\n")[0]
    assert "if (g_v9_menu_open) {" in update and "v9_turbo_apply(want);" in update  # the menu releases turbo
    apply = v9.split("static void v9_turbo_apply(unsigned want) {")[1].split("\n}\n")[0]
    assert apply.index("if (want == g_v9_turbo_active) return;") < apply.index("openspm_guest_clock_set_speed(")
    assert apply.index("openspm_guest_clock_set_speed(") < apply.index("g_v9_renderer_turbo(want)")
    pause = v9.split("static void v9_menu_pause(void) {")[1].split("\n}\n")[0]
    assert pause.index("v9_turbo_release();") < pause.index("openspm_pause_clock_pause(")
    menu_frame = v6.split("int m14_menu_pause_frame() {")[1].split("\n}\n")[0]
    assert menu_frame.index("aurora_m14_set_turbo(AURORA_M14_TURBO_ACTIVE);") < menu_frame.index("aurora_end_frame();")
    for key in ('{"Game", "turbo", "OPENSPM_TURBO", "1|0"}',
                '{"Game", "turbo_speed", "OPENSPM_TURBO_SPEED", "2|3|4|unlimited"}',
                '{"Game", "turbo_mode", "OPENSPM_TURBO_MODE", "hold|toggle"}'):
        assert key in settings, key
    # Renderer: both game present sites ask the gate right before aurora_end_frame; audio muted.
    safepoint = v4[v4.index("int present_display_copy_at_safepoint(renderer_state &state) {"):]
    assert safepoint.index("m14_turbo_before_end_frame();") < safepoint.index("aurora_end_frame();")
    drawdone = v6[v6.index("int dev7_present_completed_copy_after_drawdone(renderer_state &state) {"):]
    assert drawdone.index("m14_turbo_before_end_frame();") < drawdone.index("aurora_end_frame();")
    before = v4[v4.index("void m14_turbo_before_end_frame() {"):].split("\n}\n")[0]
    assert before.index("== 0u) return;") < before.index("aurora_m14_set_turbo(")  # off: untouched
    push = v6[v6.index("openspm_renderer_audio_push(const int16_t *lr"):].split("\n}\n")[0]
    assert push.index("m14_audio_turbo_drop(status, rate);") < push.index("SDL_PutAudioStreamData")
    assert 'extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_turbo(unsigned speed)' in v6
    assert '">> Turbo %ux", turbo' in overlay and "draw_fps_counter(); // FPS" in overlay
    # Aurora: last in the chain; skipped frames submit without acquiring/presenting.
    assert cmake.index("apply_widescreen_fit_patch.py") < cmake.index("apply_turbo_present_patch.py")
    assert "if (m14_turbo_pop_skip()) {" in patch and "if (m14TurboNow) haveB = false;" in patch
    assert "g_m14TurboFlags.fetch_and(~2u, std::memory_order_relaxed);" in patch  # skip is one-shot
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    cc = shutil.which("gcc") or shutil.which("clang")
    with tempfile.TemporaryDirectory() as tmp:
        if cxx:
            src = pathlib.Path(tmp) / "turbo_gate.cpp"
            src.write_text(TURBO_GATE_CASES, encoding="utf-8")
            subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", f"-I{HERE}", str(src), "-o",
                            str(pathlib.Path(tmp) / "turbo_gate")], check=True)
            subprocess.run([str(pathlib.Path(tmp) / "turbo_gate")], check=True)
        if cc:
            for name in ("m14_guest_clock_test", "m14_pad_map_test"):
                exe = pathlib.Path(tmp) / name
                subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'include'}", "-o",
                                str(exe), str(ROOT / "tests" / f"{name}.c")], check=True)
                subprocess.run([str(exe)], check=True, capture_output=True)


def test_presenter_surface_lock_contract() -> None:
    """Window resize with the present timeline on crashed: the worker's present
    raced the main thread's swapchain Configure (SurfaceLock is Android-only)."""
    import importlib.util
    import tempfile
    spec = importlib.util.spec_from_file_location("surface_lock", HERE / "apply_presenter_surface_lock_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake.index("apply_turbo_present_patch.py") < cmake.index("apply_presenter_surface_lock_patch.py")
    presenter = (HERE / "present_timeline_presenter.inl").read_text(encoding="utf-8")
    job = presenter.split("gfx::render_worker::enqueue_work([show, frameA, frameB, alpha] {", 1)[1]
    assert job.index("std::lock_guard surfaceGuard{webgpu::g_m14SurfaceMutex};") < job.index(
        "m14_frame_interp_choose(") < job.index("m14_present_retained_image(image)")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "lib/webgpu").mkdir(parents=True)
        cpp = ("#include <utility>\nnamespace aurora::webgpu {\n"
               "static void release_surface_locked() noexcept {\n}\n"
               "static void resize_swapchain_internal(uint32_t width, uint32_t height, uint32_t nativeWidth, "
               "uint32_t nativeHeight,\n                                      bool force) {\n}\n"
               "bool refresh_surface(bool recreate) {\n  gfx::gpu_synchronize();\n}\n}\n")
        hpp = "#include <cstdint>\nbool refresh_surface(bool recreate = true);\n"
        (root / "lib/webgpu/gpu.cpp").write_text(cpp, encoding="utf-8")
        (root / "lib/webgpu/gpu.hpp").write_text(hpp, encoding="utf-8")
        assert patch.transform(root, check_only=True) == "pending"
        assert patch.transform(root) == "applied"
        assert patch.transform(root) == "already active"
        out = (root / "lib/webgpu/gpu.cpp").read_text(encoding="utf-8")
        assert out.count(patch.GUARD) == 3 and "std::recursive_mutex g_m14SurfaceMutex;" in out
        assert out.index("gfx::gpu_synchronize();") < out.index(patch.GUARD, out.index("refresh_surface"))
        assert "extern std::recursive_mutex g_m14SurfaceMutex;" in (root / "lib/webgpu/gpu.hpp").read_text()
        (root / "lib/webgpu/gpu.cpp").write_text(cpp.replace("bool force) {", "bool f) {"), encoding="utf-8")
        try:
            patch.transform(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("drifted anchor must fail closed")


def test_copy_guest_capture_contract() -> None:
    """GX capture EFB copy lines carry the guest destination (flip analysis)."""
    import importlib.util
    import tempfile
    spec = importlib.util.spec_from_file_location("copy_guest", HERE / "apply_gx_audit_copy_guest_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake.index("apply_presenter_surface_lock_patch.py") < cmake.index("apply_gx_audit_copy_guest_patch.py")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "lib/gx").mkdir(parents=True)
        body = ("void f() {\n" + patch.OLD + "           value & 0x00FFFFFFu,\n" + patch.ARGS_OLD + "}\n")
        (root / "lib/gx/command_processor.cpp").write_text(body, encoding="utf-8")
        assert patch.transform(root, check_only=True) == "pending"
        assert patch.transform(root) == "applied"
        assert patch.transform(root) == "already active"
        out = (root / "lib/gx/command_processor.cpp").read_text(encoding="utf-8")
        assert patch.NEW in out and patch.ARGS_NEW in out and patch.OLD not in out
    analyzer = (ROOT / "tools/diag/analyze_gx_capture.py").read_text(encoding="utf-8")
    assert '"--passes"' in analyzer and 'copy.info.get("guest")' in analyzer


def main() -> None:
    test_policy()
    test_contract()
    test_widescreen_contract()
    test_present_timeline()
    test_draw_tags()
    test_frame_interp()
    test_bbox_fast()
    test_finish_before_retrace_contract()
    test_bbox_logical_contract()
    test_copy_effects_contract()
    test_viewport_origin_contract()
    test_settings_contract()
    test_cheats_contract()
    test_ax_hle_contract()
    test_audio_device_contract()
    test_mod_tick_contract()
    test_texture_pack_contract()
    test_menu_control_contract()
    test_pointer_input_contract()
    test_turbo_contract()
    test_presenter_surface_lock_contract()
    test_copy_guest_capture_contract()
    print("PASS: vanilla display pacing policy + present timeline + draw tags + frame interp + renderer/bridge period contract")


if __name__ == "__main__":
    main()
