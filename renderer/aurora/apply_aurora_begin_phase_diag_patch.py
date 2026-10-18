#!/usr/bin/env python3
"""Add low-volume phase timing around Aurora update/begin-frame.

The 2026-09-14 020300 Windows run proved that OpenSPM's outer ensure_frame
profiler can report 65-122 ms stalls while Aurora's internal frame-slot,
staging-slot, and staging-map waits all remain below 16 ms.  Therefore the
remaining wait is outside gfx::begin_frame's resource acquisition: either the
Aurora update/event path or another begin_frame phase.

This diagnostic-only adaptation times the narrow upstream phases and logs only
>=16 ms waits (first eight, powers of two, and new maxima).  It changes no
renderer ordering, frame ownership, synchronization, presentation, or guest
semantics.  The transform is exact/idempotent against the pinned Aurora
revision plus the existing OpenSPM adaptations and fails closed on source drift.
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
SIGNATURE = "kM14AuroraPhaseSlowUs"

INCLUDE_OLD = """#include <SDL3/SDL_filesystem.h>\n#include <magic_enum.hpp>\n"""
INCLUDE_NEW = """#include <SDL3/SDL_filesystem.h>\n#include <magic_enum.hpp>\n\n#include <chrono>\n"""

STATE_OLD = """namespace {\nconstexpr Module Log{\"aurora\"};\n"""
STATE_NEW = """namespace {\nconstexpr Module Log{\"aurora\"};\n\n// OpenSPM diagnostic only.  The outer adapter's ensure_frame timer includes\n// aurora_update() plus aurora_begin_frame(); identify which upstream phase owns\n// rare Broadway-visible stalls without changing any timing or ownership.\nconstexpr std::uint64_t kM14AuroraPhaseSlowUs = 16000u;\nstruct M14AuroraPhaseCounter {\n  std::uint64_t slow = 0u;\n  std::uint64_t maxUs = 0u;\n};\nM14AuroraPhaseCounter g_m14UpdateGx{};\nM14AuroraPhaseCounter g_m14UpdateEvents{};\nM14AuroraPhaseCounter g_m14UpdateTotal{};\nM14AuroraPhaseCounter g_m14BeginSurface{};\nM14AuroraPhaseCounter g_m14BeginImgui{};\nM14AuroraPhaseCounter g_m14BeginGfx{};\nM14AuroraPhaseCounter g_m14BeginFifo{};\nM14AuroraPhaseCounter g_m14BeginTotal{};\n\nusing M14AuroraPhaseClock = std::chrono::steady_clock;\n\nvoid m14_log_aurora_phase(const char* phase, M14AuroraPhaseCounter& counter,\n                          M14AuroraPhaseClock::time_point start) {\n  const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(\n      M14AuroraPhaseClock::now() - start).count();\n  const auto waitUs = elapsed > 0 ? static_cast<std::uint64_t>(elapsed) : 0u;\n  if (waitUs < kM14AuroraPhaseSlowUs) return;\n  ++counter.slow;\n  const bool newMax = waitUs > counter.maxUs;\n  if (newMax) counter.maxUs = waitUs;\n  if (counter.slow > 8u && (counter.slow & (counter.slow - 1u)) != 0u && !newMax) return;\n  Log.info(\"M14 Aurora phase wait phase={} wait_us={} slow={} max_us={}\",\n           phase, waitUs, counter.slow, counter.maxUs);\n}\n"""

UPDATE_OLD = """const AuroraEvent* update() noexcept {\n  ZoneScoped;\n  if (g_initialFrame) {\n    g_initialFrame = false;\n    input::initialize();\n  }\n#ifdef AURORA_ENABLE_GX\n  gx::update();\n#endif\n  return window::poll_events();\n}\n"""
UPDATE_NEW = """const AuroraEvent* update() noexcept {\n  ZoneScoped;\n  const auto totalStart = M14AuroraPhaseClock::now();\n  if (g_initialFrame) {\n    g_initialFrame = false;\n    input::initialize();\n  }\n#ifdef AURORA_ENABLE_GX\n  {\n    const auto phaseStart = M14AuroraPhaseClock::now();\n    gx::update();\n    m14_log_aurora_phase(\"update-gx\", g_m14UpdateGx, phaseStart);\n  }\n#endif\n  const auto eventStart = M14AuroraPhaseClock::now();\n  const AuroraEvent* events = window::poll_events();\n  m14_log_aurora_phase(\"update-events\", g_m14UpdateEvents, eventStart);\n  m14_log_aurora_phase(\"update-total\", g_m14UpdateTotal, totalStart);\n  return events;\n}\n"""

