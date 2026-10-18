#!/usr/bin/env python3
"""Retire the M14 blocking-first-use GX pipeline experiment.

Pinned Aurora's generic GX pipeline lookup uses PipelinePriority::Normal, whose
source contract is asynchronous compilation with a skipped draw while a pipeline
is pending. OpenSPM temporarily changed original-Wii GX PipelineConfig lookups to
PipelinePriority::Blocking to test whether skipped first-use draws explained the
white/invisible Bestovius materials.

Player runtime at 6b99582 disproved that as the material repair and measured
roughly 0.44-0.52 second first-use waits, directly worsening visible flip/frame
hitches. Keep the finding as renderer debt, but restore Aurora's prior async
lookup until OpenSPM has a real prewarm/replay strategy that preserves both GX
correctness and frame pacing.

This script is a migration: it recognizes either the old blocking experiment or
the already-restored pinned source shape, so normal M14-UPDATE-RUN reconfigure
heals an existing .openspm Aurora checkout without requiring a cache wipe.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
BLOCKING_SIGNATURE = "M14 raw-Wii GX pipeline correctness: blocking first use"

BASE_INCLUDE = "#include <atomic>\n#include <condition_variable>\n"
BLOCKING_INCLUDE = "#include <atomic>\n#include <chrono>\n#include <condition_variable>\n"

BASE_GX_SPECIALIZATION = '''template <>
PipelineRef find_pipeline(ShaderType type, const gx::PipelineConfig& config, NewPipelineCallback&& cb) {
  return find_pipeline_impl(type, config, std::move(cb));
}
'''

BLOCKING_GX_SPECIALIZATION = '''template <>
PipelineRef find_pipeline(ShaderType type, const gx::PipelineConfig& config, NewPipelineCallback&& cb) {
  // M14 raw-Wii GX pipeline correctness: blocking first use. Aurora's Normal
  // priority is explicitly "async skip draw"; a console FIFO draw may stall
  // for host compilation, but it must never be discarded merely because its
  // WebGPU pipeline is not ready yet. Cache/background warming stays unchanged.
  using M14PipelineClock = std::chrono::steady_clock;
  static std::atomic<uint64_t> m14BlockingLookupCount{0};
  static std::atomic<uint64_t> m14SlowBlockingLookupCount{0};
  static std::atomic<uint64_t> m14MaxBlockingLookupUs{0};

  const auto start = M14PipelineClock::now();
  const PipelineRef ref =
      find_pipeline_impl(type, config, std::move(cb), PipelinePriority::Blocking);
  const uint64_t wallUs = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
      M14PipelineClock::now() - start).count());
  const uint64_t lookup = m14BlockingLookupCount.fetch_add(1, std::memory_order_relaxed) + 1;

  uint64_t oldMax = m14MaxBlockingLookupUs.load(std::memory_order_relaxed);
  while (wallUs > oldMax &&
         !m14MaxBlockingLookupUs.compare_exchange_weak(oldMax, wallUs, std::memory_order_relaxed)) {
  }

  // Fast cache hits are intentionally silent. Report waits large enough to be
  // player-visible, bounded to first-eight/powers-of-two to keep launch.log useful.
  if (wallUs >= 2000u) {
    const uint64_t slow = m14SlowBlockingLookupCount.fetch_add(1, std::memory_order_relaxed) + 1;
    if (slow <= 8u || (slow & (slow - 1u)) == 0u) {
      Log.info("M14 GX blocking pipeline wait lookup={} slow={} wall_us={} max_us={} ref={}",
               lookup, slow, wallUs, m14MaxBlockingLookupUs.load(std::memory_order_relaxed), ref);
    }
  }
  return ref;
}
'''


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: Path, check_only: bool) -> str:
    path = source / "lib/gfx/pipeline_cache.cpp"
    text = original = path.read_text(encoding="utf-8")

    blocking_count = text.count(BLOCKING_GX_SPECIALIZATION)
    base_count = text.count(BASE_GX_SPECIALIZATION)
    if blocking_count == 1 and base_count == 0:
        text = text.replace(BLOCKING_GX_SPECIALIZATION, BASE_GX_SPECIALIZATION, 1)
        if text.count(BLOCKING_INCLUDE) == 1:
            text = text.replace(BLOCKING_INCLUDE, BASE_INCLUDE, 1)
        elif text.count(BASE_INCLUDE) != 1:
            raise RuntimeError("pipeline_cache.cpp: blocking specialization found but chrono/include shape drifted")
        result = "rolled back"
    elif blocking_count == 0 and base_count == 1:
        if BLOCKING_SIGNATURE in text:
            raise RuntimeError("pipeline_cache.cpp: blocking marker remains after specialization rollback")
        result = "already restored"
    else:
        raise RuntimeError(
            f"pipeline_cache.cpp: expected exactly one blocking or base GX specialization; "
            f"blocking={blocking_count} base={base_count}"
        )

    if BLOCKING_SIGNATURE in text:
        raise RuntimeError("pipeline_cache.cpp: blocking first-use experiment still present")
    if "find_pipeline_impl(type, config, std::move(cb), PipelinePriority::Blocking);" in text:
        raise RuntimeError("pipeline_cache.cpp: blocking GX lookup still present")
    if text.count(BASE_GX_SPECIALIZATION) != 1:
        raise RuntimeError("pipeline_cache.cpp: restored GX specialization missing or duplicated")

    changed = text != original
    if changed and not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    if check_only:
        return "verified restored async GX lookup"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()

    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
        if actual != expected:
            raise RuntimeError(f"Aurora revision mismatch: expected {expected}, got {actual}")
        result = transform(source, args.check_only)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM blocking raw-GX pipeline rollback failed: {exc}", file=sys.stderr)
        return 1

    print("Aurora raw-GX blocking pipeline experiment " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
