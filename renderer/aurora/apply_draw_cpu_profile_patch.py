#!/usr/bin/env python3
"""Add the legacy low-overhead CPU profile around raw GX draw preparation.

The graphics-correct synchronous M14 runtime can spend 50-185 ms inside one
DrawDone drain. Compact guest-array intervals removed the old prefix-copy high
water without materially improving gameplay, so this probe was used to measure
the existing handle_draw -> draw_prim path.

The probe is now retired from ordinary player runs by
apply_normal_run_diagnostic_quiet_patch.py. The newer DrawDone substage profiler
is also a strict successor: it replaces handle_draw with a more useful bounded
producer-thread measurement. Historical configure-chain entries must recognize
either successor as already satisfying this retired profiler layer; otherwise an
incremental CMake reconfigure would try to reinstall the old profiler into the
newer profiled handle_draw shape.

Fresh migration paths may still install the profiler temporarily because older
exact transforms use its source shape as a sequencing marker. The normal-run
quiet migration removes it at the end of the configure chain. No GX/FIFO/frame
semantics are changed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 GX draw CPU profile"
QUIET_SUCCESSOR_SIGNATURE = "M14 normal-run draw profiler disabled"
DRAWDONE_SUCCESSOR_SIGNATURE = "M14 GX DrawDone substage profiler active"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: Path, check_only: bool = False) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    if DRAWDONE_SUCCESSOR_SIGNATURE in text:
        return "already-superseded"
    if QUIET_SUCCESSOR_SIGNATURE in text:
        return "already-retired"
    if SIGNATURE in text:
        return "already-applied"
    if "#include <chrono>" not in text:
        raise RuntimeError("guest-array timing layer is missing; expected <chrono>")
    if "M14 raw guest-array compact interval staging active" not in text:
        raise RuntimeError("compact guest-array interval layer must be active first")

    anchor = '''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
  const auto fmt = static_cast<GXVtxFmt>(cmd & CP_VAT_MASK);
  const auto prim = static_cast<GXPrimitive>(cmd & CP_OPCODE_MASK);
  draw_prim(prim, fmt, reader.read<u16>(), reader);
}
'''
    replacement = '''static void handle_draw(u8 cmd, ByteReader& reader) noexcept {
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
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"handle_draw profile anchor expected once, found {count}")
    text = text.replace(anchor, replacement, 1)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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
            raise RuntimeError(f"Aurora revision mismatch: {actual}")
        result = transform(source, args.check_only)
        print({
            "already-superseded": "Aurora legacy GX draw CPU profiler superseded by DrawDone substage profiler",
            "already-retired": "Aurora GX draw CPU profiler already retired for normal runs",
            "already-applied": "Aurora GX draw CPU profiler already active",
            "verified": "Aurora GX draw CPU profiler verified",
            "applied": "Aurora GX draw CPU profiler applied",
        }[result])
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM draw CPU profiler adaptation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
