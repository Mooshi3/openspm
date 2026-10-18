#!/usr/bin/env python3
"""Measure real Aurora swapchain-present cadence at low volume.

Runtime 035138 proved that Wii VI/retrace delivery can sit at 60/s for long
stretches while the player still perceives severe choppiness.  The existing
OpenSPM timers measure Broadway-visible calls and Aurora frame acquisition, but
Aurora's actual WebGPU surface Present() executes later on the render worker.
Those counters therefore cannot prove that frames reach the desktop at an even
16.67 ms cadence.

Instrument only successful g_surface.Present() calls.  This costs two
steady_clock reads per *presented host frame*, never per GX draw/FIFO command.
It records a 0.5 ms interval histogram, tail counts, Present() call duration,
and emits sparse power-of-two summaries plus one shutdown summary.  No GX,
parser, shader, presentation ordering, timebase, or synchronization semantics
are changed.

The transform runs after apply_aurora_begin_phase_diag_patch.py, is exact and
idempotent against the pinned Aurora revision plus existing OpenSPM patches,
and fails closed on unexpected source drift.
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
SIGNATURE = "kM14PresentPacingBinUs"
PHASE_SIGNATURE = "kM14AuroraPhaseSlowUs"

STATE_ANCHOR = 'constexpr Module Log{"aurora"};\n'
STATE_INSERT = r'''constexpr Module Log{"aurora"};

// OpenSPM diagnostic only: observe the render-worker swapchain Present() that
// actually reaches the host window.  VI/retrace counts and main-thread
// aurora_end_frame() calls are not substitutes for desktop presentation cadence.
constexpr uint64_t kM14PresentPacingBinUs = 500u;
constexpr uint64_t kM14PresentPacingBins = 512u; // 0..255.5 ms, final bin clamps.
uint64_t g_m14PresentIntervalHist[kM14PresentPacingBins]{};
uint64_t g_m14PresentCount = 0u;
uint64_t g_m14PresentIntervalCount = 0u;
uint64_t g_m14PresentIntervalTotalUs = 0u;
uint64_t g_m14PresentIntervalMaxUs = 0u;
uint64_t g_m14PresentOver18Ms = 0u;
uint64_t g_m14PresentOver25Ms = 0u;
uint64_t g_m14PresentOver33Ms = 0u;
uint64_t g_m14PresentCallTotalUs = 0u;
uint64_t g_m14PresentCallMaxUs = 0u;
std::chrono::steady_clock::time_point g_m14LastSuccessfulPresent{};

uint64_t m14_present_percentile_us(uint64_t percentile) {
  if (g_m14PresentIntervalCount == 0u) return 0u;
  const uint64_t target = (g_m14PresentIntervalCount * percentile + 99u) / 100u;
  uint64_t cumulative = 0u;
  for (uint64_t i = 0u; i < kM14PresentPacingBins; ++i) {
    cumulative += g_m14PresentIntervalHist[i];
    if (cumulative >= target) return (i + 1u) * kM14PresentPacingBinUs;
  }
  return kM14PresentPacingBins * kM14PresentPacingBinUs;
}

void m14_log_present_pacing(const char* reason) {
  if (g_m14PresentCount == 0u) return;
  const uint64_t avgInterval = g_m14PresentIntervalCount == 0u
                                   ? 0u
                                   : g_m14PresentIntervalTotalUs / g_m14PresentIntervalCount;
  const uint64_t avgCall = g_m14PresentCallTotalUs / g_m14PresentCount;
  Log.info("M14 host present pacing reason={} presents={} intervals={} avg_us={} p50_us={} p95_us={} p99_us={} max_us={} over18={} over25={} over33={} present_call_avg_us={} present_call_max_us={}",
           reason, g_m14PresentCount, g_m14PresentIntervalCount, avgInterval,
           m14_present_percentile_us(50u), m14_present_percentile_us(95u),
           m14_present_percentile_us(99u), g_m14PresentIntervalMaxUs,
           g_m14PresentOver18Ms, g_m14PresentOver25Ms, g_m14PresentOver33Ms,
           avgCall, g_m14PresentCallMaxUs);
}

void m14_record_successful_present(std::chrono::steady_clock::time_point callStart,
                                   std::chrono::steady_clock::time_point callEnd) {
  const auto callElapsed = std::chrono::duration_cast<std::chrono::microseconds>(callEnd - callStart).count();
  const uint64_t callUs = callElapsed > 0 ? static_cast<uint64_t>(callElapsed) : 0u;
  ++g_m14PresentCount;
  g_m14PresentCallTotalUs += callUs;
  if (callUs > g_m14PresentCallMaxUs) g_m14PresentCallMaxUs = callUs;

  if (g_m14LastSuccessfulPresent.time_since_epoch().count() != 0) {
    const auto intervalElapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        callEnd - g_m14LastSuccessfulPresent).count();
    const uint64_t intervalUs = intervalElapsed > 0 ? static_cast<uint64_t>(intervalElapsed) : 0u;
    ++g_m14PresentIntervalCount;
    g_m14PresentIntervalTotalUs += intervalUs;
    if (intervalUs > g_m14PresentIntervalMaxUs) g_m14PresentIntervalMaxUs = intervalUs;
    if (intervalUs > 18000u) ++g_m14PresentOver18Ms;
    if (intervalUs > 25000u) ++g_m14PresentOver25Ms;
    if (intervalUs > 33000u) ++g_m14PresentOver33Ms;
    uint64_t bin = intervalUs / kM14PresentPacingBinUs;
    if (bin >= kM14PresentPacingBins) bin = kM14PresentPacingBins - 1u;
    ++g_m14PresentIntervalHist[bin];
  }
  g_m14LastSuccessfulPresent = callEnd;

  if (g_m14PresentCount >= 256u &&
      (g_m14PresentCount & (g_m14PresentCount - 1u)) == 0u) {
    m14_log_present_pacing("periodic");
  }
}
'''

PRESENT_OLD = r'''        if (window::is_presentable()) {
          status = g_surface.Present();
        }
      }
      if (status) {
        gfx::after_present();
'''
PRESENT_NEW = r'''        if (window::is_presentable()) {
          const auto m14PresentCallStart = std::chrono::steady_clock::now();
          status = g_surface.Present();
          const auto m14PresentCallEnd = std::chrono::steady_clock::now();
          if (status) {
            m14_record_successful_present(m14PresentCallStart, m14PresentCallEnd);
          }
        }
      }
      if (status) {
        gfx::after_present();
'''

SHUTDOWN_OLD = r'''  gx::fifo::shutdown();
  gfx::render_worker::synchronize();
#ifdef AURORA_ENABLE_RMLUI
'''
SHUTDOWN_NEW = r'''  gx::fifo::shutdown();
  gfx::render_worker::synchronize();
  m14_log_present_pacing("shutdown");
#ifdef AURORA_ENABLE_RMLUI
'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")

    if PHASE_SIGNATURE not in text:
        raise RuntimeError("begin/update phase diagnostics must be applied before present pacing")

    if SIGNATURE in text:
        required = (
            "M14 host present pacing reason={}",
            "m14_record_successful_present(m14PresentCallStart, m14PresentCallEnd);",
            'm14_log_present_pacing("shutdown");',
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError("present-pacing signature is incomplete: " + ", ".join(missing))
        return "already-applied"

    replacements = (
        (STATE_ANCHOR, STATE_INSERT, "present pacing state"),
        (PRESENT_OLD, PRESENT_NEW, "successful surface Present instrumentation"),
        (SHUTDOWN_OLD, SHUTDOWN_NEW, "shutdown pacing summary"),
    )
    for old, _new, label in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{TARGET}: {label}: expected source fragment exactly once, found {count}")
    for old, new, _label in replacements:
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
        print(f"error: Aurora present-pacing diagnostic adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora host present pacing diagnostics already applied",
        "verified": "Aurora host present pacing diagnostic transforms verified",
        "applied": "Aurora host present pacing diagnostics applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
