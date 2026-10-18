#!/usr/bin/env python3
"""Analyze the newest complete OpenSPM Yold one-NPC active-bit sweep.

The sweep is intentionally a live-console experiment, not a build variant. This
tool anchors on the newest yold-npc-baseline capture and only accepts ordinal
captures created shortly after it, preventing old/partial sessions from being
silently mixed into one ranking.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import analyze_m14_perf  # noqa: E402

CAPTURE_DIR = ROOT / ".openspm" / "m14" / "debug-captures"
SESSION_WINDOW_SECONDS = 120.0
PULSE_RE = re.compile(
    r"NPC one-pulse=on ordinal=(\d+) index=(\d+) id=(\d+) "
    r"name=([^\s]+) ticks=(\d+)"
)


def newest(pattern: str) -> Path:
    matches = sorted(
        CAPTURE_DIR.glob(f"*-{pattern}.log"),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )
    if not matches:
        raise OSError(f"no capture matching {pattern!r} in {CAPTURE_DIR}")
    return matches[0]


def parse_identity(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = list(PULSE_RE.finditer(text))
    if not matches:
        return {}
    match = matches[-1]
    return {
        "ordinal": int(match.group(1)),
        "table_index": int(match.group(2)),
        "id": int(match.group(3)),
        "name": match.group(4),
        "requested_ticks_after_first_apply": int(match.group(5)),
    }


def load_latest_session() -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    baseline_path = newest("yold-npc-baseline")
    baseline_time = baseline_path.stat().st_mtime
    baseline = analyze_m14_perf.parse_file(baseline_path)
    if int(baseline.get("phase_windows") or 0) < 1:
        raise OSError(
            f"baseline capture has no complete phase window: {baseline_path.name}"
        )

    rows: list[dict[str, Any]] = []
    for ordinal in range(7):
        pattern = f"yold-npc-{ordinal}"
        matches = sorted(
            CAPTURE_DIR.glob(f"*-{pattern}.log"),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
            reverse=True,
        )
        eligible = [
            path for path in matches
            if baseline_time <= path.stat().st_mtime
            and path.stat().st_mtime - baseline_time <= SESSION_WINDOW_SECONDS
        ]
        if not eligible:
            raise OSError(
                f"latest sweep is incomplete: no {pattern} capture within "
                f"{SESSION_WINDOW_SECONDS:.0f}s after {baseline_path.name}"
            )
        path = eligible[0]
        text = path.read_text(encoding="utf-8", errors="replace")
        if f"YOLD-NPC-SWEEP ordinal={ordinal}" not in text:
            raise OSError(
                f"{path.name} does not contain the expected ordinal marker "
                f"for {ordinal}"
            )
        identity = parse_identity(path)
        if identity and identity.get("ordinal") != ordinal:
            raise OSError(
                f"{path.name} logged the wrong pulse ordinal: {identity}"
            )
        if not identity:
            # After the first 64 cockpit commands, backend command BEGIN/END
            # logging intentionally becomes power-of-two sampled. The capture
            # marker remains authoritative for ordinal identity, while exact
            # table index/name may be unavailable from launch.log alone.
            identity = {
                "ordinal": ordinal,
                "table_index": -1,
                "id": -1,
                "name": f"ordinal-{ordinal}",
                "requested_ticks_after_first_apply": None,
            }
        perf = analyze_m14_perf.parse_file(path)
        if int(perf.get("phase_windows") or 0) < 1:
            raise OSError(
                f"{path.name} has no complete phase timing window; rerun the sweep"
            )
        row = {
            "path": str(path),
            **identity,
            "fps_median": perf.get("fps_median"),
            "disp_ms_median": perf.get("disp_ms_median"),
            "main_ms_median": perf.get("main_ms_median"),
            "disp_plus_main_ms_median": perf.get("disp_plus_main_ms_median"),
            "drawdone_ms_median": perf.get("drawdone_ms_median"),
            "first_retrace_critical_ms": perf.get("first_retrace_critical_ms"),
            "phase_windows": perf.get("phase_windows"),
        }
        rows.append(row)
    return baseline_path, baseline, rows


def delta(value: float | None, base: float | None) -> float | None:
    if value is None or base is None:
        return None
    return float(value) - float(base)


def fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render(baseline_path: Path, baseline: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    base_fps = baseline.get("fps_median")
    base_cpu = baseline.get("disp_plus_main_ms_median")
    lines = [
        "OpenSPM Yold one-NPC active-bit sweep",
        f"baseline: {baseline_path.name}",
        (
            "baseline cadence: "
            f"{fmt(base_fps)} fps, disp+main={fmt(base_cpu, 3)} ms, "
            f"windows={baseline.get('phase_windows', 0)}"
        ),
        "",
        "Each row temporarily suppressed exactly one active NPC:",
    ]

    enriched: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["fps_delta"] = delta(row.get("fps_median"), base_fps)
        row["cpu_delta_ms"] = delta(row.get("disp_plus_main_ms_median"), base_cpu)
        # Bigger positive fps and more-negative CPU time are both evidence that
        # this NPC contributes to the pathological active-entry load. The score
        # is only for ordering diagnostic rows; it is not a performance verdict.
        fps_score = row["fps_delta"] or 0.0
        cpu_score = -(row["cpu_delta_ms"] or 0.0) * 4.0
        row["signal_score"] = fps_score + cpu_score
        enriched.append(row)

    ranked = sorted(
        enriched, key=lambda item: item["signal_score"], reverse=True
    )
    for row in ranked:
        fps_delta = row.get("fps_delta")
        cpu_delta = row.get("cpu_delta_ms")
        fps_delta_text = "n/a" if fps_delta is None else f"{fps_delta:+.2f}"
        cpu_delta_text = "n/a" if cpu_delta is None else f"{cpu_delta:+.3f}"
        lines.append(
            f"  ordinal {row['ordinal']} {row['name']} "
            f"(table #{row['table_index']}, id={row['id']}): "
            f"{fmt(row.get('fps_median'))} fps ({fps_delta_text}), "
            f"disp+main={fmt(row.get('disp_plus_main_ms_median'), 3)} ms "
            f"({cpu_delta_text}), windows={row.get('phase_windows')}"
        )

    best = ranked[0]
    lines.extend([
        "",
        (
            "largest single-NPC signal: "
            f"ordinal {best['ordinal']} {best['name']} "
            f"(score={best['signal_score']:.2f}; diagnostic ordering only)"
        ),
        (
            "interpretation: if every row moves only a little, cost probably scales "
            "across active entries or shared manager/display work. If one row jumps "
            "dramatically, inspect that actor/tribe/display-registration path first."
        ),
    ])
    return "\n".join(lines)


def self_test() -> None:
    sample = (
        "M14 dev29 live debug command END n=18 'npcs pulseone 2 120' -> "
        "'NPC one-pulse=on ordinal=2 index=11 id=77 name=mura_2 ticks=119; "
        "only flag8.active is suppressed and auto-restored'\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory(prefix="openspm-npc-sweep-") as td:
        path = Path(td) / "x.log"
        path.write_text(sample, encoding="utf-8")
        ident = parse_identity(path)
        assert ident["ordinal"] == 2
        assert ident["table_index"] == 11
        assert ident["id"] == 77
        assert ident["name"] == "mura_2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("Yold NPC sweep analyzer self-test passed")
        return 0

    try:
        baseline_path, baseline, rows = load_latest_session()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {"baseline_path": str(baseline_path), "baseline": baseline, "rows": rows},
            indent=2,
            sort_keys=True,
        ))
        return 0

    print(render(baseline_path, baseline, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
