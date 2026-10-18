#!/usr/bin/env python3
"""Add cumulative OpenSPM diagnostics for Aurora frame-acquisition waits.

Early M14 probes logged only waits >=16 ms. Runtime 022139 proved that removing
61k redundant SDL event pumps did not improve the steady ~53-55 retraces/s, so
sub-frame waits now matter: a repeated 1-3 ms wait can consume far more time
than a rare 100 ms hitch without ever tripping the old logger.

This patch records every frame-slot, staging-slot, and staging-map wait while
keeping logs sparse (first 8 samples, powers of two, and >=16 ms waits). It is
diagnostic-only: ownership, counts, ordering, parsing, presentation and guest
semantics are unchanged. The transform upgrades an already-applied v1 probe in
cached Aurora checkouts and is also able to apply directly to the pinned source.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
FRAME = pathlib.Path("lib/gfx/frame.cpp")
V1_SIGNATURE = "kM14FrameAcquireSlowUs"
V2_SIGNATURE = "kM14FrameAcquireStatsV2"

INSERT_OLD = """render_worker::FrameSlotPool g_frameSlots{FrameSlotCount};
render_worker::FrameSlotPool g_stagingSlots{StagingBufferCount};
"""

DIAG_STATE = """render_worker::FrameSlotPool g_frameSlots{FrameSlotCount};
render_worker::FrameSlotPool g_stagingSlots{StagingBufferCount};

// OpenSPM diagnostic only: cumulative synchronous begin-frame waits.  These
// counters must never participate in pacing or ownership decisions.
constexpr std::uint64_t kM14FrameAcquireSlowUs = 16000u;
constexpr std::uint64_t kM14FrameAcquireStatsV2 = 2u;
struct M14FrameAcquireWaitCounter {
  std::uint64_t calls = 0u;
  std::uint64_t totalUs = 0u;
  std::uint64_t slow = 0u;
  std::uint64_t maxUs = 0u;
};
M14FrameAcquireWaitCounter g_m14FrameSlotWait{};
M14FrameAcquireWaitCounter g_m14StagingSlotWait{};
M14FrameAcquireWaitCounter g_m14StagingMapWait{};

