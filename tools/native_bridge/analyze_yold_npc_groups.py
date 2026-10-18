#!/usr/bin/env python3
"""Analyze the newest Yold Town active-NPC group/threshold sweep."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import analyze_m14_perf  # noqa: E402

CAPTURE_DIR = ROOT / ".openspm" / "m14" / "debug-captures"
SESSION_WINDOW_SECONDS = 180.0

EXPECTED = [
    ("baseline", 0),
    ("off2", 2),
    ("off4", 4),
    ("off6", 6),
    ("off7", 7),
]


def newest_for(label: str) -> list[Path]:
    return sorted(
        CAPTURE_DIR.glob(f"*-yold-group-{label}.log"),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )


def median_int(values: list[int]) -> float | None:
    return statistics.median(values) if values else None


def active_text(path: Path) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    begin = text.find("YOLD-GROUP-ACTIVE-BEGIN")
    end = text.find("YOLD-GROUP-ACTIVE-END", begin + 1) if begin >= 0 else -1
    if begin < 0 or end < 0 or end <= begin:
        return text, False
    line_start = text.rfind("\n", 0, begin)
    line_end = text.find("\n", end)
    if line_start < 0:
        line_start = 0
    else:
        line_start += 1
    if line_end < 0:
        line_end = len(text)
    return text[line_start:line_end], True


def extra_metrics_text(text: str) -> dict[str, float | None]:
    owned = [
        int(m.group(1))
        for m in re.finditer(
            r"dev112 NPC-owned disp callbacks "
            r"param_in_table=\d+ exact_entry=(\d+)",
            text,
        )
    ]
    hot = []
    for line in text.splitlines():
        if "dev111 NPC/display census" not in line:
            continue
        match = re.search(r"\[0x8008533C:(\d+)\]", line)
        if match:
            hot.append(int(match.group(1)))
    nested = [
        int(m.group(1)) / 1000.0
        for m in re.finditer(
            r"nested_steps_per_attempt_x1000=(\d+)",
            text,
        )
    ]
    return {
        "owned_callbacks_per_16_median": median_int(owned),
        "owned_callbacks_per_16_min": min(owned) if owned else None,
        "hot_8008533c_per_16_median": median_int(hot),
        "nested_steps_per_attempt_median": (
            statistics.median(nested) if nested else None
        ),
    }


def latest_session() -> tuple[Path, list[dict[str, object]]]:
    baselines = newest_for("baseline")
    if not baselines:
        raise OSError("no yold-group-baseline capture found")
    baseline_path = baselines[0]
    base_time = baseline_path.stat().st_mtime

    rows: list[dict[str, object]] = []
    for label, off_count in EXPECTED:
        candidates = newest_for(label)
        if label == "baseline":
            path = baseline_path
        else:
            eligible = [
                p for p in candidates
                if base_time <= p.stat().st_mtime
                and p.stat().st_mtime - base_time <= SESSION_WINDOW_SECONDS
            ]
            if not eligible:
                raise OSError(
                    f"latest group sweep incomplete: missing {label} capture "
                    f"within {SESSION_WINDOW_SECONDS:.0f}s of "
                    f"{baseline_path.name}"
                )
            path = eligible[0]

        scoped_text, exact_window = active_text(path)
        perf = (
            analyze_m14_perf.parse_text(scoped_text, source=str(path) + "#active")
            if label != "baseline" and exact_window
            else analyze_m14_perf.parse_file(path)
        )
        if int(perf.get("phase_windows") or 0) < 1:
            raise OSError(
                f"{path.name} has no complete main-loop phase window"
            )
        rows.append({
            "label": label,
            "off_count": off_count,
            "active_count": 7 - off_count,
            "path": path,
            "fps": perf.get("fps_median"),
            "disp_ms": perf.get("disp_ms_median"),
            "main_ms": perf.get("main_ms_median"),
            "disp_main_ms": perf.get("disp_plus_main_ms_median"),
            "done_ms": perf.get("done_ms_median"),
            "phase_windows": perf.get("phase_windows"),
            "exact_active_window": exact_window if label != "baseline" else True,
            **extra_metrics_text(scoped_text),
        })
    return baseline_path, rows


def fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def render(rows: list[dict[str, object]]) -> str:
    base = rows[0]
    base_fps = float(base["fps"]) if base["fps"] is not None else None
    base_dm = (
        float(base["disp_main_ms"])
        if base["disp_main_ms"] is not None else None
    )

    lines = [
        "OpenSPM Yold active-NPC group threshold sweep",
        "",
        " off  active     fps   dFPS  disp+main(ms)   delta(ms)  "
        "NPC-cb/16  hot8533C/16  nested-steps  exact",
    ]
    for row in rows:
        fps = float(row["fps"]) if row["fps"] is not None else None
        dm = (
            float(row["disp_main_ms"])
            if row["disp_main_ms"] is not None else None
        )
        dfps = None if fps is None or base_fps is None else fps - base_fps
        ddm = None if dm is None or base_dm is None else dm - base_dm
        lines.append(
            f"{int(row['off_count']):>4}  {int(row['active_count']):>6}  "
            f"{fmt(fps):>6}  "
            f"{('n/a' if dfps is None else f'{dfps:+.2f}'):>6}  "
            f"{fmt(dm,3):>13}  "
            f"{('n/a' if ddm is None else f'{ddm:+.3f}'):>9}  "
            f"{fmt(row['owned_callbacks_per_16_min'],0):>9}  "
            f"{fmt(row['hot_8008533c_per_16_median'],0):>11}  "
            f"{fmt(row['nested_steps_per_attempt_median'],2):>12}  "
            f"{('yes' if row.get('exact_active_window') else 'legacy'):>6}"
        )

    valid = [
        row for row in rows
        if row["fps"] is not None and row["disp_main_ms"] is not None
    ]
    if len(valid) >= 2:
        last = valid[-1]
        fps_gain = float(last["fps"]) - float(base["fps"])
        cpu_gain = float(base["disp_main_ms"]) - float(last["disp_main_ms"])
        lines.extend([
            "",
            (
                f"all-off endpoint: fps {fps_gain:+.2f}, "
                f"disp+main recovered {cpu_gain:+.3f} ms"
            ),
        ])

    # Coarse but useful classification for the next engineering branch.
    off2 = rows[1]
    off4 = rows[2]
    off6 = rows[3]
    off7 = rows[4]
    dm_vals = [
        None if r["disp_main_ms"] is None else float(r["disp_main_ms"])
        for r in rows
    ]
    fps_vals = [
        None if r["fps"] is None else float(r["fps"])
        for r in rows
    ]
    if all(v is not None for v in dm_vals + fps_vals):
        base_dm_f = dm_vals[0]
        recover6 = base_dm_f - dm_vals[3]
        recover7 = base_dm_f - dm_vals[4]
        fps6 = fps_vals[3] - fps_vals[0]
        fps7 = fps_vals[4] - fps_vals[0]
        if fps7 >= 15.0 and fps6 < 5.0:
            classification = (
                "threshold/nonlinear: removing six still behaves near baseline, "
                "but removing all seven crosses the cadence cliff"
            )
        elif recover6 > 0.0 and recover7 >= recover6 * 0.8:
            classification = (
                "distributed/scaling: cost falls progressively as active NPCs "
                "are removed"
            )
        elif abs(recover7) < 0.5 and abs(fps7) < 3.0:
            classification = (
                "weak active-set effect in this room: historical all-NPC result "
                "may depend on another scene/state distinction"
            )
        else:
            classification = (
                "mixed/nonlinear: inspect the table above before choosing the "
                "next subsystem bisector"
            )
        lines.append("classification: " + classification)

    return "\n".join(lines)


def self_test() -> None:
    sample = (
        "[OpenSPMNative:info] M14 LLVM dev111 NPC/display census "
        "frames=16 callbacks=5776 collisions=16 top= "
        "[0x8008533C:4800] [0x801B43CC:192]\n"
        "[OpenSPMNative:info] M14 LLVM dev112 NPC-owned disp callbacks "
        "param_in_table=256 exact_entry=192 table=0x8077A0A0 count=80\n"
        "[OpenSPMNative:info] M14 LLVM dev113 cadence chunk=256 state_mem=0 "
        "host_ms=1000 nested_attempts=10 nested_steps=220 "
        "nested_steps_per_attempt_x1000=22000 pc=0x0.\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory(prefix="openspm-yold-groups-") as td:
        p = Path(td) / "sample.log"
        p.write_text(sample, encoding="utf-8")
        extra = extra_metrics_text(sample)
        assert extra["owned_callbacks_per_16_min"] == 192
        assert extra["hot_8008533c_per_16_median"] == 4800
        assert extra["nested_steps_per_attempt_median"] == 22.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("Yold NPC group analyzer self-test passed")
        return 0
    try:
        _, rows = latest_session()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
