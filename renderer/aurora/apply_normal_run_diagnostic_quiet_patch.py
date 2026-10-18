#!/usr/bin/env python3
"""Remove high-frequency M14 draw profiling from ordinary player builds.

The 003347/005106/010442 runtime logs proved that the once-useful 2048-draw
profiler now emits tens of thousands of lines and measures steady_clock on every
draw. That contaminates the end-to-end performance frontier after guest-array
staging became much cheaper.

This migration is intentionally semantics-free: it restores handle_draw to the
same command decode + draw_prim call shape it had before profiling. The old
profiler script remains in the repository for deliberate/manual investigations.

The current DrawDone substage profiler is a newer bounded diagnostic successor.
When it is active, this historical quiet migration must leave it alone rather
than treating its handle_draw body as unknown source drift. The companion chain
later verifies/reapplies that same DrawDone profiler idempotently.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
PROFILE_SIGNATURE = "M14 GX draw CPU profile"
QUIET_SIGNATURE = "M14 normal-run draw profiler disabled"
DRAWDONE_SUCCESSOR_SIGNATURE = "M14 GX DrawDone substage profiler active"

PROFILE_BLOCK = '''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 GX draw CPU profile: diagnostic only. Keep timing outside draw_prim so
  // the sample includes vertex parsing, raw-array staging and draw preparation
  // exactly as the synchronous DrawDone drain experiences them.
  static u64 sM14DrawCpuCalls = 0;
  static u64 sM14DrawCpuWindowNanos = 0;
  static u64 sM14DrawCpuWindowMaxNanos = 0;
  constexpr u64 M14DrawCpuWindow = 2048;

  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  const u16 vtxCount = reader.read<u16>();
  const auto start = std::chrono::steady_clock::now();
  draw_prim(prim, fmt, vtxCount, reader);
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start).count();
  const u64 nanos = elapsed > 0 ? static_cast<u64>(elapsed) : 0u;
  ++sM14DrawCpuCalls;
  sM14DrawCpuWindowNanos += nanos;
  sM14DrawCpuWindowMaxNanos = std::max(sM14DrawCpuWindowMaxNanos, nanos);

  if ((sM14DrawCpuCalls % M14DrawCpuWindow) == 0u) {
    Log.info("M14 GX draw CPU profile calls={} window={} cpu_us={} avg_us={} max_us={}",
             sM14DrawCpuCalls, M14DrawCpuWindow,
             sM14DrawCpuWindowNanos / 1000u,
             (sM14DrawCpuWindowNanos / M14DrawCpuWindow) / 1000u,
             sM14DrawCpuWindowMaxNanos / 1000u);
    sM14DrawCpuWindowNanos = 0u;
    sM14DrawCpuWindowMaxNanos = 0u;
  }
}
'''

PLAIN_BLOCK = '''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  draw_prim(prim, fmt, reader.read<u16>(), reader);
}
'''

QUIET_BLOCK = '''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  // M14 normal-run draw profiler disabled: keep ordinary gameplay free of
  // per-draw host-clock reads and high-volume aggregate log writes.
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  draw_prim(prim, fmt, reader.read<u16>(), reader);
}
'''


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    if DRAWDONE_SUCCESSOR_SIGNATURE in text:
        return "already-superseded"
    if QUIET_SIGNATURE in text:
        return "already-applied"

    if PROFILE_SIGNATURE in text:
        count = text.count(PROFILE_BLOCK)
        if count != 1:
            raise RuntimeError(
                f"active draw profiler differs from known migration block: {count=}")
        text = text.replace(PROFILE_BLOCK, QUIET_BLOCK, 1)
        result = "verified-migration" if check_only else "migrated"
    else:
        # Fresh Aurora checkouts never had the profiler. Mark the ordinary-run
        # policy without changing draw semantics, so later configure passes are
        # idempotent and can distinguish an intentionally quiet build.
        count = text.count(PLAIN_BLOCK)
        if count != 1:
            raise RuntimeError(
                f"quiet handle_draw anchor expected once when profiler absent, found {count}")
        text = text.replace(PLAIN_BLOCK, QUIET_BLOCK, 1)
        result = "verified-quiet" if check_only else "quieted"

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
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
        print({
            "already-superseded": "Aurora normal-run quiet migration superseded by DrawDone substage profiler",
            "already-applied": "Aurora normal-run draw diagnostics already quiet",
            "verified-migration": "Aurora draw-profiler retirement migration verified",
            "migrated": "Aurora high-frequency draw profiler retired for normal runs",
            "verified-quiet": "Aurora quiet draw path verified",
            "quieted": "Aurora normal-run draw path marked quiet",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM normal-run diagnostic quiet adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