BEGIN_OLD = """bool begin_frame() noexcept {\n  ZoneScoped;\n#ifdef AURORA_ENABLE_GX\n  {\n    if (!window::is_presentable()) {\n      webgpu::release_surface();\n      return false;\n    }\n    if (window::is_paused()) {\n      return false;\n    }\n    if (!g_surface) {\n      webgpu::refresh_surface(true);\n      if (!g_surface) {\n        return false;\n      }\n    }\n  }\n\n  imgui::new_frame(window::get_window_size());\n  if (!gfx::begin_frame()) {\n    return false;\n  }\n  gx::fifo::begin_frame();\n#endif\n  return true;\n}\n"""
BEGIN_NEW = """bool begin_frame() noexcept {\n  ZoneScoped;\n  const auto totalStart = M14AuroraPhaseClock::now();\n#ifdef AURORA_ENABLE_GX\n  {\n    const auto phaseStart = M14AuroraPhaseClock::now();\n    if (!window::is_presentable()) {\n      webgpu::release_surface();\n      m14_log_aurora_phase(\"begin-surface\", g_m14BeginSurface, phaseStart);\n      m14_log_aurora_phase(\"begin-total\", g_m14BeginTotal, totalStart);\n      return false;\n    }\n    if (window::is_paused()) {\n      m14_log_aurora_phase(\"begin-surface\", g_m14BeginSurface, phaseStart);\n      m14_log_aurora_phase(\"begin-total\", g_m14BeginTotal, totalStart);\n      return false;\n    }\n    if (!g_surface) {\n      webgpu::refresh_surface(true);\n      if (!g_surface) {\n        m14_log_aurora_phase(\"begin-surface\", g_m14BeginSurface, phaseStart);\n        m14_log_aurora_phase(\"begin-total\", g_m14BeginTotal, totalStart);\n        return false;\n      }\n    }\n    m14_log_aurora_phase(\"begin-surface\", g_m14BeginSurface, phaseStart);\n  }\n\n  {\n    const auto phaseStart = M14AuroraPhaseClock::now();\n    imgui::new_frame(window::get_window_size());\n    m14_log_aurora_phase(\"begin-imgui\", g_m14BeginImgui, phaseStart);\n  }\n  {\n    const auto phaseStart = M14AuroraPhaseClock::now();\n    if (!gfx::begin_frame()) {\n      m14_log_aurora_phase(\"begin-gfx\", g_m14BeginGfx, phaseStart);\n      m14_log_aurora_phase(\"begin-total\", g_m14BeginTotal, totalStart);\n      return false;\n    }\n    m14_log_aurora_phase(\"begin-gfx\", g_m14BeginGfx, phaseStart);\n  }\n  {\n    const auto phaseStart = M14AuroraPhaseClock::now();\n    gx::fifo::begin_frame();\n    m14_log_aurora_phase(\"begin-fifo\", g_m14BeginFifo, phaseStart);\n  }\n#endif\n  m14_log_aurora_phase(\"begin-total\", g_m14BeginTotal, totalStart);\n  return true;\n}\n"""

REPLACEMENTS = (
    (INCLUDE_OLD, INCLUDE_NEW, "chrono include"),
    (STATE_OLD, STATE_NEW, "phase diagnostic state"),
    (UPDATE_OLD, UPDATE_NEW, "update phase split"),
    (BEGIN_OLD, BEGIN_NEW, "begin-frame phase split"),
)


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        required = (
            'phase={} wait_us={} slow={} max_us={}',
            '\"update-events\"',
            '\"begin-surface\"',
            '\"begin-imgui\"',
            '\"begin-gfx\"',
            '\"begin-fifo\"',
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError("Aurora phase diagnostic signature is incomplete: " + ", ".join(missing))
        return "already-applied"

    for old, _new, name in REPLACEMENTS:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{TARGET}: {name}: expected source fragment exactly once, found {count}")
    for old, new, _name in REPLACEMENTS:
        text = text.replace(old, new, 1)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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
        print(f"error: Aurora begin/update phase diagnostic adaptation failed: {exc}", file=sys.stderr)
        return 4
    if result == "already-applied":
        print("Aurora begin/update phase diagnostics already applied")
    elif result == "verified":
        print("Aurora begin/update phase diagnostic transforms verified")
    else:
        print("Aurora begin/update phase diagnostics applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
