#!/usr/bin/env python3
"""Per-second performance summary of an OpenSPM launch.log.

Reads the once-per-second lines the runtime already prints:
  * "M14 main-loop phases": frames and game-phase times (disp/main/done);
  * "M14 renderer budget": renderer time inside those phases (parse, DrawDone,
    present, bbox GPU syncs), when present;
and prints a table plus the slow stretches (seconds under --fps-threshold),
so a slowdown can be attributed to game code versus renderer work.

  python tools/diag/summarize_perf.py launch.log [--fps-threshold 57] [--all]
"""
from __future__ import annotations

import argparse
import re

KV = re.compile(r"(\w+)=(-?\d+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--fps-threshold", type=int, default=57)
    parser.add_argument("--all", action="store_true", help="print every second, not only slow ones")
    args = parser.parse_args()
    rows: list[dict[str, int]] = []
    budget: dict[str, int] | None = None
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "M14 renderer budget" in line:
                text = line.split("M14 renderer budget", 1)[1]
                b: dict[str, int] = {}
                for name in ("fifo_submit", "draw_done", "present"):
                    m = re.search(name + r"=(\d+) calls/(\d+) us", text)
                    if m:
                        b[name + "_us"] = int(m.group(2))
                m = re.search(r"bbox_maps=(\d+)/(\d+) us", text)
                if m:
                    b["bbox_maps"], b["bbox_us"] = int(m.group(1)), int(m.group(2))
                budget = b
            elif "M14 main-loop phases" in line:
                row = {k: int(v) for k, v in KV.findall(line.split("M14 main-loop phases", 1)[1])}
                if budget:
                    row.update(budget)
                    budget = None
                rows.append(row)
    if not rows:
        print("no 'M14 main-loop phases' lines found")
        return 1
    head = (f"{'sec':>4} {'fps':>4} {'disp_avg':>8} {'disp_max':>8} {'main_avg':>8} {'done_avg':>8}"
            f" {'parse_ms':>8} {'drain_ms':>8} {'bbox':>9}")
    print(head)
    slow_runs: list[list[int]] = []
    for i, r in enumerate(rows):
        slow = r.get("frames", 60) < args.fps_threshold
        if slow:
            if slow_runs and slow_runs[-1][-1] == i - 1:
                slow_runs[-1].append(i)
            else:
                slow_runs.append([i])
        if slow or args.all:
            parse = f"{r['fifo_submit_us'] / 1000:.1f}" if "fifo_submit_us" in r else "-"
            drain = f"{r['draw_done_us'] / 1000:.1f}" if "draw_done_us" in r else "-"
            bbox = f"{r['bbox_maps']}/{r['bbox_us'] / 1000:.1f}ms" if "bbox_maps" in r else "-"
            print(f"{i:>4} {r.get('frames', 0):>4} {r.get('disp_avg_us', 0) / 1000:>8.1f} "
                  f"{r.get('disp_max_us', 0) / 1000:>8.1f} {r.get('main_avg_us', 0) / 1000:>8.1f} "
                  f"{r.get('done_avg_us', 0) / 1000:>8.1f} {parse:>8} {drain:>8} {bbox:>9}")
    total = len(rows)
    slow_total = sum(len(run) for run in slow_runs)
    print(f"\n{total} seconds, {slow_total} below {args.fps_threshold} fps in {len(slow_runs)} stretch(es)")
    for run in slow_runs:
        frames = [rows[i].get("frames", 0) for i in run]
        disp = [rows[i].get("disp_avg_us", 0) / 1000 for i in run]
        print(f"  seconds {run[0]}-{run[-1]}: fps {min(frames)}-{max(frames)}, "
              f"disp avg {sum(disp) / len(disp):.1f} ms (game draw code + renderer parse)")
    if slow_total and not any("fifo_submit_us" in rows[i] for run in slow_runs for i in run):
        print("  (no renderer budget lines: this log predates them, so game vs renderer time can't be split)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