void m14_log_frame_acquire_wait(const char* kind, M14FrameAcquireWaitCounter& counter,
                                std::uint64_t waitUs, size_t slot) {
  ++counter.calls;
  counter.totalUs += waitUs;
  if (waitUs > counter.maxUs) counter.maxUs = waitUs;
  const bool isSlow = waitUs >= kM14FrameAcquireSlowUs;
  if (isSlow) ++counter.slow;
  const bool sparseSample = counter.calls <= 8u ||
      (counter.calls != 0u && (counter.calls & (counter.calls - 1u)) == 0u);
  if (!sparseSample && !isSlow) return;
  const auto avgUs = counter.calls != 0u ? counter.totalUs / counter.calls : 0u;
  Log.info("M14 Aurora frame-acquire stats kind={} slot={} calls={} total_us={} avg_us={} max_us={} slow={} last_us={}",
           kind, slot, counter.calls, counter.totalUs, avgUs, counter.maxUs, counter.slow, waitUs);
}
"""

V1_STRUCT = """struct M14FrameAcquireWaitCounter {
  std::uint64_t slow = 0u;
  std::uint64_t maxUs = 0u;
};
"""

V2_STRUCT = """constexpr std::uint64_t kM14FrameAcquireStatsV2 = 2u;
struct M14FrameAcquireWaitCounter {
  std::uint64_t calls = 0u;
  std::uint64_t totalUs = 0u;
  std::uint64_t slow = 0u;
  std::uint64_t maxUs = 0u;
};
"""

V1_LOGGER = """void m14_log_frame_acquire_wait(const char* kind, M14FrameAcquireWaitCounter& counter,
                                std::uint64_t waitUs, size_t slot) {
  if (waitUs < kM14FrameAcquireSlowUs) return;
  ++counter.slow;
  const bool newMax = waitUs > counter.maxUs;
  if (newMax) counter.maxUs = waitUs;
  if (counter.slow > 8u && (counter.slow & (counter.slow - 1u)) != 0u && !newMax) return;
  Log.info("M14 Aurora frame-acquire wait kind={} slot={} wait_us={} slow={} max_us={}",
           kind, slot, waitUs, counter.slow, counter.maxUs);
}
"""

V2_LOGGER = """void m14_log_frame_acquire_wait(const char* kind, M14FrameAcquireWaitCounter& counter,
                                std::uint64_t waitUs, size_t slot) {
  ++counter.calls;
  counter.totalUs += waitUs;
  if (waitUs > counter.maxUs) counter.maxUs = waitUs;
  const bool isSlow = waitUs >= kM14FrameAcquireSlowUs;
  if (isSlow) ++counter.slow;
  const bool sparseSample = counter.calls <= 8u ||
      (counter.calls != 0u && (counter.calls & (counter.calls - 1u)) == 0u);
  if (!sparseSample && !isSlow) return;
  const auto avgUs = counter.calls != 0u ? counter.totalUs / counter.calls : 0u;
  Log.info("M14 Aurora frame-acquire stats kind={} slot={} calls={} total_us={} avg_us={} max_us={} slow={} last_us={}",
           kind, slot, counter.calls, counter.totalUs, avgUs, counter.maxUs, counter.slow, waitUs);
}
"""

WAIT_MAP_OLD = """bool wait_for_staging_buffer(size_t slot) {
  ZoneScopedN(\"Wait for buffer map\");
  map_staging_buffer(slot);
  while (true) {
    const auto mappingState = g_mappingStates[slot].load(std::memory_order_acquire);
    if (mappingState == BufferMapState::Mapped) {
      return true;
    }
    if (mappingState == BufferMapState::Unmapped) {
      return false;
    }
    wait_for_gpu_progress(std::chrono::milliseconds{1});
  }
}
"""
WAIT_MAP_NEW = """bool wait_for_staging_buffer(size_t slot) {
  ZoneScopedN(\"Wait for buffer map\");
  const auto waitStart = PresentClock::now();
  map_staging_buffer(slot);
  while (true) {
    const auto mappingState = g_mappingStates[slot].load(std::memory_order_acquire);
    if (mappingState == BufferMapState::Mapped) {
      const auto waitUs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
          PresentClock::now() - waitStart).count());
      m14_log_frame_acquire_wait(\"staging-map\", g_m14StagingMapWait, waitUs, slot);
      return true;
    }
    if (mappingState == BufferMapState::Unmapped) {
      const auto waitUs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
          PresentClock::now() - waitStart).count());
      m14_log_frame_acquire_wait(\"staging-map-failed\", g_m14StagingMapWait, waitUs, slot);
      return false;
    }
    wait_for_gpu_progress(std::chrono::milliseconds{1});
  }
}
"""

FRAME_SLOT_OLD = """size_t acquire_frame_slot() {
  ZoneScopedN(\"Acquire frame slot\");
  const auto waitStart = PresentClock::now();
  while (true) {
    if (const auto slot = g_frameSlots.try_acquire()) {
      const auto waitDuration = PresentClock::now() - waitStart;
      const double waitMs = std::chrono::duration<double, std::milli>{waitDuration}.count();
      TracyPlot(\"aurora: frameSlotWaitMs\", waitMs);
      return *slot;
    }
    wait_for_gpu_progress(std::chrono::microseconds{100});
  }
}
"""
FRAME_SLOT_NEW = """size_t acquire_frame_slot() {
  ZoneScopedN(\"Acquire frame slot\");
  const auto waitStart = PresentClock::now();
  while (true) {
    if (const auto slot = g_frameSlots.try_acquire()) {
      const auto waitDuration = PresentClock::now() - waitStart;
      const auto waitUs = static_cast<std::uint64_t>(
          std::chrono::duration_cast<std::chrono::microseconds>(waitDuration).count());
      const double waitMs = std::chrono::duration<double, std::milli>{waitDuration}.count();
      TracyPlot(\"aurora: frameSlotWaitMs\", waitMs);
      m14_log_frame_acquire_wait(\"frame-slot\", g_m14FrameSlotWait, waitUs, *slot);
      return *slot;
    }
    wait_for_gpu_progress(std::chrono::microseconds{100});
  }
}
"""

STAGING_SLOT_OLD = """std::optional<size_t> acquire_mapped_staging_buffer() {
  ZoneScopedN(\"Acquire mapped staging buffer\");
  while (true) {
    if (auto slot = g_stagingSlots.try_acquire()) {
      if (wait_for_staging_buffer(*slot)) {
        return *slot;
      }
      g_stagingSlots.release(*slot);
      return std::nullopt;
    }
    wait_for_gpu_progress(std::chrono::microseconds{100});
  }
}
"""
STAGING_SLOT_NEW = """std::optional<size_t> acquire_mapped_staging_buffer() {
  ZoneScopedN(\"Acquire mapped staging buffer\");
  const auto waitStart = PresentClock::now();
  while (true) {
    if (auto slot = g_stagingSlots.try_acquire()) {
      const auto waitUs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
          PresentClock::now() - waitStart).count());
      m14_log_frame_acquire_wait(\"staging-slot\", g_m14StagingSlotWait, waitUs, *slot);
      if (wait_for_staging_buffer(*slot)) {
        return *slot;
      }
      g_stagingSlots.release(*slot);
      return std::nullopt;
    }
    wait_for_gpu_progress(std::chrono::microseconds{100});
  }
}
"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{FRAME}: {label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / FRAME
    text = path.read_text(encoding="utf-8")

    if V2_SIGNATURE in text:
        required = ('frame-acquire stats kind={}', 'total_us={}', 'avg_us={}',
                    '\"frame-slot\"', '\"staging-slot\"', '\"staging-map\"')
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError("frame-acquire v2 signature present but diagnostics are incomplete: " + ", ".join(missing))
        return "already-applied"

    if V1_SIGNATURE in text:
        text = replace_once(text, V1_STRUCT, V2_STRUCT, "v1 counter upgrade")
        text = replace_once(text, V1_LOGGER, V2_LOGGER, "v1 logger upgrade")
        if not check_only:
            path.write_text(text, encoding="utf-8", newline="\n")
        return "verified-upgrade" if check_only else "upgraded"

    replacements = (
        (INSERT_OLD, DIAG_STATE, "diagnostic state"),
        (WAIT_MAP_OLD, WAIT_MAP_NEW, "staging-map wait"),
        (FRAME_SLOT_OLD, FRAME_SLOT_NEW, "frame-slot wait"),
        (STAGING_SLOT_OLD, STAGING_SLOT_NEW, "staging-slot wait"),
    )
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)
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
        print(f"error: Aurora frame-acquire diagnostic adaptation failed: {exc}", file=sys.stderr)
        return 4
    messages = {
        "already-applied": "Aurora cumulative frame-acquire diagnostics already applied",
        "verified-upgrade": "Aurora cumulative frame-acquire diagnostic upgrade verified",
        "upgraded": "Aurora frame-acquire diagnostics upgraded to cumulative stats",
        "verified": "Aurora cumulative frame-acquire transforms verified",
        "applied": "Aurora cumulative frame-acquire diagnostics applied",
    }
    print(messages[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
