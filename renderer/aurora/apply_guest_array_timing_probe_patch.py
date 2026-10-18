#!/usr/bin/env python3
"""Measure heavy raw guest-array staging cost without per-frame log spam.

Gameplay now proves that complex SPM scenes repeatedly stage roughly 7.3 MiB of
raw indexed-array prefixes per Aurora frame and can run near 30 retraces/s. Byte
volume alone does not prove where host CPU time is spent. Measure wall-clock CPU
work inside m14_stage_guest_array itself (cache scan, byte compare/snapshot and
push_storage) and report sampled high-water frames.

The old high-water diagnostic logged once every heavy frame, producing thousands
of lines during normal gameplay. Keep the one-shot heavy-scene audit trigger, but
replace recurring high-water lines with first-8/powers-of-two profile samples.
This patch changes diagnostics only; GX state, staging bytes and cache semantics
remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw guest-array stage profile"
RETIRED_SIGNATURE = "M14 raw guest-array stage timing probe retired for normal play"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    if RETIRED_SIGNATURE in text:
        return "already-retired"
    if SIGNATURE in text:
        return "already-applied"
    if "M14 heavy-scene GX audit auto-armed" not in text:
        raise RuntimeError("heavy-scene audit layer must be applied first")

    text = once(text, '#include <cstdint>\n#include <cstring>\n',
                '#include <chrono>\n#include <cstdint>\n#include <cstring>\n',
                "command_processor timing include")

    globals_old = '''u64 sM14GuestArrayReuseHits = 0;
u64 sM14GuestArrayReuseMisses = 0;
u64 sM14GuestArrayStagedBytes = 0;
bool sM14GuestArrayReuseReported = false;
bool sM14GuestArrayHighWaterReported = false;
bool sM14HeavySceneAuditArmed = false;
u64 sM14LineDrawCount = 0;
'''
    globals_new = '''u64 sM14GuestArrayReuseHits = 0;
u64 sM14GuestArrayReuseMisses = 0;
u64 sM14GuestArrayStagedBytes = 0;
u64 sM14GuestArrayStageNanos = 0;
u64 sM14GuestArrayStageCalls = 0;
u64 sM14GuestArrayProfileFrames = 0;
bool sM14GuestArrayReuseReported = false;
bool sM14GuestArrayHighWaterReported = false;
bool sM14HeavySceneAuditArmed = false;
u64 sM14LineDrawCount = 0;
'''
    text = once(text, globals_old, globals_new, "guest-array profile globals")

    reset_old = '''void m14_reset_guest_array_stage_cache_if_needed() noexcept {
  const u32 frame = gfx::current_frame();
  if (sM14GuestArrayStageFrame == frame) return;
  sM14GuestArrayStages.clear();
  sM14GuestArrayStageFrame = frame;
  sM14GuestArrayReuseHits = 0;
  sM14GuestArrayReuseMisses = 0;
  sM14GuestArrayStagedBytes = 0;
  sM14GuestArrayHighWaterReported = false;
}
'''
    reset_new = '''void m14_reset_guest_array_stage_cache_if_needed() noexcept {
  const u32 frame = gfx::current_frame();
  if (sM14GuestArrayStageFrame == frame) return;
  if (sM14GuestArrayHighWaterReported) {
    const u64 sample = ++sM14GuestArrayProfileFrames;
    if (sample <= 8u || (sample & (sample - 1u)) == 0u) {
      Log.info("M14 raw guest-array stage profile sample={} frame={} staged={} entries={} calls={} hits={} misses={} cpu_us={}",
               sample, sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes,
               sM14GuestArrayStages.size(), sM14GuestArrayStageCalls,
               sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses,
               sM14GuestArrayStageNanos / 1000u);
    }
  }
  sM14GuestArrayStages.clear();
  sM14GuestArrayStageFrame = frame;
  sM14GuestArrayReuseHits = 0;
  sM14GuestArrayReuseMisses = 0;
  sM14GuestArrayStagedBytes = 0;
  sM14GuestArrayStageNanos = 0;
  sM14GuestArrayStageCalls = 0;
  sM14GuestArrayHighWaterReported = false;
}
'''
    text = once(text, reset_old, reset_new, "guest-array profile frame reset")

    stage_head_old = '''gfx::Range m14_stage_guest_array(AttrArray& array, u32 uploadSize) noexcept {
  AURORA_ASSERT(array.guestMapped && array.data != nullptr && uploadSize != 0,
                "M14 guest-array stage called with invalid raw array");
  m14_reset_guest_array_stage_cache_if_needed();

  const auto* source = static_cast<const u8*>(array.data);
'''
    stage_head_new = '''gfx::Range m14_stage_guest_array(AttrArray& array, u32 uploadSize) noexcept {
  AURORA_ASSERT(array.guestMapped && array.data != nullptr && uploadSize != 0,
                "M14 guest-array stage called with invalid raw array");
  m14_reset_guest_array_stage_cache_if_needed();
  const auto profileStart = std::chrono::steady_clock::now();
  const auto profileFinish = [&]() noexcept {
    const auto elapsed = std::chrono::steady_clock::now() - profileStart;
    sM14GuestArrayStageNanos += static_cast<u64>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count());
    ++sM14GuestArrayStageCalls;
  };

  const auto* source = static_cast<const u8*>(array.data);
'''
    text = once(text, stage_head_old, stage_head_new, "guest-array profile entry")

    hit_old = '''    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array per-frame content reuse active");
      sM14GuestArrayReuseReported = true;
    }
    return it->range;
'''
    hit_new = '''    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array per-frame content reuse active");
      sM14GuestArrayReuseReported = true;
    }
    profileFinish();
    return it->range;
'''
    text = once(text, hit_old, hit_new, "guest-array profile cache hit")

    high_old = '''  if (!sM14GuestArrayHighWaterReported && sM14GuestArrayStagedBytes + uploadSize >= 7u * 1024u * 1024u) {
    Log.info("M14 raw guest-array staging high-water frame={} staged={} next={} entries={} hits={} misses={}",
             sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,
             sM14GuestArrayStages.size(), sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses);
    if (!sM14HeavySceneAuditArmed && sM14AuditRemaining == 0u) {
      audit::arm_seqload_capture(0u);
      sM14HeavySceneAuditArmed = true;
      Log.info("M14 heavy-scene GX audit auto-armed frame={} staged={} next={} entries={} source=0x{:X} stride={}",
               sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,
               sM14GuestArrayStages.size(),
               static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),
               static_cast<u32>(array.stride));
    }
    sM14GuestArrayHighWaterReported = true;
  }
'''
    high_new = '''  if (!sM14GuestArrayHighWaterReported && sM14GuestArrayStagedBytes + uploadSize >= 7u * 1024u * 1024u) {
    if (!sM14HeavySceneAuditArmed && sM14AuditRemaining == 0u) {
      audit::arm_seqload_capture(0u);
      sM14HeavySceneAuditArmed = true;
      Log.info("M14 heavy-scene GX audit auto-armed frame={} staged={} next={} entries={} source=0x{:X} stride={}",
               sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,
               sM14GuestArrayStages.size(),
               static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),
               static_cast<u32>(array.stride));
    }
    sM14GuestArrayHighWaterReported = true;
  }
'''
    text = once(text, high_old, high_new, "guest-array sampled high-water")

    tail_old = '''  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  return range;
}
'''
    tail_new = '''  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    text = once(text, tail_old, tail_new, "guest-array profile miss return")

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
        print(f"error: Aurora guest-array timing probe failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-retired": "Aurora guest-array staging timing probe remains retired for normal play",
        "already-applied": "Aurora guest-array staging timing probe already applied",
        "verified": "Aurora guest-array staging timing probe verified",
        "applied": "Aurora guest-array staging timing probe applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
