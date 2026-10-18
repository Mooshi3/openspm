#!/usr/bin/env python3
"""Summarise OpenSPM GX frame captures (F8 / flipcapture) from launch.log.

The capture prints one line per draw, texture bind, TEV stage and EFB copy.
This groups them per capture generation and prints, for every texture copy of
the target logical size (default 72x88 = Mario's flip card):

  * the draws just before and after the copy (what the EFB held);
  * every draw that samples the copy's destination, with its TEV, blend,
    depth, write-mask and alpha-compare state;

plus the bbox trace (writes/reads) logged alongside the capture.

  python tools/diag/analyze_gx_capture.py launch.log [--size 72x88] [--context 6]
      [--texsize 86x144] [--range 3550-3650] [--limit 60]

Copy sizes come from the source rectangle: the audit logs a copy before the
renderer sets its destination size, so dst_size shows the previous copy's.
The M14 snapshot lines (star door / flip snapshot resolution) are listed too.

--texsize lists draws that bind a texture of that size (comma-separated sizes)
wherever its data lives
(the star-door halves sample CPU snapshots of the capture, not the copy);
--range dumps every draw in a draw-number range.
--passes prints one block per pass between EFB copies: the textures it
sampled by guest address (marking EFB copy targets and the snapshot buffers
from the M14 snapshot lines) and where its copy went.
--bbox-wide N lists, for every bbox read at least N logical pixels wide, the
draws since the previous read (what the game measured; e.g. Mario's flip
capture rect coming out full-screen).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

KV = re.compile(r"(\w+)=([^\s]+)")


def fields(line: str) -> dict[str, str]:
    return dict(KV.findall(line))


@dataclass
class Draw:
    n: int
    info: dict[str, str]
    textures: list[dict[str, str]] = field(default_factory=list)
    tev: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Copy:
    after_draw: int  # number of draws recorded before this copy
    info: dict[str, str]


@dataclass
class Capture:
    gen: str
    draws: dict[int, Draw] = field(default_factory=dict)
    copies: list[Copy] = field(default_factory=list)
    bbox: list[str] = field(default_factory=list)
    bbox_reads: list[tuple[int, tuple[int, int, int, int]]] = field(default_factory=list)


def parse(lines: list[str]) -> list[Capture]:
    captures: list[Capture] = []
    current: Capture | None = None
    last_draw = 0
    for line in lines:
        if "deep-frame audit active gen=" in line:
            current = Capture(fields(line).get("gen", "?"))
            captures.append(current)
            last_draw = 0
            continue
        if current is None:
            continue
        if "M14 GX audit draw gen=" in line:
            info = fields(line)
            n = int(info.get("n", "0"))
            current.draws[n] = Draw(n, info)
            last_draw = n
        elif "M14 GX audit texture draw=" in line:
            info = fields(line)
            draw = current.draws.get(int(info.get("draw", "0")))
            if draw:
                draw.textures.append(info)
        elif "M14 GX audit tev draw=" in line:
            info = fields(line)
            draw = current.draws.get(int(info.get("draw", "0")))
            if draw:
                draw.tev.append(info)
        elif "M14 GX deep EFB copy" in line:
            current.copies.append(Copy(last_draw, fields(line)))
        elif "M14 bbox trace" in line:
            # The renderer's trace lines carry the capture's draw number (draw=N);
            # older logs fall back to file order, which is not reliable.
            ordinal = re.search(r" draw=(\d+)", line)
            after = int(ordinal.group(1)) if ordinal else last_draw
            current.bbox.append(f"after draw #{after}: " + line.split("M14 bbox trace", 1)[1].strip())
            box = re.search(r"read .*?l,r,t,b=(\d+),(\d+),(\d+),(\d+)", line)
            if box:
                current.bbox_reads.append((after, tuple(int(v) for v in box.groups())))
    return captures


def copy_size(copy: Copy) -> str | None:
    # src is "x,y,WxH"; the destination size of a texture copy is the source
    # rectangle (display copies too). dst_size lags one copy behind in the audit.
    src = copy.info.get("src")
    return src.split(",")[-1] if src else copy.info.get("dst_size")


def draw_summary(draw: Draw, copy_dst: str | None = None) -> str:
    i = draw.info
    parts = [f"#{draw.n:<4} prim={i.get('prim')} vtx={i.get('vtx')} stages={i.get('tev_stages')}",
             f"blend={i.get('blend')}({i.get('src')},{i.get('dst')}) logic={i.get('logic')}",
             f"depth={i.get('depth')} writes(c/a)={i.get('writes')} cull={i.get('cull')}",
             f"acmp={i.get('alpha_cmp')} dst_alpha={i.get('dst_alpha')}"]
    for tex in draw.textures:
        mark = " <== CAPTURE" if copy_dst is not None and tex.get("data") == copy_dst else ""
        parts.append(f"\n        tex map={tex.get('map')} {tex.get('w')}x{tex.get('h')} fmt={tex.get('fmt')} "
                     f"copy={tex.get('copy')} host={tex.get('host')} wrap={tex.get('wrap')} data={tex.get('data')}{mark}")
    for tev in draw.tev:
        parts.append(f"\n        tev{tev.get('stage')} map={tev.get('map')} tc={tev.get('tc')} tcg={tev.get('tcg')} "
                     f"color={tev.get('color_abcd')} op={tev.get('color_op')} alpha={tev.get('alpha_abcd')} "
                     f"aop={tev.get('alpha_op')}")
    return "  ".join(parts[:4]) + "".join(parts[4:])


def tex_guest(tex: dict[str, str]) -> int | None:
    """Guest address of a bound texture: TEX_IMAGE3 holds address >> 5."""
    try:
        return (int(tex.get("image3", ""), 16) & 0x00FFFFFF) << 5
    except ValueError:
        return None


def copy_guest(copy: Copy, host_to_guest: dict[str, int]) -> int | None:
    value = copy.info.get("guest")
    if value:
        return int(value, 16) & 0x1FFFFFFF
    return host_to_guest.get(copy.info.get("dst", ""))  # older logs: via a texture at the same host pointer


def print_passes(cap: Capture, snapshots: list[str]) -> None:
    """One block per pass (the draws between two EFB copies): what it drew,
    which textures it sampled (by guest address, marking EFB copies and the
    snapshot buffers named in the M14 snapshot lines) and where its copy went."""
    host_to_guest: dict[str, int] = {}
    for draw in cap.draws.values():
        for tex in draw.textures:
            g = tex_guest(tex)
            if g is not None and tex.get("data"):
                host_to_guest.setdefault(tex["data"], g)
    named: dict[int, str] = {}
    for line in snapshots:
        for key in ("buffer", "source"):
            m = re.search(rf"\b{key}=0x([0-9A-Fa-f]{{8}})", line)
            if m:
                named.setdefault(int(m.group(1), 16) & 0x1FFFFFFF, f"snapshot {key}")
    copy_targets: dict[int, list[int]] = {}
    for index, copy in enumerate(cap.copies):
        g = copy_guest(copy, host_to_guest)
        if g is not None:
            copy_targets.setdefault(g, []).append(index)
    print(f"\n  --- passes (draws between EFB copies); guest addresses physical")
    start = 1
    bounds = [(c.after_draw, i) for i, c in enumerate(cap.copies)] + [(max(cap.draws, default=0), None)]
    for end, index in bounds:
        span = [cap.draws[n] for n in range(start, end + 1) if n in cap.draws]
        if span or index is not None:
            textures: dict[tuple, int] = {}
            for draw in span:
                for tex in draw.textures:
                    g = tex_guest(tex)
                    key = (tex.get("w"), tex.get("h"), tex.get("fmt"), tex.get("copy"), g)
                    textures[key] = textures.get(key, 0) + 1
            print(f"\n  pass draws #{start}-#{end} ({len(span)} draws)")
            for (w, h, fmt, is_copy, g), count in sorted(textures.items(), key=lambda kv: -kv[1])[:24]:
                notes = []
                if g is not None and g in copy_targets:
                    notes.append("written by copy " + ",".join(f"#{cap.copies[i].after_draw}" for i in copy_targets[g]))
                if g is not None and g in named:
                    notes.append(named[g])
                gtxt = f"0x{g:08X}" if g is not None else "?"
                print(f"    tex {w}x{h} fmt={fmt} efbcopy={is_copy} guest={gtxt} x{count}"
                      + (f"  <- {'; '.join(notes)}" if notes else ""))
            if index is not None:
                copy = cap.copies[index]
                c = copy.info
                g = copy_guest(copy, host_to_guest)
                gtxt = f"0x{g:08X}" if g is not None else "? (log without guest=)"
                extra = f"  <- {named[g]}" if g is not None and g in named else ""
                print(f"    => copy after #{copy.after_draw}: {c.get('kind')} src={c.get('src')} fmt={c.get('fmt')} "
                      f"clear={c.get('clear')} guest={gtxt}{extra}")
        start = end + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--size", default="72x88", help="logical copy size to follow (WxH)")
    parser.add_argument("--context", type=int, default=6, help="draws to show before/after the copy")
    parser.add_argument("--texsize", help="also list draws binding a texture of this size (WxH)")
    parser.add_argument("--range", help="also dump draws A-B")
    parser.add_argument("--limit", type=int, default=60, help="max draws per --texsize/--range listing")
    parser.add_argument("--bbox-wide", type=int, help="draws feeding bbox reads at least this wide")
    parser.add_argument("--passes", action="store_true",
                        help="summarise every pass between EFB copies (textures by guest address, copy targets)")
    args = parser.parse_args()
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    captures = parse(lines)
    snapshots = [l.split("] ", 2)[-1].strip() for l in lines
                 if "M14 snapshot" in l and "M14 snapshot scan" not in l]
    if snapshots:
        print(f"=== M14 snapshot lines: {len(snapshots)} (first 150)")
        for line in snapshots[:150]:
            print("  " + line)
        print()
    if not captures:
        print("no GX capture in this log (run with flipcapture, or press F8)")
        return 1
    for cap in captures:
        print(f"=== capture gen={cap.gen}: {len(cap.draws)} draws, {len(cap.copies)} EFB copies, "
              f"{len(cap.bbox)} bbox trace lines")
        for copy in cap.copies:
            c = copy.info
            print(f"  copy after draw #{copy.after_draw}: kind={c.get('kind')} src={c.get('src')} "
                  f"size={copy_size(copy)} fmt={c.get('fmt')} clear={c.get('clear')} dst={c.get('dst')}")
        for copy in cap.copies:
            if copy_size(copy) != args.size:
                continue
            dst = copy.info.get("dst")
            print(f"\n  --- {args.size} copy dst={dst} after draw #{copy.after_draw}: draws around it")
            for n in range(max(1, copy.after_draw - args.context + 1), copy.after_draw + args.context + 1):
                if n in cap.draws:
                    marker = "  | " if n <= copy.after_draw else "  > "
                    print(marker + draw_summary(cap.draws[n], dst))
            users = [d for d in cap.draws.values() if any(t.get("data") == dst for t in d.textures)]
            print(f"\n  --- draws sampling {dst}: {len(users)}")
            for draw in users:
                print("    " + draw_summary(draw, dst))
        if args.texsize:
            wanted = {tuple(s.split("x", 1)) for s in args.texsize.split(",")}
            users = [d for n, d in sorted(cap.draws.items())
                     if any((t.get("w"), t.get("h")) in wanted for t in d.textures)]
            print(f"\n  --- draws binding a {args.texsize} texture: {len(users)} (first {args.limit})")
            for draw in users[:args.limit]:
                print("    " + draw_summary(draw))
        if args.range:
            first, last = (int(v) for v in args.range.split("-", 1))
            span = [cap.draws[n] for n in range(first, last + 1) if n in cap.draws]
            print(f"\n  --- draws #{first}-#{last}: {len(span)} (first {args.limit})")
            for draw in span[:args.limit]:
                print("    " + draw_summary(draw))
        if args.bbox_wide:
            previous = 0
            shown = 0
            for after, (l, r, t_, b) in cap.bbox_reads:
                if r >= l and r - l + 1 >= args.bbox_wide and shown < 6:
                    shown += 1
                    span = [cap.draws[n] for n in range(previous + 1, after + 1) if n in cap.draws]
                    print(f"\n  --- bbox read after draw #{after}: l,r,t,b={l},{r},{t_},{b}; "
                          f"draws #{previous + 1}-#{after} ({len(span)}, last {args.limit}):")
                    for draw in span[-args.limit:]:
                        print("    " + draw_summary(draw))
                previous = after
        if args.passes:
            print_passes(cap, snapshots)
        if cap.bbox:
            print("\n  bbox trace:")
            for entry in cap.bbox[:200]:
                print("    " + entry)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
